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

logger = logging.getLogger(__name__)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS workbench_state (
    key TEXT PRIMARY KEY,
    payload_json TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS workbench_task_sessions (
    session_id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_workbench_task_sessions_project
    ON workbench_task_sessions(project_id, updated_at DESC);
CREATE TABLE IF NOT EXISTS workbench_chats (
    chat_id TEXT PRIMARY KEY,
    ordinal INTEGER NOT NULL,
    payload_json TEXT NOT NULL,
    summary_json TEXT NOT NULL DEFAULT '{}',
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_workbench_chats_ordinal
    ON workbench_chats(ordinal);
CREATE TABLE IF NOT EXISTS workbench_chat_messages (
    chat_id TEXT NOT NULL,
    ordinal INTEGER NOT NULL,
    message_id TEXT NOT NULL DEFAULT '',
    payload_json TEXT NOT NULL,
    PRIMARY KEY(chat_id, ordinal)
);
CREATE INDEX IF NOT EXISTS idx_workbench_chat_messages_id
    ON workbench_chat_messages(chat_id, message_id);
CREATE TABLE IF NOT EXISTS workbench_chat_change_sets (
    change_set_id TEXT PRIMARY KEY,
    chat_id TEXT NOT NULL,
    completed_at TEXT NOT NULL DEFAULT '',
    diff_chars INTEGER NOT NULL DEFAULT 0,
    payload_json TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_workbench_chat_change_sets_chat
    ON workbench_chat_change_sets(chat_id, completed_at DESC);
CREATE TABLE IF NOT EXISTS workbench_chat_change_files (
    change_set_id TEXT NOT NULL,
    ordinal INTEGER NOT NULL,
    path TEXT NOT NULL DEFAULT '',
    payload_json TEXT NOT NULL,
    diff_text TEXT NOT NULL DEFAULT '',
    PRIMARY KEY(change_set_id, ordinal)
);
CREATE INDEX IF NOT EXISTS idx_workbench_chat_change_files_path
    ON workbench_chat_change_files(change_set_id, path);
"""

_MISSING = object()
T = TypeVar("T")
_SCHEMA_LOCK = threading.Lock()
_SCHEMA_READY: set[str] = set()
# A Workbench document is merged with the latest committed value before every
# write. Serialize only the short read/merge/write transactions inside this
# process; compatibility-export filesystem I/O happens after releasing the lock.
# Cross-process contention is still handled by SQLite's busy timeout.
_DOCUMENT_WRITE_LOCK = threading.RLock()
_DEFERRED_CHAT_EXPORT_LOCK = threading.Lock()
_DEFERRED_CHAT_EXPORT_PENDING: set[tuple[str, str]] = set()
_DEFERRED_CHAT_EXPORT_RUNNING: set[tuple[str, str]] = set()
_COUNTER_FIELDS = {
    "mention_count",
    "planRevision",
    "planDefinitionRevision",
    "citation_count",
}


class TrackedDict(dict):
    """A normal dict with an out-of-band baseline used for three-way merges."""

    _workbench_base: Any
    _workbench_key: str


class TrackedList(list):
    """A normal list with an out-of-band baseline used for three-way merges."""

    _workbench_base: Any
    _workbench_key: str


def ensure_schema(db_path: str | Path) -> None:
    path = Path(db_path).expanduser().resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    cache_key = str(path)
    if cache_key in _SCHEMA_READY and path.exists():
        return
    with _SCHEMA_LOCK:
        if cache_key in _SCHEMA_READY and path.exists():
            return
        for attempt, delay in enumerate((0.0, 0.05, 0.1, 0.2, 0.4, 0.8)):
            if delay:
                time.sleep(delay)
            try:
                with sqlite3.connect(path, timeout=5) as conn:
                    conn.execute("PRAGMA busy_timeout = 5000")
                    conn.execute("PRAGMA journal_mode = WAL")
                    conn.execute("PRAGMA synchronous = NORMAL")
                    conn.executescript(_SCHEMA)
                    chat_columns = {
                        str(row[1])
                        for row in conn.execute("PRAGMA table_info(workbench_chats)")
                    }
                    if "summary_json" not in chat_columns:
                        conn.execute(
                            "ALTER TABLE workbench_chats "
                            "ADD COLUMN summary_json TEXT NOT NULL DEFAULT '{}'"
                        )
                    conn.commit()
                break
            except sqlite3.OperationalError as exc:
                if "locked" not in str(exc).lower() or attempt == 5:
                    raise
        _SCHEMA_READY.add(cache_key)


def _connect(db_path: str | Path) -> sqlite3.Connection:
    ensure_schema(db_path)
    conn = sqlite3.connect(str(db_path), timeout=30)
    conn.execute("PRAGMA busy_timeout = 30000")
    return conn


def _plain(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _plain(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_plain(item) for item in value]
    return copy.deepcopy(value)


def _tracked(value: T, key: str) -> T:
    baseline = _plain(value)
    if isinstance(value, dict):
        out = TrackedDict(_plain(value))
    elif isinstance(value, list):
        out = TrackedList(_plain(value))
    else:
        return value
    out._workbench_base = baseline
    out._workbench_key = key
    return out  # type: ignore[return-value]


def baseline(value: Any) -> Any | None:
    raw = getattr(value, "_workbench_base", None)
    return _plain(raw) if raw is not None else None


def _load_row(conn: sqlite3.Connection, key: str) -> Any | None:
    row = conn.execute(
        "SELECT payload_json FROM workbench_state WHERE key = ?",
        (key,),
    ).fetchone()
    if row is None:
        return None
    try:
        return json.loads(str(row[0]))
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"invalid Workbench SQLite payload for {key}") from exc


def _load_task_session_rows(conn: sqlite3.Connection) -> dict[str, dict[str, Any]]:
    rows = conn.execute(
        "SELECT session_id, payload_json FROM workbench_task_sessions"
    ).fetchall()
    result: dict[str, dict[str, Any]] = {}
    for session_id, payload_json in rows:
        try:
            payload = json.loads(str(payload_json))
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            raise RuntimeError(
                f"invalid Workbench task-session payload for {session_id}"
            ) from exc
        if isinstance(payload, dict):
            result[str(session_id)] = payload
    return result


def _write_task_session_row(
    conn: sqlite3.Connection,
    session: dict[str, Any],
) -> None:
    session_id = _entity_id(session)
    if not session_id:
        raise ValueError("Workbench task session is missing id")
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        """
        INSERT INTO workbench_task_sessions(
            session_id, project_id, payload_json, updated_at
        ) VALUES (?, ?, ?, ?)
        ON CONFLICT(session_id) DO UPDATE SET
            project_id = excluded.project_id,
            payload_json = excluded.payload_json,
            updated_at = excluded.updated_at
        """,
        (
            session_id,
            str(session.get("projectId") or ""),
            json.dumps(_plain(session), ensure_ascii=False),
            now,
        ),
    )


_TASK_SESSION_SUMMARY_FIELDS = (
    "id",
    "projectId",
    "kind",
    "title",
    "goal",
    "status",
    "priority",
    "createdAt",
    "updatedAt",
    "summary",
    "titleLocked",
)


def summarize_task_session(session: dict[str, Any]) -> dict[str, Any]:
    """Project-index projection for one independently stored task session."""
    summary = {
        field: _plain(session.get(field))
        for field in _TASK_SESSION_SUMMARY_FIELDS
        if field in session
    }
    summary["id"] = str(summary.get("id") or session.get("id") or "")
    summary["projectId"] = str(
        summary.get("projectId") or session.get("projectId") or ""
    )
    summary["isSummary"] = True
    plan = session.get("plan") if isinstance(session.get("plan"), list) else []
    summary["planStepCount"] = len(plan)
    resolved_statuses = {"completed", "done", "skipped"}
    summary["planCompletedCount"] = sum(
        1
        for step in plan
        if isinstance(step, dict)
        and str(step.get("status") or "pending") in resolved_statuses
    )
    current_step: dict[str, Any] | None = next(
        (
            step
            for step in plan
            if isinstance(step, dict)
            and str(step.get("status") or "pending") == "running"
        ),
        None,
    )
    if current_step is None:
        current_step = next(
            (
                step
                for step in plan
                if isinstance(step, dict)
                and str(step.get("status") or "pending") not in resolved_statuses
            ),
            None,
        )
    if current_step is not None:
        summary["planCurrentIndex"] = plan.index(current_step) + 1
        summary["planCurrentTitle"] = str(current_step.get("title") or "")
        summary["planCurrentAction"] = str(current_step.get("currentAction") or "")
    summary["eventCount"] = len(session.get("events") or []) if isinstance(
        session.get("events"), list
    ) else 0
    summary["runCount"] = len(session.get("runs") or []) if isinstance(
        session.get("runs"), list
    ) else 0
    summary["artifactCount"] = len(session.get("artifacts") or []) if isinstance(
        session.get("artifacts"), list
    ) else 0
    return summary


def _split_project_bundle(
    payload: dict[str, Any],
    summarize_session: Callable[[dict[str, Any]], dict[str, Any]],
) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    """Return the lightweight project index and independently stored sessions."""
    shell = _plain(payload)
    sessions: dict[str, dict[str, Any]] = {}
    shell_projects: list[dict[str, Any]] = []
    for raw_project in payload.get("projects") or []:
        if not isinstance(raw_project, dict):
            continue
        project = _plain(raw_project)
        summaries: list[dict[str, Any]] = []
        for raw_session in raw_project.get("sessions") or []:
            if not isinstance(raw_session, dict):
                continue
            session = _plain(raw_session)
            session_id = _entity_id(session)
            if not session_id:
                continue
            sessions[session_id] = session
            summaries.append(_plain(summarize_session(session)))
        project["sessions"] = summaries
        shell_projects.append(project)
    shell["projects"] = shell_projects
    return shell, sessions


def _hydrate_project_bundle(
    shell: dict[str, Any],
    session_rows: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    payload = _plain(shell)
    projects: list[dict[str, Any]] = []
    for raw_project in shell.get("projects") or []:
        if not isinstance(raw_project, dict):
            continue
        project = _plain(raw_project)
        sessions: list[dict[str, Any]] = []
        for reference in raw_project.get("sessions") or []:
            if not isinstance(reference, dict):
                continue
            session_id = _entity_id(reference)
            if not session_id:
                continue
            # During one-time migration the project document still contains the
            # complete legacy session.  Use it when no normalized row exists.
            sessions.append(_plain(session_rows.get(session_id, reference)))
        project["sessions"] = sessions
        projects.append(project)
    payload["projects"] = projects
    return payload


def _load_project_bundle_locked(
    conn: sqlite3.Connection,
    default_factory: Callable[[], dict[str, Any]],
    summarize_session: Callable[[dict[str, Any]], dict[str, Any]],
    *,
    legacy_path: Path | None,
    migrate: bool = True,
) -> tuple[dict[str, Any], dict[str, Any], bool]:
    """Load and, when necessary, atomically normalize the legacy project row."""
    stored_raw = _load_row(conn, "projects")
    raw = stored_raw
    if raw is None:
        raw = _initial_value(default_factory, legacy_path)
    if not isinstance(raw, dict) or not isinstance(raw.get("projects"), list):
        raw = default_factory()

    session_rows = _load_task_session_rows(conn)
    full = _hydrate_project_bundle(raw, session_rows)
    shell, sessions = _split_project_bundle(full, summarize_session)

    migrated = False
    for session_id, session in sessions.items():
        if session_id not in session_rows:
            if migrate:
                _write_task_session_row(conn, session)
            migrated = True
    if raw != shell or stored_raw is None:
        if migrate:
            _write_row(conn, "projects", shell)
        migrated = True
    if migrated:
        # Callers own the surrounding transaction; this flag exists only to
        # make the migration intent explicit.
        pass
    return shell, full, migrated


def _chat_id(chat: Any) -> str:
    if not isinstance(chat, dict):
        return ""
    return str(chat.get("id") or "").strip()


def _split_chat(chat: dict[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    payload = {str(key): _plain(value) for key, value in chat.items() if key != "messages"}
    messages = [
        _plain(message)
        for message in chat.get("messages") or []
        if isinstance(message, dict)
    ]
    payload["id"] = _chat_id(chat)
    return payload, messages


_CHAT_USAGE_KEYS = (
    "prompt_tokens",
    "completion_tokens",
    "total_tokens",
    "prompt_cache_hit_tokens",
    "prompt_cache_miss_tokens",
)


def _chat_message_summary(chat: dict[str, Any]) -> dict[str, Any]:
    messages = [item for item in chat.get("messages") or [] if isinstance(item, dict)]
    usage = {key: 0 for key in _CHAT_USAGE_KEYS}
    preview = ""
    first_message = ""
    first_fallback = ""
    completed_turn_count = 0
    for message in messages:
        content = str(message.get("content") or "").strip()
        if content and not first_fallback:
            first_fallback = content.replace("\n", " ")[:80]
        if content and not first_message and str(message.get("role") or "") == "user":
            first_message = content.replace("\n", " ")[:80]
        if (
            str(message.get("role") or "") == "assistant"
            and "processingDurationMs" in message
            and not bool(message.get("systemInitiated"))
        ):
            completed_turn_count += 1
        raw_usage = message.get("usage")
        if isinstance(raw_usage, dict):
            for key in _CHAT_USAGE_KEYS:
                try:
                    usage[key] += int(raw_usage.get(key) or 0)
                except (TypeError, ValueError):
                    pass
    for message in reversed(messages):
        content = str(message.get("content") or "").strip()
        if content:
            preview = content.replace("\n", " ")[:80]
            break
    if not usage["total_tokens"]:
        usage["total_tokens"] = usage["prompt_tokens"] + usage["completion_tokens"]
    stored_turn_count = chat.get("completedTurnCount")
    if isinstance(stored_turn_count, int) and not isinstance(stored_turn_count, bool):
        completed_turn_count = max(0, stored_turn_count)
    return {
        "messageCount": len(messages),
        "preview": preview,
        "firstMessage": first_message or first_fallback,
        "completedTurnCount": completed_turn_count,
        "usage": usage,
    }


def _write_chat_row(
    conn: sqlite3.Connection,
    chat: dict[str, Any],
    ordinal: int,
    *,
    write_messages: bool = True,
    previous_messages: list[dict[str, Any]] | None = None,
) -> None:
    chat_id = _chat_id(chat)
    if not chat_id:
        raise ValueError("Workbench chat is missing id")
    payload = {
        str(key): _plain(value)
        for key, value in chat.items()
        if key != "messages"
    }
    payload["id"] = chat_id
    messages = (
        [
            _plain(message)
            for message in chat.get("messages") or []
            if isinstance(message, dict)
        ]
        if write_messages
        else []
    )
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        """
        INSERT INTO workbench_chats(
            chat_id, ordinal, payload_json, summary_json, updated_at
        ) VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(chat_id) DO UPDATE SET
            ordinal = excluded.ordinal,
            payload_json = excluded.payload_json,
            summary_json = excluded.summary_json,
            updated_at = excluded.updated_at
        """,
        (
            chat_id,
            int(ordinal),
            json.dumps(payload, ensure_ascii=False),
            json.dumps(_chat_message_summary(chat), ensure_ascii=False),
            now,
        ),
    )
    if not write_messages:
        return
    prefix = 0
    if previous_messages is not None:
        limit = min(len(previous_messages), len(messages))
        while prefix < limit and previous_messages[prefix] == messages[prefix]:
            prefix += 1
    conn.execute(
        "DELETE FROM workbench_chat_messages WHERE chat_id = ? AND ordinal >= ?",
        (chat_id, prefix),
    )
    tail = messages[prefix:]
    if tail:
        conn.executemany(
            """
            INSERT INTO workbench_chat_messages(
                chat_id, ordinal, message_id, payload_json
            ) VALUES (?, ?, ?, ?)
            """,
            [
                (
                    chat_id,
                    index,
                    str(message.get("id") or message.get("message_id") or ""),
                    json.dumps(message, ensure_ascii=False),
                )
                for index, message in enumerate(tail, start=prefix)
            ],
        )


def _load_chat_rows(conn: sqlite3.Connection) -> dict[str, dict[str, Any]]:
    chat_rows = conn.execute(
        "SELECT chat_id, payload_json FROM workbench_chats ORDER BY ordinal, chat_id"
    ).fetchall()
    messages: dict[str, list[dict[str, Any]]] = {}
    for chat_id, payload_json in conn.execute(
        """
        SELECT chat_id, payload_json
        FROM workbench_chat_messages
        ORDER BY chat_id, ordinal
        """
    ).fetchall():
        try:
            message = json.loads(str(payload_json))
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            raise RuntimeError(
                f"invalid Workbench chat message payload for {chat_id}"
            ) from exc
        if isinstance(message, dict):
            messages.setdefault(str(chat_id), []).append(message)

    result: dict[str, dict[str, Any]] = {}
    for chat_id, payload_json in chat_rows:
        try:
            chat = json.loads(str(payload_json))
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            raise RuntimeError(f"invalid Workbench chat payload for {chat_id}") from exc
        if not isinstance(chat, dict):
            continue
        normalized_id = str(chat_id)
        chat["id"] = normalized_id
        chat["messages"] = messages.get(normalized_id, [])
        result[normalized_id] = chat
    return result


def _load_chat_row(
    conn: sqlite3.Connection,
    chat_id: str,
) -> dict[str, Any] | None:
    row = conn.execute(
        "SELECT payload_json FROM workbench_chats WHERE chat_id = ?",
        (chat_id,),
    ).fetchone()
    if row is None:
        return None
    chat = json.loads(str(row[0]))
    if not isinstance(chat, dict):
        return None
    chat["id"] = chat_id
    chat["messages"] = [
        message
        for (payload_json,) in conn.execute(
            """
            SELECT payload_json FROM workbench_chat_messages
            WHERE chat_id = ? ORDER BY ordinal
            """,
            (chat_id,),
        ).fetchall()
        if isinstance((message := json.loads(str(payload_json))), dict)
    ]
    return chat


def _chat_versions(conn: sqlite3.Connection) -> dict[str, str]:
    return {
        str(chat_id): str(updated_at)
        for chat_id, updated_at in conn.execute(
            "SELECT chat_id, updated_at FROM workbench_chats"
        ).fetchall()
    }


def _chat_shell(
    ids: list[str],
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    shell = {
        str(key): _plain(value)
        for key, value in (metadata or {}).items()
        if key not in {"chats", "chatIds", "normalizedVersion"}
    }
    shell.update({"normalizedVersion": 1, "chatIds": list(ids)})
    return shell


def _load_chat_bundle_locked(
    conn: sqlite3.Connection,
    default_factory: Callable[[], dict[str, Any]],
    *,
    legacy_path: Path | None,
    migrate: bool,
) -> tuple[dict[str, Any], bool]:
    stored = _load_row(conn, "chats")
    rows = _load_chat_rows(conn)
    migrated = False
    source_metadata = stored if isinstance(stored, dict) else {}

    legacy_chats = (
        stored.get("chats")
        if isinstance(stored, dict) and isinstance(stored.get("chats"), list)
        else None
    )
    if legacy_chats is None and not rows and stored is None:
        initial = _initial_value(default_factory, legacy_path)
        source_metadata = initial if isinstance(initial, dict) else {}
        legacy_chats = (
            initial.get("chats")
            if isinstance(initial, dict) and isinstance(initial.get("chats"), list)
            else []
        )

    if legacy_chats is not None:
        ordered = [chat for chat in legacy_chats if isinstance(chat, dict) and _chat_id(chat)]
        metadata = source_metadata
        if migrate:
            for index, chat in enumerate(ordered):
                _write_chat_row(conn, chat, index)
            _write_row(
                conn,
                "chats",
                _chat_shell([_chat_id(chat) for chat in ordered], metadata),
            )
            rows = {_chat_id(chat): _plain(chat) for chat in ordered}
        migrated = True
        value = {
            str(key): _plain(item)
            for key, item in metadata.items()
            if key != "chats"
        }
        value["chats"] = [_plain(chat) for chat in ordered]
        return value, migrated

    ids = [
        str(chat_id)
        for chat_id in (stored or {}).get("chatIds") or []
        if str(chat_id) in rows
    ]
    ids.extend(chat_id for chat_id in rows if chat_id not in ids)
    expected_shell = _chat_shell(ids, stored if isinstance(stored, dict) else None)
    if stored != expected_shell:
        if migrate:
            _write_row(conn, "chats", expected_shell)
        migrated = True
    value = {
        str(key): _plain(item)
        for key, item in expected_shell.items()
        if key not in {"chatIds", "normalizedVersion"}
    }
    value["chats"] = [rows[chat_id] for chat_id in ids]
    return value, migrated


def _tracked_bundle(
    value: dict[str, Any],
    key: str,
    *,
    versions: dict[str, str] | None = None,
) -> TrackedDict:
    """Track a hydrated normalized bundle with one defensive baseline copy."""
    out = TrackedDict(value)
    out._workbench_base = _plain(value)
    out._workbench_key = key
    if versions is not None:
        out._workbench_versions = dict(versions)
    return out


def _schedule_chat_export(db_path: str | Path, export_path: Path) -> None:
    """Coalesce large compatibility mirrors outside the request hot path."""
    target = (str(Path(db_path).expanduser().resolve()), str(export_path.resolve()))
    with _DEFERRED_CHAT_EXPORT_LOCK:
        _DEFERRED_CHAT_EXPORT_PENDING.add(target)
        if target in _DEFERRED_CHAT_EXPORT_RUNNING:
            return
        _DEFERRED_CHAT_EXPORT_RUNNING.add(target)

    def export_latest() -> None:
        while True:
            with _DEFERRED_CHAT_EXPORT_LOCK:
                _DEFERRED_CHAT_EXPORT_PENDING.discard(target)
            try:
                _export_current_document(Path(target[0]), "chats", Path(target[1]))
            except Exception:
                logger.exception(
                    "Failed to export deferred Workbench chats mirror to %s",
                    target[1],
                )
            with _DEFERRED_CHAT_EXPORT_LOCK:
                if target in _DEFERRED_CHAT_EXPORT_PENDING:
                    continue
                _DEFERRED_CHAT_EXPORT_RUNNING.discard(target)
                return

    threading.Thread(
        target=export_latest,
        name="workbench-chat-export",
        daemon=True,
    ).start()


def _initial_value(
    default_factory: Callable[[], T],
    legacy_path: Path | None,
) -> T:
    if legacy_path is not None:
        legacy = read_json_safe(legacy_path)
        if legacy is not None:
            return legacy
    return default_factory()


def _write_row(conn: sqlite3.Connection, key: str, value: Any) -> None:
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        """
        INSERT INTO workbench_state (key, payload_json, updated_at)
        VALUES (?, ?, ?)
        ON CONFLICT(key) DO UPDATE SET
            payload_json = excluded.payload_json,
            updated_at = excluded.updated_at
        """,
        (key, json.dumps(_plain(value), ensure_ascii=False), now),
    )


def read_document(
    db_path: str | Path,
    key: str,
    default_factory: Callable[[], T],
    *,
    legacy_path: Path | None = None,
) -> T:
    """Read one document, importing its legacy JSON file exactly once."""
    if key == "chats":
        return read_chat_bundle(  # type: ignore[return-value]
            db_path,
            default_factory,  # type: ignore[arg-type]
            legacy_path=legacy_path,
        )
    if key == "projects":
        # Preserve the historical document API for extensions/tests while the
        # authoritative representation is normalized behind it.
        return read_project_bundle(  # type: ignore[return-value]
            db_path,
            default_factory,  # type: ignore[arg-type]
            summarize_task_session,
            legacy_path=legacy_path,
        )
    conn = _connect(db_path)
    try:
        value = _load_row(conn, key)
        if value is None:
            conn.execute("BEGIN IMMEDIATE")
            value = _load_row(conn, key)
            if value is None:
                value = _initial_value(default_factory, legacy_path)
                _write_row(conn, key, value)
            conn.commit()
        return _tracked(value, key)
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _entity_id(value: Any) -> str:
    if not isinstance(value, dict):
        return ""
    raw = value.get("id")
    return str(raw).strip() if raw is not None else ""


def _merge_entity_list(base: list[Any], local: list[Any], remote: list[Any], path: tuple[str, ...]) -> list[Any]:
    base_by_id = {_entity_id(item): item for item in base if _entity_id(item)}
    local_by_id = {_entity_id(item): item for item in local if _entity_id(item)}
    remote_by_id = {_entity_id(item): item for item in remote if _entity_id(item)}

    # Keep the caller's ordering, then include entities committed since its read.
    order: list[str] = []
    ordered_ids: set[str] = set()
    for source in (local, remote):
        for item in source:
            item_id = _entity_id(item)
            if item_id and item_id not in ordered_ids:
                order.append(item_id)
                ordered_ids.add(item_id)

    merged: list[Any] = []
    for item_id in order:
        base_item = base_by_id.get(item_id, _MISSING)
        local_item = local_by_id.get(item_id, _MISSING)
        remote_item = remote_by_id.get(item_id, _MISSING)
        value = _three_way_merge(base_item, local_item, remote_item, path + (item_id,))
        if value is not _MISSING:
            merged.append(value)

    # Preserve unkeyed legacy values.  They are uncommon, but dropping them
    # during the migration would be worse than retaining a duplicate.
    for item in local:
        if not _entity_id(item) and item not in merged:
            merged.append(_plain(item))
    for item in remote:
        if not _entity_id(item) and item not in merged:
            merged.append(_plain(item))
    collection = path[-1] if path else ""
    if collection in {"projects", "sessions", "chats", "items"}:
        merged.sort(
            key=lambda item: str(item.get("createdAt") or "") if isinstance(item, dict) else "",
            reverse=True,
        )
    elif collection in {"messages", "events", "runs"}:
        merged.sort(
            key=lambda item: str(
                item.get("createdAt") or item.get("startedAt") or ""
            ) if isinstance(item, dict) else "",
        )
    return merged


def _merge_plain_list(base: list[Any], local: list[Any], remote: list[Any]) -> list[Any]:
    if local == base:
        return _plain(remote)
    if remote == base:
        return _plain(local)
    if local == remote:
        return _plain(local)

    # Concurrent append/set-like edits: retain the caller's order and append
    # remote-only values. Explicit local removals of baseline values stay removed.
    result = _plain(local)
    for item in remote:
        if item in base and item not in local:
            continue
        if item not in result:
            result.append(_plain(item))
    return result


def _three_way_merge(base: Any, local: Any, remote: Any, path: tuple[str, ...] = ()) -> Any:
    if local is _MISSING:
        if base is _MISSING:
            return _plain(remote)
        # An explicit local deletion wins over a concurrent edit of the same key.
        return _MISSING
    if remote is _MISSING:
        if base is _MISSING:
            return _plain(local)
        # A deletion committed after this caller's baseline must win over the
        # caller's stale edits. Preserving the edited entity here resurrects
        # tasks/chats that another request explicitly deleted.
        return _MISSING
    if base is _MISSING:
        if local == remote:
            return _plain(local)
        if isinstance(local, dict) and isinstance(remote, dict):
            base = {}
        elif isinstance(local, list) and isinstance(remote, list):
            base = []
        else:
            return _plain(local)

    if local == base:
        return _plain(remote)
    if remote == base:
        return _plain(local)

    if isinstance(base, dict) and isinstance(local, dict) and isinstance(remote, dict):
        result: dict[str, Any] = {}
        keys = set(base) | set(local) | set(remote)
        for key in keys:
            value = _three_way_merge(
                base.get(key, _MISSING),
                local.get(key, _MISSING),
                remote.get(key, _MISSING),
                path + (str(key),),
            )
            if value is not _MISSING:
                result[str(key)] = value
        return result

    if isinstance(base, list) and isinstance(local, list) and isinstance(remote, list):
        keyed = any(_entity_id(item) for item in base + local + remote)
        if keyed and all(not isinstance(item, dict) or _entity_id(item) for item in base + local + remote):
            return _merge_entity_list(base, local, remote, path)
        return _merge_plain_list(base, local, remote)

    # Counters and revisions are merged by delta, avoiding lost increments.
    if (
        path
        and path[-1] in _COUNTER_FIELDS
        and isinstance(base, (int, float))
        and not isinstance(base, bool)
        and isinstance(local, (int, float))
        and not isinstance(local, bool)
        and isinstance(remote, (int, float))
        and not isinstance(remote, bool)
    ):
        return remote + (local - base)

    if local == remote:
        return _plain(local)

    # Both writers changed the same scalar. The later commit applies its value.
    return _plain(local)


def _write_document_locked(
    db_path: str | Path,
    key: str,
    value: T,
    default_factory: Callable[[], T],
    *,
    legacy_path: Path | None = None,
    base_value: Any | None = None,
) -> T:
    """Merge and commit a document in one SQLite write transaction."""
    local = _plain(value)
    base = _plain(base_value) if base_value is not None else baseline(value)
    conn = _connect(db_path)
    try:
        conn.execute("BEGIN IMMEDIATE")
        remote = _load_row(conn, key)
        if remote is None:
            remote = _initial_value(default_factory, legacy_path)
        merged = local if base is None else _three_way_merge(base, local, remote)
        _write_row(conn, key, merged)
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

    return _tracked(merged, key)


def write_document(
    db_path: str | Path,
    key: str,
    value: T,
    default_factory: Callable[[], T],
    *,
    legacy_path: Path | None = None,
    export_path: Path | None = None,
    base_value: Any | None = None,
) -> T:
    """Merge and commit one document without racing another local writer."""
    if key == "chats":
        return write_chat_bundle(  # type: ignore[return-value]
            db_path,
            value,  # type: ignore[arg-type]
            default_factory,  # type: ignore[arg-type]
            legacy_path=legacy_path,
            export_path=export_path,
            base_value=base_value,  # type: ignore[arg-type]
        )
    if key == "projects":
        return write_project_bundle(  # type: ignore[return-value]
            db_path,
            value,  # type: ignore[arg-type]
            default_factory,  # type: ignore[arg-type]
            summarize_task_session,
            legacy_path=legacy_path,
            export_path=export_path,
            base_value=base_value,
        )
    with _DOCUMENT_WRITE_LOCK:
        result = _write_document_locked(
            db_path,
            key,
            value,
            default_factory,
            legacy_path=legacy_path,
            base_value=base_value,
        )
    # The JSON file is a compatibility mirror, never a runtime read source.
    # Do not make unrelated document commits wait for full-file serialization.
    if export_path is not None:
        try:
            _export_current_document(db_path, key, export_path)
        except Exception:
            logger.exception("Failed to export Workbench document %s to %s", key, export_path)
    return result


def read_chat_bundle(
    db_path: str | Path,
    default_factory: Callable[[], dict[str, Any]],
    *,
    legacy_path: Path | None = None,
) -> dict[str, Any]:
    """Hydrate the compatibility ``{"chats": [...]}`` shape from row storage."""
    conn = _connect(db_path)
    try:
        conn.execute("BEGIN")
        value, migration_required = _load_chat_bundle_locked(
            conn,
            default_factory,
            legacy_path=legacy_path,
            migrate=False,
        )
        conn.commit()
        if migration_required:
            conn.execute("BEGIN IMMEDIATE")
            value, _ = _load_chat_bundle_locked(
                conn,
                default_factory,
                legacy_path=legacy_path,
                migrate=True,
            )
            conn.commit()
        versions = _chat_versions(conn)
        return _tracked_bundle(value, "chats", versions=versions)
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def read_chat(
    db_path: str | Path,
    chat_id: str,
    default_factory: Callable[[], dict[str, Any]],
    *,
    legacy_path: Path | None = None,
) -> dict[str, Any] | None:
    """Read one normalized chat without decoding every conversation."""
    target = str(chat_id or "").strip()
    if not target:
        return None
    conn = _connect(db_path)
    try:
        row = conn.execute(
            "SELECT payload_json FROM workbench_chats WHERE chat_id = ?",
            (target,),
        ).fetchone()
        if row is None:
            state_row = conn.execute(
                "SELECT payload_json FROM workbench_state WHERE key = 'chats'"
            ).fetchone()
            needs_migration = state_row is None
            if state_row is not None:
                try:
                    stored = json.loads(str(state_row[0]))
                except (TypeError, ValueError, json.JSONDecodeError):
                    stored = None
                needs_migration = isinstance(stored, dict) and isinstance(
                    stored.get("chats"), list
                )
            if needs_migration:
                conn.close()
                read_chat_bundle(db_path, default_factory, legacy_path=legacy_path)
                return read_chat(
                    db_path,
                    target,
                    default_factory,
                    legacy_path=legacy_path,
                )
        if row is None:
            return None
        chat = json.loads(str(row[0]))
        if not isinstance(chat, dict):
            return None
        chat["id"] = target
        messages: list[dict[str, Any]] = []
        for (payload_json,) in conn.execute(
            """
            SELECT payload_json FROM workbench_chat_messages
            WHERE chat_id = ? ORDER BY ordinal
            """,
            (target,),
        ).fetchall():
            message = json.loads(str(payload_json))
            if isinstance(message, dict):
                messages.append(message)
        chat["messages"] = messages
        return chat
    finally:
        conn.close()


def read_chat_summaries(
    db_path: str | Path,
    default_factory: Callable[[], dict[str, Any]],
    *,
    legacy_path: Path | None = None,
) -> list[dict[str, Any]]:
    """Read chat-list projections without decoding transcript rows."""
    conn = _connect(db_path)
    try:
        rows = conn.execute(
            """
            SELECT chat_id, payload_json, summary_json
            FROM workbench_chats ORDER BY ordinal, chat_id
            """
        ).fetchall()
        if not rows:
            state = _load_row(conn, "chats")
            if not isinstance(state, dict) or isinstance(state.get("chats"), list):
                conn.close()
                read_chat_bundle(db_path, default_factory, legacy_path=legacy_path)
                return read_chat_summaries(
                    db_path,
                    default_factory,
                    legacy_path=legacy_path,
                )

        result: list[dict[str, Any]] = []
        missing: list[tuple[str, dict[str, Any]]] = []
        for chat_id, payload_json, summary_json in rows:
            chat = json.loads(str(payload_json))
            if not isinstance(chat, dict):
                continue
            chat["id"] = str(chat_id)
            try:
                summary = json.loads(str(summary_json or "{}"))
            except (TypeError, ValueError, json.JSONDecodeError):
                summary = {}
            if not isinstance(summary, dict) or "messageCount" not in summary:
                full_chat = _load_chat_row(conn, str(chat_id))
                summary = _chat_message_summary(full_chat or chat)
                missing.append((str(chat_id), summary))
            chat["_messageProjection"] = summary
            result.append(chat)

        if missing:
            conn.execute("BEGIN IMMEDIATE")
            conn.executemany(
                "UPDATE workbench_chats SET summary_json = ? WHERE chat_id = ?",
                [
                    (json.dumps(summary, ensure_ascii=False), chat_id)
                    for chat_id, summary in missing
                ],
            )
            conn.commit()
        return result
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def mutate_chat(
    db_path: str | Path,
    chat_id: str,
    mutation: Callable[[dict[str, Any]], Any],
    default_factory: Callable[[], dict[str, Any]],
    *,
    legacy_path: Path | None = None,
    export_path: Path | None = None,
) -> dict[str, Any] | None:
    """Mutate one chat atomically without hydrating sibling transcripts."""
    target = str(chat_id or "").strip()
    if not target:
        return None
    total_message_count = 0
    with _DOCUMENT_WRITE_LOCK:
        conn = _connect(db_path)
        try:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT ordinal FROM workbench_chats WHERE chat_id = ?",
                (target,),
            ).fetchone()
            if row is None:
                state = _load_row(conn, "chats")
                needs_migration = state is None or (
                    isinstance(state, dict) and isinstance(state.get("chats"), list)
                )
                if not needs_migration:
                    conn.rollback()
                    return None
                conn.rollback()
                conn.close()
                read_chat_bundle(db_path, default_factory, legacy_path=legacy_path)
                return mutate_chat(
                    db_path,
                    target,
                    mutation,
                    default_factory,
                    legacy_path=legacy_path,
                    export_path=export_path,
                )
            current = _load_chat_row(conn, target)
            if current is None:
                conn.rollback()
                return None
            before_messages = [
                _plain(item)
                for item in current.get("messages") or []
                if isinstance(item, dict)
            ]
            changed = mutation(current)
            if changed is False:
                conn.rollback()
                return current
            if _chat_id(current) != target:
                raise ValueError("Workbench chat mutation cannot change id")
            _write_chat_row(
                conn,
                current,
                int(row[0]),
                write_messages=before_messages != current.get("messages"),
                previous_messages=before_messages,
            )
            stored = _load_row(conn, "chats") or {}
            _write_row(conn, "chats", stored)
            total_message_count = int(
                conn.execute("SELECT COUNT(*) FROM workbench_chat_messages").fetchone()[0]
            )
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()
    if export_path is not None:
        if total_message_count >= 256:
            _schedule_chat_export(db_path, export_path)
        else:
            atomic_write_json(
                export_path,
                read_chat_bundle(db_path, lambda: {"chats": []}),
            )
    return current


def write_chat(
    db_path: str | Path,
    chat: dict[str, Any],
    default_factory: Callable[[], dict[str, Any]],
    *,
    base_chat: dict[str, Any] | None = None,
    legacy_path: Path | None = None,
    export_path: Path | None = None,
) -> dict[str, Any] | None:
    """Three-way merge and persist one chat without loading its siblings."""
    target = _chat_id(chat)
    if not target:
        return None
    local = _plain(chat)
    base = _plain(base_chat) if isinstance(base_chat, dict) else None

    def merge_into(current: dict[str, Any]) -> None:
        merged = (
            local
            if base is None
            else _three_way_merge(base, local, current, ("chats", target))
        )
        if not isinstance(merged, dict):
            raise ValueError("Workbench chat merge produced an invalid value")
        current.clear()
        current.update(merged)

    return mutate_chat(
        db_path,
        target,
        merge_into,
        default_factory,
        legacy_path=legacy_path,
        export_path=export_path,
    )


def _merge_chat_lists(
    base: list[Any],
    local: list[Any],
    remote: list[Any],
) -> list[dict[str, Any]]:
    base_by = {_chat_id(chat): chat for chat in base if _chat_id(chat)}
    local_by = {_chat_id(chat): chat for chat in local if _chat_id(chat)}
    remote_by = {_chat_id(chat): chat for chat in remote if _chat_id(chat)}
    order: list[str] = []
    for source in (local, remote):
        for chat in source:
            chat_id = _chat_id(chat)
            if chat_id and chat_id not in order:
                order.append(chat_id)

    merged: list[dict[str, Any]] = []
    for chat_id in order:
        base_chat = base_by.get(chat_id, _MISSING)
        local_chat = local_by.get(chat_id, _MISSING)
        remote_chat = remote_by.get(chat_id, _MISSING)
        value = _three_way_merge(
            base_chat,
            local_chat,
            remote_chat,
            ("chats", chat_id),
        )
        if isinstance(value, dict):
            merged.append(value)
    return merged


def write_chat_bundle(
    db_path: str | Path,
    value: dict[str, Any],
    default_factory: Callable[[], dict[str, Any]],
    *,
    legacy_path: Path | None = None,
    export_path: Path | None = None,
    base_value: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Merge chats by id and persist only rows whose content or order changed."""
    local = value if isinstance(value, dict) else default_factory()
    inherited_base = getattr(value, "_workbench_base", None)
    inherited_versions = getattr(value, "_workbench_versions", None)
    base = base_value if base_value is not None else inherited_base
    with _DOCUMENT_WRITE_LOCK:
        conn = _connect(db_path)
        try:
            conn.execute("BEGIN IMMEDIATE")
            if isinstance(inherited_versions, dict):
                stored = _load_row(conn, "chats")
                current_versions = _chat_versions(conn)
                base_by = {
                    _chat_id(chat): chat
                    for chat in (base or {}).get("chats") or []
                    if _chat_id(chat)
                }
                row_ids = [
                    str(row[0])
                    for row in conn.execute(
                        "SELECT chat_id FROM workbench_chats ORDER BY ordinal, chat_id"
                    ).fetchall()
                ]
                ordered_ids = [
                    str(chat_id)
                    for chat_id in (stored or {}).get("chatIds") or []
                    if str(chat_id) in current_versions
                ]
                ordered_ids.extend(
                    chat_id for chat_id in row_ids if chat_id not in ordered_ids
                )
                remote_chats: list[dict[str, Any]] = []
                for chat_id in ordered_ids:
                    if (
                        inherited_versions.get(chat_id) == current_versions.get(chat_id)
                        and chat_id in base_by
                    ):
                        remote_chats.append(base_by[chat_id])
                    else:
                        remote_chat = _load_chat_row(conn, chat_id)
                        if remote_chat is not None:
                            remote_chats.append(remote_chat)
                remote = {
                    str(key): _plain(item)
                    for key, item in (stored or {}).items()
                    if key not in {"chatIds", "normalizedVersion"}
                }
                remote["chats"] = remote_chats
            else:
                remote, _ = _load_chat_bundle_locked(
                    conn,
                    default_factory,
                    legacy_path=legacy_path,
                    migrate=True,
                )
            if not isinstance(base, dict):
                merged = {"chats": [_plain(chat) for chat in local.get("chats") or []]}
            else:
                merged_meta = _three_way_merge(
                    {key: item for key, item in base.items() if key != "chats"},
                    {key: item for key, item in local.items() if key != "chats"},
                    {key: item for key, item in remote.items() if key != "chats"},
                )
                merged = dict(merged_meta) if isinstance(merged_meta, dict) else {}
                merged["chats"] = _merge_chat_lists(
                    list(base.get("chats") or []),
                    list(local.get("chats") or []),
                    list(remote.get("chats") or []),
                )

            remote_by = {
                _chat_id(chat): chat
                for chat in remote.get("chats") or []
                if _chat_id(chat)
            }
            remote_ordinals = {
                _chat_id(chat): index
                for index, chat in enumerate(remote.get("chats") or [])
                if _chat_id(chat)
            }
            merged_ids: list[str] = []
            for index, chat in enumerate(merged.get("chats") or []):
                if not isinstance(chat, dict) or not _chat_id(chat):
                    continue
                chat_id = _chat_id(chat)
                merged_ids.append(chat_id)
                remote_chat = remote_by.get(chat_id)
                remote_messages = (
                    remote_chat.get("messages")
                    if isinstance(remote_chat, dict)
                    else _MISSING
                )
                if remote_chat != chat or index != remote_ordinals.get(chat_id, -1):
                    _write_chat_row(
                        conn,
                        chat,
                        index,
                        write_messages=remote_messages != chat.get("messages"),
                        previous_messages=(
                            remote_messages if isinstance(remote_messages, list) else None
                        ),
                    )

            removed_ids = set(remote_by) - set(merged_ids)
            if removed_ids:
                conn.executemany(
                    "DELETE FROM workbench_chat_messages WHERE chat_id = ?",
                    [(chat_id,) for chat_id in sorted(removed_ids)],
                )
                conn.executemany(
                    "DELETE FROM workbench_chats WHERE chat_id = ?",
                    [(chat_id,) for chat_id in sorted(removed_ids)],
                )
            _write_row(conn, "chats", _chat_shell(merged_ids, merged))
            conn.commit()
            committed_versions = _chat_versions(conn)
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    if export_path is not None:
        message_count = sum(
            len(chat.get("messages") or [])
            for chat in merged.get("chats") or []
            if isinstance(chat, dict) and isinstance(chat.get("messages"), list)
        )
        if message_count >= 256:
            _schedule_chat_export(db_path, export_path)
        else:
            atomic_write_json(export_path, merged)
    return _tracked_bundle(merged, "chats", versions=committed_versions)


def read_project_bundle(
    db_path: str | Path,
    default_factory: Callable[[], dict[str, Any]],
    summarize_session: Callable[[dict[str, Any]], dict[str, Any]],
    *,
    legacy_path: Path | None = None,
    lightweight: bool = False,
) -> dict[str, Any]:
    """Read normalized Workbench projects, hydrating task sessions on demand.

    The ``projects`` document is a lightweight index. Complete task-session
    payloads live in ``workbench_task_sessions`` so a single run update no
    longer rewrites every task history in every project.
    """
    conn = _connect(db_path)
    try:
        # Most project reads are already normalized and must not compete with
        # an unrelated writer for SQLite's single write reservation.
        conn.execute("BEGIN")
        shell, full, migration_required = _load_project_bundle_locked(
            conn,
            default_factory,
            summarize_session,
            legacy_path=legacy_path,
            migrate=False,
        )
        conn.commit()
        if migration_required:
            conn.execute("BEGIN IMMEDIATE")
            shell, full, _ = _load_project_bundle_locked(
                conn,
                default_factory,
                summarize_session,
                legacy_path=legacy_path,
            )
            conn.commit()
        if lightweight:
            # List payloads keep exactly one complete task: the current one.
            # The UI can resume that workspace immediately while every sibling
            # remains a compact project-index summary.
            value = _plain(shell)
            active_project_id = str(value.get("activeProjectId") or "")
            active_session_id = str(value.get("activeSessionId") or "")
            if active_project_id and active_session_id:
                full_project = next(
                    (
                        project
                        for project in full.get("projects") or []
                        if isinstance(project, dict)
                        and str(project.get("id") or "") == active_project_id
                    ),
                    None,
                )
                full_session = next(
                    (
                        session
                        for session in (full_project or {}).get("sessions") or []
                        if isinstance(session, dict)
                        and str(session.get("id") or "") == active_session_id
                    ),
                    None,
                )
                if full_session is not None:
                    for project in value.get("projects") or []:
                        if (
                            not isinstance(project, dict)
                            or str(project.get("id") or "") != active_project_id
                        ):
                            continue
                        project["sessions"] = [
                            _plain(full_session)
                            if isinstance(session, dict)
                            and str(session.get("id") or "") == active_session_id
                            else session
                            for session in project.get("sessions") or []
                        ]
                        break
        else:
            value = full
        return _tracked(value, "projects")
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def write_project_bundle(
    db_path: str | Path,
    value: dict[str, Any],
    default_factory: Callable[[], dict[str, Any]],
    summarize_session: Callable[[dict[str, Any]], dict[str, Any]],
    *,
    legacy_path: Path | None = None,
    export_path: Path | None = None,
    base_value: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Merge and atomically persist a project index plus changed task rows."""
    local = _plain(value)
    base = _plain(base_value) if base_value is not None else baseline(value)
    with _DOCUMENT_WRITE_LOCK:
        conn = _connect(db_path)
        try:
            conn.execute("BEGIN IMMEDIATE")
            _remote_shell, remote, _ = _load_project_bundle_locked(
                conn,
                default_factory,
                summarize_session,
                legacy_path=legacy_path,
            )
            merged = local if base is None else _three_way_merge(base, local, remote)
            if not isinstance(merged, dict):
                raise TypeError("Workbench projects bundle is not an object")
            shell, sessions = _split_project_bundle(merged, summarize_session)
            remote_sessions = {
                session_id: session
                for session_id, session in _load_task_session_rows(conn).items()
            }
            for session_id, session in sessions.items():
                if remote_sessions.get(session_id) != session:
                    _write_task_session_row(conn, session)
            removed_ids = set(remote_sessions) - set(sessions)
            if removed_ids:
                conn.executemany(
                    "DELETE FROM workbench_task_sessions WHERE session_id = ?",
                    [(session_id,) for session_id in sorted(removed_ids)],
                )
            _write_row(conn, "projects", shell)
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    # Keep the compatibility file complete, even though SQLite stores the
    # project index and sessions separately. This mirror is outside the SQLite
    # writer lock, matching the generic document path.
    if export_path is not None:
        atomic_write_json(export_path, merged)
    return _tracked(merged, "projects")


def patch_project_bundle_fields(
    db_path: str | Path,
    fields: dict[str, Any],
    default_factory: Callable[[], dict[str, Any]],
    summarize_session: Callable[[dict[str, Any]], dict[str, Any]],
    *,
    legacy_path: Path | None = None,
    export_path: Path | None = None,
) -> dict[str, Any]:
    """Patch project-index scalars without hydrating task rows for the write."""
    updates = _plain(fields)
    with _DOCUMENT_WRITE_LOCK:
        conn = _connect(db_path)
        try:
            conn.execute("BEGIN IMMEDIATE")
            shell, _full, _ = _load_project_bundle_locked(
                conn,
                default_factory,
                summarize_session,
                legacy_path=legacy_path,
            )
            shell.update(updates)
            _write_row(conn, "projects", shell)
            full = _hydrate_project_bundle(shell, _load_task_session_rows(conn))
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()
    if export_path is not None:
        atomic_write_json(export_path, full)
    return {name: _plain(full.get(name)) for name in updates}


def patch_document_fields(
    db_path: str | Path,
    key: str,
    fields: dict[str, Any],
    default_factory: Callable[[], dict[str, Any]],
    *,
    legacy_path: Path | None = None,
    export_path: Path | None = None,
) -> dict[str, Any]:
    """Atomically update a few top-level fields without a read/repair cycle.

    Selection-like state changes should not have to load a document through a
    domain layer that may perform expensive invariant repair or workspace
    scanning.  Read the latest committed row inside the write transaction so a
    concurrent document writer is preserved, then patch only the requested
    scalar fields.
    """
    if key == "projects":
        return patch_project_bundle_fields(
            db_path,
            fields,
            default_factory,
            summarize_task_session,
            legacy_path=legacy_path,
            export_path=export_path,
        )
    if key == "chats":
        current = read_chat_bundle(
            db_path,
            default_factory,
            legacy_path=legacy_path,
        )
        current.update(_plain(fields))
        updated = write_chat_bundle(
            db_path,
            current,
            default_factory,
            legacy_path=legacy_path,
            export_path=export_path,
        )
        return {name: _plain(updated.get(name)) for name in fields}
    updates = _plain(fields)
    with _DOCUMENT_WRITE_LOCK:
        conn = _connect(db_path)
        try:
            conn.execute("BEGIN IMMEDIATE")
            current = _load_row(conn, key)
            if current is None:
                current = _initial_value(default_factory, legacy_path)
            if not isinstance(current, dict):
                raise TypeError(f"Workbench document {key} is not an object")
            current.update(updates)
            _write_row(conn, key, current)
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    if export_path is not None:
        try:
            _export_current_document(db_path, key, export_path)
        except Exception:
            logger.exception("Failed to export Workbench document %s to %s", key, export_path)
    return {name: _plain(current.get(name)) for name in updates}


def _export_current_document(db_path: str | Path, key: str, export_path: Path) -> None:
    if key == "chats":
        atomic_write_json(
            export_path,
            read_chat_bundle(db_path, lambda: {"chats": []}),
        )
        return
    # Never retain a SQLite writer lock while encoding and atomically replacing
    # a potentially large JSON mirror. If another process commits during the
    # export, verify the row version and converge to the newest snapshot.
    for _attempt in range(4):
        conn = _connect(db_path)
        try:
            row = conn.execute(
                "SELECT payload_json, updated_at FROM workbench_state WHERE key = ?",
                (key,),
            ).fetchone()
        finally:
            conn.close()
        marker = None if row is None else str(row[1])
        if row is None:
            export_path.unlink(missing_ok=True)
        else:
            atomic_write_json(export_path, json.loads(row[0]))

        verify = _connect(db_path)
        try:
            latest = verify.execute(
                "SELECT updated_at FROM workbench_state WHERE key = ?",
                (key,),
            ).fetchone()
        finally:
            verify.close()
        latest_marker = None if latest is None else str(latest[0])
        if latest_marker == marker:
            return


def delete_document(db_path: str | Path, key: str, *, export_path: Path | None = None) -> None:
    conn = _connect(db_path)
    try:
        conn.execute("BEGIN IMMEDIATE")
        if key == "chats":
            conn.execute("DELETE FROM workbench_chat_messages")
            conn.execute("DELETE FROM workbench_chats")
        elif key == "projects":
            conn.execute("DELETE FROM workbench_task_sessions")
        elif key == "chat_changes":
            conn.execute("DELETE FROM workbench_chat_change_files")
            conn.execute("DELETE FROM workbench_chat_change_sets")
        conn.execute("DELETE FROM workbench_state WHERE key = ?", (key,))
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    if export_path is not None:
        export_path.unlink(missing_ok=True)


def list_document_keys(db_path: str | Path, *, prefix: str = "") -> list[str]:
    conn = _connect(db_path)
    try:
        if prefix:
            rows = conn.execute(
                "SELECT key FROM workbench_state WHERE key LIKE ? ORDER BY key",
                (prefix + "%",),
            ).fetchall()
        else:
            rows = conn.execute("SELECT key FROM workbench_state ORDER BY key").fetchall()
        return [str(row[0]) for row in rows]
    finally:
        conn.close()


def has_document_data(db_path: str | Path, key: str) -> bool:
    conn = _connect(db_path)
    try:
        if key == "chats":
            row = conn.execute("SELECT 1 FROM workbench_chats LIMIT 1").fetchone()
            if row is not None:
                return True
        elif key == "projects":
            row = conn.execute("SELECT 1 FROM workbench_task_sessions LIMIT 1").fetchone()
            if row is not None:
                return True
        elif key == "chat_changes":
            row = conn.execute(
                "SELECT 1 FROM workbench_chat_change_sets LIMIT 1"
            ).fetchone()
            if row is not None:
                return True
        row = conn.execute(
            "SELECT payload_json FROM workbench_state WHERE key = ?",
            (key,),
        ).fetchone()
        if row is None:
            return False
        value = json.loads(str(row[0]))
        if isinstance(value, dict):
            return any(
                bool(item)
                for name, item in value.items()
                if name not in {"normalizedVersion", "chatIds"}
            )
        return bool(value)
    finally:
        conn.close()
