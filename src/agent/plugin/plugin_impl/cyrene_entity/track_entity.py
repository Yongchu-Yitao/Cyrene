"""Create durable entities through the host-owned entity service."""

from __future__ import annotations

from typing import Any

from agent.plugin import PluginContext
from agent.plugin.native_runtime import plugin_language, plugin_localized

from ._plugin import create_tool_plugin
from ._service import current_project_id, entity_service

TOOL_NAME = "entity.track"


async def track_entity(
    arguments: dict[str, Any],
    context: PluginContext,
) -> dict[str, Any]:
    entity = await entity_service(context).create(
        language=plugin_language(context),
        type=arguments["type"],
        title=arguments["title"],
        content=arguments.get("content", ""),
        priority=arguments.get("priority", "medium"),
        due_date=arguments.get("due_date"),
        people=arguments.get("people", []),
        tags=arguments.get("tags", []),
        source=arguments.get("source", "extracted"),
        confidence=arguments.get("confidence", 1.0),
        source_round_id=(
            arguments.get("source_round_id")
            or context.data.get("run_id")
            or None
        ),
        project_id=current_project_id(context),
    )
    return {
        "ok": True,
        "entity": entity,
        "message": plugin_localized(
            context,
            "Recorded item: {title} (ID: {entity_id})",
            "已记录事务：{title}（ID: {entity_id}）",
            title=entity["title"],
            entity_id=entity["id"],
        ),
    }


plugin = create_tool_plugin(TOOL_NAME, track_entity)
handler = track_entity

__all__ = ["TOOL_NAME", "handler", "plugin", "track_entity"]
