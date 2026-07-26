"""Helpers for mapping an agent session to a Workbench project scope."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from cyrene.config import DATA_DIR
from cyrene.runtime.io import read_json_safe
from cyrene.workbench.store import read_document

_WORKBENCH_STORE = DATA_DIR / "workbench_projects.json"
_WORKBENCH_CHATS_STORE = DATA_DIR / "workbench_chats.json"
_LEGACY_DATA_KEY = "default"
_WORKBENCH_DB_PATH = ""
_CONFIGURED_PROJECTS_STORE: Path | None = None
_CONFIGURED_CHATS_STORE: Path | None = None


def configure_store(db_path: str) -> None:
    global _WORKBENCH_DB_PATH, _CONFIGURED_PROJECTS_STORE, _CONFIGURED_CHATS_STORE
    _WORKBENCH_DB_PATH = str(db_path or "")
    _CONFIGURED_PROJECTS_STORE = Path(_WORKBENCH_STORE)
    _CONFIGURED_CHATS_STORE = Path(_WORKBENCH_CHATS_STORE)


def _safe_workbench_data_key(raw: str | None) -> str:
    text = str(raw or "").strip()
    if not text:
        return _LEGACY_DATA_KEY
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", text).strip("._")
    return cleaned or _LEGACY_DATA_KEY


def _read_projects() -> list[dict[str, Any]]:
    if (
        _WORKBENCH_DB_PATH
        and _CONFIGURED_PROJECTS_STORE == Path(_WORKBENCH_STORE)
    ):
        payload = read_document(
            _WORKBENCH_DB_PATH,
            "projects",
            lambda: {"projects": []},
            legacy_path=_WORKBENCH_STORE,
        )
    else:
        payload = read_json_safe(_WORKBENCH_STORE)
    projects = payload.get("projects") if isinstance(payload, dict) else None
    return projects if isinstance(projects, list) else []


def read_projects() -> list[dict[str, Any]]:
    """Public read-only project lookup for adjacent domains."""
    return _read_projects()


def resolve_workbench_project_data_key_for_session(session_id: str | None) -> str | None:
    """Resolve a Workbench chat/task session to its project storage key.

    Returns ``None`` when the session is not attached to any Workbench project.
    A return value of ``"default"`` is valid: the initial Workbench project
    deliberately uses that legacy storage key.
    """
    sid = str(session_id or "").strip()
    if not sid:
        return None

    projects = _read_projects()
    project_id = ""

    if (
        _WORKBENCH_DB_PATH
        and _CONFIGURED_CHATS_STORE == Path(_WORKBENCH_CHATS_STORE)
    ):
        chats_payload = read_document(
            _WORKBENCH_DB_PATH,
            "chats",
            lambda: {"chats": []},
            legacy_path=_WORKBENCH_CHATS_STORE,
        )
    else:
        chats_payload = read_json_safe(_WORKBENCH_CHATS_STORE)
    chats = chats_payload.get("chats") if isinstance(chats_payload, dict) else None
    if isinstance(chats, list):
        for chat in chats:
            if str(chat.get("id") or "") == sid:
                project_id = str(chat.get("projectId") or "").strip()
                break

    if not project_id:
        for project in projects:
            for session in project.get("sessions") or []:
                if str(session.get("id") or "") == sid:
                    project_id = str(project.get("id") or "").strip()
                    break
            if project_id:
                break

    if not project_id:
        return None

    for project in projects:
        if str(project.get("id") or "") == project_id:
            return _safe_workbench_data_key(project.get("dataKey") or project_id)

    return _safe_workbench_data_key(project_id)


def resolve_workbench_project_id_for_session(session_id: str | None) -> str | None:
    """Resolve a Workbench chat/task session to its owning project id.

    Project memory uses this identity rather than ``dataKey``.  The default
    project deliberately has ``dataKey == "default"`` for legacy knowledge and
    workspace compatibility, but its memory must not alias the legacy global
    ``short_term.json`` store.
    """
    sid = str(session_id or "").strip()
    if not sid:
        return None

    projects = _read_projects()
    if (
        _WORKBENCH_DB_PATH
        and _CONFIGURED_CHATS_STORE == Path(_WORKBENCH_CHATS_STORE)
    ):
        chats_payload = read_document(
            _WORKBENCH_DB_PATH,
            "chats",
            lambda: {"chats": []},
            legacy_path=_WORKBENCH_CHATS_STORE,
        )
    else:
        chats_payload = read_json_safe(_WORKBENCH_CHATS_STORE)
    chats = chats_payload.get("chats") if isinstance(chats_payload, dict) else None
    if isinstance(chats, list):
        for chat in chats:
            if str(chat.get("id") or "") == sid:
                project_id = str(chat.get("projectId") or "").strip()
                return project_id or None

    for project in projects:
        for session in project.get("sessions") or []:
            if str(session.get("id") or "") == sid:
                project_id = str(project.get("id") or "").strip()
                return project_id or None
    return None


def resolve_workbench_session_kind(session_id: str | None) -> str | None:
    """Return ``chat`` or a project-session kind for a Workbench session."""
    sid = str(session_id or "").strip()
    if not sid:
        return None

    if (
        _WORKBENCH_DB_PATH
        and _CONFIGURED_CHATS_STORE == Path(_WORKBENCH_CHATS_STORE)
    ):
        chats_payload = read_document(
            _WORKBENCH_DB_PATH,
            "chats",
            lambda: {"chats": []},
            legacy_path=_WORKBENCH_CHATS_STORE,
        )
    else:
        chats_payload = read_json_safe(_WORKBENCH_CHATS_STORE)
    chats = chats_payload.get("chats") if isinstance(chats_payload, dict) else None
    if isinstance(chats, list):
        for chat in chats:
            if str(chat.get("id") or "") == sid:
                return "chat"

    for project in _read_projects():
        for session in project.get("sessions") or []:
            if str(session.get("id") or "") == sid:
                return str(session.get("kind") or "task").strip() or "task"
    return None


def resolve_project_data_key_for_session(session_id: str | None) -> str:
    """Compatibility resolver that falls back to the legacy ``default`` key."""
    return resolve_workbench_project_data_key_for_session(session_id) or _LEGACY_DATA_KEY


def resolve_project_knowledge_key_for_session(session_id: str | None) -> str:
    """Resolve a Workbench session to its knowledge-base storage key.

    Knowledge is keyed on the project **id** (like project memory), NOT the
    project ``dataKey``. The historical default project deliberately uses
    ``dataKey == "default"`` for the global knowledge catalog;
    keying knowledge on that would make the Workbench default project alias the
    global ``kb_default.db`` catalog and surface files from every other project.
    Keying on the id decouples it, exactly as
    :func:`resolve_workbench_project_id_for_session` does for memory. Sessions not
    attached to any project fall back to ``default`` for storage compatibility.
    """
    project_id = resolve_workbench_project_id_for_session(session_id)
    if project_id:
        return _safe_workbench_data_key(project_id)
    return _LEGACY_DATA_KEY


async def ensure_knowledge_db_for_session(session_id: str | None) -> str:
    """Return the initialized knowledge DB scoped to a Workbench session."""
    from cyrene.config import get_knowledge_db_path
    from cyrene.runtime.database import init_knowledge_db

    data_key = resolve_project_knowledge_key_for_session(session_id)
    db_path = str(get_knowledge_db_path(data_key))
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    await init_knowledge_db(db_path)
    return db_path


__all__ = [
    "configure_store",
    "ensure_knowledge_db_for_session",
    "resolve_project_data_key_for_session",
    "resolve_project_knowledge_key_for_session",
    "resolve_workbench_project_data_key_for_session",
    "resolve_workbench_project_id_for_session",
    "resolve_workbench_session_kind",
]
