"""Thin HTTP adapters for conversation context and live inbox queries."""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from cyrene.workbench.chat.conversation_context_service import (
    ConversationContextQueryService,
    ConversationContextUpdateError,
    ConversationInboxQueryService,
    ConversationNotFoundError,
)
from cyrene.workbench.http.errors import localized_error_response


def _not_found(exc: ConversationNotFoundError | None = None) -> JSONResponse:
    return localized_error_response(
        "Chat not found.",
        "未找到对话。",
        404,
        "chat_not_found",
    )


def _register_context_read_routes(
    router: APIRouter,
    context_queries: ConversationContextQueryService,
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


def _register_context_update_routes(
    router: APIRouter,
    context_queries: ConversationContextQueryService,
) -> None:
    @router.patch(
        "/api/workbench/chats/{chat_id}/context-nodes/{node_id}/system-prompt"
    )
    async def api_workbench_update_system_prompt(
        chat_id: str,
        node_id: str,
        request: Request,
    ):
        """Replace the persisted prompt text for one editable system node."""
        try:
            body = await request.json()
            content = body.get("content") if isinstance(body, dict) else None
            expected_updated_at = (
                str(body.get("expectedUpdatedAt") or "")
                if isinstance(body, dict)
                else ""
            )
            if not isinstance(content, str):
                raise ConversationContextUpdateError("system prompt is required")
            payload = await context_queries.update_system_prompt(
                chat_id,
                node_id,
                content,
                expected_updated_at,
            )
        except ConversationNotFoundError as exc:
            return _not_found(exc)
        except (ConversationContextUpdateError, ValueError):
            return localized_error_response(
                "The system prompt could not be updated.",
                "无法更新 System Prompt。",
                400,
                "system_prompt_update_failed",
            )
        return JSONResponse(payload, headers={"Cache-Control": "no-store"})

    @router.patch(
        "/api/workbench/chats/{chat_id}/context-nodes/{node_id}"
    )
    async def api_workbench_update_context_node(
        chat_id: str,
        node_id: str,
        request: Request,
    ):
        """Replace the editable content represented by one timeline node."""
        try:
            body = await request.json()
            content = body.get("content") if isinstance(body, dict) else None
            expected_updated_at = (
                str(body.get("expectedUpdatedAt") or "")
                if isinstance(body, dict)
                else ""
            )
            if not isinstance(content, str):
                raise ConversationContextUpdateError("node content is required")
            payload = await context_queries.update_node_content(
                chat_id,
                node_id,
                content,
                expected_updated_at,
            )
        except ConversationNotFoundError as exc:
            return _not_found(exc)
        except (ConversationContextUpdateError, ValueError):
            return localized_error_response(
                "The context node could not be updated. Check its content format and reload before retrying.",
                "无法更新上下文节点。请检查内容格式，并刷新后重试。",
                400,
                "context_node_update_failed",
            )
        return JSONResponse(payload, headers={"Cache-Control": "no-store"})


def _register_inbox_routes(
    router: APIRouter,
    inbox_queries: ConversationInboxQueryService,
) -> None:
    @router.get("/api/workbench/chats/{chat_id}/inbox")
    async def api_workbench_chat_inbox(chat_id: str):
        """Return Workbench activity and the Agent session inbox."""
        try:
            snapshot = await inbox_queries.snapshot(chat_id)
        except ConversationNotFoundError as exc:
            return _not_found(exc)
        return JSONResponse(snapshot, headers={"Cache-Control": "no-store"})


def register_context_routes(
    router: APIRouter,
    context_queries: ConversationContextQueryService,
    inbox_queries: ConversationInboxQueryService,
) -> None:
    _register_context_read_routes(router, context_queries)
    _register_context_update_routes(router, context_queries)
    _register_inbox_routes(router, inbox_queries)


__all__ = ["register_context_routes"]
