import pytest

from cyrene.observability.chat_pipeline_benchmark import (
    BenchmarkProfile,
    build_mock_task,
    render_markdown,
    run_benchmark,
)


@pytest.mark.asyncio
async def test_non_llm_chat_pipeline_benchmark_preserves_events_and_tools():
    profile = BenchmarkProfile(
        "test_pipeline_contract",
        concurrency=2,
        tools_per_run=4,
        turns_per_session=2,
        progress_events_per_tool=1,
        reply_chunks=4,
        tool_result_chars=64,
    )
    report = await run_benchmark(repeats=1, profiles=(profile,))

    result = report["results"][0]
    fixture = build_mock_task(profile)
    assert len(fixture.tools) == 4
    assert report["environment"]["real_llm_calls"] is False
    assert report["environment"]["network_access"] is False
    assert 0 < report["ideal_cache"]["hit_rate"] < 1
    assert 0 < result["ideal_cache"]["hit_rate"] < 1
    assert result["ideal_cache"]["llm_independent"] is True
    assert result["parallel_sessions"] == 2
    assert result["turns_per_session"] == 2
    assert result["conversation_turns"] == 4
    assert result["ideal_cache"]["series_dimension"] == "turn"
    assert len(result["ideal_cache"]["series"]) == 2
    assert result["ideal_cache"]["series"][0]["cumulative_hit_rate"] == 0.0
    assert (
        result["ideal_cache"]["series"][-1]["cumulative_hit_rate"]
        == result["ideal_cache"]["hit_rate"]
    )
    assert result["quality"]["preserved"] is True
    assert result["quality"]["stream_events"] == result["quality"]["durable_events"]
    assert result["quality"]["durable_tool_results"] == 16
    assert result["quality"]["durable_telemetry_events"] == 68
    assert result["failure_counts"] == {}
    assert 0 < result["sqlite_connections"]["inbox"] < 68
    assert result["sqlite_connections"]["event_store"] > 0
    assert result["inbox_telemetry_batches"]["max_size"] > 1
    assert result["durable_event_batches"]["count"] > 0
    assert result["events_per_second"] > 0
    assert result["tools_per_second"] > 0
    assert "| Profile | Parallel | Turns |" in render_markdown(report)
    assert "Ideal cache" in render_markdown(report)
