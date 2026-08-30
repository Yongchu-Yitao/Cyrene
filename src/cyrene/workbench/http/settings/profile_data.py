"""Profile, reset, budget, and MCP HTTP adapters."""

from __future__ import annotations

import json
import logging

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from cyrene.platform.profile_data_service import (
    ProfileDataApplicationService,
    ProfileDataError,
)
from cyrene.workbench.http.errors import localized_error_response


logger = logging.getLogger(__name__)


def _error_response(
    exc: ProfileDataError,
    *,
    en: str,
    zh: str,
    code: str,
) -> JSONResponse:
    logger.info("Profile/settings request failed [%s]", code, exc_info=True)
    if exc.status_code == 409:
        en = "Settings were changed by another client."
        zh = "设置已被其他客户端更改。"
        code = "settings_revision_conflict"
    return localized_error_response(
        en,
        zh,
        exc.status_code,
        str(exc.payload.get("code") or code),
        **{
            key: exc.payload[key]
            for key in ("revision", "expected_revision", "actual_revision")
            if key in exc.payload
        },
    )


def register_profile_routes(
    router: APIRouter,
    service: ProfileDataApplicationService,
) -> None:
    @router.put("/api/profile")
    async def api_update_profile(request: Request):
        try:
            body = await request.json()
        except (ValueError, json.JSONDecodeError):
            return localized_error_response(
                "request body must be valid JSON",
                "请求体必须是有效的 JSON。",
                400,
                "invalid_json",
            )
        if not isinstance(body, dict):
            return localized_error_response(
                "profile update must be an object",
                "个人资料更新内容必须是对象。",
                400,
                "invalid_profile",
            )
        try:
            return await service.update_profile(body)
        except ProfileDataError as exc:
            return _error_response(
                exc,
                en="Invalid profile settings.",
                zh="个人资料设置无效。",
                code="invalid_profile",
            )

    @router.post("/api/settings/reset-data")
    async def api_reset_data(request: Request):
        try:
            body = await request.json()
        except (ValueError, json.JSONDecodeError):
            body = {}
        if not isinstance(body, dict) or body.get("confirmation") != "RESET CYRENE DATA":
            return localized_error_response(
                "explicit reset confirmation is required",
                "必须明确确认重置操作。",
                400,
                "reset_confirmation_required",
            )
        try:
            return await service.reset()
        except ProfileDataError as exc:
            return _error_response(
                exc,
                en="Application data reset failed.",
                zh="应用数据重置失败。",
                code="reset_failed",
            )


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
            return _error_response(
                exc,
                en="Budget usage is temporarily unavailable.",
                zh="预算用量暂时不可用。",
                code="budget_usage_unavailable",
            )


def register_profile_data_routes(
    router: APIRouter,
    service: ProfileDataApplicationService,
) -> None:
    register_profile_routes(router, service)
    register_budget_routes(router, service)
