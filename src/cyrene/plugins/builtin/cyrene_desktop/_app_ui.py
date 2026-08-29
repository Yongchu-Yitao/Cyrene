from __future__ import annotations

from typing import Any

from cyrene.core.plugin import PluginContext


def action_tool_def(
    name: str, family: str, description: str, extra: dict[str, Any] | None = None,
    required_extra: tuple[str, ...] = (),
) -> dict[str, Any]:
    properties: dict[str, Any] = {
        "session_id": {"type": "string", "minLength": 1},
        "snapshot_id": {"type": "string", "minLength": 1},
        "revision": {"type": "integer", "minimum": 0},
        "node_id": {"type": "string", "minLength": 1},
        "action_id": {"type": "string", "minLength": 1},
        "reason": {"type": "string", "minLength": 1, "maxLength": 500},
        "idempotency_key": {"type": "string", "minLength": 8, "maxLength": 200},
    }
    properties.update(extra or {})
    return {"type": "function", "function": {
        "name": name,
        "description": description + " Uses only leased accessibility-tree node/action IDs; coordinates, selectors, scripts, and focus changes are forbidden.",
        "parameters": {
            "type": "object", "properties": properties,
            "required": ["session_id", "snapshot_id", "revision", "node_id", "action_id", "reason", "idempotency_key", *required_extra],
            "additionalProperties": False,
        },
    }}


async def run_action(
    family: str,
    args: dict[str, Any],
    context: PluginContext,
) -> str:
    from ._app_semantic_backend import execute_action, format_result
    return format_result(await execute_action(family, dict(args or {})), context)


ACTION_METADATA = {"read_only": False, "resource_keys": ("desktop:app-semantic",), "requires_order": True}
