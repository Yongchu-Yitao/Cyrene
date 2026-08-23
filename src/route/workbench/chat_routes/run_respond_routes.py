from __future__ import annotations

import asyncio

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from route import schemas as api_models
from route.workbench.chat_routes.context import ChatRouteContext


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
            return JSONResponse({"error": "chat not found"}, status_code=404)
        if service.run_manager.get(chat_id) is None:
            return JSONResponse(
                {
                    "error": "the Agent request is no longer active",
                    "code": "request_expired",
                    "failureKind": "request_expired",
                },
                status_code=409,
            )
        from cyrene.agent_runtime import (
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
            return JSONResponse(
                {"ok": False, "error": str(exc), **exc.to_public_dict()},
                status_code=409 if exc.kind == "request_expired" else 400,
            )
