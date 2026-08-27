"""Profile, reset, budget, and MCP HTTP adapters."""

from __future__ import annotations

import json

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from cyrene.runtime.profile_data_service import (
    ProfileDataApplicationService,
    ProfileDataError,
)


def _error_response(exc: ProfileDataError) -> JSONResponse:
    return JSONResponse(
        {"error": str(exc), **exc.payload},
        status_code=exc.status_code,
    )


def register_profile_routes(
    router: APIRouter,
    service: ProfileDataApplicationService,
) -> None:
    @router.put("/api/profile")
    async def api_update_profile(request: Request):
        try:
            return await service.update_profile(await request.json())
        except ProfileDataError as exc:
            return _error_response(exc)

    @router.post("/api/settings/reset-data")
    async def api_reset_data(request: Request):
        try:
            body = await request.json()
        except (ValueError, json.JSONDecodeError):
            body = {}
        if not isinstance(body, dict) or body.get("confirmation") != "RESET CYRENE DATA":
            return JSONResponse(
                {
                    "error": "explicit reset confirmation is required",
                    "code": "reset_confirmation_required",
                },
                status_code=400,
            )
        try:
            return await service.reset()
        except ProfileDataError as exc:
            return _error_response(exc)


def register_budget_routes(
    router: APIRouter,
    service: ProfileDataApplicationService,
) -> None:
    @router.get("/api/settings/budget/stats")
    async def api_budget_stats():
        return await service.budget_stats()

    @router.get("/api/budget/status")
    async def api_budget_status():
        try:
            return await service.budget_status()
        except ProfileDataError as exc:
            return _error_response(exc)


def register_mcp_routes(router: APIRouter) -> None:
    @router.get("/api/settings/mcp")
    async def api_get_mcp_servers():
        from agent.plugin.mcp_service import get_mcp_service

        service = get_mcp_service()
        return {
            "servers": service.status(),
            "configs": service.configs(redacted=True),
        }

    @router.put("/api/settings/mcp")
    async def api_update_mcp_servers(request: Request):
        from agent.plugin.mcp_service import get_mcp_service

        payload = await request.json()
        servers = payload.get("servers", []) if isinstance(payload, dict) else []
        service = get_mcp_service()
        try:
            status = await service.replace_configs(
                servers,
                merge_redacted=True,
            )
        except ValueError as exc:
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)
        return {
            "ok": True,
            "servers": status,
            "configs": service.configs(redacted=True),
        }


def register_profile_data_routes(
    router: APIRouter,
    service: ProfileDataApplicationService,
) -> None:
    register_profile_routes(router, service)
    register_budget_routes(router, service)
    register_mcp_routes(router)
