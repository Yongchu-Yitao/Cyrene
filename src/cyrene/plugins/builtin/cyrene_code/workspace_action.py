"""Agent-facing access to configured workspace actions."""

from __future__ import annotations

import json
from typing import Any

from cyrene.core.plugin import PluginContext, application_plugin_service
from cyrene.plugins.native_runtime import plugin_localized, run_context_value

TOOL_NAME = "WorkspaceAction"
TOOL_DEF = {
    "type": "function",
    "function": {
        "name": TOOL_NAME,
        "description": (
            "Discover or run project-defined Build, Run, Test, and Preview actions. "
            "Prefer this over constructing shell commands when a matching action exists. "
            "Local build/test actions may be retried inside a Goal; deployment and publishing "
            "are intentionally unsupported."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "operation": {
                    "type": "string",
                    "enum": ["list", "start", "status", "stop", "restart", "claim"],
                    "default": "list",
                },
                "action_id": {"type": "string"},
                "execution_id": {"type": "string"},
                "current_path": {"type": "string"},
            },
            "required": [],
            "additionalProperties": False,
        },
    },
}
TOOL_METADATA = {
    "main_only": True,
    "requires_order": True,
    "resource_keys": ("workspace-execution",),
    "read_only_operations": ("list", "status"),
    # The Toolbox injects its host-only reveal flag for this dedicated action.
    # A surface is therefore revealed only when the Agent deliberately asks to
    # show the named file, not for every status poll or automatic discovery.
    "resource_effects": ({
        "argument_path": ("current_path",),
        "kind": "file",
        "access": "execute",
        "phase": "both",
    },),
}


def permission_boundary(
    arguments: dict[str, Any], _context: PluginContext
) -> dict[str, Any] | None:
    operation = str(arguments.get("operation") or "list")
    if operation in {"list", "status", "stop"}:
        return None
    return {
        "kind": "workspace_execution",
        "operation": f"Workspace action: {operation}",
        "reason": str(arguments.get("action_id") or arguments.get("execution_id") or ""),
        "always_review": operation in {"start", "restart"},
        "requires_human": False,
    }


async def handler(args: dict[str, Any], context: PluginContext) -> str:
    service = application_plugin_service("workspace_execution")
    if service is None:
        raise RuntimeError(plugin_localized(
            context, "Workspace execution is unavailable.", "工作区执行功能不可用。"
        ))
    operation = str(args.get("operation") or "list")
    project_id = str(run_context_value(context, "project_id") or "")
    chat_id = str(run_context_value(context, "session_id") or "")
    current_path = str(args.get("current_path") or "")
    if operation == "list":
        result = await service.discover(project_id, current_path)
    elif operation == "start":
        goal_id = ""
        goal_service = application_plugin_service("goal")
        if goal_service is not None and chat_id:
            goal = await goal_service.get(chat_id)
            if goal and str(goal.get("status") or "") in {"active", "reviewing", "reflecting"}:
                goal_id = str(goal.get("id") or "")
        result = {"execution": await service.start(
            project_id,
            str(args.get("action_id") or ""),
            current_path=current_path,
            chat_id=chat_id,
            goal_id=goal_id,
        )}
    elif operation == "status":
        execution_id = str(args.get("execution_id") or "")
        result = ({"execution": await service.refresh(execution_id)} if execution_id
                  else await service.list(project_id))
    elif operation == "stop":
        result = {"execution": await service.stop(str(args.get("execution_id") or ""))}
    elif operation == "restart":
        result = {"execution": await service.restart(str(args.get("execution_id") or ""))}
    elif operation == "claim":
        result = {"execution": await service.claim(str(args.get("execution_id") or ""))}
    else:
        raise ValueError("unsupported workspace action operation")
    return json.dumps(result, ensure_ascii=False, default=str)


__all__ = ["TOOL_DEF", "TOOL_METADATA", "TOOL_NAME", "handler", "permission_boundary"]
