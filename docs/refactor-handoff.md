# Cyrene 重构 Handoff

更新时间：2026-07-25
当前分支：`feature/project-literature-library`
范围：除 `route` 重构之外的 Cyrene 架构重构

## 1. 当前结论

目录归类和第一轮依赖治理已经完成，但完整重构尚未完成。

现在的代码已经从“根目录堆放大量模块”迁移到 `agent/`、`workbench/`、
`model_runtime/`、`learning/`、`runtime/`、`observability/` 等领域包，并且
静态导入图中没有多模块循环依赖。与此同时，公共 API、运行期状态所有权、
Workbench 主体拆分、LLM、Subagent、Browser、Learning、领域模型和持久化边界
仍有大量工作。

下一位开发者不应继续机械移动文件。正确顺序仍然是：

```text
固化当前迁移基线
  → 收口公共 API
  → 引入 RuntimeContext
  → 拆分 Workbench
  → 统一 Host 生命周期
  → 拆分 LLM / Subagent / Browser / Learning
  → 建立领域模型和 Repository
  → 统一异常与日志
  → 前端 / Electron
  → 删除兼容门面
```

## 2. 当前验证基线

截至本 handoff 创建前，以下检查已通过：

```text
pytest 严格模式：1303 passed
多模块静态循环依赖：0
失效内部导入：0
完全重复长函数组：0
compileall：通过
git diff --check：通过
```

全量测试命令：

```bash
.venv/bin/python -m pytest -q \
  -W error::pytest.PytestUnhandledThreadExceptionWarning
```

架构守卫位于 `tests/test_architecture_boundaries.py`，当前覆盖：

- `cyrene.agent`、`cyrene.learning`、`cyrene.tooling` 包入口的惰性加载；
- 源码树中不存在多模块静态循环依赖。
- 跨模块私有导入按来源设置单调下降 budget；
- Agent 兼容门面的私有导出数量不得增长。

尚未执行完整 PyInstaller、Electron 打包和跨平台 smoke test。发布构建不能仅凭
Python 测试结果判定通过。

## 3. 已完成的工作

### 3.1 包归类

已经建立并实际使用：

```text
src/cyrene/
├── agent/
├── workbench/
├── model_runtime/
├── learning/
├── runtime/
├── observability/
├── knowledge/
├── channels/
├── tooling/
└── tool_impl/
```

没有引入 `common/`、`utils/`、`helpers/` 或集中式 `storage/` 杂物箱。

以下职责已经归入更合理的位置：

- `context_trace`、`context_debug`、`debug` → `observability/`；
- Behavior Learning 与 Claude Code 学习 → `learning/`；
- 启动、路径、配置存储、数据库、Scheduler、生命周期 → `runtime/`；
- Workbench Chat、Memory、Knowledge、Inbox、Goal Loop、通知和变更记录
  → `workbench/`；
- Shell、Claude Code、MCP、SearXNG、App Use → `tooling/backends/`；
- `workspace_changes` 保留在 Workbench 领域；
- Runtime 通知与 Workbench 通知继续分开。

### 3.2 依赖与包入口

- `agent`、`learning`、`tooling` 使用惰性包入口，避免导入包时初始化完整运行图；
- `route` 包入口也改为惰性注册；
- 工具 Catalog、Gateway、Policy、Executor 的静态循环已经解除；
- Workbench 内部循环通过延迟服务获取解除；
- Agent 最终回复从 `guidance.py` 提取到 `agent/replies.py`；
- 消息身份、合并和去重逻辑提取到 `agent/message_utils.py`；
- 机械上下文压缩提取到 `model_runtime/compaction.py`。

### 3.3 基础设施抽取

已经建立：

- `runtime/bootstrap.py`：共享初始化与外部服务启停原语；
- `runtime/lifecycle.py`：跨模块后台任务关闭；
- `runtime/task_lifecycle.py`：后台 Task 强引用、异常消费和取消等待；
- `runtime/paths.py`：跨平台 `AppPaths`；
- `runtime/sqlite_json.py`：SQLite JSON 编解码；
- `runtime/memory/archive_format.py`：归档格式解析；
- `knowledge/extractors.py`：Office/XML 内容提取；
- `knowledge/embedding_client.py`：Embedding Provider HTTP 传输；
- `tooling/backends/shell_registry.py`：外部 Shell 注册表；
- `workbench/session_view.py`：会话展示纯函数；
- `workbench/session_metrics.py`：会话统计与格式化纯函数；
- `workbench/compat.py`：Workbench 延迟兼容服务入口。

### 3.4 Tooling 控制面

`tooling/` 已具备相对清晰的边界，不应推倒重来：

- `ToolSpec`、`ToolCatalogSnapshot`、`ToolExecutionContext` 和 `ToolResult`
  已经是明确类型；
- Catalog、Snapshot、Wire、Gateway、Policy、Executor 各自有稳定职责；
- `cyrene/tools.py` 是显式公共门面；
- 包入口保持惰性；
- 工具实现继续按一工具一文件组织。

## 4. 当前量化指标

以下指标只统计 `src/cyrene` 和 `src/webui` Python 源码；`route` 不计入重构范围。

| 指标 | 当前值 | 说明 |
|---|---:|---|
| 跨模块私有符号导入 | 146 | 43 条 import 语句，97 个不同的私有导出；测试 budget 已收紧到当前值 |
| `cyrene.agent.state` 私有符号消费者 | 55 个符号导入 | 当前最大耦合中心，已由 224 降低 |
| `cyrene.agent.session` 私有符号消费者 | 22 个符号导入 | 当前第二大耦合中心 |
| 宽泛 `except Exception`/裸捕获 | 574 | 分布于 98 个模块 |
| 直接连接 SQLite 的模块 | 16 | 约 122 个连接调用点 |
| `RuntimeContext` 类型 | 0 | 只有局部 `SessionContext` 和 `ToolExecutionContext` |
| Workbench 领域 `TypedDict` | 0 | 业务结构仍主要是 `dict[str, Any]` |
| Workbench 领域 Pydantic `BaseModel` | 0 | Route schema 不能替代领域模型 |
| 多模块静态循环依赖 | 0 | 已有机械测试守卫 |

主要巨型模块：

| 文件 | 行数 | 当前混合职责 |
|---|---:|---|
| `cyrene/workbench/runtime.py` | 9,482 | Project、Session、规划、执行、搜索、Artifact、Dashboard、UI 投影 |
| `cyrene/learning/engine.py` | 4,301 | Repository、Tracking、Candidate、版本、脚本和执行 |
| `cyrene/subagent.py` | 2,848 | 注册表、状态、消息、执行、总结、回收 |
| `cyrene/browser.py` | 2,525 | 安全、Electron RPC、Playwright、HTTP 降级和页面状态 |
| `cyrene/model_runtime/client.py` | 1,690 | Candidate、请求、重试、流、遥测和错误 |
| `cyrene/runtime/database.py` | 1,507 | 多领域数据库操作 |
| `cyrene/agent/agent.py` | 1,438 | 主 Agent 执行循环 |
| `cyrene/runtime/scheduler.py` | 1,257 | Scheduler、Proactive、Heartbeat、Steward 和投递 |

## 5. P0–P3 完成度

| 优先级 | 重构项目 | 状态 | 尚未完成 |
|---|---|---|---|
| P0 | 公共 API 与依赖边界 | 部分完成 | 私有导入已由 493 降至 146；Agent 门面仍暴露 120 个私有名字 |
| P0 | RuntimeContext 与全局状态 | 部分完成 | 有 `SessionContext`、`AppPaths` 和 `ToolExecutionContext`，但没有应用级 Context/服务所有权 |
| P0 | Workbench 业务拆分 | 部分完成 | 支撑模块已迁移，主体仍集中在 9,482 行 `runtime.py` |
| P0 | 应用启动与关闭 | 部分完成 | 有共享 bootstrap/lifecycle，但 Host 模式仍重复编排启动与关闭 |
| P1 | LLM 调用管线 | 部分完成 | 包已建立，505 行 `call_llm()` 未拆 |
| P1 | Subagent 状态机 | 主体未完成 | 849 行 `_run_subagent()` 与 dict 注册表仍在 |
| P1 | Browser 子系统 | 主体未完成 | 1,026 行 `_BrowserSession` 和多 Transport 仍在同一文件 |
| P1 | Behavior Learning | 仅完成归类 | 4,301 行 Engine 尚未按 Repository/Candidate/Version/Executor 拆分 |
| P1 | 配置系统 | 未完成 | `config.py` 仍在导入时读取存储、写入 `os.environ` 并修改模块全局 |
| P1 | 持久化边界 | 部分完成 | 有共享 DB/JSON 基础，但 16 个模块仍直接连接 SQLite |
| P1 | 业务数据模型 | 未完成 | Project、Session、Plan、Chat、Run 仍以松散 dict 表达 |
| P2 | Scheduler 拆分 | 未完成 | Proactive、Heartbeat、Steward、投递和上下文仍混在 `scheduler.py` |
| P2 | 异常与可观测性 | 部分完成 | Observability 已归类；异常分类和宽泛捕获治理未完成 |
| P2 | 前端模块化 | 未完成 | Workbench 三个主要前端文件规模基本未变 |
| P2 | Electron 主进程 | 未完成 | `electron/main.js` 仍有 4,025 行 |
| P2 | 测试结构 | 部分完成 | 增加架构测试，但 `test_runtime_fixes.py` 仍有 5,941 行且大量测试依赖私有 monkeypatch |
| P3 | 构建、版本与文档 | 部分完成 | 发布 smoke test 存在；没有常规 PR CI，架构文档仍引用大量已删除旧路径 |

## 6. 关键未完成项

### 6.1 公共 API 仍未收口

`agent/__init__.py` 的惰性门面解决了导入副作用，但没有解决 API 设计：

```text
静态导出：140
其中私有名字：120
```

当前最大私有依赖来源：

```text
cyrene.agent.state    55
cyrene.agent.session  22
cyrene.agent.prompts  20
cyrene.agent.message  15
cyrene.agent.replies   9
cyrene.agent           7
```

`cyrene.tooling.runtime_support` 的私有符号导入已由 104 降为 0。Agent
运行上下文已有 `agent/context.py` 的公开查询、绑定和权限命令，
模型调用已有 `agent/model_service.py` 的普通、流式和最终回复 usage 接口。

需要先定义公共服务或查询/命令接口，再替换调用方：

- Session 状态读取与更新；
- 当前 Run、Round、Workspace 和权限上下文；
- Agent 启动、取消、等待和结果；
- Pending Question；
- Tool 执行上下文；
- 消息规范化和模型响应文本；
- Subagent 注册表、查询和生命周期命令。

不要把所有 `_private_name` 简单改成公开名字。应先确认它属于：

```text
稳定公共 API
包内 API
运行期 Context 字段
Repository 方法
纯函数
```

`call_llm.py` 当前通过 `sys.modules[__name__] = client` 保持 monkeypatch 兼容，
不是显式公共门面。`browser.py` 和 `subagent.py` 仍是实现模块，不是兼容门面。

### 6.2 RuntimeContext 尚未建立

已存在的类型：

- `runtime.paths.AppPaths`：应用路径；
- `agent.state.SessionContext`：部分 Session 任务与锁；
- `tooling.types.ToolExecutionContext`：单次 Tool 调用上下文。

仍缺少应用级 `RuntimeContext`，至少需要明确拥有：

- `AppPaths` 和数据库路径；
- 只读配置快照；
- Host 模式与外部服务句柄；
- Scheduler、Chat Run、Goal Loop、Browser、MCP 等 Manager；
- 后台 Task registry；
- 关闭顺序和幂等状态。

不要把所有状态塞进一个全局对象：

- 应用级资源由 `RuntimeContext` 拥有；
- Session 状态继续由 `SessionContext` 拥有；
- 单次 Run/Round 使用不可变 Context 或 `ContextVar`；
- Tool 调用继续使用 `ToolExecutionContext`；
- Project/Session 业务状态保存在 Repository，而不是 RuntimeContext。

`model_runtime/compaction.py` 已经提取机械消息压缩，但它还不是完整的运行期
handoff。当前压缩主要折叠对话消息，没有统一的结构化重注入契约来保证：

- Active Plan/Goal；
- Approval state；
- 已加载指令、Skill 和 Connector 状态；
- 已修改 Artifact；
- 错误与下一步；
- Workflow/Goal Loop checkpoint。

后续应把这些状态保存在 Prompt 外，并由 Context Builder 在压缩后重挂载。

### 6.3 Workbench 主体尚未拆分

目标包中的这些职责仍未成为独立模块：

```text
models.py
projects.py
sessions.py
planning.py
execution.py
artifacts.py
search.py
dashboard.py
```

当前 `workbench/runtime.py` 的自然拆分边界：

1. `models.py`
   - Project、Session、PlanStep、AcceptanceCriterion、Artifact、Run；
   - ID、Status、Priority 和 Revision 类型；
   - 只放结构与轻量校验。
2. `projects.py` / `sessions.py`
   - 默认 Project；
   - 查找、创建、更新、删除；
   - Invariant 修复；
   - 不包含 FastAPI。
3. `planning.py`
   - Init Form；
   - Explore Agent；
   - Plan 生成、校验、修订和验收标准。
4. `execution.py`
   - Task 执行上下文；
   - Agent 调用；
   - 验证和状态转换；
   - Goal Loop 只调用这里的公共接口。
5. `artifacts.py`
   - Workspace Snapshot；
   - Git Diff；
   - 文件变更和 Artifact 提升/清理。
6. `search.py`
   - Workbench 聚合搜索。
7. `dashboard.py`
   - UI Dashboard、Session 投影、Flow、状态统计。

`workbench/runtime.py` 在过渡期只能作为显式兼容门面，不能继续承载新业务。

### 6.4 Host 生命周期仍有重复

`runtime/bootstrap.py` 和 `runtime/lifecycle.py` 已经建立，但
`runtime/host.py` 的 Electron、Web、GUI 等模式仍分别编排：

- `initialize_runtime()`；
- `start_external_services()`；
- Update Check；
- Scheduler；
- Web Server；
- `stop_external_services()`；
- 后台任务关闭。

下一步应由一个应用级 async 生命周期对象统一执行：

```text
create context
  → initialize core
  → start optional services
  → start selected host
  → wait
  → stop accepting work
  → drain/cancel background tasks
  → stop external services
  → close clients/managers
```

`local_cli.py` 已经是薄兼容启动器，不要重新把 Host 逻辑搬回去。

### 6.5 LLM 管线尚未拆分

`model_runtime/client.py` 已包含 `errors.py`、`messages.py`、`pricing.py` 和
`compaction.py` 的支撑，但核心仍混合：

- Candidate 规范化和排序；
- Context Window 过滤；
- Endpoint 轮换与冷却；
- HTTP Client 生命周期；
- Payload 构建；
- 同 Endpoint 重试；
- Candidate Fallback；
- Streaming；
- DSML Tool Call 解析；
- Telemetry 和事件发布；
- 错误转换。

建议按真实依赖拆为：

```text
model_runtime/
├── candidates.py
├── request.py
├── response.py
├── retry.py
├── streaming.py
├── telemetry.py
├── errors.py
├── messages.py
└── client.py
```

`client.py` 最终只负责编排，并保持当前 `call_llm()` 签名。

### 6.6 Subagent 仍不是显式状态机

当前状态使用字符串常量和 `dict[str, Any]` 注册表：

```text
running → waiting → resumed → done / timeout / incomplete
```

需要拆为：

```text
subagents/
├── models.py
├── registry.py
├── messaging.py
├── runner.py
├── summarizer.py
└── reaper.py
```

迁移顺序：

1. 建立 `SubagentStatus` 和 `SubagentRecord`；
2. 为状态转换建立唯一函数和非法转换测试；
3. 把 Registry 持久化与锁移出运行循环；
4. 把消息收发、总结、超时回收移出 `_run_subagent()`；
5. 最后把 `subagent.py` 变成显式兼容门面。

### 6.7 Browser Transport 尚未分层

`browser.py` 同时拥有：

- SSRF/URL 安全；
- Electron RPC；
- Playwright Session；
- HTTPX 降级；
- 页面状态与缓存；
- 导航、截图、输入、滚动和接管。

建议边界：

```text
browser_runtime/
├── security.py
├── models.py
├── session.py
├── electron.py
├── playwright.py
├── http_fallback.py
└── facade.py
```

先提取 Transport Protocol 和安全策略，再移动实现。不要先把 `browser.py`
转换成同名包。

### 6.8 Learning 只完成了目录迁移

`learning/engine.py` 仍同时拥有：

- Schema 和迁移；
- Turn/Action Tracking；
- Browser Event Tracking；
- Tool Chain；
- Candidate 聚类与分配；
- Script 生成；
- Skill 版本与 Patch；
- 参数提取；
- Learned Skill 执行。

建议拆分顺序：

```text
repository.py
  → tracking.py
  → candidates.py
  → scripts.py
  → versions.py
  → executor.py
```

`learning/facade.py` 已经提供公开 API，可以作为迁移期间的稳定调用面。

### 6.9 配置系统仍有导入副作用

`config.py` 当前会：

- 导入时读取加密配置；
- 把配置写入 `os.environ`；
- 计算所有配置常量；
- 运行时再修改模块全局；
- 混合路径、Provider、Bot、Scheduler 和 UI 配置。

建议先建立不可变配置快照：

```text
AppConfig
├── paths
├── model
├── embeddings
├── channels
├── scheduler
└── ui
```

兼容 `config.py` 暂时从快照显式导出常量。配置更新应写入 Store，并在明确的
重载边界产生新快照，而不是到处修改模块变量。

### 6.10 持久化边界仍分散

当前 16 个模块直接连接 SQLite。已有 `runtime/database.py`、
`runtime/sqlite_json.py` 和 `workbench/store.py`，但尚未形成领域 Repository。

建议：

- 共享连接、事务、迁移、Row/JSON 编解码基础设施；
- Agent Budget、Knowledge、Learning、Workbench、Entity 各自拥有 Repository；
- 不把所有 SQL 继续堆进 `runtime/database.py`；
- Repository 返回领域模型或明确 DTO；
- 上层业务不直接依赖 `aiosqlite.Row`；
- 对事务、迁移、锁冲突、取消和关闭建立测试。

### 6.11 业务模型尚未建立

Pydantic 已经是依赖，但 `cyrene` 业务层没有 `BaseModel`，Workbench 也没有
`TypedDict`。Route 请求模型不能直接充当领域模型。

优先建立：

- `Project`；
- `Session`；
- `PlanStep`；
- `AcceptanceCriterion`；
- `Chat` / `ChatMessage`；
- `Run` / `RunEvent`；
- `Artifact`。

先为存储边界建立 parse/serialize，再逐步替换内部 `dict[str, Any]`。
不要一次性重写所有调用点。

### 6.12 Scheduler 仍需按职责拆分

`runtime/scheduler.py` 仍混合：

- 定时任务注册和 APScheduler；
- 主动 Heartbeat；
- Proactive Context；
- Steward；
- 消息投递；
- Workbench 活动检查。

建议保留 `scheduler.py` 作为时间调度器，把业务行为提取到
`proactive.py`、`steward.py` 和独立 Delivery Service。

### 6.13 异常体系只完成了 Model Runtime

已有 `model_runtime/errors.py`，但项目级错误分类尚未建立。

当前仍有 574 个宽泛异常捕获。不要机械把它们全部改成具体异常；先按边界定义：

- Configuration；
- Persistence；
- Provider/External Service；
- Policy/Permission；
- Validation/Protocol；
- Lifecycle/Shutdown；
- Retryable 与 Non-retryable。

要求：

- 边界层把第三方异常转换为领域异常；
- Tool/API 返回结构化错误；
- 日志不重复打印同一异常；
- Background Task 异常必须被消费；
- 用户错误、外部失败和内部错误使用不同语义。

Observability 包已建立，不需要再次移动 Trace/Debug 文件。

### 6.14 前端和 Electron 基本未开始

Workbench 前端当前规模：

| 文件 | 行数 |
|---|---:|
| `src/workbench-webui/workbench-chat.jsx` | 7,271 |
| `src/workbench-webui/workbench.jsx` | 5,902 |
| `src/workbench-webui/workbench.css` | 17,678 |

应在后端领域 API 稳定后再拆：

- Chat state、stream、message timeline、attachments、tool cards；
- Project/Session navigation；
- Goal Loop 和 Plan；
- API client；
- 共享 hooks/state；
- CSS tokens、layout、components 和 domain styles。

Electron 当前规模：

| 文件 | 行数 |
|---|---:|
| `electron/main.js` | 4,025 |
| `electron/app-use.js` | 1,301 |

建议拆分：

- Python Process Supervisor；
- Window/View 管理；
- Browser RPC；
- Local Auth；
- Tray/Menu；
- Settings；
- IPC 注册。

### 6.15 测试仍与私有实现强耦合

当前：

```text
tests/test_runtime_fixes.py             5,941 行
tests/test_workbench_frontend_logic.py  3,359 行
tests/test_workbench_init_plan.py       2,192 行
monkeypatch/patch 调用                   约 1,166
疑似私有属性 monkeypatch                约 963
```

后续应：

- 按领域拆分 `test_runtime_fixes.py`；
- 优先通过公共 Facade、Repository 和 Context fixture 注入依赖；
- 保留少量兼容门面测试；
- 为状态机、事务和生命周期增加独立单元测试；
- 为每次生产问题增加回归测试；
- 扩展架构测试，禁止新增私有跨域导入。

### 6.16 构建、版本和文档仍未统一

当前情况：

- 只有 Release Workflow，没有常规 PR 测试 Workflow；
- Release 会执行 WebUI 构建、PyInstaller/Electron 打包和 packaged smoke test；
- Python 版本为 `0.7.0b1`，Electron 使用 SemVer `0.7.0-beta.1`；
- `docs/architecture.md` 的 Project Structure 仍引用大量已删除旧模块；
- 本地 `build/build/` 存在忽略的旧 PyInstaller 产物，不可作为当前源码验证证据。

建议增加：

- 普通 PR CI：Python 测试、架构守卫、compile、diff/format 检查、WebUI build；
- Electron Node 单元测试；
- Python/Electron 版本一致性脚本；
- 构建 manifest 和静态资源检查；
- 更新 Architecture、Development、Installation 和 Usage 文档。

## 7. 推荐下一阶段执行包

### Work Package 0：固化当前迁移

目标：把当前“删除旧文件 + 未跟踪新包”作为一个原子迁移检查点。

验收：

- 所有旧路径删除和新路径新增成对出现；
- 全量严格测试通过；
- `git diff --check` 通过；
- 架构守卫通过；
- Review 后创建迁移 checkpoint commit。

### Work Package 1：公共 API Budget

目标：阻止当前 146 个私有符号导入继续增长，并继续处理剩余热点。

当前进展：budget 与 Agent 门面守卫已建立；`agent.state` 已从 224 降至
55；`tooling.runtime_support` 已从 104 降至 0。剩余工作集中在
`agent.session`、`agent.prompts`、`agent.message`、Agent 兼容门面和
`agent.state` 的包内实现依赖。

步骤：

1. 把当前私有导入清单保存为架构 allowlist/budget；
2. 禁止新增跨包私有导入；
3. 为 `agent.state` 建立公共 Context/Query/Command API；
4. 为 `tooling.runtime_support` 建立公共 Policy/Execution API；
5. 逐个删除兼容门面的私有导出。

验收：

- 私有导入总量单调下降；
- `agent/__init__.py` 不再新增 `_private_name`；
- 不产生新循环依赖；
- 全量测试通过。

### Work Package 2：RuntimeContext

目标：明确应用级资源和 Manager 的生命周期所有权。

步骤：

1. 新建 `runtime/context.py`；
2. 将 `AppPaths`、DB Path、Config Snapshot、Manager、Task Registry 纳入；
3. `runtime/bootstrap.py` 返回 Context；
4. Host/Web/CLI 使用同一 Context；
5. 逐步取消测试对模块全局的 monkeypatch。

验收：

- Runtime 初始化和关闭幂等；
- 后台任务都能追踪和关闭；
- 没有跨 Event Loop 残留 Task；
- 严格线程异常测试通过。

### Work Package 3：Workbench 模型与 Store

目标：在移动业务逻辑前建立稳定数据边界。

步骤：

1. `workbench/models.py`；
2. `workbench/projects.py`；
3. `workbench/sessions.py`；
4. Repository parse/serialize；
5. 保持 `runtime.py` 兼容调用。

验收：

- Project/Session 不再由任意 dict 字段拼装；
- Revision、Status、Plan dependency 有集中校验；
- Store round-trip 测试通过。

### Work Package 4：Workbench Planning/Execution

目标：把规划和执行从 UI/Compatibility Runtime 中移出。

步骤：

1. `planning.py`；
2. `artifacts.py`；
3. `execution.py`；
4. `search.py`；
5. `dashboard.py`；
6. `runtime.py` 只保留显式转发。

验收：

- `runtime.py` 不再新增业务函数；
- 每个服务不依赖 FastAPI；
- Workbench 全部回归测试通过；
- Route 行为保持不变。

### Work Package 5：统一 Host 生命周期

目标：Electron、Web、GUI、CLI 使用同一个启动/关闭流程。

验收：

- 每种 Host 只声明能力差异，不重复资源管理；
- Scheduler、MCP、Search、Update、Browser、Chat Run、Goal Loop 都有 Owner；
- 关闭顺序有测试。

后续 Work Package 再依次处理：

```text
LLM
  → Subagent
  → Browser
  → Learning
  → Config
  → Repository
  → Scheduler
  → Exceptions
  → Frontend
  → Electron
  → Build/Docs
  → 删除兼容层
```

## 8. Route 范围约束

用户已声明 Route 重构完成，本 handoff 不把 Route 作为下一阶段工作。

但是现有 Route 是 Workbench 兼容面的固定消费者：

```text
19 个 Route 模块使用：
from cyrene.workbench.runtime import *

4 个 Workbench Route 模块使用：
globals().update(vars(_service))
```

因此拆分 Workbench 时：

- 不要静默删除 `workbench.runtime` 中 Route 使用的名字；
- 先建立显式兼容转发；
- 用测试确认 monkeypatch 和 Handler 行为；
- 除非用户重新开放 Route 范围，不在本轮顺便修改 Route。

这是一项迁移约束，不代表应继续扩大 `import *`。

## 9. 当前 Git 工作区

工作区很脏，且新包大多仍是未跟踪目录：

```text
Modified：210
Deleted：58
Untracked：24 个文件或目录入口（包含本 handoff）
状态条目合计：292
```

重要事实：

- 删除的旧根模块和未跟踪的新包是同一轮迁移；
- `git diff --stat` 不包含未跟踪新文件，因此会显示夸张的净删除；
- 不要执行 `git reset --hard`、`git checkout --` 或清理未跟踪目录；
- 不要只提交删除旧文件而漏掉新包；
- 当前没有为本轮执行 stage 或 commit。

需要特别成对保留：

```text
旧 behavior_learning.py  ↔ learning/
旧 llm/db/config_store   ↔ model_runtime/、runtime/
旧 context_* / debug    ↔ observability/
旧 workbench_*          ↔ workbench/
旧 shell/cc/mcp/search  ↔ tooling/backends/
```

## 10. 下一位开发者的起点

先阅读：

- `docs/refactor-handoff.md`；
- `docs/architecture.md`，但注意其中 Project Structure 已过时；
- `tests/test_architecture_boundaries.py`；
- `cyrene/agent/__init__.py`；
- `cyrene/agent/state.py`；
- `cyrene/runtime/bootstrap.py`；
- `cyrene/runtime/lifecycle.py`；
- `cyrene/runtime/paths.py`；
- `cyrene/workbench/runtime.py`；
- `cyrene/model_runtime/client.py`；
- `cyrene/subagent.py`；
- `cyrene/browser.py`；
- `cyrene/learning/engine.py`。

建议先运行：

```bash
git status --short
.venv/bin/python -m pytest -q tests/test_architecture_boundaries.py
.venv/bin/python -m pytest -q \
  -W error::pytest.PytestUnhandledThreadExceptionWarning
.venv/bin/python -m compileall -q src
git diff --check
```

前端和发布阶段再运行：

```bash
npm --prefix src/webui run build
node --test electron/app-use.test.js
python build/build.py
```

## 11. 不要重复或不要做

- 不要重新进行已经完成的根目录包迁移；
- 不要重新处理 Route，除非用户明确开放；
- 不要合并 `tool_impl/` 的一工具一文件结构；
- 不要重写已经稳定的 Tool Catalog/Policy/Gateway/Executor 边界；
- 不要创建 `common/`、`utils/`、`helpers/` 或集中式 `storage/`；
- 不要用新的 `import *` 作为兼容方案；
- 不要因为文件行数大就机械切割；
- 不要在公共 API 和 RuntimeContext 之前继续大规模移动实现；
- 不要删除兼容门面，直到所有消费者和测试已经迁移；
- 不要把 Plan、Approval、Workflow 和 Artifact 状态只保存在 Prompt 中。

## 12. 完整重构的最终完成条件

只有同时满足以下条件，才能宣布这份重构计划完成：

- 跨域私有导入已经消除或仅剩有明确说明的临时 allowlist；
- 应用级 RuntimeContext 和生命周期所有权清晰；
- Workbench、LLM、Subagent、Browser、Learning 和 Scheduler 按职责拆分；
- Project、Session、Plan、Chat、Run、Artifact 有稳定领域模型；
- 业务层通过 Repository 使用持久化；
- 配置导入不再修改进程环境和模块全局；
- 异常分类、结构化错误和日志策略统一；
- 前端和 Electron 巨型文件完成领域拆分；
- 测试不再主要依赖私有实现；
- PR CI、发布构建、版本契约和文档全部一致；
- 兼容门面和旧导入在消费者迁移后被删除；
- 全量 Python、WebUI、Electron 和 packaged smoke test 通过。
