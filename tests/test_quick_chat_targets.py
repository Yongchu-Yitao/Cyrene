"""Tests for the quick-chat targets endpoint (/api/workbench/quick-chat/targets).

Covers default-project identification by data key (not name), exclusion of
legacy read-only sessions, search / sort / limit, and authoritative running
status sourced from the in-flight run registry.
"""

from pathlib import Path
from tempfile import TemporaryDirectory

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from cyrene import config as cyrene_config
from cyrene.runtime import database as db
from route.registry import register_routes


@pytest.fixture
def targets_env(monkeypatch, tmp_path):
    from cyrene.runtime import io as io_utils
    from cyrene.workbench import chat as chat_service
    from cyrene.workbench import runtime as routes_mod
    from cyrene.workbench.chat_runs import ChatRunManager

    data_dir = tmp_path / "data"
    store_dir = tmp_path / "store"
    workspace_dir = tmp_path / "workspace"
    for d in (data_dir, store_dir, workspace_dir):
        d.mkdir()

    monkeypatch.setattr(cyrene_config, "DATA_DIR", data_dir)
    monkeypatch.setattr(cyrene_config, "STORE_DIR", store_dir)
    monkeypatch.setattr(cyrene_config, "WORKSPACE_DIR", workspace_dir)
    monkeypatch.setattr(routes_mod, "DATA_DIR", data_dir)
    monkeypatch.setattr(routes_mod, "WORKSPACE_DIR", workspace_dir)
    monkeypatch.setattr(chat_service, "DATA_DIR", data_dir)
    chat_service._CHATS_STORE = data_dir / "workbench_chats.json"
    monkeypatch.setattr(chat_service, "_CHAT_RUN_MANAGER", ChatRunManager(retention_seconds=0))
    routes_mod._WORKBENCH_STORE = data_dir / "workbench_projects.json"

    # Default project deliberately NOT named "Cyrene" — resolution must rely on
    # dataKey == "default", which follows the workspace, not the display name.
    store = {
        "projects": [
            {
                "id": "p_default",
                "name": "MyWorkspace",
                "dataKey": "default",
                "workspacePath": str(workspace_dir),
                "status": "active",
                "model": "gpt-4",
                "sessions": [],
            },
            {
                "id": "p_other",
                "name": "Other Project",
                "dataKey": "p_other",
                "workspacePath": str(workspace_dir / "other"),
                "status": "active",
                "model": "gpt-4o",
                "sessions": [],
            },
        ],
        "activeProjectId": "p_default",
        "activeSessionId": "",
    }
    io_utils.atomic_write_json(routes_mod._WORKBENCH_STORE, store)

    chats = {
        "chats": [
            {
                "id": "chat_alpha",
                "projectId": "p_default",
                "kind": "chat",
                "title": "Alpha planning",
                "status": "idle",
                "model": "gpt-4",
                "updatedAt": "2026-03-03T00:00:00+00:00",
                "messages": [{"id": "m1", "role": "user", "content": "alpha hello"}],
            },
            {
                "id": "chat_beta",
                "projectId": "p_other",
                "kind": "chat",
                "title": "Beta research",
                "status": "idle",
                "model": "gpt-4o",
                "updatedAt": "2026-03-02T00:00:00+00:00",
                "messages": [{"id": "m2", "role": "user", "content": "beta hello"}],
            },
            {
                "id": "chat_gamma",
                "projectId": "p_default",
                "kind": "chat",
                "title": "Gamma notes",
                "status": "idle",
                "model": "gpt-4",
                "updatedAt": "2026-03-01T00:00:00+00:00",
                "messages": [{"id": "m3", "role": "user", "content": "gamma hello"}],
            },
        ]
    }
    io_utils.atomic_write_json(data_dir / "workbench_chats.json", chats)

    with TemporaryDirectory() as db_tmp:
        db_path = str(Path(db_tmp) / "test.db")
        import asyncio

        asyncio.run(db.init_db(db_path))
        cyrene_config.set_knowledge_db_path_override(db_path)
        routes_mod._db_path = db_path
        yield {"db_path": db_path, "data_dir": data_dir, "chat_mod": chat_service}
        cyrene_config.set_knowledge_db_path_override(None)


@pytest.fixture
def client(targets_env):
    app = FastAPI()
    register_routes(app, bot=None, db_path=targets_env["db_path"])
    return TestClient(app)


def test_default_project_resolved_by_data_key(client):
    payload = client.get("/api/workbench/quick-chat/targets").json()
    default = payload["defaultProject"]
    assert default["id"] == "p_default"
    assert default["dataKey"] == "default"
    # Identified by data key, not by being called "Cyrene".
    assert default["name"] == "MyWorkspace"


def test_targets_span_projects_sorted_and_labeled(client):
    payload = client.get("/api/workbench/quick-chat/targets").json()
    targets = payload["targets"]
    # Most-recent first across all projects.
    assert [t["chatId"] for t in targets] == ["chat_alpha", "chat_beta", "chat_gamma"]
    by_id = {t["chatId"]: t for t in targets}
    assert by_id["chat_beta"]["projectName"] == "Other Project"
    assert by_id["chat_alpha"]["projectName"] == "MyWorkspace"
    # All modern chats are writable; none are legacy read-only sessions.
    assert all(t["writable"] for t in targets)
    assert not any(str(t["chatId"]).startswith("legacy:") for t in targets)


def test_targets_search_matches_title_and_project(client):
    by_title = client.get("/api/workbench/quick-chat/targets", params={"q": "beta"}).json()
    assert [t["chatId"] for t in by_title["targets"]] == ["chat_beta"]

    by_project = client.get("/api/workbench/quick-chat/targets", params={"q": "other"}).json()
    assert [t["chatId"] for t in by_project["targets"]] == ["chat_beta"]

    none = client.get("/api/workbench/quick-chat/targets", params={"q": "zzzznope"}).json()
    assert none["targets"] == []


def test_targets_limit_caps_results(client):
    payload = client.get("/api/workbench/quick-chat/targets", params={"limit": 1}).json()
    assert [t["chatId"] for t in payload["targets"]] == ["chat_alpha"]


def test_historical_ui_mode_is_normalized_to_workbench(targets_env):
    # Historical callers may still pass a legacy ui_mode value to create_app,
    # but the sole shell and Quick Chat surface must both render Workbench.
    app = FastAPI()
    app.state.ui_mode = "legacy"
    register_routes(app, bot=None, db_path=targets_env["db_path"])
    client = TestClient(app)

    plain = client.get("/", follow_redirects=False)
    assert plain.status_code == 200
    assert "shell=legacy" not in plain.headers.get("location", "")

    surfaced = client.get("/?surface=quick-chat", follow_redirects=False)
    assert surfaced.status_code == 200


def test_running_status_reflects_run_registry(client, targets_env):
    from cyrene.workbench.chat_runs import ChatRun

    manager = targets_env["chat_mod"]._CHAT_RUN_MANAGER
    manager.runs["chat_beta"] = ChatRun("chat_beta", {"type": "ack", "chatId": "chat_beta"})

    payload = client.get("/api/workbench/quick-chat/targets").json()
    by_id = {t["chatId"]: t for t in payload["targets"]}
    assert by_id["chat_beta"]["running"] is True
    assert by_id["chat_alpha"]["running"] is False
    assert by_id["chat_gamma"]["running"] is False
