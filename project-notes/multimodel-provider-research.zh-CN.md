# 多模型 Provider 接入研究报告 — MiniMax 及更广泛兼容

> **目标**：弄清 Cyrene 现状、给出让 Cyrene 兼容 MiniMax（含 MiniMax-M3）以及其它多模型（Claude / Gemini / Kimi / GLM / MiMo / OpenRouter …）的最小可行路径与最佳重构路径，并量化每个阶段的工作量与风险。
> **范围**：模型调用层（`cyrene.model_runtime`）、消息/工具/流式规范化、候选解析、设置存储、UI（onboarding + Settings → Models + 模型选择器）。
> **不做**：embedding 端、TTS、Image generation 的多 provider 改造（这些已经有独立子模块，按相同思路单独立项）。

---

## 0. TL;DR（先看结论）

- **MiniMax（含 MiniMax-M3）已经能在 Cyrene 中跑起来**——只要用户在"自定义模型"里填 `https://api.minimaxi.com/v1`（或国际 `https://api.minimax.io/v1`）+ `MiniMax-M3` + 自己的 API Key，配额目录里也已经有了 `minimax-m3` 的价格条目。`provider` 字段会落成默认的 `openai_compatible`，`_build_payload` 生成的就是标准 OpenAI Chat Completions 请求，M3 完整支持。
- **但有 4 处 MiniMax 特有的"高级能力"现在没启用**：
  1. `thinking: {"type": "adaptive"}`（Interleaved Thinking）— 当前 `_build_payload` 只在 `is_deepseek` 分支里下发 `thinking`，M3 拿不到。
  2. `reasoning_split: true`（让 thinking 单独进 `reasoning_details` 字段，避免 `<think>…</think>` 嵌在 `content` 里）— 当前没有 `extra_body` 注入路径。
  3. `sanitize_messages_for_llm` 当前默认 `preserve_tool_reasoning=False`，会把 `reasoning_content` 剥掉；M3 的 Interleaved Thinking 明确要求完整回传推理字段，**否则多轮工具调用上下文会断**。
  4. `usage.prompt_tokens_details.cached_tokens` — 当前 `_normalized_usage` 不读这个字段，cache-hit 成本估算会失真。
- **其它模型能不能跑**取决于它是否兼容 OpenAI Chat Completions：
  - ✅ 纯 OpenAI 兼容（Kimi、GLM、MiMo、OpenRouter、vLLM/Ollama、绝大多数本地代理）— 不用改代码就能用。
  - ⚠️ 协议相似但有偏离（DeepSeek 已特殊处理、MiniMax 需要补 thinking/split/cache 字段）。
  - ❌ 协议不同（Anthropic 原生 Messages、纯 Gemini 原生）— 需要一个真正的 provider 抽象层才能干净接入。
- **关键架构债**：Cyrene 当前是"OpenAI 兼容"主导 + DeepSeek 字符串匹配补丁 + Codex OAuth 旁路，**没有任何 provider 抽象**。要支持 MiniMax 这种有 OpenAI 兼容 + Anthropic 兼容两条路、且 thinking 行为不一样的厂商，最干净的方式是引入轻量的 `Provider` 接口。
- **推荐分三阶段**：① 补 MiniMax-M3 的 OpenAI 兼容路径（最小改动，可上线）→ ② 引入 Provider 抽象，迁移 DeepSeek/Codex 走同一路径 → ③ 把 Claude、Gemini、Kimi 通过同一抽象接入。

---

## 1. Cyrene 当前模型接入架构

### 1.1 关键文件

| 文件 | 作用 | 大小 |
|---|---|---|
| `src/cyrene/model_runtime/client.py` | 统一 LLM 调用入口 `call_llm`、候选解析、流式、工具归一化、token 计量 | 2656 行 |
| `src/cyrene/model_runtime/pricing.py` | 内置模型价格目录（含 GPT-5.5、Claude-Fable-5、MiniMax-M3、DeepSeek-V4、GLM-5.2、Kimi-K2.7、MiMo-V2.5 等） | 209 行 |
| `src/cyrene/model_runtime/codex_provider.py` | Codex OAuth / OpenAI app-server 旁路 | 66 KB |
| `src/cyrene/model_runtime/codex_cli.py` | Codex CLI 二进制管理 | 16 KB |
| `src/cyrene/model_runtime/messages.py` | 工具参数解析辅助 | 4.8 KB |
| `src/cyrene/model_runtime/errors.py` | httpx 错误格式化 | 1.4 KB |
| `src/cyrene/model_runtime/image_generation.py` | 图片生成（独立路径） | 7.1 KB |
| `src/cyrene/model_runtime/opencv_runtime.py` | 视觉/多模态运行时（独立） | 13 KB |
| `src/cyrene/call_llm.py` | **仅做 `sys.modules[__name__] = _client` 的别名**，旧引用兼容 | 11 行 |
| `src/route/settings/general.py` | `/api/settings/openai*` 路由，包括 `save_and_test_llm_setup` | — |
| `src/webui/frontend/workbench-welcome.jsx` | onboarding 表单（llmSource 切换：custom/codex） | 672 行 |
| `src/webui/frontend/workbench-model.jsx` | Settings → Models 面板 | 1028 行 |

### 1.2 候选解析流程

`get_models()` 返回一个 dict 列表，每项至少含 `{id, model, name, api_key, base_url, vision_capable?, reasoning_effort?}`，可选的 `provider` 字段（缺省 `"openai_compatible"`，唯一显式取 `"codex_oauth"`）。

`_resolve_llm_candidates()`（`client.py:557`）做四件事：
1. 遍历 `get_models()`，每条调 `_normalized_candidate()` 规范化
2. 过滤掉 `index>0` 的 `codex_oauth`（OAuth 主选唯一）
3. 按 `(provider, model, base_url, api_key)` 去重
4. `_inherit_sibling_keys()` 让"同源无 key"的候选继承前面有 key 的候选的 key（解决一个 Key 多 Base URL 的常见配置）

`_resolve_candidates(model_type)` 进一步分三类：
- `"primary"` — 用户配置主链
- `"secondary"` — 单独配置的二级模型（带 `ctx_limit` / `max_concurrency`），失败时回退到 primary
- `"vision"` — 用户单独配的视觉模型优先；fallback 到主链中 `vision_capable=True` 的项

**这里已经有了一个候选级 fallback 链 + 失败冷却（`_candidate_cooling`） + 会话亲和（`_prioritize_last_success`）+ 并发租赁（`candidate_lease`）的体系**——也就是说多模型/多 provider 的"基础设施"已经搭好一半。

### 1.3 当前协议契约

`_build_payload()`（`client.py:975`）生成的就是标准 OpenAI Chat Completions body：

```python
{
  "model": <model>,
  "messages": sanitize_messages_for_llm(messages, preserve_tool_reasoning=is_deepseek),
  "max_tokens": <int?>,
  "tools": <list?>, "tool_choice": "auto",
  "response_format": <dict?>,         # 仅在无 tools 时
  "stream": True,                     # 可选
  "stream_options": {"include_usage": True},
  # 仅 DeepSeek：
  "thinking": {"type": "enabled"},
  "reasoning_effort": "high"|"max",   # DeepSeek-V4 的 effort
}
```

请求头：
```python
{"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"}
# 跳过 key 值为 "lmstudio"/"dummy"/"" 的（本地代理无需鉴权）
```

端点选择（`_normalized_llm_endpoints`）：
- 官方 DeepSeek：先 `/v1/chat/completions`，再 `/chat/completions`（生产环境实测前者更稳）
- 其它：先 `<base>/chat/completions`，缺 `/v1` 时再试 `<base>/v1/chat/completions`

### 1.4 响应归一化

- `_message_from_upstream_payload`（`client.py:1072`）兼容三种返回容器：`choices[0].message` / `message` / `output.message` / `response`
- `_normalize_dsml_tool_calls`（`client.py:1125`）解析 DeepSeek 的 `||DSML||…||DSML||` 文本回退格式，转成标准 `tool_calls`
- `_normalize_tool_call_protocol`（`client.py:1390`）上层兜底
- `_handle_stream`（`client.py:2537`）按 SSE 行解析，`_DsmlStreamFilter` 在流式里同步剥 DSML 标记

### 1.5 工具/历史消息的"两段式思考保留"

```python
if not preserve_tool_reasoning:
    for message in messages:
        message.pop("reasoning_content", None)
else:
    # DeepSeek V4 requires complete reasoning_content replay for assistant
    # turns that performed tool calls. Reasoning from ordinary assistant
    # turns remains unnecessary and is omitted to avoid context growth.
    for message in messages:
        if not (message.get("role") == "assistant" and message.get("tool_calls")):
            message.pop("reasoning_content", None)
```

这是一段**专门为 DeepSeek 写的硬编码分支**。M3 的 Interleaved Thinking 需要同等待遇，但 `is_deepseek` 判断没有覆盖它。

### 1.6 现有"provider"概念的范围

- **存储**：`provider` 字段是模型 dict 的可选字段，**默认 `openai_compatible`**，显式只用于 `codex_oauth`
- **运行时**：仅在 `client.py:1880` 读出 `provider` 用于：
  - 决定是否走 `codex_oauth` 旁路（`if provider == "codex_oauth": codex.complete(...)`）
  - 其它所有 `openai_compatible` 都走标准 OpenAI 客户端
- **UI**：`workbench-welcome.jsx` 用 `llmSource` 状态区分 `custom` / `codex`；**没有"厂商"概念**，只是"自己填 Base URL 的"vs"走 Codex OAuth 的"

---

## 2. MiniMax API 形态（外部研究）

来源：`platform.minimaxi.com/docs/api-reference/text-chat`、`platform.minimaxi.com/docs/guides/text-m2-function-call`、`minimax-m2.com/docs/api/chat-completions`、第三方 crossmodel/kyma 文档。已交叉验证 2 个独立来源。

### 2.1 端点与认证

| 区域 | Base URL | 协议 |
|---|---|---|
| 中国大陆 | `https://api.minimaxi.com/v1` | OpenAI 兼容 |
| 中国大陆（旧） | `https://api.minimaxi.com/v1/text/chatcompletion_v2` | OpenAI 兼容（仍可用） |
| 国际 | `https://api.minimax.io/v1` | OpenAI 兼容 |
| 中国大陆 | `https://api.minimaxi.com/anthropic` | **Anthropic 兼容** |
| 国际 | `https://api.minimax.io/anthropic` | **Anthropic 兼容** |

认证全部是 `Authorization: Bearer <api_key>`（Anthropic 兼容路径用 `x-api-key`）。

### 2.2 当前模型矩阵

| 模型 | 上下文 | 最大输出 | 多模态 | 备注 |
|---|---|---|---|---|
| `MiniMax-M3` | 1M | 128K（512K 长上下文档） | 文本+图片+视频 → 文本 | 2026-06-01 发布，前沿编程/Agent；支持 Interleaved Thinking |
| `MiniMax-M2.7` | — | — | — | 递归自我改进版本 |
| `MiniMax-M2.7-highspeed` | — | — | — | M2.7 高速变体 |
| `MiniMax-M2.5` | — | — | — | 编程导向 |
| `MiniMax-M2.5-highspeed` | — | — | — | M2.5 高速变体 |
| `MiniMax-M2.1` | — | — | — | 230B 总参 / 10B 激活 |
| `MiniMax-M2.1-highspeed` | — | — | — | 高速变体 |
| `MiniMax-M2` | 200K | 128K | 文本 | function calling、推理、流式 |
| `MiniMax-M1` | 1M | 8K（推荐）/32K | — | 旧推理模型；M3 已替代 |
| `MiniMax-Text-01` | 1M | 2K | — | 老模型；支持 `response_format.json_schema` |

### 2.3 请求形态（OpenAI 兼容）

```json
POST /v1/chat/completions
{
  "model": "MiniMax-M3",
  "messages": [...],
  "tools": [...],                       // function calling
  "tool_choice": "auto",
  "thinking": {"type": "adaptive"},      // M3 独有；自适应思考开关
  "reasoning_split": true,               // extra_body；让 reasoning 进 reasoning_details
  "max_completion_tokens": 131072,       // 推荐；旧 max_tokens 已弃用
  "temperature": 1, "top_p": 0.95,
  "stream": true,
  "stream_options": {"include_usage": true},
  "service_tier": "standard" | "priority"  // priority = 1.5x 价格、优先调度
}
```

注意：
- `max_completion_tokens` 是新参数名；`max_tokens` 已弃用但仍可用
- `thinking` 是 M3/M2.x 系列专属
- `reasoning_split` 在 M3 下需要走 `extra_body`（OpenAI Python SDK 的扩展点）
- `messages` 支持 `content` 是 array（text + image_url + video_url），`name` 字段可选
- `response_format` 仅 M2.5/M2.1/M2/Text-01 支持 json_schema（M3 不支持强约束 json_schema）

### 2.4 响应形态

```json
{
  "id": "...",
  "choices": [{
    "finish_reason": "stop" | "tool_calls" | "length",
    "message": {
      "role": "assistant",
      "name": "MiniMax AI",
      "content": "...",
      "tool_calls": [{"id": "...", "type": "function", "function": {"name": "...", "arguments": "..."}}],
      "reasoning_content": "..." | null,        // v1 endpoint 的内联推理
      "reasoning_details": [{"type": "reasoning.text", "id": "...", "text": "..."}],
      "audio_content": ""
    }
  }],
  "model": "MiniMax-M3",
  "object": "chat.completion",
  "usage": {
    "total_tokens": 1604,
    "prompt_tokens": 1365,
    "completion_tokens": 239,
    "prompt_tokens_details": {"cached_tokens": 114}    // 缓存命中 token
  },
  "base_resp": {"status_code": 0, "status_msg": ""}
}
```

**Interleaved Thinking 关键约束**（官方文档原话）："在多轮 Function Call 对话中，必须将完整的模型返回（即 assistant 消息）添加到对话历史，以保持思维链的连续性"。也就是 `reasoning_content` / `reasoning_details` 必须在 tool 轮次回传时原样保留——和 DeepSeek V4 的 `preserve_tool_reasoning` 是同一种需求。

### 2.5 Anthropic 兼容形态

`/anthropic` 路径用标准 Anthropic Messages API：
- 头：`x-api-key: <key>`, `anthropic-version: 2023-06-01`
- 体：`{model, max_tokens, system, messages, tools, thinking: {type: "enabled"|"disabled", budget_tokens: N}}`
- `messages[].content` 是 content block 列表：`{type: "text"|"image"|"tool_use"|"tool_result"|"thinking"}`
- 响应：`response.content` 是 block 列表（不是 OpenAI 的单条 `content` + `tool_calls`）

Cyrene 当前完全没适配这条路径。

### 2.6 价格（已对齐 Cyrene 内置目录）

Cyrene 现有的 `pricing.py:49`：
```python
# https://platform.minimaxi.com/docs/guides/pricing-paygo
# Standard tier, <=512K input, at the permanent 50% rate shown by MiniMax.
(("minimax-m3",), {"input": 2.1, "output": 8.4, "cache_hit": 0.42, "currency": "CNY"}),
```

按 `$0.33/1M input`、`$1.32/1M output`、CNY/USD ≈ 7.25 折算：
- input: 0.33 × 7.25 = 2.39
- output: 1.32 × 7.25 = 9.57
- cache_hit: 0.066 × 7.25 = 0.48

**当前内置价格略偏低**（2.1 vs 算出来的 2.39、8.4 vs 9.57、0.42 vs 0.48）。注释里写"permanent 50% rate shown by MiniMax"，应该是当时确实有 50% 折扣活动价（截至 2026-06-25）。如果活动结束，建议重新核价。**这是个真实的小问题，但不影响接入。**

### 2.7 小结：MiniMax 是什么

- **协议**：同时提供 OpenAI 兼容 + Anthropic 兼容两条 API
- **能力**：完整 function calling、Interleaved Thinking（多轮工具间持续推理）、原生多模态（text+image+video→text）、cache（`prompt_tokens_details.cached_tokens`）、priority 调度
- **对 Cyrene 的契合度**：OpenAI 兼容路径的 wire format 与 Cyrene 现状**几乎完全对齐**，只差 thinking / split / cache 三块细节

---

## 3. 兼容性差距分析（精确到行）

### 3.1 已经"能跑"的部分

| 维度 | 状态 | 证据 |
|---|---|---|
| HTTP / SSE / Bearer 鉴权 | ✅ 完全支持 | `client.py:1935-1938, 2050-2057` |
| `model` / `messages` / `tools` / `tool_choice="auto"` | ✅ | `_build_payload:985-997` |
| `stream` + `stream_options.include_usage` | ✅ | `_build_payload:1003-1005` |
| 视觉消息（`content: [{type: "text"}, {type: "image_url", ...}]`） | ✅ | `sanitize_messages_for_llm` 透传 |
| `tool` role + `tool_call_id` 回传 | ✅ | `sanitize_messages_for_llm:833-873` 严格配对 |
| Tool call id 重排防止重复 | ✅ | 同上 |
| 缓存候选 / 会话亲和 / 失败冷却 | ✅ | `_prioritize_last_success` / `_candidate_cooling` |
| 价格目录 | ✅ | `pricing.py:49` |
| HTTP 端点 `/v1/chat/completions` 优先 | ✅ | `_normalized_llm_endpoints:447-450` |
| 响应 `choices[0].message` 解析 | ✅ | `_message_from_upstream_payload:1072` |
| `finish_reason: tool_calls` | ✅ | 上游 `call_llm` 后处理 |
| 文案 `usage.total_tokens` / `prompt_tokens` / `completion_tokens` | ✅ | `_normalized_usage:1398` |

### 3.2 "能跑但能力阉割"的部分（MiniMax-M3 特有）

| 缺失能力 | 影响 | 当前代码位置 |
|---|---|---|
| `thinking: {"type": "adaptive"}` 字段 | M3 默认是自适应 thinking；不传也能跑但会被服务端默认行为覆盖（多数情况下仍是自适应） | `_build_payload:1007-1027` 只在 `is_deepseek` 分支下发 |
| `reasoning_split: true` | M3 的 thinking 默认以 `<think>…</think>` 嵌在 `content` 里返回；要分到 `reasoning_details` 必须显式请求 | 无任何注入路径；`extra_body` 不存在 |
| `reasoning_content` / `reasoning_details` 在 tool 轮次的保留 | **多轮 tool call 时 Interleaved Thinking 链路会断**——模型下一轮看不到自己上一轮怎么想的 | `sanitize_messages_for_llm:813-825` 只在 `is_deepseek` 保留 |
| `usage.prompt_tokens_details.cached_tokens` 读取 | cache hit 成本被算成 input 价，**费用估算系统性偏高** | `_normalized_usage:1398-1431` 只读 `total_tokens`/`prompt_tokens`/`completion_tokens` |
| `name` 字段（每条 message 的发送者名） | MiniMax 原生支持，影响"系统消息署谁的名"行为 | 透传（无影响） |
| `service_tier` | priority 用户希望走付费优先队列 | 不可配置 |
| `max_completion_tokens`（新参数） | 旧 `max_tokens` 仍可工作，不影响 | 不可配置 |

### 3.3 "完全跑不起来"的部分

| 能力 | 原因 | 需求规模 |
|---|---|---|
| Anthropic 兼容路径（`/anthropic`） | 协议结构差异大（content blocks 列表 vs 单 content + tool_calls） | 需要 provider 抽象层 |
| `system` 字段 vs `messages[0].role="system"` | Anthropic 用 `system`；Cyrene 用 system message | 同上 |
| `thinking: {type: "enabled", budget_tokens: N}` | 形参不同（`budget_tokens`） | 同上 |
| 流式 content block（`event: content_block_start/delta/stop`） | 不是 OpenAI 的 `delta.content` | 同上 |

### 3.4 非 MiniMax 相关但耦合的代码债

1. **DeepSeek 字符串硬编码**：`"deepseek" in model.lower()` 散落在 `_build_payload`（多处）、`_normalized_llm_endpoints`、`_normalize_dsml_tool_calls`、`_DsmlStreamFilter`、`settings/general.py:356`（`is_deepseek` 决定 `supported_reasoning_efforts`）。**任何加新厂商都要复制这套分支。**
2. **Provider 字段没有验证**：`settings_store` 接受任意字符串，运行时只识别 `openai_compatible` 和 `codex_oauth`。
3. **UI 模型选择器（composer 中的 picker）**：从 model-picker 设计 QA 看，是按 `supportedReasoningEfforts` 过滤选项的；M3 的 `reasoning_split` 模式才有 effort 概念，原生 `<think>` 模式没 effort 字段——需要先在 provider 配置里声明"支持哪些 effort 值"。
4. **MCP / Vision / Embedding**：本研究范围外，按相同思路单独立项。

---

## 4. 推荐的实施方案

### 阶段 A：补 MiniMax-M3 OpenAI 兼容路径（最小改动，预计 1–2 个 PR）

**目标**：在不动 provider 抽象的前提下，让 M3 的 thinking / split / cache 三个能力落地。多轮工具调用 reasoning 回环正确，cache 费用估算正确。

**改动清单**（按文件）：

#### A1. `model_runtime/client.py`

- **`_build_payload`** — 扩展 thinking 触发条件：
  ```python
  is_deepseek = "deepseek" in model.lower()
  is_minimax_m3 = "minimax" in model.lower() and any(
      token in model.lower() for token in ("m3", "m2.7", "m2.5", "m2.1", "m2")
  )
  ```
  - DeepSeek 分支：维持 `thinking.type=enabled` + `reasoning_effort`（已有）
  - MiniMax M3 分支：默认 `thinking.type=adaptive`；当 caller 显式 `thinking="enabled"` 时发 `enabled`，`"disabled"` 时发 `disabled`
  - 加一个 `extra_body` 通道供 `reasoning_split`：
    ```python
    extra_body: dict[str, Any] = {}
    if is_minimax_m3 and reasoning_split_requested:
        extra_body["reasoning_split"] = True
    payload["_extra_body"] = extra_body   # call_llm 取出后塞入 extra_body
    ```
  - 或者：把 `reasoning_split` 当作 model 级 capability，由 `effective_model_settings` 暴露给 UI
- **`sanitize_messages_for_llm`** — `preserve_tool_reasoning` 触发条件扩展：
  ```python
  preserve_tool_reasoning = is_deepseek or is_minimax_m3
  ```
  或者更彻底：让 `sanitize_messages_for_llm` 接受一个 `providers` 集合（`{"deepseek", "minimax_m3"}`），由调用方传入，避免再次重复字符串判断。
- **`_normalized_usage`** — 读 `prompt_tokens_details.cached_tokens`：
  ```python
  details = usage.get("prompt_tokens_details") or {}
  cache_hit = int(details.get("cached_tokens") or 0)
  ```
  并把它放进返回的 `usage` dict（键名 `cache_hit_tokens` 或 `cached_tokens`，保持向后兼容）。
- **响应 message 归一化** — 当前 `_message_from_upstream_payload` 只取 `content`、`tool_calls`；MiniMax 还会带回 `reasoning_content` / `reasoning_details`，需要把它一起带出来给上层存到 `state.json`。这一改动不只影响 MiniMax，DeepSeek 也受益。

#### A2. `model_runtime/pricing.py`

- 重新核对 `minimax-m3` 价格（2.1/8.4/0.42 vs 2.39/9.57/0.48）。建议加注释说明"活动价"或"标准价"，并在 `_PRICE_CATALOG_VERIFIED_ON` 之外加一行 MiniMax 活动结束日期。
- 可选：加 `minimax-m2.7`、`minimax-m2.5-highspeed` 等条目到目录。

#### A3. `route/settings/general.py` (model 规范化函数，`:340-365` 区域)

- 仿照 `is_deepseek` 判定，加 `is_minimax_m3`，用它决定：
  - `supported_reasoning_efforts`（M3 在 split 模式下没有 effort 概念；返回 `[]` 或 `["auto"]`）
  - 默认 `vision_capable`（M3 支持多模态 → `True`）
- 暴露给前端的字段名要稳定，否则 Settings UI 和 model picker 都要同步改。

#### A4. `webui/frontend/workbench-welcome.jsx` + `settings-overlay.jsx`

- 在 "Custom model" 表单里**加一个厂商预设下拉**（preset selector）：
  ```text
  [Custom / OpenAI-compatible ▾]  ← 默认
  ├─ OpenAI
  ├─ DeepSeek
  ├─ MiniMax (China)         → 自动填 https://api.minimaxi.com/v1
  ├─ MiniMax (International)  → 自动填 https://api.minimax.io/v1
  ├─ Kimi
  ├─ GLM (Zhipu BigModel)
  ├─ MiMo (Xiaomi)
  ├─ OpenRouter
  └─ Local (Ollama/vLLM)     → 自动填 http://localhost:11434/v1
  ```
  选完预设只填 base_url 和 model 名；选 Custom 就要手填所有字段。
- 在 MiniMax 预设下：当 model 名以 `MiniMax-M3` 开头时，显示一个开关 "Enable reasoning_split (Interleaved Thinking)"——这是大多数用户需要的默认值 on。
- Settings → Models 面板的模型行里**显示厂商徽标**（用 base_url 后缀判断即可，不要新加字段）。

#### A5. 文档

- `docs/configuration.md`：补"MiniMax 设置"小节
- `docs/usage.md`：补"切换到 MiniMax"流程
- CHANGELOG 加条目

#### A6. 测试

- `tests/test_model_runtime.py` 加：
  - mock 一个返回 `prompt_tokens_details.cached_tokens` 的上游，验证 `_normalized_usage` 解析正确
  - mock 一个返回 `reasoning_details` 的消息，验证 `sanitize_messages_for_llm(preserve_tool_reasoning=True)` 保留
  - mock 一个带 `<think>…</think>` 标签的 content，验证流式剥除（仿 DSML filter）
- `tests/test_pricing.py` 加 M3 价格 lookup 用例
- 手工验证：onboarding 选 MiniMax、跑一段多轮 tool call、查 context-debug 看 reasoning 字段回传

**预计工时**：1 个后端 PR（~300-500 行改动 + 测试） + 1 个前端 PR（~150-250 行）。**1.5–2.5 天**。

**风险**：
- `extra_body` 注入路径在 OpenAI Python SDK 上叫 `extra_body`、在裸 httpx 上要自己塞进 payload 顶层。Cyrene 用的是裸 httpx（`client.py:21`），所以最简单是直接把 `reasoning_split` 加到 payload 顶层（这是 OpenAI 协议的扩展，MiniMax 文档说"通过 extra_body 传"是因为它们沿用 OpenAI Python SDK 的术语）。
- `prompt_tokens_details` 字段是 OpenAI 后来加的；Cyrene 解析时如果是 OpenAI 官方也会拿到，但当前代码没读。**这一改动对 OpenAI/DeepSeek 等所有 provider 都是净收益**，不存在"只对 MiniMax 生效"的副作用。

### 阶段 B：引入 Provider 抽象层（中等改动，预计 1 周）

**目标**：把"协议契约"和"产品行为"分离。新加厂商只需要写一个 Provider 子类，不再到处打补丁。

#### B1. 新增 `model_runtime/providers/` 目录

```
model_runtime/providers/
├── __init__.py            # 导出 get_provider(candidate) 工厂
├── base.py                # Provider 协议（Protocol + 抽象基类）
├── openai_compatible.py   # 默认：标准 OpenAI Chat Completions
├── deepseek.py            # DSML、thinking 字段、reasoning_effort、/v1 偏好
├── codex.py               # 现有 codex_provider.py 改写成 Provider
├── minimax.py             # M3 thinking/reasoning_split/cache、Anthropic 兼容可选
├── anthropic.py           # Claude / MiniMax-Anthropic
├── gemini.py              # Google Gemini（如果决定支持）
└── local.py               # Ollama / vLLM / LM Studio
```

#### B2. Provider 协议

```python
class Provider(Protocol):
    name: str                                    # "openai_compatible" / "deepseek" / "minimax" ...

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
        candidate: dict,
    ) -> ProviderRequest:
        """构造（payload 顶层字段, headers, endpoint 列表, extra_body 字典）。"""

    def normalize_message(self, raw: dict) -> dict:
        """把上游 message 字典归一到 Cyrene 内部 message 形态。"""

    def normalize_stream_chunk(self, chunk: dict) -> list[StreamEvent]:
        """把流式 chunk 转成统一的 StreamEvent（content_delta / tool_call_delta / reasoning_delta / usage / done）。"""

    def supported_reasoning_efforts(self, model: str) -> list[str]:
        """返回 ['low', 'medium', 'high'] 这种；M3 native 模式返回 []。"""

    def supports_vision(self, model: str) -> bool: ...
    def default_base_url(self) -> str | None: ...
    def endpoints(self, base_url: str) -> list[str]: ...
    def preserve_tool_reasoning(self) -> bool: ...
    def cache_hit_tokens(self, usage: dict) -> int: ...
```

#### B3. 选 Provider 的策略

```python
def get_provider(candidate: dict) -> Provider:
    provider = str(candidate.get("provider") or "openai_compatible").strip()
    if provider == "codex_oauth":
        return CodexProvider()
    model = str(candidate.get("model") or "").lower()
    base_url = str(candidate.get("base_url") or "").lower()
    # 显式 provider 优先；显式为 "openai_compatible" 走默认
    if provider == "deepseek" or "deepseek" in model or _is_official_deepseek_base_url(base_url):
        return DeepSeekProvider()
    if provider == "minimax" or "minimax" in model or "minimaxi" in base_url or "minimax.io" in base_url:
        return MiniMaxProvider()
    if provider == "anthropic":
        return AnthropicProvider()
    return OpenAICompatibleProvider()
```

判定顺序：**显式 provider 字段 → 模型名 → base_url → 默认 openai_compatible**。这样：
- 用户选"MiniMax" 预设 → `provider="minimax"` → 走 MiniMaxProvider
- 老用户没填 provider，但 model 写的是 `minimax-m3` → 自动识别
- DeepSeek 保留旧字符串判断作为兜底，老配置不破

#### B4. 迁移点

- `call_llm` 中所有 `is_deepseek` / `_is_official_deepseek_base_url` / `"deepseek" in model.lower()` 全部替换为 `provider = get_provider(candidate); provider.prepare_request(...)` 等
- `_normalize_dsml_tool_calls` / `_DsmlStreamFilter` 移入 `DeepSeekProvider` 作为它的特殊行为
- `codex_provider.py` 重写为 `CodexProvider(Provider)`，但保持外部 API 不变
- 旧的 `provider` 字符串值（`"openai_compatible"`、`"codex_oauth"`）保持兼容；新增 `"deepseek"`、`"minimax"`、`"anthropic"`、`"gemini"`

#### B5. 阶段 B 收益

- 新加 Claude / Gemini / Kimi 等只需写一个 Provider 文件
- `is_deepseek` 这种字符串硬编码全部消失
- 流式归一化统一，model picker 不用每个 provider 单独适配
- 测试更清晰：每个 provider 一份独立单元测试

**预计工时**：1 周（含 DeepSeek/Codex 迁移、Provider 协议、新增 MiniMaxProvider、所有现有测试不破）。

### 阶段 C：Claude / Gemini / Kimi 等正式接入（接在 B 之后，按需加 provider）

| Provider | 协议 | Provider 类 | 预计工时 |
|---|---|---|---|
| Anthropic Claude | Anthropic Messages | `AnthropicProvider`（同时也服务 MiniMax 的 `/anthropic` 端点） | 2-3 天（含 system 字段分离、content blocks、tool_use/tool_result 流式） |
| Google Gemini | Gemini API（`/v1beta/models/...:generateContent`） | `GeminiProvider` | 2-3 天（含 systemInstruction、functionDeclarations、contents 数组） |
| Kimi Moonshot | OpenAI 兼容 | 走 `OpenAICompatibleProvider` 即可，UI 加预设 | 0.5 天（仅 UI + 测试） |
| Zhipu GLM | OpenAI 兼容 + 私有 | 同上 | 0.5 天 |
| Xiaomi MiMo | OpenAI 兼容 | 同上 | 0.5 天 |
| OpenRouter | OpenAI 兼容 | 同上，自动按 model 路由 | 0.5 天 |
| Ollama / vLLM / LM Studio | OpenAI 兼容 | `LocalProvider`（无 key、跳过 auth 头） | 0.5 天 |
| 阿里通义千问 Qwen / DeepSeek-Coder 等 | OpenAI 兼容 | UI 加预设即可 | 0.5 天 / 个 |

**注意**：Anthropic 和 Gemini 的接入是阶段 B 价值的真正体现——它们和 OpenAI 协议差异大，没有抽象层会被代码搞得很乱。

---

## 5. 关键设计决策点（需要你拍板）

下面这些是会影响实现路线的取舍，建议在阶段 A 之前对齐：

1. **`reasoning_split` 是默认开还是用户开关？**
   - 默认开（M3 用户拿到的体验最佳，Cyrene 自动剥离 `<think>…</think>` 标签，干净）
   - 用户开关（更"尊重契约"，但默认 off 时 M3 用户会看到大量 <think> 标签污染输出）
   - **推荐**：onboarding "MiniMax" 预设里默认 on，Settings → Models 列表里给一个 per-model 开关。

2. **要不要新增 `provider` 枚举、还是继续用字符串？**
   - 字符串（现状）：灵活，但拼写错误静默退化成 `openai_compatible`
   - 枚举（推荐）：在 `settings_store` 加 `ProviderName` Literal，UI 下拉枚举，运行时找不到就报错
   - **推荐**：用 Literal 类型（`typing.Literal["openai_compatible", "codex_oauth", "deepseek", "minimax", "anthropic", "gemini"]`），迁移成本极低。

3. **阶段 A 走完之后，阶段 B 的 Provider 抽象要不要做？**
   - 做了 → 后续加 Claude / Gemini / Kimi 都是 0.5-3 天；DeepSeek 那些字符串补丁清掉；测试分层清楚
   - 不做 → 加新厂商继续在 client.py 里堆 `if`；半年内会变 3500+ 行
   - **推荐**：做。Cyrene 现在的 `client.py` 2656 行已经接近"勉强能维护"上限。

4. **Anthropic 兼容要不要做？**
   - 做了：M3 用户可以选择 Claude 风格 API（content blocks）；未来接 Claude 直接用同一个 Provider
   - 不做：M3 用户只能用 OpenAI 兼容路径，MiniMax 平台的一半能力浪费
   - **推荐**：在阶段 B 一起做。Anthropic Provider 同时支持 Claude 官方和 MiniMax 的 `/anthropic` 端点，只需要 base_url 切换。

5. **`is_deepseek` 这些字符串判断要保留兜底吗？**
   - 老用户模型 dict 里没有 `provider` 字段；如果删掉字符串判断，他们的 `deepseek-chat` 自动识别会失效
   - 阶段 B 的 `get_provider()` 内部做 fallback 判断（`"deepseek" in model`），保留兼容
   - **推荐**：保留。判定顺序是 显式 provider 字段 → 模型名 → base_url；老配置照常工作。

---

## 6. 风险与权衡

| 风险 | 触发条件 | 缓解 |
|---|---|---|
| 阶段 A 的 `reasoning_content` 保留路径污染消息历史 | `preserve_tool_reasoning=True` 会让 M3 的 reasoning 字段在每轮 tool call 后都带回来 | 实测 M3 reasoning 长度；如发现历史爆炸，参考 DeepSeek 的"仅在 tool_call 之后保留"策略 |
| 阶段 A 加 `extra_body` 字段被 OpenAI 官方端点拒绝 | 给 OpenAI 也发 `reasoning_split` | 把 extra_body 严格限定在 MiniMaxProvider 内部，不进入 OpenAI 路径 |
| 阶段 B 抽象层过度设计 | Provider 协议字段太多，迁移到一半发现用不上 | 阶段 B 用渐进式 API：先实现 `prepare_request` + `normalize_message` 两个方法，stream 沿用现有实现；后续按需加 `normalize_stream_chunk` |
| 价格目录与实际账单不符 | MiniMax 活动结束后标准价变化 | 在 `_BUILTIN` 注释里加 `verified_on` 和 `effective_until`；加一个 `pytest` 定期跑（CI）提醒人工复核 |
| onboarding 选 "Custom" 的人填了错的 base_url | 历史问题，不在本研究范围 | 保留；可考虑加 "Test connection" 按钮（已经部分实现 `save_and_test_llm_setup`） |
| M3 长上下文（1M token）压垮 prompt cache prefix | 单模型 1M 上下文对 cache key 命中不利 | Cyrene 的 prompt cache prefix 是按"当前包设置"建的（见 `architecture.md:34-43`），不直接相关；但 stage B 应把 1M context 当作 `ctx_limit` 标记给候选，避免 context-overflow 误判 |

---

## 7. 验证计划

### 7.1 阶段 A 完成验证

- [ ] onboarding 选 "MiniMax (China)"、填 API Key、保存
- [ ] 跑一段单轮对话，看 reasoning 正常流式输出
- [ ] 跑一段多轮 tool call（用 `bash` 工具），看每轮 reasoning 都正确回传
- [ ] 在 context-debug 日志（`data/debug_*.jsonl`）里检查 `reasoning_content` / `reasoning_details` 字段是否在 tool 轮次被保留
- [ ] 设置模型价格"2.1/0.42/8.4"、跑 1000 token 输入（其中 600 cache hit）+ 200 token 输出，验证 cost 计算 = `600*0.42 + 400*2.1 + 200*8.4) / 1_000_000` ≈ 0.00325 CNY
- [ ] `uv run pytest tests/test_model_runtime.py tests/test_pricing.py tests/test_settings_general.py` 全绿
- [ ] 跑 `cyrene flow` 看候选 fallback 链路：M3 401 时是否回退到下一个候选

### 7.2 阶段 B 完成验证

- [ ] 所有 180 现有自动化测试不破
- [ ] `test_provider_dispatch.py`：每个 provider 拿一段真实录制的 upstream response，验证归一化输出与 OpenAI 路径完全一致
- [ ] DeepSeek 老用户（无 `provider` 字段）继续工作
- [ ] Codex OAuth 用户继续工作
- [ ] model picker 显示 MiniMax 选项，且 reasoning 列表正确（M3 native 模式为空、split 模式为 `["auto"]`）

### 7.3 阶段 C 完成验证

- [ ] Claude Provider：接 Anthropic 官方 + MiniMax `/anthropic`，两套端点切换无问题
- [ ] Gemini Provider：multimodal 输入（image_url）走 Gemini 原生而非 OpenAI
- [ ] Kimi/GLM/MiMo：纯 UI 改动，配置后单轮/多轮对话工作

---

## 8. 结论与下一步

**最小投入（仅阶段 A，1.5–2.5 天）**：
- 写 1 个后端 PR 让 M3 的 thinking / split / cache 三件套生效
- 写 1 个前端 PR 在 onboarding 加 MiniMax 预设
- 价格目录核对、文档、CHANGELOG
- 上线后用户即可在"自定义模型"里填 MiniMax 跑起来

**推荐路线（阶段 A + B，约 1.5–2 周）**：
- 阶段 A 的所有内容
- 阶段 B 的 Provider 抽象层（含 DeepSeek/Codex 迁移、MiniMaxProvider 实现）
- 收益：阶段 C 加 Claude / Gemini / Kimi / GLM / MiMo 等都是 0.5–3 天
- 长期收益：消除 `client.py` 的字符串硬编码债，让 2656 行的核心文件回落到 ~1500 行

**不推荐**：跳过阶段 A 直接做阶段 B——会拖慢 MiniMax 上线时间，而 M3 用户（也就是你）现在就需要它。

---

## 附录 A：参考资料

- MiniMax Chat Completions API（OpenAI 兼容）：`https://platform.minimaxi.com/docs/api-reference/text-chat`
- MiniMax Tool Use & Interleaved Thinking：`https://platform.minimaxi.com/docs/guides/text-m2-function-call`
- MiniMax 模型总览：`https://platform.minimax.io/`（国际）
- MiniMax M3 crossmodel 概览：`https://www.crossmodel.ai/models/minimax/minimax-m3`
- vLLM Ascend MiniMax-M3 部署文档（含 thinking_mode / tool-parser）：`https://docs.vllm.com.cn/projects/ascend/en/latest/tutorials/models/MiniMax-M3.html`
- MiniMax 文本生成（v1 endpoint 旧路径）：`https://platform.minimaxi.com/document/对话`
- MiniMax Function Calling 客户端安全实践：`https://minimax-ai.chat/docs/function-calling`

## 附录 B：Cyrene 内部参考

- 模型运行时代码入口：`src/cyrene/model_runtime/client.py`
- 定价目录：`src/cyrene/model_runtime/pricing.py`
- Codex OAuth 旁路：`src/cyrene/model_runtime/codex_provider.py`
- 设置保存/测试：`src/route/settings/general.py`（`save_and_test_llm_setup` ~ `:43`）
- Onboarding UI：`src/webui/frontend/workbench-welcome.jsx`（`llmSource` 状态 ~ `:293`）
- 模型选择器设计 QA：`project-notes/design-qa-model-picker.md`
- 架构说明（prompt cache prefix）：`docs/architecture.md:34-43`
- 配置文档：`docs/configuration.md:48-50`（`OPENAI_*` env）
