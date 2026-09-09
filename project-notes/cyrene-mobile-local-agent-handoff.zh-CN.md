# Cyrene Mobile 本地 Agent 实现 Handoff

更新时间：2026-08-02

状态：已实现真实 QEMU Linux Runtime，并在 Android Emulator API 35 完成自动化与端到端验证。

## 1. 最终产品边界

- 本地会话由 Android 创建、保存、恢复和删除，与桌面端 Conversation、Project、Task、`local.sessions.*` 或 `local.sync.*` 无关。
- 本地消息、Run、Tool Call、Trace 和 Artifact 只保存在 Android SQLite。
- Agent 循环、LLM API 请求、工具选择与 Tool Result continuation 均在移动端进行。
- 桌面端只用于通过既有配对 E2EE 通道复制/回写模型配置；已复制配置后，桌面离线不影响本地会话。
- API Key 在手机落盘时由 Android Keystore AES-GCM 加密；Codex OAuth Token 不复制。
- 本地会话入口位于“对话 → 选择项目 → 本地”，并与远程会话共用同一会话列表和聊天界面。
- 所有本地会话共享同一台 QEMU 虚拟机和同一份持久化 Linux 文件系统。

```text
Cyrene Desktop             Android 主 App                     Runtime Companion
┌──────────────────┐      ┌──────────────────────────┐       ┌────────────────────────┐
│ 模型配置/API Key  │E2EE ▶│ Keystore 加密模型配置     │       │ 独立 APK / 独立 UID     │
│ settings.*       │◀ 回写│ 本地 SQLite + Agent Loop │Binder▶│ QEMU TCG + Alpine 3.24 │
└──────────────────┘      │ 手机直接调用 LLM API      │       │ 持久化 ext4 /workspace │
                          └──────────────────────────┘       └────────────────────────┘
```

## 2. 移动端实现

### 2.1 会话、UI 与持久化

- `localagent/database/LocalAgentDatabase.kt` 保存 Session、Message、Run、Model Turn、Tool Call、Plan、Approval、Subagent、Artifact 和 Trace。
- `MainViewModel.kt` 将本地项目注入项目选择器，并把本地/远程会话混合到既有会话列表；本地会话复用既有聊天详情与 composer。
- 发送后先将 composer 文本复制到局部变量并立即清空，再创建本地消息，避免成功发送后输入框残留。
- `LocalAgentForegroundService.kt` 在 Android 前台服务中执行和恢复 Run。

### 2.2 手机端模型推理与设置

- `SecureStore.kt` 使用 Android Keystore 中不可导出的 AES Key 加密完整模型配置，对 UI 只返回 Secret 隐藏后的投影。
- 设置页分为“本地设置”和“桌面端设置”，模型配置是独立区块，不再出现两套含义重复的“模型设置”。
- 手机读取桌面设置时调用 `settings.models.copy`，完整复制 `{version, connections, profiles, routes}` 模型图及 revision；移动端按 route → profile → connection 解析并编辑，随后同时更新本机加密副本并通过 `settings.update` 的 `models` payload 按 revision 回写桌面。
- 手机本地 Provider 当前直接执行 `codex_oauth`、`openai` 和 `openai_compatible` 主要路由；其他 Provider 及 secondary、vision、embedding 路由仍原样保存在同一张模型图中并与桌面同步。
- `MobileProviderClient.kt` 从手机直接调用 `/chat/completions`，支持 decision → execution → tool result → continue → final。
- Provider 配置和 API Key 永不发送给 Runtime APK 或 Linux guest。

### 2.3 Agent 工具

- decision 工具：`use_tools`、`ask_user`、`quit`。
- execution 工具：`Read`、`Write`、`Edit`、`Glob`、`Grep`、`Bash`。
- 文件操作和 Bash 都通过类型化 Binder 请求进入真实 Linux guest；不存在 Android `/system/bin/sh` 回退路径。
- 文件路径必须相对 `/workspace`，拒绝绝对路径、NUL 和 `..` traversal；命令有 deadline、输入大小和输出大小上限。
- 每个 Tool Call 只产生一个结构化最终 Tool Result，包括错误、拒绝和超时。

## 3. 真实 Linux Runtime

### 3.1 组成

- 独立包：`ai.cyrene.mobile.runtime`，主 App 通过 signature permission 绑定。
- 引擎：Limbo Emulator 6.0.1 提供的 QEMU 5.1.0 TCG，打包 `arm64-v8a` 与 `x86_64` Android host ABI。
- Guest：Alpine Linux 3.24 x86_64、`linux-virt 6.18.41`。
- 磁盘：512 MiB ext4 完整 Alpine 根文件系统，`/dev/vda` 挂载为 `/`；`/workspace` 位于同一持久化磁盘。
- 网络：Runtime 声明 `INTERNET` 和 `ACCESS_NETWORK_STATE`，QEMU slirp 使用 Android 当前网络的 IPv4 DNS，为 guest 提供 NAT。模型凭据仍仅在主 App。

### 3.2 单 VM 共享语义

`CyreneRuntimeService` 进程只持有一个 `QemuRuntimeManager` 和一个 QEMU 实例。所有本地 Session mount 到该实例，session ID 仅用于请求归属和审计，不创建私有目录。因此：

- 会话 A 写入 `/workspace/a.txt`，会话 B 可立即读取；
- 任一会话 `apk add` 安装的软件对其他会话可见；
- Runtime 进程停止并冷启动后，workspace 文件和已安装软件仍保存在 ext4 根盘；
- 对话记录仍由各自 Android Session 隔离，不因文件系统共享而合并。

### 3.3 镜像签名和供应链

`runtime-image/build-runtime-bundle.sh` 固定并校验 Limbo APK、Alpine minirootfs 和 kernel APK 的 SHA-256，生成 kernel、bootstrap initramfs、rootfs、firmware、License/Notice 和 manifest。

Runtime 启动前使用内置 RSA 公钥验证 manifest 签名，再逐项验证所有资产摘要；验证失败不会启动 QEMU。Limbo/QEMU 为 GPL-2.0，对应源码版本和 URL 写入 `NOTICE.txt`。正式发布必须用受保护的 release 私钥重新签名，私钥不得进入仓库或 APK。

## 4. 构建与验证

```bash
export JAVA_HOME=/opt/homebrew/opt/openjdk@17/libexec/openjdk.jdk/Contents/Home
./gradlew :app:testDebugUnitTest :app:assembleDebug :runtime-app:assembleDebug
adb install -r runtime-app/build/outputs/apk/debug/runtime-app-debug.apk
adb install -r app/build/outputs/apk/debug/app-debug.apk
```

重新生成 Runtime Bundle：

```bash
export CYRENE_RUNTIME_SIGNING_KEY=/secure/path/runtime-signing-key.pem
./runtime-image/build-runtime-bundle.sh
```

### 2026-08-02 emulator-5554 实测

- Android API 35、ARM64 host 上启动 x86_64 QEMU TCG guest；健康信息为 `alpine-3.24-qemu-x86_64-v2 / qemu-5.1.0-tcg`。
- Guest 返回 `Linux cyrene-linux 6.18.41-0-virt ... x86_64`，`/dev/vda` 以 ext4 挂载为 `/`。
- `nslookup`、`apk update` 成功；安装并运行 `fastfetch 2.64.2 (x86_64)`。
- 强制停止 Runtime 后冷启动，无需重新安装即可再次运行 fastfetch。
- 两个 Session 的自动探针显示 `active_sessions=2`，B 读取到 A 写入的 `cross-session-proof.txt`。
- 主 App 集成探针显示 `binder_connected=true`，通过正式签名 Binder 链路执行 `uname`、跨会话 `FS_WRITE/FS_READ` 和 `/sbin/apk` 检查。
- `:app:testDebugUnitTest`、`:app:assembleDebug`、`:runtime-app:assembleDebug` 通过。
- UI 验证“对话 → 当前项目 → 本地”入口存在，并复用同一 composer；Runtime 和主 App APK 已安装到 emulator-5554。

## 5. 发布门禁

- 使用正式 Runtime RSA release key 和正式 Android APK signing key，并验证两个 APK 的 signature permission 兼容。
- 保持 Runtime 镜像输入版本、SHA-256、GPL Source/Notice 可审计。
- 验证无 API Key 出现在日志、SQLite、Runtime IPC、guest 磁盘或崩溃报告。
- 在真机分别验证 Wi‑Fi/蜂窝 DNS、磁盘空间、后台进程回收和前台服务恢复。
- 保持桌面端改动仅限已有模型配置复制/回写能力，不增加本地会话协议、数据库或模型代理。
