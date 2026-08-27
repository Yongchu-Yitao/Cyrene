"""Safely archive or permanently delete one durable entity."""

from __future__ import annotations

from typing import Any

from agent.plugin import PluginContext

from ._plugin import create_tool_plugin
from ._service import current_project_id, entity_service, resolve_entity

TOOL_NAME = "entity.delete"


async def delete_entity(
    arguments: dict[str, Any],
    context: PluginContext,
) -> dict[str, Any]:
    if not arguments.get("id") and not arguments.get("title"):
        return {
            "ok": False,
            "error": "invalid_locator",
            "message": "请提供完整 UUID、唯一 UUID 前缀或精确标题。",
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
            "message": "匹配到多条事务，为避免误删未执行。",
        }
    entity = resolution["entity"]
    if entity is None:
        return {"ok": False, "error": "not_found", "message": "未找到事务。"}

    permanent = bool(arguments.get("permanent", False))
    if not await service.delete(entity["id"], permanent=permanent):
        return {"ok": False, "error": "not_found", "message": "未找到事务。"}
    action = "permanently_deleted" if permanent else "archived"
    return {
        "ok": True,
        "action": action,
        "entity": entity,
        "message": (
            f"已{'永久删除' if permanent else '归档'}事务："
            f"{entity['title']}（ID: {entity['id']}）"
        ),
    }


plugin = create_tool_plugin(TOOL_NAME, delete_entity)
handler = delete_entity

__all__ = ["TOOL_NAME", "delete_entity", "handler", "plugin"]
