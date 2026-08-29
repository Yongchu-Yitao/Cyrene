"""Invoke an explicitly granted Plugin pack on a paired Cyrene."""

from __future__ import annotations

import json
import hashlib
from typing import Any

from cyrene.core.plugin import PluginContext
from .control import (
    REMOTE_PLUGIN_PACK_PREFIX,
)
from .common import (
    remote_tool_error,
    request_remote_command,
    resolve_selected_remote_device,
)
from cyrene.plugins.native_runtime import json_result, run_context_value

TOOL_NAME = "RemoteHarness"
TOOL_DEF = {
    "type": "function",
    "function": {
        "name": TOOL_NAME,
        "description": (
            "List, describe, or directly invoke a Plugin from an explicitly "
            "granted Plugin pack on a paired Cyrene. This does not create a "
            "remote chat or start a second Agent. "
            "Never split or base64-encode files through a remote shell; use "
            "RemoteCyreneFiles for every file or directory transfer."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "device_id": {"type": "string"},
                "project_id": {
                    "type": "string",
                    "description": "A project explicitly shared by the remote device.",
                },
                "plugin_pack": {
                    "type": "string",
                    "description": "Exact granted Plugin pack id, such as cyrene_desktop.",
                },
                "operation": {
                    "type": "string",
                    "enum": ["list", "describe", "invoke"],
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
            "required": ["project_id", "plugin_pack", "operation"],
            "additionalProperties": False,
        },
    },
}
TOOL_METADATA = {
    # The operation is polymorphic; marking the plugin mutable ensures invoke
    # always passes through the host's central PreToolUse review.
    "read_only": False,
    "resource_keys": ("remote:{device_id}",),
    "requires_order": False,
}


async def handler(
    args: dict[str, Any],
    context: PluginContext,
) -> str:
    try:
        _chat, device = resolve_selected_remote_device(
            args, context
        )
        project_id = str(args.get("project_id") or "").strip()
        plugin_pack = str(args.get("plugin_pack") or "").strip()
        operation = str(args.get("operation") or "").strip()
        if not project_id or not plugin_pack:
            raise ValueError("project_id and plugin_pack are required")
        grant = REMOTE_PLUGIN_PACK_PREFIX + plugin_pack
        if grant not in (device.get("received_capabilities") or []):
            raise PermissionError(
                f"远程设备未授权直接调用插件包 {plugin_pack}"
            )

        destructive_approved = operation == "invoke"
        if operation == "invoke":
            capability_id = str(args.get("capability_id") or "").strip()
            if not capability_id:
                raise ValueError("capability_id is required for invoke")
            if not any(
                capability in (device.get("received_capabilities") or [])
                for capability in (
                    "workspace_file:metadata",
                    "remote_job:read",
                )
            ):
                raise PermissionError(
                    "远程设备不支持 remote_authorization_v1；请升级并重新授权后再直接调用"
                )
        payload = {
            key: args[key]
            for key in (
                "query",
                "limit",
                "capability_id",
                "capability_ids",
                "arguments",
                "timeout_seconds",
            )
            if key in args
        }
        payload["plugin_pack"] = plugin_pack
        authorization_arguments = {
            "device_id": str(device["device_id"]),
            "project_id": project_id,
            "plugin_pack": plugin_pack,
            "operation": operation,
            "capability_id": str(args.get("capability_id") or ""),
            "arguments": dict(args.get("arguments") or {}),
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
        payload["call_id"] = f"remote-harness:{authorization_hash}"
        payload["authorization"] = {
            "version": 1,
            "approved": True,
            "permission_mode": str(
                run_context_value(context, "permission_mode", "default")
                or "default"
            ),
            "arguments_sha256": authorization_hash,
            "destructive_approved": destructive_approved,
        }
        result = await request_remote_command(
            {
                "device_id": str(device["device_id"]),
                "project_id": project_id,
                "command": f"harness.{operation}",
                "payload": payload,
                "timeout_seconds": args.get("timeout_seconds"),
            },
            context,
        )
        return json_result({
            **result,
            "device_id": str(device["device_id"]),
            "project_id": project_id,
        })
    except Exception as exc:
        return json_result(remote_tool_error(exc, context))


__all__ = ["TOOL_NAME", "TOOL_DEF", "TOOL_METADATA", "handler"]
