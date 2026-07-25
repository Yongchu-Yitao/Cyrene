"""Tool implementation for mutating the current Workbench task plan."""

from __future__ import annotations

from typing import Any

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
    _bot: Any,
    _chat_id: int,
    _db_path: str,
    _notify_state: dict[str, bool] | None,
) -> str:
    from cyrene.agent.state import _current_agent_id, _current_session_id, _publish_runtime_event
    from cyrene.workbench_context import resolve_workbench_session_kind

    if _current_agent_id.get() != "main":
        return "Only the main agent can update a Workbench task plan."
    session_id = str(_current_session_id.get() or "").strip()
    if not session_id:
        return "No active Workbench task session."
    if resolve_workbench_session_kind(session_id) != "task":
        return "update_task_plan is only available inside Workbench task sessions."

    operation = str(args.get("operation") or "").strip().lower()
    from cyrene.workbench_runtime import update_task_plan_for_session

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
        return "Task plan not updated: " + str(result.get("error") or "unknown error")

    await _publish_runtime_event({
        "type": "task_plan_updated",
        "operation": operation,
        "plan": result.get("plan") or [],
        "planDefinitionRevision": result.get("planDefinitionRevision"),
    })
    return "Task plan updated. Current plan revision: " + str(result.get("planDefinitionRevision") or "")


handler = _tool_update_task_plan

__all__ = ["TOOL_NAME", "TOOL_DEF", "handler", "_tool_update_task_plan"]
