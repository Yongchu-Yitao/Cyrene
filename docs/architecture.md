# Architecture

## Two-Phase Agent Loop

Cyrene uses a two-phase decision loop to minimize LLM calls for simple chat while enabling full tool use when needed. The maximum number of tool rounds is configurable (default 15).

```
User Message
    │
    ▼
Phase 1 (lightweight: only use_tools + quit)
    ├── Pure chat → return directly (1 LLM call)
    └── Needs tools → Phase 2
            │
            ▼
    Phase 2 (full tool set)
    │   ├── File ops: Read / Write / Edit / Glob / Grep
    │   ├── Shell: Bash + persistent shells
    │   ├── Search: WebSearch / WebFetch (SimpleXNG built-in)
    │   ├── Knowledge: SearchKnowledge
    │   ├── Entities: track / query / update structured entities
    │   ├── Browser: navigate / click / type / screenshot / takeover
    │   ├── Code tools: index / query codebase, git helpers
    │   ├── Claude Code bridge: check / start / prompt Claude Code sessions
    │   ├── Subagents: spawn_subagent → parallel agents
    │   ├── MCP tools: from connected MCP servers
    │   ├── Tasks: schedule / list / pause / resume / cancel
    │   ├── Skills: install / list / uninstall prompt skills
    │   ├── App Use: control macOS/Windows desktop apps natively
    │   ├── Notifications: desktop and webhook alerts
    │   └── quit → end interaction
    │
    ▼
Response returned to user
```

## Key Features

### Personality System (SOUL.md)

Inject any personality via `workspace/SOUL.md` — a structured document with identity, beliefs, relationship dynamics, memory, and patterns. A **Steward Agent** runs on a configurable interval (default 30 minutes) to review conversations and update SOUL.md via `APPEND`/`ERASE`/`MERGE` commands. Temporary entries auto-expire after 24 hours. The onboarding wizard helps set up personality on first launch.

### Multi-Agent Orchestration

Spawn sub-agents for parallel work. Each sub-agent has full tool access (except a small main-only blocklist) and communicates via a **file-based inbox** system. Lifecycle states: `running → waiting → resumed → done / timeout`. Sub-agents wait for siblings, process inbox messages, and coordinate results. The main agent collects and synthesizes outputs.

### Guidance & Inbox

While a round is executing, the user can send **guidance** messages that interrupt the agent mid-execution. Guidance is persisted to SQLite with deduplication, survives restarts, and is processed ahead of pending tool results. The inbox system also manages inter-agent messages, runtime events, and pending questions.

### Three-Layer Memory

| Layer | Storage | Capacity | Maintained by |
|---|---|---|---|
| **Context Window** | `data/state.json` | ~`MAX_HISTORY_MESSAGES` (default 40) | Auto-trimmed |
| **Short-Term** | `data/short_term.json` | Compressed summaries | Background compressor |
| **Long-Term** | `workspace/SOUL.md` | Structured document | Steward Agent (~30min) |

The short-term memory tracks emotional valence, mention count, and entry type (fact / pattern / preference / emotion). High-frequency entries (≥3 mentions) and extreme valence entries are preserved automatically. Stale entries can be explicitly **retired** by ID.

### Knowledge Base

Documents (PDF, text, images, code) dropped into the workspace are hashed, chunked, embedded, and stored in a workspace-specific SQLite database (`store/kb_<workspace>.db`). The agent can search this corpus with `SearchKnowledge`. If no embedding endpoint is configured, it falls back to FTS/text search. Images are indexed via a vision model for multimodal retrieval.

### Entities

Structured project entities (people, tasks, concepts, etc.) can be tracked with `track_entity`, queried with `query_entities`, updated with `update_entity`, and deleted with `delete_entity`. Entities are stored in the main SQLite database and support per-Workbench-project scoping.

### Skills Installer

External prompt skills packaged as `.md` files, directories, or `.zip` archives can be installed at runtime. Installed skills are injected into the system prompt when enabled. The agent can list and uninstall skills via dedicated tools.

### Behavior Learning (Patterns)

Each executed round is recorded as a short purpose plus its detailed tool chain. A background learning agent compares the new purpose with the complete project purpose catalog and assigns it to an existing candidate or creates a new one. The first occurrence is observed, the second is offered to the user for approval, and the third is learned automatically. Complex non-interactive workflows can be synthesized as approval-gated Python or shell implementations.

### MCP Protocol Support

Cyrene connects to any MCP (Model Context Protocol) server — both stdio (subprocess) and SSE (HTTP) transports. Connected MCP servers expose their tools alongside built-in tools. Manage servers via the Web UI (Settings → MCP Servers) or CLI (`cyrene mcp add/list/remove/toggle`).

### Task Scheduler

Create cron, interval, or one-shot tasks via the `schedule_task` tool. A heartbeat runs every `HEARTBEAT_INTERVAL` seconds (default 300) to execute due tasks. Tasks persist in SQLite with execution history. A **lottery system** allows the agent to send proactive messages to the user based on probability accumulation. The steward agent also runs on a configurable schedule.

### Desktop App Use

Cyrene can control native macOS and Windows desktop applications through a unified `app_use` tool gateway (list targets → connect → call). It can discover application windows, read their UI structure (accessibility tree), click, type, swipe, scroll, and dispatch keyboard shortcuts — all without taking over the user's foreground focus. macOS uses JXA/Quartz events; Windows uses PowerShell/UIA.

### Browser Live View

The Electron desktop app drives its embedded Chromium browser directly (native `WebContentsView` tabs, persistent profile, CDP-based control). Non-Electron runs can use Playwright for the same automation and live screencasting. Browser tools include navigation, clicks (by selector, ref, text, or coordinates), typing, snapshots, screenshots, scrolling, network logging, tab management, file uploads, and headed login takeover.

### Claude Code Bridge

When `tmux` and Claude Code are available, Cyrene can detect existing Claude Code sessions, start new ones, send prompts, and read their output through a terminal bridge. A learning pipeline (`cc_learner.py`) analyzes Claude Code transcripts to extract reusable patterns and tool usage.

### Code Tools

A set of codebase-aware tools under `cyrene/code_tools/`:
- **Indexer** — builds a SQLite index of symbols, references, imports, and file hashes
- **Analysis** — query symbols, callers, references, and file summaries (`SearchSymbol`, `FindReferences`, `GetFileSymbols`, `CodeReview`)
- **Git tools** — inspect diffs, blame, log, branches, and status (`GitStatus`, `GitDiff`, `GitLog`, `GitCommit`, `GitBranch`)

### Context Debugger

Every LLM call is tagged with provenance metadata (`_ctx`) describing where each context block came from (system prompt, SOUL.md, short-term memory, history, tool results, etc.). With `--verbose`, these traces are written to `data/debug_*.jsonl` and exposed via `GET /api/context-debug/events`. The Context Debugger page in the Web UI lets you inspect exactly what context was sent to any call.

### Web UI

Cyrene ships with two web front-ends:

- **Workbench UI** (default) — project-centric dashboard with Projects, Schedule, Knowledge, Memory, Chat, Model settings, and Help. Each project has its own scope.
- **Legacy Agent UI** — real-time chat with Markdown rendering, SSE event stream, Agent Flow SVG timeline, Sessions, Memory pipeline, Status, Settings, Evolution, Tasks, Knowledge, Entities, Map, Browser live view, and Claude Code terminal panel.

Both UIs bind to `127.0.0.1` and are served by the same FastAPI backend. The desktop/Electron build adds local-auth middleware backed by the OS keyring.

### Search

Built-in search uses [SimpleXNG](https://github.com/jlevy/simplexng) — no Docker required. The manager auto-generates `data/simplexng_settings.yml`, auto-starts on port 8888, and handles proxy discovery. The deep research pipeline uses query generation → parallel search → filtering → synthesis.

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
│   │   ├── agent.py                 # Main agent orchestration (_run_main_agent)
│   │   ├── coordinator.py           # Phase routing and context assembly
│   │   ├── session.py               # Session persistence and state
│   │   ├── round.py                 # Tool round lifecycle
│   │   ├── state.py                 # In-memory session state (globals)
│   │   ├── prompts.py               # System and phase prompts
│   │   ├── deep_reflection.py       # Deep Reflection capability
│   │   ├── guidance.py              # Mid-round guidance, inbox processing
│   │   ├── message.py               # Message assembly and deduplication
│   │   └── commands.py              # Slash-command parsing
│   ├── tool_impl/                   # One file per native tool (50+ tools)
│   ├── code_tools/                  # Codebase indexing, analysis, git helpers
│   ├── knowledge/                   # Document ingestion, embeddings, store
│   ├── channels/                    # Telegram and WeChat bots
│   ├── modules/                     # Deep research pipeline
│   ├── registry_tools.py            # Central native-tool registry
│   ├── mcp_manager.py               # MCP server lifecycle
│   ├── search.py                    # Deep search pipeline
│   ├── searxng_manager.py           # SimpleXNG subprocess lifecycle
│   ├── scheduler.py                 # Heartbeat, cron, lottery, steward
│   ├── soul.py                      # SOUL.md read/write
│   ├── short_term.py                # Short-term memory compression
│   ├── memory.py                    # Memory context assembly
│   ├── shells.py                    # Persistent shell sessions
│   ├── inbox.py                     # File-based inter-agent messaging
│   ├── subagent.py                  # Sub-agent lifecycle
│   ├── browser.py                   # Persistent browser context / screencast
│   ├── app_use.py                   # Desktop app control (macOS/Windows)
│   ├── cc_bridge.py / cc_terminal.py # Claude Code integration
│   ├── cc_learner.py                # Claude Code transcript analysis
│   ├── behavior_learning.py         # Purpose/tool-chain skill learning
│   ├── skills_registry.py           # Installed skill storage
│   ├── context_trace.py             # Context provenance tagging
│   ├── context_debug.py             # Verbose log inspector
│   ├── backup.py                    # Backup/restore with verification
│   ├── config.py                    # Environment config (from encrypted store)
│   ├── settings_store.py            # Runtime settings persistence
│   ├── config_store.py              # Encrypted config store (Fernet)
│   ├── setup.py / onboarding.py     # Personality setup / onboarding wizard
│   ├── cli.py                       # CLI HTTP client
│   ├── local_cli.py                 # Interactive local CLI + web entry points
│   ├── map_pin_tool.py              # Map pin/route tracking
│   ├── call_llm.py                  # LLM API call helpers
│   ├── db.py                        # SQLite database
│   ├── debug.py                     # JSONL logging + SSE event bus
│   ├── entities.py                  # Structured entity storage
│   ├── versions.py                  # Version helpers
│   ├── notifications.py             # Desktop/webhook notifications
│   ├── tool_legacy.py               # Legacy tool definitions
│   └── __main__.py                  # Default entry (Telegram / headless mode)
├── webui/                           # FastAPI + React SPA backend
│   ├── server.py                    # FastAPI app factory
│   ├── routes.py                    # REST API + SSE streams
│   ├── routes_*.py                  # Knowledge, entities, code, map routes
│   ├── auth.py                      # Local auth middleware
│   └── static/app/                  # JSX front-end components
├── workbench-webui/                 # Workbench UI front-end assets
├── tests/                           # Test suite
├── data/                            # Runtime state, debug logs, uploads
├── workspace/                       # SOUL.md, patterns, conversations
└── store/                           # SQLite databases
```
