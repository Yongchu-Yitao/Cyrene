"""Shared request helpers for project-plugin routes."""

from __future__ import annotations

from fastapi import Request
from fastapi.responses import JSONResponse


def plugin_error(exc: Exception, status: int = 400) -> JSONResponse:
    return JSONResponse(
        {"error": str(exc), "code": "plugin_error"}, status_code=status
    )


def project_id(request: Request, body: dict | None = None) -> str:
    source = body or {}
    return str(
        source.get("project_id")
        or source.get("projectId")
        or request.query_params.get("project_id")
        or request.query_params.get("projectId")
        or ""
    ).strip()


__all__ = ["plugin_error", "project_id"]
