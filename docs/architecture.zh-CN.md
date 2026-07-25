# 架构

[English](architecture.md) · [简体中文](architecture.zh-CN.md)

## 两阶段 Agent Loop

Cyrene 使用两阶段决策循环：保持模型面对的 Wire Schema 稳定，同时只在需要
时启用具体能力。Tool Round 上限可配置，默认 15。

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
    │   ├── entity/map/subagent/delivery/skill/integration
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

`workspace/SOUL.md` 保存身份、信念、关系、Memory 和 Pattern。Steward Agent
按配置间隔审阅对话，通过 `APPEND`、`ERASE`、`MERGE` 更新 SOUL。临时记录会
过期，最终回复会根据人格进行表达。

### Multi-Agent 编排

Main Agent 通过 `subagent_tools` 调用 `subagent.spawn`。每个 Subagent 获得
独立稳定 Wire Bundle；Actor Policy 过滤 Main-only 能力。Agent 通过 Inbox
发送或广播消息。生命周期为：

```text
running → waiting → resumed → done / timeout
```

### 三层 Memory

| 层 | 存储 | 容量/维护 |
|---|---|---|
| Context Window | `data/state.json` | 默认约 40 条，自动 Trim/Compact |
| Short-term | `data/short_term.json` | 压缩摘要，后台维护 |
| Long-term | `workspace/SOUL.md` | Steward Agent |

Short-term Entry 保存情绪、提及次数和 Fact/Pattern/Preference/Emotion 类型。

### Knowledge 与 Library

文档会被 Hash、分块、Embedding 并存入项目 SQLite。`knowledge_tools` 提供项目
文档和文献库能力，例如 `knowledge.search`、`knowledge.library.search`。
`AnalyzeAttachment`、`WebSearch`、`WebFetch` 是 Direct Tool。

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

### Web UI

Cyrene 同时提供：

- **Workbench**：项目 Dashboard、Schedule、Knowledge、Library、Memory、
  Chat、Model 和 Help；
- **Classic Agent UI**：Chat、SSE、Agent Flow、Session、Memory、Status、
  Setting、Evolution、Task、Knowledge、Entity、Map、Browser、Claude Code。

两者绑定 `127.0.0.1`，共用同一个 FastAPI Backend。Electron 使用 OS Keyring
和 Local Auth Middleware。

Electron Browser Tool 通过 Token-authenticated loopback RPC 直接使用内嵌
Chromium，并与可见 `WebContentsView` 共享持久 Profile。打包桌面版不包含
Playwright/第二份 Chromium。非 Electron 模式可选 Playwright，最终可回退到
`httpx` 文本导航。

### Search

内置 [SimpleXNG](https://github.com/jlevy/simplexng)，无需 Docker。Manager
生成 `data/simplexng_settings.yml`、默认使用 8888、处理代理并管理子进程。

### Context Debugger

每次 LLM 调用都带 `_ctx` Provenance，标明 System Prompt、SOUL、Short-term、
History、Tool Result 等来源。`--verbose` 写入 `data/debug_*.jsonl`，API 为
`GET /api/context-debug/events`。内部 `_ctx` 在 Provider 调用和持久化前移除。

### CLI

- `cyrene <command>`：连接 `localhost:4242` 的 HTTP Client，包含
  `start`、`stop`、`do`、`session`、`flow`、`memory`、`status`、`mcp`；
- `python -m cyrene.runtime.host`：不启动 Web Server 的交互 REPL。

## 安全与本地认证

Raw Web Server 只绑定 `127.0.0.1`，不适合作为远程服务暴露。Electron
生成随机 Local Token，保存在 OS Keyring，并要求所有 Desktop 请求携带。

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
├── webui/                   App Lifecycle、Auth、SPA Hosting
├── workbench-webui/         Workbench 前端
├── tests/
├── data/
├── workspace/
└── store/
```

`cyrene.db`、`cyrene.scheduler`、`cyrene.workbench_runtime` 等历史 Import 由
`cyrene/runtime/module_compat.py` 惰性解析到完全相同的正式模块对象，不需要
重复顶层实现文件。`local_cli.py` 是唯一物理兼容启动器，因为 Electron 开发
流程仍执行这个确切路径。
