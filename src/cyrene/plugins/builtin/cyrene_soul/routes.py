"""HTTP editing and personality-onboarding surface owned by Soul."""

import logging

import httpx
from fastapi import APIRouter, Request
from pydantic import BaseModel, ConfigDict, Field

from cyrene.workbench.http.errors import localized_error_response
from .onboarding import SoulOnboardingError

logger = logging.getLogger(__name__)


class SoulUpdateBody(BaseModel):
    model_config = ConfigDict(extra="ignore", coerce_numbers_to_str=True)

    content: str = Field(default="", max_length=200_000)


def register_soul_routes(router: APIRouter, application, onboarding) -> None:
    @router.get("/api/settings/soul")
    async def api_get_soul():
        return {"content": application.read()}

    @router.put("/api/settings/soul")
    async def api_update_soul(body: SoulUpdateBody):
        application.write(body.content)
        return {"ok": True}

    @router.post("/api/onboarding/personality")
    async def api_onboarding_personality(request: Request):
        try:
            body = await request.json()
        except ValueError:
            return localized_error_response(
                "request body must be valid JSON",
                "请求体必须是有效的 JSON。",
                400,
                "invalid_json",
            )
        if not isinstance(body, dict):
            return localized_error_response(
                "request body must be an object",
                "请求体必须是对象。",
                400,
                "invalid_request",
            )
        try:
            return await onboarding.save(
                str(body.get("mode") or ""),
                name=str(body.get("name") or ""),
                content=str(body.get("content") or ""),
            )
        except SoulOnboardingError as exc:
            messages = {
                "personality_name_required": (
                    "Personality name is required.",
                    "必须填写人格名称。",
                ),
                "personality_mode_unsupported": (
                    "This personality mode is not supported.",
                    "不支持此人格模式。",
                ),
            }
            en, zh = messages.get(
                exc.code,
                ("Invalid personality settings.", "人格设置无效。"),
            )
            return localized_error_response(
                en,
                zh,
                400,
                exc.code,
            )
        except ValueError:
            logger.info("Invalid personality setup", exc_info=True)
            return localized_error_response(
                "Invalid personality settings.",
                "人格设置无效。",
                400,
                "invalid_personality_setup",
            )
        except httpx.TimeoutException:
            logger.info("Personality generation timed out", exc_info=True)
            return localized_error_response(
                "upstream model timed out",
                "上游模型响应超时。",
                504,
                "model_timeout",
            )
        except (httpx.HTTPError, RuntimeError, OSError):
            logger.info("Personality setup is unavailable", exc_info=True)
            return localized_error_response(
                "personality setup is temporarily unavailable",
                "人格设置暂时不可用。",
                503,
                "personality_setup_unavailable",
            )


__all__ = ["register_soul_routes"]
