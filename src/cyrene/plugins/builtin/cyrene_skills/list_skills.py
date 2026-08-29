"""Tool implementation for ListSkills."""

from __future__ import annotations

import json
from typing import Any

from cyrene.core.plugin import PluginContext
from .definitions import get_native_tool_def
from .skills import build_skills

TOOL_NAME = 'ListSkills'
TOOL_DEF = get_native_tool_def(TOOL_NAME)


async def _tool_list_skills(
    _args: dict[str, Any],
    _context: PluginContext,
) -> str:
    skills = [
        {
            "id": s.get("id"),
            "name": s.get("name"),
            "desc": s.get("desc", ""),
            "enabled": s.get("enabled", True),
            "files": len(s.get("files", [])),
        }
        for s in build_skills()
    ]
    return json.dumps({"ok": True, "skills": skills}, ensure_ascii=False)


handler = _tool_list_skills

__all__ = ["TOOL_NAME", "TOOL_DEF", "handler", "_tool_list_skills"]
