"""Side-effecting typed operations against a selected remote Cyrene."""

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

TOOL_NAME = "RemoteCyreneAction"
TOOL_DEF = {
    "type": "function",
    "function": {
        "name": TOOL_NAME,
        "description": (
            "Perform a typed action on a paired Cyrene device explicitly selected "
            "in the current chat, including remote conversation and Goal actions."
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
                        "chats.create",
                        "chats.update",
                        "chats.delete",
                        "chats.send",
                        "runs.guide",
                        "runs.interrupt",
                        "goals.update",
                        "goals.confirm",
                        "goals.pause",
                        "goals.resume",
                        "goals.abort",
                        "goals.accept",
                        "approvals.respond",
                    ],
                },
                "project_id": {
                    "type": "string",
                    "description": "Remote project id for the action.",
                },
                "payload": {
                    "type": "object",
                    "description": (
                        "Command payload: chats.create {title?}; chats.update "
                        "{chat_id,title}; chats.delete {chat_id}; chats.send "
                        "{chat_id,message,permission_mode?:auto|default|plan,language?}; "
                        "runs.guide {chat_id,message,request_id?}; runs.interrupt "
                        "{chat_id}; goals.update/confirm {chat_id,objective?,"
                        "acceptanceCriteria?,constraints?,outOfScope?,durationSeconds?}; "
                        "goals.pause/resume/abort/accept {chat_id}; approvals.respond "
                        "{chat_id,question_id,answer}."
                    ),
                },
                "idempotency_key": {
                    "type": "string",
                    "minLength": 8,
                    "maxLength": 200,
                    "description": "Stable key reused when retrying this exact action.",
                },
                "reason": {
                    "type": "string",
                    "description": "Short explanation of why the remote action is needed.",
                },
                "timeout_seconds": {
                    "type": "number",
                    "minimum": 1,
                    "maximum": 120,
                },
            },
            "required": ["command", "payload", "idempotency_key"],
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
        permission, _destructive = await authorize_remote(
            TOOL_NAME,
            args,
            context,
            device_id=str(device["device_id"]),
            project_id=str(args.get("project_id") or ""),
        )
        if permission is not None:
            return json_result(permission)
        request_args = dict(args)
        request_payload = dict(args.get("payload") or {})
        if str(args.get("command") or "") == "chats.send":
            request_payload["permission_mode"] = str(
                run_context_value(context, "permission_mode", "default")
                or "default"
            )
        request_args["payload"] = request_payload
        result = await request_remote_command(
            request_args,
            context,
        )
        return json_result(result)
    except Exception as exc:
        return json_result(remote_tool_error(exc, context))


__all__ = ["TOOL_NAME", "TOOL_DEF", "TOOL_METADATA", "handler"]
