"""Exercise the production HTTP composition, not a replacement status route."""

from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from cyrene.workbench.artifacts import presentation_runtime as runtime
from cyrene.workbench.http.registry import register_routes
from cyrene.workbench.webui.auth import LocalAuthMiddleware


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setenv("CYRENE_AUTH_TOKEN", "test-secret")
    app = FastAPI()
    app.state.instance_id = "test-instance"
    app.add_middleware(LocalAuthMiddleware)
    register_routes(app, bot=None, db_path=str(tmp_path / "status.sqlite3"))
    with TestClient(app, base_url="http://127.0.0.1") as client:
        yield client


@pytest.mark.parametrize("path", ["/api/health", "/api/status"])
@pytest.mark.parametrize("token", ["", "wrong"])
def test_health_and_status_require_the_current_token(client, path, token):
    assert client.get(path, headers={"X-Cyrene-Token": token}).status_code == 401
    assert client.get("/api/instance-id").json() == {"instance_id": "test-instance"}


def test_health_does_not_depend_on_business_projections(client, monkeypatch):
    def unavailable(*args, **kwargs):
        raise AssertionError("Liveness must not read sessions, models or UI data")

    monkeypatch.setattr(runtime, "_build_sessions", unavailable)
    monkeypatch.setattr(runtime, "_build_ui_data", unavailable)
    monkeypatch.setattr(runtime, "build_status", unavailable)
    monkeypatch.setattr(runtime.project_runtime, "_get_model", unavailable)
    response = client.get("/api/health", headers={"X-Cyrene-Token": "test-secret"})
    assert response.status_code == 200
    assert response.json() == {
        "service": "cyrene", "status": "ok", "instance_id": "test-instance",
    }


def test_status_uses_live_bootstrap_projection(client, monkeypatch):
    sessions = [{"messageCount": 3, "subagents": [{"id": "worker-1"}]}]
    seen_paths = []

    def list_sessions(db_path):
        seen_paths.append(db_path)
        return sessions

    monkeypatch.setattr(runtime, "_build_sessions", list_sessions)
    monkeypatch.setattr(runtime, "_soul_presentation", lambda: {"path": "/soul"})
    monkeypatch.setattr(runtime, "load_entries", lambda: ["entry"])
    monkeypatch.setattr(runtime.project_runtime, "_get_model", lambda: "test-model")
    monkeypatch.setattr(runtime.project_runtime, "_get_base_url", lambda: "http://model")
    for name in ("_build_user", "_build_settings_meta", "get_onboarding_status"):
        monkeypatch.setattr(runtime, name, lambda: {})
    monkeypatch.setattr(runtime, "_build_dashboard", AsyncMock(return_value={}))
    monkeypatch.setattr(runtime, "_build_entities_summary", AsyncMock(return_value=[]))
    headers = {"X-Cyrene-Token": "test-secret"}
    bootstrap = client.get("/api/ui-data", headers=headers)
    response = client.get("/api/status", headers=headers)
    assert bootstrap.status_code == response.status_code == 200
    status = response.json()
    assert status == bootstrap.json()["status"]
    assert status["model"] == "test-model"
    assert status["workers"] == [{"id": "worker-1"}]
    assert status["session_messages"] == 3
    assert status["short_term_entries"] == 1
    assert status["soul_exists"] is True
    assert len(seen_paths) == 2 and seen_paths[0] == seen_paths[1]
    sessions[0]["messageCount"] = 7
    assert client.get("/api/status", headers=headers).json()["session_messages"] == 7


@pytest.mark.asyncio
async def test_chat_transport_separates_health_from_status():
    from cyrene.cli_chat import ChatTransport

    paths = []

    def handle(request):
        paths.append(request.url.path)
        payload = ({"service": "cyrene", "status": "ok"}
                   if request.url.path == "/api/health" else {"model": "test-model"})
        return httpx.Response(200, json=payload)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle), base_url="http://127.0.0.1") as http:
        transport = ChatTransport(client=http)
        assert await transport.health() == {"service": "cyrene", "status": "ok"}
        assert await transport.status() == {"model": "test-model"}
    assert paths == ["/api/health", "/api/status"]


def test_combined_session_refresh_builds_one_projection(client, monkeypatch):
    from cyrene.workbench.sessions.session_presentation import WorkbenchSessionPresentation

    calls = []
    sessions = [{"id": "a", "messageCount": 4, "subagents": [{"id": "worker"}]}]

    def list_sessions(self):
        calls.append(self.db_path)
        return sessions

    monkeypatch.setattr(WorkbenchSessionPresentation, "list", list_sessions)
    headers = {"X-Cyrene-Token": "test-secret"}
    combined = client.get("/api/workbench/sessions?include_status=true", headers=headers)
    assert combined.status_code == 200
    assert len(calls) == 1
    assert combined.json()["sessions"] == sessions
    assert combined.json()["status"]["session_messages"] == 4
    assert combined.json()["status"]["workers"] == [{"id": "worker"}]
    standalone = client.get("/api/status", headers=headers)
    assert standalone.json() == combined.json()["status"]
    assert len(calls) == 2
    assert client.get("/api/workbench/sessions", headers=headers).json() == {"sessions": sessions}
    assert len(calls) == 3
    sessions[0]["messageCount"] = 8
    assert client.get("/api/workbench/sessions?include_status=true", headers=headers).json()["status"]["session_messages"] == 8
    assert len(calls) == 4


def test_optional_status_failure_does_not_discard_sessions(client, monkeypatch, caplog):
    from cyrene.workbench.sessions.session_presentation import WorkbenchSessionPresentation

    monkeypatch.setattr(WorkbenchSessionPresentation, "list", lambda self: [{"id": "a"}])
    monkeypatch.setattr(runtime, "build_status", AsyncMock(side_effect=RuntimeError("unavailable")))
    response = client.get("/api/workbench/sessions?include_status=true", headers={"X-Cyrene-Token": "test-secret"})
    assert response.status_code == 500
    assert response.json() == {"sessions": [{"id": "a"}], "status_error": True}
    assert "Could not build supplemental Workbench status" in caplog.text
