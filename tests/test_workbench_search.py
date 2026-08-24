"""Tests for the Workbench global search endpoint and helpers."""

import asyncio
import json
import time
from pathlib import Path
from tempfile import TemporaryDirectory

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from cyrene import config as cyrene_config
from cyrene.runtime import database as db
from cyrene.workbench.runtime import _search_matches, _search_snippet, _search_workbench_items
from route.registry import register_routes


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
    from cyrene.runtime import io as io_utils
    from cyrene.workbench import chat as chat_service
    from cyrene.workbench import runtime as routes_mod

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
    monkeypatch.setattr(chat_service, "DATA_DIR", data_dir)
    chat_service._CHATS_STORE = data_dir / "workbench_chats.json"
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


def test_voice_command_silence_does_not_create_chat(client, search_env, monkeypatch):
    from cyrene.voice import engine as voice_engine

    monkeypatch.setattr(
        voice_engine,
        "status",
        lambda: {"asr_ready": True, "tts_ready": True},
    )
    monkeypatch.setattr(
        voice_engine,
        "transcribe",
        lambda _payload: {"text": "", "silence_only": True},
    )
    chats_path = search_env["data_dir"] / "workbench_chats.json"
    before = chats_path.read_text(encoding="utf-8")

    response = client.post(
        "/api/workbench/voice-command",
        files={"audio": ("silence.wav", b"RIFF-silence", "audio/wav")},
        data={"lang": "zh"},
    )

    assert response.status_code == 200
    assert response.json() == {"ok": True, "created": False, "text": ""}
    assert chats_path.read_text(encoding="utf-8") == before


def test_voice_command_silently_creates_auto_chat_in_default_project(
    client, search_env, monkeypatch,
):
    import cyrene.agent as agent
    from cyrene.voice import engine as voice_engine

    async def fake_run_agent(**_kwargs):
        return "后台命令已完成。"

    monkeypatch.setattr(agent, "run_agent", fake_run_agent)
    monkeypatch.setattr(
        voice_engine,
        "status",
        lambda: {"asr_ready": True, "tts_ready": True},
    )
    monkeypatch.setattr(
        voice_engine,
        "transcribe",
        lambda _payload: {"text": "整理今天的计划", "silence_only": False},
    )
    workbench_store = search_env["routes_mod"]._read_workbench_store()
    active_before = (
        workbench_store.get("activeProjectId"),
        workbench_store.get("activeSessionId"),
    )

    response = client.post(
        "/api/workbench/voice-command",
        files={"audio": ("command.wav", b"RIFF-command", "audio/wav")},
        data={"lang": "zh", "ui_instance_id": "test-ui"},
    )

    assert response.status_code == 200
    result = response.json()
    assert result["created"] is True
    assert result["run_id"]
    chats = json.loads(
        (search_env["data_dir"] / "workbench_chats.json").read_text(encoding="utf-8")
    )["chats"]
    created = next(chat for chat in chats if chat["id"] == result["chat_id"])
    assert created["projectId"] == "project_1"
    assert created["permissionMode"] == "auto"
    assert any(
        message.get("role") == "user" and message.get("content") == "整理今天的计划"
        for message in created["messages"]
    )
    store_after = search_env["routes_mod"]._read_workbench_store()
    assert (store_after.get("activeProjectId"), store_after.get("activeSessionId")) == active_before


def test_side_agents_are_multiple_persistent_sessions_hidden_from_main_chat_list(
    client, search_env,
):
    first = client.post(
        "/api/workbench/chats/chat_1/side-agents",
        json={"quote": "First selected passage"},
    )
    second = client.post(
        "/api/workbench/chats/chat_1/side-agents",
        json={"quote": "Second selected passage"},
    )

    assert first.status_code == 200
    assert second.status_code == 200
    first_agent = first.json()["agent"]
    second_agent = second.json()["agent"]
    assert first_agent["id"] != second_agent["id"]
    assert first_agent["kind"] == "side-agent"
    assert first_agent["parentChatId"] == "chat_1"
    assert first_agent["sourceQuote"] == "First selected passage"

    agents = client.get("/api/workbench/chats/chat_1/side-agents").json()["agents"]
    assert [item["id"] for item in agents] == [
        first_agent["id"],
        second_agent["id"],
    ]

    visible_chats = client.get(
        "/api/workbench/chats",
        params={"project": "project_1"},
    ).json()["chats"]
    assert [item["id"] for item in visible_chats] == ["chat_1"]

    stored = json.loads(
        (search_env["data_dir"] / "workbench_chats.json").read_text(
            encoding="utf-8"
        )
    )
    assert len(stored["chats"]) == 3


def test_side_agent_receives_selected_quote_without_polluting_public_question(
    client, monkeypatch,
):
    from cyrene import agent

    captured = {}

    async def fake_run_agent(**kwargs):
        captured.update(kwargs)
        return "Side answer"

    monkeypatch.setattr(agent, "run_agent", fake_run_agent)
    created = client.post(
        "/api/workbench/chats/chat_1/side-agents",
        json={"quote": "The selected source text"},
    ).json()["agent"]

    response = client.post(
        f"/api/workbench/chats/{created['id']}/messages",
        json={"message": "What does this mean?"},
    )

    assert response.status_code == 200
    assert "<selected_quote>\nThe selected source text\n</selected_quote>" in captured[
        "user_message"
    ]
    assert "<main_conversation>" in captured["user_message"]
    assert "[1. user]\nhello there" in captured["user_message"]
    assert "What does this mean?" in captured["user_message"]
    assert captured["public_user_message"] == "What does this mean?"
    detail = client.get(f"/api/workbench/chats/{created['id']}").json()["chat"]
    assert detail["messages"][0]["content"] == "What does this mean?"
    assert detail["messages"][-1]["content"] == "Side answer"


def test_rename_workbench_chat_persists_trimmed_title(client, search_env):
    response = client.patch(
        "/api/workbench/chats/chat_1",
        json={"title": "  Renamed conversation  "},
    )

    assert response.status_code == 200
    assert response.json()["chat"]["title"] == "Renamed conversation"
    stored = json.loads(
        (search_env["data_dir"] / "workbench_chats.json").read_text(encoding="utf-8")
    )
    assert stored["chats"][0]["title"] == "Renamed conversation"
    assert stored["chats"][0]["titleLocked"] is True
    assert stored["chats"][0]["updatedAt"] != "2026-01-02T00:00:00+00:00"


def test_chat_group_metadata_endpoint_forwards_language_and_title_lock(
    client, search_env, monkeypatch,
):
    from cyrene.workbench import chat as chat_routes

    captured = {}

    async def fake_generate(members, **kwargs):
        captured["members"] = members
        captured["kwargs"] = kwargs
        return {"title": "", "summary": "浏览器操作相关对话", "lang": "zh"}

    monkeypatch.setattr(chat_routes, "generate_chat_group_metadata", fake_generate)
    response = client.post(
        "/api/workbench/chat-groups/metadata",
        json={
            "groupId": "group_1",
            "members": [
                {"id": "chat_1", "title": "打开 B 站", "preview": "已打开首页"},
                {"id": "chat_2", "title": "打开 Google", "preview": "已打开搜索页"},
            ],
            "currentTitle": "我的浏览器对话",
            "titleLocked": True,
            "lang": "zh",
        },
    )

    assert response.status_code == 200
    assert response.json()["metadata"]["summary"] == "浏览器操作相关对话"
    assert captured["kwargs"] == {
        "lang": "zh",
        "title_locked": True,
        "current_title": "我的浏览器对话",
    }
    assert len(captured["members"]) == 2


def test_chat_group_metadata_endpoint_persists_before_returning(
    client, search_env, monkeypatch,
):
    from cyrene.workbench import chat as chat_routes
    from cyrene.workbench import chat_groups as chat_groups_service

    calls = []

    def fake_context(project_id, group_id, *, signature):
        calls.append(("context", project_id, group_id, signature))
        return {
            "group": {"id": group_id, "title": "新对话组", "titleLocked": False},
            "members": [
                {"id": "chat_1", "title": "打开 B 站", "preview": "首页已打开"},
                {"id": "chat_2", "title": "打开 Google", "preview": "搜索页已打开"},
            ],
            "signature": "chat_1|chat_2",
        }

    async def fake_generate(members, **kwargs):
        calls.append(("generate", members, kwargs))
        return {"title": "浏览器操作", "summary": "整理网站访问结果。", "lang": "zh"}

    async def fake_update(project_id, group_id, *, signature, metadata):
        calls.append(("persist", project_id, group_id, signature, metadata))
        return {"groups": [{
            "id": group_id,
            "title": metadata["title"],
            "summary": metadata["summary"],
            "chatIds": ["chat_1", "chat_2"],
        }]}

    monkeypatch.setattr(chat_groups_service, "get_group_metadata_context", fake_context)
    monkeypatch.setattr(chat_routes, "generate_chat_group_metadata", fake_generate)
    monkeypatch.setattr(chat_groups_service, "update_group_metadata", fake_update)
    response = client.post(
        "/api/workbench/chat-groups/metadata",
        json={
            "projectId": "project_1",
            "groupId": "group_1",
            "signature": "chat_1|chat_2",
            "members": [{"id": "ignored_1"}, {"id": "ignored_2"}],
            "lang": "zh",
        },
    )

    assert response.status_code == 200
    assert [call[0] for call in calls] == ["context", "generate", "persist"]
    assert calls[1][1][0]["id"] == "chat_1"
    assert calls[1][2]["current_title"] == "新对话组"
    assert response.json()["group"]["title"] == "浏览器操作"


def test_delete_workbench_legacy_chat_uses_session_delete(client, search_env, monkeypatch):
    routes_mod = search_env["routes_mod"]
    deleted = []

    async def fake_delete_chat_session(session_id):
        deleted.append(session_id)
        return {"ok": True, "sessions": []}, 200

    monkeypatch.setattr(routes_mod, "_delete_chat_session", fake_delete_chat_session)
    monkeypatch.setattr(
        routes_mod, "_workbench_project_data_key", lambda project: "default"
    )

    response = client.delete(
        "/api/workbench/chats/legacy%3Aproject_1%3Aarchive_2026-01-01_session_1"
    )

    assert response.status_code == 200
    assert response.json() == {"ok": True}
    assert deleted == ["archive_2026-01-01_session_1"]


def test_delete_regular_workbench_chat_still_removes_store(
    client, search_env, monkeypatch,
):
    import cyrene.agent as agent

    async def fake_clear_session_id(session_id="", **_kwargs):
        return None

    monkeypatch.setattr(agent, "clear_session_id", fake_clear_session_id)
    monkeypatch.setattr(agent, "interrupt_active_run", lambda session_id="": False)

    response = client.delete("/api/workbench/chats/chat_1")

    assert response.status_code == 200
    payload = json.loads(
        (search_env["data_dir"] / "workbench_chats.json").read_text(encoding="utf-8")
    )
    assert payload["chats"] == []


def test_interrupt_workbench_chat_settles_persisted_running_status(
    client, search_env,
):
    chats_path = search_env["data_dir"] / "workbench_chats.json"
    payload = json.loads(chats_path.read_text(encoding="utf-8"))
    payload["chats"][0]["status"] = "running"
    chats_path.write_text(json.dumps(payload), encoding="utf-8")

    response = client.post("/api/chat/interrupt?session_id=chat_1")

    assert response.status_code == 200
    assert response.json()["ok"] is True
    settled = json.loads(chats_path.read_text(encoding="utf-8"))
    assert settled["chats"][0]["status"] == "idle"


def test_compact_workbench_chat_is_an_explicit_forced_action(
    client, search_env, monkeypatch,
):
    import cyrene.agent as agent

    calls = []

    async def fake_compact(session_id="", *, ctx_limit=None, force=False):
        calls.append({
            "session_id": session_id,
            "ctx_limit": ctx_limit,
            "force": force,
        })
        return {
            "compacted": True,
            "reason": "compacted",
            "beforeTokens": 50,
            "afterTokens": 25,
            "ctxLimit": ctx_limit,
            "triggerRatio": 0.6,
        }

    monkeypatch.setattr(agent, "compact_session_if_needed", fake_compact)

    response = client.post("/api/workbench/chats/chat_1/compact")

    assert response.status_code == 200
    assert response.json()["compacted"] is True
    assert len(calls) == 1
    assert calls[0]["session_id"] == "chat_1"
    assert calls[0]["force"] is True
    assert calls[0]["ctx_limit"] > 0


def test_compact_workbench_chat_returns_running_reason_as_promptable_result(
    client, search_env, monkeypatch,
):
    import cyrene.agent as agent

    async def fake_compact(session_id="", *, ctx_limit=None, force=False):
        return {"compacted": False, "reason": "running"}

    monkeypatch.setattr(agent, "compact_session_if_needed", fake_compact)

    response = client.post("/api/workbench/chats/chat_1/compact")

    assert response.status_code == 200
    assert response.json() == {"ok": True, "compacted": False, "reason": "running"}


def test_empty_legacy_live_session_is_not_listed(search_env, monkeypatch):
    from cyrene.workbench import chat as chat_mod

    monkeypatch.setattr(
        search_env["routes_mod"],
        "_build_sessions",
        lambda: [
            {
                "id": "run_live",
                "title": "new session",
                "status": "idle",
                "chat": {"messages": []},
            },
            {
                "id": "archive_2026-01-01_session_1",
                "title": "Archived chat",
                "status": "done",
                "chat": {"messages": [{"role": "user", "body": "hello"}]},
            },
        ],
    )

    chats = chat_mod._legacy_chats("project_1")

    assert [chat["id"] for chat in chats] == [
        "legacy:project_1:archive_2026-01-01_session_1"
    ]


@pytest.mark.asyncio
async def test_delete_archived_chat_session_removes_real_archive_sections(
    search_env, monkeypatch, tmp_path,
):
    routes_mod = search_env["routes_mod"]
    conversations_dir = tmp_path / "conversations"
    conversations_dir.mkdir()
    archive = conversations_dir / "2026-01-01.md"
    archive.write_text(
        "# Conversations - 2026-01-01\n\n"
        "## 10:00:00 UTC\n"
        "<!-- archive_session_id: session_keep -->\n\n"
        "**User**: keep\n\n"
        "**Cyrene**: kept\n\n"
        "---\n\n"
        "## 11:00:00 UTC\n"
        "<!-- archive_session_id: session_delete -->\n\n"
        "**User**: delete\n\n"
        "**Cyrene**: deleted\n\n"
        "---\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(routes_mod, "CONVERSATIONS_DIR", conversations_dir)
    monkeypatch.setattr(routes_mod, "_build_sessions", lambda: [])

    payload, status_code = await routes_mod._delete_chat_session(
        "archive_2026-01-01_session_delete"
    )

    assert status_code == 200
    assert payload["ok"] is True
    remaining = routes_mod._parse_archive_sections(
        archive.read_text(encoding="utf-8")
    )
    assert [section["archive_session_id"] for section in remaining] == [
        "session_keep"
    ]


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
async def test_search_workbench_items_hides_internal_task_reports(search_env):
    from cyrene.runtime import io as io_utils

    io_utils.atomic_write_json(
        search_env["store_dir"] / "wb_memory_project_1.json",
        [
            {
                "id": "mem_1",
                "content": "User prefers dark mode",
                "category": "preference",
                "type": "preference",
                "source": "manual",
                "tags": ["ui", "theme"],
                "first_seen": "2026-01-01",
                "last_mentioned": "2026-01-02",
            },
            {
                "id": "mem_report",
                "content": "Task report: dark mode migration completed",
                "category": "task_report",
                "type": "task_report",
                "source": "agent",
                "tags": ["task report"],
                "first_seen": "2026-01-02",
                "last_mentioned": "2026-01-02",
            },
        ],
    )

    groups = await _search_workbench_items("dark mode", {"memory"}, 10)

    assert [item["memId"] for item in groups["memory"]] == ["mem_1"]


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


@pytest.mark.asyncio
async def test_search_uses_lightweight_store_without_blocking_event_loop(monkeypatch):
    from cyrene.workbench import runtime as routes_mod

    payload = {
        "projects": [{
            "id": "project_fast",
            "name": "Fast project",
            "description": "search target",
            "context": {},
            "sessions": [],
        }]
    }

    def slow_lightweight_read():
        time.sleep(0.1)
        return payload

    monkeypatch.setattr(routes_mod, "_read_workbench_store_lightweight", slow_lightweight_read)
    monkeypatch.setattr(
        routes_mod,
        "_read_workbench_store",
        lambda: (_ for _ in ()).throw(
            AssertionError("full repair reader must not run during search")
        ),
    )

    search_task = asyncio.create_task(
        routes_mod._search_workbench_items("target", {"project"}, 10)
    )
    await asyncio.sleep(0.02)

    assert not search_task.done()
    groups = await search_task
    assert [item["id"] for item in groups["project"]] == ["project_fast"]


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


def test_workbench_chat_run_uses_project_workspace(client, search_env, monkeypatch):
    from cyrene import agent
    from cyrene.runtime import host_bridge

    captured = {}

    async def fake_run_agent(**kwargs):
        captured.update(kwargs)
        return "done"

    async def fake_resolve_conversation_source(ui_instance_id):
        assert ui_instance_id == "surface-send-test"
        return "desktop_local"

    monkeypatch.setattr(agent, "run_agent", fake_run_agent)
    monkeypatch.setattr(
        host_bridge, "resolve_conversation_source", fake_resolve_conversation_source,
    )

    response = client.post(
        "/api/workbench/chats/chat_1/messages",
        json={
            "message": "inspect the project",
            "clientRequestId": "send_test_1",
            "uiInstanceId": "surface-send-test",
        },
    )

    assert response.status_code == 200
    assistant_message = response.json()["assistantMessage"]
    assert assistant_message["content"] == "done"
    assert isinstance(assistant_message["processingDurationMs"], int)
    assert assistant_message["processingDurationMs"] >= 0
    assert response.json()["userMessage"]["clientRequestId"] == "send_test_1"
    assert captured["workspace_dir"] == str(
        (search_env["data_dir"].parent / "workspace").resolve()
    )
    assert captured["ui_instance_id"] == "surface-send-test"
    assert captured["conversation_source"] == "desktop_local"
    chats = json.loads(
        (search_env["data_dir"] / "workbench_chats.json").read_text(encoding="utf-8")
    )
    assert (
        chats["chats"][0]["messages"][-1]["processingDurationMs"]
        == assistant_message["processingDurationMs"]
    )
    assert chats["chats"][0]["messages"][-2]["clientRequestId"] == "send_test_1"


def test_workbench_chat_run_uses_and_persists_workspace_override(
    client, search_env, monkeypatch,
):
    from cyrene import agent

    override = search_env["data_dir"].parent / "manually-selected"
    override.mkdir()
    captured = []

    async def fake_run_agent(**kwargs):
        captured.append(kwargs)
        return "done"

    monkeypatch.setattr(agent, "run_agent", fake_run_agent)

    response = client.post(
        "/api/workbench/chats/chat_1/messages",
        json={"message": "inspect it", "workspaceOverride": str(override)},
    )

    assert response.status_code == 200
    assert captured[-1]["workspace_dir"] == str(override.resolve())
    chats_path = search_env["data_dir"] / "workbench_chats.json"
    stored = json.loads(chats_path.read_text(encoding="utf-8"))["chats"][0]
    assert stored["workspaceOverride"] == str(override.resolve())
    listed = client.get("/api/workbench/chats?project=project_1").json()["chats"][0]
    assert listed["workspaceOverride"] == str(override.resolve())

    # Older clients and non-composer execution paths can omit the field; the
    # conversation keeps using its durable override instead of reverting.
    follow_up = client.post(
        "/api/workbench/chats/chat_1/messages",
        json={"message": "inspect it again"},
    )
    assert follow_up.status_code == 200
    assert captured[-1]["workspace_dir"] == str(override.resolve())


def test_workbench_chat_preferences_are_session_scoped_and_bound_to_run(
    client, search_env, monkeypatch,
):
    from cyrene import agent
    from cyrene.runtime import model_configuration, settings_store

    monkeypatch.setattr(settings_store, "get_models", lambda: [{
        "id": "session-model",
        "name": "Session Model",
        "model": "provider/session-model",
    }])
    monkeypatch.setattr(model_configuration, "selectable_model_candidates", lambda: [{
        "id": "session-model",
        "name": "Session Model",
        "model": "provider/session-model",
        "reasoning_effort": "",
    }])
    captured = []

    async def fake_run_agent(**kwargs):
        captured.append(kwargs)
        return "done"

    monkeypatch.setattr(agent, "run_agent", fake_run_agent)

    updated = client.patch(
        "/api/workbench/chats/chat_1",
        json={
            "soulActive": False,
            "workspaceActive": False,
            "model": "session-model",
            "reasoningEffort": "xhigh",
        },
    )

    assert updated.status_code == 200
    chat = updated.json()["chat"]
    assert chat["soulActive"] is False
    assert chat["workspaceActive"] is False
    assert chat["modelSelectionId"] == "session-model"
    assert chat["reasoningEffort"] == "xhigh"
    assert chat["remoteDeviceIds"] == []

    sent = client.post(
        "/api/workbench/chats/chat_1/messages",
        json={"message": "use this session's context"},
    )

    assert sent.status_code == 200
    assert captured[-1]["soul_enabled"] is False
    assert captured[-1]["workspace_enabled"] is False
    stored = json.loads(
        (search_env["data_dir"] / "workbench_chats.json").read_text(encoding="utf-8")
    )["chats"][0]
    assert stored["soulActive"] is False
    assert stored["workspaceActive"] is False
    assert stored["reasoningEffort"] == "xhigh"


def test_workbench_chat_rejects_unavailable_workspace_override(
    client, search_env, monkeypatch,
):
    from cyrene import agent

    called = False

    async def fake_run_agent(**kwargs):
        nonlocal called
        called = True
        return "done"

    monkeypatch.setattr(agent, "run_agent", fake_run_agent)
    missing = search_env["data_dir"].parent / "missing-directory"

    response = client.post(
        "/api/workbench/chats/chat_1/messages",
        json={"message": "inspect it", "workspaceOverride": str(missing)},
    )

    assert response.status_code == 400
    assert "does not exist" in response.json()["error"]
    assert called is False


def test_chat_session_is_llm_named_only_after_its_first_message(
    client, search_env, monkeypatch,
):
    from cyrene import agent
    from cyrene.model_runtime import client as model_client
    from cyrene.workbench import session_naming

    calls = []

    async def fake_run_agent(**kwargs):
        return "done"

    async def fake_generate(message, *, limit=60, candidate=None):
        calls.append(message)
        assert candidate is not None
        await asyncio.sleep(0)
        return "检查当前 Session 命名"

    monkeypatch.setattr(agent, "run_agent", fake_run_agent)
    monkeypatch.setattr(
        model_client,
        "resolve_session_model_candidate",
        lambda _session_id: {"id": "test", "model": "test-model"},
    )
    monkeypatch.setattr(session_naming, "generate_session_title", fake_generate)

    created = client.post(
        "/api/workbench/chats",
        json={"projectId": "project_1"},
    ).json()["chat"]
    chat_id = created["id"]

    first = client.post(
        f"/api/workbench/chats/{chat_id}/messages",
        json={"message": "检查目前 Cyrene 的 session 有没有 LLM 自动命名"},
    )
    assert first.status_code == 200

    deadline = time.monotonic() + 1
    stored_chat = None
    while time.monotonic() < deadline:
        payload = json.loads(
            (search_env["data_dir"] / "workbench_chats.json").read_text(
                encoding="utf-8"
            )
        )
        stored_chat = next(item for item in payload["chats"] if item["id"] == chat_id)
        if stored_chat.get("titleNamingStatus") == "generated":
            break
        time.sleep(0.01)

    assert stored_chat is not None
    assert stored_chat["title"] == "检查当前 Session 命名"
    assert stored_chat["titleNamingStatus"] == "generated"

    second = client.post(
        f"/api/workbench/chats/{chat_id}/messages",
        json={"message": "再检查一次"},
    )
    assert second.status_code == 200
    assert calls == ["检查目前 Cyrene 的 session 有没有 LLM 自动命名"]


def test_workbench_chat_run_persists_non_git_workspace_diff(
    client, search_env, monkeypatch,
):
    from cyrene import agent

    workspace = search_env["data_dir"].parent / "workspace"
    target = workspace / "src" / "feature.py"
    target.parent.mkdir()
    target.write_text("enabled = False\n", encoding="utf-8")

    async def fake_run_agent(**kwargs):
        assert Path(kwargs["workspace_dir"]) == workspace.resolve()
        target.write_text("enabled = True\n", encoding="utf-8")
        (workspace / "created.md").write_text("# Created\n", encoding="utf-8")
        return "done"

    monkeypatch.setattr(agent, "run_agent", fake_run_agent)
    response = client.post(
        "/api/workbench/chats/chat_1/messages",
        json={"message": "change files"},
    )
    assert response.status_code == 200

    changes_response = client.get("/api/workbench/chats/chat_1/changes")
    assert changes_response.status_code == 200
    payload = changes_response.json()
    assert payload["fileCount"] == 2
    latest = payload["changeSets"][0]
    by_path = {item["path"]: item for item in latest["files"]}
    assert by_path["src/feature.py"]["changeType"] == "modified"
    assert by_path["created.md"]["changeType"] == "created"
    assert all("diff" not in item for item in latest["files"])

    diff_response = client.get(
        "/api/workbench/chats/chat_1/changes/"
        f"{latest['id']}/files/src/feature.py"
    )
    assert diff_response.status_code == 200
    diff = diff_response.json()["change"]["diff"]
    assert "-enabled = False" in diff
    assert "+enabled = True" in diff


def test_workbench_chat_persists_intermediate_messages_between_tool_cards(
    client, search_env, monkeypatch,
):
    from cyrene import agent
    from cyrene.workbench import chat as chat_mod

    state_messages = [{"role": "user", "content": "old"}]

    async def fake_run_agent(**_kwargs):
        state_messages.extend([
            {
                "role": "assistant",
                "created_at": chat_mod._utc_now_iso(),
                "tool_calls": [{
                    "id": "search_1",
                    "function": {
                        "name": "WebSearch",
                        "arguments": json.dumps({"query": "first"}),
                    },
                }],
            },
            {"role": "tool", "tool_call_id": "search_1", "content": "found"},
            {
                "role": "assistant",
                "content": "先汇报阶段结果，我继续处理。",
                "message_id": "mid_1",
                "created_at": chat_mod._utc_now_iso(),
                "intermediate_reply": True,
            },
            {
                "role": "assistant",
                "created_at": chat_mod._utc_now_iso(),
                "tool_calls": [
                    {
                        "id": "message_1",
                        "function": {
                            "name": "send_message",
                            "arguments": json.dumps({"text": "先汇报阶段结果，我继续处理。"}),
                        },
                    },
                    {
                        "id": "bash_1",
                        "function": {
                            "name": "Bash",
                            "arguments": json.dumps({"command": "echo done"}),
                        },
                    },
                ],
            },
            {"role": "tool", "tool_call_id": "message_1", "content": "sent"},
            {"role": "tool", "tool_call_id": "bash_1", "content": "done"},
            {
                "role": "assistant",
                "content": "最终完成。",
                "created_at": chat_mod._utc_now_iso(),
            },
        ])
        return "最终完成。"

    monkeypatch.setattr(agent, "run_agent", fake_run_agent)
    monkeypatch.setattr(chat_mod, "_session_state_messages", lambda _chat_id: list(state_messages))

    response = client.post(
        "/api/workbench/chats/chat_1/messages",
        json={"message": "do it"},
    )

    assert response.status_code == 200
    saved = response.json()["assistantMessages"]
    assert [message.get("activityCard", False) for message in saved] == [
        True,
        False,
        True,
        False,
    ]
    assert [message["content"] for message in saved] == [
        "",
        "先汇报阶段结果，我继续处理。",
        "",
        "最终完成。",
    ]
    assert [entry["tool"] for entry in saved[0]["trace"]] == ["WebSearch"]
    assert [entry["tool"] for entry in saved[2]["trace"]] == ["Bash"]

    chats = json.loads((search_env["data_dir"] / "workbench_chats.json").read_text(encoding="utf-8"))
    transcript = chats["chats"][0]["messages"]
    assert [message["content"] for message in transcript[-4:]] == [
        "",
        "先汇报阶段结果，我继续处理。",
        "",
        "最终完成。",
    ]


def test_workbench_chat_network_failure_requests_resend(client, search_env, monkeypatch):
    from cyrene import agent
    from cyrene.call_llm import NETWORK_RETRY_LIMIT

    async def fake_run_agent(**_kwargs):
        raise httpx.RemoteProtocolError("Server disconnected without sending a response.")

    monkeypatch.setattr(agent, "run_agent", fake_run_agent)

    response = client.post(
        "/api/workbench/chats/chat_1/messages",
        json={"message": "inspect the project", "stream": True, "lang": "zh"},
    )

    assert response.status_code == 200
    events = [json.loads(line) for line in response.text.splitlines() if line.strip()]
    error = next(event for event in events if event.get("type") == "error")
    assert error["error"] == "model_call_failed"
    assert error["message"] == (
        f"网络连接异常，已自动重试 {NETWORK_RETRY_LIMIT} 次仍未成功。请重新发送这条消息。"
    )


def test_workbench_chat_codex_quota_error_includes_i18n_metadata(client, search_env, monkeypatch):
    from cyrene import agent
    from cyrene.model_runtime.codex_provider import (
        CODEX_QUOTA_EXHAUSTED,
        CodexAvailabilityError,
    )

    async def fake_run_agent(**_kwargs):
        raise CodexAvailabilityError(
            CODEX_QUOTA_EXHAUSTED,
            "Codex quota is exhausted; wait for the quota window to reset",
        )

    monkeypatch.setattr(agent, "run_agent", fake_run_agent)

    response = client.post(
        "/api/workbench/chats/chat_1/messages",
        json={"message": "inspect the project", "stream": True, "lang": "zh"},
    )

    assert response.status_code == 200
    events = [json.loads(line) for line in response.text.splitlines() if line.strip()]
    error = next(event for event in events if event.get("type") == "error")
    assert error["code"] == CODEX_QUOTA_EXHAUSTED
    assert error["detail_key"] == "workbenchChat.error.quotaExhausted"


def test_workspace_scope_block_uses_runtime_workspace(tmp_path):
    from cyrene.agent.prompts import workspace_scope_block

    project_workspace = tmp_path / "project"
    block = workspace_scope_block(project_workspace)

    assert f"Use `{project_workspace}` as the default root" in block
    assert "already starts at the workspace root" in block
    assert f"without `cd {project_workspace}`" in block
    assert "proactively inspect the workspace" in block
    assert "read applicable instruction files" in block


def test_workbench_chat_answer_resumes_in_conversation_workspace(
    client, search_env, monkeypatch,
):
    from cyrene.workbench import runtime as routes_mod
    from route.workbench.chat_routes import run_answer_routes

    chats_path = search_env["data_dir"] / "workbench_chats.json"
    chats = json.loads(chats_path.read_text(encoding="utf-8"))
    override = search_env["data_dir"].parent / "answer-workspace"
    override.mkdir()
    chats["chats"][0]["workspaceOverride"] = str(override)
    chats["chats"][0]["pendingQuestion"] = {"id": "question_1"}
    chats["chats"][0]["lastRun"] = {
        "id": "paused_run",
        "status": "done",
        "terminationReason": "awaiting_user",
        "outcome": "awaiting",
        "createdAt": "2026-01-02T00:00:00+00:00",
    }
    chats_path.write_text(json.dumps(chats), encoding="utf-8")
    captured = {}
    published = []

    async def capture_chat_changed(chat_id, project_id, change, **details):
        durable = json.loads(chats_path.read_text(encoding="utf-8"))["chats"][0]
        published.append({
            "chat_id": chat_id,
            "project_id": project_id,
            "change": change,
            "details": details,
            "durable_pending": durable.get("pendingQuestion"),
        })

    monkeypatch.setattr(run_answer_routes, "publish_chat_changed", capture_chat_changed)

    async def fake_answer_pending(
        session_id, question_id, answer_text, workspace_dir, **_kwargs,
    ):
        captured.update({
            "session_id": session_id,
            "question_id": question_id,
            "answer_text": answer_text,
            "workspace_dir": workspace_dir,
        })
        return "continued"

    monkeypatch.setattr(routes_mod, "_workbench_answer_pending", fake_answer_pending)

    response = client.post(
        "/api/workbench/chats/chat_1/answer",
        json={"question_id": "question_1", "answer": "continue"},
    )

    assert response.status_code == 200
    assert captured == {
        "session_id": "chat_1",
        "question_id": "question_1",
        "answer_text": "continue",
        "workspace_dir": str(override.resolve()),
    }
    payload = response.json()
    assert payload["userMessage"]["content"] == "continue"
    stored_chat = json.loads(chats_path.read_text(encoding="utf-8"))["chats"][0]
    assert payload["runId"] == stored_chat["lastRun"]["id"]
    assert payload["chatSummary"]["id"] == "chat_1"
    assert payload["chatSummary"]["runStatus"] == "completed"
    submitted = next(event for event in published if event["change"] == "answer_submitted")
    assert submitted["details"]["chatSummary"]["pendingQuestion"] is None
    assert submitted["details"]["userMessage"]["answerToQuestionId"] == "question_1"
    # Keep the durable prompt recoverable until the resumed run settles; only
    # the realtime summary should clear it optimistically.
    assert submitted["durable_pending"] == {"id": "question_1"}
    settled = next(event for event in published if event["change"] == "settled")
    assert settled["details"]["run_id"] == payload["runId"]
    assert settled["details"]["chatSummary"]["lastRun"]["id"] == payload["runId"]
    assert [message["content"] for message in stored_chat["messages"][-2:]] == ["continue", "continued"]
    assert stored_chat["messages"][-2]["answerToQuestionId"] == "question_1"
    assert "pendingQuestion" not in stored_chat
    assert stored_chat["lastRun"]["id"].startswith("run_")
    assert stored_chat["lastRun"]["outcome"] == "reply"

    listed = client.get("/api/workbench/chats?project=project_1").json()["chats"][0]
    assert listed["runStatus"] == "completed"
    assert listed["pendingQuestion"] is None


async def test_cancelled_workbench_chat_answer_consumes_question_and_records_interrupt(
    search_env, monkeypatch,
):
    from route.workbench.chat_routes.context import ChatRouteContext
    from route.workbench.chat_routes.run_answer_routes import ChatAnswerController

    chats_path = search_env["data_dir"] / "workbench_chats.json"
    chats = json.loads(chats_path.read_text(encoding="utf-8"))
    chats["chats"][0]["pendingQuestion"] = {"id": "question_cancel"}
    chats_path.write_text(json.dumps(chats), encoding="utf-8")

    async def cancelled_resume(*_args, **_kwargs):
        raise asyncio.CancelledError

    monkeypatch.setattr(
        search_env["routes_mod"],
        "_workbench_answer_pending",
        cancelled_resume,
    )
    controller = ChatAnswerController(
        ChatRouteContext.create(bot=None, db_path=search_env["db_path"])
    )
    result = await controller.answer(
        "chat_1",
        {"question_id": "question_cancel", "answer": "继续"},
    )

    assert result["interrupted"] is True
    stored = json.loads(chats_path.read_text(encoding="utf-8"))["chats"][0]
    assert result["runId"] == stored["lastRun"]["id"]
    assert "pendingQuestion" not in stored
    assert stored["lastRun"]["status"] == "cancelled"
    assert stored["lastRun"]["terminationReason"] == "user_interrupted"
    assert stored["lastRun"]["outcome"] == "interrupted"


async def test_workbench_chat_answer_stop_is_owned_and_settled_by_run_manager(
    search_env, monkeypatch,
):
    from cyrene.workbench import global_chat_service
    from cyrene.workbench.global_chat_service import GlobalChatApplicationService
    from cyrene.workbench.subagent_messaging_service import SubagentMessagingService
    from route.workbench.chat_routes.context import ChatRouteContext
    from route.workbench.chat_routes.run_answer_routes import ChatAnswerController

    chats_path = search_env["data_dir"] / "workbench_chats.json"
    chats = json.loads(chats_path.read_text(encoding="utf-8"))
    chats["chats"][0]["pendingQuestion"] = {"id": "question_stop"}
    chats_path.write_text(json.dumps(chats), encoding="utf-8")

    started = asyncio.Event()
    cancelled = asyncio.Event()

    async def blocking_resume(*_args, **_kwargs):
        started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            cancelled.set()
            raise

    monkeypatch.setattr(
        search_env["routes_mod"],
        "_workbench_answer_pending",
        blocking_resume,
    )
    agent_interrupts: list[str] = []
    monkeypatch.setattr(
        global_chat_service.agent,
        "interrupt_active_run",
        lambda session_id="": agent_interrupts.append(session_id) or False,
    )

    controller = ChatAnswerController(
        ChatRouteContext.create(bot=None, db_path=search_env["db_path"])
    )
    answer_task = asyncio.create_task(controller.answer(
        "chat_1",
        {"question_id": "question_stop", "answer": "继续"},
    ))
    await asyncio.wait_for(started.wait(), timeout=1)

    service = GlobalChatApplicationService(
        search_env["db_path"],
        bot=None,
        subagents=SubagentMessagingService(None, search_env["db_path"]),
        reset_agent_lottery=lambda: None,
    )
    stopped = await service.interrupt("chat_1")
    result = await asyncio.wait_for(answer_task, timeout=1)

    assert stopped == {"ok": True, "interrupted": True}
    assert result["interrupted"] is True
    assert cancelled.is_set()
    # A manager-owned continuation has one cancellation owner. The global
    # endpoint must not race it with a second agent-level interrupt.
    assert agent_interrupts == []
    stored = json.loads(chats_path.read_text(encoding="utf-8"))["chats"][0]
    assert stored["status"] == "idle"
    assert "pendingQuestion" not in stored
    assert stored["lastRun"]["id"] == result["runId"]
    assert stored["lastRun"]["status"] == "cancelled"
    assert stored["lastRun"]["terminationReason"] == "user_interrupted"
    assert stored["lastRun"]["outcome"] == "interrupted"


async def test_cancelled_workbench_task_answer_persists_paused_terminal_state(
    search_env, monkeypatch,
):
    from dataclasses import replace
    from route.workbench.task_session_routes.context import build_task_session_context
    from cyrene.workbench import task_runs
    from cyrene.workbench.task_execution_service import (
        TaskExecutionApplicationService,
        TaskExecutionDependencies,
    )

    store = search_env["routes_mod"]._read_workbench_store()
    session = store["projects"][0]["sessions"][0]
    session["status"] = "waiting_for_user"
    session["pendingQuestion"] = {"id": "task_question_cancel"}
    session["pendingPlanStep"] = {"stepId": "step_1", "continueAll": True}
    session["plan"] = [{
        "id": "step_1",
        "title": "继续处理",
        "status": "running",
        "startedAt": "2026-01-02T00:00:00+00:00",
    }]
    session["events"] = []
    session["runs"] = []
    search_env["routes_mod"]._write_workbench_store(store)

    async def cancelled_resume(*_args, **_kwargs):
        raise asyncio.CancelledError

    execution_dependencies = replace(
        TaskExecutionDependencies.from_runtime(search_env["routes_mod"]),
        answer_pending=cancelled_resume,
    )
    execution = TaskExecutionApplicationService(
        dependencies=execution_dependencies,
        task_runs=task_runs,
        db_path=search_env["db_path"],
    )
    context = build_task_session_context(
        search_env["db_path"], search_env["routes_mod"],
        execution_service=execution,
    )
    result = await context.run_coordination.execute(
        "answer",
        "session_1",
        {"question_id": "task_question_cancel", "answer": "继续"},
        lambda: context.execution.answer(
            "session_1", {"question_id": "task_question_cancel", "answer": "继续"}
        ),
        bypass_goal_loop_answer=True,
    )

    assert result["interrupted"] is True
    updated = search_env["routes_mod"]._read_workbench_store()
    cancelled_session = updated["projects"][0]["sessions"][0]
    assert cancelled_session["status"] == "paused"
    assert "pendingQuestion" not in cancelled_session
    assert "pendingPlanStep" not in cancelled_session
    assert cancelled_session["plan"][0]["status"] == "pending"
    assert cancelled_session["runs"][-1]["status"] == "cancelled"
    assert cancelled_session["runs"][-1]["terminationReason"] == "user_interrupted"


def test_workbench_chat_answer_can_stream_continuation_events(
    client, search_env, monkeypatch,
):
    from cyrene.agent.context import emit_reply_stream_event, publish_runtime_event
    from cyrene.workbench import runtime as routes_mod

    chats_path = search_env["data_dir"] / "workbench_chats.json"
    chats = json.loads(chats_path.read_text(encoding="utf-8"))
    chats["chats"][0]["pendingQuestion"] = {"id": "question_stream"}
    chats_path.write_text(json.dumps(chats), encoding="utf-8")

    async def fake_answer_pending(*_args, **_kwargs):
        await publish_runtime_event({
            "type": "tool_call_started",
            "tool_call_id": "tool_stream",
            "tool": "search_files",
        })
        await emit_reply_stream_event({"type": "reply_start"})
        await emit_reply_stream_event({
            "type": "reply_delta",
            "delta": "继续完成",
        })
        await emit_reply_stream_event({
            "type": "reply_done",
            "response": "继续完成",
        })
        return "继续完成"

    monkeypatch.setattr(
        routes_mod,
        "_workbench_answer_pending",
        fake_answer_pending,
    )

    response = client.post(
        "/api/workbench/chats/chat_1/answer",
        json={
            "question_id": "question_stream",
            "answer": "允许一次",
            "stream": True,
        },
    )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/x-ndjson")
    events = [
        json.loads(line)
        for line in response.text.splitlines()
        if line.strip()
    ]
    assert [event["type"] for event in events] == [
        "ack",
        "tool_call_started",
        "reply_start",
        "reply_delta",
        "reply_done",
        "workspace_changes",
        "saved",
    ]
    assert events[4]["response"] == "继续完成"
    assert events[-1]["chatSummary"]["id"] == "chat_1"
    assert events[-1]["chatSummary"]["runStatus"] == "completed"
