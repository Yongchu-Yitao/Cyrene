# Usage

[English](usage.md) · [简体中文](usage.zh-CN.md)

## Starting Cyrene

### Workbench UI (default)

```bash
python -m cyrene --workbench
```

### Legacy agent UI

```bash
python -m cyrene --agent
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
| **Welcome / Projects** | Create and switch projects; each project has its own data scope |
| **Dashboard** | Overview of active tasks, recent sessions, and system status |
| **Chat** | Project-scoped real-time chat with the agent |
| **Schedule** | View and manage scheduled tasks and deadlines |
| **Knowledge** | Upload documents, browse the knowledge base, and run semantic search |
| **Memory** | Inspect and manage project/agent memory |
| **Model** | Select LLM models and API endpoints |
| **Help** | Onboarding tips and documentation links |

---

## Legacy Agent UI

The classic UI is still available with `--agent`:

| Page | Section | What you can do |
|---|---|---|
| **Chat** | Main | Send messages, view Markdown-rendered replies, see live progress |
| | Guidance | Send guidance to running agent rounds |
| | Subagents | Monitor active sub-agents and shells |
| | Browser | Live browser screencast and takeover card |
| **Agent Flow** | Canvas | SVG timeline of LLM calls, tool executions, subagent communication |
| **Sessions** | List | Browse, search, and delete sessions |
| | Detail | View messages, tokens, subagents per session |
| **Memory** | SOUL.md | Browse and edit the personality document |
| | Short-Term | View compressed memory with emotional valence |
| | Context | Monitor context window usage |
| **Knowledge** | Documents | Upload and manage documents |
| | Search | Run semantic/keyword search over the knowledge base |
| **Entities** | List | View and edit tracked project entities |
| **Evolution** | Patterns | Review and approve learned behavior patterns |
| **Tasks** | List | View scheduled tasks and their history |
| **Map** | View | AMap/Leaflet map with pins and routes |
| **Status** | Metrics | Subagents, sessions, memory, tasks |
| | Workers | Main agent and sub-agent status |
| | Services | LLM endpoint, SOUL.md, MCP servers health |
| | Context Debugger | Inspect context traces for recent LLM calls |
| **Settings** | General | Edit SOUL.md directly, toggle stream reasoning |
| | Models | Add/remove/select LLM models |
| | Tool packages | Enable/disable complete progressive tool packages |
| | MCP Servers | Add/remove/restart MCP server connections |
| | Search | SimpleXNG built-in mode only |
| | API Keys | Edit API keys and endpoints at runtime |
| | Appearance | Theme, text size, density |

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

Both web UIs and the interactive CLI support slash commands:

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

Upload documents through the Web UI or place them in the workspace. Supported types include:

- Text files (`.md`, `.txt`, `.py`, etc.)
- PDFs
- Images (description via vision model)
- Maps and other text-based files

The ingestion pipeline:

1. Extracts text (PDF → `pypdf`, images → vision model, text → UTF-8)
2. Skips binary files and files larger than 10 MB
3. Chunks content and stores it in `store/kb_<workspace>.db`
4. Generates embeddings if an embedding endpoint is configured

In chat, the agent discovers and invokes `knowledge.search` through
`knowledge_tools`; the Knowledge page queries the same corpus directly.

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
python -m cyrene
```

The Telegram bot supports the same two-phase loop, subagents, and tools as the Web UI.

---

## WeChat Bot

Set `WECHAT_BOT_TOKEN` and `WECHAT_OWNER_ID` in the encrypted config. Start Cyrene with the web UI; the WeChat integration runs alongside it. Status is shown on the Settings page.

> WeChat support is **Alpha** and may require a working proxy setup.
