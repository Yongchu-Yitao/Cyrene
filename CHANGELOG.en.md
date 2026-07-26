# Changelog

[中文](CHANGELOG.md) · [English](CHANGELOG.en.md)

This English edition preserves the release history of the Chinese changelog.
The Chinese edition remains the most detailed record for older releases.

## [0.7.0b2] - 2026-07-27

The second 0.7.0 beta includes every change since `v0.7.0-beta.1`: terminal
wake-ups, completion-driven subagents, exact-scope permission review, runtime,
route, and Workbench consolidation, safe database migration, CI hardening, and
the simplified source startup command.

### Agents, subagents, and long-running work

- `StartShell(wake_on_exit=true)` can now outlive the current Agent turn. Cyrene
  persists the shell/chat/project/run relationship and starts a fresh Workbench
  turn with the exit code and bounded terminal tail. Busy chats queue the wake
  until their current run finalizes.
- Subagents now have explicit execution and discussion modes. Execution workers
  are governed by success criteria and evidence instead of the main Agent's
  normal round limit; discussion agents use separate round, per-agent message,
  total message, message-length, tool, wall-time, and information-gain budgets.
- Execution workers gained renewable leases, evidence checkpoints, repeated
  no-progress detection, and wide tool-call, wall-time, cost, and context safety
  fuses. Incomplete work retains partial results with an explicit outcome.
- Duplicate active Agent IDs are rejected, discussion budgets are isolated by
  discussion ID, cancellation/timeout/incomplete states are distinct, lifetime
  metrics survive reactivation, and the parent monitor has a bounded deadline.
- `send_message` is now a stable direct tool independent of the Delivery
  package. Tool lifecycle events always pair started calls with completed,
  failed, or cancelled terminal events, including progressive gateway calls.
- Added `memory.list` for a complete, countable cross-session and project memory
  inventory while preserving recall/search/save/retire boundaries.

### Permissions, safety, and auditability

- Automatic approval no longer grants full access to the rest of a run. Each
  approval creates a one-shot fingerprint bound to the exact tool, operation,
  permission kind, canonical path, command or external arguments, and reason.
- Shell and external MCP execution now elevate according to actual scope.
  Default mode keeps simple workspace-local commands smooth, while external
  working directories/paths, command substitution, opaque interpreters,
  network executables, auto-mode process execution, and MCP calls require exact
  review. Unknown MCP tools fail closed.
- A new `permission_decisions` table records session, round, source, tool,
  operation, path, result, rationale, and fingerprint. Workbench renders the
  scoped approval or denial; string values such as `"false"` cannot be treated
  as approval.
- Permission mode is normalized and persisted across retry, fork, replay, and
  recovery. Invalid modes fail closed. Forks retain attachments and state
  boundaries, and failed retries preserve the old public reply until a
  replacement succeeds.

### Performance, scheduling, and Workbench workflows

- Scheduled-task polling, proactive heartbeat, behavior learning, SOUL
  stewardship, and short-term cleanup now have independent coalesced cadences.
  Heavy maintenance is no longer coupled to every due-task poll, and the
  steward default/minimum interval is one hour.
- Behavior learning coalesces completed turns into one quiet-period job when no
  scheduler exists and avoids a second per-turn job in normal server runtimes.
  Single-tool, low-information turns skip the learning LLM.
- Usage and latency writes share database batches. Workspace finalization
  reuses unchanged snapshots based on mtime/ctime/size and marks change sets as
  exclusive or overlapping with the relevant run IDs.
- Electron gained a dedicated trusted browser-input module with React-compatible
  native setters, keyboard/input events, per-session tabs, shared login state,
  stale closed-tab protection, user-event learning telemetry, and background
  renderer throttling.
- Workbench now completes tool activities in place, shows finalization before
  workspace persistence, reconnects without resubmitting messages, truncates
  retries only after durable terminal events, and keeps LLM, plan, inbox,
  browser, and tool traces independent and localized.
- Additional Workbench hardening covers last-message retry, manual context
  compaction, project/user workspace selection, long paths, customizable
  shortcuts, clipboard/pasted files, drag-and-drop, narrow rails, native Linux
  framing and directory selection, durable settings forms, and actionable
  Knowledge, Memory, and learned-skill views.
- Literature Library regressions now cover project isolation, CRUD/statistics,
  trash and permanent batch deletion, file-type filtering, existing Knowledge
  bridging, source abstracts, idempotent Zotero sync, inline media, unique read
  events, and selection cleanup after filtering.

### Architecture, compatibility, and data

- All FastAPI adapters are centralized under `src/route/`, with registry-based
  assembly for Agent, Workbench, Settings, Tasks, Knowledge, Memory, Learning,
  Maps, Channels, System, and Code. Domain services no longer depend on WebUI.
- Canonical source ownership is organized under `agent/`, `workbench/`,
  `model_runtime/`, `learning/`, `runtime/`, `observability/`, `knowledge/`,
  `channels/`, `tooling/`, and `tool_impl/`. Stable public facades remain for
  `call_llm`, browser, subagent, memory, and tools.
- Historical imports resolve lazily to the same canonical module objects,
  preserving monkeypatch behavior, metadata, and executable aliases. Frozen
  smoke tests verify every legacy alias.
- The legacy `store/cyrene.db` migrates only when the new
  `store/cyrene.runtime.database` is empty. Migration uses SQLite backup
  semantics including WAL state, runs `quick_check`, writes an idempotent
  marker, switches atomically, and retains the source for rollback.
- Web, CLI, Electron, PyInstaller, and daemon modes now share runtime context,
  bootstrap, service, scheduler, update, and shielded shutdown ownership.
  Electron still launches the physical `local_cli.py`, which selects the
  checkout virtual environment.
- The canonical source command is now `uv run python -m cyrene`. The no-argument
  module entry starts the sole Workbench UI, `--workbench` remains compatible,
  Telegram requires `--telegram`, and `cyrene start` uses the same default.

### Single Workbench, build, and documentation

- WebUI now has one source tree under `src/webui/frontend`, organized into
  entry, platform, shared, and Workbench features, with one committed build
  output under `src/webui/static/app`. The classic UI, duplicate
  `workbench-webui`, legacy selector, redundant assets, and dead preload API
  were removed.
- Bootstrap readiness, navigation, SSE events, API/data storage, theme, i18n,
  Markdown/math/highlighting, diff, PDF, search, feedback, and browser view are
  shared infrastructure. Electron always loads this Workbench from the dynamic
  Python backend port.
- Regular CI now compiles Python and runs the complete locked all-extras pytest
  suite, rebuilds and checks committed WebUI output, and runs Electron App Use
  tests. Platform packaging and frozen smoke tests remain release gates.
- The strict 259-operation OpenAPI baseline was recaptured after reviewing ten
  generator-level schema deltas and now pins FastAPI 0.136.1 and Pydantic
  2.13.4; no schema fields are ignored.
- English and Chinese README, installation, usage, configuration, architecture,
  development, browser, project-note, handoff, roadmap, and design-QA material
  now match the single Workbench, package ownership, database name, managed
  processes, Literature/Zotero scope, WeChat QR setup, budget/backup/keyring
  boundaries, and Windows SimpleXNG limitation. Obsolete local QA screenshots
  were removed.
- The local beta2 release baseline passed all 1,403 pytest tests in the locked
  Python 3.12 environment, 49 Electron App Use/browser-input Node tests, the
  complete 32-entry WebUI rebuild, Python compilation, version consistency, and
  `git diff --check`. Platform packages and frozen smoke tests remain owned by
  the tag-triggered release workflow.

---

## [0.7.0b1] - 2026-07-23

The first 0.7.0 beta combined the post-0.6.17 Literature Library, proactive
Agent behavior changes, progressive tool-package disclosure, and cache/terminal
reply hardening.

- Added a project-isolated Literature Library with collections, tags,
  table/card views, search/filtering, a resizable detail workspace, metadata,
  notes, attachments, relations, reading status, themes, and responsive states.
- Idempotently mapped existing `kb_documents` into each project's Library
  without copying files or crossing project boundaries.
- Added file/PDF and CSL JSON/RIS/BibTeX import, Zotero Local API incremental
  sync, citation rendering, BibTeX export, and attachment-aware reading.
- Added Agent-facing structured Library listing, hybrid evidence search, and
  bounded updates of verified metadata.
- Replaced the full model-facing schema list with stable direct tools plus up
  to 12 package gateways using `discover → describe → invoke`.
- Kept Phase 1 and Phase 2 wire arrays byte-stable while enforcing phase policy
  at runtime; package switches now control schemas, prompts, and permissions.
- Showed actually used tool packages in Context rather than every enabled
  package.
- Removed an unnecessary full-history wrap-up when Phase 2 already produced a
  valid final answer, and hardened every final/stream/persistence path against
  DSML leakage.
- Made `npm run dev` launch Electron directly through `local_cli.py`.
- Constrained proactive work to one evidence-backed incremental task without
  modifying or deleting existing files.

Validation included 1,227 pytest tests, Ruff, Python compilation, Workbench and
PDF.js builds, 44 Node App Use tests, Electron syntax checks, Python
wheel/sdist, the macOS Electron package, frozen smoke tests, and version/lock
consistency.

---

## [0.6.17] - 2026-07-23

- Added compact navigation for long conversations and accurate summaries for
  attachment-only messages.
- Added a return-to-latest control when reading older messages.
- Made browser PiP avoid only vertically intersecting messages, improved
  drag/resize/reload stability, and deferred avoidance reflow during scrolling.
- Hardened guarded navigation credentials and takeover of links that open new
  tabs; improved interactive-element snapshots and removed empty live cards.

## [0.6.16-fix] - 2026-07-22

- Prevented browser PiP from covering the messages being read and stabilized
  layout during movement, streaming, and resize.
- Required short-lived snapshot-bound credentials for otherwise unreachable
  direct navigation, blocked duplicate navigation, and automatically adopted
  newly opened tabs.
- Improved snapshot relevance and avoided blank in-progress message bubbles.

## [0.6.16] - 2026-07-22

- Added per-run workspace change sets and reviewable diffs without requiring
  Git.
- Made PDF analysis select relevant pages across a document and moved PDF.js to
  its compatible legacy build.
- Added a launch screen that waits for first-screen readiness.
- Required Agents to prefer visible page links, improved fullscreen video and
  browser guidance, unified upload icons, and hid the disconnected sign-out UI.

## [0.6.15] - 2026-07-22

- Added an Agent composer and visible progress to maximized browser mode.
- Improved native/fallback browser transitions and Context polling efficiency.
- Stabilized context thresholds and strengthened visible-page-first navigation.

## [0.6.14] - 2026-07-18

- Made `browser_scroll` target real nested scroll regions using trusted wheel
  input, optional element refs or coordinates, and measured deltas.
- Added accurate root-versus-container user scroll telemetry and matching
  Electron/Playwright behavior.

## [0.6.13] - 2026-07-18

- Added draggable/resizable in-conversation browser PiP and platform-correct
  fullscreen video.
- Added guarded browser file upload, verified backup/restore with rollback,
  faster global search, safer App Use scrolling, and persistent `quit` replies.

## [0.6.12] - 2026-07-17

- Removed bundled Playwright/Chromium by default and unified browser tooling on
  Electron's `WebContentsView`.
- Added the live Context inbox, durable deduplicated mid-run guidance, and a
  compact Git-style branch tree.

## [0.6.11] - 2026-07-16

- Added per-session model affinity and failure cooldown.
- Returned clickable references from browser navigation.
- Gated App Use capabilities by platform and fixed live/persisted timeline
  ordering.

## [0.6.10] - 2026-07-15

- Simplified learned skills around explicit purpose and script synthesis.
- Calculated cost from the actual response model and cache usage.
- Kept Workbench surfaces mounted to remove page-switch flashes and improved
  reasoning activity timelines and context-window fallback behavior.

## [0.6.9] - 2026-07-14

- Made App Use coordinate operations auditable with screenshots and measured
  points.
- Made project activation an atomic lightweight update and added guarded
  project/chat caches.

## [0.6.8] - 2026-07-14

- Required fresh, bounds-checked App Use coordinates and explicit outcome
  confidence.
- Automated repeated-workflow skill learning and added bounded tool timeouts,
  non-blocking file I/O, and safer Workbench persistence retries.

## [0.6.7] - 2026-07-13

- Added the complete desktop pointer action set, atomic key sequences, and text
  selection.
- Fixed packaged App Use provider paths and added pasted files/images plus
  optimistic messages and guidance.

## [0.6.6] - 2026-07-13

- Shipped macOS and Windows desktop-app automation through a unified App Use
  gateway.
- Reduced latency by waking the Agent before asynchronous telemetry/database
  persistence and moving chat reads off the event loop.

## [0.6.5] - 2026-07-13

- Added durable mid-run Agent guidance, visible model failover, resource-keyed
  tool scheduling, and direct visual analysis of image attachments.

## [0.6.4] - 2026-07-11

- Added managed background-task shutdown and an embedded PDF viewer.
- Reworked project-isolated behavior learning and automatically completed goals
  whose acceptance criteria passed.

## [0.6.3] - 2026-07-07

- Added learned skills, auditable browser behavior capture, Chinese/English UI,
  paginated Knowledge, and adaptive context budgeting.

## [0.6.2] - 2026-07-05

- Added budget/economy controls, a localized native macOS menu, explicit
  send-file confirmation, mixed memory injection, and summary project queries.

## [0.6.1] - 2026-07-01

- Completed browser click/input/wait/network/tab tooling and added the persistent
  Electron Quick Chat tray workflow.

## [0.6.0] - 2026-06-29

- Introduced the project-centric Workbench, global Quick Chat, three-layer
  memory, live browser takeover, prompt-cache optimization, and Windows ARM64
  packages.
- Added transactional SQLite storage, workspace path safety, editable/forkable
  chats, artifact download validation, context compaction, honest task planning,
  durable runs, and independent subagent status.

## [0.5.0] - 2026-06-07

- Added live browser takeover, deep reflection, desktop authentication backed by
  the OS keyring, SSRF protection, PDF viewing, permission snapshots, and
  confirmation for high-risk tools.

## [0.4.7] - 2026-05-24

- Added directory/archive skill installation, portable update paths, compact
  learned arguments, executable-path handoff, and the flat-surface UI refresh.

## [0.4.2] - 2026-05-24

- Fixed terminal true color and control-sequence handling, cached Shell previews
  across conversations, and made terminal layout responsive.

## [0.4.1] - 2026-05-23

- Added section-by-section long Deep Research reports with a requested length
  handshake and CJK-capable PDF export.
