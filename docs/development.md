# Development

[English](development.md) · [简体中文](development.zh-CN.md)

## Development data directory

Source and Electron development runs use the platform-specific `Cyrene-dev`
data and cache directories, independently from an installed `Cyrene` build.
On the first Electron development start after this split, Cyrene copies the
former source-run `workspace/`, `store/`, `data/`, and `backups/` directories
into the new data root. Existing state already created under `Cyrene-dev` is retained under
`.legacy-development-data-backup-v1`, and the old source data remains in place.
The legacy shared Electron profile contributes only missing settings and plugin
files; caches and runtime directories from the installed build are excluded.

## Debugging

### Verbose Mode

Logs every LLM call (full prompt, tools, response, duration) and context trace to `data/debug_*.jsonl`:

```bash
python -m cyrene.platform.host --verbose
# or
uv run python -m cyrene --verbose
```

### Debug Logs

With `--verbose`, events are written to timestamped JSONL files:

```text
data/debug_20260617_133426.jsonl
data/debug_20260617_134417.jsonl
```

Each log line is a JSON object:

```json
{"type": "llm_call", "caller": "main_agent", "phase": "agent_run",
 "messages": [...], "response": {...}, "duration_ms": 423.0}

{"type": "tool_call", "caller": "subagent_poet", "tool": "send_agent_message",
 "args": {"to": "painter", "content": "..."}, "result": "Message sent.",
 "duration_ms": 150.2}
```

### Context tracing

When `--verbose` is enabled, every LLM call is tagged with `_ctx` provenance
metadata describing where each context block came from. These traces are
written to the debug JSONL and exposed through the API:

```bash
# List recent events
curl http://localhost:4242/api/context-debug/events?limit=10

# Get full event detail (including context trace)
curl http://localhost:4242/api/context-debug/events/evt_3b22f9a5c0cb
```

Via the CLI:

```bash
cyrene flow --session run_live --round round_xxx --id evt_3b22f9a5c0cb
```

Context tracing intentionally has no Workbench page after the classic UI
consolidation; use the API, CLI, or
`python -m cyrene.observability.context_debug` instead.

### Historical event inspection API

When `--verbose` is enabled, every LLM call and tool call gets a unique `event_id` (e.g., `evt_3b22f9a5c0cb`) that persists to disk. Even after a daemon restart, you can inspect full event details:

```bash
# List recent event IDs
curl http://localhost:4242/api/events/list

# Get full event detail (LLM input/output or tool args/result)
curl http://localhost:4242/api/events/evt_3b22f9a5c0cb
```

### Runtime and Workbench inspection

Use `cyrene status` for daemon health and metrics. Workbench chat/task details
show live Agent, Tool, Subagent, permission, and browser execution state;
`cyrene flow` and the event APIs provide the durable per-round trace.

## Testing

The project uses `pytest` with async support and a 60-second thread-based
timeout to avoid deadlocks. Runtime paths are isolated by test fixtures.

```bash
# Fresh locked dev/test setup
uv sync --all-extras

# Run all tests
uv run pytest -q -W error::pytest.PytestUnhandledThreadExceptionWarning

# Run a specific test file
uv run pytest tests/test_context_trace.py -v

# Topbar tabs, pinned resources, Library drag, export compatibility, and layout
uv run pytest -q \
  tests/test_workbench_recent_session_tabs.py \
  tests/test_workbench_pinned_resources.py \
  tests/test_library_tools.py \
  tests/test_chat_attachment_flow.py \
  tests/test_electron_titlebar_alignment.py
```

The normal suite uses fakes/local fixtures and must not require a live LLM
credential. Live provider/channel checks are separate manual integration tests.

The post-core-refactor working-tree run uses Python `3.12.11`, FastAPI
`0.136.1`, and Pydantic `2.13.4` from the locked environment and passes all
2,209 Python tests. The WebUI suite passes 27/27 tests, the Electron suite
passes 84/84 tests, and the production WebUI and wheel builds complete.

During the documentation audit, the OpenAPI test initially failed because its
hash had been captured with an ambient Python 3.13.12 environment using FastAPI
0.115.8 / Pydantic 2.12.5 rather than the versions already recorded in
`uv.lock`. A direct comparison found ten generator-level differences: four
upload schemas use `contentMediaType: application/octet-stream` instead of
`format: binary`, and the standard `ValidationError` schema adds `input` and
`ctx`. No route or application request model had changed.

The strict 259-operation hash was therefore recaptured in the locked
environment, and the contract now also asserts the exact FastAPI and Pydantic
generator versions. No schema field is filtered or ignored. Future dependency
updates must deliberately update both the version baseline and the reviewed
hash.

Additional release-relevant checks:

```bash
node --test electron/app-use.test.js
uv run python -m compileall -q src
npm --prefix src/cyrene/workbench/webui test
npm --prefix src/cyrene/workbench/webui run build
git diff --check
```

## Project Conventions

### Code Style

- Python 3.12+
- Ruff for linting (line length: 180)
- Type hints for all function signatures
- Async/await throughout (asyncio)

### Module and Dependency Pattern

Each module has a single responsibility. Cross-module communication uses:

- Function calls for direct imports
- Event bus (`cyrene.observability.debug`) for real-time UI updates
- Runtime inbox (`cyrene.platform.inbox`) for inter-agent messaging
- SQLite for structured persistence

The canonical layers are:

- `cyrene.core`: host-neutral Agent session, ContextTree, Hooks, plugin values,
  scopes, registry, and execution;
- `cyrene.plugins`: Cyrene's application host, model composition, Workbench
  contribution SDK, and canonical editable feature plugins under `builtin/`;
- `cyrene.workbench`: the Cyrene host adapter, with business modules grouped
  under `application`, `chat`, `tasks`, `projects`, `goals`, `planning`,
  `artifacts`, `sessions`, `control`, `workspaces`, and `ui`; persistence,
  FastAPI composition, and WebUI remain dedicated adapter packages;
- `cyrene.agents`: external ACP agent integration;
- `cyrene.model`, `cyrene.platform`, and `cyrene.observability`: provider
  support, process lifecycle, and diagnostics.

`cyrene.core` must not import product or adapter layers. FastAPI adapters belong
under `src/cyrene/workbench/http/`, and the sole frontend lives under
`src/cyrene/workbench/webui/`. The removed `agent`, `route`, and `webui`
top-level packages must not be recreated as compatibility shims.
Do not add business modules directly to the `cyrene.workbench` package root;
place each module in its owning domain package.

### Adding New Tools

1. Add the implementation to the owning pack under
   `src/cyrene/plugins/builtin/<pack>/`, or create a user `PluginPack` in the
   application data directory's `plugin_impl/` folder.
2. Define a `Plugin` with its input schema and async handler, then include it in
   the pack's `plugins` tuple.
3. Use `setup` for Session services/Hooks and `application_setup` for routes,
   process services, lifecycle, search, or Workbench contributions.
4. Keep only `Bash`, `Read`, `Write`, and `toolbox` in the fixed kernel; every
   feature tool belongs to a plugin and may be direct or toolbox-discoverable.
5. Add schema, policy, actor, localization, and host-contribution tests.

For Cyrene self-management, add public capabilities only to the main-only
`cyrene_tools` pack. UI actions must be registered on `uiSurface` with a stable
node/action ID, a bounded semantic handler, risk, and outcome; never add a
selector, script, raw-event, arbitrary-coordinate, or route-calling escape
hatch. Use `get_element` to de-duplicate an explicit node from DOM projection.
Background project/chat/data/update/lifecycle/session-message handlers remain
in `INTERNAL_ONLY_CONCRETE_TOOL_NAMES`.

Keep source and checked-in compiled WebUI output synchronized. The focused
control-plane checks are:

```bash
node --test electron/ui-surface.test.js electron/host-control.test.js
uv run pytest -q tests/test_app_control.py \
  tests/test_plugins.py \
  tests/test_tool_package_settings.py \
  tests/test_webui_consolidation_contract.py
```

For MCP server support, add servers through the Settings UI or `cyrene mcp add`.

## CI / Release

The repository has two GitHub Actions workflows:

- `.github/workflows/ci.yml` runs for pull requests, pushes to `main`, and
  manual dispatch. Its Linux jobs sync every locked extra, compile `src`, run
  the full pytest suite with unhandled thread warnings promoted to errors,
  build the WebUI, verify `src/cyrene/workbench/webui/static/app` is current, and run Electron
  App Use tests.
- `.github/workflows/release.yml` runs for version tags (`v*`) or manual
  dispatch. It builds the Workbench application as PyInstaller + Electron
  artifacts for macOS, Windows (x64/ARM64), and Linux, then validates the
  packaged backend, rendered desktop, installers, portable builds, terminal
  lifecycle, and platform-specific recovery paths.

The PR workflow does not replace real-platform packaged smoke, visual,
credentialed external-service, upgrade, or installer checks. Complete those
release gates before tagging. The frozen smoke test imports critical compiled
dependencies and all historical module aliases.

### Electron Development

```bash
uv sync --extra dev
cd electron
npm install
npm run dev
```

Electron executes `uv run cyrene --workbench --electron-mode`, so development
and manual source launches share the same project entry point and environment.

Source runs use separate `Cyrene-dev` application-data and cache directories by
default, while packaged applications continue to use `Cyrene`. Development
configuration, databases, workspaces, and `plugin_impl/` therefore cannot
overwrite an installed application's state. Set `CYRENE_USER_DATA_DIR`,
`CYRENE_CACHE_DIR`, or `CYRENE_BASE_DIR` to choose another isolated root.
