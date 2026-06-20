# Changelog

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
