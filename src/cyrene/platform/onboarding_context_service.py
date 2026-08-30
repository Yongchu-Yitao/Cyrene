"""Application service for onboarding API-key settings."""

from __future__ import annotations

from typing import Any

from cyrene import config
from cyrene.localization import localized


class OnboardingContextApplicationService:
    def get_keys(self) -> dict:
        return {"keys": config.get_env_keys_meta()}

    def update_keys(self, body: dict[str, Any]) -> dict:
        updates = {}
        for key, meta in config.editable_env_keys().items():
            value = body.get(key, "")
            if not value:
                continue
            if meta["masked"] and (value.startswith("••") or len(value) <= 8):
                continue
            updates[key] = value
        if not updates:
            return {
                "error": localized(
                    "No valid keys were provided.",
                    "未提供有效的密钥。",
                ),
                "code": "no_valid_keys",
            }
        config.write_env_keys(updates)
        return {"ok": True, "updated": list(updates)}
