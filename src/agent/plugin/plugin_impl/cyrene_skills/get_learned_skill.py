"""Tool implementation for GetLearnedSkill."""

from __future__ import annotations

import json
from typing import Any

from agent.plugin import PluginContext
from agent.plugin.native_runtime import run_context_value
from .definitions import get_native_tool_def

TOOL_NAME = "GetLearnedSkill"
TOOL_DEF = get_native_tool_def(TOOL_NAME)


async def _tool_get_learned_skill(
    args: dict[str, Any],
    context: PluginContext,
) -> str:
    name = str(args.get("name") or "").strip()
    if not name:
        return json.dumps({"ok": False, "error": "name is required"}, ensure_ascii=False)

    try:
        import cyrene.learning.orchestrator as learning

        skill = await learning.get_learned_skill_by_name(
            name,
            session_id=str(run_context_value(context, "session_id") or ""),
        )
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
                        "implementation_kind": s.get("implementation_kind"),
                        "tool_name": (s.get("implementation_reference") or {}).get("tool_name"),
                        "script_language": (s.get("implementation_reference") or {}).get("language"),
                        "script_path": (s.get("implementation_reference") or {}).get("script_path"),
                        "requires_runtime_approval": bool(
                            (s.get("implementation_reference") or {}).get("requires_runtime_approval")
                        ),
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
