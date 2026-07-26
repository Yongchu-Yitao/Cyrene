> **COMPLETED — 2026-07-26:** The consolidation, classic-UI removal, compatibility
> verification, packaging checks, documentation handoff, and post-handoff
> Electron settings audit recorded here are complete.

# WebUI / Workbench Consolidation Implementation Log

This is the completed implementation and verification record for
`COMPLETED-webui-workbench-consolidation-refactor-plan.md`. Historical baseline and
intermediate-stage descriptions below intentionally remain in past tense; the
current completion truth is Stage 5 and the architecture handoff.

## Baseline — 2026-07-26

- Branch: `feature/project-literature-library`
- HEAD: `17914e697af41c13a3c5da0092f69aa9906644af`
- Upstream: `origin/feature/project-literature-library`
- Initial worktree: clean
- Recent baseline commits:
  - `17914e6 fix: preserve Electron launch and refresh bilingual docs`
  - `5e9a004 refactor: reorganize runtime with legacy compatibility`
  - `0a4926d refactor: centralize API routes under src/route`
- Environment:
  - Python `3.13.12`
  - Node `22.17.0`
  - npm `11.7.0`
  - macOS/Darwin `25.5.0 arm64`

### Baseline verification

| Command | Result |
|---|---|
| `pytest -q` | 1,381 passed in 73.18s |
| `python -m compileall -q src` | passed |
| `cd src/webui && npm run build` | passed; 43 JSX files compiled |
| `node --test electron/app-use.test.js` | 44 passed |

The pytest run emitted one pre-existing
`PytestUnhandledThreadExceptionWarning`: an `aiosqlite` worker attempted to
signal a result after its event loop had closed during
`test_visual_describe_default_prompt_requires_a_concise_coordinate_summary`.
It did not fail the baseline, but it remains recorded rather than hidden.

### Frozen contracts

- OpenAPI: 259 operations; normalized SHA-256
  `1d87052e49c842e2cca8827d25931372454fc183c1e418aabfcc928b8a1312d9`.
- Native tool registry: 94 definitions and 94 handlers; normalized SHA-256
  `0860b150897c7272b9942c6c251c9b25f0fd99a86a526c4fafb33a999e75f863`.
- Main wire bundle: 28 definitions; SHA-256
  `29194c2d58bcb62679e65d2e7abb20e94235582d7c01dcd76f7f649f5f29f8f2`.
- Subagent wire bundle: 22 definitions; SHA-256
  `3b73d89cf5ad5cc83e32cc587891fdc562e035228c7c24d7f29471586c35a247`.
- Main-only actor-policy set: 35 concrete tool names.
- Front-end build: 43 JSX inputs plus the PDF.js core, worker, viewer, CSS,
  and 78 image assets.
- PDF.js intentionally uses the official `legacy/build` and `legacy/web`
  distributions for Electron 35 / Chromium 134 compatibility.

`tests/test_webui_consolidation_contract.py` makes these contracts executable.
It also records the pre-move Workbench cross-script globals, the surviving
script dependency order, and behavioral data-store/SSE expectations:
bootstrap data replacement, subscriber delivery, heartbeat filtering, the
200-event ring buffer, unknown-event tolerance, and chat-correlated browser
state.

### Baseline dual-root and runtime dependencies

- `src/webui/static/app` is both a maintained source/assets tree and the build
  output parent.
- `src/workbench-webui` contains 17 Workbench JSX/CSS source files.
- `webui.server` mounts `/static` and `/static/workbench-ui`.
- `build-jsx.mjs` scans both source roots and emits 43 classic scripts into
  `src/webui/static/app/compiled`.
- `index.html` loads third-party globals, classic/shared scripts, Workbench
  scripts, then `app.js` in a fixed order.
- Workbench currently has 90 direct `window.*` references. The baseline test
  separates browser/vendor globals from 65 application-owned cross-script
  globals; no new hidden application global is allowed during consolidation.
- Literal front-end API audit found 89 route prefixes across Workbench plus its
  shared runtime dependencies. Dynamic path suffixes and methods remain covered
  by the existing route/API behavior tests.
- SSE is currently sourced from `/api/events` in `data.jsx`; notable stateful
  behavior includes heartbeat filtering, notification dispatch, a 200-item
  recent-event ring, shared subscribers, refresh coalescing, map updates,
  browser-frame correlation, and browser-takeover lifecycle state.

### Legacy classification and protected compatibility

The deletion target is only the classic UI shell and its exclusive front-end
files. The following are explicitly protected:

- `legacy:*` historical chat IDs and their read-only/fork behavior;
- lazy historical Python import aliases, including `webui.workbench_*`;
- the PDF.js official legacy bundles;
- `default`, `kb_default.db`, historical database migration, and rollback data;
- `src/webui/db.sqlite3` pending a separate evidence-backed ownership audit.

### Rollback point

The initial rollback point is clean HEAD
`17914e697af41c13a3c5da0092f69aa9906644af`. No runtime source, user data, API,
or persistence behavior has been changed in the baseline/characterization
stage.

## Stage 1 — Characterization and dependency inventory

Status: complete.

Added an executable consolidation contract suite without changing production
behavior. The next gate is to run it together with the existing launch,
Workbench front-end, PDF, route, architecture, and historical-compatibility
tests before the mechanical source move.

Validation:

- 86 focused consolidation/launch/PDF/architecture/compatibility tests passed.
- `python -m compileall -q src` passed.
- `npm run build` passed with the unchanged 43-JSX input count.
- `git diff --check` passed.

Rollback: remove the two characterization/log files. No production behavior was
changed in this stage.

## Stage 2 — Mechanical Workbench source move

Status: complete.

Completed:

- Moved all 17 files from `src/workbench-webui` to
  `src/webui/frontend` without editing their behavior.
- Updated test fixture paths mechanically.
- Changed `build-jsx.mjs` to compile the moved JSX files from the new root.
- Kept `workbench.css` and `workbench-library.css` byte-for-byte as source and
  copied them into the existing `/static/app` build namespace.
- Removed the `/static/workbench-ui` server mount.
- Removed the independent `workbench-webui` PyInstaller data collection.
- Removed all live source/test/build references to `workbench-webui` and
  `/static/workbench-ui`.

Deleted:

- The now-empty `src/workbench-webui` directory.
- Only its duplicate static mount and packaging rule; no UI behavior or shared
  implementation was deleted.

Validation:

- 193 path-sensitive and front-end behavior tests passed.
- Full `pytest -q`: 1,386 passed in 78.06s, with the same recorded aiosqlite
  event-loop-close warning class.
- Electron App Use: 44 passed.
- `npm run build`: 43 JSX inputs, both Workbench CSS outputs, PDF.js legacy
  core/worker/viewer, and 78 images built successfully.
- `python -m compileall -q src` and `git diff --check` passed.
- Executable OpenAPI, tool registry/wire, actor policy, script order, and
  data/SSE baseline contracts remained unchanged.

Behavior change: none intended. The Workbench CSS URL moved from the deleted
`/static/workbench-ui` mount to the equivalent single `/static/app` namespace.

Remaining risk: the runtime still loads `app.jsx`, `data.jsx`, classic/shared
scripts, and direct application globals. The move is a clean structural rollback
point before bootstrap/data/event ownership changes.

## Stage 3 — Workbench bootstrap and shared/platform ownership

Status: complete.

Completed:

- Added `frontend/entry/bootstrap.jsx` as the sole Workbench/Quick Chat
  composition root.
- Established `CyreneUI` as the only application-owned cross-script registry.
- Moved bootstrap data, refresh coalescing, SSE correlation, the recent-event
  ring, unknown-event forwarding, navigation, readiness, and common fetch/error
  behavior under `frontend/platform`.
- Moved the existing theme tokens, toast/modal service, i18n formatting and
  search strings, Markdown/DOMPurify/highlight/KaTeX pipeline,
  `BrowserViewportPanel`, `SearchOverlay`, `DiffViewerPanel`, and PDF.js
  bridge under `frontend/shared`.
- Changed the launch-screen ready handshake from an ad-hoc
  `window.markCyreneReady` function to the one-shot `cyrene:ready` DOM event.
- Changed the Workbench shell to acquire its model through
  `CyreneUI.require("model")`; it no longer consumes a hidden
  `window.WorkbenchModel` dependency.
- Preserved `window.cyrene` as the explicit Electron preload bridge and retained
  third-party globals (`React`, `marked`, `DOMPurify`, `hljs`, `katex`,
  `Leaflet`, and PDF.js) in their existing versions and script order.

Behavior-equivalence fixes found by characterization and real-browser testing:

- `platform/runtime` must load before the shared Markdown highlighter registers
  itself. The initial moved order caused an immediate browser exception and was
  corrected in both HTML and the executable script-order contract.
- The migrated `map_pin` handler still called the deleted private `__bump`
  symbol. It now calls the public data-store bump operation; the SSE
  characterization test covers map, browser correlation, future events, and
  the 200-event ring.
- Known SSE event names now include the existing plan/progress/tool,
  destructive/external-upload confirmation, guidance, and browser-operation
  events. Unknown future events remain forwarded.

Lifecycle ownership:

- Data-store disposal closes `EventSource`, clears refresh/safety/reconnect
  timers and the periodic refresh, and removes event subscribers.
- Markdown code actions clear their poll and disconnect every
  `MutationObserver`.
- Toast timers and pending feedback promises have an unload disposal path.
- Embedded and standalone PDF consumers abort loading, detach selection/copy
  listeners and event-bus listeners, disconnect observers, and destroy loaded
  PDF documents/workers.

Validation to date:

- `npm run build`: 32 JSX inputs; sole output
  `src/webui/static/app`; PDF.js core/worker/viewer and 78 images retained.
- 153 focused platform, launch, Workbench, PDF, and consolidation tests passed
  after the lifecycle work.
- A further 149 Workbench/model/Quick Chat/consolidation tests passed after
  eliminating the final hidden model/ready globals.
- Real in-app browser checks passed for first-run onboarding, existing-user
  Workbench, Quick Chat, Search, Settings, Chat, Knowledge, Schedule, Memory,
  the standalone PDF shell, light/dark/system themes, and 1440×900 plus 800×600
  viewports. These were local isolated-backend checks, not live provider or
  credential tests.
- `python -m compileall -q src` and `git diff --check` passed at each sub-step.

Rollback: Stage 2 remains the structural rollback point. The Stage 3 changes
can be reverted service-by-service because consumers were switched before each
old implementation was removed.

## Stage 4 — Classic UI deletion and entry-point convergence

Status: complete.

Deleted after consumer migration and reference checks:

- Classic shell/page sources: `app.jsx`, `chat.jsx`, `chat-surface.*`,
  `dashboard.jsx`, `agents.jsx`, `sessions.jsx`, `status.jsx`,
  `context-debugger.jsx`, `settings.jsx`, `tweaks-panel.jsx`,
  `knowledge.jsx`, `memory.jsx`, `entities.jsx`, `evolution.jsx`,
  `tasks*.jsx`, `map-view.*`, `topbar.*`, and `wechat_settings.jsx`.
- Classic-only CodeMirror editor/action/highlight sources, xterm assets, and
  vis-network assets.
- The corresponding CodeMirror/Lezer npm dependency graph (25 installed
  packages); the retained direct dependencies are exactly esbuild `0.28.0`,
  KaTeX `0.16.22`, and PDF.js `6.1.200`.
- Electron shell-switch IPC/preload API, `shell=legacy`, the dual static mount,
  and the separate Workbench PyInstaller collection.

The final handoff review confirmed the Context Debugger is intentionally not a
Workbench page. Its classic page was deleted with the old shell, while verbose
JSONL, `/api/context-debug/events`, `cyrene flow`, and the canonical
`cyrene.observability.context_debug` module remain supported.

Preserved intentionally:

- `legacy:*` chat/archive parsing, read-only display and fork behavior;
- all lazy historical Python imports and physical `local_cli.py` launcher;
- official PDF.js `legacy/build` and `legacy/web` distributions;
- `default`, `kb_default.db`, historical SQLite migration and rollback files;
- historical global API routes and payloads even though no classic UI consumes
  them.

Entry behavior:

- Web root, Electron main window, Quick Chat, source `--workbench`, daemon, and
  frozen `--launch-web` all resolve to Workbench.
- The `ui_mode` Python parameter and old build metadata values normalize to
  Workbench for historical callers/artifacts; they do not select a second
  shell.
- Authorized deviation from the original plan: on 2026-07-26 the user
  explicitly allowed the `--agent` compatibility branch to be deleted rather
  than retained as a warning alias. The flag and its help/documentation paths
  are removed.

No files containing `legacy` were deleted by name. Every retained compatibility
category above remains covered by focused or full-suite tests.

Rollback: the staged rename set and unstaged logic/deletion set remain separate
in the working tree. No commit, push, PR, user-data mutation, or destructive
database migration has been performed.

## Stage 5 — Documentation and release validation

Status: complete. The environment-only Keychain fixture limitation recorded
below was explicitly accepted by the user on 2026-07-26 after all
repository-controlled code and release gates passed.

Updated the bilingual README, development/usage guides, architecture document,
handoff, current-development checkpoint, research report sources, roadmap path
references, browser feasibility notes, project-notes index, release-workflow
description, and static Web documentation to describe one Workbench UI and the
current `frontend`/`static/app` ownership. The historical consolidation plans
and this implementation log retain old path/shell terms where they are part of
the record rather than live guidance.

### Automated and compatibility gates

| Command / gate | Result |
|---|---|
| `pytest -q` | 1,388 passed in 79.68s; final rerun 1,388 passed in 85.60s |
| `node --test electron/app-use.test.js` | 44 passed |
| Historical database/KB/import/route/Quick Chat compatibility group | 171 passed in 12.00s |
| `python -m compileall -q src` | passed |
| `cd src/webui && npm run build` | passed; 32 JSX files and the sole `static/app` output built |
| `git diff --check` | passed |
| OpenAPI normalized snapshot | unchanged: 259 operations and baseline hash |
| Tool registry/wire/actor policy snapshots | unchanged from the baseline hashes/counts |

The historical group explicitly covered architecture boundaries, route
structure, Python import aliases, SQLite migration, chat fork/search/archive,
knowledge resolution, reset/bootstrap behavior, PDF.js compatibility, PDF
analysis context, and Quick Chat targets. It retained `legacy:*`,
`kb_default.db`, `default`, and official PDF.js legacy-build behavior.

### Runtime and visual acceptance

- Isolated source Web UI: first-run onboarding, existing-user Workbench, Quick
  Chat, Search, Settings, Chat, Knowledge, Schedule, Memory, and the standalone
  PDF shell passed.
- Light, dark, and system themes passed at 1440×900 and 800×600. These were
  real in-app browser checks; the launch overlay disappeared after the
  one-shot readiness event on both main and Quick Chat surfaces.
- A real 45-page PDF was uploaded to the isolated server and rendered through
  the retained PDF.js legacy worker. The viewer exposed 45 pages, populated
  text layers and canvases, and zoom changed from 100% to 115%. Closing the
  tab exercised the registered viewer cleanup path.
- Isolated `cyrene start`, `status`, root/static/API requests, and `stop`
  passed. Port 4242 was confirmed released afterward.
- Source Electron launched the sole Workbench shell with all platform/shared
  assets and API/SSE requests returning 200. The desktop settings API reported
  the Quick Chat accelerator registered, and the real independent Quick Chat
  `BrowserWindow` was opened through the production `openQuickChat` path. Its
  target, screenshot, context, attachment, permission, model, composer, and
  close behavior passed. A temporary test-only launch trigger was removed
  immediately after this check and is absent from the final diff.

These local checks did not call a live model/provider or third-party account:
no test credentials were available, so provider-authenticated replies,
external integrations, and real notification delivery are not claimed.

### Frozen and packaged release acceptance

- A clean PyInstaller build completed twice from the sole
  `src/webui/static/app` resource root.
- The frozen `--smoke-test` passed all critical imports, 60 historical module
  aliases, and confirmed Playwright is intentionally absent from the Electron
  bundle.
- The frozen `--launch-web` backend served the Workbench root, compiled
  bootstrap, UI-data API, OpenAPI, and PDF worker. The deleted
  `/static/workbench-ui` mount returned 404.
- Electron arm64 packaging produced `Cyrene.app`, DMG, and blockmap. The
  post-package signature passed strict verification.
- The packaged Electron app launched in an isolated diagnostic data directory
  with `CYRENE_CONFIG_KEYRING=0`; every Workbench/platform/shared asset and API
  request returned 200 and the real packaged onboarding window rendered.
- A detached baseline worktree at
  `17914e697af41c13a3c5da0092f69aa9906644af` created a project and Workbench
  chat in an isolated data directory. The current source runtime reopened the
  same directory and preserved both IDs, names, descriptions, and chat title
  while serving the sole Workbench root. The temporary worktree was removed.
- A second baseline-to-package upgrade used the existing documented
  `CYRENE_CONFIG_KEYRING=0` diagnostic mode in an entirely isolated temporary
  directory. Baseline HEAD created project `project_599defd338` and chat
  `wbchat_bf37d0958d`; the current packaged Electron app then opened that same
  directory, rendered the project in the Workbench shell, navigated to the
  preserved chat, and displayed the original title and exact chat ID. All
  project/chat/context requests returned 200. This closes the database,
  `config.enc` + file-key, frozen-backend, renderer, and packaged-Electron
  upgrade chain without touching the user's macOS Keychain.

### Accepted environment limitation

A normal packaged launch against the baseline-source-generated encrypted
configuration did not reach the renderer. Electron reported its 30-second
backend startup timeout. A live process sample showed the frozen backend
blocked inside macOS Security.framework at `SecItemCopyMatching` while the
Python keyring backend read the existing global `cyrene/config_key`; no
SecurityAgent confirmation window was exposed. The same package starts
immediately when the repository's documented diagnostic opt-out
`CYRENE_CONFIG_KEYRING=0` is used, proving the bundled backend and consolidated
UI themselves load.

This occurs before WebUI server startup and is not attributed to the UI
consolidation. Source-to-current and source-to-current-package in-place
upgrades are both verified, including encrypted configuration in the supported
isolated file-key diagnostic mode, but it still prevents an honest claim that
a normal-keyring, pre-refactor *packaged* in-place upgrade was verified on this
machine. Resolving it requires either access to a representative pre-refactor
packaged/keychain fixture whose ACL permits the new signed binary, or an
independently approved keyring/signing fix outside this UI-only refactor. No
user keychain item was modified or deleted.

On 2026-07-26 the user confirmed that repository code correctness is the
completion criterion and accepted this unavailable external fixture check. It
does not leave a code defect, temporary compatibility branch, or unverified
repository-controlled gate.

### Final handoff and documentation review — 2026-07-26

The architecture handoff was re-audited against the consolidated tree. The
user confirmed that the Workbench should preserve the previous commit's UI
behavior/visual result and that Context Debugger does not need to move into the
WebUI. No Context Debugger source, build output, script tag, or temporary
Workbench integration remains from this review; its non-UI trace interfaces
remain documented and supported.

The final documentation pass updated every affected bilingual guide, current
status/handoff/plan record, static documentation page, release-workflow
description, feasibility anchor, project-notes index, and Research Workbench
report source path. Historical old-root references remain only inside the
clearly labeled pre-refactor plan and implementation timeline.

Final review gates:

| Gate | Result |
|---|---|
| Consolidation/launch/Quick Chat/PDF/context focused pytest | 32 passed in 2.28s |
| Electron App Use | 44 passed |
| WebUI build | 32 JSX sources; sole `static/app` output passed |
| Python compile | passed |
| Local Markdown links | all resolved |
| Research report artifact JSON | valid |
| Release workflow YAML | valid |
| Stale live-document path/UI scan | passed; historical plan/log references only |
| `git diff --check` | passed |

Rollback: no commits were created. The structural move, service ownership
changes, classic deletion, and documentation remain separately visible as
staged renames plus unstaged logic/deletion changes.

### Post-handoff Electron settings audit — 2026-07-26

After the consolidated source Electron app was opened against the existing
local profile, entering Settings caused the React tree to unmount and left an
almost empty window. DevTools identified the exact renderer failure:
`ReferenceError: dataState is not defined` in `UpdateSection`. The About/update
section referenced the data store state that was local to `DataPanel`; the same
latent scope error was present in the pre-refactor Workbench source at HEAD
`17914e6`.

The behavior-preserving fix makes `UpdateSection` read `appVersion` from the
already registered platform data store. No API, payload, persisted setting,
navigation, styling, or UI structure changed. The same real-app pass also
found and removed two React reconciliation warnings by giving shortcut groups
stable fragment keys and passing the model card's fixed children as static
children. Regression contracts cover the store dependency and both list-key
invariants.

Real Electron verification used the running source app and its existing local
data:

- Settings opened from the Workbench toolbar without blanking the renderer.
- General, Appearance, Shortcuts, Models, Capabilities, Skills, Channels,
  Agents, Data, Budget, and About all rendered in sequence.
- About displayed version `v0.7.0b1`, update status, release branch, published
  date, and related links.
- Both the visible `ESC` control and the Escape key closed the overlay; it
  reopened afterward.
- After a clean reload and a second complete 11-tab pass, DevTools contained
  no uncaught exception, reference/type error, or React key warning. The only
  console notice was Electron's known development-only insecure-CSP warning.

Focused verification passed: WebUI build (32 JSX sources), Python compile,
four settings/about regression tests, 26 settings/consolidation/Quick Chat
tests, all 44 Electron App Use tests, and `git diff --check`. The post-audit
full suite passed: 1390 tests in 87.49 seconds.

Rollback: remove the local data-store lookup in `UpdateSection`, the two
React-key-only changes, and their two regression contracts. No user data was
written, deleted, or migrated during this audit.

### Completed-document normalization — 2026-07-26

The final documentation audit rechecked the repository-controlled Definition
of Done against the current tree and retained compatibility code. The closed
refactor plan, architecture handoff, and implementation record now use the
`COMPLETED-` filename prefix and begin with an explicit completion banner.
Every repository Markdown reference was updated to the new canonical names;
the removed filenames have no redirect or duplicate copy.

Living documentation such as `README*`, `docs/architecture*`,
`docs/development*`, `docs/usage*`, and `CONTEXT_DEV_PROGRESS*` remains
unprefixed because it describes the current product rather than a closed
refactor. Those documents and the static documentation site were updated to
match the actual single-Workbench source/build layout, compatibility-only
`ui_mode=agent` normalization, Python 3.13.12 verification environment, and
the current 1,390-test acceptance result.

### Later repository-wide documentation re-audit — 2026-07-26

A later pass at HEAD `c1dbc62` audited all 34 tracked Markdown files. Product
guides were corrected to describe the multi-project Workbench, managed child
processes, project Library scope, QR-based WeChat iLink flow, local estimated
budgets, Fernet-key/keyring boundary, current model-pricing catalog, and
the Windows SimpleXNG packaging/runtime boundary. Historical plans, changelog
entries, design evidence, templates, and ignored local research notes were
reviewed as historical or purpose-specific records rather than rewritten as
current product promises. The obsolete ignored local Research Workbench report
artifact directory was then removed at the user's direction after its findings
were retained in Design QA.

Current verification used Python 3.12.11, FastAPI 0.136.1, and Pydantic 2.13.4:
1,389 pytest tests passed and the OpenAPI snapshot test failed with generated
SHA `4b37a638…` versus frozen `1d87052e…`, while the operation count remained
259. The failure was reproduced in isolation and was not hidden by changing
the expected hash. WebUI build (32 JSX sources), Python compile, Electron App
Use (44 tests), local Markdown links, and `git diff --check` passed.

Those results apply to HEAD `c1dbc62` with only the documentation-audit delta
present at execution time. Source changes made afterward require their own
validation and are not covered by this record.

After the later source changes stabilized, a separate full working-tree run
completed with 1,401 passes and the same sole OpenAPI normalized-hash failure.
Python compilation, the 32-source WebUI build, and all 44 Electron App Use
tests also passed on that working tree. A cross-environment schema comparison
isolated the hash change to upload-file generator metadata (`contentMediaType`
versus `format: binary`) and the standard `ValidationError` `input`/`ctx`
fields; the historical Python 3.13.12 / FastAPI 0.115.8 / Pydantic 2.12.5
combination still passes the frozen hash.

Resolution on 2026-07-27: `uv.lock` had already selected FastAPI 0.136.1 /
Pydantic 2.13.4, so the original hash was confirmed to be an incorrectly
captured ambient-environment baseline rather than the repository's locked
baseline. After the ten generator-level deltas were reviewed, the strict hash
was recaptured under the locked versions and those versions were added as
explicit assertions. No schema field was filtered or ignored. The complete
working-tree suite then passed **1,402 tests**.

This resolution preserves the recorded UI-consolidation completion and
supersedes both the earlier “current 1,390-test acceptance result” and the
temporary failure records above. Living status is maintained in
`CONTEXT_DEV_PROGRESS.md`.
