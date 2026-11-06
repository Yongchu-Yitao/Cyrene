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
async def test_agent_message_does_not_cancel_unfinished_run(tmp_path, monkeypatch):
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
        await runtime.send(config, "new request", run_id="new", metadata={"agent_originated": True})
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


def test_partial_context_write_failure_recovers_without_duplicate_model_call(tmp_path, monkeypatch):
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
        assert len(requests) == 1
        assert "required policy" in str(requests[0])
        assert not session.final_output("run").get("error")
        assert len(attempts) >= 2
    finally:
        session.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("stage", ["model", "tools", "context"])
@pytest.mark.parametrize("interrupt_retry", [False, True])
@pytest.mark.parametrize("retry_branch", [False, True])
@pytest.mark.parametrize("restart", [False, True])
async def test_user_turn_replaces_interrupted_checkpoint_without_resuming_old_work(tmp_path, monkeypatch, stage, interrupt_retry, retry_branch, restart):
    from cyrene.core.context import ContextStoreRouter
    from cyrene.workbench.core_adapter.bridge import WorkbenchSessionBridge

    seed = make_session(tmp_path)
    tree_id, root_id = seed.tree.id, seed.tree.root_id
    seed.close()
    store = ContextStoreRouter(tmp_path / "data" / "context")
    try:
        user = store.mount(tree_id, root_id, {
            "role": "user", "content": "hello", "run_id": "old", "trigger_model": True,
        })
        if stage == "tools":
            store.mount(tree_id, user.id, {
                "role": "assistant", "run_id": "old", "content": "",
                "tool_calls": [{"id": "old-effect", "name": "Effect", "arguments": {}}],
            })
        elif stage == "context":
            store.mount(tree_id, user.id, {
                "role": "context", "run_id": "old", "trigger_model": False,
                "source_node_id": user.id,
            })
    finally:
        store.close()

    calls, effects = [], []

    async def model(arguments, context):
        calls.append(context.data.get("run_id"))
        return {"content": "retried answer", "tool_calls": []}

    async def effect(arguments, context):
        effects.append("executed")
        return "effect"

    runtime = ConversationRuntime()

    def checkpoint(*args):
        store = ContextStoreRouter(tmp_path / "data" / "context")
        try:
            return context_checkpoint_from_nodes(store.get_subtree(tree_id, root_id))
        finally:
            store.close()

    def open_bridge(config, *, resume_on_restore=True, **kwargs):
        session = make_session(tmp_path, model, tree_id=tree_id, resume_on_restore=resume_on_restore)
        session.registry.register_pack(PluginPack("effects", "test", (
            Plugin("Effect", "test", {"type": "object"}, effect),
        )), source="test")
        bridge = WorkbenchSessionBridge(session)
        if interrupt_retry and checkpoint()["status"] == "running":
            async def interrupted_submit(*args, **kwargs):
                raise RuntimeError("crash before retry submit")
            bridge.submit_result = interrupted_submit
        return bridge

    monkeypatch.setattr(runtime, "context_checkpoint", checkpoint)
    monkeypatch.setattr(runtime, "_open_bridge", open_bridge)
    monkeypatch.setattr(runtime, "kick_commit_outbox", lambda _: None)
    config = ConversationConfig(session_id=tree_id, workspace_dir=str(tmp_path), db_path="", retry=retry_branch)
    assert checkpoint()["status"] == "running"
    if restart:
        from cyrene.workbench.chat.chat_runs import ChatRunManager
        from cyrene.platform.database import init_db
        db = str(tmp_path / "workbench.db")
        await init_db(db)
        manager = ChatRunManager()
        manager.configure(db)
        manager.conversation_runtime = runtime
        manager._repository.write({"chats": [{"id": tree_id, "status": "idle"}]})
        manager.startup()
        manager.startup()  # Recovery is idempotent, including previously idle projections.
        recovered = manager._repository.read()["chats"][0]
        assert recovered["lastRun"]["status"] == "cancelled"
        assert recovered["lastRun"]["terminationReason"] == "process_restarted"
        assert checkpoint()["status"] == "cancelled"
        assert calls == effects == []
    try:
        if interrupt_retry:
            result = await runtime.send(config, "hello", run_id="interrupted-retry", publish=lambda _: None)
            assert result.text == "retried answer"
            assert checkpoint()["status"] == "completed"
            assert calls == ["interrupted-retry"]
        for run_id in ("retry-1", "retry-2"):
            result = await asyncio.wait_for(runtime.send(config, "hello", run_id=run_id, publish=lambda _: None), 5)
            assert result.text == "retried answer"
            assert checkpoint()["status"] == "completed"
            assert checkpoint()["run_id"] == run_id
        assert calls == (["interrupted-retry"] if interrupt_retry else []) + ["retry-1", "retry-2"]
        assert not effects
        store = ContextStoreRouter(tmp_path / "data" / "context")
        try:
            values = [node.value for node in store.get_subtree(tree_id, root_id)]
            reason = "process_restarted" if restart else ("replaced_by_retry" if retry_branch else "replaced_by_new_message")
            assert any(value.get("run_id") == "old" and value.get("cancel_reason") == reason
                       for value in values)
            assert any(value.get("role") == "user" and value.get("run_id") == "old"
                       and value.get("content") == "hello" for value in values)
            retries = [value for value in values if value.get("role") == "user" and value.get("run_id") == "retry-1"]
            assert len(retries) == 1
            if retry_branch:
                assert retries[0]["metadata"]["retry_of_run_id"] == ("interrupted-retry" if interrupt_retry else "old")
            else:
                assert "retry_of_run_id" not in retries[0].get("metadata", {})
        finally:
            store.close()
    finally:
        await runtime.shutdown(grace_seconds=0)


def test_paused_restore_fences_already_queued_and_late_hook_transitions(tmp_path, monkeypatch):
    from cyrene.core.context import ContextStoreRouter
    from cyrene.core.session import AgentSession

    seed = make_session(tmp_path)
    tree_id, root_id = seed.tree.id, seed.tree.root_id
    seed.close()
    with ContextStoreRouter(tmp_path / 'data' / 'context') as store:
        old_user = store.mount(tree_id, root_id, {
            'role': 'user', 'content': 'old request', 'run_id': 'old', 'trigger_model': True,
        })
    calls = []
    async def model(arguments, context):
        calls.append(context.data['run_id'])
        return {'content': 'new answer', 'tool_calls': []}

    restore = AgentSession._restore
    def queued_before_restore(session, *, resume=True):
        # A persisted ContextChange Hook may enqueue before recovery chooses
        # to pause. Force that order instead of depending on thread timing.
        session._enqueue_transition('advance', old_user)
        restore(session, resume=resume)
    monkeypatch.setattr(AgentSession, '_restore', queued_before_restore)
    reopened = make_session(tmp_path, model, tree_id=tree_id, resume_on_restore=False)
    try:
        reopened._enqueue_transition('advance', old_user)  # A late Hook too.
        asyncio.run(reopened.hooks.drain())
        reopened._wait_for_transitions()
        assert calls == []
        assert reopened.snapshot()['status'] != 'idle'
        asyncio.run(reopened.cancel('replaced_by_retry'))
        reopened.prepare_retry()
        reopened.submit('new request', run_id='new')
        drain(reopened)
        assert calls == ['new']
        assert reopened.final_output('new')['content'] == 'new answer'
    finally:
        reopened.close()


@pytest.mark.asyncio
async def test_exhausted_runtime_retries_stop_checkpoint_and_accept_new_command(tmp_path, monkeypatch):
    from cyrene.core.context import ContextStoreRouter
    from cyrene.workbench.core_adapter.bridge import WorkbenchSessionBridge

    seed = make_session(tmp_path)
    tree_id, root_id = seed.tree.id, seed.tree.root_id
    seed.close()
    with ContextStoreRouter(tmp_path / "data" / "context") as store:
        store.mount(tree_id, root_id, {
            "role": "user", "content": "old request", "run_id": "old", "trigger_model": True,
        })
    runtime = ConversationRuntime()
    monkeypatch.setattr(runtime, "_state_root", lambda config=None: tmp_path / "data")
    monkeypatch.setattr(runtime, "kick_commit_outbox", lambda _: None)
    attempts = []
    model_calls = []

    async def model(arguments, context):
        model_calls.append(context.data.get("run_id"))
        return {"content": "new answer", "tool_calls": []}

    def open_bridge(config, **kwargs):
        # Inject a persistent failure at the bridge resume boundary. Keep the
        # real tree paused so cleanup must settle it without executing old work.
        bridge = WorkbenchSessionBridge(make_session(
            tmp_path, model, tree_id=tree_id, resume_on_restore=False,
        ))
        async def broken_resume(**kwargs):
            attempts.append("resume")
            raise OSError("persistent bridge failure")
        bridge.resume_result = broken_resume
        return bridge

    monkeypatch.setattr(runtime, "_open_bridge", open_bridge)
    config = ConversationConfig(session_id=tree_id, workspace_dir=str(tmp_path), db_path="")
    try:
        with pytest.raises(OSError, match="persistent bridge failure"):
            await runtime.send(config, "old request", run_id="old")
        assert len(attempts) == 4  # Initial attempt plus three retries.
        assert runtime.context_checkpoint(tree_id)["status"] == "cancelled"
        with ContextStoreRouter(tmp_path / "data" / "context") as store:
            nodes = store.get_subtree(tree_id, root_id)
            assert any(n.value.get("cancel_reason") == "runtime_retry_exhausted" for n in nodes)
        result = await runtime.send(config, "new command", run_id="new")
        assert result.text == "new answer"
        assert model_calls == ["new"]
        assert runtime.context_checkpoint(tree_id)["status"] == "completed"
    finally:
        await runtime.shutdown(grace_seconds=0)
