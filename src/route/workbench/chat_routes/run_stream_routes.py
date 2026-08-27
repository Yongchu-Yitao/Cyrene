from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter
from fastapi.responses import StreamingResponse

from cyrene.workbench.chat_guidance_service import (
    ChatGuidanceApplicationService,
    ChatGuidanceDependencies,
)
from route import schemas as api_models
from route.errors import localized_error_response
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
            return localized_error_response(
                "This chat has no running reply.",
                "此对话当前没有正在生成的回复。",
                404,
                "chat_run_not_found",
            )
        return StreamingResponse(
            run_manager.stream(run, cursor=max(0, int(cursor or 0))),
            media_type="application/x-ndjson",
            headers={"Cache-Control": "no-cache"},
        )

    @router.post("/api/workbench/chats/{chat_id}/interrupt")
    async def api_workbench_chat_interrupt(chat_id: str):
        """Cancel only this conversation's active Plugin-kernel run."""

        interrupted = run_manager.interrupt(str(chat_id))
        return {
            "ok": True,
            "interrupted": bool(interrupted),
            "chatId": str(chat_id),
        }

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
            code = str(
                result.payload.get("code")
                or ("chat_not_found" if result.status_code == 404 else "guidance_failed")
            )
            messages = {
                "guidance_empty": (
                    "A guidance message is required.",
                    "请输入指导消息。",
                ),
                "chat_not_running": (
                    "This chat has no running reply to guide.",
                    "此对话当前没有可指导的运行中回复。",
                ),
                "guidance_persistence_failed": (
                    "The guidance could not be saved. Please try again.",
                    "无法保存指导消息，请重试。",
                ),
                "chat_not_found": ("Chat not found.", "未找到对话。"),
            }
            en, zh = messages.get(
                code,
                ("The guidance request failed.", "指导请求失败。"),
            )
            return localized_error_response(
                en,
                zh,
                result.status_code,
                code,
            )
        return result.payload

    return {"guide_chat": api_workbench_chat_guidance}
