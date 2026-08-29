"""Tool implementation for send_wechat_file."""

from __future__ import annotations

import logging
from typing import Any

from cyrene.core.plugin import PluginContext
from .definitions import get_native_tool_def
from cyrene.plugins.native_runtime import plugin_localized, publish_runtime_event, resolve_exportable_path, run_context_value

logger = logging.getLogger(__name__)

TOOL_NAME = 'send_wechat_file'
TOOL_DEF = get_native_tool_def(TOOL_NAME)


async def _tool_send_wechat_file(args: dict[str, Any], context: PluginContext) -> str:
    """Send a file to the user via WeChat CDN.

    Requires ``bot`` to be a ``WeChatClient`` (i.e. the agent is running
    on the WeChat channel).
    """
    from cyrene.core.plugin import application_plugin_service

    channels = application_plugin_service("channels")
    if channels is None:
        return plugin_localized(context, "Error: the messaging channels Plugin is unavailable.", "错误：消息渠道插件不可用。")

    path_arg = str(args.get("path", "") or "").strip()
    if not path_arg:
        return plugin_localized(context, "Error: 'path' is required.", "错误：必须提供 path。")

    if str(run_context_value(context, "agent_id", "main")) != "main":
        return plugin_localized(context, "Only the main Agent can send files via WeChat.", "只有主 Agent 可以通过微信发送文件。")

    bot = context.data.get("bot")
    if not channels.owns_channel_bot("wechat", bot):
        return plugin_localized(context, "Error: the active channel does not support WeChat delivery.", "错误：当前渠道不支持微信投递。")
    chat_id = context.data.get("chat_id")
    notify_state = context.data.get("notify_state")

    path = resolve_exportable_path(path_arg)
    if not path.exists() or not path.is_file():
        return plugin_localized(context, "Error: file not found: {path}", "错误：未找到文件：{path}", path=path)

    name = str(args.get("name", "") or "").strip() or path.name
    text = str(args.get("text", "") or "").strip()
    dedupe_key = f"{path.resolve()}|{name}|{text}"

    if notify_state is not None:
        sent_wechat_files = notify_state.setdefault("sent_wechat_files", set())
        if dedupe_key in sent_wechat_files:
            return plugin_localized(context, "Skipped duplicate WeChat file send: {name}", "已跳过重复的微信文件发送：{name}", name=name)

    # Send via WeChat if the bot supports it
    send_file_fn = getattr(bot, "send_file", None)
    if send_file_fn is not None:
        try:
            ok = await send_file_fn(chat_id=str(chat_id), filepath=str(path), filename=name)
            if not ok:
                return plugin_localized(context, "The file is too large or upload failed; a text notice was sent to WeChat instead.", "文件过大或上传失败，已改为向微信发送文本提示。")
        except Exception as e:
            logger.exception("send_wechat_file failed")
            return plugin_localized(context, "Error sending file via WeChat: {error}", "通过微信发送文件时出错：{error}", error=e)
    else:
        return plugin_localized(context, "Error: the current channel does not support WeChat file sending. Use send_file for Web UI attachments.", "错误：当前渠道不支持发送微信文件。请使用 send_file 向 Web UI 添加附件。")

    # Notify WebUI — best-effort, swallow errors so a failed notification
    # never triggers an LLM retry of the WeChat send.
    desc = plugin_localized(context, "[Sent via WeChat: {name}]", "[已通过微信发送：{name}]", name=name)
    if text:
        desc += f" — {text}"
    try:
        round_id = str(run_context_value(context, "round_id") or "").strip()
        if round_id:
            await publish_runtime_event(context, {
                "type": "assistant_message",
                "round_id": round_id,
                "client_request_id": str(run_context_value(context, "client_request_id") or ""),
                "intermediate": True,
                "message": {"role": "assistant", "content": desc, "intermediate": True},
            })
        else:
            await publish_runtime_event(context, {
                "type": "assistant_message",
                "system_initiated": True,
                "message": {"role": "assistant", "content": desc},
            })
    except Exception:
        logger.exception("Failed to write WebUI notification for WeChat file send")

    if notify_state is not None:
        sent_wechat_files = notify_state.setdefault("sent_wechat_files", set())
        sent_wechat_files.add(dedupe_key)
        notify_state["sent"] = True
    return plugin_localized(context, "File sent via WeChat: {name}", "文件已通过微信发送：{name}", name=name)


handler = _tool_send_wechat_file

__all__ = ["TOOL_NAME", "TOOL_DEF", "handler", "_tool_send_wechat_file"]
