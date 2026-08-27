"""Editable OpenAI Codex OAuth model Plugin."""

from ._shared import ModelProvider, create_model_plugin

CODEX_OAUTH_PROVIDER = ModelProvider(
    id="codex_oauth",
    name="OpenAI Codex OAuth",
    plugin_name="CodexOAuth",
    adapter="codex_oauth",
    default_base_url="codex://oauth",
    auth_type="oauth",
    capabilities=("chat", "vision", "tools", "reasoning"),
    icon="openai",
)
CODEX_OAUTH_PLUGIN = create_model_plugin(CODEX_OAUTH_PROVIDER)

__all__ = ["CODEX_OAUTH_PLUGIN", "CODEX_OAUTH_PROVIDER"]
