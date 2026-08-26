"""Tool implementation for editing one scheduled task."""

from __future__ import annotations

from typing import Any

from .definitions import get_native_tool_def
from cyrene.tooling.runtime_api import (
    compute_next_run,
    datetime,
    db,
    request_scope_elevation,
    timezone,
)

TOOL_NAME = "edit_task"
TOOL_DEF = get_native_tool_def(TOOL_NAME)

_EDITABLE_FIELDS = (
    "prompt",
    "action_type",
    "schedule_type",
    "schedule_value",
    "schedule_timezone",
    "permission_mode",
)
_SCHEDULE_FIELDS = frozenset({"schedule_type", "schedule_value", "schedule_timezone"})


async def _tool_edit_task(
    args: dict[str, Any],
    _bot: Any,
    _chat_id: int,
    db_path: str,
    _notify_state: dict[str, bool] | None,
) -> str:
    task_id = str(args.get("task_id") or "").strip()
    if not task_id:
        return "task_id is required."

    task = await db.get_task(db_path, task_id)
    if task is None:
        return f"Task {task_id} not found."

    provided = {
        field: args[field]
        for field in _EDITABLE_FIELDS
        if field in args and args[field] is not None
    }
    if not provided:
        return f"Task {task_id} was not changed: no editable fields were provided."

    updates: dict[str, Any] = {}
    if "prompt" in provided:
        prompt = str(provided["prompt"]).strip()
        if not prompt:
            return "prompt must not be empty."
        updates["prompt"] = prompt

    if "action_type" in provided:
        action_type = str(provided["action_type"]).strip().lower()
        if action_type not in {"message", "agent_task"}:
            return "action_type must be message or agent_task."
        updates["action_type"] = action_type

    if "permission_mode" in provided:
        permission_mode = str(provided["permission_mode"]).strip().lower()
        if permission_mode not in {"workspace_only", "full_access"}:
            return "permission_mode must be workspace_only or full_access."
        updates["permission_mode"] = permission_mode

    schedule_changed = any(field in provided for field in _SCHEDULE_FIELDS)
    if schedule_changed:
        schedule_type = str(provided.get("schedule_type", task.get("schedule_type") or "")).strip()
        schedule_value = str(provided.get("schedule_value", task.get("schedule_value") or "")).strip()
        schedule_timezone = str(
            provided.get("schedule_timezone", task.get("schedule_timezone") or "UTC")
        ).strip() or "UTC"

        try:
            next_run = compute_next_run(
                schedule_type,
                schedule_value,
                now=datetime.now(timezone.utc),
                timezone_name=schedule_timezone,
            )
        except ValueError as exc:
            return f"Invalid schedule: {exc}"

        if schedule_type == "once":
            schedule_value = next_run
        updates.update(
            {
                "schedule_type": schedule_type,
                "schedule_value": schedule_value,
                "schedule_timezone": schedule_timezone,
                "next_run": next_run,
            }
        )

    permission_mode = updates.get("permission_mode")
    if permission_mode == "full_access" and task.get("permission_mode") != "full_access":
        from cyrene.agent.context import has_temporary_full_access

        if not has_temporary_full_access():
            prompt_preview = str(updates.get("prompt") or task.get("prompt") or "")[:120]
            elevation_result = await request_scope_elevation(
                tool_name="edit_task",
                path_hint="",
                operation="定时任务的外部文件访问权限",
                reason=(
                    "此定时任务可能在执行时需要读写 workspace 之外的文件。\n"
                    f"任务内容：{prompt_preview}"
                ),
                permission_kind="task_permission_request",
                options=["仅此任务允许 full_access", "拒绝，保持 workspace_only"],
            )
            if elevation_result is not None:
                return elevation_result

    ok = await db.edit_task(db_path, task_id, updates)
    if not ok:
        return f"Task {task_id} not found."

    changed = ", ".join(updates)
    result = f"Task {task_id} updated: {changed}."
    if schedule_changed:
        result += f" Next run: {updates['next_run']}"
    return result


handler = _tool_edit_task

__all__ = ["TOOL_NAME", "TOOL_DEF", "handler", "_tool_edit_task"]
