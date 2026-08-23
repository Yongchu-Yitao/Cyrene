from __future__ import annotations

import asyncio
import copy
import re
from typing import Any, Awaitable, Callable

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from cyrene.workbench.chat_events import publish_chat_changed
from route import schemas as api_models
from route.workbench.chat_routes.context import ChatRouteContext


async def _prepare_action_send(
    context: ChatRouteContext,
    chat_id: str,
    body: dict[str, Any],
):
    action_id = str(body.get("actionId") or "").strip()
    value = str(body.get("value") or "")
    message_id = str(body.get("messageId") or "").strip()
    if not action_id or not message_id:
        return JSONResponse(
            {"error": "actionId and messageId are required"},
            status_code=400,
        )
    if not re.fullmatch(r"[a-z0-9_]+", action_id) or len(action_id) > 32:
        return JSONResponse({"error": "invalid action_id"}, status_code=400)
    if len(value) > 256:
        return JSONResponse({"error": "value too long"}, status_code=400)
    if chat_id.startswith("legacy:"):
        return JSONResponse({"error": "legacy chats cannot run actions"}, status_code=403)

    service = context.service
    chat = await asyncio.to_thread(service.repository.get, chat_id)
    if not chat:
        return JSONResponse({"error": "chat not found"}, status_code=404)
    base_chat = copy.deepcopy(chat)
    messages = chat.get("messages") if isinstance(chat.get("messages"), list) else []
    target = next(
        (entry for entry in messages if str(entry.get("id") or "") == message_id),
        None,
    )
    if target is None:
        return JSONResponse({"error": "message not found"}, status_code=404)
    if str(target.get("role") or "") != "assistant":
        return JSONResponse(
            {"error": "actions target assistant messages"},
            status_code=400,
        )
    content = str(target.get("content") or "")
    if not service.has_button_block(content, action_id):
        return JSONResponse({"error": "action not found in message"}, status_code=404)
    updated_content, label = service.disable_button_block(content, action_id)
    if updated_content is None:
        return JSONResponse(
            {"error": "action already handled", "code": "action_duplicate"},
            status_code=409,
        )
    target["content"] = updated_content
    chat["updatedAt"] = service.utc_now_iso()
    await asyncio.to_thread(service.repository.write_one, chat, base_chat=base_chat)
    await publish_chat_changed(
        chat_id,
        str(chat.get("projectId") or ""),
        "action_applied",
    )
    label_text = label or action_id
    if value:
        label_text = f"{label_text} ({action_id}: {value})"
    return {"message": f"[按钮操作] {label_text}", "stream": False}


def register_run_action_routes(
    router: APIRouter,
    context: ChatRouteContext,
    *,
    send_chat: Callable[[str, dict[str, Any]], Awaitable[Any]],
) -> None:
    @router.post("/api/workbench/chats/{chat_id}/actions")
    async def api_workbench_chat_action(
        chat_id: str,
        body_model: api_models.ChatActionBody,
    ):
        """Consume a model button and route it through the normal send pipeline."""
        prepared = await _prepare_action_send(
            context,
            chat_id,
            api_models.body_dict(body_model),
        )
        if isinstance(prepared, JSONResponse):
            return prepared
        return await send_chat(chat_id, prepared)
