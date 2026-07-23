# Architecture

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
- **`python -m cyrene.local_cli`** — an interactive, headless REPL that runs the agent directly without starting a web server.

## Security & Local Auth

The raw web server binds only to `127.0.0.1` and has no authentication layer. The Electron/desktop build adds `LocalAuthMiddleware`, which stores a random token in the OS keyring and requires it on every request.

## Project Structure

```
src/
├── cyrene/                          # Core engine
│   ├── agent/                       # Two-phase loop, sessions, rounds, planning
│   │   ├── agent.py                 # Main agent orchestration
│   │   ├── coordinator.py           # Phase routing and context assembly
│   │   ├── session.py               # Session persistence and state
│   │   ├── round.py                 # Tool round lifecycle
│   │   ├── state.py                 # In-memory session state
│   │   ├── prompts.py               # System and phase prompts
│   │   ├── deep_reflection.py       # Deep Reflection capability
│   │   ├── commands.py              # Slash-command parsing
│   │   └── ...
│   ├── tooling/                     # Stable tool control plane
│   │   ├── types.py                 # ToolSpec, snapshots, execution context
│   │   ├── catalog.py               # Native + MCP capability catalog
│   │   ├── snapshot.py              # Run-fixed capability snapshots
│   │   ├── wire.py                  # Deterministic main/subagent bundles
│   │   ├── packs.py                 # 12 declarative capability modules
│   │   ├── gateway.py               # discover / describe / invoke router
│   │   ├── executor.py              # Concrete handler execution
│   │   ├── validation.py            # Gateway argument validation
│   │   ├── results.py               # Stable result/error protocol
│   │   ├── policy/                  # Actor, path, shell, approval policy
│   │   └── adapters/                # MCP and learned-skill adapters
│   ├── tool_impl/                   # Native implementations by domain
│   │   ├── control/                 # ask/quit/plan/reflection
│   │   ├── core/                    # file, Bash, web, attachment
│   │   ├── code/                    # code analysis, Git, shells, Claude Code
│   │   ├── browser/                 # persistent browser operations
│   │   ├── desktop/                 # App Use
│   │   ├── memory/                  # short-term/conversation/project memory
│   │   ├── knowledge/               # documents and literature library
│   │   ├── task/                    # scheduled tasks and task plans
│   │   ├── entity/                  # durable entity tracking
│   │   ├── map/                     # pins and routes
│   │   ├── subagent/                # spawn/query/communication
│   │   ├── delivery/                # progress, messages, files
│   │   └── skills/                  # installed and learned skills
│   ├── knowledge/                   # Document ingestion, embeddings, store
│   ├── channels/                    # Telegram and WeChat bots
│   ├── modules/                     # Deep research and other pipelines
│   ├── tools.py                     # Thin public tooling facade
│   ├── mcp_manager.py               # MCP server lifecycle
│   ├── search.py                    # Deep search pipeline
│   ├── searxng_manager.py           # SimpleXNG subprocess lifecycle
│   ├── scheduler.py                 # Heartbeat, cron, lottery, steward
│   ├── soul.py                      # SOUL.md read/write
│   ├── short_term.py                # Short-term memory compression
│   ├── memory.py                    # Memory context assembly
│   ├── shells.py                    # Persistent shell sessions
│   ├── browser.py                   # Persistent browser context / screencast
│   ├── cc_bridge.py / cc_terminal.py # Claude Code integration
│   ├── behavior_learning.py         # Purpose/tool-chain skill learning
│   ├── skills_registry.py           # Installed skill storage
│   ├── context_trace.py             # Context provenance tagging
│   ├── context_debug.py             # Verbose log inspector
│   ├── config.py                    # Environment config
│   ├── settings_store.py            # Runtime settings persistence
│   ├── setup.py / onboarding.py     # Personality setup / onboarding wizard
│   ├── cli.py                       # CLI HTTP client
│   ├── local_cli.py                 # Interactive local CLI + web entry points
│   └── __main__.py                  # Default entry (Telegram / workbench flags)
├── webui/                           # FastAPI + React SPA backend
│   ├── server.py                    # FastAPI app factory
│   ├── routes.py                    # REST API + SSE streams
│   ├── routes_*.py                  # Knowledge, entities, workbench, code, map
│   ├── auth.py                      # Local auth middleware
│   └── static/app/                  # JSX front-end components
├── workbench-webui/                 # Workbench UI front-end assets
├── tests/                           # Test suite
├── data/                            # Runtime state, debug logs, uploads
├── workspace/                       # SOUL.md, patterns, conversations
└── store/                           # SQLite databases
```
