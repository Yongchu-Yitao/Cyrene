"""Editable Anthropic model Plugin."""

from ._shared import ModelProvider, create_model_plugin

ANTHROPIC_PROVIDER = ModelProvider(
    id="anthropic",
    name="Anthropic",
    plugin_name="Anthropic",
    adapter="anthropic",
    default_base_url="https://api.anthropic.com/v1",
    capabilities=("chat", "vision", "tools", "reasoning"),
    icon="anthropic",
)
ANTHROPIC_PLUGIN = create_model_plugin(ANTHROPIC_PROVIDER)

__all__ = ["ANTHROPIC_PLUGIN", "ANTHROPIC_PROVIDER"]
