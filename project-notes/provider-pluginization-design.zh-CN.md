# Provider 插件化重构设计

> **本轮交付物**：仅设计文档，不动源代码。所有改动在实施阶段按 §8 顺序落地。
> **决策记录**（来自设计讨论）：
> 1. **范围**：用户已选择"只做核心抽象 + M3 + Anthropic"作为推荐路线（详细见 §1.2）
> 2. **发现机制**：声明式注册 —— OpenAI 兼容厂商在 `cyrene_builtin_providers.py` 加 1 行即可
> 3. **向后兼容**：不做兼容（`provider` 字段必填），但写一次性 migration 脚本扫老配置
> 4. **agent loop 接口**：保持 OpenAI 标准 message 形态不动；Provider 负责把任意上游 shape 翻译成它
> **不在本设计范围**：embedding、TTS、image generation、codex_cli 二进制管理（这些是独立子模块）

---

## 1. 现状与目标

### 1.1 现状（详见 `multimodel-provider-research.zh-CN.md` §1）

- `client.py` 共 2656 行，其中 ~425 行是散在 4 个文件、6 个函数里的 provider-specific 逻辑
- 唯一的"provider"概念是 `candidate["provider"]` 字段（取值仅 `openai_compatible` / `codex_oauth` / 缺省）
- `_build_payload` 里有 `is_deepseek = "deepseek" in model.lower()` 字符串硬编码
- Codex OAuth 走 `codex_provider.py` 旁路（~66 KB，独立运行时）
- DSML 文本工具调用解析（`_normalize_dsml_tool_calls` + `_DsmlStreamFilter`）是 DeepSeek 专属

### 1.2 本设计目标

完成 "核心抽象 + M3 + Anthropic" 路线，实现后：
- 加 Kimi/GLM/MiMo/Ollama/OpenRouter/Qwen/任何 OpenAI 兼容厂商 = **1 行注册，0 行新代码**
- 加 DeepSeek（已有）/MiniMax-M3 = **1 个 ~100 行 Provider 文件**
- 加 Anthropic（Claude + MiniMax `/anthropic` 端点）= **1 个 ~250 行 Provider 文件**
- `client.py` 中的 provider-specific 字符串、if 分支全部消失
- 老的 `is_deepseek in model.lower()` 这类硬编码完全清掉

### 1.3 不做（明确划出范围）

- Gemini 原生（`:generateContent`）—— 留作下一轮；架构上允许后续加
- PyPI 第三方插件（entry point 机制）—— 当前内部产品，复杂度不值；保留扩展点
- Streaming 内容块协议层重构（content block protocol abstraction）—— 现有 OpenAI SSE 解析抽象程度够用
- 改 agent loop 消费层 —— 维持 OpenAI 标准 message 形态
- 改 `_record_token_usage_faf` / token 上报管线 —— Provider 输出统一 usage 形态后无需改

---

## 2. 架构总览

### 2.1 目录布局

```
src/cyrene/model_runtime/
├── __init__.py                          # 公开导出 get_provider_for / registry
├── client.py                            # 仅保留候选解析、调度、telemetry、HTTP 客户端池
│                                        # 调 provider.prepare_request / provider.execute / provider.parse_*
│                                        # 预计从 2656 行 → ~1500 行
├── pricing.py                           # 现状不变；新增内建 price 表按 provider.name 索引（可选）
├── errors.py                            # 现状不变
├── messages.py                          # 现状不变
├── codex_cli.py                         # 现状不变（Codex 二进制管理）
├── image_generation.py                  # 不在本设计范围
├── opencv_runtime.py                    # 不在本设计范围
│
├── providers/                           # 新增
│   ├── __init__.py                      # 导出 Provider / ProviderRequest / register_provider
│   ├── base.py                          # Provider 协议 + 数据类 + 异常
│   ├── registry.py                      # 全局 registry + get_provider_for
│   ├── openai_compatible.py             # OpenAI 兼容基类（90% 厂商用）
│   ├── deepseek.py                      # DeepSeek 特有行为
│   ├── codex.py                         # 重写 codex_provider.py → Provider 子类
│   ├── minimax.py                       # MiniMax（OpenAI 兼容路径 + M3 thinking/split/cache）
│   ├── anthropic.py                     # Anthropic Messages（含 MiniMax /anthropic 端点）
│   └── builtin_providers.py             # 1 行注册：kimi / glm / mimo / openrouter / ollama / vllm / qwen
│
└── migration/
    └── infer_provider_from_legacy.py    # 一次性脚本：把老的"无 provider 字段"配置补上
```

### 2.2 数据流

```
[Settings: models list]
       │  {provider: "minimax", model: "MiniMax-M3", base_url, api_key, ...}
       ▼
[_resolve_llm_candidates()]                # 现状不变；只检查 provider 字段必填
       │
       ▼
[get_provider_for(candidate)]              # registry lookup
       │  returns Provider instance
       ▼
[call_llm dispatcher]                      # 改写后的入口
       │
       │  for each candidate:
       │    provider.prepare_request(candidate, messages, tools, ...)
       │    → ProviderRequest{method, url, headers, body, stream, sdk_callable?}
       │
       │    if sdk_callable:  provider.execute_sdk(...)
       │    else:             dispatcher HTTP POST + provider.parse_stream / parse_response
       │
       │    provider.normalize_usage(usage) → {input, output, cache_hit, cache_miss, ...}
       │    provider.sanitize_messages(messages) (for next round)
       │
       ▼
[canonical message dict]                   # OpenAI 标准形态
{role, content, tool_calls, reasoning_content?, usage, finish_reason, model, _candidate_identity}
       │
       ▼
[agent loop, state.json, telemetry]       # 全部不动
```

### 2.3 与现状的边界

| 不变 | 变 | 新增 |
|---|---|---|
| 候选解析（`_resolve_llm_candidates`）的链式 fallback 逻辑 | `_build_payload` 拆为各 Provider 的 `prepare_request` | Provider 协议 |
| `_candidate_cooling` / `_prioritize_last_success` / `_remember_success` | `_handle_stream` 拆为通用 SSE 解析 + Provider 的 `parse_stream` | `ModelCapabilities` 数据类 |
| `_record_token_usage_faf` / `_publish_llm_event` / `context_debug` | `_normalize_dsml_tool_calls` 移到 `deepseek.py` | 声明式 `register_openai_compatible()` |
| HTTP 客户端池 / 重试 / 退避 | `_DsmlStreamFilter` 移到 `deepseek.py` | `ProviderRequest` 数据类 |
| `sanitize_messages_for_llm` 主体 | `_is_official_deepseek_base_url` / `_normalized_llm_endpoints` 拆到各 Provider | registry 查表 |
| `codex_cli` 二进制管理 | `is_deepseek in model.lower()` 散点全清 | migration 脚本 |
| `pricing.py` 价格目录 | Codex OAuth 旁路改写为 `CodexProvider.execute_sdk` |  |
| `model_candidate_identity_for_response` | `_normalized_candidate` 校验 `provider` 字段必填 |  |
| 所有 agent loop / Workbench / SSE 事件 |  |  |

---

## 3. Provider 协议

### 3.1 协议定义（`providers/base.py`）

```python
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Awaitable, Callable, ClassVar

# Re-export from a single place so callers don't reach into internals.
__all__ = [
    "Provider",
    "ProviderRequest",
    "ModelCapabilities",
    "StreamEvent",
    "ProviderError",
    "ConfigurationError",
]


# ---------------------------------------------------------------------------
# 数据类
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ModelCapabilities:
    """Provider 声明的、模型无关的元能力。"""
    vision: bool = False
    multimodal_video: bool = False
    supports_tools: bool = True
    supports_streaming: bool = True
    supports_structured_output: bool = False
    supports_thinking: bool = False
    preserve_reasoning_in_history: bool = False
    ctx_limit: int = 0
    default_max_tokens: int | None = None
    supported_reasoning_efforts: tuple[str, ...] = ()
    default_reasoning_effort: str = ""
    # 响应里 reasoning 在哪个字段。空字符串 = 嵌在 content 里。
    reasoning_response_field: str = ""
    # usage 中缓存命中的字段路径（tuple 表示嵌套）。
    cache_hit_field_path: tuple[str, ...] = ()


@dataclass(frozen=True)
class ProviderRequest:
    """Provider 对一次调用的完整指令。dispatcher 据此发送。"""
    method: str = "POST"
    url: str = ""
    urls: tuple[str, ...] = ()          # 多个 → 顺序尝试
    headers: dict[str, str] = field(default_factory=dict)
    body: dict[str, Any] | None = None
    # 若非 None，dispatcher 不走 HTTP，直接调它。用于 Codex SDK / Anthropic SDK。
    sdk_callable: Callable[..., Awaitable[Any]] | None = None
    sdk_kwargs: dict[str, Any] = field(default_factory=dict)
    # metadata
    stream: bool = False
    timeout: float = 120.0


@dataclass
class StreamEvent:
    """Provider 把任意上游 SSE / SDK 事件归一化成的内部事件。"""
    type: str                            # "reply_start" | "reply_delta" | "reasoning_start" | "reasoning_delta" | "reasoning_done" | "tool_call_delta" | "usage" | "done" | "error"
    delta: str | None = None
    response: Any = None                 # for reasoning_done
    tool_call: dict | None = None
    usage: dict | None = None
    error: BaseException | None = None


# ---------------------------------------------------------------------------
# 异常
# ---------------------------------------------------------------------------

class ProviderError(Exception):
    """Provider 内部错误（解析失败、协议不匹配）。"""

class ConfigurationError(Exception):
    """配置错误：候选缺少 provider 字段、注册表查不到等。"""


# ---------------------------------------------------------------------------
# Provider 协议
# ---------------------------------------------------------------------------

class Provider(ABC):
    """所有 model provider 必须实现的协议。

    实现要点：
    - ``name`` 是唯一标识，对应 candidate["provider"] 字段
    - 所有方法都是无状态的；同一 Provider 实例可服务多个 candidate
    - 任何 provider 不得假设自己是"OpenAI 兼容"——上游 shape 完全由 provider 自己负责
    """

    name: ClassVar[str]  # 子类必须覆写

    @abstractmethod
    def capabilities(self, model: str) -> ModelCapabilities:
        """返回该模型的能力声明。可以基于 model 名字符串判定。"""

    @abstractmethod
    def prepare_request(
        self,
        *,
        model: str,
        messages: list[dict],
        tools: list | None,
        max_tokens: int | None,
        stream: bool,
        thinking: str,
        response_format: dict | None,
        reasoning_effort: str,
        candidate: dict[str, Any],
    ) -> ProviderRequest:
        """构造一次调用的完整指令。

        Dispatcher 拿到 ProviderRequest 后决定走 HTTP 还是 sdk_callable。
        """

    def parse_response(self, data: dict[str, Any]) -> dict[str, Any]:
        """把上游 HTTP 响应（已 JSON 解码）翻译成 OpenAI 标准 message。

        默认实现：兼容 OpenAI shape 的简单解包。
        Subclass 仅在响应 shape 与 OpenAI 不同时覆写。
        """
        return _default_parse_openai_response(data)

    async def parse_stream(
        self,
        lines: AsyncIterator[str],
        *,
        tools: list | None = None,
    ) -> AsyncIterator[StreamEvent]:
        """把上游流式行（SSE 格式：每行一个 data: {...} 或 [DONE]）归一化为 StreamEvent。

        默认实现：标准 OpenAI delta 解析。
        Subclass 在 delta 字段名不同或需要剥文本工具调用时覆写。
        """
        async for event in _default_parse_openai_stream(lines, tools=tools):
            yield event

    def normalize_usage(self, usage: dict[str, Any] | None) -> dict[str, Any]:
        """把上游 usage 字典归一化为内部标准 usage。

        内部标准 usage 字段：
          - input: int               # prompt tokens (excluding cache hit)
          - output: int              # completion tokens
          - cache_hit: int           # cached prompt tokens
          - cache_miss: int          # non-cached prompt tokens (= input if cache_hit missing)
          - reasoning: int           # reasoning/thinking tokens if separately metered
          - raw: dict                # 原样保留的上游 usage
        """
        return _default_normalize_usage(usage, capabilities=self.capabilities_for_default())

    def sanitize_messages(
        self,
        messages: list[dict],
        *,
        preserve_tool_reasoning: bool = False,
    ) -> list[dict]:
        """Provider 特有的消息清洗。

        绝大多数 provider 不需要，identity 透传。
        DeepSeek / MiniMax 这种需要保留 reasoning_content 的会覆写。
        """
        return list(messages)

    def should_cooldown_on_error(self, exc: BaseException) -> bool:
        """Provider 决定某类错误是否要把候选冷却。"""
        return True  # 默认：所有错误都冷却

    # ----- 内部辅助 -----

    def capabilities_for_default(self) -> ModelCapabilities:
        """Helper for normalize_usage default — 返回一个"通用"能力。
        实际 provider 应在自己的 normalize_usage 中正确使用 capabilities()。
        """
        return ModelCapabilities()
```

### 3.2 协议设计要点

1. **`ProviderRequest` 抽象到"一次请求的全部"**：dispatcher 不需要知道"这个 provider 要不要发 thinking 字段"、"endpoint 是 /v1 还是 /anthropic"——全在 `prepare_request` 里决定
2. **`sdk_callable` 让 Codex/Anthropic SDK 干净接入**：不用专门写 "如果 provider == codex_oauth 就走另一条路" 的分支
3. **`capabilities` 声明能力**而不是"is_deepseek 字符串判定"：UI、agent loop、cost 计算都消费这个
4. **`StreamEvent` 是归一化的事件总线**：dispatcher 不需要知道 reasoning 在 `delta.reasoning_content` 还是 `delta.reasoning_details`，Provider 自己解析
5. **`sanitize_messages` 是可选的 hook**：绝大多数 provider 不需要；DeepSeek / MiniMax 用它
6. **`should_cooldown_on_error`**：Codex 有自己的"quota exhausted"概念，应该不冷却而是"等配额刷新"；这个 hook 留个口子

---

## 4. 声明式注册机制

### 4.1 注册函数（`providers/registry.py`）

```python
from __future__ import annotations

from typing import Type

from .base import Provider, ConfigurationError

_REGISTRY: dict[str, Provider] = {}


def register_provider(provider: Provider) -> None:
    """注册一个 provider 实例。同名重复注册抛错（启动期就拦截）。"""
    name = provider.name
    if not name:
        raise ConfigurationError("Provider.name 不能为空")
    if name in _REGISTRY:
        raise ConfigurationError(f"Provider '{name}' 已注册")
    _REGISTRY[name] = provider


def get_provider(name: str) -> Provider:
    if name not in _REGISTRY:
        raise ConfigurationError(
            f"Unknown provider: '{name}'. "
            f"Available: {sorted(_REGISTRY.keys())}"
        )
    return _REGISTRY[name]


def list_providers() -> list[str]:
    return sorted(_REGISTRY.keys())


def unregister_provider(name: str) -> None:
    """仅供测试使用。"""
    _REGISTRY.pop(name, None)


def reset_registry() -> None:
    """仅供测试使用。"""
    _REGISTRY.clear()
```

### 4.2 OpenAI 兼容声明式注册（`providers/openai_compatible.py`）

```python
def register_openai_compatible(
    *,
    name: str,
    default_base_url: str | None = None,
    default_model: str | None = None,
    vision: bool = False,
    multimodal_video: bool = False,
    supports_structured_output: bool = False,
    ctx_limit: int = 0,
    default_max_tokens: int | None = None,
    supported_reasoning_efforts: tuple[str, ...] = (),
    default_reasoning_effort: str = "",
    cache_hit_field_path: tuple[str, ...] = ("prompt_tokens_details", "cached_tokens"),
    **extra_caps: Any,
) -> None:
    """为任何 OpenAI 兼容厂商注册 provider。0 行新代码、1 行调用。"""

    class _OpenAICompatAuto(OpenAICompatibleProvider):
        pass

    _OpenAICompatAuto.name = name
    _OpenAICompatAuto._default_base_url = default_base_url
    _OpenAICompatAuto._default_model = default_model
    _OpenAICompatAuto._vision = vision
    _OpenAICompatAuto._multimodal_video = multimodal_video
    _OpenAICompatAuto._supports_structured_output = supports_structured_output
    _OpenAICompatAuto._ctx_limit = ctx_limit
    _OpenAICompatAuto._default_max_tokens = default_max_tokens
    _OpenAICompatAuto._supported_reasoning_efforts = supported_reasoning_efforts
    _OpenAICompatAuto._default_reasoning_effort = default_reasoning_effort
    _OpenAICompatAuto._cache_hit_field_path = cache_hit_field_path

    register_provider(_OpenAICompatAuto())


class OpenAICompatibleProvider(Provider):
    """标准 OpenAI Chat Completions 协议实现。绝大多数 provider 继承即可。"""

    # 类属性；register_openai_compatible 会覆写
    _default_base_url: ClassVar[str | None] = None
    _default_model: ClassVar[str | None] = None
    _vision: ClassVar[bool] = False
    _multimodal_video: ClassVar[bool] = False
    _supports_structured_output: ClassVar[bool] = False
    _ctx_limit: ClassVar[int] = 0
    _default_max_tokens: ClassVar[int | None] = None
    _supported_reasoning_efforts: ClassVar[tuple[str, ...]] = ()
    _default_reasoning_effort: ClassVar[str] = ""
    _cache_hit_field_path: ClassVar[tuple[str, ...]] = ("prompt_tokens_details", "cached_tokens")

    def capabilities(self, model: str) -> ModelCapabilities:
        return ModelCapabilities(
            vision=self._vision,
            multimodal_video=self._multimodal_video,
            supports_structured_output=self._supports_structured_output,
            ctx_limit=self._ctx_limit,
            default_max_tokens=self._default_max_tokens,
            supported_reasoning_efforts=self._supported_reasoning_efforts,
            default_reasoning_effort=self._default_reasoning_effort,
            cache_hit_field_path=self._cache_hit_field_path,
        )

    def prepare_request(
        self,
        *,
        model: str,
        messages: list[dict],
        tools: list | None,
        max_tokens: int | None,
        stream: bool,
        thinking: str,
        response_format: dict | None,
        reasoning_effort: str,
        candidate: dict[str, Any],
    ) -> ProviderRequest:
        body: dict[str, Any] = {
            "model": model,
            "messages": messages,
        }
        if max_tokens is not None:
            body["max_tokens"] = max_tokens
        if tools:
            body["tools"] = tools
            body["tool_choice"] = "auto"
        if response_format is not None and not tools:
            body["response_format"] = response_format
        if stream:
            body["stream"] = True
            body["stream_options"] = {"include_usage": True}

        # 构造 headers 和 url
        base_url = candidate.get("base_url") or self._default_base_url
        if not base_url:
            raise ConfigurationError(
                f"Provider '{self.name}' candidate '{candidate.get('id')}' has no base_url"
            )
        url = self._endpoint_for(base_url)

        headers = {"Content-Type": "application/json"}
        api_key = str(candidate.get("api_key") or "").strip()
        # 本地代理（无 key）约定：值为 "lmstudio"/"dummy"/"" 时不发 Authorization
        if api_key and api_key.lower() not in ("lmstudio", "dummy", ""):
            headers["Authorization"] = f"Bearer {api_key}"

        return ProviderRequest(
            method="POST",
            url=url,
            headers=headers,
            body=body,
            stream=stream,
        )

    def _endpoint_for(self, base_url: str) -> str:
        """OpenAI 兼容：优先 /v1/chat/completions，缺 /v1 时补一次。"""
        normalized = base_url.rstrip("/")
        # OpenAI 官方 / 主流代理都接受 /v1/chat/completions
        return f"{normalized}/chat/completions"
```

### 4.3 内置注册（`providers/builtin_providers.py`）

```python
# 这一个文件就是"加新厂商"的入口。
# 加 Kimi / GLM / MiMo / Ollama 这种纯 OpenAI 兼容的 = 1 行
# 加 MiniMax-M3 / DeepSeek / Anthropic = 引用 Provider 文件

from .builtin_providers import register_all
from .deepseek import DeepSeekProvider
from .minimax import MiniMaxProvider
from .anthropic import AnthropicProvider
from .codex import CodexProvider


def bootstrap_providers() -> None:
    """在 cyrene 启动时调用一次。注册全部内置 provider。"""
    # 1. 注册有自定义行为的 provider
    register_provider(DeepSeekProvider())
    register_provider(MiniMaxProvider())
    register_provider(AnthropicProvider())
    register_provider(CodexProvider())

    # 2. 声明式注册纯 OpenAI 兼容的
    register_all()


# builtin_providers.py 内部
def register_all() -> None:
    register_openai_compatible(
        name="kimi",
        default_base_url="https://api.moonshot.cn/v1",
        default_model="kimi-k2-7",
        ctx_limit=256_000,
    )
    register_openai_compatible(
        name="glm",
        default_base_url="https://open.bigmodel.cn/api/paas/v4",
        default_model="glm-4.6",
        ctx_limit=200_000,
        vision=True,
    )
    register_openai_compatible(
        name="mimo",
        default_base_url="https://api.xiaomimimo.com/v1",
        default_model="mimo-v2.5-pro",
        ctx_limit=128_000,
    )
    register_openai_compatible(
        name="openrouter",
        default_base_url="https://openrouter.ai/api/v1",
        vision=True,
    )
    register_openai_compatible(
        name="ollama",
        default_base_url="http://localhost:11434/v1",
        vision=True,
    )
    register_openai_compatible(
        name="vllm",
        default_base_url="http://localhost:8000/v1",
        vision=True,
    )
    register_openai_compatible(
        name="qwen",
        default_base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
        default_model="qwen-max",
        ctx_limit=128_000,
        vision=True,
    )
    register_openai_compatible(
        name="minimax",
        default_base_url="https://api.minimaxi.com/v1",
        default_model="MiniMax-M3",
        ctx_limit=1_000_000,
        vision=True,
        multimodal_video=True,
    )
    # 注意：上面这个 "minimax" 注册会被显式 MiniMaxProvider 覆盖（register_provider 抛错）。
    # 实际写法：先 register MiniMaxProvider，再调用 register_openai_compatible 但用不同 name
    # 或：直接不声明 "minimax" 这个 OpenAI 兼容注册，只在 MiniMaxProvider 里实现
```

> 上面示例最后那条注释说明一个细节：声明式注册适合"完全 OpenAI 兼容"的厂商；有自定义行为的厂商（M3 的 thinking、DeepSeek 的 DSML）必须走 Provider 子类，不能被声明式注册覆盖。

---

## 5. 各 Provider 实现骨架

### 5.1 `providers/deepseek.py`

```python
class DeepSeekProvider(OpenAICompatibleProvider):
    """DeepSeek V3/V4 特有：DSML 文本回退、thinking 字段、reasoning_effort。"""

    name = "deepseek"

    _default_base_url = "https://api.deepseek.com/v1"
    _default_model = "deepseek-v4-flash"
    _supported_reasoning_efforts = ("low", "medium", "high", "xhigh", "max")
    _default_reasoning_effort = "high"
    _cache_hit_field_path = ("prompt_tokens_details", "cached_tokens")

    def prepare_request(self, *, model, messages, tools, max_tokens, stream, thinking,
                        response_format, reasoning_effort, candidate):
        req = super().prepare_request(
            model=model, messages=messages, tools=tools, max_tokens=max_tokens,
            stream=stream, thinking=thinking, response_format=response_format,
            reasoning_effort=reasoning_effort, candidate=candidate,
        )

        # 注入 thinking 字段
        if thinking == "auto" or thinking == "enabled":
            req.body["thinking"] = {"type": "enabled"}
        # DeepSeek "disabled" 仍开启（保持历史行为）
        elif thinking == "disabled":
            req.body["thinking"] = {"type": "enabled"}

        # reasoning_effort 映射
        effort = (reasoning_effort or "").strip().lower()
        if effort in {"low", "medium", "high"}:
            effort = "high"
        elif effort in {"xhigh", "max"}:
            effort = "max"
        else:
            effort = "high"
        req.body["reasoning_effort"] = effort

        # 端点偏好：先 /v1/chat/completions，再 /chat/completions
        req = dataclasses.replace(req, urls=(req.url, req.url.replace("/v1/chat/completions", "/chat/completions")))
        return req

    def parse_response(self, data: dict) -> dict:
        msg = super().parse_response(data)
        return _normalize_dsml_tool_calls(msg, tools=None)  # tools 由 sanitize 那边再过一遍

    async def parse_stream(self, lines, *, tools=None):
        dsml_filter = _DsmlStreamFilter()
        async for event in super().parse_stream(lines, tools=tools):
            if event.type == "reply_delta" and event.delta:
                event = dataclasses.replace(event, delta=dsml_filter.feed(event.delta))
            yield event
        # 流结束 flush
        tail = dsml_filter.flush()
        if tail:
            yield StreamEvent(type="reply_delta", delta=tail)

    def sanitize_messages(self, messages, *, preserve_tool_reasoning=False):
        # DeepSeek V4 工具轮次需要保留 reasoning_content
        result = []
        for msg in messages:
            if msg.get("role") == "assistant" and msg.get("tool_calls"):
                # 保留
                result.append(dict(msg))
            else:
                # 剥 reasoning_content
                msg = dict(msg)
                msg.pop("reasoning_content", None)
                result.append(msg)
        return result
```

### 5.2 `providers/minimax.py`（M3 完整能力）

```python
class MiniMaxProvider(OpenAICompatibleProvider):
    """MiniMax M3 / M2.x 系列。OpenAI 兼容路径 + Interleaved Thinking。"""

    name = "minimax"

    _default_base_url = "https://api.minimaxi.com/v1"
    _default_model = "MiniMax-M3"
    _vision = True
    _multimodal_video = True
    _ctx_limit = 1_000_000
    _default_max_tokens = 131072
    _cache_hit_field_path = ("prompt_tokens_details", "cached_tokens")
    _reasoning_response_field = "reasoning_content"

    def capabilities(self, model: str) -> ModelCapabilities:
        base = super().capabilities(model)
        is_m3 = "MiniMax-M3" in model or "m3" in model.lower()
        return dataclasses.replace(
            base,
            supports_thinking=True,
            preserve_reasoning_in_history=True,
            # M3 原生模式（reasoning_split 不开）thinking 嵌在 content 里，
            # 不可选择 effort。M3 split 模式（reasoning_split=True）才有 reasoning_details
            supported_reasoning_efforts=() if is_m3 else (),
        )

    def prepare_request(self, *, model, messages, tools, max_tokens, stream, thinking,
                        response_format, reasoning_effort, candidate):
        req = super().prepare_request(
            model=model, messages=messages, tools=tools, max_tokens=max_tokens,
            stream=stream, thinking=thinking, response_format=response_format,
            reasoning_effort=reasoning_effort, candidate=candidate,
        )

        is_m3 = "MiniMax-M3" in model
        if is_m3:
            # M3 思考模式：default adaptive；UI 显式 enabled/disabled 优先
            if thinking == "enabled":
                req.body["thinking"] = {"type": "enabled"}
            elif thinking == "disabled":
                req.body["thinking"] = {"type": "disabled"}
            else:  # "auto" / 缺省
                req.body["thinking"] = {"type": "adaptive"}

            # reasoning_split：candidate 显式配置则发；否则不开（保持原 content 嵌入 <think>）
            if candidate.get("reasoning_split"):
                # OpenAI 协议的扩展字段；MiniMax 服务端把它当 extra_body 处理
                req.body["reasoning_split"] = True

            # max_completion_tokens 是新参数名；老的 max_tokens 也可工作
            if "max_tokens" in req.body and max_tokens is not None:
                req.body["max_completion_tokens"] = req.body.pop("max_tokens")

        return req

    def parse_response(self, data: dict) -> dict:
        msg = super().parse_response(data)
        # MiniMax 把 reasoning 放在 reasoning_content（M3 嵌 content 模式）
        # 或 reasoning_details（split 模式）；两者都透传
        return msg  # 已经是 OpenAI 标准 + reasoning 字段直传

    def sanitize_messages(self, messages, *, preserve_tool_reasoning=False):
        # M3 Interleaved Thinking：必须完整回传 reasoning_content / reasoning_details
        return list(messages)  # 全保留，不剥
```

### 5.3 `providers/anthropic.py`

```python
class AnthropicProvider(Provider):
    """Anthropic Messages API。同时服务 Claude 官方和 MiniMax /anthropic 端点。

    base_url 不同：claude → https://api.anthropic.com，minimax → https://api.minimaxi.com/anthropic
    """

    name = "anthropic"

    def __init__(self) -> None:
        # SDK 延迟导入；anthropic 包未必已装
        try:
            from anthropic import AsyncAnthropic
            self._AsyncAnthropic = AsyncAnthropic
        except ImportError:
            self._AsyncAnthropic = None

    def capabilities(self, model: str) -> ModelCapabilities:
        is_claude = "claude" in model.lower()
        # claude-fable-5 / claude-mythos-5 都是 200K 上下文、视觉、按需 thinking
        return ModelCapabilities(
            vision=True,
            supports_thinking=True,
            supports_tools=True,
            supports_streaming=True,
            supports_structured_output=False,
            ctx_limit=200_000,
            default_max_tokens=8192,
            supported_reasoning_efforts=("low", "medium", "high"),
            default_reasoning_effort="",
            preserve_reasoning_in_history=True,  # Anthropic 多轮 tool_use 必须回传 thinking
            cache_hit_field_path=("usage", "cache_creation_input_tokens"),  # 简化；实际是 cache_read_input_tokens
        )

    def prepare_request(self, *, model, messages, tools, max_tokens, stream, thinking,
                        response_format, reasoning_effort, candidate):
        if self._AsyncAnthropic is None:
            raise ProviderError("anthropic package not installed. `pip install anthropic`")

        base_url = candidate.get("base_url") or "https://api.anthropic.com"
        api_key = str(candidate.get("api_key") or "").strip()

        # Anthropic SDK 是闭包式调用的；这里用 sdk_callable 让 dispatcher 直接调
        async def call(messages_in, **kwargs):
            client = self._AsyncAnthropic(api_key=api_key, base_url=base_url)
            return await client.messages.create(**kwargs)

        # 拆 system / messages
        system = None
        non_system = []
        for m in messages_in(messages):
            if m.get("role") == "system":
                if system is None:
                    system = m.get("content", "")
                else:
                    system += "\n\n" + m.get("content", "")
            else:
                non_system.append(m)

        # 转换 tools: OpenAI tools → Anthropic tools
        anthropic_tools = None
        if tools:
            anthropic_tools = [
                {
                    "name": t["function"]["name"],
                    "description": t["function"].get("description", ""),
                    "input_schema": t["function"].get("parameters", {"type": "object", "properties": {}}),
                }
                for t in tools
            ]

        kwargs = {
            "model": model,
            "max_tokens": max_tokens or 8192,
            "messages": non_system,
            "stream": stream,
        }
        if system:
            kwargs["system"] = system
        if anthropic_tools:
            kwargs["tools"] = anthropic_tools
        if thinking == "enabled":
            kwargs["thinking"] = {"type": "enabled", "budget_tokens": 4096}
        elif reasoning_effort:
            effort = reasoning_effort.lower()
            budget = {"low": 2048, "medium": 4096, "high": 8192}.get(effort, 4096)
            kwargs["thinking"] = {"type": "enabled", "budget_tokens": budget}

        return ProviderRequest(
            method="POST",
            url="",  # SDK 调，不走 HTTP
            sdk_callable=call,
            sdk_kwargs=kwargs,
            stream=stream,
        )

    def parse_response(self, data) -> dict:
        # data 是 anthropic.Message
        # 归一化为 OpenAI 标准 message
        from anthropic.types import Message
        if not isinstance(data, Message):
            raise ProviderError(f"Unexpected anthropic response type: {type(data)}")

        text_parts = []
        tool_calls = []
        for block in data.content:
            if block.type == "text":
                text_parts.append(block.text)
            elif block.type == "tool_use":
                tool_calls.append({
                    "index": len(tool_calls),
                    "id": block.id,
                    "type": "function",
                    "function": {
                        "name": block.name,
                        "arguments": json.dumps(block.input, ensure_ascii=False),
                    },
                })
            elif block.type == "thinking":
                # thinking block 不入 content；归一化时丢弃
                pass

        finish_reason = {
            "end_turn": "stop",
            "tool_use": "tool_calls",
            "max_tokens": "length",
            "stop_sequence": "stop",
        }.get(str(data.stop_reason), "stop")

        usage = {
            "input_tokens": data.usage.input_tokens,
            "output_tokens": data.usage.output_tokens,
            "cache_creation_input_tokens": getattr(data.usage, "cache_creation_input_tokens", 0) or 0,
            "cache_read_input_tokens": getattr(data.usage, "cache_read_input_tokens", 0) or 0,
        }

        return {
            "role": "assistant",
            "content": "".join(text_parts),
            "tool_calls": tool_calls or None,
            "usage": usage,
            "finish_reason": finish_reason,
            "model": data.model,
        }

    async def parse_stream(self, lines, *, tools=None):
        # 如果走 SDK 路径（最常见），SDK 内部已经处理了 stream；
        # dispatcher 应当用 SDK 的 event stream，绕开这里的 parse_stream
        raise NotImplementedError("Anthropic uses SDK streaming via sdk_callable")

    def normalize_usage(self, usage):
        cache_read = int(usage.get("cache_read_input_tokens") or 0)
        cache_create = int(usage.get("cache_creation_input_tokens") or 0)
        input_total = int(usage.get("input_tokens") or 0)
        # Anthropic 习惯：input_tokens 已经包含 cache 部分
        return {
            "input": max(0, input_total - cache_read - cache_create),
            "output": int(usage.get("output_tokens") or 0),
            "cache_hit": cache_read,
            "cache_miss": 0,
            "cache_write": cache_create,
            "reasoning": 0,
            "raw": dict(usage),
        }
```

### 5.4 `providers/codex.py`

```python
class CodexProvider(Provider):
    """Codex OAuth / ChatGPT 订阅旁路。复用现有 codex_provider 逻辑。"""

    name = "codex_oauth"

    def __init__(self) -> None:
        # codex_provider.py 保留（不要删除）；这里只做薄包装
        from cyrene.model_runtime import codex_provider
        self._codex = codex_provider

    def capabilities(self, model: str) -> ModelCapabilities:
        # Codex 模型的 capability 由 app-server 动态返回
        # 静态 fallback
        return ModelCapabilities(
            vision=True,
            supports_thinking=True,
            supports_tools=True,
            supports_streaming=True,
            ctx_limit=200_000,
            supported_reasoning_efforts=("low", "medium", "high", "xhigh"),
        )

    def prepare_request(self, **kwargs) -> ProviderRequest:
        # Codex 永远走 SDK，不构造 HTTP 请求
        return ProviderRequest(
            method="POST",
            url="",
            sdk_callable=self._call_codex,
            stream=kwargs.get("stream", False),
            timeout=kwargs.get("timeout", 120.0),
        )

    async def _call_codex(self, *, messages, tools, model, stream, stream_callback,
                            transport_callback, reasoning_effort, phase, **kwargs):
        from cyrene.model_runtime.codex_provider import get_codex_provider
        codex = get_codex_provider()
        return await codex.complete(
            messages=messages,
            tools=tools,
            model=model,
            phase=phase,
            reasoning_effort=reasoning_effort,
            stream_callback=stream_callback,
            transport_callback=transport_callback,
            timeout=kwargs.get("timeout", 120.0),
        )

    def parse_response(self, data) -> dict:
        # Codex 的 complete() 已经返回 OpenAI 标准 message 形态（历史约定）
        return data

    def should_cooldown_on_error(self, exc) -> bool:
        from cyrene.model_runtime.codex_provider import codex_error_should_cooldown
        return codex_error_should_cooldown(exc)
```

---

## 6. 调用流程重构

### 6.1 改写后的 `call_llm` 主干（伪代码）

```python
async def call_llm(messages, *, model_type, candidates, tools, max_tokens, stream,
                    stream_callback, thinking, response_format, caller, phase,
                    return_text, publish_events, record_usage, round_id, session_id, ...):
    # === Part 1: 候选解析（不变）===
    resolved = candidates if candidates is not None else _resolve_candidates(model_type)
    if not resolved:
        return ""
    resolved = _prioritize_last_success(resolved, model_type, session_id)

    # 上下文窗口预过滤（不变）
    request_tokens = _request_token_estimate(messages, tools)
    output_reserve = max(int(max_tokens or 0), 0)
    required_tokens = request_tokens + output_reserve
    resolved = [c for c in resolved if _candidate_fits(c, required_tokens)] or resolved

    # 冷却跳过（不变）
    available = [c for c in resolved if not _candidate_cooling(_candidate_key(c, session_id))]
    if not available:
        available = resolved

    # === Part 2: 逐候选调度（重构）===
    client, pool_key, pool_reused = _get_http_client(timeout)
    last_error = None
    for position, candidate in enumerate(available):
        # ----- 校验 provider 必填 -----
        provider_name = candidate.get("provider")
        if not provider_name:
            raise ConfigurationError(
                f"Model candidate '{candidate.get('id')}' missing 'provider' field. "
                f"Run migration: `cyrene migrate-providers`"
            )

        try:
            provider = get_provider(provider_name)
        except ConfigurationError as exc:
            last_error = exc
            continue

        # ----- 准备请求 -----
        caps = provider.capabilities(candidate.get("model", ""))
        try:
            provider_request = provider.prepare_request(
                model=candidate["model"],
                messages=messages,
                tools=tools,
                max_tokens=max_tokens,
                stream=stream,
                thinking=thinking,
                response_format=response_format,
                reasoning_effort=candidate.get("reasoning_effort", ""),
                candidate=candidate,
            )
        except Exception as exc:
            last_error = exc
            _set_candidate_cooldown(_candidate_key(candidate, session_id))
            continue

        # ----- 发送请求 -----
        try:
            if provider_request.sdk_callable:
                msg = await _execute_sdk(
                    provider, provider_request,
                    messages=messages, tools=tools, model=candidate["model"],
                    stream=stream, stream_callback=stream_callback,
                    phase=phase, reasoning_effort=candidate.get("reasoning_effort", ""),
                )
            elif stream:
                msg = await _execute_stream(
                    client, provider, provider_request, tools=tools,
                    stream_callback=stream_callback, candidate=candidate, ...
                )
            else:
                msg = await _execute_one_shot(
                    client, provider, provider_request, tools=tools,
                    candidate=candidate, ...
                )
        except (httpx.HTTPError, ProviderError) as exc:
            if not provider.should_cooldown_on_error(exc):
                logger.info("Provider %s error not cooling down: %s", provider_name, exc)
            else:
                _set_candidate_cooldown(_candidate_key(candidate, session_id))
            last_error = exc
            continue

        # ----- 成功路径（telemetry、记录、返回） -----
        # 这里大量内容与现状相同，省略
        # ...
        return msg

    # 全部失败
    if last_error:
        raise last_error
    return ""
```

### 6.2 三个执行 helper

```python
async def _execute_one_shot(client, provider, req, *, tools, candidate, ...):
    resp = await client.post(req.url, json=req.body, headers=req.headers, timeout=req.timeout)
    if resp.status_code != 200:
        resp.raise_for_status()
    data = resp.json()
    msg = provider.parse_response(data)
    msg["usage"] = provider.normalize_usage(data.get("usage"))
    return msg


async def _execute_stream(client, provider, req, *, tools, stream_callback, ...):
    accumulated_text: list[str] = []
    reasoning_parts: list[str] = []
    tool_fragments: dict[int, dict] = {}
    usage: dict = {}
    finished_reason: str | None = None

    async with client.stream("POST", req.url, json=req.body, headers=req.headers, timeout=req.timeout) as resp:
        if resp.status_code != 200:
            resp.raise_for_status()
        async for line in resp.aiter_lines():
            line = str(line or "").strip()
            if not line or not line.startswith("data:"):
                continue
            line = line[5:].strip()
            if not line or line == "[DONE]":
                continue
            try:
                data = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(data.get("usage"), dict):
                usage = data["usage"]
            # Provider 自己的 parse_stream 处理 delta → StreamEvent
            async for event in provider.parse_stream(_singleton_async_iter(line), tools=tools):
                if event.type == "reply_delta" and event.delta:
                    accumulated_text.append(event.delta)
                    if stream_callback:
                        await stream_callback({"type": "reply_delta", "delta": event.delta})
                elif event.type == "reasoning_delta" and event.delta:
                    reasoning_parts.append(event.delta)
                    if stream_callback:
                        await stream_callback({"type": "reasoning_delta", "delta": event.delta})
                elif event.type == "tool_call_delta" and event.tool_call:
                    _merge_tool_call_delta(tool_fragments, event.tool_call)
                # ... 其余 event type 转发

    return {
        "role": "assistant",
        "content": "".join(accumulated_text),
        "tool_calls": _finalize_tool_call_fragments(tool_fragments) or None,
        "reasoning_content": "".join(reasoning_parts) or None,
        "usage": provider.normalize_usage(usage),
        "finish_reason": finished_reason or "stop",
    }


async def _execute_sdk(provider, req, *, messages, tools, model, stream, stream_callback,
                        phase, reasoning_effort):
    # Codex 走自己路径：把 stream_callback 包一下
    cb = stream_callback
    if req.stream and stream_callback:
        async def wrapped(event):
            await stream_callback(event)
        cb = wrapped
    raw = await req.sdk_callable(messages=messages, tools=tools, model=model,
                                  stream=req.stream, stream_callback=cb,
                                  phase=phase, reasoning_effort=reasoning_effort)
    # raw 可能是 OpenAI 标准 message（Codex）或其他形态（Anthropic）
    if hasattr(raw, "content") and hasattr(raw.content, "__iter__") and not isinstance(raw, dict):
        return provider.parse_response(raw)
    return raw
```

### 6.3 `_normalized_candidate` 改造

```python
def _normalized_candidate(raw, index, *, active_model, active_base_url, active_api_key):
    # ... 现有逻辑 ...
    provider_name = str(raw.get("provider") or "").strip()
    if not provider_name:
        # 不再 silently 推断；让 dispatcher 抛 ConfigurationError
        # 启动时跑 migration 脚本填上
        provider_name = ""  # 留空，dispatcher 检查
    # ... 其它字段继承 ...
    return {
        "id": ...,
        "model": ...,
        "name": ...,
        "provider": provider_name,
        "base_url": ...,
        # ...
    }
```

---

## 7. 文件级改动清单

### 7.1 新增文件

| 路径 | 行数估计 | 内容 |
|---|---|---|
| `model_runtime/providers/__init__.py` | ~20 | 公开导出 |
| `model_runtime/providers/base.py` | ~250 | Provider 协议 + 数据类 + 默认实现 |
| `model_runtime/providers/registry.py` | ~80 | 注册表 |
| `model_runtime/providers/openai_compatible.py` | ~200 | OpenAI 兼容基类 + `register_openai_compatible` |
| `model_runtime/providers/deepseek.py` | ~150 | DeepSeek 特有 |
| `model_runtime/providers/minimax.py` | ~180 | MiniMax M3 + M2.x |
| `model_runtime/providers/anthropic.py` | ~280 | Anthropic Messages |
| `model_runtime/providers/codex.py` | ~120 | 薄包装 Codex |
| `model_runtime/providers/builtin_providers.py` | ~80 | 1 行注册：kimi/glm/mimo/openrouter/ollama/vllm/qwen |
| `model_runtime/migration/infer_provider_from_legacy.py` | ~120 | 一次性 CLI：扫老配置 + 写 migration 标记 |
| `tests/test_providers/` | ~600 | 各 Provider 单元测试 + 集成测试 |
| **合计** | **~2080** | |

### 7.2 改写文件

| 路径 | 改前 | 改后 | 删 |
|---|---|---|---|
| `model_runtime/client.py` | 2656 行 | ~1500 行 | -1156 行 |
| `model_runtime/__init__.py` | 1 行 | ~15 行（导出 provider registry） | +14 |
| `model_runtime/call_llm.py` | 11 行 | 不变 | 0 |
| `route/settings/general.py`（model 规范化） | ~30 行（`is_deepseek` 启发式） | ~10 行（直接读 candidate["provider"]） | -20 |
| `webui/frontend/workbench-welcome.jsx`（onboarding 厂商预设） | 0 | ~150 行（加预设下拉） | +150 |
| `webui/frontend/settings-overlay.jsx`（Settings → Models 列表显示厂商徽标） | 0 | ~80 行 | +80 |
| `webui/frontend/workbench-model.jsx`（model picker 读 capabilities） | 0 | ~30 行 | +30 |
| `docs/configuration.md` | 现状 | 补"Provider 选择"小节 | +50 |
| `docs/usage.md` | 现状 | 补"添加新厂商"流程 | +30 |

### 7.3 删除

- `model_runtime/client.py` 内的 `_is_official_deepseek_base_url`（移到 deepseek.py）
- `model_runtime/client.py` 内的 `_normalize_dsml_tool_calls`（移到 deepseek.py）
- `model_runtime/client.py` 内的 `_DsmlStreamFilter`（移到 deepseek.py）
- `model_runtime/client.py` 内的 `is_deepseek` 字符串判断（全部清掉）

### 7.4 保留不动

- `model_runtime/codex_provider.py`（逻辑保留；`providers/codex.py` 薄包装）
- `model_runtime/codex_cli.py`
- `model_runtime/pricing.py`（加 helper：`lookup_by_provider_model(provider_name, model_name)`，可选）
- `model_runtime/messages.py`
- `model_runtime/errors.py`
- `model_runtime/image_generation.py`（独立子模块）
- `model_runtime/opencv_runtime.py`（独立子模块）
- agent loop / Workbench / SSE event / context_debug / 全套测试

---

## 8. 实施步骤（推荐顺序）

每一步都是独立可发布的 PR，最后做整体回归。

| 步骤 | 范围 | 预计工时 | 验证 |
|---|---|---|---|
| **0. 前置** | 写 `providers/base.py` + `providers/registry.py` + `providers/openai_compatible.py` + `providers/builtin_providers.py` + `bootstrap_providers()`。**不动** `client.py`。 | 0.5 天 | 启动时 `cyrene provider list` 命令可看到所有内置 provider；不影响运行 |
| **1. 抽出 DeepSeek** | 写 `providers/deepseek.py`；改 `call_llm` 让它在 `"deepseek"` provider 路径上走新接口，OpenAI 兼容路径**保持原代码**。双轨运行，老路径作为 fallback。 | 1 天 | DeepSeek 用户跑回归（多轮 tool call、reasoning 回传、DSML 文本回退） |
| **2. 抽出 Codex** | 写 `providers/codex.py`（薄包装现有 `codex_provider`）；改 `call_llm` 让 `"codex_oauth"` 走新接口。Codex 用户的 OAuth 登录、模型发现、quota 全链路不变。 | 0.5 天 | Codex 用户登录、跑对话、验 quota 错误正确显示 |
| **3. 通用 OpenAI 兼容** | 改 `call_llm` 让所有非 `deepseek`/`codex_oauth` 的候选也走 `OpenAICompatibleProvider`。验证默认 DeepSeek 配置（active_model=deepseek-chat, base_url=...deepseek）仍工作。 | 1 天 | 跑回归 180 测试；含多模型配置（Kimi/GLM/MiMo 等用声明式注册跑通） |
| **4. 加 MiniMax** | 写 `providers/minimax.py` + 修 `pricing.py` 价格；前端加 onboarding 厂商预设；UI 加"reasoning_split"开关。 | 1 天 | MiniMax M3 跑通多轮 tool call + reasoning 流式 + cache 费用计算 |
| **5. 加 Anthropic** | 写 `providers/anthropic.py`（含 MiniMax `/anthropic` 端点）；前端加 Anthropic 厂商预设。 | 1.5 天 | Claude 与 MiniMax-Anthropic 两端点切换 |
| **6. 切默认路径** | 把"双轨 fallback"删掉，让所有候选统一走 Provider 协议；删除 client.py 里的旧代码。 | 0.5 天 | 全量回归 |
| **7. Migration 脚本** | `cyrene migrate-providers` 命令：扫老配置（无 provider 字段），按 model 名/base_url 推断填上；写 migration marker。 | 0.5 天 | 老用户启动时自动 migrate；可手动跑 `cyrene migrate-providers --dry-run` 预览 |
| **8. 文档 + PR 描述** | 写 `docs/configuration.md` 的 Provider 选择章节；`docs/usage.md` 的"添加新厂商"小节；CHANGELOG。 | 0.5 天 | docs build 通过 |
| **9. 清理 + 测试** | 删 `_is_official_deepseek_base_url` 等；补充各 Provider 单元测试；CI 跑全量。 | 1 天 | CI 全绿 |
| **合计** | | **~8 天** | |

### 8.1 风险与回滚

| 风险 | 触发 | 回滚策略 |
|---|---|---|
| DeepSeek 迁移后多轮 reasoning 路径出问题 | M3 用户报告 reasoning 中断 | 步骤 1 双轨运行；切默认前删 fallback |
| Codex OAuth 重构破坏 quota 行为 | Codex 用户报告 quota 不刷新 | 步骤 2 单元测试覆盖 quota exhausted 路径 |
| OpenAI 兼容声明式注册的 cache 字段不对 | GLM/Kimi 等 cache 费用不准 | 步骤 3 跑 mock upstream usage 测试 |
| Anthropic SDK 缺包 | 用户没装 anthropic | 启动时 import 失败给明确提示，不影响其他 provider |
| Migration 脚本写错 | 老用户配置被覆盖 | 步骤 7 强制 dry-run 预览；写 `data/migration_*.json` 备份 |
| Pricing 目录与 M3 实际账单不符 | 费用估算偏差 | 报告里已标注；修 pricing.py 即可，不影响架构 |

---

## 9. 测试策略

### 9.1 单元测试（`tests/test_providers/`）

```
test_providers/
├── test_registry.py             # register/get/list；同名校验；missing name 抛错
├── test_openai_compatible.py    # 默认 base_url、headers、payload、stream_options
├── test_deepseek.py             # thinking 注入、DSML 解析（mock upstream）
├── test_minimax.py              # M3 thinking/split/max_completion_tokens、cache 字段、reasoning_content 回传
├── test_anthropic.py            # system 分离、thinking.budget_tokens、content blocks → OpenAI 形态
├── test_codex.py                # SDK call 包装、quota error 不冷却
└── test_dispatcher.py           # call_llm 用 mock provider 跑端到端
```

### 9.2 录制响应回放测试

为每个 Provider 准备一份"真实录制的 upstream response"（CI 里 `tests/fixtures/provider_responses/*.json`）：
- `deepseek_tool_call.json` — 多 tool call
- `deepseek_dsml_text_fallback.json` — DSML 文本回退
- `minimax_m3_thinking.json` — reasoning_content
- `minimax_m3_thinking_split.json` — reasoning_details
- `anthropic_tool_use.json` — content blocks
- `anthropic_thinking.json` — thinking block
- `openai_compatible_vision.json` — image_url 消息

每个 fixture 跑 `provider.parse_response(data)` 验证归一化结果与 OpenAI 标准 message 完全一致。

### 9.3 集成测试

- `tests/test_call_llm_dispatch.py`：用 mock provider 替换真实注册，跑 `call_llm` 端到端
- 保留现有 180 自动化测试不破

### 9.4 手工验证

- onboarding 选 "MiniMax (China)"、跑多轮 tool call 看 reasoning 连续
- onboarding 选 "Anthropic"、跑多轮 tool call 看 thinking block 回传
- Settings → Models 列表里手动加一个 Kimi candidate（无 `provider` 字段），看 dispatcher 报明确错误
- 跑 `cyrene migrate-providers --dry-run` 看老配置的推断结果

---

## 10. 后续路线（不在本设计，但预留扩展点）

| 后续方向 | 扩展点 | 工作量 |
|---|---|---|
| Gemini 原生 | 新增 `providers/gemini.py`（约 200 行，结构与 anthropic.py 类似） | 2-3 天 |
| OpenAI Responses API | 新增 `providers/openai_responses.py`（OpenAI 的新协议，含 web_search、code_interpreter） | 3-5 天 |
| PyPI 第三方插件 | 在 `registry.py` 加 `importlib.metadata.entry_points(group="cyrene.providers")` 扫描 | 1 天 + 文档 |
| 真正的 Canonical Message 类型 | 把 `call_llm` 返回值改为 `dataclass`/`Pydantic`；agent loop 类型注解更新 | 3-5 天 |
| 流式 content block 协议层 | 把 OpenAI delta 解析也抽出来；Anthropic/Gemini 不再走 SDK 直传 | 1 周 |
| Provider 内部 hot-swap | 在运行时切 provider 不重启（如用户改 base_url） | 0.5 天 |

---

## 11. 决策记录（ADR 摘要）

| 决策 | 选项 | 选 | 理由 |
|---|---|---|---|
| Provider 抽象范围 | A. 5 方法 / B. 12 hooks + 声明式 / C. PyPI | **B** | 90% 厂商零代码；架构债清得最干净 |
| 发现机制 | 子类 / 声明式注册 / PyPI entry point | **声明式注册** | 内部产品不需 PyPI 复杂度；足够灵活 |
| 向后兼容 | 字符串推断 / 报错 / migration 脚本 | **报错 + migration 脚本** | 强制显式 provider；migration 一次跑完老数据 |
| Agent loop 接口 | OpenAI 标准 / Canonical Message 类型 | **OpenAI 标准** | 改动面小；现有 180 测试不动 |
| OpenAI 兼容基类 | ABC / Protocol / mixin | **ABC** | 子类有清晰覆写提示；运行时类型检查比 Protocol 直观 |
| 端点 fallback 顺序 | Provider 决定 / dispatcher 决定 | **Provider 决定** | DeepSeek 偏好、Anthropic 单端点语义不同 |
| stream 归一化 | Provider 内部 / dispatcher 通用 | **Provider 内部** | DSML 剥除、Anthropic content block delta 是 provider 特性 |
| 缓存字段路径 | 硬编码 `prompt_tokens_details.cached_tokens` / 声明式 | **声明式** (`cache_hit_field_path`) | Anthropic / Gemini / OpenAI 路径都不同 |
| Codex 接入 | 重写 / 薄包装 | **薄包装** | 现有 codex_provider.py 66KB 已稳定；不重写 |
| Migration 触发时机 | 启动时自动 / 手动命令 / 强制 | **手动命令 + 启动时检测** | 启动时检测到无 provider 字段时给明确提示；不自动改用户数据 |

---

## 12. 总结

**实现后**：
- `client.py` 从 2656 行降到 ~1500 行
- 加 Kimi/GLM/MiMo/Ollama/OpenRouter/Qwen/任何 OpenAI 兼容 = 1 行
- 加 DeepSeek/MiniMax-M3 = 1 个 ~150 行 Provider 文件
- 加 Anthropic（Claude + MiniMax /anthropic）= 1 个 ~280 行 Provider 文件
- 所有 `is_deepseek in model.lower()` 字符串硬编码消失
- 老的 `_normalize_dsml_tool_calls` / `_DsmlStreamFilter` 隔离到 deepseek.py，不再污染主流程
- Codex 走 Provider 协议，quota 等特殊行为通过 `should_cooldown_on_error` 表达

**总工时**：~8 个工作日（1.5 周）

**下一步**：
1. 你 review 本设计
2. 确认后我可以做：
   - 步骤 0 + 1 + 2（核心抽象 + DeepSeek + Codex 抽出）的代码 + 测试
   - 跑通后做步骤 3 + 4（OpenAI 兼容 + MiniMax）
   - 最后做步骤 5（Anthropic）
3. 每步独立 PR，独立可回滚
