"""Workspace-scoped knowledge base API for the new Workbench UI.

This module is intentionally INDEPENDENT from the legacy ``routes_knowledge.py``
(which the old ``--agent`` UI uses). It exposes a parallel set of endpoints
under ``/api/workbench/knowledge/*`` so the two UIs never share request code.

The only thing shared is the pure data layer (``cyrene.knowledge.store`` /
``ingest`` / ``retrieve``) — that *is* the backend interface we reuse.

Per-workspace isolation: every request carries a ``workspace`` query param
(the Workbench project id). It resolves to its own ``kb_<workspace>.db`` file
via :func:`cyrene.config.get_knowledge_db_path`, so each workspace/project owns
a separate knowledge base. A missing/blank workspace falls back to ``default``.
"""

import asyncio
import logging
import mimetypes
import re
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

from fastapi import APIRouter, UploadFile
from fastapi.responses import JSONResponse, FileResponse

from cyrene.attachments import (
    UPLOADS_DIR as _UPLOADS_DIR,
    attachment_kind_from_meta,
    is_uploaded_attachment_path,
    is_exported_attachment_path,
    safe_attachment_filename,
)
from webui import api_models
from webui.api_errors import error_response
from webui.workbench_notifications import append_notification

logger = logging.getLogger(__name__)

# Cache of knowledge-db paths whose tables have already been created, so we
# init each workspace db lazily (on first touch) exactly once per process.
_kb_initialized: set[str] = set()
_kb_init_lock = asyncio.Lock()

def _safe_workspace_id(workspace_id: str | None) -> str:
    """Sanitize a workspace id into a filesystem-safe key (defaults to 'default')."""
    raw = str(workspace_id or "").strip()
    if not raw:
        return "default"
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", raw).strip("._")
    return cleaned or "default"


def _resolve_workspace_id(workspace_id: str | None) -> str:
    """Map a Workbench workspace identifier to its knowledge storage key.

    The knowledge page sends a project's ``dataKey`` (or id) as the workspace,
    but knowledge is stored under the project **id** key — the same key project
    memory uses (see ``resolve_project_knowledge_key_for_session``). For the
    legacy default project these differ (dataKey == "default", id ==
    "project_…"): returning "default" would alias the global ``kb_default.db``
    catalog and leak every project's uploaded/generated files into the default
    project's view. Match by id first, then by dataKey, and always return the
    id-based key so reads and writes agree.
    """
    wid = _safe_workspace_id(workspace_id)
    raw = str(workspace_id or "").strip()
    try:
        from webui import routes as R

        payload = R._read_workbench_store()
        project = R._workbench_find_project(payload, raw)
        if project is None:
            project = next(
                (
                    p
                    for p in payload.get("projects", [])
                    if R._workbench_project_data_key(p) == wid
                ),
                None,
            )
        if project:
            return R._workbench_project_memory_key(project)
    except Exception:
        pass
    return wid


def _safe_upload_name(filename: str) -> str:
    """Sanitize a filename for upload."""
    return safe_attachment_filename(filename, fallback_stem="upload")


async def _ensure_kb_db(workspace_id: str | None) -> str:
    """Resolve a workspace to its kb db path, creating tables on first use."""
    from cyrene.config import get_knowledge_db_path
    from cyrene.db import init_knowledge_db

    wid = _resolve_workspace_id(workspace_id)
    db_path = str(get_knowledge_db_path(wid))
    if db_path not in _kb_initialized:
        async with _kb_init_lock:
            if db_path not in _kb_initialized:
                await init_knowledge_db(db_path)
                _kb_initialized.add(db_path)
    return db_path


def _related_value_tokens(value: Any) -> set[str]:
    """Return stable exact-match tokens for a file path, URL, id, or name."""
    raw = unquote(str(value or "").strip())
    if not raw:
        return set()
    parsed = urlparse(raw)
    path_value = parsed.path if parsed.scheme or raw.startswith("/") else raw
    normalized = path_value.replace("\\", "/").rstrip("/").lower()
    tokens = {normalized} if normalized else set()
    name = normalized.rsplit("/", 1)[-1]
    if name:
        tokens.add(name)
    return tokens


def _document_match_tokens(document: dict[str, Any]) -> set[str]:
    tokens: set[str] = set()
    for value in (
        document.get("path"),
        document.get("name"),
        document.get("title"),
        (document.get("metadata") or {}).get("workspace_path"),
        (document.get("metadata") or {}).get("attachment_id"),
    ):
        tokens.update(_related_value_tokens(value))
    return tokens


def _attachment_matches_document(
    item: Any,
    document_tokens: set[str],
) -> bool:
    if not isinstance(item, dict) or not document_tokens:
        return False
    item_tokens: set[str] = set()
    for key in ("id", "name", "path", "url", "workspace_path"):
        item_tokens.update(_related_value_tokens(item.get(key)))
    return bool(document_tokens.intersection(item_tokens))


def _items_match_document(items: Any, document_tokens: set[str]) -> bool:
    return isinstance(items, list) and any(
        _attachment_matches_document(item, document_tokens) for item in items
    )


def _project_for_workspace(
    payload: dict[str, Any],
    workspace: str,
) -> dict[str, Any] | None:
    from webui import routes as R

    resolved = _resolve_workspace_id(workspace)
    raw = str(workspace or "").strip()
    for project in payload.get("projects", []):
        if not isinstance(project, dict):
            continue
        if str(project.get("id") or "") == raw:
            return project
        # ``resolved`` is the id-based knowledge key; match it so the legacy
        # default project (dataKey "default" != id) still resolves.
        if R._workbench_project_memory_key(project) == resolved:
            return project
        if str(project.get("dataKey") or "") == raw:
            return project
    return None


def _relation_preview(value: Any, limit: int = 140) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    return text[:limit]


def _build_related_conversations(
    document: dict[str, Any],
    workspace: str,
) -> list[dict[str, Any]]:
    """Resolve task/chat conversations associated with a knowledge document."""
    from webui import routes as R
    from webui import routes_workbench_chat as chat_routes

    projects_payload = R._read_workbench_store()
    project = _project_for_workspace(projects_payload, workspace)
    if not project:
        return []

    project_id = str(project.get("id") or "")
    doc_id = str(document.get("id") or "")
    metadata = document.get("metadata") if isinstance(document.get("metadata"), dict) else {}
    metadata_session_id = str(metadata.get("session_id") or "")
    metadata_run_id = str(metadata.get("run_id") or "")
    document_tokens = _document_match_tokens(document)
    conversations: list[dict[str, Any]] = []

    for session in project.get("sessions", []):
        if not isinstance(session, dict):
            continue
        session_id = str(session.get("id") or "")
        reasons: set[str] = set()
        matched_run_id = ""
        if metadata_session_id and metadata_session_id == session_id:
            reasons.add("来源任务")
            matched_run_id = metadata_run_id

        for run in session.get("runs", []):
            if not isinstance(run, dict):
                continue
            run_id = str(run.get("id") or "")
            if doc_id and doc_id in {
                str(value or "") for value in (run.get("knowledgeDocumentIds") or [])
            }:
                reasons.add("任务产出")
                matched_run_id = matched_run_id or run_id
            attachment_groups = [
                run.get("attachments"),
                run.get("artifacts"),
                run.get("fileChanges"),
            ]
            for event in run.get("events", []):
                if isinstance(event, dict):
                    attachment_groups.append(event.get("attachments"))
            if any(
                _items_match_document(items, document_tokens)
                for items in attachment_groups
            ):
                reasons.add("对话附件")
                matched_run_id = matched_run_id or run_id

        if _items_match_document(session.get("artifacts"), document_tokens):
            reasons.add("任务产出")
        if not reasons:
            continue
        conversations.append({
            "id": session_id,
            "type": "task",
            "project_id": project_id,
            "session_id": session_id,
            "run_id": matched_run_id,
            "title": str(session.get("title") or "未命名任务"),
            "preview": _relation_preview(
                session.get("summary")
                or session.get("goal")
                or session.get("agentReply")
            ),
            "status": str(session.get("status") or ""),
            "updated_at": session.get("updatedAt") or session.get("createdAt"),
            "reasons": sorted(reasons),
        })

    chats_payload = chat_routes._read_chats_store()
    for chat in chats_payload.get("chats", []):
        if not isinstance(chat, dict) or str(chat.get("projectId") or "") != project_id:
            continue
        chat_id = str(chat.get("id") or "")
        reasons: set[str] = set()
        if metadata_session_id and metadata_session_id == chat_id:
            reasons.add("来源对话")
        matched_messages = 0
        for message in chat.get("messages", []):
            if not isinstance(message, dict):
                continue
            if (
                _items_match_document(message.get("attachments"), document_tokens)
                or _items_match_document(message.get("agentAttachments"), document_tokens)
            ):
                matched_messages += 1
        if matched_messages:
            reasons.add("对话附件")
        if not reasons:
            continue
        conversations.append({
            "id": chat_id,
            "type": "chat",
            "project_id": project_id,
            "chat_id": chat_id,
            "title": str(chat.get("title") or "新对话"),
            "preview": _relation_preview(chat_routes._chat_preview(chat)),
            "status": str(chat.get("status") or "idle"),
            "updated_at": chat.get("updatedAt") or chat.get("createdAt"),
            "reasons": sorted(reasons),
            "match_count": matched_messages,
        })

    conversations.sort(
        key=lambda item: str(item.get("updated_at") or ""),
        reverse=True,
    )
    return conversations


def register_workbench_knowledge_routes(router: APIRouter) -> None:
    """Register workspace-scoped knowledge routes for the Workbench UI."""
    from cyrene.knowledge import store, ingest, retrieve

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
            documents = await store.list_documents(
                db_path,
                q=q,
                kind=kind,
                status=status,
                tag=tag,
                source=source,
                limit=limit,
                offset=max(0, offset),
            )
            total = await store.count_documents(
                db_path,
                q=q,
                kind=kind,
                status=status,
                tag=tag,
                source=source,
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
            if not (is_uploaded_attachment_path(path_str) or is_exported_attachment_path(path_str)):
                return JSONResponse({"error": "file not in allowed paths"}, status_code=403)
            file_path = Path(path_str)
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
