import pytest

from cyrene.observability.performance_benchmark import (
    DEFAULT_SCENARIOS,
    render_markdown,
    run_benchmark,
)


@pytest.mark.asyncio
async def test_deterministic_benchmark_covers_all_eight_scenarios():
    report = await run_benchmark()

    assert [item["scenario"] for item in report["results"]] == [
        scenario.name for scenario in DEFAULT_SCENARIOS
    ]
    assert len(report["results"]) == 8
    assert report["environment"]["network_access"] is False
    assert report["environment"]["real_credentials"] is False
    assert all(item["model_rounds"] >= 1 for item in report["results"])
    assert all(item["trace_span_count"] >= 2 for item in report["results"])
    assert "| Scenario | Wall ms |" in render_markdown(report)
