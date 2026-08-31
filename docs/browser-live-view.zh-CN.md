# Browser Live View 与 Login Takeover

[English](browser-live-view.md) ·
[简体中文](browser-live-view.zh-CN.md)

Agent 使用 Browser 时，Chat 右侧会显示它看到的页面和操作。当遇到 Login、
CAPTCHA 或 2FA 时，可以把 Browser 交给用户完成登录，再在同一个已认证
Session 中继续。

## Runtime 与安装

Electron 桌面版直接使用内嵌 Chromium，不需要 Playwright。每个 Conversation
有独立的内存 Tab Manager、当前页和 History，但共享持久
`persist:cyrene-browser` Partition，因此 Cookie/Login 可以跨 Conversation
和重启复用。删除 Conversation 会关闭其 Tab，但不会清除共享 Login Data。

Electron RPC 失败会直接报错，不会静默打开第二个 Browser。

非 Electron 模式可以用 `httpx` 做文本 Fetch。完整自动化、Live Screencast
和 Headed Login Takeover 需要：

```bash
pip install -e ".[browser]"
playwright install chromium
```

缺少 Playwright/Chromium 时，Live View 会显示提示；Navigate 回退到文本
Fetch，交互 Tool 返回 Runtime 不可用。

## 工作方式

### Electron

- Electron 为 Browser Tab 创建 `WebContentsView` 并嵌入 Workbench。
- Python 通过 Token-authenticated Loopback RPC 执行 Navigate、Snapshot、
  Click、Type、Wait、Network Log、Screenshot、Scroll、Tab。
- 每个 Conversation 有独立 Tab Manager。
- Background Agent 携带 Session ID，不会改变其他 Chat 可见的 Browser。
- 用户和 Agent 在同一 Conversation 操作相同 Tab/Profile。
- 所有 Manager 共享 Partition，因此一次登录可供其他 Conversation 使用。

### 固定 Browser 资源

Browser PiP 整个标题栏和最小化 Browser 按钮都可拖到顶栏资源 Shelf。PiP 拖动
用指针坐标直接判断 Shelf 矩形，因此即使 Electron 原生页面位于上层也能可靠
固定；拖到其他位置仍只移动小窗。最小化按钮只显示当前网页 favicon（缺失时
立即回退到 Browser SVG），普通点击恢复 PiP，超过拖动阈值后才进入固定交互。
最小化按钮与 PiP 复用同一套浮窗坐标提交和消息避让逻辑，可在对话内容区域内
自由移动并持久保持当前显示位置。拖向顶栏时，`body` 级 fixed 拖动代理会越过
对话标题栏的裁剪和层叠上下文，因此图标从标题栏上方穿过并准确进入 Shelf。
固定会保留原页面和 Owner Session，不移动或复制 Tab。所有 Session 的后续
Agent Turn 都能发现该固定 Browser 的 Resource ID。

Browser Frame 本身也以稳定 Cyrene UI Component 暴露。Agent 可通过
`cyrene.ui.snapshot` / `cyrene.ui.inspect` 点击标题栏按钮、拖动浮窗，并执行最大化
或还原（包括双击标题栏手势）；操作不依赖当前键盘焦点。这一层控制的是 Cyrene
浮窗，网页内部自动化仍由独立的 `browser_*` Tool Family 完成。

把 PiP 标题栏、最小化 favicon 按钮或顶栏固定 Browser 图标拖到另一个
Conversation Tab，会在目标 Session 的 Browser Manager 中创建同 URL 的新
页面。该操作复制页面入口而不是转移 Owner/控制权；登录态因共享 Partition
继续可用，但两边的 Tab、导航历史和后续操作互相独立。

Owner Session 保留正常 Browser 控制。其他 Session 只能把 Resource ID 传给
`browser_snapshot` 或 `browser_screenshot`；Navigate、Click、Type、Reload、
History、Upload 和其他页面修改都会在 Browser 执行层被拒绝。顶栏右键菜单可
显示只读页面预览，同时不会隐藏原 PiP View。

### 非 Electron Playwright

- Lazy 启动并复用一个 Persistent Browser Context；
- Profile 位于 `<DATA_DIR>/browser_profile`；
- 每次操作发布只含 Metadata 的 `browser_frame` SSE Event；
- CDP Screencast 通过 `GET /ws/browser` 发送 Binary JPEG；
- Agent 开始 Browser 操作时自动显示右侧 Panel。

## 页面操作模型与当前边界

- `browser_snapshot` 只返回当前视口中可见、可命中的元素，并为它们生成短期
  `e1`、`e2` 等 Ref。页面导航、同页导航、滚动或明显重排后应重新 Snapshot，
  不应继续使用旧 Ref。
- Electron Click 会在发送 Chromium Trusted Mouse Input 前重新定位目标、检查
  点击点是否被遮挡，并在目标移动时最多重试三次；目标持续变化时不会盲目点击。
- `browser_navigate` 会检查初始 HTTP(S) 目标并阻止 Loopback、Private、
  Link-local、Cloud Metadata 和 Reserved Network。该检查不是完整的浏览器网络
  Sandbox：页面子资源、脚本触发的导航和 Electron 中的每个 Redirect 并未全部
  经过逐请求 IP Policy，因此 Browser 仍只适合单一本地使用者访问可信公网内容。
- Snapshot、Ref Targeting 和 Wait 的 DOM Projection 主要覆盖当前 Top-level
  Document。Iframe、跨 Origin Frame、Shadow DOM、Canvas/WebGL 和复杂富文本
  Editor 没有统一的递归语义支持，必要时应使用 Screenshot 或 User Takeover。
- `browser_network_log` 来源是页面的 Resource Performance Entry，仅提供 URL、
  Initiator Type、Duration 和 Transfer Size；它不是完整 DevTools Network Trace，
  不提供 Request/Response Header、Body、Status 或 WebSocket Message。
- Navigate、Click、Type 和可选 Enter Submit 属于正常 Browser Tool Action，
  不会像文件上传一样自动触发 Human-only Confirmation。购买、发布、删除、账号
  修改等高影响动作仍必须由调用方依据用户请求和 Permission Policy 控制。

## Login Takeover

Electron Browser 本来就是可见 Native View，用户可直接在同一 Tab 登录。

非 Electron Playwright 流程：

1. Agent 调用 `browser_request_takeover`；
2. 共享 Browser 使用同一 Profile 重启为 Headed，Agent 进入 Awaiting User，
   Screencast 暂停；
3. 用户完成登录并点击“我已完成登录”；
4. Browser 使用已认证 Profile 重启为 Headless，Agent 自动继续。

Profile 是持久的，通常每个 Site 只需要一次。

此流程只适用于 Web UI 与 Browser 在同一台机器上的本地运行。

## 配置

| Key | 默认 | 说明 |
|---|---|---|
| `CYRENE_BROWSER_HEADLESS` | `1` | Playwright 默认 Headless；Takeover 仍会 Headed |
| `CYRENE_BROWSER_SCREENCAST_QUALITY` | `60` | JPEG Quality |
| `CYRENE_BROWSER_WIDTH` | `1280` | Viewport/Screencast Width |
| `CYRENE_BROWSER_HEIGHT` | `800` | Viewport/Screencast Height |
| `CYRENE_BROWSER_USER_AGENT` | 自动 | Electron/Playwright User-Agent |
| `CYRENE_BROWSER_LOCALE` | `zh-CN` | Playwright Locale |
| `CYRENE_BROWSER_ACCEPT_LANGUAGE` | `zh-CN,zh;q=0.9,en;q=0.8` | Accept-Language |

Playwright Profile 目录是 `<DATA_DIR>/browser_profile`。

Electron 开发模式：

```bash
cd electron
npm run dev
```

Electron 通过 `uv run cyrene --workbench --electron-mode` 启动 Backend。
成功时输出 `UIMODE=workbench` 和 `PORT=4242`。

## Permission

Browser Navigation 是 Network Read，与 `WebFetch`/`WebSearch` 相同，不要求
Workspace Scope Elevation。Browser Profile 只写入 `DATA_DIR`。

File Upload 更严格：

- Agent 触发 Native File Chooser 时会被 Electron 拦截；
- 必须调用 `browser_upload_files`；
- 每次都需要 Human-only Single-use Approval；
- Approval 显示 Origin、Name、Size、Media Type、SHA-256；
- 绑定 Tab、Page/Frame URL、Input 和确切 File Content；
- Page、Destination 或 File 变化会取消；
- `full_access` 不会绕过确认；
- Tool 只设置 File Input，不点击 Submit；
- Bytes 会复制到 Private Read-only Snapshot，最长 15 分钟后删除。

## Tool

| Tool | 用途 |
|---|---|
| `browser_navigate` | 打开 URL 并返回可读文本 |
| `browser_snapshot` | 返回可见元素、Ref、Text、Href、Selector、Box |
| `browser_screenshot` | 截图到 Temp PNG |
| `browser_click` | CSS Selector Click |
| `browser_click_ref` | Snapshot Ref Click |
| `browser_click_at` | Viewport Coordinate Click |
| `browser_type` | 输入，可选 Submit |
| `browser_type_ref` | 向 Ref Element 输入 |
| `browser_upload_files` | 经一次性用户批准上传确切本地文件 |
| `browser_wait` | 等待 URL/Text/Selector |
| `browser_network_log` | 查看 Resource/Fetch/XHR URL |
| `browser_tab_list` | 列出 Electron Browser Tab 及其状态 |
| `browser_tab_new` | 按用户要求新建 Tab |
| `browser_tab_select` | 切换活动 Tab |
| `browser_tab_close` | 关闭指定或活动 Tab |
| `browser_scroll` | 以可信 Wheel Input 滚动 Page 或 Nested Scroll Container |
| `browser_request_takeover` | 打开 Native Window，等待用户登录后继续 |
