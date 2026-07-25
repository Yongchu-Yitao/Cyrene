# 使用

[English](usage.md) · [简体中文](usage.zh-CN.md)

## 启动 Cyrene

### Workbench（默认）

```bash
python -m cyrene --workbench
```

### Classic Agent UI

```bash
python -m cyrene --agent
```

### 无 Web 的交互 CLI

```bash
python -m cyrene.runtime.host
```

### `cyrene` 后台 Client

```bash
cyrene start
cyrene status
cyrene stop
```

Web UI 位于 `http://localhost:4242`。首次启动会进入 API Key 与人格设置。

主数据库为 `store/cyrene.runtime.database`。旧 `store/cyrene.db` 只在新库
未承载数据时自动迁移，并保留作为回滚副本。

### Electron 开发模式

```bash
cd electron
npm run dev
```

Electron Window 会通过物理 `src/cyrene/local_cli.py` 启动 Python Backend。

## Workbench

Workbench 以项目为中心：

| 页面 | 功能 |
|---|---|
| Welcome / Projects | 创建和切换项目 |
| Dashboard | Active Task、Recent Session、System Overview |
| Chat | 项目范围实时 Agent Chat |
| Schedule | Task、Deadline、Schedule |
| Knowledge / Library | 上传、文献管理、全文/结构化检索 |
| Memory | 检查 Project/Agent Memory |
| Model | Model 与 Endpoint |
| Help | Onboarding 与文档 |

## Classic Agent UI

| 页面 | 功能 |
|---|---|
| Chat | Message、运行 Guidance、Subagent、Browser |
| Agent Flow | LLM、Tool、Subagent 时间线 |
| Sessions | 搜索、查看、删除 Session |
| Memory | SOUL、Short-term、Context |
| Knowledge | 文档上传与 Search |
| Entities | 结构化 Entity |
| Evolution | Learned Pattern |
| Tasks | Schedule 与 History |
| Map | AMap/Leaflet |
| Status | Metric、Worker、Service、Context Debugger |
| Settings | Model、Tool Package、MCP、Search、API Key、Appearance |

## CLI

`cyrene` 是连接 `http://localhost:4242` 的轻量 HTTP Client。Loopback
Readiness/API 请求会忽略环境代理。

```bash
cyrene start
cyrene status
cyrene do "你的任务" --session run_live
```

| 命令 | 说明 |
|---|---|
| `cyrene start` | 后台启动 Workbench Daemon |
| `cyrene stop` | 停止检测到的 Daemon |
| `cyrene do <text> --session <id>` | 向 Session 发送消息 |
| `cyrene session list` | 列出 Live/Archived Session |
| `cyrene session status --session <id>` | 查看 Session |
| `cyrene session delete --session <id>` | 删除 Session |
| `cyrene flow --session <id>` | 列出 Round |
| `cyrene flow --session <id> --round <r>` | 查看 Execution Trace |
| `cyrene flow --session <id> --round <r> --id <e>` | 查看单个 Event |
| `cyrene memory soul [--edit <path>]` | 查看或替换 SOUL |
| `cyrene memory short-term` | 查看 Short-term Memory |
| `cyrene memory context` | 查看 Context 状态 |
| `cyrene status` | System Status |
| `cyrene mcp list` | MCP Server/Tool |
| `cyrene mcp add <name> stdio <cmd> [args...]` | 添加 stdio MCP |
| `cyrene mcp add <name> sse <url>` | 添加 SSE MCP |
| `cyrene mcp remove <name>` | 删除 MCP |
| `cyrene mcp toggle <name>` | 启用/关闭 MCP |

`--json` 输出 Machine-readable JSON。

`cyrene start` 是幂等操作：4242 已有健康 Cyrene 时只报告该实例，不启动重复
进程。`cyrene stop` 只针对检测到的 Daemon。

## 交互 Local CLI

```bash
python -m cyrene.runtime.host
```

| 命令 | 功能 |
|---|---|
| `/h` | Help、清 Context、Reset Personality、Status |
| `/mcp` | MCP 管理 |
| `/mcp list` | 列出 MCP |
| `/clear` | 清 Session Context |
| `/deep-reflect [focus]` | Deep Reflection |
| `quit` | 退出 |

Web UI 与交互 CLI 均支持 `/deep-reflect` 和 `/clear`。

## MCP

添加 stdio：

```bash
cyrene mcp add filesystem stdio \
  npx -y @modelcontextprotocol/server-filesystem /path/to/workspace

cyrene mcp add marp-deck stdio python /path/to/mcp_server.py
```

添加 SSE：

```bash
cyrene mcp add my-api sse http://localhost:3000/mcp
```

列出：

```bash
cyrene mcp list
```

MCP Capability 通过 `integration_tools` 动态发现，单个 Schema 不会全部加入
固定 Wire Bundle。Capabilities 页面按完整 Package 开关。关闭 Package 会从
Phase 1/2 Schema、专属 Prompt 和 Runtime Permission 中移除。Direct Tool
（例如 `AnalyzeAttachment`）保持可用。

## Knowledge 与 Library

支持 Text、PDF、Image 等文档。Pipeline：

1. 提取 Text；
2. 跳过 Binary 和超过限制的文件；
3. Chunk 并写入 `store/kb_<workspace>.db`；
4. 配置 Embedding 时生成 Vector。

Agent 通过 `knowledge_tools` 调用 `knowledge.search`。Workbench Library
还提供 Collection、Tag、Citation Metadata、Zotero 和结构化 Search。

## Browser Live View

Agent 使用 Browser Tool 时，Chat 显示 Live View。Electron 直接使用持久
Chromium Tab；非 Electron Playwright 遇到 Login Wall 时可请求 Login
Takeover。详见
[Browser Live View](browser-live-view.zh-CN.md)。

## Claude Code Bridge

安装 `tmux` 和 Claude Code 后，Cyrene 可以：

- 检测现有 Claude Code Session；
- 启动新 Session；
- 发送 Prompt 和读取输出；
- 在 UI 显示 Terminal。

通过 `code_tools` 使用 `code.check_claude_code`、
`code.start_claude_code`、`code.prompt_claude_code`。

## Telegram

在 Settings/加密配置中设置：

```ini
TELEGRAM_BOT_TOKEN=your_bot_token
OWNER_ID=your_telegram_user_id
```

然后运行：

```bash
python -m cyrene
```

Telegram 使用相同的两阶段 Agent Loop、Subagent 和 Tool。

## WeChat

配置 `WECHAT_BOT_TOKEN` 和 `WECHAT_OWNER_ID`，然后启动 Web UI。状态可在
Settings 查看。

WeChat 仍是 Alpha，可能需要可用代理。
