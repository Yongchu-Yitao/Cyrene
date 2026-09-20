import asyncio

import pytest

from cyrene.core.retry import retry_runtime_operation
from cyrene.model.error_details import ModelCallError, classify_model_error
from cyrene.model.protocol_adapters import ModelStreamError
from cyrene.core.plugin import Plugin, PluginPack
from test_task_contexts import make_session


@pytest.mark.asyncio
@pytest.mark.parametrize("failures", [1, 3, 4])
async def test_nested_runtime_retries_share_exhaustion(failures):
    calls = 0
    retries = []

    async def operation():
        nonlocal calls
        calls += 1
        if calls <= failures:
            raise OSError("temporary failure")
        return "ok"

    async def notify(attempt):
        retries.append(attempt)

    async def inner():
        return await retry_runtime_operation(operation, on_retry=notify)

    if failures == 4:
        with pytest.raises(OSError):
            await retry_runtime_operation(inner)
    else:
        assert await retry_runtime_operation(inner) == "ok"
    assert calls == min(failures + 1, 4)
    assert retries == list(range(1, min(failures, 3) + 1))


@pytest.mark.asyncio
@pytest.mark.parametrize("error", [asyncio.CancelledError(), ModelCallError(classify_model_error("HTTP 401"))])
async def test_cancellation_and_typed_errors_are_not_runtime_retried(error):
    calls = 0

    async def operation():
        nonlocal calls
        calls += 1
        raise error

    with pytest.raises(type(error)):
        await retry_runtime_operation(operation)
    assert calls == 1


@pytest.mark.asyncio
async def test_cancel_between_attempts_stops_recovery():
    calls = 0

    async def operation():
        nonlocal calls
        calls += 1
        raise RuntimeError("internal failure")

    async def notify(attempt):
        asyncio.current_task().cancel()

    task = asyncio.create_task(retry_runtime_operation(operation, on_retry=notify))
    with pytest.raises(asyncio.CancelledError):
        await task
    assert calls == 1


@pytest.mark.parametrize("failures", [1, 3, 4])
def test_internal_transition_recovers_or_reports_after_three_retries(tmp_path, monkeypatch, failures):
    session = make_session(tmp_path)
    original = session._prepare_model_input
    calls = 0

    def prepare(node):
        nonlocal calls
        calls += 1
        if calls <= failures:
            raise RuntimeError("internal failure")
        return original(node)

    monkeypatch.setattr(session, "_prepare_model_input", prepare)
    try:
        session.submit("work", run_id="run")
        asyncio.run(session.drain())
        output = session.final_output("run")
        assert calls == min(failures + 1, 4)
        if failures == 4:
            assert output["failure_kind"] == "agent_transition_failed"
        else:
            assert output["content"] == "answer"
            assert not output.get("error")
    finally:
        session.close()


@pytest.mark.parametrize("failure_stage", ["stream", "tool_results"])
def test_recovery_preserves_completed_tools(tmp_path, monkeypatch, failure_stage):
    model_calls = 0
    tool_calls = 0
    failed_mounts = 0

    async def model(arguments, context):
        nonlocal model_calls
        model_calls += 1
        if model_calls == 1:
            return {"tool_calls": [{"id": "effect", "name": "Effect", "arguments": {}}]}
        if failure_stage == "stream" and model_calls <= 4:
            await context.services["model_stream"]({"type": "reply_delta", "delta": "partial"})
            raise ModelCallError(classify_model_error(ModelStreamError(
                "provider_failed", "internal error", {"provider_error_code": "server_error"})))
        return {"content": "recovered", "tool_calls": []}

    async def effect(arguments, context):
        nonlocal tool_calls
        tool_calls += 1
        return "done"

    session = make_session(tmp_path, model)
    session.registry.register_pack(PluginPack("effects", "test", (
        Plugin("Effect", "test", {"type": "object"}, effect, metadata={"read_only": True}),
    )), source="test")
    mount = session.store.mount

    def flaky_mount(tree, parent, value, *args, **kwargs):
        nonlocal failed_mounts
        if failure_stage == "tool_results" and value.get("role") == "tool_results" and failed_mounts < 3:
            failed_mounts += 1
            raise OSError("temporary storage error")
        return mount(tree, parent, value, *args, **kwargs)

    monkeypatch.setattr(session.store, "mount", flaky_mount)
    try:
        session.submit("work", run_id="run")
        asyncio.run(session.drain())
        assert session.final_output("run")["content"] == "recovered"
        assert tool_calls == 1
        assert model_calls == (5 if failure_stage == "stream" else 2)
        if failure_stage == "tool_results":
            assert failed_mounts == 3
    finally:
        session.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("failures", [3, 4])
async def test_chat_driver_retries_same_run_then_settles(failures):
    from cyrene.workbench.chat.chat_runs import ChatRunManager

    manager = ChatRunManager()
    calls = []

    async def runner(run):
        calls.append(run.run_id)
        if len(calls) <= failures:
            raise RuntimeError("driver failure")
        run.outcome = {"kind": "reply", "payload": {}}

    run, _ = manager.start_or_get("retry-test", {"type": "ack"}, runner, stream=False)
    try:
        await asyncio.wait_for(run.done.wait(), 5)
        assert calls == [run.run_id] * 4
        assert run.outcome["kind"] == ("reply" if failures == 3 else "error")
        assert len([e for e in run.events if e.get("type") == "error"]) == (failures == 4)
    finally:
        await manager.shutdown()


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["reply", "awaiting"])
async def test_post_terminal_driver_failure_does_not_retry(kind):
    from cyrene.workbench.chat.chat_runs import ChatRunManager

    manager = ChatRunManager()
    calls = 0

    async def runner(run):
        nonlocal calls
        calls += 1
        run.outcome = {"kind": kind}
        raise RuntimeError("cleanup failed")

    run, _ = manager.start_or_get("terminal-test", {"type": "ack"}, runner, stream=False)
    try:
        await asyncio.wait_for(run.done.wait(), 5)
        assert calls == 1
        assert run.outcome["kind"] == kind
        assert not any(e.get("type") == "error" for e in run.events)
    finally:
        await manager.shutdown()


@pytest.mark.asyncio
@pytest.mark.parametrize("failures", [3, 4])
async def test_builtin_result_persistence_retries_before_publishing_error(failures):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock, Mock
    from cyrene.workbench.chat.chat_runs import ChatRun
    from cyrene.workbench.http.workbench.chat_routes.run_send_routes import _SendOperation

    attempts = 0
    run = ChatRun("builtin-test", {"type": "ack"})
    result = SimpleNamespace(status="completed", run_id=run.run_id, activity_messages=[])

    def persist(*args):
        nonlocal attempts
        attempts += 1
        if attempts <= failures:
            raise OSError("database temporarily unavailable")
        return {"saved": True}

    async def publish(run, result, payload, **kwargs):
        run.outcome = {"kind": "reply", "payload": payload}

    operation = SimpleNamespace(
        chat_id=run.chat_id, environment=SimpleNamespace(workspace_dir="/tmp"),
        options=SimpleNamespace(lang="en"),
        service=SimpleNamespace(
            capture_workspace_changes_baseline=AsyncMock(return_value={}),
            finalize_workspace_changes=AsyncMock(), settle_chat_running_status=Mock(),
            chat_error_metadata=Mock(return_value={}),
            run_manager=SimpleNamespace(conversation_runtime=SimpleNamespace(kick_commit_outbox=Mock())),
        ),
        controller=SimpleNamespace(_schedule_workspace_finalize=Mock()),
        _run_turn=AsyncMock(return_value=result),
        _persist_builtin_result=persist, _publish_builtin_outcome=publish,
    )
    await _SendOperation._run_builtin(operation, run)
    assert attempts == 4
    assert operation._run_turn.await_count == 1
    assert operation.controller._schedule_workspace_finalize.call_count == 1
    assert run.outcome["kind"] == ("reply" if failures == 3 else "error")
    assert len([e for e in run.events if e.get("type") == "error"]) == (failures == 4)


@pytest.mark.asyncio
async def test_terminal_failure_does_not_restart_conversation_retry_budget(tmp_path, monkeypatch):
    from cyrene.workbench.core_adapter.bridge import WorkbenchSessionBridge, AgentSessionRunError
    from cyrene.workbench.core_adapter.conversation_runtime import ConversationConfig, ConversationRuntime

    calls = 0
    opened = 0

    async def model(arguments, context):
        nonlocal calls
        calls += 1
        raise ModelCallError(classify_model_error(ModelStreamError(
            "provider_failed", "internal error", {"provider_error_code": "server_error"})))

    runtime = ConversationRuntime()

    def open_bridge(config, **kwargs):
        nonlocal opened
        opened += 1
        return WorkbenchSessionBridge(make_session(tmp_path, model))

    monkeypatch.setattr(runtime, "_open_bridge", open_bridge)
    monkeypatch.setattr(runtime, "context_checkpoint", lambda *args: None)
    monkeypatch.setattr(runtime, "kick_commit_outbox", lambda *args: None)
    try:
        with pytest.raises(AgentSessionRunError):
            await runtime.send(ConversationConfig(session_id="test", workspace_dir=str(tmp_path), db_path=""),
                               "work", run_id="run", publish=lambda _: None)
        assert calls == 4
        assert opened == 1
    finally:
        await runtime.shutdown()
