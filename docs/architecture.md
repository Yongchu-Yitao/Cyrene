# Architecture

[English](architecture.md) · [简体中文](architecture.zh-CN.md)

## Two-Phase Agent Loop

Cyrene uses a two-phase decision loop that keeps the model-facing wire schema
stable while enabling concrete capabilities only when needed. The maximum
number of tool rounds is configurable (default 15).

```
User Message
    │
    ▼
Phase 1 (runtime policy allows use_tools / ask_user / quit)
    ├── Pure chat → return directly (1 LLM call)
    └── Needs tools → Phase 2
            │
            ▼
    Phase 2 (same fixed wire tool definitions)
    │   ├── Direct: filesystem, Bash, WebSearch/WebFetch, AnalyzeAttachment
    │   ├── code_tools / browser_tools / desktop_tools
    │   ├── memory_tools / knowledge_tools / task_tools
    │   ├── entity_tools / map_tools / subagent_tools
    │   ├── delivery_tools / skill_tools / integration_tools
    │   ├── Each module: discover → describe → invoke
    │   └── quit → end interaction
    │
    ▼
Response returned to user
```

The ordinary main-agent Phase 1 and Phase 2 calls receive the same
deterministically ordered wire bundle for the current package settings: all
direct tools plus the enabled package gateways. The Capabilities page switches
complete packages on or off. A disabled package is omitted from both the
model-facing tool schema and package-specific system-prompt instructions; its
capabilities also remain blocked by runtime validation. Changing a package
setting intentionally creates a new prompt-cache prefix, while subsequent calls
reuse that prefix until settings change again. Direct tools, including
`AnalyzeAttachment`, are not controlled by package switches.
Deep Research keeps a dedicated lightweight length-preference handshake and is
intentionally outside this cache invariant.

## Runtime Startup and Migration

All host modes share `RuntimeContext`, `ApplicationLifecycle`, and the ordered
bootstrap in `cyrene.runtime`:

```text
resolve paths → create runtime directories → migrate legacy database
→ initialize database/memory/learning → start managed services → serve UI
```

The active main database is `store/cyrene.runtime.database`. If the historical
`store/cyrene.db` exists and the new target is not populated, startup uses the
SQLite backup API, verifies the snapshot, writes an idempotent migration marker,
and retains the source for rollback. Ambiguous populated targets stop startup
instead of overwriting data.

## Key Features

### Personality System (SOUL.md)

Inject any personality via `workspace/SOUL.md` — a structured document with identity, beliefs, relationship dynamics, memory, and patterns. A **Steward Agent** runs on a configurable interval (default 30 minutes) to review conversations and update SOUL.md via `APPEND`/`ERASE`/`MERGE` commands. Temporary entries auto-expire after 24 hours. The chat filter translates all assistant output into the character's voice.

### Multi-Agent Orchestration

Invoke `subagent.spawn` through `subagent_tools` for parallel work. Each
sub-agent receives its own stable wire bundle; actor policy filters the
capabilities returned by module discovery and rejects main-only invocations.
Subagents communicate through the file-based inbox with
`subagent.send_message` or `subagent.broadcast`. Lifecycle states are
`running → waiting → resumed → done / timeout`.

### Three-Layer Memory

| Layer | Storage | Capacity | Maintained by |
|---|---|---|---|
| **Context Window** | `data/state.json` | ~`MAX_HISTORY_MESSAGES` (default 40) | Auto-trimmed |
| **Short-Term** | `data/short_term.json` | Compressed summaries | Background compressor |
| **Long-Term** | `workspace/SOUL.md` | Structured document | Steward Agent |

The short-term memory tracks emotional valence, mention count, and entry type (fact / pattern / preference / emotion). High-frequency entries (≥3 mentions) and extreme valence entries are preserved automatically.

### Knowledge Base

Documents dropped into the workspace are hashed, chunked, embedded, and stored
in a workspace-specific SQLite database. The `knowledge_tools` module exposes
project-document and literature-library capabilities such as
`knowledge.search` and `knowledge.library.search`. `AnalyzeAttachment`,
`WebSearch`, and `WebFetch` remain direct tools and are not part of this module.

### Entities

Structured project entities are managed through `entity_tools`
(`entity.track`, `entity.query`, `entity.update`, and `entity.delete`).

### Skills Installer

External prompt skills packaged as `.md` files, directories, or `.zip` archives can be installed at runtime. Installed skills are injected into the system prompt when enabled. The agent can also list and uninstall skills via dedicated tools.

### Behavior Learning (Patterns)

Each executed round is recorded as a short purpose plus its detailed tool
chain. Learned workflows are progressively disclosed through `skill_tools`;
low-risk declarative workflows can be invoked with `skill.run_learned`.

### Claude Code Bridge

When `tmux` and Claude Code are available, Cyrene can detect existing Claude Code sessions, start new ones, send prompts, and read their output through a terminal bridge (`cc_terminal.py`). This lets Cyrene delegate heavy coding sessions to Claude Code and pull the results back into chat.

### Code Tools

Codebase-aware implementations live under `cyrene/tool_impl/code/` and are
progressively exposed through `code_tools`:

- **Indexer** — builds a SQLite index of symbols, references, imports, and file hashes
- **Analysis** — query symbols, callers, references, and file summaries
- **Git tools** — inspect diffs, blame, log, branches, and status

### MCP Protocol Support

Cyrene connects to MCP servers over stdio or SSE. Connected schemas are
discovered on demand through `integration_tools`; they are no longer appended
to the fixed wire bundle. Manage servers via the Web UI or CLI.

### Task Scheduler

Create cron, interval, or one-shot tasks with `task.schedule` through
`task_tools`. Tasks persist in SQLite with execution history.

### Web UI

Cyrene ships with two web front-ends:

- **Workbench UI** (default) — project-centric dashboard with Projects, Schedule, Knowledge, Memory, Chat, Model settings, and Help.
- **Legacy Agent UI** — real-time chat with Markdown rendering, SSE event stream, Agent Flow SVG timeline, Sessions, Memory pipeline, Status, Settings, Evolution, Tasks, Knowledge, Entities, Map, Browser live view, and Claude Code terminal panel.

Both UIs bind to `127.0.0.1` and are served by the same FastAPI backend. The desktop/Electron build adds local-auth middleware backed by the OS keyring.

Desktop browser tools use Electron's embedded Chromium through a token-authenticated
loopback RPC bridge, and the visible `WebContentsView` shares the same persistent
profile as agent actions. Packaged desktop builds therefore exclude Playwright and
its separate Chromium download. Non-Electron Web UI/CLI runs retain Playwright as
an optional extra, with text-only `httpx` navigation as the final fallback.

### Search

Built-in search uses [SimpleXNG](https://github.com/jlevy/simplexng) — no Docker required. The manager auto-generates `data/simplexng_settings.yml`, auto-starts on port 8888, and handles proxy discovery. The deep research pipeline uses query generation → parallel search → filtering → synthesis.

### Context Debugger

Every LLM call is tagged with provenance metadata (`_ctx`) describing where each context block came from (system prompt, SOUL.md, short-term memory, history, tool results, etc.). With `--verbose`, these traces are written to `data/debug_*.jsonl` and exposed via `GET /api/context-debug/events`. The Context Debugger page lets you inspect exactly what context was sent to any call.

### CLI

Two CLI surfaces exist:

- **`cyrene <command>`** — a thin HTTP client that talks to the daemon at `localhost:4242` (`start`, `stop`, `do`, `session`, `flow`, `memory`, `status`, `mcp`).
- **`python -m cyrene.runtime.host`** — an interactive, headless REPL that runs the agent directly without starting a web server.

## Security & Local Auth

The raw web server binds only to `127.0.0.1` and has no authentication layer. The Electron/desktop build adds `LocalAuthMiddleware`, which stores a random token in the OS keyring and requires it on every request.

## Project Structure

```
src/
├── cyrene/                          # Core engine
│   ├── agent/                       # Agent loop and internal public API
│   ├── workbench/                   # Workbench business services
│   ├── model_runtime/               # Provider/model runtime (separate from legacy llm.py)
│   ├── learning/                    # Behavior and skill learning
│   ├── runtime/                     # Bootstrap, lifecycle, scheduling, persistence
│   ├── observability/               # Traces, debugging, and telemetry
│   ├── knowledge/                   # Document ingestion, embeddings, and storage
│   ├── channels/                    # Telegram and WeChat adapters
│   ├── tooling/                     # Stable tool control plane and backends
│   ├── tool_impl/                   # Native tool implementations by domain
│   ├── config.py                    # Environment configuration
│   ├── call_llm.py                  # Stable model-call facade
│   ├── browser.py                   # Browser session facade/runtime
│   ├── subagent.py                  # Subagent orchestration
│   ├── memory.py                    # Memory context assembly
│   ├── cli.py                       # `cyrene` daemon HTTP client
│   ├── tools.py                     # Public tooling facade
│   ├── __init__.py                  # Installs lazy legacy module aliases
│   ├── __main__.py                  # `python -m cyrene`
│   └── local_cli.py                 # Physical previous-release launcher shim
├── route/                           # All FastAPI HTTP/WebSocket adapters
│   ├── registry.py                  # Single route composition root
│   ├── schemas.py / errors.py       # Request contracts and API errors
│   ├── agent/                       # Chat, browser, sessions, collaboration
│   ├── workbench/                   # Projects, chats, tasks, knowledge, memory
│   ├── system/                      # Events, shell, updates, instance identity
│   ├── settings/ / code/ / maps/    # Domain-specific adapters
│   └── channels/                    # Channel-specific HTTP callbacks
├── webui/                           # FastAPI app lifecycle + React SPA hosting
│   ├── server.py                    # FastAPI app factory; installs route.registry
│   ├── workbench_*.py               # Background managers and UI support services
│   ├── auth.py                      # Local auth middleware
│   └── static/app/                  # JSX front-end components
├── workbench-webui/                 # Workbench UI front-end assets
├── tests/                           # Test suite
├── data/                            # Runtime state, debug logs, uploads
├── workspace/                       # SOUL.md, patterns, conversations
└── store/                           # SQLite databases
```

Historical imports such as `cyrene.db`, `cyrene.scheduler`, and
`cyrene.workbench_runtime` are resolved lazily by
`cyrene/runtime/module_compat.py` to the exact canonical module object; they do not
require duplicate top-level implementation files. `local_cli.py` is the sole
physical compatibility launcher because the previous desktop development
flow executes that exact file path.
