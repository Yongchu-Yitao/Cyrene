# 外部 Agent 接入第一阶段 Handoff

> 状态：产品方向与第一阶段交互要求已确认，供后续设计和实现直接接手。
>
> 范围：允许安装并接入外部 Agent，由 Cyrene 继续提供聊天前端；外部 Agent 可以使用 Cyrene 的模型配置，也可以使用自己的登录或配置。

## 1. 目标

第一阶段要让用户能够从 Cyrene 扩展中心安装外部 Agent，并在普通 Workbench 对话中选择由哪个 Agent 执行。聊天记录、输入框、附件、流式输出、工具轨迹、权限请求和对话面板仍由 Cyrene 展示。

第一阶段必须同时支持两种模型来源：

1. **使用 Cyrene 模型配置**：外部 Agent 通过 Cyrene Model Gateway 使用用户已配置的模型，真实长期密钥不直接交给外部 Agent。
2. **使用 Agent 自有配置**：外部 Agent 使用自己的 OAuth、API Key、环境变量或配置文件，Cyrene 只展示和驱动 Agent 声明的认证流程。

外部 Agent 保留自己的安全限制和权限管理。Cyrene 展示 Agent 发出的权限请求并回传用户的原始选择，不把不同 Agent 的权限语义重写成 Cyrene 的统一策略。

## 2. 已确认的产品决策

以下要求已经确定，实现时不应重新发散：

| 主题 | 已确认决策 |
|---|---|
| Agent 选择位置 | Agent 选择放在 Composer 现有的模型弹层内，作为根菜单第一行，位于“模型”之前；不增加第二个独立选择按钮。 |
| Agent 信息位置 | 当前对话使用的 Agent 信息放在“对话面板 → 概览”。 |
| Agent 设置位置 | 不新增独立“外部 Agent”设置页；登录、模型来源、能力、运行状态和诊断都放在“扩展 → 已安装 Agent → 详情”。 |
| Agent Tab 内容 | Agent Tab 不提供搜索框，只展示 Cyrene 推荐 Agent、全部已安装 Agent，以及“安装其他 Agent”API 入口。 |
| 非推荐 Agent | 通过安装 API 接入的非推荐 Agent 必须进入同一个“已安装”列表，并明确标记来源和信任状态。 |
| 权限 UI | 必须根据 Agent 返回的权限请求动态渲染，按钮提交原始 `optionId`。 |
| 消息与工具事件 | 使用统一事件信封和注册式渲染；根据能力与事件内容显示或降级。 |
| Composer | 必须由当前 Agent 的能力驱动，不能默认所有 Agent 都支持 Cyrene 内置 Agent 的全部功能。 |
| 模型来源 | 每个 Agent 可以使用 Cyrene 模型配置，也可以选择 Agent 自有登录或配置。 |
| 安全边界 | Agent 自己决定权限语义和安全限制；Cyrene 不伪装成外部 Agent 的权限策略引擎。 |
| 第一阶段协议 | 架构做成可扩展的内部通用接口，首个正式驱动采用 ACP stdio。 |
| 第一阶段运行关系 | 一个对话绑定一个主 Agent。Agent 聚合、handoff 和 swarm 不在本阶段实现。 |

现有 Settings 中的“Agents”继续管理 Cyrene 自身的 SOUL、主动性和 spawn policy；本项目不改变它的语义，也不把外部 Agent 配置混进去。

## 3. 第一阶段边界

### 3.1 必须交付

- 扩展中心 Agent Tab 可以展示并下载少量推荐 Agent，也可以通过安装 API 接入其他 Agent；Agent Tab 不提供搜索。
- 推荐或非推荐来源安装的 Agent 都可以升级、启用、停用和卸载，并统一出现在“已安装”列表。
- 已安装 Agent 详情可以完成认证、模型来源配置、连接测试、能力查看和诊断。
- 新对话可以在现有模型弹层中选择 Agent。
- 对话持久化 Agent 绑定、外部 Session 标识和模型来源。
- OpenCode、Codex ACP 和 Pi ACP 至少通过同一 ACP 驱动和各自声明式 Profile 接入。
- 外部 Agent 可以选择 Cyrene 管理模型或 Agent 自有配置。
- 权限请求、消息流、reasoning、工具生命周期和错误可以在现有聊天界面显示。
- Composer、对话侧栏和运行中交互根据能力协商结果启用、隐藏或降级。
- Agent 崩溃、认证过期、协议不兼容、模型绑定不兼容时提供可操作的错误状态。

### 3.2 明确不做

- 不让浏览器前端直接连接 ACP、Agent HTTP Server 或 Agent 子进程。
- 不在扩展中加载任意前端 JavaScript，也不允许 Agent 注入自己的 React UI。
- 不在 Cyrene Python 进程内加载第三方任意适配器代码；第一阶段只使用内置 Driver 和声明式 Agent Profile。
- 不承诺每个 Cyrene 模型都能用于每个 Agent。
- 不允许有消息的对话直接更换主 Agent；“使用其他 Agent 继续”应创建新对话或 fork。
- 不实现多 Agent 聚合、自动路由、Agent-to-Agent 委派或 swarm。
- 不把外部 Agent 的安全策略、沙箱或权限模式合并成 Cyrene 自己的一套策略。

## 4. 总体架构

Cyrene 前端只面对 Cyrene Backend-for-Frontend。后端通过内部统一 Agent Runtime 接入不同协议和 Agent，不把协议差异泄漏到 React 组件。

```text
Cyrene Workbench UI
        │
        │ HTTP + NDJSON/SSE
        ▼
Cyrene Agent BFF / ChatRunManager
        │
        ▼
Universal Agent Runtime（内部稳定 SPI）
        ├── ACP stdio Driver ── OpenCode
        │                    ├─ Codex ACP
        │                    └─ Pi ACP
        ├── A2A Driver（后续）
        ├── JSON-RPC / JSONL Driver（后续）
        ├── HTTP / SSE Driver（后续）
        └── CLI Driver（有限降级，后续）
        │
        ├── Cyrene Model Gateway
        └── Agent 自有认证/配置
```

“Universal”指 Cyrene 内部统一生命周期、会话、能力、事件和错误，不是要求所有 Agent 实现一份功能完全相同的新外部协议。

## 5. 内部通用 Agent 接口

建议新增独立领域层，Route 和 UI 不直接判断 `opencode`、`codex` 或 `pi`：

```python
class AgentDriver(Protocol):
    async def inspect(self, installation) -> AgentDescriptor: ...
    async def connect(self, request: AgentStartRequest) -> AgentConnection: ...

class AgentConnection(Protocol):
    async def authenticate(self, request: AuthRequest) -> AuthResult: ...
    async def open_session(self, request: OpenSessionRequest) -> AgentSession: ...
    async def load_session(self, external_session_id: str) -> AgentSession: ...
    async def prompt(self, request: PromptRequest) -> None: ...
    async def respond_permission(self, request_id: str, option_id: str) -> None: ...
    async def respond_elicitation(self, request_id: str, value: object) -> None: ...
    async def steer(self, request: SteerRequest) -> None: ...
    async def cancel(self, run_id: str) -> None: ...
    async def close(self) -> None: ...
    def events(self) -> AsyncIterator[AgentEvent]: ...
```

所有可选操作都必须先经过能力判断。不支持的操作返回稳定的 `capability_missing`，不能静默忽略。

## 6. Agent 描述与能力协商

### 6.1 Agent 描述

前端使用后端归一化后的描述，不读取插件 Manifest 原文：

```json
{
  "installationId": "agent_opencode_default",
  "agentId": "opencode",
  "displayName": "OpenCode",
  "version": "1.2.3",
  "driver": "acp_stdio",
  "protocolVersion": 1,
  "state": "ready",
  "authState": "connected",
  "defaultModelAccess": "cyrene_managed",
  "capabilities": {}
}
```

区分 `agentId` 和 `installationId`：同一个 Agent 后续可以存在多个安装或配置实例。聊天绑定 `installationId`，不能只保存产品名。

### 6.2 能力结构

能力状态至少支持 `supported`、`unsupported`、`unknown` 和 `degraded`，不能只使用布尔值：

```json
{
  "session": {
    "load": "supported",
    "fork": "unsupported",
    "close": "supported"
  },
  "input": {
    "text": "supported",
    "image": "unsupported",
    "file": "supported",
    "audio": "unknown"
  },
  "output": {
    "streaming": "supported",
    "reasoning": "degraded",
    "toolLifecycle": "supported",
    "artifacts": "supported",
    "diff": "supported"
  },
  "interaction": {
    "permission": "agent_defined",
    "elicitation": "supported",
    "steer": "unsupported",
    "cancel": "supported"
  },
  "model": {
    "agentManaged": "supported",
    "cyreneManaged": ["openai_chat", "openai_responses"],
    "switchDuringSession": "unsupported",
    "reasoningEffort": "supported"
  }
}
```

能力来源优先级：

1. 当前进程的协议握手；
2. 实际运行探测；
3. Cyrene 验证过的精确 Agent/Adapter 版本 Profile；
4. 扩展 Manifest 声明；
5. 未知能力按保守策略处理。

能力缓存必须包含 Agent 版本、平台、Driver 版本和协议版本；升级后重新探测。

## 7. 扩展中心与已安装 Agent 详情

现有扩展中心位于 `src/webui/frontend/settings-overlay.jsx` 的 `ExtensionsPanel`。第一阶段在这里增加 `agent` 分类，而不是新增 Settings Tab。

### 7.1 Agent Tab 固定布局

Agent Tab 使用两个列表和一个固定操作入口，不复用 Skill/MCP 的远程搜索交互：

```text
Agent

推荐
┌ OpenCode ─────────────────────── [安装] ┐
├ Codex ACP ────────────────────── [安装] ┤
└ Pi ACP ───────────────────────── [安装] ┘

已安装
┌ OpenCode · 推荐来源 ─────────── [详情] ┐
└ My Agent · 外部来源 ─────────── [详情] ┘

[ 安装其他 Agent ]
```

强约束：

- Agent Tab **不显示搜索框、远程搜索结果、游标分页或高级搜索开关**。
- “推荐”只展示 Cyrene 维护的少量固定推荐 Agent；第一阶段目标为 OpenCode、Codex ACP 和 Pi ACP。
- 推荐列表的数据仍来自后端 Catalog，前端不能写死版本、下载地址或校验摘要。
- 已经安装的推荐 Agent 不重复显示“安装”按钮，可以显示“已安装”或“升级”。
- “已安装”必须根据真实 Installation Store 枚举，不能取“已安装 ID”和推荐 Catalog 的交集。
- 任何通过 API、导入 Manifest 或以后其他可信安装入口安装的 `kind=agent` 项，都必须进入同一个“已安装”列表。
- 非推荐项显示“外部来源”标记，并展示 Manifest URL/Repository、Publisher、校验和与验证状态；缺失字段显示“未验证”，不能伪装成 Cyrene 推荐。
- 推荐列表为空或请求失败不影响已安装列表显示。

建议列表响应明确分离两类数据：

```json
{
  "recommended": [
    {
      "agentId": "opencode",
      "displayName": "OpenCode",
      "recommended": true,
      "installState": "available"
    }
  ],
  "installed": [
    {
      "installationId": "agent_my_agent_default",
      "agentId": "my-agent",
      "displayName": "My Agent",
      "recommended": false,
      "sourceTrust": "external_unverified",
      "installState": "installed"
    }
  ]
}
```

### 7.2 Agent 卡片

列表卡片至少显示：

- 名称、图标、版本；
- 安装/升级/启用状态；
- 运行健康状态；
- 登录状态；
- Driver 和协议，例如 `ACP · stdio`；
- 默认模型来源；
- 能力等级摘要。

推荐卡片提供直接“安装”按钮。安装仍进入统一的来源检查、摘要校验、用户确认和异步安装任务流程，不能因为是推荐项而绕过安装审计。

### 7.3 “安装其他 Agent”API 入口

Agent Tab 保留固定的“安装其他 Agent”按钮。点击后打开信息弹窗，不展开搜索框。弹窗的目的，是让用户把准确的接口说明复制给开发者或另一个 Agent，由其生成/提交兼容的外部 Agent 安装请求。

弹窗至少展示：

- API Method 和 Endpoint；
- 支持的 Source 类型，例如 Manifest URL 或本地 Manifest；
- `cyrene.agent/v1` Manifest 的必填字段；
- 当前支持的 Driver/协议和 Model Binder；
- 完整 JSON 请求示例；
- 安装流程和安全确认说明；
- 非推荐 Agent 将显示“外部来源”的说明；
- “复制接口”“复制请求示例”“复制 Manifest 模板”“复制全部说明”按钮。

建议界面：

```text
安装其他 Agent

API                 POST /api/extensions/agents/install-proposals
Content-Type        application/json
Manifest API        cyrene.agent/v1
支持                 ACP stdio

[复制接口] [复制请求示例] [复制 Manifest 模板]

通过该接口提交的 Agent 会先生成安装提案，
需要在 Cyrene 中确认来源和将运行的程序后才会安装。

[复制全部说明]                         [关闭]
```

复制内容不得包含 Cyrene Shared Token、Model Gateway Token、API Key 或任何长期凭据。接口只绑定本机并沿用 Cyrene 现有桌面认证边界。

建议采用安装提案而不是允许外部 Agent 静默执行安装：

```json
POST /api/extensions/agents/install-proposals
{
  "source": {
    "type": "manifest_url",
    "url": "https://example.com/my-agent/cyrene-agent.json"
  },
  "requestedVersion": "1.0.0"
}
```

响应：

```json
{
  "proposalId": "agent_install_xxx",
  "agentId": "my-agent",
  "displayName": "My Agent",
  "sourceTrust": "external_unverified",
  "requiresConfirmation": true,
  "inspect": {
    "driver": "acp_stdio",
    "command": "my-agent",
    "version": "1.0.0",
    "checksums": {}
  }
}
```

安装提案生成后：

1. Cyrene 验证 Manifest Schema、来源、版本、Driver 和摘要；
2. UI 展示将下载和运行的程序以及权限边界；
3. 用户显式确认；
4. 后端进入与推荐 Agent 相同的异步安装任务管线；
5. 安装成功后，该 Agent 立即出现在“已安装”列表并可打开详情完成登录、模型和能力配置。

重复提交同一 Source/Version 应返回现有 Installation 或同一个进行中的任务，避免重复安装。安装失败也应在“已安装/安装任务”区域保留可诊断状态，而不是从界面消失。

### 7.4 展开后的详情

已安装 Agent 卡片展开后，详情按以下顺序组织：

1. **概览**：版本、来源、安装路径、Driver、协议、健康状态。
2. **登录与模型**：选择“使用 Cyrene 模型”或“使用 Agent 自有配置”。
3. **能力**：按输入、输出、会话、交互和模型分组展示实际探测结果。
4. **运行**：启动命令、进程状态、最近启动时间、重启操作。
5. **诊断**：测试连接、最近错误和脱敏日志。

这些内容属于扩展详情，不再创建“外部 Agent”独立设置页面。

### 7.5 登录与模型交互

选择“使用 Cyrene 模型”时：

- 只列出该 Agent 已验证兼容的 Cyrene 模型 Profile；
- 展示 Gateway 协议，例如 OpenAI Chat 或 Responses；
- 提供“测试模型连接”；
- 不向前端返回真实 API Key 或临时 Gateway Token。

选择“使用 Agent 自有配置”时：

- UI 根据 Agent 声明的认证方法显示 OAuth、浏览器登录、终端登录、API Key 或现有配置状态；
- Agent 不需要凭据时显示“使用本机 Agent 配置”；
- 登录按钮触发后端认证流程，UI 展示 `not_configured → authenticating → connected / failed / expired`；
- 已保存凭据只显示“已配置”，不能回显密钥。

建议运行 API：

```text
GET    /api/agents
GET    /api/agents/{installation_id}
PATCH  /api/agents/{installation_id}/settings
POST   /api/agents/{installation_id}/auth/start
POST   /api/agents/{installation_id}/auth/logout
POST   /api/agents/{installation_id}/probe
POST   /api/agents/{installation_id}/restart
GET    /api/agents/{installation_id}/diagnostics
```

推荐安装、安装提案、确认、升级和卸载仍走 `/api/extensions/*`；`/api/agents/*` 只管理已安装 Agent 的运行态和配置。推荐与非推荐 Agent 必须复用同一 Installation Store、任务系统和审计记录。

## 8. Composer：Agent 选择位置与交互

当前 Composer 的模型按钮和弹层位于 `src/webui/frontend/workbench-chat.jsx` 的 `WbcComposer`。根菜单当前包含“模型 / 推理强度 / 权限模式”。

### 8.1 已确认位置

Agent 选择必须放入这个现有弹层的第一行：

```text
┌────────────────────────────┐
│ Agent        OpenCode    > │  ← 新增，固定第一行
│ 模型          GPT-5      > │
│ 推理强度         高      > │
│ 权限模式   由 Agent 管理  > │
└────────────────────────────┘
        [ GPT-5  高  ^ ]
```

不得在 Composer 底部再新增一个并列 Agent 胶囊按钮。

### 8.2 Agent 子菜单

Agent 子菜单分组展示：

- Cyrene 内置 Agent；
- 已安装且启用的外部 Agent；
- 不可用 Agent 可以保留在列表底部，但必须显示原因且不可选择。

每一项至少包含名称和状态；空间允许时显示图标、协议或简短能力等级。状态包括：

- 可用；
- 需要登录；
- 需要配置；
- 未启动；
- 版本不兼容；
- 已停用。

选择“需要登录/配置”的 Agent 时不直接绑定，跳转或打开该 Agent 的扩展详情。

### 8.3 绑定和锁定规则

- 空对话可以更改 Agent。
- 第一条消息发送时把 `installationId` 和模型来源写入对话并锁定。
- 有消息的对话中 Agent 行仍可查看，但不可直接改绑。
- 用户选择其他 Agent 时提供“在新对话中继续”，复制必要的用户选择内容或 fork 上下文，但保留原对话。
- Agent 正在运行时整个 Agent/模型来源配置不可修改。
- 模型是否能在 Session 内切换由 `model.switchDuringSession` 决定。

第一阶段的模型来源模式在“扩展 → 已安装 Agent → 详情”中按 `installationId` 配置为默认值，Composer 不再增加一层“模型来源”开关：

- `cyrene_managed`：模型子菜单显示该 Agent 兼容的 Cyrene 模型。
- `agent_managed`：模型行显示 Agent 报告的模型；无法报告时显示“由 Agent 配置”，不展示 Cyrene 模型列表。

创建对话时把当时的模型来源快照写入 Chat，防止以后修改 Agent 默认设置时悄悄改变既有对话的运行方式。

由于当前 `ensureChat()` 会惰性创建对话，前端需要在新对话尚未创建时保存一份 Composer Draft Binding，并在首次 `createChat()` 时一并提交，不能先创建默认 Agent 对话再立即改绑。

### 8.4 对话创建契约

```json
POST /api/workbench/chats
{
  "project": "project-id",
  "title": "",
  "agent": {
    "installationId": "agent_opencode_default"
  },
  "modelAccess": {
    "mode": "cyrene_managed",
    "profileId": "primary"
  }
}
```

后端返回持久化后的绑定和能力快照：

```json
{
  "chat": {
    "id": "wbchat_xxx",
    "agent": {
      "installationId": "agent_opencode_default",
      "agentId": "opencode",
      "displayName": "OpenCode",
      "version": "1.2.3",
      "driver": "acp_stdio",
      "externalSessionId": "",
      "bindingLocked": false
    },
    "modelAccess": {
      "mode": "cyrene_managed",
      "profileId": "primary",
      "model": "gpt-5"
    },
    "capabilities": {}
  }
}
```

第一条消息成功进入运行队列后 `bindingLocked` 变为 `true`。并发改绑返回 `409 agent_binding_locked`。

## 9. 对话面板概览中的 Agent 信息

当前概览由 `src/webui/frontend/workbench-chat.jsx` 的 `WbcOverviewTab` 渲染。Agent 信息放在状态行下面、模型行上面。

建议布局：

```text
状态          空闲
Agent         OpenCode
连接          已连接 · ACP
模型来源      Cyrene
模型          gpt-5
Agent 会话 ID ses_xxx
Cyrene 对话 ID wbchat_xxx
```

布局要求：

- 内置 Agent 显示 `Cyrene · 内置`；外部 Agent 显示实际名称。
- Agent 名称可点击或带详情按钮，打开“扩展 → 已安装 Agent → 对应详情”。
- `连接`反映 Agent 运行/协议状态，不与本轮“空闲/回复中”混为一谈。
- 同时保留 Cyrene 对话 ID 和外部 Agent Session ID；外部 Session 尚未创建时显示 `—`。
- 模型来源明确显示“Cyrene”或“Agent 自有配置”。
- Agent 版本和 Driver 可放在名称的辅助信息或 Tooltip，不挤占主要键值行。
- 对不报告 Token/Cache Usage 的 Agent，隐藏相应统计区，而不是显示虚假的 `0`。

## 10. 模型访问契约

统一使用 `ModelAccess`，不要在 UI 或 Chat Route 中散布 Agent 专属环境变量判断：

```json
{
  "mode": "cyrene_managed",
  "profileId": "primary",
  "protocol": "openai_responses",
  "model": "gpt-5"
}
```

或者：

```json
{
  "mode": "agent_managed"
}
```

### 10.1 Cyrene 管理模型

- Cyrene Model Gateway 第一阶段提供 OpenAI Chat Completions 和 Responses 兼容接口。
- 长期凭据只保存在 Cyrene Credential Store。
- 外部 Agent 进程只获得短期、可撤销、限定 Agent/会话/模型范围的 Token。
- Adapter 通过内置 Binder 将 Gateway Endpoint 和 Token 注入进程环境或 Agent 支持的配置接口。
- Binder 必须是 Cyrene 内置、具名并经过测试的实现；扩展 Manifest 只能声明 Binder ID，不允许包含任意 Shell 模板。
- 绑定前检查模型 API、Tool Calling、Vision、Reasoning 等兼容性，不兼容时返回 `model_binding_unsupported`。

### 10.2 Agent 管理模型

- Cyrene 不覆盖 Agent 的 Provider、模型选择或账号安全策略。
- 如果 Agent 能报告当前模型，概览展示它；无法报告时显示“由 Agent 配置”。
- Agent 自有模型列表只能来自 Agent 的正式能力或配置接口，不能由前端猜测。

## 11. 权限 UI 动态化

### 11.1 权限请求结构

```json
{
  "schemaVersion": 1,
  "type": "permission.requested",
  "requestId": "perm_123",
  "agentId": "opencode",
  "runId": "run_123",
  "payload": {
    "title": "OpenCode 请求运行命令",
    "description": "npm install",
    "toolCallId": "tool_123",
    "options": [
      { "id": "allow_once", "label": "允许一次", "kind": "allow_once" },
      { "id": "allow_session", "label": "本会话允许", "kind": "allow_session" },
      { "id": "deny", "label": "拒绝", "kind": "deny" }
    ]
  }
}
```

UI 必须：

- 显示请求来源 Agent；
- 显示 Agent 提供的标题、描述和选项顺序；
- 使用 `label` 作为显示文本，使用原始 `id` 作为提交值；
- 不根据按钮文字、按钮位置或本地化文案推导允许/拒绝；
- 对危险内容使用纯文本或经过严格清理的 Markdown，禁止 Agent 注入 HTML；
- 防止重复提交，并正确处理请求已过期、Agent 已退出和其他窗口已响应；
- 保留键盘操作、焦点状态和屏幕阅读器标签。

建议响应契约：

```json
POST /api/workbench/chats/{chat_id}/agent-requests/{request_id}/respond
{
  "response": {
    "type": "option",
    "optionId": "allow_once"
  }
}
```

开放问题/表单使用同一路由的其他 `response.type`，例如 `text` 或 `form`。现有 Cyrene `pendingQuestion` 通过内置兼容 Adapter 转换为同一结构。

Composer 中的“权限模式”也要受能力控制：

- Agent 支持可配置权限模式：展示 Agent 报告的模式和说明。
- Agent 只支持运行时询问：显示只读“由 Agent 管理”。
- Agent 不产生权限请求：隐藏这一行。
- 不把 Cyrene 的 `auto/ask` 强行映射到外部 Agent。

## 12. 统一事件信封与能力渲染

### 12.1 事件信封

所有 Driver 先在后端转换为统一事件。前端不直接解析 ACP、A2A 或 Agent 专属 JSON：

```json
{
  "schemaVersion": 1,
  "eventId": "evt_123",
  "timestamp": "2026-08-13T10:00:00Z",
  "agentId": "opencode",
  "installationId": "agent_opencode_default",
  "chatId": "wbchat_xxx",
  "runId": "run_xxx",
  "sessionId": "external_session_xxx",
  "actorId": "primary",
  "parentRunId": null,
  "type": "tool.updated",
  "payload": {},
  "extensions": {
    "acp": {}
  }
}
```

`actorId` 和 `parentRunId` 第一阶段通常为空或固定为 `primary`，但现在保留，以免未来 Agent 聚合和 swarm 需要破坏事件 Schema。

核心事件：

- `run.started`
- `run.awaiting_input`
- `run.completed`
- `run.failed`
- `run.cancelled`
- `message.started`
- `message.delta`
- `message.completed`
- `reasoning.started`
- `reasoning.delta`
- `reasoning.completed`
- `tool.started`
- `tool.updated`
- `tool.completed`
- `permission.requested`
- `permission.resolved`
- `elicitation.requested`
- `elicitation.resolved`
- `artifact.created`
- `artifact.updated`
- `usage.updated`
- `session.updated`

Agent 或协议特有信息放入命名空间 `extensions`。后端必须先做字段 allowlist 和脱敏；前端不能把任意原始对象直接渲染成 HTML。

### 12.2 前端事件路由

把当前 `consumeEventStream` 中不断增长的 `if/else` 分发改造成注册式 Router：

```javascript
var AGENT_EVENT_HANDLERS = {
  "message.delta": handleMessageDelta,
  "reasoning.delta": handleReasoningDelta,
  "tool.started": handleToolStarted,
  "tool.updated": handleToolUpdated,
  "tool.completed": handleToolCompleted,
  "permission.requested": handlePermissionRequested,
  "artifact.created": handleArtifactCreated,
};
```

要求：

- 同一个 `eventId` 幂等处理；断线重连不得重复生成 Tool Card 或消息。
- Tool 生命周期使用稳定 `toolCallId` 原位更新，保留并发工具的开始顺序。
- 没有 `toolLifecycle` 能力时不显示虚假的工具 Timeline。
- 不支持 reasoning 或没有 reasoning 事件时不生成空的 Thinking Card。
- 未知核心事件安全忽略并记录诊断；未知扩展事件只在通用、可展开的“Agent 事件”调试卡中显示脱敏摘要。
- Agent 崩溃、协议错误和传输断开统一进入 `run.failed`，但保留稳定 `failureKind` 供 UI 提供恢复动作。
- Cyrene 内置 Agent 的现有事件也要通过兼容 Normalizer 进入统一 Router，避免长期维护两套渲染逻辑。

### 12.3 Tool Card 最小结构

```json
{
  "toolCallId": "tool_123",
  "name": "bash",
  "title": "Run command",
  "status": "running",
  "progress": { "current": 1, "total": 3, "label": "Installing" },
  "inputSummary": "npm install",
  "outputSummary": "",
  "failed": false,
  "presentation": {
    "kind": "terminal"
  }
}
```

第一阶段只提供 Cyrene 内置的通用文本、终端、文件、Diff、浏览器和错误 Renderer；未知 `presentation.kind` 使用通用 Tool Card。Agent 插件不能携带自定义前端代码。

## 13. 能力驱动的 Composer 和对话 UI

前端应从当前 Chat Snapshot 读取已锁定的能力快照，并允许后端推送新版本。不要在组件中写 `if (agentId === "opencode")`。

| 能力 | UI 行为 |
|---|---|
| `input.text` 不支持 | 禁止发送，并在 Composer 显示不可用原因。 |
| `input.image` 不支持 | 文件选择器不接受图片；粘贴/拖入图片时给出明确提示。 |
| `input.file` 不支持 | 隐藏或禁用附件按钮，拖放不进入上传流程。 |
| `input.audio` 不支持 | 隐藏语音输入。 |
| `interaction.steer` 不支持 | Agent 运行期间禁用继续发送；保留停止按钮。 |
| `interaction.cancel` 不支持 | 不显示误导性的“停止”；显示“等待 Agent 完成”，但 Cyrene 仍可提供结束本地进程的诊断操作。 |
| `interaction.permission` 为 `agent_defined` | 权限模式显示“由 Agent 管理”，请求卡按 Agent 选项渲染。 |
| `model.reasoningEffort` 不支持 | 隐藏“推理强度”。 |
| `model.switchDuringSession` 不支持 | 第一条消息后模型和模型来源只读。 |
| `output.reasoning` 不支持 | 不创建 Thinking/Reasoning 区域。 |
| `output.toolLifecycle` 不支持 | 不创建工具进度区。 |
| `output.diff` 不支持 | 不因能力本身显示 Changes；只有实际产生 Change Set 时才出现。 |
| `output.artifacts` 不支持 | 隐藏空的 Artifacts 功能；收到实际兼容事件时可按事件临时显示。 |
| `session.load` 不支持 | 应用重启后把会话标记为不可恢复，并提供在新对话继续。 |

对于 `unknown`：输入和具有副作用的交互按不支持处理；纯展示能力在收到合法事件后可以按事件逐步开启。

Slash Command 也必须由 Agent Capability/Command 列表提供。Cyrene 专属命令不能无条件出现在外部 Agent 对话中。

## 14. Chat 数据模型

建议在 Chat Snapshot 中持久化：

```json
{
  "agent": {
    "installationId": "agent_opencode_default",
    "agentId": "opencode",
    "displayName": "OpenCode",
    "version": "1.2.3",
    "driver": "acp_stdio",
    "protocolVersion": 1,
    "externalSessionId": "ses_xxx",
    "bindingLocked": true
  },
  "modelAccess": {
    "mode": "cyrene_managed",
    "profileId": "primary",
    "protocol": "openai_responses",
    "model": "gpt-5"
  },
  "capabilities": {},
  "capabilitiesRevision": 3
}
```

规则：

- Chat 保存创建时的 Agent 身份快照，防止升级或卸载后历史界面失去名称和版本。
- 当前运行能力可以更新，但必须有 `capabilitiesRevision`。
- Agent 卸载后历史消息仍可读；重新运行时提示重新安装匹配版本或在新对话继续。
- `externalSessionId` 不替代 Cyrene `chat.id`。
- Model Gateway Token、Agent API Key、OAuth Token 和原始环境变量不得进入 Chat Snapshot、事件或前端状态。

## 15. 稳定错误类型与恢复 UI

后端至少归一化以下错误：

| `failureKind` | UI 建议动作 |
|---|---|
| `dependency_missing` | 打开 Agent 扩展详情或重新安装。 |
| `agent_disabled` | 打开详情并启用。 |
| `auth_required` | 打开详情并登录。 |
| `auth_expired` | 重新登录。 |
| `protocol_mismatch` | 升级/降级 Agent 或 Driver。 |
| `capability_missing` | 解释当前功能不受支持。 |
| `model_binding_unsupported` | 改用兼容 Cyrene 模型或 Agent 自有配置。 |
| `model_gateway_unavailable` | 重试 Gateway 或改用 Agent 自有配置。 |
| `agent_crashed` | 重启 Agent、查看诊断、在新对话继续。 |
| `session_not_loadable` | 在新对话继续。 |
| `request_expired` | 关闭过期权限/问题卡并刷新会话。 |

错误卡必须显示 Agent 名称和失败阶段，不能只显示通用“请求失败”。

## 16. 现有代码改动地图

### 16.1 前端

- `src/webui/frontend/workbench-chat.jsx`
  - 扩展 `WorkbenchChatModel.createChat()` 的 Agent/ModelAccess 请求。
  - `ensureChat()` 接收新对话 Draft Binding。
  - `WbcComposer` 根菜单第一行加入 Agent。
  - 模型、Reasoning、权限、附件、语音、Steer 和 Slash Command 改为能力驱动。
  - `consumeEventStream` 迁移到统一 Agent Event Router。
  - `WbcQuestionPrompt` 改为使用原始 `optionId`。
  - `WbcOverviewTab` 增加 Agent、连接、模型来源和外部 Session 信息。
  - Chat Header、列表和 Quick Chat 显示/继承 Agent 身份。
- `src/webui/frontend/settings-overlay.jsx`
  - `ExtensionsPanel` 增加 `agent` 分类。
  - Agent 分类使用“推荐 / 已安装 / 安装其他 Agent”固定布局，不渲染搜索框。
  - “安装其他 Agent”弹窗展示安装提案 API、Manifest 模板和复制按钮。
  - `ExtensionCard` 的 Agent 详情增加登录、模型、能力、运行和诊断区。
  - 不增加新的 Settings Tab。
- `src/webui/frontend/workbench.css`
  - 复用现有 `wbc-model-menu`、Overview 键值布局和 Extension Card 视觉语言。
  - 增加 Agent 状态、能力、认证和错误的样式及窄窗口适配。
- `src/webui/frontend/workbench-i18n.jsx`
  - 为上述 UI 增加完整中英文文案，Agent 返回的动态 Label 不进入本地翻译映射。
- `src/webui/frontend/workbench-quick-chat.jsx`
  - 已有对话沿用绑定 Agent。
  - 新 Quick Chat 第一阶段使用默认 Agent；不复制完整 Agent 选择弹层。
- `src/webui/frontend/platform/api.jsx`
  - 复用统一错误处理，不新增 Agent 专属 Fetch Wrapper。

根据项目约定，修改源码后必须同步仓库内 `src/webui/static/app` 生成输出。

### 16.2 后端

- 新增 Agent Domain、Driver Registry、ACP Driver、Profile/Binder 和 Process Manager。
- `src/route/workbench/chat.py` 不再直接假定唯一 `run_agent`，改由 Agent Runtime 分发。
- `src/route/schemas.py` 增加 Agent Binding、ModelAccess、Agent Request Response 和能力 Schema。
- `src/cyrene/workbench/chat_runs.py` 继续负责持久运行、重连和事件缓存，但缓存统一 Agent Event。
- `src/agent/plugin/plugin_impl/cyrene_extensions/extension_catalog.py` 和 Extension Service 增加 `agent` 类型。
- `src/route/extensions.py` 继续管理安装生命周期，增加外部 Agent 安装提案、Inspect 和确认接口；新增 Agent Runtime Routes 管理配置、认证、Probe 和诊断。
- `src/cyrene/runtime/config_store.py` 保存 Agent 默认设置和 Credential Reference，不能在普通 Settings Payload 中返回密钥。
- `src/cyrene/model_runtime/` 增加本地 Model Gateway 和短期 Token 验证；现有 Provider Client 不能直接作为可公开给 Agent 的 Gateway。

## 17. Agent Profile 与安装安全

第一阶段 Agent 扩展使用声明式 Profile：

```json
{
  "apiVersion": "cyrene.agent/v1",
  "id": "opencode",
  "displayName": "OpenCode",
  "distribution": {},
  "drivers": [
    {
      "kind": "acp_stdio",
      "command": "opencode",
      "args": ["acp"],
      "priority": 100
    }
  ],
  "modelBinders": ["openai_gateway_env"],
  "compatibility": {
    "minVersion": "...",
    "maxVersion": "..."
  }
}
```

要求：

- 安装前展示来源、版本、校验信息和将运行的程序。
- 托管二进制按版本隔离并校验摘要。
- Profile 不能携带任意 Shell、前端脚本或 Python Import Path。
- 进程环境使用 allowlist 构造，不继承不必要的 Cyrene Secret。
- 诊断日志必须脱敏 Gateway Token、API Key、Authorization Header 和 OAuth Token。

## 18. 分阶段实施顺序

### A. Contract 和兼容层

1. 建立 Agent/Capability/ModelAccess/Event Schema。
2. 用兼容 Normalizer 把现有 Cyrene Agent 事件映射到新 Event Schema。
3. 改造前端 Event Router，但保持现有内置 Agent 行为不变。

### B. 扩展安装和运行时

1. 扩展中心增加 `agent` 类型和固定推荐列表，不提供搜索。
2. 增加“安装其他 Agent”API 说明弹窗、复制操作和安全安装提案流程。
3. 确保推荐与非推荐 Agent 统一写入 Installation Store 和“已安装”列表。
4. 实现 ACP Driver、进程生命周期、Probe 和能力缓存。
5. 添加 OpenCode、Codex ACP、Pi ACP Profile。

### C. 模型与认证

1. 实现 Agent 自有认证状态和流程。
2. 实现 Cyrene Model Gateway、短期 Token 和首批 Binder。
3. 在 Agent 详情完成模型来源配置与连接测试。

### D. 对话 UI

1. 在现有模型弹层第一行增加 Agent 选择。
2. 扩展 Chat 创建和绑定锁定。
3. 在对话概览增加 Agent 信息。
4. 动态化权限卡和 Composer。
5. 完成 Agent 错误恢复 UI。

### E. 兼容验证

1. OpenCode、Codex ACP、Pi ACP 的录制协议流回放。
2. 真实进程 Smoke Test。
3. 重连、崩溃、登录过期、权限竞态和不支持能力测试。

## 19. 测试与验收标准

### 19.1 Contract Test

每个 Driver/Profile 必须通过相同契约测试：

- Inspect/Probe 能返回稳定 AgentDescriptor。
- 能创建 Session、发送 Prompt、输出文本和取消。
- 重复 Event 不会重复渲染。
- 进程输出畸形 JSON、意外退出和超时能转换为稳定错误。
- 能正确处理权限请求过期、重复响应和 Agent 先结束的竞态。
- 支持 Session Load 的 Agent 可以重连；不支持时明确降级。
- Cyrene Managed 和 Agent Managed 两种模型路径分别验证。

### 19.2 UI 验收

- Agent Tab 不显示搜索框、搜索结果分页或高级搜索入口。
- 推荐区域只显示后端 Catalog 提供的推荐 Agent，并提供安装/升级状态。
- 点击“安装其他 Agent”会显示 API、Manifest 要求、请求示例和可用的复制按钮。
- 复制内容不包含任何 Cyrene 或模型凭据。
- 通过安装 API 成功安装的非推荐 Agent 出现在同一个“已安装”列表，并标记“外部来源”和验证状态。
- 推荐 Catalog 加载失败时，已安装的非推荐 Agent 仍然可见和可管理。
- Agent 行位于现有模型弹层第一行，未增加独立 Composer Agent 按钮。
- 空对话能选择 Agent；第一条消息后绑定锁定。
- 对话面板概览能看到 Agent、连接、模型来源、模型和两个 Session ID。
- 扩展的已安装 Agent 详情包含全部设置，不存在新的外部 Agent Settings Tab。
- 不支持图片的 Agent 无法通过点击、拖放或粘贴绕过限制。
- 不支持 Steer 的 Agent 在运行中不能发送 Guidance。
- 不支持 Reasoning 的 Agent 不显示空 Thinking Card。
- 动态权限按钮按 Agent 顺序和 Label 显示，提交原始 `optionId`。
- Agent 返回未知 Tool 类型时显示通用 Tool Card，页面不崩溃。
- Agent 崩溃后显示“重启 / 查看诊断 / 在新对话继续”。
- Agent 卸载后历史对话仍可读。
- Quick Chat 打开已有对话时使用原 Agent，不错误回落到 Cyrene 内置 Agent。
- 键盘导航、焦点、ARIA Label、中英文和窄窗口布局通过检查。

### 19.3 安全验收

- 浏览器 Network、React State、Chat JSON、Event Stream 和日志中都没有长期模型密钥或 Agent 凭据。
- 外部 Agent 调用安装接口只能生成待确认提案，不能绕过 UI 确认静默安装或启动程序。
- Manifest URL、版本、命令、校验摘要和 Source Trust 在确认前可见，并进入安装审计。
- 权限 UI 不根据本地化文本判断允许/拒绝。
- Agent 输出不能注入 HTML/Script。
- 禁用或卸载 Agent 后其短期 Gateway Token 立即不可用。
- Agent Profile 无法通过 Manifest 注入任意 Shell 模板或前端代码。

## 20. 向未来聚合与 Swarm 保留的兼容点

第一阶段不实现聚合或 swarm，但以下字段现在就应稳定下来：

- Chat 绑定使用 `installationId`，而不是写死产品名。
- Event 包含 `actorId`、`runId` 和可选 `parentRunId`。
- Artifact、Tool Call 和 Permission Request 都绑定具体 `actorId`。
- Agent Runtime 通过 Driver Registry 创建连接，而不是 Chat Route 中的产品分支。
- 能力是每个 Agent/Session 的数据，不是全局常量。

未来聚合层应该作为另一个实现同一内部 Agent 接口的“编排 Agent”，因此无需重写 Cyrene 前端。

## 21. 完成定义

第一阶段只有在以下端到端流程成立时才算完成：

1. 用户在扩展中心安装一个外部 Agent。
2. 非推荐 Agent 可以通过公开在 Agent Tab 弹窗中的安装提案 API 接入，经用户确认后出现在同一“已安装”列表。
3. 用户在该 Agent 的已安装详情中选择 Cyrene 模型或完成 Agent 自有登录。
4. Probe 显示 Agent 可用并保存能力结果。
5. 用户在 Composer 现有模型弹层的第一行选择该 Agent。
6. 第一条消息创建并锁定带 Agent Binding 的对话。
7. 外部 Agent 的文字、reasoning、工具、权限和错误通过统一事件模型显示。
8. Composer 和对话侧栏根据实际能力正确启用或降级。
9. 对话面板概览完整展示 Agent 与模型来源信息。
10. 应用重启后可以恢复支持恢复的 Session，或明确提示不支持恢复。
11. 整个流程没有向前端或外部 Agent 暴露不必要的 Cyrene 长期凭据。
