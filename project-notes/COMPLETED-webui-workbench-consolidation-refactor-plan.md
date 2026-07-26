> **COMPLETED / 已完成 — 2026-07-26：** 本计划中由仓库代码控制的
> Definition of Done 已全部满足。下文保留的未来时态只用于说明历史实施依据，
> 不表示仍有待执行的重构阶段。

# Cyrene WebUI / Workbench UI 合并重构计划

[中文](COMPLETED-webui-workbench-consolidation-refactor-plan.md) ·
[English](COMPLETED-webui-workbench-consolidation-refactor-plan.en.md)

> 状态：已完成
>
> 更新日期：2026-07-26
>
> 审计基线：`feature/project-literature-library` / `5e9a0044`
>
> 文档角色：保留实施前计划与审计基线；完成证据见
> [实施记录](COMPLETED-webui-consolidation-implementation-log.md) 与
> [架构 Handoff](COMPLETED-refactor-handoff.zh-CN.md)
>
> 最终目标：删除 classic/legacy UI，只保留 Workbench UI，并将
> `src/webui` 与 `src/workbench-webui` 合并为唯一的 `src/webui`

实施结果（2026-07-26）：目标已经达成。实施期间用户明确授权直接删除
`--agent` UI Selector，不保留 deprecated alias；旧 build mode 值仍规范化为
Workbench。最终 Handoff 复核又明确确认 Context Debugger 不进入 Workbench，
但 verbose JSONL、Context Debug API、`cyrene flow` 和正式调试模块继续保留。
下文中的“当前结构”“建议”和未完成语气属于实施前历史记录，不代表当前代码。

## 1. 结论与不可妥协的验收标准

这次重构必须被定义为一次**行为保持型重构**，而不是 UI 重写、技术栈升级或
后端业务重构。除“用户不再进入 classic/legacy UI，所有入口统一进入
Workbench UI”这一项经批准的产品变化外，重构前能够使用的功能、数据、权限、
接口、启动方式和打包方式，在重构完成后都必须继续正常工作。

完成标准不是“页面能打开”，而是同时满足以下条件：

1. `src/workbench-webui` 被删除，所有保留的前端源码归入唯一的
   `src/webui`。
2. classic/legacy UI 的页面、组件、样式、静态依赖和 shell 分支被删除。
3. Workbench 当前借用 legacy UI 的公共能力已经迁入明确的 Workbench
   基础设施层，不能因删除 legacy 文件而丢失。
4. Web、Electron 开发模式、Electron 打包模式、PyInstaller、Quick Chat、
   首次启动和现有 CLI 启动链都可用。
5. 项目、任务、聊天、Agent 运行、工具调用、权限询问、浏览器、Diff、地图、
   PDF、知识库、Library、Memory、Schedule、设置和集成功能没有回归。
6. 现有用户数据不被删除、覆盖或静默重建；历史聊天和历史知识数据仍可读取。
7. OpenAPI、工具 wire schema、工具数量/名称、actor policy、SSE 事件语义和
   持久化格式没有未经批准的变化。
8. 全量自动化测试、前端构建、Electron 测试、源代码启动、冻结包 smoke
   test 和代表性端到端流程全部通过。
9. 没有以删除测试、放宽断言、吞掉异常、增加永久兼容分支等方式制造
   “假通过”。

任何阶段只要不能证明上述条件，均不得继续删除代码或合并该阶段。

## 2. 当前代码基线与审计结论

### 2.1 后端重构基线

当前 HEAD `5e9a0044` 已完成 `src/cyrene` 的主要包边界重构：

- 业务实现的规范位置已经归入
  `cyrene.agent`、`cyrene.workbench`、`cyrene.runtime`、
  `cyrene.knowledge`、`cyrene.tooling` 等领域包。
- HTTP/WebSocket 适配器统一位于 `src/route`。
- `src/webui` 当前负责 FastAPI 应用生命周期、认证和静态资源托管。
- 历史 Python import 由 `cyrene.runtime.module_compat` 延迟兼容。
- 当前交接基线报告为 1,381 项 pytest、259 个 OpenAPI operation、
  94 个工具定义/handler、44 项 Electron App Use 测试通过。
- 本计划编写期间重新执行了架构边界、路由结构和历史模块兼容测试，
  当前 HEAD 上为 74 项通过。

实施 UI 重构时必须把 `5e9a0044` 视为后端合同基线，不应同时拆分
`cyrene.workbench.runtime`、重写 Agent loop、调整工具协议或继续大规模移动
后端领域代码。否则无法判断回归来自 UI 合并还是后端重构。

### 2.2 当前前端实际上是“双目录、单页面、混合运行”

当前结构并不是两个完全独立的 UI：

```text
src/
├── webui/
│   ├── server.py
│   ├── auth.py
│   ├── build-jsx.mjs
│   ├── package.json
│   ├── workbench_*.py              # 历史 Python import 兼容
│   └── static/app/
│       ├── index.html               # legacy 与 Workbench 共用入口
│       ├── app.jsx                  # 在两个 shell 之间选择
│       ├── data.jsx                 # 全局数据、轮询和 SSE
│       ├── browser-view.jsx
│       ├── search.jsx
│       ├── code/diff.jsx
│       ├── math.js
│       ├── styles.css
│       ├── 大量 legacy 页面
│       ├── 第三方静态库和图片
│       └── compiled/                # 生成物，已被 gitignore
└── workbench-webui/
    ├── workbench.jsx
    ├── workbench-chat.jsx
    ├── workbench-i18n.jsx
    ├── settings-overlay.jsx
    ├── workbench-library.*
    ├── workbench-knowledge.jsx
    ├── workbench-memory.jsx
    ├── workbench-schedule.jsx
    └── 其他 Workbench 源码
```

`src/webui/server.py` 当前同时挂载：

- `/static/workbench-ui` → `src/workbench-webui`
- `/static` → `src/webui/static`

`build-jsx.mjs` 同时扫描 `static/app` 和 `../workbench-webui`，再把两边的
JSX 都编译到 `static/app/compiled`。`index.html` 则按固定顺序加载 legacy
脚本、公共脚本和 Workbench 脚本。因此，直接删除 `static/app` 或
`workbench-webui` 中任意一边都会破坏 Workbench。

### 2.3 Workbench 仍依赖的 legacy/公共实现

删除 legacy UI 前必须先迁移或替代这些实际依赖：

| 能力 | 当前来源 | Workbench 当前用法 | 处置 |
|---|---|---|---|
| 初始数据与刷新 | `data.jsx` | `window.DATA`、`useDataVersion`、`bumpData`、`reloadUiData`、`refreshSessions` | 提升为 Workbench data store |
| SSE 分发 | `data.jsx` | `window.__sseHandlers`、浏览器与会话实时状态 | 提升为 typed event bridge |
| 根入口与就绪信号 | `app.jsx`、`index.html` | Workbench/Quick Chat 选择、launch screen、`markCyreneReady` | 建立唯一 Workbench bootstrap |
| 浏览器视图 | `browser-view.jsx` | `BrowserViewportPanel` | 迁入 Workbench shared feature |
| 搜索 | `search.jsx` | `SearchOverlay` | 迁入 Workbench search feature |
| Diff 展示 | `code/diff.jsx`、对应 CSS | 聊天和任务变更预览 | 迁入 shared diff viewer |
| Markdown/安全渲染 | `marked`、`DOMPurify`、highlight | 聊天、知识、Library、Memory、设置 | 建立唯一 renderer |
| 数学渲染 | `math.js`、KaTeX | 通过共享 marked parser 生效 | 保持数学扩展与安全规则 |
| 地图 | Leaflet | 聊天中的地图卡片 | 保留并纳入依赖清单 |
| PDF | PDF.js 与 `pdf-setup.js` | 聊天附件和 Library 阅读/分析 | 合并 viewer，但保持兼容 bundle |
| 主题基础变量 | `styles.css` | Workbench CSS 使用 `--bg`、`--text`、`--line`、`--accent` 等 | 提取为 Workbench tokens |
| 本地化用量格式 | legacy i18n | `formatLocalizedSpend` | 迁入 Workbench i18n/format |
| Toast/确认框 | 当前全局函数 | 多个 Workbench 页面 | 迁入统一 feedback service |
| Logo/SVG | `static/app` | Workbench CSS 使用绝对静态路径 | 迁入统一 assets |

此外，Workbench 目前仍使用 React、ReactDOM、marked、DOMPurify、KaTeX、
highlight.js、Leaflet 和 PDF.js 等经典脚本。是否保留 xterm、vis-network、
CodeMirror 等依赖，必须按运行时调用和构建产物进行确认，不能只根据
`index.html` 的引用做判断。

### 2.4 “legacy” 必须分类，不能按名称批量删除

本次重构中至少存在四种完全不同的 “legacy”：

1. **classic/legacy UI shell**：本次明确要删除。
2. **历史聊天 ID**：`legacy:<project>:<session>` 仍被 Workbench 以只读方式
   展示和 fork；必须保留兼容。
3. **历史 Python import**：`webui.workbench_chat_runs` 等仍在 frozen smoke
   test 和兼容测试中；应按兼容策略处理，不能和 UI 文件一起删除。
4. **PDF.js legacy build**：这是为 Electron 35 / Chromium 134 兼容而主动
   选择的官方 bundle，必须保留，除非先证明新 Electron 运行时兼容。

历史配置、`default` 项目、`kb_default.db`、旧数据库文件名迁移等也属于数据
兼容，不属于 legacy UI。重构不得用 `rg legacy` 后批量删除。

### 2.5 入口、打包和测试仍有双 UI 假设

当前仍存在以下双 UI 分支：

- `python -m cyrene --workbench` 与 `--agent`
- `cyrene.runtime.host` 中的 `ui_mode=workbench/legacy`
- Electron 中的 `CYRENE_UI_MODE=agent`
- `/?shell=legacy`
- Electron `window:switch-shell` IPC
- build 的 `--ui-mode workbench|agent`
- PyInstaller 对 `workbench-webui` 的独立数据收集
- 大量测试直接读取 `src/workbench-webui/*.jsx`
- `test_launch_screen.py` 等测试直接读取旧的
  `src/webui/static/app/index.html`

这些路径必须按阶段收敛，不能只移动源码目录。

### 2.6 当前工作区状态

当前工作区已经有一批属于用户的文档迁移和翻译改动，`project-notes` 也为
未跟踪目录。实施重构前应先把这些工作整理成独立提交或切到干净的实施分支。
不得在 UI 重构提交中夹带现有文档删除、图片删除或 `src/cyrene` 的其他改动。

## 3. 范围与非目标

### 3.1 本次范围

- 将 `src/workbench-webui` 中的 Workbench 源码迁入 `src/webui`。
- 从旧 `src/webui/static/app` 中识别并迁移 Workbench 仍需的公共能力。
- 删除 classic/legacy UI 的页面、组件、样式、切换入口和专属依赖。
- 建立单一前端入口、单一静态目录和单一构建流程。
- 去除已经被证明等价的重复 renderer、viewer、API helper、反馈组件和样式。
- 更新 Web server、route shell、Electron、PyInstaller、构建脚本、测试和文档。
- 保留历史数据、历史 import、API、Agent 和工具合同。

### 3.2 明确非目标

- 不在同一重构中升级 React、Electron、PDF.js 或更换前端框架。
- 不同时引入 TypeScript、Vite、Next.js、Tailwind 或新的组件库。
- 不改变 Workbench 的视觉设计和交互信息架构。
- 不拆分 `cyrene.workbench.runtime` 或继续大规模移动 `src/cyrene`。
- 不重命名或重新设计现有 API。
- 不改变 Agent loop、tool schema、权限模型、上下文压缩、memory 或 goal loop。
- 不删除仅仅“看起来像 legacy”的用户数据兼容路径。
- 不顺带实现 Research Workbench roadmap 中的新功能。

如确需执行其中任何一项，必须拆成独立提案、独立基线和独立 PR。

## 4. 目标架构

### 4.1 推荐的最终目录

目录名称可在实施前小幅调整，但职责边界应保持：

```text
src/webui/
├── __init__.py
├── __main__.py
├── auth.py
├── server.py
├── assets.py                       # 源码/冻结包路径解析
├── build.mjs                       # 唯一前端构建入口
├── package.json
├── package-lock.json
├── frontend/                       # 唯一前端源码根
│   ├── index.html
│   ├── entry/
│   │   ├── workbench.jsx           # 主窗口唯一入口
│   │   └── quick-chat.jsx          # Workbench 子 surface
│   ├── platform/
│   │   ├── api.jsx
│   │   ├── data-store.jsx
│   │   ├── events.jsx
│   │   ├── electron-bridge.jsx
│   │   └── readiness.jsx
│   ├── shared/
│   │   ├── feedback/
│   │   ├── i18n/
│   │   ├── markdown/
│   │   ├── pdf/
│   │   ├── diff/
│   │   ├── browser/
│   │   └── components/
│   ├── features/
│   │   ├── chat/
│   │   ├── projects/
│   │   ├── tasks/
│   │   ├── knowledge/
│   │   ├── library/
│   │   ├── memory/
│   │   ├── schedule/
│   │   ├── search/
│   │   ├── profile/
│   │   ├── onboarding/
│   │   └── settings/
│   ├── styles/
│   │   ├── tokens.css
│   │   ├── base.css
│   │   └── features/
│   ├── assets/
│   └── vendor/                     # 仅确需自托管的浏览器依赖
└── static/                         # 构建输出；源码不在这里维护
    ├── index.html
    ├── assets/
    └── bundles/
```

第一轮机械迁移不要求立即把所有大文件按此树拆完。应先保持脚本执行顺序和
行为，再逐项把依赖迁入目标层；禁止在一次提交中同时“移动、改写、拆分、
去重、删除”同一个模块。

### 4.2 依赖方向

```text
Electron / Browser
        ↓
webui entry + platform adapters
        ↓
Workbench features + shared UI services
        ↓
route HTTP/SSE/WebSocket adapters
        ↓
cyrene.workbench / agent / runtime / knowledge / tooling
```

硬性规则：

- `src/cyrene` 不得 import `webui` 或前端代码。
- `route` 负责协议适配，不保存前端状态。
- 前端 feature 不直接修改其他 feature 的内部状态。
- API、SSE、Electron IPC、localStorage 和静态路径必须由 platform/shared
  层集中管理。
- 新的跨模块能力不能继续散落在 `window.*`；迁移期如需兼容，统一挂在一个
  有清单、有测试、可删除的 bridge 上。
- 生成物与源码分离；构建结果不得成为手工维护的第二份源码。

### 4.3 单一入口策略

最终只存在 Workbench 主入口。Quick Chat 作为 Workbench 的独立 surface
保留，但复用同一个 data/event/api/shared 层。

为降低风险，推荐两步完成：

1. 先在单目录中保持现有脚本顺序和 UMD 运行方式，实现目录合并与行为等价。
2. 稳定后再由 esbuild 生成明确的 Workbench/Quick Chat entry bundle，
   消除靠几十个 `<script>` 标签和全局声明顺序维持的隐式依赖。

不应在目录合并的第一个提交中同时切换模块系统。

## 5. 零回归功能矩阵

实施前必须为下表建立负责人、自动化用例、人工场景、数据 fixture 和结果记录。
没有覆盖的功能要先补 characterization test，再修改实现。

| 领域 | 必须保持的行为 |
|---|---|
| Web 启动 | `cyrene start/status/stop`、前台 `--workbench`、端口选择、认证、静态资源、API |
| 兼容启动 | 旧 `--agent` 调用不应崩溃；过渡期作为带告警的 Workbench 别名 |
| Electron | 源码开发启动、原生标题栏、窗口尺寸/状态、后端探测、ready 信号、退出清理 |
| Frozen | PyInstaller import、静态文件收集、`--smoke-test`、Electron 打包后的后端启动 |
| 首次使用 | launch screen、首次数据加载、LLM 设置、人格设置、时区、失败重试 |
| Quick Chat | 独立窗口/URL、目标列表、模型选择、发送、中断、关闭、回到主窗口 |
| 项目 | 列表、创建、激活、切换、项目信息和项目级数据隔离 |
| 任务 | 创建、初始化计划、阶段/步骤、状态变更、归档、恢复、任务上下文 |
| 聊天 | 新建、历史加载、流式输出、重试、中断、fork、附件、引用、历史只读会话 |
| Agent 执行 | plan、goal loop、round、inbox guidance、subagent、恢复、完成/失败状态 |
| 权限 | 高风险工具询问、actor policy、批准/拒绝、不可绕过、不可重复执行 |
| 工具 | 94 个定义/handler、名称、schema、discover/describe/invoke、包设置 |
| 上下文 | token/预算显示、compaction、session persistence、context gauge、trace |
| SSE/实时状态 | chat、tool、browser、goal、notification、状态刷新、断线重连、去重 |
| Browser | live view、截图、上传/下载、导航、session 与 chat 绑定、Electron bridge |
| 代码/Diff | 变更列表、文本 diff、语法高亮、复制、下载 artifact |
| Markdown/数学 | 消毒、代码块、highlight、KaTeX、链接、错误输入降级 |
| PDF | 预览、分页、选择、复制修复、分析上下文、worker、Electron 兼容 |
| 地图 | pins、tile、marker、polyline、失败降级 |
| Knowledge | 文档列表、上传、解析、归档、resolve、项目作用域 |
| Library | 项目级文献、阅读器、研究笔记、引用、Zotero sync、选择状态 |
| Memory | 列表、搜索、详情、语言、resolve、项目作用域 |
| Schedule | task、occurrence、创建/编辑/启停、时区、执行结果 |
| Search | 全局 overlay、键盘入口、历史/项目结果、导航 |
| Settings | 模型、keys、tools、MCP、integrations、SOUL、budget、外观、danger zone |
| Profile | 用户信息、avatar、版本、usage、activity heatmap |
| Update | 检查、changelog、下载、进度、重启 |
| Channels | Telegram/WeChat 设置和启动链不因 UI 清理被误删 |
| Persistence | 主数据库、项目 KB、配置、behavior learning、SOUL、短期记忆、debug trace |
| 安全 | loopback auth、敏感配置不入前端、XSS 消毒、路径/上传校验、日志不泄密 |
| 国际化 | 中英文字符串、日期/时区、金额/用量格式、fallback |
| 可访问性 | 键盘、focus、modal、ARIA、对比度和 reduced motion 不倒退 |

## 6. 分阶段实施方案

### 阶段 0：冻结基线和建立实施工作区

**动作**

1. 将当前工作区中的文档迁移、翻译和图片删除整理为与 UI 重构无关的提交。
2. 从 `5e9a0044` 或其明确后继提交创建干净的 `codex/...` 实施分支。
3. 记录 Python、Node、Electron、OS/架构和依赖锁文件版本。
4. 保存当前 OpenAPI、工具 registry/wire、静态文件清单、HTML 加载顺序、
   `window.*` 清单、API URL 清单和构建产物清单。
5. 用旧数据 fixture 建立可重复的启动环境，不使用开发者真实数据作为唯一验证。

**进入条件**

- 当前业务分支内容明确，UI 重构不夹带未归属改动。

**退出条件**

- 全量基线测试通过并保存日志。
- Electron source、Quick Chat、Web、frozen smoke 可在基线环境重现。
- 功能矩阵已有可执行检查项。

**回滚**

- 尚未改代码，直接回到基线提交。

### 阶段 1：补齐 characterization tests 和依赖清单

**动作**

1. 给 Workbench 当前借用的每个全局能力建立测试：
   data store、SSE、BrowserViewportPanel、SearchOverlay、DiffViewerPanel、
   markdown/math、PDF、theme tokens、ready signal。
2. 将“读取源码字符串”的测试分成两类：
   - 真正的架构约束：改为读取新路径并继续严格校验；
   - 行为断言：优先改为 DOM/Node/浏览器级行为测试。
3. 为主窗口和 Quick Chat 增加最小 E2E：
   首次启动、已有用户、聊天流式输出、权限询问、浏览器、PDF、Library、
   设置保存和退出重启。
4. 记录全部前端请求的 API、method、payload、response 和错误处理。
5. 记录全部 SSE event name、payload 必填字段、乱序/重复/断线行为。
6. 对 CSS 变量、静态资源 URL 和第三方全局对象生成机器可检查清单。

**退出条件**

- 删除任一 legacy 文件前，能由测试指出它是否仍被 Workbench 使用。
- 现有失败降级路径有用例，而不是只覆盖 happy path。
- 测试没有硬编码双目录作为产品行为。

**回滚**

- 该阶段只增加测试和审计产物，可独立回滚。

### 阶段 2：机械合并源码目录，不删除功能

**动作**

1. 用 `git mv` 将 `src/workbench-webui` 迁入
   `src/webui/frontend` 的临时 `features`/`styles` 位置。
2. 更新 `build-jsx.mjs`、server 静态路径、PyInstaller spec 和所有测试路径。
3. 保持现有文件内容、脚本顺序、URL、CSS 选择器和全局导出不变。
4. 暂时保留旧 `static/app` 中全部文件，确保这一步只验证目录合并。
5. 生成新构建产物并对比文件数量、bundle 内容、资源 404、控制台错误。
6. 删除空的 `src/workbench-webui` 目录及其独立 PyInstaller 收集逻辑。

**退出条件**

- 源码只剩一个前端根目录。
- Workbench 行为和截图与基线一致。
- 不再挂载 `/static/workbench-ui`，或仅保留有测试且限期删除的临时 redirect；
  推荐直接更新内部 URL，避免永久双路径。
- 全量 Python/Node/Electron 测试通过。

**回滚**

- 这是纯移动提交，应能单独 revert 回原目录，不影响数据和 API。

### 阶段 3：建立 Workbench 自有 bootstrap、data store 和 event bridge

**动作**

1. 从 `app.jsx` 提取 launch screen、theme、ready signal、主入口和 Quick Chat
   surface，建立唯一 Workbench bootstrap。
2. 将 `data.jsx` 中的 `DATA`、订阅、轮询、reload、session/status refresh
   迁为 Workbench data store。
3. 将 `__sseHandlers` 迁为显式 event bridge：
   - event name 常量化；
   - payload 校验；
   - chat/project/session correlation；
   - 重复事件幂等；
   - 断线重连；
   - handler 注册与卸载；
   - 未知事件记录但不崩溃。
4. 建立迁移期 `window.CyreneUI` bridge，仅承载尚未迁移的导出；列出 owner 和
   删除阶段，禁止新增任意全局名。
5. Workbench 与 Quick Chat 共享同一 store/event/api 实现。

**退出条件**

- Workbench 不再依赖 legacy shell 才能启动和刷新。
- SSE、轮询、页面切换和窗口销毁无重复 listener、无内存泄漏。
- 所有权限/工具/Agent 状态事件保持原语义。

**回滚**

- 保留上一阶段的原 data/bootstrap 适配器一个提交周期，可通过单一开关回退；
  验证完成后删除开关。

### 阶段 4：迁移 Workbench 仍需的公共 UI 能力

按以下顺序逐个迁移，每项一个小提交：

1. theme tokens、base/reset、字体和图标资源；
2. toast、confirm modal、错误边界和 loading/empty state；
3. i18n、日期/时区、用量/金额格式；
4. markdown + DOMPurify + highlight + KaTeX；
5. BrowserViewportPanel；
6. SearchOverlay；
7. DiffViewerPanel；
8. Leaflet 地图；
9. PDF.js loader/viewer/selection/analysis context；
10. Electron bridge 和 window readiness。

每一项必须：

- 先把实现迁入 `shared` 或对应 feature；
- 更新消费方为显式依赖；
- 执行单元测试、浏览器测试和截图对比；
- 用 `rg` 和运行时 coverage 证明旧实现已经没有消费方；
- 最后才删除旧文件。

**特别约束**

- PDF.js 的 `legacy/build` 不能因名称被删除。
- DOMPurify 不能被自制字符串过滤替代。
- Browser 和权限交互不能在失败时静默显示成功。
- CSS 不能只依靠“页面看起来差不多”；浅色、深色、系统主题、缩放和小窗口都
  要对比。

### 阶段 5：去除重复代码并收敛大文件

优先去重的已知候选：

- 聊天与 Library 的 PDF viewer/selection 逻辑；
- 多处 Markdown sanitize/render；
- 多处 API 请求、错误解析和 abort 处理；
- modal、toast、confirm、busy state；
- avatar、format、date/time、usage/spend；
- task/chat 中的 Diff 展示；
- Workbench 主 CSS 与 Library CSS 的重复 tokens/layout；
- i18n fallback 和语言事件；
- data refresh 和 feature 局部 cache。

执行规则：

1. 先写等价性测试，再抽取一个共享实现。
2. 只在两个实现的输入、输出、错误和生命周期都相同后合并。
3. 相似但领域语义不同的代码不要强行抽象。
4. 大文件按 feature boundary 拆分，不按任意行数拆分。
5. 每次抽取后检查 listener、AbortController、timer、object URL、
   PDF worker、browser session 的清理。
6. 不改变 API payload、DOM 测试合同和用户可见文案。

此阶段可逐步把前端内部迁到 ES module + esbuild entry bundle，但不得同时升级
第三方版本。若模块化导致调试信息或 source map 退化，应先解决再继续。

### 阶段 6：删除 classic/legacy UI

**删除前置条件**

- Workbench 已不依赖 legacy shell、页面或隐式全局。
- 主窗口、Quick Chat、首次启动和设置能在完全不加载 legacy 脚本的 HTML 中运行。
- 浏览器 coverage 和静态引用审计均显示候选文件未使用。
- 已完成一次从真实发布版本升级到候选版本的数据兼容演练。

**删除内容**

- `LegacyAppShell` 和 `readUiShellMode()` 的 legacy 分支；
- dashboard、agents、sessions、classic chat、classic settings、classic
  knowledge、classic memory、tasks calendar、terminal、context debugger 等
  仅属于旧 UI 的源码；
- `/?shell=legacy`；
- Electron shell 切换菜单/IPC 的 UI 行为；
- `CYRENE_UI_MODE=agent` 的双窗口分支；
- build 中的 classic UI 选择；
- 经证明无 Workbench 消费方的 xterm、vis-network、CodeMirror 等依赖；
- 仅支持旧 UI 的 CSS 和图片。

**兼容行为**

- 原计划建议把 `python -m cyrene --agent` 保留一个发布周期；实施时用户明确
  授权直接删除该 UI Selector，因此当前版本将其视为未知参数。
- frozen build 中历史 `agent` UI mode 值应规范化为 `workbench`，避免旧构建
  配置使程序无法启动。
- Electron 的旧 shell switch IPC 如存在外部调用，应先返回
  `{mode: "workbench", deprecated: true}`，确认无消费者后再删 preload API。

**退出条件**

- HTML 不再加载 classic/legacy 页面脚本。
- 不存在 `/static/workbench-ui` 双挂载和 `shell=legacy`。
- 用户无法进入第二套 UI；历史 build mode 仍安全规范化到 Workbench。
- 旧 UI 依赖删除后 lockfile 与许可清单已更新。

### 阶段 7：后端和兼容层清理

这是 UI 删除后的独立阶段，不能凭路由名称直接删除后端：

1. 对 `/api/workbench/*`、`/api/chat/*`、`/api/settings/*`、
   `/api/sessions`、`/api/status`、`/api/ui-data` 等做真实消费方审计。
2. “不以 workbench 命名”的 API 仍可能被 Workbench、CLI、Electron、
   channel 或外部用户使用，必须保留。
3. 只有同时满足以下条件的 route 才可删除：
   - Workbench/Quick Chat 无调用；
   - CLI/Electron/channel 无调用；
   - OpenAPI 兼容政策允许；
   - telemetry/测试证明无消费者；
   - 已给出迁移说明或版本化策略。
4. `src/webui/workbench_chat_runs.py`、
   `workbench_goal_loop.py`、`workbench_notifications.py` 是 Python import
   兼容层，不是 legacy UI。当前 frozen smoke 和兼容测试仍依赖它们。
   是否迁入 `module_compat` 必须单独做，并保持模块对象身份与 monkeypatch
   行为；不能在前端删除提交中顺带移除。
5. `src/webui/db.sqlite3` 当前是被 git 跟踪的文件，但没有发现明确的运行时引用。
   实施时应先检查 schema、历史和用途：
   - 若是测试 fixture，移动到 `tests/fixtures` 并建立测试；
   - 若是误提交的开发数据，确认无用户数据后在独立提交删除；
   - 若仍有迁移用途，建立显式路径和迁移策略。
   未完成调查前不得删除或覆盖。

**退出条件**

- route 数量/OpenAPI 的任何变化都有批准和迁移说明。
- 架构边界测试、历史 import、PyInstaller hidden import/smoke 全部通过。

### 阶段 8：构建、发布和文档收口

1. `webui.server` 只解析一个静态根并只挂载一个静态 URL namespace。
2. `route.system.shell` 始终返回 Workbench index，保留 no-cache 行为。
3. PyInstaller 只收集 `webui/static` 的最终构建输出，不再独立收集
   `workbench-webui`。
4. Electron 始终启动 Workbench；保留窗口状态、Quick Chat、ready 和退出清理。
5. build 默认和唯一正式 UI mode 为 `workbench`。
6. 更新 README、architecture、development、installation、usage、CHANGELOG
   和 refactor handoff。
7. 删除过渡 bridge、路径 redirect、临时 feature flag 和过期注释。
8. 对最终源码树执行禁止项扫描。

## 7. 测试与质量门禁

### 7.1 每个提交必须通过

```bash
python -m compileall -q src
pytest -q <本次变更相关测试>
cd src/webui && npm ci && npm run build
git diff --check
```

此外检查：

- 构建后 `git status` 没有意外修改被跟踪的生成物；
- 页面无 404、无未捕获异常、无 React key/hydration 类警告；
- 新增/删除依赖有 lockfile 和许可依据；
- 没有把 key、token、真实用户数据写入 fixture、日志或前端 bundle。

### 7.2 每个阶段必须通过

- 全量 `pytest -q`，不得低于基线覆盖；
- `node --test electron/app-use.test.js`；
- Workbench 和 Quick Chat 浏览器 E2E；
- Electron source 启动；
- `cyrene start/status/API/stop` 隔离环境测试；
- OpenAPI normalized snapshot；
- tool registry/wire snapshot；
- 历史 import alias 测试；
- 旧数据库/旧 KB/旧 chat fixture 升级测试；
- 浅色、深色、系统主题和典型窗口尺寸截图对比；
- 键盘导航、modal focus trap 和 reduced motion 检查。

### 7.3 发布候选必须通过

1. 全新安装首次启动。
2. 从重构前正式版本原地升级。
3. macOS、Windows、Linux 的支持组合；至少 CI 构建，发布平台做真实启动。
4. Electron 安装包启动、退出、重启、更新。
5. PyInstaller 新构建和 `--smoke-test`。
6. frozen Web backend 启动，访问 `/openapi.json` 和代表性 API。
7. Quick Chat 独立窗口。
8. 浏览器 session、PDF worker、SSE、Agent/background task 的正常清理。
9. 长会话、并发事件、网络断开、API 失败、权限拒绝、磁盘只读/空间不足等
   非 happy path。

### 7.4 测试修改规则

- 路径变化可以更新 fixture path，但不允许删除其行为断言。
- 源码字符串断言若被替换，新的行为测试必须至少覆盖原风险。
- 不允许用 `try/catch {}`、默认成功状态或跳过测试掩盖回归。
- 任何已知失败都必须有 issue、影响、临时措施和移除条件；发布门禁不接受
  未解释的 flaky。
- 未持有真实凭证时，只能说明 provider/channel 的模拟和启动合同通过，
  不能声称真实外部服务已验证。

## 8. 数据、安全与 Agent 合同

### 8.1 数据完整性

- 不更改 `store/cyrene.runtime.database` 的迁移顺序和冲突保护。
- 不删除旧 `store/cyrene.db` 回滚副本。
- 保留 `kb_<workspace>.db`、`kb_default.db` 与 `default` 项目的兼容语义。
- 保留 `legacy:*` 聊天读取/fork 行为。
- 配置、SOUL、short-term memory、behavior learning、debug trace 和 browser
  profile 的路径不因静态目录移动而变化。
- 升级演练前后记录表数量、关键记录数量、SQLite `quick_check` 和可读性。

### 8.2 安全

- `LocalAuthMiddleware`、loopback 限制、上传校验和下载路径校验保持有效。
- API key/secret 继续只存在于安全配置层；前端仅接收 masked/boolean 状态。
- Markdown/HTML 继续由 DOMPurify 消毒；数学和代码高亮不得绕过消毒顺序。
- Electron IPC 维持最小暴露面和来源校验。
- 日志和 telemetry 不能记录 prompt 中的凭证、文件内容或个人敏感字段。

### 8.3 Agent 与工具合同

前端重构不得改变：

- tool capability ID、名称、schema、结果协议和错误类别；
- main agent、execution agent、subagent 的 actor policy；
- 权限问题的展示、批准/拒绝和 correlation ID；
- 运行中的 frozen capability snapshot；
- plan/goal/round/inbox/subagent 的状态转换；
- context compaction、预算统计、trace 和 session persistence；
- SSE 中的 tool/permission/goal/chat 状态语义。

前端可以重新组织显示代码，但不能把失败显示成成功，也不能因未知事件而丢弃
关键权限或终止状态。

## 9. 风险登记与缓解

| 风险 | 概率/影响 | 缓解 |
|---|---|---|
| 删除 legacy 文件后 Workbench 缺少全局对象 | 高/高 | 先生成依赖清单和 characterization tests，逐个提升 shared 能力 |
| 脚本顺序变化引发运行时 redeclaration/undefined | 高/高 | 机械迁移保持顺序；模块化另做阶段；E2E 捕获控制台错误 |
| CSS 清理导致隐藏视觉回归 | 高/中 | tokens 提取、主题/尺寸截图矩阵、CSS coverage 后再删 |
| SSE listener 重复或漏卸载 | 中/高 | event bridge、handler 生命周期测试、重连/重复事件测试 |
| 历史数据被误认为 legacy 删除 | 中/极高 | legacy 分类、真实升级 fixture、只做非破坏迁移 |
| `--agent`/旧 build config 使升级后无法启动 | 高/高 | 用户授权删除 CLI selector；旧 build mode 继续 normalization |
| PyInstaller 漏收静态资源或动态模块 | 中/高 | spec 单根收集、fresh build、frozen smoke、资源 404 检查 |
| Electron ready/Quick Chat 被根入口改动破坏 | 中/高 | 独立 E2E、保持 readiness 协议、source/frozen 双测 |
| API 被错误判定为旧 UI 专用 | 中/高 | 前端/CLI/channel/外部合同四方消费审计 |
| 过度抽象使重复代码“减少”但语义耦合 | 中/中 | 只合并行为等价实现，按 feature 边界拆分 |
| UI 重构与 `src/cyrene` 重构交叉 | 高/高 | 锁定后端基线、分支/提交隔离、禁止同 PR 大规模后端移动 |
| 第三方 “legacy” bundle 被误删 | 中/高 | PDF.js 兼容测试和 Electron 版本门禁 |
| 测试只校验源码形状，运行时仍回归 | 高/中 | 将关键断言提升为 DOM/浏览器/Electron 行为测试 |

## 10. 提交与评审策略

推荐使用可独立回滚的小提交：

1. `test(webui): freeze workbench behavior and dependency contracts`
2. `refactor(webui): move workbench sources under the single webui root`
3. `build(webui): use one source and static output root`
4. `refactor(webui): introduce workbench bootstrap and data store`
5. `refactor(webui): introduce typed event bridge`
6. `refactor(webui): promote theme, feedback and i18n shared services`
7. `refactor(webui): promote browser, search and diff features`
8. `refactor(webui): consolidate markdown, math and pdf rendering`
9. `refactor(webui): deduplicate feature helpers`
10. `refactor(webui): remove classic UI shell and assets`
11. `build(electron): normalize all UI entry points to workbench`
12. `build(pyinstaller): package the single webui output`
13. `docs(webui): document the workbench-only architecture`

评审时每个提交都应回答：

- 行为基线是什么？
- 哪些文件只是移动，哪些发生逻辑变化？
- 哪项测试证明没有回归？
- 是否触及数据、权限、API、SSE、Electron 或 frozen build？
- 如何单独回滚？

不建议把整个重构压成一个超大提交。

## 11. 回滚与发布策略

### 11.1 代码回滚

- 每阶段保留可独立 revert 的边界。
- 结构移动和逻辑重写不放在同一提交。
- legacy UI 真正删除前打一个已通过全部门禁的 tag/commit。
- 删除后发现高优先级回归时，回滚到“单目录但尚未删除 legacy”的稳定点，
  而不是临时复制旧代码到新目录。

### 11.2 数据回滚

- UI 重构原则上不执行数据 schema 迁移。
- 如确需 migration，必须另建版本化、幂等、可校验、保留源数据的迁移。
- 回滚代码前确认新版本没有写入旧版本无法理解的数据。
- 不以清空数据库、重置设置或删除用户目录作为回滚手段。

### 11.3 发布

- 先内部/测试通道发布，采集启动失败、静态 404、前端异常、SSE 重连、
  Agent terminal state、PDF worker 和 Electron crash。
- `--agent` alias 的观察期建议已被实施期用户授权覆盖，CLI selector 已直接删除。
- 只有新旧安装、真实升级和 frozen/Electron 均稳定后才发布正式版本。

## 12. 最终机械验收清单

最终 CI 应增加以下可机器验证的断言：

- [x] 不存在 `src/workbench-webui`。
- [x] 只有一个前端源码根和一个静态构建输出根。
- [x] `server.py` 不再挂载 `/static/workbench-ui`。
- [x] 不存在 `shell=legacy` 和 classic UI root 分支。
- [x] `index.html` 不加载 classic 页面脚本。
- [x] Workbench 源码不直接依赖未登记的 `window.*` 全局。
- [x] 所有静态 URL 在 source、Electron 和 frozen 模式均返回 200。
- [x] 不存在手工维护的重复 compiled 源码。
- [x] 不存在被 Workbench 使用却归类为 legacy 的残留文件。
- [x] `--agent` 已按用户授权正式移除；历史 build mode 规范化已测试。
- [x] `legacy:*` 聊天、历史数据库和 `kb_default.db` 兼容测试仍通过。
- [x] PDF.js Electron 兼容测试仍通过。
- [x] OpenAPI 和工具 wire snapshot 无意外变化。
- [x] 94 个工具定义/handler 及 actor policy 保持一致。
- [x] 架构边界、route structure、import compatibility 全部通过。
- [x] 全量 pytest、Electron App Use、E2E、build、frozen smoke 全部通过。
- [x] 主窗口、Quick Chat、首次启动、设置、聊天、Browser、PDF、Library 的
  人工验收有记录。
- [x] 文档和注释不再宣称存在两套 UI。
- [x] 临时 redirect、bridge、feature flag 和 TODO 均已删除或有明确 issue。

## 13. Definition of Done

只有同时满足以下定义，才能宣布重构完成：

1. 代码结构上，Cyrene 只有 `src/webui` 一个 Web UI 包，Workbench 是唯一 UI。
2. 运行时上，不再加载 classic/legacy UI 代码，也不依赖其隐式全局和样式。
3. 产品能力上，除明确删除旧视觉 shell 和用户确认不迁入 Workbench 的
   Context Debugger 页面外，现有功能均不受影响；Context Trace 的
   JSONL/API/CLI 能力保留，且无隐藏降级、数据丢失、权限绕过或接口漂移。
4. 工程质量上，重复实现已在行为等价的前提下合并，依赖边界更明确，生成物与
   源码分离，测试比重构前更能覆盖真实运行行为。
5. 发布质量上，全新安装、原地升级、Web、Electron、Quick Chat、PyInstaller
   和支持平台全部验证通过，并有可执行的回滚方案。

上述门禁已经完成，验证命令、真实运行凭证、环境限制和回滚点记录在
`COMPLETED-webui-consolidation-implementation-log.md`。本文保留的未来时态用于说明原始
决策过程，不再表示重构仍在进行。
