"""Managed-session ownership, replay, snapshots and ordinary-Agent invisibility."""

import asyncio
from copy import deepcopy
import hashlib
from pathlib import Path
from types import SimpleNamespace

import pytest

from cyrene.core.plugin import PluginContext, PluginRegistry
from cyrene.plugins.builtin.session_management import TOOL_NAMES, load_blocks, plugin_pack
from cyrene.plugins.builtin.session_management.host import KIND, OWNER, WorkbenchHost
from cyrene.plugins.builtin.session_management.service import SessionManagementService


class Host:
    def __init__(self, directory):
        self.directory = directory
        self.chats = {key: {"id": key, "kind": "chat", "title": key, "createdAt": key} for key in ("a", "b")}
        self.states = {}
        self.deliveries = []
        self.accepted = set()
        self.snapshot = {
            "revision": "r1",
            "blocks": [
                {"block_id": "task:one", "content": "SOURCE_EVIDENCE", "chars": 15, "version": "v1", "kind": "task"},
                {"block_id": "task:two", "content": "OTHER_BLOCK", "chars": 11, "version": "v2", "kind": "task"},
            ],
        }

    def chat(self, key):
        return self.chats.get(key)

    def create(self, owner, target, title, workspace_path):
        self.chats.setdefault(target, {"id": target, "title": title, "kind": KIND, OWNER: owner, "createdAt": target, "workspaceOverride": workspace_path})
        return self.chats[target]

    def checkpoint(self, target):
        return {"status": self.status(target)}

    def status(self, target):
        return self.states.get(target, "idle")

    def version(self, chat):
        return str(chat.get("messageGeneration", 0))

    def attachment(self, content, name):
        path = self.directory / (hashlib.sha256(content).hexdigest() + "-" + name)
        path.write_bytes(content)
        return {"path": str(path), "name": name, "size": len(content)}

    async def deliver(self, owner, target, message_id, payload):
        if self.version(self.chats[target]) != payload["version"]:
            raise ValueError("cleared")
        if message_id in self.accepted:
            return True
        if self.status(target) != "idle":
            return False
        self.accepted.add(message_id)
        self.deliveries.append((owner, target, message_id, payload))
        return True

    async def stop(self, target):
        running = self.status(target) == "running"
        self.states[target] = "idle"
        return running

    def blocks(self, target):
        return deepcopy(self.snapshot)


async def service_at(tmp_path, host):
    service = SessionManagementService(tmp_path / "plugin", host)
    await service.start()
    service.worker.cancel()
    await asyncio.gather(service.worker, return_exceptions=True)
    return service


async def create(service, owner="a", effect="create", **args):
    return await service.command(owner, "session_create", {"title": "task", "workspace_path": str(service.store.path.parent), **args}, effect, ".")


@pytest.mark.asyncio
async def test_create_list_and_backend_ownership(tmp_path):
    host = Host(tmp_path)
    service = await service_at(tmp_path, host)
    try:
        result = await create(service)
        assert await create(service) == result
        target = result["session_id"]
        host.chats[target].update(createdAt="2026-11-10T10:00:00+08:00", updatedAt="2026-11-10T11:00:00+08:00")
        assert [s["session_id"] for s in (await service.list("a"))["sessions"]] == [target]
        listed = (await service.list("a"))["sessions"][0]
        assert listed["created_at"] == "2026-11-10T10:00:00+08:00"
        assert listed["updated_at"] == "2026-11-10T11:00:00+08:00"
        assert (await service.list("b"))["sessions"] == []
        with pytest.raises(ValueError, match="conflict"):
            await create(service, title="changed")
        for operation, extra in [("session_send_message", {"message": "hello"}), ("session_stop", {})]:
            for owner, target_id in [("b", target), ("a", "b")]:
                with pytest.raises(ValueError, match="not owned"):
                    await service.command(owner, operation, {"session_id": target_id, **extra}, "bad", tmp_path)
        with pytest.raises(ValueError, match="not owned"):
            await service.blocks("b", {"session_id": target})
    finally:
        await service.stop()


@pytest.mark.asyncio
async def test_waiting_cleared_deleted_and_bounded_imports(tmp_path, monkeypatch):
    from cyrene.plugins.builtin.session_management import service as module

    host = Host(tmp_path)
    service = await service_at(tmp_path, host)
    try:
        target = (await create(service))["session_id"]
        args = {"session_id": target, "message": "task"}
        sent = await service.command("a", "session_send_message", args, "send", tmp_path)
        host.states[target] = "awaiting_user"
        await service.tick()
        assert not host.deliveries
        host.states[target] = "idle"
        host.chats[target]["messageGeneration"] = 1
        service.store.update(sent["message_id"], "queued")
        await service.tick()
        assert service.store.message(sent["message_id"])["status"] == "failed"
        assert (await service.list("a"))["sessions"][0]["delivery_error"]["error"] == "cleared"
        monkeypatch.setattr(module, "MAX_LOAD_CHARS", 3)
        with pytest.raises(ValueError, match="exceed"):
            await service.blocks("a", {"session_id": target, "block_ids": ["task:one"]}, load=True)
        source = tmp_path / "too-big.txt"
        source.write_text("12345")
        monkeypatch.setattr(module, "MAX_FILE_BYTES", 4)
        with pytest.raises(ValueError, match="20 MiB"):
            await service.command("a", "session_send_message", {"session_id": target, "files": [str(source)]}, "large", tmp_path)
        assert service.store.pending_count(target) == 0
        sent = await service.command("a", "session_send_message", args, "send-again", tmp_path)
        del host.chats[target]
        await service.tick()
        assert service.store.message(sent["message_id"])["status"] == "failed"
        assert (await service.list("a"))["sessions"] == []
    finally:
        await service.stop()


@pytest.mark.asyncio
async def test_cancel_delivery_waits_for_input_admission(tmp_path):
    host = WorkbenchHost(lambda: None)
    entered = asyncio.Event()
    released = asyncio.Event()
    completed = asyncio.Event()

    async def deliver(*args):
        entered.set()
        await released.wait()
        completed.set()
        return True

    host._deliver = deliver
    task = asyncio.create_task(host.deliver("a", "target", "msg", {}))
    await entered.wait()
    task.cancel()
    await asyncio.sleep(0)
    assert not task.done()
    released.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert completed.is_set()


@pytest.mark.asyncio
async def test_file_snapshot_stop_resume_replay_and_restart(tmp_path):
    host = Host(tmp_path)
    service = await service_at(tmp_path, host)
    source = tmp_path / "input.txt"
    source.write_text("original file")
    try:
        target = (await create(service))["session_id"]
        host.states[target] = "running"
        args = {"session_id": target, "message": "read this", "files": [str(source)]}
        sent = await service.command("a", "session_send_message", args, "send", tmp_path)
        source.write_text("changed file")
        await service.tick()
        assert not host.deliveries
        stopped = await service.command("a", "session_stop", {"session_id": target}, "stop", tmp_path)
        assert stopped["cancelled_running_execution"]
        assert stopped["queued_messages_paused"] == 1
        await service.tick()
        assert not host.deliveries
        assert await service.command("a", "session_send_message", args, "send", tmp_path) == sent
        assert service.store.owned("a", target)["paused"]
        await service.stop()
        service = await service_at(tmp_path, host)
        await service.tick()
        assert not host.deliveries
        await service.command("a", "session_send_message", {"session_id": target, "message": "continue"}, "resume", tmp_path)
        await service.tick()
        await service.tick()
        assert len(host.deliveries) == 2
        snapshot = host.deliveries[0][3]["attachments"][0]
        assert Path(snapshot["path"]).read_text() == "original file"
        assert service.store.message(sent["message_id"])["status"] == "delivered"
        # Lost acknowledgement replays the same admission ID, not the task.
        service.store.update(sent["message_id"], "queued")
        await service.tick()
        assert len(host.deliveries) == 2
    finally:
        await service.stop()


@pytest.mark.asyncio
async def test_failed_acceptance_replay_keeps_original_file_and_arguments(tmp_path, monkeypatch):
    host = Host(tmp_path)
    service = await service_at(tmp_path, host)
    source = tmp_path / "data.txt"
    source.write_text("old")
    try:
        target = (await create(service))["session_id"]
        args = {"session_id": target, "files": [str(source)]}
        original = service.store.save
        monkeypatch.setattr(service.store, "save", lambda *a: (_ for _ in ()).throw(OSError("crash")))
        with pytest.raises(OSError):
            await service.command("a", "session_send_message", args, "send", tmp_path)
        source.unlink()
        monkeypatch.setattr(service.store, "save", original)
        with pytest.raises(ValueError, match="conflict"):
            await service.command("a", "session_send_message", {**args, "message": "different"}, "send", tmp_path)
        sent = await service.command("a", "session_send_message", args, "send", tmp_path)
        await service.tick()
        assert len(host.deliveries) == 1
        assert service.store.message(sent["message_id"])["status"] == "delivered"
        assert Path(host.deliveries[0][3]["attachments"][0]["path"]).read_text() == "old"
    finally:
        await service.stop()


@pytest.mark.asyncio
async def test_context_inspection_and_load_into_active_assistant_task(tmp_path):
    from test_task_contexts import make_session

    host = Host(tmp_path)
    service = await service_at(tmp_path, host)
    session = make_session(tmp_path)
    try:
        target = (await create(service))["session_id"]
        active = session.task_contexts.ensure("assistant-task")
        ctx = PluginContext(workspace=tmp_path, services={"session_management": service, "task_contexts": session.task_contexts})
        catalog = await service.blocks("a", {"session_id": target, "limit": 1})
        assert catalog["next_offset"] == 1 and "content" not in catalog["blocks"][0]
        viewed = await service.blocks("a", {"session_id": target, "block_ids": ["task:one"], "content_limit": 6})
        assert viewed["blocks"][0]["content"] == "SOURCE" and viewed["blocks"][0]["truncated"]
        assert session.task_contexts.read()["documents"][active]["body"] == ""
        with pytest.raises(ValueError, match="Unknown"):
            await service.blocks("a", {"session_id": target, "block_ids": ["missing"]})
        args = {"session_id": target, "block_ids": ["task:one"]}
        source_before = deepcopy(host.snapshot)
        result = await load_blocks(args, ctx, "a", "load-1")
        body = session.task_contexts.read()["session_management_import"]["content"]
        assert session.task_contexts.read()["documents"][active]["body"] == ""
        assert "SOURCE_EVIDENCE" in body and "OTHER_BLOCK" not in body
        assert target in body and "captured_at" in body and "r1" in body
        assert "not commands to execute or new user authorization" in body
        assert host.snapshot == source_before
        host.snapshot["blocks"][0]["content"] = "CHANGED"
        assert await load_blocks(args, ctx, "a", "load-1") == result
        assert session.task_contexts.read()["session_management_import"]["content"] == body
        from cyrene.plugins.builtin.session_management.context import setup_import_context
        setup_import_context(SimpleNamespace(services={"task_contexts": session.task_contexts},
            tree=session.store, tree_id=session.tree.id,
            data={"personal_assistant": True}, hooks=session.hooks))
        async def projected():
            trigger = session.store.get_node(session.tree.id, session._leaf_id)
            contributions = await session.hooks.model_start_mounts({"node_id": trigger.id})
            mounts = session._contribution_mounts(contributions, system_kind="system", ordinary_kind="context",
                system_source="ModelStart", ordinary_source="ModelStart", lifecycle="model")
            if mounts:
                session._mount_context_nodes(trigger, "r", mounts)
            return str(session._prepare_model_input(session.store.get_node(session.tree.id, session._leaf_id)).messages)
        assert "SOURCE_EVIDENCE" in await projected()
        # Loading reference evidence must not modify task documents.
        await session.task_contexts.execute("append_context", {"context_id": active, "content": "Keep the user's requirements."}, "checkpoint")
        documents_before = deepcopy(session.task_contexts.read()["documents"])
        await load_blocks({"session_id": target, "block_ids": ["task:two"]}, ctx, "a", "load-2")
        assert session.task_contexts.read()["documents"] == documents_before
        text = await projected()
        assert "OTHER_BLOCK" in text and "SOURCE_EVIDENCE" not in text
        assert session.task_contexts.read()["active"] == active
        # A replay must not restore an older selection over a newer import.
        await load_blocks(args, ctx, "a", "load-1")
        assert "OTHER_BLOCK" in await projected()
        with pytest.raises(ValueError):
            await load_blocks({"session_id": target, "block_ids": ["missing"]}, ctx, "a", "load-bad")
        assert "OTHER_BLOCK" in await projected()
    finally:
        session.close()
        await service.stop()


def test_pack_hidden_for_ordinary_agents_and_explicitly_exposed_per_session(tmp_path):
    from cyrene.core.session import AgentSession
    from test_task_contexts import make_session

    registry = PluginRegistry()
    registry.register_pack(plugin_pack, source="test")
    assert plugin_pack.id == "session_management"
    assert {p.name for p in plugin_pack.plugins} == set(TOOL_NAMES)
    for definitions in (registry.tool_definitions(), registry.direct_tool_definitions(), registry.tool_definitions(agent_id="child")):
        assert not set(TOOL_NAMES) & {definition["function"]["name"] for definition in definitions}
    ordinary = make_session(tmp_path)
    try:
        ordinary.registry.register_pack(plugin_pack, source="test")
        assert not set(TOOL_NAMES) & {d["function"]["name"] for d in ordinary._direct_model_tool_definitions()}
    finally:
        ordinary.close()
    from cyrene.core.plugin.tool_scope import PluginToolScope, ScopedPluginRegistry
    assistant_registry = ScopedPluginRegistry(ordinary.registry, PluginToolScope(
        tool_names=frozenset(TOOL_NAMES), model_audience="assistant"))
    assistant = AgentSession(tmp_path / "data", tmp_path, tmp_path / "plugins", registry=assistant_registry, extra_direct_tool_names=TOOL_NAMES)
    try:
        assert set(TOOL_NAMES) <= {d["function"]["name"] for d in assistant._direct_model_tool_definitions()}
    finally:
        assistant.close()


def test_host_reads_consistent_context_blocks_without_running_source(tmp_path):
    from test_task_contexts import make_session, command

    session = make_session(tmp_path)
    try:
        active = session.task_contexts.ensure("source-task")
        command(session, "append_context", {"context_id": active, "content": "saved evidence"}, "save")
        before = session.task_contexts.read()
        host = WorkbenchHost(
            lambda: SimpleNamespace(context=SimpleNamespace(conversation_context=SimpleNamespace(agent_states=SimpleNamespace(context_directory=tmp_path / "data" / "context"))))
        )
        snapshot = host.blocks(session.tree.id)
        assert snapshot["revision"]
        block = next(b for b in snapshot["blocks"] if b["block_id"] == "task:" + active)
        assert "saved evidence" in block["content"] and block["version"]
        assert session.task_contexts.read() == before
        assert host.blocks("missing")["blocks"] == []
    finally:
        session.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("selection", ["inherited", "child_override"])
async def test_host_creates_hidden_chat_and_delivers_real_attachment_payload(tmp_path, monkeypatch, selection):
    from cyrene.workbench.control.control_ports import WorkbenchChatApplicationPort
    from cyrene.core import plugin as plugin_module
    from cyrene.plugins import model_catalog
    from cyrene.workbench.http.workbench.chat_routes.run_send_routes import _SendOperation
    from cyrene.workbench.http.workbench.chat_routes.send_request import SendOptions

    chats = {"a": {"id": "a", "kind": "personal-assistant", "projectId": "p", "title": "Assistant", "messages": [{"content": "PRIVATE_NORMAL_DIALOGUE"}], "model": "m", "modelSelectionId": "parent-profile"}}
    sent = []
    preferences = {}
    candidates = [{"id": key, "model": key + "-model"} for key in ("parent-profile", "child-profile")]
    monkeypatch.setattr(plugin_module, "application_plugin_service", lambda _: SimpleNamespace(
        selectable_model_candidates=lambda: candidates, candidates_for_route=lambda _: []))
    monkeypatch.setattr(model_catalog, "set_session_model_preference",
        lambda session, candidate, effort: preferences.update({session: candidate["id"]}))
    repo = SimpleNamespace(
        get=lambda k: deepcopy(chats.get(k)),
        insert=lambda c: chats.update({c["id"]: deepcopy(c)}) or deepcopy(c),
        read_summaries=lambda: {"chats": list(chats.values())},
        _database=lambda: str(tmp_path / "app.db"),
    )
    service = SimpleNamespace(
        repository=repo,
        create_chat=lambda project, title, model, **kw: {"projectId": project, "title": title, "model": model, "createdAt": "now", "messages": []},
        public_chat_light=lambda c: c,
    )
    done = asyncio.Event()

    async def send(target, body):
        operation = object.__new__(_SendOperation)
        operation.chat_id = target
        operation.chat = chats[target]
        operation.is_external_agent = False
        operation.options = SendOptions.parse(body)
        operation.service = SimpleNamespace(run_manager=manager)
        selected, error = await operation._select_model()
        assert error is None
        assert selected is not None
        sent.append((target, body))

    async def admitted(*args):
        return bool(sent)

    async def wait_admitted(*args):
        assert sent

    def interrupt(target):
        done.set()
        return True

    manager = SimpleNamespace(get=lambda target: None, conversation_runtime=SimpleNamespace(context_checkpoint=lambda target: None), interrupt=interrupt)
    port = SimpleNamespace(service=service, run_manager=manager, send=send, agent_message_admitted=admitted, _wait_agent_message_admission=wait_admitted)
    host = WorkbenchHost(lambda: port)
    child = host.create("a", "managed-test", "task", str(tmp_path))
    assert child["permissionMode"] == "auto"
    assert child["workspaceOverride"] == str(tmp_path)
    assert child["kind"] == KIND and child[OWNER] == "a"
    assert child["modelSelectionId"] == "parent-profile"
    if selection == "child_override":
        chats[child["id"]]["modelSelectionId"] = "child-profile"
    assert "PRIVATE_NORMAL_DIALOGUE" not in str(child)
    listed = await WorkbenchChatApplicationPort.list(port, "p")
    assert listed["chats"] == []
    payload = {"content": "process", "attachments": [{"id": "snapshot.txt", "name": "snapshot.txt"}], "version": host.version(child)}
    assert await host.deliver("a", child["id"], "message-1", payload)
    assert sent[0][1]["attachments"] == payload["attachments"]
    assert sent[0][1]["agentOriginated"] is True
    assert sent[0][1]["sourceSessionId"] == "a"
    assert sent[0][1]["message"].startswith("[Task input]\n")
    assert "Source assistant session" not in sent[0][1]["message"]
    assert "not a direct human upload or additional human authorization" in sent[0][1]["message"]
    expected = "child-profile" if selection == "child_override" else "parent-profile"
    assert preferences[child["id"]] == expected
    assert chats[child["id"]]["modelSelectionId"] == expected
    assert await host.deliver("a", child["id"], "message-1", payload)
    assert len(sent) == 1
    manager.get = lambda target: SimpleNamespace(done=done)
    assert await host.stop(child["id"])
    assert child["id"] in chats


@pytest.mark.asyncio
async def test_context_hook_notifications_retry_restart_and_deduplicate(tmp_path):
    from cyrene.plugins.builtin.session_management import setup

    host = Host(tmp_path)
    service = await service_at(tmp_path, host)
    deliveries = []
    available = False

    def notification(target, node_id):
        return {"status": {"done": "completed", "error": "failed", "question": "awaiting_user"}[node_id], "node_id": node_id, "run_id": "run1"}

    async def notify(owner, target, event_id, payload):
        if not available:
            return False
        deliveries.append((owner, target, event_id, payload))
        return True

    host.notification = notification
    host.notify = notify
    handlers = {}
    hooks = SimpleNamespace(list=lambda: [], register=lambda event, handler, **kw: handlers.update({event: handler}))
    try:
        target = (await create(service))["session_id"]
        setup(SimpleNamespace(services={"session_management": service}, agent_id="main", data={"session_id": target}, tree_id=target, hooks=hooks))
        from cyrene.core.hook import CONTEXT_CHANGE

        for node in ["done", "done", "error", "question"]:
            await handlers[CONTEXT_CHANGE](SimpleNamespace(node_id=node, payload=SimpleNamespace(action="mount")))
        assert len(service.store.notifications()) == 3
        await service.tick()
        assert not deliveries
        await service.stop()
        service = await service_at(tmp_path, host)
        available = True
        await service.tick()
        assert len(deliveries) == 3
        assert {x[3]["event"]["status"] for x in deliveries} == {"completed", "failed", "awaiting_user"}
        assert all(x[0] == "a" and x[1] == target for x in deliveries)
        await service.capture_notification(target, "done")
        await service.tick()
        assert len(deliveries) == 3
    finally:
        await service.stop()


@pytest.mark.asyncio
async def test_create_requires_explicit_existing_workspace(tmp_path):
    service = await service_at(tmp_path, Host(tmp_path))
    try:
        for value in (None, "", "relative/path", str(tmp_path / "missing")):
            with pytest.raises(ValueError):
                await create(service, effect="invalid:" + str(value), workspace_path=value)
        file = tmp_path / "file.txt"
        file.write_text("not a directory")
        with pytest.raises(ValueError):
            await create(service, effect="file", workspace_path=str(file))
        assert (await service.list("a"))["sessions"] == []
        chosen = tmp_path / "child"
        chosen.mkdir()
        result = await create(service, workspace_path=str(chosen))
        listed = (await service.list("a"))["sessions"]
        assert listed[0]["session_id"] == result["session_id"]
        assert listed[0]["workspace_path"] == str(chosen)
        assert await create(service, workspace_path=str(chosen)) == result
        with pytest.raises(ValueError, match="conflict"):
            await create(service, workspace_path=str(tmp_path))
    finally:
        await service.stop()


@pytest.mark.asyncio
async def test_delivery_failure_notifies_owner_with_details(tmp_path):
    host = Host(tmp_path)
    notices = []
    async def notify(owner, target, event_id, payload):
        notices.append((owner, target, payload["event"]))
        return True
    async def fail(*args):
        raise ValueError("target was cleared")
    host.notify = notify
    host.deliver = fail
    service = await service_at(tmp_path, host)
    try:
        target = (await create(service, message="work"))["session_id"]
        await service.tick()
        assert len(notices) == 1
        owner, child, event = notices[0]
        assert (owner, child) == ("a", target)
        assert event["status"] == "failed" and event["phase"] == "delivery"
        assert event["error"] == "target was cleared"
        await service.tick()
        assert len(notices) == 1
    finally:
        await service.stop()


@pytest.mark.asyncio
async def test_assistant_answers_current_clarification_without_human_impersonation():
    child = {"kind": KIND, OWNER: "owner", "messages": []}
    pending = {"id": "q1", "kind": "clarification"}
    calls = []
    async def answer(target, body):
        calls.append(body)
        child["messages"].append({"clientRequestId": body["clientRequestId"]})
        return {"status": "answer_submitted"}
    host = WorkbenchHost(lambda: SimpleNamespace(
        service=SimpleNamespace(repository=SimpleNamespace(get=lambda _: child)), answer=answer,
        run_manager=SimpleNamespace(conversation_runtime=SimpleNamespace(context_checkpoint=lambda _: {"pending_question": pending}))))
    with pytest.raises(ValueError, match="changed"):
        await host.answer("owner", "child", "stale", "known fact", "a0")
    result = await host.answer("owner", "child", "q1", "known fact", "a1")
    assert result["status"] == "answer_submitted"
    assert calls[0]["agentOriginated"] is True and calls[0]["sourceSessionId"] == "owner"
    assert calls[0]["background"] is True
    await host.answer("owner", "child", "q1", "known fact", "a1")
    assert len(calls) == 1
    pending["kind"] = "permission"
    with pytest.raises(ValueError, match="human authorization"):
        await host.answer("owner", "child", "q1", "approve", "a2")
    with pytest.raises(ValueError, match="belongs"):
        await host.answer("stranger", "child", "q1", "answer", "a3")


@pytest.mark.asyncio
async def test_assistant_export_is_limited_to_current_owned_task_workspaces(tmp_path):
    host = Host(tmp_path)
    service = await service_at(tmp_path, host)
    workspace = tmp_path / 'task-output'
    workspace.mkdir()
    artifact = workspace / 'result.txt'
    artifact.write_text('verified output')
    outside = tmp_path / 'unrelated.txt'
    outside.write_text('unrelated')
    (workspace / 'escape').symlink_to(outside)
    try:
        result = await create(service, workspace_path=str(workspace))
        assert service.export_path('a', artifact) == artifact
        assert service.export_path('b', artifact) is None
        assert service.export_path('a', outside) is None
        assert service.export_path('a', workspace / 'escape') is None
        host.chats[result['session_id']][OWNER] = 'b'
        assert service.export_path('a', artifact) is None
    finally:
        await service.stop()


@pytest.mark.asyncio
async def test_assistant_send_file_delivers_owned_task_output(tmp_path, monkeypatch):
    from cyrene.plugins.builtin.cyrene_delivery import send_file
    host = Host(tmp_path)
    service = await service_at(tmp_path, host)
    workspace = tmp_path / 'task-output'
    workspace.mkdir()
    artifact = workspace / 'result.txt'
    artifact.write_text('verified output')
    events = []
    def outside_workspace(_):
        raise ValueError('outside assistant workspace')
    monkeypatch.setattr(send_file, 'resolve_exportable_path', outside_workspace)
    monkeypatch.setattr(send_file, 'register_generated_attachment', lambda path, **kw: {
        'id': 'result.txt', 'path': path, 'name': 'result.txt', 'size': artifact.stat().st_size,
        'content_type': 'text/plain', 'kind': 'file', 'url': '/api/workbench/exports/result.txt'})
    try:
        await create(service, workspace_path=str(workspace))
        context = PluginContext(workspace=tmp_path / 'assistant', services={
            'session_management': service, 'runtime_events': events.append},
            data={'personal_assistant': True, 'session_id': 'a', 'round_id': 'r'})
        result = await send_file.handler({'path': str(artifact)}, context)
        assert 'sent' in result
        assert any(event.get('message', {}).get('attachments') for event in events)
        with pytest.raises(ValueError):
            await send_file.handler({'path': str(tmp_path / 'unrelated.txt')}, context)
    finally:
        await service.stop()
