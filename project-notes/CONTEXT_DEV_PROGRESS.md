# Current Development Progress

[English](CONTEXT_DEV_PROGRESS.md) ·
[简体中文](CONTEXT_DEV_PROGRESS.zh-CN.md)

Updated: 2026-07-26

Branch: `feature/project-literature-library`

Baseline commit: `5e9a0044`

This file records the current development checkpoint. Older Windows/context
debugger command transcripts have been removed because they referenced modules
that now live under the canonical domain packages.

## Current Outcome

The Cyrene architecture reorganization is complete at the package-boundary
level:

- core code is organized under `agent/`, `workbench/`, `model_runtime/`,
  `learning/`, `runtime/`, `observability/`, `knowledge/`, `channels/`,
  `tooling/`, and `tool_impl/`;
- FastAPI adapters live under `src/route/`;
- Web application lifecycle and static hosting live under `src/webui/`;
- historical Python imports resolve lazily to canonical modules;
- the Electron development flow retains `src/cyrene/local_cli.py` as its only
  physical compatibility launcher;
- startup migrates `store/cyrene.db` to
  `store/cyrene.runtime.database` before database initialization.

## Audit of the Original 2026-06-01 Goals

The previous version of this file described the Context Debugger and
SimpleXNG cleanup. Those claims were rechecked against the current source and
targeted tests instead of being assumed complete.

| Original goal or issue | Current status | Evidence |
|---|---|---|
| Tag LLM context sources with `_ctx` provenance | Implemented, with an ongoing coverage invariant | `cyrene.observability.context_trace`; agent, coordinator, reflection, task-context, and model runtime call sites |
| Strip internal metadata before provider calls and persistence | Implemented | `model_runtime.client`, `observability.debug`, and `agent.session` |
| Persist a per-call context trace | Implemented | verbose JSONL events and `context_trace` summaries |
| Context Debugger UI | Implemented | `src/webui/static/app/context-debugger.jsx` and compiled asset |
| `GET /api/context-debug/events` and event detail | Implemented | `src/route/system/events.py` |
| Read in-memory and persisted debug events | Implemented | route/event log readers and debugger UI |
| Use built-in SimpleXNG instead of scraper fallbacks | Implemented | `cyrene.tooling.backends.search` only calls the SimpleXNG backend |
| Prevent loopback search traffic from using environment proxies | Implemented | `trust_env=False` plus merged `NO_PROXY/no_proxy` |
| Generate and pass SimpleXNG settings | Implemented | `searxng_manager` writes the settings path and child environment |
| Avoid `aiosqlite: Event loop is closed` during host shutdown | Fixed and regression-tested | shared application shutdown and `tests/test_runtime_host_shutdown.py` |
| Improve weather-specific answer quality | **Not implemented** | there is no dedicated weather provider/tool; generic WebSearch remains |
| Revalidate the historical Melbourne/Vancouver live LLM prompt | **Not re-run in this audit** | requires a live model/search integration and is not inferred from unit tests |

Targeted verification for the implemented items:

```text
168 passed
```

The command covered context tracing, SimpleXNG management, runtime shutdown,
and related runtime regressions with unhandled thread exceptions promoted to
test errors.

“Every possible context source” remains an engineering invariant rather than a
closed-world claim: new context-producing code must attach explicit metadata or
be covered by the trace summarizer's safe inference and tests.

## Canonical Debugging Commands

Start Workbench with verbose context tracing:

```bash
python -m cyrene --workbench --verbose
```

Start the interactive, non-Web runtime:

```bash
python -m cyrene.runtime.host --verbose
```

Start and inspect the background daemon:

```bash
cyrene start
cyrene status
cyrene flow --session run_live
cyrene stop
```

Inspect a debug JSONL file through the canonical module:

```bash
python -m cyrene.observability.context_debug \
  data/debug_YYYYMMDD_HHMMSS.jsonl --call 1
```

Historical executable aliases such as `python -m cyrene.context_debug` remain
supported, but new documentation and code should use the canonical path.

## Electron Development

Install Python and Electron dependencies, then launch from `electron/`:

```bash
uv sync --extra dev
cd electron
npm install
npm run dev
```

Electron executes `src/cyrene/local_cli.py --workbench --electron-mode`. The
launcher bootstraps the checkout's `src/` path and prefers the repository
`.venv`. Successful startup prints:

```text
UIMODE=workbench
PORT=4242
```

Chromium DevTools warnings for unsupported Autofill methods and unauthorized
optional source maps are development noise, not backend startup failures.

## Validation Baseline

Validated on macOS ARM64 with Python 3.12:

| Check | Result |
|---|---|
| Current pytest suite | 1,381 passed |
| Previous-commit functional tests | 1,286 passed |
| Excluded previous test | one static `pattern.py` source-text assertion |
| Electron App Use Node tests | 44 passed |
| OpenAPI comparison | 259 operations, schema unchanged |
| Tool registry comparison | 94 definitions and handlers, unchanged |
| Legacy module aliases | 60 verified in the frozen build |
| `cyrene start/status/API/stop` | passed in an isolated runtime |
| Legacy database migration | data retained, marker present, `quick_check=ok` |
| PyInstaller smoke/runtime | passed, including Web startup and clean shutdown |

The previous-commit exclusion is not a runtime behavior: the test directly read
the deleted physical file `src/cyrene/pattern.py`. The supported
`import cyrene.pattern` path still resolves to `cyrene.learning.facade`.

## Important Invariants

1. Do not recreate deleted top-level implementation modules as forwarding
   files. Add historical import names to
   `cyrene.runtime.module_compat.LEGACY_MODULE_ALIASES`.
2. Keep `local_cli.py` physical until Electron no longer executes it by path.
3. Run database migration before any connection opens the new runtime database.
4. Never overwrite a populated `cyrene.runtime.database` with legacy data.
5. Keep FastAPI composition in `route.registry`; domain services must not
   depend on route or Web UI modules.
6. Preserve the tool wire schema and actor policy when moving implementations.
7. Update the PyInstaller smoke test when adding a dynamic import boundary.

## Remaining Work

### Original Context/Search Scope

- Add a dedicated weather provider or structured weather extraction if
  day-level forecast quality becomes a product requirement.
- Run a credentialed Melbourne/Vancouver integration test before making a
  current claim about live model orchestration or result quality.

### Broader Product/Architecture Work

The directory migration is complete, but the following are independent future
improvements:

- replace remaining large dict-shaped Workbench models with explicit domain
  models and repositories;
- split the large browser and subagent orchestration modules into smaller state
  machines/transports;
- split behavior learning storage, candidate generation, versioning, and
  execution services;
- reduce import-time configuration mutation;
- add a normal pull-request CI workflow for pytest, Ruff, Node tests, and
  packaging smoke checks;
- implement the Research Workbench Experiments and Manuscripts phases described
  in `research-workbench-roadmap.md`.

These are not blockers for the current runtime or compatibility baseline.
