"""Tool implementation for send_wechat_file."""

from __future__ import annotations

import logging
from typing import Any

from agent.plugin import PluginContext
from .definitions import get_native_tool_def
from agent.plugin.native_runtime import publish_runtime_event, resolve_exportable_path, run_context_value

logger = logging.getLogger(__name__)

TOOL_NAME = 'send_wechat_file'
TOOL_DEF = get_native_tool_def(TOOL_NAME)


async def _tool_send_wechat_file(args: dict[str, Any], context: PluginContext) -> str:
    """Send a file to the user via WeChat CDN.

    Requires ``bot`` to be a ``WeChatClient`` (i.e. the agent is running
    on the WeChat channel).
    """
    path_arg = str(args.get("path", "") or "").strip()
    if not path_arg:
        return "Error: 'path' is required."

    if str(run_context_value(context, "agent_id", "main")) != "main":
        return "Only the main agent can send files via WeChat."

    bot = context.data.get("bot")
    chat_id = context.data.get("chat_id")
    notify_state = context.data.get("notify_state")

    path = resolve_exportable_path(path_arg)
    if not path.exists() or not path.is_file():
        return f"Error: file not found: {path}"

    name = str(args.get("name", "") or "").strip() or path.name
    text = str(args.get("text", "") or "").strip()
    dedupe_key = f"{path.resolve()}|{name}|{text}"

    if notify_state is not None:
        sent_wechat_files = notify_state.setdefault("sent_wechat_files", set())
        if dedupe_key in sent_wechat_files:
            return f"Skipped duplicate WeChat file send: {name}"

    # Send via WeChat if the bot supports it
    send_file_fn = getattr(bot, "send_file", None)
    if send_file_fn is not None:
        try:
            ok = await send_file_fn(chat_id=str(chat_id), filepath=str(path), filename=name)
            if not ok:
                return "File too large or upload failed — a text notice was sent to WeChat instead."
        except Exception as e:
            logger.exception("send_wechat_file failed")
            return f"Error sending file via WeChat: {e}"
    else:
        return "Error: current channel does not support WeChat file sending. Use send_file for WebUI attachments."

    # Notify WebUI — best-effort, swallow errors so a failed notification
    # never triggers an LLM retry of the WeChat send.
    desc = f"[WeChat sent: {name}]"
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
    return f"File sent via WeChat: {name}"


handler = _tool_send_wechat_file

__all__ = ["TOOL_NAME", "TOOL_DEF", "handler", "_tool_send_wechat_file"]
