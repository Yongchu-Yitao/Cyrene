# 使用

[English](usage.md) · [简体中文](usage.zh-CN.md)

## 启动 Cyrene

### Workbench

```bash
uv run python -m cyrene
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
| Welcome / Projects | 创建、编辑、切换和删除 Project；选择 Workspace Directory |
| Task | 创建、规划、批准、执行、暂停、验收、修复和复核 Task Session |
| Chat | Project 范围实时 Chat 和 Session History |
| Knowledge / Library | 导入 Document/书目文件、管理 Literature 和 Retrieval |
| Schedule | 查看和管理 Scheduled Task |
| Memory | 检查、搜索、创建和 Retire Project Memory |
| Settings Overlay | 配置 Model、Integration、Capability、Channel、Agent、Data 和 Budget |
| Help/Profile/Search | Secondary Overlay/Navigation，不是旧 UI 页面 |

### 顶栏 Work Tabs 与固定资源

顶栏显示最近主动打开的 3 个 Task/Chat Session。打开、新建或切换 Session 会
实时更新 MRU。右键 Session Tab 可置顶/取消置顶、复制标题、从顶栏移除，或查看
该 Chat 当前关联的 Browser 和 File。移出顶栏不会删除或停止底层 Session。

Session Tabs 与搜索之间是固定资源 Shelf。`+` 落点提供 Hover 提示，可接收：

- Chat 文件卡片和 Knowledge/Library 表格行或卡片；
- 选中文字（macOS 使用原生文字拖动）；
- Electron Browser 的 PiP 小窗或最小化胶囊。

固定后的 File/Browser 默认只显示 SVG 图标，Hover 或键盘 Focus 时才展开名称。
选中文字和无附件 Library Item 会固化为 Markdown。把 File/Text 拖到其他 Chat
Tab 只加入该 Chat 的输入草稿，不会自动发送。

把 Browser PiP、favicon 最小化按钮或已固定 Browser 图标拖到另一个 Chat Tab，
会在目标对话的 Browser 中新建同 URL 页面。两个对话共享登录 Profile，但拥有
独立页面和控制权。

顶栏支持键盘操作：Focus 后用左右方向键、Home/End 遍历 Session 与资源，
Enter/Space 打开，Delete/Backspace 移除。`Cmd/Ctrl+1…3` 打开三个 Session，
`Ctrl+Tab` / `Ctrl+Shift+Tab` 前后切换，`Cmd/Ctrl+W` 从顶栏移除当前 Session
但不停止任务。项目快捷键为 `Cmd/Ctrl+Shift+1`。

固定 File 会作为全局用户资源索引进入后续 Agent Turn。固定 Browser 只有所属
Session 保留控制权；其他 Session 只能获取 Snapshot/Screenshot，不能导航、
点击、输入、刷新、上传或进行其他页面修改。取消固定只删除顶栏引用，不删除源
文件、文字、知识库条目或页面。

Workbench 是唯一 Web UI。实时 Markdown 对话、运行 Guidance、Subagent 与
Browser 状态、Session History、Memory、Knowledge/Search、Schedule、Map、
Model/Tool/MCP/API Key 设置和主题能力都通过 Workbench 的页面、面板或
Settings Overlay 提供。

Verbose Context Trace 不属于 WebUI；通过 `cyrene flow`、
`/api/context-debug/events` 或
`python -m cyrene.observability.context_debug` 检查。

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

通过 Workbench 导入或在 Chat 中添加 Attachment。仅把任意文件放到 Project
Workspace 不会自动加入 Knowledge Database。支持保存 Text/Code、PDF、
DOCX/PPTX/XLSX、Image、Audio、Video 和其他 Binary Attachment。Pipeline：

1. 保存 Attachment、Metadata 和 Content Hash；
2. 从 PDF、Office XML、可读 Text 提取内容；配置 Vision Model 时可描述 Image；
3. 未知 Binary 只 Archive，不生成乱码 Chunk；普通 Text Extract 超过 10 MiB
   时跳过；
4. Chunk 写入 `store/kb_<project-data-key>.db`；
5. 只有配置 Embedding Provider 后才生成 Vector。

同一个 Project Database 中的 Literature Library 提供 Collection、Tag、
Status、Metadata、Note、Annotation、Attachment、Relation、Citation、
CSL JSON/RIS/BibTeX Import、JSON Export 和只读 Zotero Desktop Local API
Import。DOI/Title Lookup、Zotero Web API 双向同步和 Manuscript Editor 尚未
实现。

Agent 通过 `knowledge_tools` 使用 Project Search 和 Library Operation。

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
uv run python -m cyrene --telegram
```

Telegram 使用相同的两阶段 Agent Loop、Subagent 和 Tool。

## WeChat

打开 **Settings → Channels → WeChat**，获取 QR Code、使用微信扫码确认并
启动 Channel。iLink Bot Token 会保存到加密配置，无需重启。
`WECHAT_BOT_TOKEN` 和 `WECHAT_OWNER_ID` 仍作为历史配置输入保留，但当前 UI
流程不要求用户手工获取或填写。

WeChat 仍是 Alpha，并依赖 WeChat iLink Bot Service 的可用性与行为。
