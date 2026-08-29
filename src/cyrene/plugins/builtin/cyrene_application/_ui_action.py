from __future__ import annotations

from typing import Any

from cyrene.core.plugin import PluginContext

from cyrene.runtime.host_bridge import HostBridgeError, call_host
from cyrene.plugins.native_runtime import json_result
from cyrene.workbench.application.app_control import audit, authorize, canonical_hash, envelope, publish_result, remember_idempotent, replay_idempotent

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


async def execute_action(
    args: dict[str, Any],
    _context: PluginContext,
    *,
    operation_family: str,
    allowed_kinds: frozenset[str] | None = None,
    required_gesture_aliases: frozenset[str] | None = None,
) -> str:
    op_args = {
        key: value
        for key, value in args.items()
        if key not in {"reason", "idempotency_key", "delegation_quote", "delegation_operations"}
    }
    key = str(args.get("idempotency_key") or "")
    # A renderer can complete an action even when its acknowledgement is lost.
    # Resolve an exact retry before touching the live tree so a successful
    replay_candidates = (
        operation_family,
        operation_family + ".r2",
        operation_family + ".r3",
        "cyrene.question.answer",
        "cyrene.approval.answer",
    )
    for replay_operation_id in replay_candidates:
        replay = replay_idempotent(
            replay_operation_id,
            key,
            canonical_hash(replay_operation_id, op_args),
        )
        if replay is not None and replay.get("error_code") != "idempotency_conflict":
            return json_result(replay)
    try:
        snapshot = await call_host("ui.snapshot.current", {
            "include": ["interactive"],
            "max_depth": 1,
            "parent_node_id": str(op_args.get("node_id") or ""),
            "snapshot_id": str(op_args.get("snapshot_id") or ""),
            "revision": int(op_args.get("revision") or 0),
            "action_id": str(op_args.get("action_id") or ""),
            "allow_compatible_action": True,
            "page_size": 1,
        })
    except HostBridgeError as exc:
        return json_result(envelope("unsupported", operation_family, str(exc), error_code=exc.code))
    if snapshot.get("ok") is False:
        return json_result(envelope(
            "error", operation_family, "Current UI snapshot is unavailable.",
            revision=snapshot.get("revision"), error_code=str(snapshot.get("error") or "surface_error"),
        ))
    revision_matches = int(snapshot.get("revision") or 0) == int(op_args.get("revision") or 0)
    revision_compatible = snapshot.get("requested_revision_compatible") is True
    if (
        str(snapshot.get("snapshot_id") or "") != str(op_args.get("snapshot_id") or "")
        or not (revision_matches or revision_compatible)
    ):
        return json_result(envelope(
            "error", operation_family, "The UI snapshot changed; read the current snapshot again.",
            revision=snapshot.get("revision"), error_code="stale_snapshot",
        ))
    node = _find_node(snapshot.get("root"), str(op_args.get("node_id") or ""))
    action = next((
        item for item in (node or {}).get("actions") or []
        if isinstance(item, dict) and str(item.get("action_id") or "") == str(op_args.get("action_id") or "")
    ), None)
    if not node or not action:
        return json_result(envelope("error", operation_family, "The requested action is not listed on the current component.", error_code="action_not_available"))
    if allowed_kinds is not None and str(action.get("kind") or "") not in allowed_kinds:
        return json_result(envelope("error", operation_family, "The requested gesture does not match the action declared on this component.", error_code="gesture_not_available"))
    gesture_aliases = {
        str(item or "")
        for item in (action.get("gesture_aliases") or [])
        if str(item or "")
    }
    if required_gesture_aliases is not None and gesture_aliases.isdisjoint(required_gesture_aliases):
        return json_result(envelope(
            "error", operation_family,
            "The requested component action does not declare this gesture.",
            error_code="gesture_not_available",
        ))
    if str(action.get("requires_capability") or ""):
        return json_result(envelope("error", operation_family, "This component action must be performed through its typed capability.", error_code="requires_capability"))
    risk = str(action.get("risk") or "R1")
    if risk not in {"R0", "R1", "R2", "R3"}:
        return json_result(envelope("error", operation_family, "The component declared an unsupported action risk.", error_code="invalid_action_risk"))
    action_id = str(action.get("action_id") or "")
    if action_id.startswith("answer_"):
        operation_id = "cyrene.approval.answer" if risk == "R3" else "cyrene.question.answer"
    else:
        operation_id = {
            "R0": operation_family,
            "R1": operation_family,
            "R2": operation_family + ".r2",
            "R3": operation_family + ".r3",
        }[risk]
    fingerprint = canonical_hash(operation_id, op_args)
    replay = replay_idempotent(operation_id, key, fingerprint)
    if replay is not None:
        return json_result(replay)
    approval = await authorize(
        operation_id, op_args,
        reason=str(args.get("reason") or ""),
        delegation_quote=str(args.get("delegation_quote") or ""),
        delegation_operations=args.get("delegation_operations"),
    )
    if approval:
        return approval
    try:
        host_args = dict(op_args)
        if operation_family in {"cyrene.ui.click", "cyrene.ui.double_click"}:
            host_args["_agent_cursor_mode"] = "click"
        elif operation_family == "cyrene.ui.drag":
            host_args["_agent_cursor_mode"] = "drag"
        elif operation_family in {"cyrene.ui.type", "cyrene.ui.scroll"}:
            host_args["_agent_cursor_mode"] = "target"
        response = await call_host("ui.gesture.execute_current", host_args)
        ok = response.get("ok") is not False
        result = envelope("success" if ok else "error", operation_id, "Current UI action completed." if ok else "Current UI action was rejected.", revision=response.get("revision"), ui=response, error_code="" if ok else response.get("error", "surface_error"))
    except HostBridgeError as exc:
        result = envelope("unsupported", operation_id, str(exc), error_code=exc.code)
    result["audit_id"] = audit(operation_id, op_args, status=result["status"], risk=risk, error_code=str(result.get("error_code") or ""))
    remember_idempotent(operation_id, key, fingerprint, result)
    from cyrene.observability import debug
    await debug.publish_event({
        "type": "ui_gesture_status",
        "status": result["status"],
        "revision": result.get("revision"),
        "error_code": str(result.get("error_code") or ""),
    })
    await publish_result(result)
    return json_result(result)


__all__ = ["execute_action"]
