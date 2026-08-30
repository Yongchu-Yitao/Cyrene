"""Tests for the Agent package, kept outside the shipped source tree."""

from __future__ import annotations

import asyncio
import sqlite3
from pathlib import Path

import pytest
from fastapi import APIRouter, FastAPI
from fastapi.testclient import TestClient

from cyrene.core.plugin import PluginContext, PluginRegistry, PluginRuntime
from cyrene.plugins.builtin.cyrene_knowledge import plugin_pack
from cyrene.plugins.builtin.cyrene_knowledge.routes import register_routes
from cyrene.plugins.builtin.cyrene_knowledge.service import (
    KnowledgeService,
    WorkspaceNotFoundError,
    WorkspaceRequiredError,
    create_knowledge_service,
)
from cyrene.plugins.builtin.cyrene_knowledge.store import cosine, vectorize
from cyrene.plugins.builtin.cyrene_knowledge.zotero import sync_zotero


def _workspace(value: str) -> str:
    workspace = str(value or "").strip()
    if not workspace:
        raise WorkspaceRequiredError("workspace is required")
    if workspace not in {"project-one", "project-two"}:
        raise WorkspaceNotFoundError(f"workspace was not found: {workspace}")
    return workspace


def _service(tmp_path: Path) -> KnowledgeService:
    return KnowledgeService(
        tmp_path / "plugin-data",
        workspace_resolver=_workspace,
        zotero_settings=lambda: {
            "base_url": "http://127.0.0.1:23119/api",
            "copy_attachments": True,
        },
    )


def _client(service: KnowledgeService) -> TestClient:
    app = FastAPI()
    router = APIRouter()
    register_routes(router, service)
    app.include_router(router)
    return TestClient(app)


def test_chunk_search_uses_fts_candidates_and_blob_vectors(tmp_path: Path) -> None:
    service = _service(tmp_path)
    item = service.store.create_item(
        "project-one",
        {
            "title": "Indexed retrieval",
            "content": "bounded candidate retrieval avoids a complete Python scan",
        },
    )
    with sqlite3.connect(service.store.db_path) as connection:
        row = connection.execute(
            "SELECT vector_blob FROM chunks WHERE item_id=? LIMIT 1",
            (item["id"],),
        ).fetchone()
        assert row is not None and len(row[0]) == 128 * 4
        connection.execute(
            "UPDATE chunks SET vector_json='{invalid legacy json' WHERE item_id=?",
            (item["id"],),
        )
        assert connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='chunks_fts'"
        ).fetchone() == (1,)
        assert connection.execute(
            "SELECT COUNT(*) FROM chunk_vector_index v "
            "JOIN chunks c ON c.id=v.chunk_id WHERE c.item_id=?",
            (item["id"],),
        ).fetchone()[0] > 0

    hits = service.store.search_chunks(
        "project-one",
        "candidate retrieval",
        limit=5,
    )
    assert hits and hits[0]["item_id"] == item["id"]

    # A distinct token with the same signed hash dimension cannot match FTS;
    # finding the item proves the vector index supplied the candidate.
    source = "vectorprobe"
    collision = next(
        candidate
        for index in range(10_000)
        if (candidate := f"candidate{index}") != source
        and cosine(vectorize(source), vectorize(candidate)) > 0
    )
    vector_item = service.store.create_item(
        "project-one",
        {"title": "Vector-only candidate", "content": source},
    )
    vector_hits = service.store.search_chunks("project-one", collision, limit=5)
    assert vector_item["id"] in {hit["item_id"] for hit in vector_hits}


@pytest.mark.asyncio
async def test_configured_embedding_drives_index_status_and_semantic_search(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from cyrene.plugins.builtin.cyrene_knowledge import embeddings

    async def fake_embed_texts(texts, *, input_type="document"):
        assert input_type in {"document", "query"}
        return [[1.0, 0.0, 0.0] for _text in texts]

    monkeypatch.setattr(embeddings, "is_configured", lambda: True)
    monkeypatch.setattr(embeddings, "current_identity", lambda: ("test-embed", 3))
    monkeypatch.setattr(embeddings, "embed_texts", fake_embed_texts)

    service = _service(tmp_path)
    item = await service.create_item(
        "project-one",
        {"title": "Meaningful document", "content": "unrelated source wording"},
    )
    if service._tasks:
        await asyncio.gather(*tuple(service._tasks))

    status = await service.embedding_status("project-one")
    assert status["configured"] is True
    assert status["model"] == "test-embed"
    assert status["dimensions"] == 3
    assert status["compatible_vectors"] == status["total_chunks"]

    hits = await service.search_knowledge(
        PluginContext(data={"workspace_id": "project-one"}),
        "semantic phrase absent from the document",
        limit=5,
    )
    assert hits and hits[0]["item_id"] == item["id"]
    assert hits[0]["cosine_similarity"] == pytest.approx(1.0)
    await service.shutdown()


@pytest.mark.asyncio
async def test_completed_local_embedding_download_schedules_existing_vectors(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from cyrene.plugins.builtin.cyrene_knowledge import local_models

    service = _service(tmp_path)
    refreshed: list[str] = []
    monkeypatch.setattr(
        service,
        "_ensure_local_embedding_configuration",
        lambda: None,
    )
    monkeypatch.setattr(local_models, "start_download", lambda _model_id: {"models": []})
    monkeypatch.setattr(local_models, "is_ready", lambda _model_id: True)
    monkeypatch.delitem(
        local_models._TASKS,
        "qwen3-embedding-0.6b",
        raising=False,
    )
    monkeypatch.setattr(
        service.store,
        "embedding_workspaces",
        lambda: ["project-one", "project-two"],
    )
    monkeypatch.setattr(
        service,
        "_start_embedding_refresh",
        lambda workspace: refreshed.append(workspace) or {"running": True},
    )

    service.start_local_model_download("qwen3-embedding-0.6b")
    if service._tasks:
        await asyncio.gather(*tuple(service._tasks))

    assert refreshed == ["project-one", "project-two"]
    await service.shutdown()


@pytest.mark.asyncio
async def test_deferred_store_startup_opens_and_shutdown_closes_read_pool(
    tmp_path: Path,
) -> None:
    service = create_knowledge_service(
        tmp_path / "knowledge",
        workspace_resolver=lambda value: value or "project-one",
        zotero_settings=lambda: {},
        initialize_store=False,
    )

    await service.startup()
    item = service.store.create_item(
        "project-one",
        {"title": "Lifecycle", "content": "pooled retrieval remains available"},
    )
    hits = service.store.search_chunks("project-one", "pooled retrieval", limit=5)
    assert hits and hits[0]["item_id"] == item["id"]
    assert service.store._read_pool_open is True

    await service.shutdown()
    assert service.store._read_pool_open is False


def test_frontend_library_contract_uses_plugin_owned_backend(tmp_path: Path) -> None:
    service = _service(tmp_path)
    client = _client(service)
    workspace = {"workspace": "project-one"}

    created = client.post(
        "/api/workbench/library/items",
        params=workspace,
        json={
            "title": "Plugin-native knowledge",
            "abstract": "A complete replacement backend",
            "creators": [{"first_name": "Ada", "last_name": "Lovelace"}],
            "tags": ["plugins", "architecture"],
            "year": 2026,
        },
    )
    assert created.status_code == 200
    item = created.json()
    item_id = item["id"]
    assert item["title"] == "Plugin-native knowledge"
    assert item["creators"][0]["last_name"] == "Lovelace"

    collection = client.post(
        "/api/workbench/library/collections",
        params=workspace,
        json={"name": "Architecture"},
    ).json()
    patched = client.patch(
        f"/api/workbench/library/items/{item_id}",
        params=workspace,
        json={
            "starred": True,
            "reading_status": "reading",
            "collection_ids": [collection["id"]],
            "tags": ["plugins", "sqlite"],
        },
    )
    assert patched.status_code == 200
    assert patched.json()["starred"] is True
    assert patched.json()["collections"] == [{"id": collection["id"], "name": "Architecture", "color": ""}]

    listed = client.get(
        "/api/workbench/library/items",
        params={**workspace, "collection": collection["id"], "starred": "true"},
    ).json()
    assert listed["total"] == 1
    assert listed["items"][0]["id"] == item_id
    assert client.get("/api/workbench/library/collections", params=workspace).json()["collections"][0]["count"] == 1
    assert {value["tag"] for value in client.get("/api/workbench/library/tags", params=workspace).json()["tags"]} == {"plugins", "sqlite"}

    note = client.post(
        f"/api/workbench/library/items/{item_id}/notes",
        params=workspace,
        json={"title": "Review", "content": "Use the plugin database only."},
    ).json()
    updated_note = client.patch(
        f"/api/workbench/library/notes/{note['id']}",
        params=workspace,
        json={"content": "No legacy knowledge backend."},
    ).json()
    assert updated_note["content"] == "No legacy knowledge backend."

    other = client.post(
        "/api/workbench/library/items",
        params=workspace,
        json={"title": "Related design"},
    ).json()
    relation = client.post(
        "/api/workbench/library/relations",
        params=workspace,
        json={"src_item_id": item_id, "dst_item_id": other["id"]},
    ).json()
    detail = client.get(f"/api/workbench/library/items/{item_id}", params=workspace).json()
    assert detail["notes"][0]["content"] == "No legacy knowledge backend."
    assert detail["relations"][0]["id"] == relation["id"]

    citation = client.get(
        f"/api/workbench/library/items/{item_id}/citation",
        params={**workspace, "style": "apa"},
    ).json()
    assert "Plugin-native knowledge" in citation["citation"]
    assert citation["bibtex"].startswith("@")

    uploaded = client.post(
        "/api/workbench/library/upload",
        params=workspace,
        files={
            "files": (
                "design-notes.txt",
                b"Plugin-native retrieval evidence lives here.",
                "text/plain",
            )
        },
    )
    assert uploaded.status_code == 200
    uploaded_item = uploaded.json()["items"][0]
    assert uploaded_item["attachment_count"] == 1
    raw = client.get(uploaded_item["attachments"][0]["raw_url"])
    assert raw.status_code == 200
    assert raw.content == b"Plugin-native retrieval evidence lives here."

    search = client.get(
        "/api/workbench/library/search",
        params={**workspace, "q": "retrieval evidence", "k": 10},
    ).json()
    assert search["results"][0]["item"]["id"] == uploaded_item["id"]
    status = client.get("/api/workbench/library/embedding/status", params=workspace).json()
    assert status["configured"] is False
    assert status["compatible_vectors"] == status["total_chunks"]

    marked = client.post(
        "/api/workbench/library/read",
        params=workspace,
        json={"attachment_url": (f"/api/workbench/library/items/{uploaded_item['id']}/raw")},
    ).json()
    assert marked["item"]["reading_status"] == "read"

    deleted = client.delete(f"/api/workbench/library/items/{item_id}", params=workspace).json()
    assert deleted == {"ok": True}
    assert client.get("/api/workbench/library/items", params={**workspace, "trash": True}).json()["items"][0]["id"] == item_id
    assert client.post(f"/api/workbench/library/items/{item_id}/restore", params=workspace).status_code == 200
    assert (
        client.post(
            "/api/workbench/library/items/batch-delete",
            params=workspace,
            json={"item_ids": [other["id"]], "permanent": True},
        ).json()["deleted"]
        == 1
    )

    assert client.get("/api/workbench/library/items", params={"workspace": "missing"}).status_code == 404
    assert service.store.db_path == tmp_path / "plugin-data" / "knowledge.sqlite3"
    assert service.store.db_path.is_file()


def test_toolbox_list_describe_invoke_uses_the_same_plugin_service(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    item = asyncio.run(
        service.create_item(
            "project-one",
            {
                "title": "Tool-visible item",
                "content": "toolbox invocation evidence",
            },
        )
    )
    registry = PluginRegistry()
    registry.register_pack(plugin_pack, source="test")
    runtime = PluginRuntime(registry)
    context = PluginContext(
        data={"project_id": "project-one"},
        services={"knowledge": service},
    )

    async def scenario() -> None:
        listing = await runtime.call("toolbox", {"operation": "list"}, context)
        assert listing.success is True
        assert "cyrene_knowledge" in listing.value["packs"]

        described = await runtime.call(
            "toolbox",
            {"operation": "describe", "name": "cyrene_knowledge"},
            context,
        )
        assert described.success is True
        search_description = next(item for item in described.value["plugins"] if item["name"] == "SearchKnowledge")
        assert search_description["input_schema"]["required"] == ["query"]

        invoked = await runtime.call(
            "toolbox",
            {
                "operation": "invoke",
                "name": "SearchKnowledge",
                "arguments": {"query": "invocation evidence", "k": 4},
            },
            context,
        )
        assert invoked.success is True
        assert "toolbox invocation evidence" in invoked.value["result"]
        assert item["title"] == "Tool-visible item"

    asyncio.run(scenario())


def test_zotero_sync_is_order_independent_and_idempotent(tmp_path: Path, monkeypatch) -> None:
    service = _service(tmp_path)
    attachment_file = tmp_path / "zotero-paper.txt"
    attachment_file.write_text("Zotero attachment evidence", encoding="utf-8")

    collections = [
        {
            "key": "COLLECTION",
            "version": 2,
            "data": {"name": "Imported papers"},
        }
    ]
    records = [
        {
            "key": "ANNOTATION",
            "version": 3,
            "data": {
                "itemType": "annotation",
                "parentItem": "ATTACHMENT",
                "annotationType": "highlight",
                "annotationText": "Important evidence",
                "annotationPageLabel": "1",
            },
        },
        {
            "key": "NOTE",
            "version": 3,
            "data": {
                "itemType": "note",
                "parentItem": "PAPER",
                "note": "Imported note",
            },
        },
        {
            "key": "ATTACHMENT",
            "version": 3,
            "data": {
                "itemType": "attachment",
                "parentItem": "PAPER",
                "filename": attachment_file.name,
                "path": str(attachment_file),
                "contentType": "text/plain",
            },
        },
        {
            "key": "PAPER",
            "version": 3,
            "data": {
                "itemType": "journalArticle",
                "title": "Zotero paper",
                "collections": ["COLLECTION"],
                "creators": [{"firstName": "Grace", "lastName": "Hopper"}],
                "tags": [{"tag": "sync"}],
                "date": "2026",
            },
        },
    ]

    async def fake_collections(_self, library_id: str, library_type: str):
        assert (library_id, library_type) == ("0", "user")
        return collections, 2

    async def fake_items(_self, library_id: str, library_type: str, collection_key: str):
        assert (library_id, library_type, collection_key) == ("0", "user", "")
        return records, 3

    monkeypatch.setattr(
        "cyrene.plugins.builtin.cyrene_knowledge.zotero.ZoteroClient.collections",
        fake_collections,
    )
    monkeypatch.setattr(
        "cyrene.plugins.builtin.cyrene_knowledge.zotero.ZoteroClient.items",
        fake_items,
    )

    async def scenario() -> None:
        first = await sync_zotero(
            service,
            "project-one",
            base_url="http://127.0.0.1:23119/api",
            library_id="0",
            library_type="user",
            collection_key="",
            copy_attachments=True,
        )
        second = await sync_zotero(
            service,
            "project-one",
            base_url="http://127.0.0.1:23119/api",
            library_id="0",
            library_type="user",
            collection_key="",
            copy_attachments=True,
        )
        assert first["created"] == 1
        assert second["created"] == 0
        assert second["updated"] == 1
        assert second["skipped"] == 0

    asyncio.run(scenario())
    items = service.store.list_items("project-one")["items"]
    assert len(items) == 1
    detail = service.store.get_item("project-one", items[0]["id"])
    assert detail is not None
    assert len(detail["attachments"]) == 1
    assert len(detail["notes"]) == 1
    assert len(detail["annotations"]) == 1
    assert detail["annotations"][0]["attachment_id"] == detail["attachments"][0]["id"]
    assert detail["collections"][0]["name"] == "Imported papers"
