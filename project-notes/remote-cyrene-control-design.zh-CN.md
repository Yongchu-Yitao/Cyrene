> **COMPLETED / 结构化远程 Cyrene 控制已完成 — 2026-07-27：**
> 版本化 Control API、设备身份与配对、方向性 Grant、Project Scope、
> Signed + E2EE Envelope、局域网直连传输、Agent 远程工具、
> Durable Run Event、直连防滥用、Grant 同步、IP + 短密钥配对、
> 独立远程控制数据库、无限总大小的分块文件传输、实时传输进度、设置管理和
> “添加上下文”入口均已实现。远程桌面属于独立的后续可选能力。
>
> **当前安全边界：** 产品范围只包含同一局域网内的 Cyrene-to-Cyrene
> 连接，不需要 Relay、NAT 穿透或公网入口。`/v1/control/*` 仍只服务桌面
> 本机调用；LAN Listener 只接受配对和加密 Envelope，所有命令仍须通过
> RemoteGateway、设备签名、E2EE 和 Grant。

# Cyrene-to-Cyrene 远程控制 Handoff

[项目记录索引](README.zh-CN.md) ·
[架构重构 Handoff](COMPLETED-refactor-handoff.zh-CN.md)

更新时间：2026-07-27

分支：以当前工作树为准

审计基线：以本文件第 13 节的契约 Hash 和测试结果为准

## 1. 当前状态

本工作目标是从一台安装了 Cyrene 的电脑，安全连接并操作另一台电脑上的
Cyrene。主要能力是远程使用和监督 Agent；远程桌面只是后续可选的人工接管
通道。

目前已经完成以下内容：

- 新增严格、版本化的 `/v1/control/*` API；
- 契约暴露 Project 摘要，以及 Chat、Run、Task、Approval 和 Artifact 的
  固定操作；
- 发送 Chat 消息返回 `202 Accepted` 和稳定 `run_id`，Run 不依赖原 HTTP
  请求继续执行；
- 可以按 `run_id` 查询状态、增量读取持久事件、发送 guidance 和
  interrupt；
- 请求模型拒绝未知字段，响应不暴露 Workspace 路径或 Model Credential；
- 远程发送消息只允许 `default` 和 `plan` Permission Mode；
- 远程 Task 调度、Step 执行和 Approval 只允许 `default`，不能借用本机
  Control API 的 `auto/full_access` 模式；
- 事件出口过滤现有 `reasoning_*` 内部事件；
- OpenAPI 有独立 `Control` Tag、明确的 Operation ID 和响应模型；
- Ed25519 设备签名身份、X25519 密钥交换、Keychain 优先的私钥存储；
- 局域网 IP + 两分钟一次性短密钥配对、方向性 Capability/Project Grant、
  撤销和审计；
- Ed25519 签名、X25519/HKDF/ChaCha20-Poly1305 加密 Envelope；
- Nonce、Timestamp、Replay Protection、原子 Command Idempotency；
- 配对后保存双方 LAN 地址，通过真实 TCP/HTTP 直连投递 E2EE Envelope；
- LAN Listener 只接受配对和控制 Envelope，限制请求大小、来源、尝试频率，
  且拒绝未配对设备；
- Run 元数据与 Event 使用 SQLite 持久化，保留七天；重启中断会形成明确的
  `process_restarted` 终态和可继续读取的 Cursor；
- Remote Control 的 Pairing、Peer、Grant、Nonce、Idempotency 和 Audit
  已迁移到独立的 `<runtime-db>.remote-control` SQLite Sidecar，不再和
  高频 Agent Run Event 争用主运行库写锁；旧表会一次性兼容迁移；
- Remote Event 使用固定类型和字段 Allowlist，不输出 Reasoning、Workspace
  Change、Tool 参数、绝对路径或调试字段；
- Grant 通过 E2EE Envelope 周期同步，响应也携带被控端权威 Grant 快照；
- Cyrene 生命周期自动启动/停止 RemoteGateway；
- 设置中的“远程设备管理”Tab 和设备/Grant/配对/审计管理；
- 对话“添加上下文”可选择已配对设备，Agent 只可操作当前对话显式选中的
  设备；
- 四个 Agent Tool：`ListRemoteDevices`、`RemoteCyreneStatus`、
  `RemoteCyreneAction` 和一键创建远程对话并启动 Agent 的
  `RunRemoteCyrene`；
- `RunRemoteCyrene` 只把用户级任务交给被控端 Agent。被控端 Agent 可使用
  该设备上已经安装并授权的模型、工具、Skill、Browser、Computer Use、
  文件和集成；执行始终经过被控端自己的 Harness、Sandbox 和审批；
- Chat Attachment 与 Task Artifact 支持分块读取，不限制完整文件总大小；
  控制端自动组装到本地附件，工具卡显示实时百分比，不把 Base64 内容放入
  Agent 上下文；
- capability 响应如实报告 Remote Transport 和 Durable Event 已可用；
- Task 支持计划批准、逐步执行、Task 级审批，以及对真实 Goal Loop 的
  pause/resume/cancel。

本次“远程控制远端 Cyrene”的产品范围只包含局域网直连。公网 Relay、NAT
穿透、托管运维和 L3 远程桌面不属于当前范围。Chat Agent 进程在硬重启后
不会伪装成继续运行：
已提交事件和结果仍可读取，未完成 Run 明确终止，控制端使用同一
Idempotency Key 安全重试；持久 Goal Loop 则由既有恢复器继续调度。

## 2. 产品边界

### 2.1 三层能力

| 层级 | 能力 | 状态 |
|---|---|---|
| L1 远程使用 | Project、Chat、Task、Run 和 Artifact | 已实现 |
| L2 远程监督 | guidance、审批响应、计划执行、暂停/恢复/取消、持久事件恢复 | 已实现 |
| L3 远程接管 | 桌面视频、鼠标、键盘 | 后续可选 |

### 2.2 正式产品入口

远程能力的用户入口已经确定，不能改成隐式自动发现或全局授权：

1. 用户在“设置 → 连接”中启用远程访问；被控端生成本机局域网
   `IP:37841` 和两分钟一次性短密钥，控制端输入二者并完成设备配对；
2. 被控端明确选择授予的 Capability 和 Project Scope；
3. 用户进入某个对话，点击 Composer 的“添加上下文”；
4. “远程设备”区域只列出仍处于可信状态、且对本机授予了至少一项能力和
   至少一个 Project Scope 的已配对设备；
5. 用户选中设备后，Composer 显示“远程设备：设备名”上下文标签；
6. 只有该对话中的 Agent 可以看到并调用这个设备；移除上下文、撤销设备或
   清空 Grant 后立即失效；
7. 多个设备同时加入上下文时，Agent Tool 必须显式提供 `device_id`。

新对话尚未落盘时，选择保存在 Composer 本地状态；Chat 创建事件发生后立即
写入 `/api/workbench/chats/{chat_id}/remote-context`。已存在的 Chat 则在
选择变化时直接持久化。

“远程控制 Cyrene”优先意味着：

- 查看显式共享的 Project、Chat、Task 和运行状态；
- 在远端创建工作并向远端 Agent 下达用户级指令；
- 观察公开的回复、Plan、Artifact、进度和错误事件；Tool 参数、隐藏推理及
  Subagent 内部通信不进入远程事件；
- 对运行中的 Agent 补充 guidance；
- 回答澄清请求，并在远端 Policy 允许时提交审批决定；
- interrupt、pause、resume 或 cancel；
- 结果和文件默认保存在被控电脑；
- 控制端可显式读取对话引用的 Attachment 和 Task Artifact；传输使用分块
  协议、无完整文件大小上限，并显示进度；
- 所有工具都由被控端 Harness 验证、授权和执行。

远程桌面只用于 Agent 无法结构化呈现的本机 UI、登录、验证码、拖拽或人工
纠错。它必须单独请求、单独授权，不是 Control API 的基础传输方式。

### 2.3 明确不做

结构化远程控制不允许：

- 控制端直接执行远端 Shell 或任意 Tool；
- 远端把任意 HTTP Method、URL、数据库语句映射成本地调用；
- 直接协议命令安装 Skill、MCP 或 Integration（但远端 Agent 在其本机
  Harness 明确授权后，仍可像本地对话一样使用或管理这些能力）；
- 远程修改 Credential、SOUL、Memory 或全局 Permission；
- 远程 Backup/Restore、Reset Data、Update、Restart 或 Shutdown；
- 把控制端文本提升成 system/developer instruction；
- 用本机 `X-Cyrene-Token` 作为跨设备身份；
- 用远程审批绕过被控端 Tool Permission。

## 3. 已审计的现有架构

当前正式依赖方向是：

```text
Electron / Web UI / CLI / future Remote Client
                       │
                       ▼
                 route adapters
                       │
                       ▼
          workbench / agent / runtime services
                       │
                       ▼
        tooling policy / model runtime / persistence
```

与本工作直接相关的现状：

- `webui.server.create_app()` 创建 FastAPI App，`route.registry` 组合全部路由；
- `LocalAuthMiddleware` 约束 Host、Origin 和桌面 Token，现有 HTTP API 是
  Loopback Trust Boundary；
- `ChatRunManager` 已把 Chat Run 与 HTTP 请求生命周期解耦；
- Run Event 同时使用进程内 Ring Buffer 和 SQLite Event Log；持久记录默认
  保留七天；
- Chat guidance 使用持久 Inbox，并支持 `clientRequestId` 去重；
- Chat 已有 interrupt 和 `awaiting_user` 语义；
- Task 已有更丰富的计划、调度、暂停、恢复、取消、事件和 Artifact 能力，
  现已通过窄 Adapter 纳入 Control API；
- `src/route/workbench/chat.py` 仍承担较多编排逻辑，这是现存技术债，而不是
  新 Control Route 应复制的模式。

架构测试严格锁定 `src/cyrene/` 顶层目录。旧草案提出新增
`src/cyrene/remote/` 不符合当前边界，已经取消。后续代码应按职责进入：

- `cyrene.workbench`：Control Application Service、远程命令到业务操作的
  映射；
- `cyrene.runtime`：RemoteGateway 生命周期、出站连接和重连；
- `route`：本机 HTTP 契约适配器；
- `webui/frontend`：设备、Grant 和远程状态界面。

如果未来确实需要新顶层领域，必须先修改正式架构 Handoff 和
`test_architecture_boundaries.py`，不能绕过测试添加目录。

## 4. 当前正式源码

```text
src/
├── cyrene/
│   ├── runtime/
│   │   ├── remote_control.py       身份、配对、Grant、E2EE、Relay Client/Gateway
│   │   ├── remote_relay.py         只转发 E2EE Envelope 的 WebSocket Relay
│   │   └── remote_commands.py      固定命令到 Workbench 服务的映射与生命周期
│   ├── tool_impl/remote/
│   │   ├── common.py               当前 Chat 设备上下文授权边界
│   │   ├── list_devices.py         列出当前 Chat 选中的设备
│   │   ├── status.py               远端只读查询
│   │   ├── action.py               受权限确认保护的远端操作
│   │   └── run.py                  创建远程 Chat 并启动远端 Agent
│   └── workbench/
│       └── chat_runs.py            run_id 查询、SQLite Event、重启恢复与重放
└── route/
    ├── control_schemas.py          严格的 v1 请求/响应模型
    ├── control.py                  本机 Control API 适配器
    ├── remote_schemas.py           远程设置与 Chat Context DTO
    ├── remote.py                   配对、Grant、审计和 Chat Context API
    ├── registry.py                 路由组合
    └── workbench/
        ├── chat.py                 Chat 控制适配器与 detached 202
        ├── projects.py             Task create/list 控制适配器
        └── task_sessions.py        Task/Artifact 控制适配器

src/webui/frontend/
├── settings-overlay.jsx            “连接”设置页
├── workbench-chat.jsx              “添加上下文 → 远程设备”
├── workbench-i18n.jsx              中英文文案
└── workbench.css                   设置/更新日志高度及远程管理样式

tests/
├── test_control_api.py             Control 契约、安全边界和行为测试
└── test_remote_control.py          配对、权限、E2EE、Tool 和真实 WS Relay 测试
```

为降低行为回归风险，通过 Workbench Route 返回的窄适配器复用既有
Application Logic。Chat Adapter 只包含 list/create/get/send/guidance/
answer 和 Run Manager；Project/Task Adapter 也只返回 Control 契约需要的
固定操作，没有把所有 Workbench API 暴露给 RemoteGateway。

当前通过 Composition Root 注入窄 Adapter，RemoteGateway 不接受任意 Route
或 URL。后续可继续把 Chat 编排提取为 `cyrene.workbench` Application
Service 以降低 Route 体积，但这是内部重构项，不影响当前远程契约、权限边界
或功能闭环；RemoteGateway 仍不得自行解析并调用任意 Route Handler。

## 5. v1 Control API 契约

当前共 22 个 Path、24 个 Operation：

RemoteGateway 使用 23 个固定领域命令覆盖这 24 个 Operation；两个
Chat/Task Approval HTTP Operation 在远程协议中统一为带资源类型的
`approvals.respond`。Agent 的只读/写入远程工具枚举完整覆盖这 23 个命令，
但不会开放任意 HTTP、Tool 或 Shell。L3 远程桌面不属于本契约。

| Method | Path | Operation ID | 状态 |
|---|---|---|---|
| GET | `/v1/control/capabilities` | `control_v1_get_capabilities` | 已实现 |
| GET | `/v1/control/projects` | `control_v1_list_projects` | 已实现 |
| GET | `/v1/control/chats` | `control_v1_list_chats` | 已实现 |
| POST | `/v1/control/chats` | `control_v1_create_chat` | 已实现 |
| GET | `/v1/control/chats/{chat_id}` | `control_v1_get_chat` | 已实现 |
| POST | `/v1/control/chats/{chat_id}/messages` | `control_v1_send_chat_message` | 已实现 |
| GET | `/v1/control/chats/{chat_id}/attachments/{attachment_id}` | `control_v1_read_chat_attachment` | 已实现 |
| GET | `/v1/control/runs/{run_id}` | `control_v1_get_run` | 已实现 |
| GET | `/v1/control/runs/{run_id}/events` | `control_v1_list_run_events` | 已实现 |
| POST | `/v1/control/runs/{run_id}/guidance` | `control_v1_guide_run` | 已实现 |
| POST | `/v1/control/runs/{run_id}/interrupt` | `control_v1_interrupt_run` | 已实现 |
| GET | `/v1/control/tasks` | `control_v1_list_tasks` | 已实现 |
| POST | `/v1/control/tasks` | `control_v1_create_task` | 已实现 |
| GET | `/v1/control/tasks/{task_id}` | `control_v1_get_task` | 已实现 |
| POST | `/v1/control/tasks/{task_id}/dispatch` | `control_v1_dispatch_task` | 已实现 |
| POST | `/v1/control/tasks/{task_id}/plan/approve` | `control_v1_approve_task_plan` | 已实现 |
| POST | `/v1/control/tasks/{task_id}/steps/{step_id}/runs` | `control_v1_run_task_step` | 已实现 |
| POST | `/v1/control/tasks/{task_id}/pause` | `control_v1_pause_task` | 已实现 |
| POST | `/v1/control/tasks/{task_id}/resume` | `control_v1_resume_task` | 已实现 |
| POST | `/v1/control/tasks/{task_id}/cancel` | `control_v1_cancel_task` | 已实现 |
| POST | `/v1/control/chats/{chat_id}/approvals/{question_id}/responses` | `control_v1_respond_approval` | 已实现 |
| POST | `/v1/control/tasks/{task_id}/approvals/{question_id}/responses` | `control_v1_respond_task_approval` | 已实现 |
| GET | `/v1/control/tasks/{task_id}/artifacts` | `control_v1_list_artifacts` | 已实现 |
| GET | `/v1/control/tasks/{task_id}/artifacts/{artifact_id}` | `control_v1_read_artifact` | 已实现 |

### 5.1 Capability 协商

`GET /v1/control/capabilities` 返回：

- `api_version = "v1"`；
- `protocol_version = 1`；
- `auth_boundary = "desktop_local"`；
- `remote_transport_available = true`；
- `durable_run_events = true`；
- 可用 Operation 和 Feature Manifest。

Remote Client 必须按 Capability 调用，不应只通过 Cyrene Version 猜测能力。

### 5.2 Project 和 Chat

Project API 只返回：

- `id`、`name`、`status`、`updated_at`；
- Task 数量。

它不返回 `workspacePath`、Model、Credential 或完整 Store。Chat 使用独立
Snake Case DTO，不把内部 Workbench Store 结构直接作为公共契约。

创建 Chat Message 的请求为：

```json
{
  "message": "检查项目并继续当前工作",
  "permission_mode": "default",
  "language": "zh"
}
```

未知字段会被拒绝。远程 API 不能请求 `auto`、`full_access` 或自行携带任意
Tool Permission。

### 5.3 Run

发送消息返回：

```json
{
  "run_id": "run_...",
  "chat_id": "chat_...",
  "status": "running",
  "created_at": "2026-07-27T00:00:00+00:00",
  "event_cursor": 0
}
```

HTTP 状态是 `202 Accepted`。后续操作只使用 `run_id`，避免控制端拿任意
Chat ID 猜测或干扰不属于目标 Run 的执行。

事件接口使用：

```text
GET /v1/control/runs/{run_id}/events?after=<cursor>&limit=<1..500>
```

响应提供 `next_cursor`、`completed` 和 `truncated`。Cursor 对应 SQLite
中的单调事件序号，默认保留七天；完成 Run 即使已离开进程内保留窗口，仍可
按 `run_id` 跨重启读取。硬重启中断的 Run 会追加
`code=process_restarted` 的终态事件。

`guidance.request_id` 复用现有持久 Inbox 的 Idempotency 能力。所有远程
Side-effect Command 必须携带 Idempotency Key；SQLite 会先原子占位，
并发重复请求返回 `remote_command_in_progress`，完成后重放缓存结果，不会
重复执行副作用。

### 5.4 完整远端 Agent 使用语义

“完整使用远程 Cyrene”不是把远端任意 HTTP、Shell 或 Tool 直接暴露给
控制端，而是：

1. `RunRemoteCyrene` 在已共享 Project 中创建 Chat；
2. 通过 `chats.send` 启动被控端自己的 Agent；
3. 被控端 Agent 使用其本机完整 Harness 能力执行任务；
4. 控制端使用 `runs.events` 观察进度，通过 `runs.guide` 补充指导；
5. 遇到 Pending Question 时使用 `approvals.respond` 回答；
6. 完成后读取 Chat、Artifact 或 Attachment。

这种方式既能覆盖远端 Cyrene 的 Agent 能力，又不会绕过被控设备的权限、
Sandbox、Credential 边界和审计。旧的默认 Grant 会在精确匹配旧默认集合时
自动补入 `approval:respond`；用户定制过的 Grant 不会被扩权。

## 6. 权限和安全边界

核心原则：

> 控制端只能请求被控端 Cyrene 做事；只有被控端 Harness 能决定是否允许、
> 如何执行以及何时需要本地审批。

目前已经做到：

- 仍处于本机 Loopback Authentication Boundary；
- 使用严格、限长的请求模型；
- 只开放显式 Operation；
- 返回 Project 摘要而不是本地绝对路径；
- 不接受 Tool Name、Shell 或任意 URL；
- 限制 Permission Mode；
- 不输出现有 `reasoning_*` 事件；
- 错误使用稳定的 HTTP Status 和 `code`。
- Peer Device 公钥身份和签名验证；
- Grant、Capability、Project Scope 的双向交集校验；
- E2EE、Nonce、Timestamp 和 Replay Protection；
- 一次性配对邀请、设备撤销和审计日志；
- Agent 只能访问当前 Chat 显式选择的设备；
- Side-effect Tool 同时经过本机 Permission Mode 和被控端 Grant。

Control API 和 RemoteGateway 共用显式 Public Event Allowlist。未知事件、
`reasoning_*`、Workspace Change、Tool 参数和调试对象直接丢弃；允许的事件
也只保留固定标量字段、公开回复、受控 Pending Question 与公开 Intermediate
Message。内部 Workbench Event 或 Store JSON 不得原样转发到网络。

## 7. 目标远程架构

```text
控制端 Workbench
      │ typed command
      ▼
控制端 Remote Client
      │ 签名 + 端到端加密 envelope
      ▼
局域网 TCP/HTTP ─────── 直接投递到已配对设备 IP
      │
      ▼
被控端 Runtime RemoteGateway
      │ identity → grant → scope → policy → schema → idempotency
      ▼
Workbench Control Application Service
      │
      ▼
Agent Harness / Tool Policy / 本机执行环境
```

LAN Listener 不是 FastAPI 反向代理。RemoteGateway 只接受带版本的领域命令，例如：

```json
{
  "version": 1,
  "message_id": "msg_...",
  "command": "chat.send",
  "project_id": "project_...",
  "resource_id": "chat_...",
  "idempotency_key": "idem_...",
  "payload": {
    "message": "继续执行",
    "permission_mode": "default"
  }
}
```

被控端必须依次验证：

1. Envelope Version 和 Schema；
2. Peer Signature、Nonce、Timestamp 和 Replay Window；
3. Grant 未过期、未撤销；
4. Operation 在 Capability Allowlist；
5. Project 在 Scope；
6. 请求风险不超过被控端 Policy；
7. Idempotency Key 没有产生冲突；
8. 领域服务仍按本机 Harness 规则执行。

## 8. 身份、配对和 Grant 实现

首次开启远程访问时生成独立 Device Identity：

- Ed25519 签名密钥；
- X25519 密钥交换密钥，或采用成熟的 Noise/HPKE 方案；
- 从公钥派生不可编辑的 `device_id`；
- 私钥存入 macOS Keychain / Windows Credential Manager；
- 设备名只是展示字段，不能作为身份。

正式界面使用局域网 IP + 10 位一次性短密钥。Base64URL Invitation /
Response Bundle 仍是内部签名握手格式和兼容 API，不再要求用户复制。配对
流程：

1. 被控端选择 Capability 和 Project Scope；
2. 被控端生成至少 128 bit 内部 Secret，同时生成易输入的 10 位 Crockford
   风格短密钥，TTL 为两分钟；
3. 设置页显示本机局域网 `IP:37841` 和形如 `ABCDE-23456` 的短密钥；
4. 控制端把 IP 和短密钥交给本机 API；本机后端通过受限 LAN Listener 自动
   获取签名 Invitation、生成签名 Response 并交回被控端；
5. Listener 只接受 `/v1/pairing/claim` 和 `/v1/pairing/complete`，请求体
   上限 64 KiB；短密钥按来源每分钟最多尝试五次，首次领取后绑定来源 IP；
6. 双方验证 Device ID、公钥、签名、内部 Secret、TTL 和来源绑定后交换公钥；
7. 短密钥和内部 Invitation 立即失效，被控端持久化权威 Grant；
8. 后续远程命令直接投递到已保存的 LAN 地址，LAN Listener 只承载 Signed +
   E2EE Envelope，不暴露桌面本机 Control API。

相关桌面本机管理 API：

- `POST /api/remote/pairing/short-key`：按选定权限创建短密钥；
- `POST /api/remote/pairing/connect`：输入局域网 IP 和短密钥，一次完成双向
  公钥交换；
- 原 `/pairing/invitations`、`/accept`、`/complete` 继续作为兼容接口。

直接连接限制在 private/link-local/loopback 地址，不接受任意公网 URL，避免
把桌面本机 API 变成 SSRF 跳板。短密钥不是长期凭证，长期信任只绑定签名
公钥。6 位数字空间过小，不能独立建立长期信任。

已持久化的 Grant 包含：

- Peer Device ID 和 Public Key；
- Capability Allowlist；
- Project Scope；
- `granted_capabilities/project_scopes`：本机授予对方；
- `received_capabilities/project_scopes`：对方授予本机；
- `created_at`、`last_seen_at` 和 `revoked_at`。

最终权限是 Device Grant、Project Scope、当前本机 Policy 和具体资源状态的
交集，不能信任控制端自行声明的权限。

## 9. 传输、恢复和持久化状态

主控制面使用局域网 TCP/HTTP 直连：

- 每台启用远程访问的 Cyrene 默认监听 `0.0.0.0:37841`；
- 只接受 `/v1/pairing/claim`、`/v1/pairing/complete` 和
  `/v1/control/envelope`；
- 配对完成后双方持久化对端 private/link-local IP 与监听端口；
- 控制端直接向对端投递 Signed + E2EE Envelope；
- 接收端先校验来源地址、可信设备，再由 RemoteGateway 完成签名、解密、
  Grant、Project Scope、Replay 和 Idempotency 校验；
- HTTP 投递快速返回 `202 Accepted`，命令结果通过反向直连的加密 Response
  Envelope 返回；
- 长任务状态仍使用 Durable Event 和 Cursor 恢复，不依赖单次 HTTP 请求；
- 退出时关闭 LAN Listener、拒绝 Pending Request 并注销 Agent Tool Gateway。
- 控制端每个命令的发送、完成和失败分别记录 Audit，错误明确标记
  `controller`、`transport` 或 `remote` 来源，避免把控制端数据库错误误报为
  被控端执行错误。

Remote Control Store 与主 Runtime Store 已分离。设备身份仍沿用原逻辑库
路径派生，以保持升级前后的 Device ID 稳定；远程表则写入
`<runtime-db>.remote-control`。首次打开会从旧主库复制远程表并记录迁移，
旧表暂留用于回滚。即使主运行库正持有 `BEGIN IMMEDIATE` 写事务，远程命令
仍可完成审计、加密投递和响应。

仓库仍保留旧的 `cyrene-relay` 与 `WebSocketRemoteRelay` 作为兼容代码，但
当前产品路径不实例化它们，设置界面也不再要求 Relay URL。除非产品范围重新
扩展到跨公网或 NAT，否则不得把 Relay 重新加入默认依赖。

Durable Event 已实现为：

- SQLite 持久化 Run 元数据和 Event；
- 每个 Run 使用单调 Cursor，Event Commit 后才唤醒读取方；
- 进程内 Ring Buffer 继续承担实时低延迟 Fan-out；
- 完成或硬重启后的 Run 可从 SQLite 重新装载；
- 默认七天保留，Buffer Gap 仍返回显式 `truncated`；
- Process Restart 为未完成 Run 追加公开错误终态，不改写已提交 Cursor。

## 10. Task、Approval 和 Artifact 契约

Task、Approval 和 Artifact 已进入当前 OpenAPI，具体 Path 见第 5 节。

- Task 支持 list/create/read/dispatch、计划批准、逐步执行、
  pause/resume/cancel；
- 状态转换在服务端校验，例如只有 Active Task 可以 Pause、只有 Paused Task
  可以 Resume；
- Approval Response 可绑定 `chat_id + question_id` 或
  `task_id + question_id`，必须匹配当前 Pending Question；
- Goal Loop 控制调用真实的持久后台 Manager；普通 Task pause/cancel 也会
  中断当前 Agent Run，而不是只修改展示状态；
- Artifact List 返回受控 Metadata 和 API Download URL，不返回绝对路径；
- 本机 Control API 的 Artifact Content 使用 `FileResponse`；
- 远程 Envelope 的 Artifact/Attachment Content 使用最多 1 MiB 的单块
  Base64；完整文件没有大小上限，默认以 512 KiB 分块；
- 每块带 `offset`、`next_offset`、`size`、`eof` 和 `progress`，控制端验证
  连续偏移、自动组装并发布 `tool_call_progress`；
- Attachment 必须由目标 Chat 明确引用；引用项可指向 Cyrene 托管目录之外
  的本机文件，但不能借附件接口读取未被该 Chat 引用的任意路径；
- RemoteGateway 仍按 `artifact:read` Capability 和 Project Scope 校验。

普通澄清和已被 Grant 允许的 Approval 可以远程回答；文件写入、Shell、
Credential 和桌面接管等高风险操作仍由被控端 Harness 的 Permission Mode
决定，远端 Grant 不能绕过本地确认。

## 11. 远程桌面后续方案

只有 L1/L2 稳定后再实现 L3。推荐：

- Signaling 复用已认证的 Relay；
- Media/Input 使用 WebRTC；
- TURN 作为受限网络回退；
- macOS 使用 ScreenCaptureKit 和 Accessibility；
- Windows 使用 Desktop Duplication / Graphics Capture 和 SendInput；
- Electron 负责授权 UI、会话指示器和紧急停止。

每次桌面会话必须：

- 由被控端单独批准；
- 使用短时 Session Token；
- 明确区分 View Only 与 Keyboard/Pointer；
- 显示持续、不可隐藏的本机指示器；
- 一键终止；
- 默认禁止锁屏、登录界面、UAC Secure Desktop；
- 不自动把桌面权限继承给 Agent Tool。

## 12. 分阶段实施顺序

### Phase 1：本机 API 契约

状态：已完成。

- 严格 v1 DTO；
- Capability、Project、Chat、Run、Task、Approval、Artifact；
- Detached `202`；
- Cursor Event、guidance、interrupt；
- OpenAPI 和行为测试。

### Phase 2：服务边界和 Durable Run

状态：已完成。

- Workbench Route 通过窄 Adapter 复用既有 Application Logic；
- 固定远程命令集中在 `RemoteCommandExecutor`；
- Chat Message Idempotency；
- SQLite Run/Event Outbox；
- Public Event Allowlist 和敏感字段净化；
- Process Restart/Disconnect/Replay 测试。

### Phase 3：设备信任和 RemoteGateway

状态：已完成。

- Device Key、Keychain/Credential Manager；
- Pairing、Grant、Scope 和 Revoke；
- 局域网 IP 直连传输与双向地址持久化；
- Signed + E2EE Envelope；
- Capability Negotiation；
- Audit Log、Rate Limit 和 Abuse Protection。
- LAN 来源校验、快速投递回执、Grant 同步和离线错误语义。

### Phase 4：完整远程监督

状态：已完成。

- Task Contract；
- 计划批准与逐步执行；
- 普通 Task 和持久 Goal Loop 的 pause/resume/cancel；
- Chat/Task Clarification/Approval；
- Artifact Metadata/Download；
- 多设备冲突、断线恢复和长任务验收。

### Phase 5：可选远程桌面

- WebRTC/TURN；
- 屏幕查看；
- 独立授权的键鼠控制；
- 不可隐藏的 Session Indicator 和 Kill Switch。

## 13. 验收和测试基线

当前新增测试覆盖：

- Capability 如实报告 Remote Transport；
- Project DTO 不泄漏本地路径或 Model 字段；
- Chat list/create/get；
- 未知请求字段被拒绝；
- Detached Run 返回 `202`；
- Run 完成、SQLite 持久重放、Cursor 和 Buffer Gap；
- `reasoning_*` 不进入 Control Event；
- guidance 绑定 `run_id` 且 `request_id` 去重；
- interrupt 绑定 `run_id`；
- OpenAPI Path、Tag、Operation ID 和响应状态。
- Chat Attachment 内容端点只读取对话明确引用的文件；
- 配对方向性 Grant、单次邀请和 Project Scope；
- Grant 更新、撤销与越权拒绝；
- E2EE Envelope 篡改、签名和 Replay 拒绝；
- 配对 Bundle 篡改、Relay 未签名注册和消息速率拒绝；
- Command Idempotency 原子占位、重放、执行中和冲突；
- Grant 更新与撤销通过 E2EE 同步；
- Relay 对离线收件人返回投递失败；
- SQLite Event 跨 Manager 重载和重启中断终态；
- Public Event Allowlist 删除 Reasoning、Workspace Change 和调试字段；
- Task 计划批准、逐步执行和 Task Approval；
- Task/Goal Loop 控制使用真实执行中断与后台 Manager；
- Agent Tool 只能访问当前 Chat 选择的设备；
- 隔离的 Context API 拒绝未知/已撤销设备；
- 两个隔离数据库通过真实本机 WebSocket Relay 完成加密请求/响应；
- 主 Runtime SQLite 持有写锁时，Remote Sidecar 仍能完成加密命令；
- `RunRemoteCyrene` 通过双 Gateway 创建 Chat 并启动目标 Agent；
- 超过 10 MiB、非托管目录但被 Chat 明确引用的附件可连续分块读取；
- 控制端自动组装分块、返回本地附件且不把 Base64 暴露给 Agent；
- 设置页 12 个 Tab 无侧栏滚动；
- 设置与更新日志高度一致；
- “添加上下文”显示配对设备并生成远程设备 Chip；
- 前端 Console 无 Error/Warning。

聚焦回归：

```bash
uv run pytest -q \
  tests/test_progressive_tool_packages.py \
  tests/test_webui_consolidation_contract.py \
  tests/test_route_structure.py \
  tests/test_control_api.py \
  tests/test_remote_control.py \
  tests/test_workbench_chat_run_recovery.py \
  tests/test_architecture_boundaries.py \
  tests/test_workbench_frontend_logic.py::test_workbench_about_related_actions_only_click_right_button
```

结果：本轮聚焦远程、Control、路由、工具注册与前端测试
`198 passed in 17.46s`；契约锁定复核 `55 passed in 16.16s`。

完整回归：

```bash
uv run pytest -q
```

结果：`1466 passed in 120.00s`。

加入本阶段契约后：

- Route Declaration：`297`；
- OpenAPI Path：`250`；
- OpenAPI Operation：`295`；
- FastAPI Baseline：`0.136.1`；
- Pydantic Baseline：`2.13.4`；
- Route Contract SHA-256：
  `e10af1293db61fb04053bcd1744bd9d080750a481189a567ecc675656b9dc95e`；
- OpenAPI SHA-256：
  `f1762d75b0dc465fb980bb4f2d890b67115338b7e413845c0a4ad142babc5287`；
- Tool Registry SHA-256：
  `3b44e3cd4554cf4f722c4dc03d18955307b5d21941ea8e5e8df0db9d61f3f8f8`；
- Main Agent Wire Tool：`29`，SHA-256：
  `56f247691752283c226eb34ea1a8a902df14c8cd5c83ab94f92dbdd89f0e76f3`；
- Subagent Wire Tool：`23`，SHA-256：
  `2d29000a405e62b49ae6374bbbf43b1edb34fbf0fdee32e5816f73b026981c75`。

严格 Hash 只能在项目锁定依赖环境中更新，并且必须先逐项审查新增/删除的
Path、Method、Operation ID 和 Schema。不得为了让测试通过而盲目刷新。

## 14. 后续修改检查清单

修改 Control API 时至少运行：

```bash
uv run pytest -q tests/test_control_api.py
uv run pytest -q tests/test_route_structure.py
uv run pytest -q tests/test_webui_consolidation_contract.py
uv run pytest -q tests/test_architecture_boundaries.py
uv run pytest -q
```

远程控制后续修改必须持续回归：

- 配对过期、重复使用、Fingerprint 不匹配；
- Grant 过期、撤销、Project Scope 越权；
- Signature、Nonce、Replay 和版本降级；
- LAN 断线、目标不可达、重复投递、乱序和 Process Restart；
- 同一 Idempotency Key 同 Payload 重放与不同 Payload 冲突；
- Event Gap、过期 Cursor、Snapshot 恢复；
- 路径、Credential、Prompt 和 Reasoning 泄漏测试；
- 本机停止远程访问后立即拒绝新 Command；
- 远程审批无法绕过本机 Harness。

## 15. 禁止回归

后续实现不得：

- 把 FastAPI 直接暴露到公网；
- 把本机桌面 Token 复用为设备身份；
- 让 RemoteGateway 接受任意 Route、Tool、Shell 或 SQL；
- 让 Route 成为新的业务编排层；
- 新增未获正式架构批准的 `cyrene` 顶层目录；
- 用内存 Ring Buffer 冒充 Durable Event Log；
- 将内部 Workbench Store JSON 固化成远程公共契约；
- 在 Remote Event 中发送绝对路径、Credential、完整 Prompt 或隐藏推理；
- 让控制端授予自己 Capability 或 Project Scope；
- 让远程桌面授权自动扩大 Agent Tool Permission；
- 在未完成设备身份、Grant 和 E2EE 前把 capability 标记为
  `remote_transport_available=true`。
