import json
import sqlite3
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from fastapi import APIRouter, FastAPI
from fastapi.testclient import TestClient

from cyrene.workbench.chat.chat_repository import ChatRepository
from cyrene.workbench.chat.chat_sync import ContextCommitVersions
from cyrene.workbench.http.workbench.chat_routes.detail_routes import _register_get_route


@pytest.fixture
def sync_app(tmp_path):
    repo = ChatRepository(str(tmp_path / "app.db"))
    repo.insert({"id": "a", "status": "idle", "generatedFiles": [],
                 "messages": [{"id": "m", "role": "assistant", "content": "before"}]})
    live = {"status": "idle", "setting": False}
    on_load = []

    def full(chat):
        for callback in on_load[:]:
            on_load.remove(callback)
            callback()
        return {**deepcopy(chat), "runStatus": live["status"]}

    repo.get = Mock(wraps=repo.get)
    service = SimpleNamespace(
        repository=repo, public_chat_light=lambda _: dict(live),
        public_chat_full=Mock(side_effect=full), prune_orphaned_fork_metadata=lambda _: False,
        prewarm_workspace_changes=Mock(), resolve_chat_workspace_dir=Mock(),
        sync_chat_generated_files=Mock(),
    )
    directory = tmp_path / "context"
    directory.mkdir()
    context = SimpleNamespace(
        service=service,
        runtime=lambda: SimpleNamespace(read_store=lambda: {}, find_project=lambda *_: None),
        conversation_context=SimpleNamespace(
            agent_states=SimpleNamespace(context_directory=directory),
            activity_messages=AsyncMock(return_value=[]),
        ),
    )
    router = APIRouter()
    _register_get_route(router, context)
    app = FastAPI()
    app.include_router(router)
    with TestClient(app) as client:
        def read(revision=""):
            response = client.get("/api/workbench/chats/a", params={"sync_revision": revision})
            assert response.status_code == 200
            return response.json()
        yield SimpleNamespace(repo=repo, service=service, context=context, live=live,
                              read=read, client=client, on_load=on_load, directory=directory)


def test_idle_polls_skip_history_projection_and_return_small_response(sync_app):
    f = sync_app
    first = f.read()
    assert first["chat"]["messages"][0]["content"] == "before"
    f.repo.get.reset_mock()
    f.service.public_chat_full.reset_mock()
    f.context.conversation_context.activity_messages.reset_mock()
    for _ in range(10):
        unchanged = f.read(first["revision"])
        assert unchanged == {"unchanged": True, "revision": first["revision"]}
        assert len(json.dumps(unchanged)) < 120
    f.repo.get.assert_not_called()
    f.service.public_chat_full.assert_not_called()
    f.context.conversation_context.activity_messages.assert_not_awaited()
    # Original clients still receive the original response shape.
    assert set(f.client.get("/api/workbench/chats/a").json()) == {"chat"}


def test_same_length_message_edits_questions_and_live_status_invalidate(sync_app):
    f = sync_app
    first = f.read()
    f.repo.mutate_one("a", lambda c: c["messages"][0].update(content="after!"))
    second = f.read(first["revision"])
    assert second["chat"]["messages"][0]["content"] == "after!"
    assert second["revision"] != first["revision"]
    f.repo.mutate_metadata("a", lambda c: c.update(pendingQuestion={"id": "q", "text": "Approve?"}))
    third = f.read(second["revision"])
    assert third["chat"]["pendingQuestion"]["id"] == "q"
    f.live["status"] = "running"
    fourth = f.read(third["revision"])
    assert fourth["chat"]["runStatus"] == "running"
    f.live["setting"] = True
    assert "chat" in f.read(fourth["revision"])


def test_changes_during_hydration_are_not_acknowledged_early(sync_app):
    f = sync_app
    f.on_load.append(lambda: f.repo.mutate_one("a", lambda c: c["messages"][0].update(content="new")))
    first = f.read()
    assert first["chat"]["messages"][0]["content"] == "before"
    second = f.read(first["revision"])
    assert second["chat"]["messages"][0]["content"] == "new"
    assert f.read(second["revision"])["unchanged"] is True


def test_context_creation_and_wal_commit_invalidate_without_chat_write(sync_app):
    f = sync_app
    absent = f.read()
    with sqlite3.connect(f.directory / "index.sqlite3") as index:
        index.execute("CREATE TABLE context_tree_index(tree_id TEXT, database_path TEXT)")
        index.execute("INSERT INTO context_tree_index VALUES ('a', 'tree.sqlite3')")
    writer = sqlite3.connect(f.directory / "tree.sqlite3")
    try:
        writer.execute("PRAGMA journal_mode=WAL")
        writer.execute("CREATE TABLE activity(value TEXT)")
        writer.commit()
        first = f.read(absent["revision"])
        assert "chat" in first
        assert f.read(first["revision"])["unchanged"] is True
        writer.execute("INSERT INTO activity VALUES ('new tool result')")
        writer.commit()
        assert "chat" in f.read(first["revision"])
    finally:
        writer.close()


def test_failed_revision_and_activity_reads_remain_retryable(sync_app, monkeypatch):
    f = sync_app
    f.context.conversation_context.activity_messages.side_effect = RuntimeError("temporary projection error")
    first = f.read()
    assert first["revision"] == ""
    f.context.conversation_context.activity_messages.side_effect = None
    second = f.read(first["revision"])
    assert second["revision"]
    monkeypatch.setattr(f.repo, "sync_state", Mock(side_effect=RuntimeError("temporary probe error")))
    third = f.read(second["revision"])
    assert third["revision"] == ""
    assert third["chat"] == second["chat"]


def test_deleted_chat_is_not_reported_unchanged(sync_app):
    f = sync_app
    first = f.read()
    f.repo.write({"chats": []})
    response = f.client.get("/api/workbench/chats/a", params={"sync_revision": first["revision"]})
    assert response.status_code == 404


def test_context_tokens_observe_commits_not_uncommitted_writes_and_survive_eviction(tmp_path):
    path = tmp_path / "tree.db"
    writer = sqlite3.connect(path)
    writer.execute("PRAGMA journal_mode=WAL")
    writer.execute("CREATE TABLE data(value TEXT)")
    writer.commit()
    versions = ContextCommitVersions(max_entries=1)
    try:
        before = versions.read(path)
        writer.execute("INSERT INTO data VALUES ('uncommitted')")
        assert versions.read(path) == before
        writer.commit()
        committed = versions.read(path)
        assert committed != before
        assert versions.read(path) == committed
        # The observer does not pin a read transaction or prevent checkpointing.
        assert writer.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()[0] == 0
        other = tmp_path / "other.db"
        sqlite3.connect(other).close()
        versions.read(other)
        assert versions.read(path) != committed
    finally:
        versions.close()
        writer.close()


def test_status_endpoint_returns_live_state_without_history_or_prewarm(sync_app):
    f = sync_app
    f.live.update(status='running', runStatus='awaiting_user', pendingQuestion={'id': 'q'})
    response = f.client.get('/api/workbench/chats/a/status')
    assert response.status_code == 200
    assert response.json()['chat'] == {'id': None, 'status': 'running', 'runStatus': 'awaiting_user', 'pendingQuestion': {'id': 'q'}}
    f.repo.get.assert_not_called()
    f.service.public_chat_full.assert_not_called()
    f.service.prewarm_workspace_changes.assert_not_called()
    f.context.conversation_context.activity_messages.assert_not_awaited()
    assert f.client.get('/api/workbench/chats/missing/status').status_code == 404
