"""Tool implementation for UninstallSkill."""

from __future__ import annotations

from typing import Any

from .definitions import get_native_tool_def
from cyrene.tooling.runtime_api import (
    build_skills,
    request_scope_elevation,
    uninstall_skill,
    json,
)

TOOL_NAME = 'UninstallSkill'
TOOL_DEF = get_native_tool_def(TOOL_NAME)


async def _tool_uninstall_skill(args: dict[str, Any], _bot: Any, _chat_id: int, _db_path: str, _notify_state: dict[str, bool] | None) -> str:
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
    reviewed = await request_scope_elevation(
        tool_name=TOOL_NAME,
        path_hint=f"extension:skill:{match['id']}",
        operation=f"卸载全局 Skill：{match.get('name') or match['id']}",
        reason="Uninstalling a Skill changes Cyrene's persistent global capabilities.",
        permission_kind="extension_change",
    )
    if reviewed is not None:
        return reviewed
    removed = uninstall_skill(match["id"])
    return json.dumps({"ok": removed, "skill_id": match["id"], "name": match.get("name")}, ensure_ascii=False)


handler = _tool_uninstall_skill

__all__ = ["TOOL_NAME", "TOOL_DEF", "handler", "_tool_uninstall_skill"]
