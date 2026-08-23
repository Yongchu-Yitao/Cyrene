"""Shared theoretical cache metrics for deterministic benchmarks."""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from typing import Any


def _ideal_metric(
    *,
    basis: str,
    requests: int,
    input_units: int,
    hit_units: int,
) -> dict[str, Any]:
    total = max(0, int(input_units))
    hits = min(total, max(0, int(hit_units)))
    return {
        "kind": "ideal",
        "basis": str(basis),
        "llm_independent": True,
        "requests": max(0, int(requests)),
        "input_units": total,
        "hit_units": hits,
        "miss_units": total - hits,
        "hit_rate": round(hits / total, 6) if total else 0.0,
    }


def ideal_cache_metrics(requests: int, unique_keys: int = 1) -> dict[str, Any]:
    """Return the best possible hit rate for a deterministic fixture workload.

    The first request for each fixture key is a miss; every identical request
    after that is an ideal hit. No runtime cache or model provider is queried.
    """
    request_count = max(0, int(requests))
    unique_count = min(request_count, max(0, int(unique_keys)))
    return {
        **_ideal_metric(
            basis="deterministic_fixture_keys",
            requests=request_count,
            input_units=request_count,
            hit_units=request_count - unique_count,
        ),
        "unique_keys": unique_count,
    }


class IdealPrefixCacheTracker:
    """Estimate ideal reusable prompt units from deterministic request segments."""

    def __init__(self, *, series_dimension: str = "round") -> None:
        self._seen: dict[str, list[tuple[str, ...]]] = {}
        self._requests = 0
        self._input_units = 0
        self._hit_units = 0
        self._series_dimension = str(series_dimension or "round")
        self._series: dict[int, dict[str, int]] = {}

    @staticmethod
    def _units(value: str) -> int:
        return max(1, (len(value) + 3) // 4)

    def record(
        self,
        scope: str,
        segments: Sequence[str],
        *,
        series_index: int | None = None,
    ) -> None:
        request = tuple(str(segment) for segment in segments)
        units = tuple(self._units(segment) for segment in request)
        best_hit = 0
        for previous in self._seen.get(str(scope), []):
            shared = 0
            for current_segment, previous_segment, segment_units in zip(
                request,
                previous,
                units,
            ):
                if current_segment != previous_segment:
                    break
                shared += segment_units
            best_hit = max(best_hit, shared)
        self._seen.setdefault(str(scope), []).append(request)
        self._requests += 1
        self._input_units += sum(units)
        self._hit_units += best_hit
        if series_index is not None:
            bucket = self._series.setdefault(
                max(0, int(series_index)),
                {"requests": 0, "input_units": 0, "hit_units": 0},
            )
            bucket["requests"] += 1
            bucket["input_units"] += sum(units)
            bucket["hit_units"] += best_hit

    def metrics(self) -> dict[str, Any]:
        result = _ideal_metric(
            basis="deterministic_context_prefix",
            requests=self._requests,
            input_units=self._input_units,
            hit_units=self._hit_units,
        )
        cumulative_input = 0
        cumulative_hits = 0
        series = []
        for index in sorted(self._series):
            bucket = self._series[index]
            current = _ideal_metric(
                basis="deterministic_context_prefix",
                requests=bucket["requests"],
                input_units=bucket["input_units"],
                hit_units=bucket["hit_units"],
            )
            cumulative_input += current["input_units"]
            cumulative_hits += current["hit_units"]
            series.append({
                "index": index,
                "requests": current["requests"],
                "input_units": current["input_units"],
                "hit_units": current["hit_units"],
                "miss_units": current["miss_units"],
                "hit_rate": current["hit_rate"],
                "cumulative_input_units": cumulative_input,
                "cumulative_hit_units": cumulative_hits,
                "cumulative_hit_rate": round(
                    cumulative_hits / cumulative_input,
                    6,
                ) if cumulative_input else 0.0,
            })
        result["series_dimension"] = self._series_dimension
        result["series"] = series
        return result


def aggregate_ideal_cache_metrics(
    metrics: Iterable[Mapping[str, Any]],
) -> dict[str, Any]:
    """Combine case-level ideal cache metrics using reusable input units."""
    items = list(metrics)
    return _ideal_metric(
        basis="combined_ideal_reuse",
        requests=sum(max(0, int(item.get("requests") or 0)) for item in items),
        input_units=sum(max(0, int(item.get("input_units") or 0)) for item in items),
        hit_units=sum(max(0, int(item.get("hit_units") or 0)) for item in items),
    )


def ideal_cache_percent(metrics: Mapping[str, Any]) -> float:
    """Return a report-friendly percentage without changing stored precision."""
    return float(metrics.get("hit_rate") or 0.0) * 100


def ideal_cache_progression(metrics: Mapping[str, Any]) -> str:
    """Render cumulative per-round hit rates as a compact report trend."""
    series = metrics.get("series")
    if not isinstance(series, list) or not series:
        return "-"
    return " → ".join(
        f"{float(item.get('cumulative_hit_rate') or 0.0) * 100:.2f}%"
        for item in series
        if isinstance(item, Mapping)
    )


__all__ = [
    "aggregate_ideal_cache_metrics",
    "IdealPrefixCacheTracker",
    "ideal_cache_metrics",
    "ideal_cache_percent",
    "ideal_cache_progression",
]
