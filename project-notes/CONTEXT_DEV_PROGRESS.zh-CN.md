# 当前开发进度

[English](CONTEXT_DEV_PROGRESS.md) ·
[简体中文](CONTEXT_DEV_PROGRESS.zh-CN.md)

更新时间：2026-07-26

分支：`feature/project-literature-library`

基线提交：`5e9a0044`

本文记录当前开发检查点。旧版 Windows/Context Debugger 命令记录引用了已经
迁移的模块路径，因此已由当前源码与验证结果替代。

## 当前结论

Cyrene 的包边界重构已经完成：

- 核心代码归类到 `agent/`、`workbench/`、`model_runtime/`、
  `learning/`、`runtime/`、`observability/`、`knowledge/`、
  `channels/`、`tooling/` 和 `tool_impl/`；
- FastAPI 适配器位于 `src/route/`；
- Web 应用生命周期和静态资源托管位于 `src/webui/`；
- 历史 Python 导入会惰性解析到正式模块；
- Electron 开发模式只保留 `src/cyrene/local_cli.py` 这个物理启动垫片；
- 启动时会先把 `store/cyrene.db` 迁移到
  `store/cyrene.runtime.database`，再初始化数据库。

## 对 2026-06-01 原始目标的核查

旧版本文记录的是 Context Debugger 和 SimpleXNG 清理。本次根据当前源码和
定向测试逐项核查，没有假设它们已经完成。

| 原始目标或问题 | 当前状态 | 证据 |
|---|---|---|
| 用 `_ctx` 标记 LLM 上下文来源 | 已实现，但属于持续覆盖约束 | `cyrene.observability.context_trace`，以及 agent、coordinator、reflection、task-context、model runtime 调用点 |
| 发送给 Provider 和持久化前移除内部元数据 | 已实现 | `model_runtime.client`、`observability.debug`、`agent.session` |
| 为每次调用保存 context trace | 已实现 | verbose JSONL 事件和 `context_trace` 摘要 |
| Context Debugger UI | 已实现 | `src/webui/static/app/context-debugger.jsx` 及编译产物 |
| Context Debugger 列表与详情 API | 已实现 | `src/route/system/events.py` |
| 同时读取内存事件与持久化调试日志 | 已实现 | Route 日志读取和 Debugger UI |
| 只使用内置 SimpleXNG，不再走爬虫 fallback | 已实现 | `cyrene.tooling.backends.search` 只调用 SimpleXNG 后端 |
| 本地搜索流量不读取环境代理 | 已实现 | `trust_env=False` 和合并后的 `NO_PROXY/no_proxy` |
| 生成并传递 SimpleXNG 设置 | 已实现 | `searxng_manager` 写入设置路径和子进程环境 |
| 避免 Host 关闭时出现 `aiosqlite: Event loop is closed` | 已修复并有回归测试 | 共享 Application shutdown 和 `tests/test_runtime_host_shutdown.py` |
| 改善天气专用回答质量 | **未实现** | 当前没有独立天气 Provider/工具，仍使用通用 WebSearch |
| 重新验证墨尔本/温哥华真实 LLM Prompt | **本次未重跑** | 需要真实模型和搜索集成，不能从单元测试推断 |

已实现项目的定向测试结果：

```text
168 passed
```

覆盖范围包括 context trace、SimpleXNG 管理、Runtime 关闭和相关 Runtime
回归，并把未处理线程异常提升为测试错误。

“所有可能的上下文来源”不是一次性封闭结论，而是持续工程约束：新增上下文
生产代码必须附加显式元数据，或由 trace summarizer 的安全推断和测试覆盖。

## 正式调试命令

启用 verbose context trace 启动 Workbench：

```bash
python -m cyrene --workbench --verbose
```

启动无 Web 的交互 Runtime：

```bash
python -m cyrene.runtime.host --verbose
```

启动和检查后台 daemon：

```bash
cyrene start
cyrene status
cyrene flow --session run_live
cyrene stop
```

通过正式模块检查调试 JSONL：

```bash
python -m cyrene.observability.context_debug \
  data/debug_YYYYMMDD_HHMMSS.jsonl --call 1
```

`python -m cyrene.context_debug` 等历史可执行别名仍受支持，但新代码和文档
应使用正式路径。

## Electron 开发模式

```bash
uv sync --extra dev
cd electron
npm install
npm run dev
```

Electron 会执行
`src/cyrene/local_cli.py --workbench --electron-mode`。启动垫片会加入 checkout
的 `src/` 并优先使用仓库 `.venv`。成功启动会输出：

```text
UIMODE=workbench
PORT=4242
```

DevTools 的 Autofill 方法不支持警告和可选 source map 的 401 属于开发日志
噪声，不表示 Workbench 启动失败。

## 验证基线

在 macOS ARM64、Python 3.12 下验证：

| 检查 | 结果 |
|---|---|
| 当前 pytest | 1,381 passed |
| 上一 commit 功能测试 | 1,286 passed |
| 排除的上一 commit 测试 | 1 个静态 `pattern.py` 源码文本断言 |
| Electron App Use Node 测试 | 44 passed |
| OpenAPI 对比 | 259 个操作，Schema 未变化 |
| Tool registry 对比 | 94 个定义和 Handler，未变化 |
| 冻结产物历史模块别名 | 60 个已验证 |
| `cyrene start/status/API/stop` | 隔离 Runtime 中通过 |
| 旧数据库迁移 | 数据保留、Marker 存在、`quick_check=ok` |
| PyInstaller smoke/Runtime | Web 启动和干净关闭通过 |

排除的旧测试只是读取已删除的 `src/cyrene/pattern.py` 文本；功能性导入
`import cyrene.pattern` 仍解析到 `cyrene.learning.facade`。

## 重要约束

1. 不要为历史导入重新创建顶层转发文件；应更新
   `cyrene.runtime.module_compat.LEGACY_MODULE_ALIASES`。
2. Electron 仍按路径执行时，不要删除 `local_cli.py`。
3. 任何新数据库连接建立前必须先完成迁移。
4. 不得用旧数据覆盖已有内容的 `cyrene.runtime.database`。
5. FastAPI 组合属于 `route.registry`；领域服务不得依赖 Route/Web UI。
6. 移动 Tool 实现时必须保持 Wire schema 和 Actor policy。
7. 新增动态导入边界时更新 PyInstaller smoke test。

## 剩余工作

### 原 Context/Search 范围

- 如果产品需要精确到日的天气预报，应新增天气 Provider 或结构化天气提取。
- 在再次声明真实模型编排和答案质量前，运行带凭据的墨尔本/温哥华集成测试。

### 更广泛的产品/架构工作

目录迁移已经完成，以下是独立的后续改进：

- 用显式领域模型和 Repository 替换剩余的 Workbench 大型 dict 模型；
- 把 Browser 和 Subagent 大模块拆成更小的状态机/Transport；
- 拆分行为学习的存储、候选生成、版本和执行服务；
- 减少导入时配置变更；
- 增加常规 PR CI，执行 pytest、Ruff、Node 和打包 smoke；
- 实现 `research-workbench-roadmap.md` 中的 Experiments 和 Manuscripts。

它们不是当前 Runtime 或兼容性基线的阻塞项。
