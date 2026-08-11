# 架构

[English](architecture.md) · [简体中文](architecture.zh-CN.md)

## 两阶段 Agent Loop

Cyrene 使用两阶段决策循环：保持模型面对的 Wire Schema 稳定，同时只在需要
时启用具体能力。

```text
用户消息
    │
    ▼
Phase 1（Policy 只允许 use_tools / ask_user / quit）
    ├── 纯对话 → 直接返回（一次 LLM 调用）
    └── 需要 Tool → Phase 2
            │
            ▼
    Phase 2（同一份固定 Wire Tool 定义）
    │   ├── Direct：文件、Bash、WebSearch/WebFetch、附件分析
    │   ├── code/browser/desktop/memory/knowledge/task
    │   ├── entity/map/subagent/delivery/skill/cyrene/integration
    │   ├── 每个 Package：discover → describe → invoke
    │   └── quit → 结束交互
    ▼
返回响应
```

普通 Main Agent 的两个阶段在相同设置下获得字节稳定、顺序确定、完全相同的
Wire Bundle。Capabilities 页面按完整 Package 开关能力；关闭 Package 会同时
从 Schema、专属 Prompt 和 Runtime Permission 中移除。Direct Tool 不受这些
开关影响。Deep Research 的篇幅选择 Handshake 使用独立轻量 Tool Set。

## Runtime 启动与迁移

所有 Host Mode 共享 `RuntimeContext`、`ApplicationLifecycle` 和
`cyrene.runtime` 中的 Bootstrap：

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

Main Agent 通过 `subagent_tools` 调用 `subagent.spawn`。每个 Subagent 获得
独立稳定 Wire Bundle；Actor Policy 过滤 Main-only 能力。Agent 通过 Inbox
发送或广播消息。生命周期为：

```text
running → waiting → resumed → done / timeout
```

### Memory 分层

| 层 | 存储 | 容量/维护 |
|---|---|---|
| Conversation Context | 默认历史 Session 使用 `data/state.json`；Named Session 使用 `data/sessions/<session>/state.json` | Agent Session Runtime |
| Project Memory | 以 Project Memory Key 保存的 Workbench Document | Workbench Memory Service |
| 历史 Short-term | `data/short_term.json` | 默认 Session 兼容，由 Compressor/Steward 维护 |
| Long-term Identity | `workspace/SOUL.md` | 全局唯一，由 Steward Agent 维护 |

Short-term Entry 保存情绪、提及次数和 Fact/Pattern/Preference/Emotion 类型。

### Knowledge 与 Library

通过 Workbench 导入的文件、Chat Attachment、Generated Export 和 Zotero
Attachment 会被 Hash 并存入项目 SQLite。可提取内容会被分块；只有配置
Embedding Provider 后才生成 Embedding，否则仍提供 Lexical/FTS Retrieval。
把任意文件放进 Project Workspace 不会自动 Ingest。`knowledge_tools` 提供
项目 Document 和 Literature Library 能力；`AnalyzeAttachment`、
`WebSearch`、`WebFetch` 是 Direct Tool。

### Entity

`entity_tools` 提供 `entity.track`、`query`、`update`、`delete`。

### Skill

运行时可安装 `.md`、目录或 `.zip` Skill，并可列举、启用和卸载。已学习
Workflow 通过 `skill_tools` 渐进披露。

### 行为学习

每轮执行会记录目的和 Tool Chain。低风险声明式 Workflow 可以通过
`skill.run_learned` 调用。

### Claude Code Bridge

当 `tmux` 和 Claude Code 可用时，Cyrene 能检测、启动、发送 Prompt 并读取
Claude Code Session。实现位于 `cyrene.tooling.backends`。

### Code Tool

`cyrene/tool_impl/code/` 通过 `code_tools` 渐进提供：

- Symbol/Reference/Import/File Hash SQLite Index；
- Symbol、Caller、Reference、File Summary 分析；
- Diff、Blame、Log、Branch、Status 等 Git 能力。

### MCP

支持 stdio 与 SSE MCP Server。Schema 通过 `integration_tools` 按需发现，
不会全部加入固定 Wire Bundle。可从 Settings 或 CLI 管理。

### Scheduler

`task_tools` 的 `task.schedule` 创建 Cron、Interval、One-shot Task，并在
SQLite 保存执行历史。

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
页面。唯一源码根是 `src/webui/frontend`，唯一生成输出根是
`src/webui/static/app`。

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
- `python -m cyrene.runtime.host`：不启动 Web Server 的交互 REPL。

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
│   ├── agent/               Agent Loop 与内部公共 API
│   ├── workbench/           Workbench 业务服务
│   ├── model_runtime/       Provider/Model Runtime
│   ├── learning/            行为与 Skill 学习
│   ├── runtime/             Bootstrap、Lifecycle、Scheduler、Persistence
│   ├── observability/       Trace、Debug、Telemetry
│   ├── knowledge/           Ingestion、Embedding、Storage
│   ├── channels/            Telegram、WeChat
│   ├── tooling/             Tool Control Plane 与 Backend
│   ├── tool_impl/           按领域划分的 Tool 实现
│   ├── config.py
│   ├── call_llm.py
│   ├── browser.py
│   ├── subagent.py
│   ├── memory.py
│   ├── cli.py
│   ├── tools.py
│   ├── __init__.py
│   ├── __main__.py
│   └── local_cli.py         Electron 物理启动垫片
├── route/                   FastAPI Adapter 与 Registry
├── webui/                   App Lifecycle、Auth、唯一 Workbench 前端与 SPA Hosting
│   ├── frontend/            唯一 React/JSX 源码根
│   └── static/app/          唯一生成/打包输出根
tests/
data/
workspace/
store/
```

`cyrene.db`、`cyrene.scheduler`、`cyrene.workbench_runtime` 等历史 Import 由
`cyrene/runtime/module_compat.py` 惰性解析到完全相同的正式模块对象，不需要
重复顶层实现文件。`local_cli.py` 是唯一物理兼容启动器，因为 Electron 开发
流程仍执行这个确切路径。
