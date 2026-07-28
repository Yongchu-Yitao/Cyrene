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

`workspace/SOUL.md` is the single global personality and durable-memory
document. A **Steward Agent** reviews new conversation material and can update
it through `APPEND`/`ERASE`/`MERGE` commands. The configured interval defaults
to one hour and is clamped to a one-hour minimum. Dated `TEMPORARY` entries
older than 24 hours are filtered out when memory context is assembled; the
source document is not silently rewritten merely because an entry expires.

### Multi-Agent Orchestration

Invoke `subagent.spawn` through `subagent_tools` for parallel work. Each
sub-agent receives its own stable wire bundle; actor policy filters the
capabilities returned by module discovery and rejects main-only invocations.
Subagents communicate through the file-based inbox with
`subagent.send_message` or `subagent.broadcast`. Lifecycle states are
`running → waiting → resumed → done / timeout`.

### Memory Layers

| Layer | Storage | Capacity | Maintained by |
|---|---|---|---|
| **Conversation context** | `data/state.json` for the historical default session; `data/sessions/<session>/state.json` for named sessions | Bounded/compacted for model input | Agent session runtime |
| **Project memory** | Workbench document store, keyed by project memory key | Project-scoped captured facts and summaries | Workbench memory service |
| **Historical short-term memory** | `data/short_term.json` | Default-session compatibility summaries | Compressor / Steward |
| **Long-term identity** | `workspace/SOUL.md` | One global structured document | Steward Agent |

The short-term memory tracks emotional valence, mention count, and entry type (fact / pattern / preference / emotion). High-frequency entries (≥3 mentions) and extreme valence entries are preserved automatically.

### Knowledge Base

Documents imported through Workbench, chat attachments, generated exports, and
Zotero attachment import are hashed and stored in a project-specific SQLite
database. Extractable content is chunked; embeddings are added only when an
embedding provider is configured, otherwise lexical/FTS retrieval remains
available. Merely placing an arbitrary file in a project workspace does not
automatically ingest it. The `knowledge_tools` module exposes project-document
and literature-library capabilities. `AnalyzeAttachment`, `WebSearch`, and
`WebFetch` remain direct tools.

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

Cyrene ships one Workbench front-end. Its primary areas are Task, Chat,
Knowledge/Library, Schedule, and Memory. Search, Browser/PDF/Diff views,
settings, onboarding, help, profile, and Quick Chat are overlays, panels, or
secondary surfaces rather than separate legacy pages. The source lives under
`src/webui/frontend`; the only generated web output root is
`src/webui/static/app`.

`WorkbenchTopbar` keeps two deliberately separate collections: a local
MRU/pinned list of at most three task/chat session tabs, and a persistent
Pinned Resource Shelf. Resource drag payloads use the internal
`application/x-cyrene-work-resource+json` MIME; native macOS text drags are
accepted as `text/plain` and materialized as Markdown. Knowledge attachments
are resolved server-side so renderer payloads do not expose absolute paths.

The pinned-resource registry is stored through the Workbench document store.
Only compact file/browser indexes enter subsequent Agent context. File content
is read on demand. Browser references carry an owner session: the owner retains
normal control, while tool execution restricts other sessions to snapshot and
screenshot operations. This is an execution-layer policy, not only a prompt
instruction.

Dropping a Browser on another conversation does not transfer the original
Browser reference or elevate pinned-resource permissions. The renderer calls
Electron `browser:create-tab` for the target session's `BrowserTabManager`, then
synchronizes that conversation's PiP state through an event. Managers remain
session-isolated; only the persistent cookie/login partition is shared.

The Web UI binds to `127.0.0.1` and is served by the FastAPI backend. Electron
generates a shared token for each launch, passes it to the Python child, and
injects it as `X-Cyrene-Token` on desktop requests. The OS keyring is used for
the Fernet key protecting `data/config.enc`, not for the per-launch HTTP token.

Desktop browser tools use Electron's embedded Chromium through a token-authenticated
loopback RPC bridge, and the visible `WebContentsView` shares the same persistent
profile as agent actions. Packaged desktop builds therefore exclude Playwright and
its separate Chromium download. Non-Electron Web UI/CLI runs retain Playwright as
an optional extra, with text-only `httpx` navigation as the final fallback.

### Search

Built-in search uses [SimpleXNG](https://github.com/jlevy/simplexng) — no Docker required. The manager auto-generates `data/simplexng_settings.yml`, auto-starts on port 8888, and handles proxy discovery. The deep research pipeline uses query generation → parallel search → filtering → synthesis.

### Context tracing

Every LLM call is tagged with provenance metadata (`_ctx`) describing where
each context block came from (system prompt, SOUL.md, short-term memory,
history, tool results, and so on). With `--verbose`, these traces are written
to `data/debug_*.jsonl` and exposed through
`GET /api/context-debug/events`, `cyrene flow`, and the canonical
`cyrene.observability.context_debug` module. Context tracing intentionally has
no Workbench page.

### CLI

Two CLI surfaces exist:

- **`cyrene <command>`** — a thin HTTP client that talks to the daemon at `localhost:4242` (`start`, `stop`, `do`, `session`, `flow`, `memory`, `status`, `mcp`).
- **`python -m cyrene.runtime.host`** — an interactive, headless REPL that runs the agent directly without starting a web server.

## Security & Local Auth

The raw web server binds only to `127.0.0.1`, validates local Host/Origin
headers, and has no user-login layer. The Electron build adds
`LocalAuthMiddleware`, which requires its generated per-launch token on every
desktop request. Configuration secrets live in `data/config.enc`; its Fernet
key is stored in the OS keyring when available and falls back to a mode-0600
local key file with a warning when it is not.

These are application controls, not an OS sandbox or multi-tenant boundary.
Project stores and permission modes do not isolate mutually untrusted users.
Only the configuration blob is application-encrypted; workspace files,
databases, logs/traces, exports, and backups rely on operating-system storage
protection. Portable backup ZIPs include a logical configuration snapshot for
cross-install restore and can contain credentials.

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
│   ├── frontend/                    # Sole React/JSX source root
│   │   ├── platform/                # Bootstrap, API, SSE, data, readiness
│   │   └── shared/                  # Shared UI capabilities
│   └── static/app/                  # Sole generated/bundled output root
tests/                               # Test suite
data/                                # Source-run state, debug logs, uploads
workspace/                           # Source-run SOUL.md and user workspace
store/                               # Source-run SQLite databases
```

Historical imports such as `cyrene.db`, `cyrene.scheduler`, and
`cyrene.workbench_runtime` are resolved lazily by
`cyrene/runtime/module_compat.py` to the exact canonical module object; they do not
require duplicate top-level implementation files. `local_cli.py` is the sole
physical compatibility launcher because the previous desktop development
flow executes that exact file path.
