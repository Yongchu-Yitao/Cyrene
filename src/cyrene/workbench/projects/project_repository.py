"""Conversation-native Workbench project repository."""

from __future__ import annotations

import asyncio
import logging
import threading
from typing import Any

from cyrene.config import DB_PATH, WORKSPACE_DIR
from cyrene.workbench.persistence.store import patch_document_fields, read_document, write_document
from cyrene.workbench.projects import project_runtime

logger = logging.getLogger(__name__)
_WORKBENCH_STORE_LOCK = threading.RLock()
_db_path = str(DB_PATH)


def _configure_workbench_store(db_path: str) -> None:
    global _db_path
    _db_path = str(db_path or DB_PATH)


def _read_workbench_store() -> dict[str, Any]:
    with _WORKBENCH_STORE_LOCK:
        raw = read_document(_db_path, "projects", project_runtime._workbench_default_project)
        if not isinstance(raw, dict) or not isinstance(raw.get("projects"), list):
            raw = project_runtime._workbench_default_project()
        if not raw["projects"]:
            raw = project_runtime._workbench_default_project()
        _workbench_ensure_invariants(raw)
        return raw


def _read_workbench_store_lightweight() -> dict[str, Any]:
    return _read_workbench_store()


def find_workbench_project_lightweight(project_id: str) -> dict[str, Any] | None:
    target_id = str(project_id or "").strip()
    if not target_id:
        return None
    project = _workbench_find_project(_read_workbench_store(), target_id)
    if not isinstance(project, dict):
        return None
    result = dict(project)
    relocated_root = project_runtime._workbench_workspace_root(result)
    if relocated_root is not None:
        result["workspacePath"] = str(relocated_root)
    return result


def resolve_project_workspace_dir(project: dict[str, Any] | None) -> str:
    """Resolve and create the workspace owned by one Workbench project."""
    root = project_runtime._workbench_workspace_root(project)
    if root is None:
        return ""
    try:
        root.mkdir(parents=True, exist_ok=True)
    except OSError:
        logger.warning(
            "Workbench workspace unavailable, using global: %s",
            str((project or {}).get("workspacePath") or ""),
        )
        return ""
    return str(root)


async def resolve_project_workspace_dir_async(project: dict[str, Any] | None) -> str:
    return await asyncio.to_thread(resolve_project_workspace_dir, project)


def _write_workbench_store(
    payload: dict[str, Any], *, base_value: dict[str, Any] | None = None
) -> None:
    _workbench_ensure_invariants(payload)
    with _WORKBENCH_STORE_LOCK:
        merged = write_document(
            _db_path,
            "projects",
            payload,
            project_runtime._workbench_default_project,
            base_value=base_value,
        )
        payload.clear()
        payload.update(merged)
        if hasattr(payload, "_workbench_base"):
            payload._workbench_base = getattr(merged, "_workbench_base", dict(merged))
    from cyrene.observability.debug import publish_event_sync

    publish_event_sync({"type": "project_board_changed"})


def _persist_workbench_selection(project_id: str | None) -> dict[str, Any]:
    if project_id is None:
        return {}
    with _WORKBENCH_STORE_LOCK:
        return patch_document_fields(
            _db_path,
            "projects",
            {"activeProjectId": str(project_id).strip()},
            project_runtime._workbench_default_project,
        )


def _workbench_ensure_invariants(payload: dict[str, Any]) -> bool:
    changed = False
    projects = payload.setdefault("projects", [])
    now = project_runtime._utc_now_iso()
    for project in projects:
        project.setdefault("id", project_runtime._short_id("project"))
        project.setdefault("name", "Workspace")
        project.setdefault("description", "")
        project.setdefault("icon", "spark")
        project.setdefault("color", "")
        project.setdefault("workspacePath", str(WORKSPACE_DIR))
        project.setdefault("workspacePathSource", "user")
        project.setdefault("status", "active")
        project.setdefault("executionActions", [])
        project.setdefault("executionScope", ".")
        project.setdefault("model", project_runtime._get_model())
        project.setdefault("accountTier", "Pro")
        project.setdefault(
            "context",
            {"summary": "", "stack": [], "decisions": [], "knowledgeDocumentIds": []},
        )
        project.setdefault("createdAt", now)
        project.setdefault("updatedAt", now)
        relocated_root = project_runtime._workbench_workspace_root(project)
        if relocated_root is not None and str(project.get("workspacePath") or "") != str(relocated_root):
            project["workspacePath"] = str(relocated_root)
            changed = True
        project.setdefault(
            "dataKey", project_runtime._safe_workbench_data_key(project.get("id"))
        )
    if projects and not payload.get("activeProjectId"):
        payload["activeProjectId"] = projects[0].get("id")
        changed = True
    return changed


def _workbench_find_project(
    payload: dict[str, Any], project_id: str
) -> dict[str, Any] | None:
    for project in payload.get("projects", []):
        if str(project.get("id") or "") == project_id:
            return project
    return None


def _workbench_lightweight_store(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        **{key: value for key, value in payload.items() if key != "projects"},
        "projects": [
            dict(project)
            for project in payload.get("projects", [])
            if isinstance(project, dict)
        ],
    }


configure_workbench_store = _configure_workbench_store
read_workbench_store = _read_workbench_store
find_workbench_project = _workbench_find_project

__all__ = [
    "configure_workbench_store",
    "find_workbench_project",
    "find_workbench_project_lightweight",
    "read_workbench_store",
    "resolve_project_workspace_dir",
    "resolve_project_workspace_dir_async",
]
