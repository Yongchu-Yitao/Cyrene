"""Editable DeepSeek model Plugin."""

from ._shared import ModelProvider, create_model_plugin

DEEPSEEK_PROVIDER = ModelProvider(
    id="deepseek",
    name="DeepSeek",
    plugin_name="DeepSeek",
    adapter="openai",
    default_base_url="https://api.deepseek.com/v1",
    default_model="deepseek-chat",
    capabilities=("chat", "tools", "reasoning"),
    icon="deepseek",
    supported_reasoning_efforts=("high", "max"),
    default_reasoning_effort="high",
)
DEEPSEEK_PLUGIN = create_model_plugin(DEEPSEEK_PROVIDER)

__all__ = ["DEEPSEEK_PLUGIN", "DEEPSEEK_PROVIDER"]
