"""Application routes contributed by the editable map Plugin pack."""

from __future__ import annotations

from typing import Any

import httpx
from agent.plugin import PluginApplicationContext
from fastapi.responses import JSONResponse

from .service import MapService, map_database


def _amap_key() -> str:
    from cyrene import config

    return str(config.AMAP_API_KEY or "").strip()


def setup_application(context: PluginApplicationContext) -> None:
    service = MapService(map_database(context.data_directory))
    context.provide("maps", service)

    @context.router.get("/api/map/pins")
    async def get_map_pins(session_id: str = ""):
        return service.snapshot(session_id)

    @context.router.get("/api/amap/verify")
    async def verify_amap_key():
        key = _amap_key()
        if not key:
            return {"valid": False, "error": "Key 未配置"}
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
            error = payload.get("info") if isinstance(payload, dict) else "验证失败"
            return {"valid": False, "error": str(error or "验证失败")}
        except (httpx.HTTPError, TypeError, ValueError) as exc:
            return {"valid": False, "error": str(exc)}

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
            return JSONResponse({"error": "Key 未配置"}, status_code=400)
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
                error = payload.get("info") if isinstance(payload, dict) else "路线请求失败"
                return JSONResponse(
                    {"error": str(error or "路线请求失败")},
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
        except (httpx.HTTPError, TypeError, ValueError, KeyError, IndexError) as exc:
            return JSONResponse({"error": str(exc)}, status_code=502)


__all__ = ["setup_application"]
