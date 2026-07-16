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
    │   └── quit → end interaction
    │
    ▼
Response returned to user
```

## Key Features

### Personality System (SOUL.md)

Inject any personality via `workspace/SOUL.md` — a structured document with identity, beliefs, relationship dynamics, memory, and patterns. A **Steward Agent** runs on a configurable interval (default 30 minutes) to review conversations and update SOUL.md via `APPEND`/`ERASE`/`MERGE` commands. Temporary entries auto-expire after 24 hours. The chat filter translates all assistant output into the character's voice.

### Multi-Agent Orchestration

Spawn sub-agents for parallel work. Each sub-agent has full tool access (except a small main-only blocklist) and communicates via a **file-based inbox** system. Lifecycle states: `running → waiting → resumed → done / timeout`. Sub-agents wait for siblings, process inbox messages, and coordinate results. The main agent collects and synthesizes outputs.

### Three-Layer Memory

| Layer | Storage | Capacity | Maintained by |
|---|---|---|---|
| **Context Window** | `data/state.json` | ~`MAX_HISTORY_MESSAGES` (default 40) | Auto-trimmed |
| **Short-Term** | `data/short_term.json` | Compressed summaries | Background compressor |
| **Long-Term** | `workspace/SOUL.md` | Structured document | Steward Agent |

The short-term memory tracks emotional valence, mention count, and entry type (fact / pattern / preference / emotion). High-frequency entries (≥3 mentions) and extreme valence entries are preserved automatically.

### Knowledge Base

Documents (PDF, text, images, code, maps) dropped into the workspace are hashed, chunked, embedded, and stored in a workspace-specific SQLite database (`store/kb_<workspace>.db`). The agent can search this corpus with `SearchKnowledge`. The knowledge store supports FTS and vector-style retrieval via the configured embedding endpoint.

### Entities

Structured project entities (people, tasks, concepts, etc.) can be tracked with `track_entity`, queried with `query_entities`, updated with `update_entity`, and deleted with `delete_entity`. Entities are stored in the main SQLite database and can be scoped to Workbench projects.

### Skills Installer

External prompt skills packaged as `.md` files, directories, or `.zip` archives can be installed at runtime. Installed skills are injected into the system prompt when enabled. The agent can also list and uninstall skills via dedicated tools.

### Behavior Learning (Patterns)

Each executed round is recorded as a short purpose plus its detailed agent/browser tool chain. A background learning agent compares the new purpose with the complete project purpose catalog and assigns it to an existing candidate or creates a new one. The first occurrence is observed, the second is offered to the user, and the third is learned automatically. Complex non-interactive workflows can be synthesized as approval-gated Python or shell implementations; low-risk declarative workflows remain executable through `RunLearnedSkill`. State lives in the behavior-learning database and generated script directory.

### Claude Code Bridge

When `tmux` and Claude Code are available, Cyrene can detect existing Claude Code sessions, start new ones, send prompts, and read their output through a terminal bridge (`cc_terminal.py`). This lets Cyrene delegate heavy coding sessions to Claude Code and pull the results back into chat.

### Code Tools

A set of codebase-aware tools is provided under `cyrene/code_tools/`:

- **Indexer** — builds a SQLite index of symbols, references, imports, and file hashes
- **Analysis** — query symbols, callers, references, and file summaries
- **Git tools** — inspect diffs, blame, log, branches, and status

### MCP Protocol Support

Cyrene connects to any MCP (Model Context Protocol) server — both stdio (subprocess) and SSE (HTTP) transports. Connected MCP servers expose their tools alongside built-in tools. Manage servers via the Web UI (Settings → MCP Servers) or CLI (`cyrene mcp add/list/remove/toggle`).

### Task Scheduler

Create cron, interval, or one-shot tasks via the `schedule_task` tool. A heartbeat runs every `HEARTBEAT_INTERVAL` seconds (default 300) to execute due tasks. Tasks persist in SQLite with execution history. A **lottery system** allows the agent to send proactive messages to the user based on probability accumulation.

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
│   ├── tool_impl/                   # One file per native tool
│   ├── code_tools/                  # Codebase indexing, analysis, git helpers
│   ├── knowledge/                   # Document ingestion, embeddings, store
│   ├── channels/                    # Telegram and WeChat bots
│   ├── modules/                     # Deep research and other pipelines
│   ├── registry_tools.py            # Central native-tool registry
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
