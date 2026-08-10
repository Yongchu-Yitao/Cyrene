> **IMPLEMENTED / 已完成 — 2026-07-28：**
> 动态交互式 CLI 已按本文范围实现。`cyrene chat` 现在连接正式 Daemon 与
> Workbench Conversation，支持流式回复、公开 Agent 活动、Pending Question、
> Attachments、Interrupt、非 TTY/NDJSON 和按 Cursor 恢复 Run。
>
> **实现边界：** CLI 使用 Per-run NDJSON，不订阅单队列 `/api/events`。
> Browser 可视化交互、
> 富媒体 Viewer、Workbench 图形布局复刻和 Claude Code PTY 透传已明确排除，
> 未在本实现中加入。

# Cyrene 动态交互式 CLI Handoff

[项目记录索引](README.zh-CN.md) ·
[架构重构 Handoff](COMPLETED-refactor-handoff.zh-CN.md)

更新时间：2026-07-28

分支：`feature/project-literature-library`

实现入口：`src/cyrene/cli_chat.py`、`cyrene chat`

交互原型：`project-notes/prototypes/interactive_cli_demo.py`

```bash
uv run python project-notes/prototypes/interactive_cli_demo.py
```

该脚本只保留为离线界面演示；正式功能由 `cyrene chat` 提供。使用
`/demo`、`/plan`、`/permission` 和 `/error`
分别查看主要交互状态；也可在启动菜单输入 `1`–`4`，或直接输入任意任务。

## 1. 当前状态

Cyrene 现在已有正式的 Daemon 型交互客户端，连续输入、流式回复、运行状态、
工具进度、权限确认和可靠中断均由 `cyrene chat` 提供。

| 入口 | 当前用途 | 当前交互能力 |
|---|---|---|
| `python -m cyrene` | 启动 Workbench Web UI | 只负责启动，不进入终端对话 |
| `cyrene start/status/stop/do/...` | 后台 Daemon 的同步 HTTP Client | 一次一条命令，回复完成后整体输出 |
| `python -m cyrene.runtime.host` | 进程内 Headless REPL | 连续对话，但使用阻塞 `input()`，完成后整体输出 |
| `cyrene chat` | Daemon 的交互式流客户端 | 持久对话、公开运行事件、确认、中断与恢复 |

正式安装入口由 `pyproject.toml` 定义：

```toml
[project.scripts]
cyrene = "cyrene.cli:main"
```

`cyrene start` 在后台启动 `python -m cyrene`，等待 `4242` 上的 Workbench
服务健康；`cyrene do` 再通过 `/api/chat` 发送同步请求。Headless REPL 则在
自身进程初始化完整 Runtime，直接调用 `run_agent()`。

后端已经具备本工作的主要基础：

- `/api/chat` 接受 `stream: true`，返回 `application/x-ndjson`；
- 回复流已有 `reply_start`、`reply_delta`、`reply_done`、`awaiting_user`
  和 `error`；
- `/api/chat/answer-question` 可以流式继续 Pending Question；
- `/api/chat/interrupt` 可以中断当前 Agent Run；
- Agent 已发布 `tool_call_started`、`tool_call_progress`、
  `tool_call_finished`、`phase_transition`、`plan` 和 `plan_progress`；
- `/api/events` 已提供 SSE Runtime Event；
- Workbench Chat 另有可重连的 Chat Run Manager 与 NDJSON Run Stream。

因此本项目不需要新建 Agent Loop，也不需要为了 CLI 另做 WebSocket 协议。
主要工作是把现有能力整理成稳定的单次 Run Event Stream，并在终端侧正确渲染。

## 2. 当前问题

### 2.1 Headless REPL 阻塞事件循环

`cyrene.runtime.host._cli_loop()` 是 async 函数，但内部直接调用同步
`input()`：

```python
user_input = input("\nYou: ").strip()
```

用户停留在输入框时，同一线程上的 asyncio 任务无法正常调度。该模式还会在
`await run_agent(...)` 完成后才执行一次 `print()`，所以工具调用、阶段变化、
回复 Token 和等待确认都不可见。

### 2.2 `cyrene do` 没有使用已有流

当前 `cmd_do()` 发送普通 JSON 请求：

```python
payload = {"message": text, "session_id": session_id}
resp = _api_json("/api/chat", method="POST", json=payload)
```

服务端只有在 Payload 包含 `stream: true` 时才返回 NDJSON。现有实现因此等待
整个 Agent Run 完成，终端长时间无反馈。

### 2.3 Legacy Session 参数语义不完整

`cyrene do --session <id>` 会发送 `session_id`，但当前 `/api/chat` Legacy
Handler 没有读取这个字段；命令完成后又固定从 Session 列表中查找
`run_live`。这意味着 CLI 表面允许任意 Session，实际仍落到 Legacy Live
Session。

第一阶段必须明确：

- `cyrene chat` 默认且仅使用 `run_live`；
- `cyrene do --session` 在修正服务端路由前，不应继续暗示任意 Session 已被
  正确支持；
- 如果要支持独立 Workbench Conversation，应显式走
  `/api/workbench/chats/{chat_id}/messages`，不能只把 `session_id` 塞进
  `/api/chat`。

### 2.4 当前 SSE 不是多订阅者广播

`cyrene.observability.debug` 当前只有一个全局 `_event_queue`。
`subscribe()` 从该队列执行 `q.get()`，所以多个订阅者会竞争消费，而不是每个
订阅者收到同一事件。

直接让 Web UI 和 CLI 同时连接 `/api/events` 会产生以下风险：

- 一部分工具事件只被 CLI 收到；
- 另一部分只被 Web UI 收到；
- 断开连接后队列生命周期难以与订阅者绑定；
- Session Filter 在事件出队之后发生，错误 Session 的消费者仍会消耗事件。

在事件总线改成真正 Fan-out 之前，CLI 不得把 `/api/events` 作为关键运行状态
的唯一来源。

### 2.5 两套交互逻辑容易继续漂移

`runtime.host` 自己处理 `/h`、`/clear`、`/mcp` 和 Setup；`cyrene.cli` 则是
另一套 `argparse + HTTP` 命令。继续在 Headless REPL 中增加 Rich/TUI 会让：

- 会话、权限和 Pending Question 逻辑与 Web Route 分叉；
- CLI 每次启动都重复初始化完整 Runtime 和外部服务；
- Daemon 与 REPL 可能同时访问相同本地状态；
- Electron、Web、CLI 无法共享相同的 Run 生命周期。

正式动态 CLI 应是 Daemon 的薄客户端。

## 3. 产品目标与边界

### 3.1 第一阶段目标

新增正式入口：

```bash
cyrene chat
cyrene chat --mode default
cyrene chat --mode plan
cyrene chat --no-color
```

用户应得到：

- 连续对话；
- 模型回复实时增量输出；
- Agent 执行期间有持续状态，不出现无反馈等待；
- Pending Question 和权限选项可在终端直接回答；
- `Ctrl+C` 仅用于退出确认：第一次提示，两秒内第二次退出；不取消后台 Run；
- 输入历史、多行编辑和斜杠命令补全；
- 非 TTY、重定向和 `--json` 场景保持机器可读；
- Daemon 未启动时给出明确提示，或按确定策略自动启动并等待健康。

### 3.2 明确不实现

以下内容不是“第一阶段暂缓”，而是本 CLI 项目的正式范围外：

- 不做全屏 Dashboard；
- 不显示 Browser 实时画面，不在终端实现网页点击、拖拽或 Electron PiP 的
  等价交互；
- 不实现 PDF、图片、地图或 Diff Viewer，也不为这些内容增加终端预览或
  系统 Viewer 调度层；
- 不复制 Workbench 多栏布局、任务卡片、顶栏 Tabs、文件 Viewer 或其他图形
  导航结构；
- 不透传 Claude Code 实时终端，不实现 PTY Attach、ANSI Terminal Emulator
  或 Raw Terminal Mode；
- 不使用 Terminal Raw Mode 自己重写 Line Editor；
- 不新增 WebSocket；
- 不在回复流中暴露隐藏 Reasoning、Credential、未脱敏 Tool 参数或绝对路径；
- 不删除 `python -m cyrene.runtime.host`，它继续作为兼容/诊断入口；
- 不改变 `python -m cyrene` 默认启动 Workbench 的行为。

这些排除项不限制 Agent/Harness 调用已有 Tool：

- Agent 仍可使用 Browser Tool，但 CLI 只显示公开的文本状态和最终结果；
- Agent 仍可读取、分析或生成 PDF、图片、地图数据和 Diff，但 CLI 不负责
  渲染；
- Project、Task、Conversation 仍可通过命令和列表操作，但不复刻 Workbench
  视觉布局；
- Agent 仍可调用现有 Claude Code 相关 Tool，但 CLI 不提供实时 Terminal
  Attach。

### 3.3 正式终端形态

CLI 固定采用行式动态 UI，而不是占满屏幕的 TUI：

```text
Cyrene · run_live · default

You › 检查当前项目的启动方式

⠋ 正在执行
  ✓ discover              tooling
  ✓ read_file             pyproject.toml
  → 阶段：分析 → 执行

Cyrene › 当前项目有三类启动入口……
```

行式 UI 更适合复制输出、Shell Scrollback、CI 日志、SSH 和无障碍工具。
多 Pane TUI、图形预览和嵌入式交互面板不在本 Handoff 的后续路线中。

## 4. 正式架构决策

推荐调用链：

```text
Terminal
   │
   ▼
cyrene.cli / cyrene.cli_chat
   │  HTTP + per-run NDJSON
   ▼
route.agent.chat
   │
   ▼
agent / tooling / runtime
```

边界规则：

1. CLI 只负责输入、命令解析、Transport 和 Renderer。
2. CLI 不直接导入并运行 `run_agent()`。
3. Route 负责 Payload 验证、Run Context 和流生命周期。
4. Agent/Tooling 发布稳定、脱敏的用户可见事件。
5. `--json` 输出原始稳定事件，不混入 ANSI、Spinner 或日志。
6. Rich Renderer 的异常不能中断 Agent Run 或破坏响应读取。
7. 终端断开时应关闭 HTTP Stream；是否中断 Run 必须由明确策略决定，不能因
   Renderer 崩溃自动执行破坏性操作。

## 5. Stream 契约

### 5.1 第一阶段最小契约

请求：

```json
{
  "message": "用户输入",
  "stream": true,
  "mode": "default",
  "lang": "zh",
  "client_request_id": "cli_<uuid>"
}
```

服务端已有的最小事件：

```json
{"type":"reply_start"}
{"type":"reply_delta","delta":"增量文本"}
{"type":"reply_done","response":"最终完整文本"}
{"type":"awaiting_user","pending_question":{}}
{"type":"error","error":"model_call_failed","message":"..."}
```

CLI 必须逐行读取 NDJSON，容忍一个网络 Chunk 中有多行，也容忍一行被拆成多个
Chunk。不得用 `response.text` 等待完整 Body。

### 5.2 推荐的统一 Per-run Event Stream

为了显示工具和阶段状态，应扩展现有 Run Context Writer，使本轮公开 Runtime
Event 同时进入当前 NDJSON Stream，而不是让 CLI 连接全局 SSE。

建议在 Agent Run Context 中增加聚焦的公开事件 Writer，或将当前
`reply_stream_writer` 泛化为 `run_event_writer`。事件至少包括：

| Event | 必要字段 | 终端行为 |
|---|---|---|
| `run_start` | `run_id`、`session_id` | 初始化 Renderer |
| `phase_transition` | `from`、`to`、公开 `detail` | 更新状态行 |
| `tool_call_started` | `tool_call_id`、`tool`、脱敏摘要 | 新增运行中工具 |
| `tool_call_progress` | `tool_call_id`、`current`、`total` | 更新进度 |
| `tool_call_finished` | `tool_call_id`、`status` | 关闭工具状态 |
| `plan` | `status`、公开步骤 | 展示计划 |
| `plan_progress` | `step`、`status` | 更新步骤 |
| `reply_start/delta/done` | 回复字段 | 流式输出 |
| `awaiting_user` | Question ID、文本、选项 | 进入回答模式 |
| `run_interrupted` | `run_id` | 清理动态状态 |
| `error` | 稳定 Code、公开 Message | 显示错误并恢复 Prompt |

该 Stream 只允许当前 Run 的公开事件。现有 `redact_value()`、Tool Result
截断、Reasoning Filter 和 Permission Boundary 必须保留。

### 5.3 SSE 的后续修正

`/api/events` 仍供全局 Workbench 状态使用，但应改成真正 Fan-out：

```text
publish_event
  ├─ subscriber queue A (Web UI)
  ├─ subscriber queue B (other UI)
  └─ bounded recent-event ring
```

要求：

- 每个订阅者独立有界队列；
- 注册和 `finally` 注销；
- 先按 Session/权限过滤，再写订阅者队列；
- 慢消费者只丢自己的事件；
- 一个订阅者断开不影响其他订阅者；
- Heartbeat 是订阅级行为，不进入全局 Recent Events。

该改造有独立价值，但不是第一版流式回复的前置条件。

## 6. 终端交互设计

### 6.1 依赖

推荐使用：

- `prompt_toolkit`：异步 Prompt、历史、多行编辑、补全、输入期间安全输出；
- `rich`：Markdown、Spinner、Live Status、颜色和结构化错误；
- `httpx.AsyncClient`：复用现有依赖并消费异步 NDJSON。

因为 `cyrene chat` 是正式内置命令，建议将 `prompt-toolkit` 和 `rich` 加入核心
依赖并纳入冻结构建验证，不建议让默认安装得到一个静默降级的残缺交互模式。

非 TTY 和 `--json` 不启动 Live Renderer，按行输出稳定文本或 NDJSON。

### 6.2 键盘行为

| 输入 | 空闲状态 | Run 进行中 |
|---|---|---|
| `Enter` | 提交 | 不接受新的普通 Turn |
| `Alt+Enter` / `Esc+Enter` | 插入换行 | 不适用 |
| `Ctrl+C` | 第一次提示确认，两秒内再次退出 | 不清空输入、不取消后台 Run |
| `Ctrl+O` | 打开临时全屏思考详情；再次按下关闭 | 关闭后恢复 Prompt，不把详情写入滚屏 |
| `Ctrl+D` | 退出 CLI | 不取消后台 Run |
| `↑` / `↓` | 浏览历史 | 不改变 Run |
| `Tab` | 补全斜杠命令 | 不改变 Run |

CLI 退出时关闭当前 HTTP Subscriber；Workbench Run 继续由 Daemon 持有并完成
持久化。重新进入后可通过 `/resume` 回到对应 Session。

### 6.3 斜杠命令

```text
/help
/new
/resume [SESSION_ID]
/status
/mode default|plan|auto
/deep-reflect
/deep-research [topic]
/context
/config
/mcp
/exit
```

`/new` 选择 Project 后创建无须手工标题的新对话；`/resume` 合并列表与切换
行为，并用两行卡片显示标题、Project 与摘要，Session 之间留空行。`/config` 覆盖 Backend Settings、
Models、Capabilities、Keys、SOUL、Integrations、MCP、Skills、Remote、
Profile、Budget、Data 与 CLI Preferences。正式交互 CLI 不提供 `/clear`。
设置导航按语言显示六个 Tab，←/→ 切换 Tab，↑/↓ 选择详细项，Enter 打开；
常规与 CLI 字段列表也使用方向键选择。
`/context` 同时读取 Workbench 的 `context` 与 `context-blocks`，按 App 的
系统前缀、临时注入、对话消息三层结构显示 token、彩色比例条与缩进明细。

## 7. Pending Question 与权限确认

收到：

```json
{
  "type": "awaiting_user",
  "pending_question": {
    "id": "question_xxx",
    "text": "是否允许写入？",
    "options": ["允许一次", "拒绝"]
  }
}
```

CLI 应：

1. 停止 Spinner，但保留当前 Run 上下文；
2. 有选项时显示可选择列表，无选项时显示文本输入；
3. POST `/api/chat/answer-question`；
4. Payload 包含 `question_id`、`answer` 或 `selected_option`、
   `client_request_id` 和 `stream: true`；
5. 用同一 Renderer 消费新的 NDJSON Stream；
6. 用户拒绝时正常显示 Agent 后续结果，不把拒绝当作客户端错误。

终端不得自动批准权限，不得因为 `--yes`、非 TTY 或输入不可用而默认选择第一
项。非交互环境遇到 `awaiting_user` 时应以明确非零退出码结束，并输出
Question 的机器可读 Payload。

## 8. 源码落点

建议新增：

```text
src/cyrene/
├── cli.py                    argparse 入口与现有一次性命令
└── cli_chat.py               交互 Session、Transport、Event Parser、Renderer

tests/
├── test_cli_chat.py          输入、事件、错误、Pending Question、中断
└── test_cli.py               parser 与现有子命令回归
```

职责建议：

```python
class ChatTransport:
    async def send(...)
    async def answer(...)
    async def interrupt(...)
    async def clear(...)

class NdjsonDecoder:
    def feed(bytes) -> list[dict]
    def finish() -> list[dict]

class ChatRenderer:
    def handle(event)
    def close()

class InteractiveChat:
    async def run()
```

不要把所有实现继续堆进当前约 700 行的 `cli.py`。`cli.py` 只注册
`chat` Subparser 并调用聚焦模块。

后端可能涉及：

- `src/cyrene/agent/context.py`：绑定公开 Run Event Writer；
- `src/cyrene/agent/state.py`：将公开 Runtime Event 转发到本轮 Writer；
- `src/cyrene/workbench/runtime.py`：扩展 `_stream_agent_reply()`；
- `src/route/agent/chat.py`：稳定 Payload、Answer 和 Interrupt 契约；
- `src/cyrene/observability/debug.py`：后续 SSE Fan-out；
- `pyproject.toml` / `uv.lock`：终端依赖；
- PyInstaller/Electron Build 配置：确保新模块和依赖进入冻结包。

## 9. 分阶段实施

### Phase 1：可用的流式聊天（已完成）

- 注册 `cyrene chat`；
- 检查 Daemon 健康；
- 使用 `AsyncClient.stream()` 请求 `/api/chat`；
- 解析 `reply_*`、`awaiting_user`、`error`；
- 支持 `/help`、`/clear`、`/exit`；
- 支持双击 `Ctrl+C` 退出确认，且不取消后台 Run；
- 非 TTY 与 `--json` 行为稳定；
- 默认使用持久 Workbench Conversation；`--legacy` 可显式使用 `run_live`。

完成标准：长回复可以边生成边显示，断网、模型错误和用户中断后都能恢复到
Prompt。

### Phase 2：动态 Agent 活动（已完成）

- 将公开 Tool/Phase/Plan 事件合并到 Per-run NDJSON；
- Rich 行式区域显示活动工具与进度；
- 发送后使用随机变换且不连续重复的单字符星形 Spinner 实时刷新活动计时，
  结束时显示总用时；
- 思考阶段复用 Workbench 已有中英文话术池，约每四秒随机切换且避免连续重复；
- 消费 `reasoning_start/delta/done`，默认折叠并通过可擦除的 Ctrl+O
  临时全屏界面查看；
- 回复正文与运行状态分区；
- 保证所有参数脱敏与 Result 截断；
- 增加 Event 顺序、重复和未知事件兼容测试。

完成标准：CLI 不订阅全局 SSE 也能完整显示本轮公开活动。

### Phase 3：交互质量（已完成）

- Prompt Toolkit 异步输入；
- 历史记录持久化；
- 多行编辑与命令补全；
- Markdown/Code Block 保持原文输出；
- 终端宽度变化、窄屏、无颜色和 Windows Terminal 验证；
- 一次性流调用由 `cyrene chat "..."` 提供，不改变兼容命令 `cyrene do`。

### Phase 4：Workbench Conversation（已完成）

- 允许选择或创建独立 Workbench Chat；
- 使用 Workbench Chat Run Manager 的 Detached/Resume 能力；
- 显示 Chat 标题、Project 和稳定 Run ID；
- CLI 重启后可按 Cursor 恢复仍在运行的 Run；
- 修正或正式弃用 Legacy `--session` 假语义。

该阶段不能简单修改 `/api/chat` 的全局状态，必须复用正式 Workbench Chat
Application Service 和持久 Run Event。该阶段只提供命令、列表和状态文本，
不实现 Workbench 多栏布局、任务卡片或顶栏 Tabs。

## 10. 测试与验证

### 10.1 单元测试

- NDJSON 行跨 Chunk、单 Chunk 多行、末尾无换行；
- 空行、未知 Event、无效 JSON 和过大 Event；
- Unicode、中文、Emoji、组合字符；
- `reply_done` 与累积 `reply_delta` 不一致时以最终快照为准；
- Pending Question 有选项/无选项/连续提问；
- Model Error、HTTP 4xx/5xx、连接拒绝、读超时；
- `Ctrl+C` 空闲、运行中、Interrupt 超时；
- Renderer 异常不丢失 Transport Cleanup；
- `--json` 中 stdout 只含 JSON，诊断进入 stderr；
- 非 TTY 不输出 ANSI。

### 10.2 API/Runtime 测试

- `stream: true` 的 Media Type 和 Event 顺序；
- Tool Started/Finished 成对；
- Tool Progress 绑定正确 `tool_call_id`；
- 每条 Event 绑定正确 Run/Session；
- 一个 Run 的事件不会进入另一个 Run Stream；
- Reasoning、Credential、绝对路径和敏感参数不出现在公开 Stream；
- Answer Question 继续原 Round；
- Interrupt 产生明确终态；
- 客户端断开后的 Run 策略符合契约；
- SSE Fan-out 改造后两个订阅者都收到相同事件，慢消费者互不影响。

### 10.3 手工验收

至少在 macOS Terminal、Windows Terminal 和一个非 TTY 管道场景验证：

1. Daemon 未启动时的提示；
2. 普通聊天与长 Markdown 回复；
3. 调用多个工具并显示进度；
4. 计划生成与步骤更新；
5. 文件写入权限允许一次和拒绝；
6. 自由文本 Clarification；
7. 运行中 `Ctrl+C`；
8. 断网和模型超时；
9. 中英文及宽字符对齐；
10. `cyrene chat --json | jq`；
11. Web UI 与 CLI 同时打开时事件不丢失；
12. CLI 退出后 Daemon 继续正常工作。

以下内容不进入手工验收：Browser 画面/点击/拖拽、PDF/图片/地图/Diff 预览、
Workbench 图形布局和 Claude Code PTY Attach。

建议验证命令：

```bash
uv run pytest -q tests/test_cli.py tests/test_cli_chat.py
uv run pytest -q tests/test_context_trace.py tests/test_workbench_chat_runs.py
uv run python -m compileall -q src
uv run cyrene --help
uv run cyrene chat
```

如果修改前端共享 Event Bus，还需构建 Web UI 并运行对应 Workbench/Event
回归。若新增核心依赖，还需执行冻结构建 Smoke Test。

## 11. 兼容与迁移

- 保持现有 `cyrene start/status/stop/do/session/flow/memory/mcp` 命令；
- `cyrene chat` 是新增子命令，不改变脚本现有行为；
- `python -m cyrene` 继续默认启动 Workbench；
- `python -m cyrene.runtime.host` 暂时保留，并在帮助文档中标为
  Headless/Diagnostic REPL；
- 不复活已删除的 `--agent` UI Selector；
- 不在 `src/cyrene/` 新增顶层 Package，避免违反架构边界测试；
- 新模块必须纳入动态导入/冻结包检查；
- 更新 `README.md`、`README.zh-CN.md`、`docs/usage*.md` 和
  `docs/installation*.md` 时，必须清楚区分 Web 启动与交互 CLI。

## 12. 风险与开放决策

### 12.1 必须在实现前确定

1. CLI 断开时，Run 默认继续还是自动中断。推荐：正常退出前主动询问；网络
   断开不自动中断，以免短暂故障导致任务丢失。
2. 第一版是否自动启动 Daemon。推荐：交互 TTY 可询问或自动启动并显示日志；
   非 TTY 只报错，不隐式创建后台进程。
3. 历史文件位置与保留策略。不得把敏感回复或 Credential 写入明文 History。
4. `auto` Permission Mode 是否允许在 CLI 暴露。应与 Web 的正式权限定义一致，
   不能因为终端环境默认扩大权限。

### 12.2 已确定

- 第一阶段入口是 `cyrene chat`；
- 第一阶段复用 Daemon HTTP/NDJSON；
- 固定使用行式交互，不做全屏 TUI；
- 不做 Browser 可视化交互或 Electron PiP 等价层；
- 不做 PDF、图片、地图和 Diff Viewer；
- 不复刻 Workbench 多栏布局、任务卡片和顶栏 Tabs；
- 不做 Claude Code PTY/Raw Terminal 透传；
- 不新建 WebSocket；
- 不直接用当前单队列 SSE 驱动关键状态；
- 不让 CLI 自己运行第二套 Agent Runtime；
- Pending Question 和权限确认必须显式处理；
- JSON/非 TTY 是正式兼容面，不是事后降级。

## 13. 接手清单

接手实现前：

- 阅读 `src/cyrene/cli.py`、`src/cyrene/runtime/host.py`；
- 阅读 `src/route/agent/chat.py` 的 Chat/Answer/Interrupt Route；
- 阅读 `src/cyrene/workbench/runtime.py::_stream_agent_reply()`；
- 阅读 `src/cyrene/agent/context.py` 和 `src/cyrene/agent/state.py` 的 Stream
  Writer；
- 阅读 `src/cyrene/observability/debug.py` 的单队列 Event Bus；
- 确认当前工作区存在其他未提交改动，避免覆盖；
- 先写 Transport/Decoder 测试，再接 Renderer；
- 每个 Phase 独立验证，不把 Event Bus、TUI 和 Session Migration 一次提交。

交付前：

- 更新本文状态与真实测试数字；
- 如果只完成部分 Phase，逐项标注“已实现/待实现”；
- 更新项目记录索引和用户/开发者文档；
- 记录最终分支、Commit 和冻结构建结果；
- 不以“终端看起来会动”替代 Pending Question、Interrupt、脱敏和非 TTY 验收。
