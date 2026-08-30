"""Application routes and process lifecycle for the browser Plugin pack."""

from __future__ import annotations

from pathlib import Path

from cyrene.plugins.context import PluginApplicationContext

from . import runtime
from .live_service import BrowserLiveApplicationService


class BrowserApplicationService(BrowserLiveApplicationService):
    """Browser capability published by the active Plugin pack."""

    def __init__(self, data_directory: str | Path) -> None:
        super().__init__()
        self.data_directory = Path(data_directory).expanduser().resolve()

    async def shutdown(self) -> None:
        await runtime.close_session()

    async def prepare_data_reset(self) -> dict[str, bool]:
        result = await runtime.clear_browser_data(
            self.data_directory / "browser_profile"
        )
        return {"browser_logins": bool(result.get("ok"))}

    async def close_session(self, session_id: str) -> dict[str, object]:
        return await runtime.close_electron_browser_session(session_id)

    async def finish_round(self, session_id: str, round_id: str) -> dict[str, object]:
        return await runtime.finish_electron_browser_round(session_id, round_id)

    def storage_paths(self) -> dict[str, tuple[Path, ...]]:
        """Expose the browser pack's persistent profile to storage settings."""

        return {"browser": (self.data_directory / "browser_profile",)}


def setup_application(context: PluginApplicationContext) -> None:
    from .routes import register_browser_routes

    service = BrowserApplicationService(context.data_directory)
    service = register_browser_routes(
        context.router,
        context.bot,
        context.db_path,
        service=service,
    )
    context.provide("browser", service)
    from cyrene.platform.settings_service import (
        PluginSettingsContribution,
        plugin_setting_spec,
    )

    context.provide(
        "browser_settings",
        PluginSettingsContribution(specs=(
            plugin_setting_spec(
                "proxy_browser_enabled", "boolean", False,
                tab="general", apply_mode="immediate",
            ),
        )),
    )
    context.expose_frontend("browser")
    context.on_shutdown(service.shutdown)


__all__ = ["BrowserApplicationService", "setup_application"]
