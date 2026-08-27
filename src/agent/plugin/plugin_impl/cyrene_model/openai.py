"""Editable OpenAI model Plugin."""

from ._shared import ModelProvider, create_model_plugin

OPENAI_PROVIDER = ModelProvider(
    id="openai",
    name="OpenAI",
    plugin_name="OpenAI",
    adapter="openai",
    default_base_url="https://api.openai.com/v1",
    capabilities=("chat", "vision", "tools", "reasoning"),
    icon="openai",
)
OPENAI_PLUGIN = create_model_plugin(OPENAI_PROVIDER)

__all__ = ["OPENAI_PLUGIN", "OPENAI_PROVIDER"]
