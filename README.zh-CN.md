<p align="center">
  <img src="https://img.shields.io/badge/python-3.12+-blue" alt="Python">
  <img src="https://img.shields.io/badge/version-0.7.0b1-blue" alt="Version">
  <img src="https://img.shields.io/badge/license-Apache%202.0-green" alt="License">
  <img src="https://img.shields.io/badge/status-beta-orange" alt="Status">
</p>

<p align="center">
  <img src="docs/assets/cyrene-hero.png" alt="Cyrene 主视觉" width="100%">
</p>

<h1 align="center">Cyrene — 会持续进化的 AI Agent</h1>

<p align="center"><a href="README.md">English</a> · <strong>简体中文</strong></p>

<p align="center">
  开源、本地优先的 AI Agent：拥有可进化人格、并行 Subagent、Workbench
  桌面界面和零外部基础设施。无需 Docker、Redis 或独立向量数据库。
</p>

---

## Cyrene 是什么？

Cyrene 是一个持续运行的本地 AI Agent。它通过 `SOUL.md` 保存并更新人格，
跨会话记忆对话，按需启动并行 Subagent，并可通过定时任务主动工作。

它连接任何 OpenAI-compatible LLM API。Agent loop、FastAPI Web Server、
Scheduler、SimpleXNG 搜索、Memory 和 Knowledge 都运行在同一个 Python
进程中，以 SQLite 和本地文件持久化。

- 一个进程承载 Agent、Web Server、Scheduler 和内置搜索。
- 只提供项目中心的 Workbench Web UI。
- 默认 DeepSeek，也可使用 GPT、Claude、Qwen 或本地兼容模型。
- 正式领域包分离 Agent、Workbench、Model Runtime、Learning、Lifecycle、
  Observability、Knowledge、Channels 和 Tooling，并兼容历史 Python Import。

## 功能

### Agent 核心

- **两阶段 Agent Loop**：先判断直接回答还是需要 Tool，再进入执行阶段。
- **`SOUL.md` 人格**：持久化身份、偏好、关系和长期记忆。
- **Deep Research**：规划问题、并行检索、多轮综合并导出报告。
- **Deep Reflection**：通过多轮内部重构处理复杂或模糊请求。
- **行为学习**：从过去执行中提取可复用 Workflow。

### Memory 与 Knowledge

- **三层 Memory**：Context Window、跨会话 Short-term、长期 `SOUL.md`。
- **Knowledge Base**：上传和索引文档、PDF、图片，支持文本/向量检索。
- **项目文献库**：Collection、Tag、引用元数据、Zotero 同步、结构化检索。
- **Entity**：跟踪人、系统、项目项等结构化事实。

### Tool 与自动化

- **并行 Subagent**：独立执行并通过 Inbox 协调。
- **内置 SimpleXNG**：无需 Docker 或外部搜索 Key。
- **MCP**：连接 stdio 或 SSE Model Context Protocol Server。
- **Task Scheduler**：Cron、Interval、One-shot 和主动工作 Lottery。
- **Browser Live View**：Electron 直接驱动持久 Chromium；非 Electron 可选
  Playwright。
- **Code/Claude Code Tool**：代码索引、Git、tmux Claude Code Bridge。
- **Skill Installer**：运行时安装 `.md`、目录或 `.zip` Skill。

### 界面与 Channel

- **Workbench**：项目 Dashboard、Schedule、Knowledge、Memory、Library、
  Chat、Session、Browser、Settings 和可跟踪 Task Execution。
- **Context Trace**：通过 verbose JSONL、Context Debug API 或
  `cyrene flow` 检查每次 LLM 调用实际收到的 Prompt、Memory、History 和
  Tool。
- **Electron**：macOS、Windows、Linux 桌面构建，凭据保存在 OS Keyring。
- **Telegram / WeChat**：Telegram 稳定，WeChat 为 Alpha。
- **Map**：AMap/Leaflet 地图和地点 Tool。

## 当前限制（v0.7.0b1）

- 单用户、单 Workspace，无多用户隔离。
- Raw Web UI 只绑定 `127.0.0.1` 且不提供远程认证；Electron 使用本地 Token。
- Session History 尚无完整保留策略。
- 部分 Agent 错误仍可能缺少清晰的用户通知。
- API 尚未做版本前缀。
- 没有 LLM 调用配额或花费上限。
- Windows 源码安装可能需要 SimpleXNG 兼容补丁，推荐预构建安装包。
- 本地测试覆盖完整，但 GitHub Actions 当前主要执行 Release Packaging，
  尚无完整 PR pytest Matrix。

## 快速开始

### 预构建版本

从 [Releases](https://github.com/Yongchu-Yitao/Cyrene/releases) 下载对应平台
安装包。Windows x64 和 ARM64 分别提供。

### 从源码运行

需要 Python 3.12+。构建 WebUI 或运行 Electron 还需要 Node.js 20+。

```bash
# 安装 Python 依赖
uv sync

# 必要时编译 WebUI JSX
cd src/webui
npm install
node build-jsx.mjs
cd ../..

# 前台运行
python -m cyrene --workbench

# 或后台 daemon
cyrene start
```

打开 `http://localhost:4242`。首次启动会进入 API Key 和人格设置向导。

正常使用不需要 `.env`。配置保存在加密的 `data/config.enc`，由 Onboarding
或 Settings 管理。

主数据库是 `store/cyrene.runtime.database`。如果检测到旧
`store/cyrene.db` 且新库未承载数据，启动会通过 SQLite Backup API 生成并
校验新库，写入迁移 Marker，并保留旧文件作为回滚副本。

### Electron 源码开发

Electron Package 位于 `electron/`：

```bash
uv sync
cd electron
npm install
npm run dev
```

Electron 会按物理路径执行 `src/cyrene/local_cli.py`。该启动垫片会加入
checkout 的 `src/` 并优先使用 `.venv`。

常见问题：

- 根目录没有 `package.json`，必须在 `electron/` 执行 `npm run dev`。
- 缺少 `cryptography` 等模块表示 `.venv` 不完整，应重新安装依赖。
- Electron 模式的 Raw HTTP 请求可能因缺少 Desktop Token 返回 401。
- DevTools 的 Autofill 方法警告和可选 Source Map 401 不表示启动失败。

可选依赖：

```bash
# 非 Electron Browser Automation
uv pip install -e ".[browser]"
playwright install chromium

# 开发测试
uv pip install -e ".[dev]"
uv run pytest -q
```

Windows 源码安装见
[安装文档](docs/installation.zh-CN.md#windows)。

## 文档

- [安装](docs/installation.zh-CN.md)
- [使用](docs/usage.zh-CN.md)
- [配置](docs/configuration.zh-CN.md)
- [架构](docs/architecture.zh-CN.md)
- [开发](docs/development.zh-CN.md)
- [Browser Live View](docs/browser-live-view.zh-CN.md)
- [变更记录](CHANGELOG.md)
- [已完成的架构 Handoff](project-notes/COMPLETED-refactor-handoff.zh-CN.md)
- [Research Workbench 路线图](project-notes/research-workbench-roadmap.md)
- [已完成的 WebUI / Workbench UI 合并重构计划](project-notes/COMPLETED-webui-workbench-consolidation-refactor-plan.md)
- [已完成的 WebUI / Workbench UI 合并实施记录](project-notes/COMPLETED-webui-consolidation-implementation-log.md)
- [当前开发进度](project-notes/CONTEXT_DEV_PROGRESS.zh-CN.md)
- [设计 QA](project-notes/design-qa.zh-CN.md)
- [浏览器浮窗动态避让可行性研究](project-notes/browser-dynamic-layout-feasibility.md)

## 技术栈

- Python 3.12+、FastAPI、Uvicorn、SQLite
- uv（推荐）或 pip
- Ruff，行长 180
- OpenAI-compatible LLM API
- 内置 SimpleXNG
- Electron Chromium；非 Electron 可选 Playwright
- Electron + electron-builder + OS Keyring
- python-telegram-bot、WeChat
- Fernet 加密配置

## License

Apache 2.0
