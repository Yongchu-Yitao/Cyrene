"""Tool implementation for send_wechat_file."""

from __future__ import annotations

from typing import Any

from cyrene.tooling.native_definitions import get_native_tool_def
from cyrene.tooling.runtime_support import (
    _request_external_delivery_confirmation,
    _resolve_exportable_path,
    logger,
)

TOOL_NAME = 'send_wechat_file'
TOOL_DEF = get_native_tool_def(TOOL_NAME)


async def _tool_send_wechat_file(args: dict[str, Any], bot: Any, chat_id: int, _db_path: str, notify_state: dict[str, bool] | None) -> str:
    """Send a file to the user via WeChat CDN.

    Requires ``bot`` to be a ``WeChatClient`` (i.e. the agent is running
    on the WeChat channel).
    """
    path_arg = str(args.get("path", "") or "").strip()
    if not path_arg:
        return "Error: 'path' is required."

    from cyrene.agent.state import _current_agent_id, _current_round_id, _current_client_request_id
    from cyrene.agent.message import _insert_intermediate_user_reply
    from cyrene.agent.session import append_system_message

    if _current_agent_id.get() != "main":
        return "Only the main agent can send files via WeChat."

    path = _resolve_exportable_path(path_arg)
    if not path.exists() or not path.is_file():
        return f"Error: file not found: {path}"

    name = str(args.get("name", "") or "").strip() or path.name
    text = str(args.get("text", "") or "").strip()
    dedupe_key = f"{path.resolve()}|{name}|{text}"

    if notify_state is not None:
        sent_wechat_files = notify_state.setdefault("sent_wechat_files", set())
        if dedupe_key in sent_wechat_files:
            return f"Skipped duplicate WeChat file send: {name}"

    confirm = await _request_external_delivery_confirmation(
        tool_name="send_wechat_file",
        operation="外发 WeChat 文件",
        detail=f"文件：{path}\n名称：{name}\n附言：{text[:200]}",
        path_hint=str(path),
    )
    if confirm is not None:
        return confirm

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
        round_id = str(_current_round_id.get() or "").strip()
        if round_id:
            client_request_id = str(_current_client_request_id.get() or "").strip()
            await _insert_intermediate_user_reply(desc, round_id=round_id, client_request_id=client_request_id)
        else:
            await append_system_message(desc, message_meta={})
    except Exception:
        logger.exception("Failed to write WebUI notification for WeChat file send")

    if notify_state is not None:
        sent_wechat_files = notify_state.setdefault("sent_wechat_files", set())
        sent_wechat_files.add(dedupe_key)
        notify_state["sent"] = True
    return f"File sent via WeChat: {name}"


handler = _tool_send_wechat_file

__all__ = ["TOOL_NAME", "TOOL_DEF", "handler", "_tool_send_wechat_file"]
