from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter
from fastapi.responses import JSONResponse, StreamingResponse

from cyrene.workbench.chat_guidance_service import (
    ChatGuidanceApplicationService,
    ChatGuidanceDependencies,
)
from route import schemas as api_models
from route.workbench.chat_routes.context import ChatRouteContext


def register_run_stream_routes(
    router: APIRouter,
    context: ChatRouteContext,
) -> dict[str, Any]:
    service = context.service
    run_manager = service.run_manager
    guidance_service = ChatGuidanceApplicationService(
        ChatGuidanceDependencies(
            run_manager=run_manager,
            get_chat=service.repository.get,
            mutate_chat=service.repository.mutate_one,
            public_message=service.public_message,
            utc_now_iso=service.utc_now_iso,
            short_id=service.short_id,
        )
    )

    @router.get("/api/workbench/chats/{chat_id}/run-stream")
    async def api_workbench_chat_run_stream(chat_id: str, cursor: int = 0):
        """Reconnect to an existing streamed run without submitting a message."""
        replay_lookup = getattr(run_manager, "get_replayable", run_manager.get)
        run = replay_lookup(chat_id)
        if run is None:
            await asyncio.to_thread(service.settle_chat_running_status, chat_id)
            return JSONResponse(
                {"error": "chat has no running reply", "code": "chat_run_not_found"},
                status_code=404,
            )
        return StreamingResponse(
            run_manager.stream(run, cursor=max(0, int(cursor or 0))),
            media_type="application/x-ndjson",
            headers={"Cache-Control": "no-cache"},
        )

    @router.post("/api/workbench/chats/{chat_id}/guidance")
    async def api_workbench_chat_guidance(
        chat_id: str,
        body_model: api_models.ChatGuidanceBody,
    ):
        body = api_models.body_dict(body_model)
        result = await guidance_service.submit(
            chat_id=chat_id,
            message=str(body.get("message") or "").strip(),
            client_request_id=str(body.get("clientRequestId") or "").strip(),
        )
        if result.status_code != 200:
            return JSONResponse(result.payload, status_code=result.status_code)
        return result.payload

    return {"guide_chat": api_workbench_chat_guidance}
