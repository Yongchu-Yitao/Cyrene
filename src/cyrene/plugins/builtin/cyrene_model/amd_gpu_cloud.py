"""Editable AMD GPU Cloud model Plugin."""

from ._shared import ModelProvider, create_model_plugin

AMD_GPU_CLOUD_PROVIDER = ModelProvider(
    id="amd_gpu_cloud",
    name="AMD GPU Cloud",
    plugin_name="AMDGPUCloud",
    adapter="openai",
    default_base_url="https://developer.amd.com.cn/radeon/api/v1",
    capabilities=("chat", "vision", "tools", "reasoning"),
    icon="amd",
)
AMD_GPU_CLOUD_PLUGIN = create_model_plugin(AMD_GPU_CLOUD_PROVIDER)

__all__ = ["AMD_GPU_CLOUD_PLUGIN", "AMD_GPU_CLOUD_PROVIDER"]
