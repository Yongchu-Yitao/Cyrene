# Changelog

[中文](CHANGELOG.md) · [English](CHANGELOG.en.md)

This English edition preserves the release history of the Chinese changelog.
The Chinese edition remains the most detailed record for older releases.

## [Unreleased] - 2026-07-26

- **Repository documentation was re-audited against `c1dbc62`.** Current guides
  now distinguish the single official Workbench UI from its multiple project
  workspaces, document managed child processes, the implemented Literature
  Library boundary, QR-based WeChat iLink setup, local estimated budgets,
  Fernet-key/keyring and portable-backup boundaries, active versus historical
  scheduler settings, and the Windows SimpleXNG packaging/runtime caveat.
- **The OpenAPI characterization baseline now uses the locked environment.**
  The previous hash had accidentally been captured with ambient FastAPI
  0.115.8 / Pydantic 2.12.5 rather than the long-standing `uv.lock` versions.
  After reviewing all ten generated-schema deltas, the strict 259-operation
  hash was recaptured with FastAPI 0.136.1 / Pydantic 2.13.4 and those generator
  versions were added to the contract. No field is ignored and the full suite
  now passes 1,402 tests.
- **A regular GitHub Actions CI workflow was added.** Pull requests, pushes to
  `main`, and manual runs now compile Python and run the full pytest suite in
  the locked all-extras environment, build and verify the checked-in WebUI
  output, and run Electron App Use tests. Release packaging remains a separate
  workflow.

- **Source ownership was reorganized by domain.** Canonical implementations now
  live under `agent/`, `workbench/`, `model_runtime/`, `learning/`, `runtime/`,
  `observability/`, `knowledge/`, `channels/`, `tooling/`, and `tool_impl/`.
  The `cyrene/` root retains stable public entry points and the physical
  `local_cli.py` launcher still required by Electron development.
- **Historical Python imports resolve to the canonical module object.**
  `runtime/module_compat.py` installs lazy aliases while preserving monkeypatch
  behavior, module metadata, and executable `python -m` aliases.
- **The old database filename migrates safely at first startup.** If
  `store/cyrene.db` exists and `store/cyrene.runtime.database` is not populated,
  startup takes a consistent SQLite backup including WAL data, runs
  `quick_check`, writes an idempotency marker, and atomically enables the new
  file. The source remains available for rollback and populated targets are
  never overwritten.
- **Startup and lifecycle ownership are unified.** Web, interactive CLI,
  Electron, PyInstaller, and the daemon share runtime context, initialization,
  and shutdown behavior. Real isolated `cyrene start`, `status`, API, and
  `stop` checks passed; Electron development still launches current source
  through `src/cyrene/local_cli.py`.
- **Compatibility and build validation expanded at that refactor checkpoint.**
  The suite then passed
  1,381 tests. The previous commit passed 1,286 functional tests, excluding one
  shape test that read the deleted physical `pattern.py` source. Electron App
  Use passed 44 tests. The frozen build verified 60 legacy aliases, 259 OpenAPI
  operations, Web startup, database migration, and clean shutdown.
- **Documentation now matches the source boundaries.** README, architecture,
  installation, usage, configuration, development, the refactor handoff,
  Research Workbench roadmap, and design QA now describe the current packages,
  database filename, and startup commands. The obsolete ignored local Research
  Workbench report artifacts were removed after their historical findings were
  retained in Design QA.

### Technical notes

- Database migration runs before any database initialization.
- `cyrene.call_llm`, `browser`, `subagent`, `memory`, and `tools` remain stable
  public facades.
- `local_cli.py` retains source-checkout `.venv` selection and now has a
  direct-path regression test, preventing Electron development from using a
  system Python that lacks project dependencies.
- FastAPI adapters live in `src/route/`, Workbench services in
  `src/cyrene/workbench/`, and Web lifecycle/static hosting in `src/webui/`.
- Frozen-build smoke tests import all historical aliases and verify identity
  with their canonical targets.

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
