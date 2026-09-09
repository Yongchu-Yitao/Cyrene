> **SHIPPED / v0.1.1 主流程已实现并发布，增强项仍待完成 — 2026-08-02**
>
> Cyrene Mobile 已完成首个公开版本，当前仓库版本为 `0.1.1`（tag `v0.1.1`）。
> 本文记录当前代码事实、桌面端依赖、安全边界、已知限制和后续维护基线，不再作为
> “尚未开始编码”的目标设计稿。
>
> 移动端仍是纯控制器：模型、Agent、Tool、Skill、MCP、Browser、文件系统和任务
> 执行均发生在桌面端 Cyrene。正式连接支持同一局域网与 Tailscale 数字 IP 直连，
> 不依赖云 Relay、账号服务或公网 API。

# Cyrene Mobile Controller 实现 Handoff

[桌面端 Cyrene-to-Cyrene 远程控制设计](../../Cyrene/project-notes/remote-cyrene-control-design.zh-CN.md) ·
[桌面端架构说明](../../Cyrene/docs/architecture.zh-CN.md) ·
[移动端更新记录](../CHANGELOG.md) ·
[界面设计 QA](../design-qa.md)

更新时间：2026-08-02

移动端仓库：`Cyrene-mobile`

桌面端依赖仓库：`../Cyrene`

当前发布：`0.1.1`，`versionCode = 2`

桌面端兼容基线：完整功能需要 Cyrene Desktop `0.7.0-beta.9` 或更新版本；
建议与当前桌面端主分支或最新发布版本配套使用。

## 实施状态摘要

### 已完成

| 领域 | 已落地能力 |
|---|---|
| 配对与安全 | IP + 十位短密钥配对、Fingerprint 用户确认、Ed25519 签名、X25519/HKDF/ChaCha20-Poly1305 E2EE、Grant、Project Scope、Replay 和 Idempotency |
| 设备 | 保存和切换多台桌面设备、移除本机信任、按设备保存缓存 |
| Project / 会话 | 读取授权项目，跨项目聚合并按时间展示所有 Chat 和 Task |
| Chat / Run | 创建、读取、发送、重命名、删除、Markdown、工具调用、附件、增量事件轮询、指导和停止 |
| 权限确认 | 自动、默认、Plan 三种模式，回答 Chat/Task 澄清和提权问题 |
| Task | 创建、派发、批准计划、执行步骤、暂停、恢复、取消、回答问题和查看产物 |
| 文件 | 多附件上传，Attachment/Artifact 分块下载，图片缩略图、原图和文件查看 |
| 终端 | 在授权项目内打开、读取、写入、中断和关闭按设备隔离的远程 Shell |
| 桌面设置 | 读取/更新允许的 Agent 设置，选择模型与 reasoning effort，管理桌面 OpenAI OAuth |
| 本地体验 | 桌面数据缓存、错误重试、下载进度、中英文、浅色/深色/跟随系统主题 |
| App 更新 | 检查 GitHub Release、下载 APK、校验包名并调用系统安装器 |

### 部分完成

| 领域 | 已完成部分 | 尚缺部分 |
|---|---|---|
| Controller-only Transport | 新桌面端使用 `/v1/control/request`，正常命令无需桌面反向连接 | 为兼容旧桌面端，客户端仍会启动 `LegacyResponseListener`；尚未达到“Android 永不监听” |
| LAN / Tailscale | 支持 LAN 与 Tailscale 数字 IP，拒绝公网 IP、域名、URL 和 Redirect | 每个 Peer 只保存一个地址；没有 LAN/Tailscale 多 Endpoint、健康检测和自动回退 |
| Run 恢复 | App 退出不终止桌面 Run/Task，重开后会重新读取桌面持久状态 | Run Cursor 未在 Android 持久化；没有后台持续监控、完成/等待/失败通知 |
| 本地数据层 | 已按 Peer 保存 JSON 快照并在冷启动时先恢复再刷新 | 尚未采用 Room/Proto DataStore，也未实现原规划的多模块 Repository 架构 |
| 测试 | 已有协议 Fixture、权限模式、缓存、会话排序和更新服务 JVM 测试，桌面端有远程协议回归 | 缺少 Compose UI、Instrumentation、进程重启和完整跨仓库端到端自动化测试 |

### 尚未实现

- 同一设备保存 LAN + Tailscale 多 Endpoint、自动健康检查与无感回退；
- Tailscale MagicDNS 和 QR Code 配对；
- 用户显式开启的后台 Run 监控及系统通知；
- Android 平板、折叠屏专用双栏布局；
- 生物识别保护已配对设备；
- Android Share Sheet、Photo Picker 和 DocumentsProvider 深度集成；
- Target SDK 37 所需的 `ACCESS_LOCAL_NETWORK` 声明、运行时授权和拒绝态 UX；
- APK Release 签名、证书或独立摘要校验；当前只检查安装包包名；
- 移除 Legacy Transport 与 Android 本机监听器；
- 将大型 `MainActivity.kt` / `MainViewModel.kt` 拆分为 Feature、Repository 和独立数据层。

## 1. 当前实现结论

当前 App 已经能够完成从配对到远程工作的主要闭环：

```text
Android Compose UI
        │
        ▼
MainViewModel / 本地缓存
        │ typed command
        ▼
CyreneClient
        │ Ed25519 + X25519/HKDF + ChaCha20-Poly1305
        ▼
POST /v1/control/request
        │ LAN / Tailscale HTTP（应用层 E2EE）
        ▼
Desktop RemoteGateway
        │ identity → replay → grant → scope → schema → idempotency
        ▼
Desktop Workbench / Agent / Shell / File
```

已实现的主路径包括：

- IP + 十位短密钥配对与 Fingerprint 确认；
- 多台桌面设备保存、切换和本机移除；
- 授权项目读取与跨项目“所有会话”聚合；
- 对话创建、读取、发送、重命名、删除、指导和停止；
- 自动、默认、Plan 三种权限模式及澄清/提权问题回答；
- Task 创建、派发、计划/步骤执行、暂停、恢复、取消和问题回答；
- Attachment/Artifact 分块下载、消息内图片缩略图、原图和文件查看；
- 项目范围内的交互式远程 Shell；
- 桌面设置读取/更新、模型选择、reasoning effort 和 OpenAI OAuth 管理；
- 本地最近状态缓存、浅色/深色/跟随系统主题、中英文界面；
- GitHub Release 检查、APK 下载和系统安装流程。

App 退出或被 Android 杀死不会终止桌面端已经开始的 Chat Run 或 Task。当前 App
只在前台观察页面时轮询 Run；重新打开后会重新读取桌面端详情和持久状态。

## 2. 产品与安全边界

### 2.1 移动端负责

- 保存移动设备 Identity、可信桌面 Peer 和 UI 偏好；
- 构造固定类型的 Remote Command；
- 对 Envelope 签名、加密、验签和解密；
- 呈现桌面端返回的项目、对话、任务、事件、变更和文件；
- 将用户输入、附件、批准选择和 Shell 输入发送到桌面端；
- 缓存已读取的桌面数据以改善冷启动与断线体验。

### 2.2 桌面端负责

- 所有 LLM、Agent、Tool、Skill、MCP 和 Integration 执行；
- Workspace、Chat、Task、Run、Attachment、Artifact 和 Shell 的真实状态；
- Device Grant、Project Scope、Replay、Idempotency 与 Schema 校验；
- Credential 和 OpenAI OAuth Token 的保存；
- 文件访问边界与 Shell 工作目录约束；
- Remote Control SQLite Sidecar 与 Audit Log。

### 2.3 仍然明确不做

- 在 Android 运行 Cyrene Agent 或 Python Runtime；
- 将桌面本机 `/api/*`、`/v1/control/*` 或 `X-Cyrene-Token` 直接作为移动业务 API；
- 接受任意 URL、Route、HTTP Method、SQL、Python、Tool Name 或未注册 Command；
- 远程读取未被当前 Chat、Task 或授权项目明确引用的任意文件；
- 建设公网 Relay、云账号、NAT 穿透或托管 Tailscale 登录；
- 提供桌面视频、鼠标、键盘或登录界面接管；
- 远程 Backup、Restore、Reset、Update、Restart、Shutdown；
- 在移动端保存桌面 Provider Credential 或 OAuth Token。

远程 Shell 是当前版本明确加入的能力，但必须同时满足：已授权项目 Scope、
`pluginpack:cyrene_code` Grant、桌面端按设备隔离 Shell，以及工作目录限制在该项目中。

## 3. 实际源码结构

当前实现是单 `app` 模块，并未采用最初规划的多模块/Room 架构：

```text
Cyrene-mobile/
├── app/
│   ├── build.gradle.kts
│   └── src/
│       ├── main/
│       │   ├── AndroidManifest.xml
│       │   ├── kotlin/ai/cyrene/mobile/
│       │   │   ├── CyreneMobileApp.kt       App 主题初始化
│       │   │   ├── MainActivity.kt          Compose UI、导航与交互组件
│       │   │   ├── MainViewModel.kt         状态、命令编排、轮询与缓存同步
│       │   │   ├── ApprovalQuestion.kt      确认问题解析
│       │   │   ├── PermissionMode.kt        auto/default/plan 语义
│       │   │   ├── RecentSessions.kt        跨项目会话聚合
│       │   │   ├── ThemeMode.kt              系统主题同步
│       │   │   ├── network/
│       │   │   │   └── CyreneClient.kt      配对、Endpoint Policy、传输
│       │   │   ├── protocol/
│       │   │   │   ├── CanonicalJson.kt
│       │   │   │   └── CyreneCrypto.kt      Identity、KDF、AEAD、Envelope
│       │   │   └── data/
│       │   │       ├── SecureStore.kt        Identity、Peer、偏好
│       │   │       ├── DesktopDataCache.kt   桌面数据 JSON 快照
│       │   │       ├── GithubUpdateService.kt
│       │   │       └── ApkUpdateDownloader.kt
│       │   └── res/                          中英文、主题、图标与安全配置
│       └── test/                             JVM 单元测试
├── gradle/libs.versions.toml
├── README.md
├── CHANGELOG.md
├── design-qa.md
└── project-notes/
```

主要依赖方向为：

```text
Compose UI → MainViewModel → CyreneClient → protocol/crypto
                       └── SecureStore / DesktopDataCache
```

维护时应避免让 UI 直接构造 Envelope 或操作私钥。若未来继续扩大功能面，应优先把
`MainActivity.kt` 和 `MainViewModel.kt` 按 Feature 拆分；当前二者已经承担大量职责。

## 4. Android 平台基线

当前锁定配置：

| 项目 | 当前值 |
|---|---|
| Min SDK | 28（Android 9） |
| Compile / Target SDK | 35 |
| JDK / JVM Target | 17 |
| Android Gradle Plugin | 8.8.2 |
| Kotlin | 2.0.21 |
| Compose BOM | 2025.02.00 |
| Coroutines | 1.10.1 |
| Bouncy Castle | 1.80 |
| Markwon | 4.6.2 |

Manifest 当前声明：

```xml
<uses-permission android:name="android.permission.INTERNET" />
<uses-permission android:name="android.permission.REQUEST_INSTALL_PACKAGES" />
```

App 使用 Cleartext HTTP 承载应用层 E2EE Envelope，`network_security_config.xml`
允许 Cleartext。所有桌面连接仍须经过 `EndpointPolicy`；更新检查和 APK 下载则访问
固定 GitHub HTTPS 地址。

`allowBackup=false`，SharedPreferences 和 Database 也被排除在 Cloud Backup 与
Device Transfer 之外。当前 Target SDK 35 尚未声明 Android 17 / API 37 的
`ACCESS_LOCAL_NETWORK`；未来升级 Target SDK 时必须补充声明与运行时授权。

## 5. Identity、密钥与协议

### 5.1 Identity 持久化

首次启动生成 32-byte Ed25519 与 X25519 Private Key：

```text
device_id = "dev_" + base64url(SHA256(signing_public_key)[0:18])
fingerprint = SHA256(signing_public_key) 的前 16 bytes（32 个十六进制字符）分组显示
```

Raw Private Key Bundle 由 Android Keystore 中不可导出的 AES-256-GCM Master Key
加密后保存在 SharedPreferences。解密失败时会清除旧 Ciphertext 并生成新 Identity，
此时必须重新配对。Peer 公钥、Grant、Scope、Host 和 Port 保存在同一私有
SharedPreferences 中。

### 5.2 Wire Compatibility

协议版本为 1，移动端与桌面端逐字节保持：

- Canonical JSON：UTF-8、Object Key 排序、无多余空白；
- Base64URL：无 Padding；
- Envelope 签名：Ed25519；
- Shared Secret：X25519；
- KDF：HKDF-SHA256；
- AEAD：ChaCha20-Poly1305；
- Device ID、Fingerprint、Signature 与 HKDF 有固定 Python/Kotlin 兼容测试向量。

桌面端继续执行五分钟 Timestamp Window、持久 Nonce Replay Protection、Grant、
Project Scope、固定 Command Allowlist 和 Side-effect Idempotency。

## 6. 配对与 Endpoint Policy

### 6.1 当前配对流程

1. 用户输入 LAN/Tailscale 数字 IP、端口和十位短密钥；
2. 移动端校验地址与端口；
3. `POST /v1/pairing/claim` 领取 Invitation；
4. 校验 Version、Kind、TTL、Device ID、Ed25519 Signature 与 Fingerprint；
5. UI 展示桌面设备名和 Fingerprint，由用户确认；
6. 移动端生成带自身 Identity、HMAC Proof 和 Signature 的 Pairing Response；
7. `POST /v1/pairing/complete`，声明：

```json
{
  "transport_mode": "request_response",
  "client_features": [
    "inline_response_v1",
    "durable_run_events",
    "chunked_files"
  ]
}
```

8. 保存 Peer，并读取授权 Project。

为兼容旧桌面端，Complete Request 仍携带 `listener_port`；若桌面端拒绝新的
`transport_mode` 字段，客户端会移除协商字段并退回旧配对格式。

### 6.2 当前允许的地址

- RFC1918 / `InetAddress.isSiteLocalAddress`；
- IPv4/IPv6 Link-local；
- Loopback（开发测试）；
- IPv6 ULA `fc00::/7`；
- Tailscale CGNAT `100.64.0.0/10`；
- 端口 `1024..65535`，UI 默认 `37841`。

当前明确拒绝 Scheme、Path、Query、Fragment、Domain 和公网 IP；请求不跟随
Redirect。客户端不扫描局域网，也不自动尝试端口范围。

### 6.3 与最初设计不同的地方

每个 Peer 当前只保存一个 `host + port`，尚未实现同一设备多 Endpoint、网络类型感知、
延迟记录、自动回退或 MagicDNS。切换 LAN/Tailscale 地址目前需要重新配对/更新本地
Peer 数据的产品流程，不能把目标架构中的 Endpoint 列表当成已实现功能。

## 7. Controller-only Transport

### 7.1 当前主路径

所有业务命令优先使用：

```text
POST /v1/control/request
```

请求体是 `kind = command` 的加密 Envelope；桌面端在同一个 HTTP Response 中返回
`kind = response` 的加密 Envelope。Android 不需要可被桌面端回连，LAN → Tailscale
的来源地址变化也不改变已配对 Device Identity。

`runs.wait` 的 Payload 等待时间为 25 秒，单次 HTTP 请求超时为 50 秒；收到事件后
更新当前 UI，再继续轮询。
轮询只属于当前 `ViewModel` 生命周期，不使用后台 Service。

### 7.2 兼容旧桌面端

如果 `/v1/control/request` 返回 `404` 或 `405`，客户端会退回：

```text
POST /v1/control/envelope → 202 Accepted
Desktop → Android LegacyResponseListener → encrypted response
```

当前 `CyreneClient.command()` 在发起主路径请求前也会初始化一个本机
`LegacyResponseListener`，监听 `37841..37940` 中可用端口，必要时再使用随机端口。
因此从实现事实看，最新桌面端的正常命令不依赖反向连接，但 App 进程仍会为兼容模式
打开本机 ServerSocket。后续若结束旧桌面兼容，应移除此监听器及 `listener_port`，才能
完全满足“移动端永不监听”的更严格边界。

### 7.3 当前错误映射

客户端明确处理 `403 / 404 / 409 / 413 / 429 / 503`；其他状态优先显示桌面端
明文 `error`，否则显示通用 HTTP 错误。领域失败位于解密后的
`result.ok/code/error` 中。请求禁止 Redirect，并设置 `Cache-Control: no-store`。

## 8. 当前 Remote Command 覆盖

### 8.1 移动端已调用

| 领域 | Command | 当前用途 |
|---|---|---|
| Project | `projects.list` | 获取已授权项目 |
| Chat | `chats.list/create/read/update/delete/send` | 会话完整主流程 |
| Run | `runs.wait/guide/interrupt` | 事件轮询、补充指导、停止 |
| Approval | `approvals.respond` | Chat/Task 澄清与提权回答 |
| Task | `tasks.list/create/read/dispatch/approve_plan/run_step/pause/resume/cancel` | Task 生命周期 |
| Change | `changes.read` | 右侧栏 Diff 查看 |
| File | `artifacts.list/read`、`attachments.read` | 产物、附件、图片下载 |
| Settings | `settings.read/update` | 桌面 Agent 设置、模型、reasoning effort |
| OAuth | `settings.openai_oauth.read/login/logout` | 桌面 OpenAI OAuth 管理 |
| Shell | `shell.open/read/write/interrupt/close` | 项目内交互式终端 |

客户端的 Side-effect 集合会为写命令生成唯一 `idempotency_key`。需要保持与桌面端
`_SIDE_EFFECT_COMMANDS` 同步；新增写命令时两端必须同时更新。

### 8.2 桌面已支持、当前 UI 未直接使用

- `capabilities.read`；
- `runs.read`、`runs.events`；
- `harness.list`、`harness.describe`、`harness.invoke`。

这些命令存在于桌面 Allowlist 不等于移动端已经提供对应界面。不得只依据桌面端
Handler 宣称移动功能完成。

### 8.3 Permission Mode

移动端模式映射：

| UI 模式 | Wire 值 | 行为 |
|---|---|---|
| 自动 | `auto` | 首次安装默认；允许桌面按自动策略运行 |
| 默认 | `default` | 使用桌面标准权限确认 |
| Plan | `plan` | 对话先规划；回答 Approval/执行 Task 时降为 `default` |

模式保存在本机偏好中，并随 Chat 发送、Task 派发和 Approval 回答传递。真正的权限
决策仍由桌面端执行，移动端模式不能扩大 Device Grant 或 Project Scope。

## 9. UI 与交互现状

### 9.1 导航

当前为单 Activity Compose App：

- 左侧抽屉：设备、对话、任务、终端和“所有会话”；
- 设置入口位于抽屉右下角；
- 对话/任务内容区向左滑可打开桌面式右侧栏；
- 右侧栏按返回数据动态显示概览、上下文、子 Agent、变更、查看器、地图、计划、
  产物和分支等页签；
- 多项目的 Chat 和 Task 按更新时间混排并标注项目。

### 9.2 Chat

- 新建会话后立即发送首条消息，标题由桌面端逻辑生成；
- 支持 Markdown、代码、工具调用、状态和附件展示；
- 支持多文件上传、图片缩略图、原图预览和文件打开；
- 支持运行中指导、停止、确认问题、重命名和删除；
- Run 使用 `run_id + cursor` 增量轮询；本地会先插入待发送用户消息，再用桌面结果校正。

### 9.3 Task

- 创建时选择授权项目并填写标题/目标；
- 详情页复用 Chat Composer，可携带附件派发或运行步骤；
- 支持暂停、恢复、取消和 Approval；
- 产物按 512 KiB 分块读取，写入 `.part`，校验 offset/size/eof 后原子发布。

### 9.4 Terminal

- 打开页面时为当前项目创建新 Shell；
- `shell.read` 约每 800 ms 轮询，失败后有上限退避；
- 支持发送、历史导航、常用控制键、`Ctrl-C`、清屏和关闭；
- 屏幕仅保留最近 500 行，显示前会清理 ANSI 控制序列；
- 切换设备/项目时关闭或丢弃旧会话状态。

### 9.5 Settings 与更新

- 支持 App 语言、主题和默认 Permission Mode；
- 动态读取桌面设置 Schema 并更新允许的设置；
- 支持桌面模型、reasoning effort 与 OpenAI OAuth 登录/退出；
- OAuth 授权链接可在手机浏览器打开，但 Token 只存桌面端；
- 关于页通过固定 GitHub Releases API 检查版本，下载 APK 后校验包名再调用系统安装器。

## 10. 本地数据与缓存

当前未使用 Room。数据分为：

| 数据 | 存储 |
|---|---|
| 加密 Identity Bundle | SharedPreferences + Android Keystore AES-GCM |
| Peer、活动设备、主题、语言、权限模式 | 私有 SharedPreferences |
| Project/Chat/Task/Artifact 快照 | `filesDir/desktop-data/<peer>.json` |
| Attachment/Image/Artifact 下载 | App 私有或 External Files 目录 |
| APK 更新 | App External Files `Downloads/updates` |

`DesktopDataCache` 按 Peer 保存项目列表、各项目 Chat/Task 列表、详情与 Task
Artifact。启动时先恢复缓存，再刷新项目；随后限制并发地后台预取所有授权项目数据。
缓存只是离线展示与启动优化，桌面端始终是 Source of Truth。

本机“忘记设备”只移除手机端 Peer 和该 Peer 的桌面数据快照，不会撤销桌面端 Grant。
要彻底撤销设备，仍需在桌面 Cyrene 的连接设置中撤销。

## 11. 文件与附件边界

- Chat 上传附件由 Android 读取后编码进固定 `chats.send` / Task Payload；
- 下载必须提供当前 Project、Chat/Task 和 Attachment/Artifact ID；
- 桌面端再次校验对象归属与 Project Scope；
- 下载采用固定上限的分块 Base64，不接受任意路径；
- 临时 `.part` 文件完成并校验后才替换最终文件；
- 图片缩略图和原图分别缓存，UI 对大图使用采样解码；
- APK 更新下载不走 RemoteGateway，只允许 GitHub Release 返回的 HTTPS Asset URL，
  安装前检查 APK 包名为本应用。

## 12. 桌面端实现依赖

关键桌面端源码：

```text
../Cyrene/src/cyrene/runtime/
├── remote_control.py       Identity、Pairing、Grant、Envelope、Gateway
├── remote_pairing.py       LAN Listener、request/response 与 legacy transport
└── remote_commands.py      Command 到 Workbench/Agent/Shell 的映射

../Cyrene/src/route/
├── remote.py               桌面本机远程设置与配对管理
└── remote_schemas.py       远程设置 DTO
```

移动端 `0.1.1` 依赖桌面端至少包含：

- `/v1/control/request` inline response transport；
- Chat update/delete、Change detail、Attachment/Image 读取；
- Approval 与 `auto/default/plan` Permission Mode；
- Desktop Settings、Model 和 OpenAI OAuth Commands；
- 按设备与项目隔离的 `shell.*` Commands；
- 移动端右侧栏所需的 Chat/Task 扩展字段。

两仓库协议修改必须同步检查：

1. `_COMMAND_CAPABILITIES` 与移动端调用集合；
2. `_SIDE_EFFECT_COMMANDS` 与 Android `SIDE_EFFECTS`；
3. Payload 字段、大小上限和错误码；
4. Public Event/Detail 字段是否仍经过安全过滤；
5. Python/Kotlin Canonical JSON、KDF 和 Envelope Fixture；
6. 最低兼容桌面版本与 CHANGELOG。

## 13. 构建与验证

要求本机具有 JDK 17 和 Android SDK 35：

```bash
./gradlew test lint assembleDebug
```

Debug APK：

```text
app/build/outputs/apk/debug/app-debug.apk
```

当前 JVM 测试覆盖：

- Canonical JSON、Identity、Signature、HKDF 跨语言 Fixture；
- Permission Mode 与 Approval Question 解析；
- 跨项目会话聚合与排序；
- Desktop Data Cache Round-trip；
- GitHub Release 解析与版本比较。

桌面端已有与移动相关的回归测试，至少覆盖：

- 无 Controller Listener 的 `/v1/control/request`；
- `transport_mode = request_response` 协商；
- Grant、Project Scope、Replay 和 Idempotency；
- Chat/Task/Attachment/Artifact 远程命令；
- Remote Settings、OAuth 与 Shell 设备/项目隔离。

发布前仍需做真机/模拟器手工验证：配对、Fingerprint、浅/深色、中英文、左右侧栏、
Markdown、附件、Run 恢复、Task 控制、终端、设备切换、APK 更新，以及 LAN/Tailscale
各一次完整闭环。

## 14. 已知限制与后续优先级

### P1：安全与兼容

- 决定旧桌面兼容窗口；结束后移除 Android `LegacyResponseListener`，让移动端彻底不监听；
- 为 Target SDK 37 增加 `ACCESS_LOCAL_NETWORK` 声明、运行时权限与拒绝态 UX；
- 为 GitHub APK 更新增加 Release 签名/证书或独立摘要校验，而不仅是包名检查；
- 增补错误响应去敏、日志审计和恶意/畸形 Envelope 的 Android 端测试。

### P2：可靠性与架构

- 将单 Peer 单地址升级为 LAN + Tailscale 多 Endpoint、健康状态与自动回退；
- 将 `MainActivity.kt`、`MainViewModel.kt` 拆为 Feature/UI/Repository 层；
- 评估用 Room/Proto DataStore 替代 SharedPreferences + JSON 快照，并设计 Migration；
- 增加 Compose UI、Instrumentation、进程重启与跨仓库端到端测试；
- 对 Run/Event 采用更明确的持久 Cursor，避免只依赖详情重读恢复界面。

### P3：产品增强

- 用户显式开启的后台 Run 监控、完成/等待/失败通知；
- QR Code 配对与 Tailscale MagicDNS；
- 平板/折叠屏双栏布局；
- 生物识别保护已配对设备；
- Android Share Sheet、Photo Picker 与 DocumentsProvider 深度集成。

## 15. 禁止回归

后续实现不得：

- 绕过 RemoteGateway 直接调用桌面本机 API；
- 把 `X-Cyrene-Token`、Provider Credential、OAuth Token 或 Private Key 写入日志/UI；
- 接受公网地址、任意域名、Redirect 后的新目标或用户提供的 URL Path；
- 允许未授权 Project 的 Chat、Task、Artifact、Attachment 或 Shell；
- 将 Mobile Permission Mode 当作扩大 Grant 的依据；
- 通过任意命令名、任意 Tool Name 或任意文件路径扩展能力；
- 因手机断网、App 退出或页面切换终止桌面 Chat Run/Task；
- 静默接受 Fingerprint 变化或用新 Peer 覆盖旧可信身份；
- 把本地缓存当成桌面 Source of Truth；
- 新增写命令却遗漏 Idempotency、Capability、Scope、Schema 和 Audit。

## 16. 维护检查清单

修改移动端或桌面端远程能力时，至少确认：

- [ ] 功能描述与实际 UI、Command 和 Grant 一致；
- [ ] Android 与 Desktop Side-effect 集合一致；
- [ ] Project Scope 在桌面端对象读取前再次验证；
- [ ] 新字段只暴露公开、去敏数据；
- [ ] Identity、Peer 与缓存仍不参与 Backup/Transfer；
- [ ] Endpoint Policy 未被通用 URL Client 绕过；
- [ ] App 退出不会改变桌面 Run/Task 生命周期；
- [ ] 中英文、浅色/深色和窄屏布局均验证；
- [ ] `./gradlew test lint assembleDebug` 通过；
- [ ] 桌面远程控制回归测试通过；
- [ ] README、CHANGELOG、兼容版本与本文同步更新。

本文后续应持续描述“已经存在的代码事实”。未落地能力必须放入“已知限制与后续
优先级”，不得再混入当前功能矩阵。
