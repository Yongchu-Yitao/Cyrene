"""Tool implementation for InstallSkill."""

from __future__ import annotations

from typing import Any

from cyrene.tooling.native_definitions import get_native_tool_def
from cyrene.tooling.runtime_api import (
    install_skill_from_path,
    request_scope_elevation,
    resolve_tool_path,
    json,
)

TOOL_NAME = 'InstallSkill'
TOOL_DEF = get_native_tool_def(TOOL_NAME)


async def _tool_install_skill(args: dict[str, Any], _bot: Any, _chat_id: int, _db_path: str, _notify_state: dict[str, bool] | None) -> str:
    path_str = str(args.get("path", "")).strip()
    if not path_str:
        return json.dumps({"ok": False, "error": "path is required"}, ensure_ascii=False)
    try:
        source = resolve_tool_path(path_str)
    except ValueError:
        return json.dumps({"ok": False, "error": "skill source must be within workspace"}, ensure_ascii=False)
    source = source.resolve()
    if not source.exists():
        return json.dumps({"ok": False, "error": f"path does not exist: {source}"}, ensure_ascii=False)
    reviewed = await request_scope_elevation(
        tool_name=TOOL_NAME,
        path_hint=f"extension:skill:{source}",
        operation=f"安装全局 Skill：{source.name}",
        reason="Installing a Skill changes Cyrene's persistent global capabilities.",
        permission_kind="extension_change",
    )
    if reviewed is not None:
        return reviewed
    result = install_skill_from_path(source)
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
    return json.dumps({"ok": False, "error": result.get("error", "unknown error")}, ensure_ascii=False)


handler = _tool_install_skill

__all__ = ["TOOL_NAME", "TOOL_DEF", "handler", "_tool_install_skill"]
