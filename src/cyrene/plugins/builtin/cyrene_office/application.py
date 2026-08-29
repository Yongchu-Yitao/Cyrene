"""Settings routes and gateway lifecycle for the Office Plugin pack."""

from __future__ import annotations

import os

from cyrene.plugins.context import PluginApplicationContext


def _gateway_enabled(instance_id: str) -> bool:
    forced = os.environ.get("CYRENE_OFFICE_FORCE_START", "").lower()
    return bool(instance_id) or forced in {"1", "true", "yes"}


def setup_application(context: PluginApplicationContext) -> None:
    from .gateway import get_office_gateway_runtime
    from .settings_routes import register_office_integration_routes

    runtime = get_office_gateway_runtime()
    register_office_integration_routes(context.router)
    context.provide("office", runtime)
    from cyrene.runtime.settings_service import (
        PluginSettingsContribution,
        SettingControlSpec,
    )

    context.provide(
        "office_settings",
        PluginSettingsContribution(controls=(
            SettingControlSpec("integrations.office", "integrations", "current_ui", "cyrene.ui.inspect", "R2"),
        )),
    )
    context.expose_frontend("office")

    instance_id = str(getattr(context.app.state, "instance_id", "") or "")

    async def start() -> None:
        # Desktop hosts have an instance id. Manual web launches can opt in
        # without making every lightweight app factory bind the fixed port.
        if _gateway_enabled(instance_id):
            await runtime.start()

    async def stop() -> None:
        # The settings route can start the gateway on demand even when the
        # application host did not auto-start it.  Pack shutdown must remain
        # authoritative for every start path.
        await runtime.stop()

    context.on_startup(start)
    context.on_shutdown(stop)


__all__ = ["setup_application"]
