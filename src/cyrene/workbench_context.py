"""Helpers for mapping an agent session to a Workbench project scope."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from cyrene.config import DATA_DIR
from cyrene.io_utils import read_json_safe
from cyrene.workbench_store import read_document

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


def resolve_project_data_key_for_session(session_id: str | None) -> str:
    """Compatibility resolver that falls back to the legacy ``default`` key."""
    return resolve_workbench_project_data_key_for_session(session_id) or _LEGACY_DATA_KEY


async def ensure_knowledge_db_for_session(session_id: str | None) -> str:
    """Return the initialized knowledge DB scoped to a Workbench session."""
    from cyrene.config import get_knowledge_db_path
    from cyrene.db import init_knowledge_db

    data_key = resolve_project_data_key_for_session(session_id)
    db_path = str(get_knowledge_db_path(data_key))
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    await init_knowledge_db(db_path)
    return db_path


__all__ = [
    "configure_store",
    "ensure_knowledge_db_for_session",
    "resolve_project_data_key_for_session",
    "resolve_workbench_project_data_key_for_session",
]
