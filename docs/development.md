# Development

[English](development.md) · [简体中文](development.zh-CN.md)

## Debugging

### Verbose Mode

Logs every LLM call (full prompt, tools, response, duration) and context trace to `data/debug_*.jsonl`:

```bash
python -m cyrene.runtime.host --verbose
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
{"type": "llm_call", "caller": "main_agent", "phase": "phase1",
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
python -m pytest tests/test_context_trace.py -v

# Topbar tabs, pinned resources, Library drag, export compatibility, and layout
uv run pytest -q \
  tests/test_workbench_recent_session_tabs.py \
  tests/test_workbench_pinned_resources.py \
  tests/test_workbench_library.py \
  tests/test_chat_attachment_flow.py \
  tests/test_electron_titlebar_alignment.py
```

The normal suite uses fakes/local fixtures and must not require a live LLM
credential. Live provider/channel checks are separate manual integration tests.

The latest stable working-tree run uses Python `3.12.11`, FastAPI `0.136.1`,
and Pydantic `2.13.4` from the locked environment and passes all 1,402 tests.

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
python -m compileall -q src
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
- Runtime inbox (`cyrene.runtime.inbox`) for inter-agent messaging
- SQLite for structured persistence

Canonical implementation packages are `agent`, `workbench`, `model_runtime`,
`learning`, `runtime`, `observability`, `knowledge`, `channels`, `tooling`, and
`tool_impl`. Historical imports are maintained in
`cyrene.runtime.module_compat`; do not add duplicate top-level shim files.

FastAPI adapters belong under `src/route/`. Domain code must not import route or
Web UI modules.

### Adding New Tools

1. Create the module in the matching domain under `cyrene/tool_impl/`
   (for example `cyrene/tool_impl/knowledge/my_tool.py`)
2. Export `TOOL_DEF` (dict) and `handler` (async callable)
3. Add its module path to `cyrene/tool_impl/__init__.py::NATIVE_TOOL_MODULES`
4. If it is deferred, assign one stable capability ID in
   `cyrene/tooling/packs.py::CAPABILITY_BINDINGS`; direct tools must be an
   intentional addition to the fixed wire contract
5. Add policy metadata/tests and optionally a UI/settings entry

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
  tests/test_progressive_tool_packages.py \
  tests/test_tool_package_settings.py \
  tests/test_webui_consolidation_contract.py
```

For MCP server support, add servers through the Settings UI or `cyrene mcp add`.

## CI / Release

The repository has two GitHub Actions workflows:

- `.github/workflows/ci.yml` runs for pull requests, pushes to `main`, and
  manual dispatch. Its Linux jobs sync every locked extra, compile `src`, run
  the full pytest suite with unhandled thread warnings promoted to errors,
  build the WebUI, verify `src/webui/static/app` is current, and run Electron
  App Use tests.
- `.github/workflows/release.yml` runs for version tags (`v*`) or manual
  dispatch. The compatibility `ui_mode` input still accepts `workbench` or
  historical `agent`, but the build normalizes both values to the sole
  Workbench UI. It builds PyInstaller + Electron artifacts for macOS, Windows
  (x64/ARM64), and Linux and runs the packaged app `--smoke-test`.

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

Electron directly executes `src/cyrene/local_cli.py`, which bootstraps the
checkout and delegates to `cyrene.runtime.host`. Keep that physical launcher
until Electron's process contract changes.
