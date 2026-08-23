import pytest

from cyrene.observability.feature_performance_benchmark import (
    FeatureBenchmarkConfig,
    render_markdown as render_feature_markdown,
    run_benchmark,
)
from cyrene.observability.benchmark_cache import ideal_cache_metrics
from cyrene.observability.performance_suite import (
    compare_with_baseline,
    render_markdown as render_suite_markdown,
    run_suite,
)


@pytest.mark.asyncio
async def test_local_feature_benchmark_covers_major_non_llm_runtimes():
    report = await run_benchmark(
        repeats=1,
        config=FeatureBenchmarkConfig(
            parallel_workers=2,
            rounds_per_worker=3,
            event_subscribers=4,
            event_count=5,
            terminal_chunks=8,
            terminal_chunk_bytes=1024,
            knowledge_documents=4,
            knowledge_chunks_per_document=8,
            knowledge_searches=4,
            scheduled_tasks=4,
            hash_file_bytes=64 * 1024,
            hash_cache_reads=5,
        )
    )

    scenarios = {item["scenario"] for item in report["results"]}
    assert scenarios == {
        "event_bus_fanout",
        "output_stream_persist_screen_replay",
        "fts_search_concurrency",
        "chunk_replace_concurrency",
        "runtime_database_init",
        "scheduled_task_crud_concurrency",
        "content_hash_cold_and_cached",
    }
    assert report["environment"]["real_llm_calls"] is False
    assert report["environment"]["network_access"] is False
    assert report["quality"]["preserved"] is True
    assert report["ideal_cache"]["hit_rate"] == 0.666667
    assert all(item["ideal_cache"]["hit_rate"] == 0.666667 for item in report["results"])
    assert all(
        [point["cumulative_hit_rate"] for point in item["ideal_cache"]["series"]]
        == [0.0, 0.5, 0.666667]
        for item in report["results"]
    )
    assert all(item["details"]["parallel_workers"] == 2 for item in report["results"])
    assert all(item["details"]["rounds_per_worker"] == 3 for item in report["results"])
    assert all(item["operations_per_second"] > 0 for item in report["results"])
    assert "| Feature | Scenario |" in render_feature_markdown(report)
    assert "Ideal cache" in render_feature_markdown(report)


def test_suite_baseline_comparison_flags_only_timing_regressions():
    current = [{
        "id": "features/files/hash",
        "group": "features",
        "scenario": "hash",
        "primary_metric": "wall_ms",
        "primary_ms": 125.0,
        "quality_preserved": True,
        "ideal_cache_hit_rate": 0.5,
    }]
    baseline = {"cases": [{**current[0], "primary_ms": 100.0}]}

    comparison = compare_with_baseline(
        current,
        baseline,
        regression_threshold_percent=20.0,
    )

    assert comparison["matched_cases"] == 1
    assert comparison["regressions"][0]["delta_percent"] == 25.0
    suite_report = {
        "generated_at": "2026-01-01T00:00:00+00:00",
        "cases": current,
        "comparison": comparison,
    }
    assert "Baseline comparison" in render_suite_markdown(suite_report)


@pytest.mark.asyncio
async def test_suite_normalizes_selected_groups(monkeypatch: pytest.MonkeyPatch):
    from cyrene.observability import performance_suite

    async def fake_search(_repeats: int):
        return {
            "median_ms": 12.5,
            "quality": {"preserved": True},
            "workload": {
                "parallel_sessions": 2,
                "rounds_per_session": 3,
            },
            "ideal_cache": ideal_cache_metrics(2),
        }

    monkeypatch.setitem(performance_suite.GROUP_RUNNERS, "search", fake_search)

    report = await run_suite(groups=("search",), repeats=2)

    assert report["groups"] == ["search"]
    assert report["quality"]["preserved"] is True
    assert report["cases"] == [{
        "id": "search/simplexng_search_and_fetch",
        "group": "search",
        "scenario": "simplexng_search_and_fetch",
        "primary_metric": "median_ms",
        "primary_ms": 12.5,
        "quality_preserved": True,
        "parallel_workers": 2,
        "rounds_per_worker": 3,
        "ideal_cache_hit_rate": 0.5,
        "ideal_cache_progression": [],
    }]
    assert report["ideal_cache"]["hit_rate"] == 0.5
