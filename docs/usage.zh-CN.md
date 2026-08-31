# 使用

[English](usage.md) · [简体中文](usage.zh-CN.md)

## 启动 Cyrene

### Workbench

```bash
uv run python -m cyrene
```

### 无 Web 的交互 CLI

```bash
python -m cyrene.platform.host
```

### `cyrene` 后台 Client

```bash
cyrene chat
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

Electron Window 会通过 `uv run cyrene --workbench --electron-mode` 启动
Python Backend。

## Workbench

Workbench 以项目为中心：

| 页面 | 功能 |
|---|---|
| Welcome / Projects | 创建、编辑、切换和删除 Project；选择 Workspace Directory |
| Chat | Project 范围的对话、计划、Goal Loop、Agent Run 和 Session History |
| Knowledge / Library | 导入 Document/书目文件、管理 Literature 和 Retrieval |
| Schedule | 查看和管理 Scheduled Task |
| Memory | 检查、搜索、创建和 Retire Project Memory |
| Settings Overlay | 配置 Model、Integration、Capability、Channel、Agent、Data 和 Budget |
| Help/Profile/Search | Secondary Overlay/Navigation，不是旧 UI 页面 |

### Conversation Goal 与 Plan

Cyrene 不再提供独立 Task 产品或 Task 页面。项目工作直接新建 Conversation；当
结果必须持续执行直到验收时，在对话中输入 `/goal`。Agent 会先研究需求并与用户
讨论，再提出具体目标和验收标准。Goal Tab 只在目标存在时显示，用户可在其中编辑
目标和最长持续时间、确认目标、查看当前 Plan 与 Review 结果、手动接受当前成果，
或随时停止目标。

确认后，普通 Agent Turn 结束不会静默终止 Goal。Cyrene 会持续规划、执行、测试
和修复，直到独立 Reviewer 给出“通过”，或用户停止、需要审批/回答、触发明确的
安全限制。Review 不通过时会显示关键缺口，并把缺口带入下一轮修复。发布、发送、
部署等外部动作仍统一走 Permission Review，不会因 Goal Loop 被自动重复执行。

### Dynamic File Workspace

当用户明确要求 Agent 编辑或显示某个文件，或处理目录结构时，Cyrene 可在对话
旁边打开 Workspace。可用 Tab 由实际内容决定：

- **Editor** 显示当前 Text File，并跟随已确认的磁盘变更；
- **Terminal** 显示正在运行的 Workspace Action 或用户打开的 Terminal；
- **Problems** 显示 Build/Test Diagnostic，并可跳回对应文件；
- **Review** 用同一 Diff Viewer 比较 Conversation Snapshot 或 Git Working Tree；
- **Preview** 显示可用的 Web Endpoint、PDF、Image 或 Generated File；
- **Files** 浏览 Workspace，且不会一次加载完整的大型目录树。

没有内容的 Tab 自动隐藏。Active Tab 在底部栏高亮，Action 与 Review Control 保持
在顶部。Surface 会跨导航和重启记住 Current File、Folder、Review Source、
Execution 与用户接管的布局。Agent Activity 只更新已有 Surface，不会不断打开无关
文件，也不会替换尚未保存的用户 Buffer。

### Project Action 与 Runtime Plugin

Project Editor 提供可选 Action Profile。Cyrene 可以根据 Workspace 自动填写，用户
也可主动补充或修复。Workspace Toolbar 对选中的 Build、Run、Test 或 Preview
Action 只显示一个 Run Button；Long-running Process 活动时可 Stop。有限命令会
保留 Output 与 Diagnostic，不会被误报成 Terminal Crash；长驻服务使用稳定的
Terminal Identity，并允许短暂断线后重连。

Project 支持由 Plugin 提供。内置类型覆盖使用 Node.js、Bun、pnpm、Yarn 或 Deno
的 JavaScript/TypeScript，Python 与 uv、TeX、Go、Rust、使用 Maven/Gradle 的
Java、Makefile 以及 GitHub Repository。在 Extensions 中安装对应 Runtime，或
检测到系统已经安装后，会自动安装或启用相应 Project Plugin。TeX 编译与应用启动
共用同一套 Action、Terminal、Diagnostic、Artifact 与 Preview 体系。

### 顶栏 Work Tabs 与固定资源

顶栏显示最近主动打开的 3 个 Conversation Session。打开、新建或切换 Session 会
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
但不停止对话。项目快捷键为 `Cmd/Ctrl+Shift+1`。

固定 File 会作为全局用户资源索引进入后续 Agent Turn。固定 Browser 只有所属
Session 保留控制权；其他 Session 只能获取 Snapshot/Screenshot，不能导航、
点击、输入、刷新、上传或进行其他页面修改。取消固定只删除顶栏引用，不删除源
文件、文字、知识库条目或页面。

Workbench 是唯一 Web UI。实时 Markdown 对话、运行 Guidance、Subagent 与
Browser 状态、Session History、Memory、Knowledge/Search、Schedule、Map、
Model/Tool/MCP/API Key 设置和主题能力都通过 Workbench 的页面、面板或
Settings Overlay 提供。

### Agent 操作 Cyrene 界面

在本地 Electron Workbench 轮次中，Main Agent 可以通过 `cyrene_tools` Snapshot
和 Inspect 当前可见 Cyrene Surface，再执行该确切 Snapshot Revision 声明的
Click、Double Click、Type、Scroll 或 Drag。它不使用 App Use，也不依赖键盘焦点。New Chat、
Search、项目切换、当前视口 Chat List、右键菜单、Settings Tab 和 Browser 浮窗
标题栏使用稳定语义节点；其他当前视口标准控件由受限 DOM Projection 补充。
流式消息和内容更新不会使这些稳定动作过期；新出现的审批、问题、Layer 或 Action
集合仍会使 Revision 变化。若无关的全局 Revision 变化，Renderer 会通过有界的
节点级动作租约继续执行未变化的目标；调用方仍须原样传入 Snapshot Revision，不能
自行替换成最新数字。

Double Click 使用独立能力，只有 Inspect 到的 Action 明确声明 `double_press` 或
`double_click` 时才会执行。Browser PiP 小窗标题栏会声明
`maximize + double_press`，因此 Agent 可在不聚焦 Cyrene、不提供坐标的情况下双击
标题栏完成最大化；普通单击按钮会被 Double Click 能力拒绝。

Agent 可以填写当前可见 Composer。发送或发送 Guidance 是 R2，必须由同一真实
本地用户轮次精确要求，或经过普通本机确认；停止当前运行是 R1。Agent 不能调用
隐藏后台 Dispatcher。向其他对话发送时，必须在可见 UI 中切换过去、填写
目标 Composer，再调用其显式 Submit。

Typed Settings 覆盖全部非模型 Settings Tab。直接修改携带 Revision；若用户同时
修改，Agent 会得到冲突而不是覆盖用户新值。Secret、OAuth、扫码、文件选择、
系统权限和 Models Tab 仍由用户亲自完成。

Verbose Context Trace 不属于 WebUI；通过 `cyrene flow`、
`/api/context-debug/events` 或
`python -m cyrene.observability.context_debug` 检查。

## CLI

`cyrene` 是连接 `http://localhost:4242` 的轻量 HTTP Client。Loopback
Readiness/API 请求会忽略环境代理。

```bash
cyrene start
cyrene status
cyrene chat
cyrene do "你的任务" --session run_live
```

| 命令 | 说明 |
|---|---|
| `cyrene start` | 后台启动 Workbench Daemon |
| `cyrene stop` | 停止检测到的 Daemon |
| `cyrene chat [text]` | 打开交互式流式对话，或发送一条消息后退出 |
| `cyrene chat --list` | 列出持久 Workbench 对话 |
| `cyrene chat --chat <id>` | 继续已有 Workbench 对话 |
| `cyrene chat --chat <id> --resume --cursor <n>` | 从事件序号恢复当前/最近运行 |
| `cyrene chat --json <text>` | 将公开运行事件输出为 NDJSON |
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
若目标 Daemon 启用了本地 Token 认证，CLI 会自动读取
`CYRENE_AUTH_TOKEN` 并发送 `X-Cyrene-Token`。

### 交互式流 CLI

`cyrene chat` 是推荐的终端交互入口：若 Daemon 尚未运行，它会自动后台启动，
随后进入交互界面。它连接后台 Daemon，并复用与 Workbench
相同的持久 Conversation 和 Run：

```bash
cyrene chat --chat CHAT_ID
cyrene chat --mode plan
```

会话内可使用 `/new`、`/resume`、`/mode`、`/attach`、`/attachments`、
`/detach`、`/deep-reflect`、`/deep-research`、`/context`、`/config`、
`/status`、`/mcp` 和 `/exit`。`/new` 会选择 Project；`/resume` 列出带
Project 名称的 Session；每个 Session 使用“标题与 Project / 内容摘要”
两行卡片显示，卡片之间留空行。选择菜单支持 ↑/↓ 和 Enter。Alt+Enter 插入换行；
第一次 Ctrl+C 提示确认，两秒内再次按下才退出 CLI，且不会中断后台 Run。
若进入后直接输入内容，CLI 会在默认 Project 自动创建新对话。

发送消息后，CLI 会使用随机变换且不连续重复的星形 Spinner
（`✶ ✸ ✹ ✺ ✷ ◌`）实时显示当前活动与累计用时；思考阶段复用 App
现有的自然话术池，并约每四秒随机切换且不连续重复。
完成时显示总用时。模型提供的
思考流默认折叠为“思考了 Ns”。按 Ctrl+O 会打开临时全屏详情，使用
Ctrl+O、Esc、Q 或 Ctrl+C 返回后详情会真正从界面消失，不写入终端滚屏。

`/context` 与 App 的“对话上下文”卡片读取同一份组成数据，显示消息 token
总数、彩色比例条，以及“系统前缀 / 临时注入 / 对话消息”分组。用户、助手、
工具和各系统注入块均缩进显示，并使用与 App 对应的语义颜色。

这些分组直接投影持久 ContextTree，不是另一套 Prompt 估算。`SessionStart` 在每个
对话中只冻结一次 System Prompt、SOUL、Memory 与已学习技能；`TurnStart` 每轮追加
输入框选择的 Workspace、MCP、Attachment 与 Runtime Context。稳定前缀逐字节复用，
变化只发生在其后的动态后缀。详见[架构说明](architecture.zh-CN.md#插件如何组成一个-agent)。

`/config` 使用本地化的两轴设置导航：←/→ 在“常规、模型、工具、连接、数据、
关于”Tab 之间切换，↑/↓ 选择当前 Tab 的详细设置项，Enter 打开。常规设置和
CLI 偏好的具体字段也使用方向键选择；界面与操作提示随 `language` 切换中英文。

CLI 显示文本回复和公开的 Tool/Phase/Plan 状态。Browser 实时画面与直接操作、
富媒体 Viewer、Workbench 图形布局和 Raw PTY 不属于该终端界面。

Electron Desktop 启动 Backend 后会发布仅当前系统用户可读（Unix 权限
`0600`）的本地连接凭据。CLI 会自动连接这一个 Backend，因此 Electron 与
CLI 可以同时运行，并共享相同的 Project、Conversation、Memory 和
运行状态；不会启动第二份会争用数据库与 Scheduler 的 Backend。

## 进程内 Local CLI（Legacy）

```bash
python -m cyrene.platform.host
```

| 命令 | 功能 |
|---|---|
| `/h` | Help、清 Context、Reset Personality、Status |
| `/mcp` | MCP 管理 |
| `/mcp list` | 列出 MCP |
| `/clear` | 清 Session Context |
| `/deep-reflect [focus]` | Deep Reflection |
| `quit` | 退出 |

进程内 Legacy CLI 继续支持 `/clear`；新的 `cyrene` 交互界面使用 `/new`
创建独立对话，不提供 `/clear`。

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

MCP Capability 通过启用的 MCP 插件动态发现，无需重启。输入框的 Context 菜单
决定当前对话挂载哪些 Server，Plugin Center 决定插件是否可用。Agent 通过
`toolbox.list → describe → invoke` 发现已启用的工具包和独立工具；设为直接可见的
工具也会出现在即时 Tool 列表中。关闭插件会移除其工具和上下文贡献，Runtime 仍会
拒绝历史消息中的过期调用。

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
Takeover。复杂页面应先调用 `browser_snapshot`，优先使用短期 Ref 而不是猜测
CSS Selector；页面导航、滚动或明显重排后需要重新 Snapshot。Iframe、Shadow
DOM、Canvas/WebGL 或复杂 Editor 无法通过当前 Top-level DOM Projection 稳定
操作时，改用 Screenshot 或 User Takeover。

文件上传始终使用 `browser_upload_files` 的一次性人工批准。普通 Click、Type 和
Enter Submit 不会自动获得同等级确认，因此购买、发布、删除和账号修改等高影响
动作必须与用户请求一致。`browser_network_log` 只是 Resource Performance
摘要，Browser URL 检查也不是覆盖全部子资源与 Redirect 的网络 Sandbox。详见
[Browser Live View](browser-live-view.zh-CN.md)。

## 共享持久化终端

Cyrene Terminal Daemon 独立于桌面窗口托管交互式 PTY。用户与 Agent
可以重连同一个终端、运行 TUI，并在 Electron 重启后保留滚动记录与元数据。
Agent 创建的终端绑定到对应对话，通过 `code.shell.*` 使用。

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

Telegram 与 Web UI 使用相同的插件原生 Agent Runtime、上下文插件、Subagent
和工具发现流程。

## WeChat

打开 **Settings → Channels → WeChat**，获取 QR Code、使用微信扫码确认并
启动 Channel。iLink Bot Token 会保存到加密配置，无需重启。
`WECHAT_BOT_TOKEN` 和 `WECHAT_OWNER_ID` 仍作为历史配置输入保留，但当前 UI
流程不要求用户手工获取或填写。

WeChat 仍是 Alpha，并依赖 WeChat iLink Bot Service 的可用性与行为。
