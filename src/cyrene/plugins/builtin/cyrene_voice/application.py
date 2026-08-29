"""Application routes and lifecycle for the voice Plugin pack."""

from __future__ import annotations

from cyrene.plugins.context import PluginApplicationContext

from .routes import register_voice_routes as register_voice_api_routes
from .service import VoiceService
from .voice_command import VoiceCommandApplicationService
from .workbench_routes import register_voice_routes as register_workbench_voice_routes


class _HostServiceProxy:
    """Resolve a generic Workbench port after route composition completes."""

    def __init__(self, app: object, name: str) -> None:
        self._app = app
        self._name = name

    def _service(self):
        state = getattr(self._app, "state", None)
        host = getattr(state, "plugin_application_host", None)
        service = host.service(self._name) if host is not None else None
        if service is None:
            raise RuntimeError(f"Voice Plugin dependency is unavailable: {self._name}")
        return service

    def __getattr__(self, name: str):
        return getattr(self._service(), name)


def setup_application(context: PluginApplicationContext) -> None:
    service = VoiceService()
    voice_command = VoiceCommandApplicationService(
        service,
        chat=_HostServiceProxy(context.app, "workbench_chat"),
        projects=_HostServiceProxy(context.app, "workbench_projects"),
    )
    register_voice_api_routes(context.router, service=service)
    register_workbench_voice_routes(context.router, voice_command)
    context.provide("voice", service)
    context.provide("voice_command", voice_command)
    from cyrene.runtime.settings_service import (
        PluginSettingsContribution,
        SettingControlSpec,
    )

    context.provide(
        "voice_settings",
        PluginSettingsContribution(controls=(
            SettingControlSpec("voice.settings", "voice", "current_ui", "cyrene.ui.inspect", "R2"),
            SettingControlSpec("voice.profile", "voice", "user_ceremony", "cyrene.file_picker", "R3"),
        )),
    )
    context.expose_frontend("voice")
    context.on_startup(service.startup)
    context.on_shutdown(service.shutdown)


__all__ = ["setup_application"]
