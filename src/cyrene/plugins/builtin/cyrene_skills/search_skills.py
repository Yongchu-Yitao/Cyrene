"""Progressively discover enabled external Skills."""

from __future__ import annotations

import json
from typing import Any

from cyrene.core.plugin import PluginContext
from .skills import search_skills
from .definitions import get_native_tool_def

TOOL_NAME = "SearchSkills"
TOOL_DEF = get_native_tool_def(TOOL_NAME)
TOOL_METADATA = {"read_only": True, "resource_keys": ("skills:installed",), "requires_order": False}


async def _tool_search_skills(
    args: dict[str, Any],
    _context: PluginContext,
) -> str:
    query = str(args.get("query") or "").strip()
    results = search_skills(query)
    return json.dumps({"ok": True, "query": query, "count": len(results), "skills": results}, ensure_ascii=False)


handler = _tool_search_skills

__all__ = ["TOOL_NAME", "TOOL_DEF", "TOOL_METADATA", "handler", "_tool_search_skills"]
