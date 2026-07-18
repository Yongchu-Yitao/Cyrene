# Development

## Debugging

### Verbose Mode

Logs every LLM call (full prompt, tools, response, duration) and context trace to `data/debug_*.jsonl`:

```bash
python -m cyrene.local_cli --verbose
# or
python -m cyrene --workbench --verbose
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

### Context Debugger

When `--verbose` is enabled, every LLM call is tagged with `_ctx` provenance metadata describing where each context block came from. These traces are written to the debug JSONL and exposed through the Web UI **Context Debugger** page and the API:

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

### Event Inspection (legacy)

When `--verbose` is enabled, every LLM call and tool call gets a unique `event_id` (e.g., `evt_3b22f9a5c0cb`) that persists to disk. Even after a daemon restart, you can inspect full event details:

```bash
# List recent event IDs
curl http://localhost:4242/api/events/list

# Get full event detail (LLM input/output or tool args/result)
curl http://localhost:4242/api/events/evt_3b22f9a5c0cb
```

### Web UI Debug

The **Status** page shows live debug logs, system metrics, worker status, and service health. The **Agent Flow** page visualizes every step of the agent's execution as an interactive SVG flowchart.

## Testing

The project uses `pytest` with async support and a 60-second thread-based timeout to avoid deadlocks.

```bash
# Fresh dev test setup (installs package + test dependencies)
uv pip install -e ".[dev]"

# Run all tests
uv run pytest -q

# Run a specific test file
python -m pytest tests/test_context_trace.py -v
```

Some tests require an LLM endpoint to be configured. Set `OPENAI_API_KEY` and `OPENAI_BASE_URL` before running those tests.

## Project Conventions

### Code Style

- Python 3.12+
- Ruff for linting (line length: 180)
- Type hints for all function signatures
- Async/await throughout (asyncio)

### Module Pattern

Each module has a single responsibility. Cross-module communication uses:

- Function calls for direct imports
- Event bus (`debug.publish_event` / `debug.subscribe`) for real-time UI updates
- File-based inbox (`inbox.py`) for inter-agent messaging
- SQLite for structured persistence

### Adding New Tools

1. Create a new module under `cyrene/tool_impl/` (e.g., `my_tool.py`)
2. Export `TOOL_DEF` (dict) and `handler` (async callable)
3. Register the module in `cyrene/registry_tools.py::_NATIVE_TOOL_MODULES`
4. Optionally add a UI/settings entry in `src/webui/static/app/settings.jsx`

For MCP server support, add servers through the Settings UI or `cyrene mcp add`.

## CI / Release

The repository uses GitHub Actions for release builds:

- Workflow: `.github/workflows/release.yml`
- Triggers: version tags (`v*`) or manual dispatch with a choice of default UI (`workbench` or `agent`)
- Builds: PyInstaller + Electron for macOS, Windows (x64/ARM64), and Linux
- Smoke test: packaged app `--smoke-test`

There is currently no continuous-integration test job; run tests locally before tagging a release.
