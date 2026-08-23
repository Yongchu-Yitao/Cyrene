"""Shared validation and explicit error mapping for library routes."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, Awaitable

from fastapi import HTTPException
from fastapi.responses import JSONResponse

from cyrene.knowledge.library_services import LibraryRequestError
from cyrene.knowledge.workspace import WorkspaceNotFoundError, WorkspaceRequiredError


def require_library_workspace(workspace: str, resolver: Callable[[str], str]) -> None:
    try:
        resolver(workspace)
    except WorkspaceRequiredError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except WorkspaceNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


async def library_call(awaitable: Awaitable[Any]) -> Any:
    try:
        return await awaitable
    except LibraryRequestError as exc:
        return JSONResponse({"error": str(exc)}, status_code=exc.status_code)


def bool_param(value: str | bool | None) -> bool | None:
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return value
    return str(value).lower() in {"1", "true", "yes", "on"}


__all__ = ["bool_param", "library_call", "require_library_workspace"]
