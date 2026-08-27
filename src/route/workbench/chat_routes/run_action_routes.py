from __future__ import annotations

import asyncio
import copy
import re
from typing import Any, Awaitable, Callable

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from cyrene.localization import app_language, localized
from cyrene.workbench.chat_events import publish_chat_changed
from route import schemas as api_models
from route.errors import localized_error_response
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
        return localized_error_response(
            "An action ID and message ID are required.",
            "缺少操作 ID 或消息 ID。",
            400,
            "action_target_required",
        )
    if not re.fullmatch(r"[a-z0-9_]+", action_id) or len(action_id) > 32:
        return localized_error_response(
            "The action ID is invalid.",
            "操作 ID 无效。",
            400,
            "invalid_action_id",
        )
    if len(value) > 256:
        return localized_error_response(
            "The action value is too long.",
            "操作值过长。",
            400,
            "action_value_too_long",
        )
    service = context.service
    chat = await asyncio.to_thread(service.repository.get, chat_id)
    if not chat:
        return localized_error_response(
            "Chat not found.", "未找到对话。", 404, "chat_not_found"
        )
    base_chat = copy.deepcopy(chat)
    messages = chat.get("messages") if isinstance(chat.get("messages"), list) else []
    target = next(
        (entry for entry in messages if str(entry.get("id") or "") == message_id),
        None,
    )
    if target is None:
        return localized_error_response(
            "Message not found.", "未找到消息。", 404, "message_not_found"
        )
    if str(target.get("role") or "") != "assistant":
        return localized_error_response(
            "Actions can only target assistant messages.",
            "操作只能应用于助手消息。",
            400,
            "invalid_action_target",
        )
    content = str(target.get("content") or "")
    if not service.has_button_block(content, action_id):
        return localized_error_response(
            "The action was not found in this message.",
            "此消息中未找到该操作。",
            404,
            "action_not_found",
        )
    updated_content, label = service.disable_button_block(content, action_id)
    if updated_content is None:
        return localized_error_response(
            "This action has already been handled.",
            "此操作已处理。",
            409,
            "action_duplicate",
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
    language = app_language()
    return {
        "message": localized(
            "[Button action] {label}",
            "[按钮操作] {label}",
            language=language,
            label=label_text,
        ),
        "stream": False,
    }


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
