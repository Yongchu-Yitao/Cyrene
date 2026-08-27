"""Application service for profile, reset, and budget settings."""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from datetime import datetime, timezone
from typing import Any, Protocol

from cyrene.runtime.budget import BudgetUsageQueryError, get_budget_state
from cyrene.model_runtime.pricing import cost_from_cny
from cyrene.runtime import config_store, settings_store
from cyrene.runtime.data_reset import DataResetApplicationService
from cyrene.config import DB_PATH
from cyrene.runtime import database
from cyrene.runtime.settings_service import SettingsServiceError, update

logger = logging.getLogger(__name__)
SettingsChangedPublisher = Callable[[str, int | None, list[str]], Awaitable[None]]


class ProfileQueryPort(Protocol):
    def user(self) -> dict[str, Any]: ...


class ProfileDataError(RuntimeError):
    def __init__(self, message: str, status_code: int, **payload: Any) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.payload = payload


class ProfileDataApplicationService:
    """Own profile mutations and settings-facing query projections."""

    def __init__(
        self,
        db_path: str,
        queries: ProfileQueryPort,
        reset_service: DataResetApplicationService,
        publish_settings_changed: SettingsChangedPublisher,
    ) -> None:
        self.db_path = str(db_path or DB_PATH)
        self.queries = queries
        self.reset_service = reset_service
        self.publish_settings_changed = publish_settings_changed

    async def update_profile(self, body: dict[str, Any]) -> dict[str, Any]:
        key_map = {
            "name": "profile_name",
            "bio": "profile_bio",
            "avatar": "profile_avatar",
            "avatar_emoji": "profile_avatar_emoji",
            "avatar_color": "profile_avatar_color",
        }
        changes = {
            setting_key: body[public_key]
            for public_key, setting_key in key_map.items()
            if public_key in body
        }
        try:
            result = update(
                "profile",
                changes,
                actor="ui",
                expected_revision=body.get("expected_revision"),
            )
        except config_store.SettingsRevisionConflict as exc:
            raise ProfileDataError(str(exc), 409, revision=exc.actual) from exc
        except SettingsServiceError as exc:
            raise ProfileDataError(str(exc), 400) from exc
        await self.publish_settings_changed(
            "profile",
            result["revision"],
            result["changed"],
        )
        return {
            "ok": True,
            "changed": [key for key in key_map if key in body],
            "revision": result["revision"],
            "user": self.queries.user(),
        }

    async def reset(self) -> dict[str, Any]:
        try:
            return await self.reset_service.reset_app_data()
        except Exception as exc:
            logger.exception("Application data reset failed")
            detail = str(exc) or exc.__class__.__name__
            raise ProfileDataError(
                "application data reset failed",
                500,
                detail=detail,
                code="reset_failed",
            ) from exc

    async def budget_stats(self) -> dict[str, Any]:
        currency = str(
            settings_store.get_all().get("budget_currency") or "CNY"
        ).upper()
        now = datetime.now(timezone.utc)
        month_start = datetime(now.year, now.month, 1, tzinfo=timezone.utc)
        try:
            stats = await database.get_token_usage_stats(
                self.db_path,
                since=month_start,
            )
        except Exception:
            stats = {}
        by_model = stats.get("by_model", [])
        by_day = stats.get("by_day", [])
        total = stats.get("total", {})
        rows = [self._model_budget_row(item, currency) for item in by_model]
        rows.sort(key=lambda row: row["cost"], reverse=True)
        return {
            "models": rows,
            "by_day": [self._day_budget_row(item, currency) for item in by_day],
            "total_cost": round(sum(row["cost"] for row in rows), 4),
            "total_requests": int(total.get("requests", 0)),
            "max_request_tokens": int(total.get("max_total_tokens") or 0),
            "max_request_cost": round(
                cost_from_cny(float(total.get("max_cost") or 0), currency),
                4,
            ),
        }

    async def budget_status(self) -> dict[str, Any]:
        settings = settings_store.get_all()
        try:
            return await get_budget_state(
                self.db_path,
                monthly=float(settings.get("budget_monthly") or 0),
                enabled=bool(settings.get("budget_enabled", False)),
            )
        except BudgetUsageQueryError as exc:
            raise ProfileDataError(
                "budget usage is temporarily unavailable",
                503,
            ) from exc

    @staticmethod
    def _model_budget_row(item: dict[str, Any], currency: str) -> dict[str, Any]:
        return {
            "model": item.get("model", ""),
            "requests": int(item.get("requests", 0)),
            "prompt_tokens": int(item.get("prompt_tokens", 0)),
            "completion_tokens": int(item.get("completion_tokens", 0)),
            "cost": round(cost_from_cny(float(item.get("cost", 0)), currency), 4),
        }

    @staticmethod
    def _day_budget_row(item: dict[str, Any], currency: str) -> dict[str, Any]:
        return {
            "day": str(item.get("day") or ""),
            "requests": int(item.get("requests") or 0),
            "total_tokens": int(item.get("total_tokens") or 0),
            "cost": round(
                cost_from_cny(float(item.get("cost") or 0), currency),
                4,
            ),
        }
