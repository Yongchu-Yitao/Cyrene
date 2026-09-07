"""Application identity and authenticated API liveness routes."""

from __future__ import annotations

from fastapi import APIRouter, Request


def register_instance_routes(router: APIRouter) -> None:
    @router.get("/api/health")
    async def api_health(request: Request) -> dict[str, str]:
        return {"service": "cyrene", "status": "ok",
                "instance_id": str(request.app.state.instance_id or "")}

    @router.get("/api/instance-id")
    async def api_instance_id(request: Request) -> dict[str, str]:
        return {"instance_id": str(request.app.state.instance_id or "")}


__all__ = ["register_instance_routes"]
