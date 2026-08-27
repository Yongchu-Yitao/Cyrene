"""List durable entities in the current project scope."""

from __future__ import annotations

from typing import Any

from agent.plugin import PluginContext

from ._plugin import create_tool_plugin
from ._service import current_project_id, entity_service

TOOL_NAME = "entity.list"


async def list_entities(
    arguments: dict[str, Any],
    context: PluginContext,
) -> dict[str, Any]:
    entities = await entity_service(context).list(
        type=arguments.get("type"),
        status=arguments.get("status"),
        project_id=current_project_id(context),
        limit=arguments.get("limit", 50),
    )
    return {
        "ok": True,
        "count": len(entities),
        "entities": entities,
        "message": f"找到 {len(entities)} 条事务。",
    }


plugin = create_tool_plugin(TOOL_NAME, list_entities, allow_parallel=True)
handler = list_entities

__all__ = ["TOOL_NAME", "handler", "list_entities", "plugin"]
