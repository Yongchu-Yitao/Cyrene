from cyrene.observability.simplexng_performance_benchmark import (
    SearchBenchmarkWorkload,
    run_benchmark,
)


async def test_simplexng_pipeline_benchmark_is_quality_identical():
    report = await run_benchmark(
        repeats=1,
        workload=SearchBenchmarkWorkload(
            parallel_sessions=2,
            rounds_per_session=3,
        ),
    )

    assert report["median_ms"] > 0
    assert report["quality"]["preserved"] is True
    assert report["quality"]["output_byte_identical"] is True
    assert report["quality"]["output_sha256"]
    assert report["workload"] == {
        "parallel_sessions": 2,
        "rounds_per_session": 3,
    }
    assert report["quality"]["actual_outputs"] == 6
    assert report["ideal_cache"]["hit_rate"] == 0.666667
    assert report["ideal_cache"]["llm_independent"] is True
    assert report["ideal_cache"]["series_dimension"] == "round"
    assert [
        item["cumulative_hit_rate"] for item in report["ideal_cache"]["series"]
    ] == [0.0, 0.5, 0.666667]
