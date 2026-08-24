"""HTTP settings adapter for media providers and execution policy."""

from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from cyrene.media.model_catalog import (
    SUPPORTED_MODEL_PROVIDERS,
    provider_model_catalog,
)
from cyrene.media.settings import (
    get_media_settings,
    merge_media_settings_update,
    public_media_settings,
)
from cyrene.runtime.config_store import SettingsRevisionConflict
from cyrene.runtime.settings_store import get_revision


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
            return JSONResponse(
                {"error": "unknown media provider"},
                status_code=404,
            )
        settings = await asyncio.to_thread(get_media_settings)
        return await provider_model_catalog(normalized, settings)

    @router.put("/api/settings/media")
    async def api_update_media_settings(request: Request):
        body = await request.json()
        if not isinstance(body, dict):
            return JSONResponse({"error": "media settings must be an object"}, status_code=400)
        expected = body.pop("expected_revision", None)
        if expected is not None and (not isinstance(expected, int) or isinstance(expected, bool)):
            return JSONResponse({"error": "expected_revision must be an integer"}, status_code=400)
        try:
            await asyncio.to_thread(
                merge_media_settings_update,
                body,
                expected_revision=expected,
            )
        except SettingsRevisionConflict as exc:
            return JSONResponse(
                {
                    "error": str(exc),
                    "expected_revision": exc.expected,
                    "revision": exc.actual,
                },
                status_code=409,
            )
        except (TypeError, ValueError) as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        return await _response()


__all__ = ["register_media_settings_routes"]
