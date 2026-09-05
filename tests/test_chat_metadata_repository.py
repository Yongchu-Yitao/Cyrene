from __future__ import annotations

import copy
import sqlite3
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import APIRouter, FastAPI
from fastapi.testclient import TestClient

from cyrene.workbench.chat.chat_repository import ChatRepository
from cyrene.workbench.persistence import chat_repository as persistence


@pytest.fixture
def repository(tmp_path):
    repo = ChatRepository(str(tmp_path / "chats.db"))
    repo.write({"chats": [{
        "id": "chat", "projectId": "project", "title": "Original",
        "titleNamingStatus": "pending", "soulActive": False, "workspaceActive": False,
        "messages": [{"id": f"m{i}", "role": "user", "content": "x" * 2048} for i in range(1000)],
    }]})
    return repo


@pytest.fixture
def forbid_transcript_sql(monkeypatch):
    """Fail on any transcript/shell access, including reads hidden by caching."""
    connect = persistence._connect
    statements = []

    def guarded_connect(path):
        conn = connect(path)

        def authorize(action, table, column, database, trigger):
            if table in {"workbench_chat_messages", "workbench_state"}:
                return sqlite3.SQLITE_DENY
            if action == sqlite3.SQLITE_UPDATE and table == "workbench_chats":
                if column not in {"payload_json", "updated_at"}:
                    return sqlite3.SQLITE_DENY
            return sqlite3.SQLITE_OK

        conn.set_authorizer(authorize)
        conn.set_trace_callback(statements.append)
        return conn

    monkeypatch.setattr(persistence, "_connect", guarded_connect)
    return statements


def test_metadata_update_never_accesses_transcript_or_rewrites_summary(repository, forbid_transcript_sql):
    before = repository.get_metadata("chat")
    edited = copy.deepcopy(before)
    edited["title"] = "Renamed"
    saved = repository.write_metadata(edited, base_metadata=before)
    assert saved["title"] == "Renamed"
    assert saved["_messageProjection"] == before["_messageProjection"]
    assert saved["_messageProjection"]["messageCount"] == 1000
    assert "messages" not in saved
    assert len([sql for sql in forbid_transcript_sql if sql.startswith("UPDATE")]) == 1


def test_metadata_merge_preserves_concurrent_message_and_metadata_commits(repository):
    stale_bundle = repository.read()
    before = repository.get_metadata("chat")
    edited = copy.deepcopy(before)
    edited["title"] = "Manual title"
    edited["titleLocked"] = True

    def append(chat):
        chat["messages"].append({"id": "new", "role": "assistant", "content": "reply"})
        chat["model"] = "new-model"

    repository.mutate_one("chat", append)
    saved = repository.write_metadata(edited, base_metadata=before)
    assert saved["model"] == "new-model"
    assert saved["_messageProjection"]["messageCount"] == 1001
    # Legacy bundle writers must observe the metadata version change too.
    stale_bundle["chats"][0]["messages"].append({"id": "stale", "role": "user", "content": "another"})
    repository.write(stale_bundle)
    chat = repository.get("chat")
    assert chat["title"] == "Manual title"
    assert chat["titleLocked"] is True
    assert chat["model"] == "new-model"
    assert {item["id"] for item in chat["messages"]} >= {"new", "stale"}


@pytest.mark.parametrize("invalid", [{"messages": []}, {"_messageProjection": {}}, {"id": "other"}])
def test_metadata_boundary_rejects_transcript_and_identity_changes(repository, invalid):
    before = repository.get_metadata("chat")
    with pytest.raises(ValueError):
        repository.mutate_metadata("chat", lambda chat: chat.update(invalid))
    assert repository.get_metadata("chat") == before


def test_missing_and_cancelled_metadata_mutations_do_not_write(repository, forbid_transcript_sql):
    assert repository.get_metadata("missing") is None
    assert repository.mutate_metadata("missing", lambda chat: chat.update(title="new")) is None
    before = repository.get_metadata("chat")

    def cancel(chat):
        chat["title"] = "discard"
        return False

    assert repository.mutate_metadata("chat", cancel) == before
    assert not any(sql.startswith("UPDATE") for sql in forbid_transcript_sql)


def _service(repository):
    from cyrene.workbench.chat.chat_application import public_chat_light

    composer = SimpleNamespace(normalize=lambda value: value or {})
    return SimpleNamespace(
        repository=repository,
        utc_now_iso=lambda: "2026-09-05T00:00:00+00:00",
        public_chat_light=lambda chat: public_chat_light(chat, composer_context=composer),
    )


def test_http_patch_returns_summary_without_loading_history(repository, forbid_transcript_sql, monkeypatch):
    from cyrene.workbench.http.workbench.chat_routes import detail_routes

    changed = AsyncMock()
    monkeypatch.setattr(detail_routes, "publish_chat_changed", changed)
    context = SimpleNamespace(service=_service(repository), runtime=lambda: SimpleNamespace())
    router = APIRouter()
    detail_routes._register_update_route(router, context)
    app = FastAPI()
    app.include_router(router)
    with TestClient(app) as client:
        response = client.patch("/api/workbench/chats/chat", json={"title": "  Renamed  ", "reasoningEffort": "high"})
        assert response.status_code == 200
        chat = response.json()["chat"]
        assert chat["title"] == "Renamed"
        assert chat["reasoningEffort"] == "high"
        assert chat["messageCount"] == 1000
        assert "messages" not in chat and "files" not in chat
        assert client.patch("/api/workbench/chats/missing", json={"title": "new"}).status_code == 404
    assert repository.get_metadata("chat")["titleLocked"] is True
    changed.assert_awaited_once_with("chat", "project", "updated")


def test_agent_binding_remains_locked_with_metadata_only_reads(repository, monkeypatch):
    from cyrene.workbench.http.workbench.chat_routes import detail_routes

    # Even legacy rows without a populated summary cannot bypass the check.
    conn = persistence._connect(repository._database())
    conn.execute("UPDATE workbench_chats SET summary_json = '{}'")
    conn.commit()
    conn.close()
    context = SimpleNamespace(service=_service(repository), runtime=lambda: SimpleNamespace(get_model=lambda: ""))
    router = APIRouter()
    detail_routes._register_update_route(router, context)
    app = FastAPI()
    app.include_router(router)
    with TestClient(app) as client:
        response = client.patch("/api/workbench/chats/chat", json={"agent": {}})
    assert response.status_code == 409


def test_application_and_automatic_rename_use_metadata_boundary(repository, forbid_transcript_sql, monkeypatch):
    from cyrene.workbench.application import app_services
    from cyrene.workbench.chat.chat_session_naming_service import ChatSessionNamingApplicationService, ChatSessionNamingDependencies

    monkeypatch.setattr(app_services, "_chat_repository", lambda: repository)
    namer = ChatSessionNamingApplicationService(ChatSessionNamingDependencies(
        mutate_metadata=repository.mutate_metadata, utc_now_iso=lambda: "now",
    ))
    assert namer._persist("chat", "Generated title") is True
    renamed = app_services.rename_chat("chat", "Manual title")
    assert renamed["title"] == "Manual title"
    assert "messages" not in renamed
    repository.mutate_metadata("chat", lambda chat: chat.update(titleNamingStatus="pending"))
    assert namer._persist("chat", "Late generated title") is False
    assert repository.get_metadata("chat")["title"] == "Manual title"


@pytest.mark.asyncio
async def test_control_update_returns_metadata(repository, forbid_transcript_sql, monkeypatch):
    from cyrene.workbench.control import control_ports

    monkeypatch.setattr(control_ports, "publish_chat_changed", AsyncMock())
    # Only update's service dependency is relevant to this command.
    port = object.__new__(control_ports.WorkbenchChatApplicationPort)
    port.service = _service(repository)
    result = await port.update("chat", {"title": "Remote title"})
    assert result["chat"]["title"] == "Remote title"
    assert result["chat"]["messageCount"] == 1000
    assert "messages" not in result["chat"]
