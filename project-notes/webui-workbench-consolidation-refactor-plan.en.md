# Cyrene WebUI / Workbench UI Consolidation Refactor Plan

[中文](webui-workbench-consolidation-refactor-plan.md) ·
[English](webui-workbench-consolidation-refactor-plan.en.md)

> Status: planned, not implemented
>
> Updated: 2026-07-26
>
> Audit baseline: `feature/project-literature-library` / `5e9a0044`
>
> Scope of this document: planning only
>
> Goal: remove the classic/legacy UI, retain Workbench as the only UI, and
> consolidate `src/webui` and `src/workbench-webui` into one `src/webui`

## 1. Acceptance standard

This must be a behavior-preserving refactor, not a UI rewrite, framework
upgrade, or backend redesign. The only approved product change is that every UI
entry opens Workbench rather than the classic shell.

Completion requires all of the following:

1. `src/workbench-webui` is removed and all retained frontend source belongs to
   `src/webui`.
2. Classic pages, components, styles, dependencies, and shell branches are
   removed.
3. Shared facilities currently borrowed by Workbench are moved into explicit
   Workbench infrastructure before their old files are deleted.
4. Web, Electron development/package modes, PyInstaller, Quick Chat, first
   startup, and CLI launch paths work.
5. Projects, tasks, chats, Agent runs, tools, permissions, browser, diff, maps,
   PDF, Knowledge, Library, Memory, Schedule, settings, and integrations do not
   regress.
6. Existing data is neither removed nor silently rebuilt; historical chats and
   Knowledge remain readable.
7. OpenAPI, tool wire schemas/counts/names, actor policy, SSE semantics, and
   persistence formats do not change without approval.
8. Full Python/Node/Electron/build/frozen and representative E2E validation
   passes.
9. Tests are not deleted or weakened, exceptions are not swallowed, and
   permanent compatibility branches are not added merely to manufacture a
   passing result.

Deletion must stop whenever these conditions cannot be demonstrated.

## 2. Audited baseline

### 2.1 Backend baseline

Commit `5e9a0044` established the current `src/cyrene` domain boundaries:
business code is under `cyrene.agent`, `cyrene.workbench`, `cyrene.runtime`,
`cyrene.knowledge`, `cyrene.tooling`, and related packages; protocol adapters
are under `src/route`; `src/webui` owns FastAPI lifecycle, authentication, and
static hosting; historical imports use `cyrene.runtime.module_compat`.

The current documented baseline is 1,381 pytest tests, 259 OpenAPI operations,
94 tool definitions/handlers, and 44 Electron App Use tests. The original plan
audit also reran 74 architecture, route, and import-compatibility tests.

The UI consolidation must not simultaneously split
`cyrene.workbench.runtime`, rewrite the Agent loop, alter the tool protocol, or
perform another broad backend move.

### 2.2 The frontend is two source directories but one mixed page

The current structure is:

```text
src/
├── webui/
│   ├── server.py, auth.py, build-jsx.mjs, package.json
│   ├── workbench_*.py              # historical Python import compatibility
│   └── static/app/
│       ├── index.html, app.jsx, data.jsx
│       ├── browser-view.jsx, search.jsx, code/diff.jsx
│       ├── math.js, styles.css, legacy pages, assets and vendor scripts
│       └── compiled/                # ignored build output
└── workbench-webui/
    ├── workbench.jsx, workbench-chat.jsx, workbench-i18n.jsx
    ├── settings-overlay.jsx, workbench-library.*
    └── knowledge, memory, schedule, and other Workbench sources
```

`src/webui/server.py` mounts both `/static/workbench-ui` and `/static`.
`build-jsx.mjs` compiles JSX from both trees into
`static/app/compiled`, and `index.html` loads legacy, shared, and Workbench
scripts in a fixed order. Deleting either tree today would break Workbench.

### 2.3 Shared/legacy facilities Workbench still consumes

Before classic UI deletion, these dependencies need explicit ownership:

| Capability | Current source | Required destination |
|---|---|---|
| Initial state and refresh | `data.jsx` globals and refresh functions | Workbench data store |
| SSE dispatch | `data.jsx`, `window.__sseHandlers` | typed event bridge |
| Bootstrap/readiness | `app.jsx`, `index.html` | one Workbench bootstrap |
| Browser view | `browser-view.jsx` | shared browser feature |
| Search | `search.jsx` | Workbench search feature |
| Diff | `code/diff.jsx` and CSS | shared diff viewer |
| Markdown/security/math | marked, DOMPurify, highlight, KaTeX, `math.js` | one renderer |
| Maps and PDF | Leaflet, PDF.js | registered shared features |
| Theme tokens | `styles.css` | Workbench tokens/base styles |
| Formatting/feedback | legacy i18n, toast and confirm globals | shared services |
| Logos/assets | `static/app` absolute paths | unified assets |

React, ReactDOM, marked, DOMPurify, KaTeX, highlight.js, Leaflet, and PDF.js
remain active. xterm, vis-network, CodeMirror, and other dependencies must be
decided from runtime and bundle evidence, not filenames alone.

### 2.4 “Legacy” is not one category

The classic UI shell is removable. Historical `legacy:<project>:<session>` chat
IDs, Python import compatibility modules, PDF.js's intentionally selected
official `legacy/build`, old configuration/data, default project/KB semantics,
and old database migration are not classic UI and must remain compatible.

### 2.5 Entry and packaging assumptions that still need convergence

The repository still contains `--workbench`/`--agent`, runtime UI modes,
Electron `CYRENE_UI_MODE=agent`, `/?shell=legacy`, shell-switch IPC, build
UI-mode flags, separate PyInstaller collection for `workbench-webui`, tests
that read that directory, and launch tests that read the old index. Each must be
handled deliberately.

### 2.6 Working-tree condition

The current document/translation/report-untracking changes must be committed
separately or moved to a clean implementation branch. They must not be mixed
into the future UI refactor. The plan does not authorize that refactor now.

## 3. Scope and non-goals

In scope:

- move Workbench sources under `src/webui`;
- promote shared facilities that Workbench still uses;
- delete classic UI code, entry branches, styles, and exclusive dependencies;
- establish one entry, static namespace, and build;
- deduplicate only behavior-equivalent renderers/viewers/helpers;
- update server paths, Electron, PyInstaller, builds, tests, and docs;
- preserve historical data/import/API/Agent/tool contracts.

Not in scope:

- upgrading React, Electron, PDF.js, or the frontend framework;
- adding TypeScript, Vite, Next.js, Tailwind, or a component library;
- changing visual design or information architecture;
- splitting `cyrene.workbench.runtime` or broadly moving `src/cyrene`;
- redesigning APIs, Agent/tool/permission/context/memory/goal behavior;
- deleting data paths merely named legacy;
- implementing new Research Workbench roadmap features.

Any exception needs a separate proposal, baseline, and PR.

## 4. Target architecture

```text
src/webui/
├── __init__.py, __main__.py, auth.py, server.py, assets.py
├── build.mjs, package.json, package-lock.json
├── frontend/
│   ├── index.html
│   ├── entry/          # workbench.jsx, quick-chat.jsx
│   ├── platform/       # api, data store, events, Electron, readiness
│   ├── shared/         # feedback, i18n, markdown, PDF, diff, browser, UI
│   ├── features/       # chat, projects, tasks, knowledge, library, etc.
│   ├── styles/         # tokens, base, feature styles
│   ├── assets/
│   └── vendor/
└── static/             # build output only
```

The first move should preserve execution order and behavior before files are
split into this ideal tree. Do not move, rewrite, split, deduplicate, and delete
the same module in one commit.

Dependency direction is Electron/browser → WebUI entry/platform →
Workbench/shared features → route adapters → domain services. Domain code must
not import WebUI; routes hold no frontend state; features do not mutate each
other's internals; API/SSE/IPC/localStorage/static paths are centralized; any
temporary `window.CyreneUI` bridge has an inventory, owner, tests, and removal
phase.

Workbench becomes the only main entry. Quick Chat remains a separate surface
sharing the same data/event/API/services. First preserve the existing script
order in one directory, then introduce explicit esbuild entries in a separate
stage.

## 5. Zero-regression matrix

Characterization coverage is required for:

- Web/CLI/Electron/frozen startup, auth, static assets, API, readiness, shutdown;
- first-run onboarding and Quick Chat;
- projects, tasks, chats, Agent rounds/goals/inbox/subagents and recovery;
- permission correlation and non-bypassable approve/reject behavior;
- all 94 tools, progressive packages, schemas, actor policy, and settings;
- context budgets, compaction, persistence, trace, and live events;
- browser, file transfer, diff, Markdown/math, PDF, maps, Knowledge, Library;
- Memory, Schedule, search, settings, profile, updates, and channels;
- runtime/config/KB/learning/SOUL/debug/browser persistence;
- loopback security, secret masking, XSS/path/upload protection;
- Chinese/English formatting and accessibility, including keyboard, ARIA,
  contrast, and reduced motion.

Every uncovered area gets a characterization test before implementation changes.

## 6. Phased implementation

### Phase 0 — Freeze the baseline

Separate current documentation changes, create a clean `codex/...`
implementation branch, record toolchain/lock versions, save OpenAPI/tool/static/
HTML/global/API manifests, and prepare old-data fixtures. Exit only when full
baseline, Electron, Quick Chat, Web, frozen smoke, and the functional matrix are
reproducible.

### Phase 1 — Characterization and dependency inventory

Test every borrowed global capability, replace behavior-important source-text
assertions with DOM/Node/browser checks, add minimal main-window/Quick-Chat E2E,
catalog API requests and SSE contracts, and machine-check CSS variables/assets/
vendor globals. No legacy file can be deleted until tests identify whether
Workbench consumes it.

### Phase 2 — Mechanical source-tree merge

Use `git mv` to move `src/workbench-webui` into a temporary structure under
`src/webui/frontend`; update build/server/spec/test paths without changing
contents, URLs, selectors, script order, or globals; keep all old
`static/app` files for this step; compare builds, 404s, and console output; then
remove the empty directory and separate package collection. This must be a
revertible move-only commit.

### Phase 3 — Workbench bootstrap, data store, and event bridge

Extract launch/theme/readiness/entry/Quick Chat, replace `DATA` and refresh
globals with a shared store, and replace `__sseHandlers` with validated,
correlated, idempotent, reconnecting event dispatch. A temporary inventoried
bridge is allowed for unmigrated exports only. Exit when Workbench no longer
needs the legacy shell to start or refresh and listeners clean up correctly.

### Phase 4 — Promote shared UI capabilities

Move one item per small commit in this order: tokens/assets; feedback/errors/
states; i18n/formatting; Markdown/security/highlight/math; browser; search; diff;
maps; PDF; Electron/readiness. Each move requires explicit imports, unit and
browser tests, visual comparison, proof that the old implementation has no
consumer, and only then deletion. PDF.js `legacy/build` and DOMPurify must not
be removed or replaced with unsafe substitutes.

### Phase 5 — Deduplicate and split

Consolidate only behavior-equivalent PDF, Markdown, API/error/abort, feedback,
formatting, diff, style-token, i18n, refresh/cache implementations. Split by
feature boundaries and verify cleanup of listeners, abort controllers, timers,
object URLs, PDF workers, and browser sessions. ES modules/esbuild entries may
be introduced here, without dependency upgrades.

### Phase 6 — Remove classic UI

Prerequisites: Workbench/Quick Chat/first run/settings operate with no classic
script, static and runtime coverage show no consumers, and a real upgrade
exercise passes.

Remove classic shell/pages/styles/assets, `shell=legacy`, Electron shell switch,
the dual UI branch, classic build selection, and dependencies proven exclusive
to classic UI. Keep `--agent` as a deprecated Workbench alias for at least a
transition release unless version policy explicitly permits removal. Normalize
old frozen mode values and deprecate IPC safely.

### Phase 7 — Backend and compatibility cleanup

Audit real consumers before deleting any route. Non-Workbench-named APIs may
still serve Workbench, CLI, Electron, channels, or external clients.
`src/webui/workbench_*.py` compatibility modules are not classic UI and need a
separate module-identity/monkeypatch-preserving migration if changed. Investigate
the tracked `src/webui/db.sqlite3` before moving or deleting it.

### Phase 8 — Build, release, and documentation closure

Use one static root/namespace, always serve Workbench, collect only final
`webui/static` output in PyInstaller, always launch Workbench in Electron, make
Workbench the only formal build mode, update every guide/handoff/changelog, and
remove temporary bridges, redirects, flags, and stale comments.

## 7. Quality gates

Every commit:

```bash
python -m compileall -q src
pytest -q <focused tests>
cd src/webui && npm ci && npm run build
git diff --check
```

It must also leave no accidental generated changes, frontend 404/unhandled/
React lifecycle errors, unreviewed dependency/lock/license changes, or secrets
and real user data in fixtures/logs/bundles.

Every phase additionally requires full pytest, Electron App Use, Workbench and
Quick Chat E2E, Electron source startup, isolated `cyrene start/status/API/stop`,
OpenAPI/tool snapshots, import aliases, old data/KB/chat upgrades, theme/window
visual comparison, keyboard/modal/reduced-motion checks.

Release candidates require new install, in-place upgrade, supported-platform
build/start checks, Electron package restart/update, PyInstaller smoke, frozen
Web/OpenAPI/API, Quick Chat, resource cleanup, and non-happy-path stress.

Path-only test fixtures may move, but behavior assertions cannot be deleted.
Known failures need an issue, impact, mitigation, and removal condition.
Credentialless tests prove simulated/startup contracts only, not live provider
compatibility.

## 8. Data, security, and Agent contracts

Do not alter main-database migration order/protection, remove the old rollback
copy, break project KB/default semantics or `legacy:*` chat reading/forking, or
move config/SOUL/short-term/learning/debug/browser-profile data. Upgrade tests
record table/row counts and SQLite `quick_check`.

Preserve loopback auth, upload/download validation, secret masking, DOMPurify
ordering, minimal validated Electron IPC, and sensitive-log rules.

Frontend restructuring must not change capability IDs, tool schemas/results/
errors, actor policy, permission correlation, frozen run snapshots, plan/goal/
round/inbox/subagent transitions, compaction/budget/trace/session persistence,
or SSE state semantics.

## 9. Principal risks

High-impact risks include deleting globals still used by Workbench, changing
script order, hidden CSS regressions, duplicate/leaked SSE handlers, treating
data as disposable legacy, breaking `--agent` upgrades, missing frozen assets,
breaking Electron readiness/Quick Chat, deleting externally consumed APIs,
over-abstracting similar-but-different code, mixing backend/UI refactors,
removing compatibility vendor bundles, and relying on source-shape tests rather
than runtime behavior. The phased inventory, characterization, visual matrix,
upgrade fixtures, isolated commits, and release gates mitigate these risks.

## 10. Commit and review strategy

Use small independently revertible commits for: behavior contracts; source
move; single build root; bootstrap/store; event bridge; shared
theme/feedback/i18n; browser/search/diff; Markdown/math/PDF; deduplication;
classic removal; Electron normalization; PyInstaller packaging; documentation.

Every review states the baseline, distinguishes moves from logic changes,
identifies regression evidence, calls out data/API/SSE/Electron/frozen impact,
and explains isolated rollback. Do not squash the entire refactor into one
opaque commit.

## 11. Rollback and release

Keep phase boundaries revertible and never mix structural moves with rewrites.
Tag the last fully validated pre-deletion state. On a high-severity issue, roll
back to the single-tree pre-classic-deletion state instead of copying old files
back piecemeal.

UI consolidation should not migrate data. Any unavoidable migration is
versioned, idempotent, validated, and source-preserving; clearing databases or
user directories is not rollback.

Release through an internal/test channel first and monitor startup, static 404s,
frontend exceptions, SSE reconnection, Agent terminal state, PDF worker, and
Electron crashes. Keep deprecated `--agent` during observation and promote only
after new installs, upgrades, frozen, and Electron paths are stable.

## 12. Mechanical completion checklist

- [ ] `src/workbench-webui` does not exist.
- [ ] There is one frontend source root and one static output root.
- [ ] `/static/workbench-ui`, `shell=legacy`, and the classic root branch are gone.
- [ ] The final HTML loads no classic page scripts.
- [ ] Workbench has no unregistered `window.*` dependencies.
- [ ] Every static URL returns 200 in source, Electron, and frozen modes.
- [ ] Generated output is not a second hand-maintained source tree.
- [ ] No Workbench dependency is mislabeled and stranded as legacy.
- [ ] `--agent` compatibility has a tested deprecation/removal policy.
- [ ] Historical chats/databases/default KB and PDF.js compatibility pass.
- [ ] OpenAPI and tool wire snapshots have no accidental changes.
- [ ] All 94 tool handlers and actor policy remain stable.
- [ ] Architecture/route/import tests pass.
- [ ] Full pytest, Electron App Use, E2E, build, and frozen smoke pass.
- [ ] Manual main/Quick Chat/first-run/settings/chat/browser/PDF/Library evidence exists.
- [ ] Documentation no longer claims two formal UIs.
- [ ] Temporary redirects, bridges, flags, and TODOs are removed or tracked.

## 13. Definition of done

The refactor is complete only when `src/webui` is the sole Web UI package;
runtime loads no classic UI or its implicit globals/styles; all existing
capabilities remain intact except the explicitly removed visual shell; data,
permissions, APIs, and tools have no hidden regression; equivalent duplicates
are safely consolidated; source and generated output are separate; new install,
upgrade, Web, Electron, Quick Chat, PyInstaller, and supported platforms pass;
and an executable rollback exists.

Until every gate passes, the work remains planned or in progress—not complete
merely because directories were merged or one page opens.
