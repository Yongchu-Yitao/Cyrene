import json
import logging
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from route.registry import register_routes


def _client(monkeypatch, tmp_path: Path) -> TestClient:
    from cyrene.workbench import runtime as routes

    data_dir = tmp_path / "data"
    data_dir.mkdir()
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    store_path = data_dir / "workbench_projects.json"
    store_path.write_text(
        json.dumps(
            {
                "projects": [
                    {
                        "id": "project_1",
                        "name": "Validation",
                        "workspacePath": str(workspace),
                        "sessions": [
                            {
                                "id": "session_1",
                                "projectId": "project_1",
                                "kind": "task",
                                "title": "Validation task",
                                "goal": "",
                                "status": "idle",
                                "priority": "medium",
                                "constraints": [],
                                "events": [],
                                "runs": [],
                                "artifacts": [],
                                "acceptanceCriteria": [],
                                "plan": [],
                            }
                        ],
                    }
                ],
                "activeProjectId": "project_1",
                "activeSessionId": "session_1",
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(routes, "DATA_DIR", data_dir)
    monkeypatch.setattr(routes, "_WORKBENCH_STORE", store_path)
    monkeypatch.setattr(routes, "append_notification", lambda **_kwargs: {})
    app = FastAPI()
    register_routes(app, bot=None, db_path=str(tmp_path / "test.db"))
    return TestClient(app)


def test_session_patch_rejects_unknown_status(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)

    response = client.patch(
        "/api/task-sessions/session_1",
        json={"status": "arbitrary-status"},
    )

    assert response.status_code == 400
    assert response.json()["code"] == "validation_error"
    current = client.get("/api/task-sessions/session_1")
    assert current.status_code == 200
    assert current.json()["session"]["status"] == "idle"


def test_session_patch_accepts_existing_statuses(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)

    response = client.patch(
        "/api/task-sessions/session_1",
        json={"status": "waiting_for_approval"},
    )

    assert response.status_code == 200
    assert response.json()["session"]["status"] == "waiting_for_approval"


def test_activate_returns_small_selection_payload_without_heavy_store_read(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)

    from cyrene.workbench import runtime as routes

    monkeypatch.setattr(
        routes,
        "_read_workbench_store",
        lambda: (_ for _ in ()).throw(AssertionError("heavy store read must not run")),
    )
    response = client.patch(
        "/api/workbench/activate",
        json={"projectId": "project_1", "sessionId": ""},
    )

    assert response.status_code == 200
    assert response.json() == {
        "ok": True,
        "activeProjectId": "project_1",
        "activeSessionId": "",
    }


def test_unstarted_session_cannot_be_paused(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)

    response = client.patch(
        "/api/task-sessions/session_1",
        json={"status": "paused"},
    )

    assert response.status_code == 409
    assert response.json()["code"] == "invalid_status_transition"
    current = client.get("/api/task-sessions/session_1")
    assert current.status_code == 200
    assert current.json()["session"]["status"] == "idle"


def test_projects_summary_keeps_active_session_full_and_compacts_inactive(monkeypatch, tmp_path):
    from cyrene.workbench import runtime as routes

    client = _client(monkeypatch, tmp_path)
    store_path = routes._WORKBENCH_STORE
    store_path.write_text(
        json.dumps(
            {
                "projects": [
                    {
                        "id": "project_1",
                        "name": "Validation",
                        "workspacePath": str(tmp_path / "workspace"),
                        "sessions": [
                            {
                                "id": "session_active",
                                "projectId": "project_1",
                                "kind": "task",
                                "title": "Active",
                                "status": "completed",
                                "plan": [{"id": "step_1", "title": "Step"}],
                                "events": [{"id": "event_1", "body": "large event body"}],
                                "runs": [{"id": "run_1", "response": "large run body"}],
                                "artifacts": [{"id": "artifact_1"}],
                            },
                            {
                                "id": "session_inactive",
                                "projectId": "project_1",
                                "kind": "task",
                                "title": "Inactive",
                                "status": "completed",
                                "plan": [{"id": "step_2", "title": "Step"}],
                                "events": [{"id": "event_2", "body": "large event body"}],
                                "runs": [{"id": "run_2", "response": "large run body"}],
                                "artifacts": [{"id": "artifact_2"}],
                            },
                        ],
                    }
                ],
                "activeProjectId": "project_1",
                "activeSessionId": "session_active",
            }
        ),
        encoding="utf-8",
    )

    response = client.get("/api/projects?detail=summary")

    assert response.status_code == 200
    sessions = response.json()["projects"][0]["sessions"]
    active = next(item for item in sessions if item["id"] == "session_active")
    inactive = next(item for item in sessions if item["id"] == "session_inactive")
    assert active["events"][0]["id"] == "event_1"
    assert active["runs"][0]["id"] == "run_1"
    assert inactive["isSummary"] is True
    assert inactive["eventCount"] == 1
    assert inactive["runCount"] == 1
    assert "events" not in inactive
    assert "runs" not in inactive


def test_session_detail_returns_project_shell_without_full_sibling_history(monkeypatch, tmp_path):
    from cyrene.workbench import runtime as routes

    client = _client(monkeypatch, tmp_path)
    store_path = routes._WORKBENCH_STORE
    store_path.write_text(
        json.dumps(
            {
                "projects": [
                    {
                        "id": "project_1",
                        "name": "Validation",
                        "workspacePath": str(tmp_path / "workspace"),
                        "sessions": [
                            {
                                "id": "session_1",
                                "projectId": "project_1",
                                "kind": "task",
                                "title": "Target",
                                "status": "completed",
                                "events": [{"id": "event_target"}],
                                "runs": [{"id": "run_target"}],
                                "plan": [],
                            },
                            {
                                "id": "session_sibling",
                                "projectId": "project_1",
                                "kind": "task",
                                "title": "Sibling",
                                "status": "completed",
                                "events": [{"id": "event_sibling"}],
                                "runs": [{"id": "run_sibling"}],
                                "plan": [],
                            },
                        ],
                    }
                ],
                "activeProjectId": "project_1",
                "activeSessionId": "session_1",
            }
        ),
        encoding="utf-8",
    )

    response = client.get("/api/task-sessions/session_1")

    assert response.status_code == 200
    payload = response.json()
    assert payload["session"]["events"][0]["id"] == "event_target"
    sibling = next(item for item in payload["project"]["sessions"] if item["id"] == "session_sibling")
    assert sibling["isSummary"] is True
    assert sibling["eventCount"] == 1
    assert "events" not in sibling
    assert "runs" not in sibling


def test_malformed_json_uses_standard_400_response(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)

    response = client.patch(
        "/api/task-sessions/session_1",
        content="{",
        headers={"Content-Type": "application/json"},
    )

    assert response.status_code == 400
    assert response.json()["code"] == "validation_error"


def test_project_creation_validates_and_creates_writable_workspace(
    monkeypatch, tmp_path
):
    client = _client(monkeypatch, tmp_path)
    target = tmp_path / "new-project"

    response = client.post(
        "/api/projects",
        json={"name": "New project", "workspacePath": str(target)},
    )

    assert response.status_code == 200
    assert response.json()["project"]["workspacePath"] == str(target.resolve())
    assert response.json()["project"]["workspacePathSource"] == "user"
    assert target.is_dir()


def test_project_creation_accepts_snake_case_workspace_alias(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    target = tmp_path / "snake-project"

    response = client.post(
        "/api/projects",
        json={"name": "Snake project", "workspace_path": str(target)},
    )

    assert response.status_code == 200
    assert response.json()["project"]["workspacePath"] == str(target.resolve())
    assert response.json()["project"]["workspacePathSource"] == "user"


def test_project_creation_rejects_workspace_outside_allowed_roots(
    monkeypatch, tmp_path
):
    from route import workspace as workspace_validation

    allowed = tmp_path / "allowed"
    allowed.mkdir()
    outside = tmp_path / "outside"
    monkeypatch.setattr(
        workspace_validation,
        "allowed_workspace_roots",
        lambda: (allowed.resolve(),),
    )
    client = _client(monkeypatch, tmp_path)

    response = client.post(
        "/api/projects",
        json={"name": "Outside", "workspacePath": str(outside)},
    )

    assert response.status_code == 400
    assert response.json()["code"] == "workspace_path_not_allowed"
    assert not outside.exists()


def test_project_update_cannot_bypass_workspace_root_validation(
    monkeypatch, tmp_path
):
    from route import workspace as workspace_validation

    allowed = tmp_path / "allowed"
    allowed.mkdir()
    outside = tmp_path / "outside"
    monkeypatch.setattr(
        workspace_validation,
        "allowed_workspace_roots",
        lambda: (allowed.resolve(),),
    )
    client = _client(monkeypatch, tmp_path)

    response = client.patch(
        "/api/projects/project_1",
        json={"workspacePath": str(outside)},
    )

    assert response.status_code == 400
    assert response.json()["code"] == "workspace_path_not_allowed"
    current = client.get("/api/task-sessions/session_1")
    assert current.json()["project"]["workspacePath"] != str(outside)


def test_default_project_cannot_be_deleted(monkeypatch, tmp_path):
    from cyrene.workbench import runtime as routes

    client = _client(monkeypatch, tmp_path)
    store_path = routes._WORKBENCH_STORE
    store_path.write_text(
        json.dumps(
            {
                "projects": [
                    {
                        "id": "project_default",
                        "name": "Cyrene",
                        "dataKey": "default",
                        "workspacePath": str(tmp_path / "workspace"),
                        "sessions": [],
                    }
                ],
                "activeProjectId": "project_default",
                "activeSessionId": "",
            }
        ),
        encoding="utf-8",
    )

    response = client.delete("/api/projects/project_default")

    assert response.status_code == 400
    assert response.json()["code"] == "default_project_protected"
    remaining = client.get("/api/projects")
    assert remaining.status_code == 200
    assert any(p["id"] == "project_default" for p in remaining.json()["projects"])


def test_non_default_project_can_be_deleted(monkeypatch, tmp_path):
    from cyrene.workbench import runtime as routes

    client = _client(monkeypatch, tmp_path)
    store_path = routes._WORKBENCH_STORE
    store_path.write_text(
        json.dumps(
            {
                "projects": [
                    {
                        "id": "project_default",
                        "name": "Cyrene",
                        "dataKey": "default",
                        "workspacePath": str(tmp_path / "workspace"),
                        "sessions": [],
                    },
                    {
                        "id": "project_extra",
                        "name": "Extra",
                        "dataKey": "extra",
                        "workspacePath": str(tmp_path / "workspace"),
                        "sessions": [],
                    },
                ],
                "activeProjectId": "project_extra",
                "activeSessionId": "",
            }
        ),
        encoding="utf-8",
    )

    response = client.delete("/api/projects/project_extra")

    assert response.status_code == 200
    remaining = client.get("/api/projects")
    ids = [p["id"] for p in remaining.json()["projects"]]
    assert "project_extra" not in ids
    assert "project_default" in ids



def test_workspace_validation_rejects_unwritable_directory(monkeypatch, tmp_path):
    from route import workspace as workspace_validation

    target = tmp_path / "workspace"
    target.mkdir()
    monkeypatch.setattr(
        workspace_validation,
        "allowed_workspace_roots",
        lambda: (tmp_path.resolve(),),
    )

    def deny_write(*_args, **_kwargs):
        raise PermissionError("read-only")

    monkeypatch.setattr(workspace_validation.tempfile, "NamedTemporaryFile", deny_write)

    try:
        workspace_validation.validate_workspace_path(str(target))
    except workspace_validation.WorkspacePathError as exc:
        assert exc.code == "workspace_path_not_writable"
    else:
        raise AssertionError("unwritable workspace was accepted")


def test_unhandled_api_error_returns_500_and_logs_traceback(caplog):
    from route.errors import install_api_exception_handlers

    app = FastAPI()
    install_api_exception_handlers(app)

    @app.get("/explode")
    async def explode():
        raise RuntimeError("boom")

    with caplog.at_level(logging.ERROR, logger="route.errors"):
        response = TestClient(app, raise_server_exceptions=False).get("/explode")

    assert response.status_code == 500
    assert response.json() == {
        "error": "internal server error",
        "code": "internal_server_error",
    }
    assert "RuntimeError: boom" in caplog.text


def test_workbench_storage_error_is_500_and_logged(monkeypatch, tmp_path, caplog):
    from route.workbench import memory as routes_workbench_memory

    client = _client(monkeypatch, tmp_path)

    def fail(_workspace):
        raise OSError("disk failed")

    monkeypatch.setattr(routes_workbench_memory, "_build_payload", fail)
    with caplog.at_level(logging.ERROR, logger="route.workbench.memory"):
        response = client.get("/api/workbench/memory?workspace=project_1")

    assert response.status_code == 500
    assert response.json()["code"] == "memory_list_failed"
    assert "OSError: disk failed" in caplog.text
