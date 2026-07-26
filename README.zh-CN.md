<p align="center">
  <img src="https://img.shields.io/badge/python-3.12+-blue" alt="Python">
  <img src="https://img.shields.io/badge/version-0.7.0b1-blue" alt="Version">
  <img src="https://img.shields.io/badge/license-Apache%202.0-green" alt="License">
  <img src="https://img.shields.io/badge/status-beta-orange" alt="Status">
</p>

<p align="center">
  <img src="docs/assets/cyrene-hero.png" alt="Cyrene hero image" width="100%">
</p>

<h1 align="center">Cyrene — 会成长的 AI Agent</h1>

<p align="center"><a href="README.md">English</a> · <strong>简体中文</strong></p>

<p align="center">
  开源、本地优先的 AI Agent，具备持久记忆、并行 Subagent、Project Workspace
  和 Workbench Desktop UI。
</p>

## Cyrene 能做什么

- **跨 Session 记忆**：保留长期 Personality 和 Memory，同时隔离不同 Project
  的工作上下文。
- **完成多步骤任务**：规划、调用 Tool、并行委派 Subagent、验证结果，并恢复
  中断的任务。
- **研究与反思**：执行带引用的 Deep Research、生成 PDF Report，并在任务受阻
  时进行 Deep Reflection。
- **管理 Project**：统一管理 Workspace、Chat、Task、Memory、Knowledge、
  Entity、Schedule 和 Literature Collection。
- **理解文件**：导入并检索文本、PDF、Office Document、Markdown、Image、
  Audio、Video 和其他 Attachment。
- **管理文献**：支持 Collection、Tag、Note、Annotation、Citation、
  Attachment、Relation、CSL JSON、RIS、BibTeX，以及只读 Zotero Desktop
  Import。
- **使用网页与本地工具**：搜索和浏览网页、编辑文件、执行 Shell/Git、连接
  MCP Server，并使用已安装 Skill。
- **自动执行重复工作**：运行 Cron、Interval 和 One-shot Task，并可发送
  Desktop、Telegram 或 WeChat Notification。
- **使用 Workbench 或 Electron**：在 Browser 与 Desktop App 中使用同一套
  Workbench，包含 Quick Chat、Markdown、Code、Diff、Map、PDF、File Preview
  和 Browser View。

Cyrene 只有一个正式 Web UI：**Workbench**。

## 当前限制

- Cyrene 面向单一本地使用者；Project 是组织边界，不是独立 User 或安全租户。
- Web Server 仅供本机访问，不适合直接暴露到公网。
- Tool Permission 能减少误操作，但不等同于 OS、VM 或 Container Sandbox。
- Prompt 和选中的 Context 会发送到配置的 Model Service；Integration 也可能
  与其配置的服务交换数据。
- Chat Model 目前需要 OpenAI-compatible Endpoint。
- Usage Budget 是本地估算，不是 Provider Billing Control。
- 数据没有自动 Retention Period，除非用户明确删除或 Reset。
- Electron Browser Cookie 和 Login 会跨 Project 共享。
- HTTP API 尚未作为稳定 Public API 版本化。
- Literature DOI/Title Lookup、Zotero Web API 双向同步、Experiments 和
  Manuscripts 尚未实现。
- Windows 源码安装受上游 SimpleXNG 限制；请使用预构建 App 或仓库内 Release
  Workflow。
- Pull-request CI 在 Linux 上覆盖完整 Python Suite、WebUI Build 和 Electron
  App Use Test；打包、视觉、升级和带 Credential 的 Integration 仍是
  Release/Manual Gate。

精确验证基线见[开发指南](docs/development.zh-CN.md)，已知工程风险见
[当前开发进度](project-notes/CONTEXT_DEV_PROGRESS.zh-CN.md)。

## 快速开始

### Desktop App

从 [GitHub Releases](https://github.com/Yongchu-Yitao/Cyrene/releases)
下载对应平台产物。

### 从源码运行

需要 Python 3.12+、`uv` 和 Node.js 20+。

```bash
uv sync

cd src/webui
npm install
npm run build
cd ../..

uv run python -m cyrene --workbench
```

打开 `http://localhost:4242`。首次启动会引导完成 Model 与 Personality 配置。

启动 Electron App：

```bash
cd electron
npm install
npm run dev
```

后台服务命令：

```bash
uv run cyrene start
uv run cyrene status
uv run cyrene stop
```

平台安装、可选 Browser Support、Channel 和开发测试说明见
[安装指南](docs/installation.zh-CN.md)与
[开发指南](docs/development.zh-CN.md)。

## 文档

- [安装指南](docs/installation.zh-CN.md)
- [使用指南](docs/usage.zh-CN.md)
- [配置说明](docs/configuration.zh-CN.md)
- [架构说明](docs/architecture.zh-CN.md)
- [开发指南](docs/development.zh-CN.md)
- [当前开发进度](project-notes/CONTEXT_DEV_PROGRESS.zh-CN.md)
- [更新日志](CHANGELOG.md)

## License

[Apache License 2.0](LICENSE)
