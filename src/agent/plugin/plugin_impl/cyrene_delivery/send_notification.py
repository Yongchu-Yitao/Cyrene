"""Tool implementation for send_notification."""

from __future__ import annotations

from typing import Any

from agent.plugin import PluginContext
from agent.plugin.native_runtime import plugin_localized, run_context_value

from .definitions import get_native_tool_def

TOOL_NAME = 'send_notification'
TOOL_DEF = get_native_tool_def(TOOL_NAME)


async def _tool_send_notification(args: dict[str, Any], context: PluginContext) -> str:
    from cyrene.runtime.notifications import notify

    title = str(args.get("title") or "Cyrene").strip()
    text = str(args.get("text") or "").strip()
    channel = str(args.get("channel") or "auto").strip()
    if not text:
        return plugin_localized(context, "No notification text was provided.", "未提供通知内容。")

    source = str(run_context_value(context, "conversation_source") or "")

    # When the conversation started from WebUI (default), skip Telegram and WeChat
    # so that WebUI interactions don't leak to external messaging channels.
    # The settings toggle (notify_telegram / notify_wechat) still controls
    # scheduled/background notifications through the scheduler.
    if source == "webui":
        if channel in ("telegram", "wechat"):
            return plugin_localized(
                context,
                "{channel} notifications are not available from the Web UI.",
                "Web UI 不支持发送 {channel} 通知。",
                channel=channel.capitalize(),
            )
        if channel == "auto":
            # Only try sse — desktop/webhook are local and OK too, but "auto"
            # from WebUI should not attempt Telegram/WeChat
            result = await notify(title, text, channel="sse")
        else:
            result = await notify(title, text, channel=channel)
    else:
        result = await notify(title, text, channel=channel)

    if result.get("ok"):
        channels = list(result.get("channels", {}).keys())
        return plugin_localized(
            context,
            "Notification sent via: {channels}",
            "通知已通过以下渠道发送：{channels}",
            channels=", ".join(channels),
        )
    errors = [f"{k}: {v.get('error', '?')}" for k, v in result.get("channels", {}).items() if not v.get("ok")]
    return plugin_localized(
        context,
        "Notification failed: {errors}",
        "通知发送失败：{errors}",
        errors="; ".join(errors),
    )


handler = _tool_send_notification

__all__ = ["TOOL_NAME", "TOOL_DEF", "handler", "_tool_send_notification"]
