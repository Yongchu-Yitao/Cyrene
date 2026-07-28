# Current Development Progress

[English](CONTEXT_DEV_PROGRESS.md) ·
[简体中文](CONTEXT_DEV_PROGRESS.zh-CN.md)

Updated: 2026-07-28

Branch: `feature/project-literature-library`

Package-boundary baseline: `5e9a0044`

UI-consolidation worktree baseline: `17914e697af41c13a3c5da0092f69aa9906644af`

Current audited HEAD: `c1dbc62f24460d123b5bac03dc42ce9411319fb1`

This file records the current development checkpoint. Older Windows/context
debugger command transcripts have been removed because they referenced modules
that now live under the canonical domain packages.

The completed refactor records are
[the architecture handoff](COMPLETED-refactor-handoff.md),
[the WebUI consolidation plan](COMPLETED-webui-workbench-consolidation-refactor-plan.en.md),
and [the implementation log](COMPLETED-webui-consolidation-implementation-log.md).
This progress file remains unprefixed because it is a living status index, not
a closed refactor record.

## Current Outcome

The Cyrene architecture reorganization is complete at the package-boundary
level:

- core code is organized under `agent/`, `workbench/`, `model_runtime/`,
  `learning/`, `runtime/`, `observability/`, `knowledge/`, `channels/`,
  `tooling/`, and `tool_impl/`;
- FastAPI adapters live under `src/route/`;
- Web application lifecycle and static hosting live under `src/webui/`;
- `src/webui/frontend` is the sole Workbench source root and
  `src/webui/static/app` is the sole generated output root;
- the classic shell, dual static mount, and `--agent` UI selector are removed;
- historical Python imports resolve lazily to canonical modules;
- the Electron development flow retains `src/cyrene/local_cli.py` as its only
  physical compatibility launcher;
- startup migrates `store/cyrene.db` to
  `store/cyrene.runtime.database` before database initialization.

## Current Workbench Topbar Checkpoint

- The breadcrumb is replaced by a real-time MRU of the three most recently
  opened task/chat sessions, with persistent pin/hide state and a context menu.
- A distinct Pinned Resource Shelf accepts chat files, Knowledge/Library items,
  native macOS selected text, and floating/minimized Electron Browser surfaces.
- File/text resources can be delivered to another chat draft. Selected text
  and knowledge items without attachments are materialized as Markdown.
- Browsers can be dropped on another chat to copy the URL into that session's
  independent Browser manager. The topbar now supports arrow traversal,
  direct/cyclic session switching, and removal shortcuts.
- Pinned files are global user-resource indexes for later Agent turns. Pinned
  Browsers are owner-controlled and read-only to other sessions at tool
  execution.
- The detailed implementation and acceptance baseline is maintained in
  [Topbar Work Tabs and Pinned Resources Handoff](topbar-work-tabs-design.zh-CN.md).

## Audit of the Original 2026-06-01 Goals

The previous version of this file described the Context Debugger and
SimpleXNG cleanup. Those claims were rechecked against the current source and
targeted tests instead of being assumed complete.

| Original goal or issue | Current status | Evidence |
|---|---|---|
| Tag LLM context sources with `_ctx` provenance | Implemented, with an ongoing coverage invariant | `cyrene.observability.context_trace`; agent, coordinator, reflection, task-context, and model runtime call sites |
| Strip internal metadata before provider calls and persistence | Implemented | `model_runtime.client`, `observability.debug`, and `agent.session` |
| Persist a per-call context trace | Implemented | verbose JSONL events and `context_trace` summaries |
| Context Debugger UI | Intentionally not part of Workbench | Confirmed during the final consolidation handoff review; trace/API/CLI support remains |
| `GET /api/context-debug/events` and event detail | Implemented | `src/route/system/events.py` |
| Read in-memory and persisted debug events | Implemented | route/event log readers, `cyrene flow`, and `cyrene.observability.context_debug` |
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

## Current validation and historical baseline

The completed-refactor acceptance was validated on macOS ARM64 with Python
3.13.12. A fresh documentation audit on the current checkout used Python
3.12.11, FastAPI 0.136.1, and Pydantic 2.13.4:

| Check | Result |
|---|---|
| Latest stable working-tree pytest | **1,402 passed** |
| OpenAPI contract | 259 operations; strict hash passes with FastAPI 0.136.1 / Pydantic 2.13.4 |
| Historical audit diagnosis | 1,389/1 and 1,401/1 exposed a baseline captured with the wrong ambient dependency versions |
| Reviewed generator delta | Four upload-file items use `contentMediaType` instead of `format: binary`; standard `ValidationError` adds `input` and `ctx` |
| Historical post-settings-audit pytest | 1,390 passed on Python 3.13.12 |
| Previous-commit functional tests | 1,286 passed |
| Excluded previous test | one static `pattern.py` source-text assertion |
| Electron App Use Node tests | 44 passed |
| OpenAPI comparison | 259 operations, schema unchanged |
| Tool registry comparison | 94 definitions and handlers, unchanged |
| Legacy module aliases | 60 verified in the frozen build |
| `cyrene start/status/API/stop` | passed in an isolated runtime |
| Legacy database migration | data retained, marker present, `quick_check=ok` |
| PyInstaller smoke/runtime | passed, including Web startup and clean shutdown |

The earlier failure was isolated and compared across environments before any
baseline changed. `uv.lock` had already selected FastAPI 0.136.1 and Pydantic
2.13.4; the original characterization hash had accidentally been captured with
ambient FastAPI 0.115.8 and Pydantic 2.12.5. After reviewing every generated
delta and confirming no application route or request-model change, the strict
hash was recaptured in the locked environment. The test now asserts both
generator versions as well as the full schema hash, and no field is filtered.

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
- extend the pull-request CI beyond its current full pytest, WebUI-build, and
  Electron App Use coverage with Ruff and packaged smoke checks where their
  platform cost is justified;
- implement the Research Workbench Experiments and Manuscripts phases described
  in `research-workbench-roadmap.md`.

These are not blockers for the current runtime or compatibility baseline.
