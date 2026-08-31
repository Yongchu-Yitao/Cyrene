# 远程桌面插件

`cyrene_remote_desktop` 在已配对的 Cyrene 设备之间提供远程画面、鼠标键盘接管，以及系统登录提供器的接入边界。

## 安全模型

建立会话必须同时满足两项条件：

1. 目标机对该设备授予 `remote_desktop:view`、`remote_desktop:control` 或 `remote_desktop:login` 能力；
2. 目标机用户在“远程桌面”插件面板中开启 30–900 秒的短时共享租约。

面板中的“开启共享”是显式授权动作：它会把所选能力加入该设备的配对授权，同时创建内存租约。租约过期、目标机重启、插件停止或用户点击“立即撤销”后，远程会话不可继续。画面和输入使用现有 Cyrene Remote 的端到端加密命令通道；审计只记录命令名称，不记录输入内容。

## 当前用户会话

内置 `user_session` 提供器复用 Cyrene Electron 的 App Use 桥接：

- 可枚举并捕获当前登录用户的应用窗口；
- 画面会缩放并转为 JPEG 后通过加密通道传输；
- 控制模式支持点击、右键、滚动、快捷键、特殊键和文本输入；
- Linux 上现有 App Use 仅支持语义模式，因此目前不能使用该视觉提供器。

这一路径不具备系统权限，无法捕获锁屏、安全桌面或登录界面。

## 系统登录提供器

系统登录必须由单独安装、经过系统授权的特权 companion 提供。插件只连接本机回环地址，不接收远程 companion 地址。配置：

```text
CYRENE_REMOTE_DESKTOP_COMPANION_URL=http://127.0.0.1:<port>
CYRENE_REMOTE_DESKTOP_COMPANION_TOKEN=<random-secret>
```

companion 接收 `POST /rpc`，使用请求头 `X-Cyrene-Remote-Desktop-Token`，请求体为：

```json
{"method":"login.begin","args":{"target_id":"display-1"}}
```

插件使用的方法为 `targets`、`login.begin`、`frame`、`input`、`close`。所有响应必须为 JSON 对象；失败响应使用 `{"ok": false, "code": "..."}`。`frame` 返回 Base64 图像及尺寸，`login.begin` 返回 `session_id`。

系统登录模式会拒绝 `text` 输入，密码不会经 Cyrene Remote 转发。凭据输入应由目标机本地认证、系统 SSO、智能卡/生物识别，或 companion 自己的原生安全认证界面完成。

## 使用

1. 在两台设备的“远程”设置中完成配对并保持传输在线。
2. 在目标机打开“远程桌面”插件，选择控制端设备、权限和有效时间，然后点击“开启共享”。
3. 在控制端打开同一插件，选择目标设备与模式，读取窗口列表并连接。
4. 控制结束后点击“断开”；目标机可随时点击“立即撤销”。
