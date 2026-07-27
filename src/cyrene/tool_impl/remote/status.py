"""Read-only typed operations against a selected remote Cyrene."""

from __future__ import annotations

import base64
from typing import Any
from uuid import uuid4

from cyrene.tool_impl.remote.common import remote_tool_error, request_remote_command
from cyrene.tooling.executor import publish_tool_progress
from cyrene.tooling.runtime_api import (
    json_result,
    register_generated_attachment,
)

_TRANSFER_COMMANDS = frozenset({"artifacts.read", "attachments.read"})
_TRANSFER_CHUNK_BYTES = 512 * 1024

TOOL_NAME = "RemoteCyreneStatus"
TOOL_DEF = {
    "type": "function",
    "function": {
        "name": TOOL_NAME,
        "description": (
            "Read status and records from a paired Cyrene device explicitly "
            "selected in the current chat. Use ListRemoteDevices first when needed."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "device_id": {
                    "type": "string",
                    "description": "Selected device id. Required when multiple devices are attached.",
                },
                "command": {
                    "type": "string",
                    "enum": [
                        "capabilities.read",
                        "projects.list",
                        "chats.list",
                        "chats.read",
                        "runs.read",
                        "runs.events",
                        "tasks.list",
                        "tasks.read",
                        "artifacts.list",
                        "artifacts.read",
                        "attachments.read",
                    ],
                },
                "project_id": {
                    "type": "string",
                    "description": "Remote project id for project-scoped operations.",
                },
                "payload": {
                    "type": "object",
                    "description": (
                        "Command payload: chats.read {chat_id}; runs.read {run_id}; "
                        "runs.events {run_id,cursor?,limit?}; tasks.read {task_id}; "
                        "artifacts.list {task_id}; artifacts.read {task_id,artifact_id}. "
                        "attachments.read {chat_id,attachment_id}. File reads are "
                        "downloaded in chunks with live progress and return a local "
                        "attachment path; complete file size is not limited. "
                        "capabilities.read, projects.list, chats.list and tasks.list "
                        "need no payload beyond project_id where applicable."
                    ),
                },
                "timeout_seconds": {
                    "type": "number",
                    "minimum": 1,
                    "maximum": 120,
                },
            },
            "required": ["command"],
            "additionalProperties": False,
        },
    },
}
TOOL_METADATA = {
    "read_only": True,
    "resource_keys": ("remote:{device_id}",),
    "requires_order": False,
}


async def handler(
    args: dict[str, Any],
    _bot: Any,
    chat_id: int,
    db_path: str,
    _notify_state: dict[str, bool] | None,
) -> str:
    try:
        command = str(args.get("command") or "")
        if command in _TRANSFER_COMMANDS:
            return json_result(
                await _download_remote_file(
                    args,
                    db_path,
                    fallback_chat_id=chat_id,
                )
            )
        result = await request_remote_command(
            args,
            db_path,
            fallback_chat_id=chat_id,
        )
        return json_result(result)
    except Exception as exc:
        return json_result(remote_tool_error(exc))


async def _download_remote_file(
    args: dict[str, Any],
    db_path: str,
    *,
    fallback_chat_id: object,
) -> dict[str, Any]:
    """Assemble an unlimited remote file without exposing chunks to the LLM."""
    payload = dict(args.get("payload") or {})
    offset = 0
    total: int | None = None
    filename = ""
    media_type = "application/octet-stream"
    transfer_key = str(args.get("idempotency_key") or uuid4().hex)

    from cyrene.config import DATA_DIR
    from cyrene.runtime.attachments import safe_attachment_filename

    transfer_dir = DATA_DIR / "remote_transfers"
    transfer_dir.mkdir(parents=True, exist_ok=True)
    partial = transfer_dir / f"{uuid4().hex}.part"
    try:
        with partial.open("wb") as handle:
            while True:
                result = await request_remote_command(
                    {
                        **args,
                        "payload": {
                            **payload,
                            "offset": offset,
                            "limit": _TRANSFER_CHUNK_BYTES,
                        },
                        "idempotency_key": (
                            f"{transfer_key}:chunk:{offset}"
                        ),
                    },
                    db_path,
                    fallback_chat_id=fallback_chat_id,
                )
                if result.get("ok") is False:
                    return result
                encoded = str(result.get("content_base64") or "")
                chunk = base64.b64decode(encoded, validate=True) if encoded else b""
                expected_offset = int(result.get("offset") or 0)
                if expected_offset != offset:
                    raise RuntimeError("remote transfer returned an unexpected offset")
                handle.write(chunk)
                offset += len(chunk)
                total = int(result.get("size") or 0)
                filename = str(result.get("filename") or filename or "file")
                media_type = str(result.get("media_type") or media_type)
                await publish_tool_progress(
                    current=offset,
                    total=total,
                    label=filename,
                )
                if bool(result.get("eof")):
                    break
                next_offset = int(result.get("next_offset") or offset)
                if next_offset != offset or not chunk:
                    raise RuntimeError("remote transfer made no forward progress")

        final_name = safe_attachment_filename(
            filename,
            fallback_stem="remote-file",
        )
        ready = partial.with_name(f"{uuid4().hex}_{final_name}")
        partial.replace(ready)
        attachment = register_generated_attachment(
            str(ready),
            display_name=filename or final_name,
        )
        ready.unlink(missing_ok=True)
        return {
            "ok": True,
            "downloaded": True,
            "size": total or 0,
            "filename": filename or final_name,
            "attachment": attachment,
        }
    finally:
        partial.unlink(missing_ok=True)


__all__ = ["TOOL_NAME", "TOOL_DEF", "TOOL_METADATA", "handler"]
