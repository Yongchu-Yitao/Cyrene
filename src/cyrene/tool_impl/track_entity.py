"""Tool implementation for track_entity."""

from __future__ import annotations

from cyrene import tool_legacy as _legacy

TOOL_NAME = 'track_entity'
TOOL_DEF = next(td for td in _legacy.TOOL_DEFS if td["function"]["name"] == TOOL_NAME)


async def _tool_track_entity(args, bot, chat_id, db_path, notify_state):
    from cyrene.entities import create_entity
    from cyrene.agent.state import _current_session_id
    from cyrene.workbench_context import resolve_project_data_key_for_session

    # Scope the entity to the active Workbench project so its deadline shows on
    # that project's calendar (日程). Outside a Workbench session → "default".
    project_id = resolve_project_data_key_for_session(_current_session_id.get())
    entity = await create_entity(
        db_path,
        type=args.get("type", "task"),
        title=args["title"],
        content=args.get("content", ""),
        priority=args.get("priority", "medium"),
        due_date=args.get("due_date"),
        people=args.get("people", []),
        tags=args.get("tags", []),
        source=args.get("source", "extracted"),
        confidence=args.get("confidence", 1.0),
        source_round_id=args.get("source_round_id"),
        project_id=project_id,
    )
    return f"已记录事务：{entity['title']}（ID: {entity['id']}）"


handler = _tool_track_entity

__all__ = ["TOOL_NAME", "TOOL_DEF", "handler", "_tool_track_entity"]
