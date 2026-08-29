"""Thin onboarding, composer-context, and API-key HTTP adapters."""

from __future__ import annotations

import logging
from typing import Any

import httpx
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from cyrene.runtime.onboarding import (
    get_onboarding_status,
    save_and_test_llm_setup,
    save_codex_oauth_setup,
)
from cyrene.runtime.onboarding_context_service import OnboardingContextApplicationService
from cyrene.workbench.http.errors import localized_error_response


logger = logging.getLogger(__name__)

_VALIDATION_MESSAGES = {
    "LLM endpoint is required": "必须填写 LLM 接口地址。",
    "Model name is required": "必须填写模型名称。",
    "Selected model endpoint is unavailable": "所选模型接口当前不可用。",
    "Codex model is required": "必须选择 Codex 模型。",
    "OpenAI OAuth login is required": "需要先登录 OpenAI OAuth。",
    "Selected Codex model is unavailable": "所选 Codex 模型不可用。",
    "Selected reasoning effort is unavailable for this model": (
        "所选推理强度不适用于此模型。"
    ),
}


async def _request_object(request: Request) -> tuple[dict[str, Any] | None, JSONResponse | None]:
    try:
        body = await request.json()
    except ValueError:
        return None, localized_error_response(
            "request body must be valid JSON",
            "请求体必须是有效的 JSON。",
            400,
            "invalid_json",
        )
    if not isinstance(body, dict):
        return None, localized_error_response(
            "request body must be an object",
            "请求体必须是对象。",
            400,
            "invalid_request",
        )
    return body, None


def _validation_error(exc: ValueError, *, code: str) -> JSONResponse:
    message = str(exc)
    translated = _VALIDATION_MESSAGES.get(message)
    if translated is None:
        logger.info("Invalid onboarding request [%s]", code, exc_info=True)
        return localized_error_response(
            "Invalid onboarding settings.",
            "引导设置无效。",
            400,
            code,
        )
    return localized_error_response(message, translated, 400, code)


def register_onboarding_routes(router: APIRouter) -> None:
    @router.get("/api/onboarding")
    async def api_get_onboarding():
        return {"onboarding": get_onboarding_status()}

    @router.post("/api/onboarding/llm")
    async def api_onboarding_llm(request: Request):
        body, error = await _request_object(request)
        if error is not None:
            return error
        assert body is not None
        try:
            return await save_and_test_llm_setup(
                str(body.get("api_key") or ""),
                str(body.get("base_url") or ""),
                str(body.get("model") or ""),
                str(body.get("provider_id") or ""),
            )
        except ValueError as exc:
            return _validation_error(exc, code="invalid_llm_setup")
        except httpx.TimeoutException:
            logger.info("Onboarding model probe timed out", exc_info=True)
            return localized_error_response(
                "upstream model timed out",
                "上游模型响应超时。",
                504,
                "model_timeout",
            )
        except httpx.HTTPError:
            logger.info("Onboarding model probe failed", exc_info=True)
            return localized_error_response(
                "upstream model request failed",
                "上游模型请求失败。",
                502,
                "model_request_failed",
            )
        except (RuntimeError, OSError):
            logger.info("Onboarding model setup is unavailable", exc_info=True)
            return localized_error_response(
                "model setup is temporarily unavailable",
                "模型设置暂时不可用。",
                503,
                "model_setup_unavailable",
            )

    @router.post("/api/onboarding/openai-oauth")
    async def api_onboarding_openai_oauth(request: Request):
        body, error = await _request_object(request)
        if error is not None:
            return error
        assert body is not None
        try:
            return await save_codex_oauth_setup(
                str(body.get("model") or ""), str(body.get("reasoning_effort") or "")
            )
        except ValueError as exc:
            return _validation_error(exc, code="invalid_codex_setup")
        except (RuntimeError, OSError, TimeoutError):
            logger.info("Codex onboarding model validation failed", exc_info=True)
            return localized_error_response(
                "Codex model validation failed",
                "Codex 模型验证失败。",
                503,
                "codex_model_validation_failed",
            )

def register_key_routes(
    router: APIRouter, service: OnboardingContextApplicationService
) -> None:
    @router.get("/api/settings/keys")
    async def api_get_keys():
        return service.get_keys()

    @router.put("/api/settings/keys")
    async def api_update_keys(request: Request):
        body, error = await _request_object(request)
        if error is not None:
            return error
        assert body is not None
        result = service.update_keys(body)
        if result.get("error"):
            return localized_error_response(
                "no valid keys provided",
                "未提供有效的密钥。",
                400,
                "no_valid_keys",
            )
        return result


def register_onboarding_context_routes(
    router: APIRouter, application_service: OnboardingContextApplicationService
) -> None:
    register_onboarding_routes(router)
    register_key_routes(router, application_service)
