# Configuration

## Encrypted Config Store

Cyrene stores most configuration in a Fernet-encrypted JSON config blob (`data/config.enc` by default). You do **not** need a `.env` file for normal operation. The first-run onboarding wizard writes the required values, and the Web UI Settings page can update them at runtime.

A legacy `.env.example` is still shipped for backward compatibility, but new installs should use the onboarding wizard or Settings UI.

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
| `WECHAT_OWNER_ID` | WeChat owner ID | — |

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
| `SCHEDULER_INTERVAL` | Legacy heartbeat interval in seconds | `60` |
| `HEARTBEAT_INTERVAL` | Modern heartbeat interval in seconds | `300` |
| `HEARTBEAT_LOTTERY_INTERVAL` | Proactive-message lottery interval in seconds | `1800` |
| `DAYTIME_START` | Hour considered the start of daytime | `6` |
| `DAYTIME_END` | Hour considered the end of daytime | `22` |

### Steward & Pattern Learning

| Variable | Description | Default |
|---|---|---|
| `STEWARD_INTERVAL` | Seconds between SOUL.md steward runs | `1800` |
| `PATTERN_DETECTION_INTERVAL` | Seconds between behavior-pattern scans | `600` |
| `LOTTERY_DELTA` | Base lottery probability increment | `0.15` |
| `LOTTERY_MAX` | Lottery probability cap | `0.85` |

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
- **Tools** — Enable or disable specific tools
- **Search** — Built-in SimpleXNG only
- **MCP Servers** — Add, remove, and restart MCP server connections
- **SOUL.md** — Edit the personality document directly

## Browser Configuration

Browser-specific settings are documented in [browser-live-view.md](browser-live-view.md). They use the `CYRENE_BROWSER_*` key namespace and are read from the encrypted config store.

## Model Pricing

Cyrene tracks token usage and estimates cost for the model that served each response. An explicit saved price wins; otherwise Cyrene uses the built-in price for known models, and records zero only when the model has no configured or built-in price.

| Model | Input (per 1M tokens) | Output (per 1M tokens) |
|---|---|---|
| DeepSeek Chat | $0.14 | $0.28 |
| Claude Haiku 4.5 | $0.25 | $1.25 |
| Claude Sonnet 4.6 | $3.00 | $15.00 |
| Claude Opus 4.7 | $15.00 | $75.00 |
