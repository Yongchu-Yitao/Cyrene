"""Transactional SQLite storage for Workbench state.

The original Workbench stores used whole-file JSON read/modify/write cycles.
Atomic rename prevented torn files, but concurrent writers could still replace
each other's completed updates.  This module keeps the existing document-shaped
API while making SQLite the source of truth.

Reads return a tracked top-level dict/list carrying its baseline snapshot.
Writes run under ``BEGIN IMMEDIATE`` and three-way merge the caller's changes
with the latest committed document.  Lists of entities are merged by stable
``id`` so concurrent messages, sessions, notifications, and memories are
preserved.
"""

from __future__ import annotations

import copy
import json
import logging
import sqlite3
import threading
import time
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, TypeVar

from cyrene.runtime.io import atomic_write_json, read_json_safe
from cyrene.workbench.persistence.document_merge import (
    TrackedDict,
    TrackedList,
    baseline,
    entity_id as _entity_id,
    plain as _plain,
    three_way_merge as _three_way_merge,
    tracked as _tracked,
)
from cyrene.workbench.persistence.chat_repository import ChatPorts, ChatRepository
from cyrene.workbench.persistence.project_repository import ProjectPorts, ProjectRepository
from cyrene.workbench.persistence.document_repository import DocumentPorts, DocumentRepository
from cyrene.workbench.persistence.schema import (
    SCHEMA_READY as _SCHEMA_READY,
    connect as _connect,
    ensure_schema,
)

logger = logging.getLogger(__name__)


T = TypeVar("T")
# A Workbench document is merged with the latest committed value before every
# write. Serialize only the short read/merge/write transactions inside this
# process; compatibility-export filesystem I/O happens after releasing the lock.
# Cross-process contention is still handled by SQLite's busy timeout.
_DOCUMENT_WRITE_LOCK = threading.RLock()

























































































def _chat_repository() -> ChatRepository:
    return ChatRepository(ChatPorts(
        document_write_lock=_DOCUMENT_WRITE_LOCK,
        export_current_document=_export_current_document,
        initial_value=_initial_value,
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


def _load_chat_bundle_locked(conn: sqlite3.Connection, default_factory: Callable[[], dict[str, Any]], *, legacy_path: Path | None, migrate: bool) -> tuple[dict[str, Any], bool]:
    return _chat_repository()._load_chat_bundle_locked(conn, default_factory, legacy_path=legacy_path, migrate=migrate)


def _tracked_bundle(value: dict[str, Any], key: str, *, versions: dict[str, str] | None=None) -> TrackedDict:
    return _chat_repository()._tracked_bundle(value, key, versions=versions)


def _schedule_chat_export(db_path: str | Path, export_path: Path) -> None:
    return _chat_repository()._schedule_chat_export(db_path, export_path)


def read_chat_bundle(db_path: str | Path, default_factory: Callable[[], dict[str, Any]], *, legacy_path: Path | None=None) -> dict[str, Any]:
    return _chat_repository().read_chat_bundle(db_path, default_factory, legacy_path=legacy_path)


def read_chat(db_path: str | Path, chat_id: str, default_factory: Callable[[], dict[str, Any]], *, legacy_path: Path | None=None) -> dict[str, Any] | None:
    return _chat_repository().read_chat(db_path, chat_id, default_factory, legacy_path=legacy_path)


def read_chat_summaries(db_path: str | Path, default_factory: Callable[[], dict[str, Any]], *, legacy_path: Path | None=None) -> list[dict[str, Any]]:
    return _chat_repository().read_chat_summaries(db_path, default_factory, legacy_path=legacy_path)


def mutate_chat(db_path: str | Path, chat_id: str, mutation: Callable[[dict[str, Any]], Any], default_factory: Callable[[], dict[str, Any]], *, legacy_path: Path | None=None, export_path: Path | None=None) -> dict[str, Any] | None:
    return _chat_repository().mutate_chat(db_path, chat_id, mutation, default_factory, legacy_path=legacy_path, export_path=export_path)


def write_chat(db_path: str | Path, chat: dict[str, Any], default_factory: Callable[[], dict[str, Any]], *, base_chat: dict[str, Any] | None=None, legacy_path: Path | None=None, export_path: Path | None=None) -> dict[str, Any] | None:
    return _chat_repository().write_chat(db_path, chat, default_factory, base_chat=base_chat, legacy_path=legacy_path, export_path=export_path)


def _merge_chat_lists(base: list[Any], local: list[Any], remote: list[Any]) -> list[dict[str, Any]]:
    return _chat_repository()._merge_chat_lists(base, local, remote)


def write_chat_bundle(db_path: str | Path, value: dict[str, Any], default_factory: Callable[[], dict[str, Any]], *, legacy_path: Path | None=None, export_path: Path | None=None, base_value: dict[str, Any] | None=None) -> dict[str, Any]:
    return _chat_repository().write_chat_bundle(db_path, value, default_factory, legacy_path=legacy_path, export_path=export_path, base_value=base_value)









def _project_repository() -> ProjectRepository:
    return ProjectRepository(ProjectPorts(
        document_write_lock=_DOCUMENT_WRITE_LOCK,
        initial_value=_initial_value,
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


def _load_project_bundle_locked(conn: sqlite3.Connection, default_factory: Callable[[], dict[str, Any]], summarize_session: Callable[[dict[str, Any]], dict[str, Any]], *, legacy_path: Path | None, migrate: bool=True) -> tuple[dict[str, Any], dict[str, Any], bool]:
    return _project_repository()._load_project_bundle_locked(conn, default_factory, summarize_session, legacy_path=legacy_path, migrate=migrate)


def read_project_bundle(db_path: str | Path, default_factory: Callable[[], dict[str, Any]], summarize_session: Callable[[dict[str, Any]], dict[str, Any]], *, legacy_path: Path | None=None, lightweight: bool=False) -> dict[str, Any]:
    return _project_repository().read_project_bundle(db_path, default_factory, summarize_session, legacy_path=legacy_path, lightweight=lightweight)


def write_project_bundle(db_path: str | Path, value: dict[str, Any], default_factory: Callable[[], dict[str, Any]], summarize_session: Callable[[dict[str, Any]], dict[str, Any]], *, legacy_path: Path | None=None, export_path: Path | None=None, base_value: dict[str, Any] | None=None) -> dict[str, Any]:
    return _project_repository().write_project_bundle(db_path, value, default_factory, summarize_session, legacy_path=legacy_path, export_path=export_path, base_value=base_value)


def patch_project_bundle_fields(db_path: str | Path, fields: dict[str, Any], default_factory: Callable[[], dict[str, Any]], summarize_session: Callable[[dict[str, Any]], dict[str, Any]], *, legacy_path: Path | None=None, export_path: Path | None=None) -> dict[str, Any]:
    return _project_repository().patch_project_bundle_fields(db_path, fields, default_factory, summarize_session, legacy_path=legacy_path, export_path=export_path)












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


def _initial_value(default_factory: Callable[[], T], legacy_path: Path | None) -> T:
    return _document_repository()._initial_value(default_factory, legacy_path)


def _write_row(conn: sqlite3.Connection, key: str, value: Any) -> None:
    return _document_repository()._write_row(conn, key, value)


def read_document(db_path: str | Path, key: str, default_factory: Callable[[], T], *, legacy_path: Path | None=None) -> T:
    return _document_repository().read_document(db_path, key, default_factory, legacy_path=legacy_path)


def _write_document_locked(db_path: str | Path, key: str, value: T, default_factory: Callable[[], T], *, legacy_path: Path | None=None, base_value: Any | None=None) -> T:
    return _document_repository()._write_document_locked(db_path, key, value, default_factory, legacy_path=legacy_path, base_value=base_value)


def write_document(db_path: str | Path, key: str, value: T, default_factory: Callable[[], T], *, legacy_path: Path | None=None, export_path: Path | None=None, base_value: Any | None=None) -> T:
    return _document_repository().write_document(db_path, key, value, default_factory, legacy_path=legacy_path, export_path=export_path, base_value=base_value)


def patch_document_fields(db_path: str | Path, key: str, fields: dict[str, Any], default_factory: Callable[[], dict[str, Any]], *, legacy_path: Path | None=None, export_path: Path | None=None) -> dict[str, Any]:
    return _document_repository().patch_document_fields(db_path, key, fields, default_factory, legacy_path=legacy_path, export_path=export_path)


def _export_current_document(db_path: str | Path, key: str, export_path: Path) -> None:
    return _document_repository()._export_current_document(db_path, key, export_path)


def delete_document(db_path: str | Path, key: str, *, export_path: Path | None=None) -> None:
    return _document_repository().delete_document(db_path, key, export_path=export_path)


def list_document_keys(db_path: str | Path, *, prefix: str='') -> list[str]:
    return _document_repository().list_document_keys(db_path, prefix=prefix)


def has_document_data(db_path: str | Path, key: str) -> bool:
    return _document_repository().has_document_data(db_path, key)
