"""Safely update one durable entity."""

from __future__ import annotations

from typing import Any

from agent.plugin import PluginContext
from agent.plugin.native_runtime import plugin_language, plugin_localized

from ._plugin import create_tool_plugin
from ._service import current_project_id, entity_service, resolve_entity

TOOL_NAME = "entity.update"


async def update_entity(
    arguments: dict[str, Any],
    context: PluginContext,
) -> dict[str, Any]:
    if not arguments.get("id") and not arguments.get("title"):
        return {
            "ok": False,
            "error": "invalid_locator",
            "message": plugin_localized(
                context,
                "Provide a full UUID, a unique UUID prefix, or an exact title.",
                "请提供完整 UUID、唯一 UUID 前缀或精确标题。",
            ),
        }
    service = entity_service(context)
    resolution = await resolve_entity(
        service,
        entity_id=arguments.get("id"),
        title=arguments.get("title"),
        type=arguments.get("type"),
        project_id=current_project_id(context),
    )
    if resolution["matches"]:
        return {
            "ok": False,
            "error": "ambiguous",
            "matched_by": resolution["matched_by"],
            "matches": resolution["matches"],
            "message": plugin_localized(
                context,
                "Multiple items matched; nothing was changed to avoid a mistake.",
                "匹配到多条事务，为避免误改未执行。",
            ),
        }
    entity = resolution["entity"]
    if entity is None:
        return {"ok": False, "error": "not_found", "message": plugin_localized(context, "Item not found.", "未找到事务。")}

    field = str(arguments["field"])
    updated = await service.update(
        entity["id"],
        language=plugin_language(context),
        **{field: arguments.get("value")},
    )
    if updated is None:
        return {"ok": False, "error": "not_found", "message": plugin_localized(context, "Item not found.", "未找到事务。")}
    return {
        "ok": True,
        "entity": updated,
        "updated_field": field,
        "message": plugin_localized(
            context,
            "Updated {field} for item {title}.",
            "已更新事务 {title} 的 {field}。",
            title=updated["title"],
            field=field,
        ),
    }


plugin = create_tool_plugin(TOOL_NAME, update_entity)
handler = update_entity

__all__ = ["TOOL_NAME", "handler", "plugin", "update_entity"]
