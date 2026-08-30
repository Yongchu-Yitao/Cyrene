"""Application routes contributed by the editable map Plugin pack."""

from __future__ import annotations

import logging
from typing import Any

import httpx
from cyrene.plugins.context import PluginApplicationContext
from fastapi.responses import JSONResponse

from cyrene.localization import localized
from .service import MapService, map_database

logger = logging.getLogger(__name__)


def _amap_key() -> str:
    from cyrene.platform import config_store

    return str(config_store.get_env("AMAP_API_KEY", "") or "").strip()


def setup_application(context: PluginApplicationContext) -> None:
    service = MapService(map_database(context.data_directory))
    context.provide("maps", service)
    context.on_startup(service.initialize)
    context.on_shutdown(service.shutdown)
    context.expose_frontend("map")
    from cyrene.platform.settings_service import (
        PluginSettingsContribution,
        SettingControlSpec,
    )

    context.provide(
        "map_settings",
        PluginSettingsContribution(controls=(
            SettingControlSpec("general.map_provider", "general", "current_ui", "cyrene.ui.inspect", "R1"),
            SettingControlSpec("general.amap_api_key", "general", "user_ceremony", "cyrene.secret.input", "R3", secret=True),
        )),
    )

    @context.router.get("/api/map/pins")
    async def get_map_pins(session_id: str = ""):
        return service.snapshot(session_id)

    @context.router.get("/api/amap/verify")
    async def verify_amap_key():
        key = _amap_key()
        if not key:
            return {
                "valid": False,
                "error": localized(
                    "The Amap API key is not configured.",
                    "高德地图 API Key 未配置。",
                ),
            }
        try:
            async with httpx.AsyncClient() as client:
                response = await client.get(
                    "https://restapi.amap.com/v3/direction/driving",
                    params={
                        "key": key,
                        "origin": "116.4,39.9",
                        "destination": "116.5,39.9",
                    },
                    timeout=10,
                )
            payload = response.json()
            if isinstance(payload, dict) and payload.get("status") == "1":
                return {"valid": True}
            return {
                "valid": False,
                "error": localized(
                    "The Amap API key could not be verified.",
                    "无法验证高德地图 API Key。",
                ),
            }
        except (httpx.HTTPError, TypeError, ValueError):
            logger.warning("Amap key verification failed", exc_info=True)
            return {
                "valid": False,
                "error": localized(
                    "The Amap API key could not be verified.",
                    "无法验证高德地图 API Key。",
                ),
            }

    @context.router.get("/api/amap/direction")
    async def amap_direction(
        fromLng: float,
        fromLat: float,
        toLng: float,
        toLat: float,
        profile: str = "driving",
    ):
        key = _amap_key()
        if not key:
            return JSONResponse(
                {
                    "error": localized(
                        "The Amap API key is not configured.",
                        "高德地图 API Key 未配置。",
                    ),
                    "code": "amap_key_not_configured",
                },
                status_code=400,
            )
        amap_profile = {
            "driving": "driving",
            "walking": "walking",
            "cycling": "bicycling",
        }.get(profile, "driving")
        try:
            async with httpx.AsyncClient() as client:
                response = await client.get(
                    f"https://restapi.amap.com/v3/direction/{amap_profile}",
                    params={
                        "key": key,
                        "origin": f"{fromLng},{fromLat}",
                        "destination": f"{toLng},{toLat}",
                        "extensions": "base",
                        "strategy": "0",
                    },
                    timeout=15,
                )
            payload: Any = response.json()
            if not isinstance(payload, dict) or payload.get("status") != "1":
                return JSONResponse(
                    {
                        "error": localized(
                            "The route request failed.",
                            "路线请求失败。",
                        ),
                        "code": "amap_route_failed",
                    },
                    status_code=502,
                )
            paths = payload.get("route", {}).get("paths", [])
            steps = paths[0].get("steps", []) if paths else []
            coordinates: list[list[float]] = []
            for step in steps:
                if not isinstance(step, dict):
                    continue
                for point in str(step.get("polyline") or "").split(";"):
                    if not point or "," not in point:
                        continue
                    lng_value, lat_value = point.split(",", 1)
                    coordinates.append([float(lng_value), float(lat_value)])
            return {"coordinates": coordinates}
        except (httpx.HTTPError, TypeError, ValueError, KeyError, IndexError):
            logger.warning("Amap route request failed", exc_info=True)
            return JSONResponse(
                {
                    "error": localized(
                        "The route request failed.",
                        "路线请求失败。",
                    ),
                    "code": "amap_route_failed",
                },
                status_code=502,
            )


__all__ = ["setup_application"]
