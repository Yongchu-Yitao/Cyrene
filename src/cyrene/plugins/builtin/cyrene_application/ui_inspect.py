from __future__ import annotations

from typing import Any
from cyrene.core.plugin import PluginContext

from ._ui_snapshot import read_current_tree

TOOL_NAME = "CyreneUIInspect"
TOOL_DEF = {"type": "function", "function": {
    "name": TOOL_NAME,
    "description": "Inspect one component selected from a Cyrene UI snapshot. Unrelated UI changes do not invalidate its node lease; the target's own semantic change does. Returns state, declared gestures, expected outcomes, and a paginated child subtree without performing the action.",
    "parameters": {
        "type": "object",
        "properties": {
            "snapshot_id": {"type": "string", "maxLength": 160},
            "revision": {"type": "integer", "minimum": 1},
            "node_id": {"type": "string", "maxLength": 160},
            "include": {"type": "array", "items": {"type": "string", "enum": ["interactive", "text"]}, "uniqueItems": True},
            "max_depth": {"type": "integer", "minimum": 1, "maximum": 12},
            "cursor": {"type": "string", "maxLength": 500},
            "page_size": {"type": "integer", "minimum": 1, "maximum": 200},
        },
        "required": ["snapshot_id", "revision", "node_id"],
        "additionalProperties": False,
    },
}}
TOOL_METADATA = {"read_only": True, "resource_keys": ("cyrene:current-surface",), "requires_order": True}


async def handler(args: dict[str, Any], context: PluginContext) -> str:
    request = dict(args)
    request["parent_node_id"] = request.pop("node_id")
    # Inspect is bound to the selected component, not to unrelated global UI
    # churn. The renderer still rejects the read if this node disappeared,
    # changed layer, or changed its own exposed semantics.
    request["allow_compatible_node"] = True
    request["_agent_cursor_mode"] = "inspect"
    request.setdefault("include", ["interactive", "text"])
    return await read_current_tree(
        request,
        context,
        operation_id="cyrene.ui.inspect",
        success_message="Current UI component inspected.",
    )


__all__ = ["TOOL_NAME", "TOOL_DEF", "TOOL_METADATA", "handler"]
