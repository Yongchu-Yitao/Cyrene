"""Editable OpenCode Go model Plugin."""

from ._shared import ModelProvider, create_model_plugin

OPENCODE_GO_PROVIDER = ModelProvider(
    id="opencode_go",
    name="OpenCode Go",
    plugin_name="OpenCodeGo",
    adapter="openai",
    default_base_url="https://opencode.ai/zen/go/v1",
    capabilities=("chat", "vision", "tools", "reasoning"),
    icon="opencode",
)
OPENCODE_GO_PLUGIN = create_model_plugin(OPENCODE_GO_PROVIDER)

__all__ = ["OPENCODE_GO_PLUGIN", "OPENCODE_GO_PROVIDER"]
