"""Real PTY/ConPTY performance benchmark for Cyrene terminals.

Run on each supported host so the same matrix exercises POSIX PTY on macOS or
Linux and ConPTY through pywinpty on Windows::

    uv run python -m cyrene.observability.terminal_performance_benchmark

Unlike the deterministic feature benchmark, this launches real child
processes, passes their output through the platform PTY, parses it with pyte,
persists segmented scrollback, queries history/commands, and optionally relays
live events through an actual loopback WebSocket connection.
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import json
import math
import os
import re
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path
from typing import Any


WORKLOADS = ("plain", "ansi", "tui")
_PROCESS_EXIT_TIMEOUT_SECONDS = 120 if sys.platform == "win32" else 30
_INPUT_LINE_ENDING = b"\r" if sys.platform == "win32" else b"\n"
_CHILD_PROGRAM = r"""
import os
import sys
import time

kind, raw_total, gate = sys.argv[1:4]
release = sys.argv[4] if len(sys.argv) > 4 else ""
total = int(raw_total)
while not os.path.exists(gate):
    time.sleep(0.001)
stream = sys.stdout.buffer
written = 0
frame = 0
while written < total:
    marker = f"__CYRENE_FRAME_{frame:08x}__".encode("ascii")
    if kind == "plain":
        chunk = marker + b" compile unit=terminal_benchmark status=ok\n"
    elif kind == "ansi":
        chunk = b"\x1b[36m[compile]\x1b[0m \x1b[1m" + marker + b"\x1b[0m status=ok\n"
    else:
        chunk = b"\x1b[2J\x1b[H\x1b[32m" + marker + b"\x1b[0m\n\x1b[2;1Hprogress 100%\x1b[K"
    stream.write(chunk)
    written += len(chunk)
    frame += 1
stream.write(f"__CYRENE_FRAME_COUNT_{frame:08x}__".encode("ascii"))
stream.write(
    b"\x1b]133;A\x1b\\\x1b]133;B\x1b\\benchmark command\r\n"
    b"\x1b]133;C\x1b\\CYRENE_BENCHMARK_COMMAND_OUTPUT\r\n"
    b"\x1b]133;D;0\x1b\\"
)
stream.write(b"__CYRENE_BENCHMARK_COMPLETE__")
stream.flush()
while release and not os.path.exists(release):
    time.sleep(0.001)
"""

_INTERACTIVE_CHILD_PROGRAM = r"""
import sys

stream = sys.stdout.buffer
stream.write(b"INTERACTIVE_READY\r\n")
stream.flush()
for line in sys.stdin.buffer:
    stream.write(b"INTERACTIVE_ECHO:" + line)
    stream.flush()
    if line.strip().lower() == b"quit":
        break
"""

_SOURCE_FRAME_RE = re.compile(
    rb"__CYRENE_(FRAME_COUNT|FRAME)_([0-9a-f]{8})__"
)
_SOURCE_COMPLETE_MARKER = b"__CYRENE_BENCHMARK_COMPLETE__"


class _SourceFrameValidator:
    """Validate monotonic source markers without retaining the output stream."""

    def __init__(self) -> None:
        self._buffer = bytearray()
        self.bytes_seen = 0
        self.frames_seen = 0
        self.sequence_errors = 0
        self.expected_frames: int | None = None
        self.complete = False

    def feed(self, data: bytes) -> None:
        self.bytes_seen += len(data)
        self._buffer.extend(data)
        if _SOURCE_COMPLETE_MARKER in self._buffer:
            self.complete = True
        consumed = 0
        for match in _SOURCE_FRAME_RE.finditer(self._buffer):
            kind = bytes(match.group(1))
            value = int(match.group(2), 16)
            if kind == b"FRAME":
                if value != self.frames_seen:
                    self.sequence_errors += 1
                self.frames_seen += 1
            else:
                self.expected_frames = value
            consumed = match.end()
        if consumed:
            del self._buffer[:consumed]
        if len(self._buffer) > 128:
            del self._buffer[:-128]

    @property
    def valid(self) -> bool:
        return bool(
            self.frames_seen > 0
            and self.expected_frames == self.frames_seen
            and self.sequence_errors == 0
        )


def _percentile(values: list[float], percentile: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, math.ceil(len(ordered) * percentile) - 1))
    return ordered[index]


def _rss_bytes() -> int:
    """Best available current process RSS without adding a benchmark dependency."""
    if sys.platform == "win32":  # pragma: no cover - exercised on Windows CI/hosts
        import ctypes
        from ctypes import wintypes

        class Counters(ctypes.Structure):
            _fields_ = [
                ("cb", wintypes.DWORD),
                ("PageFaultCount", wintypes.DWORD),
                ("PeakWorkingSetSize", ctypes.c_size_t),
                ("WorkingSetSize", ctypes.c_size_t),
                ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                ("PagefileUsage", ctypes.c_size_t),
                ("PeakPagefileUsage", ctypes.c_size_t),
            ]

        counters = Counters()
        counters.cb = ctypes.sizeof(counters)
        ctypes.windll.psapi.GetProcessMemoryInfo(
            ctypes.windll.kernel32.GetCurrentProcess(),
            ctypes.byref(counters), counters.cb,
        )
        return int(counters.WorkingSetSize)
    if sys.platform.startswith("linux"):
        try:
            resident_pages = int(Path("/proc/self/statm").read_text().split()[1])
            return resident_pages * int(os.sysconf("SC_PAGE_SIZE"))
        except (OSError, ValueError, IndexError):
            pass
    import resource

    maximum = int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
    return maximum if sys.platform == "darwin" else maximum * 1024


def _process_write_bytes() -> int:
    """Return cumulative process disk writes using the host OS counter."""
    if sys.platform == "win32":  # pragma: no cover - exercised on Windows CI/hosts
        import ctypes

        class IoCounters(ctypes.Structure):
            _fields_ = [(name, ctypes.c_ulonglong) for name in (
                "ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
                "ReadTransferCount", "WriteTransferCount", "OtherTransferCount",
            )]

        counters = IoCounters()
        ctypes.windll.kernel32.GetProcessIoCounters(
            ctypes.windll.kernel32.GetCurrentProcess(), ctypes.byref(counters)
        )
        return int(counters.WriteTransferCount)
    if sys.platform.startswith("linux"):
        try:
            values = {
                key: int(value.strip())
                for key, value in (
                    line.split(":", 1)
                    for line in Path("/proc/self/io").read_text().splitlines()
                )
            }
            return values.get("write_bytes", 0)
        except (OSError, ValueError):
            pass
    import resource

    return int(resource.getrusage(resource.RUSAGE_SELF).ru_oublock) * 512


async def _wait_for_exit(manager: Any, terminal_id: str) -> None:
    # ConPTY can keep draining a sustained multi-megabyte stream after the
    # child has finished writing, especially on emulated ARM64 CI runners.
    async with asyncio.timeout(_PROCESS_EXIT_TIMEOUT_SECONDS):
        while manager.get(terminal_id).status not in {"exited", "closed"}:
            await asyncio.sleep(0.005)


def _benchmark_argv(workload: str, total_bytes: int, gate: Path, release: Path) -> list[str]:
    return [
        sys.executable, "-u", "-c", _CHILD_PROGRAM, workload,
        str(total_bytes), str(gate), str(release),
    ]


async def _close_benchmark_terminals(manager: Any, terminal_ids: tuple[str, ...]) -> None:
    for terminal_id in terminal_ids:
        if terminal_id in manager._sessions:
            await manager.close(terminal_id, remove=True)


def _benchmark_case_root(root: Path, name: str) -> Path:
    target = root / name
    target.mkdir(parents=True)
    return target


async def _heartbeat(
    stop: asyncio.Event, latencies_ms: list[float], rss_samples: list[int],
) -> None:
    interval = 0.002
    expected = asyncio.get_running_loop().time() + interval
    while not stop.is_set():
        await asyncio.sleep(interval)
        now = asyncio.get_running_loop().time()
        latencies_ms.append(max(0.0, (now - expected) * 1000))
        rss_samples.append(_rss_bytes())
        expected = now + interval


async def _websocket_relay(
    manager: Any, terminal_id: str, gate: Path, release: Path,
) -> tuple[list[float], int, int, int]:
    from websockets.asyncio.client import connect
    from websockets.asyncio.server import serve

    connected = asyncio.Event()
    received_bytes = 0
    resyncs = 0
    sequence_errors = 0
    expected_seq = 0
    released = False
    latencies_ms: list[float] = []
    relayed_frames = _SourceFrameValidator()

    async def handler(websocket: Any) -> None:
        queue = manager.subscribe(terminal_id)
        try:
            replay_end = manager.get(terminal_id).next_seq
            for event in await manager.replay_async(
                terminal_id, 0, end_seq=replay_end
            ):
                await websocket.send(json.dumps(event, separators=(",", ":")))
            connected.set()
            while True:
                event = await queue.get()
                if event.get("type") == "output":
                    start = int(event["seq"])
                    end = int(event["nextSeq"])
                    if end <= replay_end:
                        continue
                    if start < replay_end:
                        data = base64.b64decode(event["data"])
                        event = {
                            **event,
                            "seq": replay_end,
                            "data": base64.b64encode(
                                data[replay_end - start:]
                            ).decode("ascii"),
                        }
                await websocket.send(json.dumps(event, separators=(",", ":")))
                if (
                    event.get("type") == "state"
                    and event.get("terminal", {}).get("status") in {"exited", "closed"}
                ):
                    return
        finally:
            manager.unsubscribe(terminal_id, queue)

    async def client(uri: str) -> None:
        nonlocal received_bytes, resyncs, sequence_errors, expected_seq, released
        async with connect(uri, max_size=None, proxy=None) as websocket:
            await connected.wait()
            gate.touch()
            async for raw in websocket:
                received_at = time.time()
                event = json.loads(raw)
                if event.get("type") == "output":
                    if int(event["seq"]) != expected_seq:
                        sequence_errors += 1
                    expected_seq = int(event["nextSeq"])
                    received_bytes += int(event["nextSeq"]) - int(event["seq"])
                    relayed_frames.feed(base64.b64decode(event["data"]))
                    created_at = datetime.fromisoformat(str(event["createdAt"]))
                    latencies_ms.append(
                        max(0.0, (received_at - created_at.timestamp()) * 1000)
                    )
                    if relayed_frames.complete and not released:
                        released = True
                        release.touch()
                        if sys.platform == "win32":
                            await manager.close(terminal_id)
                elif event.get("type") == "resync_required":
                    resyncs += 1
                elif (
                    event.get("type") == "state"
                    and event.get("terminal", {}).get("status") in {"exited", "closed"}
                ):
                    return

    async with serve(handler, "127.0.0.1", 0) as server:
        port = int(server.sockets[0].getsockname()[1])
        await client(f"ws://127.0.0.1:{port}")
    return latencies_ms, received_bytes, resyncs, sequence_errors


async def _run_case(
    root: Path, workload: str, subscribed: bool, total_bytes: int, output_limit: int,
) -> dict[str, Any]:
    from cyrene.plugins.builtin.cyrene_code.terminal.manager import TerminalManager

    name = f"{workload}-{'subscribed' if subscribed else 'unsubscribed'}"
    case_root = _benchmark_case_root(root, name)
    gate = case_root / "start.gate"
    release = case_root / "release.gate"
    manager = TerminalManager(output_limit=output_limit, state_dir=case_root / "state")
    source_frames = _SourceFrameValidator()
    append_output = manager._append_output

    def validate_source(session: Any, data: bytes) -> None:
        source_frames.feed(data)
        append_output(session, data)

    manager._append_output = validate_source  # type: ignore[method-assign]
    terminal = await manager.create_resolved(
        "benchmark",
        cwd=str(case_root),
        shell="python",
        argv=_benchmark_argv(workload, total_bytes, gate, release),
        title=f"{workload}-{subscribed}",
        launch_mode="one_shot",
    )
    terminal_id = str(terminal["id"])
    writer = manager._persistence_writer
    assert writer is not None
    initial_worker = writer.metrics()
    initial_rss = _rss_bytes()
    initial_disk_writes = _process_write_bytes()
    heartbeat_latencies: list[float] = []
    rss_samples = [initial_rss]
    heartbeat_stop = asyncio.Event()
    heartbeat_task = asyncio.create_task(
        _heartbeat(heartbeat_stop, heartbeat_latencies, rss_samples)
    )
    started = time.perf_counter()
    websocket_latencies: list[float] = []
    websocket_bytes = 0
    resyncs = 0
    websocket_sequence_errors = 0
    try:
        if subscribed:
            (
                websocket_latencies, websocket_bytes, resyncs,
                websocket_sequence_errors,
            ) = await _websocket_relay(
                manager, terminal_id, gate, release
            )
        else:
            gate.touch()
            async with asyncio.timeout(_PROCESS_EXIT_TIMEOUT_SECONDS):
                while not source_frames.complete:
                    await asyncio.sleep(0.005)
            release.touch()
            if sys.platform == "win32":
                await manager.close(terminal_id)
        await _wait_for_exit(manager, terminal_id)
        manager.flush()
        screen = await manager.screen_snapshot_async(terminal_id)
        matches = await manager.search_history_async(
            "benchmark", "cyrene_benchmark_command_output", terminal_id=terminal_id
        )
        commands = await manager.commands_async(terminal_id)
        replay = await manager.replay_async(terminal_id, 0)
        elapsed_ms = (time.perf_counter() - started) * 1000
        worker_metrics = writer.metrics()
        session = manager.get(terminal_id)
        segment_bytes = sum(
            path.stat().st_size
            for path in manager._scroll_segment_dir(terminal_id).glob("*.bin")
        )
        event_rows = int(manager._db.execute(
            "SELECT COUNT(*) FROM terminal_output_events WHERE terminal_id=?",
            (terminal_id,),
        ).fetchone()[0])
        process_writes = max(0, _process_write_bytes() - initial_disk_writes)
        worker_writes = worker_metrics["bytesWritten"] - initial_worker["bytesWritten"]
        parsed_bytes = worker_metrics["screenBytesParsed"] - initial_worker["screenBytesParsed"]
        screen_updates = worker_metrics["screenUpdates"] - initial_worker["screenUpdates"]
        screen_batches = worker_metrics["screenBatches"] - initial_worker["screenBatches"]
        actual_bytes = session.next_seq
        write_amplification = worker_writes / max(actual_bytes, 1)
        replay_expected = int(replay[0]["seq"]) if replay else actual_bytes
        replay_sequence_errors = 0
        replay_bytes = 0
        replay_data = bytearray()
        for event in replay:
            start = int(event["seq"])
            end = int(event["nextSeq"])
            decoded = base64.b64decode(event["data"])
            if start != replay_expected or len(decoded) != end - start:
                replay_sequence_errors += 1
            replay_expected = end
            replay_bytes += len(decoded)
            replay_data.extend(decoded)
        if replay_expected != actual_bytes:
            replay_sequence_errors += 1
        replay_start = int(replay[0]["seq"]) if replay else actual_bytes
        expected_replay = b"".join(
            chunk.data[max(0, replay_start - chunk.start):]
            for chunk in session.output
            if chunk.end > replay_start
        )
        replay_data_matches = bytes(replay_data) == expected_replay
        return {
            "workload": workload,
            "subscribed": subscribed,
            "ptyBackend": "conpty" if sys.platform == "win32" else "posix_pty",
            "requestedBytes": total_bytes,
            "actualPtyBytes": actual_bytes,
            "sourceBytesObserved": source_frames.bytes_seen,
            "sourceFramesObserved": source_frames.frames_seen,
            "sourceFramesExpected": source_frames.expected_frames,
            "sourceFrameSequenceErrors": source_frames.sequence_errors,
            "sourceFramesValid": source_frames.valid,
            "historyMatchFound": bool(matches),
            "commandsCaptured": bool(commands),
            "commandOutputVisible": (
                "CYRENE_BENCHMARK_COMMAND_OUTPUT" in screen["screenText"]
            ),
            "elapsedMs": round(elapsed_ms, 3),
            "throughputMiBPerSecond": round(
                actual_bytes / (1024 * 1024) / max(elapsed_ms / 1000, 0.000001), 3
            ),
            "eventLoopDelayP95Ms": round(_percentile(heartbeat_latencies, 0.95), 3),
            "eventLoopDelayMaxMs": round(max(heartbeat_latencies, default=0.0), 3),
            "rssPeakDeltaBytes": max(rss_samples) - initial_rss,
            "processDiskWriteBytes": process_writes,
            "scrollbackBytesWritten": worker_writes,
            "scrollbackWriteAmplification": round(
                write_amplification, 4
            ),
            "retainedSegmentBytes": segment_bytes,
            "segmentsDeleted": worker_metrics["segmentsDeleted"] - initial_worker["segmentsDeleted"],
            "sqliteOutputEventRows": event_rows,
            "screenBytesParsed": parsed_bytes,
            "screenUpdates": screen_updates,
            "screenBatches": screen_batches,
            "webSocketLatencyP95Ms": round(_percentile(websocket_latencies, 0.95), 3),
            "webSocketLatencyMaxMs": round(max(websocket_latencies, default=0.0), 3),
            "webSocketBytes": websocket_bytes,
            "webSocketResyncs": resyncs,
            "webSocketSequenceErrors": websocket_sequence_errors,
            "replayBytes": replay_bytes,
            "replaySequenceErrors": replay_sequence_errors,
            "replayDataMatches": replay_data_matches,
            "workerQueueWaitMaxMs": round(
                worker_metrics["workerQueueWaitMaxUs"] / 1000, 3
            ),
            "queryQueueWaitMaxMs": round(
                worker_metrics["queryQueueWaitMaxUs"] / 1000, 3
            ),
            "terminalWorkPeakBytes": worker_metrics["terminalWorkPeakBytes"],
            "qualityPreserved": bool(
                matches
                and commands
                and "CYRENE_BENCHMARK_COMMAND_OUTPUT" in screen["screenText"]
                and actual_bytes >= total_bytes
                and source_frames.bytes_seen == actual_bytes
                and source_frames.valid
                and parsed_bytes == actual_bytes
                and 0 < screen_batches <= screen_updates
                and replay_sequence_errors == 0
                and replay_data_matches
                and write_amplification < 1.1
                and (
                    not subscribed
                    or (
                        websocket_bytes == actual_bytes
                        and resyncs == 0
                        and websocket_sequence_errors == 0
                    )
                )
            ),
        }
    finally:
        heartbeat_stop.set()
        await heartbeat_task
        await _close_benchmark_terminals(manager, (terminal_id,))
        manager.close_store()


async def _run_fairness_case(
    root: Path, total_bytes: int, output_limit: int,
) -> dict[str, Any]:
    """Measure an interactive terminal while a second terminal floods output."""
    from websockets.asyncio.client import connect
    from websockets.asyncio.server import serve

    from cyrene.plugins.builtin.cyrene_code.terminal.manager import TerminalManager

    case_root = _benchmark_case_root(root, "multi-terminal-fairness")
    noisy_gate = case_root / "noisy.gate"
    noisy_release = case_root / "noisy.release"
    manager = TerminalManager(output_limit=output_limit, state_dir=case_root / "state")
    noisy = await manager.create_resolved(
        "benchmark",
        cwd=str(case_root),
        shell="python",
        argv=_benchmark_argv("ansi", total_bytes, noisy_gate, noisy_release),
        title="fairness-noisy",
        launch_mode="one_shot",
    )
    interactive = await manager.create_resolved(
        "benchmark",
        cwd=str(case_root),
        shell="python",
        argv=[sys.executable, "-u", "-c", _INTERACTIVE_CHILD_PROGRAM],
        title="fairness-interactive",
        launch_mode="one_shot",
    )
    noisy_id = str(noisy["id"])
    interactive_id = str(interactive["id"])
    connected = asyncio.Event()
    resyncs = 0
    sequence_errors = 0
    expected_seq: int | None = None
    interactive_output = bytearray()
    echo_latency_ms = 0.0
    noisy_running_at_echo = False

    async def handler(websocket: Any) -> None:
        queue = manager.subscribe(interactive_id)
        connected.set()

        async def send_events() -> None:
            while True:
                event = await queue.get()
                await websocket.send(json.dumps(event, separators=(",", ":")))
                if (
                    event.get("type") == "state"
                    and event.get("terminal", {}).get("status") in {"exited", "closed"}
                ):
                    return

        async def receive_input() -> None:
            async for raw in websocket:
                message = json.loads(raw)
                if message.get("type") != "input":
                    continue
                await manager.write_bytes(
                    interactive_id,
                    base64.b64decode(str(message.get("data") or "")),
                    actor="user",
                )

        try:
            sender = asyncio.create_task(send_events())
            receiver = asyncio.create_task(receive_input())
            done, pending = await asyncio.wait(
                {sender, receiver}, return_when=asyncio.FIRST_COMPLETED
            )
            for task in pending:
                task.cancel()
            await asyncio.gather(*done, *pending, return_exceptions=True)
        finally:
            manager.unsubscribe(interactive_id, queue)

    try:
        async with serve(handler, "127.0.0.1", 0) as server:
            port = int(server.sockets[0].getsockname()[1])
            async with connect(
                f"ws://127.0.0.1:{port}", max_size=None, proxy=None
            ) as websocket:
                await connected.wait()
                noisy_gate.touch()
                sent_at = time.perf_counter()
                await websocket.send(json.dumps({
                    "type": "input",
                    "data": base64.b64encode(b"latency-probe" + _INPUT_LINE_ENDING).decode("ascii"),
                }))
                async with asyncio.timeout(30):
                    async for raw in websocket:
                        event = json.loads(raw)
                        if event.get("type") == "resync_required":
                            resyncs += 1
                            continue
                        if event.get("type") != "output":
                            continue
                        start = int(event["seq"])
                        end = int(event["nextSeq"])
                        if expected_seq is not None and start != expected_seq:
                            sequence_errors += 1
                        expected_seq = end
                        data = base64.b64decode(event["data"])
                        interactive_output.extend(data)
                        if b"INTERACTIVE_ECHO:latency-probe" in interactive_output:
                            echo_latency_ms = (time.perf_counter() - sent_at) * 1000
                            noisy_running_at_echo = (
                                manager.get(noisy_id).status == "running"
                            )
                            noisy_release.touch()
                            await websocket.send(json.dumps({
                                "type": "input",
                                "data": base64.b64encode(b"quit" + _INPUT_LINE_ENDING).decode("ascii"),
                            }))
                            break
        await _wait_for_exit(manager, noisy_id)
        await _wait_for_exit(manager, interactive_id)
        manager.flush()
        writer = manager._persistence_writer
        assert writer is not None
        metrics = writer.metrics()
        screen = await manager.screen_snapshot_async(interactive_id)
        return {
            "ptyBackend": "conpty" if sys.platform == "win32" else "posix_pty",
            "noisyBytes": manager.get(noisy_id).next_seq,
            "interactiveEchoLatencyMs": round(echo_latency_ms, 3),
            "noisyRunningAtEcho": noisy_running_at_echo,
            "webSocketResyncs": resyncs,
            "webSocketSequenceErrors": sequence_errors,
            "workerQueueWaitMaxMs": round(
                metrics["workerQueueWaitMaxUs"] / 1000, 3
            ),
            "queryQueueWaitMaxMs": round(
                metrics["queryQueueWaitMaxUs"] / 1000, 3
            ),
            "terminalWorkPeakBytes": metrics["terminalWorkPeakBytes"],
            "screenUpdates": metrics["screenUpdates"],
            "screenBatches": metrics["screenBatches"],
            "qualityPreserved": bool(
                echo_latency_ms > 0
                and noisy_running_at_echo
                and resyncs == 0
                and sequence_errors == 0
                and 0 < metrics["screenBatches"] <= metrics["screenUpdates"]
                and "INTERACTIVE_ECHO:latency-probe" in screen["screenText"]
            ),
        }
    finally:
        await _close_benchmark_terminals(manager, (interactive_id, noisy_id))
        manager.close_store()


async def run_benchmark(
    *, total_bytes: int = 24 * 1024 * 1024,
    output_limit: int = 16 * 1024 * 1024,
) -> dict[str, Any]:
    """Run all real terminal workloads with and without a WebSocket consumer."""
    total_bytes = max(64 * 1024, int(total_bytes))
    output_limit = max(64 * 1024, int(output_limit))
    with tempfile.TemporaryDirectory(prefix="cyrene-terminal-benchmark-") as temporary:
        root = Path(temporary)
        cases = [
            await _run_case(root, workload, subscribed, total_bytes, output_limit)
            for workload in WORKLOADS
            for subscribed in (False, True)
        ]
        fairness = await _run_fairness_case(root, total_bytes, output_limit)
    return {
        "schemaVersion": 2,
        "platform": sys.platform,
        "python": sys.version.split()[0],
        "totalBytesPerCase": total_bytes,
        "outputLimit": output_limit,
        "cases": cases,
        "fairness": fairness,
        "qualityPreserved": (
            all(case["qualityPreserved"] for case in cases)
            and fairness["qualityPreserved"]
        ),
    }


def compare_with_baseline(
    report: dict[str, Any], baseline: dict[str, Any],
    *, regression_threshold_percent: float = 20.0,
) -> dict[str, Any]:
    """Compare latency only when a matching historical platform case exists."""
    threshold = max(0.0, float(regression_threshold_percent))
    previous = {
        (str(case["workload"]), bool(case["subscribed"])): case
        for case in baseline.get("cases", [])
        if case.get("ptyBackend")
    }
    regressions: list[dict[str, Any]] = []
    matched = 0
    for case in report.get("cases", []):
        prior = previous.get((str(case["workload"]), bool(case["subscribed"])))
        if prior is None or prior.get("ptyBackend") != case.get("ptyBackend"):
            continue
        matched += 1
        for metric in ("eventLoopDelayP95Ms", "webSocketLatencyP95Ms"):
            old = float(prior.get(metric) or 0)
            current = float(case.get(metric) or 0)
            if old <= 0:
                continue
            delta = ((current - old) / old) * 100
            if delta > threshold:
                regressions.append({
                    "workload": case["workload"],
                    "subscribed": case["subscribed"],
                    "metric": metric,
                    "baseline": round(old, 3),
                    "current": round(current, 3),
                    "deltaPercent": round(delta, 2),
                })
    current_fairness = report.get("fairness", {})
    prior_fairness = baseline.get("fairness", {})
    if (
        current_fairness.get("ptyBackend")
        and current_fairness.get("ptyBackend") == prior_fairness.get("ptyBackend")
    ):
        old = float(prior_fairness.get("interactiveEchoLatencyMs") or 0)
        current = float(current_fairness.get("interactiveEchoLatencyMs") or 0)
        if old > 0:
            delta = ((current - old) / old) * 100
            if delta > threshold:
                regressions.append({
                    "workload": "multi-terminal-fairness",
                    "subscribed": True,
                    "metric": "interactiveEchoLatencyMs",
                    "baseline": round(old, 3),
                    "current": round(current, 3),
                    "deltaPercent": round(delta, 2),
                })
    return {
        "matchedCases": matched,
        "thresholdPercent": threshold,
        "regressions": regressions,
        "passed": not regressions,
    }


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Cyrene real terminal performance benchmark",
        "",
        f"Platform: `{report['platform']}`; output limit: `{report['outputLimit']}` bytes.",
        "",
        "| Workload | Frontend | PTY | MiB/s | Loop p95 ms | RSS delta MiB | Disk writes MiB | Scroll write amp | WS p95 ms | Quality |",
        "|---|---|---|---:|---:|---:|---:|---:|---:|---|",
    ]
    for case in report["cases"]:
        lines.append(
            "| {workload} | {frontend} | {ptyBackend} | {throughputMiBPerSecond:.3f} | "
            "{eventLoopDelayP95Ms:.3f} | {rss:.2f} | {disk:.2f} | "
            "{scrollbackWriteAmplification:.4f} | {webSocketLatencyP95Ms:.3f} | {quality} |".format(
                **case,
                frontend="subscribed" if case["subscribed"] else "none",
                rss=case["rssPeakDeltaBytes"] / (1024 * 1024),
                disk=case["processDiskWriteBytes"] / (1024 * 1024),
                quality="pass" if case["qualityPreserved"] else "FAIL",
            )
        )
    fairness = report.get("fairness", {})
    failed_cases = [
        case for case in report["cases"] if not case["qualityPreserved"]
    ]
    for case in failed_cases:
        lines.extend([
            "",
            f"### Failed quality details: {case['workload']} / "
            f"{'subscribed' if case['subscribed'] else 'none'}",
            "",
            "```json",
            json.dumps(case, indent=2, sort_keys=True),
            "```",
        ])
    if fairness:
        lines.extend([
            "",
            "## Multi-terminal fairness",
            "",
            "Interactive echo during a noisy terminal: "
            f"`{float(fairness['interactiveEchoLatencyMs']):.3f} ms`; "
            f"worker queue max wait: `{float(fairness['workerQueueWaitMaxMs']):.3f} ms`; "
            f"resyncs: `{int(fairness['webSocketResyncs'])}`; "
            f"quality: `{'pass' if fairness['qualityPreserved'] else 'FAIL'}`.",
        ])
    return "\n".join(lines) + "\n"


async def _main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bytes", type=int, default=24 * 1024 * 1024)
    parser.add_argument("--output-limit", type=int, default=16 * 1024 * 1024)
    parser.add_argument("--json", type=Path)
    parser.add_argument("--baseline", type=Path)
    parser.add_argument("--regression-threshold-percent", type=float, default=20.0)
    parser.add_argument("--fail-on-quality", action="store_true")
    arguments = parser.parse_args()
    report = await run_benchmark(
        total_bytes=arguments.bytes, output_limit=arguments.output_limit
    )
    if arguments.baseline:
        baseline = json.loads(arguments.baseline.read_text(encoding="utf-8"))
        report["baselineComparison"] = compare_with_baseline(
            report, baseline,
            regression_threshold_percent=arguments.regression_threshold_percent,
        )
    if arguments.json:
        arguments.json.parent.mkdir(parents=True, exist_ok=True)
        arguments.json.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(render_markdown(report))
    if arguments.fail_on_quality and (
        not report["qualityPreserved"]
        or not report.get("baselineComparison", {"passed": True})["passed"]
    ):
        raise SystemExit(1)


if __name__ == "__main__":
    asyncio.run(_main())


__all__ = [
    "WORKLOADS", "compare_with_baseline", "render_markdown", "run_benchmark",
]
