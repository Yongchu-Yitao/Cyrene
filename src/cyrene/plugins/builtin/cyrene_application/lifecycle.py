from __future__ import annotations

from typing import Any
from cyrene.core.plugin import PluginContext

from cyrene.runtime.host_actions import cancel_action, list_actions, schedule_action
from cyrene.runtime.host_bridge import HostBridgeError, call_host
from cyrene.plugins.native_runtime import json_result
from cyrene.workbench.application.app_control import DELEGATION_OPERATIONS_SCHEMA, audit, authorization_decision, authorize, canonical_hash, envelope, publish_result, remember_idempotent, replay_idempotent

TOOL_NAME = "CyreneLifecycleControl"
TOOL_DEF = {"type": "function", "function": {
    "name": TOOL_NAME,
    "description": "Read, cancel or schedule a durable Cyrene restart/quit after the current reply is finalized.",
    "parameters": {
        "type": "object",
        "properties": {
            "operation": {"type": "string", "enum": ["status", "cancel", "restart_backend", "restart_app", "quit"]},
            "action_id": {"type": "string", "maxLength": 160},
            "delegation_quote": {"type": "string", "maxLength": 500},
            "delegation_operations": DELEGATION_OPERATIONS_SCHEMA,
            "reason": {"type": "string", "maxLength": 500},
            "idempotency_key": {"type": "string", "maxLength": 160},
        },
        "required": ["operation"],
        "additionalProperties": False,
    },
}}
TOOL_METADATA = {"read_only": False, "resource_keys": ("cyrene:lifecycle",), "requires_order": True}


async def handler(args: dict[str, Any], _context: PluginContext) -> str:
    operation = str(args.get("operation") or "")
    if operation == "status":
        return json_result(envelope("success", "cyrene.app.lifecycle", "Pending host actions read.", actions=list_actions()))
    if operation == "cancel":
        op_id = "cyrene.app.lifecycle.cancel"
        op_args = {"action_id": str(args.get("action_id") or "")}
        key = str(args.get("idempotency_key") or "")
        if not key:
            return json_result(envelope("error", op_id, "idempotency_key is required.", error_code="idempotency_required"))
        fingerprint = canonical_hash(op_id, op_args)
        replay = replay_idempotent(op_id, key, fingerprint)
        if replay is not None:
            return json_result(replay)
        approval = await authorize(op_id, op_args, reason=str(args.get("reason") or ""))
        if approval:
            return approval
        try:
            action = cancel_action(str(args.get("action_id") or ""))
            result = envelope("success", op_id, "Pending host action cancelled.", action_id=action["action_id"], effects=[action])
            result["audit_id"] = audit(op_id, op_args, status="success", risk="R1")
        except (LookupError, ValueError) as exc:
            result = envelope("error", op_id, str(exc), error_code="lifecycle_error")
        remember_idempotent(op_id, key, fingerprint, result)
        await publish_result(result)
        return json_result(result)
    key = str(args.get("idempotency_key") or "")
    if not key:
        return json_result(envelope("error", "cyrene.app.lifecycle", "idempotency_key is required.", error_code="idempotency_required"))
    op_args = {"action": operation}
    fingerprint = canonical_hash("cyrene.app.lifecycle", op_args)
    replay = replay_idempotent("cyrene.app.lifecycle", key, fingerprint)
    if replay is not None:
        return json_result(replay)
    approval = await authorize(
        "cyrene.app.lifecycle", op_args,
        reason=str(args.get("reason") or ""),
        delegation_quote=str(args.get("delegation_quote") or ""),
        delegation_operations=args.get("delegation_operations"),
    )
    if approval:
        return approval
    try:
        host_status = await call_host("host.status")
        if host_status.get("ok") is False or host_status.get("hostKind") != "electron":
            raise ValueError("Electron host is unavailable")
        decision = authorization_decision("cyrene.app.lifecycle", op_args)
        action = schedule_action(
            operation,
            idempotency_key=key,
            parameter_hash=fingerprint,
            expected_app_version=str(host_status.get("appVersion") or ""),
            approval_receipt=str(decision.get("receipt") or fingerprint),
        )
        result = envelope("scheduled", "cyrene.app.lifecycle", "Host action scheduled after final reply persistence.", action_id=action["action_id"], apply_mode="deferred", effects=[{"action": operation, "status": action["status"]}])
        result["audit_id"] = audit("cyrene.app.lifecycle", op_args, status="scheduled", risk="R3")
    except (HostBridgeError, ValueError) as exc:
        result = envelope("error", "cyrene.app.lifecycle", str(exc), error_code="lifecycle_error")
    remember_idempotent("cyrene.app.lifecycle", key, fingerprint, result)
    await publish_result(result)
    return json_result(result)


__all__ = ["TOOL_NAME", "TOOL_DEF", "TOOL_METADATA", "handler"]
