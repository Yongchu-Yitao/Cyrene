<p align="center">
  <img src="https://img.shields.io/badge/python-3.12+-blue" alt="Python">
  <img src="https://img.shields.io/badge/version-0.7.0b5-blue" alt="Version">
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

uv run python -m cyrene
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
uv run cyrene
uv run cyrene status
uv run cyrene stop
```

裸命令 `cyrene` 会在需要时启动后台服务并直接进入交互界面。
`cyrene chat` 提供流式回复、工具/计划进度、权限确认、附件、历史对话切换、
运行中断与断线恢复；一次性调用可使用
`cyrene chat --json "你的任务"`。

平台安装、可选 Browser Support、Channel 和开发测试说明见
[安装指南](docs/installation.zh-CN.md)与
[开发指南](docs/development.zh-CN.md)。

## 文档

- [安装指南](docs/installation.zh-CN.md)
- [使用指南](docs/usage.zh-CN.md)
- [配置说明](docs/configuration.zh-CN.md)
- [架构说明](docs/architecture.zh-CN.md)
- [开发指南](docs/development.zh-CN.md)
- [当前限制](docs/limitations.zh-CN.md)
- [当前开发进度](project-notes/CONTEXT_DEV_PROGRESS.zh-CN.md)
- [更新日志](CHANGELOG.md)

## License

[Apache License 2.0](LICENSE)
