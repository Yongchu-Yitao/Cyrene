from __future__ import annotations

import json
import os
from typing import Any

from cyrene.tool_impl.office._shared import (
    APPLY_BATCH_DEF,
    GET_CONTEXT_DEF,
    INSPECT_DEF,
    RENDER_SLIDE_DEF,
    tool_def,
)
from cyrene.tooling.runtime_api import json_result

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


def _requested_ids(args: dict[str, Any]) -> set[str]:
    result = {str(args.get("capability_id") or "")}
    result.update(str(item) for item in (args.get("capability_ids") or []))
    return {item for item in result if item}


async def handler(
    args: dict[str, Any],
    bot: Any,
    chat_id: int,
    db_path: str,
    notify_state: dict[str, bool] | None,
) -> str:
    requested = _requested_ids(args)
    escape_enabled = os.environ.get("CYRENE_PPT_ESCAPE_ENABLED") == "1"
    if requested & _ESCAPE_IDS and not escape_enabled:
        return json_result({
            "status": "error",
            "error_code": "escape_disabled",
            "message": "PowerPoint escape capabilities are disabled. Set CYRENE_PPT_ESCAPE_ENABLED=1 in developer mode and retry with confirmed=true.",
        })
    if args.get("operation") == "invoke" and requested & _ESCAPE_IDS:
        arguments = args.get("arguments") if isinstance(args.get("arguments"), dict) else {}
        if arguments.get("confirmed") is not True:
            return json_result({"status": "error", "error_code": "confirmation_required", "message": "Escape capabilities require confirmed=true."})

    if args.get("operation") == "describe" and requested & set(_CORE_CAPABILITIES):
        ordered = [str(item) for item in (args.get("capability_ids") or [])]
        if args.get("capability_id"):
            ordered.insert(0, str(args["capability_id"]))
        ordered = list(dict.fromkeys(item for item in ordered if item))
        core_details = [_CORE_CAPABILITIES[item] for item in ordered if item in _CORE_CAPABILITIES]
        remaining = [item for item in ordered if item not in _CORE_CAPABILITIES]
        if not remaining:
            return json_result({"status": "success", "module": "office_tools", "capabilities": core_details})
        forwarded = dict(args)
        forwarded.pop("capability_id", None)
        forwarded["capability_ids"] = remaining
        from cyrene.tooling.gateway import execute_wire_tool
        raw = await execute_wire_tool("office_tools", forwarded, bot, chat_id, db_path, notify_state)
        try:
            payload = json.loads(raw)
        except (TypeError, json.JSONDecodeError):
            return raw
        if payload.get("status") == "success" and isinstance(payload.get("capabilities"), list):
            details_by_id = {str(item.get("id") or ""): item for item in [*core_details, *payload["capabilities"]]}
            payload["capabilities"] = [details_by_id[item] for item in ordered if item in details_by_id]
        return json.dumps(payload, ensure_ascii=False)

    from cyrene.tooling.gateway import execute_wire_tool

    raw = await execute_wire_tool("office_tools", args, bot, chat_id, db_path, notify_state)
    if escape_enabled or args.get("operation") != "discover":
        return raw
    try:
        payload = json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        return raw
    capabilities = payload.get("capabilities")
    if isinstance(capabilities, list):
        payload["capabilities"] = [item for item in capabilities if str(item.get("id") or "") not in _ESCAPE_IDS]
    return json.dumps(payload, ensure_ascii=False)


__all__ = ["TOOL_DEF", "TOOL_METADATA", "handler"]
