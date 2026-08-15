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
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


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


async def _run_once() -> dict[str, Any]:
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

    started = time.perf_counter()
    try:
        search._search_simplexng = fake_search
        search._fetch_url = fake_fetch
        output = await search._deep_search_simplexng("fixture query")
    finally:
        for name, original in originals.items():
            setattr(search, name, original)

    return {
        "wall_ms": (time.perf_counter() - started) * 1000,
        "output": output,
    }


async def run_benchmark(*, repeats: int = 5) -> dict[str, Any]:
    """Run the deterministic pipeline and enforce the quality contract."""
    repeats = max(1, int(repeats))
    samples = [await _run_once() for _ in range(repeats)]

    reference_output = samples[0]["output"]
    output_identical = all(
        sample["output"] == reference_output for sample in samples
    )
    quality_preserved = output_identical

    return {
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
        "repeats": repeats,
        "median_ms": round(statistics.median(sample["wall_ms"] for sample in samples), 3),
        "quality": {
            "preserved": quality_preserved,
            "output_byte_identical": output_identical,
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
            f"| Median pipeline time | {report['median_ms']:.3f} ms |",
            f"| Output byte-identical | {quality['output_byte_identical']} |",
            f"| Quality contract preserved | {quality['preserved']} |",
            "",
            "This is a deterministic orchestration benchmark. It performs no network or real model calls.",
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
