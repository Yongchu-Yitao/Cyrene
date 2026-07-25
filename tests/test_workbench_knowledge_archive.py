import json
import zipfile
from pathlib import Path

import pytest


@pytest.mark.asyncio
async def test_resolve_and_initialize_session_scoped_knowledge_db(tmp_path, monkeypatch):
    from cyrene import config
    from cyrene.workbench import context as workbench_context

    projects_path = tmp_path / "workbench_projects.json"
    projects_path.write_text(
        json.dumps({
            "projects": [{
                "id": "project-1",
                "dataKey": "customer_alpha",
                "sessions": [{"id": "session-1"}],
            }]
        }),
        encoding="utf-8",
    )
    monkeypatch.setattr(workbench_context, "_WORKBENCH_DB_PATH", "")
    monkeypatch.setattr(workbench_context, "_WORKBENCH_STORE", projects_path)
    monkeypatch.setattr(workbench_context, "_WORKBENCH_CHATS_STORE", tmp_path / "missing.json")
    monkeypatch.setattr(config, "STORE_DIR", tmp_path / "store")

    db_path = await workbench_context.ensure_knowledge_db_for_session("session-1")

    # Knowledge is keyed on the project id, NOT dataKey, so the legacy default
    # project (dataKey "default") cannot alias the global kb_default.db catalog.
    assert Path(db_path).name == "kb_project-1.db"
    assert Path(db_path).exists()


@pytest.mark.asyncio
async def test_archive_workbench_run_indexes_summary_and_file(tmp_path, monkeypatch):
    from cyrene import config
    from cyrene.runtime import attachments
    from cyrene.knowledge import store
    from cyrene.knowledge import workbench

    exports = tmp_path / "exports"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    produced = workspace / "result.md"
    produced.write_text("# Produced report\n\nunique artifact evidence", encoding="utf-8")

    monkeypatch.setattr(config, "STORE_DIR", tmp_path / "store")
    monkeypatch.setattr(attachments, "EXPORTS_DIR", exports)
    monkeypatch.setattr(workbench, "EXPORTS_DIR", exports)

    documents = await workbench.archive_workbench_run(
        data_key="project_scope",
        session_id="session-1",
        run_id="run-1",
        title="Analysis task",
        goal="Produce a report",
        user_input="Analyze the evidence",
        agent_response="The generated conclusion contains unique task evidence.",
        file_changes=[{"path": "result.md", "status": "created"}],
        workspace_root=workspace,
    )

    db_path = str(config.get_knowledge_db_path("project_scope"))
    stored = await store.list_documents(db_path, limit=20)
    chunks = [
        chunk
        for document in stored
        for chunk in await store.get_chunks(db_path, document["id"], with_embedding=False)
    ]

    assert {document["source"] for document in documents} == {
        "workbench_task",
        "workbench_artifact",
    }
    assert all(document["status"] == "indexed" for document in stored)
    assert "unique task evidence" in "\n".join(chunk["content"] for chunk in chunks)
    assert "unique artifact evidence" in "\n".join(chunk["content"] for chunk in chunks)


@pytest.mark.asyncio
async def test_docx_text_is_extracted_for_knowledge_indexing(tmp_path):
    from cyrene.knowledge.ingest import extract_document_text

    docx = tmp_path / "report.docx"
    with zipfile.ZipFile(docx, "w") as archive:
        archive.writestr(
            "word/document.xml",
            (
                '<?xml version="1.0" encoding="UTF-8"?>'
                '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
                "<w:body><w:p><w:r><w:t>Office knowledge content</w:t></w:r></w:p></w:body>"
                "</w:document>"
            ),
        )

    text = await extract_document_text(docx, "file")

    assert "Office knowledge content" in text


@pytest.mark.asyncio
async def test_extensionless_docx_text_is_detected_from_zip_structure(tmp_path):
    from cyrene.knowledge.ingest import extract_document_text

    uploaded = tmp_path / "upload_without_extension"
    with zipfile.ZipFile(uploaded, "w") as archive:
        archive.writestr(
            "word/document.xml",
            (
                '<?xml version="1.0" encoding="UTF-8"?>'
                '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
                "<w:body><w:p><w:r><w:t>Recovered DOCX content</w:t></w:r></w:p></w:body>"
                "</w:document>"
            ),
        )

    text = await extract_document_text(uploaded, "file")

    assert "Recovered DOCX content" in text


@pytest.mark.asyncio
async def test_changed_document_is_marked_pending_for_reindex(tmp_path):
    from cyrene.runtime import database as db
    from cyrene.knowledge import store

    db_path = str(tmp_path / "knowledge.db")
    await db.init_knowledge_db(db_path)
    file_path = tmp_path / "result.md"
    file_path.write_text("first version", encoding="utf-8")
    first = await store.upsert_document_by_path(
        db_path,
        path=str(file_path),
        source="generated",
        content_hash=store.content_hash_file(file_path),
    )
    await store.update_document(db_path, first["id"], status="indexed", indexed_at=store._now())

    file_path.write_text("second version", encoding="utf-8")
    updated = await store.upsert_document_by_path(
        db_path,
        path=str(file_path),
        source="generated",
        content_hash=store.content_hash_file(file_path),
    )

    assert updated["id"] == first["id"]
    assert updated["status"] == "pending"
    assert updated["indexed_at"] is None


@pytest.mark.asyncio
async def test_list_documents_with_limit_zero_returns_all(tmp_path):
    from cyrene.runtime import database as db
    from cyrene.knowledge import store

    db_path = str(tmp_path / "knowledge.db")
    await db.init_knowledge_db(db_path)
    for i in range(5):
        f = tmp_path / f"doc_{i}.md"
        f.write_text(f"content {i}", encoding="utf-8")
        await store.upsert_document_by_path(
            db_path,
            path=str(f),
            source="generated",
            content_hash=store.content_hash_file(f),
        )

    all_docs = await store.list_documents(db_path, limit=0)
    limited = await store.list_documents(db_path, limit=2)

    assert len(all_docs) == 5
    assert len(limited) == 2


@pytest.mark.asyncio
async def test_count_documents_matches_list_without_limit(tmp_path):
    from cyrene.runtime import database as db
    from cyrene.knowledge import store

    db_path = str(tmp_path / "knowledge.db")
    await db.init_knowledge_db(db_path)
    for i in range(3):
        f = tmp_path / f"file_{i}.md"
        f.write_text(f"body {i}", encoding="utf-8")
        await store.upsert_document_by_path(
            db_path,
            path=str(f),
            source="generated",
            kind="markdown",
            content_hash=store.content_hash_file(f),
        )

    total = await store.count_documents(db_path)
    by_kind = await store.count_documents(db_path, kind="markdown")
    all_docs = await store.list_documents(db_path, limit=0)

    assert total == 3
    assert by_kind == 3
    assert len(all_docs) == total


@pytest.mark.asyncio
async def test_workbench_knowledge_list_returns_total(tmp_path, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from cyrene import config
    from cyrene.runtime import database as db
    from cyrene.knowledge import store
    from route.workbench import knowledge as routes_workbench_knowledge

    monkeypatch.setattr(config, "STORE_DIR", tmp_path / "store")
    monkeypatch.setattr(routes_workbench_knowledge, "_resolve_workspace_id", lambda ws: str(ws))

    db_path = str(tmp_path / "knowledge.db")
    await db.init_knowledge_db(db_path)
    for i in range(3):
        f = tmp_path / f"file_{i}.md"
        f.write_text(f"body {i}", encoding="utf-8")
        await store.upsert_document_by_path(
            db_path,
            path=str(f),
            source="generated",
            content_hash=store.content_hash_file(f),
        )

    async def _ensure_kb(workspace):
        return db_path

    monkeypatch.setattr(routes_workbench_knowledge, "_ensure_kb_db", _ensure_kb)

    app = FastAPI()
    routes_workbench_knowledge.register_workbench_knowledge_routes(app.router)
    client = TestClient(app)

    response = client.get("/api/workbench/knowledge/documents?workspace=test")
    payload = response.json()

    assert payload["total"] == 3
    assert len(payload["documents"]) == 3


@pytest.mark.asyncio
async def test_workbench_knowledge_supports_paged_list_and_light_detail(tmp_path, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from cyrene.runtime import database as db
    from cyrene.knowledge import store
    from route.workbench import knowledge as routes_workbench_knowledge

    db_path = str(tmp_path / "knowledge.db")
    await db.init_knowledge_db(db_path)
    docs = []
    for i in range(3):
        f = tmp_path / f"paged_{i}.md"
        f.write_text(f"body {i}", encoding="utf-8")
        doc = await store.upsert_document_by_path(
            db_path,
            path=str(f),
            source="generated",
            content_hash=store.content_hash_file(f),
        )
        docs.append(doc)
    await store.replace_chunks(
        db_path,
        docs[0]["id"],
        [{"ordinal": 0, "content": "chunk text"}],
    )

    async def _ensure_kb(workspace):
        return db_path

    monkeypatch.setattr(routes_workbench_knowledge, "_ensure_kb_db", _ensure_kb)

    app = FastAPI()
    routes_workbench_knowledge.register_workbench_knowledge_routes(app.router)
    client = TestClient(app)

    page = client.get(
        "/api/workbench/knowledge/documents",
        params={"workspace": "test", "limit": 2, "offset": 2},
    ).json()
    light_detail = client.get(
        f"/api/workbench/knowledge/documents/{docs[0]['id']}",
        params={"workspace": "test", "include_chunks": "false"},
    ).json()
    full_detail = client.get(
        f"/api/workbench/knowledge/documents/{docs[0]['id']}",
        params={"workspace": "test"},
    ).json()

    assert page["total"] == 3
    assert len(page["documents"]) == 1
    assert light_detail["chunks"] == []
    assert full_detail["chunks"][0]["content"] == "chunk text"


@pytest.mark.asyncio
async def test_workbench_knowledge_shows_final_archives_by_default(tmp_path, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from cyrene.runtime import database as db
    from cyrene.knowledge import store
    from route.workbench import knowledge as routes_workbench_knowledge

    db_path = str(tmp_path / "knowledge.db")
    await db.init_knowledge_db(db_path)
    source_file = tmp_path / "source.md"
    summary_file = tmp_path / "summary.md"
    artifact_file = tmp_path / "Info.plist"
    source_file.write_text("durable source", encoding="utf-8")
    summary_file.write_text("task summary", encoding="utf-8")
    artifact_file.write_text("generated artifact", encoding="utf-8")

    source_doc = await store.upsert_document_by_path(
        db_path,
        path=str(source_file),
        source="kb_upload",
        name="source.md",
        content_hash=store.content_hash_file(source_file),
    )
    summary_doc = await store.upsert_document_by_path(
        db_path,
        path=str(summary_file),
        source="workbench_task",
        name="summary.md",
        content_hash=store.content_hash_file(summary_file),
    )
    artifact_doc = await store.upsert_document_by_path(
        db_path,
        path=str(artifact_file),
        source="workbench_artifact",
        name="Info.plist",
        content_hash=store.content_hash_file(artifact_file),
    )

    async def _ensure_kb(_workspace):
        return db_path

    monkeypatch.setattr(routes_workbench_knowledge, "_ensure_kb_db", _ensure_kb)
    monkeypatch.setattr(
        routes_workbench_knowledge,
        "_resolve_workspace_id",
        lambda workspace: str(workspace),
    )

    app = FastAPI()
    routes_workbench_knowledge.register_workbench_knowledge_routes(app.router)
    client = TestClient(app)

    default_payload = client.get("/api/workbench/knowledge/documents?workspace=test").json()
    artifact_source_payload = client.get(
        "/api/workbench/knowledge/documents?workspace=test&source=workbench_artifact"
    ).json()

    assert default_payload["total"] == 3
    assert {doc["id"] for doc in default_payload["documents"]} == {
        source_doc["id"],
        summary_doc["id"],
        artifact_doc["id"],
    }
    assert [doc["id"] for doc in artifact_source_payload["documents"]] == [artifact_doc["id"]]


@pytest.mark.asyncio
async def test_workbench_knowledge_search_includes_final_archives_by_default(tmp_path, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from cyrene.runtime import database as db
    from cyrene.knowledge import store
    from route.workbench import knowledge as routes_workbench_knowledge

    db_path = str(tmp_path / "knowledge.db")
    await db.init_knowledge_db(db_path)
    source = await store.create_document(
        db_path,
        name="source.md",
        path=str(tmp_path / "source.md"),
        source="kb_upload",
    )
    artifact = await store.create_document(
        db_path,
        name="artifact.md",
        path=str(tmp_path / "artifact.md"),
        source="workbench_artifact",
    )
    await store.replace_chunks(
        db_path,
        source["id"],
        [{"ordinal": 0, "content": "shared needle from durable knowledge"}],
    )
    await store.replace_chunks(
        db_path,
        artifact["id"],
        [{"ordinal": 0, "content": "shared needle from generated build artifact"}],
    )

    async def _ensure_kb(_workspace):
        return db_path

    monkeypatch.setattr(routes_workbench_knowledge, "_ensure_kb_db", _ensure_kb)

    app = FastAPI()
    routes_workbench_knowledge.register_workbench_knowledge_routes(app.router)
    client = TestClient(app)

    default_results = client.get(
        "/api/workbench/knowledge/search",
        params={"workspace": "test", "q": "shared needle", "k": 10},
    ).json()["results"]
    assert {item["document_id"] for item in default_results} == {source["id"], artifact["id"]}


@pytest.mark.asyncio
async def test_workbench_knowledge_related_returns_tasks_chats_and_reverse_relations(
    tmp_path, monkeypatch
):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from cyrene.runtime import database as db
    from cyrene.knowledge import store
    from cyrene.workbench import runtime as routes
    from cyrene.workbench import chat as routes_workbench_chat
    from route.workbench import knowledge as routes_workbench_knowledge

    db_path = str(tmp_path / "knowledge.db")
    await db.init_knowledge_db(db_path)
    report = tmp_path / "report.pdf"
    report.write_bytes(b"report")
    source = tmp_path / "source.md"
    source.write_text("source", encoding="utf-8")
    report_doc = await store.upsert_document_by_path(
        db_path,
        path=str(report),
        source="generated",
        name="report.pdf",
        metadata={"sent_to_chat": True},
        content_hash=store.content_hash_file(report),
    )
    source_doc = await store.upsert_document_by_path(
        db_path,
        path=str(source),
        source="kb_upload",
        name="source.md",
        content_hash=store.content_hash_file(source),
    )
    await store.create_relation(
        db_path,
        src_id=source_doc["id"],
        dst_id=report_doc["id"],
        relation="supports",
    )

    monkeypatch.setattr(
        routes,
        "_read_workbench_store",
        lambda: {
            "projects": [{
                "id": "project-1",
                "dataKey": "workspace-1",
                "sessions": [{
                    "id": "session-1",
                    "title": "Build report",
                    "goal": "Produce the final report",
                    "status": "review",
                    "updatedAt": "2026-06-25T10:00:00Z",
                    "runs": [{
                        "id": "run-1",
                        "knowledgeDocumentIds": [report_doc["id"]],
                    }],
                }],
            }],
        },
    )
    monkeypatch.setattr(
        routes_workbench_chat,
        "_read_chats_store",
        lambda: {
            "chats": [{
                "id": "wbchat-1",
                "projectId": "project-1",
                "title": "Review report",
                "status": "idle",
                "updatedAt": "2026-06-25T11:00:00Z",
                "messages": [{
                    "role": "assistant",
                    "content": "Here is the report.",
                    "attachments": [{
                        "id": report.name,
                        "name": "report.pdf",
                        "url": f"/api/chat/export/{report.name}",
                    }],
                }],
            }],
        },
    )

    async def _ensure_kb(_workspace):
        return db_path

    monkeypatch.setattr(routes_workbench_knowledge, "_ensure_kb_db", _ensure_kb)
    monkeypatch.setattr(
        routes_workbench_knowledge,
        "_resolve_workspace_id",
        lambda workspace: str(workspace),
    )

    app = FastAPI()
    routes_workbench_knowledge.register_workbench_knowledge_routes(app.router)
    client = TestClient(app)

    response = client.get(
        f"/api/workbench/knowledge/documents/{report_doc['id']}/related"
        "?workspace=workspace-1"
    )
    payload = response.json()

    assert response.status_code == 200
    assert payload["counts"] == {"documents": 1, "conversations": 2}
    assert {item["type"] for item in payload["conversations"]} == {"task", "chat"}
    assert payload["document_relations"][0]["direction"] == "incoming"
    assert payload["document_relations"][0]["document"]["id"] == source_doc["id"]
