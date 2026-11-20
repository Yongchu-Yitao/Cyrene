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

把网页小窗的标题栏、最小化图标或顶栏固定的网页拖到另一个对话，就会在那里打开同一网址。两个对话共享登录状态，各自保留页面、浏览历史和操作权。

原对话可以继续操作固定的网页。其他对话只能用资源编号调用 `browser_snapshot` 或 `browser_screenshot`；浏览器工具执行时会检查权限。右键顶栏图标可以查看预览，原来的小窗仍然可见。

### 非 Electron Playwright

- Lazy 启动并复用一个 Persistent Browser Context；
- Profile 位于 `<DATA_DIR>/browser_profile`；
- 每次操作发布只含 Metadata 的 `browser_frame` SSE Event；
- CDP Screencast 通过 `GET /ws/browser` 发送 Binary JPEG；
- Agent 开始 Browser 操作时自动显示右侧 Panel。

## 页面操作模型与当前边界

- `browser_snapshot` 给当前视口里可点击的元素分配临时编号，例如 `e1`、`e2`。打开新页、页内跳转、滚动或布局变化后，再取一次快照。
- 桌面版会在点击前重新检查目标的位置和遮挡情况。目标移动时最多重试三次，仍在移动就跳过这次点击。
- 首次打开网址前，`browser_navigate` 会检查地址，拦截本机、内网、链路本地、云元数据和保留地址。子资源、脚本跳转和桌面版重定向可能绕过这项检查。浏览器适合单人在本机访问可信的公网内容。
- 快照和等待主要针对当前网页的顶层内容。遇到嵌入页面、跨域框架、Shadow DOM、画布或富文本编辑器，可以查看截图或接管浏览器。
- `browser_network_log` 列出资源网址、发起类型、耗时和传输大小。查看请求头、正文、状态码或 WebSocket 消息需要使用开发者工具。
- 文件上传需要单独确认。点击、输入和回车提交遵循普通工具权限。涉及购买、发布、删除或修改账号时，还要核对用户请求和相应权限。

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

### 本地 HTML 交互预览

桌面端 Agent 可调用 `browser_open_file({"path": "demo/index.html"})`，在内置浏览器的独立预览标签页打开当前工作区内的 HTML。随后可使用现有的快照、截图、点击和输入工具检查页面。再次调用会替换上一个本地预览；修改后也可使用浏览器刷新按钮加载最新内容。

支持 HTML 同目录及其子目录内的相对 CSS、JavaScript、图片、字体等资源，以及隔离的 localStorage/sessionStorage。预览不共享普通浏览器的登录信息，关闭后清理临时资源。依赖需要保存在本地：外部网络、弹窗、隐藏文件及目录外资源均不开放。普通 `browser_navigate` 的公网 URL 校验保持不变；非 Electron 环境会明确返回不支持本地预览。
