"""Thin onboarding, context, SOUL, and API-key HTTP adapters."""

from __future__ import annotations

import asyncio

import httpx
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from cyrene.model_runtime.errors import format_httpx_error
from cyrene.runtime.onboarding import (
    get_onboarding_status,
    save_and_test_llm_setup,
    save_codex_oauth_setup,
    save_personality_setup,
)
from cyrene.runtime.onboarding_context_service import OnboardingContextApplicationService


def register_onboarding_routes(router: APIRouter) -> None:
    @router.get("/api/onboarding")
    async def api_get_onboarding():
        return {"onboarding": get_onboarding_status()}

    @router.post("/api/onboarding/llm")
    async def api_onboarding_llm(request: Request):
        body = await request.json()
        try:
            return await save_and_test_llm_setup(
                str(body.get("api_key") or ""),
                str(body.get("base_url") or ""),
                str(body.get("model") or ""),
            )
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        except httpx.TimeoutException as exc:
            return JSONResponse({"error": "upstream model timed out", "detail": str(exc)}, status_code=504)
        except httpx.HTTPError as exc:
            return JSONResponse({"error": "upstream model request failed", "detail": format_httpx_error(exc)}, status_code=502)

    @router.post("/api/onboarding/openai-oauth")
    async def api_onboarding_openai_oauth(request: Request):
        body = await request.json()
        try:
            return await save_codex_oauth_setup(
                str(body.get("model") or ""), str(body.get("reasoning_effort") or "")
            )
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        except (RuntimeError, OSError, TimeoutError) as exc:
            return JSONResponse({"error": "Codex model validation failed", "detail": str(exc)}, status_code=503)

    @router.post("/api/onboarding/personality")
    async def api_onboarding_personality(request: Request):
        body = await request.json()
        try:
            return await save_personality_setup(
                str(body.get("mode") or ""),
                name=str(body.get("name") or ""),
                content=str(body.get("content") or ""),
            )
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        except httpx.TimeoutException as exc:
            return JSONResponse({"error": "upstream model timed out", "detail": str(exc)}, status_code=504)
        except httpx.HTTPError as exc:
            return JSONResponse({"error": "upstream model request failed", "detail": format_httpx_error(exc)}, status_code=502)


def register_context_routes(
    router: APIRouter, service: OnboardingContextApplicationService
) -> None:
    @router.get("/api/context/state")
    async def api_context_state():
        return await asyncio.to_thread(service.context_state)

    @router.post("/api/context/remove-soul")
    async def api_remove_soul():
        return service.set_soul_active(False)

    @router.post("/api/context/add-soul")
    async def api_add_soul():
        return service.set_soul_active(True)

    @router.post("/api/context/remove-workspace")
    async def api_remove_workspace():
        return await asyncio.to_thread(service.set_workspace_active, False)

    @router.post("/api/context/add-workspace")
    async def api_add_workspace(request: Request):
        body = await request.json()
        return await asyncio.to_thread(service.activate_workspace, str(body.get("path", "")))

    @router.post("/api/context/pick-directory")
    async def api_pick_directory():
        return await service.pick_directory()


def register_soul_and_key_routes(
    router: APIRouter, service: OnboardingContextApplicationService
) -> None:
    @router.get("/api/settings/soul")
    async def api_get_soul():
        return service.get_soul()

    @router.put("/api/settings/soul")
    async def api_update_soul(request: Request):
        body = await request.json()
        return service.update_soul(body.get("content", ""))

    @router.get("/api/settings/keys")
    async def api_get_keys():
        return service.get_keys()

    @router.put("/api/settings/keys")
    async def api_update_keys(request: Request):
        result = service.update_keys(await request.json())
        if result.get("error"):
            return JSONResponse(result, status_code=400)
        return result


def register_onboarding_context_routes(
    router: APIRouter, application_service: OnboardingContextApplicationService
) -> None:
    register_onboarding_routes(router)
    register_context_routes(router, application_service)
    register_soul_and_key_routes(router, application_service)
