"""Deterministic non-LLM benchmark for the Workbench chat pipeline.

The fixture exercises the real chat run manager, durable event log, tool inbox,
tool concurrency scheduler, NDJSON stream and terminal cleanup.  Model output
and tool results are prebuilt, so timing contains no network or LLM latency.

Run with::

    uv run python -m cyrene.observability.chat_pipeline_benchmark
"""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import platform
import sqlite3
import statistics
import sys
import tempfile
import threading
import time
from collections import Counter
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from cyrene.observability.benchmark_cache import (
    IdealPrefixCacheTracker,
    aggregate_ideal_cache_metrics,
    ideal_cache_percent,
    ideal_cache_progression,
)

try:
    import resource
except ImportError:  # pragma: no cover - Windows fallback
    resource = None


@dataclass(frozen=True, slots=True)
class MockToolCall:
    name: str
    arguments: dict[str, Any]
    result: str
    read_only: bool = True


@dataclass(frozen=True, slots=True)
class MockTask:
    prompt: str
    reply: str
    tools: tuple[MockToolCall, ...]


@dataclass(frozen=True, slots=True)
class BenchmarkProfile:
    name: str
    concurrency: int
    tools_per_run: int
    turns_per_session: int = 3
    progress_events_per_tool: int = 2
    reply_chunks: int = 24
    tool_result_chars: int = 512


DEFAULT_PROFILES = (
    BenchmarkProfile("single_heavy_run", concurrency=2, tools_per_run=32),
    BenchmarkProfile("dense_concurrency", concurrency=24, tools_per_run=12),
    BenchmarkProfile("multi_tool_storm", concurrency=12, tools_per_run=40),
)


def _rss_bytes() -> int:
    if resource is None:
        return 0
    value = int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
    return value if sys.platform == "darwin" else value * 1024


def _percentile(values: list[float], percentile: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, math.ceil(percentile * len(ordered)) - 1))
    return ordered[index]


def build_mock_task(profile: BenchmarkProfile) -> MockTask:
    """Build the fixed model reply and tool calls used by one profile."""
    result = "x" * max(1, int(profile.tool_result_chars))
    tools = tuple(
        MockToolCall(
            name=("Read", "Grep", "ListFiles", "Inspect")[index % 4],
            arguments={"fixture": index, "path": f"/benchmark/item-{index}"},
            result=result,
        )
        for index in range(max(0, int(profile.tools_per_run)))
    )
    return MockTask(
        prompt="Execute the deterministic Cyrene performance task.",
        reply="Deterministic Cyrene benchmark reply. " * 32,
        tools=tools,
    )


def _reply_chunks(text: str, count: int) -> list[str]:
    count = max(1, int(count))
    width = max(1, math.ceil(len(text) / count))
    return [text[index : index + width] for index in range(0, len(text), width)]


async def _event_loop_lag(stop: asyncio.Event, samples: list[float]) -> None:
    interval = 0.002
    target = time.perf_counter() + interval
    while not stop.is_set():
        await asyncio.sleep(max(0.0, target - time.perf_counter()))
        now = time.perf_counter()
        samples.append(max(0.0, (now - target) * 1000))
        target = now + interval


@dataclass(slots=True)
class _ProfileMetrics:
    stream_event_counts: dict[str, int]
    run_latencies: list[float]
    first_event_latencies: list[float]
    failures: list[str]
    lag_samples: list[float]
    counter_lock: threading.Lock
    inbox_connections: int = 0
    inbox_telemetry_batch_sizes: list[int] = field(default_factory=list)
    event_store_connections: int = 0
    durable_event_batch_sizes: list[int] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class _ProfileInstrumentation:
    inbox_module: Any
    original_inbox_connect: Any
    original_inbox_record_events: Any
    event_store: Any
    original_event_store_connect: Any
    original_append_many: Any

    def restore(self) -> None:
        self.inbox_module.WorkbenchAgentInbox._connect = self.original_inbox_connect
        self.inbox_module.WorkbenchAgentInbox._record_events = self.original_inbox_record_events
        if self.event_store is not None and self.original_event_store_connect is not None and self.original_append_many is not None:
            self.event_store._connect = self.original_event_store_connect
            self.event_store.append_many = self.original_append_many


def _new_profile_metrics() -> _ProfileMetrics:
    return _ProfileMetrics(
        stream_event_counts={},
        run_latencies=[],
        first_event_latencies=[],
        failures=[],
        lag_samples=[],
        counter_lock=threading.Lock(),
    )


def _install_profile_instrumentation(
    manager: Any,
    metrics: _ProfileMetrics,
    inbox_module: Any,
) -> _ProfileInstrumentation:
    original_inbox_connect = inbox_module.WorkbenchAgentInbox._connect
    original_inbox_record_events = inbox_module.WorkbenchAgentInbox._record_events
    event_store = manager._event_store
    original_event_store_connect = event_store._connect if event_store is not None else None
    original_append_many = event_store.append_many if event_store is not None else None
    def tracked_inbox_connect(self: Any) -> sqlite3.Connection:
        with metrics.counter_lock:
            metrics.inbox_connections += 1
        return original_inbox_connect(self)

    inbox_module.WorkbenchAgentInbox._connect = tracked_inbox_connect

    def tracked_inbox_record_events(self: Any, rows: list[tuple[Any, ...]]) -> None:
        with metrics.counter_lock:
            metrics.inbox_telemetry_batch_sizes.append(len(rows))
        original_inbox_record_events(self, rows)

    inbox_module.WorkbenchAgentInbox._record_events = tracked_inbox_record_events
    if event_store is not None and original_event_store_connect is not None and original_append_many is not None:

        def tracked_event_store_connect() -> sqlite3.Connection:
            with metrics.counter_lock:
                metrics.event_store_connections += 1
            return original_event_store_connect()

        def tracked_append_many(run_id: str, events: Any) -> None:
            with metrics.counter_lock:
                metrics.durable_event_batch_sizes.append(len(events))
            original_append_many(run_id, events)

        event_store._connect = tracked_event_store_connect
        event_store.append_many = tracked_append_many

    return _ProfileInstrumentation(
        inbox_module=inbox_module,
        original_inbox_connect=original_inbox_connect,
        original_inbox_record_events=original_inbox_record_events,
        event_store=event_store,
        original_event_store_connect=original_event_store_connect,
        original_append_many=original_append_many,
    )


def _build_tool_batch(
    profile: BenchmarkProfile,
    fixture: MockTask,
    run: Any,
    chat_id: str,
    turn: int,
) -> list[tuple[Any, ...]]:
    calls = []
    for tool_index, tool in enumerate(fixture.tools):
        tool_call_id = f"{chat_id}_turn_{turn}_tool_{tool_index}"

        async def execute_tool(
            call: MockToolCall = tool,
            call_id: str = tool_call_id,
        ) -> str:
            await run.publish(
                {
                    "type": "tool_call_started",
                    "tool_call_id": call_id,
                    "tool": call.name,
                    "args": call.arguments,
                }
            )
            for progress_index in range(profile.progress_events_per_tool):
                await run.publish(
                    {
                        "type": "tool_call_progress",
                        "tool_call_id": call_id,
                        "tool": call.name,
                        "current": progress_index + 1,
                        "total": profile.progress_events_per_tool,
                    }
                )
                await asyncio.sleep(0)
            await run.publish(
                {
                    "type": "tool_call_finished",
                    "tool_call_id": call_id,
                    "tool": call.name,
                    "status": "completed",
                }
            )
            return call.result

        calls.append(
            (
                tool_call_id,
                tool.name,
                execute_tool,
                {
                    "read_only": tool.read_only,
                    "requires_order": not tool.read_only,
                    "resource_keys": [f"fixture:{tool_index}"],
                    "arguments": tool.arguments,
                },
            )
        )
    return calls


async def _run_scripted_agent(
    profile: BenchmarkProfile,
    fixture: MockTask,
    run: Any,
    chat_id: str,
    turn: int,
) -> None:
    await run.publish({"type": "reasoning_delta", "delta": "scripted"})
    calls = _build_tool_batch(profile, fixture, run, chat_id, turn)
    run.inbox.submit_tool_batch(calls, batch_id=f"batch_{chat_id}_{turn}")
    for tool_index in range(len(fixture.tools)):
        await run.inbox.wait_for_tool_result(
            f"{chat_id}_turn_{turn}_tool_{tool_index}"
        )
    await run.inbox.wait_for_active_tools()
    await run.publish({"type": "reply_start"})
    for chunk in _reply_chunks(fixture.reply, profile.reply_chunks):
        await run.publish({"type": "reply_delta", "delta": chunk})
    await run.publish({"type": "reply_done", "response": fixture.reply})
    await run.publish(
        {
            "type": "saved",
            "assistantMessage": {"role": "assistant", "content": fixture.reply},
        }
    )
    run.outcome = {"kind": "reply"}


async def _execute_profile_run(
    profile: BenchmarkProfile,
    fixture: MockTask,
    manager: Any,
    metrics: _ProfileMetrics,
    cache_tracker: IdealPrefixCacheTracker,
    index: int,
) -> None:
    chat_id = f"benchmark_chat_{index}"
    history_segments = ["system:cyrene deterministic chat benchmark"]
    for turn in range(max(2, int(profile.turns_per_session))):
        started = time.perf_counter()
        first_event_at = 0.0
        user_prompt = f"{fixture.prompt} Conversation turn {turn + 1}."
        cache_tracker.record(
            chat_id,
            [*history_segments, f"user:{user_prompt}"],
            series_index=turn,
        )

        async def runner(run: Any, current_turn: int = turn) -> None:
            await _run_scripted_agent(
                profile,
                fixture,
                run,
                chat_id,
                current_turn,
            )

        run, is_new = manager.start_or_get(
            chat_id,
            {
                "type": "ack",
                "chatId": chat_id,
                "clientRequestId": f"request_{index}_{turn}",
            },
            runner,
            stream=True,
        )
        if not is_new:
            raise RuntimeError(f"benchmark run collision: {chat_id} turn {turn}")

        count = 0
        async for line in manager.stream(run):
            event = json.loads(line)
            if not first_event_at:
                first_event_at = time.perf_counter()
            if event.get("type"):
                count += 1
        metrics.stream_event_counts[f"{chat_id}:{turn}"] = count

        completed = time.perf_counter()
        if run.status != "done":
            outcome = run.outcome if isinstance(run.outcome, dict) else {}
            error = outcome.get("exc")
            metrics.failures.append(
                f"{type(error).__name__}: {error}"
                if error
                else str(run.termination_reason or run.status)
            )
        metrics.run_latencies.append((completed - started) * 1000)
        metrics.first_event_latencies.append(
            ((first_event_at or completed) - started) * 1000
        )
        history_segments.extend([
            f"user:{user_prompt}",
            f"assistant:{fixture.reply}",
        ])


def _read_durable_counts(db_path: Path) -> dict[str, int]:
    with sqlite3.connect(db_path) as conn:
        return {
            "runs": int(conn.execute("SELECT COUNT(*) FROM workbench_chat_runs").fetchone()[0]),
            "events": int(conn.execute("SELECT COUNT(*) FROM workbench_chat_run_events").fetchone()[0]),
            "tool_results": int(conn.execute("SELECT COUNT(*) FROM workbench_agent_inbox WHERE event_type='tool_result'").fetchone()[0]),
            "telemetry_events": int(conn.execute("SELECT COUNT(*) FROM workbench_agent_run_events").fetchone()[0]),
        }


def _profile_result(
    profile: BenchmarkProfile,
    fixture: MockTask,
    metrics: _ProfileMetrics,
    counts: dict[str, int],
    *,
    wall_ms: float,
    rss_before: int,
    ideal_cache: dict[str, Any],
) -> dict[str, Any]:
    parallel_sessions = max(2, int(profile.concurrency))
    turns_per_session = max(2, int(profile.turns_per_session))
    run_count = parallel_sessions * turns_per_session
    expected_per_run = 1 + 1 + len(fixture.tools) * (2 + profile.progress_events_per_tool) + 1 + len(_reply_chunks(fixture.reply, profile.reply_chunks)) + 1 + 1
    total_stream_events = sum(metrics.stream_event_counts.values())
    expected_total_events = expected_per_run * run_count
    expected_tool_results = len(fixture.tools) * run_count
    expected_telemetry_events = (len(fixture.tools) * 4 + 1) * run_count
    quality = {
        "preserved": (
            not metrics.failures
            and counts["runs"] == run_count
            and counts["events"] == expected_total_events
            and total_stream_events == expected_total_events
            and counts["tool_results"] == expected_tool_results
            and counts["telemetry_events"] == expected_telemetry_events
        ),
        "expected_events": expected_total_events,
        "stream_events": total_stream_events,
        "durable_events": counts["events"],
        "expected_tool_results": expected_tool_results,
        "durable_tool_results": counts["tool_results"],
        "expected_telemetry_events": expected_telemetry_events,
        "durable_telemetry_events": counts["telemetry_events"],
        "failed_runs": len(metrics.failures),
    }
    batch_sizes = metrics.durable_event_batch_sizes
    telemetry_batch_sizes = metrics.inbox_telemetry_batch_sizes
    return {
        "profile": profile.name,
        "parallel_sessions": parallel_sessions,
        "turns_per_session": turns_per_session,
        "conversation_turns": run_count,
        "wall_ms": round(wall_ms, 3),
        "run_latency_p50_ms": round(statistics.median(metrics.run_latencies), 3),
        "run_latency_p95_ms": round(_percentile(metrics.run_latencies, 0.95), 3),
        "first_event_p95_ms": round(_percentile(metrics.first_event_latencies, 0.95), 3),
        "event_loop_lag_p95_ms": round(_percentile(metrics.lag_samples, 0.95), 3),
        "event_loop_lag_max_ms": round(max(metrics.lag_samples, default=0.0), 3),
        "events_per_second": round(total_stream_events / max(wall_ms / 1000, 0.000001), 1),
        "tools_per_second": round(expected_tool_results / max(wall_ms / 1000, 0.000001), 1),
        "rss_delta_bytes": max(0, _rss_bytes() - rss_before),
        "telemetry_events": counts["telemetry_events"],
        "sqlite_connections": {
            "inbox": metrics.inbox_connections,
            "event_store": metrics.event_store_connections,
        },
        "inbox_telemetry_batches": {
            "count": len(telemetry_batch_sizes),
            "median_size": round(statistics.median(telemetry_batch_sizes), 3) if telemetry_batch_sizes else 0,
            "max_size": max(telemetry_batch_sizes, default=0),
        },
        "durable_event_batches": {
            "count": len(batch_sizes),
            "median_size": round(statistics.median(batch_sizes), 3) if batch_sizes else 0,
            "max_size": max(batch_sizes, default=0),
        },
        "failure_counts": dict(Counter(metrics.failures)),
        "ideal_cache": ideal_cache,
        "quality": quality,
    }


async def _run_profile(profile: BenchmarkProfile, db_path: Path) -> dict[str, Any]:
    from cyrene.workbench.application import inbox as inbox_module
    from cyrene.workbench.chat import chat_runs

    fixture = build_mock_task(profile)
    manager = chat_runs.ChatRunManager(retention_seconds=0)
    manager.configure(str(db_path))
    metrics = _new_profile_metrics()
    cache_tracker = IdealPrefixCacheTracker(series_dimension="turn")
    lag_stop = asyncio.Event()
    instrumentation = _install_profile_instrumentation(
        manager,
        metrics,
        inbox_module,
    )

    rss_before = _rss_bytes()
    lag_task = asyncio.create_task(_event_loop_lag(lag_stop, metrics.lag_samples))
    started = time.perf_counter()
    try:
        await asyncio.gather(*(
            _execute_profile_run(
                profile,
                fixture,
                manager,
                metrics,
                cache_tracker,
                index,
            )
            for index in range(max(2, int(profile.concurrency)))
        ))
    finally:
        lag_stop.set()
        await lag_task
        instrumentation.restore()
    wall_ms = (time.perf_counter() - started) * 1000
    await manager.shutdown()

    counts = _read_durable_counts(db_path)
    return _profile_result(
        profile,
        fixture,
        metrics,
        counts,
        wall_ms=wall_ms,
        rss_before=rss_before,
        ideal_cache=cache_tracker.metrics(),
    )


async def run_benchmark(
    *,
    repeats: int = 1,
    profiles: tuple[BenchmarkProfile, ...] = DEFAULT_PROFILES,
) -> dict[str, Any]:
    """Run deterministic profiles and return median latency/throughput."""
    repeats = max(1, int(repeats))
    results = []
    with tempfile.TemporaryDirectory(prefix="cyrene-chat-benchmark-") as temporary:
        root = Path(temporary)
        for profile in profiles:
            samples = []
            for repeat in range(repeats):
                samples.append(await _run_profile(profile, root / f"{profile.name}-{repeat}.db"))
            combined = dict(samples[-1])
            for key in (
                "wall_ms",
                "run_latency_p50_ms",
                "run_latency_p95_ms",
                "first_event_p95_ms",
                "event_loop_lag_p95_ms",
                "event_loop_lag_max_ms",
                "events_per_second",
                "tools_per_second",
            ):
                combined[key] = round(statistics.median(float(sample[key]) for sample in samples), 3)
            combined["rss_delta_bytes"] = max(sample["rss_delta_bytes"] for sample in samples)
            combined["samples_ms"] = [sample["wall_ms"] for sample in samples]
            combined["quality"]["preserved"] = all(sample["quality"]["preserved"] for sample in samples)
            results.append(combined)
    return {
        "schema_version": 2,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "environment": {
            "python": platform.python_version(),
            "platform": platform.platform(),
            "repeats": repeats,
            "network_access": False,
            "real_credentials": False,
            "real_llm_calls": False,
        },
        "profiles": [asdict(profile) for profile in profiles],
        "results": results,
        "ideal_cache": aggregate_ideal_cache_metrics(
            item["ideal_cache"] for item in results
        ),
    }


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Cyrene non-LLM chat pipeline benchmark",
        "",
        f"Generated: `{report['generated_at']}`",
        "",
        "| Profile | Parallel | Turns | Wall ms | Run p50 | Run p95 | First event p95 | Loop lag p95 | Events/s | Tools/s | Cache progression | Ideal cache | Quality |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|---:|---|",
    ]
    for item in report["results"]:
        lines.append(
            "| {profile} | {parallel_sessions} | {turns_per_session} | "
            "{wall_ms:.3f} | {run_latency_p50_ms:.3f} | "
            "{run_latency_p95_ms:.3f} | {first_event_p95_ms:.3f} | "
            "{event_loop_lag_p95_ms:.3f} | {events_per_second:.1f} | "
            "{tools_per_second:.1f} | {cache_progression} | "
            "{ideal_cache_rate:.2f}% | {quality_label} |".format(
                **item,
                cache_progression=ideal_cache_progression(item["ideal_cache"]),
                ideal_cache_rate=ideal_cache_percent(item["ideal_cache"]),
                quality_label=("pass" if item["quality"]["preserved"] else "FAIL"),
            )
        )
    lines.extend(
        [
            "",
            "> The fixture uses real Cyrene orchestration/persistence with scripted replies and tools; it performs no network or LLM calls.",
            "> Ideal cache rate is theoretical fixture reuse; no runtime or model cache is read.",
            "",
        ]
    )
    return "\n".join(lines)


async def write_report(output_dir: Path, *, repeats: int = 1) -> tuple[Path, Path]:
    report = await run_benchmark(repeats=repeats)
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / "chat-pipeline-performance.json"
    markdown_path = output_dir / "chat-pipeline-performance.md"
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    markdown_path.write_text(render_markdown(report), encoding="utf-8")
    return json_path, markdown_path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", default="output/performance")
    parser.add_argument("--repeats", type=int, default=1)
    args = parser.parse_args()
    json_path, markdown_path = asyncio.run(write_report(Path(args.output_dir), repeats=args.repeats))
    print(json_path)
    print(markdown_path)


if __name__ == "__main__":
    main()
