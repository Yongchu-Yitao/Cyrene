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
import json
import math
import os
import statistics
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path
from typing import Any


WORKLOADS = ("plain", "ansi", "tui")
_CHILD_PROGRAM = r"""
import os
import sys
import time

kind, raw_total, gate = sys.argv[1:4]
total = int(raw_total)
while not os.path.exists(gate):
    time.sleep(0.001)
if kind == "plain":
    block = b"compile unit=terminal_benchmark status=ok value=0123456789\n"
elif kind == "ansi":
    block = b"\x1b[36m[compile]\x1b[0m \x1b[1mterminal_benchmark\x1b[0m status=ok\n"
else:
    block = b"\x1b[2J\x1b[H\x1b[32mterminal_benchmark\x1b[0m\n\x1b[2;1Hprogress 100%\x1b[K"
stream = sys.stdout.buffer
remaining = total
while remaining:
    chunk = block[:remaining]
    stream.write(chunk)
    remaining -= len(chunk)
stream.write(
    b"\x1b]133;A\x1b\\\x1b]133;B\x1b\\benchmark command\r\n"
    b"\x1b]133;C\x1b\\CYRENE_BENCHMARK_COMMAND_OUTPUT\r\n"
    b"\x1b]133;D;0\x1b\\"
)
stream.flush()
"""


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
    async with asyncio.timeout(30):
        while manager.get(terminal_id).status not in {"exited", "closed"}:
            await asyncio.sleep(0.005)


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
    manager: Any, terminal_id: str, gate: Path,
) -> tuple[list[float], int, int]:
    from websockets.asyncio.client import connect
    from websockets.asyncio.server import serve

    connected = asyncio.Event()
    received_bytes = 0
    resyncs = 0
    latencies_ms: list[float] = []

    async def handler(websocket: Any) -> None:
        queue = manager.subscribe(terminal_id)
        connected.set()
        try:
            while True:
                event = await queue.get()
                await websocket.send(json.dumps(event, separators=(",", ":")))
                if (
                    event.get("type") == "state"
                    and event.get("terminal", {}).get("status") in {"exited", "closed"}
                ):
                    return
        finally:
            manager.unsubscribe(terminal_id, queue)

    async def client(uri: str) -> None:
        nonlocal received_bytes, resyncs
        async with connect(uri, max_size=None, proxy=None) as websocket:
            await connected.wait()
            gate.touch()
            async for raw in websocket:
                received_at = time.time()
                event = json.loads(raw)
                if event.get("type") == "output":
                    received_bytes += int(event["nextSeq"]) - int(event["seq"])
                    created_at = datetime.fromisoformat(str(event["createdAt"]))
                    latencies_ms.append(
                        max(0.0, (received_at - created_at.timestamp()) * 1000)
                    )
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
    return latencies_ms, received_bytes, resyncs


async def _run_case(
    root: Path, workload: str, subscribed: bool, total_bytes: int,
    output_limit: int,
) -> dict[str, Any]:
    from cyrene.terminal.manager import TerminalManager

    case_root = root / f"{workload}-{'subscribed' if subscribed else 'unsubscribed'}"
    case_root.mkdir(parents=True)
    gate = case_root / "start.gate"
    manager = TerminalManager(output_limit=output_limit, state_dir=case_root / "state")
    terminal = await manager.create_resolved(
        "benchmark",
        cwd=str(case_root),
        shell="python",
        argv=[sys.executable, "-u", "-c", _CHILD_PROGRAM, workload, str(total_bytes), str(gate)],
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
    try:
        if subscribed:
            websocket_latencies, websocket_bytes, resyncs = await _websocket_relay(
                manager, terminal_id, gate
            )
        else:
            gate.touch()
        await _wait_for_exit(manager, terminal_id)
        manager.flush()
        screen = await manager.screen_snapshot_async(terminal_id)
        matches = await manager.search_history_async(
            "benchmark", "cyrene_benchmark_command_output", terminal_id=terminal_id
        )
        commands = await manager.commands_async(terminal_id)
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
        actual_bytes = session.next_seq
        return {
            "workload": workload,
            "subscribed": subscribed,
            "ptyBackend": "conpty" if sys.platform == "win32" else "posix_pty",
            "requestedBytes": total_bytes,
            "actualPtyBytes": actual_bytes,
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
                worker_writes / max(actual_bytes, 1), 4
            ),
            "retainedSegmentBytes": segment_bytes,
            "segmentsDeleted": worker_metrics["segmentsDeleted"] - initial_worker["segmentsDeleted"],
            "sqliteOutputEventRows": event_rows,
            "screenBytesParsed": parsed_bytes,
            "webSocketLatencyP95Ms": round(_percentile(websocket_latencies, 0.95), 3),
            "webSocketLatencyMaxMs": round(max(websocket_latencies, default=0.0), 3),
            "webSocketBytes": websocket_bytes,
            "webSocketResyncs": resyncs,
            "qualityPreserved": bool(
                matches
                and commands
                and "CYRENE_BENCHMARK_COMMAND_OUTPUT" in screen["screenText"]
                and parsed_bytes == actual_bytes
                and (not subscribed or (websocket_bytes == actual_bytes and resyncs == 0))
            ),
        }
    finally:
        heartbeat_stop.set()
        await heartbeat_task
        if terminal_id in manager._sessions:
            await manager.close(terminal_id, remove=True)
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
    return {
        "schemaVersion": 1,
        "platform": sys.platform,
        "python": sys.version.split()[0],
        "totalBytesPerCase": total_bytes,
        "outputLimit": output_limit,
        "cases": cases,
        "qualityPreserved": all(case["qualityPreserved"] for case in cases),
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
    return "\n".join(lines) + "\n"


async def _main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bytes", type=int, default=24 * 1024 * 1024)
    parser.add_argument("--output-limit", type=int, default=16 * 1024 * 1024)
    parser.add_argument("--json", type=Path)
    arguments = parser.parse_args()
    report = await run_benchmark(
        total_bytes=arguments.bytes, output_limit=arguments.output_limit
    )
    if arguments.json:
        arguments.json.parent.mkdir(parents=True, exist_ok=True)
        arguments.json.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(render_markdown(report))


if __name__ == "__main__":
    asyncio.run(_main())


__all__ = ["WORKLOADS", "render_markdown", "run_benchmark"]
