"""Harness-owned workspace change tracking for Workbench chat runs.

The tracker deliberately does not inspect Git.  It snapshots the workspace at
the start and end of one agent run, computes durable text diffs, and persists a
run-scoped change set outside the public chat transcript.
"""

from __future__ import annotations

import asyncio
import difflib
import hashlib
import json
import logging
import os
import sqlite3
import uuid
from dataclasses import dataclass
from collections.abc import Mapping
from .file_index import FileIndex, FileIndexEdit
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pathspec import GitIgnoreSpec
from watchfiles import awatch

from cyrene.platform.paths import CYRENE_DIR_NAME
from cyrene.workbench.persistence.store import ensure_schema

logger = logging.getLogger(__name__)

_IGNORED_DIRS = {
    ".git", ".hg", ".svn", ".idea", ".pytest_cache", ".mypy_cache",
    ".ruff_cache", ".tox", ".venv", "__pycache__", "node_modules",
}
# Cyrene writes these folders as an implementation detail of a conversation.
# They are useful memory/plan mirrors, but they are not files the Agent created
# for the user and must never appear as run-scoped workspace changes. Since
# everything Cyrene-owned now lives under the hidden .cyrene dir, any path
# rooted there is managed.
_CYRENE_MANAGED_ROOT_DIRS = frozenset({CYRENE_DIR_NAME})
_MAX_TEXT_FILE_BYTES = 1_000_000
_MAX_CAPTURED_TEXT_BYTES = 32_000_000
_MAX_CAPTURED_TEXT_FILES = 2_000
_MAX_HASH_FILE_BYTES = 16_000_000
_MAX_DIFF_CHARS = 2_000_000
_MAX_CHANGE_SET_DIFF_CHARS = 10_000_000
_MAX_CHANGE_SETS_PER_CHAT = 50
_MAX_CHANGE_SETS_TOTAL = 500
_MAX_STORED_DIFF_CHARS = 25_000_000


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def is_cyrene_managed_workspace_path(
    path_value: Any,
    workspace_root: Any = None,
) -> bool:
    """Return whether a workspace-relative path is Cyrene-owned run state."""
    normalized = str(path_value or "").strip().replace("\\", "/")
    while normalized.startswith("./"):
        normalized = normalized[2:]
    if not normalized or normalized.startswith("/"):
        return False
    root_name = normalized.split("/", 1)[0]
    return root_name in _CYRENE_MANAGED_ROOT_DIRS


@dataclass(frozen=True)
class WorkspaceFileState:
    mtime_ns: int
    size: int
    digest: str
    text: str | None
    ctime_ns: int = 0
    inode: int = 0


@dataclass(frozen=True)
class WorkspaceSnapshot:
    root: Path
    files: Mapping[str, WorkspaceFileState]
    captured_at: str

    def __post_init__(self) -> None:
        if isinstance(self.files, FileIndexEdit):
            object.__setattr__(self, "files", self.files.freeze())
        elif not isinstance(self.files, FileIndex):
            object.__setattr__(self, "files", FileIndex(self.files))


@dataclass(frozen=True, slots=True)
class _IgnoreRuleSet:
    base: str
    spec: GitIgnoreSpec


class WorkspaceIgnoreMatcher:
    """Apply Git-compatible ignore files relative to their owning directory."""

    def __init__(self, root: Path, rules: tuple[_IgnoreRuleSet, ...]) -> None:
        self.root = root
        self.rules = rules

    @classmethod
    def load(cls, root: Path) -> "WorkspaceIgnoreMatcher":
        rules: list[_IgnoreRuleSet] = []
        for current, dirnames, filenames in os.walk(root):
            dirnames[:] = [name for name in dirnames if name not in _IGNORED_DIRS]
            current_path = Path(current)
            try:
                relative_dir = current_path.relative_to(root).as_posix()
            except ValueError:
                continue
            if relative_dir == ".":
                relative_dir = ""
                dirnames[:] = [
                    name for name in dirnames
                    if name not in _CYRENE_MANAGED_ROOT_DIRS
                ]
            ignore_file = current_path / ".gitignore"
            if ".gitignore" in filenames:
                try:
                    lines = ignore_file.read_text(encoding="utf-8").splitlines()
                except (OSError, UnicodeError):
                    lines = []
                if lines:
                    rules.append(
                        _IgnoreRuleSet(
                            base=relative_dir,
                            spec=GitIgnoreSpec.from_lines(lines),
                        )
                    )
            matcher = cls(root, tuple(rules))
            dirnames[:] = [
                name for name in dirnames
                if not matcher.is_ignored(
                    (current_path / name).relative_to(root).as_posix(),
                    is_dir=True,
                )
            ]
        return cls(root, tuple(rules))

    def is_ignored(self, relative: str, *, is_dir: bool = False) -> bool:
        normalized = relative.strip("/").replace("\\", "/")
        if not normalized:
            return False
        parts = normalized.split("/")
        if parts[0] in _CYRENE_MANAGED_ROOT_DIRS or any(
            part in _IGNORED_DIRS for part in parts
        ):
            return True
        ignored = False
        candidate = normalized + ("/" if is_dir else "")
        for rule_set in self.rules:
            base = rule_set.base
            if base:
                if normalized == base:
                    scoped = ""
                elif normalized.startswith(base + "/"):
                    scoped = candidate[len(base) + 1 :]
                else:
                    continue
            else:
                scoped = candidate
            result = rule_set.spec.check_file(scoped)
            if result.include is not None:
                ignored = bool(result.include)
        return ignored


class WorkspaceSnapshotIndex:
    """Long-lived immutable snapshot maintained from filesystem events."""

    def __init__(self, workspace_root: str | Path) -> None:
        self.root = Path(workspace_root).expanduser().resolve()
        self._snapshot: WorkspaceSnapshot | None = None
        self._matcher: WorkspaceIgnoreMatcher | None = None
        self._ready = asyncio.Event()
        self._stop = asyncio.Event()
        self._task: asyncio.Task[Any] | None = None
        self._snapshot_lock = asyncio.Lock()
        self._initial_error: BaseException | None = None

    def start(self) -> None:
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(
                self._watch(),
                name=f"workspace-snapshot:{self.root.name}",
            )

    async def snapshot(self, *, settle: bool = True) -> WorkspaceSnapshot | None:
        self.start()
        await self._ready.wait()
        if self._initial_error is not None:
            return None
        # Let the low-latency watcher deliver writes made immediately before a
        # run boundary, then wait for any in-flight incremental application.
        if settle:
            await asyncio.sleep(0.02)
        async with self._snapshot_lock:
            return self._snapshot

    async def close(self) -> None:
        self._stop.set()
        task = self._task
        if task is not None and not task.done():
            task.cancel()
        if task is not None:
            await asyncio.gather(task, return_exceptions=True)
        self._task = None

    def _watch_filter(self, _change: Any, raw_path: str) -> bool:
        path = Path(raw_path)
        try:
            relative = path.relative_to(self.root).as_posix()
        except ValueError:
            return False
        if path.name == ".gitignore":
            return True
        matcher = self._matcher
        return matcher is None or not matcher.is_ignored(
            relative,
            is_dir=path.is_dir(),
        )

    async def _watch(self) -> None:
        watcher = awatch(
            self.root,
            watch_filter=self._watch_filter,
            debounce=10,
            step=5,
            stop_event=self._stop,
            ignore_permission_denied=True,
        )
        next_changes: asyncio.Task[Any] | None = None
        try:
            # Arm the native watcher before the initial walk. Events arriving
            # during that walk are replayed onto its result, closing the usual
            # scan-then-subscribe race.
            next_changes = asyncio.create_task(anext(watcher))
            await asyncio.sleep(0)
            matcher = await asyncio.to_thread(WorkspaceIgnoreMatcher.load, self.root)
            initial = await asyncio.to_thread(
                capture_workspace_snapshot,
                self.root,
                ignore_matcher=matcher,
            )
            async with self._snapshot_lock:
                self._matcher = matcher
                self._snapshot = initial
            self._ready.set()
            while True:
                changes = await next_changes
                next_changes = asyncio.create_task(anext(watcher))
                paths = {path for _change, path in changes}
                ignore_changed = any(Path(path).name == ".gitignore" for path in paths)
                async with self._snapshot_lock:
                    previous = self._snapshot
                    if ignore_changed:
                        matcher = await asyncio.to_thread(
                            WorkspaceIgnoreMatcher.load,
                            self.root,
                        )
                        updated = await asyncio.to_thread(
                            capture_workspace_snapshot,
                            self.root,
                            previous=previous,
                            ignore_matcher=matcher,
                        )
                        self._matcher = matcher
                    elif previous is not None:
                        updated = await asyncio.to_thread(
                            capture_workspace_snapshot,
                            self.root,
                            previous=previous,
                            changed_paths=paths,
                            ignore_matcher=matcher,
                        )
                    else:
                        updated = None
                    self._snapshot = updated
        except asyncio.CancelledError:
            raise
        except BaseException as exc:
            self._initial_error = exc
            logger.exception("Workspace snapshot watcher failed for %s", self.root)
        finally:
            if next_changes is not None and not next_changes.done():
                next_changes.cancel()
                await asyncio.gather(next_changes, return_exceptions=True)
            await watcher.aclose()
            self._ready.set()


def _read_file_state(
    path: Path,
    *,
    capture_text: bool,
    stat_result: os.stat_result | None = None,
) -> WorkspaceFileState | None:
    try:
        stat = stat_result or path.stat()
        if not path.is_file() or path.is_symlink():
            return None
        data: bytes | None = None
        digest = ""
        if stat.st_size <= _MAX_HASH_FILE_BYTES:
            data = path.read_bytes()
            digest = hashlib.sha256(data).hexdigest()
        text: str | None = None
        if capture_text and stat.st_size <= _MAX_TEXT_FILE_BYTES:
            if data is None:
                data = path.read_bytes()
            if b"\x00" not in data:
                try:
                    text = data.decode("utf-8")
                except UnicodeDecodeError:
                    text = None
        return WorkspaceFileState(
            mtime_ns=int(stat.st_mtime_ns),
            size=int(stat.st_size),
            digest=digest,
            text=text,
            ctime_ns=int(stat.st_ctime_ns),
            inode=int(stat.st_ino),
        )
    except OSError:
        return None


def capture_workspace_snapshot(
    workspace_root: str | Path | None,
    *,
    previous: WorkspaceSnapshot | None = None,
    changed_paths: set[str] | None = None,
    ignore_matcher: WorkspaceIgnoreMatcher | None = None,
) -> WorkspaceSnapshot | None:
    """Capture a bounded snapshot, reusing unchanged state when available.

    The pre-run snapshot still owns the before-text needed for durable diffs.
    During finalization, unchanged files can reuse that immutable state after a
    cheap metadata check instead of being read and hashed for a second time.
    """
    if not workspace_root:
        return None
    try:
        root = Path(workspace_root).expanduser().resolve()
    except OSError:
        return None
    if not root.exists() or not root.is_dir():
        return None
    if previous is None or previous.root != root:
        previous = None
    matcher = ignore_matcher or WorkspaceIgnoreMatcher.load(root)
    if previous is not None and changed_paths is not None:
        return _capture_incremental_snapshot(root, previous, changed_paths, matcher)

    files: dict[str, WorkspaceFileState] = {}
    captured_text_bytes = 0
    captured_text_files = 0
    try:
        for current, dirnames, filenames in os.walk(root):
            current_path = Path(current)
            dirnames[:] = [
                name for name in dirnames
                if not matcher.is_ignored(
                    (current_path / name).relative_to(root).as_posix(),
                    is_dir=True,
                )
            ]
            for filename in filenames:
                target = current_path / filename
                try:
                    rel = target.relative_to(root).as_posix()
                    if matcher.is_ignored(rel):
                        continue
                    stat = target.stat()
                    size = stat.st_size
                except (OSError, ValueError):
                    continue
                prior = previous.files.get(rel) if previous is not None else None
                if (
                    prior is not None
                    and prior.inode == int(stat.st_ino)
                    and prior.mtime_ns == int(stat.st_mtime_ns)
                    and prior.ctime_ns == int(stat.st_ctime_ns)
                    and prior.size == int(size)
                ):
                    state = prior
                else:
                    capture_text = (
                        captured_text_files < _MAX_CAPTURED_TEXT_FILES
                        and size <= _MAX_TEXT_FILE_BYTES
                        and captured_text_bytes + size <= _MAX_CAPTURED_TEXT_BYTES
                    )
                    state = _read_file_state(
                        target,
                        capture_text=capture_text,
                        stat_result=stat,
                    )
                if state is None:
                    continue
                files[rel] = state
                if state.text is not None:
                    captured_text_files += 1
                    captured_text_bytes += size
    except OSError:
        pass
    return WorkspaceSnapshot(root=root, files=files, captured_at=_utc_now_iso())


def _capture_incremental_snapshot(
    root: Path,
    previous: WorkspaceSnapshot,
    changed_paths: set[str],
    ignore_matcher: WorkspaceIgnoreMatcher,
) -> WorkspaceSnapshot:
    """Apply watcher-reported paths to an immutable prior snapshot.

    Directory events are rescanned recursively, while file events touch only
    that file. The long-lived index serializes these applications so immutable
    snapshots can be shared safely by overlapping chat runs.
    """
    files = previous.files.edit()
    captured_text_files = files.text_count
    captured_text_bytes = files.text_bytes

    def remove_relative(relative: str) -> None:
        nonlocal captured_text_bytes, captured_text_files
        if relative in {"", "."}:
            captured_text_files = 0
            captured_text_bytes = 0
            files.clear()
            return
        removed = files.pop(relative, None)
        if removed is not None and removed.text is not None:
            captured_text_files -= 1
            captured_text_bytes -= removed.size
        if removed is not None:
            return  # A known regular file cannot contain indexed descendants.
        prefix = relative.rstrip("/") + "/"
        for stored_path in [path for path in files if path.startswith(prefix)]:
            removed = files.pop(stored_path, None)
            if removed is not None and removed.text is not None:
                captured_text_files -= 1
                captured_text_bytes -= removed.size

    def capture_file(target: Path) -> None:
        nonlocal captured_text_bytes, captured_text_files
        try:
            relative = target.relative_to(root).as_posix()
        except ValueError:
            return
        prior = files.get(relative)
        if ignore_matcher.is_ignored(relative):
            remove_relative(relative)
            return
        try:
            stat = target.stat()
        except OSError:
            remove_relative(relative)
            return
        if not target.is_file() or target.is_symlink():
            remove_relative(relative)
            return
        if (
            prior is not None
            and prior.inode == int(stat.st_ino)
            and prior.mtime_ns == int(stat.st_mtime_ns)
            and prior.ctime_ns == int(stat.st_ctime_ns)
            and prior.size == int(stat.st_size)
        ):
            return
        if prior is not None:
            files.pop(relative, None)
            if prior.text is not None:
                captured_text_files -= 1
                captured_text_bytes -= prior.size
        capture_text = (
            captured_text_files < _MAX_CAPTURED_TEXT_FILES
            and stat.st_size <= _MAX_TEXT_FILE_BYTES
            and captured_text_bytes + stat.st_size <= _MAX_CAPTURED_TEXT_BYTES
        )
        state = _read_file_state(
            target,
            capture_text=capture_text,
            stat_result=stat,
        )
        if state is None:
            return
        files[relative] = state
        if state.text is not None:
            captured_text_files += 1
            captured_text_bytes += state.size

    for raw_path in sorted(changed_paths):
        try:
            target = Path(raw_path).expanduser().resolve(strict=False)
            relative = target.relative_to(root).as_posix()
        except (OSError, RuntimeError, ValueError):
            continue
        if not target.exists():
            remove_relative(relative)
            continue
        if target.is_dir():
            remove_relative(relative)
            if ignore_matcher.is_ignored(relative, is_dir=True):
                continue
            for current, dirnames, filenames in os.walk(target):
                current_path = Path(current)
                dirnames[:] = [
                    name for name in dirnames
                    if not ignore_matcher.is_ignored(
                        (current_path / name).relative_to(root).as_posix(),
                        is_dir=True,
                    )
                ]
                for filename in filenames:
                    capture_file(current_path / filename)
            continue
        capture_file(target)

    return WorkspaceSnapshot(root=root, files=files, captured_at=_utc_now_iso())


def _same_file(before: WorkspaceFileState, after: WorkspaceFileState) -> bool:
    if before.digest and after.digest:
        return before.digest == after.digest
    return (
        before.inode == after.inode
        and before.mtime_ns == after.mtime_ns
        and before.ctime_ns == after.ctime_ns
        and before.size == after.size
    )


def _unified_diff(path: str, before: str, after: str, change_type: str) -> str:
    left = "/dev/null" if change_type == "created" else f"a/{path}"
    right = "/dev/null" if change_type == "deleted" else f"b/{path}"
    # ``keepends=True`` can concatenate ``-old`` and ``+new`` when either input
    # lacks a final newline.  Give every rendered diff row its own terminator;
    # the viewer cares about line changes, not the source's EOF marker.
    rows = difflib.unified_diff(
        before.splitlines(),
        after.splitlines(),
        fromfile=left,
        tofile=right,
        lineterm="",
    )
    diff = "\n".join(rows)
    if diff:
        diff += "\n"
    if len(diff) > _MAX_DIFF_CHARS:
        return diff[:_MAX_DIFF_CHARS] + "\n... diff truncated by Workbench ...\n"
    return diff


def _line_counts(diff: str) -> tuple[int, int]:
    additions = 0
    deletions = 0
    for line in diff.splitlines():
        if line.startswith("+++") or line.startswith("---"):
            continue
        if line.startswith("+"):
            additions += 1
        elif line.startswith("-"):
            deletions += 1
    return additions, deletions


def compare_workspace_snapshots(
    before: WorkspaceSnapshot | None,
    after: WorkspaceSnapshot | None,
) -> list[dict[str, Any]]:
    """Return created, modified, and deleted files for one run."""
    if before is None or after is None or before.root != after.root:
        return []
    changes: list[dict[str, Any]] = []
    for path in sorted(set(before.files) | set(after.files)):
        if is_cyrene_managed_workspace_path(path, after.root):
            continue
        old = before.files.get(path)
        new = after.files.get(path)
        if old is not None and new is not None and _same_file(old, new):
            continue
        change_type = "created" if old is None else "deleted" if new is None else "modified"
        old_text = old.text if old is not None else ""
        new_text = new.text if new is not None else ""
        text_available = (
            (old is None or old.text is not None)
            and (new is None or new.text is not None)
        )
        diff = _unified_diff(path, old_text or "", new_text or "", change_type) if text_available else ""
        additions, deletions = _line_counts(diff)
        item: dict[str, Any] = {
            "id": f"file_{uuid.uuid4().hex[:12]}",
            "path": path,
            "changeType": change_type,
            "status": change_type,
            "beforeHash": old.digest if old is not None else "",
            "afterHash": new.digest if new is not None else "",
            "beforeSize": old.size if old is not None else 0,
            "afterSize": new.size if new is not None else 0,
            "binary": not text_available,
            "additions": additions,
            "deletions": deletions,
            "source": "workspace_snapshot",
        }
        if diff:
            item["diff"] = diff
        elif not text_available:
            item["diffUnavailableReason"] = "binary_or_too_large"
        changes.append(item)
    return changes


def build_change_set(
    *,
    chat_id: str,
    run_id: str,
    before: WorkspaceSnapshot | None,
    after: WorkspaceSnapshot | None,
    status: str,
    attribution: str = "exclusive",
    overlapping_run_ids: list[str] | None = None,
) -> dict[str, Any]:
    files = compare_workspace_snapshots(before, after)
    retained_diff_chars = 0
    for item in files:
        diff = str(item.get("diff") or "")
        if not diff:
            continue
        if retained_diff_chars + len(diff) > _MAX_CHANGE_SET_DIFF_CHARS:
            item.pop("diff", None)
            item["diffUnavailableReason"] = "change_set_limit"
            continue
        retained_diff_chars += len(diff)
    return {
        "id": str(run_id or f"changeset_{uuid.uuid4().hex}"),
        "chatId": str(chat_id),
        "runId": str(run_id),
        "status": str(status or "completed"),
        "attribution": (
            "overlapping" if str(attribution) == "overlapping" else "exclusive"
        ),
        "overlappingRunIds": [
            str(item)
            for item in (overlapping_run_ids or [])
            if str(item)
        ],
        "workspacePath": str(before.root if before is not None else after.root if after is not None else ""),
        "startedAt": before.captured_at if before is not None else "",
        "completedAt": after.captured_at if after is not None else _utc_now_iso(),
        "fileCount": len(files),
        "additions": sum(int(item.get("additions") or 0) for item in files),
        "deletions": sum(int(item.get("deletions") or 0) for item in files),
        "files": files,
    }


def _changes_connect(db_path: str) -> sqlite3.Connection:
    ensure_schema(db_path)
    conn = sqlite3.connect(db_path, timeout=30)
    conn.execute("PRAGMA busy_timeout = 30000")
    return conn


def _touch_normalized_store(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        INSERT INTO workbench_state(key, payload_json, updated_at)
        VALUES ('chat_changes', ?, ?)
        ON CONFLICT(key) DO UPDATE SET
            payload_json = excluded.payload_json,
            updated_at = excluded.updated_at
        """,
        (json.dumps({"normalizedVersion": 1}), _utc_now_iso()),
    )


def _write_change_set_rows(
    conn: sqlite3.Connection,
    change_set: dict[str, Any],
) -> None:
    change_id = str(change_set.get("id") or "").strip()
    if not change_id:
        raise ValueError("Workbench change set is missing id")
    files = [item for item in change_set.get("files") or [] if isinstance(item, dict)]
    metadata = {key: value for key, value in change_set.items() if key != "files"}
    diff_chars = sum(len(str(item.get("diff") or "")) for item in files)
    conn.execute(
        """
        INSERT INTO workbench_chat_change_sets(
            change_set_id, chat_id, completed_at, diff_chars,
            payload_json, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(change_set_id) DO UPDATE SET
            chat_id = excluded.chat_id,
            completed_at = excluded.completed_at,
            diff_chars = excluded.diff_chars,
            payload_json = excluded.payload_json,
            updated_at = excluded.updated_at
        """,
        (
            change_id,
            str(change_set.get("chatId") or ""),
            str(change_set.get("completedAt") or ""),
            diff_chars,
            json.dumps(metadata, ensure_ascii=False),
            _utc_now_iso(),
        ),
    )
    conn.execute(
        "DELETE FROM workbench_chat_change_files WHERE change_set_id = ?",
        (change_id,),
    )
    if files:
        conn.executemany(
            """
            INSERT INTO workbench_chat_change_files(
                change_set_id, ordinal, path, payload_json, diff_text
            ) VALUES (?, ?, ?, ?, ?)
            """,
            [
                (
                    change_id,
                    ordinal,
                    str(item.get("path") or ""),
                    json.dumps(
                        {key: value for key, value in item.items() if key != "diff"},
                        ensure_ascii=False,
                    ),
                    str(item.get("diff") or ""),
                )
                for ordinal, item in enumerate(files)
            ],
        )


def _load_change_sets(
    conn: sqlite3.Connection,
    *,
    chat_id: str | None = None,
    include_diffs: bool = True,
) -> list[dict[str, Any]]:
    parameters: tuple[Any, ...] = ()
    where = ""
    if chat_id is not None:
        where = "WHERE chat_id = ?"
        parameters = (str(chat_id),)
    rows = conn.execute(
        f"""
        SELECT change_set_id, payload_json
        FROM workbench_chat_change_sets
        {where}
        ORDER BY completed_at DESC, rowid ASC
        """,
        parameters,
    ).fetchall()
    result: list[dict[str, Any]] = []
    for change_id, payload_json in rows:
        try:
            change_set = json.loads(str(payload_json))
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            raise RuntimeError(
                f"invalid Workbench change-set payload for {change_id}"
            ) from exc
        if not isinstance(change_set, dict):
            continue
        files: list[dict[str, Any]] = []
        file_query = (
            "SELECT payload_json, diff_text "
            if include_diffs
            else "SELECT payload_json, '' AS diff_text "
        ) + (
            "FROM workbench_chat_change_files "
            "WHERE change_set_id = ? ORDER BY ordinal"
        )
        for file_payload, diff_text in conn.execute(
            file_query,
            (str(change_id),),
        ).fetchall():
            try:
                item = json.loads(str(file_payload))
            except (TypeError, ValueError, json.JSONDecodeError) as exc:
                raise RuntimeError(
                    f"invalid Workbench change-file payload for {change_id}"
                ) from exc
            if not isinstance(item, dict):
                continue
            if include_diffs and diff_text:
                item["diff"] = str(diff_text)
            files.append(item)
        change_set["files"] = files
        result.append(change_set)
    return result


def _ensure_normalized_store(db_path: str) -> None:
    if not str(db_path or "").strip():
        raise ValueError("Workbench workspace changes require a database path")
    ensure_schema(db_path)


def _read_store(db_path: str) -> dict[str, Any]:
    _ensure_normalized_store(db_path)
    conn = _changes_connect(db_path)
    try:
        return {"changeSets": _load_change_sets(conn)}
    finally:
        conn.close()


def _write_store(db_path: str, payload: dict[str, Any]) -> None:
    _ensure_normalized_store(db_path)
    conn = _changes_connect(db_path)
    try:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute("DELETE FROM workbench_chat_change_files")
        conn.execute("DELETE FROM workbench_chat_change_sets")
        for item in payload.get("changeSets") or []:
            if isinstance(item, dict) and str(item.get("id") or "").strip():
                _write_change_set_rows(conn, item)
        _touch_normalized_store(conn)
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def save_change_set(db_path: str, change_set: dict[str, Any]) -> dict[str, Any]:
    change_id = str(change_set.get("id") or "")
    replacement = dict(change_set)
    workspace_root = change_set.get("workspacePath")
    replacement["files"] = [
        dict(item)
        for item in change_set.get("files") or []
        if isinstance(item, dict)
        and not is_cyrene_managed_workspace_path(item.get("path"), workspace_root)
    ]
    replacement["fileCount"] = len(replacement["files"])
    replacement["additions"] = sum(
        int(item.get("additions") or 0) for item in replacement["files"]
    )
    replacement["deletions"] = sum(
        int(item.get("deletions") or 0) for item in replacement["files"]
    )
    if db_path:
        _ensure_normalized_store(db_path)
        conn = _changes_connect(db_path)
        try:
            conn.execute("BEGIN IMMEDIATE")
            if not replacement["files"]:
                conn.execute(
                    "DELETE FROM workbench_chat_change_files WHERE change_set_id = ?",
                    (change_id,),
                )
                removed = conn.execute(
                    "DELETE FROM workbench_chat_change_sets WHERE change_set_id = ?",
                    (change_id,),
                ).rowcount
                if removed:
                    _touch_normalized_store(conn)
                conn.commit()
                return replacement

            _write_change_set_rows(conn, replacement)
            rows = conn.execute(
                """
                SELECT change_set_id, chat_id, diff_chars
                FROM workbench_chat_change_sets
                ORDER BY completed_at DESC, rowid ASC
                """
            ).fetchall()
            per_chat: dict[str, int] = {}
            kept_ids: list[str] = []
            stored_diff_chars = 0
            for stored_id, item_chat_id, item_diff_chars in rows:
                normalized_chat_id = str(item_chat_id or "")
                if per_chat.get(normalized_chat_id, 0) >= _MAX_CHANGE_SETS_PER_CHAT:
                    continue
                if len(kept_ids) >= _MAX_CHANGE_SETS_TOTAL:
                    continue
                diff_chars = int(item_diff_chars or 0)
                if kept_ids and stored_diff_chars + diff_chars > _MAX_STORED_DIFF_CHARS:
                    continue
                kept_ids.append(str(stored_id))
                stored_diff_chars += diff_chars
                per_chat[normalized_chat_id] = per_chat.get(normalized_chat_id, 0) + 1
            dropped_ids = [str(row[0]) for row in rows if str(row[0]) not in kept_ids]
            if dropped_ids:
                conn.executemany(
                    "DELETE FROM workbench_chat_change_files WHERE change_set_id = ?",
                    [(item_id,) for item_id in dropped_ids],
                )
                conn.executemany(
                    "DELETE FROM workbench_chat_change_sets WHERE change_set_id = ?",
                    [(item_id,) for item_id in dropped_ids],
                )
            _touch_normalized_store(conn)
            conn.commit()
            return replacement
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    payload = _read_store(db_path)
    items = payload.setdefault("changeSets", [])
    if not replacement["files"]:
        kept = [
            item for item in items
            if not isinstance(item, dict) or str(item.get("id") or "") != change_id
        ]
        if len(kept) != len(items):
            payload["changeSets"] = kept
            _write_store(db_path, payload)
        return replacement
    for index, item in enumerate(items):
        if isinstance(item, dict) and str(item.get("id") or "") == change_id:
            items[index] = replacement
            break
    else:
        items.append(replacement)
    # Keep this whole-document store bounded.  Newest runs win first within
    # each chat, then globally; individual change sets are already diff-capped.
    items.sort(key=lambda item: str(item.get("completedAt") or ""), reverse=True)
    per_chat: dict[str, int] = {}
    kept: list[dict[str, Any]] = []
    stored_diff_chars = 0
    for item in items:
        if not isinstance(item, dict):
            continue
        item_chat_id = str(item.get("chatId") or "")
        if per_chat.get(item_chat_id, 0) >= _MAX_CHANGE_SETS_PER_CHAT:
            continue
        if len(kept) >= _MAX_CHANGE_SETS_TOTAL:
            continue
        item_diff_chars = sum(
            len(str(file_item.get("diff") or ""))
            for file_item in item.get("files") or []
            if isinstance(file_item, dict)
        )
        if kept and stored_diff_chars + item_diff_chars > _MAX_STORED_DIFF_CHARS:
            continue
        kept.append(item)
        stored_diff_chars += item_diff_chars
        per_chat[item_chat_id] = per_chat.get(item_chat_id, 0) + 1
    payload["changeSets"] = kept
    _write_store(db_path, payload)
    return replacement


def _public_change_set(change_set: dict[str, Any]) -> dict[str, Any]:
    result = {
        key: value for key, value in change_set.items()
        if key not in {"files", "workspacePath"}
    }
    workspace_root = change_set.get("workspacePath")
    result["files"] = [
        {key: value for key, value in item.items() if key != "diff"}
        for item in change_set.get("files") or []
        if isinstance(item, dict)
        and not is_cyrene_managed_workspace_path(item.get("path"), workspace_root)
    ]
    result["fileCount"] = len(result["files"])
    result["additions"] = sum(int(item.get("additions") or 0) for item in result["files"])
    result["deletions"] = sum(int(item.get("deletions") or 0) for item in result["files"])
    return result


def list_chat_change_sets(db_path: str, chat_id: str) -> list[dict[str, Any]]:
    if db_path:
        _ensure_normalized_store(db_path)
        conn = _changes_connect(db_path)
        try:
            stored_items = _load_change_sets(
                conn,
                chat_id=str(chat_id),
                include_diffs=False,
            )
        finally:
            conn.close()
        return [
            public
            for item in stored_items
            if (public := _public_change_set(item)).get("fileCount")
        ]
    payload = _read_store(db_path)
    items: list[dict[str, Any]] = []
    for item in payload.get("changeSets") or []:
        if not isinstance(item, dict):
            continue
        if str(item.get("chatId") or "") != str(chat_id):
            continue
        public = _public_change_set(item)
        if public.get("fileCount"):
            items.append(public)
    items.sort(key=lambda item: str(item.get("completedAt") or ""), reverse=True)
    return items


def get_chat_file_change(
    db_path: str,
    chat_id: str,
    change_set_id: str,
    file_path: str,
) -> dict[str, Any] | None:
    if db_path:
        _ensure_normalized_store(db_path)
        conn = _changes_connect(db_path)
        try:
            row = conn.execute(
                """
                SELECT payload_json FROM workbench_chat_change_sets
                WHERE change_set_id = ? AND chat_id = ?
                """,
                (str(change_set_id), str(chat_id)),
            ).fetchone()
            if row is None:
                return None
            metadata = json.loads(str(row[0]))
            workspace_root = metadata.get("workspacePath") if isinstance(metadata, dict) else None
            if is_cyrene_managed_workspace_path(file_path, workspace_root):
                return None
            file_row = conn.execute(
                """
                SELECT payload_json, diff_text
                FROM workbench_chat_change_files
                WHERE change_set_id = ? AND path = ?
                ORDER BY ordinal LIMIT 1
                """,
                (str(change_set_id), str(file_path)),
            ).fetchone()
            if file_row is None:
                return None
            item = json.loads(str(file_row[0]))
            if not isinstance(item, dict):
                return None
            if file_row[1]:
                item["diff"] = str(file_row[1])
            return item
        finally:
            conn.close()
    payload = _read_store(db_path)
    for change_set in payload.get("changeSets") or []:
        if not isinstance(change_set, dict):
            continue
        if str(change_set.get("chatId") or "") != str(chat_id):
            continue
        if str(change_set.get("id") or "") != str(change_set_id):
            continue
        if is_cyrene_managed_workspace_path(file_path, change_set.get("workspacePath")):
            return None
        for item in change_set.get("files") or []:
            if isinstance(item, dict) and str(item.get("path") or "") == str(file_path):
                return dict(item)
    return None


def delete_chat_change_sets(db_path: str, chat_id: str) -> int:
    if db_path:
        _ensure_normalized_store(db_path)
        conn = _changes_connect(db_path)
        try:
            conn.execute("BEGIN IMMEDIATE")
            ids = [
                str(row[0])
                for row in conn.execute(
                    "SELECT change_set_id FROM workbench_chat_change_sets WHERE chat_id = ?",
                    (str(chat_id),),
                ).fetchall()
            ]
            if ids:
                conn.executemany(
                    "DELETE FROM workbench_chat_change_files WHERE change_set_id = ?",
                    [(item_id,) for item_id in ids],
                )
                conn.execute(
                    "DELETE FROM workbench_chat_change_sets WHERE chat_id = ?",
                    (str(chat_id),),
                )
                _touch_normalized_store(conn)
            conn.commit()
            return len(ids)
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()
    payload = _read_store(db_path)
    items = payload.get("changeSets") if isinstance(payload.get("changeSets"), list) else []
    kept = [
        item for item in items
        if not isinstance(item, dict) or str(item.get("chatId") or "") != str(chat_id)
    ]
    removed = len(items) - len(kept)
    if removed:
        payload["changeSets"] = kept
        _write_store(db_path, payload)
    return removed


__all__ = [
    "WorkspaceSnapshot",
    "build_change_set",
    "capture_workspace_snapshot",
    "compare_workspace_snapshots",
    "delete_chat_change_sets",
    "get_chat_file_change",
    "is_cyrene_managed_workspace_path",
    "list_chat_change_sets",
    "save_change_set",
]
