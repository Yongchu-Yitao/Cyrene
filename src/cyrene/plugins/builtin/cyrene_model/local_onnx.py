"""Editable local ONNX embedding model Plugin."""

from ._shared import ModelProvider, create_local_model_plugin

LOCAL_ONNX_PROVIDER = ModelProvider(
    id="local_onnx",
    name="Local ONNX",
    plugin_name="LocalONNX",
    adapter="local_onnx",
    default_base_url="",
    default_model="qwen3-embedding-0.6b",
    auth_type="none",
    capabilities=("embedding",),
    icon="onnx",
    supports_discovery=False,
)
LOCAL_ONNX_PLUGIN = create_local_model_plugin(LOCAL_ONNX_PROVIDER)

__all__ = ["LOCAL_ONNX_PLUGIN", "LOCAL_ONNX_PROVIDER"]
