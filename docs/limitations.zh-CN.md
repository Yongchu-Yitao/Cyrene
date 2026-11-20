# 当前限制

[English](limitations.md) · [简体中文](limitations.zh-CN.md)

## 使用者与安全边界

- Cyrene 供一个人在本机使用，网页服务也只供本机访问。项目用来归类工作，同一系统账号下的项目共享该账号的访问权限。需要隔离用户或程序时，使用操作系统账号、虚拟机或容器。
- 工具权限决定助理可以执行哪些操作。桌面浏览器的 Cookie 和登录状态在各项目间共享。
- 固定文件后，每个对话都能找到它，并通过文件工具读取。固定网页的操作权留在原对话，其他对话只可查看快照或截图。
- 打开网址前，浏览器会检查地址，拦截本机、内网、链路本地、云元数据和保留网络地址。网页加载资源、脚本跳转和桌面浏览器重定向仍有可能访问这些地址。
- 上传文件需由用户单独确认。网页中的购买、发布、删除和账号修改按用户请求和普通工具权限处理，即使最后一步只是点击或按回车。

## 模型、集成与预算

- Prompt 和选中的 Context 会发送到配置的 Model Service；Integration 也可能
  与其配置的服务交换数据。
- Chat Model 目前需要 OpenAI-compatible Endpoint。
- 预算按本机记录的用量估算，实际费用以服务商账单为准。

## 数据与 API 生命周期

- 数据会一直保留，直到你删除数据或执行重置。
- HTTP API 尚未作为稳定 Public API 版本化。

## Cyrene 自控制边界

- 快照和检查工具显示当前已经打开的界面。
  导航、展开、滚动或打开右键菜单后，Agent 必须重新读取 Snapshot。
- 优先使用稳定语义控件；通用 DOM Projection 只覆盖当前可见且可操作的 HTML
  控件。仅存在于 Canvas/WebGL 的控件需要专用语义 Adapter，且不会暴露裸屏幕坐标。
- Model Selection、Secret、Account Ceremony、破坏性 Reset/Delete 和 Human-only
  Confirmation 不属于 Typed Self-management Setting。
- 发送当前输入框是显式 R2 操作，需要匹配的用户请求或正常授权；停止当前运行仍为 R1。
- 后台业务 Service 保持 Internal。公开给 Agent 的能力用于控制可见 UI 和非模型
  Typed Setting，不直接暴露 Project、Chat 或 Data Management API。

## 尚未实现的功能

- 按 DOI 或标题查找文献
- 通过 Zotero Web API 双向同步
- 实验与手稿管理

## 平台与验证限制

- Windows 源码安装受上游 SimpleXNG 限制；请使用预构建 App 或仓库内 Release
  Workflow。
- Pull-request CI 在 Linux 上覆盖完整 Python Suite、WebUI Build 和 Electron
  App Use Test；打包、视觉、升级和带 Credential 的 Integration 仍是
  Release/Manual Gate。
- Browser 的 Python/Electron 测试主要验证协议、状态机、安全检查和独立 JS
  行为，许多测试使用 Fake Page、Mock Locator 或 Source Contract；复杂真实网站、
  OAuth/CAPTCHA、跨 Origin Iframe、Shadow DOM 和各平台原生 Window Flow 仍需要
  Manual/Release Integration 验证。
- Browser Snapshot、Ref Targeting 和 Wait 主要覆盖当前 Top-level DOM；Iframe、
  Shadow DOM、Canvas/WebGL 和复杂 Editor 没有统一的递归语义支持。Network Log
  显示页面加载资源的耗时和大小。完整的请求记录请在开发者工具中查看。

精确验证基线见[开发指南](development.zh-CN.md)，已知工程风险见
[开发记录](../project-notes/README.md)。
