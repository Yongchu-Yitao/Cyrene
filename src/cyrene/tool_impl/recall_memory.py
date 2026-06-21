"""Tool implementation for RecallMemory.

RecallMemory is intentionally limited to recent short-term memory. Historical
conversation archives are handled by the separate RecallConversation tool.
"""

from __future__ import annotations

from typing import Any

from cyrene import tool_legacy as _legacy
from cyrene import short_term
from cyrene.tool_legacy import _json_result

TOOL_NAME = 'RecallMemory'
TOOL_DEF = next(td for td in _legacy.TOOL_DEFS if td["function"]["name"] == TOOL_NAME)


async def _tool_recall_memory(args: dict[str, Any], _bot: Any, _chat_id: int, _db_path: str, _notify_state: dict[str, bool] | None) -> str:
    """Return recent short-term memories, optionally filtered by keyword/type."""
    query = str(args.get("query", "") or "").strip()
    memory_type = str(args.get("type", "") or "").strip().lower()
    limit = max(1, min(int(args.get("limit", 10) or 10), 20))

    entries = [
        entry for entry in short_term.load_entries()
        if isinstance(entry, dict)
        and (not memory_type or str(entry.get("type") or "").strip().lower() == memory_type)
        and (
            not query
            or query.casefold() in str(entry.get("content") or "").casefold()
        )
    ]
    entries.sort(
        key=lambda entry: (
            str(entry.get("last_mentioned") or entry.get("first_seen") or ""),
            int(entry.get("mention_count") or 1),
        ),
        reverse=True,
    )
    payload: dict[str, Any] = {
        "query": query,
        "type": memory_type,
        "memories": [
            {
                "content": item.get("content", ""),
                "type": item.get("type", ""),
                "first_seen": item.get("first_seen", ""),
                "last_mentioned": item.get("last_mentioned", ""),
                "mention_count": int(item.get("mention_count") or 1),
                "emotional_valence": item.get("emotional_valence", 0),
            }
            for item in entries[:limit]
        ],
    }
    if not payload["memories"]:
        payload["note"] = "No recent memory matches found for the given filters."
    return _json_result(payload)


handler = _tool_recall_memory

__all__ = ["TOOL_NAME", "TOOL_DEF", "handler", "_tool_recall_memory"]
