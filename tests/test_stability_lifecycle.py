"""Regression coverage for execution ownership and lossless ACP exchanges."""
from __future__ import annotations

import asyncio
import json
import threading
from types import SimpleNamespace

import pytest

from cyrene.agents.acp_protocol import ACP_METHOD_REQUEST_PERMISSION
from cyrene.agents.acp_transport import AcpStdioTransport, AcpTransportError
from cyrene.platform.task_lifecycle import TaskShutdownTimeout, cancel_and_wait
from cyrene.workbench.core_adapter.conversation_runtime import ConversationConfig, ConversationRuntime


def blocked_transport(**kwargs):
    entered = asyncio.Event()
    class Stdin:
        def write(self, data):
            json.loads(data)
        async def drain(self):
            entered.set()
            await asyncio.Event().wait()
    process = SimpleNamespace(returncode=None, pid=1, stdin=Stdin())
    def terminate():
        process.returncode = -15
    process.terminate = terminate
    transport = AcpStdioTransport("fake-agent", **kwargs)
    transport._started = True
    transport.process = process
    return transport, entered


@pytest.mark.asyncio
async def test_request_deadline_covers_pipe_backpressure():
    transport, _ = blocked_transport(request_timeout=0.02)
    with pytest.raises(AcpTransportError, match="timed out"):
        await asyncio.wait_for(transport.request("initialize"), 1)
    assert not transport._pending
    assert transport._closed
    assert transport.process.returncode is not None


@pytest.mark.asyncio
async def test_unlimited_prompt_still_has_finite_write_deadline():
    transport, _ = blocked_transport(write_timeout=0.02)
    with pytest.raises(AcpTransportError, match="write did not settle"):
        await asyncio.wait_for(transport.request("session/prompt", timeout=0), 1)
    assert not transport._pending


@pytest.mark.asyncio
async def test_cancel_during_write_retires_stream_and_releases_request():
    transport, entered = blocked_transport()
    request = asyncio.create_task(transport.request("initialize"))
    await entered.wait()
    request.cancel()
    with pytest.raises(asyncio.CancelledError):
        await request
    assert not transport._pending
    assert transport._closed
    with pytest.raises(AcpTransportError):
        await transport.request("initialize")


@pytest.mark.asyncio
async def test_queue_overflow_is_terminal_and_does_not_evict_permission():
    transport = AcpStdioTransport("fake-agent", notification_limit=2)
    pending = asyncio.get_running_loop().create_future()
    transport._pending[1] = pending
    permission = {"jsonrpc": "2.0", "id": 42, "method": ACP_METHOD_REQUEST_PERMISSION, "params": {}}
    delta = {"jsonrpc": "2.0", "method": "session/update", "params": {}}
    transport._queue_notification(permission)
    transport._queue_notification(delta)
    transport._queue_notification(delta)
    with pytest.raises(AcpTransportError) as raised:
        await pending
    assert raised.value.detail["kind"] == "event_overflow"
    assert raised.value.retryable is False
    iterator = transport.notifications()
    assert await anext(iterator) == permission
    assert await anext(iterator) == delta
    with pytest.raises(AcpTransportError):
        await anext(iterator)
    assert transport._notifications.empty()


@pytest.mark.asyncio
async def test_load_replay_filter_preserves_peer_requests():
    transport = AcpStdioTransport("fake-agent", notification_limit=4)
    permission = {"jsonrpc": "2.0", "id": 42, "method": ACP_METHOD_REQUEST_PERMISSION, "params": {}}
    delta = {"jsonrpc": "2.0", "method": "session/update", "params": {}}
    for frame in (delta, permission, delta):
        transport._queue_notification(frame)
    assert await transport.discard_notifications_until_quiet(quiet_seconds=0.01) == 2
    assert transport._notifications.get_nowait() == permission
    assert transport._notifications.empty()


@pytest.mark.asyncio
async def test_shutdown_deadline_does_not_recancel_or_forget_live_finalizer():
    entered, finalizing, release = asyncio.Event(), asyncio.Event(), asyncio.Event()
    async def work():
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            finalizing.set()
            await release.wait()
    task = asyncio.create_task(work(), name="owned-finalizer")
    await entered.wait()
    try:
        with pytest.raises(TaskShutdownTimeout) as raised:
            await cancel_and_wait([task], timeout=0.02)
        assert raised.value.pending == {task}
        assert finalizing.is_set()
        assert not task.done()
        with pytest.raises(TaskShutdownTimeout):
            await cancel_and_wait([task], timeout=0.02)
        assert task.cancelling() == 1
    finally:
        release.set()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
async def test_shutdown_stops_operation_before_releasing_bridge(tmp_path, monkeypatch):
    runtime = ConversationRuntime()
    entered = asyncio.Event()
    events = []
    class Bridge:
        def close(self):
            events.append("closed")
    monkeypatch.setattr(runtime, "_open_bridge", lambda *args, **kwargs: Bridge())
    async def operate(bridge):
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            events.append("operation-stopped")
    config = ConversationConfig(session_id="chat", workspace_dir=str(tmp_path), db_path="")
    waiter = asyncio.create_task(runtime._with_bridge(config, operate, publish=None))
    await entered.wait()
    await runtime.shutdown(grace_seconds=0)
    with pytest.raises(asyncio.CancelledError):
        await waiter
    assert events == ["operation-stopped", "closed"]
    assert not runtime._operations
    assert not runtime._active
    with pytest.raises(RuntimeError, match="shutting down"):
        await runtime._with_bridge(config, operate, publish=None)
    runtime.kick_commit_outbox("chat")
    assert not runtime._outbox_tasks


@pytest.mark.asyncio
@pytest.mark.parametrize("phase", ["open", "close"])
async def test_shutdown_retains_ownership_during_worker_io(tmp_path, monkeypatch, phase):
    runtime = ConversationRuntime()
    loop = asyncio.get_running_loop()
    entered = asyncio.Event()
    release = threading.Event()
    closed = []
    def blocking_phase():
        loop.call_soon_threadsafe(entered.set)
        assert release.wait(3)
    class Bridge:
        def close(self):
            if phase == "close":
                blocking_phase()
            closed.append(True)
    def open_bridge(*args, **kwargs):
        if phase == "open":
            blocking_phase()
        return Bridge()
    monkeypatch.setattr(runtime, "_open_bridge", open_bridge)
    async def operate(bridge):
        return None
    config = ConversationConfig(session_id="chat", workspace_dir=str(tmp_path), db_path="")
    waiter = asyncio.create_task(runtime._with_bridge(config, operate, publish=None))
    await entered.wait()
    try:
        with pytest.raises(TaskShutdownTimeout):
            await runtime.shutdown(grace_seconds=0, timeout=0.02)
        assert runtime._operations
        assert runtime._chat_lock("chat").locked()
        assert not closed
    finally:
        release.set()
        await asyncio.gather(waiter, return_exceptions=True)
        await runtime.shutdown(grace_seconds=0)
    assert closed == [True]
    assert not runtime._operations


@pytest.mark.asyncio
async def test_shutdown_closes_real_session_without_cancelling_durable_run(tmp_path, monkeypatch):
    from test_task_contexts import make_session
    from cyrene.core.context import ContextStoreRouter
    from cyrene.workbench.core_adapter.conversation_runtime import context_checkpoint_from_nodes
    loop = asyncio.get_running_loop()
    entered = asyncio.Event()
    async def model(arguments, context):
        loop.call_soon_threadsafe(entered.set)
        await asyncio.Event().wait()
    session = make_session(tmp_path, model)
    tree_id, root_id = session.tree.id, session.tree.root_id
    runtime = ConversationRuntime()
    monkeypatch.setattr(runtime, "_open_bridge", lambda *args, **kwargs: SimpleNamespace(close=session.close))
    async def operate(bridge):
        session.submit("work", run_id="recover-me")
        await session.drain()
    config = ConversationConfig(session_id="chat", workspace_dir=str(tmp_path), db_path="")
    waiter = asyncio.create_task(runtime._with_bridge(config, operate, publish=None))
    try:
        await asyncio.wait_for(entered.wait(), 3)
        await runtime.shutdown(grace_seconds=0, timeout=2)
    finally:
        await asyncio.gather(waiter, return_exceptions=True)
        session.close()
    store = ContextStoreRouter(tmp_path / "data" / "context")
    try:
        checkpoint = context_checkpoint_from_nodes(store.get_subtree(tree_id, root_id))
        assert checkpoint["run_id"] == "recover-me"
        assert checkpoint["status"] == "running"
    finally:
        store.close()


@pytest.mark.asyncio
async def test_shutdown_preserves_unacknowledged_commit(tmp_path, monkeypatch):
    from cyrene.workbench.chat import chat_repository
    runtime = ConversationRuntime()
    entered = asyncio.Event()
    event = {"event_id": "commit", "run_id": "run", "node_id": "node"}
    acknowledged, failed = [], []
    class Repository:
        def __init__(self, path):
            pass
        def pending_commit_events(self, chat, *, limit):
            return [event]
        def complete_commit_event(self, event_id):
            acknowledged.append(event_id)
        def fail_commit_event(self, event_id, error):
            failed.append(event_id)
    class Bridge:
        async def commit_public_turn(self, *args):
            entered.set()
            await asyncio.Event().wait()
        def close(self):
            pass
    monkeypatch.setattr(chat_repository, "ChatRepository", Repository)
    monkeypatch.setattr(runtime, "_open_bridge", lambda *args, **kwargs: Bridge())
    config = ConversationConfig(session_id="chat", workspace_dir=str(tmp_path), db_path="")
    runtime._configs["chat"] = config
    runtime.kick_commit_outbox("chat")
    await entered.wait()
    await runtime.shutdown(grace_seconds=0)
    assert not acknowledged
    assert failed == ["commit"]
    assert not runtime._operations
    assert not runtime._outbox_tasks


@pytest.mark.asyncio
async def test_manager_shutdown_reaches_the_execution_owner(tmp_path, monkeypatch):
    from cyrene.workbench.chat.chat_runs import ChatRunManager
    manager = ChatRunManager(shutdown_grace_seconds=0)
    entered = asyncio.Event()
    closed = []
    cancelled = []
    class Bridge:
        session = SimpleNamespace(request_cancel=lambda reason: cancelled.append(reason))
        def close(self):
            closed.append(True)
    monkeypatch.setattr(manager.conversation_runtime, "_open_bridge", lambda *args, **kwargs: Bridge())
    config = ConversationConfig(session_id="chat", workspace_dir=str(tmp_path), db_path="")
    async def operate(bridge):
        entered.set()
        await asyncio.Event().wait()
    async def runner(run):
        await manager.conversation_runtime._with_bridge(config, operate, publish=None)
    run, _ = manager.start_or_get("chat", {"type": "ack"}, runner, stream=False)
    await entered.wait()
    await asyncio.wait_for(manager.shutdown(), 2)
    assert closed == [True]
    assert cancelled == ["shutdown_timeout"]
    assert run.done.is_set()
    assert not manager._leases
    assert not manager.conversation_runtime._operations


@pytest.mark.asyncio
async def test_grace_period_allows_reply_and_its_commit_to_finish(tmp_path, monkeypatch):
    runtime = ConversationRuntime()
    entered, release = asyncio.Event(), asyncio.Event()
    committed = []
    class Bridge:
        def close(self):
            pass
    monkeypatch.setattr(runtime, "_open_bridge", lambda *args, **kwargs: Bridge())
    config = ConversationConfig(session_id="chat", workspace_dir=str(tmp_path), db_path="")
    async def commit(**kwargs):
        async def apply(bridge):
            committed.append("commit")
        await runtime._with_bridge(config, apply, publish=None)
    monkeypatch.setattr(runtime, "drain_commit_outbox", commit)
    async def operate(bridge):
        entered.set()
        await release.wait()
        runtime.kick_commit_outbox("chat")
    waiter = asyncio.create_task(runtime._with_bridge(config, operate, publish=None))
    await entered.wait()
    shutdown = asyncio.create_task(runtime.shutdown(grace_seconds=1))
    await asyncio.sleep(0)
    release.set()
    await asyncio.gather(shutdown, waiter)
    assert committed == ["commit"]
