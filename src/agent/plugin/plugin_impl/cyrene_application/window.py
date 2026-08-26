from __future__ import annotations

from typing import Any

from cyrene.runtime.host_bridge import HostBridgeError, call_host
from cyrene.tooling.runtime_api import json_result
from cyrene.workbench.app_control import audit, authorize, canonical_hash, envelope, publish_result, remember_idempotent, replay_idempotent

TOOL_NAME = "CyreneWindowControl"
TOOL_DEF = {"type": "function", "function": {
    "name": TOOL_NAME,
    "description": "Inspect or change only the window bound to this run's current Cyrene surface.",
    "parameters": {
        "type": "object",
        "properties": {
            "action": {"type": "string", "enum": ["status", "reveal", "focus", "hide", "minimize", "maximize", "restore", "enter_fullscreen", "exit_fullscreen", "set_frame", "quick_chat_open", "quick_chat_close", "quick_chat_status"]},
            "x_ratio": {"type": "number", "minimum": 0, "maximum": 1},
            "y_ratio": {"type": "number", "minimum": 0, "maximum": 1},
            "width_ratio": {"type": "number", "minimum": 0.2, "maximum": 1},
            "height_ratio": {"type": "number", "minimum": 0.2, "maximum": 1},
            "reason": {"type": "string", "maxLength": 500},
            "idempotency_key": {"type": "string", "minLength": 1, "maxLength": 160, "description": "Required for every window request so retries remain argument-bound; read-only status actions do not persist it."},
        },
        "required": ["action", "idempotency_key"],
        "additionalProperties": False,
    },
}}
TOOL_METADATA = {"read_only": False, "resource_keys": ("cyrene:current-window",), "requires_order": True}


async def handler(args: dict[str, Any], _bot: Any, _chat_id: int, _db_path: str, _notify: Any) -> str:
    action = str(args.get("action") or "")
    mutation = action not in {"status", "quick_chat_status"}
    op_args = {key: value for key, value in args.items() if key not in {"reason", "idempotency_key"}}
    fingerprint = canonical_hash("cyrene.app.window", op_args)
    key = str(args.get("idempotency_key") or "")
    if mutation:
        replay = replay_idempotent("cyrene.app.window", key, fingerprint)
        if replay is not None:
            return json_result(replay)
        if not key:
            return json_result(envelope("error", "cyrene.app.window", "idempotency_key is required.", error_code="idempotency_required"))
        approval = await authorize("cyrene.app.window", op_args, reason=str(args.get("reason") or ""))
        if approval:
            return approval
    try:
        host = await call_host("window.control", op_args)
        status = "success" if host.get("ok") is not False else "error"
        result = envelope(status, "cyrene.app.window", f"Current window action {action} completed." if status == "success" else "Current window action failed.", host=host)
    except HostBridgeError as exc:
        result = envelope("unsupported", "cyrene.app.window", str(exc), error_code=exc.code)
    if mutation:
        audit_id = audit("cyrene.app.window", op_args, status=result["status"], risk="R1", error_code=str(result.get("error_code") or ""))
        result["audit_id"] = audit_id
        remember_idempotent("cyrene.app.window", key, fingerprint, result)
        await publish_result(result)
    return json_result(result)


__all__ = ["TOOL_NAME", "TOOL_DEF", "TOOL_METADATA", "handler"]
