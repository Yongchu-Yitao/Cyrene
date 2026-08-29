"""Installed external Agent runtime and settings routes."""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from cyrene.plugins.builtin.cyrene_extensions import extension_agent_runtime as agent_runtime
from cyrene.localization import localized


logger = logging.getLogger(__name__)


def _error(
    exc: Exception,
    status_code: int,
    *,
    code: str,
    en: str,
    zh: str,
) -> JSONResponse:
    kind = str(getattr(exc, "kind", "") or "")
    message = localized(en, zh)
    logger.info(
        "Agent route operation failed [%s]",
        code,
        exc_info=(type(exc), exc, exc.__traceback__),
    )
    payload = {
        "ok": False,
        "code": code,
        "error": kind or message,
        "message": message,
    }
    if kind:
        payload["failureKind"] = kind
    return JSONResponse(payload, status_code=status_code)


def _not_found(exc: Exception) -> JSONResponse:
    return _error(
        exc,
        404,
        code="agent_installation_not_found",
        en="The agent installation was not found.",
        zh="未找到该智能体安装记录。",
    )


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
            return _not_found(exc)

    @router.patch("/api/agents/{installation_id}/settings")
    async def api_agent_settings(installation_id: str, request: Request):
        try:
            body = await request.json()
            if not isinstance(body, dict):
                raise ValueError("agent settings payload must be an object")
            return {"ok": True, "agent": agent_runtime.update_agent_settings(installation_id, body, actor="user")}
        except KeyError as exc:
            return _not_found(exc)
        except Exception as exc:
            return _error(
                exc,
                400,
                code="agent_settings_invalid",
                en="The agent settings are invalid.",
                zh="智能体设置无效。",
            )

    @router.post("/api/agents/{installation_id}/auth/start")
    async def api_agent_auth_start(installation_id: str):
        try:
            return await agent_runtime.auth_agent(installation_id, "authentication")
        except KeyError as exc:
            return _not_found(exc)
        except Exception as exc:
            return _error(
                exc,
                400,
                code="agent_auth_failed",
                en="The agent authentication operation failed.",
                zh="智能体认证操作失败。",
            )

    @router.post("/api/agents/{installation_id}/auth/logout")
    async def api_agent_auth_logout(installation_id: str):
        try:
            return await agent_runtime.auth_agent(installation_id, "logout")
        except KeyError as exc:
            return _not_found(exc)
        except Exception as exc:
            return _error(
                exc,
                400,
                code="agent_auth_failed",
                en="The agent authentication operation failed.",
                zh="智能体认证操作失败。",
            )

    @router.post("/api/agents/{installation_id}/probe")
    async def api_agent_probe(installation_id: str):
        try:
            return await agent_runtime.probe_agent(installation_id)
        except KeyError as exc:
            return _not_found(exc)
        except Exception as exc:
            return _error(
                exc,
                500,
                code="agent_probe_failed",
                en="The agent health check failed.",
                zh="智能体健康检查失败。",
            )

    @router.post("/api/agents/{installation_id}/restart")
    async def api_agent_restart(installation_id: str):
        try:
            return await agent_runtime.restart_agent(installation_id)
        except KeyError as exc:
            return _not_found(exc)
        except Exception as exc:
            return _error(
                exc,
                500,
                code="agent_restart_failed",
                en="The agent could not be restarted.",
                zh="无法重启该智能体。",
            )

    @router.get("/api/agents/{installation_id}/diagnostics")
    async def api_agent_diagnostics(installation_id: str):
        try:
            return agent_runtime.diagnostics_placeholder(installation_id)
        except KeyError as exc:
            return _not_found(exc)
        except Exception as exc:
            return _error(
                exc,
                500,
                code="agent_diagnostics_failed",
                en="Agent diagnostics are unavailable.",
                zh="智能体诊断信息不可用。",
            )
