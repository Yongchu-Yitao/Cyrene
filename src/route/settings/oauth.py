"""OpenAI OAuth and Codex CLI HTTP adapters."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse


async def _codex_oauth_snapshot(
    *,
    include_limits: bool = True,
    include_models: bool = True,
    stale_limits: bool = False,
) -> dict[str, Any]:
    from cyrene.model_runtime.codex_provider import get_codex_provider
    from cyrene.runtime.settings_store import get as get_setting

    snapshot = await get_codex_provider().snapshot(
        include_limits=include_limits,
        include_models=include_models,
        stale_limits=stale_limits,
    )
    snapshot["quota_enabled"] = bool(
        get_setting("codex_budget_enabled", True)
    )
    return snapshot



def register_oauth_routes(router: APIRouter) -> None:
    @router.get("/api/settings/openai-oauth")
    async def api_get_openai_oauth():
        try:
            # Login state + model choices are the interactive path. Quota has a
            # separate endpoint/panel and must not delay showing "connected".
            return await _codex_oauth_snapshot(include_limits=False)
        except (RuntimeError, OSError, TimeoutError) as exc:
            return {
                "available": False, "connected": False, "models": [],
                "limits": {}, "quota_enabled": True, "error": str(exc),
            }

    @router.post("/api/settings/openai-oauth/login")
    async def api_start_openai_oauth_login():
        from cyrene.model_runtime.codex_provider import get_codex_provider
        from cyrene.runtime.settings_store import set_ as set_setting

        set_setting("codex_budget_enabled", True)
        try:
            return await get_codex_provider().start_login()
        except (RuntimeError, OSError, TimeoutError) as exc:
            return JSONResponse({"error": str(exc)}, status_code=503)

    @router.post("/api/settings/openai-oauth/logout")
    async def api_openai_oauth_logout():
        from cyrene.model_runtime.codex_provider import get_codex_provider

        try:
            await get_codex_provider().logout()
        except (RuntimeError, OSError, TimeoutError) as exc:
            return JSONResponse({"error": str(exc)}, status_code=503)
        return {"ok": True}

    @router.get("/api/settings/openai-oauth/cli")
    async def api_get_codex_cli_status():
        from cyrene.model_runtime import codex_cli

        return codex_cli.status()

    @router.post("/api/settings/openai-oauth/cli/download")
    async def api_start_codex_cli_download(request: Request):
        """Start the Codex CLI download.

        Contract: the response mirrors codex_cli.status(). A JSON body with
        ``force=true`` (sent by the settings UI when the snapshot reports
        ``cli.broken``) is the reinstall path for a broken-but-installed
        runtime: the current install is wiped and the SDK-pinned version —
        the one known to speak the SDK's protocol — is downloaded.
        """
        from cyrene.model_runtime import codex_cli

        try:
            body = await request.json()
        except ValueError:
            body = {}
        try:
            return codex_cli.start_download(force=bool(body.get("force")))
        except Exception as exc:
            return JSONResponse({"error": str(exc)}, status_code=503)

    @router.get("/api/settings/openai-oauth/limits")
    async def api_get_openai_oauth_limits():
        try:
            # This surface only needs account + quota data. Reuse the latest
            # snapshot immediately and refresh old limits in the background;
            # model discovery belongs to the separate model-settings endpoint.
            snapshot = await _codex_oauth_snapshot(
                include_models=False,
                stale_limits=True,
            )
            return {
                "available": snapshot.get("available", True),
                "connected": snapshot.get("connected", False),
                "account": snapshot.get("account"),
                "limits": snapshot.get("limits") or {},
                "quota_enabled": snapshot.get("quota_enabled", True),
            }
        except (RuntimeError, OSError, TimeoutError) as exc:
            return {
                "available": False, "connected": False, "limits": {},
                "quota_enabled": True, "error": str(exc),
            }



