"""Tests for the Workbench global search endpoint and helpers."""

import asyncio
import json
import sys
import time
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import MagicMock

import httpx
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

    async def fake_clear_session_id(session_id=""):
        return None

    monkeypatch.setattr(agent, "clear_session_id", fake_clear_session_id)
    monkeypatch.setattr(agent, "interrupt_active_run", lambda session_id="": False)

    response = client.delete("/api/workbench/chats/chat_1")

    assert response.status_code == 200
    payload = json.loads(
        (search_env["data_dir"] / "workbench_chats.json").read_text(encoding="utf-8")
    )
    assert payload["chats"] == []


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
    from webui import routes_workbench_chat as chat_mod

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
    from cyrene import io_utils

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
    from webui import routes as routes_mod

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

    captured = {}

    async def fake_run_agent(**kwargs):
        captured.update(kwargs)
        return "done"

    monkeypatch.setattr(agent, "run_agent", fake_run_agent)

    response = client.post(
        "/api/workbench/chats/chat_1/messages",
        json={"message": "inspect the project"},
    )

    assert response.status_code == 200
    assert response.json()["assistantMessage"]["content"] == "done"
    assert captured["workspace_dir"] == str(
        (search_env["data_dir"].parent / "workspace").resolve()
    )


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
    from webui import routes_workbench_chat as chat_mod

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


def test_workspace_scope_block_uses_runtime_workspace(tmp_path):
    from cyrene.agent.prompts import workspace_scope_block

    project_workspace = tmp_path / "project"
    block = workspace_scope_block(project_workspace)

    assert f"Your workspace is at `{project_workspace}`." in block
    assert "already starts with CWD set to the workspace root" in block
    assert f"do not prepend `cd {project_workspace}`" in block


def test_workbench_chat_answer_resumes_in_project_workspace(
    client, search_env, monkeypatch,
):
    from webui import routes as routes_mod

    chats_path = search_env["data_dir"] / "workbench_chats.json"
    chats = json.loads(chats_path.read_text(encoding="utf-8"))
    chats["chats"][0]["pendingQuestion"] = {"id": "question_1"}
    chats_path.write_text(json.dumps(chats), encoding="utf-8")
    captured = {}

    async def fake_answer_pending(session_id, question_id, answer_text, workspace_dir):
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
        "workspace_dir": str(
            (search_env["data_dir"].parent / "workspace").resolve()
        ),
    }
    payload = response.json()
    assert payload["userMessage"]["content"] == "continue"
    stored = json.loads(chats_path.read_text(encoding="utf-8"))["chats"][0]["messages"]
    assert [message["content"] for message in stored[-2:]] == ["continue", "continued"]
    assert stored[-2]["answerToQuestionId"] == "question_1"
