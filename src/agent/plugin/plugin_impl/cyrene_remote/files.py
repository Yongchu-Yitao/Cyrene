"""High-level bidirectional remote workspace file channel."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
from pathlib import Path, PurePosixPath
from typing import Any
from uuid import uuid4

from agent.plugin import PluginContext
from cyrene.config import DATA_DIR
from cyrene.runtime.attachments import register_generated_attachment, safe_attachment_filename
from .common import (
    remote_tool_error,
    request_remote_command,
    resolve_selected_remote_device,
)
from agent.plugin.execution import publish_plugin_progress as publish_tool_progress
from agent.plugin.native_runtime import (
    json_result,
    resolve_tool_path,
    run_context_value,
)

TOOL_NAME = "RemoteCyreneFiles"
TOOL_DEF = {
    "type": "function",
    "function": {
        "name": TOOL_NAME,
        "description": (
            "Transfer and manage remote files or directories. Relative paths are based "
            "at the explicitly shared project; absolute device paths follow the current "
            "controller chat's local permission mode. Upload/download/sync bytes use a "
            "resumable encrypted runtime channel; never encode or split file contents "
            "manually in shell."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "device_id": {"type": "string"},
                "project_id": {"type": "string"},
                "operation": {
                    "type": "string",
                    "enum": [
                        "stat", "list", "manifest", "download", "upload", "sync",
                        "mkdir", "touch", "copy", "move", "apply_patch", "delete", "delete_tree",
                    ],
                },
                "local_path": {"type": "string"},
                "remote_path": {"type": "string"},
                "source": {"type": "string"},
                "destination": {"type": "string"},
                "conflict_policy": {
                    "type": "string",
                    "enum": ["fail", "skip", "rename", "overwrite", "overwrite_if_unchanged"],
                },
                "delete_extraneous": {"type": "boolean"},
                "payload": {"type": "object"},
                "reason": {"type": "string"},
                "timeout_seconds": {"type": "number", "minimum": 1, "maximum": 120},
            },
            "required": ["project_id", "operation"],
            "additionalProperties": False,
        },
    },
}
TOOL_METADATA = {
    "read_only": False,
    "resource_keys": ("remote:{device_id}",),
    "requires_order": True,
}

_CHUNK_BYTES = 512 * 1024


async def _request(
    args: dict[str, Any],
    context: PluginContext,
    *,
    command: str,
    payload: dict[str, Any],
    key: str = "",
) -> dict[str, Any]:
    wire_payload = dict(payload)
    authorization_arguments = {
        "device_id": str(args.get("device_id") or ""),
        "project_id": str(args.get("project_id") or ""),
        "command": command,
        "payload": wire_payload,
    }
    authorization_hash = hashlib.sha256(
        json.dumps(
            authorization_arguments,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        ).encode("utf-8")
    ).hexdigest()
    wire_payload["_authorization"] = {
        "version": 1,
        "approved": True,
        "permission_mode": str(
            run_context_value(context, "permission_mode", "default")
            or "default"
        ),
        "scope": "single_operation",
        "outside_workspace": bool(args.get("_remote_allow_outside")),
        "arguments_sha256": authorization_hash,
    }
    return await request_remote_command(
        {
            "device_id": args.get("device_id"),
            "project_id": args.get("project_id"),
            "command": command,
            "payload": wire_payload,
            "idempotency_key": key or ("remote-file-" + uuid4().hex),
            "timeout_seconds": args.get("timeout_seconds"),
        },
        context,
    )


def _is_absolute_remote_path(value: Any) -> bool:
    text = str(value or "").replace("\\", "/").strip()
    return bool(text) and (PurePosixPath(text).is_absolute() or ":" in text.split("/", 1)[0])


def _operation_uses_absolute_path(args: dict[str, Any]) -> bool:
    payload = dict(args.get("payload") or {})
    values = [
        args.get("remote_path"),
        args.get("source"),
        args.get("destination"),
        payload.get("path"),
        payload.get("source"),
        payload.get("destination"),
    ]
    return any(_is_absolute_remote_path(value) for value in values)


async def handler(
    args: dict[str, Any],
    context: PluginContext,
) -> str:
    try:
        _chat, device = resolve_selected_remote_device(args, context)
        operation = str(args.get("operation") or "")
        project_id = str(args.get("project_id") or "").strip()
        if not project_id:
            raise ValueError("project_id is required")
        required = {
            "stat": "workspace_file:metadata",
            "list": "workspace_file:metadata",
            "manifest": "workspace_file:metadata",
            "download": "workspace_file:read",
            "upload": "workspace_file:write",
            "sync": "workspace_directory:transfer",
            "mkdir": "workspace_file:write",
            "touch": "workspace_file:write",
            "copy": "workspace_file:write",
            "move": "workspace_file:move",
            "apply_patch": "workspace_file:write",
            "delete": "workspace_file:delete",
            "delete_tree": "workspace_file:delete",
        }.get(operation)
        if not required:
            raise ValueError("unsupported remote file operation")
        if required not in (device.get("received_capabilities") or []):
            raise PermissionError(f"remote device did not grant {required}")

        remote_outside = _operation_uses_absolute_path(args)
        if operation == "upload":
            request_args = {
                **args,
                "device_id": str(device["device_id"]),
                "_remote_allow_outside": remote_outside,
            }
            return json_result(await _upload(request_args, context))
        if operation == "download":
            request_args = {
                **args,
                "device_id": str(device["device_id"]),
                "_remote_allow_outside": remote_outside,
            }
            return json_result(await _download(request_args, context))
        if operation == "sync":
            request_args = {
                **args,
                "device_id": str(device["device_id"]),
                "_remote_allow_outside": remote_outside,
            }
            return json_result(await _sync(request_args, context))

        payload = dict(args.get("payload") or {})
        if args.get("remote_path") and "path" not in payload:
            payload["path"] = args["remote_path"]
        if args.get("source") and "source" not in payload:
            payload["source"] = args["source"]
        if args.get("destination") and "destination" not in payload:
            payload["destination"] = args["destination"]
        command = f"files.{operation}"
        request_args = {
            **args,
            "device_id": str(device["device_id"]),
            "_remote_allow_outside": remote_outside,
        }
        return json_result(
            await _request(
                request_args,
                context,
                command=command,
                payload=payload,
            )
        )
    except Exception as exc:
        return json_result(remote_tool_error(exc))


async def _upload_file(
    args: dict[str, Any],
    context: PluginContext,
    local_path: Path,
    remote_path: str,
    *,
    conflict_policy: str,
    known_sha256: str = "",
    known_identity: tuple[int, int, int, int, int] | None = None,
) -> dict[str, Any]:
    current_identity = _file_identity(local_path)
    size = current_identity[2]
    sha256 = (
        known_sha256
        if known_sha256 and known_identity == current_identity
        else await _hash_file(local_path)
    )
    transfer_identity = json_result({
        "device_id": str(args.get("device_id") or ""),
        "project_id": str(args.get("project_id") or ""),
        "remote_path": remote_path,
        "size": size,
        "sha256": sha256,
        "conflict_policy": conflict_policy,
    })
    transfer_id = "transfer_" + hashlib.sha256(transfer_identity.encode()).hexdigest()[:40]
    begin = await _request(
        args,
        context,
        command="files.upload.begin",
        payload={
            "transfer_id": transfer_id,
            "path": remote_path,
            "size": size,
            "sha256": sha256,
            "conflict_policy": conflict_policy,
        },
        key=f"{transfer_id}:begin",
    )
    if begin.get("ok") is False:
        return begin
    if begin.get("state") in {"complete", "skipped"}:
        return {
            "ok": True,
            "transfer_id": transfer_id,
            "path": remote_path,
            "resumed": True,
            "state": begin.get("state"),
        }
    offset = int(begin.get("offset") or 0)
    try:
        with local_path.open("rb") as handle:
            handle.seek(offset)
            while offset < size:
                chunk = handle.read(_CHUNK_BYTES)
                if not chunk:
                    raise RuntimeError("local file ended before declared size")
                result = await _request(
                    args,
                    context,
                    command="files.upload.chunk",
                    payload={
                        "transfer_id": transfer_id,
                        "offset": offset,
                        "chunk_sha256": hashlib.sha256(chunk).hexdigest(),
                        "content_base64": base64.b64encode(chunk).decode("ascii"),
                    },
                    key=f"{transfer_id}:chunk:{offset}",
                )
                if result.get("ok") is False:
                    return result
                offset = int(result.get("next_offset") or 0)
                await publish_tool_progress(current=offset, total=size, label=local_path.name)
        return await _request(
            args,
            context,
            command="files.upload.commit",
            payload={"transfer_id": transfer_id},
            key=f"{transfer_id}:commit",
        )
    except BaseException:
        # Keep the verified staging bytes. A retry derives the same transfer id
        # and resumes from the target-reported offset.
        raise


async def _upload(
    args: dict[str, Any],
    context: PluginContext,
) -> dict[str, Any]:
    local = resolve_tool_path(str(args.get("local_path") or ""))
    if not local.is_file():
        raise ValueError("local_path must be a regular file; use sync for directories")
    remote = str(args.get("remote_path") or local.name).replace("\\", "/")
    return await _upload_file(
        args,
        context,
        local,
        remote,
        conflict_policy=str(args.get("conflict_policy") or "fail"),
    )


async def _download(
    args: dict[str, Any],
    context: PluginContext,
) -> dict[str, Any]:
    remote_path = str(args.get("remote_path") or "")
    if not remote_path:
        raise ValueError("remote_path is required")
    metadata = await _request(
        args,
        context,
        command="files.stat",
        payload={"path": remote_path, "include_hash": True},
    )
    if metadata.get("ok") is False:
        return metadata
    entry = dict(metadata.get("entry") or {})
    if entry.get("kind") != "file":
        raise ValueError("remote_path must be a regular file")
    total = int(entry.get("size") or 0)
    expected_sha = str(entry.get("sha256") or "")
    transfer_identity = json_result({
        "device_id": str(args.get("device_id") or ""),
        "project_id": str(args.get("project_id") or ""),
        "remote_path": remote_path,
        "size": total,
        "sha256": expected_sha,
    })
    transfer_id = "download_" + hashlib.sha256(transfer_identity.encode()).hexdigest()[:40]
    transfer_dir = DATA_DIR / "remote_transfers"
    transfer_dir.mkdir(parents=True, exist_ok=True)
    partial = transfer_dir / f"{transfer_id}.part"
    offset = partial.stat().st_size if partial.exists() else 0
    if offset > total:
        partial.unlink(missing_ok=True)
        offset = 0
    download_digest = hashlib.sha256()
    if offset:
        await asyncio.to_thread(_update_hash_from_file, partial, download_digest)
    try:
        with partial.open("ab") as handle:
            while True:
                result = await _request(
                    args,
                    context,
                    command="files.download",
                    payload={"path": remote_path, "offset": offset, "limit": _CHUNK_BYTES},
                    key=f"{transfer_id}:chunk:{offset}",
                )
                if result.get("ok") is False:
                    return result
                if int(result.get("offset") or 0) != offset:
                    raise RuntimeError("remote transfer returned an unexpected offset")
                chunk = base64.b64decode(str(result.get("content_base64") or ""), validate=True)
                if hashlib.sha256(chunk).hexdigest() != str(result.get("chunk_sha256") or ""):
                    raise RuntimeError("remote transfer chunk checksum mismatch")
                handle.write(chunk)
                download_digest.update(chunk)
                offset += len(chunk)
                if int(result.get("size") or 0) != total:
                    raise RuntimeError("remote file changed during transfer")
                expected_sha = str(result.get("sha256") or expected_sha)
                await publish_tool_progress(current=offset, total=total, label=Path(remote_path).name)
                if bool(result.get("eof")):
                    break
                if int(result.get("next_offset") or -1) != offset or not chunk:
                    raise RuntimeError("remote transfer made no forward progress")
        actual_sha = download_digest.hexdigest()
        if expected_sha and actual_sha != expected_sha:
            raise RuntimeError("remote transfer final checksum mismatch")
        filename = safe_attachment_filename(Path(remote_path).name, fallback_stem="remote-file")
        ready = partial.with_name(f"{uuid4().hex}_{filename}")
        partial.replace(ready)
        attachment = register_generated_attachment(str(ready), display_name=Path(remote_path).name or filename)
        ready.unlink(missing_ok=True)
        return {"ok": True, "downloaded": True, "path": remote_path, "size": total, "sha256": actual_sha, "attachment": attachment}
    except (ValueError, RuntimeError):
        partial.unlink(missing_ok=True)
        raise


async def _sync(
    args: dict[str, Any],
    context: PluginContext,
) -> dict[str, Any]:
    local_root = resolve_tool_path(str(args.get("local_path") or ""))
    if not local_root.is_dir():
        raise ValueError("local_path must be a directory for sync")
    remote_root = str(args.get("remote_path") or ".").replace("\\", "/").strip() or "."
    if remote_root != "/":
        remote_root = remote_root.rstrip("/") or "."
    entries = []
    local_files: dict[str, Path] = {}
    local_hashes: dict[str, tuple[str, tuple[int, int, int, int, int] | None]] = {}
    directories = [] if remote_root == "." else [remote_root]
    for path in sorted(local_root.rglob("*")):
        if path.is_symlink():
            continue
        relative = path.relative_to(local_root).as_posix()
        remote = relative if remote_root == "." else str(PurePosixPath(remote_root) / relative)
        if path.is_dir():
            entries.append({"path": remote, "kind": "directory"})
            directories.append(remote)
        elif path.is_file():
            identity_before = _file_identity(path)
            digest = await _hash_file(path)
            identity_after = _file_identity(path)
            stable_identity = identity_after if identity_before == identity_after else None
            entries.append({"path": remote, "kind": "file", "size": identity_after[2], "sha256": digest})
            local_files[remote] = path
            local_hashes[remote] = (digest, stable_identity)
    sync_id = "sync_" + uuid4().hex
    diff = await _request(
        args,
        context,
        command="files.sync.prepare",
        payload={"sync_id": sync_id, "path": remote_root, "entries": entries},
        key=f"{sync_id}:prepare",
    )
    if diff.get("ok") is False:
        return diff
    apply_result = await _request(
        args,
        context,
        command="files.sync.apply",
        payload={
            "sync_id": sync_id,
            "directories": directories,
            "delete": diff.get("delete") or [],
            "delete_extraneous": bool(args.get("delete_extraneous")),
        },
        key=f"{sync_id}:apply",
    )
    if apply_result.get("ok") is False:
        return apply_result
    uploaded = []
    for remote in diff.get("upload") or []:
        local = local_files.get(str(remote))
        if local is None:
            continue
        known_sha256, known_identity = local_hashes[str(remote)]
        result = await _upload_file(
            args,
            context,
            local,
            str(remote),
            conflict_policy="overwrite",
            known_sha256=known_sha256,
            known_identity=known_identity,
        )
        if result.get("ok") is False:
            return {**result, "sync_id": sync_id, "uploaded": uploaded}
        uploaded.append(str(remote))
    committed = await _request(
        args,
        context,
        command="files.sync.commit",
        payload={"sync_id": sync_id, "path": remote_root, "include_hash": True},
        key=f"{sync_id}:commit",
    )
    return {"ok": committed.get("ok") is not False, "sync_id": sync_id, "uploaded": uploaded, "deleted": apply_result.get("deleted") or [], "manifest": committed.get("entries") or []}


async def _hash_file(path: Path) -> str:
    def calculate() -> str:
        digest = hashlib.sha256()
        _update_hash_from_file(path, digest)
        return digest.hexdigest()

    return await asyncio.to_thread(calculate)


def _update_hash_from_file(path: Path, digest: Any) -> None:
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)


def _file_identity(path: Path) -> tuple[int, int, int, int, int]:
    stat = path.stat()
    return (
        int(stat.st_dev),
        int(stat.st_ino),
        int(stat.st_size),
        int(stat.st_mtime_ns),
        int(stat.st_ctime_ns),
    )


__all__ = ["TOOL_NAME", "TOOL_DEF", "TOOL_METADATA", "handler"]
