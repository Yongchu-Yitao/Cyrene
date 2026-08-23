"""Thin HTTP adapters for versioned Workbench project memory."""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from cyrene.workbench.project_memory_prompt import (
    ProjectMemoryApplicationError,
    ProjectMemoryApplicationService,
)
from route import schemas as api_models
from route.errors import error_response


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
        body_model: api_models.ProjectMemoryPromptUpdateBody,
    ):
        body = api_models.body_dict(body_model)
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
        body_model: api_models.ProjectMemoryPromptRestoreBody,
    ):
        body = api_models.body_dict(body_model)
        try:
            return await application_service.restore(
                project_id,
                str(body.get("modifiedAt") or ""),
                base_modified_at=str(body.get("baseModifiedAt") or ""),
            )
        except ProjectMemoryApplicationError as exc:
            return _project_memory_error(exc)

    @router.post("/api/workbench/chats/{chat_id}/memory-learning")
    async def trigger_chat_memory_learning(chat_id: str, request: Request):
        if str(chat_id or "").startswith("legacy:"):
            return error_response(
                "legacy chats do not have an exact model-context snapshot",
                409,
                "no_completed_context",
            )
        try:
            body = await request.json()
        except Exception:  # Empty bodies from older clients remain valid.
            body = {}
        language = str(body.get("lang") or "") if isinstance(body, dict) else ""
        try:
            result = await application_service.learn_from_chat(chat_id, language=language)
        except ProjectMemoryApplicationError as exc:
            return _project_memory_error(exc)
        return JSONResponse(result, status_code=202 if result.get("status") == "queued" else 200)


__all__ = ["register_project_memory_routes"]
