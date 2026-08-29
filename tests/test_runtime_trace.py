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


@pytest.mark.asyncio
async def test_standalone_inbox_trace_does_not_write_sqlite_on_result_path(
    monkeypatch,
    tmp_path,
):
    """Inbox diagnostics without a run root must not create a synchronous trace root."""
    from cyrene.runtime import database
    from cyrene.workbench.application.inbox import WorkbenchAgentInbox

    persistence_calls = 0

    async def forbidden_trace_write(*_args, **_kwargs):
        nonlocal persistence_calls
        persistence_calls += 1
        raise AssertionError("trace persistence entered the tool result hot path")

    monkeypatch.setattr(
        database,
        "record_runtime_trace_spans",
        forbidden_trace_write,
    )
    for index in range(20):
        inbox = WorkbenchAgentInbox(
            f"chat-{index}",
            str(tmp_path / f"inbox-{index}.sqlite3"),
        )

        async def runner() -> str:
            return "ok"

        inbox.submit_tool_batch([(f"call-{index}", "Read", runner)])
        assert await inbox.wait_for_tool_result(f"call-{index}") == "ok"
        await inbox.close()

    assert persistence_calls == 0
