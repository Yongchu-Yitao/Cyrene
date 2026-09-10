# Android 原生浏览器接入

Android 使用独立 WebView 显示网页，复用 Workbench 的浏览器标签、地址栏、前进后退、刷新、最大化与画面预览。电脑端继续使用 Electron。网页不是 Linux 浏览器的截图流。

## 已接通的路径

- 只有 Workbench 的受限 WebView 注册 `CyreneAndroidBrowser`。普通网页 WebView 没有应用 JavaScript 接口。受限 WebView 内的预览 iframe 虽可能看到接口对象，但请求必须携带仅向可信顶层根页面注入的随机凭据；导航时轮换，防止预览内容调用浏览器命令。
- Workbench 的 Android 适配器实现 `window.cyrene.browser`，维护 Promise 请求、状态订阅和 `/ws/browser/native-host` 反向 RPC。该 WebSocket 经过现有本机 Host、Origin、令牌鉴权。
- Python 浏览器工具通过原有原生浏览器 RPC 分支发送命令，Android 根据对话 ID 选择同一组标签。连接中断时返回错误，不启动另一套 Playwright 页面。
- 支持打开/关闭/切换标签、导航、返回、刷新、页面文本及元素快照、元素引用点击/输入、坐标点击、滚动、条件等待和当前视口截图。快照导航凭据在原生侧保存，绑定标签和 URL，有两分钟有效期且只能消费一次。
- 用户直接触控网页、使用软键盘；文件输入可调用 Android 文件选择器。用户和 Agent 共享页面以及 WebView 的 Cookie。
- 标签 URL、对话归属和选中状态存入应用私有设置，重新创建 Activity 后按需加载；这不是 DOM 或未提交表单的完整进程快照。返回应用仍复用现有 Activity 中的页面。
- 页面限制为 HTTP(S)，不向页面开放本机 IP、localhost、file、content 或 intent 导航。最多保留 12 个标签。

## 仍有明确边界

这不是 Electron 全量移植，也尚未完成手机端到端验收。

- DOM 自动化目前覆盖顶层文档；跨域 iframe、复杂 Shadow DOM、站点要求可信输入事件的操作需要后续补齐。
- 尚未提供 Agent 文件上传、下载管理、网络日志、Linux 本地文件预览、桌面专用浮层和完整视频全屏适配；这些 RPC 返回不支持，不伪造成功。手机文件选择器是用户交互功能。
- WebView Cookie 保留，但与旧 Linux Chromium 的 Cookie 不自动互通；网站可能要求重新登录。
- 整页长截图、标签完整前进后退历史、后台长期运行和系统回收后恢复未作桌面同等保证。
- 浏览器数据清理尚未接通，不能直接清除整个 WebView Cookie 存储，否则会同时影响 Workbench 本机会话。

## 构建与验收

需要一起构建 Android 壳、Workbench 前端及 guest 后端；guest 服务使用 `CYRENE_BROWSER_HOST=android`，从启动时就禁止退回另一浏览器。只覆盖旧 APK 而不更新内置前后端不足以启用完整通路。

本轮使用独立临时 Gradle 输出及前端构建目录验证，不覆盖主任务的 APK，不安装手机、不重启服务。

本轮验证结果：Python 原生桥接与鉴权测试 4 项、前端桥接与 DOM 命令测试 3 项、Android URL 策略测试 2 项全部通过；Android Kotlin 编译与 Workbench 前端独立打包通过，`git diff --check` 无错误。这些结果不代表真机端到端验收通过。

验收应先使用可控网页：Agent 导航后确认同一标签出现；用户输入后由 Agent 快照读到；再验证引用点击、中文输入、截图、菜单遮挡、标签对话隔离、重新进入及连接中断。真实网站登录、触控键盘和性能需要真机检查，不能用编译或协议单元测试替代。
