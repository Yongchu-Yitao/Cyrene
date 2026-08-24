# 当前限制

[English](limitations.md) · [简体中文](limitations.zh-CN.md)

本文档记录当前 Cyrene 测试版的边界和已知限制。这些内容属于产品与部署约束，
不只是待修复 Bug 清单。

## 使用者与安全边界

- Cyrene 面向单一本地使用者；Project 是组织边界，不是独立 User 或安全租户。
- Web Server 仅供本机访问，不适合直接暴露到公网。
- Tool Permission 能减少误操作，但不等同于 OS、VM 或 Container Sandbox。
- Electron Browser Cookie 和 Login 会跨 Project 共享。
- 把 File 固定到顶栏是明确的全局分享动作：所有 Session 都能发现其索引，并可
  通过正常 File Tool 读取。
- 其他 Session 固定的 Browser 在 Cyrene Tool 层只读，但所有 Electron Browser
  Session 仍共享同一个本地 Cookie Partition。

## 模型、集成与预算

- Prompt 和选中的 Context 会发送到配置的 Model Service；Integration 也可能
  与其配置的服务交换数据。
- Chat Model 目前需要 OpenAI-compatible Endpoint。
- Usage Budget 是本地估算，不是 Provider Billing Control。

## 数据与 API 生命周期

- 数据没有自动 Retention Period，除非用户明确删除或 Reset。
- HTTP API 尚未作为稳定 Public API 版本化。

## Cyrene 自控制边界

- Snapshot 与 Inspect 只暴露当前已经渲染的界面，不预测尚未出现的未来页面。
  导航、展开、滚动或打开右键菜单后，Agent 必须重新读取 Snapshot。
- 优先使用稳定语义控件；通用 DOM Projection 只覆盖当前可见且可操作的 HTML
  控件。仅存在于 Canvas/WebGL 的控件需要专用语义 Adapter，且不会暴露裸屏幕坐标。
- Model Selection、Secret、Account Ceremony、破坏性 Reset/Delete 和 Human-only
  Confirmation 不属于 Typed Self-management Setting。
- 发送当前输入框是显式 R2 操作，需要匹配的用户请求或正常授权；停止当前运行仍为 R1。
- 后台业务 Service 保持 Internal。公开给 Agent 的能力用于控制可见 UI 和非模型
  Typed Setting，不直接暴露 Project、Chat 或 Data Management API。

## 尚未实现的功能

- Literature DOI 与 Title Lookup 尚未实现。
- Zotero Web API 双向同步尚未实现。
- Experiments 和 Manuscripts 尚未实现。

## 平台与验证限制

- Windows 源码安装受上游 SimpleXNG 限制；请使用预构建 App 或仓库内 Release
  Workflow。
- Pull-request CI 在 Linux 上覆盖完整 Python Suite、WebUI Build 和 Electron
  App Use Test；打包、视觉、升级和带 Credential 的 Integration 仍是
  Release/Manual Gate。

精确验证基线见[开发指南](development.zh-CN.md)，已知工程风险见
[开发记录](../project-notes/README.md)。
