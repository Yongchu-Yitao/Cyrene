# Changelog

## [0.6.2] - 2026-07-05

0.6.2 是一个功能型更新，重点引入自适应预算控制系统与经济模式以优化 token 花费，同时补齐 macOS 原生菜单栏、Workbench UI 体验改进与多项运行时修复。

### Added

- **自适应预算控制系统** — 全新 `AdaptiveBudgetController` 规则引擎（纯规则驱动，无 ML 依赖）。按月 / 周 / 5 小时三层窗口动态分配预算，根据剩余预算、近期消费速率和历史活跃密度自适应调整每层额度。新增 `/api/budget/status` 与 `/api/budget/models` 端点提供实时状态与按模型分拆的花费明细。超支策略可配置：仅告警、自动切换到更便宜的模型、阻止新请求。所有计算以用户配置币种（CNY / USD）为准。
- **经济模式 (Economy Mode)** — `budget_mode` 设置控制开启后，自动清除已完成轮次的 tool 结果，只保留 user ↔ assistant 对话主干，显著降低 context 膨胀与 token 消耗。
- **预算面板 UI** — 设置页新增「预算」页签（`BudgetPanel` 组件），提供月度限额、币种、超支动作、经济 / 标准模式、结算日配置，以及按模型分拆的当前月花费明细。侧栏项目列表同步显示 5 小时 / 周预算窗口实时状态。
- **macOS 原生菜单栏** — 中英文双语完整应用菜单（文件 / 编辑 / 视图 / 窗口 / 帮助），含新建对话 / 项目 / 任务、切换主题 / 侧栏、全屏、缩放等操作，通过 `menu:action` IPC 桥接到 Workbench 前端。
- **账户菜单** — 侧栏底部新增用户头像入口与下拉菜单，提供设置、登出等快捷操作。

### Changed

- **文件交付反馈增强** — `send_file` / `send_wechat_file` 工具调用成功后，自动从工具返回值构建用户可见确认文本（例如「文件已发给你：report.pdf」），替换空泛的 `"Done."` 占位回复；最终回复提示词同步优化以避免无信息量的占位语。
- **记忆注入策略改进** — 注入列表改为 top 20（按 `mention_count`）+ 随机 5 条混合，兼顾高频与多样性；注入 ID 快照到 session 级别，单次会话内复用同一集合，保持 prompt cache 稳定。
- **工作台数据按需加载** — `GET /api/projects` 新增 `?detail=summary` 参数，返回项目壳体 + 会话摘要（不含完整历史 payload）；非活跃 session 仅传摘要，切换时才拉取完整数据，大幅减少首屏传输量。`/api/task-sessions/{id}` 返回的 project 同步压缩为 shell。
- **Workbench UI 优化** — 模块页面延迟挂载（`mountedPages`），减少首屏渲染量；侧栏折叠与侧面板隐藏按钮统一样式；HTML 预览 iframe 自动注入 `viewport` meta；对话页新增文件下载按钮。
- **更新确认改用非阻塞模态框** — 升级确认从原生 `window.confirm` 切换为 `window.confirmModal`，不再阻塞 UI。
- **成本统计切换到 CNY** — DB `token_usage` 表的 `estimated_cost` 默认币种从 USD 改为 CNY，与内置模型定价保持一致，减少不必要的汇率转换。

### Fixed

- **Shell stderr 重定向解析** — `2>/path` 和 `&>/path` 的正则不再错误匹配 `;` `&` `|` 等 shell 元字符，避免误判文件写入目标。
- **直播段 ID 稳定性** — 工具轮次中 assistant 中间回复在持久化分配 `message_id` 之前，使用 SHA1 内容指纹生成稳定 UI ID，避免每次扫描产生重复条目。

### Tests

- 新增 `tests/test_adaptive_budget.py`（630 行），覆盖自适应预算控制器的全部核心逻辑：新用户零历史、各窗口预算计算、压力系数、变化限幅、活跃密度估计等。
- 新增 `tests/test_quit_reply.py`、`tests/test_workbench_api_validation.py`。
- 更新 `tests/test_workbench_chat_segments.py`、`tests/test_workbench_frontend_logic.py` 与版本号一致。

## [0.6.1] - 2026-07-01

0.6.1 是 `0.6.0` 之后的正式维护版本，重点补齐浏览器自动化能力、桌面 Quick Chat 常驻体验、文档站与若干运行时稳定性修复。本版本为稳定 release，不是 beta / prerelease。

### Added

- **浏览器自动化工具扩展** — 新增按坐标点击、按元素引用点击、按文本点击、按元素引用输入、等待、网络日志、结构化快照与多标签页管理等工具；Workbench / Legacy UI 中的浏览器实况说明与工具注册同步更新。
- **桌面 Quick Chat 常驻入口** — 增强 Electron 后台驻留、托盘入口、窗口复用和主进程消息契约；补充托盘图标资源与背景运行测试。
- **文档站资源** — 新增 `docs/web` 静态文档站、Logo、交互脚本和样式，方便发布包外独立浏览项目说明。

### Changed

- **元素交互更稳** — Electron / BrowserTabManager 的元素操作改为更可靠的页面级交互路径，减少受页面自定义 JS click 限制影响的失败。
- **Chat-only 流式回复路径简化** — 流式请求不再额外发起一次 final reply LLM 调用，直接复用 phase-1 回复并保留用量统计，降低延迟与 token 消耗。
- **行为学习遥测后台化** — 工具执行后的 `record_action` 记录改为后台任务，不再阻塞工具结果返回；失败只记录 debug 日志。
- **发布版本元数据统一** — Python 包、Electron 应用、lockfile、README、文档站、WeChat client 标识、WebUI cache-busting 参数与前端断言统一到 `0.6.1`。

### Fixed

- **命令行生成的交付物提示** — Workbench 任务提示词明确要求 Bash / shell 生成最终文件后也必须调用 `send_file`，避免文件已生成但不进入「产物」面板。
- **地图 pin 视觉** — Leaflet pin 改为自定义可主题适配的地图标记，修复默认 marker 资源在部分打包场景下不稳定的问题。
- **设置页侧栏交互** — 调整设置浮层 tab 的 hover、active 与 focus-visible 样式，降低视觉抖动并改善键盘焦点反馈。
- **测试环境依赖 mock** — Quick Chat 和 runtime tests 对 PIL / pypdf 的处理更稳，避免真实依赖存在时被 MagicMock 污染。

### Tests

- 新增 / 更新浏览器会话、Quick Chat、托盘图标、后台驻留、行为学习、更新器平台匹配、Workbench 初始化计划、运行时修复与前端 cache-busting 等测试。

## [0.6.0] - 2026-06-29

首个正式版本，汇总自 **v0.5.1** 以来的累积更新。0.6.0 的主线是全新的 **Workbench 工作台**——以项目为中心的桌面工作环境，以及围绕它的可恢复任务执行、并发对话、记忆 / 知识体系，加上大量提示词缓存与稳定性优化。本版本把 `feat/workbench` 分支的全部工作合并进 `main`，并将版本号正式定为 `0.6.0`。

> 各 `0.6.0-beta.*` 预发布的逐版本明细见下方 beta 段。

### 🏗️ Workbench 工作台（全新）

- **以项目为中心的工作台** — 全新桌面 UI：每个项目独立的看板、日程、知识、记忆、对话与个人资料页，与经典单 agent UI 并存、共享后端。
- **诚实的逐步任务执行** — 计划基于工作区实况生成（计划模式提交前可只读预探索），任务按步骤推进、可跟随可干预、可中途修订并保留进度；意图分流（问题 / 指令 / 任务）与「任务完成」收尾（finalize）。
- **可恢复的对话运行** — Chat 运行由进程级运行管理器持有，断线、切页或网络抖动后 agent 仍在后台继续并持久化结果，前端经独立只读接口重连追上；支持并发多会话。
- **对话编辑与分叉** — 可编辑已发送消息并从该点分叉新对话（原对话保留、标注 Forked），也可从任意位置重新生成回复（事务式替换，失败不丢旧回复）。
- **SQLite 事务存储** — 以 SQLite 为单一真相源（`BEGIN IMMEDIATE` + 三方合并 + WAL），并发消息 / 会话 / 通知 / 记忆互不覆盖，取代旧的整文件 JSON 读改写。
- **工作区隔离与安全** — 每个项目限定到自身 `workspacePath`（主 agent 与 subagent 一致），新建项目走空工作区引导；workspace 路径安全校验防穿越。
- **产物与文件变更** — Artifact 一键下载（服务端校验不逃逸 workspace）；即使非 Git 仓库也能记录有界文本快照生成统一 diff；`send_file` 显式产物信号优先于 git 推断。
- **上下文可视化** — 上下文分段 token 仪表 + 手动压缩对话；Settings 浮层、平台感知的全局快捷键管理器、subagent 状态 / 载荷面板。

### 💬 Quick Chat 全局快捷对话（全新）

- **全局快捷键唤起** — `Ctrl/Cmd+Shift+Space` 任意界面呼出浮动窗口，支持活跃窗口截图粘贴、独立窗口复用、常驻托盘生命周期。
- **完整对话体验** — 与主对话页共享运行管理器与消息卡片：工具调用轨迹、生成附件、实时思考卡片；运行服务端持久化，关窗后 agent 仍在后台继续。

### 🧠 记忆与知识

- **三层记忆 + 退场** — 上下文 → 短期跨会话摘要 → 长期 `SOUL.md`；短期与项目记忆均可按精确 ID 退场（`retire_short_term_memory` / `retire_project_memory`），退场条目留档但不再注入与召回。
- **记忆分类重构** — 区分 `habit`（如何做事）/ `conversation`（如何沟通）/ `preference`（静态喜好），新增内部 `reflection` 分类（agent bookkeeping，不在用户页显示）；多词记忆召回支持分词 OR 匹配。
- **检索工具** — 新增 `RecallConversation`（按关键词 / session / 日期检索历史对话）与 `search_project_memory`（项目内记忆搜索）；`RecallMemory` 为每条短期记忆返回稳定 `memory_id`。
- **知识库** — `ListKnowledgeDocuments` 枚举文档与索引状态；Workbench 步骤摘要自动归档进项目知识库供后续检索；按 workspace 隔离；支持图片 vision 索引。
- **实体优先** — 涉及任务 / 计划 / 待办 / 决策的回答先查实体、以记录为准，执行计划前先拉取活跃事务与决策以复用。

### 🌐 浏览器自动化

- **实时直播不堵流** — 画面经 `/ws/browser` 以二进制 JPEG 帧传输，SSE 只走轻量元数据，避免 base64 截图挤占事件流。
- **面板内实时控制 + 登录接管** — 用户可在直播面板内经 CDP 注入鼠标 / 键盘 / IME 直接操作 headless 页面（agent 动作自动让位）；遇登录墙 / 验证码 / 2FA 可经 `browser_request_takeover` 或切换有头窗口完成，再回到同一已登录会话继续；Workbench 自动打开浏览器侧栏。

### ⚙️ Agent 运行时与提示词缓存

- **提示词缓存优化** — 静态系统块前缀化（`static_system_extra`）、run 级上下文前置于用户消息（`fixed_ephemeral_system`）、temporal 上下文移到尾部、统一 phase1/2 工具集、`quit(reply=)` 直接交付（消除收尾重建——曾占缓存 miss 约 53%），显著提升前缀缓存命中。
- **运行打断** — 跟踪当前运行任务，打断时连同在跑的 subagent 一并取消。
- **稳定性** — LLM 瞬时错误有限重试、蒸馏上限与压缩阈值预检、SSE 心跳保活；DSML 流式工具标记抑制，防止泄露到 UI。
- **跨平台 Shell 与破坏性操作确认** — 自动识别 Shell 类型并调整执行策略；`rm` / `git reset --hard` / `dd` 等高危命令即使 full/auto 模式也强制二次确认，外发文件纳入不可逆副作用确认。

### 📦 平台、打包与更新器

- **Windows on ARM** — CI 同时构建 ARM64 与 x64 安装包；Release 默认捆绑 Workbench UI。
- **运行时目录治理** — 新增 `cyrene.app_paths` 统一解析数据 / 缓存 / 临时目录，打包后不再把运行时数据写入只读资源或系统临时目录。
- **更新器** — 按平台 / 架构正确匹配安装包（修复 Windows 被推 macOS `.dmg`）、读取 release asset 的 sha256 校验、可选 beta 更新通道、后台检查并在工作台提示新版本与体积。
- **启动不阻塞** — 知识迁移 / vision 索引后台化，修复打包桌面端 30s 启动超时；修复更新重启的 launch guard。

### 🔒 安全

- **HTML artifact 隔离** — 用户生成的 HTML 仅在沙盒 `srcDoc` iframe 预览，不允许在继承后端 session 的子窗口打开。
- **路径校验与敏感信息脱敏** — workspace 路径穿越防护；错误文本中的 token / 密钥自动脱敏。

### ✅ 测试

- 全周期新增 / 更新数十个测试套件，覆盖 Workbench 任务 / 计划 / 对话 / 记忆 / 知识 / 存储并发、Quick Chat、浏览器接管与实时控制、更新器平台匹配、提示词缓存、Shell 守卫与破坏性操作确认等。前端 cache-bust 断言与各处版本元数据统一为 `0.6.0`。

## [0.6.0b16] - 2026-06-29

### Added

- **应用路径集中管理** — 新增 `cyrene.app_paths`，统一解析安装资源目录、用户数据目录、缓存目录和应用临时目录。打包运行时写入 `Application Support` / `%APPDATA%` / XDG data，临时与缓存产物写入平台缓存目录，避免把运行时数据混入安装资源。
- **破坏性操作二次确认** — Bash / SendShell / StartShell 现在会识别 `rm`、`git reset --hard`、`git clean -f`、`dd`、`truncate`、强制覆盖等高风险命令；即使处于 full access 或 auto 模式，也必须由用户确认。外发 WebUI / Telegram / WeChat 文件也纳入不可逆副作用确认。
- **Workbench 浏览器侧栏** — Workbench Chat 会在浏览器自动化或登录接管时自动打开 Browser 侧栏，并允许直接在侧栏点击“我已完成登录”继续任务。

### Fixed

- **打包应用运行时目录污染** — 配置、数据库、workspace、备份暂存、代码格式化暂存、技能安装暂存、通知脚本、SearXNG 日志、浏览器截图和更新下载现在都使用统一的用户数据 / 临时目录，减少 macOS / Windows 打包后写入只读资源目录或系统临时目录残留的问题。
- **浏览器直播不再堵塞 SSE** — `browser_frame` SSE 事件改为只发送 URL、title、action、target 等轻量元数据；实时画面通过 `/ws/browser` 发送二进制 JPEG 帧，前端用 object URL 渲染并及时释放，避免 base64 截图挤占共享事件流。
- **更新重启启动保护** — 修复更新重启路径的 launch guard，避免重启后误判已有实例或丢失启动状态。

### Changed

- **静态资源缓存版本统一** — WebUI 所有 JS/CSS cache-busting 参数统一为 `beta16`。
- **版本元数据统一** — Python 包、Electron 应用、Electron lockfile、README badge、WeChat client 标识与 `uv.lock` 统一到 beta16。

### Tests

- 新增/更新 `test_app_paths`、`test_browser_session`、`test_runtime_fixes`、`test_workbench_frontend_logic`，覆盖平台路径解析、临时产物清理、二进制浏览器帧传输、登录接管确认和破坏性操作确认。
- 前端 cache-bust 断言同步到 beta16。

## [0.6.0b15] - 2026-06-28

### Added

- **Workbench 无 Git diff 捕获** — Workbench 运行前后现在会记录有界 UTF-8 文本快照；即使项目目录不是 Git 仓库，也能为新增、修改、删除文件生成统一 diff，并把 diff 写入 run、step related files 与 artifact，避免后续查看文件变化时只能看到“整文件快照”或空 diff。
- **Workbench 会话召回进入项目 workspace** — `RecallConversation` 在 Workbench 任务/聊天上下文中优先搜索当前项目 `conversations/*.md`，返回 `scope=workbench_workspace`、session id 和 source file，避免跨项目或 legacy archive 召回不相关对话。

### Fixed

- **`quit(reply=...)` 历史不再丢失最终回复** — 直接通过 `quit(reply=...)` 收尾的回答现在会同步写入 assistant history content，确保用户可见 transcript 与下一轮 LLM 读取的会话历史一致。
- **多词记忆召回过窄** — `RecallMemory` 与 Workbench 项目记忆搜索支持空格分词 OR 匹配并按短语/term 命中排序，像“照片 人物 头像 识别”这类查询不再要求整段完全连续命中。
- **文件 diff 查看更稳** — Workbench diff API 会优先使用 recorded diff；Git diff 为空时补查 staged diff，对无内容变化的 timestamp-only 变动返回记录原因，避免误导性地把当前整文件当作变更。

### Changed

- **静态资源缓存版本统一** — WebUI 所有 JS/CSS cache-busting 参数统一为 `beta15`。
- **版本元数据统一** — Python 包、Electron 应用、Electron lockfile、README badge、WeChat client 标识与 `uv.lock` 统一到 beta15。

### Tests

- 新增/更新 `test_quit_reply`、`test_runtime_fixes`、`test_workbench_init_plan`、`test_workbench_memory_language` 覆盖 quit reply 持久化、workspace conversation recall、记忆 OR 查询和 Workbench diff 记录。
- 前端 cache-bust 断言同步到 beta15。

## [0.6.0b14] - 2026-06-28

### Fixed

- **Electron 30s 启动超时（知识迁移阻塞）** — `migrate_default_project_knowledge` 现在以 `asyncio.create_task` 后台化运行，不再在 lifespan `startup` 钩子里同步等待。迁移涉及逐文档的 vision/embedding LLM 调用，耗时无上界；之前它阻塞 uvicorn 全部 startup 事件，`PORT=` 迟迟无法打印，桌面端因超 30s 超时无法启动。现在服务器立即就绪，迁移在后台继续进行。
- **Vision 候选链顺序反转** — `_resolve_vision_candidates` 修复：视觉专属模型（用户配置的视觉端点）现在排在候选链首位，文本主模型（如 DeepSeek）作为兜底降级。此前文本主模型被先尝试，每张图片必然 400 失败一次再回落视觉模型，在大批量文档分析时累积拖慢启动。
- **反思记忆污染 `fact` bucket** — goal loop 反思产生的 `excluded_paths`（死路）和 `promising_directions`（有效方向）现在写入内部 `reflection` 分类，而非 `fact`，不再虚增用户记忆页的"事实信息"计数，且不在记忆页展示。反思条目仍注入每次 agent run（跨 session 传递学习成果）。

### Changed

- **记忆分类体系重构** — `conversation` 分类从"对话记忆"重定义为"**对话习惯**"（用户希望 agent 如何与其沟通的重复偏好，例如"用中文回复"、"直接给结论"），现在会注入每次 agent run（之前因认为高噪低价值而被排除）。新增内部 `reflection` 分类（agent bookkeeping 专用，不在用户页显示）。`_EXTRACT_PROMPT` 和 `save_project_memory` 工具描述同步重写，区分 `habit`（如何做事）、`conversation`（如何沟通）、`preference`（对产物/工具的静态喜好），提示模型选到最精确的分类。
- **Agent 实体查询铁律** — 提示词新增明确约束："任何涉及用户任务 / 项目 / 待办 / 决策 / 日程的回答，一律先查实体、以记录为准，不得凭记忆或印象作答。"同时新增规则：生成或执行项目任务计划前，先 `list_entities(status="active")` + 相关 `query_entities` 拉活跃任务与决策，复用已有结论、避免与既有事务冲突。
- **Cyrene 品牌按钮** — Workbench 顶栏 Cyrene Logo 按钮由跳转任务页改为打开"设置 → 关于"页。

### Tests

- 前端 cache-bust 断言同步到 beta14。

## [0.6.0b13] - 2026-06-28

### Added

- **Workbench 项目共享上下文（`workbench_task_context`）** — 新模块，专为 Workbench 任务 session 提供项目级共享上下文。`sharedContext` 字段挂载在 project 对象上，记录任务描述、最终目标和当前成果（`currentOutcome`）。主 agent 运行时自动注入项目固定块 + session 任务/计划/验收标准；子代理（subagent）同样获得项目上下文，并在完成后把最终回复追加到 `currentOutcome.entries` 供同一项目下所有 session 共享。
- **计划模式预探索** — 计划模式下，主 agent 在提交计划前现在可以调用只读探索工具（读文件、搜索项目记忆、查询外部信息），完成后再调用 `enter_plan_mode`。规划器接收到压缩后的工具调用摘要（`_history_context_text`），计划将真正基于工作区实际状态生成，而不再是纯推断。
- **项目记忆 Session 基线快照** — Session 启动时记录当前记忆 ID 集合作为固定前缀（cache-stable），Session 期间新增的记忆条目自动进入 volatile 尾部独立渲染，不影响已缓存的固定前缀。下一个新 Session 开始时再次快照，把 volatile 条目提升为固定块。

### Fixed

- **对话分段渲染 — 多文件交付顺序** — `_reorder_tool_produced_replies` 修复：同一回合内多个文件分段交付时，每段交付回复现在都能正确排列到其对应工具卡片之后（而不仅限于单文件场景）。
- **知识 workspace 隔离** — `_resolve_workspace_id` 修复：默认项目记忆不再跨项目泄漏；新增启动事件将默认项目知识从旧版共享数据库中解耦，写入项目专属存储。
- **Subagent 收尾超时** — goal loop 等待 subagent settle 时现在带超时，不再因 subagent 意外挂起而无限阻塞。

### Changed

- **Prompt 缓存优化 — `fixed_ephemeral_system`** — `run_agent` / coordinator / `_run_main_agent` 新增 `fixed_ephemeral_system` 参数。Run 级上下文（Workbench 任务简报、项目记忆快照、temporal context、conversation identity）现在插入到当前用户消息之前，而非 prompt 尾部；工具回合通过纯 append 演进，前一轮请求是下一轮请求的完整前缀，缓存命中显著提升。原 `ephemeral_system` 仅保留给真正需要每轮变化的 volatile 尾部内容。
- **Phase 1 首轮也使用完整工具集** — 移除首轮使用轻量 phase1 工具集的例外：常规对话的第一轮 Phase 1 decision 现在与后续轮次一样使用 `wire_tool_defs`，利用 DeepSeek 工具敏感前缀缓存，首轮不再因工具集差异导致额外 cache miss。
- **记忆注入 API 增强** — `render_memory_for_injection` 新增 `include_ids`、`exclude_ids`、`preserve_id_order`、`header` 参数，支持精确控制哪些记忆条目注入、以什么顺序、使用什么标题头部；新增 `memory_injection_ids` 辅助函数返回当前可注入的 ID 有序列表。
- **macOS 流量灯间距修复** — Workbench UI 在 macOS 上正确保留 traffic light 按钮空间，避免布局重叠。
- **静态资源缓存版本统一** — WebUI 所有 JS/CSS cache-busting 参数统一为 `beta13`。
- **版本元数据统一** — Python 包、Electron 应用、Electron lockfile、README badge、WeChat client 标识统一到 beta13。

### Tests

- 新增 `test_workbench_task_context`（共享上下文构建与 outcome 写入）、`test_workbench_chat_plan`（计划模式预探索与修订）、`test_cache_fixes`（fixed ephemeral 注入位置、首轮 phase1 工具集）。
- 更新 `test_runtime_fixes`；前端 cache-bust 断言同步到 beta13。

## [0.6.0b12] - 2026-06-27

### Added

- **Quick Chat 升级为完整对话体验** — 快捷对话窗口改用与主对话页相同的共享运行管理器（`WorkbenchChatRuntimes`）和消息卡片渲染。回复现在带工具调用轨迹（trace）、生成的文件附件，以及实时“思考 / 调用工具”卡片，与主界面完全一致，不再是简化的文本气泡。运行在服务端持久化：关闭或重新唤起快捷窗口只停止本地流消费，Agent 仍在后台继续执行，进度可在主窗口查看。
- **Quick Chat 窗口尺寸与滚动优化** — 空闲时保持紧凑但留足上方权限 / 命令菜单空间；首次发送后窗口一次性增高到对话所需高度，之后用户可自由调整、布局随之自适应。滚动具备粘性：仅在贴近底部时跟随最新消息，向上翻阅历史不再被拽回底部。

### Fixed

- **默认项目记忆数显示为 0** — 记忆页用项目 `dataKey` 作 workspace，但记忆按项目 id 存储；旧版默认项目两者不同（`dataKey = default`、`id = project_…`），仅按 id 匹配会落空到空的 “default” store。现先按 id、再按 dataKey 解析，任一标识都能命中同一项目记忆库。
- **WebUI / Workbench 通道误报“已发送微信文件”** — 当前通道不具备 `send_file` 能力时不再向模型暴露 `send_wechat_file` 工具，避免一次必然失败的调用、浪费一个回合并留下误导性的“已发送”卡片。
- **`send_file` 把整段回答塞进 caption** — 明确 `send_file` 的 `text` 只是文件旁的简短说明，模型仍需另写完整最终回复，避免一回合塌缩成裸 “Done.”。
- **心跳间隔被强制为 60 的倍数** — 设置项允许输入任意秒数（step 改为 1），不再在输入时强制取整或回退默认值。

### Changed

- **Agent 事务追踪：主动检索** — Agent 现在会在对话涉及用户个人事务、计划、项目时主动调用 `list_entities`/`query_entities` 获取当前状态再作答，而非等待用户明确要求查询。新增五条具体触发规则：首轮个人话题自动扫描活跃事务、指代词／项目名触发关键词检索、延续性工作开始前先确认待办、更新事务前先检索 ID、记录前去重检查。
- **Prompt 缓存优化 #7 — `quit(reply=)`** — `quit` 工具新增 `reply` 参数承载最终回复文本。模型收尾时把答案写进 `reply` 直接交付，省去原先 tools=None 的“收尾重建”调用——该调用因 `tools` 数组不在前缀最前端，与主链零共享缓存、需重处理整段历史，是此前缓存 miss 的头号来源（约占 53%）。各阶段 prompt 与工具定义同步更新，引导模型把完整答案写进 `reply`。
- **对话分段渲染改进** — 中途“让我查一下…”这类既有正文、又调用工具的回合现在单独成块呈现（此前正文被丢弃）；失败的工具调用在 trace 卡片中以 ✕ 标记；`send_file` / `send_wechat_file` 交付的文件回复被重排到其工具卡片之后，渲染顺序固定为 [工具卡片] → [交付文件]。
- **Chat 流式渲染性能** — 回复增量（reply delta）的重渲染按 `requestAnimationFrame` 合并，每帧最多一次；已完成消息的 Markdown 解析做记忆化，实时消息仅在文本变化时重解析，避免长回复每帧 O(n²) 重新解析整段会话。
- **静态资源缓存版本统一** — WebUI 所有 JS/CSS cache-busting 参数统一为 `beta12`。
- **版本元数据统一** — Python 包、Electron 应用、Electron lockfile、README badge、WeChat client 标识统一到 beta12（并补齐 beta.11 遗漏的 README badge 与 `uv.lock`）。

### Tests

- 新增 `test_quit_reply`（quit reply 直接交付、跳过收尾重建）、`test_workbench_chat_segments`（中途前导语成块、失败 trace 标记、交付回复重排序）、`test_workbench_memory_resolve`（默认项目按 id / dataKey 双重解析）。
- 更新 Quick Chat 契约测试以匹配共享 run-manager 架构；前端 cache-bust 断言同步到 beta12；WebUI 42 个 JSX 全量编译通过。

## [0.6.0b11] - 2026-06-26

### Added

- **全局快捷键快速对话（Quick Chat）** — 新增 `Ctrl+Shift+Space`（macOS `Cmd+Shift+Space`）全局快捷键，任意界面一键呼出浮动 Quick Chat 窗口。支持截图粘贴、独立窗口复用、常驻系统托盘的 app 生命周期（Electron）。由独立 `window.WbcComposer` 实例渲染，与主 Workbench 互不干扰。
- **Quick Chat 截图支持** — 快捷键唤起后自动捕获活跃窗口截图并粘贴到输入框；可粘贴多条后续截图，按发送自动清空。
- **更丰富的思考短语列表** — Agent 思考状态从 20 条扩展到 25 条随机短语，覆盖 more deliberate / 系统性推理表达。

### Fixed

- **Quick Chat 首轮消息带失效的先决条件** — `is_quick_chat` 标记与 `scene = 'quick_chat'` 对齐，确保初轮消息不走 workbench project 锚定路径。
- **Quick Chat 重复 target 匹配** — `/api/quick-chat/targets` 端点对同 socket 的去重返回，避免浮动窗口重复初始化。

### Changed

- **版本号更新至 0.6.0-beta.11** — Python 包、Electron 应用、lockfile、前端 cache-busting 参数统一更新。
- **静态资源缓存版本统一** — WebUI 所有 JS/CSS cache-busting 参数统一为 `beta11`。

### Tests

- 新增 Quick Chat targets 端点测试、全局快捷键前端测试覆盖。

## [0.6.0b10] - 2026-06-24

### Added

- **可恢复的 Workbench Chat 运行** — Chat Agent 运行由进程级 `ChatRunManager` 持有，不再依赖单个 HTTP 流的生命周期。浏览器断线、切换页面或短暂网络中断后，Agent 仍会继续执行并持久化结果；前端通过独立只读重连接口恢复事件流。
- **手动压缩对话上下文** — Workbench Chat 概览新增“压缩对话”操作，可在自动阈值前显式执行现有上下文折叠流程，并显示压缩前后的 context 占用比例。
- **项目记忆退役工具** — 新增 `retire_project_memory`，Agent 可按精确 memory ID 将错误、过时或已被取代的项目记忆标记为 retired。记录仍可在 Memory 页面恢复，但不会继续注入 Agent 上下文或出现在普通搜索结果中。
- **回复期间继续编辑草稿** — Agent 回复期间输入框保持可编辑，用户可提前准备下一条消息；发送仍被锁定，停止当前运行后才可提交。

### Fixed

- **新消息被错误当作运行重连而静默丢弃** — `POST /messages` 现在只负责创建新运行；已有运行时统一返回 `409 chat_run_in_progress`。重连改走独立 `GET /run-stream`，从协议层区分“发送新消息”和“恢复旧事件流”。
- **非流式请求可绕过单聊天运行锁** — 流式与非流式 Chat 请求现在都先在同一个运行注册表中原子占位，防止同一聊天并发启动多个 Agent、覆盖状态或打乱 transcript 顺序。
- **重试失败会删除旧回复** — 重新生成改为事务式替换：旧回复在新回复持久化前保持不动；模型调用失败时恢复原 Agent state，只有成功或进入 awaiting-user 状态后才提交截断。
- **重复附件上传导致当前会话文件失效** — Knowledge Base 内容去重不再删除 Chat transcript 正在引用的上传路径；缺失的 canonical KB 路径可由新的同内容文件恢复并重新索引。
- **附件缺失后误扫本机文件系统** — `AnalyzeAttachment` 对文件缺失返回终止型结构化错误，提示重新上传，并明确禁止使用 Glob/Grep/Bash/find 扫描设备寻找替代文件。
- **图片附件预览破图与溢出** — Composer 和历史消息中的图片加载失败时自动降级为文件 chip，预览容器限制溢出。
- **系统主动轮次可能调用 `ask_user`** — proactive/system-initiated 轮次不再暴露或执行 `ask_user`，必须自主完成检查或静默结束。
- **未知 context window 时按消息数丢历史** — 无法确定模型上下文窗口时不再执行有损的固定条数裁剪；仅在 token budget 可确定时压缩。
- **短期记忆提取长期停留在最早消息** — 记忆压缩窗口改为最近 20 条用户/助手消息，并过滤 retired 条目。
- **内部 task report 污染 Memory 页面和全局搜索** — `task_report` 继续作为内部规划上下文保存，但不再计入用户可见记忆列表、统计、来源图和 Workbench 搜索。

### Changed

- **静态资源缓存版本统一** — WebUI 所有 JS/CSS cache-busting 参数统一为 `beta10`，确保升级后加载同一版本的完整前端资源。
- **版本元数据统一** — Python 包、Electron 应用、Electron lockfile、README badge、WeChat client 标识统一到 beta 10。

### Tests

- 新增 Chat 运行断线继续执行、显式重连、非流式运行注册、重试回滚、重复附件保留、缺失附件终止、手动上下文压缩、记忆退役、内部记忆隐藏和 proactive 工具限制等回归测试。
- WebUI JSX 全量编译通过；相关 Python 测试覆盖 session persistence、runtime、knowledge、Workbench chat/memory/search 和前端逻辑。

## [0.6.0b9] - 2026-06-23

### Added

- **Workbench API 封装层（`workbench_chat_runs.py`）** — 新增独立模块封装 chat run 生命周期：创建/恢复 run、sse 心跳、中止、轮次历史与 run 元数据路由。前端通过 `workbench-api.jsx` 统一客户端调用，替换原先散落在 `workbench-chat.jsx` 里的 `fetch` 调用，降低耦合。
- **Chat 心跳（Heartbeat）** — Workbench Chat 流式响应新增 SSE 心跳帧，保持长连接活跃、防止中间代理/网关因空闲超时断流；前端在心跳到达时更新「正在思考」指示而不写入消息。
- **LLM 蒸馏上限与阈值预检** — `call_llm.py` 引入压缩块蒸馏最大尝试次数，防止增量压缩无限重试；新增 `exceeds_compaction_threshold` 预检，仅在消息上下文真正超过压缩阈值时才触发蒸馏。
- **LLM 瞬时错误重试** — 对 LLM 调用中的瞬时服务器错误加入有限次数自动重试，超限后才失败；新增针对瞬时错误与重试行为的测试。
- **Workbench 初始化任务优先级指示** — 初始化 UI 增加任务管理与优先级视觉指示，更清晰展示 init 阶段任务队列。

### Fixed

- **Workbench Chat 代码块语法高亮与复制按钮丢失** — marked v5+ 移除 `highlight` 选项后 `highlight.jsx` 沦为死代码，代码块不再带 hljs span。改用 marked v13 正确的渲染器经 `marked.use` 输出完整 `<pre><code>` 块（含行号与语言标签）。`actions.jsx` 原先只扫描 `.msg-list`（legacy chat），复制/编辑按钮无法触达 workbench 的 `.wbc-thread`；现同时监听 `.msg-list`、`.wbc-thread`、`.wbc-side-body`，并在 SPA 导航后以 2s 轮询重新挂载观察器。`highlight.css` 选择器由 `.msg-body-only` 扩展到 `.wbc-msg-body.markdown`，行号、语言标签、操作栏与 hljs 主题在 workbench 全部生效。渲染器顶层 try/catch 在 hljs 失败时回退为纯转义文本，避免 `marked.parse` 崩溃。
- **Workbench Profile 仪表盘统计数据不实时刷新** — Profile 页消费 `DATA.dashboard`（KPI、活跃度热图、insights、top tools），但该数据仅在 bootstrap 时获取一次，SSE 事件总线与 15s 轮询均未触及。新增轻量 `GET /api/dashboard?tz=` 路由只返回 `_build_dashboard`，避免重建完整 `ui-data`；`refreshDashboard()` 带请求序列守卫与 JSON 指纹跳过无变化重渲，经 3s 防抖订阅 SSE 事件并纳入 15s 全局轮询。
- **默认 Workbench 项目可被删除** — `DELETE /api/projects/{id}` 端点对默认项目（`dataKey === "default"`）缺少守卫，会清空其 sessions、chats、memory。现对默认项目返回 `400 default_project_protected`，前端隐藏默认项目的删除按钮并在调用失败时给出友好提示。

### Changed

- **Workbench 路由 JSON 响应与错误分类** — `routes.py` 统一处理 JSON 响应格式，并对解析失败加入错误分类，便于前端区分瞬时错误与永久错误。

### Tests

- 新增 `tests/test_call_llm_candidates.py` 瞬时服务器错误用例 — 覆盖重试行为与失败处理。
- 更新 `tests/test_workbench_api_validation.py` — 覆盖默认项目删除保护（`default_project_protected`）。
- 更新 `tests/test_workbench_init_plan.py` — 覆盖 init 任务管理与优先级指示。

### Docs

- **README / .gitignore 与项目状态同步** — Quick Start 切换到 uv（lock file 已提交）并附 pip 回退；补充 WebUI JSX 预编译步骤（`compiled/` 被 gitignore、由 `index.html` 加载）与 Node.js 20+ 前置；列出 `browser`/`dev` 可选 extras、细化 Testing 限制说明，tech stack 加入 uv + Ruff。`.gitignore` 不再忽略 `uv.lock`，新增 `.ruff_cache/`、`backups/` 与根锚定 `/db.sqlite3[-*]`；移除误提交的根 `db.sqlite3`（真正运行时 DB 在 `store/cyrene.db`）。

## [0.6.0b8] - 2026-06-23

### Fixed

- **新建空项目错误地读取默认 workspace 内容** — 在 Workbench 新建项目时若未显式选择 workspace 路径，后端会静默回退到全局默认 `WORKSPACE_DIR`（`<repo>/workspace/`，非空），导致 `_is_workspace_empty()` 返回 `False`，init 流程跳过"全新项目"引导分支，改为启动 explore-agent 去列默认 workspace 里的 `SOUL.md`、`conversations/` 等已有文件。现改为：缺失 `workspacePath` 时自动在 `WORKSPACE_DIR/projects/<project_id>` 下创建一个空的逐项目子目录，确保新项目走"全新项目，工作区还没有代码"的引导路径。
- **前端预填当前项目 workspace 路径** — "新建项目"弹窗原先用当前活动项目的 `workspacePath`（通常是非空的全局 `WORKSPACE_DIR`）预填路径输入框，用户若不修改就会误传默认 workspace。改为默认留空，强制用户选择或走自动创建路径。
- **Glob/Grep 工具硬编码全局 workspace** — `tool_impl/glob.py`、`tool_impl/grep.py`（及 `tool_legacy.py` 中的 legacy 副本）的 `Glob` 和 `Grep` 工具原先硬编码 `WORKSPACE_DIR.glob(...)` / `path.relative_to(WORKSPACE_DIR)`，忽略了 `active_workspace_dir()` ContextVar，导致即使项目正确设置了 `workspacePath`，主 agent 的文件搜索仍然读取全局默认 workspace。改为使用 `active_workspace_dir()`，与 `Read`、`Bash`、`git_tools` 等工具对齐。这也修复了 Grep 在非默认 workspace 下 `relative_to(WORKSPACE_DIR)` 抛 `ValueError` 的潜在崩溃。

## [0.6.0b7] - 2026-06-23

### Added

- **Chat 编辑与分叉（Edit & Branch）** — Workbench Chat 支持编辑已发送的用户消息并从该点分叉出新对话；原对话保持不变，分叉对话标注"Forked"标记并可点击回溯源对话。同时支持从任意位置重新生成回复。
- **Workbench 个人资料页** — 导航栏新增独立的 Profile 页面，展示对话轮数、活跃天数等统计数据，支持头像/emoji 自定义与功能开关。
- **键盘快捷键管理器** — 新增平台感知的全局快捷键模块（搜索、新对话、新任务、命令面板、切换项目、折叠侧栏、设置）。⌘ 在 macOS / Ctrl 在其他平台自动适配；用户可在 Settings → Shortcuts 中自定义绑定并持久化到 localStorage。
- **Settings 浮层面板** — 新增浮动式设置面板，含 Shortcuts 等多个标签页。
- **Workbench SQLite 事务存储** — 新增 `workbench_store.py` 模块，以 SQLite 为单一真相源，通过 `BEGIN IMMEDIATE` + 三方合并实现并发写入安全；实体列表按稳定 `id` 合并，并发消息、会话、通知与记忆互不覆盖，替代旧的整文件 JSON 读-改-写循环。
- **Workspace 路径安全校验** — 新增 `workspace_validation.py` 安全边界模块，限制用户选择的 workspace 目录必须在允许的根路径下（home、workspace dir、temp、挂载点等），防止路径穿越。
- **API 请求校验** — 新增 `api_models.py`（pydantic 校验请求体）与 `api_errors.py`（统一 HTTP 错误处理）覆盖 Workbench API 路由。
- **DSML 流式过滤** — `call_llm.py` 新增 `_DsmlStreamFilter`，在流式输出中增量剥离 DeepSeek 文本 DSML 工具调用标记，防止标记泄露到 UI；原始文本保留供流结束后恢复为真实工具调用。
- **Context window 仪表** — Workbench 新增上下文分段 token 追踪与 chat context payload 展示。

### Changed

- **Chat 房间样式重构** — 头像列表与 subagent chip 布局重构，视觉更紧凑；用户消息编辑态与 fork 标记样式完善；触屏设备下编辑按钮可见性改善。
- **Chat 状态管理增强** — 编辑流程中对话状态处理更稳健；流式中间消息（工具调用过程中的 partial assistant turns）处理改进。

### Tests

- 新增 `tests/test_workbench_chat_fork.py` — 覆盖对话分叉功能与状态管理，确保原对话不受影响。
- 新增 `tests/test_dsml_stream_filter.py` — 覆盖流式 DSML 标记抑制，防止工具调用标记泄露到 UI。
- 新增 `tests/test_workbench_context_gauge.py` — 覆盖上下文分段 token 与 chat context payload。
- 新增 `tests/test_workbench_sqlite_store.py` — 覆盖 SQLite 存储并发操作与三方合并。
- 新增 `tests/test_workbench_api_validation.py` — 覆盖 API 请求校验与错误处理。
- 新增 `tests/test_conversation_archive.py` — 覆盖对话归档。
- 更新 `test_profile_stats.py`、`test_runtime_fixes.py`、`test_workbench_frontend_logic.py`、`test_workbench_init_plan.py`、`test_workbench_knowledge_archive.py`、`test_workbench_memory_language.py` — 补充 profile 统计、运行时修复、前端逻辑、初始化计划、知识归档与记忆语言偏好场景。

## [0.6.0b6] - 2026-06-22

### Added

- **RecallConversation 工具** — Agent 现在可以通过 `RecallConversation` 工具按关键词、session ID 或日期检索历史对话轮次，返回匹配的用户消息与助手回复摘要，支持数量限制（最多 10 条）。
- **search_project_memory 工具** — Workbench 任务内新增 `search_project_memory` 工具，Agent 可按分类、来源、关键词搜索当前项目的持久化记忆条目；仅在 Workbench 项目 context 内可用，其他场景返回结构化错误。
- **右侧面板调整大小 & 导航栏折叠** — Workbench 右侧面板（查看器/地图/等）现在支持拖拽调整宽度；左侧导航 rail 支持折叠/展开，收起后只显示图标，节省水平空间。

### Changed

- **提示词缓存优化（`static_system_extra`）** — `run_agent` / `_run_chat_agent` 新增 `static_system_extra` 参数，将 Workbench 任务执行模式和产物交付规则从每轮重新注入的 `ephemeral_system` 尾部提升到字节稳定的 SYSTEM 前缀，减少重复 token 处理；Goal Loop 同步接入。
- **`temporal_context` 移到 ephemeral 尾部** — 日期/时区上下文从 SYSTEM 前缀移至每轮 ephemeral 块，防止日期翻转导致整个 system+history 前缀缓存失效。
- **对话中间消息处理** — Workbench Chat 现在正确处理工具调用过程中的中间文本块（partial assistant turns），流式展示更流畅；`workbench-model.jsx` runtime 快照新增 `watchRequestId` 字段供滚动锚定使用。
- **RecallMemory 增强** — `RecallMemory` 工具调用路径重构，统一使用 `recall_conversations` 后端；Workbench Chat 侧边栏展示记忆检索结果时支持折叠/展开查看。
- **SimpleXNG 子进程守护** — 新增 `simplexng_child.py` 模块，SimpleXNG 搜索子进程通过父进程 PID 存活检测（含 PID 复用防护）在主进程退出时自动退出，避免僵尸进程。
- **用户资料页增强** — 资料统计数据（对话轮数、活跃天数等）接入 DB 层；新增 i18n 翻译覆盖中英文；data 层新增对应查询接口。
- **WeChat 频道 Web 接口健壮性** — `wechat/web.py` 补充异常处理，防止网络抖动导致未捕获异常。

### Tests

- 新增 `tests/test_wechat_web.py` — 覆盖 WeChat web 接口异常路径。
- 新增 `tests/test_workbench_dispatch_finalize.py` — 覆盖意图分流与任务收尾（finalize）流程。
- 新增 `tests/test_workbench_memory_language.py` — 覆盖 Workbench 记忆写入的语言偏好透传。
- 新增 `tests/test_searxng_manager.py` — 覆盖 SearXNG/SimpleXNG 管理器启动与候选端点逻辑。
- 新增 `tests/test_profile_stats.py` — 覆盖用户资料统计数据查询。

## [0.6.0b5] - 2026-06-20

### Security

- **HTML artifact 隔离** — 用户生成的 HTML 文件不再允许通过「↗」按钮在普通 Electron 子窗口中打开（子窗口会继承已认证的本地后端 session，存在 API 滥用风险）。HTML 预览保留在沙盒 `srcDoc` iframe 内；Artifacts 列表和 Viewer 中的「↗」外链按钮对 HTML 文件隐藏。

### Added

- **Artifact 一键下载** — Workbench 侧边栏 Artifacts 标签页中，`file_change` 类产物现在可直接点击行下载；路径在服务端校验不得逃逸项目 workspace。
- **`send_file` 显式信号** — Agent 调用 `send_file` 工具时，该文件以 `produced` 状态记入 file-change 追踪，优先级高于 git 推断结果。
- **HTML 查看器 base href 注入** — 内嵌 HTML 预览器自动在文档头部注入 `<base href>` 标签，使相对路径的脚本和资源能正确解析。
- **Subagent 文件交付指引** — Subagent 系统提示明确：产物文件应写入 workspace 并在 `quit` 摘要中报告路径，由主 Agent 统一通过 `send_file` 交付，Subagent 不应自行调用该工具。

### Fixed

- **SQLite 并发锁死** — `behavior_learning.py`、`db.py`、`workbench_goal_loop.py` 均切换至 WAL journal 模式并设置 busy timeout（15 s），消除 goal loop 与工具/聊天写入并发时出现的 "database is locked" 错误。
- **Schema 初始化幂等** — `_ensure_schema` 引入进程级 `_SCHEMA_READY` 缓存，避免每次读写都重复执行建表事务。
- **文件路径显示** — `_workbench_display_path` 修复相对/绝对路径计算逻辑：workspace 外的路径返回空字符串而非泄漏绝对路径。
- **Git 推断不覆盖工具写入** — `_workbench_merge_file_changes` 新增保护：git 推断的状态无法降级已由 Write/Edit/send_file 明确写入的文件记录。
- **失效文件记录清理** — `_workbench_prune_invalid_file_records` 在每次状态不变量检查时移除 path 缺失或 name 为空的陈旧记录。
- **`send_file` 路径解析** — `_resolve_exportable_path` 现在以当前 Workbench 任务的 `workspacePath` 为首选根目录，修复在项目 workspace 内写入的文件无法发送的问题。

### Changed

- **步骤进展 UI 精简** — 展开步骤的详情面板由卡片网格改为紧凑的摘要行布局（进展文本与相关文件并列显示）。
- **PDF 缩放修复** — PDF embed 元素现在将缩放值编入 `key`，确保缩放变化时正确重新渲染。
- **CI pre-release 自动检测** — release workflow 中 `is_pre` 标志改为从 tag 名自动匹配（`alpha/beta/[0-9]b[0-9]/rc`），无需手动维护。

### Tests

- 新增 `tests/test_workbench_artifact_download.py` — 覆盖 artifact 下载路径解析与路径穿越防护。
- 更新 `tests/test_workbench_frontend_logic.py`、`tests/test_workbench_init_plan.py` — 补充 goal loop 及初始化计划场景。

## [0.6.0b4] - 2026-06-19

### Added

- **跨平台 Shell 运行时** — 新增 `shell_runtime` 模块，自动检测用户 Shell 类型（bash/zsh/fish/PowerShell/cmd）并据此调整命令执行策略；非 POSIX Shell 下写入/删除类命令触发安全拦截，防止误操作。
- **语言偏好持久化** — 新增 `app_language` 配置项；主动 Agent（心跳、proactive chat）现在以用户界面语言回复，语言设置跨重启保持一致。
- **附件分析增强** — 改进附件类型识别与文件名处理；文档提取支持更多格式，提升知识库 ingest 稳定性。
- **工作区搜索集成测试** — 新增 `test_workbench_search.py`、`test_shell_runtime.py`、`test_bash_nonbash_guard.py` 等测试套件，覆盖 Shell 守卫、proactive 语言、知识库归档流程。

### Changed

- **`workspace_scope_block` 增强** — 非 bash Shell 下自动追加额外警告提示，明确限制跨目录操作。
- **Proactive Agent 上下文** — `_run_chat_agent` 和 `run_heartbeat_agent` 接入检测到的 Shell 类型与语言偏好，生成更符合用户环境的回复。
- **知识库路由健壮性** — `routes_knowledge.py` / `routes_workbench_knowledge.py` 统一错误处理；`list_knowledge_documents` 工具返回更完整的文件元数据。

## [0.6.0b3] - 2026-06-19

### Added

- **ListKnowledgeDocuments 工具** — Agent 现在可以通过 `ListKnowledgeDocuments` 工具枚举当前 Workbench 会话的知识库文档，返回每个文件的名称、索引状态（可检索/未检索）和 chunk 数量，支持按 status 过滤与数量限制。
- **Workbench run 结果自动归档知识库** — 每次 Agent 完成一个 Workbench 步骤，任务摘要（目标、请求、结果、产出文件列表）将自动以 Markdown 形式归档到项目知识库，Agent 后续可通过 `SearchKnowledge` 检索历史执行记录。
- **Linux 原生目录选择器** — Electron `dialog.showOpenDialog` IPC 接口，Linux 用户在 Chat 和 Create 界面现在可以使用系统原生文件夹选择对话框（macOS/Windows 不受影响）。

### Changed

- **流式运行引擎 (module-level runtime engine)** — `WorkbenchChatRuntimes` 提升到模块级别，对话流在用户切换页面或视图时不再中断：streaming 状态（文本、工具进度、中断句柄）跨组件 mount/unmount 完整保留，切回时自动追上最新内容。
- **Follow-up 任务上下文传递** — 追踪任务通过新 API 端点创建，携带当前会话的完整 context，避免 follow-up 任务缺少背景信息导致重复探索。
- **聊天会话删除** — `DELETE /api/chats/:session_id` 正确处理 `run_live`（重置实时会话）和 `archive_*`（按日期和 session ID 定位历史会话）两类特殊会话，不再返回 400。

### Fixed

- **Workbench 依赖校验** — 计划步骤依赖图现在拒绝循环依赖、缺失依赖和非法顺序；依赖辅助函数保留可见顺序，阻止未满足依赖的步骤执行。
- **通知已读状态** — 未读通知按可见性正确管理，切换视图不再误重置已读状态。

## [0.6.0b2] - 2026-06-19

### Fixed

- **Windows updater asset selection** — `_platform_filter()` 返回的 `win64.exe` 与 CI 实际产出的 `Cyrene-<ver>-win-x64.exe` / `-win-arm64.exe` 不匹配，导致 Windows 用户被推送 macOS `.dmg`。现按 `platform.machine()` 正确区分 x64/ARM64，并修复了 Linux token 大小写不一致导致匹配失败的 latent bug。新增全套平台匹配回归测试。
- **Plan regeneration failure handling** — 重新生成计划失败时不再创建兜底计划（`model.buildPlanSteps`），而是保留原计划不变，避免数据丢失。
- **Acceptance criteria verification** — 验收模型返回的 results 数组会按标准校验是否覆盖了所有 criteria，若遗漏则抛出结构化错误而非静默接受。

### Changed

- **Workspace-scoped agent context** — `_WORKSPACE_SCOPE_BLOCK` 从编译时常量改为 `workspace_scope_block(active_workspace_dir())` 运行时函数，确保主 Agent 和 Subagent 都限定到项目实际的 workspacePath，而非全局默认目录。Chat run / answer 接口均已接入项目 workspace。
- **Init plan 生成重试** — `_workbench_generate_init_task_plan` 默认重试 5 次（原为 1 次），每次失败记录分类和原因，5 次均失败后返回结构化错误（`init_plan_generation_failed`），不再返回兜底空计划。UI 新增 `InitPlanError` 组件展示失败详情和「重新开始」按钮。
- **LLM 错误分类与脱敏** — 新增 `_WorkbenchGenerationError` 异常类和 `_workbench_generation_error()` 转换函数，将超时 / 鉴权 / 限流 / 上游 / 网络错误分类为友好提示；错误文本中的 Bearer token、sk- 密钥、api_key 等敏感信息自动脱敏。
- **JSON 解析健壮性** — `_workbench_parse_json_object` 跳过无效的花括号片段；explore agent 在 JSON 解析失败时自动重试一次修复。
- **Plan revision 的 replace 模式** — 明确要求「全新生成」时不会把旧计划步骤塞进 prompt，减少模型锚定。
- **Cache-bust 版本号更新** — `index.html` 中各 JS/CSS 资源版本戳统一更新。
- **测试覆盖新增** — updater 平台匹配、init plan 重试/恢复/错误报告、workspace scope、search workspace 集成、generation error 脱敏等场景全面覆盖。

## [0.6.0b1] - 2026-06-18

### Added

- **Beta update channel** — New "Receive beta releases" toggle on the About → Updates card. When enabled, the in-app updater scans GitHub pre-releases (via the releases list) instead of only the stable `latest`, so testers can opt into beta builds.
- **Subagent UI** — Workbench now surfaces subagent status, payload inspection, and page-independent rendering; new subagent-related i18n strings added.
- **Plan revision flow** — Agent can now detect conflicts with an existing plan and revise it in-place while preserving step progress, instead of always generating a fresh plan.
- **Proactive activity detection tests** — New test suite for proactive user-activity triggers in the workbench scheduler.
- **Knowledge data clearing** — New API and test coverage for resetting knowledge data per workspace.

### Changed

- **Acceptance criteria handling** — Criteria are normalised and reset correctly on each plan generation cycle; improved error handling in `WorkbenchModel`.
- **Workbench chat** — Parallel-conversation support: removed `lockedByOther` lock state; subagent chat snapshots handled separately from main chat stream.
- **Welcome / onboarding** — Timezone setting now uses long-value layout for readability; onboarding timezone selection covered by tests.
- **App icon refresh** — Updated icon assets across all platforms (macOS `.icns`, Windows `.ico`, Linux `.png`).
- **CSS layout** — New subagent panel styles; responsive layout fixes for project list with open context menus.

### Fixed

- `routes.py` task controller no longer silently drops plan-conflict errors; returns structured revision response instead.
- `set_task_goal.py` legacy tool updated to match revised subagent payload schema.

## [0.6.0b0] - 2026-06-17

### Added

- **Windows on ARM support** — CI now builds native ARM64 installers alongside x64.
- **Workbench UI by default** — GitHub Releases now bundle the workbench UI as the default shell.

### Changed

- Windows installer filenames now include architecture: `Cyrene-${version}-win-x64.exe` / `Cyrene-${version}-win-arm64.exe`.

## [0.5.0] - 2026-06-07

### Added

- **Browser live view** — WebSocket-based live browser screencasting directly in chat; headless→headed takeover for native-window login flows.
- **Deep Reflection** — New agent capability for multi-round context reframing, improving reasoning on ambiguous or complex queries.
- **Desktop authentication** — Local auth middleware with OS keyring integration (macOS/Windows/Linux); port persistence across restarts.
- **Session export** — Export full session history to file; tool round refactoring for cleaner agent state.
- **SSRF protection** — Blocks server-side request forgery on user-supplied URLs; screenshot temp-file cleanup.
- **Content hash deduplication** — Documents tracked by content hash to prevent duplicate uploads in knowledge base.

### Changed

- **PDF viewer** — Embedded panel with pinch-to-zoom, touch events, and iframe isolation for attachment previews.
- **Permission system** — Permission snapshot before skill execution; high-risk tool confirmation flow; workspace scope guard for read/write/shell ops.
- **MCP management** — Server restart button; per-server environment variable editing in settings UI.
- **WeChat channel** — Pending question formatting and improved response routing for group chats.
- **Chat interface** — `watchRequestId` in runtime snapshot for scroll anchoring; mutation diff for assistant reply updates; internal field stripping before LLM calls.
- **macOS notifications** — Desktop notifications via `terminal-notifier` on macOS.

### Fixed

- Model failure handling in streaming responses now surfaces errors to the user rather than silently dropping them.
- DSML tool markup in final reply with retry mechanism for malformed responses.

## [0.4.7] - 2026-05-24

### Added

- **Pattern learning improvements** — Action tracking now persists compact args, enabling more informative pattern replay. Added subsequence-based deduplication for cleaner pattern candidates. New `scan_for_manual_learn()` endpoint promotes high-confidence historical patterns immediately without waiting for a new session.
- **Skill installer overhaul** — Now supports installing skills from directories and zip archives in addition to single files. Includes validation for archives (size limits, entry count, path safety). Tracks `source_kind` (file/directory/archive) per skill record.
- **Self-aware updater** — Electron now passes `CYRENE_APP_EXECUTABLE` to the Python backend, so update scripts target the real install path instead of hardcoded locations on macOS, Windows, and Linux.
- **Evolution page enhancements** — Added new learning pattern metrics (exchanges, avg length, directive count, cadence, rounds) with i18n support. New `patternIntro` copy explaining the learn-now flow.

### Changed

- **Flat surface design refresh** — New CSS variables system (`--canvas-bg`, `--surface-*`, `--control-*`) with updated light/dark theme backgrounds. Sidebar, topbar, dashboard, cards, and settings panels migrated to flat surfaces with refined shadows.
- **Pattern scanner** — Scripts list now sorted by creation date descending. Candidate metadata expanded with `first_seen`, `last_seen`, `confidence`, and `round_ids` for better debugging.
- **Skills UI** — Install picker now accepts folders and zips (macOS `choose file or folder`). Skill detail shows source kind. Build skill prompt block uses `entrypoint_name` for display.
- **Update scripts** — macOS: no longer hardcodes `/Applications/Cyrene.app`. Windows: uses real install path with proper process creation flags (`CREATE_NEW_CONSOLE | CREATE_NEW_PROCESS_GROUP | DETACHED_PROCESS`). Linux: atomic replacement via `.new` + `mv`. All scripts now `set -e` and use shell quoting for safety.
- **Evolution page layout** — Migrated from inline styles to CSS classes. New scrollable container layout prevents overflow.

### Fixed

- **Deep research reports** — Compressed in session history to reduce token usage.

## [0.4.2] - 2026-05-24

### Fixed

- **Claude Code terminal colors** — Complete rewrite of the CC terminal color pipeline:
  - Switched tmux default-terminal from `xterm-256color` to `tmux-256color` for truecolor (24-bit) support
  - Added UTF-8-aware C1→7bit control character conversion (`_c1_to_7bit()`) to handle tmux-256color's 8-bit CSI/OSC/DCS sequences
  - Fixed shell card preview lines leaking across conversations by adding per-session CC preview caching
  - Fixed expanded terminal layout: terminal now overlays the chat area correctly, with ResizeObserver-based auto-resizing and raf-retry for xterm.js renderer readiness
  - Restored visible footer with "Cyrene learning" metadata
  - Removed duplicate title in expanded terminal

## [0.4.1] - 2026-05-23

### Added

- **Multi-turn deep research report generation** — Phase 3 now generates reports section by section:
  1. Loads a report template defining the fixed section skeleton
  2. LLM generates a JSON outline with dynamic subsections under "核心发现"
  3. Each writing unit (section/subsection) is generated in a separate LLM call, allowing arbitrarily long reports beyond single-turn output limits
  4. References are accumulated per-section via `## New References` markers and automatically deduplicated and assembled into the final References section
  5. An optional expansion pass thickens thin sections when total length is below threshold

- **User-selectable report length** — Before starting research, the agent asks the user for desired report length (long/medium/short/custom) via `ask_user` with structured options buttons. Length preference controls number of research tracks and per-section detail.

- **Default report template** (`report_template.md`) — 7-section skeleton (执行摘要, 背景, 核心发现, 分析与启示, 局限性, 结论, 参考文献) bundled in the package. The template defines fixed top-level sections while allowing dynamic subsections under "核心发现".

### Improved

- **PDF report formatting** — Complete rewrite of `report_export.py`:
  - CJK font support: Noto Sans CJK SC (fallback to STSong-Light)
  - Improved layout: larger margins (20mm), better line spacing (leading=20), increased heading spacing
  - Proper heading hierarchy with bold section titles and visual separation

- **Settings persistence** — Settings loading and saving now uses deep copy to prevent mutation issues, with atomic writes for crash safety.

### Fixed

- **Report length question resume** — When the agent pauses to ask about report length, the `deep-research` command context is now preserved through the pending question metadata, ensuring the agent resumes with full deep research prompts and spawn policy.

- **CSS cache invalidation** — Static asset version bumped to force browser cache refresh.
