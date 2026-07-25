"""Update the durable progress of an approved Workbench conversation plan."""

from __future__ import annotations

from typing import Any

from cyrene.tooling.native_definitions import get_native_tool_def

TOOL_NAME = "update_plan_progress"
TOOL_DEF = get_native_tool_def(TOOL_NAME)


async def _tool_update_plan_progress(
    args: dict[str, Any],
    _bot: Any,
    _chat_id: int,
    _db_path: str,
    _notify_state: dict[str, bool] | None,
) -> str:
    from cyrene.agent.state import _current_agent_id, _current_session_id, _publish_runtime_event
    from cyrene.workbench_chat_service import update_chat_plan_progress

    if _current_agent_id.get() != "main":
        return "Only the main agent can update plan progress."
    session_id = str(_current_session_id.get() or "").strip()
    if not session_id:
        return "No active Workbench conversation plan."
    try:
        step = int(args.get("step"))
    except (TypeError, ValueError):
        return "Invalid plan step."
    status = str(args.get("status") or "").strip()
    note = str(args.get("note") or "").strip()
    plan = update_chat_plan_progress(session_id, step, status, note)
    if not plan:
        return "No active approved plan was found."
    await _publish_runtime_event({
        "type": "plan_progress",
        "plan": plan,
        "step": step,
        "status": status,
        "note": note,
    })
    return f"Plan step {step} updated to {status}."


handler = _tool_update_plan_progress

__all__ = ["TOOL_NAME", "TOOL_DEF", "handler", "_tool_update_plan_progress"]
