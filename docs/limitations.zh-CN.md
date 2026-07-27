# 当前限制

[English](limitations.md) · [简体中文](limitations.zh-CN.md)

本文档记录当前 Cyrene 测试版的边界和已知限制。这些内容属于产品与部署约束，
不只是待修复 Bug 清单。

## 使用者与安全边界

- Cyrene 面向单一本地使用者；Project 是组织边界，不是独立 User 或安全租户。
- Web Server 仅供本机访问，不适合直接暴露到公网。
- Tool Permission 能减少误操作，但不等同于 OS、VM 或 Container Sandbox。
- Electron Browser Cookie 和 Login 会跨 Project 共享。

## 模型、集成与预算

- Prompt 和选中的 Context 会发送到配置的 Model Service；Integration 也可能
  与其配置的服务交换数据。
- Chat Model 目前需要 OpenAI-compatible Endpoint。
- Usage Budget 是本地估算，不是 Provider Billing Control。

## 数据与 API 生命周期

- 数据没有自动 Retention Period，除非用户明确删除或 Reset。
- HTTP API 尚未作为稳定 Public API 版本化。

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
[当前开发进度](../project-notes/CONTEXT_DEV_PROGRESS.zh-CN.md)。
