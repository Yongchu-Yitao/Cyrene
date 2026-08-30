"""Structured-memory HTTP routes owned by the memory Plugin."""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from .structured import (
    MemoryApplicationError,
    MemoryApplicationService,
    MemoryCreateDTO,
    MemoryUpdateDTO,
)
from cyrene.workbench.http.errors import error_response


class _Body(BaseModel):
    model_config = ConfigDict(extra="ignore", coerce_numbers_to_str=True)


class MemoryCreateBody(_Body):
    content: str = Field(min_length=1, max_length=200_000)
    category: Literal["preference", "project", "habit", "fact", "conversation"] = "fact"
    source: Literal["conversation", "knowledge", "manual", "agent", "other"] = "manual"
    confidence: Literal["", "high", "medium", "low"] = ""
    tags: list[Any] | str | None = None


class MemoryUpdateBody(_Body):
    content: str | None = Field(default=None, min_length=1, max_length=200_000)
    category: Literal[
        "preference", "project", "habit", "fact", "conversation", "reflection",
    ] | None = None
    source: Literal["conversation", "knowledge", "manual", "agent", "other"] | None = None
    confidence: Literal["", "high", "medium", "low"] | None = None
    tags: list[Any] | str | None = None
    stale: bool | None = None


def _body_dict(body: _Body) -> dict[str, Any]:
    return body.model_dump(exclude_unset=True)


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
        body_model: MemoryCreateBody, workspace: str = "default"
    ):
        body = _body_dict(body_model)
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
        body_model: MemoryUpdateBody,
        workspace: str = "default",
    ):
        body = _body_dict(body_model)
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
