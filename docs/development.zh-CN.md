# 开发

[English](development.md) · [简体中文](development.zh-CN.md)

## 调试

### Verbose Mode

记录每次 LLM 调用、Tool、Response、耗时和 Context Trace：

```bash
python -m cyrene.runtime.host --verbose
# 或
uv run python -m cyrene --verbose
```

日志写入 `data/debug_*.jsonl`，每行是一个 JSON Event，例如：

```json
{"type": "llm_call", "caller": "main_agent", "phase": "agent_run",
 "messages": [], "response": {}, "duration_ms": 423.0}
```

### Context Trace

`_ctx` Metadata 描述每个 Context Block 的来源，可从 API 查看：

```bash
curl http://localhost:4242/api/context-debug/events?limit=10
curl http://localhost:4242/api/context-debug/events/evt_3b22f9a5c0cb
```

CLI：

```bash
cyrene flow --session run_live --round round_xxx --id evt_3b22f9a5c0cb
```

classic UI 合并完成后，Context Trace 不再提供 Workbench 页面；请使用 API、
CLI 或 `python -m cyrene.observability.context_debug`。

历史 Event API 仍可用：

```bash
curl http://localhost:4242/api/events/list
curl http://localhost:4242/api/events/evt_3b22f9a5c0cb
```

`cyrene status` 显示 Daemon Health 与 Metric。Workbench Chat/Task Detail
展示实时 Agent、Tool、Subagent、Permission 和 Browser 状态；持久 Round
Trace 通过 `cyrene flow` 与 Event API 检查。

## 测试

pytest 使用 Async Support 和 60 秒 Thread Timeout。Fixture 会隔离 Runtime
Path。普通测试不得依赖真实 LLM Credential。

```bash
# 安装开发依赖
uv sync --all-extras

# 完整测试，并把未处理线程异常视为错误
uv run pytest -q \
  -W error::pytest.PytestUnhandledThreadExceptionWarning

# 单个文件
python -m pytest tests/test_context_trace.py -v

# 顶栏 Tab、固定资源、Library 拖动、导出兼容和标题栏布局
uv run pytest -q \
  tests/test_workbench_recent_session_tabs.py \
  tests/test_workbench_pinned_resources.py \
  tests/test_workbench_library.py \
  tests/test_chat_attachment_flow.py \
  tests/test_electron_titlebar_alignment.py
```

Release 相关检查：

```bash
node --test electron/app-use.test.js
python -m compileall -q src
git diff --check
```

真实 LLM、Telegram、WeChat、远程 MCP 等属于带凭据的手工集成测试。

最新稳定 Worktree 使用 Locked Environment 中的 Python `3.12.11`、
FastAPI `0.136.1`、Pydantic `2.13.4`，完整 1,402 项测试全部通过。

文档复核期间 OpenAPI Test 最初失败，是因为其 Hash 由 Ambient Python
3.13.12、FastAPI 0.115.8、Pydantic 2.12.5 采集，而不是使用 `uv.lock` 中
早已存在的版本。直接对比发现 10 个 Generator-level Difference：4 个
Upload Schema 用 `contentMediaType: application/octet-stream` 替代
`format: binary`，标准 `ValidationError` 增加 `input` 和 `ctx`；Route 和
Application Request Model 都没有变化。

因此，259 个 Operation 的严格 Hash 已在 Locked Environment 中重新采集，
Contract 还会显式检查 FastAPI/Pydantic Generator Version。没有过滤或忽略
任何 Schema Field；未来 Dependency Update 必须同时审查并更新 Version
Baseline 与 Hash。

## 项目约定

### Code Style

- Python 3.12+
- Ruff，行长 180
- 公共 Function 使用 Type Hint
- Async IO 使用 `asyncio`

### 模块与依赖

正式实现包：

- `agent`
- `workbench`
- `model_runtime`
- `learning`
- `runtime`
- `observability`
- `knowledge`
- `channels`
- `tooling`
- `tool_impl`

历史 Import 由 `cyrene.runtime.module_compat` 维护，不要创建重复顶层 Shim。
FastAPI Adapter 位于 `src/route/`；领域代码不得依赖 Route 或 Web UI。

跨模块通信使用：

- 显式 Function/Service 调用；
- `cyrene.observability.debug` Event Bus；
- `cyrene.runtime.inbox` Agent Message；
- SQLite/Document Store 持久化。

### 新增 Tool

1. 在匹配领域的 `cyrene/tool_impl/` 创建模块；
2. 导出 `TOOL_DEF` 和 Async `handler`；
3. 加入 Native Tool Module Registry；
4. Deferred Tool 分配稳定 Capability ID；Direct Tool 变更固定 Wire Contract
   必须有明确理由；
5. 增加 Policy、Schema、Actor 和 UI/Setting 测试。

Cyrene 自管理的公开能力只能加入 Main-only `cyrene_tools`。UI Action 必须在
`uiSurface` 注册稳定 Node/Action ID、受限语义 Handler、Risk 和 Outcome；禁止
增加 Selector、Script、Raw Event、任意坐标或 Route 调用逃生口。显式节点使用
`get_element` 与 DOM Projection 去重。Project/Chat/Data/Update/Lifecycle/
Session-message 后台 handler 必须保留在 `INTERNAL_ONLY_CONCRETE_TOOL_NAMES`。

源码和仓库内 Compiled WebUI Output 必须同步。聚焦验证为：

```bash
node --test electron/ui-surface.test.js electron/host-control.test.js
uv run pytest -q tests/test_app_control.py \
  tests/test_progressive_tool_packages.py \
  tests/test_tool_package_settings.py \
  tests/test_webui_consolidation_contract.py
```

MCP Server 通过 Settings 或 `cyrene mcp add` 配置。

## Electron 开发

```bash
uv sync --extra dev
cd electron
npm install
npm run dev
```

Electron 执行 `uv run cyrene --workbench --electron-mode`，因此开发模式与
手动源码启动共享同一个项目入口和环境。

## CI / Release

Repository 有两个 GitHub Actions Workflow：

- `.github/workflows/ci.yml` 在 Pull Request、推送到 `main` 或手工 Dispatch
  时执行。Linux Job 会同步 Lockfile 中全部 Extra、编译 `src`、执行完整
  pytest（把未处理 Thread Warning 提升为 Error）、构建 WebUI、确认
  `src/webui/static/app` 已同步提交，并执行 Electron App Use Test。
- `.github/workflows/release.yml` 在 Version Tag (`v*`) 或手工 Dispatch 时
  执行。手工触发仍可传入兼容 `ui_mode` 值 `workbench` 或历史 `agent`，但构建
  会把两者规范化为唯一 Workbench UI；随后为 macOS、Windows x64/ARM64 和
  Linux 构建 PyInstaller + Electron，并执行冻结产物 `--smoke-test`。

PR Workflow 不能替代真实平台打包 Smoke、视觉、带 Credential 外部服务、
原地升级或 Installer 检查；Tag 前仍须完成这些 Release Gate。冻结 Smoke
会导入关键编译依赖和全部历史模块 Alias。
