# Cyrene 远程桌面插件完整设计与实施计划

> 状态：产品方案已确认；Cyrene PluginPack、Workbench/Electron 宿主适配和自动化测试已实现。平台原生 FreeRDP Sidecar 作为独立发布制品按下述契约探测和装载，不属于本次 Plugin 源码实现范围。
>
> 归属：Project Notes；作为后续设计、实现和验收的基线记录。
>
> 目标读者：后续负责 Cyrene Plugin、Workbench、远程连接、Electron、原生 Sidecar、平台 Agent、测试与发布的开发者。
>
> 范围：通过可选 PluginPack 为已经配对的 Cyrene 远程设备提供桌面画面、用户键鼠控制、系统登录、双向音频、多显示器切换、剪贴板和文件传输；Agent 第一阶段只能查看画面。

## 0. 当前实施快照

本文件仍是完整产品与架构基线；以下记录当前仓库已经落地的 Plugin 范围，避免后续把设计目标和已签名发布制品混为一谈。

| 范围 | 当前状态 |
|---|---|
| `cyrene_remote_desktop` PluginPack、依赖声明、Frontend View、Agent 工具 | 已实现 |
| 动态设备集合、点击替换全部 Pane、拖动分屏、关闭恢复布局 | 已实现 |
| 通用 Pane 设置贡献、四档画质、会话态显示约束 | 已实现 |
| 布局 scope/revision/origin 授权、切换撤销、Agent 自授权拒绝、主 Agent 只读取帧 | 已实现 |
| 统一资源观察流光、引用计数和 reduced-motion | 已实现 |
| Electron 当前桌面 WebRTC、键鼠、系统音频、显示器切换 | 已实现并按平台能力与设备授权降级；Wayland 未配置原生输入桥时明确为只读 |
| 当前桌面麦克风回传 | WebRTC 上行与启停清理已实现；仅在配置 `CYRENE_REMOTE_DESKTOP_MIC_SINK_ID` 虚拟输入端点时发布能力，不把普通扬声器播放伪装成麦克风注入 |
| 文本、图片、文件/目录剪贴板与现有加密文件引擎复用 | 已实现；带大小、数量、路径、哈希和 TTL 边界 |
| 一次性系统登录凭据窗口与内存 Credential Broker | 已实现 |
| 已授权设备直接连接、持续状态窗口、紧急断开与 30 秒防立即重连 | 已实现；不再要求被控端逐次批准 |
| 当前桌面 WebRTC 断线重连、短期 TURN REST 凭据 | 已实现；生产 TURN 服务部署和公网矩阵仍待验收 |
| secure surface | 已接入宿主锁屏状态、实时通知与轮询，Agent 取帧会拒绝；UAC/登录安全桌面的完整识别仍依赖原生 Provider/RDP Sidecar |
| FreeRDP Sidecar 进程协议、能力探测、凭据管道和打包查找 | 已实现 |
| RDP 端口发现、监听诊断、非 RDP 占用识别和 Sidecar 动态本地端口 | 已实现 |
| Windows/Linux FreeRDP 原生二进制、签名、SBOM 和平台认证 | 外部发布制品；缺失时插件明确显示 `unsupported`，不会伪装为可用 |
| Windows/macOS/Linux 真实设备发布矩阵 | 仍需使用正式 Sidecar 制品完成发布验收；当前仓库不能据此宣称全平台端到端已验证 |
| 本次集中自动化验证 | 10 个 Electron/Plugin JavaScript 文件语法通过；App Use 输入桥 45/45、Remote Desktop/Remote 控制/Plugin/Workbench 分屏相关 Python 测试 484/484 通过 |

本次实现遵守“远程桌面业务只属于插件”的边界。Workbench Core 仅增加可复用的集合工具、Pane 菜单、用户手势来源和资源观察宿主能力；`cyrene_remote` 仅增加声明式 capability、远程命令分发和受限文件 Scope，没有在核心层复制远程桌面业务。

当前 Electron 回退实现已经支持双向图片/文件剪贴板，但 Chromium/Electron 不提供跨平台的系统剪贴板 delayed-rendering API，因此“远端复制、控制端在其他本机应用粘贴”方向会在收到 offer 后立即走现有文件通道取回内容。发布用原生 Sidecar 仍需实现按系统粘贴请求延迟取回，才能满足下文的最终时机目标；不得为追求延迟传输而绕过现有文件通道。

当前可验证结论是：插件注册、设备卡片、点击/拖动分屏、会话授权、WebRTC 协商、当前桌面宿主、键鼠生命周期、音频能力降级、显示器切换、剪贴板文件通道、Agent 只读观察和流光状态在仓库级链路上已经闭合。完整发布可用性仍有三个外部前置条件：打包并签名 `cyrene-freerdp-sidecar`，在目标设备部署可用的 RDP/桌面与音频组件，以及为跨复杂网络连接配置 STUN/TURN。任一前置条件缺失时，插件会返回明确的 `unsupported`/诊断状态，不会静默回退成一个看似成功但不可操作的会话。

## 1. 目标

在 Cyrene 已有远程设备配对、端到端加密控制、项目授权和文件传输能力之上，增加一个独立的远程桌面插件。用户应能从 Workbench 左侧“工具”区域展开远程设备列表，通过点击或拖动设备卡片打开标准 Pane，并在 Pane 中完成连接、登录、查看和控制。

第一阶段必须满足：

- 被控端覆盖 Windows、macOS 和 Linux。
- Windows/Linux 支持登录前的远程图形登录；macOS 不承诺 FileVault 启动前解锁。
- 支持接管当前桌面和登录远程桌面两种会话语义。
- 支持远端系统音频和控制端麦克风回传。
- 支持发现并切换显示器；第一阶段一次只渲染一个显示器。
- 支持用户鼠标、键盘、组合键和中文输入。
- 支持文本、图片和文件剪贴板。
- 图片与文件剪贴板复用 Cyrene 已有文件通道，并延迟到实际粘贴时传输。
- 普通文件传输继续使用现有 `RemoteCyreneFiles`，不启用 RDP Drive Redirection。
- Agent 只能查看与其对话卡片处于同一 Pane Workspace 的远程桌面，不能建立连接、操作输入、切换显示器、使用音频或访问剪贴板。
- Agent 实际查看远程画面时，Workbench Pane Host 必须显示统一流光特效。
- 远程桌面必须遵循 Cyrene 现有 Pane 点击、拖动、移动、关闭、恢复和拉手菜单逻辑。

## 2. 已确认的产品决策

以下决策已经确认，实现时不应重新发散：

| 主题 | 已确认决策 |
|---|---|
| 交付形式 | 新增独立可选 `cyrene_remote_desktop` PluginPack，依赖现有 `cyrene_remote`。 |
| 被控平台 | Windows、macOS、Linux。 |
| 控制平台 | 第一阶段只做 Cyrene 桌面端，暂不考虑 iOS/Android。 |
| 连接模式 | 同时提供“接管当前桌面”和“登录远程桌面”。 |
| Linux 外部协议 | Ubuntu/GNOME 优先系统原生 RDP；通用 Linux 使用 xrdp；Wayland 当前桌面接管使用 Portal/PipeWire/libei 补充。 |
| Windows | RDP 用于远程登录；当前桌面接管使用平台 Agent。 |
| macOS | 使用原生屏幕、音频和输入 API；接受 FileVault 重启后无法通过普通远程桌面解锁。 |
| RDP 客户端 | 使用 FreeRDP 原生 Sidecar，不把不完整的浏览器 RDP 实现作为第一阶段核心。 |
| 网络 | 现有 E2EE WSS 用于控制面；实时通道优先 WebRTC P2P，失败走 TURN；不暴露远端 3389。 |
| 用户能力 | 画面、鼠标、键盘、远端系统音频、麦克风回传、显示器切换、剪贴板和文件传输。 |
| Agent 能力 | 第一阶段只查看画面，不听音频、不转写音频、不操作输入、不切换显示器、不访问剪贴板。 |
| Agent 授权 | 只要对话卡片与远程桌面卡片处于同一个 Pane Workspace，该对话的主 Agent 就可查看；离开布局立即撤销。 |
| Subagent | 第一阶段不继承远程桌面查看能力。 |
| 查看反馈 | Agent 每次实际取帧时由 Pane Host 显示统一流光和“Agent 正在查看”状态。 |
| 工具入口 | 左侧“工具 → 远程桌面”展开设备卡片列表。 |
| Pane 归属 | 远程桌面属于项目级 Pane Workspace，不绑定当前对话；对话仅在被放入同一布局时获得查看关系。 |
| 点击卡片 | 使用现有 `replaceWorkspace` 语义：保存当前布局，远程桌面替换全部 Pane 卡片并成为唯一 Pane。 |
| 拖动卡片 | 使用现有 Plugin View 拖放和落点规则，不清空其他卡片。 |
| 关闭卡片 | 结束该卡片拥有的远程会话，并恢复点击前保存的 Pane 布局。 |
| 画质设置 | 在统一分屏拉手菜单的“设置”子菜单中选择自动、流畅、均衡、清晰。 |
| 多显示器 | 第一阶段单屏渲染、随时切换；不同时拼接多块显示器。 |
| 文本剪贴板 | 支持双向文本剪贴板。 |
| 图片剪贴板 | 支持双向图片剪贴板，图片内容通过现有文件通道传输。 |
| 文件剪贴板 | 支持双向文件/目录剪贴板，内容通过现有文件通道传输。 |
| 剪贴板传输时机 | 复制时只交换元数据，用户实际粘贴时才传输图片或文件内容。 |
| 普通文件传输 | 复用现有 `RemoteCyreneFiles`，不通过 RDP 文件通道或剪贴板手工编码。 |
| 凭据 | 密码仅在内存中存在，断开后销毁；第一阶段不提供“记住密码”。 |
| 重启恢复 | 恢复卡片和布局，但不自动重新登录；用户显式点击后重连。 |
| 录像 | 第一阶段不录制或持久化远程画面、音频和麦克风内容。 |
| 连接授权 | 已配对设备获得对应远程桌面 capability 后直接连接，不再弹出被控端逐次批准窗口。 |

## 3. 明确不做

- 不让 Agent 建立、批准、断开或抢占远程桌面连接。
- 不给 Agent 提供鼠标、键盘、剪贴板、音频、麦克风或显示器切换工具。
- 不让 Agent 通过 UI 自动化把自己的对话拖入分屏后完成自我授权。
- 不支持移动端控制器或移动端被控主机。
- 不保证 macOS FileVault 预启动环境可远程解锁。
- 不在第一阶段同时显示或拼接多个显示器。
- 不支持多人同时控制；一台被控设备同一时刻只有一个控制会话。
- 不允许静默抢占已有控制者。
- 不将 RDP 3389/3390 直接暴露到公网。
- 不使用 RDP Drive Redirection 传输普通文件或剪贴板文件。
- 不把密码、验证码、截图、音频或剪贴板内容写入普通日志、聊天文本或审计正文。

## 4. 术语与会话模式

### 4.1 接管当前桌面

连接被控设备当前可见的用户桌面。本地显示器和远端用户看到同一工作区，适用于远程协助、共同排障和接管已经登录的会话。

特点：

- 显示器列表代表物理显示器或当前桌面暴露的实际输出。
- 切换显示器不会创建新的系统用户会话。
- 已配对设备获得对应 capability 后直接连接；连接期间持续显示状态窗口并提供紧急断开。
- Windows/macOS/Linux Wayland 的实现分别由平台 Provider 负责。

### 4.2 登录远程桌面

通过操作系统远程桌面服务进入登录界面或独立图形会话。

特点：

- Windows 和 Linux 主要使用 RDP。
- 显示器是 RDP 协商得到的虚拟显示器，不保证对应物理输出。
- 登录凭据由 Cyrene 宿主安全窗口收集，并以一次性句柄交给 Sidecar。
- xrdp 通常创建或恢复独立 Xorg/Xvnc 会话，不等价于物理控制台接管。

## 5. 总体架构

```text
Cyrene Workbench
├── 左侧工具集合
│   └── 远程桌面设备卡片
├── Pane Workspace
│   └── Remote Desktop Plugin View
├── 通用 Pane 拉手菜单与流光状态
└── Agent 只读工具
        │
        ▼
cyrene_remote_desktop PluginPack
├── Application Service / Route / Frontend RPC
├── Session Registry
├── Pane Layout Grant Service
├── Credential Broker
├── Audit / Diagnostics
├── Controller Sidecar Manager
└── Host Provider Manager
        │
        ├── 复用 cyrene_remote
        │   ├── 设备身份与配对
        │   ├── E2EE 控制信封
        │   ├── 设备授权
        │   ├── 远程事件
        │   └── RemoteCyreneFiles
        │
        ├── 实时连接面
        │   ├── ICE / STUN / TURN
        │   ├── WebRTC MediaTrack
        │   └── WebRTC DataChannel
        │
        └── 平台运行时
            ├── Controller FreeRDP Sidecar
            ├── Windows Host Agent
            ├── Linux Host Agent
            └── macOS Host Agent
```

职责边界：

- `cyrene_remote` 继续拥有可信设备、配对、公钥、E2EE 控制面和文件通道。
- `cyrene_remote_desktop` 拥有桌面会话、媒体协商、凭据代理、Pane、Agent 查看和平台 Provider。
- Workbench Core 只扩展通用的“可展开工具集合”“Pane 菜单贡献”“用户手势来源”和“资源正在被 Agent 观察”机制，不硬编码远程桌面业务。
- 原生 Sidecar/Host Agent 负责高权限或高吞吐工作；Python Plugin 不直接承担实时像素和输入循环。

## 6. PluginPack 与运行时包装

建议目录边界：

```text
src/cyrene/plugins/builtin/cyrene_remote_desktop/
├── __init__.py
├── application.py
├── capabilities.py
├── commands.py
├── credentials.py
├── diagnostics.py
├── events.py
├── grants.py
├── providers.py
├── routes.py
├── schemas.py
├── sessions.py
├── tools/
│   ├── list_sessions.py
│   └── inspect.py
└── ui/
    ├── index.html
    ├── remote-desktop.js
    └── remote-desktop.css

native/remote-desktop/
├── controller-sidecar/
├── host-common/
├── host-windows/
├── host-linux/
└── host-macos/
```

实际原生目录可以根据现有打包流水线调整，但必须保持以下原则：

- PluginPack 是用户看到的安装和启用单位。
- 原生运行时是该 PluginPack 的声明式依赖，不是另一个需要用户理解的产品。
- 控制端和被控端都要启用兼容版本的插件。
- 原生组件必须有平台、架构、版本、校验和和签名元数据。
- 插件禁用或卸载时必须停止服务并撤销后台监听，不留下可连接的孤儿进程。

## 7. 平台能力矩阵

| 平台/模式 | 画面 | 用户输入 | 系统音频 | 麦克风回传 | 登录前连接 | 显示器切换 |
|---|---:|---:|---:|---:|---:|---:|
| Windows RDP 登录 | 是 | 是 | 是 | 是 | 是 | RDP 虚拟显示器 |
| Windows 当前桌面 | 是 | 是 | 是 | 是 | 否；安全登录走 RDP | 物理显示器 |
| Ubuntu 24.04+ GNOME RDP | 是 | 是 | Provider 探测/补充 | Provider 探测/补充 | Remote Login 模式支持 | Provider 暴露的显示器 |
| 通用 Linux xrdp | 是 | 是 | 需要音频模块 | 需要音频模块 | 是 | RDP 虚拟显示器 |
| Linux Wayland Portal | 是 | 是 | PipeWire | PipeWire/虚拟源 | 否 | 用户授权的物理显示器 |
| macOS 当前桌面 | 是 | 是 | 是 | 是 | 不承诺 FileVault 预启动 | 物理显示器 |

初始验证等级建议：

- Tier 1：Windows 11、macOS 14+、Ubuntu 24.04 LTS GNOME。
- Tier 2：Windows Server 2022+、Windows 10 22H2、Debian 系 xrdp、Fedora 系 Wayland。
- 其他现代 Linux 发行版按 Provider 能力探测展示为 `supported`、`degraded` 或 `unsupported`，不能假装完全兼容。

最终最低版本应在原型通过后写入正式支持矩阵；这里的 Tier 是实施和验证优先级，不是发布承诺。

## 8. Provider 接口

平台差异必须收敛到稳定 Provider SPI，Route、工具和 React 不判断具体操作系统：

```python
class RemoteDesktopProvider(Protocol):
    async def probe(self) -> ProviderDescriptor: ...
    async def prepare(self, request: PrepareRequest) -> PrepareResult: ...
    async def connect(self, request: ConnectRequest) -> DesktopSession: ...
    async def reconnect(self, session_id: str) -> DesktopSession: ...
    async def disconnect(self, session_id: str) -> None: ...
    async def list_displays(self, session_id: str) -> list[DisplayDescriptor]: ...
    async def select_display(self, session_id: str, display_id: str) -> None: ...
    async def set_quality(self, session_id: str, mode: str) -> None: ...
    async def set_microphone(self, session_id: str, enabled: bool) -> None: ...
    async def snapshot(self, session_id: str, region: Region | None) -> Snapshot: ...
```

能力状态不得只有布尔值，至少支持：

- `supported`
- `unsupported`
- `degraded`
- `permission_required`
- `component_missing`
- `unknown`

能力快照包含 Provider 版本、OS、桌面环境、RDP 服务版本、音频后端、显示服务器和权限状态。运行时升级或环境改变后必须重新探测。

## 9. 各平台后端

### 9.1 Windows

远程登录：

- 使用系统 RDP 服务。
- Controller Sidecar 使用 FreeRDP、TLS、NLA/CredSSP 和 RDP 虚拟通道。
- RDP 只绑定本机或受防火墙保护的接口；Cyrene Host Agent 只允许通过已授权隧道访问。
- 登录、锁屏、断线重连和虚拟显示器由 RDP Provider 管理。

当前桌面：

- 优先 Windows Graphics Capture；需要时使用 Desktop Duplication 作为兼容回退。
- 用户输入使用受控的系统输入注入路径。
- 系统音频使用 WASAPI Loopback。
- 麦克风回传在 RDP 模式使用音频输入重定向，在原生模式使用 WebRTC 音轨和受控音频端点。
- 安全桌面、UAC 和登录界面不得通过普通用户态捕获/注入绕过；需要该能力时切换到 RDP 登录语义。

### 9.2 Linux / Ubuntu

选择顺序：

1. Ubuntu 24.04+ GNOME：优先 `gnome-remote-desktop`。
2. 通用登录会话：`xrdp + xorgxrdp`，必要时回退 Xvnc。
3. 当前 Wayland 会话：XDG RemoteDesktop Portal + ScreenCast/PipeWire + libei/EIS。

要求：

- Provider 必须明确报告是“当前桌面”还是“独立远程会话”。
- xrdp 音频需要检测并安装 PulseAudio/PipeWire 对应模块；缺失时展示可修复诊断，不静默无声。
- Portal 模式尊重桌面环境的授权窗口和 restore token 语义，不伪造永久无人值守能力。
- systemd Host Agent 只暴露已声明的桌面服务，不提供任意 TCP 转发。
- Linux 输入、剪贴板和显示器坐标必须同时考虑 X11、Wayland 和缩放比例。

### 9.3 macOS

- 画面和系统音频使用 ScreenCaptureKit。
- 输入使用受系统辅助功能授权约束的 Core Graphics 事件路径。
- 麦克风通过系统媒体权限采集并建立 WebRTC 音轨。
- 安装流程逐项检查屏幕录制、辅助功能和麦克风权限。
- Host Agent 使用合适的 LaunchDaemon/用户态 Agent 拆分，系统服务不假设能直接访问用户 WindowServer。
- FileVault 预启动环境不在普通远程桌面承诺内；卡片应明确显示限制，而不是无限重试。

## 10. RDP Controller Sidecar

FreeRDP Sidecar 负责：

- TLS/NLA/CredSSP 连接。
- 图形更新解码和帧缓冲。
- RDPSND 系统音频输出。
- RDPEAI 麦克风输入。
- 键盘、鼠标、Unicode 输入和组合键。
- RDP Display Control、多显示器和动态分辨率。
- 自动重连、证书校验和稳定错误映射。

Sidecar 不直接创建独立原生窗口。它通过受认证的本机通道向 Plugin View 提供统一 MediaStream：

```text
Remote RDP Server
        ▲
        │ localhost RDP bridge on controlled host
        │
Reliable ordered WebRTC DataChannel
        │
Controller FreeRDP Sidecar
├── decoded framebuffer ──► local WebRTC VideoTrack ──► Plugin Pane
├── decoded audio ────────► local WebRTC AudioTrack ──► Plugin Pane
├── user input ◄────────── local control channel ◄──── Plugin Pane
└── microphone ◄────────── local audio track ◄──────── Plugin Pane
```

这样可以避免跨平台嵌入 HWND/NSView/X11 子窗口，并让 RDP 和原生 Provider 在前端共享同一个视频、音频和输入表面。

实施原型必须测量本地二次编码的 CPU、GPU、延迟和文本清晰度。如果成本过高，可以在保持 Pane API 不变的前提下优化为损伤矩形、共享纹理或直接传递兼容压缩帧。

## 11. 网络与隧道

### 11.1 控制面

继续使用 `cyrene_remote` 当前的：

- Ed25519 设备签名身份。
- X25519 密钥协商。
- ChaCha20-Poly1305 E2EE 信封。
- 设备指纹、配对、能力和项目授权。
- 审计、幂等键和 typed command。

控制面只承担：

- Provider 能力探测。
- 创建/批准/拒绝会话。
- ICE、STUN、TURN 协商。
- 短期会话令牌。
- 状态、错误和审计事件。
- 文件通道命令。

不得通过 JSON/base64 E2EE 信封持续发送视频、音频或高频鼠标事件。

### 11.2 实时数据面

- 优先 ICE 直连。
- 直连失败自动使用 TURN。
- 原生桌面使用 SRTP MediaTrack 传输视频和音频。
- 输入使用低延迟 DataChannel。
- RDP 字节流使用可靠、有序 DataChannel。
- TURN 凭据应短期签发，不把长期密钥放入 Plugin iframe。
- 所有会话都绑定设备 ID、控制者 ID、会话 ID、过期时间和能力集合。

### 11.3 RDP 端口边界

- 不在公网开放 3389/3390。
- Host Agent 的 RDP bridge 只连接受控本机地址和已允许端口。
- Windows 从 `RDP-Tcp/PortNumber` 读取实际监听端口；GNOME Remote Desktop 优先读取 `org.gnome.desktop.remote-desktop.rdp port`，其他 Linux 从 xrdp `[Globals] port` 读取，缺失时才回退 3389；部署环境可用 `CYRENE_RDP_PORT` 显式覆盖。
- 目标端口已由预期 RDP 服务监听是正常状态，不得当作冲突；协议握手发现该监听者不是 RDP 服务时返回 `rdp_port_occupied_by_other_service`，不得杀进程或自动改写系统端口。
- Sidecar/bridge 自己需要的本机临时监听端口必须绑定 `127.0.0.1:0`，由操作系统选择空闲端口，避免与 3389 或其他 Cyrene 实例竞争。
- 配置端口没有监听时返回 `rdp_service_not_listening`；本地动态端口分配失败返回可重试的 `rdp_local_port_allocation_failed`，两者不得合并成笼统网络错误。
- 客户端不能借助桌面隧道访问任意内网 TCP 目标。
- RDP 服务证书必须校验并支持指纹固定；第一次信任由用户确认。
- RDP 自身 TLS/NLA 与 Cyrene 隧道形成纵深防御，不因外层加密而关闭内层校验。

## 12. 设备配对与能力

建议新增能力：

```text
desktop:session_connect
desktop:current_session
desktop:remote_login
desktop:screen_view_user
desktop:screen_view_agent
desktop:input_user
desktop:input_agent             # 预留，V1 永不授予
desktop:display_list
desktop:display_select_user
desktop:display_select_agent    # 预留，V1 永不授予
desktop:audio_output_user
desktop:audio_input_user
desktop:audio_agent             # 预留，V1 永不授予
desktop:clipboard_text_user
desktop:clipboard_image_user
desktop:clipboard_file_user
desktop:clipboard_agent         # 预留，V1 永不授予
```

权限同时包含两层：

1. 配对设备长期授予的能力。
2. 当前桌面会话从配对能力派生的细粒度权限。

前端隐藏按钮不能替代后端能力检查。任何未授权输入、音频、显示器或剪贴板帧都必须在 Sidecar/Host Agent 边界再次拒绝。

## 13. 从工具进入

Workbench 左侧“工具”增加通用可展开集合：

```text
工具
├── 文件
├── 终端
└── 远程桌面  ▾
    ├── Ubuntu 工作站      在线
    ├── Windows PC         已连接
    └── MacBook Pro        离线
```

“远程桌面”标题只负责展开/收起。设备卡片显示：

- 设备名称、OS 和架构。
- 在线、离线、连接中、已连接、需要修复。
- Host Agent 和桌面 Provider 是否就绪。
- 可用模式：当前桌面、远程登录。
- 显示器数量、音频和麦克风能力摘要。
- 已有会话是否可以恢复。

不要在 `rail.jsx` 硬编码远程设备 API。应扩展通用 `project_tools`/Plugin View 元数据，使 Plugin 可以贡献动态卡片集合，例如：

```json
{
  "id": "remote_desktop",
  "presentation": "collection",
  "title": "远程桌面",
  "items_method": "remoteDesktop.cards.list",
  "view": "main"
}
```

每张设备卡片使用 `device_id` 作为稳定 `instanceId`，通过统一 Plugin View Drag MIME 进入 Pane 系统。

## 14. 点击、拖动与 Pane 生命周期

### 14.1 点击设备卡片

严格复用 `replaceWorkspace`：

```text
用户点击设备卡片
    → 保存当前 Pane Layout 作为 restoreLayout
    → 创建/复用 Remote Desktop Pane Card
    → left = [remoteDesktopCard], right = []
    → 启动连接状态机
```

如果同一设备卡片已经存在，则聚焦现有卡片，不创建第二个连接。

### 14.2 拖动设备卡片

- 使用现有 Plugin View drag payload。
- 使用统一左右、上下和卡片替换落点。
- 拖入布局不会清空其他卡片。
- 在布局内移动保持同一 session/card identity，不重新登录。
- 不增加远程桌面专属拖放区域。

### 14.3 关闭

- 关闭 Remote Desktop Card 时终止连接。
- 取消麦克风采集、媒体轨道、DataChannel、凭据句柄和临时授权。
- 清理或计划清理剪贴板临时目录。
- 如果卡片来自点击替换，恢复保存的旧布局。
- 应用重启只恢复卡片描述，不自动登录；卡片显示“重新连接”。

## 15. 连接状态机

```text
idle
  → probing
  → needs_component / needs_permission / ready
  → selecting_mode
  → waiting_credentials（仅系统登录）
  → gathering_ice
  → connecting_direct
  → connecting_turn（直连失败）
  → authenticating_rdp / starting_native_session
  → connected
  → reconnecting
  → connected | failed | disconnected
```

要求：

- 每个状态有稳定代码、可本地化标题、说明和可操作恢复动作。
- 用户取消凭据、系统权限或麦克风提示必须是正常取消，不记录为崩溃。
- 凭据错误允许有限重试，并避免触发账户锁定风暴。
- 网络重连直接复用配对设备能力，但凭据句柄失效后必须重新输入。
- 第二个控制者在现有会话断开前直接拒绝，避免静默抢占。

## 16. 安全凭据流程

Plugin iframe 不直接接触密码：

```text
Plugin View 请求登录
    → Workbench Host 打开安全凭据窗口
    → 用户输入用户名/域/密码
    → Host 将密码交给 Credential Broker
    → Broker 生成一次性 credential_handle
    → Sidecar 使用句柄读取凭据
    → 前端只收到成功/取消/失败
    → 连接完成或断开后销毁句柄和内存
```

规则：

- 密码字段关闭自动补全、遥测、历史记录和普通异常回显。
- API、日志、tool result、SSE、审计和 Plugin `postMessage` 不得包含密码。
- 第一阶段可保存用户名、域、模式和显示偏好，不保存密码。
- 登录界面、验证码、UAC、安全桌面或被 Provider 标记为 `secure_surface` 时，Agent 只能收到遮罩帧和状态。

## 17. 远程桌面 Pane

建议结构：

```text
┌──────────────────────────────────────────────────────┐
│ 设备 ▾  模式  显示器 ▾     🔊 音量  🎤 麦克风        │
├──────────────────────────────────────────────────────┤
│                                                      │
│                    远程桌面画面                      │
│                                                      │
├──────────────────────────────────────────────────────┤
│ 文件传输  剪贴板状态  连接质量  全屏  断开           │
└──────────────────────────────────────────────────────┘
```

行为：

- 视频按当前 Pane 尺寸等比适配，默认不拉伸。
- 点击画面后捕获键盘；`Esc` 释放捕获。
- 提供系统组合键菜单，包括 `Ctrl+Alt+Delete` 等不能由浏览器直接转发的按键。
- 麦克风默认关闭；开启后显示持续、明显的红色状态。
- 显示器插拔后刷新列表；当前显示器消失时回退主显示器。
- 切换显示器不停止系统音频和麦克风。
- 连接质量状态显示分辨率、帧率、估算码率、时延和 P2P/TURN。
- 文件传输入口调用现有远程文件服务，并使用现有进度事件。

## 18. 通用分屏拉手菜单扩展

现有 `WbcSplitGripBar` 不应出现 `if remote-desktop`。需要增加通用 Pane Card Menu Contribution，例如：

```ts
type PaneMenuContribution = {
  id: string
  label: string
  icon?: string
  kind: "action" | "submenu" | "radio-group"
  items?: PaneMenuContribution[]
  selectedValue?: string
  invoke?: (value?: string) => void
}
```

远程桌面卡片贡献：

```text
拉手菜单
├── 新建对话
├── 移到另一侧
├── 设置  ›
│   └── 画质模式
│       ├── 自动       ✓
│       ├── 流畅
│       ├── 均衡
│       └── 清晰
└── 关闭分屏
```

要求：

- 单选项使用标准 `menuitemradio` 和 `aria-checked`。
- 键盘可以打开子菜单、上下选择、确认和返回。
- 菜单关闭、拖动、窗口失焦和卡片卸载时清理状态。
- 其他 Plugin/资源以后可以使用同一接口贡献设置。
- 设置更新进入卡片状态和 Plugin Application Service，不由 Workbench 解释画质含义。

## 19. 画质模式

| 模式 | 目标行为 |
|---|---|
| 自动 | 根据带宽、RTT、丢包、抖动、画面变化和解码负载动态调整；默认模式。 |
| 流畅 | 优先交互延迟和帧率，必要时降分辨率、码率和细节。 |
| 均衡 | 尽量保持可读文字，在帧率与码率之间平衡。 |
| 清晰 | 优先原生分辨率与文字细节，允许更高码率和较低帧率。 |

规则：

- 切换立即生效，不重新创建登录会话。
- 按设备保存最近选择；新设备默认为自动。
- 切换显示器保留选择。
- 即使选择清晰，也保留避免连接失效的最低自适应保护。
- Agent 截图来自用户当前真实渲染帧，不提供隐藏的更高清旁路。

建议初始目标：

| 场景 | 目标 |
|---|---|
| 普通办公 | 1080p 60fps |
| 4K | 4K 30fps |
| 弱网 | 可自动降至 720p/15fps |
| 局域网交互延迟 | 目标低于 100ms |
| TURN 连接交互延迟 | 目标低于 250ms |
| 断线恢复 | 目标 5 秒内恢复或给出明确失败 |

这些是验收目标，不应作为所有硬件和网络环境下的绝对保证。

## 20. 显示器模型

统一描述：

```json
{
  "id": "display-2",
  "name": "Dell U2723QE",
  "width": 3840,
  "height": 2160,
  "scale": 2.0,
  "rotation": 0,
  "primary": false,
  "kind": "physical"
}
```

命令：

```text
display.list
display.select(display_id)
display.changed
```

坐标规则：

- 用户输入坐标始终相对于当前选中显示器。
- Provider 负责转换为物理像素、逻辑像素或 RDP 虚拟桌面坐标。
- 当前桌面模式列出物理/Portal 输出。
- 远程登录模式列出 RDP 虚拟显示器。
- 第一阶段只将一个显示器送入 Pane；不生成跨屏拼接坐标系。
- Agent 只能查看用户当前选中的显示器，不能指定另一个显示器。

## 21. 用户输入

- 鼠标支持移动、按下、释放、双击、滚轮和常用扩展按钮。
- 键盘支持物理键、Unicode 文本、修饰键和组合键。
- 中文输入优先通过 Unicode/text composition 路径，不能简单假设 US 键盘扫描码。
- 输入捕获时必须有可见状态和明确退出方式。
- 本地紧急断开快捷键优先于远端输入。
- 卡片失焦、窗口失焦、连接中断或权限撤销时释放所有按键和鼠标按钮，避免远端出现“卡键”。
- Agent 输入 capability 仅预留协议字段，第一阶段不注册工具、不签发令牌、不处理事件。

## 22. 音频与麦克风

### 22.1 远端系统音频

- Windows/Linux RDP 优先使用标准 RDP Audio Output。
- 原生当前桌面 Provider 使用平台系统音频捕获。
- 前端使用统一 AudioTrack 和音量/静音控制。
- 切换显示器不影响系统音频。
- 网络恶化时可以独立降低音频码率，不因视频降级而完全中断。

### 22.2 麦克风回传

- 默认关闭。
- 首次开启触发控制端系统麦克风权限。
- 每次会话需要用户显式开启；应用重启不自动恢复。
- RDP 模式使用音频输入重定向；原生模式使用 WebRTC 音轨和 Provider 受控输入端点。
- 开启期间 Pane 持续显示红色麦克风状态。
- 卡片关闭、连接断开或应用进入安全状态时立即停止采集。

### 22.3 Agent 边界

- Agent 工具结果不包含音轨、音频片段、频谱或转写。
- 不在后台自动调用语音识别。
- `desktop:audio_agent` 保留但第一阶段永不授予。

## 23. 剪贴板

### 23.1 文本

- 支持双向 Unicode 文本。
- 限制单次大小并对超限内容给出明确提示。
- 用户可以在 Pane 中暂时关闭剪贴板同步。
- Agent 不可读取或写入文本剪贴板。

### 23.2 图片

复制时：

- 只交换类型、尺寸、估算大小和一次性 clipboard offer ID。
- 不立即上传像素。

粘贴时：

- 请求 offer 对应内容。
- 规范化为 PNG 托管文件。
- 使用现有文件通道分块、校验、断点续传和原子提交。
- 目标 Host Agent 将完成后的 PNG 写入原生图片剪贴板，再发送粘贴。

### 23.3 文件与目录

复制时只同步清单：

- 文件名和相对路径。
- 文件/目录类型。
- 大小、修改时间和必要权限。
- offer ID，不包含可供远端直接访问的本地绝对路径。

粘贴时：

- 通过现有上传/download/sync 原语传输。
- 保持目录层级和多文件顺序。
- 传输完成前不向目标应用发送最终粘贴事件。
- 失败或取消时保留 offer，允许重新粘贴和断点续传。

### 23.4 内部沙箱 Scope

现有 `RemoteCyreneFiles` 是项目导向的高层工具。桌面剪贴板必须复用其底层安全传输和校验实现，但增加内部、受限 Scope：

```text
remote_desktop_clipboard/<session_id>/<transfer_id>/
```

约束：

- Scope 只允许当前桌面会话双方访问。
- 不能通过 clipboard payload 指定任意目标绝对目录。
- 每个文件和整个 offer 都有大小、数量和生命周期限制。
- 断开后按 TTL 清理未被应用接管的临时内容。
- 审计记录方向、文件数量、总字节数和结果，不记录内容或完整敏感路径。

## 24. 普通文件传输

Pane 中的“发送文件”“接收文件”“浏览远程文件”调用现有 `RemoteCyreneFiles`：

- `files.upload.begin/chunk/commit/abort`
- `files.download`
- `files.sync.prepare/diff/apply/commit/abort`
- 已有 SHA-256、断点续传、冲突策略、原子提交和项目/绝对路径授权。

远程桌面插件不得复制这套实现，也不得把大文件转成聊天 JSON、RDP clipboard 或 shell base64。

桌面媒体、普通文件传输和剪贴板传输使用独立队列；大文件不得阻塞视频、音频和输入。

## 25. Pane 布局派生的 Agent 查看授权

### 25.1 授权规则

```text
同一个 Pane Workspace
├── Remote Desktop Card(session S)
├── Chat Card(A)  → A 的主 Agent可查看 S
├── Chat Card(B)  → B 的主 Agent可查看 S
└── File/Terminal → 不产生 Agent 权限
```

还必须同时满足：

- 远程设备已授予 `desktop:screen_view_agent`。
- 会话不是 `secure_surface`，或 Provider 能返回安全遮罩帧。
- Chat Card 的加入来自真实用户手势或用户明确批准的布局变更。
- 调用者是该对话的主 Agent，不是 Subagent、Scheduler 或其他系统调用者。

### 25.2 防止自我授权

Workbench 已有语义 UI 操作可能允许 Agent 打开或移动 Pane。布局授权必须保留 `origin`：

```text
user_pointer
user_keyboard
restored_user_layout
agent_ui_action
system_restore
```

只有 `user_pointer`、`user_keyboard` 和经过用户重新确认的恢复布局能创建新的桌面查看 grant。`agent_ui_action` 不能使调用 Agent 获得权限。

### 25.3 后端关系

```text
desktop_session_id
    → pane_layout_id
        → revision
        → user_granted_chat_ids
```

- 前端每次布局改变提交带 revision 的投影。
- 后端验证卡片、会话和对话均存在，并拒绝旧 revision 回放。
- 工具调用从 `PluginContext` 获取真实 chat/tree/agent 身份，不信任参数中的 chat ID。
- 对话移出布局、远程卡片关闭、session 更换或 grant 撤销后立即禁止新截图。
- 正在执行的取帧在权限撤销时应取消或返回拒绝，不继续交付像素。

## 26. Agent 工具

只增加两个只读、`main_only` 工具。

### 26.1 `ListRemoteDesktopSessions`

用途：列出当前调用对话通过 Pane Layout 获得查看权的会话。

输入：

```json
{
  "type": "object",
  "properties": {},
  "additionalProperties": false
}
```

输出示例：

```json
{
  "sessions": [{
    "session_id": "rds_01...",
    "device_id": "dev_01...",
    "device_name": "Ubuntu Workstation",
    "mode": "current_desktop",
    "state": "connected",
    "display": {
      "id": "display-1",
      "name": "主显示器",
      "width": 2560,
      "height": 1440
    },
    "secure_surface": false,
    "audio_available_to_agent": false
  }]
}
```

### 26.2 `InspectRemoteDesktop`

用途：获取当前选中显示器的一张新鲜截图；可选读取局部区域。

输入草案：

```json
{
  "type": "object",
  "properties": {
    "session_id": {"type": "string"},
    "region": {
      "type": "object",
      "properties": {
        "x": {"type": "integer", "minimum": 0},
        "y": {"type": "integer", "minimum": 0},
        "width": {"type": "integer", "minimum": 1},
        "height": {"type": "integer", "minimum": 1}
      },
      "required": ["x", "y", "width", "height"],
      "additionalProperties": false
    },
    "reason": {"type": "string", "minLength": 1, "maxLength": 300}
  },
  "required": ["session_id", "reason"],
  "additionalProperties": false
}
```

工具返回：

- 通过 Cyrene 托管图片/附件通道交给模型的一张图片，不把大段 base64 放入 JSON。
- `captured_at`、尺寸、display ID、画质模式、是否遮罩。
- 不包含音频、剪贴板、用户按键或其他显示器内容。

稳定错误：

- `no_authorized_desktop_session`
- `desktop_session_not_found`
- `desktop_session_disconnected`
- `desktop_view_permission_revoked`
- `desktop_secure_surface_masked`
- `desktop_snapshot_rate_limited`
- `desktop_snapshot_failed`

第一阶段不注册任何 `connect/click/type/key/display_select/audio/clipboard` Agent 工具。

## 27. 统一 Agent 查看流光

流光是 Workbench 的通用资源观察状态，不是 Plugin iframe 内部动画。

事件生命周期：

```text
resource_observation.started
├── resource_kind = remote_desktop
├── resource_id = session_id
├── pane_card_id
├── chat_id
└── tool_call_id

resource_observation.ended
├── outcome = success | denied | failed | cancelled
└── duration_ms
```

视觉规则：

- `InspectRemoteDesktop` 通过全部权限检查并真正开始取帧时启动。
- Pane Card 外框和 Host Strip 使用统一 Cyrene accent 流光。
- 显示“Agent 正在查看”；多个主 Agent 同时查看时显示数量。
- 多个调用共享一层效果，使用引用计数；最后一个调用结束后播放短暂收尾。
- 工具失败、取消、权限撤销或超时立即结束。
- 只有拥有权限但没有调用工具时不显示。
- `prefers-reduced-motion` 下使用静态高亮边框、眼睛图标和文本，不播放移动渐变。
- 流光必须在 Host 层覆盖沙箱 iframe 边界，并随卡片移动、缩放和恢复布局。

该机制以后可以复用到浏览器、摄像头或其他实时敏感资源。

## 28. 数据模型

建议持久化：

### 28.1 会话元数据

```text
remote_desktop_sessions
├── session_id
├── device_id
├── controller_device_id
├── provider_id
├── mode
├── state
├── pane_card_id
├── pane_layout_id
├── selected_display_id
├── quality_mode
├── transport_kind
├── created_at / connected_at / disconnected_at
└── last_error_code
```

持久化记录只用于状态恢复和诊断；应用重启后会话状态归一为 `disconnected/reconnect_required`。

### 28.2 设备偏好

```text
remote_desktop_device_preferences
├── device_id
├── preferred_mode
├── quality_mode
├── preferred_display_id（仅提示，连接后重新验证）
├── clipboard_enabled
└── updated_at
```

不得持久化：

- 密码或验证码。
- 原始视频帧和截图。
- 音频或麦克风数据。
- 剪贴板内容。
- 长期 TURN 会话凭据。

## 29. Route、Frontend RPC 与事件

建议 API 范围：

```text
GET    /api/remote-desktop/cards
GET    /api/remote-desktop/sessions
POST   /api/remote-desktop/sessions
GET    /api/remote-desktop/sessions/{id}
POST   /api/remote-desktop/sessions/{id}/reconnect
DELETE /api/remote-desktop/sessions/{id}
GET    /api/remote-desktop/sessions/{id}/displays
PUT    /api/remote-desktop/sessions/{id}/display
PUT    /api/remote-desktop/sessions/{id}/quality
PUT    /api/remote-desktop/sessions/{id}/microphone
POST   /api/remote-desktop/sessions/{id}/credentials/request
POST   /api/remote-desktop/layout-grants
GET    /api/remote-desktop/diagnostics/{device_id}
```

所有 mutation 使用严格 Pydantic schema、`extra="forbid"`、稳定错误码和幂等键。高频像素、音频和输入不得走这些 JSON Route。

实时状态事件：

```text
remote_desktop_cards_changed
remote_desktop_session_changed
remote_desktop_displays_changed
remote_desktop_quality_changed
remote_desktop_microphone_changed
remote_desktop_transport_changed
remote_desktop_clipboard_transfer
resource_observation.started
resource_observation.ended
```

事件只含元数据，不含密码、截图、音频或剪贴板内容。

## 30. 审计与隐私

记录：

- 会话申请、连接、重连、占用拒绝和断开。
- P2P/TURN 结果和 Provider 类型。
- 显示器切换。
- 麦克风启停。
- 画质模式变化。
- Agent 查看开始/结束、调用对话和结果。
- 普通文件与剪贴板文件传输方向、数量、字节和结果。
- 权限、凭据、证书和安全表面错误码。

不记录：

- 画面、截图正文和 OCR。
- 系统音频、麦克风或转写。
- 用户按键和密码字段。
- 剪贴板正文、图片或文件内容。
- 完整凭据、TURN secret 或会话密钥。

日志清洗必须覆盖 FreeRDP、平台 API、WebRTC、ffmpeg/codec 和 Sidecar stderr。

## 31. 错误与恢复

主要错误类别：

| 类别 | 示例 | 用户恢复动作 |
|---|---|---|
| 配对/授权 | 能力未授予、设备撤销 | 打开远程设置重新授权 |
| 运行时 | Sidecar 缺失、版本不兼容 | 安装/更新组件 |
| 系统权限 | 屏幕录制、辅助功能、麦克风未授权 | 打开系统设置并重新检测 |
| RDP | 服务未启用、端口被非 RDP 服务占用、NLA/凭据失败、证书变化 | 启用服务、修正端口或占用、重新输入、确认新证书 |
| Linux 后端 | xrdp/xorgxrdp/音频模块缺失 | 执行受审查的安装修复 |
| 网络 | ICE 失败、TURN 不可用 | 检查网络或 TURN 设置 |
| 媒体 | 编码器不可用、帧率过低 | 降级软件编码或画质 |
| 剪贴板 | offer 过期、文件变化、空间不足 | 重新复制/粘贴或释放空间 |
| Agent 查看 | 不在同一布局、权限撤销、安全表面 | 用户调整布局或完成安全输入 |

错误必须明确区分控制端、被控端、Relay/TURN、Provider 和目标 OS 来源。

## 32. 安装、更新与诊断

### 32.1 Windows

- 安装 Host Service 需要管理员权限。
- 检测 RDP 服务、版本、防火墙和策略。
- Controller Sidecar 与 Host Agent 支持 x64/ARM64 目标架构。
- 发布组件签名并验证下载摘要。

### 32.2 Linux

- 使用 systemd 管理 Host Agent。
- 检测桌面环境、X11/Wayland、PipeWire/PulseAudio、Portal、xrdp 和 xorgxrdp。
- 安装系统组件前展示将执行的包管理器操作和权限范围。
- 不用任意 shell string 作为修复接口；使用平台、包和参数的 typed action。

### 32.3 macOS

- 使用签名和公证的原生组件。
- 检查屏幕录制、辅助功能和麦克风授权。
- 明确显示需要退出重开或系统设置操作的状态。
- 区分 LaunchDaemon 与用户 Agent 生命周期。

### 32.4 更新

- Plugin、Controller Sidecar、Host Agent 和协议分别有版本。
- 会话建立前协商最低/最高兼容协议。
- 更新过程中不启动新会话；已有会话给出明确结束/稍后更新选择。
- 回滚只替换该插件原生组件，不影响现有 `cyrene_remote` 设备身份和文件数据。
- 发布前完成 FreeRDP、xrdp 音频模块、codec、WebRTC/TURN 依赖的许可证和 SBOM 审查。

## 33. 实施阶段

### 阶段 0：契约与原型

交付：

- 冻结 capability、session、Provider、事件和错误 schema。
- FreeRDP Sidecar 最小原型：RDP 登录、帧输出、系统音频、麦克风、输入和动态尺寸。
- WebRTC DataChannel TCP bridge 原型。
- Plugin Pane 本地 MediaStream 渲染原型。
- 评估二次编码延迟、CPU/GPU 和文字清晰度。
- Ubuntu GNOME RDP、xrdp、Windows RDP、macOS ScreenCaptureKit 的能力探测样例。

退出条件：核心技术链路可行，未发现需要改变已确认产品交互的阻断问题。

### 阶段 1：Plugin、工具集合与 Pane 基础

交付：

- `cyrene_remote_desktop` PluginPack。
- 动态可展开 Plugin Tool Collection 通用接口。
- 远程设备卡片、在线状态和稳定 `instanceId`。
- 点击 `replaceWorkspace`、拖动、移动、关闭和恢复布局。
- Remote Desktop Pane 的空态、探测态和连接状态机 UI。

### 阶段 2：通用 Pane 菜单、布局授权与流光

交付：

- 通用 Pane Menu Contribution。
- 画质模式单选菜单和设备偏好。
- 带 origin/revision 的 Pane Layout Grant Service。
- `ListRemoteDesktopSessions` 和 `InspectRemoteDesktop`。
- 通用 `resource_observation` 事件与 Host 层流光。
- 安全表面遮罩和 Subagent/Agent 自授权拒绝。

### 阶段 3：连接与安全隧道

交付：

- ICE/STUN/TURN 配置和短期凭据。
- P2P 优先、TURN 回退。
- RDP 可靠有序 DataChannel bridge。
- session capability token、证书固定、重连和接管请求。
- Credential Broker 和 Host 安全凭据窗口。

### 阶段 4：Windows/Linux RDP

交付：

- FreeRDP Sidecar 完整集成。
- Windows 系统登录。
- Ubuntu GNOME RDP 和通用 xrdp 登录。
- RDP 图形、输入、音频输出、麦克风输入和虚拟显示器。
- RDP 安装/服务/证书/凭据诊断。

### 阶段 5：当前桌面 Provider

交付：

- Windows 当前桌面画面、输入和 WASAPI 音频。
- Linux GNOME sharing/Wayland Portal/PipeWire/libei。
- macOS ScreenCaptureKit、输入、系统音频和权限引导。
- 物理显示器发现和热切换。

### 阶段 6：质量、自适应和可靠性

交付：

- 自动、流畅、均衡、清晰策略。
- 带宽、RTT、丢包、抖动和解码负载反馈。
- 硬件编码优先和软件降级。
- 卡键恢复、分辨率变化、显示器插拔、睡眠/唤醒和断线重连。

### 阶段 7：剪贴板与文件集成

交付：

- 双向 Unicode 文本剪贴板。
- 图片 clipboard offer、粘贴时 PNG 传输和原生剪贴板注入。
- 文件/目录清单、粘贴时 transfer/sync 和进度。
- `remote_desktop_clipboard` 内部 Scope、TTL、限制和清理。
- Pane 普通文件按钮复用 `RemoteCyreneFiles`。

### 阶段 8：打包、更新与安全加固

交付：

- Windows/macOS/Linux 原生组件打包和签名。
- 安装、更新、回滚和卸载。
- 系统权限诊断和修复动作。
- 安全审计、日志清洗、SBOM 和依赖审查。
- Feature Flag 和渐进式发布。

## 34. 测试计划

遵循项目测试要求：Python 测试使用 `uv run pytest ...`；实施期间先完成同一阶段的编辑和自审，再运行一次与变更范围相称的集中测试，除非诊断不确定行为确实需要中间测试。

### 34.1 Python/Backend 单元测试

- capability 校验、长期 grant 和 session token。
- Provider 探测归一化。
- session 状态机和稳定错误。
- Credential Broker 生命周期和日志清洗。
- Pane Layout revision/origin 授权和撤销竞态。
- Agent main-only、Subagent、Scheduler 和错误 chat ID 拒绝。
- secure surface 遮罩。
- clipboard Scope、TTL、路径边界、大小/数量限制。
- 现有文件通道断点续传、哈希和原子提交回归。

### 34.2 Workbench/Frontend 测试

- 工具集合展开、设备卡片状态和键盘操作。
- 点击替换所有 Pane 并保存/恢复布局。
- 拖动卡片遵循通用落点，移动时不重连。
- 拉手设置子菜单、radio 语义和画质持久化。
- 流光引用计数、失败结束、多个 Agent 和 reduced-motion。
- 麦克风状态、显示器切换和连接错误卡片。
- Plugin iframe 无法直接读取密码。

### 34.3 Electron 测试

- 本机 Sidecar 启停、认证和崩溃恢复。
- Plugin View WebRTC、音频播放和麦克风权限。
- 窗口切换、Pane 拖动、最小化、睡眠/唤醒。
- 系统安全凭据窗口不把密码发给 iframe。
- 硬件加速可用/不可用回退。

### 34.4 原生测试

- FreeRDP NLA、证书、重连、RDPSND、RDPEAI、输入和 Display Control。
- Windows 当前桌面、多屏、WASAPI、UAC/安全桌面边界。
- Ubuntu GNOME Remote Login/Desktop Sharing。
- xrdp+xorgxrdp、音频模块和会话恢复。
- Wayland Portal 用户批准、restore token、PipeWire/libei。
- macOS ScreenCaptureKit、辅助功能、麦克风和权限变化。

### 34.5 安全测试

- 未配对设备、被撤销设备和过期令牌。
- RDP 隧道访问任意地址/端口的 SSRF/内网转发尝试。
- Agent 通过 UI 语义动作自我加入布局。
- Subagent 直接调用 Inspect。
- 旧 layout revision、伪造 session/chat/card ID。
- secure surface 原始帧泄漏。
- 密码、剪贴板和音频内容进入日志/事件/崩溃报告。
- 剪贴板路径穿越、符号链接、TOCTOU 和超限 payload。
- TURN 凭据重放和跨设备使用。

### 34.6 性能测试

- 1080p60、4K30 和 720p15。
- 静态文本、滚动、视频播放和高变化画面。
- P2P 与 TURN。
- 多小时会话的内存、句柄、线程和音视频漂移。
- 大文件传输时的输入延迟和音视频稳定性。
- 频繁 Agent 截图时的帧率和内存影响。
- 多显示器切换和缩放变化。

### 34.7 人工跨平台矩阵

至少覆盖：

- Windows 控制 Windows/Linux/macOS。
- macOS 控制 Windows/Linux/macOS。
- Linux 控制 Windows/Linux/macOS。
- LAN 直连、双 NAT、TURN、断网恢复。
- 当前桌面、远程登录、锁屏、注销、重启后的重新连接。
- 文本、图片、单文件、多文件、目录和大文件剪贴板双向传输。

## 35. 验收标准

### 35.1 入口与 Pane

- “工具 → 远程桌面”能展开已配对设备卡片。
- 点击设备卡片替换全部 Pane，关闭后恢复旧布局。
- 拖动设备卡片与其他资源使用相同落点、移动和菜单。
- 卡片移动/缩放不造成重连。

### 35.2 连接

- Windows/Linux 能完成远程登录。
- Windows/macOS/Linux 能在支持环境中接管当前桌面。
- 直连失败时能自动使用 TURN。
- 公网不需要开放 RDP 端口。
- 错误提供稳定、可操作的恢复信息。

### 35.3 交互与媒体

- 鼠标、键盘、组合键和中文输入可用。
- 系统音频和麦克风双向可用，麦克风默认关闭且状态持续可见。
- 显示器可发现、热更新和切换，音频不中断。
- 四种画质模式能从拉手菜单选择并即时生效。

### 35.4 Agent 查看

- 同一 Pane Workspace 中由用户加入的每个 Chat 主 Agent 都能列出并查看会话。
- Chat 移出布局后立即失去权限。
- Agent/语义 UI 操作不能自我授权。
- Subagent 不能调用。
- Agent 查看时远程 Pane 显示统一流光；结束、失败和取消后正确消失。
- Agent 不获得音频、输入、显示器切换或剪贴板能力。

### 35.5 剪贴板与文件

- 文本、图片、文件和目录都能双向复制粘贴。
- 图片/文件复制时不立即传输，粘贴时才开始。
- 传输使用现有文件通道的分块、断点续传、哈希和原子提交。
- 普通文件按钮复用 `RemoteCyreneFiles`。
- 文件传输不显著阻塞画面、音频和输入。

### 35.6 安全与隐私

- 密码只存在于内存，不进入 iframe、日志、事件或数据库。
- secure surface 不向 Agent 返回原始帧。
- 不持久化桌面画面和音频。
- 被控端有持续连接状态和紧急断开入口。
- 所有 Sidecar/Host Agent 请求都在原生边界再次验证 capability token。

## 36. 主要风险与缓解

| 风险 | 缓解措施 |
|---|---|
| FreeRDP 帧二次编码增加延迟 | 阶段 0 先量化；保留损伤矩形、共享纹理和直接压缩帧优化接口。 |
| Linux 桌面环境碎片化 | Provider 探测和分级能力；Tier 1 聚焦 Ubuntu GNOME，通用 xrdp 提供登录回退。 |
| Wayland 无人值守受 Portal 限制 | 尊重系统授权；不把 Portal 恢复 token 解释为任意永久控制。 |
| Windows/macOS 安全桌面限制 | 明确切换到 RDP/系统支持模式，不绕过 OS 安全边界。 |
| Pane 布局被 Agent 操作导致自授权 | 布局变更记录可信 origin，后端只接受用户手势 grant。 |
| 剪贴板泄漏敏感文件 | 粘贴时才传输、会话 Scope、TTL、大小限制和明显进度。 |
| 大文件影响实时媒体 | 独立队列和带宽优先级，媒体/输入高于文件传输。 |
| TURN 成本和滥用 | 短期凭据、设备/会话绑定、配额、速率限制和审计。 |
| 原生组件供应链 | 签名、校验和、SBOM、固定版本、许可证审查和可回滚更新。 |
| 密码或媒体进入错误日志 | 统一秘密类型、结构化错误、stderr 清洗和泄漏测试。 |

## 37. 发布策略

建议 Feature Flag：

```text
remote_desktop.enabled
remote_desktop.rdp
remote_desktop.native_capture
remote_desktop.agent_observe
remote_desktop.audio_input
remote_desktop.clipboard_binary
remote_desktop.turn
```

发布顺序：

1. 开发者模式和回环测试。
2. LAN、单平台、已配对设备能力授权后直接连接。
3. Windows/Linux RDP 登录。
4. 三平台当前桌面。
5. TURN 公网连接。
6. 双向音频、多显示器和二进制剪贴板。
7. Agent 只读查看和统一流光。
8. 默认向满足诊断条件的桌面用户开放。

远程桌面插件默认可选安装；诊断不满足的平台不得显示虚假的“已就绪”。

## 38. 实施前检查清单

- [x] 本文已作为产品和架构基线接受。
- [ ] FreeRDP Sidecar 原型验证通过。
- [ ] 确认 STUN/TURN 部署和短期凭据签发方式。
- [x] Plugin 支持 coturn shared-secret/HMAC 短期凭据，TTL 限制为 60–3600 秒；生产 secret、配额与服务地址仍由部署阶段确认。
- [x] 确认通用可展开工具集合 API。
- [x] 确认通用 Pane Menu Contribution API。
- [x] 确认 Pane Layout user-origin 授权的可信来源。
- [x] 确认 Credential Broker 和宿主安全窗口。
- [ ] 确认原生组件安装、签名、更新和回滚路径。
- [ ] 冻结第一批 Tier 1 OS/架构测试镜像。
- [ ] 完成第三方依赖许可证和 SBOM 预审。
- [x] 为本次 Plugin 实现执行集中、与风险相称的最终测试命令。

## 39. 相关现有实现

- PluginPack 与 Frontend View：`docs/project-plugins.zh-CN.md`
- 现有远程设备 Plugin：`src/cyrene/plugins/builtin/cyrene_remote/`
- 设备身份、能力和 E2EE：`src/cyrene/plugins/builtin/cyrene_remote/control.py`
- 远程文件通道：`src/cyrene/plugins/builtin/cyrene_remote/files.py`
- 目标端文件实现：`src/cyrene/plugins/builtin/cyrene_remote/workspace.py`
- Plugin Project Tools：`src/cyrene/workbench/webui/frontend/features/chat/rail.jsx`
- Pane Layout：`src/cyrene/workbench/webui/frontend/features/chat/pane-layout-controller.jsx`
- Pane Drag/Drop：`src/cyrene/workbench/webui/frontend/features/chat/pane-drop-controller.jsx`
- Pane 拉手菜单：`src/cyrene/workbench/webui/frontend/features/chat/split-pane.jsx`
- Pane 语义 UI：`src/cyrene/workbench/webui/frontend/features/chat/pane-semantic-controller.jsx`

## 40. 外部技术参考

- Ubuntu Remote Desktop：<https://documentation.ubuntu.com/desktop/en/24.04/how-to/share-your-desktop-remotely/>
- xrdp：<https://github.com/neutrinolabs/xrdp>
- xrdp PulseAudio 模块：<https://github.com/neutrinolabs/pulseaudio-module-xrdp>
- XDG RemoteDesktop Portal：<https://flatpak.github.io/xdg-desktop-portal/docs/doc-org.freedesktop.portal.RemoteDesktop.html>
- FreeRDP：<https://github.com/FreeRDP/FreeRDP>
- Apple ScreenCaptureKit：<https://developer.apple.com/documentation/screencapturekit/>
- Apple CGEvent：<https://developer.apple.com/documentation/coregraphics/cgevent>
