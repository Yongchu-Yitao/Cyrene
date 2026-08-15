from cyrene.observability.simplexng_performance_benchmark import run_benchmark


async def test_simplexng_pipeline_benchmark_is_quality_identical():
    report = await run_benchmark(repeats=2)

    assert report["median_ms"] > 0
    assert report["quality"]["preserved"] is True
    assert report["quality"]["output_byte_identical"] is True
    assert report["quality"]["output_sha256"]
