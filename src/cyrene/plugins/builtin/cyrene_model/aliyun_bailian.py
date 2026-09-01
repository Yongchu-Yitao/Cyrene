"""Editable Alibaba Cloud Model Studio (Bailian) model Plugin."""

from ._shared import ModelProvider, create_model_plugin


ALIYUN_BAILIAN_PROVIDER = ModelProvider(
    id="aliyun_bailian",
    name="Alibaba Cloud Model Studio",
    plugin_name="AliyunBailian",
    adapter="openai",
    default_base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
    default_model="qwen-plus",
    capabilities=("chat", "vision", "tools", "reasoning"),
    icon="aliyun",
    include_stream_usage=True,
)
ALIYUN_BAILIAN_PLUGIN = create_model_plugin(ALIYUN_BAILIAN_PROVIDER)


__all__ = ["ALIYUN_BAILIAN_PLUGIN", "ALIYUN_BAILIAN_PROVIDER"]
