"""Editable OpenRouter model Plugin."""

from ._shared import ModelProvider, create_model_plugin

OPENROUTER_PROVIDER = ModelProvider(
    id="openrouter",
    name="OpenRouter",
    plugin_name="OpenRouter",
    adapter="openai",
    default_base_url="https://openrouter.ai/api/v1",
    capabilities=("chat", "vision", "tools", "reasoning"),
    icon="openrouter",
)
OPENROUTER_PLUGIN = create_model_plugin(OPENROUTER_PROVIDER)

__all__ = ["OPENROUTER_PLUGIN", "OPENROUTER_PROVIDER"]
