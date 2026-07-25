"""Workspace-scoped knowledge base API for the new Workbench UI.

This module is intentionally independent from the legacy
``route/knowledge.py``
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
import re
from typing import Any
from urllib.parse import unquote, urlparse


from cyrene.runtime.attachments import (
    safe_attachment_filename,
)
from cyrene.workbench.compat import runtime_service

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
        R = runtime_service()
        # The Workbench sends the canonical project id on the normal path.
        # Resolve that without running the full project invariant/backfill scan,
        # which can be much more expensive than the tiny knowledge query itself.
        project = R._workbench_find_project_lightweight(raw)
        if project:
            return R._workbench_project_memory_key(project)

        # Compatibility path for older clients that still send dataKey.
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
    from cyrene.runtime.database import init_knowledge_db

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
    R = runtime_service()

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
    R = runtime_service()
    from cyrene.workbench import chat as chat_routes

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
