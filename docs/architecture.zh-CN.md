# 架构

[English](architecture.md) · [简体中文](architecture.zh-CN.md)

## 插件原生 Agent Runtime

Cyrene 现在使用由插件组装的一套连续 Agent Runtime，不再先经过路由阶段、再
进入另一套执行阶段。用户消息进入同一个可持久恢复的 Run，并在其中连续完成
模型输出、工具调用、向用户提问、取消、恢复、上下文压缩和最终结果投递。

```text
用户消息
    │
    ▼
由已启用的上下文插件构建 Context Tree
    │
    ▼
连续 Agent Run
    ├── 回复或向用户提问
    ├── 调用直接可见工具
    ├── toolbox.list → describe → invoke
    ├── 创建 Subagent 或通过 Inbox 协作
    └── 完成、取消或恢复
    │
    ▼
持久化并发布最终结果
```

只有维持 Runtime 运转所需的 Kernel Tool 固定在核心中。其余工具、工具包、
Context Mount、后台任务、应用服务、Route、界面贡献、Channel、Schedule、
Proactive、Knowledge 和 SOUL.md 都由插件提供。工具包和独立工具统一通过
`toolbox.list → describe → invoke` 发现；用户也可以把选定工具设为 Agent 直接
可见。Runtime 按当前插件 Schema 校验参数，并接受字段顺序不同但条目和内容完整
对应的对象参数。

启用的上下文插件把 Block 发布到可追踪的 Context Tree；稳定 Block 保持稳定
Identity 以复用 Prompt Cache，标准 Compactor 在不改变持久历史的前提下控制长
对话长度。必需的 System Prompt 插件首先挂载可编辑的 Agent 基础指令，内核只创建
空的 System Root。Composer Context 插件负责输入框中的 Workspace、MCP、Skills 等
上下文选择；SOUL 插件启用时把人格 Block 挂载在 System Prompt 正下方。Subagent
创建时继承 Main Agent 的初始 Tree，并额外获得 Main Agent 的任务指令，之后通过
持久 Inbox 协作。

### 插件如何组成一个 Agent

组装分为三个 Scope。Application 组装加载启用的插件包，由它们贡献 Route、
Service、Background Job、Channel、Settings Panel 与 Workbench View；Session
组装把同一批插件附着到一个 ContextTree，发布 Session Service 并绑定持久 Hook；
Run 组装再用当前对话选项触发这些 Hook，得到这一轮实际发送给模型的输入和能力集。

| 组装层 | 提供者 | Agent 实际获得的内容 |
|---|---|---|
| Kernel | Agent Runtime | 空 ContextTree Root、恢复/取消机制，以及固定的 `Bash`、`Read`、`Write`、`toolbox` |
| 基础指令 | `cyrene_system_prompt` | 挂载在 `system` 位置的可编辑 System Prompt；缺失或为空时 Fail Closed |
| 人格 | `cyrene_soul` | 可选 SOUL.md Block，挂载在 `top`，紧随 System Prompt |
| 对话选项 | `cyrene_composer_context` | 输入框 Context 菜单选择的 Workspace、MCP Server、Skills 与其他能力 |
| Runtime 与 Memory | Context、Memory、Entity 和功能插件 | 本轮 Metadata、Project Memory、相关 Entity、Attachment 与功能上下文 Block |
| 推理 | Model Provider 插件 | Model Catalog、选中的 Profile、Completion Stream、Usage 与 Model Identity |
| 能力 | 工具包和独立工具插件 | 直接可见工具的 Schema；其他工具通过 `toolbox` 渐进发现其 Metadata 与 Schema |
| 工作前后行为 | Tree-local Hook | 权限决策、参数归一化、学习记录、Context 计量、收尾与取消 |

Plugin Center 控制全局可用性；Composer Context 插件控制每个对话的选择；工具
可见性则是第三个独立开关：“Agent 直接可见”会把当前 Schema 放入模型即时 Tool
List，“Agent 寻找使用”则只通过 Toolbox 发现。两条路径解析的是同一个活动 Plugin，
经过同一套 Schema 校验和 Hook 审核，不存在第二份工具实现。

ContextTree 不只是 Transcript，也是完整的组装记录。它持久化 System Root、带来源
的有序 Context Mount、User/Assistant Node、Tool Call/Result、Model Identity 与
Usage、Compaction Node、Subagent State 和恢复 Checkpoint。“对话上下文”面板和 CLI
`/context` 都投影这棵 Tree，因此界面显示的构成就是实际发送给模型的构成。

### Hook 生命周期

Hook 属于 ContextTree，并在恢复后保留 Plugin Binding。Session 打开时插件包绑定
实现；恢复旧 Tree 时会先按相同 Plugin ID 重新绑定，再继续未完成工作。

| Hook | 在 Agent 组装中的职责 |
|---|---|
| `SessionStart` | 每个对话只运行一次，并冻结有序且可缓存的稳定 Context 前缀。 |
| `TurnStart` | 构建本轮动态 Context 后缀，并为重试冻结结果。 |
| `ContextChange` | 响应已提交的 Tree 变化，让依赖上下文的 Session 工作继续推进，无需轮询另一份状态。 |
| `ContextUsed` | 接收真实 Model Path 中每个 Block 的 Token 贡献与使用比例，供 Memory 和 Compaction 计量。 |
| `PreToolUse` | 对每个已解析调用运行确定性参数护栏。Plugin 可声明 `permission_boundary`，也可在解析真实目标后通过 Session Permission Service 报告动态边界；只有实际边界才进入固定 Permission Reviewer，由它基于最终参数允许、阻止或暂停并请求一次精确用户确认。 |
| `PostToolUse` | 只观察一次已完成结果，让插件持久化 Learning、Activity 或 Integration State。 |
| `SessionEnd` | Run 已有持久结果后，完成插件拥有的收尾工作。 |
| `Stop` | 用户停止 Run 或 Session 关闭时，取消或关闭插件拥有的工作。 |

Context Contribution 是普通 Plugin Output，不是 Model Router 内部的字符串拼接。
每项贡献都有稳定 Tree Identity、Mount Position、Source 与 Failure Policy。System
Prompt、Composer Context 等必需 Provider Fail Closed；可选的临时 Runtime Context
可以 Fail Open。Session Mount 始终位于 Turn Mount 之前；稳定 Identity 与逐字节复用
能保留 Provider Prompt Cache 前缀，切换选项只生成下一轮明确的动态后缀。
只有 SessionStart Mount 会投影到首条 System Message；TurnStart Mount 追加到当前
User Message，形成 `稳定 system → 既有历史 → 当前 user + 动态后缀` 的缓存顺序。
冻结前缀带有持久依赖指纹，覆盖 SessionStart Hook 拓扑、贡献插件包实现版本、SOUL
状态与内容、稳定 Memory Snapshot，以及已学习技能版本。指纹变化只创建一次新的稳定
Epoch，之后未变化的轮次继续复用。

## Runtime 启动与迁移

所有 Host Mode 共享 `RuntimeContext`、`ApplicationLifecycle` 和
`cyrene.platform` 中的 Bootstrap：

```text
解析路径 → 创建 Runtime 目录 → 迁移旧数据库
→ 初始化数据库/Memory/Learning → 启动托管服务 → 提供 UI
```

主数据库是 `store/cyrene.runtime.database`。如果存在旧
`store/cyrene.db` 且新 Target 未承载数据，启动会使用 SQLite Backup API，
校验 Snapshot，写入幂等 Marker，并保留 Source 用于回滚。如果 Target 已有
数据但状态不明确，启动会停止而不是覆盖。

## 主要功能

### 人格系统（SOUL.md）

`workspace/SOUL.md` 是唯一的全局 Personality 与持久 Memory Document。
Steward Agent 审阅新 Conversation，并可通过 `APPEND`、`ERASE`、`MERGE`
更新它。默认间隔为一小时，且最短会被限制为一小时。带日期的 `TEMPORARY`
Entry 超过 24 小时后会在组装 Memory Context 时被过滤，但不会仅因过期而
静默改写 Source Document。

### Multi-Agent 编排

`cyrene_subagent` 插件包通过 `toolbox.list → describe → invoke` 提供创建、定向
消息、广播和 Round 查询工具；选中的工具也可设为直接可见。每个 Subagent 从 Main
Agent 的初始 ContextTree 组成加上 Main Agent 的任务指令开始，随后拥有独立 Branch
和 Model Loop。Actor Policy 移除 Main-only 能力，持久 Inbox 双向传递消息。生命周期为：

```text
running → waiting → resumed → done / timeout
```

### Memory 分层

| 层 | 存储 | 容量/维护 |
|---|---|---|
| Conversation Context | `data/context/` 下的 Tree SQLite Store 与 Tree Index | Agent ContextTree Runtime；完整历史持久化，模型输入按需压缩 |
| Project Memory | 以 Project Memory Key 保存的 Workbench Document | Workbench Memory Service |
| Short-term Memory | `data/plugin_data/cyrene_memory/short_term.json` | `cyrene_memory` 插件维护跨 Session Summary、过期与退役状态 |
| Long-term Identity | `workspace/SOUL.md` | 全局唯一，由 `cyrene_soul` 插件与 Steward Agent 维护 |

Short-term Entry 保存情绪、提及次数和 Fact/Pattern/Preference/Emotion 类型。

### Knowledge 与 Library

可编辑的 `cyrene_knowledge` 插件包完整拥有 Knowledge 后端：SQLite Schema、
托管附件、内容抽取、分块、本地向量、混合检索、Zotero 同步、Workbench HTTP
Route、全局搜索与 Agent 工具。数据统一保存在
`data/plugin_data/cyrene_knowledge/`，所有记录按 Workbench Project 隔离。
Chat Attachment、Generated Export 和完成后的 Task Artifact 都进入同一个
插件服务；旧 `cyrene.knowledge` 与 Workbench Library Route 不再位于活动调用链。
Agent 通过 `toolbox.list → describe → invoke` 使用这些工具。

### Entity

可编辑的 `cyrene_entity` 插件包通过
`toolbox.list → describe → invoke` 提供 `entity.track`、`query`、`update`、`delete`；
旧实体工具入口不再注册。

### Skill

运行时可安装 `.md`、目录或 `.zip` Skill，并可列举、启用和卸载。已学习
Workflow 通过 `skill_tools` 渐进披露。

### 行为学习

每轮执行会记录目的和 Tool Chain。低风险声明式 Workflow 可以通过
`skill.run_learned` 调用。

### Terminal Daemon

独立本地服务负责交互式 PTY、持久化元数据、滚动记录、VT 屏幕和退出唤醒。
Electron 与 Web 后端仅作为客户端连接，因此关闭视图不会结束终端。

### Code Tool

可编辑的 `cyrene.plugins.builtin.cyrene_code` 插件包通过 `code_tools`
渐进提供：

- Symbol/Reference/Import/File Hash SQLite Index；
- Symbol、Caller、Reference、File Summary 分析；
- Diff、Blame、Log、Branch、Status 等 Git 能力。

### MCP

支持 stdio 与 SSE MCP Server。Schema 通过 `integration_tools` 按需发现，
不会全部加入固定 Wire Bundle。可从 Settings 或 CLI 管理。

### Scheduler

可编辑的 `cyrene_schedule` 插件包负责全部定时任务行为。Agent 通过
`toolbox.list → describe → invoke` 使用 `schedule.create`、`list`、`edit`、
`pause`、`resume`、`cancel` 和 `runs`。隐藏的 `schedule.tick` 在插件元数据中
声明后台任务；通用 Plugin Background Host 只提供时钟，并在每次触发时调用用户
目录中的当前实现。

任务和执行历史持久化在 SQLite。Lease Claim、稳定 Run ID 与按 Revision 的
Finalize 防止重复执行，也避免旧 Run 覆盖并发 Pause/Edit。Agent Task 直接通过
Workbench Chat Runtime 执行并把一个最终结果投递回 Workbench。插件包的
`application_setup` 同时注册 Workbench Route、全局搜索 Provider 和 Schedule
模块；插件未加载时不会回退到另一套内置定时任务后端。

### Cyrene 自管理控制面

`cyrene_tools` 是只给 Main Agent 的渐进式工具包。模型 Wire 只包含一个稳定
Gateway，具体 Schema 通过 `discover → describe → invoke` 披露。公开能力仅包括
App Status/Window、当前 Surface 的 Snapshot/Inspect/Click/Double Click/Type/Scroll/Drag，
以及 Typed Settings Describe/Read/Update。

UI 控制绑定发起当前本地轮次的 Electron Renderer。Snapshot 只暴露当前层和当前
视口，Inspect 读取一个组件和分页子树；Mutation 绑定确切 `snapshot_id`、Revision、
Node 和 Action，不接受 Selector、Script、Raw Event 或任意坐标。显式语义节点与
受限 DOM 投影会去重。消息正文、流式输出和消息控件重渲染仍可读取，但不会推进
可操作 Revision；审批、问题、Layer 和 Action 集合变化仍会推进。Composer Send
是显式 R2 动作，停止当前运行是 R1。若无关的全局 Revision 仍然推进，Renderer
会用有界节点级动作租约核对 Node、Action、Risk、Scope 和安全关键状态；完全未变
才允许旧 Snapshot 执行，Agent 仍须原样传递旧 Revision。

Double Click 是独立 Gesture Capability，只接受显式声明 `double_press` 或
`double_click` 的 `invoke` Action。例如 Browser PiP 标题栏声明
`maximize + double_press`，Agent 可直接调用 Renderer 注册的 Handler 完成最大化，
不依赖窗口焦点和屏幕坐标；普通单击 Action 会被该工具拒绝。

Project、Chat、Backup、Update、Lifecycle 和 Cross-session Message handler 都是
Internal Service，并从所有 Agent Catalog 屏蔽。唯一公开的持久后台 Mutation 是
带 Revision 的非模型 Typed Settings Service。R2/R3 委托根据真实本地用户本轮
原文审核；批量票据按参数绑定顺序逐项消费。

Agent 可提供精确用户引用；若省略，则由同一个 Permission Reviewer 审查当前完整
`desktop_local` 用户请求。缺少 Client Request ID 不会使已绑定的可信 Session/Round
身份失效。Permission Card 根据结构化 Meta 和当前界面语言生成文案，把带风险后缀
的 Operation ID 映射为本地化 Capability 名称，并隐藏内部关联 Fingerprint。

### Web UI

Cyrene 只提供 Workbench 前端。主要区域是 Task、Chat、Knowledge/Library、
Schedule 和 Memory；Search、Browser/PDF/Diff、Settings、Onboarding、Help、
Profile 和 Quick Chat 是 Overlay、Panel 或 Secondary Surface，不是旧 UI
页面。唯一源码根是 `src/cyrene/workbench/webui/frontend`，唯一生成输出根是
`src/cyrene/workbench/webui/static/app`。

`WorkbenchTopbar` 明确维护两个独立集合：最多 3 个 Task/Chat Session 的本地
MRU/置顶列表，以及持久化的 Pinned Resource Shelf。资源拖动使用内部 MIME
`application/x-cyrene-work-resource+json`；macOS 原生文字拖动以
`text/plain` 接收并固化为 Markdown。知识库附件由服务端解析，Renderer Payload
不暴露本机绝对路径。

固定资源 Registry 通过 Workbench Document Store 持久化。后续 Agent Context
只注入紧凑的 File/Browser 索引，文件正文按需读取。Browser Reference 记录
Owner Session：Owner 保留正常控制，其他 Session 在 Tool 执行层只能调用
Snapshot/Screenshot。这是执行层权限约束，不只是 Prompt 提示。

Browser 拖到另一个 Conversation 时不转移原 Browser Reference，也不提升固定
资源权限。Renderer 通过 Electron `browser:create-tab` 为目标 Session 的
`BrowserTabManager` 新建同 URL 页面，再以事件同步目标对话的 PiP 状态。Manager
仍按 Session 隔离，只有持久 Partition（Cookie/Login）共享。

Web UI 绑定 `127.0.0.1`，由 FastAPI Backend 提供。Electron 每次启动生成
Shared Token、传给 Python Child，并把它作为 `X-Cyrene-Token` 注入 Desktop
Request。OS Keyring 保存的是保护 `data/config.enc` 的 Fernet Key，不是该
Per-launch HTTP Token。

Electron Browser Tool 通过 Token-authenticated loopback RPC 直接使用内嵌
Chromium，并与可见 `WebContentsView` 共享持久 Profile。打包桌面版不包含
Playwright/第二份 Chromium。非 Electron 模式可选 Playwright，最终可回退到
`httpx` 文本导航。

### Search

内置 [SimpleXNG](https://github.com/jlevy/simplexng)，无需 Docker。Manager
生成 `data/simplexng_settings.yml`、默认使用 8888、处理代理并管理子进程。

### Context Trace

每次 LLM 调用都带 `_ctx` Provenance，标明 System Prompt、SOUL、Short-term、
History、Tool Result 等来源。`--verbose` 写入 `data/debug_*.jsonl`，API 为
`GET /api/context-debug/events`，也可使用 `cyrene flow` 或正式模块
`cyrene.observability.context_debug`。Context Trace 不提供 Workbench 页面；
内部 `_ctx` 在 Provider 调用和持久化前移除。

### CLI

- `cyrene <command>`：连接 `localhost:4242` 的 HTTP Client，包含
  `start`、`stop`、`do`、`session`、`flow`、`memory`、`status`、`mcp`；
- `python -m cyrene.platform.host`：不启动 Web Server 的交互 REPL。

## 安全与本地认证

Raw Web Server 只绑定 `127.0.0.1`，校验本地 Host/Origin，没有 User Login，
不适合作为远程服务暴露。Electron 要求 Desktop 请求携带 Per-launch Token。
Credential 保存在 `data/config.enc`；其 Fernet Key 优先进入 OS Keyring，
Keyring 不可用时回退到权限为 0600 的本地 Key File 并记录 Warning。

这些属于应用层 Control，不是 OS Sandbox 或 Multi-tenant Boundary。Project
Store 和 Permission Mode 不能隔离互不信任的用户。只有 Config Blob 由应用
加密；Workspace File、Database、Log/Trace、Export 和 Backup 仍依赖操作系统
Storage Protection。Portable Backup ZIP 为跨 Installation Restore 包含
Logical Config Snapshot，其中可能存在 Credential。

## 项目结构

```text
src/
├── cyrene/
│   ├── core/                与 Host 无关的 Agent Runtime
│   │   ├── context/         持久 Context Tree
│   │   ├── hook/            Tree-local Lifecycle Hook
│   │   └── plugin/          Plugin 对象、Scope、Registry 与执行
│   ├── plugins/             Cyrene 产品插件层
│   │   ├── application.py  Application Scope Plugin Host
│   │   ├── context.py      Workbench Application Contribution SDK
│   │   ├── model_*.py      Model Provider 组装
│   │   └── builtin/        标准可编辑功能插件
│   ├── workbench/           Cyrene Host 适配层
│   │   ├── application/    应用编排、事件与通知
│   │   ├── chat/           Chat Service、Repository 与 Run
│   │   ├── tasks/          Task 执行与 Workflow Service
│   │   ├── projects/       Project 生命周期、文件与组装
│   │   ├── goals/          可持久恢复的 Goal Loop
│   │   ├── planning/       Planning Contract 与 Helper
│   │   ├── artifacts/      Artifact、Presentation 与 Export
│   │   ├── sessions/       Session Context 与 Presentation
│   │   ├── control/        外部 Control Port 与 Projection
│   │   ├── workspaces/     Workspace Change 与 Diff Service
│   │   ├── ui/             UI Surface 抽象
│   │   ├── core_adapter/   Session/Chat/Task 与 Core 的桥接
│   │   ├── http/           FastAPI/HTTP 组装
│   │   ├── persistence/    Workbench 持久化
│   │   └── webui/         App Lifecycle 与唯一 SPA 源码/输出
│   ├── agents/              外部 ACP Agent 集成
│   ├── model/               Provider Transport/Runtime 支持
│   ├── platform/            进程 Bootstrap、配置、存储与 Lifecycle
│   └── observability/       Trace、Debug、Telemetry
tests/
data/
workspace/
store/
```

`cyrene.core` 不得导入 `cyrene.plugins` 或 `cyrene.workbench`。产品层通过
Plugin Scope 提供 Model 和 Application Service，Workbench 在打开 Core Session
时显式传入该 Application Scope。原顶层 `agent`、`route`、`webui` 包已删除，
不发布兼容外壳或重复实现。
Workbench 业务模块按领域组织；包根目录不放业务 Service 实现，也不保留旧路径
转发模块。
