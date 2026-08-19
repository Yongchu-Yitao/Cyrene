<p align="center">
  <img src="https://img.shields.io/badge/python-3.12+-blue" alt="Python">
  <img src="https://img.shields.io/badge/version-0.7.10-blue" alt="Version">
  <img src="https://img.shields.io/badge/license-Apache%202.0-green" alt="License">
  <img src="https://img.shields.io/badge/status-stable-brightgreen" alt="Status">
</p>

<p align="center">
  <img src="docs/assets/cyrene-hero.png" alt="Cyrene hero image" width="100%">
</p>

<h1 align="center">Cyrene — AI Agent That Evolves</h1>

<p align="center"><strong>English</strong> · <a href="README.zh-CN.md">简体中文</a></p>

<p align="center">
  An open-source, local-first AI agent with durable memory, parallel subagents,
  project workspaces, and a Workbench desktop UI.
</p>

## What Cyrene can do

- **An agent that grows with you** — Cyrene carries its personality and useful
  memories across sessions, while keeping every project's context cleanly
  isolated.
- **Context that flows with the work** — Cyrene composes traceable, shareable
  context blocks: project goals and outcomes flow across tasks, chat histories
  stay isolated, and tasks and subagents receive only the plans, memory, and
  execution state they need. Stable blocks remain reusable, and the full
  composition stays inspectable.
- **From conversation to verified results** — Cyrene can plan, browse, edit
  files, run shell and Git operations, connect MCP servers, use skills, delegate
  to parallel subagents, verify the result, and resume interrupted work.
- **Research you can trace and reuse** — Cyrene combines cited web research with
  your PDFs, Office files, media, and literature library, then turns the evidence
  into structured knowledge or polished PDF reports.
- **A browser the agent can actually operate** — watch Cyrene navigate, click,
  type, upload, and inspect pages in a live view. When login, CAPTCHA, or 2FA
  needs you, take over the same browser and hand it back without losing the
  session.
- **An agent that can manage itself** — through permissioned, auditable tools,
  Cyrene can inspect and operate its own UI, adjust settings, manage projects and
  chats, back up data, and handle updates.
- **A workspace for long-running thinking** — projects bring chats, tasks,
  memories, knowledge, entities, schedules, and literature together in one
  Workbench, available in both the browser and desktop app.
- **Automation that keeps working** — schedule one-shot or recurring tasks and
  receive results through desktop, Telegram, or WeChat notifications.

## Quick start

### Desktop app

Download an artifact from
[GitHub Releases](https://github.com/Yongchu-Yitao/Cyrene/releases).

### From source

Requires Python 3.12+, `uv`, and Node.js 22.12+.

```bash
uv sync

cd src/webui
npm install
npm run build
cd ../..

uv run python -m cyrene
```

Open `http://localhost:4242`. First run guides you through model and personality
setup.

To launch the Electron app:

```bash
cd electron
npm install
npm run dev
```

Background service commands:

```bash
uv run cyrene
uv run cyrene status
uv run cyrene stop
```

Bare `cyrene` starts the background service when needed and enters interactive
chat directly. `cyrene chat` provides streaming replies, tool and plan progress, permission
prompts, attachments, conversation switching, interruption, and run resume.
For scripts, use `cyrene chat --json "your task"`.

For platform setup, optional browser support, channels, and development tests,
see [Installation](docs/installation.md) and
[Development](docs/development.md).

## Documentation

- [Installation](docs/installation.md)
- [Usage](docs/usage.md)
- [Configuration](docs/configuration.md)
- [Architecture](docs/architecture.md)
- [Development](docs/development.md)
- [Current limitations](docs/limitations.md)
- [Current development progress](project-notes/CONTEXT_DEV_PROGRESS.md)
- [Changelog](CHANGELOG.en.md)

## License

[Apache License 2.0](LICENSE)
