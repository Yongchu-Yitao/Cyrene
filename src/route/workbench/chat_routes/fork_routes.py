from __future__ import annotations

import asyncio
import logging
from typing import Any

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from cyrene.workbench import project_runtime
from cyrene.workbench.chat_events import publish_chat_changed
from route import schemas as api_models
from route.workbench.chat_routes.context import ChatRouteContext

logger = logging.getLogger(__name__)


def _build_fork_chat(service, chat: dict[str, Any], message_id: str, new_content: str):
    from cyrene.agent_runtime.builtin import normalize_agent_binding

    if not normalize_agent_binding(chat.get("agent") if isinstance(chat.get("agent"), dict) else None).is_builtin:
        return JSONResponse(
            {
                "error": "This Agent does not support conversation forks",
                "code": "capability_missing",
                "failureKind": "capability_missing",
            },
            status_code=409,
        )
    messages = chat.get("messages") if isinstance(chat.get("messages"), list) else []
    if not messages:
        return JSONResponse({"error": "chat has no messages"}, status_code=400)
    edit_index = next(
        (index for index, entry in enumerate(messages) if str(entry.get("id") or "") == message_id),
        -1,
    )
    if edit_index < 0:
        return JSONResponse({"error": "message not found"}, status_code=404)
    if str(messages[edit_index].get("role") or "") != "user":
        return JSONResponse({"error": "can only edit user messages"}, status_code=400)

    user_ordinal = sum(1 for entry in messages[: edit_index + 1] if str(entry.get("role") or "") == "user")
    project_id = str(chat.get("projectId") or "")
    now = service.utc_now_iso()
    new_chat = service.create_chat(
        project_id,
        str(chat.get("title") or ""),
        str(chat.get("model") or project_runtime._get_model() or ""),
        project_memory_snapshot=(dict(chat.get("projectMemorySnapshot") or {}) if isinstance(chat.get("projectMemorySnapshot"), dict) else None),
    )
    new_chat["forkedFromChatId"] = str(chat.get("id") or "")
    new_chat["forkedAtMessageId"] = message_id
    if chat.get("workspaceOverride"):
        new_chat["workspaceOverride"] = str(chat["workspaceOverride"])
    new_chat["soulActive"] = service.chat_soul_active(chat)
    new_chat["workspaceActive"] = service.chat_workspace_active(chat)
    if chat.get("reasoningEffort"):
        new_chat["reasoningEffort"] = str(chat["reasoningEffort"])
    new_chat["forkMessage"] = new_content.replace("\n", " ").strip()[:80]

    prefix = []
    for entry in messages[:edit_index]:
        copied = dict(entry)
        copied.pop("usage", None)
        prefix.append(copied)
    orig = messages[edit_index]
    edited_entry: dict[str, Any] = {
        "id": service.short_id("msg"),
        "role": "user",
        "content": new_content,
        "createdAt": now,
    }
    if isinstance(orig.get("attachments"), list) and orig["attachments"]:
        edited_entry["attachments"] = orig["attachments"]
        if orig.get("agentAttachments"):
            edited_entry["agentAttachments"] = orig["agentAttachments"]
    new_chat["messages"] = prefix + [edited_entry]
    new_chat["completedTurnCount"] = service.completed_turn_count({"messages": prefix})
    new_chat["updatedAt"] = now
    return new_chat, user_ordinal


def register_fork_routes(router: APIRouter, context: ChatRouteContext) -> None:
    service = context.service
    _find_chat = service.repository.find
    _public_chat_full = service.public_chat_full
    _read_chats_store = service.repository.read
    _write_chats_store = service.repository.write

    @router.post("/api/workbench/chats/{chat_id}/fork")
    async def api_workbench_chat_fork(chat_id: str, body_model: api_models.ChatForkBody):
        """Fork a conversation at an edited user message.

        Creates a new chat with the prefix transcript (everything before the
        edited user message) plus a NEW user entry bearing the edited content
        and the original attachments. The source chat is preserved untouched.
        The active ContextTree path is copied up to the same user boundary, so
        the fork can replay the edited turn through a normal send without
        mutating or depending on the retired ``state.json`` session. The Agent
        is not run here.
        """
        body = api_models.body_dict(body_model)
        message_id = str(body.get("messageId") or "").strip()
        new_content = str(body.get("content") or "").strip()
        if not message_id:
            return JSONResponse({"error": "messageId is required"}, status_code=400)
        if not new_content:
            return JSONResponse({"error": "content is required"}, status_code=400)
        payload = await asyncio.to_thread(_read_chats_store)
        chat = _find_chat(payload, chat_id)
        if not chat:
            return JSONResponse({"error": "chat not found"}, status_code=404)
        project_id = str(chat.get("projectId") or "")
        fork_result = _build_fork_chat(service, chat, message_id, new_content)
        if isinstance(fork_result, JSONResponse):
            return fork_result
        new_chat, user_ordinal = fork_result

        new_chat_id = str(new_chat.get("id") or "")
        try:
            await asyncio.to_thread(
                service.run_manager.conversation_runtime.fork_context,
                chat_id,
                new_chat_id,
                user_ordinal=user_ordinal,
            )
        except Exception as exc:
            logger.exception(
                "Failed to fork ContextTree %s from %s",
                new_chat_id,
                chat_id,
            )
            return JSONResponse(
                {
                    "error": str(exc) or "conversation context could not be forked",
                    "code": "context_fork_failed",
                },
                status_code=409,
            )

        payload.setdefault("chats", []).insert(0, new_chat)
        try:
            await asyncio.to_thread(_write_chats_store, payload)
        except Exception:
            await asyncio.to_thread(
                service.run_manager.conversation_runtime.delete_context,
                new_chat_id,
            )
            raise

        await publish_chat_changed(chat_id, project_id, "forked", fork_chat_id=new_chat_id)

        return {"ok": True, "chat": _public_chat_full(new_chat)}
