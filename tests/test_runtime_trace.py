import pytest

from cyrene.observability.trace import (
    bind_trace_context,
    bind_trace_sink,
    trace_span,
)
from cyrene.runtime.database import (
    get_runtime_trace,
    init_db,
    record_llm_telemetry_batch,
)


@pytest.mark.asyncio
async def test_trace_context_builds_parent_child_waterfall(tmp_path):
    db_path = str(tmp_path / "trace.sqlite3")
    await init_db(db_path)
    captured = []

    with bind_trace_context(trace_id="trace_1", run_id="run_1", db_path=db_path):
        with bind_trace_sink(captured.append):
            async with trace_span("run", "test_run", span_id="run_span"):
                async with trace_span("round", "test_round", span_id="round_span"):
                    pass

    assert [event["span_id"] for event in captured] == ["round_span", "run_span"]
    assert captured[0]["parent_span_id"] == "run_span"
    waterfall = await get_runtime_trace(db_path, "trace_1")
    assert {span["span_id"] for span in waterfall} == {"run_span", "round_span"}
    assert next(span for span in waterfall if span["span_id"] == "round_span")[
        "parent_span_id"
    ] == "run_span"


@pytest.mark.asyncio
async def test_model_latency_is_projected_into_unified_trace(tmp_path):
    db_path = str(tmp_path / "model-trace.sqlite3")
    await init_db(db_path)
    await record_llm_telemetry_batch(
        db_path,
        latency_events=[{
            "call_id": "llm_1",
            "span_id": "llm_1.attempt.1",
            "trace_id": "trace_model",
            "run_id": "run_model",
            "parent_span_id": "round_model",
            "round_id": "round_model",
            "caller": "main",
            "phase": "phase2",
            "model": "fixture-model",
            "attempt": 1,
            "outcome": "success",
            "request_ms": 125,
            "ttft_ms": 50,
            "generation_ms": 75,
            "prompt_tokens": 100,
            "completion_tokens": 20,
        }],
    )

    waterfall = await get_runtime_trace(db_path, "trace_model")
    assert waterfall[0]["kind"] == "model"
    assert waterfall[0]["parent_span_id"] == "round_model"
    assert waterfall[0]["attributes"]["ttft_ms"] == 50
