<p align="center">
  <img src="https://img.shields.io/badge/python-3.12+-blue" alt="Python">
  <img src="https://img.shields.io/badge/version-0.9.0-beta11-blue" alt="Version">
  <img src="https://img.shields.io/badge/license-Apache%202.0-green" alt="License">
  <img src="https://img.shields.io/badge/status-stable-brightgreen" alt="Status">
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

- **一个会与你共同成长的 Agent**：Cyrene 能跨会话延续 Personality 与有价值
  的长期记忆，同时让每个 Project 的上下文保持清晰隔离。
- **让上下文随工作流动**：Cyrene 将上下文编排为可追踪、可共享的 Block；每个
  Conversation 独立保留 Goal、Plan、Workspace、Memory 与执行状态，Subagent
  只获取所需上下文；稳定 Block 持续复用，完整构成随时可查。
- **从对话一路做到结果验收**：Cyrene 能规划任务、操作浏览器、编辑文件、执行
  Shell 与 Git、连接 MCP Server、调用 Skill、并行委派 Subagent、验证结果，并
  在中断后继续执行。
- **研究过程可追溯，成果可复用**：Cyrene 能把带引用的网页研究与你的 PDF、
  Office 文档、音视频和文献库结合起来，沉淀为结构化知识或精美的 PDF 报告。
- **真正会操作浏览器**：你可以实时看到 Cyrene 浏览页面、点击、输入、上传和
  检查内容；遇到登录、CAPTCHA 或 2FA 时，可在同一个浏览器中接管，完成后再
  交还给 Agent，无需丢失会话状态。Snapshot Ref、可信点击和一次性批准上传
  降低误操作，但当前 DOM Projection 主要覆盖 Top-level Document，且 Browser
  URL Policy 不等同于完整的逐 Request 网络 Sandbox；详见
  [Browser 能力与边界](docs/browser-live-view.zh-CN.md#页面操作模型与当前边界)。
- **实时创作 PowerPoint**：通过本地 Office 加载项，Cyrene 能检查、批量新增、
  移动、缩放和修改当前演示文稿中的元素，并在每页完成后直接让 PowerPoint
  渲染和验证；用户可以连续看到整套 PPT 的渐进式变化。
- **一个能管理自己的 Agent**：通过受权限约束且可审计的工具，Cyrene 可以查看
  和操作自己的界面、调整设置、管理 Project 与 Chat、备份数据并处理更新。
- **承载长期思考的一体化工作空间**：Project 将 Conversation、Goal Loop、
  Plan、可编辑文件、Terminal、Review、Memory、Knowledge、Entity、Schedule
  与 Literature 汇集到同一个 Workbench，并可在浏览器或桌面端使用。
- **离开之后也会继续工作**：支持一次性与周期性自动任务，并可通过桌面、
  Telegram 或微信接收结果通知。

## 一个由插件组装出来的 Agent

Cyrene 的人格、记忆、模型、工具与工作流都由插件提供，而不是固化在一个不可改变
的 Agent 中。不同 Conversation 和 Project 可以启用不同能力；你也可以在 Plugin
Center 中管理插件和工具的可见性。

实现原理见[架构说明](docs/architecture.zh-CN.md)，扩展方式见
[自定义插件](docs/project-plugins.zh-CN.md)。

## 快速开始

### Desktop App

从 [GitHub Releases](https://github.com/Yongchu-Yitao/Cyrene/releases)
下载对应平台产物。

### 从源码运行

需要 Python 3.12+、`uv` 和 Node.js 22.12+。

```bash
uv sync

cd src/cyrene/workbench/webui
npm install
npm run build
cd ../../../..

uv run cyrene
```

打开 `http://localhost:4242`。首次启动会引导完成 Model 与 Personality 配置。

启动 Electron App：

```bash
cd electron
npm install
npm run dev
```

Workbench 后端和终端客户端命令：

```bash
uv run cyrene
uv run cyrene chat
uv run cyrene status
```

裸命令 `cyrene` 会使用新 Agent Runtime 启动 Workbench 后端。
`cyrene chat` 提供流式回复、工具/计划进度、权限确认、附件、历史对话切换、
运行中断与断线恢复；一次性调用可使用
`cyrene chat --json "你的任务"`。

平台安装、可选 Browser Support、Channel 和开发测试说明见
[安装指南](docs/installation.zh-CN.md)与
[开发指南](docs/development.zh-CN.md)。

## 文档

- [安装指南](docs/installation.zh-CN.md)
- [使用指南](docs/usage.zh-CN.md)
- [实时控制 PowerPoint](docs/office-live-control.zh-CN.md)
- [配置说明](docs/configuration.zh-CN.md)
- [架构说明](docs/architecture.zh-CN.md)
- [自定义插件](docs/project-plugins.zh-CN.md)
- [开发指南](docs/development.zh-CN.md)
- [当前限制](docs/limitations.zh-CN.md)
- [开发记录](project-notes/README.md)
- [更新日志](CHANGELOG.md)

## License

[Apache License 2.0](LICENSE)
