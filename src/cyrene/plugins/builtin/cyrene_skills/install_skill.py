"""Tool implementation for InstallSkill."""

from __future__ import annotations

import json
from typing import Any

from cyrene.core.plugin import PluginContext
from .definitions import get_native_tool_def
from cyrene.plugins.native_runtime import (
    plugin_language,
    plugin_localized,
    resolve_tool_path,
)
from .skills import install_skill_from_path

TOOL_NAME = 'InstallSkill'
TOOL_DEF = get_native_tool_def(TOOL_NAME)


async def _tool_install_skill(
    args: dict[str, Any],
    context: PluginContext,
) -> str:
    path_str = str(args.get("path", "")).strip()
    if not path_str:
        return json.dumps(
            {
                "ok": False,
                "code": "skill_path_required",
                "error": plugin_localized(
                    context,
                    "A skill path is required.",
                    "必须提供技能路径。",
                ),
            },
            ensure_ascii=False,
        )
    try:
        source = resolve_tool_path(path_str, context)
    except ValueError:
        return json.dumps(
            {
                "ok": False,
                "code": "skill_path_outside_workspace",
                "error": plugin_localized(
                    context,
                    "The skill source must be within the workspace.",
                    "技能来源必须位于工作区内。",
                ),
            },
            ensure_ascii=False,
        )
    source = source.resolve()
    if not source.exists():
        return json.dumps(
            {
                "ok": False,
                "code": "skill_path_not_found",
                "error": plugin_localized(
                    context,
                    "The skill path does not exist: {path}",
                    "技能路径不存在：{path}",
                    path=source,
                ),
            },
            ensure_ascii=False,
        )
    result = install_skill_from_path(
        source,
        language=plugin_language(context),
    )
    if result.get("ok"):
        skill = result.get("skill", {})
        summary = {
            "ok": True,
            "skill": {
                "id": skill.get("id"),
                "name": skill.get("name"),
                "desc": skill.get("desc"),
                "enabled": skill.get("enabled", True),
                "files": len(skill.get("files", [])),
            },
        }
        if result.get("already_installed"):
            summary["already_installed"] = True
        return json.dumps(summary, ensure_ascii=False)
    return json.dumps(
        {
            "ok": False,
            "code": str(result.get("code") or "skill_install_failed"),
            "error": str(
                result.get("error")
                or plugin_localized(
                    context,
                    "The skill could not be installed.",
                    "无法安装技能。",
                )
            ),
        },
        ensure_ascii=False,
    )


handler = _tool_install_skill

__all__ = ["TOOL_NAME", "TOOL_DEF", "handler", "_tool_install_skill"]
