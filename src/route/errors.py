"""Shared HTTP error handling for JSON API routes."""

from __future__ import annotations

import logging
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

logger = logging.getLogger(__name__)


def error_response(message: str, status_code: int, code: str, **details: Any) -> JSONResponse:
    payload: dict[str, Any] = {"error": message, "code": code}
    payload.update(details)
    return JSONResponse(payload, status_code=status_code)


def install_api_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(RequestValidationError)
    async def _request_validation_error(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        fields = [
            {
                "field": ".".join(str(part) for part in error.get("loc", ()) if part != "body"),
                "message": str(error.get("msg") or "invalid value"),
                "type": str(error.get("type") or "validation_error"),
            }
            for error in exc.errors()
        ]
        return error_response(
            "invalid request",
            400,
            "validation_error",
            details=fields,
        )

    @app.exception_handler(Exception)
    async def _unhandled_api_error(request: Request, exc: Exception) -> JSONResponse:
        logger.error(
            "Unhandled HTTP request error: %s %s",
            request.method,
            request.url.path,
            exc_info=(type(exc), exc, exc.__traceback__),
        )
        return error_response(
            "internal server error",
            500,
            "internal_server_error",
        )
