"""Editable generic OpenAI-compatible model Plugin."""

from ._shared import ModelProvider, create_model_plugin

OPENAI_COMPATIBLE_PROVIDER = ModelProvider(
    id="openai_compatible",
    name="OpenAI Compatible",
    plugin_name="OpenAICompatible",
    adapter="openai_compatible",
    default_base_url="https://api.openai.com/v1",
    auth_type="optional",
    capabilities=("chat", "vision", "embedding", "tools", "reasoning"),
    icon="",
)
OPENAI_COMPATIBLE_PLUGIN = create_model_plugin(OPENAI_COMPATIBLE_PROVIDER)

__all__ = ["OPENAI_COMPATIBLE_PLUGIN", "OPENAI_COMPATIBLE_PROVIDER"]
