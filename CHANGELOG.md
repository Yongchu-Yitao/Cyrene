# Changelog

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
