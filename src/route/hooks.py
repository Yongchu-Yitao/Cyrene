"""Thin HTTP adapters for Agent Hook management."""

from json import JSONDecodeError

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from cyrene.hooks.application_service import HookApplicationError, HookApplicationService


def _hook_error(exc: HookApplicationError) -> JSONResponse:
    return JSONResponse(
        {"ok": False, "error": str(exc)}, status_code=exc.status_code
    )


def register_hook_routes(router: APIRouter, service: HookApplicationService) -> None:
    @router.get("/api/hooks")
    async def api_hooks():
        return service.list()

    @router.post("/api/hooks")
    async def api_create_hook(request: Request):
        try:
            return await service.create(await request.json())
        except HookApplicationError as exc:
            return _hook_error(exc)

    @router.put("/api/hooks/{hook_id}")
    async def api_update_hook(hook_id: str, request: Request):
        try:
            return await service.update(hook_id, await request.json())
        except HookApplicationError as exc:
            return _hook_error(exc)

    @router.delete("/api/hooks/{hook_id}")
    async def api_delete_hook(hook_id: str):
        try:
            return await service.delete(hook_id)
        except HookApplicationError as exc:
            return _hook_error(exc)

    @router.post("/api/hooks/{hook_id}/enabled")
    async def api_enable_hook(hook_id: str, request: Request):
        body = await request.json()
        try:
            return await service.set_enabled(hook_id, body.get("enabled"))
        except HookApplicationError as exc:
            return _hook_error(exc)

    @router.post("/api/hooks/{hook_id}/test")
    async def api_test_hook(hook_id: str, request: Request):
        try:
            body = await request.json()
        except JSONDecodeError:
            body = {}
        try:
            return await service.test(hook_id, body if isinstance(body, dict) else {})
        except HookApplicationError as exc:
            return _hook_error(exc)

    @router.get("/api/hooks/audit/records")
    async def api_hook_audit(limit: int = 200):
        return service.audit(limit)

    @router.post("/api/hooks/proposals/{proposal_id}/decision")
    async def api_hook_proposal_decision(proposal_id: str, request: Request):
        body = await request.json()
        try:
            return service.decide_proposal(proposal_id, body.get("approve"))
        except HookApplicationError as exc:
            return _hook_error(exc)

    @router.post("/api/hooks/extensions/cli/{extension_id}/configure")
    async def api_configure_cli_hook(extension_id: str):
        try:
            return service.configure_cli(extension_id)
        except HookApplicationError as exc:
            return _hook_error(exc)


__all__ = ["register_hook_routes"]
