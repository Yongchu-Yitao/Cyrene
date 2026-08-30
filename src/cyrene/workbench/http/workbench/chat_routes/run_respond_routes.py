from __future__ import annotations

import asyncio

from fastapi import APIRouter

from cyrene.workbench.http import schemas as api_models
from cyrene.workbench.http.errors import localized_error_response
from cyrene.workbench.http.workbench.chat_routes.context import ChatRouteContext


def register_run_respond_routes(router: APIRouter, context: ChatRouteContext) -> None:
    service = context.service

    @router.post("/api/workbench/chats/{chat_id}/agent-requests/{request_id}/respond")
    async def api_workbench_agent_request_respond(
        chat_id: str,
        request_id: str,
        body_model: api_models.AgentRequestResponseBody,
    ):
        """Forward a dynamic Agent-owned permission or elicitation response."""
        chat = await asyncio.to_thread(service.repository.get, chat_id)
        if not chat:
            return localized_error_response(
                "Chat not found.", "未找到对话。", 404, "chat_not_found"
            )
        if service.run_manager.get(chat_id) is None:
            return localized_error_response(
                "The Agent request is no longer active.",
                "该 Agent 请求已失效。",
                409,
                "request_expired",
                failureKind="request_expired",
            )
        from cyrene.agents import (
            AgentRuntimeError,
            respond_to_external_agent_request,
        )

        body = api_models.body_dict(body_model)
        try:
            return await respond_to_external_agent_request(
                chat_id,
                request_id,
                body.get("response") if isinstance(body.get("response"), dict) else {},
            )
        except AgentRuntimeError as exc:
            messages = {
                "request_expired": (
                    "The Agent request is no longer active.",
                    "该 Agent 请求已失效。",
                ),
                "capability_missing": (
                    "This Agent does not support that response.",
                    "此 Agent 不支持该响应。",
                ),
                "agent_disabled": (
                    "The Agent is disabled.",
                    "该 Agent 已停用。",
                ),
                "agent_crashed": (
                    "The Agent stopped unexpectedly. Start it again and retry.",
                    "Agent 意外停止，请重新启动后再试。",
                ),
            }
            en, zh = messages.get(
                exc.kind,
                (
                    "The Agent response could not be submitted.",
                    "无法提交 Agent 响应。",
                ),
            )
            return localized_error_response(
                en,
                zh,
                409 if exc.kind == "request_expired" else 400,
                exc.kind,
                failureKind=exc.kind,
                retryable=exc.retryable,
                ok=False,
            )
