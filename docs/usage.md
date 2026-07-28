# Usage

[English](usage.md) · [简体中文](usage.zh-CN.md)

## Starting Cyrene

### Workbench UI

```bash
uv run python -m cyrene
```

### Interactive local CLI (no web server)

```bash
python -m cyrene.runtime.host
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

This launches the Electron window and starts the Python backend through the
physical `src/cyrene/local_cli.py` launcher.

---

## Workbench UI

The default Workbench UI is organized around projects:

| Page | What you can do |
|---|---|
| **Welcome / Projects** | Create, edit, switch, and delete projects; choose each workspace directory |
| **Task** | Create, plan, approve, execute, pause, verify, repair, and review task sessions |
| **Chat** | Project-scoped real-time chat and session history |
| **Knowledge / Library** | Import documents and bibliography files; manage literature and retrieval |
| **Schedule** | View and manage scheduled tasks |
| **Memory** | Inspect, search, create, and retire project memories |
| **Settings overlay** | Configure models, integrations, capabilities, channels, agents, data, and budgets |
| **Help/Profile/Search** | Secondary overlays and navigation, not separate legacy pages |

### Topbar work tabs and pinned resources

The topbar shows the three most recently opened task or chat sessions. Opening,
creating, or switching a session updates this MRU list immediately. Right-click
a session tab to pin/unpin it, copy its title, remove it from the topbar, or
inspect the browser and file resources currently associated with a chat.
Removing a tab does not delete or stop the underlying session.

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
cyrene do "your task" --session run_live
```

### Commands

| Command | Description |
|---|---|
| `cyrene start` | Start the daemon in the background |
| `cyrene stop` | Stop the daemon |
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

---

## Interactive Local CLI

```bash
python -m cyrene.runtime.host
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

MCP capabilities become discoverable through `integration_tools` without a
restart; their individual schemas are not appended to the fixed wire bundle.
The Capabilities settings page controls all 12 packages with one switch per
package. Turning a package off omits its gateway schema and package-specific
prompt instructions from both Phase 1 and Phase 2, and runtime validation still
blocks stale calls. The two phases keep identical tool arrays for the current
setting; toggling a package intentionally starts a new cache prefix. Direct
tools such as `AnalyzeAttachment` remain available.

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

## Claude Code Bridge

If you have `tmux` and Claude Code installed, Cyrene can:

- Detect existing Claude Code tmux sessions
- Start a new Claude Code session in a tmux pane
- Send prompts to Claude Code and read the response
- Show a live terminal view of the Claude Code session in the UI

Use `code.check_claude_code`, `code.start_claude_code`, and
`code.prompt_claude_code` through `code_tools`.

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

The Telegram bot supports the same two-phase loop, subagents, and tools as the Web UI.

---

## WeChat Bot

Open **Settings → Channels → WeChat**, request a QR code, scan it in WeChat,
confirm the login, and start the channel. The returned iLink bot token is stored
in the encrypted configuration and no restart is required. Environment/config
keys `WECHAT_BOT_TOKEN` and `WECHAT_OWNER_ID` remain compatibility inputs, but
the current UI flow does not require users to obtain or enter them manually.

> WeChat support is **Alpha** and depends on the availability and behavior of
> the WeChat iLink Bot service.
