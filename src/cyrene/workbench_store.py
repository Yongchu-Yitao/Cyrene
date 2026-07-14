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

from cyrene.io_utils import atomic_write_json, read_json_safe

logger = logging.getLogger(__name__)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS workbench_state (
    key TEXT PRIMARY KEY,
    payload_json TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
"""

_MISSING = object()
T = TypeVar("T")
_SCHEMA_LOCK = threading.Lock()
_SCHEMA_READY: set[str] = set()
# A Workbench document is merged with the latest committed value before every
# write.  Serializing those read/merge/write/export cycles inside this process
# both reduces SQLite writer contention and preserves commit/export ordering.
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
                    conn.execute(_SCHEMA)
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
        if local == base:
            return _MISSING
        return _plain(local)
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
    export_path: Path | None = None,
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

    # Compatibility/export mirror only. Runtime reads never consult this file
    # once the SQLite row exists. Re-read while holding the SQLite writer lock
    # so two processes cannot publish exports in the reverse of commit order.
    if export_path is not None:
        try:
            _export_current_document(db_path, key, export_path)
        except Exception:
            logger.exception("Failed to export Workbench document %s to %s", key, export_path)
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
    with _DOCUMENT_WRITE_LOCK:
        return _write_document_locked(
            db_path,
            key,
            value,
            default_factory,
            legacy_path=legacy_path,
            export_path=export_path,
            base_value=base_value,
        )


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
    conn = _connect(db_path)
    try:
        conn.execute("BEGIN IMMEDIATE")
        current = _load_row(conn, key)
        if current is None:
            export_path.unlink(missing_ok=True)
        else:
            atomic_write_json(export_path, current)
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


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
