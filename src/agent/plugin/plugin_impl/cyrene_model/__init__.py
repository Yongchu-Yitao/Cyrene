"""Editable model implementations for every provider supported by Cyrene."""

from agent.plugin import PluginPack

from .amd_gpu_cloud import AMD_GPU_CLOUD_PLUGIN, AMD_GPU_CLOUD_PROVIDER
from .anthropic import ANTHROPIC_PLUGIN, ANTHROPIC_PROVIDER
from .codex_oauth import CODEX_OAUTH_PLUGIN, CODEX_OAUTH_PROVIDER
from .deepseek import DEEPSEEK_PLUGIN, DEEPSEEK_PROVIDER
from .gemini import GEMINI_PLUGIN, GEMINI_PROVIDER
from .glm import GLM_PLUGIN, GLM_PROVIDER
from .kimi import KIMI_PLUGIN, KIMI_PROVIDER
from .local_onnx import LOCAL_ONNX_PLUGIN, LOCAL_ONNX_PROVIDER
from .minimax import MINIMAX_PLUGIN, MINIMAX_PROVIDER
from .ollama import OLLAMA_PLUGIN, OLLAMA_PROVIDER
from .openai import OPENAI_PLUGIN, OPENAI_PROVIDER
from .openai_compatible import (
    OPENAI_COMPATIBLE_PLUGIN,
    OPENAI_COMPATIBLE_PROVIDER,
)
from .opencode_go import OPENCODE_GO_PLUGIN, OPENCODE_GO_PROVIDER
from .openrouter import OPENROUTER_PLUGIN, OPENROUTER_PROVIDER

MODEL_PROVIDERS = (
    OPENAI_PROVIDER,
    OPENAI_COMPATIBLE_PROVIDER,
    ANTHROPIC_PROVIDER,
    DEEPSEEK_PROVIDER,
    MINIMAX_PROVIDER,
    CODEX_OAUTH_PROVIDER,
    LOCAL_ONNX_PROVIDER,
    OLLAMA_PROVIDER,
    KIMI_PROVIDER,
    GLM_PROVIDER,
    OPENCODE_GO_PROVIDER,
    GEMINI_PROVIDER,
    OPENROUTER_PROVIDER,
    AMD_GPU_CLOUD_PROVIDER,
)

plugin_pack = PluginPack(
    id="cyrene_model",
    description="Editable model providers and discovery adapters.",
    plugins=(
        OPENAI_PLUGIN,
        OPENAI_COMPATIBLE_PLUGIN,
        ANTHROPIC_PLUGIN,
        DEEPSEEK_PLUGIN,
        MINIMAX_PLUGIN,
        CODEX_OAUTH_PLUGIN,
        LOCAL_ONNX_PLUGIN,
        OLLAMA_PLUGIN,
        KIMI_PLUGIN,
        GLM_PLUGIN,
        OPENCODE_GO_PLUGIN,
        GEMINI_PLUGIN,
        OPENROUTER_PLUGIN,
        AMD_GPU_CLOUD_PLUGIN,
    ),
)

__all__ = ["MODEL_PROVIDERS", "plugin_pack"]
