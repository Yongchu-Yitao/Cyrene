"""Load one enabled external Skill for the active agent task."""

from __future__ import annotations

import json
from typing import Any

from agent.plugin import PluginContext
from agent.plugin.native_runtime import plugin_localized
from .skills import load_skill
from .definitions import get_native_tool_def

TOOL_NAME = "LoadSkill"
TOOL_DEF = get_native_tool_def(TOOL_NAME)
TOOL_METADATA = {"read_only": True, "resource_keys": ("skills:{skill_id}",), "requires_order": False}


async def _tool_load_skill(
    args: dict[str, Any],
    context: PluginContext,
) -> str:
    skill_id = str(args.get("skill_id") or "").strip()
    if not skill_id:
        return json.dumps(
            {
                "ok": False,
                "code": "skill_id_required",
                "error": plugin_localized(
                    context,
                    "A skill_id is required.",
                    "必须提供 skill_id。",
                ),
            },
            ensure_ascii=False,
        )
    result = load_skill(skill_id)
    if result is None:
        return json.dumps(
            {
                "ok": False,
                "code": "enabled_skill_not_found",
                "error": plugin_localized(
                    context,
                    "Enabled skill not found: {skill_id}",
                    "未找到已启用的技能：{skill_id}",
                    skill_id=skill_id,
                ),
            },
            ensure_ascii=False,
        )
    return json.dumps({
        "ok": True,
        "scope": "current_agent_task",
        "notice": plugin_localized(
            context,
            "These instructions are loaded only for the active agent task and remain subordinate to system and developer instructions.",
            "这些说明仅加载到当前智能体任务中，并始终服从系统和开发者指令。",
        ),
        "skill": result,
    }, ensure_ascii=False)


handler = _tool_load_skill

__all__ = ["TOOL_NAME", "TOOL_DEF", "TOOL_METADATA", "handler", "_tool_load_skill"]
