"""Versioned Control API adapters for Conversation-native Goals."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from cyrene.core.plugin import application_plugin_service


def _service() -> Any:
    service = application_plugin_service("goal")
    if service is None:
        raise RuntimeError("Conversation Goal service is unavailable")
    return service


def _error(exc: Exception) -> JSONResponse:
    if isinstance(exc, LookupError):
        status, code = 404, "goal_not_found"
    elif isinstance(exc, ValueError):
        status, code = 409, "invalid_goal_transition"
    else:
        status, code = 503, "goal_service_unavailable"
    return JSONResponse(
        {"ok": False, "code": code, "error": str(exc)},
        status_code=status,
    )


async def _body(request: Request) -> dict[str, Any]:
    try:
        value = await request.json()
    except Exception:
        value = {}
    return dict(value) if isinstance(value, Mapping) else {}


def register_goal_routes(router: APIRouter) -> None:
    @router.get("/v1/control/chats/{chat_id}/goal", tags=["Control"])
    async def control_get_goal(chat_id: str):
        try:
            goal = await _service().get(chat_id)
            return {"ok": True, "goal": _service().public(goal) if goal else None}
        except Exception as exc:
            return _error(exc)

    @router.patch("/v1/control/chats/{chat_id}/goal", tags=["Control"])
    async def control_update_goal(chat_id: str, request: Request):
        try:
            goal = await _service().update(chat_id, await _body(request))
            return {"ok": True, "goal": _service().public(goal)}
        except Exception as exc:
            return _error(exc)

    @router.post("/v1/control/chats/{chat_id}/goal/confirm", tags=["Control"])
    async def control_confirm_goal(chat_id: str, request: Request):
        try:
            goal = await _service().confirm(chat_id, await _body(request))
            return {"ok": True, "goal": _service().public(goal)}
        except Exception as exc:
            return _error(exc)

    def action_route(action: str) -> None:
        async def handler(chat_id: str):
            try:
                goal = await getattr(_service(), action)(chat_id)
                return {"ok": True, "goal": _service().public(goal)}
            except Exception as exc:
                return _error(exc)

        router.add_api_route(
            f"/v1/control/chats/{{chat_id}}/goal/{action}",
            handler,
            methods=["POST"],
            tags=["Control"],
            name=f"control_v1_goal_{action}",
        )

    for action in ("pause", "resume", "abort", "accept"):
        action_route(action)


__all__ = ["register_goal_routes"]
