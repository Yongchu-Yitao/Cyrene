"""Editable GLM model Plugin."""

from ._shared import ModelProvider, create_model_plugin

GLM_PROVIDER = ModelProvider(
    id="glm",
    name="GLM",
    plugin_name="GLM",
    adapter="openai",
    default_base_url="https://open.bigmodel.cn/api/paas/v4",
    capabilities=("chat", "vision", "tools", "reasoning"),
    icon="glm",
)
GLM_PLUGIN = create_model_plugin(GLM_PROVIDER)

__all__ = ["GLM_PLUGIN", "GLM_PROVIDER"]
