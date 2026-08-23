import asyncio

import pytest
from fastapi import APIRouter, FastAPI
from fastapi.testclient import TestClient

from cyrene.runtime.database import init_db, log_task_run
from cyrene.workbench.schedule_repository import ScheduleRepository, WorkspaceProjectResolver
from cyrene.workbench.schedule_service import CreateScheduleCommand, ScheduleApplicationService
from route.workbench.schedule import register_workbench_schedule_routes


def _client(tmp_path, notifications):
    db_path = str(tmp_path / "schedule.sqlite3")
    asyncio.run(init_db(db_path))
    project = {"id": "project_1", "dataKey": "workspace_1"}
    resolver = WorkspaceProjectResolver(
        find_project_lightweight=lambda project_id: project if project_id == "project_1" else None,
        read_projects=lambda: [project],
    )
    service = ScheduleApplicationService(
        ScheduleRepository(db_path),
        resolver,
        lambda **payload: notifications.append(payload),
    )
    app = FastAPI()
    router = APIRouter()
    register_workbench_schedule_routes(router, application_service=service)
    app.include_router(router)
    return TestClient(app), db_path


def test_schedule_crud_notifications_and_scoped_run_history(tmp_path):
    notifications = []
    client, db_path = _client(tmp_path, notifications)

    created = client.post(
        "/api/workbench/schedule/tasks?workspace=project_1",
        json={
            "prompt": "Daily workspace check",
            "schedule_type": "interval",
            "schedule_value": "3600",
            "schedule_timezone": "UTC",
        },
    )
    assert created.status_code == 200
    task_id = created.json()["id"]
    assert created.json()["workspace"] == "workspace_1"
    assert created.json()["tasks"][0]["permission_mode"] == "workspace_only"
    assert notifications[-1]["source"] == "schedule_created"

    updated = client.put(
        f"/api/workbench/schedule/tasks/{task_id}?workspace=project_1",
        json={"prompt": "Updated workspace check", "status": "paused"},
    )
    assert updated.status_code == 200
    assert updated.json()["tasks"][0]["prompt"] == "Updated workspace check"
    assert updated.json()["tasks"][0]["status"] == "paused"
    assert notifications[-1]["source"] == "schedule_updated"

    asyncio.run(log_task_run(db_path, task_id, 125, "success", result="done"))
    runs = client.get(f"/api/workbench/schedule/tasks/{task_id}/runs?workspace=project_1")
    assert runs.status_code == 200
    assert runs.json()["runs"][0]["duration_ms"] == 125

    wrong_workspace = client.delete(f"/api/workbench/schedule/tasks/{task_id}?workspace=other")
    assert wrong_workspace.status_code == 404
    deleted = client.delete(f"/api/workbench/schedule/tasks/{task_id}?workspace=project_1")
    assert deleted.status_code == 200
    assert deleted.json()["tasks"] == []


class _OrderedRepository:
    def __init__(self, calls):
        self.calls = calls

    async def create(self, values):
        self.calls.append(("create", values["project_id"]))
        return "task_1"

    async def list_tasks(self, workspace_id):
        self.calls.append(("list", workspace_id))
        return []


def test_schedule_create_keeps_commit_notification_refresh_order():
    calls = []
    resolver = WorkspaceProjectResolver(
        find_project_lightweight=lambda _workspace: None,
        read_projects=lambda: [],
    )
    service = ScheduleApplicationService(
        _OrderedRepository(calls),
        resolver,
        lambda **_payload: calls.append(("notify", "default")),
    )

    asyncio.run(service.create(CreateScheduleCommand("default", {
        "prompt": "Check order", "schedule_type": "interval", "schedule_value": "60",
    })))

    assert calls == [("create", "default"), ("notify", "default"), ("list", "default")]


def test_occurrence_query_does_not_hide_entity_repository_failures():
    class FailingEntityRepository:
        async def list_tasks(self, _workspace_id):
            return []

        async def list_deadline_entities(self, _workspace_id):
            raise RuntimeError("entity database unavailable")

    service = ScheduleApplicationService(
        FailingEntityRepository(),
        WorkspaceProjectResolver(
            find_project_lightweight=lambda _workspace: None,
            read_projects=lambda: [],
        ),
        lambda **_payload: None,
    )

    with pytest.raises(RuntimeError, match="entity database unavailable"):
        asyncio.run(service.list_occurrences("default", "", ""))
