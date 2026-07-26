<p align="center">
  <img src="https://img.shields.io/badge/python-3.12+-blue" alt="Python">
  <img src="https://img.shields.io/badge/version-0.7.0b1-blue" alt="Version">
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

Cyrene has one official Web UI: **Workbench**.

## Current limitations

- Cyrene is designed for one local operator. Projects are organizational
  boundaries, not separate users or security tenants.
- The Web server is local-only and is not intended for public internet
  exposure.
- Tool permissions reduce accidental actions but do not provide an operating
  system, VM, or container sandbox.
- Prompts and selected context are sent to configured model services.
  Integrations may also exchange data with their configured services.
- Chat models currently require an OpenAI-compatible endpoint.
- Usage budgets are local estimates, not provider billing controls.
- Data has no automatic retention period; it remains until explicitly removed
  or reset.
- Electron browser cookies and logins are shared across projects.
- The HTTP API is not yet versioned as a stable public API.
- Literature DOI/title lookup, Zotero Web API two-way sync, Experiments, and
  Manuscripts are not implemented.
- Windows source installation has an upstream SimpleXNG limitation; use a
  pre-built app or follow the checked-in release workflow.
- Pull-request CI covers the full Python suite, WebUI build, and Electron App
  Use tests on Linux. Packaged, visual, upgrade, and credentialed integration
  checks remain release/manual gates.

See [Development](docs/development.md) for the exact validation baseline and
[Current Development Progress](project-notes/CONTEXT_DEV_PROGRESS.md) for
known engineering risks.

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

uv run python -m cyrene --workbench
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
uv run cyrene start
uv run cyrene status
uv run cyrene stop
```

For platform setup, optional browser support, channels, and development tests,
see [Installation](docs/installation.md) and
[Development](docs/development.md).

## Documentation

- [Installation](docs/installation.md)
- [Usage](docs/usage.md)
- [Configuration](docs/configuration.md)
- [Architecture](docs/architecture.md)
- [Development](docs/development.md)
- [Current development progress](project-notes/CONTEXT_DEV_PROGRESS.md)
- [Changelog](CHANGELOG.en.md)

## License

[Apache License 2.0](LICENSE)
