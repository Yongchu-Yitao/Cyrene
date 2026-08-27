"""Tool implementation for RunLearnedSkill."""

from __future__ import annotations

import json
import re as _re
from typing import Any

from agent.plugin import PluginContext
from agent.plugin.execution import invoke_plugin
from agent.plugin.native_runtime import run_context_value
from .definitions import get_native_tool_def
from cyrene.learning.replay import (
    AUTO_REPLAY_BLOCKED_TOOLS,
    HIGH_RISK_TOOLS,
    REPLAY_IGNORED_TOOLS,
)

TOOL_NAME = "RunLearnedSkill"
TOOL_DEF = get_native_tool_def(TOOL_NAME)

def _normalize_learned_step(
    step: dict[str, Any],
) -> tuple[str, dict[str, Any]]:
    reference = step.get("implementation_reference") or {}
    return (
        str(
            step.get("capability_id")
            or step.get("tool")
            or reference.get("tool_name")
            or ""
        ).strip(),
        dict(
            step.get("arguments")
            or step.get("args")
            or reference.get("args_template")
            or {}
        ),
    )


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
        if tool_name in REPLAY_IGNORED_TOOLS:
            continue
        if tool_name in HIGH_RISK_TOOLS or tool_name in AUTO_REPLAY_BLOCKED_TOOLS:
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
) -> dict[str, Any]:
    if not str(tool_name or "").strip():
        return {
            "tool": "",
            "ok": False,
            "output": "Learned step is missing its Plugin name.",
            "truncated": False,
        }
    if not isinstance(resolved_args, dict):
        raw = f"Tool {tool_name} failed: resolved arguments must be a dict, got {type(resolved_args).__name__}"
        return {"tool": tool_name, "ok": False, "output": raw, "truncated": False}
    try:
        raw = await invoke_plugin(tool_name, resolved_args, review=True)
    except Exception as exc:
        raw = f"Plugin {tool_name} failed: {exc}"
        ok = False
    else:
        ok = True
    raw_str = str(raw)
    if ok:
        try:
            decoded = json.loads(raw_str)
        except (TypeError, ValueError):
            decoded = None
        if isinstance(decoded, dict) and (
            decoded.get("ok") is False
            or bool(decoded.get("error"))
            or str(decoded.get("status") or "").lower() in {"error", "failed", "failure"}
        ):
            ok = False
    is_truncated = len(raw_str) > 4000
    return {
        "tool": tool_name,
        "ok": ok,
        "output": raw_str[:4000],
        "truncated": is_truncated,
    }


async def _tool_run_learned_skill(
    args: dict[str, Any],
    context: PluginContext,
) -> str:
    name = str(args.get("name") or "").strip()
    if not name:
        return json.dumps({"ok": False, "error": "name is required"}, ensure_ascii=False)

    params = dict(args.get("params") or {})

    try:
        import cyrene.learning.orchestrator as learning

        explicit_skill_id = str(context.data.get("learning_skill_id") or "").strip()
        if explicit_skill_id:
            skill = await learning.get_learned_skill(explicit_skill_id)
            if skill is not None and str(skill.get("name") or "") != name:
                skill = None
        else:
            skill = await learning.get_learned_skill_by_name(
                name,
                session_id=str(run_context_value(context, "session_id") or ""),
            )
        if skill is None:
            return json.dumps({"ok": False, "error": f"no active learned skill named '{name}'"}, ensure_ascii=False)

        schema = skill.get("input_schema") or []
        params = _params_with_defaults(schema, params)
        param_errors = _validate_params(schema, params)
        if param_errors:
            await learning.record_manual_skill_run(
                skill["skill_id"], int(skill["version"]),
                execution_status="fallback",
            )
            return json.dumps({"ok": False, "error": "; ".join(param_errors)}, ensure_ascii=False)

        if _has_unsafe_step(skill["steps"]):
            await learning.record_manual_skill_run(
                skill["skill_id"], int(skill["version"]),
                execution_status="fallback",
            )
            return json.dumps(
                {"ok": False, "error": "skill contains high-risk steps that cannot be auto-executed"},
                ensure_ascii=False,
            )

        results: list[dict[str, Any]] = []
        all_ok = True
        for step in skill["steps"]:
            if not step.get("enabled", True):
                continue
            tool_name, args_template = _normalize_learned_step(step)
            if tool_name in REPLAY_IGNORED_TOOLS:
                continue
            items = args_template.get("_items")
            if isinstance(items, list) and items:
                for item_args in items:
                    resolved = _resolve_value_template(item_args, params)
                    step_result = await _execute_one(tool_name, resolved)
                    all_ok = all_ok and step_result["ok"]
                    results.append(step_result)
                    if not step_result["ok"]:
                        break
            else:
                resolved_args = _resolve_value_template(args_template, params)
                step_result = await _execute_one(tool_name, resolved_args)
                all_ok = all_ok and step_result["ok"]
                results.append(step_result)
            if not all_ok:
                break

        status = "success" if all_ok else "failure"
        await learning.record_manual_skill_run(
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
