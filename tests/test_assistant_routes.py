import asyncio
from types import SimpleNamespace

import pytest
from fastapi import APIRouter, HTTPException
from cyrene.plugins.builtin.session_management.routes import register_routes
from cyrene.plugins.builtin.session_management.service import SessionManagementService
from cyrene.plugins.builtin.session_management.host import WorkbenchHost


@pytest.mark.asyncio
async def test_assistant_entry_is_global_durable_and_load_uses_chat_kernel(tmp_path, monkeypatch):
    from cyrene.workbench.projects import project_repository

    monkeypatch.setattr(project_repository, "find_workbench_project_lightweight", lambda p: {"id": p, "workspacePath": str(tmp_path)} if p in {"p", "q"} else None)
    from cyrene.workbench.chat.chat_repository import ChatRepository

    messages = []
    repo = ChatRepository(str(tmp_path / "app.db"))

    async def send(target, body):
        messages.append((target, body))
        return {"ok": True}

    port = SimpleNamespace(
        service=SimpleNamespace(repository=repo, create_chat=lambda project, title, model="", **kw: {"projectId": project, "title": title, "messages": []}, public_chat_light=lambda c: c),
        send=send,
        run_manager=SimpleNamespace(get=lambda _: None, conversation_runtime=SimpleNamespace(context_checkpoint=lambda _: None)),
    )
    host = WorkbenchHost(lambda: port)
    host.blocks = lambda _: {"blocks": [{"block_id": "task:t1", "kind": "task", "active": False, "content": "private"}]}
    service = SessionManagementService(tmp_path, host)
    await service.start()
    service.worker.cancel()
    await asyncio.gather(service.worker, return_exceptions=True)
    router = APIRouter()
    register_routes(SimpleNamespace(router=router), service)
    endpoints = {r.name: r.endpoint for r in router.routes}
    try:
        from cyrene.workbench.assistant import ASSISTANT_ID, ASSISTANT_SCOPE_ID
        assert {r.path for r in router.routes} == {
            "/api/workbench/assistant", "/api/workbench/assistant/load/{context_id}"}
        first = await endpoints["open_assistant"]()
        assert first == await endpoints["open_assistant"]()
        assert first["chat"]["id"] == ASSISTANT_ID
        assert first["chat"]["projectId"] == ASSISTANT_SCOPE_ID
        assert first["chat"]["kind"] == "personal-assistant"
        assert ChatRepository(str(tmp_path / "app.db")).get(first["chat"]["id"]) is not None
        task = host.create(first["chat"]["id"], "managed-test", "Task", str(tmp_path))
        assert repo.get(task["id"])["assistantOwnerSessionId"] == first["chat"]["id"]
        result = await endpoints["sidebar"]()
        assert "content" not in result["blocks"][0]
        await endpoints["load_context"]("t1")
        assert messages[0][0] == first["chat"]["id"]
        assert "t1" in messages[0][1]["message"]
        with pytest.raises(HTTPException):
            await endpoints["load_context"]("not-real")
        assert result["scope"]["id"] == ASSISTANT_SCOPE_ID
        # Existing project assistants become history tasks, with no transcript loss.
        repo.insert({"id": "assistant_old", "kind": "personal-assistant", "projectId": "p",
                     "messages": [{"id": "m", "role": "user", "content": "keep history"}]})
        child = host.create("assistant_old", "managed_old", "Old task", str(tmp_path))
        service.store.register(child["id"], "assistant_old")
        service.store.enqueue("assistant_old", child["id"], "queued", {"content": "continue"})
        service.store.notify("event", "assistant_old", child["id"], {"owner_version": "old", "event": {}})
        await endpoints["open_assistant"]()
        await endpoints["open_assistant"]()  # Idempotent, including queue migration.
        old = repo.get("assistant_old")
        assert old["kind"] == "assistant-task"
        assert old["messages"][0]["content"] == "keep history"
        assert old["workspaceOverride"] == str(tmp_path)
        assert old["projectId"] == ASSISTANT_SCOPE_ID
        assert repo.get(child["id"])["assistantOwnerSessionId"] == ASSISTANT_ID
        assert repo.get(child["id"])["workspaceOverride"] == str(tmp_path)
        assert service.store.owned(ASSISTANT_ID, child["id"])
        assert service.store.owned(ASSISTANT_ID, old["id"])
        assert service.store.message("queued")["owner"] == ASSISTANT_ID
        assert service.store.notifications()[0]["owner"] == ASSISTANT_ID
        assert sum(c["kind"] == "personal-assistant" for c in repo.read_summaries()["chats"]) == 1
    finally:
        await service.stop()


def test_assistant_execution_scope_survives_without_any_user_project(monkeypatch, tmp_path):
    from cyrene.workbench.assistant import ASSISTANT_SCOPE_ID
    from cyrene.workbench.projects import project_repository
    from cyrene.workbench.chat.chat_application import resolve_chat_workspace_dir
    from cyrene import config

    monkeypatch.setattr(config, "WORKSPACE_DIR", tmp_path / "assistant-workspace")
    def no_project_store():
        raise AssertionError("Global assistant must not need a user project")
    monkeypatch.setattr(project_repository, "_read_workbench_store", no_project_store)
    scope = project_repository.find_workbench_project_lightweight(ASSISTANT_SCOPE_ID)
    assert scope == project_repository.find_workbench_project({"projects": []}, ASSISTANT_SCOPE_ID)
    assert resolve_chat_workspace_dir({}, scope, project_repository.resolve_project_workspace_dir) == str(tmp_path / "assistant-workspace")
    assert (tmp_path / "assistant-workspace").is_dir()


@pytest.mark.parametrize("previously_migrated", [False, True])
def test_original_assistant_remains_the_singleton_with_history_and_children(tmp_path, monkeypatch, previously_migrated):
    from cyrene.workbench.assistant import ASSISTANT_ID, ASSISTANT_SCOPE_ID
    from cyrene.workbench.chat.chat_repository import ChatRepository
    from cyrene.workbench.projects import project_repository
    from cyrene.workbench.persistence.store import read_document

    monkeypatch.setattr(project_repository, "find_workbench_project_lightweight", lambda _: {"workspacePath": str(tmp_path)})
    repo = ChatRepository(str(tmp_path / "app.db"))
    original = "assistant_original"
    history = [{"id": "user-old", "role": "user", "content": "Research Diffusion"},
               {"id": "answer-old", "role": "assistant", "content": "Previous research", "attachments": [{"url": "/old-result.md"}]}]
    repo.insert({"id": original, "kind": "assistant-task" if previously_migrated else "personal-assistant",
                 "legacyPersonalAssistant": previously_migrated, "projectId": "p", "title": "Cyrene",
                 "createdAt": "2026-11-01", "messages": history, "model": "original-model"})
    if previously_migrated:
        repo.insert({"id": ASSISTANT_ID, "kind": "personal-assistant", "projectId": ASSISTANT_SCOPE_ID,
                     "title": "Cyrene", "createdAt": "2026-11-06",
                     "messages": [{"id": "experiment", "role": "user", "content": "UI experiment"}]})
    previous_owner = ASSISTANT_ID if previously_migrated else original
    repo.insert({"id": "managed_child", "kind": "assistant-task", "projectId": "p",
                 "assistantOwnerSessionId": previous_owner, "messages": [], "title": "Child"})
    def no_new_chat(*args, **kwargs):
        pytest.fail("An existing assistant must not be replaced by a new conversation")
    port = SimpleNamespace(service=SimpleNamespace(repository=repo, create_chat=no_new_chat))
    host = WorkbenchHost(lambda: port)
    service = SessionManagementService(tmp_path, host)
    service.store.register("managed_child", previous_owner)
    if previously_migrated:
        service.store.register(original, previous_owner)
    service.store.enqueue(previous_owner, "managed_child", "queued", {"content": "continue"})
    service.store.notify("notification", previous_owner, "managed_child", {"owner_version": "old", "event": {}})

    result = service.initialize_assistant()
    assert result["id"] == original
    assert result["messages"] == history
    assert result["model"] == "original-model"
    assert result["kind"] == "personal-assistant"
    assert result["projectId"] == ASSISTANT_SCOPE_ID
    assert result["workspaceOverride"] == str(tmp_path)
    assert not result.get("assistantOwnerSessionId")
    assert not result.get("legacyPersonalAssistant")
    assert read_document(repo._database(), "personal_assistant_identity", dict)["session_id"] == original
    assert repo.get("managed_child")["assistantOwnerSessionId"] == original
    assert service.store.owned(original, "managed_child")
    assert original not in [row["id"] for row in service.store.sessions(original)]
    assert service.store.message("queued")["owner"] == original
    assert service.store.notifications()[0]["owner"] == original
    if previously_migrated:
        assert repo.get(ASSISTANT_ID)["messages"][0]["content"] == "UI experiment"
        assert repo.get(ASSISTANT_ID)["kind"] == "assistant-task"
    restarted = SessionManagementService(tmp_path, host)
    assert restarted.initialize_assistant()["id"] == original
    assert restarted.initialize_assistant()["messages"] == history
    assert sum(c["kind"] == "personal-assistant" for c in repo.read_summaries()["chats"]) == 1
