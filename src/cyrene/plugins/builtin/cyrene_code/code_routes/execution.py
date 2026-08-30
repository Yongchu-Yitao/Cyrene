"""HTTP adapters for project workspace actions and managed executions."""

from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel, Field

from cyrene.workbench.http.errors import error_response
from ..workspace_execution import WorkspaceExecutionError, WorkspaceExecutionService


class ExecutionStartBody(BaseModel):
    projectId: str = Field(min_length=1, max_length=200)
    actionId: str = Field(min_length=1, max_length=160)
    currentPath: str = Field(default="", max_length=4096)
    chatId: str = Field(default="", max_length=200)
    goalId: str = Field(default="", max_length=200)


def _error(exc: WorkspaceExecutionError):
    return error_response(exc.message, exc.status_code, exc.code)


def register_execution_routes(
    router: APIRouter, service: WorkspaceExecutionService
) -> None:
    @router.get("/workspace-actions")
    async def list_workspace_actions(projectId: str, path: str = ""):
        try:
            return await service.discover(projectId, path)
        except WorkspaceExecutionError as exc:
            return _error(exc)

    @router.get("/executions")
    async def list_executions(projectId: str):
        try:
            return await service.list(projectId)
        except WorkspaceExecutionError as exc:
            return _error(exc)

    @router.get("/workspace-review")
    async def workspace_review(projectId: str, chatId: str = ""):
        try:
            return await service.review(projectId, chatId)
        except WorkspaceExecutionError as exc:
            return _error(exc)

    @router.post("/executions", status_code=201)
    async def start_execution(body: ExecutionStartBody):
        try:
            return {"execution": await service.start(
                body.projectId, body.actionId, current_path=body.currentPath,
                chat_id=body.chatId, goal_id=body.goalId,
            )}
        except WorkspaceExecutionError as exc:
            return _error(exc)

    @router.get("/executions/{execution_id}")
    async def get_execution(execution_id: str):
        try:
            return {"execution": await service.refresh(execution_id)}
        except WorkspaceExecutionError as exc:
            return _error(exc)

    @router.post("/executions/{execution_id}/stop")
    async def stop_execution(execution_id: str):
        try:
            return {"execution": await service.stop(execution_id)}
        except WorkspaceExecutionError as exc:
            return _error(exc)

    @router.post("/executions/{execution_id}/restart")
    async def restart_execution(execution_id: str):
        try:
            return {"execution": await service.restart(execution_id)}
        except WorkspaceExecutionError as exc:
            return _error(exc)

    @router.post("/executions/{execution_id}/claim")
    async def claim_execution(execution_id: str):
        try:
            return {"execution": await service.claim(execution_id)}
        except WorkspaceExecutionError as exc:
            return _error(exc)


__all__ = ["ExecutionStartBody", "register_execution_routes"]
