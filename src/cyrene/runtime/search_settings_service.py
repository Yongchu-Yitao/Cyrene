"""Application service for ordered web-search provider settings."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from cyrene.runtime import config_store, search_settings

SettingsChangedPublisher = Callable[[str, int | None, list[str]], Awaitable[None]]


class SearchSettingsApplicationError(RuntimeError):
    def __init__(self, message: str, status_code: int, revision: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.revision = revision


class SearchSettingsApplicationService:
    def __init__(self, publish_settings_changed: SettingsChangedPublisher) -> None:
        self._publish_settings_changed = publish_settings_changed

    def get_settings(self) -> dict[str, Any]:
        return search_settings.public_settings()

    async def update_settings(self, body: Any) -> dict[str, Any]:
        try:
            result = search_settings.update_settings(body)
        except config_store.SettingsRevisionConflict as exc:
            raise SearchSettingsApplicationError(str(exc), 409, exc.actual) from exc
        except search_settings.SearchSettingsError as exc:
            raise SearchSettingsApplicationError(str(exc), 400) from exc
        await self._publish_settings_changed(
            "search",
            result["revision"],
            ["search", "enabled_plugins"],
        )
        return result


__all__ = ["SearchSettingsApplicationError", "SearchSettingsApplicationService"]
