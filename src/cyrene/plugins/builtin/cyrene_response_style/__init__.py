"""User-facing reply preferences shared by the Workbench and assistant menu."""

from cyrene.core.plugin import PluginPack
from .context import DETAIL, TONE, setup_response_style


def application_setup(context):
    from cyrene.platform.settings_service import PluginSettingsContribution, plugin_setting_spec

    specs = tuple(
        plugin_setting_spec(key, "string", "inherit", tab="agents",
                            enum=("inherit", *choices), apply_mode="next_run")
        for key, choices in (("reply_detail", DETAIL), ("reply_tone", TONE))
    )
    context.provide("response_style_settings", PluginSettingsContribution(specs=specs))
    context.expose_frontend("response_style")


plugin_pack = PluginPack(
    id="cyrene_response_style",
    description="Apply the user's preferred reply detail and tone.",
    plugins=(),
    setup=setup_response_style,
    application_setup=application_setup,
    metadata={"required": True, "i18n": {
        "en": {"name": "Reply style", "description": "Apply the user's preferred reply detail and tone."},
        "zh": {"name": "回复风格", "description": "根据用户选择调整回复详略和语气。"},
    }},
)
