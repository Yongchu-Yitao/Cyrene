"""Tests for the Workbench global search endpoint and helpers."""

import json
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

pil_mock = MagicMock()
pil_mock.__version__ = "9.0.0"
sys.modules["PIL"] = pil_mock
pil_mock.Image = MagicMock()

from cyrene import config as cyrene_config
from cyrene import db
from webui.routes import _search_matches, _search_snippet, _search_workbench_items, register_routes


def test_search_matches_substring():
    assert _search_matches("hello", "Hello world") is True
    assert _search_matches("hello world", "Hello   World") is True
    assert _search_matches("foo", "bar") is False
    assert _search_matches("", "text") is False
    assert _search_matches("query", "") is False


def test_search_snippet_centers_match():
    text = "a " * 50 + "needle" + " b " * 50
    snippet = _search_snippet(text, "needle", length=30)
    assert "needle" in snippet
    assert "…" in snippet or len(snippet) <= 30


def test_search_snippet_flexible_whitespace():
    snippet = _search_snippet("hello   world", "hello world")
    assert "hello" in snippet
    assert "world" in snippet


def test_search_snippet_no_match_returns_prefix():
    assert _search_snippet("just some text", "missing").startswith("just")


@pytest.fixture
def temp_db():
    with TemporaryDirectory() as tmpdir:
        db_path = str(Path(tmpdir) / "test.db")
        import asyncio

        asyncio.run(db.init_db(db_path))
        cyrene_config.set_knowledge_db_path_override(db_path)
        yield db_path
        cyrene_config.set_knowledge_db_path_override(None)


@pytest.fixture
def search_env(monkeypatch, tmp_path, temp_db):
    """Prepare isolated DATA_DIR / STORE_DIR / WORKSPACE_DIR for search tests."""
    from cyrene import config as cyrene_config
    from cyrene import io_utils
    from webui import routes as routes_mod
    from webui import routes_workbench_chat as chat_mod

    data_dir = tmp_path / "data"
    store_dir = tmp_path / "store"
    workspace_dir = tmp_path / "workspace"
    data_dir.mkdir()
    store_dir.mkdir()
    workspace_dir.mkdir()

    monkeypatch.setattr(cyrene_config, "DATA_DIR", data_dir)
    monkeypatch.setattr(cyrene_config, "STORE_DIR", store_dir)
    monkeypatch.setattr(cyrene_config, "WORKSPACE_DIR", workspace_dir)
    monkeypatch.setattr(routes_mod, "DATA_DIR", data_dir)
    monkeypatch.setattr(routes_mod, "WORKSPACE_DIR", workspace_dir)
    monkeypatch.setattr(chat_mod, "DATA_DIR", data_dir)
    chat_mod._CHATS_STORE = data_dir / "workbench_chats.json"
    routes_mod._WORKBENCH_STORE = data_dir / "workbench_projects.json"
    routes_mod._db_path = temp_db

    # Default empty workbench store so _read_workbench_store doesn't create one.
    store = {
        "projects": [
            {
                "id": "project_1",
                "name": "Alpha Project",
                "dataKey": "project_1",
                "description": "The first project",
                "workspacePath": str(workspace_dir),
                "status": "active",
                "model": "gpt-4",
                "context": {"summary": "Alpha summary"},
                "createdAt": "2026-01-01T00:00:00+00:00",
                "updatedAt": "2026-01-02T00:00:00+00:00",
                "sessions": [
                    {
                        "id": "session_1",
                        "projectId": "project_1",
                        "kind": "task",
                        "title": "Fix login bug",
                        "goal": "Investigate the authentication failure",
                        "status": "idle",
                        "priority": "high",
                        "createdAt": "2026-01-01T00:00:00+00:00",
                        "updatedAt": "2026-01-02T00:00:00+00:00",
                    }
                ],
            }
        ],
        "activeProjectId": "project_1",
        "activeSessionId": "session_1",
    }
    io_utils.atomic_write_json(routes_mod._WORKBENCH_STORE, store)

    chats = {
        "chats": [
            {
                "id": "chat_1",
                "projectId": "project_1",
                "title": "Onboarding chat",
                "preview": "Welcome to the project",
                "createdAt": "2026-01-01T00:00:00+00:00",
                "updatedAt": "2026-01-02T00:00:00+00:00",
                "messages": [{"role": "user", "content": "hello there"}],
            }
        ]
    }
    io_utils.atomic_write_json(data_dir / "workbench_chats.json", chats)

    memory = [
        {
            "id": "mem_1",
            "content": "User prefers dark mode",
            "category": "preference",
            "type": "preference",
            "source": "manual",
            "tags": ["ui", "theme"],
            "first_seen": "2026-01-01",
            "last_mentioned": "2026-01-02",
        }
    ]
    io_utils.atomic_write_json(store_dir / "wb_memory_project_1.json", memory)

    # Create a scheduled task scoped to the project data key.
    import aiosqlite
    import asyncio

    async def _create_task():
        async with aiosqlite.connect(temp_db) as conn:
            await conn.execute(
                "INSERT INTO scheduled_tasks (id, chat_id, project_id, prompt, schedule_type, schedule_value, next_run, created_at, permission_mode) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                ("task_1", -1, "project_1", "Daily backup", "cron", "0 0 * * *", "2026-01-02T00:00:00+00:00", "2026-01-01T00:00:00+00:00", "workspace_only"),
            )
            await conn.commit()

    asyncio.run(_create_task())

    yield {"db_path": temp_db, "data_dir": data_dir, "store_dir": store_dir, "routes_mod": routes_mod}


@pytest.fixture
def client(search_env):
    app = FastAPI()
    register_routes(app, bot=None, db_path=search_env["db_path"])
    return TestClient(app)


@pytest.mark.asyncio
async def test_search_workbench_items_project_and_task(search_env):
    groups = await _search_workbench_items("Alpha", {"project", "task"}, 10)
    assert len(groups["project"]) == 1
    assert groups["project"][0]["type"] == "project"
    assert groups["project"][0]["title"] == "Alpha Project"
    assert len(groups["task"]) == 0

    groups = await _search_workbench_items("login", {"project", "task"}, 10)
    assert len(groups["task"]) == 1
    assert groups["task"][0]["type"] == "task"
    assert groups["task"][0]["title"] == "Fix login bug"


@pytest.mark.asyncio
async def test_search_workbench_items_chat(search_env):
    groups = await _search_workbench_items("Onboarding", {"chat"}, 10)
    assert len(groups["chat"]) == 1
    assert groups["chat"][0]["type"] == "chat"

    groups = await _search_workbench_items("hello", {"chat"}, 10)
    assert len(groups["chat"]) == 1
    assert groups["chat"][0]["chatId"] == "chat_1"


@pytest.mark.asyncio
async def test_search_workbench_items_memory(search_env):
    groups = await _search_workbench_items("dark mode", {"memory"}, 10)
    assert len(groups["memory"]) == 1
    assert groups["memory"][0]["type"] == "memory"
    assert groups["memory"][0]["memId"] == "mem_1"


@pytest.mark.asyncio
async def test_search_workbench_items_schedule(search_env):
    groups = await _search_workbench_items("Daily backup", {"schedule"}, 10)
    assert len(groups["schedule"]) == 1
    assert groups["schedule"][0]["type"] == "schedule"
    assert groups["schedule"][0]["taskId"] == "task_1"


@pytest.mark.asyncio
async def test_search_workbench_items_type_filter(search_env):
    groups = await _search_workbench_items("Alpha", {"project"}, 10)
    assert "task" not in groups or len(groups["task"]) == 0
    assert len(groups["project"]) == 1


@pytest.mark.asyncio
async def test_search_workbench_items_no_query():
    groups = await _search_workbench_items("", {"project"}, 10)
    assert groups == {"project": []}


def test_api_workbench_search_returns_grouped_results(client):
    response = client.get("/api/workbench/search", params={"q": "Alpha"})
    assert response.status_code == 200
    data = response.json()
    assert data["ok"] is True
    assert "groups" in data
    assert data["groups"]["project"][0]["title"] == "Alpha Project"


def test_api_workbench_search_type_filter(client):
    response = client.get("/api/workbench/search", params={"q": "Alpha", "types": "project"})
    assert response.status_code == 200
    data = response.json()
    assert data["groups"]["project"]
    assert not data["groups"].get("task")


def test_api_workbench_search_rejects_empty_query(client):
    response = client.get("/api/workbench/search")
    assert response.status_code == 200
    data = response.json()
    assert data["ok"] is False
    assert "query" in data["error"].lower()


def test_api_workbench_search_legacy_endpoint_still_works(client):
    response = client.get("/api/search/conversations", params={"q": "test"})
    assert response.status_code == 200
    data = response.json()
    assert "ok" in data
