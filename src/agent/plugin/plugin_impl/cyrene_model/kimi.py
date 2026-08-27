"""Editable Kimi model Plugin."""

from ._shared import ModelProvider, create_model_plugin

KIMI_PROVIDER = ModelProvider(
    id="kimi",
    name="Kimi",
    plugin_name="Kimi",
    adapter="openai",
    default_base_url="https://api.moonshot.cn/v1",
    capabilities=("chat", "vision", "tools", "reasoning"),
    icon="kimi",
)
KIMI_PLUGIN = create_model_plugin(KIMI_PROVIDER)

__all__ = ["KIMI_PLUGIN", "KIMI_PROVIDER"]
