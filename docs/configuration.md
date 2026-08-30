# Configuration

[English](configuration.md) · [简体中文](configuration.zh-CN.md)

## Encrypted Config Store

Cyrene stores configuration in a Fernet-encrypted JSON config blob
(`data/config.enc` by default). The first-run onboarding wizard writes the
required values, and the Web UI Settings page can update them at runtime.

The encrypted values live in `data/config.enc`. Its Fernet key is stored in the
OS keyring when available. Headless/portable environments without a working
keyring fall back to `data/.config_key` with mode `0600` and emit a warning;
that fallback protects the key only through filesystem permissions.

Portable backup ZIPs are not encrypted by Cyrene. They contain a logical
configuration snapshot—including configured credentials—so that restore can
re-encrypt it with the destination installation's key. Treat every exported
backup as a secret.

## Runtime Paths and Persistence

Source runs default to the checkout root. Packaged runs use the operating
system's application-data and cache locations. Tests and portable deployments
can override the resolved paths before Python imports Cyrene:

| Variable | Purpose |
|---|---|
| `CYRENE_BASE_DIR` | Runtime base containing `workspace/`, `store/`, and `data/` |
| `CYRENE_USER_DATA_DIR` | OS/user application-data root |
| `CYRENE_CACHE_DIR` | Cache root |
| `CYRENE_TEMP_DIR` | Temporary artifact root |
| `CYRENE_INSTALL_RESOURCES_DIR` | Packaged/static resource override |
| `CYRENE_ALLOWED_WORKSPACE_ROOTS` | Additional allowed project roots |

The active main database is `store/cyrene.runtime.database`.

## Environment Variables

The following variables are read at startup. Most can also be edited at runtime through the Web UI.

### Models

Models are not configured through environment variables. Settings → Models
stores one canonical graph of Provider Plugin connections, model profiles, and
independent primary, secondary, vision, and embedding routes. Provider catalogs
and inference operations are supplied by editable Model Plugins.

### Agent

| Variable | Description | Default |
|---|---|---|
| `ASSISTANT_NAME` | Agent display name | `Cyrene` |
| `MAX_TOOL_OUTPUT_CHARS` | Optional character cap for tool results sent to the LLM (`0` disables the global cap) | `0` |

### Telegram (optional)

| Variable | Description | Default |
|---|---|---|
| `TELEGRAM_BOT_TOKEN` | Bot token for Telegram interface | — |
| `OWNER_ID` | Your Telegram user ID | — |

### WeChat (optional)

| Variable | Description | Default |
|---|---|---|
| `WECHAT_BOT_TOKEN` | WeChat bot token | — |
| `WECHAT_OWNER_ID` | Optional WeChat owner ID | — |

### Embedding / Knowledge Base (optional)

Configure an embedding-capable profile in Settings → Models and add it to the
embedding route. If that route is empty, the knowledge base falls back to
FTS/text search.

### Scheduling

| Variable | Description | Default |
|---|---|---|
| `SCHEDULER_INTERVAL` | Scheduled-task polling interval in seconds | `60` |

### Steward & Pattern Learning

| Variable | Description | Default |
|---|---|---|
| `STEWARD_INTERVAL` | Seconds between SOUL.md steward runs (minimum one hour) | `3600` |
| `PATTERN_DETECTION_INTERVAL` | Seconds between behavior-pattern scans | `600` |

The active proactive cadence is the encrypted runtime setting
`heartbeat_interval`, exposed in Settings and defaulting to `1800` seconds.
The scheduler reads it at startup.

### Search

| Variable | Description | Default |
|---|---|---|
| `SEARXNG_AUTO_START` | Auto-launch SimpleXNG | `1` (enabled) |
| `SEARXNG_PORT` | SimpleXNG listen port | `8888` |
| `SEARXNG_HOST` | SimpleXNG bind address | `127.0.0.1` |
| `SEARXNG_URL` | External SearXNG URL (overrides auto-start) | — |
| `SEARCH_PROXY` | Manual proxy for search HTTP requests | — |

> When an official DeepSeek V4 model is configured with an
> `https://api.deepseek.com` API key, Cyrene uses DeepSeek's server-side
> [Responses API Web Search](https://api-docs.deepseek.com/guides/responses_api/)
> first. The documented `deepseek-v4-flash` model is
> used as the search worker. If native search is unavailable or fails, Cyrene
> automatically falls back to SimpleXNG. Third-party DeepSeek-compatible
> endpoints do not activate native search. The older DDG/Bing/Baidu scrapers
> remain removed.

### Web UI

| Variable | Description | Default |
|---|---|---|
| `WEB_PORT` | Web UI port | `4242` |

### Map

| Variable | Description | Default |
|---|---|---|
| `AMAP_API_KEY` | AMap API key for map tiles/geocoding | — |

## Runtime Settings

Most settings can be edited at runtime through the Web UI **Settings** page without restarting:

- **Credentials** — Update channel and map credentials
- **Models** — Manage Provider Plugin connections, profiles, and role routes
- **Plugin packs and standalone Plugins** — Enable or disable Agent capabilities
- **Agents** — Main-agent execution is completion-driven with no tool-round limit; configure execution subagent lease checkpoints, no-progress detection, wide tool/time/cost/context safety fuses, and separate discussion round/message/information-gain limits
- **Search** — official DeepSeek Responses Web Search when configured, with built-in SimpleXNG fallback
- **MCP Servers** — Add, remove, and restart MCP server connections
- **SOUL.md** — Edit the personality document directly
- **Budget** — Configure estimated-cost tracking, CNY/USD display, billing
  start day, adaptive mode, and warn/block behavior

Plugin Center is the composition control plane. A pack switch enables or
removes all of its application, session, Hook, context, and tool contributions.
Each non-core tool menu can edit the model-facing name and description, choose
**directly visible** or **Agent finds and uses**, or delete the user-owned
source. Required kernel plugins remain fixed. Per-conversation workspace, MCP,
and skills selections are controlled separately by the input box's
composer-context plugin. See [Architecture](architecture.md#how-plugins-become-one-agent)
and [Custom plugins](project-plugins.md).

### Agent-visible typed settings

`CyreneSettingsDescribe`, `CyreneSettingsRead`, and `CyreneSettingsUpdate` are
available only to the main Agent through the `cyrene_application` Plugin pack.
The namespaces are
`runtime`, `desktop`, `appearance`, `profile`, and `shortcuts`.

Updates are atomic compare-and-swap patches and require the latest
`expected_revision`. A stale revision returns a conflict and preserves the
user's newer edit. Shortcut patches preserve unspecified actions and use
`null` only to reset a named binding. Models, secret values, secret redaction,
and the availability of the settings Plugins themselves cannot be changed through this
control plane.

## Browser Configuration

Browser-specific settings are documented in [browser-live-view.md](browser-live-view.md). They use the `CYRENE_BROWSER_*` key namespace and are read from the encrypted config store.

Electron injects `CYRENE_AUTH_TOKEN`, `CYRENE_ELECTRON_RPC_PORT`, and
`CYRENE_ELECTRON_RPC_TOKEN` into its child runtime. These are internal
per-launch security values and should not be persisted manually.

## Model pricing and budgets

Cyrene records token usage and estimates cost for the model that actually
served each response. An explicit saved price wins, followed by the built-in
catalog in `cyrene.model.pricing`; an unknown unpriced model records
zero. User prices accept `input/output` or `input/cache-hit/output` per one
million tokens. Prefix a value with `$` for USD or `¥` for CNY; unmarked values
default to CNY. Recorded costs are normalized to CNY for aggregation, then
converted to the currency selected beside Monthly Budget for display and
budget enforcement. Billing-period totals use the configured start timestamp,
not a rolling-day approximation.

The catalog is code data, not a live quote. Its current source is marked as
verified on 2026-06-25 and uses a fixed `7.25 CNY = 1 USD` conversion for
USD-priced providers. It contains aliases for GPT 5.5, Claude Fable/Mythos 5,
Gemini 3.5 Flash/3.1 Pro Preview, DeepSeek V4 Flash/Pro, GLM 5.2, MiniMax M3,
MiMo V2.5, and Kimi K2.7 Code. Check the source and provider invoice before
relying on an estimate.

Budget Settings can divide a configured monthly amount into adaptive monthly,
weekly, and five-hour windows and can warn or block new Workbench runs. These
are local gates over Cyrene's estimates, not provider-side quotas or billing
guarantees.
