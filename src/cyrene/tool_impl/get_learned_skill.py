"""Tool implementation for GetLearnedSkill."""

from __future__ import annotations

from typing import Any

from cyrene import tool_legacy as _legacy
from cyrene.tool_legacy import json

TOOL_NAME = "GetLearnedSkill"
TOOL_DEF = next(td for td in _legacy.TOOL_DEFS if td["function"]["name"] == TOOL_NAME)


async def _tool_get_learned_skill(args: dict[str, Any], _bot: Any, _chat_id: int, _db_path: str, _notify_state: dict[str, bool] | None) -> str:
    name = str(args.get("name") or "").strip()
    if not name:
        return json.dumps({"ok": False, "error": "name is required"}, ensure_ascii=False)

    try:
        from cyrene import behavior_learning as _bl

        skill = await _bl.get_learned_skill_by_name(name)
        if skill is None:
            return json.dumps({"ok": False, "error": f"no active learned skill named '{name}'"}, ensure_ascii=False)

        detail = {
            "ok": True,
            "skill": {
                "id": skill["skill_id"],
                "name": skill["name"],
                "description": skill["description"],
                "version": skill["version"],
                "skill_type": skill["skill_type"],
                "requires_llm": skill["requires_llm"],
                "risk_level": skill["risk_level"],
                "trigger": skill["trigger"],
                "input_schema": skill["input_schema"],
                "steps": [
                    {
                        "step_id": s.get("step_id"),
                        "description": s.get("description"),
                        "tool_name": (s.get("implementation_reference") or {}).get("tool_name"),
                        "failure_policy": s.get("failure_policy"),
                    }
                    for s in skill["steps"]
                ],
                "run_statistics": skill["run_statistics"],
                "created_at": skill["created_at"],
                "updated_at": skill["updated_at"],
            },
        }
        return json.dumps(detail, ensure_ascii=False)
    except Exception as exc:
        return json.dumps({"ok": False, "error": f"failed to retrieve skill: {exc}"}, ensure_ascii=False)


handler = _tool_get_learned_skill

__all__ = ["TOOL_NAME", "TOOL_DEF", "handler", "_tool_get_learned_skill"]
