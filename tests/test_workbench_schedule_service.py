import asyncio
import sqlite3

import pytest
from fastapi import APIRouter, FastAPI
from fastapi.testclient import TestClient

from agent.plugin import PluginApplicationHost, PluginRegistry
from agent.plugin.native_tools import seed_builtin_plugin_directory
from agent.plugin.plugin_impl.cyrene_entity.service import EntityService
from agent.plugin.plugin_impl.cyrene_schedule.repository import ScheduleRepository
from agent.plugin.plugin_impl.cyrene_schedule.routes import (
    register_workbench_schedule_routes,
)
from agent.plugin.plugin_impl.cyrene_schedule.service import ScheduleRuntimeService
from agent.plugin.plugin_impl.cyrene_schedule.workbench_repository import (
    WorkspaceProjectResolver,
)
from agent.plugin.plugin_impl.cyrene_schedule.workbench_service import (
    ScheduleApplicationService,
)
from cyrene.runtime.database import init_db
from cyrene.workbench.store import ensure_schema as ensure_workbench_schema


def _client(tmp_path, notifications):
    db_path = str(tmp_path / "schedule.sqlite3")
    asyncio.run(init_db(db_path))
    ensure_workbench_schema(db_path)
    project = {"id": "project_1", "dataKey": "workspace_1"}
    resolver = WorkspaceProjectResolver(
        find_project_lightweight=(
            lambda project_id: project if project_id == "project_1" else None
        ),
        read_projects=lambda: [project],
    )
    plugin_directory = tmp_path / "plugin_impl"
    seed_builtin_plugin_directory(plugin_directory)
    registry = PluginRegistry()
    assert registry.load_directory(plugin_directory) == ()
    runtime_service = ScheduleRuntimeService(
        db_path,
        plugin_directory=plugin_directory,
    )
    asyncio.run(runtime_service.ensure_ready())
    service = ScheduleApplicationService(
        db_path,
        resolver,
        lambda **payload: notifications.append(payload),
        entities=EntityService(db_path),
        registry=registry,
        runtime_service=runtime_service,
    )
    app = FastAPI()
    router = APIRouter()
    register_workbench_schedule_routes(router, application_service=service)
    app.include_router(router)
    return TestClient(app), db_path, service


def test_schedule_crud_runs_and_project_isolation_use_plugin_runtime(tmp_path):
    notifications = []
    client, db_path, _service = _client(tmp_path, notifications)

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

    asyncio.run(
        ScheduleRepository(db_path).log_run(
            task_id,
            125,
            "success",
            result="done",
        )
    )
    runs = client.get(
        f"/api/workbench/schedule/tasks/{task_id}/runs?workspace=project_1"
    )
    assert runs.status_code == 200
    assert runs.json()["runs"][0]["duration_ms"] == 125

    wrong_workspace = client.delete(
        f"/api/workbench/schedule/tasks/{task_id}?workspace=other"
    )
    assert wrong_workspace.status_code == 400
    deleted = client.delete(
        f"/api/workbench/schedule/tasks/{task_id}?workspace=project_1"
    )
    assert deleted.status_code == 200
    assert deleted.json()["tasks"] == []


def test_occurrence_query_surfaces_entity_failures(tmp_path, monkeypatch):
    client, _db_path, service = _client(tmp_path, [])

    async def fail(**_kwargs):
        raise RuntimeError("entity database unavailable")

    monkeypatch.setattr(service.entities, "list", fail)
    with pytest.raises(RuntimeError, match="entity database unavailable"):
        asyncio.run(service.list_occurrences("default", "", ""))
    client.close()


def test_schedule_pack_owns_routes_service_and_frontend_module(tmp_path):
    db_path = str(tmp_path / "application.sqlite3")
    asyncio.run(init_db(db_path))
    app = FastAPI()
    host = PluginApplicationHost.load_user_plugins(
        app=app,
        bot=None,
        db_path=db_path,
        data_directory=tmp_path / "data",
        plugin_directory=tmp_path / "plugin_impl",
    )
    router = APIRouter()
    host.attach(router)

    with sqlite3.connect(db_path) as database:
        assert database.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' "
            "AND name = 'scheduled_tasks'"
        ).fetchone() is None

    asyncio.run(host.startup())

    assert "cyrene_schedule" in host.attached_packs
    assert host.service("schedules") is not None
    assert host.service("schedule_application") is not None
    assert "schedule" in host.frontend_modules
    assert any(
        getattr(route, "path", "") == "/api/workbench/schedule/tasks"
        for route in router.routes
    )
    with sqlite3.connect(db_path) as database:
        tables = {
            str(row[0])
            for row in database.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
    assert {"scheduled_tasks", "task_run_logs"} <= tables
    asyncio.run(host.shutdown())
