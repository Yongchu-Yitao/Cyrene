"""Read one text resource inside an enabled external Skill root."""

from __future__ import annotations

import json
from typing import Any

from cyrene.core.plugin import PluginContext
from cyrene.plugins.native_runtime import plugin_language, plugin_localized
from .skills import read_skill_resource
from .definitions import get_native_tool_def

TOOL_NAME = "ReadSkillResource"
TOOL_DEF = get_native_tool_def(TOOL_NAME)
TOOL_METADATA = {"read_only": True, "resource_keys": ("skills:{skill_id}",), "requires_order": False}


async def _tool_read_skill_resource(
    args: dict[str, Any],
    context: PluginContext,
) -> str:
    skill_id = str(args.get("skill_id") or "").strip()
    resource_path = str(args.get("path") or "").strip()
    if not skill_id or not resource_path:
        return json.dumps(
            {
                "ok": False,
                "code": "skill_resource_parameters_required",
                "error": plugin_localized(
                    context,
                    "skill_id and path are required.",
                    "必须提供 skill_id 和 path。",
                ),
            },
            ensure_ascii=False,
        )
    result = read_skill_resource(
        skill_id,
        resource_path,
        language=plugin_language(context),
    )
    result["scope"] = "current_agent_task"
    return json.dumps(result, ensure_ascii=False)


handler = _tool_read_skill_resource

__all__ = ["TOOL_NAME", "TOOL_DEF", "TOOL_METADATA", "handler", "_tool_read_skill_resource"]
