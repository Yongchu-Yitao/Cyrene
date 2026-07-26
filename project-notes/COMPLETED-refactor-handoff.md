> **COMPLETED — 2026-07-26:** The package-boundary refactor and WebUI /
> Workbench consolidation described here are finished. This file is the
> canonical post-refactor architecture and compatibility handoff.

# Cyrene Architecture Handoff

[English](COMPLETED-refactor-handoff.md) ·
[简体中文](COMPLETED-refactor-handoff.zh-CN.md)

Updated: 2026-07-26

Branch: `feature/project-literature-library`

Package-boundary baseline: `5e9a0044`

UI-consolidation worktree baseline: `17914e697af41c13a3c5da0092f69aa9906644af`

## 1. Executive Status

The package-boundary refactor and WebUI / Workbench consolidation are complete
and operational. Cyrene now has one canonical implementation location for each
backend domain, one Workbench frontend source root, and one generated Web
output root while preserving historical Python import and user-data behavior.

The current acceptance baseline covers:

- the complete current pytest suite;
- all previous-commit functional tests except one source-file shape assertion;
- exact OpenAPI and tool-wire compatibility;
- one Workbench UI with the classic shell, dual mount, and `--agent` selector
  removed;
- real `cyrene start/status/API/stop`;
- Electron development startup through the physical launcher;
- real Workbench, Quick Chat, theme/viewport, Browser, and PDF acceptance;
- first-start migration from the old database filename;
- newly built PyInstaller and Electron applications, including dynamic import
  aliases and a baseline-to-current packaged upgrade.

This handoff describes the architecture that should be preserved and identifies
future improvements that are separate from the completed directory migration.

## 2. Canonical Source Layout

```text
src/
├── cyrene/
│   ├── agent/               agent loop and its internal public API
│   ├── workbench/           Workbench business services
│   ├── model_runtime/       provider calls, messages, compaction, pricing
│   ├── learning/            behavior and learned-skill services
│   ├── runtime/             startup, lifecycle, persistence, scheduling
│   ├── observability/       trace, debug, and telemetry
│   ├── knowledge/           ingestion, embeddings, retrieval, library storage
│   ├── channels/            Telegram and WeChat adapters
│   ├── tooling/             tool catalog, policy, wire protocol, backends
│   ├── tool_impl/           concrete native tool implementations
│   ├── config.py            stable configuration facade
│   ├── call_llm.py          stable model-call facade
│   ├── browser.py           browser runtime/facade
│   ├── subagent.py          subagent orchestration
│   ├── memory.py            memory context facade
│   ├── cli.py               installed `cyrene` HTTP client
│   ├── tools.py             public tooling facade
│   ├── __init__.py          installs lazy historical import aliases
│   ├── __main__.py          `python -m cyrene`
│   └── local_cli.py         Electron/direct-file launch shim
├── route/                   FastAPI HTTP/WebSocket adapters
└── webui/                   app lifecycle, auth, static hosting
    ├── frontend/            sole Workbench front-end source root
    └── static/app/          sole generated/bundled output root
```

`local_cli.py` is deliberately not a business implementation. Electron
development mode executes that exact path, so removing it would break a real
startup flow. It aliases execution to `cyrene.runtime.host`.

## 3. Dependency Direction

The intended dependency flow is:

```text
Electron / Web UI / channels / CLI
                 │
                 ▼
          route + webui adapters
                 │
                 ▼
 agent / workbench / runtime / knowledge / learning
                 │
                 ▼
 model_runtime / tooling / observability / persistence
```

Rules:

1. Domain services must not import FastAPI route modules or front-end modules.
2. Route modules validate/translate requests and call domain services.
3. `webui.server` owns FastAPI application composition through
   `route.registry`.
4. Concrete tools live under `tool_impl`; discovery, policy, schema stability,
   and execution live under `tooling`.
5. New implementation code belongs in its canonical domain, not in a historical
   top-level module name.

Architecture tests enforce the allowed top-level `cyrene/` directories and
files.

## 4. Public and Historical Python APIs

Stable physical public modules include:

- `cyrene.config`
- `cyrene.call_llm`
- `cyrene.browser`
- `cyrene.subagent`
- `cyrene.memory`
- `cyrene.cli`
- `cyrene.tools`
- `cyrene.agent`

Historical paths such as `cyrene.db`, `cyrene.pattern`,
`cyrene.scheduler`, and `cyrene.workbench_runtime` are handled by
`cyrene.runtime.module_compat`.

The compatibility loader:

- imports targets lazily;
- returns the exact canonical module object;
- preserves monkeypatch behavior;
- restores canonical `__name__`, `__spec__`, and related metadata;
- supplies a virtual `cyrene.modules` namespace;
- supports executable aliases that are still invoked with `python -m`.

Do not recreate one-file wrappers for these aliases. Add a mapping and a
compatibility test instead.

## 5. Runtime Composition

The shared runtime is built from:

- `runtime.context` — immutable/resolved runtime paths and process context;
- `runtime.application` — manager/task ownership and application shutdown;
- `runtime.bootstrap` — ordered initialization and external services;
- `runtime.lifecycle` — cancellation and background-work cleanup;
- `runtime.host` — interactive, Web, Electron, and frozen entry modes;
- `runtime.paths` — source/bundled/user-data path resolution.

The startup order is intentionally:

```text
resolve paths
  → ensure runtime directories
  → migrate legacy database if needed
  → initialize the active database
  → initialize SOUL/inbox/short-term memory/learning
  → start scheduler and optional integrations
  → serve the selected interface
```

Shutdown owns schedulers, background tasks, browser/search/MCP processes, and
other registered managers. New long-lived resources must be registered with
the application lifecycle rather than left as untracked globals.

## 6. Database Filename Migration

The active database is:

```text
store/cyrene.runtime.database
```

The historical filename is:

```text
store/cyrene.db
```

`runtime.database_migration.migrate_legacy_database()` runs before database
initialization and follows these safety rules:

1. use the SQLite backup API so committed WAL data is included;
2. write to a temporary target and run `PRAGMA quick_check`;
3. add the `legacy-database-filename-v1` marker;
4. atomically replace only an absent or initialized-but-empty target;
5. retain the source database as the rollback copy;
6. never overwrite a populated target;
7. make repeated startup idempotent.

If both old and new databases contain data without a migration marker, startup
stops with an actionable error rather than choosing one silently.

## 7. Persistence Boundaries

Current storage is intentionally mixed:

| Data | Location |
|---|---|
| Main runtime state | `store/cyrene.runtime.database` |
| Project knowledge/library | `store/kb_<workspace>.db` |
| Encrypted configuration | `data/config.enc` |
| Behavior learning | `data/behavior-learning.db` |
| SOUL | `workspace/SOUL.md` |
| Short-term memory | `data/short_term.json` |
| Debug traces | `data/debug_*.jsonl` |
| Browser profile | `data/browser_profile/` outside Electron |

`runtime.sqlite_json` and `workbench.store` provide safe JSON/document
persistence helpers. Future domain repositories should build on these
boundaries rather than introducing ad-hoc SQLite connections in route modules.

## 8. Tooling Contract

The model-facing tool contract is a control plane, not a direct list of every
implementation:

- direct tools remain in a fixed wire bundle;
- enabled tool packages expose stable gateways;
- package use follows `discover → describe → invoke`;
- catalog snapshots are frozen per agent run;
- actor policy differentiates main agent, execution agent, and subagent access;
- stale or disabled calls are rejected at runtime.

The previous and current registries match at 94 tool definitions and handlers.
When moving a tool:

1. preserve its capability ID and concrete name;
2. preserve schema and result protocol;
3. update the canonical native-module registry;
4. keep policy metadata and actor restrictions;
5. run catalog, wire, package-settings, and compatibility tests.

## 9. Route and Workbench Boundaries

All HTTP/WebSocket composition is under `src/route/`:

- `route.registry` is the composition root;
- `route.agent` handles chat, sessions, browser, and collaboration adapters;
- `route.workbench` handles project, task-session, knowledge, memory, schedule,
  and chat adapters;
- `route.system` handles events, shell, updates, and instance identity;
- settings, code, maps, entities, tasks, and channels have dedicated adapters.

Workbench business logic is under `cyrene.workbench`. The large
`workbench.runtime` module remains a canonical composition module, not a
top-level historical file. New business logic should go into focused Workbench
services and be re-exported only when a stable consumer requires it.

## 10. Build and Entry Points

Supported entry points:

| Entry | Purpose |
|---|---|
| `cyrene start` | detached Workbench daemon |
| `cyrene status` / `cyrene stop` | daemon client operations |
| `python -m cyrene --workbench` | foreground Workbench Web UI |
| `python -m cyrene.runtime.host` | interactive headless REPL |
| `electron: npm run dev` | Electron development app |
| frozen `Cyrene --launch-web` | Electron/frozen Web backend |

The former `--agent` UI selector has been removed; all Web and Electron
launches enter Workbench. Historical build-mode value `agent` is normalized to
Workbench for old automation/artifacts and does not select another UI. The
PyInstaller spec enumerates all local Python modules because tools and adapters
use dynamic imports. The frozen smoke test imports critical compiled
dependencies and verifies all historical aliases.

## 11. Validation Baseline

The 2026-07-26 baseline is:

| Validation | Result |
|---|---|
| Current pytest | 1,390 passed |
| Previous-commit functional pytest | 1,286 passed |
| Previous source-shape test | 1 intentionally excluded |
| Electron App Use | 44 passed |
| Web UI build | 32 JSX sources; sole `static/app` output passed |
| OpenAPI | 259 operations, normalized schema unchanged |
| Tool registry | 94 definitions/handlers, unchanged |
| CLI lifecycle | start/status/API/stop passed |
| Source Electron | main window, backend/static/API, and Quick Chat passed |
| Browser/PDF/themes | real Browser/PDF and light/dark/system viewport checks passed |
| Legacy DB migration | source retained, data copied, marker and quick-check passed |
| Packaging/upgrade | PyInstaller, Electron, 60 aliases, frozen Web/API, and isolated baseline upgrade passed |
| Python compile and diff check | passed |

The excluded old test read `src/cyrene/pattern.py` as text. That file is no
longer part of the canonical tree; the functional import remains supported.

## 12. Remaining Work

The following are valid future improvements, not incomplete migration steps:

### P1

- introduce explicit Workbench domain models and repositories;
- split `subagent.py` into a typed state machine and coordination services;
- split `browser.py` into session, transport, policy, and capture boundaries;
- divide behavior learning into storage, candidates, versions, and execution;
- reduce import-time configuration/global mutation;
- add pull-request CI for pytest, Ruff, Node tests, and packaged smoke.

### P2

- split scheduler concerns into task execution, proactive work, steward,
  heartbeat, delivery, and cleanup services;
- expand typed project-level exception categories;
- continue front-end module decomposition;
- reduce Electron main-process responsibilities.

### Research Workbench

The project-scoped Library is implemented. Experiments, reproducible run
tracking, Manuscripts, and provenance remain roadmap work documented in
`research-workbench-roadmap.md`.

## 13. Change Checklist

Before merging future architecture changes:

```bash
uv run pytest -q
node --test electron/app-use.test.js
python -m compileall -q src
git diff --check
```

For release-affecting changes, also:

1. build Web UI static assets;
2. run `cyrene start/status/stop` in an isolated data directory;
3. run Electron development mode;
4. build PyInstaller and execute `--smoke-test`;
5. start the frozen Web backend and inspect `/openapi.json`;
6. verify old-database migration with a real SQLite fixture.

## 14. Do Not Regress

- Do not restore deleted top-level implementation files to satisfy tests that
  inspect source layout.
- Do not remove `local_cli.py` while Electron executes it directly.
- Do not copy SQLite files with raw filesystem copy during migration.
- Do not import route/Web UI modules from domain services.
- Do not expose every concrete tool schema directly to the model.
- Do not let package settings change a running agent's frozen capability
  snapshot.
- Do not document a Workbench Context Debugger page. Context tracing is retained
  through verbose JSONL, `/api/context-debug/events`, `cyrene flow`, and
  `cyrene.observability.context_debug`.
- Do not claim external LLM/channel/provider compatibility without credentials
  and a live integration test.
