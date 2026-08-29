"""SOUL.md context-mounting Plugin pack."""

from cyrene.plugins.context import PluginApplicationContext
from cyrene.core.plugin import PluginPack

from .service import setup_soul
from .onboarding import SoulOnboardingApplication
from .store import SoulApplication


def application_setup(context: PluginApplicationContext) -> None:
    from cyrene.runtime.settings_service import (
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


plugin_pack = PluginPack(
    id="cyrene_soul",
    description="Mount the enabled SOUL.md persona directly below the system prompt.",
    plugins=(),
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
