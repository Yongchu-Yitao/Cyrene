"""Update the durable progress of an approved Workbench conversation plan."""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

from cyrene.core.plugin import PluginContext
from cyrene.plugins.native_runtime import (
    plugin_localized,
    publish_runtime_event,
    run_context_value,
)

from .definitions import get_native_tool_def

TOOL_NAME = "update_plan_progress"
TOOL_DEF = get_native_tool_def(TOOL_NAME)


def _decoded(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    try:
        return json.loads(value)
    except (TypeError, ValueError, json.JSONDecodeError):
        return value


def _current_plan(context: PluginContext) -> dict[str, Any] | None:
    if context.tree is None or not context.tree_id or not context.node_id:
        return None
    try:
        path = context.tree.get_path(context.tree_id, context.node_id)
    except Exception:
        return None
    for node in reversed(path):
        value = node.value if isinstance(node.value, Mapping) else {}
        if value.get("role") != "tool_results":
            continue
        results = value.get("results")
        for result in reversed(results if isinstance(results, list) else []):
            if not isinstance(result, Mapping):
                continue
            if str(result.get("name") or "") != "enter_plan_mode":
                continue
            payload = _decoded(result.get("value"))
            plan = payload.get("plan") if isinstance(payload, Mapping) else None
            if isinstance(plan, Mapping):
                return {
                    **dict(plan),
                    "steps": [
                        dict(item)
                        for item in (plan.get("steps") or ())
                        if isinstance(item, Mapping)
                    ],
                }
    return None


async def _tool_update_plan_progress(
    args: dict[str, Any],
    context: PluginContext,
) -> str:
    if str(run_context_value(context, "agent_id", "main")) != "main":
        return plugin_localized(
            context,
            "Only the main agent can update plan progress.",
            "只有主 Agent 可以更新计划进度。",
        )
    session_id = str(run_context_value(context, "session_id") or "").strip()
    if not session_id:
        return plugin_localized(
            context,
            "No active Workbench conversation plan.",
            "当前没有活动的工作台对话计划。",
        )
    try:
        step = int(args.get("step"))
    except (TypeError, ValueError):
        return plugin_localized(context, "Invalid plan step.", "计划步骤无效。")
    status = str(args.get("status") or "").strip()
    note = str(args.get("note") or "").strip()
    if status not in {"in_progress", "completed", "failed", "skipped"}:
        return plugin_localized(context, "Invalid plan status.", "计划状态无效。")
    plan = _current_plan(context)
    steps = plan.get("steps") if isinstance(plan, dict) else None
    if not isinstance(steps, list) or step < 1 or step > len(steps):
        return plugin_localized(
            context,
            "No active approved plan was found.",
            "未找到活动且已批准的计划。",
        )
    target = steps[step - 1]
    target["status"] = status
    target["note"] = note
    if status == "in_progress":
        plan["status"] = "active"
        for index, other in enumerate(steps):
            if index != step - 1 and other.get("status") == "in_progress":
                other["status"] = "pending"
    elif all(
        str(item.get("status") or "pending")
        in {"completed", "failed", "skipped"}
        for item in steps
    ):
        plan["status"] = "completed"
    elif str(plan.get("status") or "") == "proposed":
        plan["status"] = "active"
    await publish_runtime_event(context, {
        "type": "plan_progress",
        "plan": plan,
        "step": step,
        "status": status,
        "note": note,
    })
    status_label = plugin_localized(
        context,
        status,
        {
            "in_progress": "进行中",
            "completed": "已完成",
            "failed": "失败",
            "skipped": "已跳过",
        }.get(status, status),
    )
    return plugin_localized(
        context,
        "Plan step {step} was updated to {status}.",
        "计划步骤 {step} 已更新为{status}。",
        step=step,
        status=status_label,
    )


handler = _tool_update_plan_progress

__all__ = ["TOOL_NAME", "TOOL_DEF", "handler", "_tool_update_plan_progress"]
