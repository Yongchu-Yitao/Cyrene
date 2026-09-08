"""Fault injection for run ownership and durable execution boundaries."""
import asyncio
from types import SimpleNamespace

import pytest

from cyrene.core.hook import SESSION_START, TURN_START, SESSION_END
from cyrene.core.plugin import Plugin, PluginCall, PluginPack
from cyrene.workbench.core_adapter.conversation_runtime import (
    ConversationConfig, ConversationRuntime, context_checkpoint_from_nodes,
)
from test_task_contexts import make_session


def drain(session):
    asyncio.run(asyncio.wait_for(session.drain(), 5))


@pytest.mark.parametrize("terminal_flag,status", [("cancelled", "cancelled"), ("error", "failed")])
def test_late_inbox_cannot_override_terminal_checkpoint_or_block_new_run(tmp_path, terminal_flag, status):
    from cyrene.core.context import ContextStoreRouter
    from cyrene.core.restore_decision import RunTerminatedError

    calls = []

    async def model(arguments, context):
        calls.append(context.data.get("run_id"))
        return {"content": "answer", "tool_calls": []}

    session = make_session(tmp_path, model)
    session.submit("work", run_id="old")
    drain(session)
    tree_id, root_id = session.tree.id, session.tree.root_id
    parent_id = session.snapshot()["leaf_id"]
    session.close()
    # Reproduce the old installed version's durable shape without executing
    # the obsolete delivery code: terminal -> late inbox -> context node.
    store = ContextStoreRouter(tmp_path / "data" / "context")
    try:
        terminal = store.mount(tree_id, parent_id, {
            "role": "assistant", "run_id": "old", terminal_flag: True,
            "content": "ended", "session_end_complete": True,
        })
        late = store.mount(tree_id, terminal.id, {
            "role": "user", "run_id": "old", "trigger_model": False,
            "metadata": {"source": "agent_inbox"}, "content": "user_interrupted",
        })
        store.mount(tree_id, late.id, {"role": "context", "trigger_model": False})
        checkpoint = context_checkpoint_from_nodes(store.get_subtree(tree_id, root_id))
        assert checkpoint == {"status": status, "run_id": "old", "node_id": terminal.id}
    finally:
        store.close()

    reopened = make_session(tmp_path, model, tree_id=tree_id)
    try:
        assert reopened.snapshot()["leaf_id"] == terminal.id
        before = len(reopened.store.get_subtree(tree_id, root_id))
        with pytest.raises(RunTerminatedError):
            reopened.submit("late result", run_id="old", metadata={"source": "agent_inbox"})
        assert len(reopened.store.get_subtree(tree_id, root_id)) == before
        assert reopened.is_idle
        reopened.submit("retry", run_id="new")
        drain(reopened)
        assert reopened.final_output("new")["content"] == "answer"
        assert len(calls) == 2
        checkpoint = context_checkpoint_from_nodes(reopened.store.get_subtree(tree_id, root_id))
        assert checkpoint["run_id"] == "new"
        assert checkpoint["status"] == "completed"
    finally:
        reopened.close()


@pytest.mark.parametrize("event", [SESSION_START, TURN_START])
def test_required_context_failure_is_a_failed_transition_not_a_stuck_hook(tmp_path, event):
    requests = []
    async def model(arguments, context):
        requests.append(arguments)
        return {"content": "answer", "tool_calls": []}
    session = make_session(tmp_path, model)
    async def fail(event):
        raise RuntimeError("required security context unavailable")
    session.hooks.register(event, fail, failure_policy="closed", hook_id="required-context")
    try:
        session.submit("work", run_id="run")
        drain(session)
        assert not requests
        assert session.snapshot()["status"] == "idle"
        assert session.final_output("run")["error"] is True
        session.hooks.unregister("required-context")
        session.submit("try again", run_id="next")
        drain(session)
        assert len(requests) == 1
    finally:
        session.close()


def test_parallel_results_survive_receipt_failure_without_reexecution(tmp_path, monkeypatch):
    requests, executed = [], []
    async def model(arguments, context):
        requests.append(arguments)
        return {"content": "answer", "tool_calls": [
            {"id": str(i), "name": "Effect", "arguments": {"i": i}} for i in range(2)
        ] if len(requests) == 1 else []}
    async def effect(arguments, context):
        executed.append(arguments["i"])
        await asyncio.sleep(0)
        return "written"
    session = make_session(tmp_path, model)
    session.registry.register_pack(PluginPack("effects", "test", (
        Plugin("Effect", "test", {"type": "object"}, effect,
               allow_parallel=True, metadata={"read_only": True}),
    )), source="test")
    save = session.store.save_effect_result
    def fail_receipt(tree, node, call, result):
        if call == "0":
            raise OSError("receipt write failed")
        return save(tree, node, call, result)
    monkeypatch.setattr(session.store, "save_effect_result", fail_receipt)
    try:
        session.submit("work", run_id="run")
        drain(session)
        assert sorted(executed) == [0, 1]
        assert len(requests) == 2
        assert "tool_result_not_persisted" in str(requests[-1])
        assert "written" in str(requests[-1])
        assert not session.final_output("run").get("error")
    finally:
        session.close()


def test_execution_fence_survives_store_reopen(tmp_path):
    session = make_session(tmp_path)
    try:
        node = session.store.mount(session.tree.id, session.tree.root_id, {"role": "system"})
        tree_id = session.tree.id
        call = PluginCall(name="Effect", arguments={}, id="effect")
        assert session._claim_effect(node.id, call) is None
        assert session._claim_effect(node.id, call).failure.error_code == "tool_execution_unknown"
    finally:
        session.close()
    from cyrene.core.context import ContextStoreRouter
    # Reopening the same store proves the fence is not an in-memory cache.
    store = ContextStoreRouter(tmp_path / "data" / "context")
    try:
        receipts = store.effect_results(tree_id, node.id)
        assert receipts["effect"]["failure"]["error_code"] == "tool_execution_unknown"
    finally:
        store.close()


def test_failed_execution_fence_never_starts_tool(tmp_path, monkeypatch):
    requests, executed = [], []
    async def model(arguments, context):
        requests.append(arguments)
        return {"content": "answer", "tool_calls": [
            {"id": "effect", "name": "Effect", "arguments": {}}
        ] if len(requests) == 1 else []}
    session = make_session(tmp_path, model)
    session.registry.register_pack(PluginPack("effect", "test", (
        Plugin("Effect", "test", {"type": "object"}, lambda *_: executed.append(True),
               metadata={"read_only": True}),
    )), source="test")
    def fail(*args):
        raise OSError("cannot register execution")
    monkeypatch.setattr(session.store, "claim_effect", fail)
    try:
        session.submit("work", run_id="run")
        drain(session)
        assert not executed
        assert "tool_execution_not_started" in str(requests[-1])
    finally:
        session.close()


def test_session_end_failure_preserves_answer_and_checkpoints_each_hook(tmp_path):
    session = make_session(tmp_path)
    calls = []
    async def good(event):
        calls.append("good")
    async def bad(event):
        calls.append("bad")
        raise RuntimeError("post-processing unavailable")
    session.hooks.register(SESSION_END, good, hook_id="good")
    session.hooks.register(SESSION_END, bad, hook_id="bad", failure_policy="closed")
    try:
        session.submit("work", run_id="run")
        drain(session)
        output = session.final_output("run")
        assert output["content"] == "answer"
        assert not output.get("error")
        leaf = session.store.get_node(session.tree.id, session.snapshot()["leaf_id"])
        assert leaf.value["answer_complete"] is True
        assert not leaf.value.get("session_end_complete")
        assert context_checkpoint_from_nodes([leaf])["status"] == "completed"
        asyncio.run(session._finish_success(leaf))
        assert calls == ["good", "bad"]
        receipts = session.store.effect_results(session.tree.id, leaf.id)
        assert receipts["session_end:good"]["phase"] == "completed"
        assert receipts["session_end:bad"]["phase"] == "failed"
    finally:
        session.close()


def test_session_end_final_write_failure_does_not_repeat_hooks(tmp_path, monkeypatch):
    session = make_session(tmp_path)
    calls = []
    async def hook(event):
        calls.append(True)
    session.hooks.register(SESSION_END, hook, hook_id="once")
    update = session.store.update_node
    def fail_final(tree, node, value):
        if value.get("session_end_complete"):
            raise OSError("completion write failed")
        return update(tree, node, value)
    monkeypatch.setattr(session.store, "update_node", fail_final)
    try:
        session.submit("work", run_id="run")
        drain(session)
        assert session.final_output("run")["content"] == "answer"
        assert calls == [True]
        monkeypatch.setattr(session.store, "update_node", update)
        leaf = session.store.get_node(session.tree.id, session.snapshot()["leaf_id"])
        asyncio.run(session._finish_success(leaf))
        assert calls == [True]
        assert session.store.get_node(session.tree.id, leaf.id).value["session_end_complete"]
    finally:
        session.close()


@pytest.mark.asyncio
async def test_caller_cancel_does_not_release_bridge_or_chat_lock(tmp_path, monkeypatch):
    runtime = ConversationRuntime()
    entered, release = asyncio.Event(), asyncio.Event()
    events = []
    class Bridge:
        def close(self):
            events.append("closed")
    monkeypatch.setattr(runtime, "_open_bridge", lambda *args, **kwargs: Bridge())
    config = ConversationConfig(session_id="chat", workspace_dir=str(tmp_path), db_path="")
    async def first(bridge):
        events.append("first")
        entered.set()
        await release.wait()
        events.append("finished")
    async def second(bridge):
        events.append("second")
    waiter = asyncio.create_task(runtime._with_bridge(config, first, publish=None))
    await entered.wait()
    waiter.cancel()
    with pytest.raises(asyncio.CancelledError):
        await waiter
    next_waiter = asyncio.create_task(runtime._with_bridge(config, second, publish=None))
    await asyncio.sleep(0)
    assert events == ["first"]
    assert "chat" in runtime._active
    release.set()
    await next_waiter
    await runtime.shutdown()
    assert events == ["first", "finished", "closed", "second", "closed"]
    assert not runtime._operations


@pytest.mark.asyncio
async def test_different_run_id_does_not_cancel_unfinished_run(tmp_path, monkeypatch):
    runtime = ConversationRuntime()
    cancelled = []
    bridge = SimpleNamespace(snapshot=lambda: {"status": "tools", "run_id": "old"},
                             cancel=lambda reason: cancelled.append(reason))
    async def use_bridge(config, operation, **kwargs):
        return await operation(bridge)
    monkeypatch.setattr(runtime, "_with_bridge", use_bridge)
    monkeypatch.setattr(runtime, "kick_commit_outbox", lambda _: None)
    config = ConversationConfig(session_id="chat", workspace_dir=str(tmp_path), db_path="")
    with pytest.raises(RuntimeError, match="unfinished run"):
        await runtime.send(config, "new request", run_id="new")
    assert not cancelled


@pytest.mark.asyncio
async def test_conflicting_run_is_rejected_before_bridge_can_resume_it(tmp_path, monkeypatch):
    runtime = ConversationRuntime()
    monkeypatch.setattr(runtime, "context_checkpoint", lambda *args:
                        {"status": "running", "run_id": "old"})
    def must_not_open(*args, **kwargs):
        raise AssertionError("Opening the bridge would already resume the old run")
    monkeypatch.setattr(runtime, "_open_bridge", must_not_open)
    config = ConversationConfig(session_id="chat", workspace_dir=str(tmp_path), db_path="")
    async def operate(bridge):
        raise AssertionError("must not execute")
    with pytest.raises(RuntimeError, match="unfinished run"):
        await runtime._with_bridge(config, operate, publish=None, expected_run_id="new")
    await runtime.shutdown()


def test_unserializable_result_retains_execution_outcome_without_poisoning_batch():
    from datetime import datetime, timezone
    from cyrene.core.plugin.batch_catcher import PluginBatchCatcher
    from cyrene.core.plugin import PluginCallResult
    import json
    value = {}
    value["cycle"] = value
    def cannot_save(result):
        raise ValueError("circular result")
    catcher = PluginBatchCatcher([PluginCall(name="Effect", arguments={}, id="call")], on_result=cannot_save)
    original = PluginCallResult("call", "Effect", True, value, "", datetime.now(timezone.utc))
    catcher.catch(original)
    assert catcher.execution_results["call"] is original
    result = catcher.results()[0]
    assert result.failure.error_code == "tool_result_not_persisted"
    json.dumps(result.value)


def test_partial_context_write_failure_cannot_be_reopened_by_queued_delivery(tmp_path, monkeypatch):
    requests = []
    async def model(arguments, context):
        requests.append(arguments)
        return {"content": "answer", "tool_calls": []}
    session = make_session(tmp_path, model)
    async def mounts(details):
        return [{"kind": "system", "source": "required", "content": "required policy"}]
    session.hooks.register(SESSION_START, lambda event: "required policy", hook_id="required-context")
    monkeypatch.setattr(session, "build_session_mounts", mounts)
    mount = session.store.mount
    attempts = []
    def fail_once(tree, parent, value, **kwargs):
        if value.get("role") == "context":
            attempts.append(True)
            if len(attempts) == 1:
                raise OSError("context child write failed")
        return mount(tree, parent, value, **kwargs)
    monkeypatch.setattr(session.store, "mount", fail_once)
    try:
        session.submit("work", run_id="run")
        drain(session)
        assert not requests
        assert session.final_output("run")["error"] is True
        assert len(attempts) == 1
    finally:
        session.close()
