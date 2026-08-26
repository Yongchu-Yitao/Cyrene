"""Tool implementation for query_entities."""

from __future__ import annotations

from .definitions import get_native_tool_def

TOOL_NAME = 'query_entities'
TOOL_DEF = get_native_tool_def(TOOL_NAME)


async def _tool_query_entities(args, bot, chat_id, db_path, notify_state):
    from .store import query_entities
    entities = await query_entities(
        db_path,
        q=args.get("q", ""),
        type=args.get("type"),
        due_before=args.get("due_before"),
    )
    if not entities:
        return "没有找到匹配的事务。"
    lines = [
        f"- [{e['type']}] {e['title']}（ID: {e['id']}）"
        + (f"：{e['content']}" if e.get('content') else "")
        for e in entities
    ]
    return f"找到 {len(entities)} 条事务：\n" + "\n".join(lines)


handler = _tool_query_entities

__all__ = ["TOOL_NAME", "TOOL_DEF", "handler", "_tool_query_entities"]
