"""Tool implementation for enter_plan_mode.

Lets the main agent self-trigger 计划模式: decompose the current request into
steps, show it in the right sidebar 计划 tab, and pause for the user's
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
from .state import current_plan, persist_plan

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
        dependency_indexes: list[int] = []
        for dependency in (
            raw.get("dependsOnStepIndexes")
            if isinstance(raw.get("dependsOnStepIndexes"), list)
            else ()
        ):
            try:
                dependency_index = int(dependency)
            except (TypeError, ValueError):
                dependency_index = 0
            if dependency_index < 1 or dependency_index >= index:
                return json_result({
                    "status": "invalid_arguments",
                    "error": plugin_localized(
                        context,
                        "Step {step} has an invalid prerequisite index: {dependency}.",
                        "步骤 {step} 的前置步骤序号无效：{dependency}。",
                        step=index,
                        dependency=dependency,
                    ),
                })
            if dependency_index not in dependency_indexes:
                dependency_indexes.append(dependency_index)
        context_files = [
            {
                "source": "workspace",
                "path": str(item or "").strip(),
                "name": str(item or "").strip().replace("\\", "/").split("/")[-1],
            }
            for item in (
                raw.get("contextFiles")
                if isinstance(raw.get("contextFiles"), list)
                else ()
            )[:20]
            if str(item or "").strip()
        ]
        steps.append({
            "id": f"step_{index}",
            "title": step_title,
            "description": str(raw.get("description") or "").strip(),
            "dependsOn": [f"step_{dependency}" for dependency in dependency_indexes],
            "command": str(raw.get("command") or "").strip(),
            "contextFiles": context_files,
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
    if not persist_plan(context, plan):
        return json_result({
            "status": "plan_storage_unavailable",
            "error": plugin_localized(
                context,
                "The plan file could not be saved.",
                "无法保存计划文件。",
            ),
        })
    plan = current_plan(context) or plan
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
