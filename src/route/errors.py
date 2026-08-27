"""Shared HTTP error handling for JSON API routes."""

from __future__ import annotations

import logging
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from cyrene.localization import app_language, localized

logger = logging.getLogger(__name__)


def error_response(message: str, status_code: int, code: str, **details: Any) -> JSONResponse:
    payload: dict[str, Any] = {"error": message, "code": code}
    payload.update(details)
    return JSONResponse(payload, status_code=status_code)


def localized_error_payload(
    en: str,
    zh: str,
    code: str,
    *,
    language: Any = None,
    **details: Any,
) -> dict[str, Any]:
    """Build a stable error envelope without exposing exception text."""

    payload: dict[str, Any] = {
        "error": localized(en, zh, language=app_language(language)),
        "code": code,
    }
    payload.update(details)
    return payload


def localized_error_response(
    en: str,
    zh: str,
    status_code: int,
    code: str,
    *,
    language: Any = None,
    **details: Any,
) -> JSONResponse:
    return JSONResponse(
        localized_error_payload(
            en,
            zh,
            code,
            language=language,
            **details,
        ),
        status_code=status_code,
    )


def install_api_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(RequestValidationError)
    async def _request_validation_error(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        fields = [
            {
                "field": ".".join(str(part) for part in error.get("loc", ()) if part != "body"),
                "message": localized("Invalid value.", "值无效。"),
                "type": str(error.get("type") or "validation_error"),
            }
            for error in exc.errors()
        ]
        return error_response(
            localized("Invalid request.", "请求无效。"),
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
            localized("Internal server error.", "服务器内部错误。"),
            500,
            "internal_server_error",
        )
