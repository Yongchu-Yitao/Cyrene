"""Tool implementation for UninstallSkill."""

from __future__ import annotations

import json
from typing import Any

from agent.plugin import PluginContext
from .definitions import get_native_tool_def
from cyrene.learning.skills import build_skills, uninstall_skill

TOOL_NAME = 'UninstallSkill'
TOOL_DEF = get_native_tool_def(TOOL_NAME)


async def _tool_uninstall_skill(
    args: dict[str, Any],
    _context: PluginContext,
) -> str:
    skill_id = str(args.get("skill_id", "")).strip()
    if not skill_id:
        return json.dumps({"ok": False, "error": "skill_id is required"}, ensure_ascii=False)
    skills = build_skills()
    match = None
    for s in skills:
        if s.get("id") == skill_id or s.get("name", "").lower() == skill_id.lower():
            match = s
            break
    if not match:
        return json.dumps({"ok": False, "error": f"skill not found: {skill_id}"}, ensure_ascii=False)
    removed = uninstall_skill(match["id"])
    return json.dumps({"ok": removed, "skill_id": match["id"], "name": match.get("name")}, ensure_ascii=False)


handler = _tool_uninstall_skill

__all__ = ["TOOL_NAME", "TOOL_DEF", "handler", "_tool_uninstall_skill"]
