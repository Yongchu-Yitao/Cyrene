"""Tool implementation for schedule_task."""

from __future__ import annotations

from typing import Any

from cyrene.tooling.native_definitions import get_native_tool_def
from cyrene.tooling.runtime_api import (
    request_scope_elevation,
    compute_next_run,
    datetime,
    db,
    timezone,
)
from cyrene.workbench.context import resolve_project_data_key_for_session

TOOL_NAME = 'schedule_task'
TOOL_DEF = get_native_tool_def(TOOL_NAME)


async def _tool_schedule_task(args: dict[str, Any], _bot: Any, chat_id: int, db_path: str, _notify_state: dict[str, bool] | None) -> str:
    from cyrene.agent.context import get_current_session_id

    stype = str(args["schedule_type"])
    svalue = str(args["schedule_value"])
    schedule_timezone = str(args.get("schedule_timezone") or "UTC").strip() or "UTC"
    now = datetime.now(timezone.utc)
    permission_mode = str(args.get("permission_mode", "workspace_only") or "workspace_only").strip().lower()
    if permission_mode not in ("workspace_only", "full_access"):
        permission_mode = "workspace_only"

    next_run = compute_next_run(
        stype,
        svalue,
        now=now,
        timezone_name=schedule_timezone,
    )
    if stype == "once":
        # Persist the normalized UTC time as the stored value too, so a re-read
        # of the task shows exactly when it will fire.
        svalue = next_run

    # 如果任务需要 full_access 权限，先向用户申请（已授权时跳过）
    if permission_mode == "full_access":
        from cyrene.agent.context import has_temporary_full_access
        if not has_temporary_full_access():
            prompt_preview = str(args.get("prompt", ""))[:120]
            elevation_result = await request_scope_elevation(
                tool_name="schedule_task",
                path_hint="",
                operation="定时任务的外部文件访问权限",
                reason=f"此定时任务可能在执行时需要读写 workspace 之外的文件。\n任务内容：{prompt_preview}",
                permission_kind="task_permission_request",
                options=["仅此任务允许 full_access", "拒绝，保持 workspace_only"],
            )
            # None=已授权(auto 模式批准/full_access 短路)，继续创建任务；
            # 非 None=拒绝串或 awaiting_user JSON，直接回传给 agent。
            if elevation_result is not None:
                return elevation_result

    origin_session_id = str(get_current_session_id() or "").strip()
    project_id = resolve_project_data_key_for_session(origin_session_id)
    action_type = str(args.get("action_type") or "agent_task").strip().lower()
    if action_type not in {"message", "agent_task"}:
        action_type = "agent_task"
    task_id = await db.create_task(
        db_path,
        chat_id,
        str(args["prompt"]),
        stype,
        svalue,
        next_run,
        permission_mode=permission_mode,
        project_id=project_id,
        schedule_timezone=schedule_timezone,
        origin_session_id=origin_session_id,
        action_type=action_type,
    )
    return f"Task {task_id} scheduled. Next run: {next_run} 权限模式：{permission_mode}"


handler = _tool_schedule_task

__all__ = ["TOOL_NAME", "TOOL_DEF", "handler", "_tool_schedule_task"]
