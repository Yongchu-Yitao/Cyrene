"""Side-effecting typed operations against a selected remote Cyrene."""

from __future__ import annotations

import hashlib
import json
from typing import Any

from cyrene.agent.context import current_run_context

from cyrene.tool_impl.remote.common import (
    remote_tool_error,
    request_remote_command,
    resolve_selected_remote_device,
)
from cyrene.tooling.runtime_api import json_result, request_scope_elevation

TOOL_NAME = "RemoteCyreneAction"
TOOL_DEF = {
    "type": "function",
    "function": {
        "name": TOOL_NAME,
        "description": (
            "Perform a typed action on a paired Cyrene device explicitly selected "
            "in the current chat. This is a compatibility path for remote chat/task "
            "lifecycle actions. Prefer RemoteHarness for ordinary remote control so "
            "no remote chat or second Agent is created."
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
                        "tasks.create",
                        "tasks.dispatch",
                        "tasks.approve_plan",
                        "tasks.run_step",
                        "tasks.pause",
                        "tasks.resume",
                        "tasks.cancel",
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
                        "{chat_id}; tasks.create {title?,goal,priority?}; tasks.dispatch "
                        "{task_id,message}; tasks.approve_plan {task_id}; tasks.run_step "
                        "{task_id,step_id,message}; tasks.pause/resume/cancel {task_id}; "
                        "approvals.respond {chat_id|task_id,question_id,answer}."
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
    _bot: Any,
    chat_id: int,
    db_path: str,
    _notify_state: dict[str, bool] | None,
) -> str:
    try:
        _chat, device = resolve_selected_remote_device(
            args,
            db_path,
            fallback_chat_id=chat_id,
        )
        request_args = dict(args)
        request_payload = dict(args.get("payload") or {})
        if str(args.get("command") or "") in {
            "chats.send",
            "tasks.dispatch",
            "tasks.run_step",
        }:
            request_payload["permission_mode"] = current_run_context().permission_mode
        request_args["payload"] = request_payload
        exact_operation = json.dumps(
            {
                "device_id": str(device["device_id"]),
                "project_id": str(args.get("project_id") or ""),
                "command": str(args.get("command") or ""),
                "payload": request_payload,
                "idempotency_key": str(args.get("idempotency_key") or ""),
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        )
        operation_sha256 = hashlib.sha256(exact_operation.encode("utf-8")).hexdigest()
        permission = await request_scope_elevation(
            tool_name=TOOL_NAME,
            path_hint=str(device["device_id"]),
            operation=f"操作远程 Cyrene：{args.get('command') or ''}",
            reason=(
                str(args.get("reason") or "执行用户请求的远程设备操作")
                + "\n精确操作："
                + exact_operation[:8000]
                + "\nSHA-256："
                + operation_sha256
            ),
            permission_kind="remote_device_action",
            options=["允许执行这一次", "拒绝"],
            scope_hint="远程设备操作的 ",
            meta_extra={
                "device_id": str(device["device_id"]),
                "project_id": str(args.get("project_id") or ""),
                "command": str(args.get("command") or ""),
                "operation_sha256": operation_sha256,
            },
        )
        if permission is not None:
            return permission
        result = await request_remote_command(
            request_args,
            db_path,
            fallback_chat_id=chat_id,
        )
        return json_result(result)
    except Exception as exc:
        return json_result(remote_tool_error(exc))


__all__ = ["TOOL_NAME", "TOOL_DEF", "TOOL_METADATA", "handler"]
