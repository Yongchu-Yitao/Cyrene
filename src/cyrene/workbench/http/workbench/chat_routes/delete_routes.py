from __future__ import annotations

import asyncio
import logging
from typing import Any

from fastapi import APIRouter

from cyrene.workbench.chat import chat_groups
from cyrene.workbench.chat.chat_events import publish_chat_changed
from cyrene.workbench.workspaces.workspace_changes import delete_chat_change_sets
from cyrene.workbench.http.errors import localized_error_response
from cyrene.workbench.http.workbench.chat_routes.context import ChatRouteContext

logger = logging.getLogger(__name__)


async def _cleanup_deleted_chat(
    service,
    removed_chat_id: str,
    memory_service=None,
) -> None:
    from cyrene.agent_runtime.model_gateway import revoke_model_gateway_scope

    revoke_model_gateway_scope(chat_id=removed_chat_id)
    try:
        await asyncio.to_thread(delete_chat_change_sets, service.db_path, removed_chat_id)
    except Exception:
        logger.exception("Failed to delete workspace change history for chat %s", removed_chat_id)
    try:
        from cyrene.core.plugin import application_plugin_service

        browser_service = application_plugin_service("browser")
        if browser_service is not None:
            await browser_service.close_session(removed_chat_id)
    except Exception:
        logger.exception("Failed to close Electron browser for chat %s", removed_chat_id)
    if memory_service is not None:
        try:
            await memory_service.delete_chat(removed_chat_id)
        except Exception:
            logger.exception(
                "Failed to delete project-memory context for chat %s",
                removed_chat_id,
            )


def register_delete_routes(router: APIRouter, context: ChatRouteContext) -> dict[str, Any] | None:
    service = context.service
    _clear_fork_metadata = service.clear_fork_metadata
    _read_chats_store = service.repository.read
    _write_chats_store = service.repository.write
    terminate_chat_agents = service.terminate_chat_agents

    @router.delete("/api/workbench/chats/{chat_id}")
    async def api_workbench_delete_chat(chat_id: str):
        payload = await asyncio.to_thread(_read_chats_store)
        chats = payload.get("chats", [])
        removed_root = next(
            (chat for chat in chats if str(chat.get("id") or "") == chat_id),
            None,
        )
        if removed_root is None:
            return localized_error_response(
                "Chat not found.", "未找到对话。", 404, "chat_not_found"
            )
        removed_project_id = str(removed_root.get("projectId") or "")
        removed_chat_ids = {
            chat_id,
            *[str(chat.get("id") or "") for chat in chats if str(chat.get("kind") or "") == "side-agent" and str(chat.get("parentChatId") or "") == chat_id],
        }
        try:
            await terminate_chat_agents(removed_chat_ids)
        except Exception:
            logger.exception("Failed to terminate agents for deleted chat %s", chat_id)
            return localized_error_response(
                "The chat's agents could not be stopped. Please try again.",
                "无法停止该对话的 Agent，请重试。",
                503,
                "chat_agents_termination_failed",
            )
        next_chats = [chat for chat in chats if str(chat.get("id") or "") not in removed_chat_ids]
        for chat in next_chats:
            if str(chat.get("forkedFromChatId") or "") == chat_id:
                _clear_fork_metadata(chat)
        try:
            # Revoke group authority before deleting the chat record. If this
            # fails, leave the chat intact rather than persisting a stale group.
            await chat_groups.remove_chat(chat_id, removed_project_id)
        except Exception:
            logger.exception("Failed to remove deleted chat %s from chat groups", chat_id)
            return localized_error_response(
                "The chat could not be removed from its group. Please try again.",
                "无法将该对话从群组中移除，请重试。",
                503,
                "chat_group_membership_revoke_failed",
            )
        payload["chats"] = next_chats
        await asyncio.to_thread(_write_chats_store, payload)
        await publish_chat_changed(chat_id, removed_project_id, "deleted")
        for removed_chat_id in removed_chat_ids:
            await _cleanup_deleted_chat(service, removed_chat_id, context.memory)
        return {"ok": True}

    return {"delete_chat": api_workbench_delete_chat}
