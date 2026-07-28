<p align="center">
  <img src="https://img.shields.io/badge/python-3.12+-blue" alt="Python">
  <img src="https://img.shields.io/badge/version-0.7.0b5-blue" alt="Version">
  <img src="https://img.shields.io/badge/license-Apache%202.0-green" alt="License">
  <img src="https://img.shields.io/badge/status-beta-orange" alt="Status">
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

- **Remember across sessions** — maintain a durable personality and memory while
  keeping project work separate.
- **Complete multi-step work** — plan, use tools, delegate to parallel
  subagents, verify results, and resume interrupted tasks.
- **Research and reflect** — conduct cited deep research, generate PDF reports,
  and use deep reflection when a task is stuck.
- **Organize projects** — manage project workspaces, chats, tasks, memories,
  knowledge, entities, schedules, and literature collections.
- **Understand files** — ingest and search text, PDFs, Office documents,
  Markdown, images, audio, video, and other attachments.
- **Work with literature** — manage collections, tags, notes, annotations,
  citations, attachments, relations, CSL JSON, RIS, BibTeX, and read-only Zotero
  Desktop imports.
- **Use the web and local tools** — search and browse the web, edit files, run
  shell and Git operations, connect MCP servers, and use installed skills.
- **Automate recurring work** — run cron, interval, and one-shot tasks and send
  optional desktop, Telegram, or WeChat notifications.
- **Run in Workbench or Electron** — use the same Workbench experience in a
  browser or desktop app, with Quick Chat, rich Markdown, code, diff, map, PDF,
  file-preview, and browser views.

## Quick start

### Desktop app

Download an artifact from
[GitHub Releases](https://github.com/Yongchu-Yitao/Cyrene/releases).

### From source

Requires Python 3.12+, `uv`, and Node.js 20+.

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
