"""Editable Ollama model Plugin."""

from ._shared import ModelProvider, create_model_plugin

OLLAMA_PROVIDER = ModelProvider(
    id="ollama",
    name="Ollama",
    plugin_name="Ollama",
    adapter="ollama",
    default_base_url="http://127.0.0.1:11434",
    auth_type="none",
    capabilities=("chat", "vision", "embedding", "tools"),
    icon="ollama",
)
OLLAMA_PLUGIN = create_model_plugin(OLLAMA_PROVIDER)

__all__ = ["OLLAMA_PLUGIN", "OLLAMA_PROVIDER"]
