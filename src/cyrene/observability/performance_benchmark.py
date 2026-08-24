"""Deterministic agent-loop benchmark for performance refactors.

Run with::

    uv run python -m cyrene.observability.performance_benchmark

The benchmark patches only the in-process agent seams, uses no credentials or
network access, and restores every patched object before returning.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import platform
import statistics
import sys
import time
from contextvars import ContextVar
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

try:
    import resource
except ImportError:  # pragma: no cover - Windows fallback
    resource = None

from cyrene.agent.context import bind_run_context
from cyrene.observability.benchmark_cache import (
    IdealPrefixCacheTracker,
    aggregate_ideal_cache_metrics,
    ideal_cache_percent,
    ideal_cache_progression,
)
from cyrene.observability.trace import bind_trace_context, bind_trace_sink, trace_span


@dataclass(frozen=True, slots=True)
class BenchmarkScenario:
    name: str
    tool_names: tuple[str, ...] = ()
    history_chars: int = 0
    tool_result_chars: int = 256
    background_contention: bool = False


@dataclass(frozen=True, slots=True)
class AgentBenchmarkWorkload:
    parallel_sessions: int = 4
    turns_per_session: int = 3


DEFAULT_SCENARIOS = (
    BenchmarkScenario("pure_chat"),
    BenchmarkScenario("single_tool", ("Read",)),
    BenchmarkScenario("multi_tool", ("Read", "Grep", "Bash")),
    BenchmarkScenario("web_search", ("WebSearch",)),
    BenchmarkScenario("search_and_fetch", ("WebSearch", "WebFetch")),
    BenchmarkScenario("long_history", history_chars=120_000),
    BenchmarkScenario("large_tool_result", ("Read",), tool_result_chars=500_000),
    BenchmarkScenario("background_contention", ("Read",), background_contention=True),
)
DEFAULT_WORKLOAD = AgentBenchmarkWorkload()


def _rss_bytes() -> int:
    if resource is None:
        return 0
    value = int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
    return value if sys.platform == "darwin" else value * 1024


def _tool_call(call_id: str, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": call_id,
        "function": {"name": name, "arguments": json.dumps(arguments)},
    }


def _arguments_for(name: str) -> dict[str, Any]:
    if name == "Read":
        return {"file_path": "/benchmark/fixture.txt"}
    if name == "Grep":
        return {"pattern": "benchmark", "path": "/benchmark"}
    if name == "Bash":
        return {"command": "benchmark-noop"}
    if name == "WebSearch":
        return {"query": "deterministic benchmark query"}
    if name == "WebFetch":
        return {"url": "https://benchmark.invalid/source"}
    return {}


def _prompt_token_estimate(messages: list[dict], tools: list | None) -> int:
    """Stable benchmark-only estimate without runtime-private dependencies."""
    serialized = json.dumps(
        {"messages": messages, "tools": tools or []},
        ensure_ascii=False,
        separators=(",", ":"),
        default=str,
    )
    return max(1, (len(serialized) + 3) // 4)


async def _run_scenario(
    scenario: BenchmarkScenario,
    workload: AgentBenchmarkWorkload,
) -> dict[str, Any]:
    from cyrene.agent import agent as agent_core
    from cyrene.agent import state as agent_state
    from cyrene.agent.lane_protocol import current_agent_lane
    from cyrene.model_runtime.transcript_policy import (
        ProviderFamily,
        TranscriptPolicy,
    )

    parallel_sessions = max(2, int(workload.parallel_sessions))
    turns_per_session = max(2, int(workload.turns_per_session))
    model_calls = 0
    tool_calls = 0
    event_count = 0
    prompt_tokens_total = 0
    max_prompt_tokens = 0
    trace_events: list[dict[str, Any]] = []
    background_ticks = 0
    background_stop = asyncio.Event()
    cache_tracker = IdealPrefixCacheTracker(series_dimension="turn")
    lane_cache_tracker = IdealPrefixCacheTracker(series_dimension="turn")
    model_round_index: ContextVar[int] = ContextVar(
        "benchmark_model_round_index",
        default=0,
    )
    cache_scope: ContextVar[str] = ContextVar(
        "benchmark_cache_scope",
        default="benchmark",
    )
    cache_turn: ContextVar[int] = ContextVar(
        "benchmark_cache_turn",
        default=0,
    )

    originals = {
        "_call_llm": agent_core._call_llm,
        "_execute_tool": agent_core._execute_tool,
        "_save_session_messages": agent_core._save_session_messages,
        "_append_session_message": agent_core._append_session_message,
        "append_or_upsert_lane_record": agent_core.append_or_upsert_lane_record,
        "_publish_runtime_event": agent_core._publish_runtime_event,
    }

    async def fake_call_llm(messages, tools=None, **_kwargs):
        nonlocal model_calls, prompt_tokens_total, max_prompt_tokens
        current_round = model_round_index.get() + 1
        model_round_index.set(current_round)
        model_calls += 1
        prompt_tokens = _prompt_token_estimate(messages, tools)
        prompt_tokens_total += prompt_tokens
        max_prompt_tokens = max(max_prompt_tokens, prompt_tokens)
        request_units = [
            *(
                json.dumps(
                    {
                        "role": message.get("role"),
                        "content": message.get("content"),
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                    default=str,
                )
                for message in messages
            ),
            "tools:"
            + json.dumps(
                tools or [],
                ensure_ascii=False,
                sort_keys=True,
                default=str,
            ),
        ]
        session_scope = cache_scope.get()
        cache_tracker.record(
            session_scope,
            request_units,
            series_index=cache_turn.get(),
        )
        lane_cache_tracker.record(
            f"{session_scope}:{str(_kwargs.get('cache_lane') or current_agent_lane())}",
            request_units,
            series_index=cache_turn.get(),
        )
        async with trace_span(
            "model",
            "benchmark.fake_model",
            span_id=f"model_{uuid4().hex}",
            attributes={"prompt_tokens": prompt_tokens},
        ):
            await asyncio.sleep(0)
        if not scenario.tool_names:
            return {
                "content": "Deterministic benchmark reply.",
                "tool_calls": [_tool_call("quit_1", "quit", {})],
            }
        if current_round == 1:
            return {
                "content": "",
                "tool_calls": [
                    _tool_call(
                        "use_tools_1",
                        "use_tools",
                        {
                            "execution_brief": (
                                "Intent: run the deterministic fixture. "
                                "First: execute its initial tool."
                            )
                        },
                    )
                ],
            }
        if current_round == 2:
            return {
                "content": "",
                "tool_calls": [
                    _tool_call(f"tool_{index}", name, _arguments_for(name))
                    for index, name in enumerate(scenario.tool_names, start=1)
                ],
            }
        return {
            "content": "Deterministic benchmark tool result.",
            "tool_calls": [_tool_call("quit_2", "quit", {})],
        }

    async def fake_execute_tool(name, _arguments, _bot, _chat_id, _db_path, _notify):
        nonlocal tool_calls
        tool_calls += 1
        async with trace_span(
            "tool",
            str(name),
            span_id=f"tool_{uuid4().hex}",
            attributes={"result_chars": scenario.tool_result_chars},
        ):
            await asyncio.sleep(0)
        return "x" * scenario.tool_result_chars

    async def fake_save(*_args, **_kwargs):
        return None

    async def fake_append(*_args, **_kwargs):
        return None

    async def fake_publish(_event):
        nonlocal event_count
        event_count += 1

    async def background_load() -> None:
        nonlocal background_ticks
        while not background_stop.is_set():
            background_ticks += 1
            await asyncio.sleep(0)

    def initial_history() -> list[dict[str, Any]]:
        if not scenario.history_chars:
            return []
        chunk = "history fixture " * 128
        text = (chunk * ((scenario.history_chars // len(chunk)) + 1))[
            : scenario.history_chars
        ]
        return [{"role": "user", "content": text}]

    async def run_session(index: int) -> None:
        session_id = f"session_{scenario.name}_{index}"
        history = initial_history()
        lease = agent_state.RunModelLease(
            f"benchmark-lease-{scenario.name}-{index}",
            {"primary": ()},
            provider_family=ProviderFamily.OPENAI_COMPATIBLE,
            transcript_policy=TranscriptPolicy.DUAL_LANE,
        )
        lease_token = agent_state._run_model_lease.set(lease)
        try:
            for turn in range(turns_per_session):
                run_id = f"bench_{scenario.name}_{index}_{turn}_{uuid4().hex}"
                round_id = f"round_{scenario.name}_{index}_{turn}"
                user_message = (
                    "Run deterministic benchmark fixture "
                    f"for conversation turn {turn + 1}."
                )
                model_token = model_round_index.set(0)
                cache_token = cache_scope.set(session_id)
                turn_token = cache_turn.set(turn)
                try:
                    with bind_run_context(session_id=session_id, round_id=round_id):
                        with bind_trace_context(
                            trace_id=run_id,
                            run_id=run_id,
                            session_id=session_id,
                            round_id=round_id,
                        ):
                            with bind_trace_sink(trace_events.append):
                                async with trace_span(
                                    "run",
                                    "benchmark_scenario",
                                    span_id=run_id,
                                ):
                                    response = await agent_core._run_main_agent(
                                        user_message,
                                        history,
                                        None,
                                        index,
                                        "",
                                        persist_user_message=False,
                                    )
                finally:
                    cache_scope.reset(cache_token)
                    cache_turn.reset(turn_token)
                    model_round_index.reset(model_token)
                history.extend([
                    {"role": "user", "content": user_message},
                    {"role": "assistant", "content": str(response)},
                ])
        finally:
            agent_state._run_model_lease.reset(lease_token)

    rss_before = _rss_bytes()
    started = time.perf_counter()
    background_task: asyncio.Task[None] | None = None
    try:
        agent_core._call_llm = fake_call_llm
        agent_core._execute_tool = fake_execute_tool
        agent_core._save_session_messages = fake_save
        agent_core._append_session_message = fake_append
        agent_core.append_or_upsert_lane_record = fake_append
        agent_core._publish_runtime_event = fake_publish
        if scenario.background_contention:
            background_task = asyncio.create_task(background_load())
        await asyncio.gather(*(run_session(index) for index in range(parallel_sessions)))
    finally:
        background_stop.set()
        if background_task is not None:
            await background_task
        for name, original in originals.items():
            setattr(agent_core, name, original)

    wall_ms = (time.perf_counter() - started) * 1000
    rss_after = _rss_bytes()
    return {
        "scenario": scenario.name,
        "parallel_sessions": parallel_sessions,
        "turns_per_session": turns_per_session,
        "conversation_turns": parallel_sessions * turns_per_session,
        "wall_ms": round(wall_ms, 3),
        "model_rounds": model_calls,
        "tool_calls": tool_calls,
        "prompt_tokens_total": prompt_tokens_total,
        "max_prompt_tokens": max_prompt_tokens,
        "event_count": event_count,
        "rss_delta_bytes": max(0, rss_after - rss_before),
        "trace_span_count": len(trace_events),
        "background_ticks": background_ticks,
        "ideal_cache": cache_tracker.metrics(),
        "ideal_lane_cache": lane_cache_tracker.metrics(),
    }


async def run_benchmark(
    *,
    repeats: int = 1,
    scenarios: tuple[BenchmarkScenario, ...] = DEFAULT_SCENARIOS,
    workload: AgentBenchmarkWorkload = DEFAULT_WORKLOAD,
) -> dict[str, Any]:
    """Run fixtures sequentially and return stable counters plus timing samples."""
    repeats = max(1, int(repeats))
    results = []
    for scenario in scenarios:
        samples = [
            await _run_scenario(scenario, workload) for _ in range(repeats)
        ]
        combined = dict(samples[-1])
        combined["wall_ms"] = round(
            statistics.median(sample["wall_ms"] for sample in samples), 3
        )
        combined["rss_delta_bytes"] = max(
            sample["rss_delta_bytes"] for sample in samples
        )
        combined["samples"] = [sample["wall_ms"] for sample in samples]
        results.append(combined)
    return {
        "schema_version": 3,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "environment": {
            "python": platform.python_version(),
            "platform": platform.platform(),
            "repeats": repeats,
            "network_access": False,
            "real_credentials": False,
            "real_llm_calls": False,
        },
        "fixtures": [asdict(scenario) for scenario in scenarios],
        "workload": {
            "parallel_sessions": max(2, int(workload.parallel_sessions)),
            "turns_per_session": max(2, int(workload.turns_per_session)),
        },
        "results": results,
        "ideal_cache": aggregate_ideal_cache_metrics(
            item["ideal_cache"] for item in results
        ),
        "ideal_lane_cache": aggregate_ideal_cache_metrics(
            item["ideal_lane_cache"] for item in results
        ),
    }


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Cyrene Agent Performance Benchmark",
        "",
        f"Generated: `{report['generated_at']}`",
        "",
        "| Scenario | Parallel | Turns | Wall ms | Model rounds | Tools | Prompt tokens | Max prompt | Events | RSS delta | Spans | Cache progression | Ideal cache | Lane cache |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|---:|---:|",
    ]
    for item in report["results"]:
        lines.append(
            "| {scenario} | {parallel_sessions} | {turns_per_session} | "
            "{wall_ms:.3f} | {model_rounds} | {tool_calls} | "
            "{prompt_tokens_total} | {max_prompt_tokens} | {event_count} | "
            "{rss_delta_bytes} | {trace_span_count} | {cache_progression} | "
            "{ideal_cache_rate:.2f}% | {ideal_lane_cache_rate:.2f}% |".format(
                **item,
                cache_progression=ideal_cache_progression(item["ideal_cache"]),
                ideal_cache_rate=ideal_cache_percent(item["ideal_cache"]),
                ideal_lane_cache_rate=ideal_cache_percent(item["ideal_lane_cache"]),
            )
        )
    lines.extend([
        "",
        "> The fixtures use deterministic fake models/tools and do not access the network or real credentials.",
        "> Ideal cache rate is theoretical fixture reuse only; it does not query a runtime cache or LLM provider.",
        "",
    ])
    return "\n".join(lines)


async def write_report(output_dir: Path, *, repeats: int = 1) -> tuple[Path, Path]:
    report = await run_benchmark(repeats=repeats)
    output_dir.mkdir(parents=True, exist_ok=True)
    stem = f"agent-performance-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
    json_path = output_dir / f"{stem}.json"
    markdown_path = output_dir / f"{stem}.md"
    json_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    markdown_path.write_text(render_markdown(report), encoding="utf-8")
    return json_path, markdown_path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", default="output/performance")
    parser.add_argument("--repeats", type=int, default=1)
    args = parser.parse_args()
    json_path, markdown_path = asyncio.run(
        write_report(Path(args.output_dir), repeats=args.repeats)
    )
    print(json_path)
    print(markdown_path)


if __name__ == "__main__":
    main()
