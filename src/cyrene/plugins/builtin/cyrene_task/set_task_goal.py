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

from cyrene.core.plugin import PluginContext
from cyrene.plugins.native_runtime import run_context_value
from cyrene.localization import app_language, localized

TOOL_NAME = 'set_task_goal'
TOOL_DEF = {
    "type": "function",
    "function": {
        "name": TOOL_NAME,
        "description": (
            "Set or correct the current Workbench task's goal, short title, "
            "and/or one-line summary. Provide at least one field. A title "
            "manually locked by the user is preserved."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "goal": {"type": "string"},
                "title": {"type": "string"},
                "summary": {"type": "string"},
            },
            "additionalProperties": False,
        },
    },
}


async def _tool_set_task_goal(
    args: dict[str, Any],
    context: PluginContext,
) -> str:
    """Set/correct the current Workbench task's goal, title, and/or summary."""
    language = app_language(context.data.get("lang"))
    goal = str(args.get("goal", "") or "").strip()
    title = str(args.get("title", "") or "").strip()
    summary = str(args.get("summary", "") or "").strip()
    if not goal and not title and not summary:
        return localized(
            "Not set: provide at least one of goal, title, or summary.",
            "未更新：请至少提供目标、标题或简介中的一项。",
            language=language,
        )
    if goal and len(goal) < 3:
        return localized(
            "Not set: the goal is too short.",
            "未更新：目标过短。",
            language=language,
        )

    session_id = str(run_context_value(context, "session_id", "") or "").strip()
    if not session_id:
        return localized(
            "Not set: set_task_goal is available only inside a Workbench task.",
            "未更新：set_task_goal 仅可在工作台任务中使用。",
            language=language,
        )

    # Lazy import: the store lives in the webui layer (loaded in the server
    # process); importing it at module load would invert package layering.
    from cyrene.workbench.tasks.task_goal_service import set_task_goal_for_session

    result = await set_task_goal_for_session(session_id, goal, title, summary)
    if not result.get("ok"):
        return localized(
            "Not set: {error}",
            "未更新：{error}",
            language=language,
            error=str(result.get("error") or localized(
                "Could not update the task.",
                "无法更新任务。",
                language=language,
            )),
        )
    parts: list[str] = []
    if result.get("goal"):
        parts.append("goal=" + str(result.get("goal")))
    if result.get("title"):
        parts.append("title=" + str(result.get("title")))
    if result.get("summary"):
        parts.append("summary=" + str(result.get("summary")))
    msg = (
        localized(
            "Task updated: {fields}",
            "任务已更新：{fields}",
            language=language,
            fields=", ".join(parts),
        )
        if parts
        else localized("Task updated.", "任务已更新。", language=language)
    )
    if result.get("titleBlocked"):
        msg += localized(
            " (The title was manually set by the user and was not changed; other fields were updated.)",
            "（注意：标题已被用户手动设定，未改动；其余字段已更新。）",
            language=language,
        )
    return msg


handler = _tool_set_task_goal

__all__ = ["TOOL_NAME", "TOOL_DEF", "handler", "_tool_set_task_goal"]
