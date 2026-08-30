"""Tool implementation for send_file."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from cyrene.core.plugin import PluginContext
from .definitions import get_native_tool_def
from cyrene.plugins.native_runtime import (
    json_result,
    plugin_localized,
    publish_runtime_event,
    resolve_exportable_path,
    run_context_value,
)
from cyrene.platform.attachments import (
    build_public_attachment_payload,
    register_generated_attachment,
)

logger = logging.getLogger(__name__)

TOOL_NAME = 'send_file'
TOOL_DEF = get_native_tool_def(TOOL_NAME)


async def _tool_send_file(args: dict[str, Any], context: PluginContext) -> str:
    path_arg = str(args.get("path", "") or "").strip()
    if not path_arg:
        return plugin_localized(context, "Error: 'path' is required.", "错误：必须提供 path。")

    if str(run_context_value(context, "agent_id", "main")) != "main":
        return plugin_localized(context, "Only the main Agent can send a file to the Web UI.", "只有主 Agent 可以向 Web UI 发送文件。")

    path = resolve_exportable_path(path_arg)
    if not path.exists() or not path.is_file():
        return plugin_localized(context, "Error: file not found: {path}", "错误：未找到文件：{path}", path=path)

    text = str(args.get("text", "") or "").strip()
    registered = register_generated_attachment(str(path), display_name=str(args.get("name", "") or "").strip() or None)
    attachment = build_public_attachment_payload(registered)

    # Register generated files through the knowledge Plugin for the active
    # conversation.
    try:
        import mimetypes
        doc_path = registered.get("path", "")
        current_session_id = str(run_context_value(context, "session_id") or "")
        knowledge = context.services.get("knowledge")
        if knowledge is not None and doc_path:
            from pathlib import Path
            doc_file = Path(doc_path)
            content_type = mimetypes.guess_type(str(doc_file))[0] or "application/octet-stream"
            await knowledge.register_attachments(
                current_session_id,
                [{
                    "path": str(doc_file.resolve()),
                    "name": registered.get("name", doc_file.name),
                    "content_type": content_type,
                }],
            )
    except Exception as e:
        logger.debug(f"Failed to register generated file in knowledge base: {e}")

    round_id = str(run_context_value(context, "round_id") or "").strip()
    client_request_id = str(run_context_value(context, "client_request_id") or "").strip()
    if round_id:
        public_message = {
            "id": f"assistant_{uuid4().hex}",
            "role": "assistant",
            "content": text,
            "createdAt": datetime.now(timezone.utc).isoformat(),
            "intermediate": True,
            "roundId": round_id,
            "attachments": [attachment],
        }
        await publish_runtime_event(context, {"type": "intermediate_message", "message": public_message})
        await publish_runtime_event(context, {
            "type": "assistant_message",
            "round_id": round_id,
            "client_request_id": client_request_id,
            "intermediate": True,
            "message_id": public_message["id"],
            "message": public_message,
        })
    else:
        await publish_runtime_event(context, {
            "type": "assistant_message",
            "system_initiated": True,
            "message": {"role": "assistant", "content": text, "attachments": [attachment]},
        })
    notify_state = context.data.get("notify_state")
    if isinstance(notify_state, dict):
        notify_state["sent"] = True
    return json_result({
        "status": "sent",
        "attachment": attachment,
    })


handler = _tool_send_file

__all__ = ["TOOL_NAME", "TOOL_DEF", "handler", "_tool_send_file"]
