"""Tool implementation for UninstallSkill."""

from __future__ import annotations

import json
from typing import Any

from agent.plugin import PluginContext
from agent.plugin.native_runtime import plugin_localized
from .definitions import get_native_tool_def
from .skills import build_skills, uninstall_skill

TOOL_NAME = 'UninstallSkill'
TOOL_DEF = get_native_tool_def(TOOL_NAME)


async def _tool_uninstall_skill(
    args: dict[str, Any],
    context: PluginContext,
) -> str:
    skill_id = str(args.get("skill_id", "")).strip()
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
    skills = build_skills()
    match = None
    for s in skills:
        if s.get("id") == skill_id or s.get("name", "").lower() == skill_id.lower():
            match = s
            break
    if not match:
        return json.dumps(
            {
                "ok": False,
                "code": "skill_not_found",
                "error": plugin_localized(
                    context,
                    "Skill not found: {skill_id}",
                    "未找到技能：{skill_id}",
                    skill_id=skill_id,
                ),
            },
            ensure_ascii=False,
        )
    removed = uninstall_skill(match["id"])
    return json.dumps({"ok": removed, "skill_id": match["id"], "name": match.get("name")}, ensure_ascii=False)


handler = _tool_uninstall_skill

__all__ = ["TOOL_NAME", "TOOL_DEF", "handler", "_tool_uninstall_skill"]
