"""Application-instance identity route."""

from __future__ import annotations

from fastapi import APIRouter, Request


def register_instance_routes(router: APIRouter) -> None:
    @router.get("/api/instance-id")
    async def api_instance_id(request: Request) -> dict[str, str]:
        return {"instance_id": str(request.app.state.instance_id or "")}


__all__ = ["register_instance_routes"]
