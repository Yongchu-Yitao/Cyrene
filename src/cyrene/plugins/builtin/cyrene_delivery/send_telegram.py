"""Tool implementation for send_telegram."""

from __future__ import annotations

from typing import Any

from cyrene.core.plugin import PluginContext
from cyrene.plugins.native_runtime import plugin_localized

from .definitions import get_native_tool_def

TOOL_NAME = 'send_telegram'
TOOL_DEF = get_native_tool_def(TOOL_NAME)


async def _tool_send_message(args: dict[str, Any], context: PluginContext) -> str:
    from cyrene.core.plugin import application_plugin_service

    channels = application_plugin_service("channels")
    if channels is None:
        return plugin_localized(context, "Error: the messaging channels Plugin is unavailable.", "错误：消息渠道插件不可用。")
    bot = context.data.get("bot")
    if not channels.owns_channel_bot("telegram", bot):
        return plugin_localized(context, "Error: the active channel does not support Telegram delivery.", "错误：当前渠道不支持 Telegram 投递。")
    chat_id = context.data.get("chat_id")
    notify_state = context.data.get("notify_state")
    text = str(args.get("text", ""))
    if bot is not None:
        await bot.send_message(chat_id=chat_id, text=text)
    if notify_state is not None:
        notify_state["sent"] = True
    return plugin_localized(context, "Message sent.", "消息已发送。")


handler = _tool_send_message

__all__ = ["TOOL_NAME", "TOOL_DEF", "handler", "_tool_send_message"]
