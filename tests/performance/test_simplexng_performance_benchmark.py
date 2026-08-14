from cyrene.observability.simplexng_performance_benchmark import run_benchmark


async def test_simplexng_parallel_prepare_is_faster_and_quality_identical():
    report = await run_benchmark(repeats=2)

    assert report["latency_reduced"] is True
    assert report["optimized_parallel_median_ms"] < report["baseline_serial_median_ms"]
    assert report["quality"]["preserved"] is True
    assert report["quality"]["output_byte_identical"] is True
    assert report["quality"]["filter_input_identical"] is True
    assert report["quality"]["one_internal_model_call"] is True
