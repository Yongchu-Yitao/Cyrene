"""Human-approved local-file upload for the embedded browser."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
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
from cyrene.core.plugin import PluginContext
from cyrene.plugins.native_runtime import (
    json_result,
    plugin_localized,
    publish_runtime_event,
    resolve_tool_path,
)


logger = logging.getLogger(__name__)

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
            "input[type=file] from browser_snapshot. The host reviews the exact tool call before "
            "execution; this tool binds the destination, input, filenames, sizes, and hashes. "
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
TOOL_METADATA = {
    "read_only": False,
    "requires_order": True,
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
            return None, "empty_path"
        try:
            resolved = resolve_tool_path(path_arg)
        except FileNotFoundError:
            logger.debug("Browser upload path was not found", exc_info=True)
            return None, "file_not_found"
        except (OSError, RuntimeError, ValueError):
            logger.debug("Browser upload path could not be resolved", exc_info=True)
            return None, "file_unavailable"
        key = str(resolved)
        if key in seen:
            continue
        seen.add(key)
        resolved_paths.append(resolved)

    try:
        metadata = await asyncio.gather(*[asyncio.to_thread(_file_metadata, path) for path in resolved_paths])
    except FileNotFoundError:
        logger.debug("Browser upload file was not found", exc_info=True)
        return None, "file_not_found"
    except OSError:
        logger.debug("Browser upload file could not be read", exc_info=True)
        return None, "file_unavailable"
    except ValueError:
        logger.debug("Browser upload file failed validation", exc_info=True)
        return None, "file_invalid"
    return list(metadata), None


def _resolve_error_message(context: PluginContext, code: str | None) -> str:
    messages = {
        "empty_path": (
            "Browser upload failed: file paths must not be empty.",
            "浏览器上传失败：文件路径不能为空。",
        ),
        "file_not_found": (
            "Browser upload failed: a selected file was not found.",
            "浏览器上传失败：未找到所选文件。",
        ),
        "file_unavailable": (
            "Browser upload failed: a selected file could not be read.",
            "浏览器上传失败：无法读取所选文件。",
        ),
        "file_invalid": (
            "Browser upload failed: a selected file is invalid, changed, or exceeds the upload limit.",
            "浏览器上传失败：所选文件无效、已发生变化或超过上传限制。",
        ),
    }
    en, zh = messages.get(
        str(code or ""),
        ("Browser upload failed.", "浏览器上传失败。"),
    )
    return plugin_localized(context, en, zh)


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
    context: PluginContext,
) -> str:
    from . import runtime
    from .browser_output import browser_error_text

    chooser_id = str(args.get("chooser_id") or "").strip()
    ref = str(args.get("ref") or "").strip()
    if bool(chooser_id) == bool(ref):
        return plugin_localized(
            context,
            "Browser upload failed: provide exactly one of chooser_id or ref.",
            "浏览器上传失败：chooser_id 和 ref 必须且只能提供一个。",
        )
    raw_paths = args.get("paths")
    if not isinstance(raw_paths, list) or not 1 <= len(raw_paths) <= _MAX_FILES:
        return plugin_localized(
            context,
            "Browser upload failed: paths must contain between 1 and {maximum} files.",
            "浏览器上传失败：paths 必须包含 1 到 {maximum} 个文件。",
            maximum=_MAX_FILES,
        )

    prepared = await runtime.prepare_file_upload(chooser_id=chooser_id, ref=ref)
    if prepared.get("ok") is not True:
        return plugin_localized(
            context,
            "Browser upload target unavailable: {error}",
            "浏览器上传目标不可用：{error}",
            error=browser_error_text(
                prepared,
                context,
                "The browser upload target could not be prepared.",
                "无法准备浏览器上传目标。",
            ),
        )
    target = prepared.get("target") if isinstance(prepared.get("target"), dict) else {}
    if not target.get("id") or not target.get("origin"):
        return plugin_localized(
            context,
            "Browser upload target unavailable: the destination origin could not be verified.",
            "浏览器上传目标不可用：无法验证目标来源。",
        )
    destination = urlparse(str(target.get("origin") or ""))
    if destination.scheme not in {"http", "https"} or not destination.netloc:
        return plugin_localized(
            context,
            "Browser upload target unavailable: files may only be disclosed to a verified HTTP(S) origin.",
            "浏览器上传目标不可用：文件只能披露给已验证的 HTTP(S) 来源。",
        )

    files, error = await _resolve_files([str(item or "") for item in raw_paths])
    if error is not None:
        return _resolve_error_message(context, error)
    assert files is not None
    if not target.get("multiple") and len(files) != 1:
        return plugin_localized(
            context,
            "Browser upload failed: this file input accepts only one file.",
            "浏览器上传失败：此文件输入框只接受一个文件。",
        )

    fingerprint = _upload_fingerprint(target, files)
    # Re-resolve the browser node and re-hash every file immediately before the
    # side effect. Any changed page, origin, path, size, or content cancels the
    # action rather than silently widening the centrally reviewed call.
    prepared_now = await runtime.prepare_file_upload(chooser_id=chooser_id, ref=ref)
    files_now, validation_error = await _resolve_files([str(item or "") for item in raw_paths])
    if prepared_now.get("ok") is not True or validation_error is not None or files_now is None:
        public_error = (
            _resolve_error_message(context, validation_error)
            if validation_error is not None
            else browser_error_text(
                prepared_now,
                context,
                "Revalidation failed.",
                "重新验证失败。",
            )
        )
        await publish_runtime_event(context, {
            "type": "external_browser_upload",
            "status": "cancelled_before_execution",
            "fingerprint": fingerprint,
            "error": public_error,
        })
        return plugin_localized(
            context,
            "Browser upload was cancelled during revalidation: {error}",
            "浏览器上传在重新验证期间已取消：{error}",
            error=public_error,
        )
    target_now = prepared_now.get("target") if isinstance(prepared_now.get("target"), dict) else {}
    if _upload_fingerprint(target_now, files_now) != fingerprint:
        await publish_runtime_event(context, {
            "type": "external_browser_upload",
            "status": "cancelled_binding_changed",
            "fingerprint": fingerprint,
        })
        return plugin_localized(
            context,
            "Browser upload was cancelled: the destination or file content changed after approval.",
            "浏览器上传已取消：目标或文件内容在批准后发生变化。",
        )

    await publish_runtime_event(context, {
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
    result: dict[str, Any] = {
        "ok": False,
        "error": plugin_localized(
            context,
            "The approved file snapshot was not created.",
            "未创建已批准文件的快照。",
        ),
        "code": "UPLOAD_SNAPSHOT_NOT_CREATED",
    }
    try:
        staging_root = _new_upload_snapshot()
        staged_files = await asyncio.to_thread(
            _stage_approved_files,
            files_now,
            staging_root,
        )
        result = await runtime.set_input_files(target_now, staged_files)
    except Exception:
        logger.warning("Approved browser upload snapshot failed", exc_info=True)
        result = {
            "ok": False,
            "error": plugin_localized(
                context,
                "The approved file snapshot could not be prepared.",
                "无法准备已批准文件的快照。",
            ),
            "code": "UPLOAD_SNAPSHOT_FAILED",
        }
    if result.get("ok") is True and staging_root is not None:
        _retain_upload_snapshot(staging_root)
    elif staging_root is not None:
        _remove_upload_snapshot(staging_root)
    status = "completed" if result.get("ok") is True else "failed"
    result_error = browser_error_text(
        result,
        context,
        "The browser could not attach the approved files.",
        "浏览器无法附加已批准的文件。",
    )
    await publish_runtime_event(context, {
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
        "error": "" if status == "completed" else result_error,
    })
    if result.get("ok") is not True:
        return plugin_localized(
            context,
            "Browser upload failed after approval: {error}",
            "浏览器上传在批准后失败：{error}",
            error=result_error,
        )
    return json_result({
        "status": "files_attached",
        "origin": str(target_now.get("origin") or ""),
        "url": str(result.get("url") or target_now.get("topUrl") or ""),
        "files": _public_files(files_now),
        "note": plugin_localized(
            context,
            "Files were attached to the page. A separate submit button, if any, was not clicked.",
            "文件已附加到页面。若页面存在单独的提交按钮，尚未点击该按钮。",
        ),
    })


handler = _tool_browser_upload_files

__all__ = [
    "TOOL_NAME",
    "TOOL_DEF",
    "handler",
    "_tool_browser_upload_files",
    "_upload_fingerprint",
]
