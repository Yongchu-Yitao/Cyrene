"""Project-memory HTTP routes owned by the memory Plugin."""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from .project_memory import (
    ProjectMemoryApplicationError,
    ProjectMemoryApplicationService,
)
from cyrene.workbench.http.errors import error_response


class _Body(BaseModel):
    model_config = ConfigDict(extra="ignore", coerce_numbers_to_str=True)


class ProjectMemoryPromptUpdateBody(_Body):
    prompt: str = Field(default="", max_length=16_000)
    baseModifiedAt: str = Field(default="", max_length=100)


class ProjectMemoryPromptRestoreBody(_Body):
    modifiedAt: str = Field(min_length=1, max_length=100)
    baseModifiedAt: str = Field(default="", max_length=100)


class MemoryLearningBody(_Body):
    lang: Literal["en", "zh"]


def _body_dict(body: _Body) -> dict:
    return body.model_dump(exclude_unset=True)


def _project_memory_error(exc: ProjectMemoryApplicationError) -> JSONResponse:
    if exc.code:
        return error_response(str(exc), exc.status_code, exc.code)
    return JSONResponse({"error": str(exc)}, status_code=exc.status_code)


def register_project_memory_routes(
    router: APIRouter,
    application_service: ProjectMemoryApplicationService,
) -> None:
    @router.get("/api/projects/{project_id}/memory-prompt")
    async def get_project_memory_prompt(project_id: str, include_memories: bool = True):
        try:
            return await application_service.get(project_id, include_memories=include_memories)
        except ProjectMemoryApplicationError as exc:
            return _project_memory_error(exc)

    @router.patch("/api/projects/{project_id}/memory-prompt")
    async def update_project_memory_prompt(
        project_id: str,
        body_model: ProjectMemoryPromptUpdateBody,
    ):
        body = _body_dict(body_model)
        try:
            return await application_service.update(
                project_id,
                str(body.get("prompt") or ""),
                base_modified_at=str(body.get("baseModifiedAt") or ""),
            )
        except ProjectMemoryApplicationError as exc:
            return _project_memory_error(exc)

    @router.post("/api/projects/{project_id}/memory-prompt/restore")
    async def restore_project_memory_prompt(
        project_id: str,
        body_model: ProjectMemoryPromptRestoreBody,
    ):
        body = _body_dict(body_model)
        try:
            return await application_service.restore(
                project_id,
                str(body.get("modifiedAt") or ""),
                base_modified_at=str(body.get("baseModifiedAt") or ""),
            )
        except ProjectMemoryApplicationError as exc:
            return _project_memory_error(exc)

    @router.post("/api/workbench/chats/{chat_id}/memory-learning")
    async def trigger_chat_memory_learning(
        chat_id: str,
        body_model: MemoryLearningBody,
    ):
        try:
            result = await application_service.learn_from_chat(
                chat_id,
                language=body_model.lang,
            )
        except ProjectMemoryApplicationError as exc:
            return _project_memory_error(exc)
        return JSONResponse(result, status_code=202 if result.get("status") == "queued" else 200)


__all__ = ["register_project_memory_routes"]
