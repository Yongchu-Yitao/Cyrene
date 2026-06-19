import json
import zipfile
from pathlib import Path

import pytest


@pytest.mark.asyncio
async def test_resolve_and_initialize_session_scoped_knowledge_db(tmp_path, monkeypatch):
    from cyrene import config
    from cyrene import workbench_context

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
    monkeypatch.setattr(workbench_context, "_WORKBENCH_STORE", projects_path)
    monkeypatch.setattr(workbench_context, "_WORKBENCH_CHATS_STORE", tmp_path / "missing.json")
    monkeypatch.setattr(config, "STORE_DIR", tmp_path / "store")

    db_path = await workbench_context.ensure_knowledge_db_for_session("session-1")

    assert Path(db_path).name == "kb_customer_alpha.db"
    assert Path(db_path).exists()


@pytest.mark.asyncio
async def test_archive_workbench_run_indexes_summary_and_file(tmp_path, monkeypatch):
    from cyrene import attachments, config
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
async def test_changed_document_is_marked_pending_for_reindex(tmp_path):
    from cyrene import db
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
