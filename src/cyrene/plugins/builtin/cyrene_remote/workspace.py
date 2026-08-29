"""Remote Plugin project-confined workspace files and durable process jobs.

The wire protocol carries fixed operations.  File bytes are chunked here and
never enter an Agent prompt; upload staging lives outside the shared project
until a verified atomic commit.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import os
import shutil
import signal
import sqlite3
import stat as stat_module
from contextvars import ContextVar
from pathlib import Path, PurePosixPath
from typing import Any, Awaitable, Callable
from uuid import uuid4

from .control import RemoteControlStore, utc_iso
from cyrene.workbench.projects.project_repository import (
    find_workbench_project_lightweight,
    resolve_project_workspace_dir,
)

TRANSFER_CHUNK_BYTES = 512 * 1024
MAX_TRANSFER_CHUNK_BYTES = 1024 * 1024
MAX_MANIFEST_ENTRIES = 50_000
MAX_JOB_LOG_READ_BYTES = 256 * 1024
_remote_outside_workspace: ContextVar[bool] = ContextVar(
    "remote_outside_workspace",
    default=False,
)


def _text(payload: dict[str, Any], key: str, *, required: bool = True) -> str:
    value = str(payload.get(key) or "").strip()
    if required and not value:
        raise ValueError(f"{key} is required")
    if "\x00" in value:
        raise ValueError(f"{key} is invalid")
    return value


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


class RemoteWorkspaceFiles:
    def __init__(self, store: RemoteControlStore) -> None:
        self.store = store
        self.transfer_dir = Path(store.remote_db_path + ".transfers")
        self.transfer_dir.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def _workspace(project_id: str) -> Path:
        project = find_workbench_project_lightweight(project_id)
        if project is None:
            raise ValueError("authorized project no longer exists")
        raw = resolve_project_workspace_dir(project)
        if not raw:
            raise ValueError("shared project has no workspace")
        root = Path(raw).expanduser().resolve()
        if not root.is_dir():
            raise ValueError("shared project workspace is unavailable")
        return root

    @staticmethod
    def _target(
        root: Path,
        raw_path: str,
        *,
        allow_root: bool = False,
        allow_outside: bool | None = None,
    ) -> Path:
        value = str(raw_path or ".").replace("\\", "/").strip()
        pure = PurePosixPath(value)
        outside_allowed = (
            _remote_outside_workspace.get()
            if allow_outside is None
            else bool(allow_outside)
        )
        if any(part in {"", ".."} for part in pure.parts):
            raise ValueError("remote path must be project-relative")
        if pure.parts and ":" in pure.parts[0]:
            raise ValueError("remote path must be project-relative")
        if pure.is_absolute():
            if not outside_allowed:
                raise ValueError(
                    "absolute remote paths require authorization from the controller's local permission mode"
                )
            unresolved = Path(value)
        else:
            unresolved = root / Path(*pure.parts)
        candidate = unresolved.resolve(strict=False)
        if not outside_allowed and candidate != root and root not in candidate.parents:
            raise ValueError("remote path escapes the shared project")
        if candidate == root and not allow_root:
            raise ValueError("operation requires a project-relative path")
        current = Path(unresolved.anchor) if unresolved.is_absolute() else root
        parts = unresolved.parts[1:] if unresolved.is_absolute() else pure.parts
        for part in parts:
            current /= part
            if current.exists() and current.is_symlink():
                raise ValueError("symbolic links are disabled for remote files")
        return candidate

    @staticmethod
    def _display_path(path: Path, root: Path) -> str:
        if path == root:
            return "."
        try:
            return path.relative_to(root).as_posix()
        except ValueError:
            return str(path)

    @staticmethod
    def _public_stat(path: Path, root: Path, *, include_hash: bool = False) -> dict[str, Any]:
        info = path.stat()
        kind = "directory" if path.is_dir() else "file"
        result = {
            "path": RemoteWorkspaceFiles._display_path(path, root),
            "kind": kind,
            "size": int(info.st_size) if kind == "file" else 0,
            "modified_ns": int(info.st_mtime_ns),
            "mode": stat_module.S_IMODE(info.st_mode),
        }
        if include_hash and kind == "file":
            result["sha256"] = _sha256_file(path)
        return result

    def _transfer_row(self, transfer_id: str) -> sqlite3.Row:
        with self.store._lock, self.store._connect() as conn:
            row = conn.execute(
                "SELECT * FROM remote_file_transfers WHERE transfer_id = ?",
                (transfer_id,),
            ).fetchone()
        if row is None:
            raise ValueError("remote file transfer not found")
        return row

    @staticmethod
    def _check_transfer_owner(row: sqlite3.Row, peer: str, project: str) -> None:
        if str(row["peer_device_id"]) != peer or str(row["project_id"]) != project:
            raise PermissionError("remote file transfer belongs to another scope")

    async def execute(
        self,
        peer_device_id: str,
        command: str,
        project_id: str,
        payload: dict[str, Any],
        *,
        allow_outside: bool = False,
    ) -> dict[str, Any]:
        token = _remote_outside_workspace.set(bool(allow_outside))
        try:
            return await self._execute(peer_device_id, command, project_id, payload)
        finally:
            _remote_outside_workspace.reset(token)

    async def _execute(
        self,
        peer_device_id: str,
        command: str,
        project_id: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        root = self._workspace(project_id)
        operation = command.removeprefix("files.")
        peer = self.store.get_peer(peer_device_id)
        granted = set((peer or {}).get("granted_capabilities") or [])
        overwrite_requested = (
            operation == "upload.begin"
            and str(payload.get("conflict_policy") or "fail")
            in {"overwrite", "overwrite_if_unchanged"}
        ) or (
            operation in {"copy", "move"} and bool(payload.get("overwrite"))
        )
        if overwrite_requested and "workspace_file:overwrite" not in granted:
            raise PermissionError("remote file overwrite is not granted")
        if operation in {"read", "download"}:
            return await asyncio.to_thread(self._read, root, payload)
        if operation == "stat":
            path = self._target(root, _text(payload, "path"), allow_root=True)
            if not path.exists():
                return {"ok": False, "code": "remote_file_not_found", "error": "path not found"}
            return {"ok": True, "entry": self._public_stat(path, root, include_hash=bool(payload.get("include_hash")))}
        if operation == "hash":
            path = self._target(root, _text(payload, "path"))
            if not path.is_file():
                raise ValueError("path is not a regular file")
            return {"ok": True, "path": self._display_path(path, root), "size": path.stat().st_size, "sha256": await asyncio.to_thread(_sha256_file, path)}
        if operation == "list":
            return await asyncio.to_thread(self._list, root, payload)
        if operation == "manifest":
            return await asyncio.to_thread(self._manifest, root, payload)
        if operation == "upload.begin":
            return await asyncio.to_thread(self._upload_begin, peer_device_id, project_id, root, payload)
        if operation == "upload.chunk":
            return await asyncio.to_thread(self._upload_chunk, peer_device_id, project_id, payload)
        if operation == "upload.commit":
            return await asyncio.to_thread(self._upload_commit, peer_device_id, project_id, root, payload)
        if operation == "upload.abort":
            return await asyncio.to_thread(self._upload_abort, peer_device_id, project_id, payload)
        if operation == "mkdir":
            path = self._target(root, _text(payload, "path"))
            path.mkdir(parents=bool(payload.get("parents", True)), exist_ok=bool(payload.get("exist_ok", True)))
            return {"ok": True, "entry": self._public_stat(path, root)}
        if operation == "touch":
            path = self._target(root, _text(payload, "path"))
            path.parent.mkdir(parents=True, exist_ok=True)
            path.touch(exist_ok=bool(payload.get("exist_ok", True)))
            return {"ok": True, "entry": self._public_stat(path, root)}
        if operation == "copy":
            return await asyncio.to_thread(self._copy, root, payload)
        if operation == "move":
            return await asyncio.to_thread(self._move, root, payload)
        if operation in {"delete", "delete_tree"}:
            return await asyncio.to_thread(self._delete, root, payload, operation == "delete_tree")
        if operation == "apply_patch":
            return await asyncio.to_thread(self._apply_patch, root, payload)
        if operation in {"sync.prepare", "sync.diff"}:
            return await asyncio.to_thread(self._sync_diff, root, payload)
        if operation == "sync.apply":
            return await asyncio.to_thread(self._sync_apply, root, payload)
        if operation == "sync.commit":
            result = self._manifest(root, payload)
            return {**result, "sync_id": str(payload.get("sync_id") or "")}
        if operation == "sync.abort":
            return {"ok": True, "aborted": True, "sync_id": str(payload.get("sync_id") or "")}
        raise ValueError("unsupported remote file operation")

    def _read(self, root: Path, payload: dict[str, Any]) -> dict[str, Any]:
        path = self._target(root, _text(payload, "path"))
        if not path.is_file():
            return {"ok": False, "code": "remote_file_not_found", "error": "file not found"}
        size = path.stat().st_size
        offset = max(0, int(payload.get("offset") or 0))
        limit = max(1, min(int(payload.get("limit") or TRANSFER_CHUNK_BYTES), MAX_TRANSFER_CHUNK_BYTES))
        if offset > size:
            raise ValueError("file offset exceeds size")
        with path.open("rb") as handle:
            handle.seek(offset)
            chunk = handle.read(limit)
        next_offset = offset + len(chunk)
        return {
            "ok": True,
            "path": self._display_path(path, root),
            "filename": path.name,
            "size": size,
            "sha256": _sha256_file(path) if bool(payload.get("include_hash")) else "",
            "offset": offset,
            "next_offset": next_offset,
            "eof": next_offset >= size,
            "chunk_sha256": hashlib.sha256(chunk).hexdigest(),
            "content_base64": base64.b64encode(chunk).decode("ascii"),
        }

    def _list(self, root: Path, payload: dict[str, Any]) -> dict[str, Any]:
        directory = self._target(root, str(payload.get("path") or "."), allow_root=True)
        if not directory.is_dir():
            raise ValueError("path is not a directory")
        include_hash = bool(payload.get("include_hash"))
        entries = []
        for path in sorted(directory.iterdir(), key=lambda item: item.name.lower()):
            if path.is_symlink():
                continue
            entries.append(self._public_stat(path, root, include_hash=include_hash))
        return {"ok": True, "path": self._display_path(directory, root), "entries": entries}

    def _manifest(self, root: Path, payload: dict[str, Any]) -> dict[str, Any]:
        directory = self._target(root, str(payload.get("path") or "."), allow_root=True)
        if not directory.is_dir():
            raise ValueError("path is not a directory")
        include_hash = bool(payload.get("include_hash", True))
        entries: list[dict[str, Any]] = []
        for path in sorted(directory.rglob("*")):
            if path.is_symlink():
                continue
            entries.append(self._public_stat(path, root, include_hash=include_hash))
            if len(entries) > MAX_MANIFEST_ENTRIES:
                raise ValueError("directory manifest exceeds entry limit")
        return {"ok": True, "path": self._display_path(directory, root), "entries": entries}

    def _upload_begin(self, peer: str, project: str, root: Path, payload: dict[str, Any]) -> dict[str, Any]:
        relative = _text(payload, "path")
        self._target(root, relative)
        transfer_id = str(payload.get("transfer_id") or ("transfer_" + uuid4().hex))
        expected_size = max(0, int(payload.get("size") or 0))
        expected_sha = str(payload.get("sha256") or "").lower()
        if expected_sha and (len(expected_sha) != 64 or any(ch not in "0123456789abcdef" for ch in expected_sha)):
            raise ValueError("sha256 is invalid")
        policy = str(payload.get("conflict_policy") or "fail")
        if policy not in {"fail", "skip", "rename", "overwrite", "overwrite_if_unchanged"}:
            raise ValueError("unsupported file conflict policy")
        staging = self.transfer_dir / f"{transfer_id}.part"
        now = utc_iso()
        with self.store._lock, self.store._connect() as conn:
            existing = conn.execute("SELECT * FROM remote_file_transfers WHERE transfer_id = ?", (transfer_id,)).fetchone()
            if existing is not None:
                self._check_transfer_owner(existing, peer, project)
                if str(existing["relative_path"]) != relative or int(existing["expected_size"]) != expected_size or str(existing["expected_sha256"]) != expected_sha:
                    raise ValueError("transfer id conflicts with another upload")
                existing_state = str(existing["state"])
                if existing_state in {"complete", "skipped"}:
                    return {"ok": True, "transfer_id": transfer_id, "offset": int(existing["received_size"]), "chunk_bytes": TRANSFER_CHUNK_BYTES, "resumed": True, "state": existing_state}
                if existing_state != "active":
                    Path(str(existing["staging_path"])).unlink(missing_ok=True)
                    conn.execute("DELETE FROM remote_file_transfers WHERE transfer_id = ?", (transfer_id,))
                else:
                    return {"ok": True, "transfer_id": transfer_id, "offset": int(existing["received_size"]), "chunk_bytes": TRANSFER_CHUNK_BYTES, "resumed": True, "state": "active"}
            staging.touch(exist_ok=False)
            conn.execute(
                """INSERT INTO remote_file_transfers(
                    transfer_id, peer_device_id, project_id, direction,
                    relative_path, staging_path, expected_size, expected_sha256,
                    received_size, conflict_policy, state, created_at, updated_at
                ) VALUES (?, ?, ?, 'upload', ?, ?, ?, ?, 0, ?, 'active', ?, ?)""",
                (transfer_id, peer, project, relative, str(staging), expected_size, expected_sha, policy, now, now),
            )
        self.store.audit(
            "remote_file_transfer_started",
            peer_device_id=peer,
            command="files.upload",
            outcome="active",
            detail={"transfer_id": transfer_id, "project_id": project, "path": relative, "size": expected_size},
        )
        return {"ok": True, "transfer_id": transfer_id, "offset": 0, "chunk_bytes": TRANSFER_CHUNK_BYTES, "resumed": False, "state": "active"}

    def _upload_chunk(self, peer: str, project: str, payload: dict[str, Any]) -> dict[str, Any]:
        transfer_id = _text(payload, "transfer_id")
        row = self._transfer_row(transfer_id)
        self._check_transfer_owner(row, peer, project)
        if str(row["state"]) != "active":
            raise ValueError("remote file transfer is not active")
        encoded = _text(payload, "content_base64", required=False)
        try:
            chunk = base64.b64decode(encoded, validate=True) if encoded else b""
        except Exception as exc:
            raise ValueError("file chunk is not valid base64") from exc
        if len(chunk) > MAX_TRANSFER_CHUNK_BYTES:
            raise ValueError("file chunk exceeds negotiated limit")
        expected_chunk_sha = str(payload.get("chunk_sha256") or "").lower()
        actual_chunk_sha = hashlib.sha256(chunk).hexdigest()
        if expected_chunk_sha and expected_chunk_sha != actual_chunk_sha:
            raise ValueError("file chunk checksum mismatch")
        offset = max(0, int(payload.get("offset") or 0))
        current = int(row["received_size"])
        staging = Path(str(row["staging_path"]))
        if offset < current:
            with staging.open("rb") as handle:
                handle.seek(offset)
                existing = handle.read(len(chunk))
            if existing != chunk:
                raise ValueError("replayed file chunk differs from stored content")
            return {"ok": True, "transfer_id": transfer_id, "offset": offset, "next_offset": offset + len(chunk), "duplicate": True}
        if offset != current:
            raise ValueError("file chunk offset is not contiguous")
        with staging.open("ab") as handle:
            handle.write(chunk)
            handle.flush()
            os.fsync(handle.fileno())
        next_offset = current + len(chunk)
        with self.store._lock, self.store._connect() as conn:
            conn.execute("UPDATE remote_file_transfers SET received_size = ?, updated_at = ? WHERE transfer_id = ?", (next_offset, utc_iso(), transfer_id))
        return {"ok": True, "transfer_id": transfer_id, "offset": offset, "next_offset": next_offset, "chunk_sha256": actual_chunk_sha}

    def _upload_commit(self, peer: str, project: str, root: Path, payload: dict[str, Any]) -> dict[str, Any]:
        transfer_id = _text(payload, "transfer_id")
        row = self._transfer_row(transfer_id)
        self._check_transfer_owner(row, peer, project)
        staging = Path(str(row["staging_path"]))
        if not staging.is_file():
            raise ValueError("upload staging file is unavailable")
        size = staging.stat().st_size
        if size != int(row["expected_size"]):
            raise ValueError("uploaded file size does not match manifest")
        actual_sha = _sha256_file(staging)
        expected_sha = str(row["expected_sha256"])
        if expected_sha and actual_sha != expected_sha:
            raise ValueError("uploaded file checksum mismatch")
        target = self._target(root, str(row["relative_path"]))
        policy = str(row["conflict_policy"])
        if target.exists():
            if policy == "skip":
                staging.unlink(missing_ok=True)
                self._finish_transfer(transfer_id, "skipped")
                return {"ok": True, "transfer_id": transfer_id, "skipped": True, "path": self._display_path(target, root)}
            if policy == "fail":
                raise FileExistsError("remote file already exists")
            if policy == "overwrite_if_unchanged":
                expected_target = str(payload.get("expected_target_sha256") or "")
                if not expected_target or not target.is_file() or _sha256_file(target) != expected_target:
                    raise ValueError("remote file changed since conflict check")
            if policy == "rename":
                index = 1
                base = target
                while target.exists():
                    target = base.with_name(f"{base.stem} ({index}){base.suffix}")
                    index += 1
        target.parent.mkdir(parents=True, exist_ok=True)
        os.replace(staging, target)
        self._finish_transfer(transfer_id, "complete")
        self.store.audit(
            "remote_file_transfer_completed",
            peer_device_id=peer,
            command="files.upload",
            outcome="complete",
            detail={"transfer_id": transfer_id, "project_id": project, "path": self._display_path(target, root), "size": size, "sha256": actual_sha},
        )
        return {"ok": True, "transfer_id": transfer_id, "path": self._display_path(target, root), "size": size, "sha256": actual_sha, "committed": True}

    def _finish_transfer(self, transfer_id: str, state: str) -> None:
        with self.store._lock, self.store._connect() as conn:
            conn.execute("UPDATE remote_file_transfers SET state = ?, updated_at = ? WHERE transfer_id = ?", (state, utc_iso(), transfer_id))

    def _upload_abort(self, peer: str, project: str, payload: dict[str, Any]) -> dict[str, Any]:
        transfer_id = _text(payload, "transfer_id")
        row = self._transfer_row(transfer_id)
        self._check_transfer_owner(row, peer, project)
        Path(str(row["staging_path"])).unlink(missing_ok=True)
        self._finish_transfer(transfer_id, "aborted")
        self.store.audit(
            "remote_file_transfer_aborted",
            peer_device_id=peer,
            command="files.upload",
            outcome="aborted",
            detail={"transfer_id": transfer_id, "project_id": project},
        )
        return {"ok": True, "transfer_id": transfer_id, "aborted": True}

    def _copy(self, root: Path, payload: dict[str, Any]) -> dict[str, Any]:
        source = self._target(root, _text(payload, "source"))
        target = self._target(root, _text(payload, "destination"))
        if target.exists() and not bool(payload.get("overwrite")):
            raise FileExistsError("destination already exists")
        target.parent.mkdir(parents=True, exist_ok=True)
        if source.is_dir():
            if any(path.is_symlink() for path in source.rglob("*")):
                raise ValueError("symbolic links are disabled for remote files")
            shutil.copytree(source, target, dirs_exist_ok=bool(payload.get("overwrite")), symlinks=False)
        else:
            shutil.copy2(source, target)
        return {"ok": True, "entry": self._public_stat(target, root)}

    def _move(self, root: Path, payload: dict[str, Any]) -> dict[str, Any]:
        source = self._target(root, _text(payload, "source"))
        target = self._target(root, _text(payload, "destination"))
        if target.exists() and not bool(payload.get("overwrite")):
            raise FileExistsError("destination already exists")
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            if target.is_dir():
                shutil.rmtree(target)
            else:
                target.unlink()
        os.replace(source, target)
        return {"ok": True, "entry": self._public_stat(target, root)}

    def _delete(self, root: Path, payload: dict[str, Any], recursive: bool) -> dict[str, Any]:
        path = self._target(root, _text(payload, "path"))
        if not path.exists():
            return {"ok": True, "deleted": False, "path": self._display_path(path, root)}
        if path.is_dir():
            if not recursive:
                path.rmdir()
            else:
                shutil.rmtree(path)
        else:
            path.unlink()
        return {"ok": True, "deleted": True, "path": self._display_path(path, root)}

    def _apply_patch(self, root: Path, payload: dict[str, Any]) -> dict[str, Any]:
        path = self._target(root, _text(payload, "path"))
        if not path.is_file():
            raise ValueError("patch target is not a regular file")
        original = path.read_text(encoding=str(payload.get("encoding") or "utf-8"))
        edits = payload.get("edits")
        if not isinstance(edits, list) or not edits:
            raise ValueError("patch edits are required")
        updated = original
        for edit in edits:
            if not isinstance(edit, dict):
                raise ValueError("patch edit is invalid")
            old = str(edit.get("old") or "")
            new = str(edit.get("new") or "")
            count = int(edit.get("count") or 1)
            if not old or updated.count(old) < count:
                raise ValueError("patch context did not match target")
            updated = updated.replace(old, new, count)
        expected = str(payload.get("expected_sha256") or "")
        if expected and hashlib.sha256(original.encode()).hexdigest() != expected:
            raise ValueError("patch target changed since it was read")
        temporary = path.with_name(path.name + ".cyrene-patch-" + uuid4().hex)
        temporary.write_text(updated, encoding=str(payload.get("encoding") or "utf-8"))
        os.replace(temporary, path)
        return {"ok": True, "entry": self._public_stat(path, root, include_hash=True)}

    def _sync_diff(self, root: Path, payload: dict[str, Any]) -> dict[str, Any]:
        base = self._target(root, str(payload.get("path") or "."), allow_root=True)
        client_entries = payload.get("entries")
        if not isinstance(client_entries, list):
            raise ValueError("sync manifest entries are required")
        if base.exists() and not base.is_dir():
            raise ValueError("sync destination is not a directory")
        remote = (
            self._manifest(
                root,
                {
                    "path": self._display_path(base, root),
                    "include_hash": True,
                },
            )["entries"]
            if base.exists()
            else []
        )
        remote_map = {str(item["path"]): item for item in remote}
        client_map = {
            str(item.get("path") or ""): item
            for item in client_entries
            if isinstance(item, dict) and str(item.get("path") or "")
        }
        upload = [
            path
            for path, item in client_map.items()
            if item.get("kind", "file") == "file"
            and (
                path not in remote_map
                or remote_map[path].get("kind") != "file"
                or str(item.get("sha256") or "")
                != str(remote_map[path].get("sha256") or "")
            )
        ]
        delete = sorted(
            (path for path in remote_map if path not in client_map),
            key=lambda value: (value.count("/"), value),
            reverse=True,
        )
        return {"ok": True, "sync_id": str(payload.get("sync_id") or ("sync_" + uuid4().hex)), "upload": sorted(upload), "delete": delete, "remote_entries": remote}

    def _sync_apply(self, root: Path, payload: dict[str, Any]) -> dict[str, Any]:
        created = []
        deleted = []
        for raw in payload.get("directories") or []:
            path = self._target(root, str(raw))
            path.mkdir(parents=True, exist_ok=True)
            created.append(self._display_path(path, root))
        if bool(payload.get("delete_extraneous")):
            for raw in sorted((str(item) for item in payload.get("delete") or []), reverse=True):
                path = self._target(root, raw)
                if path.is_dir():
                    shutil.rmtree(path)
                elif path.exists():
                    path.unlink()
                deleted.append(raw)
        return {"ok": True, "sync_id": str(payload.get("sync_id") or ""), "created": created, "deleted": deleted}


class RemoteJobManager:
    def __init__(self, store: RemoteControlStore) -> None:
        self.store = store
        self.files = RemoteWorkspaceFiles(store)
        self.job_dir = Path(store.remote_db_path + ".jobs")
        self.job_dir.mkdir(parents=True, exist_ok=True)
        self._processes: dict[str, asyncio.subprocess.Process] = {}
        self._log_handles: dict[str, Any] = {}
        self._watchers: dict[str, asyncio.Task[None]] = {}
        self._event_sender: Callable[[str, dict[str, Any]], Awaitable[None]] | None = None
        with self.store._lock, self.store._connect() as conn:
            conn.execute("UPDATE remote_jobs SET status = 'interrupted', completed_at = ?, updated_at = ? WHERE status IN ('starting', 'running')", (utc_iso(), utc_iso()))

    def set_event_sender(
        self,
        sender: Callable[[str, dict[str, Any]], Awaitable[None]] | None,
    ) -> None:
        self._event_sender = sender

    def _job_row(self, job_id: str) -> sqlite3.Row:
        with self.store._lock, self.store._connect() as conn:
            row = conn.execute("SELECT * FROM remote_jobs WHERE job_id = ?", (job_id,)).fetchone()
        if row is None:
            raise ValueError("remote job not found")
        return row

    @staticmethod
    def _check_owner(row: sqlite3.Row, peer: str, project: str) -> None:
        if str(row["peer_device_id"]) != peer or str(row["project_id"]) != project:
            raise PermissionError("remote job belongs to another scope")

    async def execute(
        self,
        peer: str,
        command: str,
        project: str,
        payload: dict[str, Any],
        *,
        allow_outside: bool = False,
    ) -> dict[str, Any]:
        operation = command.removeprefix("jobs.")
        if operation == "start":
            return await self._start(
                peer,
                project,
                payload,
                allow_outside=allow_outside,
            )
        job_id = _text(payload, "job_id")
        row = self._job_row(job_id)
        self._check_owner(row, peer, project)
        if operation == "read":
            return await asyncio.to_thread(self._read, row, payload)
        if operation == "wait":
            process = self._processes.get(job_id)
            timeout = max(0.1, min(float(payload.get("timeout_seconds") or 25), 55.0))
            if process is not None and process.returncode is None:
                try:
                    await asyncio.wait_for(asyncio.shield(process.wait()), timeout=timeout)
                except asyncio.TimeoutError:
                    pass
                if process.returncode is not None:
                    watcher = self._watchers.get(job_id)
                    if watcher is not None:
                        await asyncio.shield(watcher)
            return await asyncio.to_thread(self._read, self._job_row(job_id), payload)
        if operation in {"cancel", "interrupt"}:
            return await self._stop(job_id, interrupt=operation == "interrupt")
        if operation == "artifacts":
            return self._artifacts(row, project)
        raise ValueError("unsupported remote job operation")

    async def _start(
        self,
        peer: str,
        project: str,
        payload: dict[str, Any],
        *,
        allow_outside: bool,
    ) -> dict[str, Any]:
        argv = payload.get("argv")
        if not isinstance(argv, list) or not argv or any(not isinstance(item, str) or not item for item in argv):
            raise ValueError("jobs.start requires a non-empty argv string array")
        if len(argv) > 256 or sum(len(item) for item in argv) > 64_000:
            raise ValueError("job command is too large")
        root = self.files._workspace(project)
        cwd = self.files._target(
            root,
            str(payload.get("cwd") or "."),
            allow_root=True,
            allow_outside=allow_outside,
        )
        if not cwd.is_dir():
            raise ValueError("job cwd is not a directory")
        job_id = str(payload.get("job_id") or ("job_" + uuid4().hex))
        log_path = self.job_dir / f"{job_id}.log"
        env = os.environ.copy()
        supplied_env = payload.get("env") or {}
        if not isinstance(supplied_env, dict) or len(supplied_env) > 100:
            raise ValueError("job env is invalid")
        for key, value in supplied_env.items():
            key = str(key)
            if not key or "\x00" in key or "=" in key:
                raise ValueError("job env key is invalid")
            env[key] = str(value)
        artifacts = [str(item) for item in payload.get("artifact_paths") or []][:100]
        for item in artifacts:
            self.files._target(root, item, allow_outside=allow_outside)
        now = utc_iso()
        command_hash = hashlib.sha256(
            json.dumps(
                {
                    "argv": argv,
                    "cwd": self.files._display_path(cwd, root),
                    "env": supplied_env,
                    "artifact_paths": artifacts,
                },
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode()
        ).hexdigest()
        with self.store._lock, self.store._connect() as conn:
            existing = conn.execute("SELECT * FROM remote_jobs WHERE job_id = ?", (job_id,)).fetchone()
            if existing is not None:
                self._check_owner(existing, peer, project)
                if str(existing["command_hash"]) != command_hash:
                    raise ValueError("job id conflicts with another command")
                return self._public(existing, cursor=int(payload.get("cursor") or 0))
            conn.execute(
                """INSERT INTO remote_jobs(job_id, peer_device_id, project_id,
                    command_hash, origin_chat_id, cwd_relative, outside_workspace,
                    log_path, status,
                    artifact_paths_json, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'starting', ?, ?, ?)""",
                (job_id, peer, project, command_hash, str(payload.get("origin_chat_id") or ""), self.files._display_path(cwd, root), int(allow_outside), str(log_path), json.dumps(artifacts), now, now),
            )
        handle = log_path.open("ab", buffering=0)
        try:
            process = await asyncio.create_subprocess_exec(
                *argv,
                cwd=str(cwd),
                env=env,
                stdin=asyncio.subprocess.DEVNULL,
                stdout=handle,
                stderr=asyncio.subprocess.STDOUT,
                start_new_session=True,
            )
        except Exception:
            handle.close()
            with self.store._lock, self.store._connect() as conn:
                conn.execute("UPDATE remote_jobs SET status = 'failed', completed_at = ?, updated_at = ? WHERE job_id = ?", (utc_iso(), utc_iso(), job_id))
            raise
        self._processes[job_id] = process
        self._log_handles[job_id] = handle
        with self.store._lock, self.store._connect() as conn:
            conn.execute("UPDATE remote_jobs SET pid = ?, status = 'running', started_at = ?, updated_at = ? WHERE job_id = ?", (process.pid, utc_iso(), utc_iso(), job_id))
        self._watchers[job_id] = asyncio.create_task(
            self._watch(job_id, process),
            name=f"remote-job-{job_id}",
        )
        self.store.audit(
            "remote_job_started",
            peer_device_id=peer,
            command="jobs.start",
            outcome="running",
            detail={"job_id": job_id, "project_id": project, "command_sha256": command_hash},
        )
        return self._public(self._job_row(job_id), cursor=0)

    async def _watch(self, job_id: str, process: asyncio.subprocess.Process) -> None:
        exit_code = await process.wait()
        status = "completed" if exit_code == 0 else "failed"
        with self.store._lock, self.store._connect() as conn:
            current = conn.execute("SELECT status FROM remote_jobs WHERE job_id = ?", (job_id,)).fetchone()
            if current is not None and str(current["status"]) in {"cancelling", "interrupting"}:
                status = "cancelled" if str(current["status"]) == "cancelling" else "interrupted"
            conn.execute("UPDATE remote_jobs SET status = ?, exit_code = ?, completed_at = ?, updated_at = ? WHERE job_id = ?", (status, exit_code, utc_iso(), utc_iso(), job_id))
        handle = self._log_handles.pop(job_id, None)
        if handle is not None:
            handle.close()
        self._processes.pop(job_id, None)
        row = self._job_row(job_id)
        self.store.audit(
            "remote_job_completed",
            peer_device_id=str(row["peer_device_id"]),
            command="jobs.start",
            outcome=status,
            detail={"job_id": job_id, "project_id": str(row["project_id"]), "exit_code": exit_code},
        )
        if self._event_sender is not None:
            try:
                await self._event_sender(
                    str(row["peer_device_id"]),
                    {
                        "type": "remote_job_update",
                        "session_id": str(row["origin_chat_id"] or ""),
                        "project_id": str(row["project_id"]),
                        "job_id": job_id,
                        "status": status,
                        "exit_code": exit_code,
                    },
                )
            except Exception:
                self.store.audit(
                    "remote_job_event_deferred",
                    peer_device_id=str(row["peer_device_id"]),
                    command="jobs.start",
                    outcome="offline",
                    detail={"job_id": job_id},
                )
        self._watchers.pop(job_id, None)

    async def _stop(self, job_id: str, *, interrupt: bool) -> dict[str, Any]:
        process = self._processes.get(job_id)
        if process is None or process.returncode is not None:
            return self._public(self._job_row(job_id), cursor=0)
        status = "interrupting" if interrupt else "cancelling"
        with self.store._lock, self.store._connect() as conn:
            conn.execute("UPDATE remote_jobs SET status = ?, updated_at = ? WHERE job_id = ?", (status, utc_iso(), job_id))
        try:
            os.killpg(process.pid, signal.SIGINT if interrupt else signal.SIGTERM)
        except (ProcessLookupError, PermissionError):
            process.send_signal(signal.SIGINT if interrupt else signal.SIGTERM)
        try:
            await asyncio.wait_for(asyncio.shield(process.wait()), timeout=5.0)
        except asyncio.TimeoutError:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                process.kill()
            await process.wait()
        watcher = self._watchers.get(job_id)
        if watcher is not None:
            await asyncio.shield(watcher)
        return self._public(self._job_row(job_id), cursor=0)

    def _public(self, row: sqlite3.Row, *, cursor: int) -> dict[str, Any]:
        log = self._read(row, {"cursor": cursor})
        return {**log, "job_id": str(row["job_id"]), "status": str(row["status"]), "pid": int(row["pid"]), "exit_code": row["exit_code"], "created_at": str(row["created_at"]), "started_at": str(row["started_at"]), "completed_at": str(row["completed_at"])}

    def _read(self, row: sqlite3.Row, payload: dict[str, Any]) -> dict[str, Any]:
        path = Path(str(row["log_path"]))
        size = path.stat().st_size if path.exists() else 0
        cursor = max(0, min(int(payload.get("cursor") or 0), size))
        limit = max(1, min(int(payload.get("limit") or MAX_JOB_LOG_READ_BYTES), MAX_JOB_LOG_READ_BYTES))
        data = b""
        if path.exists():
            with path.open("rb") as handle:
                handle.seek(cursor)
                data = handle.read(limit)
        next_cursor = cursor + len(data)
        return {"ok": True, "job_id": str(row["job_id"]), "status": str(row["status"]), "exit_code": row["exit_code"], "cursor": cursor, "next_cursor": next_cursor, "log_size": size, "eof": next_cursor >= size, "output": data.decode("utf-8", errors="replace")}

    def _artifacts(self, row: sqlite3.Row, project: str) -> dict[str, Any]:
        root = self.files._workspace(project)
        artifacts = []
        for raw in json.loads(str(row["artifact_paths_json"] or "[]")):
            path = self.files._target(
                root,
                str(raw),
                allow_outside=bool(row["outside_workspace"]),
            )
            if path.exists() and not path.is_symlink():
                artifacts.append(self.files._public_stat(path, root, include_hash=path.is_file()))
        return {"ok": True, "job_id": str(row["job_id"]), "artifacts": artifacts}


__all__ = [
    "MAX_TRANSFER_CHUNK_BYTES",
    "RemoteJobManager",
    "RemoteWorkspaceFiles",
    "TRANSFER_CHUNK_BYTES",
]
