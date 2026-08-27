"""OpenAI OAuth and Codex CLI adapters registered by ``cyrene_model``."""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Request

from cyrene.localization import localized
from route.errors import localized_error_response


logger = logging.getLogger(__name__)


def _public_codex_cli_status(value: Any) -> dict[str, Any]:
    status = dict(value) if isinstance(value, dict) else {}
    if status.get("error"):
        status["error"] = localized(
            "Codex CLI is unavailable.",
            "Codex CLI 不可用。",
        )
        status["error_code"] = "codex_cli_unavailable"
    return status


def _public_oauth_snapshot(value: Any) -> dict[str, Any]:
    snapshot = dict(value) if isinstance(value, dict) else {}
    if isinstance(snapshot.get("cli"), dict):
        snapshot["cli"] = _public_codex_cli_status(snapshot["cli"])
    if snapshot.get("error"):
        snapshot["error"] = localized(
            "Codex account connection is unavailable.",
            "Codex 账户连接不可用。",
        )
        snapshot["error_code"] = "codex_connection_unavailable"
    raw_errors = snapshot.get("errors")
    if isinstance(raw_errors, dict) and raw_errors:
        safe_errors: dict[str, str] = {}
        for key in raw_errors:
            if key == "models":
                safe_errors[key] = localized(
                    "Codex model list is unavailable.",
                    "Codex 模型列表不可用。",
                )
            elif key == "limits":
                safe_errors[key] = localized(
                    "Codex quota is temporarily unavailable.",
                    "Codex 配额暂时不可用。",
                )
            else:
                safe_errors["account"] = localized(
                    "Codex account data is unavailable.",
                    "Codex 账户数据不可用。",
                )
        snapshot["errors"] = safe_errors
        snapshot["error_codes"] = {
            key: {
                "models": "codex_models_unavailable",
                "limits": "codex_quota_unavailable",
                "account": "codex_account_data_unavailable",
            }[key]
            for key in safe_errors
        }
    return snapshot


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
    return _public_oauth_snapshot(snapshot)



def register_oauth_routes(router: APIRouter) -> None:
    @router.get("/api/settings/openai-oauth")
    async def api_get_openai_oauth():
        try:
            # Login state + model choices are the interactive path. Quota has a
            # separate endpoint/panel and must not delay showing "connected".
            return await _codex_oauth_snapshot(include_limits=False)
        except (RuntimeError, OSError, TimeoutError):
            logger.info("Unable to load OpenAI OAuth status", exc_info=True)
            return {
                "available": False, "connected": False, "models": [],
                "limits": {}, "quota_enabled": True,
                "error": localized(
                    "Codex account status is unavailable.",
                    "Codex 账户状态不可用。",
                ),
                "error_code": "codex_status_unavailable",
            }

    @router.post("/api/settings/openai-oauth/login")
    async def api_start_openai_oauth_login():
        from cyrene.model_runtime.codex_provider import get_codex_provider
        from cyrene.runtime.settings_store import set_ as set_setting

        set_setting("codex_budget_enabled", True)
        try:
            return await get_codex_provider().start_login()
        except (RuntimeError, OSError, TimeoutError):
            logger.info("Unable to start OpenAI OAuth login", exc_info=True)
            return localized_error_response(
                "OpenAI OAuth login could not be started.",
                "无法启动 OpenAI OAuth 登录。",
                503,
                "oauth_login_unavailable",
            )

    @router.post("/api/settings/openai-oauth/logout")
    async def api_openai_oauth_logout():
        from cyrene.model_runtime.codex_provider import get_codex_provider

        try:
            await get_codex_provider().logout()
        except (RuntimeError, OSError, TimeoutError):
            logger.info("Unable to log out of OpenAI OAuth", exc_info=True)
            return localized_error_response(
                "OpenAI OAuth logout failed.",
                "退出 OpenAI OAuth 失败。",
                503,
                "oauth_logout_failed",
            )
        return {"ok": True}

    @router.get("/api/settings/openai-oauth/cli")
    async def api_get_codex_cli_status():
        from cyrene.model_runtime import codex_cli

        return _public_codex_cli_status(codex_cli.status())

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
        if not isinstance(body, dict):
            return localized_error_response(
                "request body must be an object",
                "请求体必须是对象。",
                400,
                "invalid_request",
            )
        try:
            return _public_codex_cli_status(
                codex_cli.start_download(force=bool(body.get("force")))
            )
        except Exception:
            logger.info("Unable to start Codex CLI download", exc_info=True)
            return localized_error_response(
                "Codex CLI download could not be started.",
                "无法启动 Codex CLI 下载。",
                503,
                "codex_cli_download_failed",
            )

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
        except (RuntimeError, OSError, TimeoutError):
            logger.info("Unable to load OpenAI OAuth limits", exc_info=True)
            return {
                "available": False, "connected": False, "limits": {},
                "quota_enabled": True,
                "error": localized(
                    "Codex quota is temporarily unavailable.",
                    "Codex 配额暂时不可用。",
                ),
                "error_code": "codex_quota_unavailable",
            }
