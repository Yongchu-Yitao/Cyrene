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
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Load and, when necessary, atomically normalize the legacy project row."""
    raw = _load_row(conn, "projects")
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
            _write_task_session_row(conn, session)
            migrated = True
    if raw != shell or _load_row(conn, "projects") is None:
        _write_row(conn, "projects", shell)
        migrated = True
    if migrated:
        # Callers own the surrounding transaction; this flag exists only to
        # make the migration intent explicit.
        pass
    return shell, full


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
        conn.execute("BEGIN IMMEDIATE")
        shell, full = _load_project_bundle_locked(
            conn,
            default_factory,
            summarize_session,
            legacy_path=legacy_path,
        )
        conn.commit()
        value = shell if lightweight else full
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
            _remote_shell, remote = _load_project_bundle_locked(
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
            shell, _full = _load_project_bundle_locked(
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
        conn.execute("DELETE FROM workbench_state WHERE key = ?", (key,))
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    if export_path is not None:
        _export_current_document(db_path, key, export_path)


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
        row = conn.execute(
            "SELECT payload_json FROM workbench_state WHERE key = ?",
            (key,),
        ).fetchone()
        if row is None:
            return False
        value = json.loads(str(row[0]))
        if isinstance(value, dict):
            return any(bool(item) for item in value.values())
        return bool(value)
    finally:
        conn.close()
