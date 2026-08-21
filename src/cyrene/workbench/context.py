"""Helpers for mapping an agent session to a Workbench project scope."""

from __future__ import annotations

import json
import re
import sqlite3
import threading
from pathlib import Path
from typing import Any

from cyrene.config import DATA_DIR
from cyrene.runtime.io import read_json_safe
from cyrene.workbench.store import ensure_schema, read_document

_WORKBENCH_STORE = DATA_DIR / "workbench_projects.json"
_WORKBENCH_CHATS_STORE = DATA_DIR / "workbench_chats.json"
_LEGACY_DATA_KEY = "default"
_WORKBENCH_DB_PATH = ""
_CONFIGURED_PROJECTS_STORE: Path | None = None
_CONFIGURED_CHATS_STORE: Path | None = None
_SCOPE_CACHE_LOCK = threading.RLock()
_SCOPE_CACHE_SIGNATURE: tuple[Any, ...] | None = None
_SCOPE_CACHE: dict[str, Any] = {"sessions": {}, "projectIdsByDataKey": {}}


def configure_store(db_path: str) -> None:
    global _WORKBENCH_DB_PATH, _CONFIGURED_PROJECTS_STORE, _CONFIGURED_CHATS_STORE
    global _SCOPE_CACHE_SIGNATURE, _SCOPE_CACHE
    _WORKBENCH_DB_PATH = str(db_path or "")
    _CONFIGURED_PROJECTS_STORE = Path(_WORKBENCH_STORE)
    _CONFIGURED_CHATS_STORE = Path(_WORKBENCH_CHATS_STORE)
    with _SCOPE_CACHE_LOCK:
        _SCOPE_CACHE_SIGNATURE = None
        _SCOPE_CACHE = {"sessions": {}, "projectIdsByDataKey": {}}


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


def _legacy_scope_signature() -> tuple[Any, ...]:
    signature: list[Any] = ["json"]
    for path in (_WORKBENCH_CHATS_STORE, _WORKBENCH_STORE):
        try:
            stat = path.stat()
            signature.extend((str(path), stat.st_ino, stat.st_size, stat.st_mtime_ns))
        except OSError:
            signature.extend((str(path), 0, 0, 0))
    return tuple(signature)


def _sqlite_scope_signature() -> tuple[Any, ...]:
    ensure_schema(_WORKBENCH_DB_PATH)
    with sqlite3.connect(_WORKBENCH_DB_PATH, timeout=5) as conn:
        conn.execute("PRAGMA busy_timeout = 5000")
        rows = conn.execute(
            """
            SELECT key, updated_at FROM workbench_state
            WHERE key IN ('chats', 'projects') ORDER BY key
            """
        ).fetchall()
    return ("sqlite",) + tuple((str(key), str(updated_at)) for key, updated_at in rows)


def _scope_signature() -> tuple[Any, ...]:
    if (
        _WORKBENCH_DB_PATH
        and _CONFIGURED_CHATS_STORE == Path(_WORKBENCH_CHATS_STORE)
        and _CONFIGURED_PROJECTS_STORE == Path(_WORKBENCH_STORE)
    ):
        return _sqlite_scope_signature()
    return _legacy_scope_signature()


def _scope_sources() -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    if (
        _WORKBENCH_DB_PATH
        and _CONFIGURED_CHATS_STORE == Path(_WORKBENCH_CHATS_STORE)
        and _CONFIGURED_PROJECTS_STORE == Path(_WORKBENCH_STORE)
    ):
        ensure_schema(_WORKBENCH_DB_PATH)
        with sqlite3.connect(_WORKBENCH_DB_PATH, timeout=5) as conn:
            conn.execute("PRAGMA busy_timeout = 5000")
            chat_rows = conn.execute(
                "SELECT payload_json FROM workbench_chats ORDER BY ordinal, chat_id"
            ).fetchall()
            state_rows = dict(
                conn.execute(
                    """
                    SELECT key, payload_json FROM workbench_state
                    WHERE key IN ('chats', 'projects')
                    """
                ).fetchall()
            )

        chats: list[dict[str, Any]] = []
        for (payload_json,) in chat_rows:
            try:
                chat = json.loads(str(payload_json))
            except (TypeError, ValueError):
                continue
            if isinstance(chat, dict):
                chats.append(chat)

        # Import a pre-normalization chat document once. Subsequent scope
        # lookups use only lightweight chat rows and never decode transcripts.
        legacy_chats = None
        try:
            stored_chats = json.loads(str(state_rows.get("chats") or "null"))
            legacy_chats = stored_chats.get("chats") if isinstance(stored_chats, dict) else None
        except (TypeError, ValueError):
            pass
        if "chats" not in state_rows or isinstance(legacy_chats, list):
            read_document(
                _WORKBENCH_DB_PATH,
                "chats",
                lambda: {"chats": []},
                legacy_path=_WORKBENCH_CHATS_STORE,
            )
            return _scope_sources()

        try:
            projects_payload = json.loads(str(state_rows.get("projects") or "null"))
        except (TypeError, ValueError):
            projects_payload = None
        if not isinstance(projects_payload, dict):
            projects_payload = read_document(
                _WORKBENCH_DB_PATH,
                "projects",
                lambda: {"projects": []},
                legacy_path=_WORKBENCH_STORE,
            )
        projects = projects_payload.get("projects")
        return chats, projects if isinstance(projects, list) else []

    chats_payload = read_json_safe(_WORKBENCH_CHATS_STORE)
    projects_payload = read_json_safe(_WORKBENCH_STORE)
    chats = chats_payload.get("chats") if isinstance(chats_payload, dict) else None
    projects = projects_payload.get("projects") if isinstance(projects_payload, dict) else None
    return (
        chats if isinstance(chats, list) else [],
        projects if isinstance(projects, list) else [],
    )


def _build_scope_cache() -> dict[str, Any]:
    chats, projects = _scope_sources()
    sessions: dict[str, dict[str, str]] = {}
    project_ids_by_data_key: dict[str, str] = {}
    project_keys: dict[str, str] = {}
    for project in projects:
        if not isinstance(project, dict):
            continue
        project_id = str(project.get("id") or "").strip()
        if not project_id:
            continue
        project_key = _safe_workbench_data_key(project.get("dataKey") or project_id)
        project_keys[project_id] = project_key
        project_ids_by_data_key.setdefault(project_key, project_id)
        for session in project.get("sessions") or []:
            if not isinstance(session, dict):
                continue
            session_id = str(session.get("id") or "").strip()
            if session_id:
                sessions.setdefault(
                    session_id,
                    {
                        "project_id": project_id,
                        "project_key": project_key,
                        "session_kind": str(session.get("kind") or "task").strip() or "task",
                    },
                )
    # Chat ownership remains authoritative when a stale task summary happens
    # to retain the same id during migration.
    for chat in chats:
        if not isinstance(chat, dict):
            continue
        session_id = str(chat.get("id") or "").strip()
        project_id = str(chat.get("projectId") or "").strip()
        if session_id and project_id:
            sessions[session_id] = {
                "project_id": project_id,
                "project_key": project_keys.get(
                    project_id, _safe_workbench_data_key(project_id)
                ),
                "session_kind": "chat",
            }
    return {
        "sessions": sessions,
        "projectIdsByDataKey": project_ids_by_data_key,
    }


def _scope_cache() -> dict[str, Any]:
    global _SCOPE_CACHE_SIGNATURE, _SCOPE_CACHE
    signature = _scope_signature()
    with _SCOPE_CACHE_LOCK:
        if signature == _SCOPE_CACHE_SIGNATURE:
            return _SCOPE_CACHE
        # Recheck after building so a concurrent chat/project commit cannot
        # publish an index paired with the preceding version marker.
        for _attempt in range(2):
            cache = _build_scope_cache()
            latest_signature = _scope_signature()
            if latest_signature == signature:
                _SCOPE_CACHE = cache
                _SCOPE_CACHE_SIGNATURE = latest_signature
                return _SCOPE_CACHE
            signature = latest_signature
        _SCOPE_CACHE = cache
        _SCOPE_CACHE_SIGNATURE = signature
        return _SCOPE_CACHE


def resolve_workbench_session_scope(session_id: str | None) -> dict[str, str | None]:
    """Resolve project id, storage key and kind from one versioned index."""
    sid = str(session_id or "").strip()
    scope = _scope_cache().get("sessions", {}).get(sid) if sid else None
    if not isinstance(scope, dict):
        return {"project_id": None, "project_key": None, "session_kind": None}
    return {
        "project_id": str(scope.get("project_id") or "") or None,
        "project_key": str(scope.get("project_key") or "") or None,
        "session_kind": str(scope.get("session_kind") or "") or None,
    }


def resolve_workbench_project_id_for_data_key(data_key: str | None) -> str | None:
    key = _safe_workbench_data_key(data_key)
    value = _scope_cache().get("projectIdsByDataKey", {}).get(key)
    return str(value or "").strip() or None


def resolve_workbench_project_data_key_for_session(session_id: str | None) -> str | None:
    """Resolve a Workbench chat/task session to its project storage key.

    Returns ``None`` when the session is not attached to any Workbench project.
    A return value of ``"default"`` is valid: the initial Workbench project
    deliberately uses that legacy storage key.
    """
    return resolve_workbench_session_scope(session_id)["project_key"]


def resolve_workbench_project_id_for_session(session_id: str | None) -> str | None:
    """Resolve a Workbench chat/task session to its owning project id.

    Project memory uses this identity rather than ``dataKey``.  The default
    project deliberately has ``dataKey == "default"`` for legacy knowledge and
    workspace compatibility, but its memory must not alias the legacy global
    ``short_term.json`` store.
    """
    return resolve_workbench_session_scope(session_id)["project_id"]


def resolve_workbench_session_kind(session_id: str | None) -> str | None:
    """Return ``chat`` or a project-session kind for a Workbench session."""
    return resolve_workbench_session_scope(session_id)["session_kind"]


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
    "resolve_workbench_project_id_for_data_key",
    "resolve_workbench_project_id_for_session",
    "resolve_workbench_session_scope",
    "resolve_workbench_session_kind",
]
