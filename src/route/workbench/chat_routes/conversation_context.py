"""Thin HTTP adapters for conversation context and live inbox queries."""

from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from cyrene.workbench.conversation_context_service import (
    ConversationContextQueryService,
    ConversationInboxQueryService,
    ConversationNotFoundError,
)


def _legacy_session_id(chat_id: str) -> str:
    if not chat_id.startswith("legacy:"):
        return ""
    _prefix, _project_id, session_id = (chat_id.split(":", 2) + ["", ""])[:3]
    return session_id


def _not_found(exc: ConversationNotFoundError | None = None) -> JSONResponse:
    return JSONResponse(
        {"error": str(exc) if exc is not None else "chat not found"},
        status_code=404,
    )


def register_context_routes(
    router: APIRouter,
    context_queries: ConversationContextQueryService,
    inbox_queries: ConversationInboxQueryService,
) -> None:
    @router.get("/api/workbench/chats/{chat_id}/subagents")
    async def api_workbench_chat_subagents(chat_id: str, round_id: str = ""):
        if chat_id.startswith("legacy:"):
            return {"rounds": [], "activeRoundId": "", "agents": [], "messages": []}
        try:
            return await context_queries.subagents(chat_id, round_id)
        except ConversationNotFoundError as exc:
            return _not_found(exc)

    @router.get("/api/workbench/chats/{chat_id}/context")
    async def api_workbench_chat_context(chat_id: str):
        """Live context-window gauge + composition for the overview panel."""
        session_id = _legacy_session_id(chat_id)
        if chat_id.startswith("legacy:") and not session_id:
            return _not_found()
        try:
            return await context_queries.summary(
                chat_id,
                legacy_session_id=session_id,
            )
        except ConversationNotFoundError as exc:
            return _not_found(exc)

    @router.post("/api/workbench/chats/{chat_id}/compact")
    async def api_workbench_chat_compact(chat_id: str):
        """Let the user explicitly run the normal session compaction flow."""
        if chat_id.startswith("legacy:"):
            return JSONResponse(
                {"error": "legacy chat context is read-only"},
                status_code=403,
            )
        try:
            return await context_queries.compact(chat_id)
        except ConversationNotFoundError as exc:
            return _not_found(exc)

    @router.get("/api/workbench/chats/{chat_id}/context-blocks")
    async def api_workbench_chat_context_blocks(chat_id: str):
        """Context block composition using the same token math as the Overview gauge."""
        legacy = chat_id.startswith("legacy:")
        state_id = _legacy_session_id(chat_id) if legacy else chat_id
        if legacy and not state_id:
            return _not_found()
        return await context_queries.blocks(
            chat_id,
            state_id,
            legacy=legacy,
        )

    @router.get("/api/workbench/chats/{chat_id}/inbox")
    async def api_workbench_chat_inbox(chat_id: str):
        """Return only the current live inbox for this conversation."""
        if chat_id.startswith("legacy:"):
            return JSONResponse(
                {"error": "legacy chat has no Workbench inbox"},
                status_code=404,
            )
        try:
            snapshot = await inbox_queries.snapshot(chat_id)
        except ConversationNotFoundError as exc:
            return _not_found(exc)
        return JSONResponse(snapshot, headers={"Cache-Control": "no-store"})


__all__ = ["register_context_routes"]
