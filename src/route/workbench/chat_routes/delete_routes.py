from __future__ import annotations

import asyncio
import logging
from typing import Any

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from cyrene.workbench import chat_groups
from cyrene.workbench.chat_events import publish_chat_changed
from cyrene.workbench.workspace_changes import delete_chat_change_sets
from route.workbench.chat_routes.context import ChatRouteContext

logger = logging.getLogger(__name__)


async def _cleanup_deleted_chat(service, removed_chat_id: str) -> None:
    from cyrene.agent_runtime.model_gateway import revoke_model_gateway_scope

    revoke_model_gateway_scope(chat_id=removed_chat_id)
    try:
        await asyncio.to_thread(delete_chat_change_sets, service.db_path, removed_chat_id)
    except Exception:
        logger.exception("Failed to delete workspace change history for chat %s", removed_chat_id)
    try:
        from cyrene.browser import close_electron_browser_session

        await close_electron_browser_session(removed_chat_id)
    except Exception:
        logger.exception("Failed to close Electron browser for chat %s", removed_chat_id)
    try:
        from cyrene.workbench.project_memory_prompt import cancel_chat_jobs, delete_chat_context

        await cancel_chat_jobs(removed_chat_id)
        await asyncio.to_thread(delete_chat_context, removed_chat_id)
    except Exception:
        logger.exception("Failed to delete project-memory context for chat %s", removed_chat_id)


def register_delete_routes(router: APIRouter, context: ChatRouteContext) -> dict[str, Any] | None:
    service = context.service
    _routes = context.runtime
    _project_data_key = context.project_data_key
    _clear_fork_metadata = service.clear_fork_metadata
    _read_chats_store = service.repository.read
    _write_chats_store = service.repository.write
    terminate_chat_agents = service.terminate_chat_agents

    @router.delete("/api/workbench/chats/{chat_id}")
    async def api_workbench_delete_chat(chat_id: str):
        if chat_id.startswith("legacy:"):
            _prefix, project_id, session_id = (chat_id.split(":", 2) + ["", ""])[:3]
            if not project_id or not session_id or _project_data_key(project_id) != "default":
                return JSONResponse({"error": "chat not found"}, status_code=404)
            payload, status_code = await _routes().delete_chat_session(session_id)
            if status_code != 200:
                return JSONResponse(payload, status_code=status_code)
            try:
                from cyrene.browser import close_electron_browser_session

                await close_electron_browser_session(session_id)
            except Exception:
                logger.exception("Failed to close Electron browser for chat %s", session_id)
            await publish_chat_changed(chat_id, project_id, "deleted")
            return {"ok": True}
        payload = await asyncio.to_thread(_read_chats_store)
        chats = payload.get("chats", [])
        removed_root = next(
            (chat for chat in chats if str(chat.get("id") or "") == chat_id),
            None,
        )
        if removed_root is None:
            return JSONResponse({"error": "chat not found"}, status_code=404)
        removed_project_id = str(removed_root.get("projectId") or "")
        removed_chat_ids = {
            chat_id,
            *[str(chat.get("id") or "") for chat in chats if str(chat.get("kind") or "") == "side-agent" and str(chat.get("parentChatId") or "") == chat_id],
        }
        try:
            await terminate_chat_agents(removed_chat_ids)
        except Exception:
            logger.exception("Failed to terminate agents for deleted chat %s", chat_id)
            return JSONResponse(
                {"error": "chat agents could not be terminated"},
                status_code=503,
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
            return JSONResponse(
                {"error": "chat group membership could not be revoked"},
                status_code=503,
            )
        payload["chats"] = next_chats
        await asyncio.to_thread(_write_chats_store, payload)
        await publish_chat_changed(chat_id, removed_project_id, "deleted")
        for removed_chat_id in removed_chat_ids:
            await _cleanup_deleted_chat(service, removed_chat_id)
        return {"ok": True}

    return {"delete_chat": api_workbench_delete_chat}
