"""Control project, chat, and chat-approval routes."""

from typing import Any

from fastapi import APIRouter, Query
from fastapi.responses import JSONResponse

from cyrene.workbench.control_services import ControlProjectQueryService
from route import schemas as workbench_schemas
from route.control_schemas import (
    ControlApprovalResponse, ControlApprovalResponseRequest, ControlChatCreateRequest,
    ControlChatListResponse, ControlChatMessageRequest, ControlChatResponse,
    ControlProjectListResponse, ControlProjectSummary, ControlRunAccepted,
)
from route.control_routes.common import COMMON_ERRORS, chat_detail, chat_summary, control_call


def register_project_chat_routes(
    router: APIRouter,
    service: ControlProjectQueryService,
    *,
    run_manager: Any,
) -> None:
    @router.get("/v1/control/projects", response_model=ControlProjectListResponse, tags=["Control"], operation_id="control_v1_list_projects")
    async def control_list_projects() -> ControlProjectListResponse:
        projects = await service.list_projects()
        return ControlProjectListResponse(projects=[
            ControlProjectSummary(
                id=str(raw.get("id") or ""), name=str(raw.get("name") or ""),
                status=str(raw.get("status") or "active"), updated_at=str(raw.get("updatedAt") or ""),
                task_count=len([item for item in raw.get("sessions") or [] if isinstance(item, dict) and str(item.get("kind") or "task") == "task"]),
            ) for raw in projects
        ])

    @router.get("/v1/control/chats", response_model=ControlChatListResponse, responses=COMMON_ERRORS, tags=["Control"], operation_id="control_v1_list_chats")
    async def control_list_chats(project_id: str = Query(default="", max_length=200)):
        result = await control_call(service.list_chats(project_id))
        if isinstance(result, JSONResponse):
            return result
        return ControlChatListResponse(chats=[chat_summary(raw, run_manager) for raw in result if not str(raw.get("id") or "").startswith("legacy:")])

    @router.post("/v1/control/chats", response_model=ControlChatResponse, status_code=201, responses=COMMON_ERRORS, tags=["Control"], operation_id="control_v1_create_chat")
    async def control_create_chat(request: ControlChatCreateRequest):
        result = await control_call(service.create_chat(workbench_schemas.ChatCreateBody(project=request.project_id, title=request.title)))
        return result if isinstance(result, JSONResponse) else ControlChatResponse(chat=chat_detail(result, run_manager))

    @router.get("/v1/control/chats/{chat_id}", response_model=ControlChatResponse, responses=COMMON_ERRORS, tags=["Control"], operation_id="control_v1_get_chat")
    async def control_get_chat(chat_id: str):
        result = await control_call(service.get_chat(chat_id))
        return result if isinstance(result, JSONResponse) else ControlChatResponse(chat=chat_detail(result, run_manager))

    @router.post("/v1/control/chats/{chat_id}/messages", response_model=ControlRunAccepted, status_code=202, responses=COMMON_ERRORS, tags=["Control"], operation_id="control_v1_send_chat_message")
    async def control_send_chat_message(chat_id: str, request: ControlChatMessageRequest):
        return await control_call(service.send_chat(chat_id, {"message": request.message, "mode": request.permission_mode, "lang": request.language, "stream": True}))

    @router.post("/v1/control/chats/{chat_id}/approvals/{question_id}/responses", response_model=ControlApprovalResponse, responses=COMMON_ERRORS, tags=["Control"], operation_id="control_v1_respond_approval")
    async def control_respond_approval(chat_id: str, question_id: str, request: ControlApprovalResponseRequest):
        result = await control_call(service.answer_chat(chat_id, workbench_schemas.AnswerBody(question_id=question_id, answer=request.answer, mode=request.permission_mode)))
        if isinstance(result, JSONResponse):
            return result
        return ControlApprovalResponse(accepted=True, chat_id=chat_id, question_id=question_id, awaiting_user=bool(result.get("awaitingUser")))


__all__ = ["register_project_chat_routes"]
