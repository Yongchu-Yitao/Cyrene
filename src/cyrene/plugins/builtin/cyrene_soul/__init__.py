"""SOUL.md context-mounting Plugin pack."""

from cyrene.plugins.context import PluginApplicationContext
from cyrene.core.plugin import Plugin, PluginContext, PluginPack

from .service import setup_soul
from .onboarding import SoulOnboardingApplication
from .store import SoulApplication


def application_setup(context: PluginApplicationContext) -> None:
    from cyrene.platform.settings_service import (
        PluginSettingsContribution,
        SettingControlSpec,
    )

    application = SoulApplication()
    onboarding = SoulOnboardingApplication(application)
    from .routes import register_soul_routes

    register_soul_routes(context.router, application, onboarding)
    context.provide("soul", application)
    context.provide("soul_onboarding", onboarding)
    context.on_startup(application.startup)
    context.provide(
        "soul_settings",
        PluginSettingsContribution(controls=(
            SettingControlSpec("agents.soul", "agents", "current_ui", "cyrene.ui.inspect", "R2"),
        )),
    )
    context.expose_frontend("soul")


async def _clear_expired_temporary(_arguments: dict, context: PluginContext) -> dict:
    context.services["soul"].clear_expired_temporary()
    return {"ok": True}


temporary_cleanup_job = Plugin(
    name="soul.temporary.cleanup",
    description="Remove expired entries from the TEMPORARY section of SOUL.md.",
    input_schema={"type": "object", "properties": {}, "additionalProperties": False},
    handler=_clear_expired_temporary,
    metadata={
        "model_visible": False,
        "background_job": {
            "id": "soul_temporary_cleanup",
            "interval_seconds": 86400,
            "run_on_start": True,
        },
    },
)


plugin_pack = PluginPack(
    id="cyrene_soul",
    description="Mount the enabled SOUL.md persona directly below the system prompt.",
    plugins=(temporary_cleanup_job,),
    setup=setup_soul,
    application_setup=application_setup,
    metadata={
        "i18n": {
            "en": {
                "name": "Soul",
                "description": "Mount the enabled SOUL.md persona directly below the system prompt.",
            },
            "zh": {
                "name": "灵魂",
                "description": "启用后，将 SOUL.md 人格挂载到系统提示词正下方。",
            },
        }
    },
)

__all__ = [
    "SoulApplication",
    "SoulOnboardingApplication",
    "application_setup",
    "plugin_pack",
]
