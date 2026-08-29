"""Create a remote chat and start its Cyrene Agent in one supervised action."""

from __future__ import annotations

from typing import Any

from cyrene.core.plugin import PluginContext

from .common import (
    remote_tool_error,
    request_remote_command,
    resolve_selected_remote_device,
)
from .permission import authorize_remote
from cyrene.plugins.native_runtime import json_result, run_context_value

TOOL_NAME = "RunRemoteCyrene"
TOOL_DEF = {
    "type": "function",
    "function": {
        "name": TOOL_NAME,
        "description": (
            "Start Agent work on a paired Cyrene selected in the current chat. "
            "This creates a remote chat, sends the instruction to the remote Agent, "
            "and returns chat_id/run_id for RemoteCyreneStatus. The remote "
            "Cyrene keeps its own tools, skills, permissions, approvals, and "
            "sandbox; this tool never bypasses its harness."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "device_id": {
                    "type": "string",
                    "description": (
                        "Selected device id. Required when multiple devices "
                        "are attached."
                    ),
                },
                "project_id": {
                    "type": "string",
                    "description": "A project explicitly shared by the remote device.",
                },
                "message": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 200000,
                    "description": "User-level instruction for the remote Cyrene Agent.",
                },
                "title": {
                    "type": "string",
                    "maxLength": 160,
                    "description": "Optional title for the new remote chat.",
                },
                "permission_mode": {
                    "type": "string",
                    "enum": ["auto", "default", "plan", "full_access"],
                    "description": (
                        "The current controller chat's local permission mode remains "
                        "authoritative for this remote run."
                    ),
                },
                "language": {
                    "type": "string",
                    "enum": ["", "zh", "en"],
                },
                "idempotency_key": {
                    "type": "string",
                    "minLength": 8,
                    "maxLength": 180,
                    "description": "Stable key reused when retrying this exact run.",
                },
                "reason": {
                    "type": "string",
                    "description": "Why the remote Cyrene needs to perform this work.",
                },
                "timeout_seconds": {
                    "type": "number",
                    "minimum": 1,
                    "maximum": 120,
                },
            },
            "required": [
                "project_id",
                "message",
                "idempotency_key",
            ],
            "additionalProperties": False,
        },
    },
}
TOOL_METADATA = {
    "read_only": False,
    "resource_keys": ("remote:{device_id}",),
    "requires_order": True,
}


async def handler(
    args: dict[str, Any],
    context: PluginContext,
) -> str:
    try:
        _chat, device = resolve_selected_remote_device(
            args,
            context,
        )
        project_id = str(args.get("project_id") or "").strip()
        message = str(args.get("message") or "").strip()
        idempotency_key = str(args.get("idempotency_key") or "").strip()
        if not project_id:
            raise ValueError("project_id is required")
        if not message:
            raise ValueError("message is required")
        if len(idempotency_key) < 8:
            raise ValueError("idempotency_key must contain at least 8 characters")

        permission, _destructive = await authorize_remote(
            TOOL_NAME,
            args,
            context,
            device_id=str(device["device_id"]),
            project_id=project_id,
        )
        if permission is not None:
            return json_result(permission)

        controller_mode = str(
            run_context_value(context, "permission_mode", "default")
            or "default"
        )
        shared = {
            "device_id": str(device["device_id"]),
            "project_id": project_id,
            "timeout_seconds": args.get("timeout_seconds"),
        }
        created = await request_remote_command(
            {
                **shared,
                "command": "chats.create",
                "payload": {"title": str(args.get("title") or "")[:160]},
                "idempotency_key": f"{idempotency_key}:create",
            },
            context,
        )
        if created.get("ok") is False:
            return json_result(
                {
                    **created,
                    "stage": "create_chat",
                    "error_origin": created.get("error_origin") or "remote",
                }
            )
        remote_chat = dict(created.get("chat") or {})
        remote_chat_id = str(remote_chat.get("id") or "").strip()
        if not remote_chat_id:
            raise RuntimeError("remote Cyrene did not return a chat id")

        started = await request_remote_command(
            {
                **shared,
                "command": "chats.send",
                "payload": {
                    "chat_id": remote_chat_id,
                    "message": message,
                    "permission_mode": controller_mode,
                    "language": str(args.get("language") or ""),
                },
                "idempotency_key": f"{idempotency_key}:send",
            },
            context,
        )
        if started.get("ok") is False:
            return json_result(
                {
                    **started,
                    "stage": "start_agent",
                    "chat_created": True,
                    "chat": remote_chat,
                    "error_origin": started.get("error_origin") or "remote",
                }
            )
        return json_result(
            {
                "ok": True,
                "device_id": str(device["device_id"]),
                "project_id": project_id,
                "chat": remote_chat,
                "run_id": str(started.get("run_id") or ""),
                "status": str(started.get("status") or "running"),
                "created_at": str(started.get("created_at") or ""),
                "event_cursor": int(started.get("event_cursor") or 0),
                "duplicate": bool(
                    created.get("duplicate") or started.get("duplicate")
                ),
            }
        )
    except Exception as exc:
        return json_result(remote_tool_error(exc, context))


__all__ = ["TOOL_NAME", "TOOL_DEF", "TOOL_METADATA", "handler"]
