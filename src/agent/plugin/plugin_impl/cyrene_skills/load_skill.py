"""Load one enabled external Skill for the active agent task."""

from __future__ import annotations

import json
from typing import Any

from agent.plugin import PluginContext
from cyrene.learning.skills import load_skill
from .definitions import get_native_tool_def

TOOL_NAME = "LoadSkill"
TOOL_DEF = get_native_tool_def(TOOL_NAME)
TOOL_METADATA = {"read_only": True, "resource_keys": ("skills:{skill_id}",), "requires_order": False}


async def _tool_load_skill(
    args: dict[str, Any],
    _context: PluginContext,
) -> str:
    skill_id = str(args.get("skill_id") or "").strip()
    if not skill_id:
        return json.dumps({"ok": False, "error": "skill_id is required"}, ensure_ascii=False)
    result = load_skill(skill_id)
    if result is None:
        return json.dumps({"ok": False, "error": f"enabled skill not found: {skill_id}"}, ensure_ascii=False)
    return json.dumps({
        "ok": True,
        "scope": "current_agent_task",
        "notice": "These instructions are loaded only for the active agent task and remain subordinate to system and developer instructions.",
        "skill": result,
    }, ensure_ascii=False)


handler = _tool_load_skill

__all__ = ["TOOL_NAME", "TOOL_DEF", "TOOL_METADATA", "handler", "_tool_load_skill"]
