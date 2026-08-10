"""Tool implementation for delete_entity."""

from __future__ import annotations

from cyrene.tooling.native_definitions import get_native_tool_def

TOOL_NAME = 'delete_entity'
TOOL_DEF = get_native_tool_def(TOOL_NAME)


async def _tool_delete_entity(args, bot, chat_id, db_path, notify_state):
    from cyrene.tool_impl.entity.store import (
        delete_entity,
        find_entities_by_id_prefix,
        find_entities_by_title,
        get_entity,
    )

    requested_id = str(args.get("id") or "").strip()
    requested_title = str(args.get("title") or "").strip()
    requested_type = str(args.get("type") or "").strip() or None

    entity = None
    if requested_id:
        # Prefer an exact ID, then accept a unique prefix for compatibility with
        # older tool results that exposed only the first eight UUID characters.
        entity = await get_entity(db_path, requested_id)
        if entity is None:
            prefix_matches = await find_entities_by_id_prefix(db_path, requested_id)
            if len(prefix_matches) == 1:
                entity = prefix_matches[0]
            elif len(prefix_matches) > 1:
                return _ambiguous_result(prefix_matches, "ID 前缀")

        # Older callers sometimes put a title in the `id` field.  Preserve that
        # behavior safely by falling back to an exact-title lookup only when the
        # ID lookup produced no result.
        if entity is None and not requested_title:
            requested_title = requested_id

    if entity is None and requested_title:
        title_matches = await find_entities_by_title(
            db_path,
            requested_title,
            type=requested_type,
        )
        if not title_matches:
            return f"未找到事务（ID/标题：{requested_id or requested_title}）"
        if len(title_matches) > 1:
            return _ambiguous_result(title_matches, "标题")
        entity = title_matches[0]

    if entity is None:
        return "删除事务失败：请提供完整 UUID、唯一 UUID 前缀或精确标题。"

    success = await delete_entity(
        db_path,
        entity["id"],
        permanent=bool(args.get("permanent", False)),
    )
    if not success:
        return f"未找到事务 {entity['id']}"
    action = "永久删除" if args.get("permanent", False) else "归档"
    return f"已{action}事务：{entity['title']}（ID: {entity['id']}）"


def _ambiguous_result(entities: list[dict], matched_by: str) -> str:
    lines = [
        f"- [{entity['type']}] {entity['title']}（ID: {entity['id']}，{entity['status']}）"
        for entity in entities
    ]
    return (
        f"{matched_by}匹配到多条事务，为避免误删未执行。"
        "请使用下面任一条的完整 ID 重试：\n"
        + "\n".join(lines)
    )


handler = _tool_delete_entity

__all__ = ["TOOL_NAME", "TOOL_DEF", "handler", "_tool_delete_entity"]
