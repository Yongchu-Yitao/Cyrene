"""Tool implementation for set_task_goal.

Lets a Workbench task agent set or correct the current task's goal, short title,
and/or one-line summary (简介) — e.g. once it has explored the project and
understands what should be done, or when the user's opener was a question rather
than a goal. The task scope is resolved from the active session id, so the agent
never has to know (or be trusted with) the storage key. The title is locked once
the user has manually edited it; the agent can still update goal and summary.
"""

from __future__ import annotations

from typing import Any

from cyrene.tooling.native_definitions import get_native_tool_def

TOOL_NAME = 'set_task_goal'
TOOL_DEF = get_native_tool_def(TOOL_NAME)


async def _tool_set_task_goal(
    args: dict[str, Any],
    _bot: Any,
    _chat_id: int,
    _db_path: str,
    _notify_state: dict[str, bool] | None,
) -> str:
    """Set/correct the current Workbench task's goal, title, and/or summary."""
    from cyrene.agent.context import get_current_session_id

    goal = str(args.get("goal", "") or "").strip()
    title = str(args.get("title", "") or "").strip()
    summary = str(args.get("summary", "") or "").strip()
    if not goal and not title and not summary:
        return "Not set: provide at least one of goal, title, or summary."
    if goal and len(goal) < 3:
        return "Not set: 'goal' is too short."

    session_id = str(get_current_session_id() or "").strip()
    if not session_id:
        return "Not set: set_task_goal is only available inside a Workbench task."

    # Lazy import: the store lives in the webui layer (loaded in the server
    # process); importing it at module load would invert package layering.
    from cyrene.workbench.runtime import set_task_goal_for_session

    result = await set_task_goal_for_session(session_id, goal, title, summary)
    if not result.get("ok"):
        return "Not set: " + str(result.get("error") or "could not update the task.")
    parts: list[str] = []
    if result.get("goal"):
        parts.append("goal=" + str(result.get("goal")))
    if result.get("title"):
        parts.append("title=" + str(result.get("title")))
    if result.get("summary"):
        parts.append("summary=" + str(result.get("summary")))
    msg = "Task updated: " + ", ".join(parts) if parts else "Task updated."
    if result.get("titleBlocked"):
        msg += "（注意：标题已被用户手动设定，未改动；其余字段已更新。）"
    return msg


handler = _tool_set_task_goal

__all__ = ["TOOL_NAME", "TOOL_DEF", "handler", "_tool_set_task_goal"]
