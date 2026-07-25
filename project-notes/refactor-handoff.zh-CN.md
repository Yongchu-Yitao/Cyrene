# Cyrene 架构重构 Handoff

[English](refactor-handoff.md) ·
[简体中文](refactor-handoff.zh-CN.md)

更新时间：2026-07-26

分支：`feature/project-literature-library`

基线提交：`5e9a0044`

## 1. 当前状态

包边界重构已经完成并可正常运行。Cyrene 的每个领域现在只有一个正式实现
位置，同时继续支持历史 Python 导入行为。

当前验收基线覆盖：

- 当前完整 pytest；
- 上一 commit 的全部功能测试，除一个源码文件形状断言；
- OpenAPI 和 Tool Wire 精确兼容；
- 真实 `cyrene start/status/API/stop`；
- 通过物理启动垫片运行 Electron 开发模式；
- 首次启动旧数据库文件名迁移；
- 重新构建的 PyInstaller 应用及动态导入别名。

本文描述需要保持的当前架构。后续改进不属于未完成的目录迁移。

## 2. 正式源码结构

```text
src/
├── cyrene/
│   ├── agent/               Agent loop 与内部公共 API
│   ├── workbench/           Workbench 业务服务
│   ├── model_runtime/       Provider 调用、消息、压缩和价格
│   ├── learning/            行为学习和 Learned Skill
│   ├── runtime/             启动、生命周期、持久化、调度
│   ├── observability/       Trace、Debug、Telemetry
│   ├── knowledge/           摄取、Embedding、检索、文献库存储
│   ├── channels/            Telegram 与微信适配器
│   ├── tooling/             Tool Catalog、Policy、Wire、Backend
│   ├── tool_impl/           按领域划分的原生 Tool 实现
│   ├── config.py            稳定配置门面
│   ├── call_llm.py          稳定模型调用门面
│   ├── browser.py           Browser Runtime/门面
│   ├── subagent.py          Subagent 编排
│   ├── memory.py            Memory Context 门面
│   ├── cli.py               已安装的 `cyrene` HTTP Client
│   ├── tools.py             公共 Tooling 门面
│   ├── __init__.py          安装惰性历史导入别名
│   ├── __main__.py          `python -m cyrene`
│   └── local_cli.py         Electron/物理文件启动垫片
├── route/                   FastAPI HTTP/WebSocket 适配器
├── webui/                   应用生命周期、认证和静态资源
└── workbench-webui/         Workbench 前端源码
```

`local_cli.py` 不是业务实现。Electron 开发模式会执行这个确切路径，因此删除
它会破坏真实启动流程。它最终委托给 `cyrene.runtime.host`。

## 3. 依赖方向

```text
Electron / Web UI / channels / CLI
                 │
                 ▼
          route + webui adapters
                 │
                 ▼
 agent / workbench / runtime / knowledge / learning
                 │
                 ▼
 model_runtime / tooling / observability / persistence
```

规则：

1. 领域服务不得导入 FastAPI Route 或前端模块。
2. Route 负责验证/翻译请求并调用领域服务。
3. `webui.server` 通过 `route.registry` 组合 FastAPI 应用。
4. 具体 Tool 位于 `tool_impl`；发现、Policy、Schema 稳定性和执行属于
   `tooling`。
5. 新实现必须进入正式领域目录，不得放回历史顶层模块名。

架构测试会约束 `cyrene/` 顶层允许存在的目录和文件。

## 4. 公共 API 与历史 Python API

稳定的物理公共模块包括：

- `cyrene.config`
- `cyrene.call_llm`
- `cyrene.browser`
- `cyrene.subagent`
- `cyrene.memory`
- `cyrene.cli`
- `cyrene.tools`
- `cyrene.agent`

`cyrene.db`、`cyrene.pattern`、`cyrene.scheduler`、
`cyrene.workbench_runtime` 等历史路径由
`cyrene.runtime.module_compat` 处理。

兼容 Loader 会：

- 惰性导入 Target；
- 返回完全相同的正式模块对象；
- 保持 monkeypatch 行为；
- 恢复正式的 `__name__`、`__spec__` 等元数据；
- 提供虚拟 `cyrene.modules` Namespace；
- 支持仍通过 `python -m` 调用的可执行别名。

不要重新创建单文件 Wrapper。新增历史路径时应添加 Mapping 和兼容测试。

## 5. Runtime 组合

共享 Runtime 由以下组件构成：

- `runtime.context`：解析后的 Runtime Path 和进程上下文；
- `runtime.application`：Manager/Task 所有权与关闭；
- `runtime.bootstrap`：有序初始化和外部服务；
- `runtime.lifecycle`：取消和后台工作清理；
- `runtime.host`：交互、Web、Electron 和冻结模式；
- `runtime.paths`：源码/打包/用户数据路径解析。

启动顺序：

```text
解析路径
  → 创建 Runtime 目录
  → 必要时迁移旧数据库
  → 初始化正式数据库
  → 初始化 SOUL/Inbox/短期记忆/Learning
  → 启动 Scheduler 与可选集成
  → 提供选定界面
```

Scheduler、后台 Task、Browser/Search/MCP 进程和其他 Manager 均由生命周期
管理。新的长生命周期资源必须注册到 Application Lifecycle。

## 6. 数据库文件名迁移

正式数据库：

```text
store/cyrene.runtime.database
```

历史数据库：

```text
store/cyrene.db
```

`runtime.database_migration.migrate_legacy_database()` 在数据库初始化前运行：

1. 使用 SQLite backup API，包含已提交 WAL 数据；
2. 写入临时目标并执行 `PRAGMA quick_check`；
3. 写入 `legacy-database-filename-v1` Marker；
4. 只替换不存在或已初始化但无行数据的 Target；
5. 保留 Source 作为回滚副本；
6. 绝不覆盖已有数据的 Target；
7. 重复启动保持幂等。

如果新旧数据库都包含数据但没有迁移 Marker，启动会报出可操作错误，不会静默
选择或覆盖。

## 7. 持久化边界

| 数据 | 位置 |
|---|---|
| 主 Runtime 状态 | `store/cyrene.runtime.database` |
| 项目 Knowledge/Library | `store/kb_<workspace>.db` |
| 加密配置 | `data/config.enc` |
| 行为学习 | `data/behavior-learning.db` |
| SOUL | `workspace/SOUL.md` |
| 短期记忆 | `data/short_term.json` |
| Debug Trace | `data/debug_*.jsonl` |
| 非 Electron Browser Profile | `data/browser_profile/` |

`runtime.sqlite_json` 和 `workbench.store` 提供安全 JSON/Document
持久化 Helper。后续 Repository 应建立在这些边界上，不要在 Route 中新增临时
SQLite 连接。

## 8. Tooling 契约

模型面对的是 Tool 控制面，不是每个实现的完整列表：

- Direct Tool 保持固定 Wire Bundle；
- 启用的 Tool Package 暴露稳定 Gateway；
- Package 使用 `discover → describe → invoke`；
- Catalog Snapshot 在每个 Agent Run 冻结；
- Actor Policy 区分 Main、Execution Agent 和 Subagent；
- 过期或关闭的调用在 Runtime 被拒绝。

上一 commit 与当前 Registry 都是 94 个 Tool 定义和 Handler。移动 Tool 时：

1. 保持 Capability ID 和 Concrete Name；
2. 保持 Schema 与 Result Protocol；
3. 更新正式 Native Module Registry；
4. 保持 Policy Metadata 和 Actor 限制；
5. 运行 Catalog、Wire、Package Setting 和兼容测试。

## 9. Route 与 Workbench 边界

所有 HTTP/WebSocket 组合位于 `src/route/`：

- `route.registry` 是组合入口；
- `route.agent` 处理 Chat、Session、Browser 和 Collaboration；
- `route.workbench` 处理 Project、Task Session、Knowledge、Memory、
  Schedule 和 Chat；
- `route.system` 处理 Event、Shell、Update 和 Instance Identity；
- Settings、Code、Map、Entity、Task、Channel 有独立 Adapter。

Workbench 业务逻辑位于 `cyrene.workbench`。`workbench.runtime` 是正式组合
模块，不是历史顶层文件。新业务应进入聚焦服务，仅在稳定消费者需要时导出。

## 10. Build 与入口

| 入口 | 用途 |
|---|---|
| `cyrene start` | Detached Workbench daemon |
| `cyrene status` / `cyrene stop` | Daemon Client |
| `python -m cyrene --workbench` | 前台 Workbench Web UI |
| `python -m cyrene --agent` | 前台 Classic UI |
| `python -m cyrene.runtime.host` | 交互 Headless REPL |
| `electron: npm run dev` | Electron 开发应用 |
| 冻结 `Cyrene --launch-web` | Electron/冻结 Web Backend |

PyInstaller Spec 会枚举所有本地 Python 模块，因为 Tool 与 Adapter 使用动态
导入。冻结 smoke test 会导入关键编译依赖并验证所有历史别名。

## 11. 验证基线

| 验证 | 结果 |
|---|---|
| 当前 pytest | 1,381 passed |
| 上一 commit 功能 pytest | 1,286 passed |
| 上一源码形状测试 | 1 个按要求排除 |
| Electron App Use | 44 passed |
| OpenAPI | 259 个操作，规范化 Schema 未变化 |
| Tool Registry | 94 个定义/Handler，未变化 |
| CLI 生命周期 | start/status/API/stop 通过 |
| 源码 Electron | Window/Backend/Static/API 启动通过 |
| 旧 DB 迁移 | Source 保留、数据复制、Marker/quick-check 通过 |
| PyInstaller | Build、60 个 Alias、Web/API/Migration/Shutdown 通过 |
| Python Compile 与 Diff Check | 通过 |

排除的旧测试只是读取 `src/cyrene/pattern.py` 文本。这个文件不属于正式目录，
功能性 Import 仍受支持。

## 12. 后续工作

以下是未来改进，不是未完成的迁移步骤。

### P1

- 建立显式 Workbench 领域模型和 Repository；
- 把 `subagent.py` 拆为 Typed State Machine 和协调服务；
- 把 `browser.py` 拆为 Session、Transport、Policy、Capture；
- 拆分行为学习的 Storage、Candidate、Version、Execution；
- 减少导入时配置/全局变更；
- 增加 pytest、Ruff、Node、打包 smoke 的 PR CI。

### P2

- 按 Task Execution、Proactive、Steward、Heartbeat、Delivery、Cleanup
  拆分 Scheduler；
- 扩展项目级 Typed Exception；
- 继续拆分前端大模块；
- 降低 Electron Main Process 职责。

### Research Workbench

项目隔离 Library 已实现。Experiment、可复现 Run、Manuscript 和 Provenance
仍属于 `research-workbench-roadmap.md` 路线图。

## 13. 变更检查清单

```bash
uv run pytest -q
node --test electron/app-use.test.js
python -m compileall -q src
git diff --check
```

影响发布时还应：

1. 构建 Web UI 静态资源；
2. 在隔离数据目录运行 `cyrene start/status/stop`；
3. 运行 Electron 开发模式；
4. 构建 PyInstaller 并执行 `--smoke-test`；
5. 启动冻结 Web Backend 并检查 `/openapi.json`；
6. 使用真实 SQLite Fixture 验证旧数据库迁移。

## 14. 禁止回归

- 不要为了源码布局测试恢复已删除的顶层实现文件。
- Electron 仍直接执行时不要删除 `local_cli.py`。
- 数据库迁移不得使用普通文件复制。
- 领域服务不得导入 Route/Web UI。
- 不要把所有具体 Tool Schema 直接暴露给模型。
- Tool Package 设置不得改变运行中 Agent 的冻结 Capability Snapshot。
- 没有凭据和真实集成测试时，不要声称外部 LLM/Channel/Provider 完全兼容。
