"""Public Workbench projection for completed scheduler Agent turns."""

from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timezone
from typing import Any

from agent.plugin import active_plugin_service

from cyrene.runtime.settings_store import is_soul_active, is_workspace_active
from cyrene.workbench.chat_events import publish_chat_changed
from cyrene.workbench.chat_service import ChatService
from cyrene.workbench.context_records import append_context_record


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _short_id(prefix: str) -> str:
    return f"{str(prefix or 'id')}_{uuid.uuid4().hex[:12]}"


async def _ensure_proactive_context(
    db_path: str,
    chat_id: str,
    message: dict[str, Any],
) -> None:
    from agent.workbench.conversation_runtime import ConversationRuntime

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


async def create_proactive_chat(
    db_path: str,
    project_id: str,
    text: str,
    *,
    chat_id: str,
    model: str = "",
    source_chat_id: str = "",
    lang: str = "",
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
        existing_message = next(
            (
                dict(item)
                for item in existing.get("messages") or ()
                if isinstance(item, dict)
                and str(item.get("role") or "") == "assistant"
            ),
            {},
        )
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
            "title": str(existing.get("title") or "主动工作"),
        }

    memory_snapshot = None
    memory_service = active_plugin_service("memory")
    snapshot_loader = getattr(memory_service, "current_snapshot", None)
    if callable(snapshot_loader):
        loaded = await asyncio.to_thread(snapshot_loader, normalized_project_id)
        if isinstance(loaded, dict):
            memory_snapshot = dict(loaded)

    now = _utc_now_iso()
    title = "Proactive work" if str(lang or "").lower() == "en" else "主动工作"
    message = {
        "id": _short_id("msg"),
        "role": "assistant",
        "content": content,
        "createdAt": now,
        "model": str(model or ""),
        "proactive": True,
        "systemInitiated": True,
    }
    chat: dict[str, Any] = service.create_chat(
        normalized_project_id,
        title,
        str(model or ""),
        soul_active=bool(is_soul_active()),
        workspace_active=bool(is_workspace_active()),
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

    def insert(payload: dict[str, Any]) -> dict[str, Any]:
        current = service.repository.find(payload, stable_chat_id)
        if current is not None:
            return {"created": False, "chat": dict(current)}
        payload.setdefault("chats", []).insert(0, chat)
        return {"created": True, "chat": dict(chat)}

    outcome = await asyncio.to_thread(service.repository.mutate, insert)
    stored_chat = dict(outcome.get("chat") or chat)
    stored_message = next(
        (
            dict(item)
            for item in stored_chat.get("messages") or ()
            if isinstance(item, dict)
            and str(item.get("role") or "") == "assistant"
        ),
        message,
    )
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


__all__ = ["create_proactive_chat"]
