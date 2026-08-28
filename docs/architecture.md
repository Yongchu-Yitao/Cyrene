# Architecture

[English](architecture.md) · [简体中文](architecture.zh-CN.md)

## Plugin-Native Agent Runtime

Cyrene now uses one continuous Agent runtime assembled from plugins. There is
no routing phase followed by a separate execution phase: a user message enters
one durable run, and that run continues through model output, tool calls,
questions, cancellation, recovery, context compaction, and final delivery.

```text
User message
    │
    ▼
Build the context tree from enabled context plugins
    │
    ▼
Continuous Agent run
    ├── reply or ask the user
    ├── use a directly visible tool
    ├── toolbox.list → describe → invoke
    ├── spawn or communicate with subagents
    └── finish, cancel, or recover
    │
    ▼
Persist and publish the final result
```

Only the kernel tools required to operate the runtime are fixed. Everything
else—including tools, toolboxes, context mounts, background jobs, application
services, routes, UI contributions, channels, schedules, proactive work,
knowledge, and SOUL.md—is supplied by a plugin. Toolboxes and standalone tools
share the same `toolbox.list → describe → invoke` discovery path; selected
tools can also be made directly visible to the model. Runtime validation checks
arguments against the active plugin schema while accepting semantically
equivalent object-field ordering.

Enabled context plugins publish blocks into a traceable context tree. Stable
blocks keep stable identities for prompt-cache reuse, while the standard
compactor bounds long conversations without changing their durable history.
The required system-prompt plugin mounts the editable base Agent instructions
first; the kernel creates only an empty system root. The composer-context plugin
owns the chat input selections for workspaces, MCP servers, skills, and other
context capabilities. The SOUL plugin mounts the enabled personality block
immediately below the system prompt. Subagents start with the same initial tree
as the main Agent plus the main Agent's assignment, then coordinate through the
durable inbox.

### How plugins become one Agent

Composition happens at three scopes. Application composition loads enabled
packs and lets them contribute routes, services, background jobs, channels,
settings panels, and Workbench views. Session composition attaches the same
packs to one ContextTree, publishes session services, and binds durable Hooks.
Run composition invokes those Hooks with the current conversation selections
and produces the exact model input and capability set for one turn.

| Composition layer | Supplied by | What the Agent receives |
|---|---|---|
| Kernel | Agent runtime | Empty ContextTree root, recovery/cancellation machinery, and the fixed `Bash`, `Read`, `Write`, and `toolbox` tools |
| Base instructions | `cyrene_system_prompt` | Editable system prompt mounted at the `system` position; a missing or empty contribution fails closed |
| Personality | `cyrene_soul` | Optional SOUL.md block mounted at `top`, directly below the system prompt |
| Conversation choices | `cyrene_composer_context` | The workspaces, MCP servers, skills, and other capabilities selected in the input-box context menu |
| Runtime and memory | Context, memory, entity, and feature plugins | Turn metadata, project memory, relevant entities, attachments, and feature-specific context blocks |
| Inference | Model Provider plugins | Model catalog, selected profile, completion stream, usage, and model identity |
| Capabilities | Tool packs and standalone tool plugins | Direct schemas for tools marked visible; metadata and schemas discoverable through `toolbox` for everything else |
| Behavior around work | Tree-local Hooks | Permission decisions, argument normalization, learning capture, context accounting, finalization, and cancellation |

Plugin Center controls global availability. The composer-context plugin owns
per-conversation selections. Tool visibility is a third, independent choice:
**directly visible** places the current schema in the model's immediate tool
list, while **Agent finds and uses** keeps it behind toolbox discovery. Both
paths resolve the same active Plugin object and pass the same schema validation
and Hook review; there is no duplicate tool implementation.

The ContextTree is the composition record, not only a transcript. It persists
the system root, ordered context mounts and their sources, user and assistant
nodes, tool calls and results, model identity and usage, compaction nodes,
subagent state, and recovery checkpoints. The Conversation Context panel and
CLI `/context` view project this same tree, so the displayed composition is the
composition sent to the model.

### Hook lifecycle

Hooks belong to a tree and retain their Plugin binding across recovery. A pack
binds implementations when the session opens; restored trees rebind the same
Plugin IDs before pending work resumes.

| Hook | Role in the composed Agent |
|---|---|
| `SessionStart` | Builds ordered context mounts for a run. `system` comes first, `top` follows, and ordinary contributions retain deterministic registration order. |
| `ContextChange` | Reacts to committed tree changes and advances context-dependent session work without polling a separate state store. |
| `ContextUsed` | Receives the token contribution and usage ratio of the actual model path for memory and compaction accounting. |
| `PreToolUse` | Reviews a resolved call, may normalize its arguments, allow it, or block it. The fixed permission reviewer sees the final arguments. |
| `PostToolUse` | Observes the completed result once and lets plugins persist learning, activity, or integration state. |
| `SessionEnd` | Finalizes plugin-owned work after the run has a durable result. |
| `Stop` | Cancels or closes plugin-owned work when the user stops a run or the session shuts down. |

Context contributions are ordinary Plugin output, not string concatenation in
the model router. A contribution has a stable tree identity, mount position,
source, and failure policy. Required providers such as the system prompt and
composer context fail closed; optional, transient runtime context may fail open.
Stable identities preserve reusable prompt prefixes, while replacing a
selection creates the next explicit mount rather than silently mutating history.

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

The `cyrene_subagent` Plugin pack provides spawn, direct-message, broadcast, and
round-query tools through `toolbox.list → describe → invoke`; selected tools
may also be made directly visible. Each subagent starts with the main Agent's
initial ContextTree composition plus the main Agent's assignment, then owns an
independent branch and model loop. Actor policy removes main-only capabilities,
and the durable inbox carries messages in both directions. Lifecycle states are
`running → waiting → resumed → done / timeout`.

### Memory Layers

| Layer | Storage | Capacity | Maintained by |
|---|---|---|---|
| **Conversation context** | Per-tree SQLite stores under `data/context/` plus its tree index | Durable history; bounded/compacted projection for model input | Agent ContextTree runtime |
| **Project memory** | Workbench document store, keyed by project memory key | Project-scoped captured facts and summaries | Workbench memory service |
| **Short-term memory** | `data/plugin_data/cyrene_memory/short_term.json` | Cross-session summaries with expiry and retirement state | `cyrene_memory` Plugin |
| **Long-term identity** | `workspace/SOUL.md` | One global structured document | `cyrene_soul` Plugin / Steward Agent |

The short-term memory tracks emotional valence, mention count, and entry type (fact / pattern / preference / emotion). High-frequency entries (≥3 mentions) and extreme valence entries are preserved automatically.

### Knowledge Base

The editable `cyrene_knowledge` Plugin owns the complete knowledge backend:
SQLite schema, managed attachment files, extraction, chunking, local vectors,
hybrid retrieval, Zotero synchronization, Workbench HTTP routes, global search,
and Agent tools. Its data lives under
`data/plugin_data/cyrene_knowledge/`; every row is keyed by Workbench project.
Chat attachments, generated exports, and completed task artifacts enter through
the same service. The old `cyrene.knowledge`/Workbench-library route stack is
not part of the active request path. Agent access uses
`toolbox.list → describe → invoke`.

### Entities

Structured project entities are managed exclusively by the editable
`cyrene_entity` Plugin pack (`entity.track`, `entity.query`, `entity.update`,
and `entity.delete`) through `toolbox.list → describe → invoke`.

### Skills Installer

External prompt skills packaged as `.md` files, directories, or `.zip` archives can be installed at runtime. Installed skills are injected into the system prompt when enabled. The agent can also list and uninstall skills via dedicated tools.

### Behavior Learning (Patterns)

Each executed round is recorded as a short purpose plus its detailed tool
chain. Learned workflows are progressively disclosed through `skill_tools`;
low-risk declarative workflows can be invoked with `skill.run_learned`.

### Terminal Daemon

An independent local daemon owns interactive PTYs, persistent metadata,
scrollback, rendered VT screens, and durable exit wakes. Electron and the Web
backend attach as clients, so closing a view never terminates a terminal.

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

The editable `cyrene_schedule` Plugin pack owns scheduled-task behavior. Agents
discover `schedule.create`, `schedule.list`, `schedule.edit`, `schedule.pause`,
`schedule.resume`, `schedule.cancel`, and `schedule.runs` through
`toolbox.list → describe → invoke`. Its hidden `schedule.tick` Plugin declares a
background job in metadata; the generic Plugin background host supplies the
clock and invokes the current user-edited implementation.

Tasks and run history are durable in SQLite. Lease-based claims, stable run IDs,
and revision-checked finalization prevent duplicate execution and stale runs from
undoing a concurrent pause or edit. Agent actions execute through the Workbench
Chat runtime and project one result back into Workbench. The pack's
`application_setup` contribution owns its Workbench routes, global-search
provider, and Schedule module activation, so removing or breaking the pack does
not leave a second built-in schedule backend active.

### Cyrene self-management control plane

`cyrene_tools` is a main-agent-only progressive package. The model-facing wire
contains one stable gateway; concrete schemas are disclosed through
`discover → describe → invoke`. Its public capabilities are app status/window,
current-surface snapshot/inspect/click/double-click/type/scroll/drag, and typed settings
describe/read/update.

UI control is bound to the Electron renderer that originated the current local
turn. A snapshot exposes only the active layer and current viewport; inspect
reads one component and its paginated subtree. Mutations bind the exact
`snapshot_id`, revision, node and action, and never accept selectors, scripts,
raw events, or arbitrary coordinates. Explicit semantic nodes and the bounded
DOM projection are de-duplicated. Transcript text, streaming output, and
message-control re-renders remain readable but do not advance the actionable
revision; approvals, questions, layers, and action-set changes still do.
If an unrelated global revision nevertheless advances, a bounded renderer-side
action lease lets an earlier snapshot act only when that node's action, risk,
scope, and safety-relevant state are unchanged. The agent must still pass the
original revision verbatim.
Double-click is a separate gesture capability: it accepts only an `invoke`
action that explicitly advertises `double_press` or `double_click`. For example,
the Browser PiP titlebar advertises `maximize + double_press`, so the agent can
maximize it through the registered renderer handler without window focus or
screen coordinates. Ordinary single-click actions are rejected by this tool.
Composer send is an explicit R2 action while
interrupting the current run is R1.

Project, chat, backup, update, lifecycle, and cross-session message handlers are
internal services and are blocked from every agent catalog. The only persistent
backend mutation exposed to the agent is the typed, revisioned non-model
settings service. R2/R3 delegation is reviewed against the exact real local-user
turn and, for a batch, consumed in argument-bound order.
The model may provide an exact quote; if it omits one, the same permission
reviewer evaluates the full current desktop-local request. A missing client
request ID does not invalidate a trusted session/round identity. Permission
cards render structured metadata through the current UI language, normalize
risk-qualified operation IDs to localized capability names, and hide internal
correlation fingerprints.

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
│   └── local_cli.py                 # Legacy direct-file compatibility shim
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
require duplicate top-level implementation files. `local_cli.py` remains a
legacy direct-file compatibility shim; current source and Electron development
launches use the `cyrene` project entry point.
