"""Core configuration HTTP adapters."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from cyrene.platform.config_integration_service import (
    ConfigIntegrationApplicationService,
    ConfigIntegrationError,
)
from cyrene.workbench.http.errors import localized_error_response


logger = logging.getLogger(__name__)


def _error_response(
    exc: ConfigIntegrationError,
    *,
    en: str,
    zh: str,
    code: str,
) -> JSONResponse:
    """Return a localized boundary error without forwarding exception text."""

    logger.info("Settings request failed [%s]", code, exc_info=True)
    details = {
        key: exc.payload[key]
        for key in (
            "revision",
            "expected_revision",
            "actual_revision",
            "settings",
        )
        if key in exc.payload
    }
    if exc.status_code == 409:
        en = "Settings were changed by another client."
        zh = "设置已被其他客户端更改。"
        code = "settings_revision_conflict"
    return localized_error_response(
        en,
        zh,
        exc.status_code,
        code,
        **details,
    )


def _invalid_json_response() -> JSONResponse:
    return localized_error_response(
        "request body must be valid JSON",
        "请求体必须是有效的 JSON。",
        400,
        "invalid_json",
    )


def register_config_read_routes(
    router: APIRouter,
    service: ConfigIntegrationApplicationService,
) -> None:
    @router.get("/api/settings/config")
    async def api_get_config():
        return service.config()

    @router.get("/api/settings/storage")
    async def api_get_storage():
        return await service.storage()



def register_namespace_routes(
    router: APIRouter,
    service: ConfigIntegrationApplicationService,
) -> None:
    @router.get("/api/settings/namespaces/{namespace}")
    async def api_get_settings_namespace(namespace: str):
        try:
            return await service.read_namespace(namespace)
        except ConfigIntegrationError as exc:
            return _error_response(
                exc,
                en="Unable to load settings.",
                zh="无法加载设置。",
                code="settings_read_failed",
            )

    @router.put("/api/settings/namespaces/{namespace}")
    async def api_update_settings_namespace(namespace: str, request: Request):
        try:
            body = await request.json()
        except ValueError:
            return _invalid_json_response()
        if not isinstance(body, dict):
            return localized_error_response(
                "settings update must be an object",
                "设置更新内容必须是对象。",
                400,
                "invalid_settings",
            )
        try:
            return await service.update_namespace(namespace, body)
        except ConfigIntegrationError as exc:
            return _error_response(
                exc,
                en="Unable to update settings.",
                zh="无法更新设置。",
                code="settings_update_failed",
            )

    @router.put("/api/settings/config")
    async def api_update_config(request: Request):
        try:
            body = await request.json()
        except ValueError:
            return _invalid_json_response()
        if not isinstance(body, dict):
            return localized_error_response(
                "settings update must be an object",
                "设置更新内容必须是对象。",
                400,
                "invalid_settings",
            )
        try:
            return await service.update_config(body)
        except ConfigIntegrationError as exc:
            return _error_response(
                exc,
                en="Unable to update settings.",
                zh="无法更新设置。",
                code="settings_update_failed",
            )



def register_config_integration_routes(
    router: APIRouter,
    service: ConfigIntegrationApplicationService,
) -> None:
    register_config_read_routes(router, service)
    register_namespace_routes(router, service)
