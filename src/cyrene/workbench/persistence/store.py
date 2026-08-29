"""Transactional SQLite storage for Workbench state.

This module exposes the current document-shaped API over SQLite as the sole
source of truth.

Reads return a tracked top-level dict/list carrying its baseline snapshot.
Writes run under ``BEGIN IMMEDIATE`` and three-way merge the caller's changes
with the latest committed document.  Lists of entities are merged by stable
``id`` so concurrent messages, sessions, notifications, and memories are
preserved.
"""

from __future__ import annotations

import logging
import sqlite3
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any, TypeVar

from cyrene.workbench.persistence.document_merge import (
    TrackedDict,
)
from cyrene.workbench.persistence.chat_repository import ChatPorts, ChatRepository
from cyrene.workbench.persistence.project_repository import ProjectPorts, ProjectRepository
from cyrene.workbench.persistence.document_repository import DocumentPorts, DocumentRepository
from cyrene.workbench.persistence.schema import ensure_schema as ensure_schema

logger = logging.getLogger(__name__)


T = TypeVar("T")
# A Workbench document is merged with the latest committed value before every
# write. Serialize only the short read/merge/write transactions inside this
# process. Cross-process contention is handled by SQLite's busy timeout.
_DOCUMENT_WRITE_LOCK = threading.RLock()

























































































def _chat_repository() -> ChatRepository:
    return ChatRepository(ChatPorts(
        document_write_lock=_DOCUMENT_WRITE_LOCK,
        load_row=_load_row,
        write_row=_write_row,
    ))


def _chat_id(chat: Any) -> str:
    return _chat_repository()._chat_id(chat)


def _split_chat(chat: dict[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    return _chat_repository()._split_chat(chat)


def _chat_message_summary(chat: dict[str, Any]) -> dict[str, Any]:
    return _chat_repository()._chat_message_summary(chat)


def _write_chat_row(conn: sqlite3.Connection, chat: dict[str, Any], ordinal: int, *, write_messages: bool=True, previous_messages: list[dict[str, Any]] | None=None) -> None:
    return _chat_repository()._write_chat_row(conn, chat, ordinal, write_messages=write_messages, previous_messages=previous_messages)


def _load_chat_rows(conn: sqlite3.Connection) -> dict[str, dict[str, Any]]:
    return _chat_repository()._load_chat_rows(conn)


def _load_chat_row(conn: sqlite3.Connection, chat_id: str) -> dict[str, Any] | None:
    return _chat_repository()._load_chat_row(conn, chat_id)


def _chat_versions(conn: sqlite3.Connection) -> dict[str, str]:
    return _chat_repository()._chat_versions(conn)


def _chat_shell(ids: list[str], metadata: dict[str, Any] | None=None) -> dict[str, Any]:
    return _chat_repository()._chat_shell(ids, metadata)


def _load_chat_bundle_locked(
    conn: sqlite3.Connection,
    *,
    write_shell: bool,
) -> tuple[dict[str, Any], bool]:
    return _chat_repository()._load_chat_bundle_locked(
        conn,
        write_shell=write_shell,
    )


def _tracked_bundle(value: dict[str, Any], key: str, *, versions: dict[str, str] | None=None) -> TrackedDict:
    return _chat_repository()._tracked_bundle(value, key, versions=versions)


def read_chat_bundle(db_path: str | Path, default_factory: Callable[[], dict[str, Any]]) -> dict[str, Any]:
    return _chat_repository().read_chat_bundle(db_path, default_factory)


def read_chat(db_path: str | Path, chat_id: str, default_factory: Callable[[], dict[str, Any]]) -> dict[str, Any] | None:
    return _chat_repository().read_chat(db_path, chat_id, default_factory)


def read_chat_summaries(db_path: str | Path, default_factory: Callable[[], dict[str, Any]]) -> list[dict[str, Any]]:
    return _chat_repository().read_chat_summaries(db_path, default_factory)


def mutate_chat(db_path: str | Path, chat_id: str, mutation: Callable[[dict[str, Any]], Any], default_factory: Callable[[], dict[str, Any]]) -> dict[str, Any] | None:
    return _chat_repository().mutate_chat(db_path, chat_id, mutation, default_factory)


def write_chat(db_path: str | Path, chat: dict[str, Any], default_factory: Callable[[], dict[str, Any]], *, base_chat: dict[str, Any] | None=None) -> dict[str, Any] | None:
    return _chat_repository().write_chat(db_path, chat, default_factory, base_chat=base_chat)


def _merge_chat_lists(base: list[Any], local: list[Any], remote: list[Any]) -> list[dict[str, Any]]:
    return _chat_repository()._merge_chat_lists(base, local, remote)


def write_chat_bundle(db_path: str | Path, value: dict[str, Any], default_factory: Callable[[], dict[str, Any]], *, base_value: dict[str, Any] | None=None) -> dict[str, Any]:
    return _chat_repository().write_chat_bundle(db_path, value, default_factory, base_value=base_value)









def _project_repository() -> ProjectRepository:
    return ProjectRepository(ProjectPorts(
        document_write_lock=_DOCUMENT_WRITE_LOCK,
        load_row=_load_row,
        write_row=_write_row,
    ))


def _load_task_session_rows(conn: sqlite3.Connection) -> dict[str, dict[str, Any]]:
    return _project_repository()._load_task_session_rows(conn)


def _write_task_session_row(conn: sqlite3.Connection, session: dict[str, Any]) -> None:
    return _project_repository()._write_task_session_row(conn, session)


def summarize_task_session(session: dict[str, Any]) -> dict[str, Any]:
    return _project_repository().summarize_task_session(session)


def _split_project_bundle(payload: dict[str, Any], summarize_session: Callable[[dict[str, Any]], dict[str, Any]]) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    return _project_repository()._split_project_bundle(payload, summarize_session)


def _hydrate_project_bundle(shell: dict[str, Any], session_rows: dict[str, dict[str, Any]]) -> dict[str, Any]:
    return _project_repository()._hydrate_project_bundle(shell, session_rows)


def _load_project_bundle_locked(conn: sqlite3.Connection, default_factory: Callable[[], dict[str, Any]], summarize_session: Callable[[dict[str, Any]], dict[str, Any]]) -> tuple[dict[str, Any], dict[str, Any]]:
    return _project_repository()._load_project_bundle_locked(conn, default_factory, summarize_session)


def read_project_bundle(db_path: str | Path, default_factory: Callable[[], dict[str, Any]], summarize_session: Callable[[dict[str, Any]], dict[str, Any]], *, lightweight: bool=False) -> dict[str, Any]:
    return _project_repository().read_project_bundle(db_path, default_factory, summarize_session, lightweight=lightweight)


def write_project_bundle(db_path: str | Path, value: dict[str, Any], default_factory: Callable[[], dict[str, Any]], summarize_session: Callable[[dict[str, Any]], dict[str, Any]], *, base_value: dict[str, Any] | None=None) -> dict[str, Any]:
    return _project_repository().write_project_bundle(db_path, value, default_factory, summarize_session, base_value=base_value)


def patch_project_bundle_fields(db_path: str | Path, fields: dict[str, Any], default_factory: Callable[[], dict[str, Any]], summarize_session: Callable[[dict[str, Any]], dict[str, Any]]) -> dict[str, Any]:
    return _project_repository().patch_project_bundle_fields(db_path, fields, default_factory, summarize_session)












def _document_repository() -> DocumentRepository:
    return DocumentRepository(DocumentPorts(
        document_write_lock=_DOCUMENT_WRITE_LOCK,
        patch_project_bundle_fields=patch_project_bundle_fields,
        read_chat_bundle=read_chat_bundle,
        read_project_bundle=read_project_bundle,
        summarize_task_session=summarize_task_session,
        write_chat_bundle=write_chat_bundle,
        write_project_bundle=write_project_bundle,
    ))


def _load_row(conn: sqlite3.Connection, key: str) -> Any | None:
    return _document_repository()._load_row(conn, key)


def _initial_value(default_factory: Callable[[], T]) -> T:
    return _document_repository()._initial_value(default_factory)


def _write_row(conn: sqlite3.Connection, key: str, value: Any) -> None:
    return _document_repository()._write_row(conn, key, value)


def read_document(db_path: str | Path, key: str, default_factory: Callable[[], T]) -> T:
    return _document_repository().read_document(db_path, key, default_factory)


def _write_document_locked(db_path: str | Path, key: str, value: T, default_factory: Callable[[], T], *, base_value: Any | None=None) -> T:
    return _document_repository()._write_document_locked(db_path, key, value, default_factory, base_value=base_value)


def write_document(db_path: str | Path, key: str, value: T, default_factory: Callable[[], T], *, base_value: Any | None=None) -> T:
    return _document_repository().write_document(db_path, key, value, default_factory, base_value=base_value)


def patch_document_fields(db_path: str | Path, key: str, fields: dict[str, Any], default_factory: Callable[[], dict[str, Any]]) -> dict[str, Any]:
    return _document_repository().patch_document_fields(db_path, key, fields, default_factory)


def delete_document(db_path: str | Path, key: str) -> None:
    return _document_repository().delete_document(db_path, key)


def list_document_keys(db_path: str | Path, *, prefix: str='') -> list[str]:
    return _document_repository().list_document_keys(db_path, prefix=prefix)


def has_document_data(db_path: str | Path, key: str) -> bool:
    return _document_repository().has_document_data(db_path, key)
