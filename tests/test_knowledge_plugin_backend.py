"""Tests for the Agent package, kept outside the shipped source tree."""

from __future__ import annotations

import asyncio
from pathlib import Path

from fastapi import APIRouter, FastAPI
from fastapi.testclient import TestClient

from agent.plugin import PluginContext, PluginRegistry, PluginRuntime
from agent.plugin.plugin_impl.cyrene_knowledge import plugin_pack
from agent.plugin.plugin_impl.cyrene_knowledge.routes import register_routes
from agent.plugin.plugin_impl.cyrene_knowledge.service import (
    KnowledgeService,
    WorkspaceNotFoundError,
    WorkspaceRequiredError,
)
from agent.plugin.plugin_impl.cyrene_knowledge.zotero import sync_zotero


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
    assert status["configured"] is True
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
        "agent.plugin.plugin_impl.cyrene_knowledge.zotero.ZoteroClient.collections",
        fake_collections,
    )
    monkeypatch.setattr(
        "agent.plugin.plugin_impl.cyrene_knowledge.zotero.ZoteroClient.items",
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


def test_completed_workbench_artifacts_archive_into_plugin_storage(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    deliverable = workspace_root / "report.md"
    deliverable.write_text("# Final report\n\nPlugin-owned archive.", encoding="utf-8")
    project = {"id": "project-one", "context": {}}
    session = {
        "id": "session-one",
        "status": "completed",
        "goal": "Produce a report",
        "artifacts": [
            {
                "type": "file_change",
                "path": "report.md",
                "status": "produced",
            }
        ],
    }
    run = {"id": "run-one", "agentResponse": "Done"}

    async def scenario() -> None:
        first = await service.archive_run(project, session, run, workspace_root, "2026-08-26T00:00:00Z")
        second = await service.archive_run(project, session, run, workspace_root, "2026-08-26T00:00:01Z")
        assert len(first) == len(second) == 1
        assert first[0]["id"] == second[0]["id"]

    asyncio.run(scenario())
    listed = service.store.list_items("project-one")["items"]
    assert len(listed) == 1
    assert listed[0]["provider"] == "workbench_artifact"
    assert listed[0]["tags"] == ["artifact", "workbench"]
    assert len(listed[0]["attachments"]) == 1
    assert run["knowledgeDocumentIds"] == [listed[0]["id"]]
    assert project["context"]["knowledgeDocumentIds"] == [listed[0]["id"]]


def test_plugin_backend_has_no_legacy_knowledge_imports() -> None:
    plugin_root = (
        Path(__file__).parents[1]
        / "src"
        / "agent"
        / "plugin"
        / "plugin_impl"
        / "cyrene_knowledge"
    )
    source = "\n".join(path.read_text(encoding="utf-8") for path in plugin_root.glob("*.py"))
    assert "cyrene.knowledge" not in source
    assert "route.workbench.library" not in source
