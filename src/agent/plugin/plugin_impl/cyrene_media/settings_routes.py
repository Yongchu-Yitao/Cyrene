"""HTTP settings adapter for media providers and execution policy."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from fastapi import APIRouter, Request

from .model_catalog import (
    SUPPORTED_MODEL_PROVIDERS,
    provider_model_catalog,
)
from .settings import (
    get_media_settings,
    merge_media_settings_update,
    public_media_settings,
)
from cyrene.runtime.config_store import SettingsRevisionConflict
from cyrene.runtime.settings_store import get_revision
from route.errors import localized_error_response


logger = logging.getLogger(__name__)


def _response_sync() -> dict[str, Any]:
    return {**public_media_settings(get_media_settings()), "revision": get_revision()}


async def _response() -> dict[str, Any]:
    return await asyncio.to_thread(_response_sync)


def register_media_settings_routes(router: APIRouter) -> None:
    @router.get("/api/settings/media")
    async def api_get_media_settings():
        return await _response()

    @router.get("/api/settings/media/providers/{provider}/models")
    async def api_get_media_provider_models(provider: str):
        normalized = str(provider or "").strip().lower()
        if normalized not in SUPPORTED_MODEL_PROVIDERS:
            return localized_error_response(
                "unknown media provider",
                "未知的媒体服务商",
                404,
                "media_provider_unknown",
            )
        settings = await asyncio.to_thread(get_media_settings)
        return await provider_model_catalog(normalized, settings)

    @router.put("/api/settings/media")
    async def api_update_media_settings(request: Request):
        try:
            body = await request.json()
        except ValueError:
            return localized_error_response(
                "request body must be valid JSON",
                "请求体必须是有效的 JSON。",
                400,
                "invalid_json",
            )
        if not isinstance(body, dict):
            return localized_error_response(
                "media settings must be an object",
                "媒体设置必须是对象。",
                400,
                "invalid_media_settings",
            )
        expected = body.pop("expected_revision", None)
        if expected is not None and (not isinstance(expected, int) or isinstance(expected, bool)):
            return localized_error_response(
                "expected_revision must be an integer",
                "expected_revision 必须是整数。",
                400,
                "invalid_settings_revision",
            )
        try:
            await asyncio.to_thread(
                merge_media_settings_update,
                body,
                expected_revision=expected,
            )
        except SettingsRevisionConflict as exc:
            return localized_error_response(
                "Media settings were changed by another client.",
                "媒体设置已被其他客户端更改。",
                409,
                "settings_revision_conflict",
                expected_revision=exc.expected,
                revision=exc.actual,
            )
        except (TypeError, ValueError) as exc:
            logger.info("Invalid media settings update", exc_info=True)
            return localized_error_response(
                "Invalid media settings.",
                "媒体设置无效。",
                400,
                "invalid_media_settings",
            )
        return await _response()


__all__ = ["register_media_settings_routes"]
