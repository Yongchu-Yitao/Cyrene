"""Tool implementation for retire_short_term_memory."""

from __future__ import annotations

from typing import Any

from cyrene import short_term
from cyrene import tool_legacy as _legacy
from cyrene.tool_legacy import _json_result

TOOL_NAME = "retire_short_term_memory"
TOOL_DEF = next(td for td in _legacy.TOOL_DEFS if td["function"]["name"] == TOOL_NAME)


async def _tool_retire_short_term_memory(
    args: dict[str, Any],
    _bot: Any,
    _chat_id: int,
    _db_path: str,
    _notify_state: dict[str, bool] | None,
) -> str:
    """Retire one short-term memory entry by exact id."""
    memory_id = str(args.get("memory_id", "") or "").strip()
    if not memory_id:
        return _json_result({
            "status": "error",
            "type": "invalid_arguments",
            "message": "memory_id is required",
        })

    retired, changed = short_term.retire_entry(
        memory_id,
        reason=str(args.get("reason", "") or "").strip(),
    )
    if retired is None:
        return _json_result({
            "status": "error",
            "type": "not_found",
            "message": f"Short-term memory {memory_id} was not found.",
        })

    return _json_result({
        "status": "success",
        "memory_id": memory_id,
        "changed": changed,
        "stale": True,
        "content": str(retired.get("content") or ""),
        "message": (
            "Short-term memory retired. It will no longer be injected into agent "
            "runs or returned by RecallMemory."
            if changed
            else "Short-term memory was already retired."
        ),
    })


handler = _tool_retire_short_term_memory

__all__ = [
    "TOOL_NAME",
    "TOOL_DEF",
    "handler",
    "_tool_retire_short_term_memory",
]
