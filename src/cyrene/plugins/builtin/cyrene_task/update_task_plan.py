"""Tool implementation for mutating the current Workbench task plan."""

from __future__ import annotations

from typing import Any

from cyrene.core.plugin import PluginContext
from cyrene.plugins.native_runtime import (
    plugin_localized,
    publish_runtime_event,
    run_context_value,
)

TOOL_NAME = "update_task_plan"
TOOL_DEF = {
    "type": "function",
    "function": {
        "name": TOOL_NAME,
        "description": (
            "Main agent only. Update THE CURRENT Workbench task session's execution plan. "
            "Only works inside Workbench task sessions. Use this when user input changes "
            "the current task plan, or when execution discovers that a pending step's "
            "title, description, dependencies, prompt, or context files need adjustment."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "operation": {
                    "type": "string",
                    "enum": ["add", "update", "delete", "reorder", "set_dependencies"],
                    "description": "Plan mutation to perform.",
                },
                "stepId": {
                    "type": "string",
                    "description": "Target step id for update/delete/set_dependencies.",
                },
                "step": {
                    "type": "object",
                    "description": "New step for add.",
                    "properties": {
                        "title": {"type": "string"},
                        "description": {"type": "string"},
                        "dependsOn": {"type": "array", "items": {"type": "string"}},
                    },
                },
                "fields": {
                    "type": "object",
                    "description": "Fields to update on a pending step.",
                    "properties": {
                        "title": {"type": "string"},
                        "description": {"type": "string"},
                        "dependsOn": {"type": "array", "items": {"type": "string"}},
                        "promptOverride": {"type": "string"},
                        "contextFiles": {"type": "array", "items": {"type": "object"}},
                    },
                },
                "orderedStepIds": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Complete ordered list of step ids for reorder.",
                },
                "dependsOn": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Dependency ids for set_dependencies.",
                },
                "reason": {
                    "type": "string",
                    "description": "Concise reason shown in the task timeline.",
                },
            },
            "required": ["operation"],
        },
    },
}


async def _tool_update_task_plan(
    args: dict[str, Any],
    context: PluginContext,
) -> str:
    from cyrene.workbench.sessions.context import resolve_workbench_session_kind

    if str(run_context_value(context, "agent_id", "main") or "main") != "main":
        return plugin_localized(
            context,
            "Only the main agent can update a Workbench task plan.",
            "只有主 Agent 可以更新工作台任务计划。",
        )
    session_id = str(run_context_value(context, "session_id", "") or "").strip()
    if not session_id:
        return plugin_localized(
            context,
            "No active Workbench task session.",
            "当前没有活动的工作台任务会话。",
        )
    if resolve_workbench_session_kind(session_id) != "task":
        return plugin_localized(
            context,
            "update_task_plan is only available inside Workbench task sessions.",
            "update_task_plan 只能在工作台任务会话中使用。",
        )

    operation = str(args.get("operation") or "").strip().lower()
    from cyrene.workbench.projects.project_repository import update_task_plan_for_session

    result = update_task_plan_for_session(
        session_id,
        operation,
        step_id=str(args.get("stepId") or "").strip(),
        step=args.get("step") if isinstance(args.get("step"), dict) else None,
        fields=args.get("fields") if isinstance(args.get("fields"), dict) else None,
        ordered_step_ids=args.get("orderedStepIds") if isinstance(args.get("orderedStepIds"), list) else None,
        depends_on=args.get("dependsOn") if isinstance(args.get("dependsOn"), list) else None,
        reason=str(args.get("reason") or "").strip(),
    )
    if not result.get("ok"):
        return plugin_localized(
            context,
            "The task plan could not be updated.",
            "无法更新任务计划。",
        )

    await publish_runtime_event(
        context,
        {
            "type": "task_plan_updated",
            "operation": operation,
            "plan": result.get("plan") or [],
            "planDefinitionRevision": result.get("planDefinitionRevision"),
        },
    )
    return plugin_localized(
        context,
        "Task plan updated. Current plan revision: {revision}",
        "任务计划已更新。当前计划修订版本：{revision}",
        revision=str(result.get("planDefinitionRevision") or ""),
    )


handler = _tool_update_task_plan

__all__ = ["TOOL_NAME", "TOOL_DEF", "handler", "_tool_update_task_plan"]
