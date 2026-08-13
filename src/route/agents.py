"""Installed external Agent runtime and settings routes."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from cyrene.extensions import agent_runtime


def _error(exc: Exception, status_code: int = 404) -> JSONResponse:
    kind = str(getattr(exc, "kind", "") or "")
    payload = {"ok": False, "error": kind or str(exc)}
    if kind:
        payload["failureKind"] = kind
        payload["detail"] = str(exc)
    return JSONResponse(payload, status_code=status_code)


def register_agent_routes(router: APIRouter, _bot: Any, _db_path: str) -> None:
    """Install /api/agents runtime and settings adapters on ``router``."""

    @router.get("/api/agents")
    async def api_list_agents():
        return {"agents": [agent_runtime.agent_card(record) for record in agent_runtime.list_agent_installations()]}

    @router.get("/api/agents/{installation_id}")
    async def api_agent_detail(installation_id: str):
        try:
            return {"agent": agent_runtime.get_agent_detail(installation_id)}
        except KeyError as exc:
            return _error(exc)

    @router.patch("/api/agents/{installation_id}/settings")
    async def api_agent_settings(installation_id: str, request: Request):
        try:
            body = await request.json()
            return {"ok": True, "agent": agent_runtime.update_agent_settings(installation_id, body, actor="user")}
        except KeyError as exc:
            return _error(exc)
        except Exception as exc:
            return _error(exc, 400)

    @router.post("/api/agents/{installation_id}/auth/start")
    async def api_agent_auth_start(installation_id: str):
        try:
            return await agent_runtime.auth_agent(installation_id, "authentication")
        except KeyError as exc:
            return _error(exc)
        except Exception as exc:
            return _error(exc, 400)

    @router.post("/api/agents/{installation_id}/auth/logout")
    async def api_agent_auth_logout(installation_id: str):
        try:
            return await agent_runtime.auth_agent(installation_id, "logout")
        except KeyError as exc:
            return _error(exc)
        except Exception as exc:
            return _error(exc, 400)

    @router.post("/api/agents/{installation_id}/probe")
    async def api_agent_probe(installation_id: str):
        try:
            return await agent_runtime.probe_agent(installation_id)
        except KeyError as exc:
            return _error(exc)

    @router.post("/api/agents/{installation_id}/restart")
    async def api_agent_restart(installation_id: str):
        try:
            return await agent_runtime.restart_agent(installation_id)
        except KeyError as exc:
            return _error(exc)

    @router.get("/api/agents/{installation_id}/diagnostics")
    async def api_agent_diagnostics(installation_id: str):
        try:
            return agent_runtime.diagnostics_placeholder(installation_id)
        except KeyError as exc:
            return _error(exc)
