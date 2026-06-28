"""Tool implementation for RecallMemory.

RecallMemory is intentionally limited to recent short-term memory. Historical
conversation archives are handled by the separate RecallConversation tool.
"""

from __future__ import annotations

import re
from typing import Any

from cyrene import tool_legacy as _legacy
from cyrene import short_term
from cyrene.tool_legacy import _json_result

TOOL_NAME = 'RecallMemory'
TOOL_DEF = next(td for td in _legacy.TOOL_DEFS if td["function"]["name"] == TOOL_NAME)
_MAX_CONTENT_CHARS = 800
_MAX_RESULT_CONTENT_CHARS = 6000


def _bounded_content(value: Any) -> tuple[str, bool]:
    text = str(value or "")
    if len(text) <= _MAX_CONTENT_CHARS:
        return text, False
    return text[:_MAX_CONTENT_CHARS] + "…", True


def _split_memory_query(query: str) -> tuple[str, list[str]]:
    needle = str(query or "").strip().casefold()
    if not needle:
        return "", []
    return needle, [term for term in re.split(r"\s+", needle) if term]


async def _tool_recall_memory(args: dict[str, Any], _bot: Any, _chat_id: int, _db_path: str, _notify_state: dict[str, bool] | None) -> str:
    """Return recent short-term memories, optionally filtered by keyword/type."""
    query = str(args.get("query", "") or "").strip()
    needle, terms = _split_memory_query(query)
    memory_type = str(args.get("type", "") or "").strip().lower()
    limit = max(1, min(int(args.get("limit", 10) or 10), 20))

    entries = [
        entry for entry in short_term.load_entries()
        if isinstance(entry, dict)
        and not entry.get("stale")
        and (not memory_type or str(entry.get("type") or "").strip().lower() == memory_type)
        and (
            not needle
            or needle in str(entry.get("content") or "").casefold()
            or any(term in str(entry.get("content") or "").casefold() for term in terms)
        )
    ]
    entries.sort(
        key=lambda entry: (
            str(entry.get("last_mentioned") or entry.get("first_seen") or ""),
            int(entry.get("mention_count") or 1),
        ),
        reverse=True,
    )
    memories: list[dict[str, Any]] = []
    content_chars = 0
    content_truncated = False
    candidates = entries[:limit]
    for item in candidates:
        content, was_truncated = _bounded_content(item.get("content", ""))
        if memories and content_chars + len(content) > _MAX_RESULT_CONTENT_CHARS:
            content_truncated = True
            break
        if not memories and len(content) > _MAX_RESULT_CONTENT_CHARS:
            content = content[:_MAX_RESULT_CONTENT_CHARS] + "…"
            was_truncated = True
        memory = {
            "content": content,
            "type": item.get("type", ""),
            "first_seen": item.get("first_seen", ""),
            "last_mentioned": item.get("last_mentioned", ""),
            "mention_count": int(item.get("mention_count") or 1),
            "emotional_valence": item.get("emotional_valence", 0),
        }
        if was_truncated:
            memory["content_truncated"] = True
            content_truncated = True
        memories.append(memory)
        content_chars += len(content)

    payload: dict[str, Any] = {
        "query": query,
        "type": memory_type,
        "available_matches": len(entries),
        "memories": memories,
        "truncated": content_truncated or len(memories) < len(candidates),
    }
    if not payload["memories"]:
        payload["note"] = "No recent memory matches found for the given filters."
    return _json_result(payload)


handler = _tool_recall_memory

__all__ = ["TOOL_NAME", "TOOL_DEF", "handler", "_tool_recall_memory"]
