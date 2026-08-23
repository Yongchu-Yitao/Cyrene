"""Thin FastAPI adapters for workspace-scoped Workbench memory."""

from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from cyrene.workbench.memory import (
    MemoryApplicationError,
    MemoryApplicationService,
    MemoryCreateDTO,
    MemoryUpdateDTO,
)
from route import schemas as api_models
from route.errors import error_response


def _memory_error(exc: MemoryApplicationError) -> JSONResponse:
    if exc.code:
        return error_response(str(exc), exc.status_code, exc.code)
    return JSONResponse({"error": str(exc)}, status_code=exc.status_code)


def register_workbench_memory_routes(
    router: APIRouter,
    application_service: MemoryApplicationService,
) -> None:
    @router.get("/api/workbench/memory")
    async def wb_list_memory(workspace: str = "default", include_hidden: bool = False):
        try:
            return application_service.list(workspace, include_hidden=include_hidden)
        except MemoryApplicationError as exc:
            return _memory_error(exc)

    @router.post("/api/workbench/memory")
    async def wb_create_memory(
        body_model: api_models.MemoryCreateBody, workspace: str = "default"
    ):
        body = api_models.body_dict(body_model)
        dto = MemoryCreateDTO(
            content=str(body.get("content") or ""),
            category=str(body.get("category") or ""),
            source=str(body.get("source") or "manual"),
            confidence=str(body.get("confidence") or ""),
            tags=body.get("tags"),
        )
        try:
            return application_service.create(workspace, dto)
        except MemoryApplicationError as exc:
            return _memory_error(exc)

    @router.patch("/api/workbench/memory/{mem_id}")
    async def wb_update_memory(
        mem_id: str,
        body_model: api_models.MemoryUpdateBody,
        workspace: str = "default",
    ):
        body = api_models.body_dict(body_model)
        dto = MemoryUpdateDTO(body, frozenset(body))
        try:
            return application_service.update(workspace, mem_id, dto)
        except MemoryApplicationError as exc:
            return _memory_error(exc)

    @router.delete("/api/workbench/memory/{mem_id}")
    async def wb_delete_memory(mem_id: str, workspace: str = "default"):
        try:
            return application_service.delete(workspace, mem_id)
        except MemoryApplicationError as exc:
            return _memory_error(exc)


__all__ = ["register_workbench_memory_routes"]
