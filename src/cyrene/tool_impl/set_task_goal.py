"""Tool implementation for set_task_goal.

Lets a Workbench task agent set or correct the current task's goal (and an
optional short title) — e.g. once it has explored the project and understands
what should be done, or when the user's opener was a question rather than a
goal. The task scope is resolved from the active session id, so the agent never
has to know (or be trusted with) the storage key.
"""

from __future__ import annotations

from typing import Any

from cyrene import tool_legacy as _legacy

TOOL_NAME = 'set_task_goal'
TOOL_DEF = next(td for td in _legacy.TOOL_DEFS if td["function"]["name"] == TOOL_NAME)


async def _tool_set_task_goal(
    args: dict[str, Any],
    _bot: Any,
    _chat_id: int,
    _db_path: str,
    _notify_state: dict[str, bool] | None,
) -> str:
    """Set/correct the current Workbench task's goal (+ optional title)."""
    from cyrene.agent.state import _current_session_id

    goal = str(args.get("goal", "") or "").strip()
    title = str(args.get("title", "") or "").strip()
    if len(goal) < 3:
        return "Not set: 'goal' is empty or too short."

    session_id = str(_current_session_id.get() or "").strip()
    if not session_id:
        return "Not set: set_task_goal is only available inside a Workbench task."

    # Lazy import: the store lives in the webui layer (loaded in the server
    # process); importing it at module load would invert package layering.
    from webui.routes import set_task_goal_for_session

    result = set_task_goal_for_session(session_id, goal, title)
    if not result.get("ok"):
        return "Not set: " + str(result.get("error") or "could not update the task.")
    saved_title = str(result.get("title") or "")
    msg = "Task goal updated: " + str(result.get("goal") or goal)
    if saved_title:
        msg += f" (title: {saved_title})"
    return msg


handler = _tool_set_task_goal

__all__ = ["TOOL_NAME", "TOOL_DEF", "handler", "_tool_set_task_goal"]
