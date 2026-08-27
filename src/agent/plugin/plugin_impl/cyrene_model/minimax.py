"""Editable MiniMax model Plugin with automatic model discovery."""

from ._shared import ModelProvider, create_model_plugin

DEFAULT_BASE_URL = "https://api.minimaxi.com/v1"
DEFAULT_MODEL = "MiniMax-M2.7"

MINIMAX_PROVIDER = ModelProvider(
    id="minimax",
    name="MiniMax",
    plugin_name="MiniMax",
    adapter="openai",
    default_base_url=DEFAULT_BASE_URL,
    default_model=DEFAULT_MODEL,
    capabilities=("chat", "tools", "reasoning"),
    icon="minimax",
)
MINIMAX_PLUGIN = create_model_plugin(MINIMAX_PROVIDER)

__all__ = [
    "DEFAULT_BASE_URL",
    "DEFAULT_MODEL",
    "MINIMAX_PLUGIN",
    "MINIMAX_PROVIDER",
]
