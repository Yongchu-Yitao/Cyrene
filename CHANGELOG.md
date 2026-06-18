# Changelog

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
