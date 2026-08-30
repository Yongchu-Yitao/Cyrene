# Usage

[English](usage.md) · [简体中文](usage.zh-CN.md)

## Starting Cyrene

### Workbench UI

```bash
uv run python -m cyrene
```

### Interactive local CLI (no web server)

```bash
python -m cyrene.platform.host
```

### Via the `cyrene` command-line client

```bash
# Start the daemon in the background
cyrene start

# Stop it
cyrene stop
```

Open `http://localhost:4242` for the web UI.

> The first launch runs an onboarding wizard for API key and personality setup.

The active main database is `store/cyrene.runtime.database`. Older
`store/cyrene.db` data is migrated automatically on startup when the new target
is not populated; the old database is retained for rollback.

### Electron desktop development

```bash
cd electron
npm run dev
```

This launches the Electron window and starts the Python backend through
`uv run cyrene --workbench --electron-mode`.

---

## Workbench UI

The default Workbench UI is organized around projects:

| Page | What you can do |
|---|---|
| **Welcome / Projects** | Create, edit, switch, and delete projects; choose each workspace directory |
| **Chat** | Project-scoped conversations, plans, goal loops, agent runs, and session history |
| **Knowledge / Library** | Import documents and bibliography files; manage literature and retrieval |
| **Schedule** | View and manage scheduled tasks |
| **Memory** | Inspect, search, create, and retire project memories |
| **Settings overlay** | Configure models, integrations, capabilities, channels, agents, data, and budgets |
| **Help/Profile/Search** | Secondary overlays and navigation, not separate legacy pages |

### Conversation goals and plans

Cyrene no longer has a separate Task product or Task page. Create a
conversation for project work and enter `/goal` when the outcome must keep
running until it is verified. The Agent first researches and discusses the
request, then proposes a concrete objective and acceptance criteria. The Goal
tab appears only while that goal exists and lets you edit the objective and
maximum duration, confirm it, inspect the current plan and review result,
manually accept the current result, or stop the goal at any time.

After confirmation, ordinary Agent turns cannot silently end the goal. Cyrene
continues planning, working, testing, and repairing until an independent review
reports pass, or until you stop it, an approval or answer is required, or an
explicit safety limit is reached. A failed review shows the critical gaps and
feeds them into the next repair round. External actions still use the normal
permission review instead of being repeated automatically.

### Dynamic file workspace

When you explicitly ask the Agent to edit or show a file, or to inspect a
directory structure, Cyrene can open a workspace beside the conversation. Its
available tabs are data-driven:

- **Editor** shows the active text file and follows verified file changes.
- **Terminal** shows a running workspace action or a terminal you opened.
- **Problems** shows build and test diagnostics and links back to their files.
- **Review** compares the conversation snapshot or the Git working tree with a
  shared diff viewer.
- **Preview** shows an available web endpoint, PDF, image, or generated file.
- **Files** browses the workspace without loading an entire large tree.

Tabs with no content are hidden. The active tab is highlighted in the bottom
bar, while action and review controls stay at the top. The surface remembers
the current file, folder, review source, execution, and user-owned layout across
navigation and restart. Agent activity updates an existing surface but does not
continually open unrelated files or replace an unsaved user buffer.

### Project actions and runtime plugins

The project editor includes optional action profiles. Detection can fill these
from the workspace, and you can correct or enter them manually. The workspace
toolbar then offers one Run button for the selected Build, Run, Test, or Preview
action, with Stop when a long-running process is active. Finite commands keep
their output and diagnostics without being reported as a crashed terminal;
long-running services keep a stable terminal identity and can reconnect after a
short interruption.

Project support is supplied by plugins. The built-in set recognizes JavaScript
and TypeScript projects using Node.js, Bun, pnpm, Yarn, or Deno; Python and uv;
TeX; Go; Rust; Java projects using Maven or Gradle; Makefiles; and GitHub
repositories. Installing a matching runtime in Extensions—or detecting that it
is already installed on the system—automatically installs or enables the
corresponding project plugin. TeX compilation and application startup use the
same action, terminal, diagnostics, artifact, and preview system.

### Topbar work tabs and pinned resources

The topbar shows the three most recently opened conversations. Opening,
creating, or switching a session updates this MRU list immediately. Right-click
a session tab to pin/unpin it, copy its title, remove it from the topbar, or
inspect the browser and file resources currently associated with a chat.
Removing a tab does not delete or stop the underlying conversation.

The resource shelf sits between the session tabs and Search. Its `+` target has
a hover hint and accepts:

- chat file cards and Knowledge/Library rows or cards;
- selected text (native text drag on macOS);
- the floating or minimized Electron Browser.

Dropped files and browsers appear as SVG-only chips; hover or keyboard focus
reveals the name. Dropping selected text or a library item without an attachment
materializes a Markdown file. Dropping a file or text resource on another chat
tab adds it to that chat's draft without sending.

A Browser PiP, favicon-only minimized button, or pinned Browser chip can be
dropped on another chat tab. The target conversation opens the same URL in its
own Browser manager. Both conversations share the login profile but retain
independent pages and control.

The topbar is keyboard-operable. Once focused, Left/Right and Home/End traverse
sessions and resources, Enter/Space opens, and Delete/Backspace removes.
`Cmd/Ctrl+1…3` opens the three sessions directly, `Ctrl+Tab` and
`Ctrl+Shift+Tab` cycle, and `Cmd/Ctrl+W` removes the current session without
stopping it. The project shortcut is `Cmd/Ctrl+Shift+1`.

Pinned files are listed as global user-provided resources for subsequent Agent
turns. A pinned Browser remains controllable only by its owner session; other
sessions can request a snapshot or screenshot but cannot navigate, click, type,
reload, upload, or otherwise mutate it. Unpinning removes only the topbar
reference and never deletes the source file, text, knowledge item, or page.

---

Workbench is the only Web UI. Real-time Markdown chat, guidance, subagent and
browser status, session history, memory, knowledge/search, scheduling, maps,
model/tool/MCP/API-key settings, theme controls, and the other established
Workbench capabilities remain available through its pages, panels, and settings
overlay.

### Agent control of the Cyrene UI

In a local Electron Workbench turn, the main agent can use `cyrene_tools` to
snapshot and inspect the current visible Cyrene surface, then invoke only the
click, double-click, type, scroll, or drag actions declared by that exact
snapshot revision.
This does not use App Use and is independent of keyboard focus. New Chat,
Search, project switching, the visible chat list, context menus, settings tabs,
and the floating Browser titlebar have stable semantic nodes; other visible
standard controls are added through a bounded current-viewport projection.
Streaming message/content updates do not expire those stable actions; newly
available approvals, questions, layers, or action sets do. If an unrelated
global revision changes, the renderer can still execute an unchanged target
through its bounded node-specific action lease; callers must pass the snapshot
revision verbatim rather than substituting the latest number.

Double-click uses its own capability and is accepted only when the inspected
action advertises `double_press` or `double_click`. The Browser PiP titlebar
advertises `maximize + double_press`, so the agent can double-click it to
maximize the window without focusing Cyrene or supplying coordinates. A normal
single-click button is rejected by the double-click capability.

The agent may fill the visible composer. Sending or sending guidance is an R2
action and requires an exact request from the same real local user turn (or the
normal local confirmation UI); stopping a running response is R1. The agent
cannot use a hidden background dispatcher. To send to another conversation it
must switch there in the visible UI, fill that composer, and invoke its visible
submit action.

Typed settings cover every non-model Settings tab. Direct changes use a
revision value, so a concurrent user edit causes a conflict instead of being
overwritten. Secrets, OAuth, QR login, file selection, OS permissions, and the
Models tab remain user-controlled ceremonies.

Verbose context traces are intentionally inspected outside the Web UI through
`cyrene flow`, `/api/context-debug/events`, or
`python -m cyrene.observability.context_debug`.

---

## CLI

The `cyrene` command is a thin HTTP client that communicates with the daemon at
`http://localhost:4242`. Loopback readiness and API calls explicitly ignore
environment proxy settings.

```bash
# Start daemon (background)
cyrene start

# In a new terminal:
cyrene status
cyrene chat
cyrene do "your task" --session run_live
```

### Commands

| Command | Description |
|---|---|
| `cyrene start` | Start the daemon in the background |
| `cyrene stop` | Stop the daemon |
| `cyrene chat [text]` | Open streaming interactive chat, or send one message and exit |
| `cyrene chat --list` | List persistent Workbench conversations |
| `cyrene chat --chat <id>` | Continue an existing Workbench conversation |
| `cyrene chat --chat <id> --resume --cursor <n>` | Resume a current/recent run after an event sequence |
| `cyrene chat --json <text>` | Emit public run events as NDJSON |
| `cyrene do <text> --session <id>` | Send a message to an agent session |
| `cyrene session list` | List all sessions (live + archived) |
| `cyrene session status --session <id>` | Show session details |
| `cyrene session delete --session <id>` | Delete a session |
| `cyrene flow --session <id>` | List agent rounds |
| `cyrene flow --session <id> --round <r>` | Show round execution trace |
| `cyrene flow --session <id> --round <r> --id <e>` | Inspect a specific event (LLM call or tool call) |
| `cyrene memory soul [--edit <path>]` | Print SOUL.md or replace it from a file |
| `cyrene memory short-term` | Print short-term memory entries |
| `cyrene memory context` | Print context window status |
| `cyrene status` | System status and metrics |
| `cyrene mcp list` | List MCP servers and their tools |
| `cyrene mcp add <name> stdio <cmd> [args...]` | Add a stdio MCP server |
| `cyrene mcp add <name> sse <url>` | Add an SSE MCP server |
| `cyrene mcp remove <name>` | Remove an MCP server |
| `cyrene mcp toggle <name>` | Enable/disable an MCP server |

Use `--json` for machine-readable output.

`cyrene start` is idempotent: if a healthy Cyrene daemon already owns port
4242, it reports that instance instead of launching a duplicate. `cyrene stop`
only targets the detected daemon.
When the target daemon enables local token authentication, the CLI reads
`CYRENE_AUTH_TOKEN` and sends it as `X-Cyrene-Token`.

---

## Interactive streaming CLI

`cyrene chat` is the recommended terminal interface: it starts the daemon in
the background when needed, then enters interactive chat. It connects to the
background daemon and shares Workbench's persistent conversations and runs:

```bash
cyrene chat --chat CHAT_ID
cyrene chat --mode plan
```

In the session, use `/new`, `/resume`, `/mode`, `/attach`, `/attachments`,
`/detach`, `/deep-reflect`, `/deep-research`, `/context`, `/config`, `/status`,
`/mcp`, and `/exit`. `/new` selects a project; `/resume` lists sessions with
their project names. Each session uses a two-line title/project and preview
card, with a blank line between cards. Selection menus support Up/Down and Enter. Alt+Enter
inserts a newline; the first Ctrl+C asks for confirmation and a second press
within two seconds exits the CLI without interrupting the background run.
Entering text immediately creates a conversation in the default project.

After a message is sent, the CLI uses a randomized, non-repeating star spinner
(`✶ ✸ ✹ ✺ ✷ ◌`) to show the current activity and elapsed time, then prints the
total duration on completion. While thinking, it reuses the app's existing
localized phrase pool and picks a different phrase about every four seconds.
Model-provided reasoning is
collapsed to “Thought for Ns” by default. Ctrl+O opens a temporary full-screen
viewer; Ctrl+O, Escape, Q, or Ctrl+C closes it and restores the prompt without
leaving the reasoning text in terminal scrollback.

`/context` uses the same composition data as the app's Conversation Context
card. It shows message tokens, a colored composition bar, and grouped System
Prefix, Ephemeral, and Conversation Message blocks. User, assistant, tool, and
system-injection rows are consistently indented.

These groups project the durable ContextTree, not a separate prompt estimate.
`SessionStart` freezes the editable system prompt, SOUL, memory, and learned
skills once per conversation. `TurnStart` appends composer-selected workspace,
MCP, attachments, and runtime context for each turn. The stable prefix is reused
byte-for-byte before the changing suffix. See [Architecture](architecture.md#how-plugins-become-one-agent).

`/config` uses a localized two-axis settings navigator: Left/Right switches
between General, Models, Tools, Connections, Data, and About tabs; Up/Down
selects a detailed setting in the active tab; Enter opens it. General and CLI
preference fields also use arrow-key selection, and labels follow `language`.

The CLI renders text replies and public tool, phase, and plan status. Live
browser interaction, rich-media viewers, Workbench's graphical layout, and raw
PTY passthrough are intentionally outside the terminal UI.

After Electron starts its backend, it publishes a local connection capability
readable only by the current OS user (`0600` on Unix). The CLI discovers and
connects to that same backend, so Electron and the CLI can run simultaneously
while sharing projects, conversations, memory, and run state. A second
backend is not started to contend for the database or scheduler.

---

## In-process local CLI (legacy)

```bash
python -m cyrene.platform.host
```

This starts the agent directly without a web server. Available in-conversation commands:

| Command | Action |
|---|---|
| `/h` | Help menu — clear context, reset personality, system status |
| `/mcp` | MCP server management (list/add/remove/toggle/test) |
| `/mcp list` | List configured MCP servers |
| `/clear` | Reset session context |
| `/deep-reflect [focus]` | Run Deep Reflection on the given focus |
| `quit` | Exit |

---

## Slash Commands in Chat

Workbench and the interactive CLI support slash commands:

| Command | Description |
|---|---|
| `/deep-reflect [focus]` | Trigger multi-round context reframing |
| `/clear` | Reset the current session context |

---

## MCP Server Management

Cyrene supports the [Model Context Protocol](https://modelcontextprotocol.io) for connecting external tools.

### Add a stdio server

```bash
# Filesystem tools via npm package
cyrene mcp add filesystem stdio npx -y @modelcontextprotocol/server-filesystem /path/to/workspace

# Python-based MCP server
cyrene mcp add marp-deck stdio python /path/to/mcp_server.py
```

### Add an SSE server

```bash
cyrene mcp add my-api sse http://localhost:3000/mcp
```

### List connected servers

```bash
cyrene mcp list
```

```text
Name              Transport    Status         Tools    Endpoint
filesystem        stdio        connected      3        npx -y @modelcontextprotocol/server-filesystem .
marp-deck         stdio        connected      4        python mcp_server.py
```

MCP capabilities become discoverable through the enabled MCP plugin without a
restart. The composer context menu controls which servers are mounted for the
current conversation, while Plugin Center controls plugin availability. The
Agent discovers enabled toolboxes and standalone tools through
`toolbox.list → describe → invoke`; tools marked as directly visible are also
included in the model's immediate tool list. Disabling a plugin removes its
tools and context contributions, and runtime validation rejects stale calls.

---

## Knowledge Base

Import documents through Workbench or attach them in chat. Merely placing an
arbitrary file in a project workspace does not automatically add it to the
knowledge database. Preserved/importable content includes:

- UTF-8 text and code files
- PDF, DOCX, PPTX, and XLSX
- Images (description requires a configured vision-capable model)
- Audio, video, archives, and unknown binary files as preserved attachments

The ingestion pipeline:

1. Preserves the attachment and records metadata/content hash
2. Extracts text from PDF, Office XML, images (when vision is configured), and
   readable text; unknown binaries remain archived without text chunks
3. Skips generic text extraction for files larger than 10 MiB (PDF/Office have
   their own extractors)
4. Chunks extractable content into `store/kb_<project-data-key>.db`
5. Generates embeddings only when an embedding provider is configured

The Literature Library in that same project database adds collections, tags,
status, metadata, notes, annotations, attachments, relations, citations,
CSL JSON/RIS/BibTeX import, JSON export, and read-only Zotero Desktop Local API
import. It does not yet provide DOI/title lookup, Zotero Web API bidirectional
sync, or a manuscript editor.

In chat, the agent discovers project search and Library operations through
`knowledge_tools`; the Knowledge/Library page queries the same project corpus.

---

## Browser Live View

When the agent uses browser tools, the chat UI shows a live screencast of the page. If the agent hits a login wall, it can request a **login takeover**: a real headed browser window opens, you log in, and the agent resumes in the now-authenticated session.

See [browser-live-view.md](browser-live-view.md) for setup and configuration.

---

## Shared persistent terminals

Cyrene Terminal Daemon owns interactive PTYs independently from the desktop
window. User and Agent can reconnect to the same terminal, run TUIs, and keep
scrollback and metadata across Electron restarts. Agent-created terminals are
bound to their conversation and are exposed through `code.shell.*`.

---

## Telegram Bot

Set these in the Settings UI or encrypted config:

```ini
TELEGRAM_BOT_TOKEN=your_bot_token
OWNER_ID=your_telegram_user_id
```

Then run:

```bash
uv run python -m cyrene --telegram
```

The Telegram bot uses the same plugin-native Agent runtime, context plugins,
subagents, and tool discovery flow as the Web UI.

---

## WeChat Bot

Open **Settings → Channels → WeChat**, request a QR code, scan it in WeChat,
confirm the login, and start the channel. The returned iLink bot token is stored
in the encrypted configuration and no restart is required. Environment/config
keys `WECHAT_BOT_TOKEN` and `WECHAT_OWNER_ID` remain compatibility inputs, but
the current UI flow does not require users to obtain or enter them manually.

> WeChat support is **Alpha** and depends on the availability and behavior of
> the WeChat iLink Bot service.
