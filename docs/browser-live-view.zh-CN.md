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

### 非 Electron Playwright

- Lazy 启动并复用一个 Persistent Browser Context；
- Profile 位于 `<DATA_DIR>/browser_profile`；
- 每次操作发布只含 Metadata 的 `browser_frame` SSE Event；
- CDP Screencast 通过 `GET /ws/browser` 发送 Binary JPEG；
- Agent 开始 Browser 操作时自动显示右侧 Panel。

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

Electron 通过 `src/cyrene/local_cli.py` 启动 Backend。成功时输出
`UIMODE=workbench` 和 `PORT=4242`。

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
| `browser_click_text` | Visible Text/Accessible Label Click |
| `browser_click_at` | Viewport Coordinate Click |
| `browser_type` | 输入，可选 Submit |
| `browser_type_ref` | 向 Ref Element 输入 |
| `browser_upload_files` | 经一次性用户批准上传确切本地文件 |
| `browser_wait` | 等待 URL/Text/Selector |
| `browser_network_log` | 查看 Resource/Fetch/XHR URL |
| `browser_request_takeover` | 打开 Native Window，等待用户登录后继续 |
