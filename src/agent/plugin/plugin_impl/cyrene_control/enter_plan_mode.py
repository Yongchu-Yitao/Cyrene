"""Tool implementation for enter_plan_mode.

Lets the main agent self-trigger 计划模式: decompose the current request into
steps → tasks, show it in the right sidebar 计划 tab, and pause for the user's
approve / reject / revise decision.
"""

from __future__ import annotations

from typing import Any
from uuid import uuid4

from agent.plugin import PluginContext
from agent.plugin.native_runtime import json_result, publish_runtime_event, run_context_value
from .definitions import get_native_tool_def

TOOL_NAME = 'enter_plan_mode'
TOOL_DEF = get_native_tool_def(TOOL_NAME)


async def _tool_enter_plan_mode(args: dict[str, Any], context: PluginContext) -> str:
    if str(run_context_value(context, "agent_id", "main")) != "main":
        return "Only the main agent can enter plan mode."
    round_id = str(run_context_value(context, "round_id") or "").strip()
    if not round_id:
        return "Cannot enter plan mode outside an active chat round."

    title = str(args.get("title") or "").strip()
    raw_steps = args.get("steps")
    if not title or not isinstance(raw_steps, list) or not raw_steps:
        return json_result({
            "status": "invalid_arguments",
            "error": "title and at least one plan step are required",
        })
    plan_id = "plan_" + uuid4().hex[:12]
    steps: list[dict[str, Any]] = []
    for index, raw in enumerate(raw_steps[:20], start=1):
        if not isinstance(raw, dict):
            return json_result({
                "status": "invalid_arguments",
                "error": f"steps[{index - 1}] must be an object",
            })
        step_title = str(raw.get("title") or "").strip()
        if not step_title:
            return json_result({
                "status": "invalid_arguments",
                "error": f"steps[{index - 1}].title is required",
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
        "text": "请确认是否按这个计划继续。",
        "options": ["批准", "修改", "拒绝"],
        "allow_custom": True,
        "plan": plan,
        "option_count": 3,
    })


handler = _tool_enter_plan_mode

__all__ = ["TOOL_NAME", "TOOL_DEF", "handler", "_tool_enter_plan_mode"]
