import asyncio
from types import SimpleNamespace

import pytest
from fastapi import APIRouter
from cyrene.plugins.builtin.session_management.routes import register_routes
from cyrene.plugins.builtin.session_management.service import SessionManagementService
from cyrene.plugins.builtin.session_management.host import WorkbenchHost


@pytest.mark.asyncio
async def test_assistant_entry_is_global_durable_and_does_not_read_contexts(tmp_path):
    from cyrene.workbench.projects.assistant import ASSISTANT_ID, ASSISTANT_SCOPE_ID
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
    assert [(r.path, r.methods) for r in router.routes] == [("/api/workbench/assistant", {"GET"}), ("/api/workbench/assistant/reset", {"POST"}),
        ("/api/workbench/assistant/work-device", {"GET"}), ("/api/workbench/assistant/work-device", {"PUT"})]
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
    from cyrene.workbench.projects.assistant import ASSISTANT_SCOPE_ID
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
    from cyrene.workbench.projects.assistant import ASSISTANT_ID, ASSISTANT_SCOPE_ID
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


@pytest.mark.asyncio
async def test_assistant_reset_deletes_tasks_and_durable_queues(tmp_path):
    from cyrene.workbench.projects.assistant import ASSISTANT_ID, ASSISTANT_SCOPE_ID
    from cyrene.workbench.chat.chat_repository import ChatRepository
    from cyrene.workbench.sessions.session_presentation import WorkbenchSessionError

    repo = ChatRepository(str(tmp_path / "app.db"))
    for id, kind, owner in [(ASSISTANT_ID, "personal-assistant", ""),
                            ("task", "assistant-task", ASSISTANT_ID),
                            ("orphan", "assistant-task", ASSISTANT_ID),
                            ("ordinary", "chat", "")]:
        repo.insert({"id": id, "kind": kind, "projectId": ASSISTANT_SCOPE_ID,
                     "assistantOwnerSessionId": owner, "status": "idle",
                     "messages": [{"id": "message", "role": "user", "content": "keep?"}]})
    ordinary = repo.get("ordinary")
    host = WorkbenchHost(lambda: SimpleNamespace(service=SimpleNamespace(repository=repo)))
    service = SessionManagementService(tmp_path, host)
    service.store.register("task", ASSISTANT_ID)
    service.store.register("missing", ASSISTANT_ID)
    service.store.enqueue(ASSISTANT_ID, "task", "queued", {})
    service.store.notify("notice", ASSISTANT_ID, "task", {})
    service.store.save_presentation("task", {"title": "Old task"})
    key = ASSISTANT_ID + ":session_create:old"
    service.store.reserve(key, "fingerprint")
    service.store.save(key, "fingerprint", {"session_id": "task"})
    await service.start()
    service.worker.cancel()
    await asyncio.gather(service.worker, return_exceptions=True)
    try:
        # A busy task must prevent partial removal of any conversation.
        task = repo.get("task")
        task["status"] = "running"
        repo.write_one(task)
        with pytest.raises(WorkbenchSessionError):
            await service.reset_assistant()
        assert repo.get(ASSISTANT_ID)["messages"]
        assert repo.get("orphan") is not None
        assert service.store.message("queued") is not None
        task["status"] = "idle"
        repo.write_one(task)
        router = APIRouter()
        register_routes(SimpleNamespace(router=router), service)
        result = await router.routes[1].endpoint()
        assert result == {"ok": True, "deletedTasks": 2}
        assert repo.get(ASSISTANT_ID)["messages"] == []
        assert repo.get(ASSISTANT_ID)["messageGeneration"] == 1
        assert repo.get("task") is None
        assert repo.get("orphan") is None
        assert repo.get("ordinary") == ordinary
        assert service.store.sessions(ASSISTANT_ID) == []
        assert service.store.message("queued") is None
        assert service.store.notifications() == []
        assert service.store.presentation("task") == {}
        assert service.store.replay(key, "fingerprint") is None
        assert (await service.reset_assistant())["deletedTasks"] == 0
    finally:
        await service.stop()


@pytest.mark.asyncio
async def test_work_device_selection_persists_without_mutating_existing_tasks(tmp_path, monkeypatch):
    from unittest.mock import AsyncMock
    from cyrene.workbench.chat.chat_repository import ChatRepository
    from cyrene.workbench.projects.assistant import ASSISTANT_ID
    from cyrene.plugins.builtin.session_management import routes

    repo = ChatRepository(str(tmp_path / "app.db"))
    port = SimpleNamespace(service=SimpleNamespace(repository=repo,
        create_chat=lambda project, title, **kw: {"projectId": project, "title": title, "messages": []}))
    host = WorkbenchHost(lambda: port)
    service = SessionManagementService(tmp_path, host)
    await service.start()
    service.worker.cancel()
    await asyncio.gather(service.worker, return_exceptions=True)
    router = APIRouter()
    register_routes(SimpleNamespace(router=router), service)
    descriptor = {"device_id": "peer", "display_name": "Remote", "projects": []}
    remote = SimpleNamespace(describe_work_device=AsyncMock(return_value=descriptor),
        task_devices=AsyncMock(return_value={"peers": [], "enabled": True}))
    monkeypatch.setattr(routes, "application_plugin_service", lambda _: remote)
    endpoints = {(r.path, next(iter(r.methods))): r.endpoint for r in router.routes}
    choose = endpoints[("/api/workbench/assistant/work-device", "PUT")]
    read = endpoints[("/api/workbench/assistant/work-device", "GET")]
    try:
        assert (await choose({"device_id": "peer"}))["work_device"] == descriptor
        assert (await read())["work_device"] == descriptor
        assert ChatRepository(str(tmp_path / "app.db")).get(ASSISTANT_ID)["workDevice"] == descriptor
        repo.insert({"id": "old-task", "workDevice": descriptor, "messages": []})
        await choose({"device_id": "local"})
        assert repo.get(ASSISTANT_ID)["workDevice"] is None
        assert repo.get("old-task")["workDevice"] == descriptor
        assert not repo.get(ASSISTANT_ID).get("remoteDeviceIds")
        remote.describe_work_device.side_effect = ValueError("upgrade required")
        assert (await choose({"device_id": "old-peer"})).status_code == 409
        assert repo.get(ASSISTANT_ID)["workDevice"] is None
    finally:
        await service.stop()


@pytest.mark.asyncio
async def test_registered_devices_load_even_while_task_command_holds_service_lock(monkeypatch):
    from unittest.mock import AsyncMock
    from cyrene.plugins.builtin.session_management import routes
    chat = {'id': 'assistant', 'workDevice': None}
    lock = asyncio.Lock()
    service = SimpleNamespace(lock=lock, host=SimpleNamespace(chat=lambda _: chat))
    remote = SimpleNamespace(task_devices=AsyncMock(return_value={'enabled': True, 'peers': [{'device_id': 'peer'}]}),
        check_task_device=AsyncMock(return_value={'device_id': 'peer', 'task_state': 'offline', 'task_eligible': False}))
    monkeypatch.setattr(routes, 'application_plugin_service', lambda _: remote)
    router = APIRouter()
    register_routes(SimpleNamespace(router=router), service)
    read = next(r.endpoint for r in router.routes if r.path.endswith('work-device') and 'GET' in r.methods)
    async with lock:
        result = await asyncio.wait_for(read(), 0.5)
        assert result['peers'] == [{'device_id': 'peer'}]
        assert (await read(device_id='peer'))['peer']['task_state'] == 'offline'
    remote.check_task_device.assert_awaited_once_with('peer')


@pytest.mark.asyncio
async def test_bootstrap_includes_local_device_name_without_remote_probe(monkeypatch):
    from unittest.mock import AsyncMock
    from cyrene.workbench.artifacts import presentation_service
    monkeypatch.setattr(presentation_service.presentation_runtime, '_build_ui_data', AsyncMock(return_value={}))
    remote = SimpleNamespace(store=SimpleNamespace(get_settings=lambda: {'device_name': 'My MacBook'}))
    host = SimpleNamespace(frontend_modules=['remote'], active_services={'remote': remote})
    query = presentation_service.PresentationQueryService(plugin_host=host)
    assert (await query.ui_data())['localDeviceName'] == 'My MacBook'
