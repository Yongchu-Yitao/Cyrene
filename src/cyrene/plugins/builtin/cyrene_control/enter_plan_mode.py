"""Tool implementation for enter_plan_mode.

Lets the main agent self-trigger 计划模式: decompose the current request into
steps → tasks, show it in the right sidebar 计划 tab, and pause for the user's
approve / reject / revise decision.
"""

from __future__ import annotations

from typing import Any
from uuid import uuid4

from cyrene.core.plugin import PluginContext
from cyrene.plugins.native_runtime import (
    json_result,
    plugin_localized,
    publish_runtime_event,
    run_context_value,
)
from .definitions import get_native_tool_def

TOOL_NAME = 'enter_plan_mode'
TOOL_DEF = get_native_tool_def(TOOL_NAME)


async def _tool_enter_plan_mode(args: dict[str, Any], context: PluginContext) -> str:
    if str(run_context_value(context, "agent_id", "main")) != "main":
        return plugin_localized(
            context,
            "Only the main Agent can enter plan mode.",
            "只有主 Agent 可以进入计划模式。",
        )
    round_id = str(run_context_value(context, "round_id") or "").strip()
    if not round_id:
        return plugin_localized(
            context,
            "Plan mode requires an active chat turn.",
            "只能在进行中的聊天轮次进入计划模式。",
        )

    title = str(args.get("title") or "").strip()
    raw_steps = args.get("steps")
    if not title or not isinstance(raw_steps, list) or not raw_steps:
        return json_result({
            "status": "invalid_arguments",
            "error": plugin_localized(
                context,
                "A title and at least one plan step are required.",
                "必须提供标题和至少一个计划步骤。",
            ),
        })
    plan_id = "plan_" + uuid4().hex[:12]
    steps: list[dict[str, Any]] = []
    for index, raw in enumerate(raw_steps[:20], start=1):
        if not isinstance(raw, dict):
            return json_result({
                "status": "invalid_arguments",
                "error": plugin_localized(
                    context,
                    "steps[{index}] must be an object.",
                    "steps[{index}] 必须是对象。",
                    index=index - 1,
                ),
            })
        step_title = str(raw.get("title") or "").strip()
        if not step_title:
            return json_result({
                "status": "invalid_arguments",
                "error": plugin_localized(
                    context,
                    "steps[{index}].title is required.",
                    "必须填写 steps[{index}].title。",
                    index=index - 1,
                ),
            })
        tasks = [
            str(item or "").strip()
            for item in (raw.get("tasks") or ())[:20]
            if str(item or "").strip()
        ] if isinstance(raw.get("tasks") or (), list) else []
        steps.append({
            "id": f"step_{index}",
            "title": step_title,
            "tasks": tasks,
            "status": "pending",
            "note": "",
        })
    plan = {
        "planId": plan_id,
        "roundId": round_id,
        "title": title,
        "summary": str(args.get("summary") or "").strip(),
        "status": "proposed",
        "steps": steps,
    }
    await publish_runtime_event(context, {
        "type": "plan",
        "status": "proposed",
        "plan": plan,
        "round_id": round_id,
        "client_request_id": str(run_context_value(context, "client_request_id") or ""),
    })
    return json_result({
        "status": "awaiting_user",
        "question_id": "question_" + plan_id,
        "kind": "plan_confirmation",
        "text": plugin_localized(
            context,
            "Confirm whether to continue with this plan.",
            "请确认是否按这个计划继续。",
        ),
        "options": [
            plugin_localized(context, "Approve", "批准"),
            plugin_localized(context, "Revise", "修改"),
            plugin_localized(context, "Reject", "拒绝"),
        ],
        "allow_custom": True,
        "plan": plan,
        "option_count": 3,
    })


handler = _tool_enter_plan_mode

__all__ = ["TOOL_NAME", "TOOL_DEF", "handler", "_tool_enter_plan_mode"]
