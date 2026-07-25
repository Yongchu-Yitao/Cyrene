"""Regression tests for Workbench knowledge workspace isolation.

The knowledge page sends a project's ``dataKey`` (or id) as the ``workspace``
query param. Knowledge is stored under the project **id** key — the same key
project memory uses. For the legacy default project these differ (dataKey ==
"default", id == "project_…"). Previously the knowledge resolver returned the
``dataKey``, so the default project read ``kb_default.db`` — the shared legacy
catalog that the startup sync fills with the entire global attachment domain
(every project's uploads/exports). That made the default project's knowledge
view surface files from every other project.

These tests lock in that the default project now resolves to its id-scoped
knowledge db (decoupled from the global "default" catalog), that the agent write
path agrees, and that the one-time migration only lifts the default project's own
docs out of the shared db.
"""

import json

import pytest

from cyrene import workbench_runtime as R
from cyrene import workbench_knowledge_service as kb


@pytest.fixture
def default_project_store(monkeypatch):
    """Single legacy default project whose dataKey ("default") != id."""
    payload = {
        "projects": [
            {"id": "project_abc123", "dataKey": "default", "name": "Cyrene"}
        ]
    }
    monkeypatch.setattr(R, "_read_workbench_store", lambda: payload)
    return payload


# ── resolver: read path (Workbench knowledge page) ──────────────────────────

def test_resolve_default_data_key_decouples_to_project_id_key(default_project_store):
    # Frontend sends dataKey "default"; it must resolve to the project's id-based
    # knowledge key, NOT the shared global "default" catalog db.
    assert kb._resolve_workspace_id("default") == "project_abc123"


def test_resolve_default_project_id_maps_to_same_key(default_project_store):
    # Passing the id directly resolves to the same id-based key.
    assert kb._resolve_workspace_id("project_abc123") == "project_abc123"


def test_resolve_non_default_project_is_unchanged(monkeypatch):
    # Non-default projects have dataKey == safe(id), so the key is the id either
    # way — this change is a no-op for them.
    payload = {"projects": [{"id": "project_x", "dataKey": "project_x", "name": "X"}]}
    monkeypatch.setattr(R, "_read_workbench_store", lambda: payload)
    assert kb._resolve_workspace_id("project_x") == "project_x"


def test_resolve_unknown_workspace_falls_back_to_sanitized_id(default_project_store):
    assert kb._resolve_workspace_id("project_other") == "project_other"


@pytest.mark.asyncio
async def test_ensure_kb_db_uses_id_scoped_file_for_default_project(
    tmp_path, monkeypatch, default_project_store
):
    # End-to-end on the read side: the default project's kb file is kb_<id>.db,
    # not the global kb_default.db.
    from cyrene import config

    store_dir = tmp_path / "store"
    store_dir.mkdir()
    monkeypatch.setattr(config, "STORE_DIR", store_dir)
    db_path = await kb._ensure_kb_db("default")
    assert db_path.endswith("kb_project_abc123.db")
    assert not db_path.endswith("kb_default.db")


# ── resolver: agent write path (session -> knowledge db) ────────────────────

def _point_stores(monkeypatch, tmp_path, projects, chats=None):
    from cyrene import workbench_context as wc

    projects_path = tmp_path / "workbench_projects.json"
    projects_path.write_text(json.dumps({"projects": projects}), encoding="utf-8")
    chats_path = tmp_path / "workbench_chats.json"
    chats_path.write_text(json.dumps({"chats": chats or []}), encoding="utf-8")
    monkeypatch.setattr(wc, "_WORKBENCH_DB_PATH", "")
    monkeypatch.setattr(wc, "_WORKBENCH_STORE", projects_path)
    monkeypatch.setattr(wc, "_WORKBENCH_CHATS_STORE", chats_path)


def test_session_knowledge_key_decouples_default_project(monkeypatch, tmp_path):
    from cyrene import workbench_context as wc

    _point_stores(
        monkeypatch,
        tmp_path,
        projects=[{
            "id": "project_abc123",
            "dataKey": "default",
            "sessions": [{"id": "session-1"}],
        }],
    )
    # A default-project session writes knowledge under the id key, not "default".
    assert wc.resolve_project_knowledge_key_for_session("session-1") == "project_abc123"
    # Its dataKey resolver still returns "default" (entities/schedule rely on it).
    assert wc.resolve_project_data_key_for_session("session-1") == "default"


def test_session_knowledge_key_falls_back_to_default_when_unattached(monkeypatch, tmp_path):
    from cyrene import workbench_context as wc

    _point_stores(monkeypatch, tmp_path, projects=[])
    # Legacy --agent / unattached sessions keep using the global kb_default.db.
    assert wc.resolve_project_knowledge_key_for_session("ghost-session") == "default"


def test_workbench_session_kind_distinguishes_chat_from_task(monkeypatch, tmp_path):
    from cyrene import workbench_context as wc

    _point_stores(
        monkeypatch,
        tmp_path,
        projects=[{
            "id": "project_abc123",
            "dataKey": "default",
            "sessions": [{"id": "task-1", "kind": "task"}, {"id": "init-1", "kind": "init"}],
        }],
        chats=[{"id": "chat-1", "projectId": "project_abc123"}],
    )

    assert wc.resolve_workbench_session_kind("chat-1") == "chat"
    assert wc.resolve_workbench_session_kind("task-1") == "task"
    assert wc.resolve_workbench_session_kind("init-1") == "init"
    assert wc.resolve_workbench_session_kind("missing") is None


# ── migration: lift only the default project's own docs ─────────────────────

@pytest.mark.asyncio
async def test_migration_lifts_only_attributable_docs(tmp_path, monkeypatch):
    from cyrene import config
    from cyrene.db import init_knowledge_db
    from cyrene.knowledge import store, workbench

    monkeypatch.setattr(config, "STORE_DIR", tmp_path / "store")
    (tmp_path / "store").mkdir()
    _point_stores(
        monkeypatch,
        tmp_path,
        projects=[{
            "id": "project_abc123",
            "dataKey": "default",
            "sessions": [{"id": "session-1"}],
        }],
    )

    source_db = str(config.get_knowledge_db_path("default"))
    await init_knowledge_db(source_db)

    def _make(name, body):
        f = tmp_path / name
        f.write_text(body, encoding="utf-8")
        return f

    archive = _make("archive.md", "default project task archive")
    own_generated = _make("own.md", "produced by default project session")
    catalog_generated = _make("global.md", "global export from some other project")
    catalog_upload = _make("upload.md", "global chat upload")

    await store.upsert_document_by_path(
        source_db, path=str(archive), source="workbench_task",
        content_hash=store.content_hash_file(archive),
    )
    await store.upsert_document_by_path(
        source_db, path=str(own_generated), source="generated",
        metadata={"session_id": "session-1"},
        content_hash=store.content_hash_file(own_generated),
    )
    await store.upsert_document_by_path(
        source_db, path=str(catalog_generated), source="generated",
        content_hash=store.content_hash_file(catalog_generated),
    )
    await store.upsert_document_by_path(
        source_db, path=str(catalog_upload), source="chat_upload",
        content_hash=store.content_hash_file(catalog_upload),
    )

    result = await workbench.migrate_default_project_knowledge()
    assert result["migrated"] == 2
    assert result["target"] == "project_abc123"

    target_db = str(config.get_knowledge_db_path("project_abc123"))
    moved = {doc["path"] for doc in await store.list_documents(target_db, limit=0)}
    assert str(archive.resolve()) in moved
    assert str(own_generated.resolve()) in moved
    # Global catalog docs (no session linkage) stay behind in kb_default.db.
    assert str(catalog_generated.resolve()) not in moved
    assert str(catalog_upload.resolve()) not in moved

    # The shared legacy db is left intact (non-destructive) for the --agent UI.
    assert len(await store.list_documents(source_db, limit=0)) == 4


@pytest.mark.asyncio
async def test_migration_is_idempotent(tmp_path, monkeypatch):
    from cyrene import config
    from cyrene.db import init_knowledge_db
    from cyrene.knowledge import store, workbench

    monkeypatch.setattr(config, "STORE_DIR", tmp_path / "store")
    (tmp_path / "store").mkdir()
    _point_stores(
        monkeypatch,
        tmp_path,
        projects=[{"id": "project_abc123", "dataKey": "default", "sessions": []}],
    )

    source_db = str(config.get_knowledge_db_path("default"))
    await init_knowledge_db(source_db)
    archive = tmp_path / "archive.md"
    archive.write_text("task archive", encoding="utf-8")
    await store.upsert_document_by_path(
        source_db, path=str(archive), source="workbench_task",
        content_hash=store.content_hash_file(archive),
    )

    first = await workbench.migrate_default_project_knowledge()
    second = await workbench.migrate_default_project_knowledge()
    assert first["migrated"] == 1
    assert second["migrated"] == 0  # already present -> skipped

    target_db = str(config.get_knowledge_db_path("project_abc123"))
    assert len(await store.list_documents(target_db, limit=0)) == 1
