"""Tool implementation for RunLearnedSkill."""

from __future__ import annotations

import re as _re
from typing import Any

from cyrene.tooling.native_definitions import get_native_tool_def
from cyrene.behavior_learning import (
    _AUTO_REPLAY_BLOCKED_TOOLS as _BL_BLOCKED_TOOLS,
    _HIGH_RISK_TOOLS as _BL_HIGH_RISK_TOOLS,
)
from cyrene.tooling.runtime_support import json
from cyrene.tooling.executor import _execute_tool, _skip_action_recording
from cyrene.tooling.adapters.learned_skills import normalize_learned_step

TOOL_NAME = "RunLearnedSkill"
TOOL_DEF = get_native_tool_def(TOOL_NAME)

# WARNING: When adding a new destructive or interactive capability,
# add it to the appropriate set below so RunLearnedSkill does not auto-execute it.
_HIGH_RISK_TOOLS: frozenset[str] = _BL_HIGH_RISK_TOOLS | frozenset({
    "browser_navigate", "browser_click", "browser_click_ref", "browser_click_text", "browser_click_at",
    "browser_type", "browser_type_ref",
})

_AUTO_REPLAY_BLOCKED_TOOLS = _BL_BLOCKED_TOOLS


def _has_unsafe_step(steps: list[dict[str, Any]]) -> bool:
    for step in steps:
        if not step.get("enabled", True):
            continue
        ref = step.get("implementation_reference") or {}
        if str(step.get("implementation_kind") or "") == "script":
            # Learning-agent generated Python/shell is executable code.  It is
            # retained as the Skill implementation, but this auto-run tool has
            # no approval token and therefore must always fall back to the
            # normal agent permission path.
            return True
        tool_name = str(ref.get("tool_name") or "")
        if tool_name in _HIGH_RISK_TOOLS or tool_name in _AUTO_REPLAY_BLOCKED_TOOLS:
            return True
    return False


def _validate_params(schema: list[dict[str, Any]], params: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    for item in schema:
        name = str(item.get("parameter_name") or item.get("name") or "").strip()
        if not name:
            continue
        value = params.get(name)
        if bool(item.get("required")) and (value is None or value == ""):
            errors.append(f"missing required parameter: {name}")
            continue
        if value is None:
            continue
        kind = str(item.get("type") or "text")
        if kind.startswith("list") and not isinstance(value, list):
            errors.append(f"parameter {name} must be a list")
        elif kind == "boolean" and not isinstance(value, bool):
            errors.append(f"parameter {name} must be a boolean")
        elif kind == "number" and not isinstance(value, (int, float)):
            errors.append(f"parameter {name} must be a number")
        elif kind in {"text", "string", "path", "url", "date"} and not isinstance(value, str):
            errors.append(f"parameter {name} must be a string")
    return errors


def _params_with_defaults(schema: list[dict[str, Any]], params: dict[str, Any]) -> dict[str, Any]:
    resolved = dict(params)
    for item in schema:
        name = str(item.get("parameter_name") or item.get("name") or "").strip()
        if name and name not in resolved and "default_value" in item:
            resolved[name] = item.get("default_value")
    return resolved


def _resolve_value_template(value: Any, params: dict[str, Any]) -> Any:
    if isinstance(value, str):
        if "{{" not in value:
            return value
        full = _re.fullmatch(r"\{\{(.+?)\}\}", value)
        if full and full.group(1) in params:
            return params[full.group(1)]
        def _replacer(m: _re.Match) -> str:
            key = m.group(1)
            return str(params.get(key, m.group(0)))
        return _re.sub(r"\{\{(.+?)\}\}", _replacer, value)
    if isinstance(value, list):
        return [_resolve_value_template(item, params) for item in value]
    if isinstance(value, dict):
        return {key: _resolve_value_template(item, params) for key, item in value.items()}
    return value


async def _execute_one(
    tool_name: str,
    resolved_args: dict[str, Any] | Any,
    bot: Any,
    chat_id: int,
    db_path: str,
    notify_state: dict[str, bool] | None,
) -> dict[str, Any]:
    if not isinstance(resolved_args, dict):
        raw = f"Tool {tool_name} failed: resolved arguments must be a dict, got {type(resolved_args).__name__}"
        return {"tool": tool_name, "ok": False, "output": raw, "truncated": False}
    try:
        raw = await _execute_tool(tool_name, resolved_args, bot, chat_id, db_path, notify_state)
    except Exception as exc:
        raw = f"Tool {tool_name} failed: {exc}"
    raw_str = str(raw)
    ok = not raw_str.startswith(f"Tool {tool_name} failed:")
    is_truncated = len(raw_str) > 4000
    return {
        "tool": tool_name,
        "ok": ok,
        "output": raw_str[:4000],
        "truncated": is_truncated,
    }


async def _tool_run_learned_skill(args: dict[str, Any], bot: Any, chat_id: int, db_path: str, notify_state: dict[str, bool] | None) -> str:
    name = str(args.get("name") or "").strip()
    if not name:
        return json.dumps({"ok": False, "error": "name is required"}, ensure_ascii=False)

    params = dict(args.get("params") or {})

    try:
        from cyrene import behavior_learning as _bl

        skill = await _bl.get_learned_skill_by_name(name)
        if skill is None:
            return json.dumps({"ok": False, "error": f"no active learned skill named '{name}'"}, ensure_ascii=False)

        schema = skill.get("input_schema") or []
        params = _params_with_defaults(schema, params)
        param_errors = _validate_params(schema, params)
        if param_errors:
            await _bl.record_manual_skill_run(
                skill["skill_id"], int(skill["version"]),
                execution_status="fallback",
            )
            return json.dumps({"ok": False, "error": "; ".join(param_errors)}, ensure_ascii=False)

        if _has_unsafe_step(skill["steps"]):
            await _bl.record_manual_skill_run(
                skill["skill_id"], int(skill["version"]),
                execution_status="fallback",
            )
            return json.dumps(
                {"ok": False, "error": "skill contains high-risk steps that cannot be auto-executed"},
                ensure_ascii=False,
            )

        results: list[dict[str, Any]] = []
        all_ok = True
        # Suppress action recording during execution to avoid circular relearning
        # and inflated telemetry — the skill's steps are already learned.
        _rec_token = _skip_action_recording.set(True)
        try:
            for step in skill["steps"]:
                if not step.get("enabled", True):
                    continue
                tool_name, args_template = normalize_learned_step(step)
                items = args_template.get("_items")
                if isinstance(items, list) and items:
                    for item_args in items:
                        resolved = _resolve_value_template(item_args, params)
                        step_result = await _execute_one(tool_name, resolved, bot, chat_id, db_path, notify_state)
                        all_ok = all_ok and step_result["ok"]
                        results.append(step_result)
                        if not step_result["ok"]:
                            break
                else:
                    resolved_args = _resolve_value_template(args_template, params)
                    step_result = await _execute_one(tool_name, resolved_args, bot, chat_id, db_path, notify_state)
                    all_ok = all_ok and step_result["ok"]
                    results.append(step_result)
                if not all_ok:
                    break
        finally:
            _skip_action_recording.reset(_rec_token)

        status = "success" if all_ok else "failure"
        await _bl.record_manual_skill_run(
            skill["skill_id"], int(skill["version"]),
            execution_status=status,
            consistency_score=1.0 if all_ok else 0.0,
        )

        return json.dumps({
            "ok": all_ok,
            "skill": skill["name"],
            "steps_total": len(skill["steps"]),
            "steps_executed": len(results),
            "results": results,
        }, ensure_ascii=False)
    except Exception as exc:
        return json.dumps({"ok": False, "error": f"skill execution failed: {exc}"}, ensure_ascii=False)


handler = _tool_run_learned_skill

__all__ = ["TOOL_NAME", "TOOL_DEF", "handler", "_tool_run_learned_skill"]
