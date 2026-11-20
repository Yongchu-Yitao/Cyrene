# Architecture

[English](architecture.md) · [简体中文](architecture.zh-CN.md)

## Plugin-Native Agent Runtime

Cyrene assembles each agent from plugins. A user message starts a durable run in which the agent calls the model, uses tools, asks questions, and delivers the result. The runtime handles cancellation, recovery, and context compaction within that run.

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
and hook review.

The context tree records each turn and the inputs assembled for the model. It persists
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
| `SessionStart` | Runs once per conversation and freezes the ordered, cache-stable context prefix. |
| `TurnStart` | Builds the current turn's dynamic context suffix and freezes it for retries. |
| `ContextChange` | Reacts to committed tree changes and advances context-dependent session work without polling a separate state store. |
| `ContextUsed` | Receives the token contribution and usage ratio of the actual model path for memory and compaction accounting. |
| `PreToolUse` | Runs deterministic argument guards for every resolved call. A Plugin may declare a `permission_boundary`, or report a dynamic boundary after resolving its real target through the session permission service. Only an actual boundary reaches the fixed permission reviewer, which sees the final arguments and may allow, block, or pause for an exact user confirmation. |
| `PostToolUse` | Observes the completed result once and lets plugins persist learning, activity, or integration state. |
| `SessionEnd` | Finalizes plugin-owned work after the run has a durable result. |
| `Stop` | Cancels or closes plugin-owned work when the user stops a run or the session shuts down. |

Plugins provide context to the model. For each contribution, Cyrene records a stable ID in the context tree, its source, where it belongs, and how to handle a loading error. The system instructions and the context selected in the composer must be ready before Cyrene calls the model. A turn can proceed if an optional piece of runtime context is unavailable.

At the start of a conversation, Cyrene puts the content from `SessionStart` hooks in the first system message. At the start of each turn, it appends the content from `TurnStart` hooks to the current user message. For example, when you switch workspaces in the composer, the new selection goes to the model with your next message. Earlier messages stay as they were.

Cyrene reuses the exact bytes of the opening system message so the model provider can reuse its prompt cache. It tracks the arrangement of session-start hooks, the versions of contributing plugins, SOUL state and content, the memory snapshot, and learned skill versions. When a dependency changes, Cyrene rebuilds the opening message once and reuses it on subsequent turns.

## Runtime Startup and Migration

All host modes share `RuntimeContext`, `ApplicationLifecycle`, and the ordered
bootstrap in `cyrene.platform`:

```text
resolve paths → create runtime directories → migrate legacy database
→ initialize database/memory/learning → start managed services → serve UI
```

The active main database is `store/cyrene.runtime.database`. If the historical
`store/cyrene.db` exists and the new target is not populated, startup uses the
SQLite backup API, verifies the snapshot, writes an idempotent migration marker,
and retains the source for rollback. If the target already holds data and its migration status is unclear, startup stops and preserves the data.

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
Chat attachments and generated exports enter through
the same service. Agent access uses
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

Codebase-aware implementations live in the editable
`cyrene.plugins.builtin.cyrene_code` pack and are progressively exposed through
`code_tools`:

- **Indexer** — builds a SQLite index of symbols, references, imports, and file hashes
- **Analysis** — query symbols, callers, references, and file summaries
- **Git tools** — inspect diffs, blame, log, branches, and status

### Conversation-owned goals and dynamic workspaces

Cyrene saves work by conversation. Enabled plugins keep goals, plans, and workspace state with the conversation they belong to.

```text
Conversation
├── ContextTree and continuous Agent run
├── cyrene_goal state: objective, limits, review, loop status
├── cyrene_control state: plan and step progress
├── conversation changes: file snapshots and review baseline
└── workspace surface: editor, terminal, problems, review, preview, files
```

The `cyrene_goal` pack owns `/goal`, goal discussion and confirmation, the
visible Goal tab, independent review, repair loops, manual acceptance, stop,
completion notification, and restart recovery. A goal lease serializes each
iteration with normal conversation execution. A model turn ending is not goal
completion: only a passing review or explicit user acceptance completes it.
Permission requests, user questions, safety limits, and user stop are explicit
loop boundaries.

File and directory tools publish validated workspace-relative resource effects.
The conversation surface broker interprets those effects only when the user has
explicitly requested a file or structure to be shown, or when an existing
surface can be updated. Plugins contribute file types, project types, actions,
and surfaces through the normal extension registry; they cannot write directly
to pane state or reveal paths outside the project workspace.

`cyrene_code` supplies the shared workspace execution service and native
surface. Editor buffers use content versions to distinguish clean reloads from
user-owned unsaved changes. Conversation snapshots and Git working-tree diffs
share one diff renderer but retain their different baselines. Terminal,
diagnostic, preview, and file tabs exist only when content is available, and
the selected resource and layout are persisted per conversation.

Project-type plugins detect safe actions for JavaScript/TypeScript, Python,
TeX, Go, Rust, Java, Make, and GitHub workspaces. Their activation follows the
matching runtime extensions. Each action describes a program and its arguments. The provider resolves them within the validated workspace, while one execution service manages identity, status,
terminal attachment, diagnostics, artifacts, endpoints, interruption, and
restart. This is why TeX PDF compilation and an application development server
use the same Build/Run/Test/Preview path.

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
provider, and Schedule module activation.

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

The main interface brings together chat, documents and literature, schedules, and memory. Search, settings, help, and profile open within it. You can read a web page or PDF beside a conversation, or compare file changes there. First-run setup and Quick Chat use the same frontend.

**Conversations and pinned resources.** The top bar shows up to three recent or pinned conversations. A separate shelf remembers pinned resources across restarts. Drag selected text onto the shelf to save it as Markdown. Pin a file and other conversations can find it by its index. The agent reads the contents when it needs them. The backend looks up library attachments so the interface can refer to them without receiving their absolute paths.

**Opening a page in two conversations.** The conversation that opened a page keeps control of it. Browser tools let other conversations inspect a pinned page through snapshots and screenshots. Drag the page into another conversation to open the same URL in a new tab there. Each conversation controls its own tab. Both share cookies and login state.

**How the agent uses the browser.** In the desktop app, you and the agent use the same embedded Chromium browser. Its 17 operations are available to the main agent and run in order by default. A snapshot gives temporary references to visible elements that can receive a click. After moving the pointer, Cyrene checks that the target is still in place and unobstructed before clicking. Waits and resource timings come from the current page. Embedded frames and custom controls may need separate inspection.

Browser pages run in an isolated sandbox with Node integration disabled. Camera, microphone, location, and screen capture are blocked by default. You approve each file upload for a particular destination. Cyrene checks the file again before sending it, and the approval expires after one use. Clicks, typing, and form submissions follow their own permission rules. [Browser interaction and permissions](browser-live-view.md)

**Connections and builds.** FastAPI serves the interface locally. The desktop app controls its embedded browser over an authenticated local connection. Outside the desktop app, the web UI and CLI can use Playwright or fall back to reading page text with `httpx`. Before opening a URL, Cyrene checks its IP address and blocks loopback, private, link-local, cloud metadata, and reserved addresses. The text client checks every redirect. The desktop browser checks only some requests, and Playwright checks the final URL after connecting. Enforce network isolation at the OS or network level when required.

Frontend source lives in `src/cyrene/workbench/webui/frontend`. Builds go to `src/cyrene/workbench/webui/static/app`. The top bar component, `WorkbenchTopbar`, saves pinned resources in the document store. In-app drags use `application/x-cyrene-work-resource+json`, and text dragged from macOS arrives as `text/plain`.

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
- **`python -m cyrene.platform.host`** — an interactive, headless REPL that runs the agent directly without starting a web server.

## Security & Local Auth

**Who can connect?** The web server listens on the local address `127.0.0.1` and checks the host and origin of incoming requests. It serves one person on one computer and has no account login. Keep it accessible only from that computer. At each launch the desktop app generates a fresh token and sends it to the local backend in the `X-Cyrene-Token` header. Projects organize work, and tool permissions govern agent actions. Use operating-system controls to isolate people who share a machine.

**Where are secrets stored?** Cyrene encrypts configuration secrets in `data/config.enc`. It keeps the encryption key in the OS keyring when available. If the keyring is unavailable, Cyrene logs a warning and writes the key to a file that only the current OS user can read or write (mode `0600`).

**What is in a backup?** A portable backup ZIP includes a copy of the configuration so you can restore it on another installation. That copy may contain API keys or other credentials. Handle it as you would a file containing those keys. Workspace files, databases, logs, traces, exports, and backups rely on operating-system file permissions and storage protection.

## Project Structure

```
src/
├── cyrene/
│   ├── core/                        # Host-neutral Agent runtime
│   │   ├── context/                 # Persistent Context Tree
│   │   ├── hook/                    # Tree-local lifecycle hooks
│   │   └── plugin/                  # Plugin values, scopes, registry, execution
│   ├── plugins/                     # Cyrene product plugin layer
│   │   ├── application.py          # Application-scope plugin host
│   │   ├── context.py              # Workbench application contribution SDK
│   │   ├── model_*.py              # Model-provider composition
│   │   └── builtin/                # Canonical editable feature plugins
│   ├── workbench/                   # Cyrene host adapter
│   │   ├── application/            # App orchestration, events, notifications
│   │   ├── chat/                   # Chat services, repositories, and runs
│   │   ├── projects/               # Project lifecycle, files, and composition
│   │   ├── artifacts/              # Artifacts, presentation, and exports
│   │   ├── sessions/               # Session context and presentation
│   │   ├── control/                # External control ports and projections
│   │   ├── workspaces/             # Workspace changes and diff services
│   │   ├── ui/                     # UI surface abstractions
│   │   ├── core_adapter/           # Conversation bridge to core
│   │   ├── http/                   # FastAPI/HTTP composition
│   │   ├── persistence/            # Workbench persistence
│   │   └── webui/                 # App lifecycle and sole SPA source/output
│   ├── agents/                      # External ACP agent integration
│   ├── model/                       # Provider transport/runtime support
│   ├── platform/                    # Process bootstrap, config, storage, lifecycle
│   └── observability/               # Traces, debugging, and telemetry
tests/                               # Test suite
data/                                # Source-run state, debug logs, uploads
workspace/                           # Source-run SOUL.md and user workspace
store/                               # Source-run SQLite databases
```

The core package, `cyrene.core`, stays independent of the plugin and interface layers. When the interface opens a core session, it passes in model and application services through a plugin scope. Plugins store goals and plans on each conversation. The `cyrene.workbench` subpackages organize application services by function.
