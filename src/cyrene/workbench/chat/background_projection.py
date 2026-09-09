"""Idempotent Workbench projection shared by background-turn producers.

Producer plugins retain titles, scheduling and delivery policy. Context IDs,
assistant-only turn counts, atomic insertion and notification ordering live here.
"""
from __future__ import annotations

import asyncio
import uuid
from collections.abc import Callable, Mapping
from datetime import datetime, timezone
from typing import Any

from cyrene.core.plugin import application_plugin_service
from cyrene.workbench.chat.chat_events import publish_chat_changed
from cyrene.workbench.chat.chat_service import ChatService
from cyrene.workbench.sessions.context_records import append_context_record


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _short_id(prefix: str) -> str:
    return f"{str(prefix or 'id')}_{uuid.uuid4().hex[:12]}"


async def _ensure_proactive_context(
    db_path: str,
    chat_id: str,
    message: dict[str, Any],
) -> None:
    from cyrene.workbench.core_adapter.conversation_runtime import ConversationRuntime

    checkpoint = await asyncio.to_thread(
        ConversationRuntime(str(db_path or "")).context_checkpoint,
        chat_id,
    )
    if (
        isinstance(checkpoint, dict)
        and str(checkpoint.get("status") or "") == "completed"
    ):
        # Heartbeat work is executed with the public chat id, so its full
        # system/user/assistant history already exists in this ContextTree.
        return
    context_id = f"context_proactive_{chat_id}"
    await asyncio.to_thread(
        append_context_record,
        str(db_path or ""),
        chat_id,
        {
            "role": "assistant",
            "content": str(message.get("content") or ""),
            "model": str(message.get("model") or ""),
            "run_id": f"proactive_{chat_id}",
            "message_id": context_id,
            "public_message_id": str(message.get("id") or ""),
            "session_end_complete": True,
            "system_initiated": True,
            "proactive": True,
        },
        node_id=context_id,
        create_tree=True,
        require_idle=True,
    )


async def _project_memory_snapshot(project_id: str) -> dict[str, Any] | None:
    memory_service = application_plugin_service("memory")
    snapshot_loader = getattr(memory_service, "current_snapshot", None)
    if not callable(snapshot_loader):
        return None
    loaded = await asyncio.to_thread(snapshot_loader, project_id)
    return dict(loaded) if isinstance(loaded, dict) else None


def _first_assistant_message(chat: dict[str, Any], default: dict[str, Any]) -> dict[str, Any]:
    return next(
        (dict(item) for item in chat.get("messages") or ()
         if isinstance(item, dict) and str(item.get("role") or "") == "assistant"),
        default,
    )


async def create_completed_background_chat(
    db_path: str,
    project_id: str,
    text: str,
    *,
    chat_id: str,
    model: str = "",
    message_fields: Callable[[], Mapping[str, Any]] | None = None,
    source_chat_id: str = "",
    title: str,
) -> dict[str, str] | None:
    """Persist one completed Plugin turn as a dedicated public chat."""

    content = str(text or "").strip()
    normalized_project_id = str(project_id or "").strip()
    stable_chat_id = str(chat_id or "").strip()
    if not normalized_project_id or not stable_chat_id or not content:
        return None

    service = ChatService(str(db_path or ""))
    existing = await asyncio.to_thread(
        service.repository.get,
        stable_chat_id,
    )
    if existing is not None:
        existing_message = _first_assistant_message(existing, {})
        if existing_message:
            await _ensure_proactive_context(
                str(db_path or ""),
                stable_chat_id,
                existing_message,
            )
        return {
            "chat_id": stable_chat_id,
            "project_id": str(
                existing.get("projectId") or normalized_project_id
            ),
            "title": str(
                existing.get("title")
                or title
            ),
        }

    memory_snapshot = await _project_memory_snapshot(normalized_project_id)

    now = _utc_now_iso()
    message = {
        "id": _short_id("msg"),
        "role": "assistant",
        "content": content,
        "createdAt": now,
        "model": str(model or ""),
        "proactive": True,
        "systemInitiated": True,
        **(message_fields() if message_fields is not None else {}),
    }
    chat: dict[str, Any] = service.create_chat(
        normalized_project_id,
        title,
        str(model or ""),
    )
    chat.update(
        {
            "id": stable_chat_id,
            "titleLocked": True,
            "createdAt": now,
            "updatedAt": now,
            "messages": [message],
            # The public transcript starts with an assistant-only scheduler
            # result; the first actual user exchange still begins at turn zero.
            "completedTurnCount": 0,
            "proactive": True,
        }
    )
    if source_chat_id:
        chat["sourceChatId"] = str(source_chat_id)
    if memory_snapshot is not None:
        chat["projectMemorySnapshot"] = memory_snapshot

    return await _store_completed_chat(service, chat, message, db_path, stable_chat_id, normalized_project_id, title, now)


async def append_completed_background_message(
    db_path: str,
    chat_id: str,
    text: str,
    *,
    delivery_id: str,
    model: str = "",
    message_fields: Callable[[], Mapping[str, Any]] | None = None,
) -> dict[str, str] | None:
    """Append one idempotent scheduler result to an existing public chat."""

    content = str(text or "").strip()
    target_chat_id = str(chat_id or "").strip()
    identity = str(delivery_id or "").strip()
    if not content or not target_chat_id or not identity:
        return None

    service = ChatService(str(db_path or ""))
    now = _utc_now_iso()
    message_id = f"msg_{identity}"
    context_id = f"context_{identity}"
    message = {
        "id": message_id,
        "role": "assistant",
        "content": content,
        "createdAt": now,
        "model": str(model or ""),
        "proactive": True,
        "scheduled": True,
        "systemInitiated": True,
        **(message_fields() if message_fields is not None else {}),
    }
    mutation_result: dict[str, Any] = {"created": False}

    def append(chat: dict[str, Any]) -> None:
        messages = [
            item for item in chat.get("messages") or () if isinstance(item, dict)
        ]
        stored = next(
            (
                item
                for item in messages
                if str(item.get("id") or "") == message_id
            ),
            None,
        )
        if stored is None:
            service.merge_chat_messages_chronologically(chat, [message])
            chat["updatedAt"] = max(str(chat.get("updatedAt") or ""), now)
            mutation_result["created"] = True
            stored = message
        mutation_result["message"] = stored
        mutation_result["project_id"] = str(chat.get("projectId") or "")
        mutation_result["title"] = str(chat.get("title") or "")
        mutation_result["summary"] = service.public_chat_light(chat)

    updated = await asyncio.to_thread(
        service.repository.mutate_one,
        target_chat_id,
        append,
    )
    if updated is None:
        return None

    stored_message = dict(mutation_result.get("message") or message)
    await asyncio.to_thread(
        append_context_record,
        str(db_path or ""),
        target_chat_id,
        {
            "role": "assistant",
            "content": str(stored_message.get("content") or ""),
            "model": str(stored_message.get("model") or ""),
            "run_id": identity,
            "message_id": context_id,
            "public_message_id": str(stored_message.get("id") or message_id),
            "session_end_complete": True,
            "system_initiated": True,
            "scheduled": True,
        },
        node_id=context_id,
        require_idle=True,
    )
    if mutation_result["created"]:
        await publish_chat_changed(
            target_chat_id,
            mutation_result["project_id"],
            "scheduled_message",
            chatSummary=mutation_result["summary"],
            assistantMessages=[service.public_message(stored_message)],
        )
    return {
        "chat_id": target_chat_id,
        "project_id": mutation_result["project_id"],
        "title": mutation_result["title"],
    }


async def _store_completed_chat(
    service: ChatService, chat: dict[str, Any], message: dict[str, Any],
    db_path: str, stable_chat_id: str, normalized_project_id: str, title: str, now: str,
) -> dict[str, str]:
    def insert(payload: dict[str, Any]) -> dict[str, Any]:
        current = service.repository.find(payload, stable_chat_id)
        if current is not None:
            return {"created": False, "chat": dict(current)}
        payload.setdefault("chats", []).insert(0, chat)
        return {"created": True, "chat": dict(chat)}

    outcome = await asyncio.to_thread(service.repository.mutate, insert)
    stored_chat = dict(outcome.get("chat") or chat)
    stored_message = _first_assistant_message(stored_chat, message)
    await _ensure_proactive_context(
        str(db_path or ""),
        stable_chat_id,
        stored_message,
    )
    if not outcome.get("created"):
        return {
            "chat_id": stable_chat_id,
            "project_id": str(
                stored_chat.get("projectId") or normalized_project_id
            ),
            "title": str(stored_chat.get("title") or title),
        }

    result = {
        "chat_id": stable_chat_id,
        "project_id": normalized_project_id,
        "title": title,
    }
    await publish_chat_changed(
        stable_chat_id,
        normalized_project_id,
        "created",
        chatSummary=service.public_chat_light(stored_chat),
    )
    from cyrene.observability import debug

    await debug.publish_event(
        {
            "type": "workbench_proactive_message",
            "session_id": stable_chat_id,
            "chat_id": stable_chat_id,
            "project_id": normalized_project_id,
            "updated_at": now,
            "message": dict(message),
        },
        session_id=stable_chat_id,
    )
    return result
