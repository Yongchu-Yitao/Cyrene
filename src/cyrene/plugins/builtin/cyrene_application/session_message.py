from __future__ import annotations

from typing import Any

from cyrene.core.plugin import PluginContext
from cyrene.runtime.host_bridge import HostBridgeError, call_host
from cyrene.plugins.native_runtime import json_result, run_context_value
from cyrene.workbench.application import app_services
from cyrene.workbench.application.app_control import (
    audit,
    authorize,
    canonical_hash,
    envelope,
    publish_result,
    remember_idempotent,
    replay_idempotent,
)

TOOL_NAME = "CyreneSessionMessage"
TOOL_DEF = {"type": "function", "function": {
    "name": TOOL_NAME,
    "description": (
        "Put text in the composer shown in the exact current UI surface snapshot and dispatch it "
        "to that other task/chat as an agent-originated message. It cannot submit the "
        "calling session's own composer."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "snapshot_id": {"type": "string", "maxLength": 160},
            "revision": {"type": "integer", "minimum": 1},
            "node_id": {"type": "string", "maxLength": 160},
            "message": {"type": "string", "minLength": 1, "maxLength": 20000},
            "reason": {"type": "string", "minLength": 1, "maxLength": 500},
            "idempotency_key": {"type": "string", "minLength": 1, "maxLength": 160},
        },
        "required": ["snapshot_id", "revision", "node_id", "message", "reason", "idempotency_key"],
        "additionalProperties": False,
    },
}}
TOOL_METADATA = {
    "read_only": False,
    "resource_keys": ("cyrene:current-surface", "cyrene:sessions"),
    "requires_order": True,
}


def _find_node(node: Any, node_id: str) -> dict[str, Any] | None:
    if not isinstance(node, dict):
        return None
    if str(node.get("node_id") or "") == node_id:
        return node
    for child in node.get("children") or []:
        found = _find_node(child, node_id)
        if found is not None:
            return found
    return None


def _action(node: dict[str, Any], action_id: str) -> dict[str, Any] | None:
    return next((
        item for item in node.get("actions") or []
        if isinstance(item, dict) and str(item.get("action_id") or "") == action_id
    ), None)


async def handler(args: dict[str, Any], context: PluginContext) -> str:
    operation_id = "cyrene.session.message"
    message = str(args.get("message") or "").strip()
    op_args = {
        "snapshot_id": str(args.get("snapshot_id") or ""),
        "revision": int(args.get("revision") or 0),
        "node_id": str(args.get("node_id") or ""),
        "message": message,
    }
    key = str(args.get("idempotency_key") or "")
    fingerprint = canonical_hash(operation_id, op_args)
    replay = replay_idempotent(operation_id, key, fingerprint)
    if replay is not None:
        return json_result(replay)
    try:
        snapshot = await call_host("ui.snapshot.current", {
            "include": ["interactive"],
            "max_depth": 1,
            "parent_node_id": op_args["node_id"],
            "page_size": 1,
        })
    except HostBridgeError as exc:
        return json_result(envelope("unsupported", operation_id, str(exc), error_code=exc.code))
    if snapshot.get("ok") is False:
        return json_result(envelope(
            "error", operation_id, "Current UI surface snapshot is unavailable.",
            revision=snapshot.get("revision"),
            error_code=str(snapshot.get("error") or "surface_error"),
        ))
    if (
        str(snapshot.get("snapshot_id") or "") != op_args["snapshot_id"]
        or int(snapshot.get("revision") or 0) != op_args["revision"]
    ):
        return json_result(envelope(
            "error", operation_id, "The UI surface snapshot changed; read it again before sending.",
            revision=snapshot.get("revision"), error_code="stale_snapshot",
        ))
    node = _find_node(snapshot.get("root"), op_args["node_id"])
    state = dict((node or {}).get("state") or {})
    target_id = str(state.get("session_id") or "")
    target_kind = str(state.get("session_kind") or "")
    if (
        not node
        or str(node.get("role") or "") != "textbox"
        or target_kind not in {"chat", "task"}
        or not target_id
        or not bool(state.get("draft_empty"))
        or state.get("submit_exposed") is not False
        or _action(node, "set_value") is None
        or _action(node, "clear_value") is None
    ):
        return json_result(envelope(
            "error", operation_id,
            "The selected current-tree node is not an empty dispatchable session composer.",
            error_code="invalid_session_composer",
        ))
    calling_session_id = str(run_context_value(context, "session_id") or "")
    if target_id == calling_session_id:
        return json_result(envelope(
            "error", operation_id,
            "The agent may prepare its own visible draft but cannot submit a new run into itself.",
            error_code="self_session_submit_forbidden",
        ))
    approval = await authorize(
        operation_id,
        op_args,
        reason=str(args.get("reason") or ""),
    )
    if approval:
        return approval
    try:
        typed = await call_host("ui.gesture.execute_current", {
            "snapshot_id": op_args["snapshot_id"],
            "revision": op_args["revision"],
            "node_id": op_args["node_id"],
            "action_id": "set_value",
            "input": {"value": message},
        })
        if typed.get("ok") is False:
            result = envelope(
                "error", operation_id, "The current composer rejected the text.",
                revision=typed.get("revision"),
                error_code=str(typed.get("error") or "surface_error"),
            )
        else:
            delivery = await app_services.dispatch_session_message(
                target_kind,
                target_id,
                message,
                origin_session_id=calling_session_id,
            )
            cleared = await call_host("ui.gesture.execute_current", {
                "snapshot_id": op_args["snapshot_id"],
                "revision": int(typed.get("revision") or 0),
                "node_id": op_args["node_id"],
                "action_id": "clear_value",
                "input": {"expected_value": message},
            })
            result = envelope(
                "success", operation_id,
                "Agent-originated text was dispatched to the current-tree session.",
                revision=cleared.get("revision", typed.get("revision")),
                effects=[{
                    "target_session_id": target_id,
                    "target_session_kind": target_kind,
                    "run_id": str(delivery.get("run_id") or ""),
                    "delivery_status": str(delivery.get("status") or ""),
                    "draft_cleared": cleared.get("ok") is not False,
                }],
            )
    except (LookupError, ValueError) as exc:
        result = envelope(
            "error", operation_id,
            f"The message remains in the visible composer because dispatch failed: {exc}",
            error_code="session_dispatch_error",
        )
    except HostBridgeError as exc:
        result = envelope("unsupported", operation_id, str(exc), error_code=exc.code)
    result["audit_id"] = audit(
        operation_id, op_args,
        status=result["status"], risk="R2",
        diff={
            "target_session_id": target_id,
            "target_session_kind": target_kind,
            "origin_session_id": calling_session_id,
        },
        error_code=str(result.get("error_code") or ""),
    )
    remember_idempotent(operation_id, key, fingerprint, result)
    await publish_result(result)
    return json_result(result)


__all__ = ["TOOL_NAME", "TOOL_DEF", "TOOL_METADATA", "handler"]
