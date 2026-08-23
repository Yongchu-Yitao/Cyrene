import pytest

from cyrene.observability.performance_benchmark import (
    AgentBenchmarkWorkload,
    DEFAULT_SCENARIOS,
    render_markdown,
    run_benchmark,
)


@pytest.mark.asyncio
async def test_deterministic_benchmark_covers_all_eight_scenarios():
    report = await run_benchmark(
        repeats=1,
        workload=AgentBenchmarkWorkload(
            parallel_sessions=2,
            turns_per_session=3,
        ),
    )

    assert [item["scenario"] for item in report["results"]] == [
        scenario.name for scenario in DEFAULT_SCENARIOS
    ]
    assert len(report["results"]) == 8
    assert report["environment"]["network_access"] is False
    assert report["environment"]["real_credentials"] is False
    assert report["environment"]["real_llm_calls"] is False
    assert report["ideal_cache"]["llm_independent"] is True
    assert report["workload"] == {
        "parallel_sessions": 2,
        "turns_per_session": 3,
    }
    assert all(item["conversation_turns"] == 6 for item in report["results"])
    assert all(0 < item["ideal_cache"]["hit_rate"] < 1 for item in report["results"])
    assert all(item["ideal_cache"]["series_dimension"] == "turn" for item in report["results"])
    assert all(len(item["ideal_cache"]["series"]) == 3 for item in report["results"])
    assert all(
        item["ideal_cache"]["series"][-1]["cumulative_hit_rate"]
        == item["ideal_cache"]["hit_rate"]
        for item in report["results"]
    )
    assert all(item["model_rounds"] >= 1 for item in report["results"])
    assert all(item["trace_span_count"] >= 2 for item in report["results"])
    assert "| Scenario | Parallel | Turns |" in render_markdown(report)
    assert "Ideal cache" in render_markdown(report)
