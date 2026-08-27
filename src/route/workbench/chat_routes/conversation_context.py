"""Thin HTTP adapters for conversation context and live inbox queries."""

from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from cyrene.workbench.conversation_context_service import (
    ConversationContextQueryService,
    ConversationInboxQueryService,
    ConversationNotFoundError,
)
from route.errors import localized_error_response


def _not_found(exc: ConversationNotFoundError | None = None) -> JSONResponse:
    return localized_error_response(
        "Chat not found.",
        "未找到对话。",
        404,
        "chat_not_found",
    )


def register_context_routes(
    router: APIRouter,
    context_queries: ConversationContextQueryService,
    inbox_queries: ConversationInboxQueryService,
) -> None:
    @router.get("/api/workbench/chats/{chat_id}/subagents")
    async def api_workbench_chat_subagents(chat_id: str, round_id: str = ""):
        try:
            payload = await context_queries.subagents(chat_id, round_id)
        except ConversationNotFoundError as exc:
            return _not_found(exc)
        return JSONResponse(payload, headers={"Cache-Control": "no-store"})

    @router.get("/api/workbench/chats/{chat_id}/context")
    async def api_workbench_chat_context(chat_id: str):
        """Project the Agent ContextTree into the overview panel."""
        try:
            payload = await context_queries.summary(chat_id)
        except ConversationNotFoundError as exc:
            return _not_found(exc)
        return JSONResponse(payload, headers={"Cache-Control": "no-store"})

    @router.post("/api/workbench/chats/{chat_id}/compact")
    async def api_workbench_chat_compact(chat_id: str):
        """Request compaction from the Agent ContextTree backend."""
        try:
            return await context_queries.compact(chat_id)
        except ConversationNotFoundError as exc:
            return _not_found(exc)

    @router.get("/api/workbench/chats/{chat_id}/context-blocks")
    async def api_workbench_chat_context_blocks(chat_id: str):
        """Return the current Agent ContextTree composition."""
        try:
            payload = await context_queries.blocks(chat_id)
        except ConversationNotFoundError as exc:
            return _not_found(exc)
        return JSONResponse(payload, headers={"Cache-Control": "no-store"})

    @router.get("/api/workbench/chats/{chat_id}/inbox")
    async def api_workbench_chat_inbox(chat_id: str):
        """Return Workbench activity and the Agent session inbox."""
        try:
            snapshot = await inbox_queries.snapshot(chat_id)
        except ConversationNotFoundError as exc:
            return _not_found(exc)
        return JSONResponse(snapshot, headers={"Cache-Control": "no-store"})


__all__ = ["register_context_routes"]
