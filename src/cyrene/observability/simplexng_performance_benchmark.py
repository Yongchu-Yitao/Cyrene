"""Deterministic latency and quality benchmark for the SimpleXNG pipeline.

The fixture replaces network and model boundaries with stable delayed functions.
It compares the previous serial fetch-then-filter schedule with the production
parallel schedule while requiring byte-identical evidence output and exactly one
internal model call for relevance filtering.

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
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


_SEARCH_DELAY_SECONDS = 0.010
_FETCH_DELAY_SECONDS = 0.060
_FILTER_DELAY_SECONDS = 0.040

_RESULTS = tuple(
    {
        "title": f"Fixture source {index}",
        "url": f"https://example.test/source-{index}",
        "snippet": f"Stable snippet {index}",
        "query": "fixture query",
    }
    for index in range(1, 6)
)


def _fingerprint(value: Any) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


async def _run_once(*, parallel_prepare: bool) -> dict[str, Any]:
    from cyrene.tooling.backends import search

    originals = {
        "_search_simplexng": search._search_simplexng,
        "_fetch_url": search._fetch_url,
        "_filter_results": search._filter_results,
    }
    filter_inputs: list[dict[str, Any]] = []

    async def fake_search(_query: str) -> list[dict]:
        await asyncio.sleep(_SEARCH_DELAY_SECONDS)
        return [dict(item) for item in _RESULTS]

    async def fake_fetch(url: str) -> str:
        await asyncio.sleep(_FETCH_DELAY_SECONDS)
        return f"Stable body for {url.rsplit('-', 1)[-1]}"

    async def fake_filter(raw_results: list[dict], topic: str) -> list[dict]:
        await asyncio.sleep(_FILTER_DELAY_SECONDS)
        assert topic == "fixture query"
        filter_inputs.append({
            "topic": topic,
            "sources": [dict(item) for item in raw_results],
        })
        return [raw_results[index] for index in (0, 2, 4)]

    started = time.perf_counter()
    try:
        search._search_simplexng = fake_search
        search._fetch_url = fake_fetch
        search._filter_results = fake_filter
        output = await search._deep_search_simplexng(
            "fixture query",
            _parallel_prepare=parallel_prepare,
        )
    finally:
        for name, original in originals.items():
            setattr(search, name, original)

    return {
        "wall_ms": (time.perf_counter() - started) * 1000,
        "output": output,
        "filter_input": filter_inputs[0],
        "internal_model_calls": len(filter_inputs),
    }


async def run_benchmark(*, repeats: int = 5) -> dict[str, Any]:
    """Compare old and new schedules and enforce the quality contract."""
    repeats = max(1, int(repeats))
    serial_samples = [
        await _run_once(parallel_prepare=False) for _ in range(repeats)
    ]
    parallel_samples = [
        await _run_once(parallel_prepare=True) for _ in range(repeats)
    ]

    reference_output = serial_samples[0]["output"]
    reference_input = serial_samples[0]["filter_input"]
    output_identical = all(
        sample["output"] == reference_output
        for sample in [*serial_samples, *parallel_samples]
    )
    filter_input_identical = all(
        sample["filter_input"] == reference_input
        for sample in [*serial_samples, *parallel_samples]
    )
    one_internal_model_call = all(
        sample["internal_model_calls"] == 1
        for sample in [*serial_samples, *parallel_samples]
    )
    quality_preserved = (
        output_identical and filter_input_identical and one_internal_model_call
    )

    serial_ms = statistics.median(sample["wall_ms"] for sample in serial_samples)
    parallel_ms = statistics.median(sample["wall_ms"] for sample in parallel_samples)
    saved_ms = serial_ms - parallel_ms
    latency_reduced = saved_ms > 0

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "fixture": {
            "network_access": False,
            "real_model_calls": False,
            "result_count": len(_RESULTS),
            "fetched_count": len(_RESULTS),
            "filtered_count": 3,
            "delays_ms": {
                "search": int(_SEARCH_DELAY_SECONDS * 1000),
                "fetch": int(_FETCH_DELAY_SECONDS * 1000),
                "filter": int(_FILTER_DELAY_SECONDS * 1000),
            },
        },
        "repeats": repeats,
        "baseline_serial_median_ms": round(serial_ms, 3),
        "optimized_parallel_median_ms": round(parallel_ms, 3),
        "saved_ms": round(saved_ms, 3),
        "latency_reduction_percent": round(saved_ms / serial_ms * 100, 2),
        "latency_reduced": latency_reduced,
        "quality": {
            "preserved": quality_preserved,
            "output_byte_identical": output_identical,
            "filter_input_identical": filter_input_identical,
            "one_internal_model_call": one_internal_model_call,
            "output_sha256": hashlib.sha256(reference_output.encode("utf-8")).hexdigest(),
            "filter_input_sha256": _fingerprint(reference_input),
        },
    }


def render_markdown(report: dict[str, Any]) -> str:
    quality = report["quality"]
    return "\n".join(
        [
            "# SimpleXNG latency and quality benchmark",
            "",
            f"Generated: {report['generated_at']}",
            "",
            "| Metric | Result |",
            "|---|---:|",
            f"| Serial baseline median | {report['baseline_serial_median_ms']:.3f} ms |",
            f"| Parallel optimized median | {report['optimized_parallel_median_ms']:.3f} ms |",
            f"| Median time saved | {report['saved_ms']:.3f} ms |",
            f"| Latency reduction | {report['latency_reduction_percent']:.2f}% |",
            f"| Output byte-identical | {quality['output_byte_identical']} |",
            f"| Filter input identical | {quality['filter_input_identical']} |",
            f"| One internal model call | {quality['one_internal_model_call']} |",
            f"| Quality contract preserved | {quality['preserved']} |",
            "",
            "This is a deterministic orchestration benchmark. It performs no network or real model calls.",
            "The production trace records actual fetch/filter overlap for live searches.",
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
    if not report["latency_reduced"]:
        raise SystemExit("SimpleXNG optimization did not reduce latency")

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
