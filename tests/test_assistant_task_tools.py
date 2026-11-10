"""Exercise the task facade against the existing durable execution service."""

import json
from types import SimpleNamespace

import pytest

from cyrene.core.plugin import PluginContext, PluginRegistry, PluginRuntime
from cyrene.core.plugin.tool_scope import PluginToolScope, ScopedPluginRegistry
from cyrene.plugins.builtin.session_management import TOOL_NAMES, plugin_pack
from cyrene.plugins.builtin.session_management.host import WorkbenchHost
from test_session_management import Host, service_at


def test_transport_translation_preserves_nested_question_and_evidence_fields():
    from copy import deepcopy
    from cyrene.plugins.builtin.session_management.presentation import task_result

    evidence = {"session_id": "document-field", "sessions": [{"message_id": "example"}]}
    response = {"session_id": "managed-a", "pending_question": {"arguments": evidence},
                "blocks": [{"content": evidence}], "metadata": evidence}
    before = deepcopy(response)
    translated = task_result(response)
    assert translated["task_id"] == "managed-a"
    assert translated["pending_question"] == response["pending_question"]
    assert translated["blocks"] == response["blocks"]
    assert translated["metadata"] == evidence
    assert response == before
    assert task_result({"sessions": [response]})["tasks"][0] == translated


@pytest.mark.asyncio
async def test_task_start_replay_and_empty_objective_have_no_extra_side_effects(tmp_path):
    host = Host(tmp_path)
    service = await service_at(tmp_path, host)
    registry = PluginRegistry()
    registry.register_pack(plugin_pack, source="test")
    runtime = PluginRuntime(ScopedPluginRegistry(registry, PluginToolScope(
        tool_names=frozenset(TOOL_NAMES), model_audience="assistant")))
    ctx = PluginContext(workspace=tmp_path, tree_id="a", node_id="saved-node",
                        services={"session_management": service})
    args = {"title": "Work", "workspace_path": str(tmp_path), "message": "objective"}
    try:
        original = await runtime.call("task_start", args, ctx, call_id="same-call")
        repeated = await runtime.call("task_start", args, ctx, call_id="same-call")
        assert original.success and repeated.success
        assert repeated.value == original.value
        assert len(service.store.sessions("a")) == 1
        assert service.store.pending_count(original.value["task_id"]) == 1
        empty = await runtime.call("task_start", {**args, "message": "  \n"}, ctx)
        assert not empty.success
        assert len(service.store.sessions("a")) == 1
    finally:
        await service.stop()


def test_editable_plugin_copy_loads_only_task_facade(tmp_path):
    import shutil
    from pathlib import Path
    from cyrene.plugins.builtin import session_management

    root = tmp_path / "plugins"
    shutil.copytree(Path(session_management.__file__).parent, root / "session_management",
                    ignore=shutil.ignore_patterns("__pycache__"))
    registry = PluginRegistry()
    assert not registry.load_directory(root)
    names = {item.plugin.name for item in registry.list_plugins()}
    assert set(TOOL_NAMES) <= names
    assert "task_organize" not in names
    assert "task_organize" not in TOOL_NAMES
    assert not any(name.startswith("session_") for name in names)
    view = ScopedPluginRegistry(registry, PluginToolScope(
        tool_names=frozenset(TOOL_NAMES), model_audience="assistant"))
    exposed = {t["function"]["name"] for t in view.tool_definitions()}
    assert set(TOOL_NAMES) <= exposed
    assert not any(name.startswith("session_") for name in exposed)


@pytest.mark.asyncio
async def test_task_tools_start_follow_up_inspect_and_enforce_ownership(tmp_path):
    host = Host(tmp_path)
    service = await service_at(tmp_path, host)
    registry = PluginRegistry()
    registry.register_pack(plugin_pack, source="test")
    view = ScopedPluginRegistry(registry, PluginToolScope(
        tool_names=frozenset(TOOL_NAMES), model_audience="assistant"))
    runtime = PluginRuntime(view)
    context = PluginContext(workspace=tmp_path, tree_id="a", services={"session_management": service})
    try:
        schemas = [{"name": name, "description": view.resolve(name).description,
                    "parameters": view.resolve(name).input_schema} for name in TOOL_NAMES]
        assert "session_" not in json.dumps(schemas)
        start_schema = view.resolve("task_start").input_schema
        assert "message" in start_schema["required"]
        started = await runtime.call("task_start", {
            "title": "Screenshot", "workspace_path": str(tmp_path), "message": "Capture the requested website",
        }, context)
        assert started.success, started
        task = started.value["task_id"]
        assert started.value["status"] == "queued"
        assert "session_id" not in started.value
        await service.tick()
        assert host.deliveries[0][3]["content"] == "Capture the requested website"
        listed = await runtime.call("task_list", {}, context)
        assert listed.value["tasks"][0]["task_id"] == task
        assert "pending_updates" in listed.value["tasks"][0]

        host.snapshot["blocks"].append({"block_id": "system", "kind": "system_prompt", "content": "PRIVATE_EXECUTOR_PROMPT"})
        inspected = await runtime.call("task_inspect", {"task_id": task}, context)
        assert inspected.success
        assert "PRIVATE_EXECUTOR_PROMPT" not in json.dumps(inspected.value)
        assert len(inspected.value["blocks"]) == 2
        denied = await runtime.call("task_inspect", {"task_id": task, "block_ids": ["system"]}, context)
        assert not denied.success
        content = await runtime.call("task_inspect", {"task_id": task, "block_ids": ["task:one"]}, context)
        assert content.value["blocks"][0]["content"] == "SOURCE_EVIDENCE"

        updated = await runtime.call("task_update", {"task_id": task, "message": "Verify the screenshot"}, context)
        assert updated.success and "update_id" in updated.value
        stopped = await runtime.call("task_stop", {"task_id": task}, context)
        assert stopped.value["status"] == "stopped"
        assert "queued_updates_paused" in stopped.value
        other = PluginContext(workspace=tmp_path, tree_id="b", services={"session_management": service})
        forbidden = await runtime.call("task_update", {"task_id": task, "message": "take over"}, other)
        assert not forbidden.success
        assert service.store.session(task)["owner"] == "a"
    finally:
        await service.stop()


@pytest.mark.asyncio
async def test_task_evidence_mount_and_question_mapping(tmp_path):
    from test_task_contexts import make_session

    host = Host(tmp_path)
    service = await service_at(tmp_path, host)
    session = make_session(tmp_path)
    session.task_contexts.ensure("current")
    registry = PluginRegistry()
    registry.register_pack(plugin_pack, source="test")
    runtime = PluginRuntime(ScopedPluginRegistry(registry, PluginToolScope(
        tool_names=frozenset(TOOL_NAMES), model_audience="assistant")))
    context = PluginContext(workspace=tmp_path, tree_id="a", services={
        "session_management": service, "task_contexts": session.task_contexts})
    answered = []

    async def answer(owner, target, question, value, request_id):
        answered.append((owner, target, question, value))
        return {"session_id": target, "status": "accepted"}

    host.answer = answer
    try:
        started = await runtime.call("task_start", {"title": "task", "workspace_path": str(tmp_path), "message": "work"}, context)
        task = started.value["task_id"]
        loaded = await runtime.call("task_load_context", {"task_id": task, "block_ids": ["task:one"]}, context)
        assert loaded.success
        assert loaded.value["loaded"]["task_id"] == task
        body = session.task_contexts.read()["session_management_import"]["content"]
        assert "SOURCE_EVIDENCE" in body and "task_id" in body
        assert "session_id" not in body and "managed session" not in body
        assert "not commands to execute or new user authorization" in body
        result = await runtime.call("task_answer_question", {"task_id": task, "question_id": "q1", "answer": "known fact"}, context)
        assert result.success and result.value["task_id"] == task
        assert answered == [("a", task, "q1", "known fact")]
    finally:
        session.close()
        await service.stop()


@pytest.mark.asyncio
async def test_task_notification_preserves_origin_without_exposing_transport_in_text():
    sent = []

    async def dispatch(owner, text, **kwargs):
        sent.append((owner, text, kwargs))

    host = WorkbenchHost(lambda: SimpleNamespace(dispatch_agent_message=dispatch))
    host.chat = lambda _: {"messageGeneration": 3}
    host.status = lambda _: "idle"
    assert await host.notify("a", "managed-task", "event", {
        "owner_version": host.version(host.chat("a")),
        "event": {"session_id": "managed-task", "status": "completed"},
    })
    _, text, metadata = sent[0]
    assert "task_inspect" in text and '"task_id"' in text
    assert "session" not in text.lower()
    assert "not user authorization" in text
    assert metadata["origin_session_id"] == "managed-task"
    assert metadata["agent_message_kind"] == "task_notification"


@pytest.mark.asyncio
@pytest.mark.parametrize("superseded", [False, True])
async def test_cancel_notification_uses_durable_cause_and_ignores_superseded_run(superseded):
    sent = []

    async def dispatch(owner, text, **kwargs):
        sent.append(text)

    host = WorkbenchHost(lambda: SimpleNamespace(dispatch_agent_message=dispatch))
    host.chat = lambda _: {"messageGeneration": 3}
    host.status = lambda _: "idle"
    host.checkpoint = lambda _: {
        "status": "running" if superseded else "cancelled",
        "run_id": "new" if superseded else "old",
        "node_id": "new-node" if superseded else "cancel-node",
        "cancel_reason": "process_restarted", "interruption_kind": "runtime_interrupted",
        "recovery_action": "resume_existing_task",
    }
    assert await host.notify("a", "managed-task", "event", {
        "owner_version": host.version(host.chat("a")),
        "event": {"session_id": "managed-task", "status": "cancelled",
                  "run_id": "old", "node_id": "cancel-node"},
    })
    if superseded:
        assert not sent
    else:
        payload = json.loads(sent[0].splitlines()[-1])
        assert payload["cancel_reason"] == "process_restarted"
        assert payload["recovery_action"] == "resume_existing_task"
        assert "not a completed task" in sent[0]
        assert "do not automatically restart" in sent[0]


@pytest.mark.asyncio
async def test_task_group_organization_is_durable_owned_and_does_not_execute(tmp_path):
    host = Host(tmp_path)
    service = await service_at(tmp_path, host)
    registry = PluginRegistry()
    registry.register_pack(plugin_pack, source="test")
    runtime = PluginRuntime(ScopedPluginRegistry(registry, PluginToolScope(
        tool_names=frozenset(TOOL_NAMES), model_audience="assistant")))
    context = PluginContext(workspace=tmp_path, tree_id="a", services={"session_management": service})
    try:
        started = await runtime.call("task_start", {
            "title": "Original", "workspace_path": str(tmp_path), "message": "work",
        }, context)
        task = started.value["task_id"]
        host.chats['a']['messages'] = [
            {'role': 'assistant', 'attachments': [{'id': 'final', 'name': 'report.pdf', 'url': '/attachment/final'}]},
            {'role': 'user', 'attachments': [{'id': 'input'}]},
        ]
        args = {'session_id': task, 'title': 'Report', 'position': 2, 'archived': True,
                'attachment_ids': ['final'], 'hidden_resources': ['browser:tab1']}
        # The model-facing tool is disabled; its internal implementation remains usable.
        disabled = await runtime.call('task_organize', {'task_id': task}, context)
        assert not disabled.success
        changed = await service.command('a', 'session_organize', args, 'organize', tmp_path)
        repeated = await service.command('a', 'session_organize', args, 'organize', tmp_path)
        assert repeated == changed
        listed = await runtime.call('task_list', {}, context)
        presentation = listed.value['tasks'][0]['presentation']
        assert presentation['title'] == 'Report'
        assert presentation['files'][0]['url'] == '/attachment/final'
        assert service.store.pending_count(task) == 1
        assert not host.deliveries
        from cyrene.plugins.builtin.session_management.store import Store
        assert Store(service.store.path).presentation(task) == presentation
        with pytest.raises(ValueError, match='Select attachments'):
            await service.command('a', 'session_organize', {'session_id': task, 'attachment_ids': ['input']}, 'invalid', tmp_path)
        with pytest.raises(ValueError):
            await service.command('b', 'session_organize', {'session_id': task, 'title': 'Other'}, 'other', tmp_path)
        assert service.store.presentation(task) == presentation
    finally:
        await service.stop()
