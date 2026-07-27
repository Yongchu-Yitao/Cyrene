"""Invoke an explicitly granted tool package on a paired Cyrene."""

from __future__ import annotations

import json
from typing import Any

from cyrene.runtime.remote_control import REMOTE_TOOL_PACK_PREFIX
from cyrene.tool_impl.remote.common import (
    remote_tool_error,
    request_remote_command,
    resolve_selected_remote_device,
)
from cyrene.tooling.runtime_api import json_result, request_scope_elevation

TOOL_NAME = "RemoteHarness"
TOOL_DEF = {
    "type": "function",
    "function": {
        "name": TOOL_NAME,
        "description": (
            "Preferred way to control a paired Cyrene: discover, describe, or "
            "directly invoke a capability from a remotely granted tool package. "
            "This does not create a remote chat or start a second Agent. Use "
            "RemoteCyreneAction/RunRemoteCyrene only as compatibility fallbacks."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "device_id": {"type": "string"},
                "project_id": {
                    "type": "string",
                    "description": "A project explicitly shared by the remote device.",
                },
                "tool_pack": {
                    "type": "string",
                    "description": "Granted package wire name, such as desktop_tools.",
                },
                "operation": {
                    "type": "string",
                    "enum": ["discover", "describe", "invoke"],
                },
                "query": {"type": "string"},
                "limit": {"type": "integer", "minimum": 1, "maximum": 50},
                "capability_id": {"type": "string"},
                "capability_ids": {
                    "type": "array",
                    "items": {"type": "string"},
                    "maxItems": 20,
                },
                "arguments": {"type": "object"},
                "reason": {
                    "type": "string",
                    "description": "Why this exact remote capability is needed.",
                },
                "timeout_seconds": {
                    "type": "number",
                    "minimum": 1,
                    "maximum": 120,
                },
            },
            "required": ["project_id", "tool_pack", "operation"],
            "additionalProperties": False,
        },
    },
}
TOOL_METADATA = {
    # Discovery is read-only. Invoke performs its own exact, argument-bound
    # controller-side elevation before crossing the device boundary.
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
        _chat, device = resolve_selected_remote_device(
            args, db_path, fallback_chat_id=chat_id
        )
        project_id = str(args.get("project_id") or "").strip()
        tool_pack = str(args.get("tool_pack") or "").strip()
        operation = str(args.get("operation") or "").strip()
        if not project_id or not tool_pack:
            raise ValueError("project_id and tool_pack are required")
        grant = REMOTE_TOOL_PACK_PREFIX + tool_pack
        if grant not in (device.get("received_capabilities") or []):
            raise PermissionError(
                f"远程设备未授权直接调用工具包 {tool_pack}"
            )

        if operation == "invoke":
            capability_id = str(args.get("capability_id") or "").strip()
            if not capability_id:
                raise ValueError("capability_id is required for invoke")
            rendered_arguments = json.dumps(
                args.get("arguments") or {},
                ensure_ascii=False,
                sort_keys=True,
                default=str,
            )
            permission = await request_scope_elevation(
                tool_name=TOOL_NAME,
                path_hint=str(device["device_id"]),
                operation=f"在远程设备直接调用 {capability_id}",
                reason=(
                    str(args.get("reason") or "执行用户请求的远程操作")
                    + "\n工具包："
                    + tool_pack
                    + "\n参数："
                    + rendered_arguments[:4000]
                ),
                permission_kind="remote_harness_invoke",
                options=["允许执行这一次", "拒绝"],
                scope_hint="远程工具调用的 ",
                meta_extra={
                    "device_id": str(device["device_id"]),
                    "project_id": project_id,
                    "tool_pack": tool_pack,
                    "capability_id": capability_id,
                    "arguments": dict(args.get("arguments") or {}),
                },
            )
            if permission is not None:
                return permission

        payload = {
            key: args[key]
            for key in (
                "tool_pack",
                "query",
                "limit",
                "capability_id",
                "capability_ids",
                "arguments",
                "timeout_seconds",
            )
            if key in args
        }
        payload["call_id"] = (
            f"{device['device_id']}:{project_id}:{operation}:"
            f"{str(args.get('capability_id') or tool_pack)}"
        )
        result = await request_remote_command(
            {
                "device_id": str(device["device_id"]),
                "project_id": project_id,
                "command": f"harness.{operation}",
                "payload": payload,
                "timeout_seconds": args.get("timeout_seconds"),
            },
            db_path,
            fallback_chat_id=chat_id,
        )
        return json_result({
            **result,
            "device_id": str(device["device_id"]),
            "project_id": project_id,
        })
    except Exception as exc:
        return json_result(remote_tool_error(exc))


__all__ = ["TOOL_NAME", "TOOL_DEF", "TOOL_METADATA", "handler"]
