"""Small public agent actions used by native tool adapters."""

from __future__ import annotations

from typing import Any

from cyrene.agent import state as _state


async def complete_interaction(
    arguments: dict[str, Any],
    bot: Any,
    chat_id: int,
    database_path: str,
    notify_state: dict[str, bool] | None,
) -> str:
    return await _state._tool_quit(
        arguments,
        bot,
        chat_id,
        database_path,
        notify_state,
    )


__all__ = ["complete_interaction"]
