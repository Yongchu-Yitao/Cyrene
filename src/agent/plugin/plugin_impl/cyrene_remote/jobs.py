"""Durable high-level jobs on a selected remote Cyrene project."""

from __future__ import annotations

import hashlib
import json
from typing import Any
from uuid import uuid4

from cyrene.agent.context import current_run_context
from .common import (
    remote_tool_error,
    request_remote_command,
    resolve_selected_remote_device,
)
from cyrene.tooling.runtime_api import json_result, request_scope_elevation

TOOL_NAME = "RemoteCyreneJobs"
TOOL_DEF = {
    "type": "function",
    "function": {
        "name": TOOL_NAME,
        "description": (
            "Start, follow, wait for, interrupt, or cancel a durable process job "
            "on a selected remote Cyrene. Relative cwd/artifact paths are based at the "
            "shared project; absolute paths follow the controller chat's local permission "
            "mode. Prefer this over an interactive remote shell for long-running work."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "device_id": {"type": "string"},
                "project_id": {"type": "string"},
                "operation": {"type": "string", "enum": ["start", "read", "wait", "interrupt", "cancel", "artifacts"]},
                "job_id": {"type": "string"},
                "argv": {"type": "array", "items": {"type": "string"}, "maxItems": 256},
                "cwd": {"type": "string"},
                "env": {"type": "object", "additionalProperties": {"type": "string"}},
                "artifact_paths": {"type": "array", "items": {"type": "string"}, "maxItems": 100},
                "cursor": {"type": "integer", "minimum": 0},
                "limit": {"type": "integer", "minimum": 1, "maximum": 262144},
                "timeout_seconds": {"type": "number", "minimum": 0.1, "maximum": 55},
                "reason": {"type": "string"},
            },
            "required": ["project_id", "operation"],
            "additionalProperties": False,
        },
    },
}
TOOL_METADATA = {
    "read_only": True,
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
        _chat, device = resolve_selected_remote_device(args, db_path, fallback_chat_id=chat_id)
        operation = str(args.get("operation") or "")
        project_id = str(args.get("project_id") or "").strip()
        if not project_id:
            raise ValueError("project_id is required")
        required = "remote_job:run" if operation == "start" else "remote_job:control" if operation in {"interrupt", "cancel"} else "remote_job:read"
        if required not in (device.get("received_capabilities") or []):
            raise PermissionError(f"remote device did not grant {required}")
        outside_workspace = bool(
            operation == "start"
            and (
                _is_absolute_path(args.get("cwd"))
                or any(_is_absolute_path(item) for item in args.get("artifact_paths") or [])
            )
        )
        if operation in {"start", "interrupt", "cancel"}:
            exact_operation = json.dumps(
                {
                    "device_id": str(device["device_id"]),
                    "project_id": project_id,
                    "operation": operation,
                    "job_id": str(args.get("job_id") or ""),
                    "argv": list(args.get("argv") or []),
                    "cwd": str(args.get("cwd") or "."),
                    "env": dict(args.get("env") or {}),
                    "artifact_paths": list(args.get("artifact_paths") or []),
                },
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            permission = await request_scope_elevation(
                tool_name=TOOL_NAME,
                path_hint=f"{device['device_id']}:{project_id}:{str(args.get('cwd') or '.')}",
                operation=f"远程作业 {operation}",
                reason=(
                    str(args.get("reason") or "执行用户请求的远程作业")
                    + "\n精确操作："
                    + exact_operation[:8000]
                    + "\nSHA-256："
                    + hashlib.sha256(exact_operation.encode()).hexdigest()
                ),
                permission_kind="remote_job_operation",
                options=["允许执行这一次", "拒绝"],
                scope_hint="远程项目中的 ",
                meta_extra={"device_id": str(device["device_id"]), "project_id": project_id, "operation": operation},
            )
            if permission is not None:
                return permission
        payload = {
            key: args[key]
            for key in ("job_id", "argv", "cwd", "env", "artifact_paths", "cursor", "limit", "timeout_seconds")
            if key in args
        }
        if operation == "start":
            origin_chat_id = current_run_context().session_id or str(chat_id or "")
            exact = {
                "device_id": str(device["device_id"]),
                "project_id": project_id,
                "argv": list(args.get("argv") or []),
                "cwd": str(args.get("cwd") or "."),
                "env": dict(args.get("env") or {}),
                "artifact_paths": list(args.get("artifact_paths") or []),
                "origin_chat_id": origin_chat_id,
            }
            digest = hashlib.sha256(json.dumps(exact, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
            payload["job_id"] = str(args.get("job_id") or f"job_{digest[:32]}")
            payload["origin_chat_id"] = origin_chat_id
        authorization_arguments = {
            "device_id": str(device["device_id"]),
            "project_id": project_id,
            "command": f"jobs.{operation}",
            "payload": payload,
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
        payload["_authorization"] = {
            "version": 1,
            "approved": True,
            "permission_mode": current_run_context().permission_mode,
            "scope": "single_operation",
            "outside_workspace": outside_workspace,
            "arguments_sha256": authorization_hash,
        }
        result = await request_remote_command(
            {
                "device_id": str(device["device_id"]),
                "project_id": project_id,
                "command": f"jobs.{operation}",
                "payload": payload,
                "idempotency_key": f"remote-job:{payload.get('job_id') or uuid4().hex}:{operation}:{payload.get('cursor') or 0}",
                "timeout_seconds": args.get("timeout_seconds") or 30,
            },
            db_path,
            fallback_chat_id=chat_id,
        )
        return json_result(result)
    except Exception as exc:
        return json_result(remote_tool_error(exc))


def _is_absolute_path(value: Any) -> bool:
    text = str(value or "").replace("\\", "/").strip()
    return bool(text) and (text.startswith("/") or ":" in text.split("/", 1)[0])


__all__ = ["TOOL_NAME", "TOOL_DEF", "TOOL_METADATA", "handler"]
