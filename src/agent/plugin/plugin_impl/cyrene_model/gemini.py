"""Editable Gemini model Plugin."""

from ._shared import ModelProvider, create_model_plugin

GEMINI_PROVIDER = ModelProvider(
    id="gemini",
    name="Gemini",
    plugin_name="Gemini",
    adapter="gemini",
    default_base_url="https://generativelanguage.googleapis.com/v1beta",
    capabilities=("chat", "vision", "tools", "reasoning"),
    icon="gemini",
)
GEMINI_PLUGIN = create_model_plugin(GEMINI_PROVIDER)

__all__ = ["GEMINI_PLUGIN", "GEMINI_PROVIDER"]
