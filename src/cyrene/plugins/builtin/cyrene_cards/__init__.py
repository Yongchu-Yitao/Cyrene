"""Assistant-only card authoring, input state and persistent pinning."""

from cyrene.core.plugin import PluginPack
from .context import setup
from .definitions import TOOL_NAMES
from .tools import plugins


def application_setup(context):
    from .application import setup_application
    setup_application(context)


plugin_pack = PluginPack(
    id="cyrene_cards",
    description="Assistant-only tools to create, update, read, pin and archive interactive card data.",
    plugins=plugins,
    setup=setup,
    application_setup=application_setup,
    metadata={"i18n": {
        "en": {"name": "Cards", "description": "Assistant card templates, user inputs and persistent pinning."},
        "zh": {"name": "卡片", "description": "助理专用的卡片模板、用户输入和持久固定。"},
    }},
)

__all__ = ["TOOL_NAMES", "plugin_pack", "setup", "application_setup"]
