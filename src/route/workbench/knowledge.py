"""FastAPI adapters for workspace-scoped Workbench knowledge."""

# Service symbols are bound below so request adapters remain separate from
# persistence and knowledge-domain operations.
# ruff: noqa: F821

from __future__ import annotations

import mimetypes
from datetime import datetime
from pathlib import Path
from typing import Any

from cyrene.workbench import knowledge as _service
from cyrene.runtime.attachments import (
    UPLOADS_DIR as _UPLOADS_DIR,
    attachment_kind_from_meta,
    resolve_managed_attachment_path,
)
from fastapi import APIRouter, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from route import schemas as api_models
from route.errors import error_response
from cyrene.workbench.notifications import append_notification

globals().update({
    name: value
    for name, value in vars(_service).items()
    if not name.startswith("__")
})


def register_workbench_knowledge_routes(router: APIRouter) -> None:
    """Register workspace-scoped knowledge routes for the Workbench UI."""
    from cyrene.knowledge import store, ingest, retrieve
    reembed_state: dict[str, dict[str, Any]] = {}

    @router.get("/api/workbench/knowledge/embedding/status")
    async def wb_embedding_status(workspace: str = ""):
        db_path = await _ensure_kb_db(workspace)
        info = await store.get_corpus_embedding_info(db_path)
        from cyrene.knowledge import embeddings

        model, dimensions = embeddings.current_identity()
        configured = embeddings.is_configured()
        coverage = await store.get_embedding_coverage(db_path, model, dimensions)
        # Missing optional embeddings means lexical-only retrieval, not a
        # broken corpus.  A mismatch is actionable only when the configured
        # provider is actually available and can generate replacement vectors.
        mismatch = bool(
            configured
            and coverage["total_chunks"]
            and coverage["pending_vectors"]
        )
        return {
            **info,
            **coverage,
            "configured": configured,
            "retrieval_mode": "hybrid" if configured else "keyword",
            "model": model,
            "dimensions": dimensions,
            "mismatch": mismatch,
            "reembed": reembed_state.get(db_path, {"running": False}),
        }

    @router.post("/api/workbench/knowledge/reembed")
    async def wb_reembed(workspace: str = ""):
        db_path = await _ensure_kb_db(workspace)
        current = reembed_state.get(db_path, {})
        if current.get("running"):
            return {"ok": True, "reembed": current}

        async def run() -> None:
            reembed_state[db_path] = {"running": True, "error": ""}
            try:
                result = await ingest.reembed_all(db_path)
                reembed_state[db_path] = {"running": False, **result, "error": ""}
            except Exception as exc:
                logger.exception("Knowledge re-embedding failed for %s", workspace)
                reembed_state[db_path] = {"running": False, "error": str(exc)}

        asyncio.create_task(run())
        return {"ok": True, "reembed": {"running": True}}

    @router.get("/api/workbench/knowledge/documents")
    async def wb_list_documents(
        workspace: str = "",
        q: str = None,
        kind: str = None,
        status: str = None,
        tag: str = None,
        source: str = None,
        limit: int = 0,
        offset: int = 0,
    ):
        """List documents in a workspace's knowledge base.

        ``limit <= 0`` (the default) returns all documents so the Workbench UI
        never silently truncates. A ``total`` count is always included.
        """
        try:
            db_path = await _ensure_kb_db(workspace)
            documents, total = await asyncio.gather(
                store.list_documents(
                    db_path,
                    q=q,
                    kind=kind,
                    status=status,
                    tag=tag,
                    source=source,
                    limit=limit,
                    offset=max(0, offset),
                ),
                store.count_documents(
                    db_path,
                    q=q,
                    kind=kind,
                    status=status,
                    tag=tag,
                    source=source,
                ),
            )
            return {
                "documents": documents,
                "total": total,
                "workspace": _resolve_workspace_id(workspace),
            }
        except Exception:
            logger.exception("Failed to list knowledge documents for %s", workspace)
            return error_response("List failed", 500, "knowledge_list_failed")

    @router.get("/api/workbench/knowledge/stats")
    async def wb_get_stats(workspace: str = ""):
        """Aggregate stats for a workspace's knowledge base."""
        try:
            db_path = await _ensure_kb_db(workspace)
            return await store.get_stats(db_path)
        except Exception:
            logger.exception("Failed to load knowledge stats for %s", workspace)
            return error_response("Stats failed", 500, "knowledge_stats_failed")

    @router.get("/api/workbench/knowledge/documents/{doc_id}")
    async def wb_get_document(
        doc_id: str,
        workspace: str = "",
        include_chunks: bool = True,
        chunks_limit: int = 200,
    ):
        """Get a document with its chunks and relations."""
        try:
            db_path = await _ensure_kb_db(workspace)
            doc = await store.get_document(db_path, doc_id)
            if not doc:
                return JSONResponse({"error": "not found"}, status_code=404)
            chunks = (
                await store.get_chunks(
                    db_path,
                    doc_id,
                    with_embedding=False,
                    limit=max(1, min(chunks_limit, 500)),
                )
                if include_chunks
                else []
            )
            relations = await store.list_relations(db_path, document_id=doc_id)
            return {**doc, "chunks": chunks, "relations": relations}
        except Exception:
            logger.exception("Failed to load knowledge document %s", doc_id)
            return error_response("Get failed", 500, "knowledge_get_failed")

    @router.get("/api/workbench/knowledge/documents/{doc_id}/related")
    async def wb_get_document_related(doc_id: str, workspace: str = ""):
        """Return bidirectional document links plus related Workbench conversations."""
        try:
            db_path = await _ensure_kb_db(workspace)
            doc = await store.get_document(db_path, doc_id)
            if not doc:
                return JSONResponse({"error": "not found"}, status_code=404)

            relations = await store.list_relations(db_path, document_id=doc_id)
            documents = await store.list_documents(db_path, limit=0)
            documents_by_id = {
                str(item.get("id") or ""): item
                for item in documents
                if isinstance(item, dict)
            }
            document_relations: list[dict[str, Any]] = []
            for relation in relations:
                src_id = str(relation.get("src_id") or "")
                dst_id = str(relation.get("dst_id") or "")
                other_id = dst_id if src_id == doc_id else src_id
                other = documents_by_id.get(other_id)
                document_relations.append({
                    **relation,
                    "direction": "outgoing" if src_id == doc_id else "incoming",
                    "document": other,
                })

            conversations = _build_related_conversations(doc, workspace)
            return {
                "document_relations": document_relations,
                "conversations": conversations,
                "counts": {
                    "documents": len(document_relations),
                    "conversations": len(conversations),
                },
            }
        except Exception:
            logger.exception("Failed to load related items for knowledge document %s", doc_id)
            return error_response("Related items failed", 500, "knowledge_related_failed")

    @router.post("/api/workbench/knowledge/documents")
    async def wb_upload_documents(files: list[UploadFile], workspace: str = ""):
        """Upload one or more documents into a workspace's knowledge base."""
        if not files:
            return JSONResponse({"error": "no files uploaded"}, status_code=400)

        try:
            db_path = await _ensure_kb_db(workspace)
        except Exception:
            logger.exception("Failed to initialize knowledge workspace %s", workspace)
            return error_response(
                "Workspace init failed", 500, "knowledge_workspace_init_failed"
            )

        _UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
        documents: list[dict[str, Any]] = []
        now = datetime.now().strftime("%Y%m%d_%H%M%S")

        for index, file in enumerate(files, start=1):
            try:
                safe_name = _safe_upload_name(file.filename or "")
                target = _UPLOADS_DIR / f"{now}_{index:02d}_{safe_name}"
                content = await file.read()
                target.write_bytes(content)
                content_hash = store.content_hash_bytes(content)

                content_type = str(
                    file.content_type
                    or mimetypes.guess_type(str(target))[0]
                    or "application/octet-stream"
                )
                kind = attachment_kind_from_meta(content_type, target.name)

                doc = await store.upsert_document_by_path(
                    db_path,
                    path=str(target.resolve()),
                    source="kb_upload",
                    name=file.filename or safe_name,
                    content_type=content_type,
                    kind=kind,
                    size=len(content),
                    content_hash=content_hash,
                )
                # If this content already existed, drop the freshly written dupe.
                if doc.get("path") and str(Path(doc["path"]).resolve()) != str(target.resolve()):
                    target.unlink(missing_ok=True)
                documents.append(doc)

                if doc.get("status") in {"pending", "error"}:
                    asyncio.create_task(ingest.index_document(db_path, doc["id"]))
            except Exception:
                logger.exception(
                    "Failed to upload knowledge document %s for %s",
                    file.filename,
                    workspace,
                )
                return error_response(
                    f"Failed to upload {file.filename}",
                    500,
                    "knowledge_upload_failed",
                )
        for doc in documents:
            append_notification(
                title="文件上传完成",
                body=f"文件「{doc.get('name') or '未命名文件'}」已上传到知识库。",
                tab="system",
                project_ref=workspace,
                source="knowledge_upload",
                source_label="知识库",
                link_label=str(doc.get("name") or ""),
                meta={"documentId": doc.get("id")},
            )
        return {"documents": documents}

    @router.patch("/api/workbench/knowledge/documents/{doc_id}")
    async def wb_update_document(
        doc_id: str,
        body_model: api_models.KnowledgeUpdateBody,
        workspace: str = "",
    ):
        """Update document metadata (title / tags / summary)."""
        body = api_models.body_dict(body_model)
        try:
            db_path = await _ensure_kb_db(workspace)
            allowed_fields = {"title", "tags", "summary", "entity_id"}
            filtered = {k: v for k, v in (body or {}).items() if k in allowed_fields}
            if not filtered:
                doc = await store.get_document(db_path, doc_id)
                return doc or JSONResponse({"error": "not found"}, status_code=404)
            updated = await store.update_document(db_path, doc_id, **filtered)
            return updated or JSONResponse({"error": "not found"}, status_code=404)
        except Exception:
            logger.exception("Failed to update knowledge document %s", doc_id)
            return error_response("Update failed", 500, "knowledge_update_failed")

    @router.post("/api/workbench/knowledge/documents/{doc_id}/reindex")
    async def wb_reindex_document(doc_id: str, workspace: str = ""):
        """Re-run extraction + indexing for a document."""
        try:
            db_path = await _ensure_kb_db(workspace)
            doc = await store.get_document(db_path, doc_id)
            if not doc:
                return JSONResponse({"error": "not found"}, status_code=404)
            asyncio.create_task(ingest.reindex_document(db_path, doc_id))
            append_notification(
                title="文件重建索引",
                body=f"文件「{doc.get('name') or '未命名文件'}」已加入重新索引队列。",
                tab="system",
                project_ref=workspace,
                source="knowledge_reindex",
                source_label="知识库",
                link_label=str(doc.get("name") or ""),
                meta={"documentId": doc_id},
            )
            return {"ok": True}
        except Exception:
            logger.exception("Failed to reindex knowledge document %s", doc_id)
            return error_response("Reindex failed", 500, "knowledge_reindex_failed")

    @router.delete("/api/workbench/knowledge/documents/{doc_id}")
    async def wb_delete_document(doc_id: str, workspace: str = ""):
        """Delete a document (and its on-disk file)."""
        try:
            db_path = await _ensure_kb_db(workspace)
            success = await store.delete_document(db_path, doc_id, remove_file=True)
            return {"ok": success}
        except Exception:
            logger.exception("Failed to delete knowledge document %s", doc_id)
            return error_response("Delete failed", 500, "knowledge_delete_failed")

    @router.get("/api/workbench/knowledge/documents/{doc_id}/raw")
    async def wb_get_document_raw(doc_id: str, workspace: str = ""):
        """Download / preview the original document file."""
        try:
            db_path = await _ensure_kb_db(workspace)
            doc = await store.get_document(db_path, doc_id)
            if not doc:
                return JSONResponse({"error": "not found"}, status_code=404)
            path_str = doc.get("path", "")
            if not path_str:
                return JSONResponse({"error": "no file path"}, status_code=404)
            file_path = resolve_managed_attachment_path(path_str)
            if file_path is None:
                return JSONResponse({"error": "file not in allowed paths"}, status_code=403)
            if not file_path.exists():
                return JSONResponse({"error": "file not found on disk"}, status_code=404)
            return FileResponse(
                str(file_path), media_type=doc.get("content_type", "application/octet-stream")
            )
        except Exception:
            logger.exception("Failed raw access for knowledge document %s", doc_id)
            return error_response("Raw access failed", 500, "knowledge_raw_failed")

    @router.get("/api/workbench/knowledge/search")
    async def wb_search_knowledge(
        workspace: str = "",
        q: str = "",
        k: int = 8,
    ):
        """Full-text / hybrid search within a workspace's knowledge base."""
        try:
            if not q.strip():
                return {"results": []}
            db_path = await _ensure_kb_db(workspace)
            results = await retrieve.search_knowledge(
                db_path,
                q,
                k=k,
            )
            return {"results": results}
        except Exception:
            logger.exception("Failed knowledge search for %s", workspace)
            return error_response("Search failed", 500, "knowledge_search_failed")


__all__ = ["register_workbench_knowledge_routes"]
