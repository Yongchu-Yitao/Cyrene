"""Harness-owned workspace change tracking for Workbench chat runs.

The tracker deliberately does not inspect Git.  It snapshots the workspace at
the start and end of one agent run, computes durable text diffs, and persists a
run-scoped change set outside the public chat transcript.
"""

from __future__ import annotations

import difflib
import hashlib
import os
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from cyrene.config import DATA_DIR
from cyrene.io_utils import atomic_write_json, read_json_safe
from cyrene.workbench_store import read_document, write_document


_LEGACY_STORE = DATA_DIR / "workbench_chat_changes.json"
_IGNORED_DIRS = {
    ".git", ".hg", ".svn", ".idea", ".pytest_cache", ".mypy_cache",
    ".ruff_cache", ".tox", ".venv", "__pycache__", "node_modules",
}
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


@dataclass(frozen=True)
class WorkspaceFileState:
    mtime_ns: int
    size: int
    digest: str
    text: str | None
    ctime_ns: int = 0


@dataclass(frozen=True)
class WorkspaceSnapshot:
    root: Path
    files: dict[str, WorkspaceFileState]
    captured_at: str


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
        )
    except OSError:
        return None


def capture_workspace_snapshot(
    workspace_root: str | Path | None,
    *,
    previous: WorkspaceSnapshot | None = None,
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

    files: dict[str, WorkspaceFileState] = {}
    captured_text_bytes = 0
    captured_text_files = 0
    try:
        for current, dirnames, filenames in os.walk(root):
            dirnames[:] = [name for name in dirnames if name not in _IGNORED_DIRS]
            current_path = Path(current)
            for filename in filenames:
                target = current_path / filename
                try:
                    rel = target.relative_to(root).as_posix()
                    stat = target.stat()
                    size = stat.st_size
                except (OSError, ValueError):
                    continue
                prior = previous.files.get(rel) if previous is not None else None
                if (
                    prior is not None
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


def _same_file(before: WorkspaceFileState, after: WorkspaceFileState) -> bool:
    if before.digest and after.digest:
        return before.digest == after.digest
    return (
        before.mtime_ns == after.mtime_ns
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


def _read_store(db_path: str) -> dict[str, Any]:
    if db_path:
        return read_document(
            db_path,
            "chat_changes",
            lambda: {"changeSets": []},
        )
    data = read_json_safe(_LEGACY_STORE)
    return data if isinstance(data, dict) else {"changeSets": []}


def _write_store(db_path: str, payload: dict[str, Any]) -> None:
    if db_path:
        merged = write_document(
            db_path,
            "chat_changes",
            payload,
            lambda: {"changeSets": []},
        )
        payload.clear()
        payload.update(merged)
        return
    atomic_write_json(_LEGACY_STORE, payload)


def save_change_set(db_path: str, change_set: dict[str, Any]) -> dict[str, Any]:
    payload = _read_store(db_path)
    items = payload.setdefault("changeSets", [])
    change_id = str(change_set.get("id") or "")
    replacement = dict(change_set)
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
    result["files"] = [
        {key: value for key, value in item.items() if key != "diff"}
        for item in change_set.get("files") or []
        if isinstance(item, dict)
    ]
    return result


def list_chat_change_sets(db_path: str, chat_id: str) -> list[dict[str, Any]]:
    payload = _read_store(db_path)
    items = [
        _public_change_set(item)
        for item in payload.get("changeSets") or []
        if isinstance(item, dict) and str(item.get("chatId") or "") == str(chat_id)
    ]
    items.sort(key=lambda item: str(item.get("completedAt") or ""), reverse=True)
    return items


def get_chat_file_change(
    db_path: str,
    chat_id: str,
    change_set_id: str,
    file_path: str,
) -> dict[str, Any] | None:
    payload = _read_store(db_path)
    for change_set in payload.get("changeSets") or []:
        if not isinstance(change_set, dict):
            continue
        if str(change_set.get("chatId") or "") != str(chat_id):
            continue
        if str(change_set.get("id") or "") != str(change_set_id):
            continue
        for item in change_set.get("files") or []:
            if isinstance(item, dict) and str(item.get("path") or "") == str(file_path):
                return dict(item)
    return None


def delete_chat_change_sets(db_path: str, chat_id: str) -> int:
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
    "list_chat_change_sets",
    "save_change_set",
]
