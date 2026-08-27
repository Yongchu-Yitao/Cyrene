"""Tool implementation for send_telegram."""

from __future__ import annotations

from typing import Any

from agent.plugin import PluginContext

from .definitions import get_native_tool_def

TOOL_NAME = 'send_telegram'
TOOL_DEF = get_native_tool_def(TOOL_NAME)


async def _tool_send_message(args: dict[str, Any], context: PluginContext) -> str:
    bot = context.data.get("bot")
    chat_id = context.data.get("chat_id")
    notify_state = context.data.get("notify_state")
    text = str(args.get("text", ""))
    if bot is not None:
        await bot.send_message(chat_id=chat_id, text=text)
    if notify_state is not None:
        notify_state["sent"] = True
    return "Message sent."


handler = _tool_send_message

__all__ = ["TOOL_NAME", "TOOL_DEF", "handler", "_tool_send_message"]
