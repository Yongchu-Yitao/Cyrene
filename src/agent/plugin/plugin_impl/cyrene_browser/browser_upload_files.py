"""Human-approved local-file upload for the embedded browser."""

from __future__ import annotations

import asyncio
import hashlib
import json
import mimetypes
import os
import shutil
import stat
import tempfile
import threading
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from cyrene.runtime.paths import TEMP_DIR
from cyrene.agent.context import (
    consume_external_upload_grant,
    publish_runtime_event,
)
from cyrene.tooling.runtime_api import (
    json_result,
    request_external_upload_confirmation as _request_external_upload_confirmation,
    request_read_elevation,
    resolve_tool_path,
)

TOOL_NAME = "browser_upload_files"
_MAX_FILES = 10
_MAX_FILE_BYTES = 100 * 1024 * 1024
_SNAPSHOT_TTL_SECONDS = 15 * 60
_SNAPSHOT_PREFIX = "cyrene-browser-upload-"

TOOL_DEF = {
    "type": "function",
    "function": {
        "name": TOOL_NAME,
        "description": (
            "Attach local files to a browser file input without opening the system file picker. "
            "Use chooser_id returned by an intercepted browser click, or use the ref of a visible "
            "input[type=file] from browser_snapshot. This always pauses for a human, single-use "
            "approval bound to the destination, input, filenames, sizes, and SHA-256 hashes. "
            "It never submits a separate form button."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "chooser_id": {
                    "type": "string",
                    "description": "chooser_id returned after FILE_CHOOSER_INTERCEPTED.",
                },
                "ref": {
                    "type": "string",
                    "description": "Visible file-input ref from browser_snapshot, such as e12.",
                },
                "paths": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Exact workspace-relative or approved absolute paths to upload (1-10 files).",
                },
                "reason": {
                    "type": "string",
                    "description": "Why these files need to be disclosed to this website.",
                },
            },
            "required": ["paths"],
        },
    },
}


def _file_metadata(path: Path) -> dict[str, Any]:
    link_info = os.lstat(path)
    if stat.S_ISLNK(link_info.st_mode):
        raise ValueError(f"Only regular, non-symlink files may be uploaded: {path.name}")
    source_fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    digest = hashlib.sha256()
    try:
        before = os.fstat(source_fd)
        if not stat.S_ISREG(before.st_mode):
            raise ValueError(f"Only regular, non-symlink files may be uploaded: {path.name}")
        if before.st_size > _MAX_FILE_BYTES:
            raise ValueError(f"File exceeds the 100 MiB browser upload limit: {path.name}")
        with os.fdopen(source_fd, "rb", closefd=False) as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        after = os.fstat(source_fd)
    finally:
        os.close(source_fd)
    identity_before = (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns, before.st_ctime_ns)
    identity_after = (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns)
    if identity_before != identity_after:
        raise ValueError(f"File changed while it was being inspected: {path.name}")
    return {
        "path": str(path),
        "name": path.name,
        "size": int(before.st_size),
        "sha256": digest.hexdigest(),
        "content_type": mimetypes.guess_type(path.name)[0] or "application/octet-stream",
    }


def _stage_approved_files(files: list[dict[str, Any]], staging_root: Path) -> list[dict[str, Any]]:
    """Copy the exact approved bytes into private, short-lived upload snapshots."""
    staged: list[dict[str, Any]] = []
    nofollow = getattr(os, "O_NOFOLLOW", 0)
    for index, item in enumerate(files):
        source = Path(str(item.get("path") or ""))
        destination_dir = staging_root / f"{index:02d}"
        destination_dir.mkdir(mode=0o700)
        approved_name = str(item.get("name") or source.name)
        if not approved_name or Path(approved_name).name != approved_name:
            raise ValueError("Approved upload filename is invalid.")
        destination = destination_dir / approved_name
        source_fd = os.open(source, os.O_RDONLY | nofollow)
        digest = hashlib.sha256()
        copied = 0
        try:
            before = os.fstat(source_fd)
            if not stat.S_ISREG(before.st_mode):
                raise ValueError(f"Only regular files may be uploaded: {source.name}")
            with os.fdopen(source_fd, "rb", closefd=False) as reader, destination.open("xb") as writer:
                for chunk in iter(lambda: reader.read(1024 * 1024), b""):
                    digest.update(chunk)
                    copied += len(chunk)
                    writer.write(chunk)
                writer.flush()
                os.fsync(writer.fileno())
            after = os.fstat(source_fd)
        finally:
            os.close(source_fd)
        identity_before = (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns, before.st_ctime_ns)
        identity_after = (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns)
        actual_sha256 = digest.hexdigest()
        if (
            identity_before != identity_after
            or copied != int(item.get("size") or 0)
            or actual_sha256 != str(item.get("sha256") or "").lower()
        ):
            raise ValueError(f"File content changed while preparing the approved upload: {source.name}")
        destination.chmod(0o400)
        staged.append({**item, "path": str(destination), "sha256": actual_sha256, "size": copied})
    return staged


def _remove_upload_snapshot(staging_root: Path) -> None:
    try:
        shutil.rmtree(staging_root)
    except FileNotFoundError:
        pass
    except OSError:
        pass


def _new_upload_snapshot() -> Path:
    TEMP_DIR.mkdir(parents=True, exist_ok=True)
    cutoff = time.time() - _SNAPSHOT_TTL_SECONDS
    for candidate in TEMP_DIR.glob(f"{_SNAPSHOT_PREFIX}*"):
        try:
            if candidate.stat().st_mtime <= cutoff:
                _remove_upload_snapshot(candidate)
        except OSError:
            continue
    staging_root = Path(tempfile.mkdtemp(prefix=_SNAPSHOT_PREFIX, dir=TEMP_DIR))
    staging_root.chmod(0o700)
    return staging_root


def _retain_upload_snapshot(staging_root: Path) -> None:
    """Keep FileList backing bytes briefly so a later form submit can read them."""
    timer = threading.Timer(_SNAPSHOT_TTL_SECONDS, _remove_upload_snapshot, args=(staging_root,))
    timer.daemon = True
    timer.start()


async def _resolve_files(path_args: list[str]) -> tuple[list[dict[str, Any]] | None, str | None]:
    resolved_paths: list[Path] = []
    seen: set[str] = set()
    for raw in path_args:
        path_arg = str(raw or "").strip()
        if not path_arg:
            return None, "File paths must not be empty."
        try:
            resolved = resolve_tool_path(path_arg)
        except ValueError:
            elevation = await request_read_elevation(
                tool_name=TOOL_NAME,
                path_hint=path_arg,
                reason="Agent needs to read this file in order to disclose it to an external website.",
            )
            if elevation is not None:
                return None, elevation
            resolved = resolve_tool_path(path_arg)
        key = str(resolved)
        if key in seen:
            continue
        seen.add(key)
        resolved_paths.append(resolved)

    try:
        metadata = await asyncio.gather(*[asyncio.to_thread(_file_metadata, path) for path in resolved_paths])
    except FileNotFoundError as exc:
        return None, f"Browser upload failed: file not found: {exc.filename or exc}"
    except (OSError, ValueError) as exc:
        return None, f"Browser upload failed: {exc}"
    return list(metadata), None


def _upload_fingerprint(target: dict[str, Any], files: list[dict[str, Any]]) -> str:
    payload = {
        "target": {
            "id": str(target.get("id") or ""),
            "tab_id": str(target.get("tabId") or ""),
            "chooser_id": str(target.get("chooserId") or ""),
            "upload_id": str(target.get("uploadId") or ""),
            "origin": str(target.get("origin") or ""),
            "top_url": str(target.get("topUrl") or ""),
            "frame_url": str(target.get("frameUrl") or ""),
            "frame_loader_id": str(target.get("frameLoaderId") or ""),
            "mode": str(target.get("mode") or ""),
            "accept": str(target.get("accept") or ""),
            "multiple": bool(target.get("multiple")),
            "name": str(target.get("name") or ""),
            "aria_label": str(target.get("ariaLabel") or ""),
        },
        "files": [
            {
                "path": str(item.get("path") or ""),
                "name": str(item.get("name") or ""),
                "size": int(item.get("size") or 0),
                "sha256": str(item.get("sha256") or ""),
            }
            for item in files
        ],
    }
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _consume_upload_grant(fingerprint: str) -> bool:
    return consume_external_upload_grant(fingerprint)


def _public_files(files: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "name": str(item.get("name") or ""),
            "size": int(item.get("size") or 0),
            "sha256": str(item.get("sha256") or ""),
            "content_type": str(item.get("content_type") or "application/octet-stream"),
        }
        for item in files
    ]


async def _tool_browser_upload_files(
    args: dict[str, Any],
    _bot: Any,
    _chat_id: int,
    _db_path: str,
    _notify_state: dict[str, bool] | None,
) -> str:
    from cyrene import browser

    chooser_id = str(args.get("chooser_id") or "").strip()
    ref = str(args.get("ref") or "").strip()
    if bool(chooser_id) == bool(ref):
        return "Browser upload failed: provide exactly one of chooser_id or ref."
    raw_paths = args.get("paths")
    if not isinstance(raw_paths, list) or not 1 <= len(raw_paths) <= _MAX_FILES:
        return f"Browser upload failed: paths must contain between 1 and {_MAX_FILES} files."

    prepared = await browser.prepare_file_upload(chooser_id=chooser_id, ref=ref)
    if prepared.get("ok") is not True:
        return f"Browser upload target unavailable: {prepared.get('error', 'unknown error')}"
    target = prepared.get("target") if isinstance(prepared.get("target"), dict) else {}
    if not target.get("id") or not target.get("origin"):
        return "Browser upload target unavailable: the destination origin could not be verified."
    destination = urlparse(str(target.get("origin") or ""))
    if destination.scheme not in {"http", "https"} or not destination.netloc:
        return "Browser upload target unavailable: files may only be disclosed to a verified HTTP(S) origin."

    files, error = await _resolve_files([str(item or "") for item in raw_paths])
    if error is not None:
        return error
    assert files is not None
    if not target.get("multiple") and len(files) != 1:
        return "Browser upload failed: this file input accepts only one file."

    fingerprint = _upload_fingerprint(target, files)
    approval = await _request_external_upload_confirmation(
        fingerprint=fingerprint,
        target=target,
        files=files,
        reason=str(args.get("reason") or "").strip(),
    )
    if approval is not None:
        return approval
    if not _consume_upload_grant(fingerprint):
        return "Browser upload denied: no matching single-use human approval is available."

    # Re-resolve the browser node and re-hash every file after consuming the
    # approval. Any changed page, origin, path, size, or content cancels the
    # action and requires a fresh approval rather than silently widening it.
    prepared_now = await browser.prepare_file_upload(chooser_id=chooser_id, ref=ref)
    files_now, validation_error = await _resolve_files([str(item or "") for item in raw_paths])
    if prepared_now.get("ok") is not True or validation_error is not None or files_now is None:
        await publish_runtime_event({
            "type": "external_browser_upload",
            "status": "cancelled_before_execution",
            "fingerprint": fingerprint,
            "error": validation_error or prepared_now.get("error") or "revalidation failed",
        })
        return f"Browser upload cancelled during revalidation: {validation_error or prepared_now.get('error') or 'unknown error'}"
    target_now = prepared_now.get("target") if isinstance(prepared_now.get("target"), dict) else {}
    if _upload_fingerprint(target_now, files_now) != fingerprint:
        await publish_runtime_event({
            "type": "external_browser_upload",
            "status": "cancelled_binding_changed",
            "fingerprint": fingerprint,
        })
        return "Browser upload cancelled: the destination or file content changed after approval."

    await publish_runtime_event({
        "type": "external_browser_upload",
        "status": "executing",
        "effect": "file_input_population",
        "fingerprint": fingerprint,
        "target": {
            "origin": str(target_now.get("origin") or ""),
            "top_url": str(target_now.get("topUrl") or ""),
            "frame_url": str(target_now.get("frameUrl") or ""),
            "target_id": str(target_now.get("id") or ""),
        },
        "files": _public_files(files_now),
    })
    staging_root: Path | None = None
    result: dict[str, Any] = {"ok": False, "error": "Approved file snapshot was not created."}
    try:
        staging_root = _new_upload_snapshot()
        staged_files = await asyncio.to_thread(
            _stage_approved_files,
            files_now,
            staging_root,
        )
        result = await browser.set_input_files(target_now, staged_files)
    except Exception as exc:
        result = {"ok": False, "error": f"Approved file snapshot failed: {exc}"}
    if result.get("ok") is True and staging_root is not None:
        _retain_upload_snapshot(staging_root)
    elif staging_root is not None:
        _remove_upload_snapshot(staging_root)
    status = "completed" if result.get("ok") is True else "failed"
    await publish_runtime_event({
        "type": "external_browser_upload",
        "status": status,
        "effect": "file_input_populated" if status == "completed" else "file_input_population_failed",
        "separate_submit_clicked": False,
        "fingerprint": fingerprint,
        "target": {
            "origin": str(target_now.get("origin") or ""),
            "target_id": str(target_now.get("id") or ""),
        },
        "files": _public_files(files_now),
        "error": "" if status == "completed" else str(result.get("error") or "unknown error"),
    })
    if result.get("ok") is not True:
        return f"Browser upload failed after approval: {result.get('error', 'unknown error')}"
    return json_result({
        "status": "files_attached",
        "origin": str(target_now.get("origin") or ""),
        "url": str(result.get("url") or target_now.get("topUrl") or ""),
        "files": _public_files(files_now),
        "note": "Files were attached to the page. A separate submit button, if any, was not clicked.",
    })


handler = _tool_browser_upload_files

__all__ = [
    "TOOL_NAME",
    "TOOL_DEF",
    "handler",
    "_tool_browser_upload_files",
    "_upload_fingerprint",
]
