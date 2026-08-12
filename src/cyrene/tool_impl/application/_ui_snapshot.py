from __future__ import annotations

from typing import Any

from cyrene.agent.context import current_run_context
from cyrene.runtime.host_bridge import HostBridgeError, call_host
from cyrene.tooling.runtime_api import json_result
from cyrene.workbench.app_control import envelope


def _annotate_session_relation(snapshot: dict[str, Any]) -> None:
    """Expose whether the visible UI belongs to the run reading the tree."""
    surface = snapshot.get("surface")
    if not isinstance(surface, dict):
        surface = {}
        snapshot["surface"] = surface
    visible_session_id = str(surface.get("visible_session_id") or "")
    calling_session_id = str(current_run_context().session_id or "")
    if visible_session_id and calling_session_id:
        relation = "same" if visible_session_id == calling_session_id else "different"
    else:
        relation = "unknown"
    surface["calling_session_id"] = calling_session_id
    surface["session_relation"] = relation


async def read_current_tree(
    args: dict[str, Any],
    *,
    operation_id: str,
    success_message: str = "Current UI surface snapshot read.",
) -> str:
    from cyrene.observability import debug

    try:
        snapshot = await call_host("ui.snapshot.current", args)
        if snapshot.get("ok") is False:
            await debug.publish_event({"type": "ui_snapshot_status", "status": "failed", "error_code": snapshot.get("error", "surface_error")})
            return json_result(envelope("error", operation_id, "Current UI snapshot could not be read.", revision=snapshot.get("revision"), error_code=snapshot.get("error", "surface_error")))
        _annotate_session_relation(snapshot)
        await debug.publish_event({"type": "ui_snapshot_status", "status": "success", "revision": snapshot.get("revision")})
        return json_result(envelope("success", operation_id, success_message, revision=snapshot.get("revision"), snapshot=snapshot))
    except HostBridgeError as exc:
        await debug.publish_event({"type": "ui_snapshot_status", "status": "failed", "error_code": exc.code})
        return json_result(envelope("unsupported", operation_id, str(exc), error_code=exc.code))
__all__ = ["read_current_tree"]
