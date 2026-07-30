# Configuration

[English](configuration.md) · [简体中文](configuration.zh-CN.md)

## Encrypted Config Store

Cyrene stores most configuration in a Fernet-encrypted JSON config blob (`data/config.enc` by default). You do **not** need a `.env` file for normal operation. The first-run onboarding wizard writes the required values, and the Web UI Settings page can update them at runtime.

A legacy `.env.example` is still shipped for backward compatibility, but new installs should use the onboarding wizard or Settings UI.

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

The active main database is `store/cyrene.runtime.database`. On first startup,
an existing `store/cyrene.db` is migrated only when the new target is absent or
row-empty. The source remains in place as a rollback copy.

## Environment Variables

The following variables are read at startup. Most can also be edited at runtime through the Web UI.

### LLM

| Variable | Description | Default |
|---|---|---|
| `OPENAI_API_KEY` | API key (OpenAI / DeepSeek / compatible) | — |
| `OPENAI_BASE_URL` | API endpoint URL | `https://api.deepseek.com/v1` |
| `OPENAI_MODEL` | Model name | `deepseek-v4-flash` |

### Agent

| Variable | Description | Default |
|---|---|---|
| `ASSISTANT_NAME` | Agent display name | `Cyrene` |
| `MAX_TOOL_ROUNDS` | Maximum tool-use rounds per user message | `15` |
| `MAX_HISTORY_MESSAGES` | Messages kept in the context window | `40` |
| `MAX_TOOL_OUTPUT_CHARS` | Character cap for tool results sent to the LLM | `12000` |

### Telegram (optional)

| Variable | Description | Default |
|---|---|---|
| `TELEGRAM_BOT_TOKEN` | Bot token for Telegram interface | — |
| `OWNER_ID` | Your Telegram user ID | — |

### WeChat (optional)

| Variable | Description | Default |
|---|---|---|
| `WECHAT_BOT_TOKEN` | WeChat bot token | — |
| `WECHAT_OWNER_ID` | Historical owner-ID compatibility field; the current QR flow discovers senders | — |

### Embedding / Knowledge Base (optional)

| Variable | Description | Default |
|---|---|---|
| `EMBEDDING_BASE_URL` | OpenAI-compatible embedding endpoint | — |
| `EMBEDDING_API_KEY` | API key for the embedding endpoint | — |
| `EMBEDDING_MODEL` | Embedding model name | — |

> If no embedding endpoint is configured, the knowledge base falls back to FTS/text search.

### Scheduling

| Variable | Description | Default |
|---|---|---|
| `SCHEDULER_INTERVAL` | Scheduled-task polling interval in seconds | `60` |
| `HEARTBEAT_INTERVAL` | Historical compatibility value; not the active proactive cadence | `300` |
| `HEARTBEAT_LOTTERY_INTERVAL` | Historical compatibility value; not read by the current scheduler | `1800` |
| `DAYTIME_START` | Historical compatibility value; current proactive window is fixed at 06:00 | `6` |
| `DAYTIME_END` | Historical compatibility value; current proactive window ends at 22:00 | `22` |

### Steward & Pattern Learning

| Variable | Description | Default |
|---|---|---|
| `STEWARD_INTERVAL` | Seconds between SOUL.md steward runs (minimum one hour) | `3600` |
| `PATTERN_DETECTION_INTERVAL` | Seconds between behavior-pattern scans | `600` |
| `LOTTERY_DELTA` | Historical compatibility value; current lottery increment is fixed at `0.15` | `0.15` |
| `LOTTERY_MAX` | Historical compatibility value; current lottery cap is fixed at `0.85` | `0.85` |

The active proactive cadence is the encrypted runtime setting
`heartbeat_interval`, exposed in Settings and defaulting to `1800` seconds.
The scheduler reads it at startup. The historical environment keys above are
still parsed for compatibility, but changing them does not currently change
the proactive cadence, daytime window, or lottery parameters.

### Search

| Variable | Description | Default |
|---|---|---|
| `SEARXNG_AUTO_START` | Auto-launch SimpleXNG | `1` (enabled) |
| `SEARXNG_PORT` | SimpleXNG listen port | `8888` |
| `SEARXNG_HOST` | SimpleXNG bind address | `127.0.0.1` |
| `SEARXNG_URL` | External SearXNG URL (overrides auto-start) | — |
| `SEARCH_PROXY` | Manual proxy for search HTTP requests | — |

> Cyrene now uses **SimpleXNG only** for web search. The older DDG/Bing/Baidu scrapers have been removed.

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

- **API Keys** — Update `OPENAI_API_KEY`, `OPENAI_BASE_URL`, `OPENAI_MODEL`, `TELEGRAM_BOT_TOKEN`, `WECHAT_BOT_TOKEN`, `AMAP_API_KEY`, embedding credentials
- **Models** — Add or remove model configurations
- **Tool packages** — Enable or disable complete progressive-disclosure
  packages; direct control/filesystem/web tools remain part of the stable wire
  contract
- **Agents** — Main-agent execution is completion-driven with no tool-round limit; configure execution subagent lease checkpoints, no-progress detection, wide tool/time/cost/context safety fuses, and separate discussion round/message/information-gain limits
- **Search** — Built-in SimpleXNG only
- **MCP Servers** — Add, remove, and restart MCP server connections
- **SOUL.md** — Edit the personality document directly
- **Budget** — Configure estimated-cost tracking, CNY/USD display, billing
  start day, adaptive mode, and warn/block behavior

## Browser Configuration

Browser-specific settings are documented in [browser-live-view.md](browser-live-view.md). They use the `CYRENE_BROWSER_*` key namespace and are read from the encrypted config store.

Electron injects `CYRENE_AUTH_TOKEN`, `CYRENE_ELECTRON_RPC_PORT`, and
`CYRENE_ELECTRON_RPC_TOKEN` into its child runtime. These are internal
per-launch security values and should not be persisted manually.

## Model pricing and budgets

Cyrene records token usage and estimates cost for the model that actually
served each response. An explicit saved price wins, followed by the built-in
catalog in `cyrene.model_runtime.pricing`; an unknown unpriced model records
zero. User prices accept `input/output` or `input/cache-hit/output` per one
million tokens. Prefix a value with `$` for USD or `¥` for CNY; unmarked values
default to CNY.

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
