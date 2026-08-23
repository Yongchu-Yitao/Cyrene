"""Deterministic latency benchmark for the SimpleXNG pipeline.

The fixture replaces network boundaries with stable delayed functions and
measures the end-to-end search + fetch pipeline with byte-identical evidence
output. The pipeline performs no internal model calls.

Run with::

    uv run python -m cyrene.observability.simplexng_performance_benchmark
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import statistics
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from cyrene.observability.benchmark_cache import (
    IdealPrefixCacheTracker,
    ideal_cache_percent,
    ideal_cache_progression,
)


_SEARCH_DELAY_SECONDS = 0.010
_FETCH_DELAY_SECONDS = 0.060

_RESULTS = tuple(
    {
        "title": f"Fixture source {index}",
        "url": f"https://example.test/source-{index}",
        "snippet": f"Stable snippet {index}",
        "query": "fixture query",
    }
    for index in range(1, 6)
)


@dataclass(frozen=True, slots=True)
class SearchBenchmarkWorkload:
    parallel_sessions: int = 6
    rounds_per_session: int = 3


DEFAULT_WORKLOAD = SearchBenchmarkWorkload()


async def _run_once(workload: SearchBenchmarkWorkload) -> dict[str, Any]:
    from cyrene.tooling.backends import search

    originals = {
        "_search_simplexng": search._search_simplexng,
        "_fetch_url": search._fetch_url,
    }

    async def fake_search(_query: str) -> list[dict]:
        await asyncio.sleep(_SEARCH_DELAY_SECONDS)
        return [dict(item) for item in _RESULTS]

    async def fake_fetch(url: str, session: Any = None) -> str:
        await asyncio.sleep(_FETCH_DELAY_SECONDS)
        return f"Stable body for {url.rsplit('-', 1)[-1]}"

    parallel_sessions = max(2, int(workload.parallel_sessions))
    rounds_per_session = max(2, int(workload.rounds_per_session))
    cache_tracker = IdealPrefixCacheTracker(series_dimension="round")

    async def run_session(index: int) -> list[str]:
        outputs = []
        scope = f"search_session_{index}"
        for round_index in range(rounds_per_session):
            cache_tracker.record(
                scope,
                ["search:fixture query"],
                series_index=round_index,
            )
            for item in _RESULTS:
                cache_tracker.record(
                    scope,
                    [f"fetch:{item['url']}"],
                    series_index=round_index,
                )
            outputs.append(await search._deep_search_simplexng("fixture query"))
        return outputs

    started = time.perf_counter()
    try:
        search._search_simplexng = fake_search
        search._fetch_url = fake_fetch
        session_outputs = await asyncio.gather(*(
            run_session(index) for index in range(parallel_sessions)
        ))
    finally:
        for name, original in originals.items():
            setattr(search, name, original)

    return {
        "wall_ms": (time.perf_counter() - started) * 1000,
        "outputs": [output for session in session_outputs for output in session],
        "ideal_cache": cache_tracker.metrics(),
    }


async def run_benchmark(
    *,
    repeats: int = 5,
    workload: SearchBenchmarkWorkload = DEFAULT_WORKLOAD,
) -> dict[str, Any]:
    """Run the deterministic pipeline and enforce the quality contract."""
    repeats = max(1, int(repeats))
    samples = [await _run_once(workload) for _ in range(repeats)]

    reference_output = samples[0]["outputs"][0]
    output_identical = all(
        output == reference_output
        for sample in samples
        for output in sample["outputs"]
    )
    quality_preserved = output_identical

    return {
        "schema_version": 2,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "fixture": {
            "network_access": False,
            "real_model_calls": False,
            "result_count": len(_RESULTS),
            "fetched_count": len(_RESULTS),
            "delays_ms": {
                "search": int(_SEARCH_DELAY_SECONDS * 1000),
                "fetch": int(_FETCH_DELAY_SECONDS * 1000),
            },
        },
        "workload": {
            "parallel_sessions": max(2, int(workload.parallel_sessions)),
            "rounds_per_session": max(2, int(workload.rounds_per_session)),
        },
        "repeats": repeats,
        "median_ms": round(statistics.median(sample["wall_ms"] for sample in samples), 3),
        "ideal_cache": samples[-1]["ideal_cache"],
        "quality": {
            "preserved": quality_preserved,
            "output_byte_identical": output_identical,
            "expected_outputs": (
                max(2, int(workload.parallel_sessions))
                * max(2, int(workload.rounds_per_session))
            ),
            "actual_outputs": len(samples[-1]["outputs"]),
            "output_sha256": hashlib.sha256(reference_output.encode("utf-8")).hexdigest(),
        },
    }


def render_markdown(report: dict[str, Any]) -> str:
    quality = report["quality"]
    return "\n".join(
        [
            "# SimpleXNG latency benchmark",
            "",
            f"Generated: {report['generated_at']}",
            "",
            "| Metric | Result |",
            "|---|---:|",
            f"| Parallel sessions | {report['workload']['parallel_sessions']} |",
            f"| Rounds per session | {report['workload']['rounds_per_session']} |",
            f"| Median pipeline time | {report['median_ms']:.3f} ms |",
            f"| Cache progression | {ideal_cache_progression(report['ideal_cache'])} |",
            f"| Ideal cache hit rate | {ideal_cache_percent(report['ideal_cache']):.2f}% |",
            f"| Output byte-identical | {quality['output_byte_identical']} |",
            f"| Quality contract preserved | {quality['preserved']} |",
            "",
            "This is a deterministic orchestration benchmark. It performs no network or real model calls.",
            "Ideal cache rate is theoretical fixture reuse; no runtime or model cache is read.",
            "",
        ]
    )


async def _main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repeats", type=int, default=5)
    parser.add_argument("--output-dir", default="output/performance")
    args = parser.parse_args()

    report = await run_benchmark(repeats=args.repeats)
    if not report["quality"]["preserved"]:
        raise SystemExit("SimpleXNG quality contract failed")

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / "simplexng-performance.json"
    markdown_path = output_dir / "simplexng-performance.md"
    json_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    markdown_path.write_text(render_markdown(report), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    print(f"Wrote {json_path} and {markdown_path}")


if __name__ == "__main__":
    asyncio.run(_main())
