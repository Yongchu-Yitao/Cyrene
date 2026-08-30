"""Main-Agent tools for proposing a Goal and submitting a candidate result."""

from __future__ import annotations

from typing import Any

from cyrene.core.plugin import PluginContext
from cyrene.plugins.native_runtime import json_result, plugin_localized, publish_runtime_event


async def propose_goal(args: dict[str, Any], context: PluginContext) -> str:
    service = context.services.get("goal")
    if service is None:
        return plugin_localized(context, "Goal service is unavailable.", "目标服务当前不可用。")
    objective = str(args.get("objective") or "").strip()
    criteria = args.get("acceptanceCriteria")
    if len(objective) < 3 or not isinstance(criteria, list) or not criteria:
        return json_result({
            "status": "invalid_arguments",
            "error": plugin_localized(
                context,
                "A concrete objective and at least one acceptance criterion are required.",
                "需要提供明确目标和至少一项验收标准。",
            ),
        })
    goal = await service.propose_from_context(context, args)
    await publish_runtime_event(context, {
        "type": "goal_proposed",
        "goal": service.public(goal),
    })
    return json_result({
        "status": "proposed",
        "goal": service.public(goal),
        "message": plugin_localized(
            context,
            "The Goal proposal is ready for the user to edit and confirm.",
            "目标提案已生成，等待用户编辑并确认。",
        ),
    })


async def submit_goal_result(args: dict[str, Any], context: PluginContext) -> str:
    service = context.services.get("goal")
    if service is None:
        return plugin_localized(context, "Goal service is unavailable.", "目标服务当前不可用。")
    summary = str(args.get("summary") or "").strip()
    if not summary:
        return json_result({
            "status": "invalid_arguments",
            "error": plugin_localized(context, "A result summary is required.", "必须提供结果摘要。"),
        })
    goal = await service.submit_candidate_from_context(context, args)
    if goal is None:
        return plugin_localized(context, "No active Goal was found.", "当前没有正在执行的目标。")
    await publish_runtime_event(context, {
        "type": "goal_candidate_submitted",
        "goal": service.public(goal),
    })
    return json_result({
        "status": "candidate_submitted",
        "message": plugin_localized(
            context,
            "The candidate result was submitted for independent review.",
            "候选结果已提交独立审查。",
        ),
    })


PROPOSE_SCHEMA = {
    "type": "object",
    "properties": {
        "objective": {"type": "string", "minLength": 3},
        "acceptanceCriteria": {
            "type": "array",
            "minItems": 1,
            "maxItems": 20,
            "items": {"type": "string"},
        },
        "constraints": {"type": "array", "items": {"type": "string"}, "maxItems": 20},
        "outOfScope": {"type": "array", "items": {"type": "string"}, "maxItems": 20},
        "suggestedDurationSeconds": {"type": "integer", "minimum": 300, "maximum": 604800},
    },
    "required": ["objective", "acceptanceCriteria"],
    "additionalProperties": False,
}

SUBMIT_SCHEMA = {
    "type": "object",
    "properties": {
        "summary": {"type": "string", "minLength": 1},
        "evidence": {"type": "array", "items": {"type": "string"}, "maxItems": 50},
        "deliverables": {"type": "array", "items": {"type": "string"}, "maxItems": 50},
    },
    "required": ["summary"],
    "additionalProperties": False,
}


__all__ = ["PROPOSE_SCHEMA", "SUBMIT_SCHEMA", "propose_goal", "submit_goal_result"]
