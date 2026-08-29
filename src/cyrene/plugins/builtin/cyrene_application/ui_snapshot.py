from __future__ import annotations

from typing import Any
from cyrene.core.plugin import PluginContext

from ._ui_snapshot import read_current_tree

TOOL_NAME = "CyreneUISnapshot"
TOOL_DEF = {"type": "function", "function": {
    "name": TOOL_NAME,
    "description": "Snapshot the current Cyrene UI layer as a structured, paginated semantic component tree. This is read-only and does not activate or focus the app.",
    "parameters": {
        "type": "object",
        "properties": {
            "include": {"type": "array", "items": {"type": "string", "enum": ["interactive", "text"]}, "uniqueItems": True},
            "max_depth": {"type": "integer", "minimum": 1, "maximum": 12},
            "cursor": {"type": "string", "maxLength": 500},
            "page_size": {"type": "integer", "minimum": 1, "maximum": 200},
        },
        "additionalProperties": False,
    },
}}
TOOL_METADATA = {"read_only": True, "resource_keys": ("cyrene:current-surface",), "requires_order": True}


async def handler(args: dict[str, Any], context: PluginContext) -> str:
    request = dict(args)
    request.setdefault("include", ["interactive", "text"])
    return await read_current_tree(
        request,
        context,
        operation_id="cyrene.ui.snapshot",
        success_message="Current UI layer snapshot read.",
    )


__all__ = ["TOOL_NAME", "TOOL_DEF", "TOOL_METADATA", "handler"]
