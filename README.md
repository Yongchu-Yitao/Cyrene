<p align="center">
  <img src="https://img.shields.io/badge/python-3.12+-blue" alt="Python">
  <img src="https://img.shields.io/badge/version-0.5.1-blue" alt="Version">
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

### Agent Core

- **Two-phase agent loop** — every turn first decides whether it can answer directly (one LLM call, no tools) or whether it needs to act; only then does it enter the tool-using phase. Simple chat stays cheap and fast, while real work still gets the full toolset.
- **SOUL.md personality** — Cyrene keeps a personality document it rewrites itself. As it learns your preferences, voice, and the people and projects in your life, it edits its own `SOUL.md`, so the personality evolves across sessions instead of resetting every time.
- **Deep Research** — a multi-round research pipeline that plans sub-questions, searches and reads sources across several rounds, and exports a structured PDF report at the end.
- **Deep Reflection** — for complex or ambiguous requests, Cyrene reframes the problem over several internal rounds before answering, trading a little latency for a better-aimed response.
- **Behavior learning** — distills reusable action patterns from past conversations, so recurring workflows get faster and more consistent over time.

### Memory & Knowledge

- **Three-tier memory** — context window → short-term cross-session summaries → long-term `SOUL.md`. Conversations are compressed into short-term entries; a steward agent promotes the durable ones to long-term. Stale or superseded short-term memories can be **retired** so they stop being injected and recalled.
- **Knowledge base** — upload documents, PDFs, and images; Cyrene embeds and indexes them (including vision indexing for images) so the agent can search and cite them mid-task.
- **Entities** — track structured project entities (people, systems, items) that the agent can query and update as facts change.

### Tools & Automation

- **Parallel sub-agents** — spawn independent agents with full tool access to work in parallel, coordinated through an inbox so their results flow back into the main run.
- **Built-in web search** — bundled SimpleXNG (SearXNG engine) means web search works out of the box, with no Docker and no external search API key.
- **MCP protocol** — connect any stdio or SSE Model Context Protocol server to extend the toolset with third-party capabilities.
- **Task scheduler** — cron, interval, and one-shot scheduled tasks, plus a proactive lottery system that lets Cyrene act on its own initiative rather than only when prompted.
- **Browser live view** — full browser automation (navigate, click, type, snapshot, scroll, network log, wait). The Electron app drives its embedded Chromium directly with native tabs; source/CLI web runs can use Playwright for login takeover.
- **Desktop App Use** — control macOS and Windows desktop applications: detect windows, read UI structure, click, type, and swipe — all without taking over your foreground.
- **Code tools** — codebase indexing, symbol search, call-chain analysis, and git helpers for working inside repositories.
- **Claude Code bridge** — detect, launch, and prompt Claude Code tmux sessions directly from within Cyrene.
- **Skills installer** — install `.md` / `.zip` prompt skills at runtime to teach Cyrene new procedures without a redeploy.

### Interfaces & Channels

- **Workbench UI** — a project-centric desktop experience: per-project dashboard, schedule, knowledge, memory, and chat, with honest step-by-step task execution you can follow and steer.
- **Legacy agent UI** — the classic single-agent web UI: real-time chat, agent-flow timeline, session history, memory pipeline, context debugger, and settings.
- **Context debugger** — inspect exactly what context (system prompt, memory, conversation history, tool set) was sent to each individual LLM call.
- **Electron desktop app** — packaged builds for macOS, Windows (x64 + ARM64), and Linux via CI, with credentials stored in the OS keyring. Its embedded Chromium powers browser tools, so releases do not ship a second Playwright/Chromium runtime.
- **Telegram bot** — full agent access from Telegram.
- **WeChat bot** — basic WeChat integration.
- **Map engine** — interactive AMap / Leaflet map with pins for location-based tasks.

---

## Limitations (current as of v0.5.1)

- **Single-user** — one workspace, one SOUL.md, no user isolation
- **Local-only Web UI** — binds to `127.0.0.1`; the desktop app uses OS keyring auth, but the raw web server has no auth layer
- **No data retention policy** — session history grows indefinitely
- **Limited error recovery** — agent crashes are silently caught; the user is not always notified
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

Optional extras:

- **Browser live view & login takeover outside Electron** — `pip install -e ".[browser]"` then `playwright install chromium` (desktop releases need no extra install)
- **Development & tests** — `pip install -e ".[dev]"` then `uv run pytest -q`

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
