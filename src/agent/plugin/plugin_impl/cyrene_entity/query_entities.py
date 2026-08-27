"""Search durable entities in the current project scope."""

from __future__ import annotations

from typing import Any

from agent.plugin import PluginContext
from agent.plugin.native_runtime import plugin_localized_plural

from ._plugin import create_tool_plugin
from ._service import current_project_id, entity_service

TOOL_NAME = "entity.query"


async def query_entities(
    arguments: dict[str, Any],
    context: PluginContext,
) -> dict[str, Any]:
    entities = await entity_service(context).query(
        arguments.get("q", ""),
        type=arguments.get("type"),
        status=arguments.get("status"),
        due_before=arguments.get("due_before"),
        project_id=current_project_id(context),
        limit=arguments.get("limit", 50),
    )
    return {
        "ok": True,
        "count": len(entities),
        "entities": entities,
        "message": plugin_localized_plural(
            context,
            "Found {count} item.",
            "Found {count} items.",
            "找到 {count} 条事务。",
            count=len(entities),
        ),
    }


plugin = create_tool_plugin(TOOL_NAME, query_entities, allow_parallel=True)
handler = query_entities

__all__ = ["TOOL_NAME", "handler", "plugin", "query_entities"]
