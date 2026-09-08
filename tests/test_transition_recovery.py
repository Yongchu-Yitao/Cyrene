"""Recover local failures without losing task context or the transition worker."""
import asyncio
import threading
from copy import deepcopy
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from cyrene.core.plugin import Plugin, PluginCallResult, PluginContext, PluginPack, PluginRegistry, PluginRuntime
from cyrene.core.transition_driver import TransitionCallbacks, TransitionDriver
from test_task_contexts import make_session


@pytest.mark.parametrize("response", [
    [{"id": "same", "name": "Read", "arguments": {}}] * 2,
    [None], [{"id": "bad", "name": "Read", "arguments": "invalid"}],
])
def test_invalid_batch_retries_before_execution(tmp_path, response):
    requests = []
    async def model(arguments, context):
        requests.append(arguments)
        return {"content": "done", "tool_calls": response if len(requests) == 1 else []}
    session = make_session(tmp_path, model)
    try:
        session.submit("work", run_id="run")
        asyncio.run(session.drain())
        assert len(requests) == 2
        assert session.snapshot()["status"] == "idle"
    finally:
        session.close()


@pytest.mark.parametrize("mode", ["missing", "exception", "cancel", "empty", "success", "user_cancel"])
def test_reflection_preserves_context_on_failure_and_continues(tmp_path, mode):
    requests = []
    reflecting = threading.Event()
    async def model(arguments, context):
        requests.append(deepcopy(arguments))
        return {"content": "done", "tool_calls": [
            {"id": "reflect", "name": "DeepReflect", "arguments": {}}
        ] if len(requests) == 1 else []}
    session = make_session(tmp_path, model)
    session.registry.register_pack(PluginPack("reflection-test", "test", (
        Plugin("DeepReflect", "test", {"type": "object"}, lambda *_: None,
               metadata={"session_transition": "deep_reflection", "read_only": True}),
    )), source="test")
    async def reflect(*args):
        if mode == "user_cancel":
            reflecting.set()
            await asyncio.Event().wait()
        if mode == "exception":
            raise RuntimeError("secondary model failed")
        if mode == "cancel":
            raise asyncio.CancelledError()
        return {"rendered_model_context": "NEW_CONTEXT" if mode == "success" else ""}
    services = session._plugin_services
    session._plugin_services = lambda: {**services(), "deep_reflection":
        None if mode == "missing" else SimpleNamespace(reflect=reflect)}
    active = session.task_contexts.ensure("test")
    state = session.task_contexts.read()
    state["documents"][active]["body"] = "ORIGINAL_CONTEXT"
    session.task_contexts.write(state)
    try:
        session.submit("work", run_id="run")
        if mode == "user_cancel":
            assert reflecting.wait(3)
            asyncio.run(session.cancel(timeout=3))
        asyncio.run(session.drain())
        assert len(requests) == (1 if mode == "user_cancel" else 2)
        state = session.task_contexts.read()
        assert state["documents"][active]["body"] == (
            "NEW_CONTEXT" if mode == "success" else "ORIGINAL_CONTEXT")
        assert any(k.startswith("reflect:") for k in state["receipts"]) == (mode == "success")
        assert session.snapshot()["status"] == "idle"
        if mode != "user_cancel":
            assert ("NEW_CONTEXT" if mode == "success" else "DeepReflect failed") in str(requests[-1])
    finally:
        session.close()


def test_local_plugin_cancellation_is_retryable_but_task_cancellation_propagates():
    registry = PluginRegistry()
    async def handler(arguments, context):
        if arguments.get("external"):
            asyncio.current_task().cancel()
            await asyncio.sleep(0)
        raise asyncio.CancelledError()
    registry.register_pack(PluginPack("test", "test", (
        Plugin("CancelTool", "test", {"type": "object"}, handler),
    )), source="test")
    runtime = PluginRuntime(registry)
    result = asyncio.run(runtime.call("CancelTool", {}, PluginContext()))
    assert not result.success
    assert result.failure.error_code == "plugin_cancelled"
    assert result.failure.retryable
    assert result.failure.circuit_scope == "none"
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(runtime.call("CancelTool", {"external": True}, PluginContext()))


@pytest.mark.parametrize("cancel", [False, True])
def test_failure_callback_failure_does_not_kill_worker(cancel):
    seen = []
    failures = []
    async def execute(kind, node):
        seen.append(node.id)
        if node.id == "bad":
            if cancel:
                raise asyncio.CancelledError()
            raise ValueError("tool failed")
    async def failure(*args):
        failures.append(args)
        raise RuntimeError("storage failed")
    driver = TransitionDriver("test", TransitionCallbacks(
        key=lambda n: n.id, run_id=lambda n: "run", cancelled=lambda _: False,
        coroutine=execute, failure=failure, idle=lambda _: None, snapshot=lambda: {},
    ))
    driver.enqueue("tools", SimpleNamespace(id="bad"))
    driver.enqueue("tools", SimpleNamespace(id="good"))
    driver.thread.start()
    try:
        driver.wait()
        assert seen == ["bad", "good"]
        assert len(failures) == 1
        assert driver.thread.is_alive()
    finally:
        with driver.condition:
            driver.stop_locked()
        driver.join()


def test_failure_settles_even_when_error_node_cannot_be_saved(tmp_path, monkeypatch):
    session = make_session(tmp_path)
    def fail(*args, **kwargs):
        raise OSError("storage unavailable")
    try:
        session._current_run_id = "run"
        session._status = "tools"
        monkeypatch.setattr(session, "_mount_assistant", fail)
        with pytest.raises(OSError):
            asyncio.run(session._transition_failure(
                SimpleNamespace(id="node", updated_at=datetime.now(timezone.utc)), "run", ValueError()))
        assert session.snapshot()["status"] == "idle"
    finally:
        session.close()


def test_resource_presentation_failure_is_optional(tmp_path, monkeypatch):
    session = make_session(tmp_path)
    try:
        session._plugin_context_data["project_id"] = "project"
        monkeypatch.setattr(session.registry, "resolve", lambda *a, **kw: SimpleNamespace(resource_effects=("effect",)))
        def fail(*args, **kwargs):
            raise ValueError("bad resource template")
        monkeypatch.setattr("cyrene.core.session.workspace_resource_locations", fail)
        assert session._resource_presentation({"name": "Read"}, phase="completed") == {}
        node = session.store.mount(session.tree.id, session.tree.root_id, {
            "role": "assistant", "tool_calls": [{"id": "read", "name": "Read"}],
        })
        saved = []
        monkeypatch.setattr(session.store, "save_effect_result", lambda *args: saved.append(args))
        session._persist_effect_result(node.id, PluginCallResult(
            "read", "Read", True, "already executed", "", datetime.now(timezone.utc)))
        assert saved[0][-1]["success"] is True
        assert saved[0][-1]["value"] == "already executed"
    finally:
        session.close()


def test_persistent_invalid_batch_has_bounded_retries(tmp_path):
    from cyrene.core.session import _MODEL_RESPONSE_INVALID_RETRY_LIMIT
    requests = []
    async def model(arguments, context):
        requests.append(arguments)
        return {"tool_calls": [None]}
    session = make_session(tmp_path, model)
    try:
        session.submit("work", run_id="run")
        asyncio.run(session.drain())
        assert len(requests) == _MODEL_RESPONSE_INVALID_RETRY_LIMIT + 1
        assert session.snapshot()["status"] == "idle"
    finally:
        session.close()
