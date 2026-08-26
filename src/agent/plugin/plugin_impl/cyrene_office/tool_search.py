from __future__ import annotations

from copy import deepcopy
import json
import os
import re
from typing import Any

from agent.plugin.execution import PluginInvocationError, invoke_plugin
from cyrene.tooling.runtime_api import json_result

from . import kit as _kit
from ._shared import (
    APPLY_BATCH_DEF,
    GET_CONTEXT_DEF,
    INSPECT_DEF,
    RENDER_SLIDE_DEF,
    tool_def,
)

TOOL_DEF = tool_def(
    "PowerPointToolSearch",
    "Progressively find, describe, and invoke L1-L6 PowerPoint capabilities. Search before complex work; the five core tools remain sufficient for common edits.",
    {
        "operation": {"type": "string", "enum": ["discover", "describe", "invoke"]},
        "query": {"type": "string"},
        "capability_id": {"type": "string"},
        "capability_ids": {"type": "array", "items": {"type": "string"}, "maxItems": 20},
        "arguments": {"type": "object"},
        "limit": {"type": "integer", "minimum": 1, "maximum": 50},
    },
    ["operation"],
)
TOOL_METADATA = {"read_only": False, "resource_keys": ("office:tool-search",), "requires_order": True}

_ESCAPE_IDS = {"ppt.execute_officejs", "ppt.replace_slide_ooxml"}


def _core_detail(capability_id: str, definition: dict[str, Any]) -> dict[str, Any]:
    function = definition["function"]
    return {
        "id": capability_id,
        "description": function["description"],
        "input_schema": function["parameters"],
        "source": "core",
        "direct_tool": function["name"],
    }


_CORE_CAPABILITIES = {
    "ppt.get_context": _core_detail("ppt.get_context", GET_CONTEXT_DEF),
    "ppt.inspect": _core_detail("ppt.inspect", INSPECT_DEF),
    "ppt.apply_batch": _core_detail("ppt.apply_batch", APPLY_BATCH_DEF),
    "ppt.render": _core_detail("ppt.render", RENDER_SLIDE_DEF),
    "ppt.tool_search": _core_detail("ppt.tool_search", TOOL_DEF),
}
_CORE_TARGETS = {
    "ppt.get_context": "PowerPointGetContext",
    "ppt.inspect": "PowerPointInspect",
    "ppt.apply_batch": "PowerPointApplyBatch",
    "ppt.render": "PowerPointRenderSlide",
}


def _deferred_bindings() -> dict[str, str]:
    rows = (
        *_kit.READ_DEFS,
        *_kit.EDIT_OPS,
        *_kit.COMPOSE,
        *_kit.REVIEW,
        *_kit.ADVANCED,
        *_kit.ESCAPE,
    )
    bindings: dict[str, str] = {}
    for row in rows:
        concrete_name, capability_id = str(row[0]), str(row[1])
        previous = bindings.setdefault(capability_id, concrete_name)
        if previous != concrete_name:
            raise RuntimeError(f"duplicate PowerPoint capability ID: {capability_id}")
    return bindings


def _build_deferred_index() -> tuple[dict[str, dict[str, Any]], dict[str, str]]:
    definitions: list[dict[str, Any]] = []
    handlers: dict[str, Any] = {}
    metadata: dict[str, dict[str, Any]] = {}
    _kit.register_all(definitions, handlers, metadata)

    targets = _deferred_bindings()
    capability_by_name = {name: capability_id for capability_id, name in targets.items()}
    if len(capability_by_name) != len(targets):
        raise RuntimeError("PowerPoint capability bindings contain duplicate concrete tools")

    details: dict[str, dict[str, Any]] = {}
    declared_names: set[str] = set()
    for definition in definitions:
        function = definition.get("function") or {}
        concrete_name = str(function.get("name") or "").strip()
        capability_id = capability_by_name.get(concrete_name)
        if not capability_id:
            raise RuntimeError(
                f"PowerPoint deferred tool has no capability binding: {concrete_name}"
            )
        if capability_id in details:
            raise RuntimeError(f"duplicate PowerPoint capability definition: {capability_id}")
        declared_names.add(concrete_name)
        details[capability_id] = {
            "id": capability_id,
            "description": str(function.get("description") or ""),
            "input_schema": deepcopy(
                function.get("parameters") or {"type": "object", "properties": {}}
            ),
            "source": "native",
        }

    missing = sorted(set(capability_by_name) - declared_names)
    extras = sorted(declared_names - set(capability_by_name))
    if missing or extras or set(handlers) != declared_names or set(metadata) != declared_names:
        raise RuntimeError(
            "PowerPoint local capability declarations are inconsistent: "
            f"missing={missing}, extras={extras}"
        )
    return details, targets


_DEFERRED_CAPABILITIES, _DEFERRED_TARGETS = _build_deferred_index()


def _requested_ids(args: dict[str, Any]) -> list[str]:
    ordered: list[str] = []
    if args.get("capability_id"):
        ordered.append(str(args["capability_id"]).strip())
    ordered.extend(str(item).strip() for item in (args.get("capability_ids") or []))
    return list(dict.fromkeys(item for item in ordered if item))


def _error(error_code: str, message: str) -> str:
    return json_result({
        "status": "error",
        "module": "office_tools",
        "error_code": error_code,
        "message": message,
    })


def _discover(args: dict[str, Any], *, escape_enabled: bool) -> str:
    limit = max(1, min(int(args.get("limit") or 20), 50))
    terms = list(dict.fromkeys(
        term.casefold()
        for term in re.findall(r"[\w.-]+", str(args.get("query") or ""), flags=re.UNICODE)
        if term
    ))
    capabilities = [
        (capability_id, detail)
        for capability_id, detail in _DEFERRED_CAPABILITIES.items()
        if escape_enabled or capability_id not in _ESCAPE_IDS
    ]
    ranked: list[tuple[int, int, str, dict[str, Any]]] = []
    for index, (capability_id, detail) in enumerate(capabilities):
        identity = f"{capability_id} {_DEFERRED_TARGETS[capability_id]}".casefold()
        haystack = f"{identity} {detail['description']}".casefold()
        matched = sum(1 for term in terms if term in haystack)
        identity_matches = sum(1 for term in terms if term in identity)
        ranked.append((matched + identity_matches * 2, index, capability_id, detail))
    if terms and any(score > 0 for score, _index, _id, _detail in ranked):
        ranked.sort(key=lambda item: (-item[0], item[1]))
    summaries = [
        {"id": capability_id, "description": detail["description"]}
        for _score, _index, capability_id, detail in ranked[:limit]
    ]
    return json_result({
        "status": "success",
        "module": "office_tools",
        "capabilities": summaries,
        "important": (
            "Capability IDs are identifiers, not function names. Use "
            "PowerPointToolSearch for describe and invoke."
        ),
        "next": (
            "Call PowerPointToolSearch with operation=describe and the selected "
            "capability_ids, then invoke one capability_id with matching arguments."
        ),
        "example_describe": {
            "tool": "PowerPointToolSearch",
            "arguments": {
                "operation": "describe",
                "capability_ids": (
                    [str(summaries[0]["id"])] if summaries else ["<capability_id>"]
                ),
            },
        },
    })


def _describe(capability_ids: list[str]) -> str:
    if not capability_ids:
        return _error(
            "invalid_arguments",
            "`capability_id` or `capability_ids` is required for operation=describe.",
        )
    available = {**_CORE_CAPABILITIES, **_DEFERRED_CAPABILITIES}
    missing = [capability_id for capability_id in capability_ids if capability_id not in available]
    if missing:
        return _error(
            "unknown_capability",
            f"Unavailable capability ID(s): {', '.join(missing)}.",
        )
    return json_result({
        "status": "success",
        "module": "office_tools",
        "capabilities": [deepcopy(available[item]) for item in capability_ids[:20]],
    })


async def _invoke(args: dict[str, Any]) -> str:
    capability_id = str(args.get("capability_id") or "").strip()
    if not capability_id:
        return _error(
            "invalid_arguments",
            "`capability_id` is required for operation=invoke.",
        )
    target = _DEFERRED_TARGETS.get(capability_id) or _CORE_TARGETS.get(capability_id)
    if target is None:
        return _error(
            "unknown_capability",
            f"Unavailable capability ID: {capability_id}.",
        )
    arguments = args.get("arguments")
    if arguments is None:
        arguments = {}
    if not isinstance(arguments, dict):
        return _error("invalid_arguments", "`arguments` must be an object.")
    try:
        raw = await invoke_plugin(target, arguments, review=True)
    except PluginInvocationError as exc:
        return _error(
            "nested_invoke_failed",
            exc.result.error or f"Plugin invocation failed: {target}",
        )
    result: Any = raw
    if isinstance(raw, str):
        try:
            result = json.loads(raw)
        except (TypeError, ValueError, json.JSONDecodeError):
            result = raw
    status = "success"
    if isinstance(result, dict) and str(result.get("status") or ""):
        status = str(result["status"])
    return json_result({
        "status": status,
        "capability_id": capability_id,
        "result": result,
    })


async def handler(
    args: dict[str, Any],
    _bot: Any,
    _chat_id: int,
    _db_path: str,
    _notify_state: dict[str, bool] | None,
) -> str:
    operation = str(args.get("operation") or "").strip()
    requested = _requested_ids(args)
    escape_enabled = os.environ.get("CYRENE_PPT_ESCAPE_ENABLED") == "1"
    if set(requested) & _ESCAPE_IDS and not escape_enabled:
        return _error(
            "escape_disabled",
            "PowerPoint escape capabilities are disabled. Set "
            "CYRENE_PPT_ESCAPE_ENABLED=1 in developer mode and retry with confirmed=true.",
        )
    if operation == "invoke" and set(requested) & _ESCAPE_IDS:
        arguments = args.get("arguments") if isinstance(args.get("arguments"), dict) else {}
        if arguments.get("confirmed") is not True:
            return _error(
                "confirmation_required",
                "Escape capabilities require confirmed=true.",
            )

    if operation == "discover":
        return _discover(args, escape_enabled=escape_enabled)
    if operation == "describe":
        return _describe(requested)
    if operation == "invoke":
        return await _invoke(args)
    return _error("invalid_arguments", "`operation` must be discover, describe, or invoke.")


__all__ = ["TOOL_DEF", "TOOL_METADATA", "handler"]
