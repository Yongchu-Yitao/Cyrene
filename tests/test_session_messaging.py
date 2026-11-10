from __future__ import annotations

import asyncio
import copy
from types import SimpleNamespace

import pytest

from cyrene.core.plugin import Plugin, PluginContext, PluginPack, PluginRegistry
from cyrene.plugins.builtin.cyrene_sessions import plugin_pack, list_sessions, send_session_message
from cyrene.plugins.builtin.cyrene_sessions.service import SessionMessagingService
from cyrene.plugins.builtin.cyrene_sessions.store import MessageStore
from cyrene.workbench.application.inbox import WorkbenchAgentInbox, WorkbenchGuidanceChannel
from cyrene.workbench.control.control_ports import WorkbenchChatApplicationPort


class Repository:
    def __init__(self):
        self.chats = {key: {"id": key, "title": f"Chat {key}", "createdAt": key,
                            "messages": []} for key in ("a", "b", "c")}

    def get(self, key):
        return copy.deepcopy(self.chats.get(key))

    def read_summaries(self):
        return {"chats": [self.get(key) for key in self.chats]}


class Port:
    def __init__(self):
        self.repository = Repository()
        self.service = SimpleNamespace(repository=self.repository)
        self.runs = {}
        self.checkpoints = {}
        self.run_manager = SimpleNamespace(
            get=self.runs.get,
            conversation_runtime=SimpleNamespace(context_checkpoint=self.checkpoints.get),
        )
        self.deliveries = []
        self.admitted = set()

    async def agent_message_admitted(self, target, request_id):
        return (target, request_id) in self.admitted

    async def dispatch_agent_message(self, target, content, **kwargs):
        self.admitted.add((target, kwargs["client_request_id"]))
        self.deliveries.append((target, content, kwargs))
        self.repository.chats[target]["messages"].append({
            "clientRequestId": kwargs["client_request_id"], "content": content,
        })
        return {"status": "guided" if target in self.runs else "started"}


async def service_without_worker(tmp_path, port):
    service = SessionMessagingService(tmp_path, lambda: port)
    # Bind normally, then remove timing from tests; tick is the production worker operation.
    await service.start()
    service.task.cancel()
    await asyncio.gather(service.task, return_exceptions=True)
    return service


@pytest.mark.asyncio
async def test_two_tools_discovery_send_idle_wake_and_reply(tmp_path):
    port = Port()
    ready = asyncio.Event()
    ready.set()
    port.runs.update({key: SimpleNamespace(status="running", ready=ready) for key in ("a", "b")})
    service = await service_without_worker(tmp_path, port)
    context = PluginContext(data={"session_id": "a", "agent_id": "main"},
                            services={"session_messaging": service})
    try:
        assert {tool.name for tool in plugin_pack.plugins} == {"list_sessions", "send_session_message"}
        assert await list_sessions({}, context) == {"sessions": [{"session_id": "b", "title": "Chat b"}]}
        # The target becomes idle between list and send.
        del port.runs["b"]
        sent = await send_session_message({"target_session_id": "b", "content": "question"}, context)
        assert sent["status"] == "queued"
        assert port.deliveries == []
        await service.tick()
        assert service.store.get(sent["message_id"])["status"] == "delivered"
        target, text, provenance = port.deliveries[0]
        assert target == "b" and "question" in text and "Chat a" in text
        assert '"session_id": "a"' in text
        assert provenance["origin_session_id"] == "a"
        context.data["session_id"] = "b"
        reply = await send_session_message({"target_session_id": "a", "content": "answer"}, context)
        assert reply["status"] == "delivered"
        assert port.deliveries[1][0] == "a"
        await service.tick()
        assert len(port.deliveries) == 2
        assert port.deliveries[1][0] == "a"
        assert port.deliveries[1][2]["origin_session_id"] == "b"
    finally:
        await service.stop()


@pytest.mark.asyncio
async def test_waiting_checkpoint_clear_delete_and_external_target(tmp_path):
    port = Port()
    service = await service_without_worker(tmp_path, port)
    try:
        port.repository.chats["b"]["pendingQuestion"] = {"id": "permission"}
        sent = await service.send("a", "b", "not an approval", "effect")
        await service.tick()
        assert not port.deliveries
        del port.repository.chats["b"]["pendingQuestion"]
        port.checkpoints["b"] = {"status": "awaiting_user"}
        await service.tick()
        assert not port.deliveries
        port.checkpoints.clear()
        port.repository.chats["b"]["messageGeneration"] = 1
        await service.tick()
        assert service.store.get(sent["message_id"])["status"] == "failed"
        sent = await service.send("a", "c", "hello", "other")
        del port.repository.chats["c"]
        await service.tick()
        assert service.store.get(sent["message_id"])["status"] == "failed"
        port.repository.chats["b"]["agent"] = {"agentId": "external", "driver": "acp", "installationId": "ext"}
        with pytest.raises(ValueError, match="built-in"):
            await service.send("a", "b", "hello", "external")
        with pytest.raises(ValueError, match="another"):
            await service.send("a", "a", "hello", "self")
    finally:
        await service.stop()


@pytest.mark.asyncio
async def test_restart_idempotency_and_lost_response(tmp_path):
    port = Port()
    service = await service_without_worker(tmp_path, port)
    first = await service.send("a", "b", "hello", "run:call")
    assert await service.send("a", "b", "hello", "run:call") == first
    with pytest.raises(ValueError, match="different"):
        await service.send("a", "b", "changed", "run:call")
    await service.stop()
    restarted = await service_without_worker(tmp_path, port)
    try:
        await restarted.tick()
        assert len(port.deliveries) == 1
        # Crash after recipient's admission, before sender's delivered acknowledgement.
        restarted.store.update(first["message_id"], "queued")
        with restarted.store.connect() as db:
            db.execute("UPDATE messages SET next_attempt=0")
        await restarted.tick()
        assert len(port.deliveries) == 1
        assert restarted.store.get(first["message_id"])["status"] == "delivered"
    finally:
        await restarted.stop()


@pytest.mark.asyncio
async def test_cross_loop_sender_and_plugin_shutdown(tmp_path):
    service = await service_without_worker(tmp_path, Port())
    try:
        result = await asyncio.to_thread(lambda: asyncio.run(service.send("a", "b", "hello", "effect")))
        assert result["status"] == "queued"
    finally:
        await service.stop()
    with pytest.raises(RuntimeError, match="not running"):
        await service.list_sessions("a")


def test_store_limits_and_fifo(tmp_path):
    store = MessageStore(tmp_path / "messages.sqlite3")
    first = store.accept("a", "b", "first", "A", "1", "version")
    store.accept("a", "b", "second", "A", "2", "version")
    other = store.accept("a", "c", "independent", "A", "3", "version")
    assert [row["id"] for row in store.pending()] == [first["id"], other["id"]]
    for index in range(17):
        store.accept("a", "c", "hello", "A", str(index+4), "version")
    with pytest.raises(ValueError, match="rate limit"):
        store.accept("a", "c", "hello", "A", "limit", "version")


def test_delivery_lease_excludes_second_worker_and_recovers_after_crash(tmp_path):
    store = MessageStore(tmp_path / "queue.sqlite")
    row = store.accept("a", "b", "hello", "A", "effect", "version")
    second = MessageStore(tmp_path / "queue.sqlite")
    assert store.claim(row["id"])
    assert not second.claim(row["id"])
    assert second.pending() == []
    with store.connect() as db:
        db.execute("UPDATE messages SET lease_until=0")
    assert second.pending()[0]["id"] == row["id"]
    assert second.claim(row["id"])


@pytest.mark.asyncio
async def test_transient_delivery_failure_retries_same_message_id(tmp_path):
    port = Port()
    service = await service_without_worker(tmp_path, port)
    dispatch = port.dispatch_agent_message

    async def unavailable(*args, **kwargs):
        raise RuntimeError("temporary failure")

    try:
        sent = await service.send("a", "b", "hello", "effect")
        port.dispatch_agent_message = unavailable
        await service.tick()
        row = service.store.get(sent["message_id"])
        assert row["status"] == "queued" and row["attempts"] == 1
        assert row["error"] == "temporary failure"
        with service.store.connect() as db:
            db.execute("UPDATE messages SET next_attempt=0")
        port.dispatch_agent_message = dispatch
        await service.tick()
        assert port.deliveries[0][2]["client_request_id"] == sent["message_id"]
        assert service.store.get(sent["message_id"])["status"] == "delivered"
    finally:
        await service.stop()


@pytest.mark.asyncio
async def test_peer_input_steers_like_user_guidance_without_granting_authorization(tmp_path):
    inbox = WorkbenchAgentInbox("b", run_id="run-b", db_path=str(tmp_path / "inbox.sqlite"))
    channel = WorkbenchGuidanceChannel(inbox)
    channel.bind_owner_loop()
    waiter = asyncio.create_task(channel.wait())
    try:
        await inbox.put_guidance("peer", client_request_id="peer", agent_originated=True, origin_session_id="a")
        assert await asyncio.wait_for(waiter, 1)
        assert channel.has_pending
        assert inbox._guidance_signal.is_set()
        await inbox.put_guidance("human", client_request_id="human")
        assert await asyncio.wait_for(waiter, 1)
        events = await channel.collect()
        assert len(events) == 2
        from cyrene.plugins.builtin.cyrene_guidance.service import GuidanceService
        value = GuidanceService(None).node_value(events, run_id="run-b")
        assert value["authorization_request"] == "human"
        assert value["metadata"]["raw_guidance"] == "human"
        assert value["metadata"]["contains_agent_messages"]
        assert "peer" in value["content"]
        await channel.acknowledge(events)
        assert not channel.has_pending
    finally:
        waiter.cancel()
        await asyncio.gather(waiter, return_exceptions=True)
        channel.close()
        await inbox.close(termination_reason="completed")


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["", "task_notification"])
async def test_recovered_agent_guidance_preserves_notification_queueing(tmp_path, kind):
    path = str(tmp_path / "inbox.sqlite")
    original = WorkbenchAgentInbox("b", run_id="before-restart", db_path=path)
    recovered = None
    channel = None
    try:
        await original.put_guidance("updated input", client_request_id="update",
                                    agent_originated=True, origin_session_id="a",
                                    agent_message_kind=kind)
        # Reopen durable pending rows as a new run, before acknowledging them.
        recovered = WorkbenchAgentInbox("b", run_id="after-restart", db_path=path)
        channel = WorkbenchGuidanceChannel(recovered)
        channel.bind_owner_loop()
        assert channel.has_pending
        assert recovered._guidance_signal.is_set() == (kind != "task_notification")
        assert channel._interrupt_pending == (kind != "task_notification")
        if kind != "task_notification":
            assert await asyncio.wait_for(channel.wait(), 1)
        events = await channel.collect()
        assert len(events) == 1 and events[0]["payload"]["agent_message_kind"] == kind
        await channel.acknowledge(events)
    finally:
        if channel:
            channel.close()
        if recovered:
            await recovered.close(termination_reason="completed")
        await original.close(termination_reason="completed")


@pytest.mark.asyncio
async def test_task_notifications_queue_without_masking_later_steering(tmp_path):
    inbox = WorkbenchAgentInbox("b", run_id="run-b", db_path=str(tmp_path / "inbox.sqlite"))
    channel = WorkbenchGuidanceChannel(inbox)
    channel.bind_owner_loop()
    waiter = asyncio.create_task(channel.wait())
    try:
        await inbox.put_guidance("task completed", client_request_id="notification",
            agent_originated=True, origin_session_id="task", agent_message_kind="task_notification")
        await asyncio.sleep(0)
        assert channel.has_pending and not waiter.done()
        assert not inbox._guidance_signal.is_set()
        notifications = await channel.collect()
        channel.requeue(notifications)
        await asyncio.sleep(0)
        assert channel.has_pending and not waiter.done()
        assert not channel._interrupt_pending

        await inbox.put_guidance("change the goal", client_request_id="update",
            agent_originated=True, origin_session_id="owner")
        assert await asyncio.wait_for(waiter, 1)
        assert inbox._guidance_signal.is_set()
        # A later notification must not clear an already-pending steer signal.
        await inbox.put_guidance("another task completed", client_request_id="notification-2",
            agent_originated=True, origin_session_id="task-2", agent_message_kind="task_notification")
        assert await asyncio.wait_for(channel.wait(), 1)
        events = await channel.collect()
        assert [event["payload"]["text"] for event in events] == [
            "task completed", "change the goal", "another task completed"]
        await channel.acknowledge(events)
        assert not channel.has_pending
    finally:
        waiter.cancel()
        await asyncio.gather(waiter, return_exceptions=True)
        channel.close()
        await inbox.close(termination_reason="completed")


@pytest.mark.asyncio
async def test_real_dispatch_port_idle_and_running_provenance(tmp_path):
    calls = []
    port = object.__new__(WorkbenchChatApplicationPort)
    port.run_manager = SimpleNamespace(get=lambda _: None)
    port.service = SimpleNamespace(repository=SimpleNamespace(
        _database=lambda: str(tmp_path / "app.db"), get=lambda _: {"id": "b"}))
    admitted = False

    async def has_admission(*_):
        return admitted

    port.agent_message_admitted = has_admission

    async def send(chat_id, body):
        nonlocal admitted
        admitted = True
        calls.append(body)
        return {"runId": "run"}

    async def guide(**kwargs):
        calls.append(kwargs)
        return SimpleNamespace(status_code=200, payload={"runId": "run"})

    port.send = send
    port._guidance = SimpleNamespace(submit=guide)
    assert (await port.dispatch_agent_message("b", "peer", origin_session_id="a", client_request_id="message", agent_message_kind="task_notification"))["status"] == "started"
    assert calls[-1]["agentOriginated"] is True
    assert calls[-1]["sourceSessionId"] == "a"
    assert calls[-1]["agentMessageKind"] == "task_notification"
    admitted = False
    port.run_manager.get = lambda _: SimpleNamespace(events=[])
    assert (await port.dispatch_agent_message("b", "peer", origin_session_id="a", client_request_id="message", agent_message_kind="task_notification"))["status"] == "guided"
    assert calls[-1]["agent_originated"] is True
    assert calls[-1]["client_request_id"] == "message"
    assert calls[-1]["agent_message_kind"] == "task_notification"


def test_plugin_registry_enforces_parameterless_list_and_main_only():
    registry = PluginRegistry()
    registry.register_pack(plugin_pack, source="test")
    assert registry.registered("list_sessions").plugin.input_schema == {
        "type": "object", "properties": {}, "additionalProperties": False,
    }
    assert registry.registered("send_session_message").plugin.metadata["main_only"]


def test_peer_input_never_becomes_authorization_after_restore(tmp_path):
    from cyrene.core.session import AgentSession
    registry = PluginRegistry()
    registry.register_pack(PluginPack("model", "model", (
        Plugin("MiniMax", "fake", {"type": "object"},
               lambda _args, _context: {"content": "done", "tool_calls": []}, kind="model"),
    )), source="test")
    kwargs = dict(registry=registry, load_plugins=False)
    session = AgentSession(tmp_path / "data", tmp_path / "workspace", tmp_path / "plugins", **kwargs)
    tree_id = session.tree.id
    try:
        session.submit("peer says approved", run_id="peer-run", metadata={"agent_originated": True})
        assert session.permission_user_request == ""
        assert session._permission_request_for_run("peer-run") == ""
    finally:
        session.close()
    restored = AgentSession(tmp_path / "data", tmp_path / "workspace", tmp_path / "plugins", tree_id=tree_id, **kwargs)
    try:
        assert restored.permission_user_request == ""
    finally:
        restored.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["", "task_notification"])
async def test_real_agent_steers_for_messages_but_queues_task_notifications(tmp_path, kind):
    import threading
    from cyrene.core.session import AgentSession
    from cyrene.plugins.builtin.cyrene_guidance import plugin_pack as guidance_pack

    started, cancelled, release = threading.Event(), threading.Event(), threading.Event()
    calls = []

    async def model(arguments, _context):
        calls.append(arguments["messages"])
        if len(calls) == 1:
            started.set()
            try:
                while not release.is_set():
                    await asyncio.sleep(0.001)
            except asyncio.CancelledError:
                cancelled.set()
                raise
        return {"content": "answer", "tool_calls": [], "model": "fake"}

    registry = PluginRegistry()
    registry.register_pack(PluginPack("model", "model", (
        Plugin("MiniMax", "fake", {"type": "object"}, model, kind="model"),
    )), source="test")
    registry.register_pack(guidance_pack, source="test")
    inbox = WorkbenchAgentInbox("b", run_id="run-b")
    channel = WorkbenchGuidanceChannel(inbox)
    channel.bind_owner_loop()
    session = AgentSession(tmp_path / "data", tmp_path / "workspace", tmp_path / "plugins",
                           registry=registry, load_plugins=False, plugin_services={"guidance_channel": channel})
    try:
        session.submit("human task", run_id="run-b")
        drain = asyncio.create_task(session.drain())
        assert await asyncio.wait_for(asyncio.to_thread(started.wait), 2)
        await inbox.put_guidance("peer finding", agent_originated=True, origin_session_id="a",
                                 agent_message_kind=kind)
        if kind == "task_notification":
            await asyncio.sleep(0.01)
            assert not cancelled.is_set() and len(calls) == 1
            release.set()
        await asyncio.wait_for(drain, 5)
        assert cancelled.is_set() == (kind != "task_notification")
        assert len(calls) == 2
        assert "peer finding" in str(calls[-1])
        assert session.permission_user_request == "human task"
        assert inbox._guidance_pending_count == 0
    finally:
        release.set()
        session.close()
        channel.close()
        await inbox.close(termination_reason="completed")


@pytest.mark.asyncio
async def test_application_pack_lifecycle_and_service_visibility(tmp_path):
    from fastapi import FastAPI, APIRouter
    from cyrene.plugins import PluginApplicationHost, set_application_plugin_scope

    registry = PluginRegistry()
    registry.register_pack(plugin_pack, source="test")
    host = PluginApplicationHost(app=FastAPI(), registry=registry, bot=None,
                                 db_path=str(tmp_path / "app.sqlite"),
                                 data_directory=tmp_path, plugin_directory=tmp_path / "plugins")
    host.services["workbench_chat"] = Port()
    host.attach(APIRouter())
    set_application_plugin_scope(host)
    try:
        await host.startup()
        service = host.service("session_messaging")
        assert service is not None and not service.task.done()
        await service.send("a", "b", "persist across disable", "effect")
        registry.configure_activation(plugins={}, packs={"cyrene_sessions": False})
        await host.reconcile_activation()
        assert service.task is None
        assert host.service("session_messaging") is None
        registry.configure_activation(plugins={}, packs={"cyrene_sessions": True})
        await host.reconcile_activation()
        assert host.service("session_messaging") is service
        assert service.task is not None and not service.task.done()
        await service.tick()
        assert host.services["workbench_chat"].deliveries
    finally:
        await host.shutdown()
        set_application_plugin_scope(None)


def durable_port(tmp_path):
    from cyrene.workbench.chat.chat_runs import ChatRunManager
    manager = ChatRunManager()
    manager.configure(str(tmp_path / "app.sqlite"))
    manager._repository.write({"chats": [
        {"id": key, "title": key, "createdAt": key, "status": "idle", "messages": []}
        for key in ("a", "b", "c")
    ]})
    port = object.__new__(WorkbenchChatApplicationPort)
    port.run_manager = manager
    port.service = SimpleNamespace(repository=manager._repository)
    return port


@pytest.mark.asyncio
async def test_public_persist_without_runtime_admission_is_replayed_once(tmp_path, monkeypatch):
    from cyrene.workbench.http.workbench.chat_routes.run_send_routes import _SendOperation
    from cyrene.workbench.http.workbench.chat_routes.send_input import append_user_message, SendInput
    from cyrene.workbench.chat import chat_groups

    port = durable_port(tmp_path)
    service = await service_without_worker(tmp_path / "plugin", port)
    first = await service.send("a", "b", "hello", "effect")
    row = service.store.get(first["message_id"])
    content = service.render(row)
    source = SendInput(content, content, "", [], [])
    from cyrene.workbench.http.workbench.chat_routes.send_request import SendOrigin
    origin = SendOrigin.parse({"agentOriginated": True, "clientRequestId": row["id"], "sourceSessionId": "a"})
    op = object.__new__(_SendOperation)
    op.chat_id = "b"
    op.origin = origin
    op.chat = port.service.repository.get("b")
    op.base_chat = copy.deepcopy(op.chat)
    op.is_side_agent = False
    op.environment = SimpleNamespace(model=SimpleNamespace(candidate={}, agent_managed=False))
    op.service = SimpleNamespace(repository=port.service.repository, mark_user_activity=lambda *_: None)
    op.turn = append_user_message(op.chat, op.chat["messages"], source, origin, "2026-01-01", lambda _: "original-id")

    async def no_groups(*_):
        pass

    monkeypatch.setattr(chat_groups, "reconcile_session", no_groups)
    assert await op._persist_user_turn() is None
    # This is the actual persisted state at the crash window before _dispatch.
    port.run_manager.startup()
    assert not await port.agent_message_admitted("b", row["id"])
    calls = []

    async def replay(chat_id, text, **kwargs):
        calls.append(kwargs)
        chat = port.service.repository.get(chat_id)
        turn = append_user_message(chat, chat["messages"], source, origin, "later", lambda _: "wrong-new-id")
        assert turn.user_entry["id"] == "original-id"
        assert len(chat["messages"]) == 1
        port.service.repository.write_one(chat)

    port.dispatch_agent_message = replay
    try:
        await service.tick()
        assert len(calls) == 1
        assert service.store.get(row["id"])["status"] == "delivered"
    finally:
        await service.stop()


@pytest.mark.asyncio
async def test_committed_context_input_is_durable_receipt_after_restart(tmp_path):
    from cyrene.core.session import AgentSession
    port = durable_port(tmp_path)
    registry = PluginRegistry()
    registry.register_pack(PluginPack("model", "model", (
        Plugin("MiniMax", "fake", {"type": "object"}, lambda *_: {"content": "done"}, kind="model"),
    )), source="test")
    service = await service_without_worker(tmp_path / "plugin", port)
    sent = await service.send("a", "b", "hello", "effect")
    session = AgentSession(tmp_path / "agent-state", tmp_path / "work", tmp_path / "plugins",
                           registry=registry, load_plugins=False, tree_id="b")
    try:
        session.submit("hello", run_id="receiver-run", metadata={
            "agent_originated": True, "client_request_id": sent["message_id"], "origin_session_id": "a",
        })
    finally:
        session.close()
    assert await port.agent_message_admitted("b", sent["message_id"])
    async def unexpected(*_, **__):
        pytest.fail("A committed input must not be sent twice")
    port.dispatch_agent_message = unexpected
    try:
        await service.tick()
        assert service.store.get(sent["message_id"])["status"] == "delivered"
    finally:
        await service.stop()


@pytest.mark.asyncio
async def test_recovered_inbox_preserves_and_repairs_peer_provenance(tmp_path):
    from cyrene.workbench.sessions.session_presentation import WorkbenchSessionPresentation
    port = durable_port(tmp_path)
    inbox = WorkbenchAgentInbox("b", port.service.repository._database(), run_id="run-b")
    try:
        await inbox.put_guidance("peer", client_request_id="peer-id", public_message_id="public-id",
                                 agent_originated=True, origin_session_id="a", agent_message_kind="task_notification")
        port.run_manager._reconcile_inbox_guidance_messages()
        entry = port.service.repository.get("b")["messages"][0]
        assert entry["agentOriginated"] is True and entry["originSessionId"] == "a"
        assert entry["agentMessageKind"] == "task_notification"
        def strip(chat):
            chat["messages"][0].pop("agentOriginated")
            chat["messages"][0].pop("originSessionId")
        port.service.repository.mutate_one("b", strip)
        assert port.run_manager._reconcile_inbox_guidance_messages() == 1
        assert port.service.repository.get("b")["messages"][0]["agentOriginated"] is True
        assert await port.agent_message_admitted("b", "peer-id")
    finally:
        await inbox.close(termination_reason="completed")
    # A cleared session must not resurrect inbox history or old admission evidence.
    WorkbenchSessionPresentation(port.service.repository._database()).clear("b")
    assert port.run_manager._reconcile_inbox_guidance_messages() == 0
    assert port.service.repository.get("b")["messages"] == []
    assert not await port.agent_message_admitted("b", "peer-id")


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["failed", "cancelled"])
async def test_terminal_checkpoint_allows_a_new_peer_run(tmp_path, status):
    port = Port()
    port.checkpoints["b"] = {"status": status, "run_id": "old-run"}
    service = await service_without_worker(tmp_path, port)
    try:
        sent = await service.send("a", "b", "new task", "new-effect")
        await service.tick()
        assert len(port.deliveries) == 1
        assert service.store.get(sent["message_id"])["status"] == "delivered"
    finally:
        await service.stop()


@pytest.mark.asyncio
async def test_more_than_batch_size_blocked_recipients_cannot_starve_ready_one(tmp_path):
    port = Port()
    service = await service_without_worker(tmp_path, port)
    try:
        for index in range(33):
            target = f"target-{index}"
            port.repository.chats[target] = {"id": target, "createdAt": target, "messages": []}
            if index < 32:
                port.repository.chats[target]["pendingQuestion"] = {"id": "question"}
            await service.send("a" if index < 20 else "b", target, "hello", str(index))
        await service.tick()
        assert not port.deliveries
        await service.tick()
        assert [item[0] for item in port.deliveries] == ["target-32"]
        assert all(row["attempts"] == 0 for row in service.store.pending())
    finally:
        await service.stop()


@pytest.mark.asyncio
async def test_clear_between_preflight_and_dispatch_rejects_old_generation(tmp_path):
    from cyrene.workbench.sessions.session_presentation import WorkbenchSessionPresentation
    port = durable_port(tmp_path)
    service = await service_without_worker(tmp_path / "plugin", port)
    presentation = WorkbenchSessionPresentation(port.service.repository._database())
    original = port.dispatch_agent_message
    async def clear_then_dispatch(*args, **kwargs):
        await asyncio.to_thread(presentation.clear, "b")
        return await original(*args, **kwargs)
    port.dispatch_agent_message = clear_then_dispatch
    async def forbidden(*_, **__):
        pytest.fail("An old-generation message cannot reach send")
    port.send = forbidden
    try:
        sent = await service.send("a", "b", "old message", "effect")
        await service.tick()
        assert service.store.get(sent["message_id"])["status"] == "failed"
        assert port.service.repository.get("b")["messageGeneration"] == 1
        assert port.service.repository.get("b")["messages"] == []
    finally:
        await service.stop()


@pytest.mark.asyncio
async def test_clear_excluded_during_admission_and_cancellation_does_not_release_early(tmp_path):
    from cyrene.workbench.sessions.session_presentation import WorkbenchSessionPresentation, WorkbenchSessionError
    port = durable_port(tmp_path)
    presentation = WorkbenchSessionPresentation(port.service.repository._database())
    entered, release = asyncio.Event(), asyncio.Event()
    async def blocked_send(*_, **__):
        entered.set()
        await release.wait()
        return {"status": "admitted"}
    port._dispatch_agent_message = blocked_send
    task = asyncio.create_task(port.dispatch_agent_message("b", "peer", client_request_id="req"))
    await entered.wait()
    task.cancel()
    await asyncio.sleep(0)
    try:
        with pytest.raises(WorkbenchSessionError, match="admission"):
            await asyncio.to_thread(presentation.clear, "b")
    finally:
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
    await asyncio.to_thread(presentation.clear, "b")
    assert port.service.repository.get("b")["messageGeneration"] == 1


@pytest.mark.asyncio
async def test_in_memory_runner_ack_without_input_is_not_delivery(tmp_path):
    from cyrene.workbench.control.control_services import ControlServiceError
    port = durable_port(tmp_path)
    async def premature_ack(*_, **__):
        return {"runId": "died-before-submit"}
    port.send = premature_ack
    with pytest.raises(ControlServiceError, match="before input admission"):
        await port.dispatch_agent_message("b", "hello", client_request_id="request")


@pytest.mark.asyncio
async def test_real_failed_checkpoint_wakes_new_agent_run(tmp_path):
    from cyrene.core.session import AgentSession
    from cyrene.model.error_details import ModelCallError, classify_model_error
    port = durable_port(tmp_path)
    registry = PluginRegistry()
    async def fail(*_):
        raise ModelCallError(classify_model_error("HTTP 401 Unauthorized: Invalid API Key"))
    registry.register_pack(PluginPack("model", "model", (
        Plugin("MiniMax", "fake", {"type": "object"}, fail, kind="model"),
    )), source="test")
    def open_session():
        return AgentSession(tmp_path / "agent-state", tmp_path / "work", tmp_path / "plugins",
                            registry=registry, load_plugins=False, tree_id="b")
    session = open_session()
    try:
        session.submit("old human request", run_id="old-run")
        await session.drain()
        assert session.is_idle
    finally:
        session.close()
    assert port.run_manager.conversation_runtime.context_checkpoint("b")["status"] == "failed"
    calls = []
    async def send(chat_id, body):
        calls.append(body)
        reopened = open_session()
        try:
            reopened.submit(body["message"], run_id="new-run", metadata={
                "client_request_id": body["clientRequestId"], "agent_originated": True,
                "origin_session_id": body["sourceSessionId"],
            })
        finally:
            reopened.close()
        return {"runId": "new-run"}
    port.send = send
    service = await service_without_worker(tmp_path / "plugin", port)
    try:
        sent = await service.send("a", "b", "new request", "new-effect")
        await service.tick()
        assert len(calls) == 1
        assert service.store.get(sent["message_id"])["status"] == "delivered"
        assert port.run_manager.conversation_runtime.context_checkpoint("b")["run_id"] == "new-run"
    finally:
        await service.stop()


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["", "task_notification"])
async def test_replay_as_guidance_reuses_previously_persisted_turn(tmp_path, kind):
    from cyrene.workbench.chat.chat_guidance_service import ChatGuidanceApplicationService, ChatGuidanceDependencies
    port = durable_port(tmp_path)
    repository = port.service.repository
    repository.mutate_one("b", lambda chat: chat["messages"].append({
        "id": "original-id", "role": "user", "content": "peer", "clientRequestId": "req",
        "createdAt": "2026-01-01", "agentOriginated": True, "originSessionId": "a", "agentMessageKind": kind,
    }))
    ready = asyncio.Event()
    ready.set()
    inbox = WorkbenchAgentInbox("b", repository._database(), run_id="other-run")
    async def publish(_):
        pass
    run = SimpleNamespace(status="running", ready=ready, inbox=inbox, run_id="other-run", publish=publish)
    guidance = ChatGuidanceApplicationService(ChatGuidanceDependencies(
        run_manager=SimpleNamespace(get=lambda _: run), get_chat=repository.get,
        mutate_chat=repository.mutate_one, public_message=lambda entry: entry,
        utc_now_iso=lambda: "2026-02-01", short_id=lambda _: "new-id",
    ))
    try:
        result = await guidance.submit(chat_id="b", message="peer", client_request_id="req",
                                       agent_originated=True, origin_session_id="a", agent_message_kind=kind)
        assert result.status_code == 200
        messages = repository.get("b")["messages"]
        assert len(messages) == 1
        assert messages[0]["id"] == "original-id" and messages[0]["guidance"]
        assert messages[0].get("agentMessageKind", "") == kind
        assert result.payload["userMessage"].get("agentMessageKind", "") == kind
        assert await port.agent_message_admitted("b", "req")
    finally:
        await inbox.close(termination_reason="completed")


@pytest.mark.asyncio
async def test_card_user_dispatch_idle_active_race_and_admission_replay(tmp_path):
    import json
    from cyrene.workbench.control.control_services import ControlServiceError
    port = durable_port(tmp_path)
    calls, admitted = [], set()
    active = None
    port.run_manager.get = lambda _: active
    async def has_input(chat_id, request_id):
        return request_id in admitted
    port.user_message_admitted = has_input
    async def send(chat_id, body):
        calls.append(body)
        admitted.add(body["clientRequestId"])
        return {"runId": "run"}
    async def guide(chat_id, body):
        calls.append(body)
        admitted.add(body["clientRequestId"])
        return {"runId": "run"}
    port.send, port.guide = send, guide
    version = json.dumps(["b", 0])
    result = await port.dispatch_user_message("b", "human selection", client_request_id="first", expected_version=version)
    assert result["status"] == "started"
    assert calls[0]["conversationSource"] == "card_submission" and not calls[0].get("agentOriginated")
    assert (await port.dispatch_user_message("b", "human selection", client_request_id="first", expected_version=version))["duplicate"]
    assert len(calls) == 1
    active = SimpleNamespace(events=[], run_id="run")
    assert (await port.dispatch_user_message("b", "next", client_request_id="second", expected_version=version))["status"] == "guided"
    assert not calls[-1].get("agentOriginated")
    async def ending(chat_id, body):
        nonlocal active
        active = None
        raise ControlServiceError("finishing", code="chat_not_running", status_code=409)
    port.guide = ending
    assert (await port.dispatch_user_message("b", "race", client_request_id="third", expected_version=version))["status"] == "started"
    with pytest.raises(ControlServiceError) as caught:
        await port.dispatch_user_message("b", "old", client_request_id="old", expected_version='["b", 1]')
    assert caught.value.status_code == 410


@pytest.mark.asyncio
async def test_card_human_turn_public_replay_and_durable_guidance(tmp_path):
    from cyrene.workbench.http.workbench.chat_routes.send_input import append_user_message, SendInput
    from cyrene.workbench.http.workbench.chat_routes.send_request import SendOrigin
    from cyrene.workbench.chat.chat_guidance_service import ChatGuidanceApplicationService, ChatGuidanceDependencies
    port = durable_port(tmp_path)
    repository = port.service.repository
    chat = repository.get("b")
    source = SendInput("selection", "selection", "", [], [])
    origin = SendOrigin.parse({"conversationSource": "card_submission", "clientRequestId": "card-req"})
    for _ in range(2):
        append_user_message(chat, chat["messages"], source, origin, "now", lambda _: "public-id")
    assert len(chat["messages"]) == 1 and not chat["messages"][0].get("agentOriginated")
    repository.write_one(chat)
    ready = asyncio.Event()
    ready.set()
    inbox = WorkbenchAgentInbox("b", repository._database(), run_id="running")
    async def publish(_):
        pass
    run = SimpleNamespace(status="running", ready=ready, inbox=inbox, run_id="running", publish=publish)
    guidance = ChatGuidanceApplicationService(ChatGuidanceDependencies(
        run_manager=SimpleNamespace(get=lambda _: run), get_chat=repository.get,
        mutate_chat=repository.mutate_one, public_message=lambda entry: entry,
        utc_now_iso=lambda: "later", short_id=lambda _: "wrong-new-id"))
    try:
        for _ in range(2):
            result = await guidance.submit(chat_id="b", message="selection", client_request_id="card-req")
            assert result.status_code == 200
        messages = repository.get("b")["messages"]
        assert len(messages) == 1 and messages[0]["id"] == "public-id" and messages[0]["guidance"]
        assert not messages[0].get("agentOriginated")
        assert await port.user_message_admitted("b", "card-req")
        assert not await port.agent_message_admitted("b", "card-req")
    finally:
        await inbox.close(termination_reason="completed")


@pytest.mark.asyncio
async def test_card_dispatch_recognizes_committed_human_input_after_lost_reply(tmp_path):
    import json
    from cyrene.core.session import AgentSession
    port = durable_port(tmp_path)
    registry = PluginRegistry()
    async def unused_model(*_):
        raise AssertionError("Admission replay must not call a model")
    registry.register_pack(PluginPack("model", "model", (
        Plugin("MiniMax", "fake", {"type": "object"}, unused_model, kind="model"),
    )), source="test")
    session = AgentSession(tmp_path / "agent-state", tmp_path / "work", tmp_path / "plugins",
                           registry=registry, load_plugins=False, tree_id="b")
    try:
        session.submit("submitted selection", run_id="card-run", metadata={
            "client_request_id": "card-event", "agent_originated": False})
    finally:
        session.close()
    assert await port.user_message_admitted("b", "card-event")
    assert not await port.agent_message_admitted("b", "card-event")
    result = await port.dispatch_user_message("b", "submitted selection", client_request_id="card-event",
                                              expected_version=json.dumps(["b", 0]))
    assert result["duplicate"] and result["status"] == "admitted"
