"""Unified deterministic performance suite for Cyrene.

Run every non-network benchmark with::

    uv run python -m cyrene.observability.performance_suite

Use ``--groups`` to select comma-separated groups and ``--baseline`` to
compare a run with a previously generated suite JSON report.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import platform
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable

from cyrene.observability.benchmark_cache import (
    aggregate_ideal_cache_metrics,
    ideal_cache_percent,
)


DEFAULT_GROUPS = ("chat", "search", "features")


async def _run_chat(repeats: int) -> dict[str, Any]:
    from cyrene.observability.chat_pipeline_benchmark import run_benchmark

    return await run_benchmark(repeats=repeats)


async def _run_search(repeats: int) -> dict[str, Any]:
    from cyrene.observability.simplexng_performance_benchmark import run_benchmark

    return await run_benchmark(repeats=repeats)


async def _run_features(repeats: int) -> dict[str, Any]:
    from cyrene.observability.feature_performance_benchmark import run_benchmark

    return await run_benchmark(repeats=repeats)


GroupRunner = Callable[[int], Awaitable[dict[str, Any]]]
GROUP_RUNNERS: dict[str, GroupRunner] = {
    "chat": _run_chat,
    "search": _run_search,
    "features": _run_features,
}


def _ideal_cache_hit_rate(value: Any) -> float:
    return float(value.get("hit_rate") or 0.0) if isinstance(value, dict) else 0.0


def _ideal_cache_progression(value: Any) -> list[float]:
    if not isinstance(value, dict) or not isinstance(value.get("series"), list):
        return []
    return [
        float(item.get("cumulative_hit_rate") or 0.0)
        for item in value["series"]
        if isinstance(item, dict)
    ]


def _format_cache_progression(values: Any) -> str:
    if not isinstance(values, list) or not values:
        return "-"
    return " → ".join(f"{float(value) * 100:.2f}%" for value in values)


def _normalized_cases(group: str, report: dict[str, Any]) -> list[dict[str, Any]]:
    if group == "chat":
        return [
            {
                "id": f"chat/{item['profile']}",
                "group": group,
                "scenario": item["profile"],
                "primary_metric": "run_latency_p95_ms",
                "primary_ms": float(item["run_latency_p95_ms"]),
                "quality_preserved": bool(item["quality"]["preserved"]),
                "parallel_workers": int(item["parallel_sessions"]),
                "rounds_per_worker": int(item["turns_per_session"]),
                "ideal_cache_hit_rate": _ideal_cache_hit_rate(
                    item.get("ideal_cache")
                ),
                "ideal_cache_progression": _ideal_cache_progression(
                    item.get("ideal_cache")
                ),
            }
            for item in report["results"]
        ]
    if group == "search":
        return [{
            "id": "search/simplexng_search_and_fetch",
            "group": group,
            "scenario": "simplexng_search_and_fetch",
            "primary_metric": "median_ms",
            "primary_ms": float(report["median_ms"]),
            "quality_preserved": bool(report["quality"]["preserved"]),
            "parallel_workers": int(report["workload"]["parallel_sessions"]),
            "rounds_per_worker": int(report["workload"]["rounds_per_session"]),
            "ideal_cache_hit_rate": _ideal_cache_hit_rate(
                report.get("ideal_cache")
            ),
            "ideal_cache_progression": _ideal_cache_progression(
                report.get("ideal_cache")
            ),
        }]
    if group == "features":
        return [
            {
                "id": f"features/{item['feature']}/{item['scenario']}",
                "group": group,
                "scenario": f"{item['feature']}: {item['scenario']}",
                "primary_metric": "latency_p95_ms",
                "primary_ms": float(item["latency_p95_ms"] or item["wall_ms"]),
                "quality_preserved": bool(item["quality"]["preserved"]),
                "parallel_workers": int(item["details"]["parallel_workers"]),
                "rounds_per_worker": int(item["details"]["rounds_per_worker"]),
                "ideal_cache_hit_rate": _ideal_cache_hit_rate(
                    item.get("ideal_cache")
                ),
                "ideal_cache_progression": _ideal_cache_progression(
                    item.get("ideal_cache")
                ),
            }
            for item in report["results"]
        ]
    return []


def compare_with_baseline(
    cases: list[dict[str, Any]],
    baseline: dict[str, Any],
    *,
    regression_threshold_percent: float = 20.0,
) -> dict[str, Any]:
    baseline_cases = {
        str(item.get("id") or ""): item
        for item in baseline.get("cases", [])
        if item.get("id")
    }
    comparisons = []
    threshold = max(0.0, float(regression_threshold_percent))
    for item in cases:
        previous = baseline_cases.get(item["id"])
        if previous is None:
            continue
        previous_ms = float(previous.get("primary_ms") or 0.0)
        current_ms = float(item.get("primary_ms") or 0.0)
        delta_percent = (
            ((current_ms - previous_ms) / previous_ms) * 100
            if previous_ms > 0
            else 0.0
        )
        comparisons.append({
            "id": item["id"],
            "baseline_ms": round(previous_ms, 3),
            "current_ms": round(current_ms, 3),
            "delta_percent": round(delta_percent, 2),
            "regression": delta_percent > threshold,
        })
    return {
        "threshold_percent": threshold,
        "matched_cases": len(comparisons),
        "regressions": [item for item in comparisons if item["regression"]],
        "comparisons": comparisons,
    }


async def run_suite(
    *,
    groups: tuple[str, ...] = DEFAULT_GROUPS,
    repeats: int = 1,
    baseline: dict[str, Any] | None = None,
    regression_threshold_percent: float = 20.0,
) -> dict[str, Any]:
    selected = tuple(dict.fromkeys(groups))
    unknown = [group for group in selected if group not in GROUP_RUNNERS]
    if unknown:
        raise ValueError(f"unknown benchmark groups: {', '.join(unknown)}")
    raw: dict[str, Any] = {}
    cases: list[dict[str, Any]] = []
    for group in selected:
        report = await GROUP_RUNNERS[group](max(1, int(repeats)))
        raw[group] = report
        cases.extend(_normalized_cases(group, report))
    failed_cases = [item["id"] for item in cases if not item["quality_preserved"]]
    comparison = (
        compare_with_baseline(
            cases,
            baseline,
            regression_threshold_percent=regression_threshold_percent,
        )
        if baseline is not None
        else None
    )
    return {
        "schema_version": 2,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "environment": {
            "python": platform.python_version(),
            "platform": platform.platform(),
            "repeats": max(1, int(repeats)),
            "network_access": False,
            "real_credentials": False,
            "real_llm_calls": False,
        },
        "groups": list(selected),
        "cases": cases,
        "quality": {
            "preserved": not failed_cases,
            "failed_cases": failed_cases,
        },
        "ideal_cache": aggregate_ideal_cache_metrics(
            report["ideal_cache"]
            for report in raw.values()
            if isinstance(report.get("ideal_cache"), dict)
        ),
        "comparison": comparison,
        "reports": raw,
    }


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Cyrene performance benchmark suite",
        "",
        f"Generated: `{report['generated_at']}`",
        "",
        "| Group | Scenario | Parallel | Rounds | Primary metric | Result | Cache progression | Ideal cache | Quality |",
        "|---|---|---:|---:|---|---:|---|---:|---|",
    ]
    for item in report["cases"]:
        display = {
            **item,
            "parallel_workers": int(item.get("parallel_workers") or 0),
            "rounds_per_worker": int(item.get("rounds_per_worker") or 0),
            "cache_progression": _format_cache_progression(
                item.get("ideal_cache_progression")
            ),
        }
        lines.append(
            "| {group} | {scenario} | {parallel_workers} | {rounds_per_worker} | "
            "{primary_metric} | {primary_ms:.3f} ms | {cache_progression} | "
            "{ideal_cache_rate:.2f}% | {quality_label} |".format(
                **display,
                ideal_cache_rate=float(item.get("ideal_cache_hit_rate") or 0.0) * 100,
                quality_label="pass" if item["quality_preserved"] else "FAIL",
            )
        )
    comparison = report.get("comparison")
    if comparison is not None:
        lines.extend([
            "",
            "## Baseline comparison",
            "",
            f"Regression threshold: `{comparison['threshold_percent']:.1f}%`",
            "",
            "| Case | Baseline | Current | Delta | Regression |",
            "|---|---:|---:|---:|---|",
        ])
        for item in comparison["comparisons"]:
            lines.append(
                "| {id} | {baseline_ms:.3f} ms | {current_ms:.3f} ms | "
                "{delta_percent:+.2f}% | {regression} |".format(**item)
            )
    lines.extend([
        "",
        "> Quality failures are reported independently from timing regressions. The suite performs no real LLM or network calls.",
        f"> Aggregate ideal cache hit rate: {ideal_cache_percent(report.get('ideal_cache') or {}):.2f}%; it is derived only from deterministic fixture reuse.",
        "",
    ])
    return "\n".join(lines)


async def write_report(
    output_dir: Path,
    *,
    groups: tuple[str, ...] = DEFAULT_GROUPS,
    repeats: int = 1,
    baseline_path: Path | None = None,
    regression_threshold_percent: float = 20.0,
) -> tuple[Path, Path, dict[str, Any]]:
    baseline = (
        json.loads(baseline_path.read_text(encoding="utf-8"))
        if baseline_path is not None
        else None
    )
    report = await run_suite(
        groups=groups,
        repeats=repeats,
        baseline=baseline,
        regression_threshold_percent=regression_threshold_percent,
    )
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / "cyrene-performance-suite.json"
    markdown_path = output_dir / "cyrene-performance-suite.md"
    json_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    markdown_path.write_text(render_markdown(report), encoding="utf-8")
    return json_path, markdown_path, report


def _parse_groups(value: str) -> tuple[str, ...]:
    groups = tuple(part.strip() for part in value.split(",") if part.strip())
    return groups or DEFAULT_GROUPS


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--groups", default=",".join(DEFAULT_GROUPS))
    parser.add_argument("--repeats", type=int, default=1)
    parser.add_argument("--output-dir", default="output/performance")
    parser.add_argument("--baseline", type=Path)
    parser.add_argument("--regression-threshold-percent", type=float, default=20.0)
    parser.add_argument("--fail-on-quality", action="store_true")
    args = parser.parse_args()
    json_path, markdown_path, report = asyncio.run(
        write_report(
            Path(args.output_dir),
            groups=_parse_groups(args.groups),
            repeats=args.repeats,
            baseline_path=args.baseline,
            regression_threshold_percent=args.regression_threshold_percent,
        )
    )
    print(json_path)
    print(markdown_path)
    if args.fail_on_quality and not report["quality"]["preserved"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()


__all__ = [
    "DEFAULT_GROUPS",
    "compare_with_baseline",
    "render_markdown",
    "run_suite",
    "write_report",
]
