<p align="center">
  <img src="https://img.shields.io/badge/python-3.12+-blue" alt="Python">
  <img src="https://img.shields.io/badge/version-0.6.17-blue" alt="Version">
  <img src="https://img.shields.io/badge/license-Apache%202.0-green" alt="License">
  <img src="https://img.shields.io/badge/status-beta-orange" alt="Status">
</p>

<p align="center">
  <img src="docs/assets/cyrene-hero.png" alt="Cyrene hero image" width="100%">
</p>

<h1 align="center">Cyrene — AI Agent That Evolves</h1>

<p align="center">
  An open-source AI agent framework with a living personality, parallel subagents,<br>
  a workbench-style desktop UI, and zero infrastructure. No Docker, no Redis, just Python.
</p>

---

## What is Cyrene?

Cyrene is an AI agent that **runs continuously** — it has a self-rewriting personality (`SOUL.md`), remembers conversations across sessions, spawns sub-agents for parallel work, and can act proactively via scheduled tasks.

It runs as a local daemon with two web front-ends (and optional Telegram/WeChat bots), connecting to any OpenAI-compatible LLM API. Everything — memory, knowledge, scheduler, browser automation, search — lives in a single Python process backed by SQLite and flat files. There is no external infrastructure to stand up: no Docker, no Redis, no vector database service.

A quick map of the moving parts:

- **One process** hosts the agent loop, the FastAPI web server, the scheduler, and the bundled search engine.
- **Two UIs** ship side by side — a project-centric **Workbench** (default) and the classic single-agent **Legacy** UI — sharing the same backend.
- **Any OpenAI-compatible API** works (DeepSeek by default; Claude, GPT, Qwen, and local models all fit).

---

## Features

Cyrene packs a lot into that single process. Here is the full picture, grouped by what you would reach for.

### 🧠 Agent core

The reasoning loop and the pieces that make Cyrene feel less like a stateless chatbot.

- **Two-phase agent loop** — every turn first decides whether it can answer directly (one LLM call, no tools) or whether it needs to act; only then does it enter the tool-using phase. Simple chat stays cheap and fast, while real work still gets the full toolset. *Stable*
- **`SOUL.md` personality** — Cyrene keeps a personality document it rewrites itself. As it learns your preferences, voice, and the people and projects in your life, it edits its own `SOUL.md`, so the personality evolves across sessions instead of resetting every time. *Stable*
- **Deep Research** — a multi-round research pipeline that plans sub-questions, searches and reads sources across several rounds, and exports a structured PDF report at the end. *Stable*
- **Deep Reflection** — for complex or ambiguous requests, Cyrene reframes the problem over several internal rounds before answering, trading a little latency for a better-aimed response. *Beta*
- **Behavior learning** — distills reusable action patterns from past conversations, so recurring workflows get faster and more consistent over time. *Beta*

### 🗂️ Memory & knowledge

How Cyrene remembers across sessions and works with your documents.

- **Three-tier memory** — context window → short-term cross-session summaries → long-term `SOUL.md`. Conversations are compressed into short-term entries; a "steward" promotes the durable ones to long-term. Stale or superseded short-term memories can be **retired** so they stop being injected and recalled, without being destructively deleted. *Stable*
- **Knowledge base** — upload documents, PDFs, and images; Cyrene embeds and indexes them (including vision indexing for images) so the agent can search and cite them mid-task. *Stable*
- **Entities** — track structured project entities (people, systems, items) that the agent can query and update as facts change. *Stable*

### 🛠️ Tools & automation

What Cyrene can actually *do* beyond talking.

- **Parallel sub-agents** — spawn independent agents with full tool access to work in parallel, coordinated through an inbox so their results flow back into the main run. *Stable*
- **Built-in web search** — bundled SimpleXNG (SearXNG engine) means web search works out of the box, with no Docker and no external search API key. *Stable*
- **MCP protocol** — connect any stdio or SSE [Model Context Protocol](https://modelcontextprotocol.io) server to extend the toolset with third-party capabilities. *Stable*
- **Task scheduler** — cron, interval, and one-shot scheduled tasks, plus a proactive "lottery" system that lets Cyrene act on its own initiative rather than only when prompted. *Stable*
- **Browser live view** — the Electron app drives its embedded, persistent browser directly (logins survive across runs), with native tabs and in-panel control. Source/CLI web runs can opt into Playwright for the same automation tools, live screencast, and login takeover flow. *Beta*
- **Code tools** — codebase indexing, symbol search, and git helpers for working inside repositories. *Beta*
- **Claude Code bridge** — detect, launch, and chat with Claude Code tmux sessions directly from within Cyrene. *Beta*
- **Skills installer** — install `.md` / `.zip` prompt skills at runtime to teach Cyrene new procedures without a redeploy. *Stable*

### 🖥️ Interfaces & channels

Where you actually talk to Cyrene.

- **Workbench UI** — a project-centric desktop experience: per-project dashboard, schedule, knowledge, memory, and chat, with honest step-by-step task execution you can follow and steer. *Stable*
- **Legacy agent UI** — the classic single-agent web UI: real-time chat, an agent-flow timeline, session history, and settings. *Stable*
- **Context debugger** — inspect exactly what context (system prompt, memory, conversation history, tool set) was sent to each individual LLM call. *Stable*
- **Electron desktop app** — packaged builds for macOS, Windows (x64 + ARM64), and Linux via CI, with credentials stored in the OS keyring. Its embedded Chromium powers browser tools, so releases do not ship a second Playwright/Chromium runtime. *Beta*
- **Telegram bot** — full agent access from Telegram. *Stable*
- **WeChat bot** — basic WeChat integration. *Alpha*
- **Map engine** — interactive AMap / Leaflet map with pins for location-based tasks. *Beta*

---

## Limitations (current as of v0.6.17)

- **Single-user** — one workspace, one `SOUL.md`, no user isolation
- **Local-only Web UI** — binds to `127.0.0.1`; the desktop app uses OS keyring auth, but the raw web server has no auth layer
- **No data retention policy** — session history grows indefinitely
- **Limited error recovery** — agent crashes are caught silently; the user is not always notified
- **No API versioning** — all endpoints live under a bare `/api/`
- **No rate/cost limiting** — there is no LLM call quota or spend protection
- **Windows from source** — requires manual patching of vendored dependencies; the pre-built installer is recommended
- **Testing** — unit tests exist (`uv run pytest -q`) but the pytest suite is not run in CI (CI only smoke-tests the packaged app), and there are no integration/E2E tests

---

## Quick Start

### Option A: Pre-built (macOS / Windows / Linux)

Download the latest release for your platform from the [Releases page](https://github.com/Yongchu-Yitao/Cyrene/releases).

> Windows ARM64 and x64 installers are provided separately.

### Option B: From source

Requires Python 3.12+ and [Node.js 20+](https://nodejs.org/) (for the WebUI JSX precompilation step).

```bash
# 1. Install dependencies (uv recommended — uv.lock is committed for reproducible builds)
uv sync           # or: pip install -e .

# 2. Precompile the WebUI JSX → JS (required from source; releases bundle this already)
cd src/webui && npm install && node build-jsx.mjs && cd ../..

# 3. Run
python -m cyrene --workbench     # Workbench UI (default)
python -m cyrene --agent         # Classic agent UI (legacy)
```

Open `http://localhost:4242`. First launch runs an onboarding wizard that guides you through API key configuration and personality setup.

> No `.env` file is required. All configuration is stored in an encrypted store (`data/config.enc` by default) and managed through the Web UI settings or onboarding wizard. A legacy `.env.example` is kept for backward compatibility.

### Electron app from source (development)

The Electron package lives in `electron/`, not at the repository root. Before
launching it, activate the same Python environment in which the project
dependencies were installed (`uv sync`, `pip install -e .`, or the equivalent).
Then use:

```bash
cd electron

# Keep both npm and the active environment's python3 visible to Electron, and
# make the source checkout importable by the Python backend subprocess.
CYRENE_ROOT="$(cd .. && pwd)"
PYTHON3_DIR="$(dirname "$(command -v python3)")"
env PATH="$PYTHON3_DIR:$PATH" PYTHONPATH="$CYRENE_ROOT/src" npm run dev
```

You can verify the selected interpreter before launching:

```bash
PYTHONPATH="$(cd ../src && pwd)" python3 -c \
  'import cyrene, cryptography, fastapi, uvicorn; print("Python environment OK")'
```

Common launch pitfalls:

- Running `npm run dev` from the repository root fails because the root has no
  `package.json`; run it from `electron/`.
- `ModuleNotFoundError: No module named 'cyrene'` means the checkout's `src/`
  directory is not on `PYTHONPATH`. Use the command above instead of launching
  Electron with a bare `npm run dev`.
- `ModuleNotFoundError: No module named 'cryptography'` (or another runtime
  dependency) means Electron resolved a different system `python3` from the one
  used to install Cyrene. Activate the intended environment and keep its binary
  directory first in `PATH` as shown above.
- A raw request to `http://127.0.0.1:4242/` may return `401 Unauthorized` while
  the Electron backend is healthy: the desktop window supplies its generated
  authentication token. Confirm startup using the `UIMODE=workbench`,
  `PORT=4242`, and HTTP `200` entries in the Electron terminal log.
- Chromium DevTools messages about `Autofill.enable` / `Autofill.setAddresses`,
  and `401` responses for optional JavaScript source maps, are development-log
  noise and do not indicate that Workbench failed to load.

Optional extras:

- **Browser live view & login takeover outside Electron** — `uv pip install -e ".[browser]"` then `playwright install chromium` (desktop releases need no extra install)
- **Development & tests** — `uv pip install -e ".[dev]"` then `uv run pytest -q`

> **Windows?** Pre-built binary recommended. For source (requires patching vendored SimpleXNG deps), see [docs/installation.md](docs/installation.md#windows).

---

## Documentation

- [Installation](docs/installation.md) — Linux, macOS, Windows
- [Architecture](docs/architecture.md) — Two-phase loop, features, project structure
- [Usage](docs/usage.md) — Workbench UI, legacy UI, CLI commands, in-conversation commands
- [Configuration](docs/configuration.md) — Environment variables reference
- [Development](docs/development.md) — Debugging, verbose logging, testing
- [Browser Live View](docs/browser-live-view.md) — Browser screencasting and login takeover

---

## Tech Stack

- **Runtime** — Python 3.12+, FastAPI, Uvicorn, SQLite
- **Package manager** — uv (lock file committed); pip also supported
- **Linting** — Ruff (line length 180)
- **LLM** — OpenAI-compatible API (default: DeepSeek, works with Claude/GPT/Qwen)
- **Search** — SimpleXNG (bundled, no Docker)
- **Browser** — Electron embedded Chromium on desktop; optional Playwright + WebSocket screencasting outside Electron
- **Desktop** — Electron + electron-builder, persistent native browser partition, OS keyring (keyring)
- **Channels** — python-telegram-bot, WeChat (itchat)
- **Encryption** — Fernet (cryptography) for config store

---

## License

Apache 2.0
