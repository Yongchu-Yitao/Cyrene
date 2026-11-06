import asyncio
from types import SimpleNamespace

import pytest
from fastapi import APIRouter
from cyrene.plugins.builtin.session_management.routes import register_routes
from cyrene.plugins.builtin.session_management.service import SessionManagementService
from cyrene.plugins.builtin.session_management.host import WorkbenchHost


@pytest.mark.asyncio
async def test_assistant_entry_is_global_durable_and_does_not_read_contexts(tmp_path):
    from cyrene.workbench.assistant import ASSISTANT_ID, ASSISTANT_SCOPE_ID
    from cyrene.workbench.chat.chat_repository import ChatRepository

    repo = ChatRepository(str(tmp_path / "app.db"))
    port = SimpleNamespace(
        service=SimpleNamespace(repository=repo, create_chat=lambda project, title, model="", **kw: {"projectId": project, "title": title, "messages": []}, public_chat_light=lambda c: c),
        run_manager=SimpleNamespace(get=lambda _: None, conversation_runtime=SimpleNamespace(context_checkpoint=lambda _: None)),
    )
    host = WorkbenchHost(lambda: port)
    host.blocks = lambda _: pytest.fail("The assistant sidebar must not read context blocks")
    service = SessionManagementService(tmp_path, host)
    await service.start()
    service.worker.cancel()
    await asyncio.gather(service.worker, return_exceptions=True)
    router = APIRouter()
    register_routes(SimpleNamespace(router=router), service)
    assert [(r.path, r.methods) for r in router.routes] == [("/api/workbench/assistant", {"GET"})]
    sidebar = router.routes[0].endpoint
    try:
        first, second = await asyncio.gather(sidebar(), sidebar())
        assert first == second == await sidebar()
        assert first["assistant_id"] == ASSISTANT_ID
        assert first["scope"]["id"] == ASSISTANT_SCOPE_ID
        assert first["sessions"] == []
        assert "blocks" not in first
        assert len(repo.read_summaries()["chats"]) == 1
        assert first["chats"][0]["kind"] == "personal-assistant"
        assert ChatRepository(str(tmp_path / "app.db")).get(ASSISTANT_ID) is not None
        task = host.create(ASSISTANT_ID, "managed-test", "Task", str(tmp_path))
        service.store.register(task["id"], ASSISTANT_ID)
        result = await sidebar()
        assert [item["session_id"] for item in result["sessions"]] == [task["id"]]
        assert {chat["id"] for chat in result["chats"]} == {ASSISTANT_ID, task["id"]}
        assert repo.get(task["id"])["assistantOwnerSessionId"] == ASSISTANT_ID
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


def test_single_assistant_is_preserved_across_restarts_without_scanning_chats(tmp_path, monkeypatch):
    from cyrene.workbench.assistant import ASSISTANT_ID, ASSISTANT_SCOPE_ID
    from cyrene.workbench.chat.chat_repository import ChatRepository

    repo = ChatRepository(str(tmp_path / "app.db"))
    ordinary = {"id": "ordinary", "projectId": "p", "title": "My chat", "messages": []}
    repo.insert(ordinary)
    ordinary_before = repo.get("ordinary")
    created = []

    def create_chat(project, title, model="", **kwargs):
        created.append((project, title, kwargs))
        return {"projectId": project, "title": title, "messages": []}

    def no_scan():
        pytest.fail("Opening the singleton must not scan other conversations")

    monkeypatch.setattr(repo, "read_summaries", no_scan)
    host = WorkbenchHost(lambda: SimpleNamespace(service=SimpleNamespace(repository=repo, create_chat=create_chat)))
    service = SessionManagementService(tmp_path, host)
    result = service.initialize_assistant()
    assert result["id"] == ASSISTANT_ID
    assert result["projectId"] == ASSISTANT_SCOPE_ID
    assert result["kind"] == "personal-assistant"
    assert created[0][2]["project_memory_active"] is False

    history = [{"id": "user", "role": "user", "content": "Keep this conversation"}]
    result.update(messages=history, model="chosen-model")
    repo.write_one(result)
    child = host.create(ASSISTANT_ID, "managed_child", "Child", str(tmp_path))
    service.store.register(child["id"], ASSISTANT_ID)
    service.store.enqueue(ASSISTANT_ID, child["id"], "queued", {"content": "continue"})
    service.store.notify("notification", ASSISTANT_ID, child["id"], {"owner_version": host.version(result), "event": {}})
    created_before = len(created)

    restarted = SessionManagementService(tmp_path, host)
    for _ in range(2):
        current = restarted.initialize_assistant()
        assert current["messages"] == history
        assert current["model"] == "chosen-model"
        assert current["id"] == ASSISTANT_ID
    assert len(created) == created_before
    assert restarted.store.owned(ASSISTANT_ID, child["id"])
    assert restarted.store.message("queued")["owner"] == ASSISTANT_ID
    assert restarted.store.notifications()[0]["owner"] == ASSISTANT_ID
    assert repo.get("ordinary") == ordinary_before
