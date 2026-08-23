from cyrene.observability.benchmark_cache import (
    IdealPrefixCacheTracker,
    aggregate_ideal_cache_metrics,
    ideal_cache_metrics,
)


def test_ideal_cache_metrics_count_first_access_per_fixture_as_a_miss():
    metrics = ideal_cache_metrics(5, unique_keys=2)

    assert metrics == {
        "kind": "ideal",
        "basis": "deterministic_fixture_keys",
        "llm_independent": True,
        "requests": 5,
        "input_units": 5,
        "hit_units": 3,
        "miss_units": 2,
        "unique_keys": 2,
        "hit_rate": 0.6,
    }


def test_ideal_cache_aggregate_is_weighted_by_requests():
    metrics = aggregate_ideal_cache_metrics([
        ideal_cache_metrics(3, unique_keys=1),
        ideal_cache_metrics(2, unique_keys=2),
    ])

    assert metrics["requests"] == 5
    assert metrics["input_units"] == 5
    assert metrics["hit_units"] == 2
    assert metrics["hit_rate"] == 0.4


def test_prefix_cache_counts_only_reusable_multi_turn_context():
    tracker = IdealPrefixCacheTracker()

    tracker.record(
        "conversation-a",
        ["system", "user one"],
        series_index=0,
    )
    tracker.record(
        "conversation-a",
        ["system", "user one", "assistant one", "user two"],
        series_index=1,
    )
    tracker.record(
        "conversation-b",
        ["system", "user one"],
        series_index=0,
    )

    metrics = tracker.metrics()

    assert metrics["basis"] == "deterministic_context_prefix"
    assert metrics["requests"] == 3
    assert metrics["input_units"] == 18
    assert metrics["hit_units"] == 4
    assert metrics["hit_rate"] == 0.222222
    assert metrics["series_dimension"] == "round"
    assert [item["hit_rate"] for item in metrics["series"]] == [0.0, 0.4]
    assert [item["cumulative_hit_rate"] for item in metrics["series"]] == [
        0.0,
        0.222222,
    ]
