# 配置

[English](configuration.md) · [简体中文](configuration.zh-CN.md)

## 加密配置存储

Cyrene 将大部分配置保存在 Fernet 加密的 JSON Blob 中，源码模式默认位置是
`data/config.enc`。正常使用不需要 `.env`。首次启动向导和 Web UI Settings
可以写入并在运行时更新配置。

`.env.example` 只用于历史兼容和参考，新安装应使用 Onboarding 或 Settings。

## Runtime 路径与持久化

源码模式默认使用 checkout 根目录；打包模式使用操作系统 Application Data
和 Cache。测试或 Portable 部署可在首次 Import Cyrene 前覆盖：

| 变量 | 用途 |
|---|---|
| `CYRENE_BASE_DIR` | 包含 `workspace/`、`store/`、`data/` 的 Runtime 根 |
| `CYRENE_USER_DATA_DIR` | 用户 Application Data 根 |
| `CYRENE_CACHE_DIR` | Cache 根 |
| `CYRENE_TEMP_DIR` | 临时产物根 |
| `CYRENE_INSTALL_RESOURCES_DIR` | 打包/静态资源覆盖 |
| `CYRENE_ALLOWED_WORKSPACE_ROOTS` | 额外允许的项目根目录 |

主数据库是 `store/cyrene.runtime.database`。首次启动仅在新 Target 不存在或
没有行数据时迁移旧 `store/cyrene.db`，并保留 Source 作为回滚副本。

## 环境变量

多数变量也可在 Web UI 中编辑。

### LLM

| 变量 | 说明 | 默认 |
|---|---|---|
| `OPENAI_API_KEY` | OpenAI/DeepSeek/兼容 API Key | — |
| `OPENAI_BASE_URL` | API Endpoint | `https://api.deepseek.com/v1` |
| `OPENAI_MODEL` | 模型名称 | `deepseek-v4-flash` |

### Agent

| 变量 | 说明 | 默认 |
|---|---|---|
| `ASSISTANT_NAME` | 显示名称 | `Cyrene` |
| `MAX_TOOL_ROUNDS` | 每条消息最大 Tool Round | `15` |
| `MAX_HISTORY_MESSAGES` | Context Window 保留消息数 | `40` |
| `MAX_TOOL_OUTPUT_CHARS` | 发送给 LLM 的 Tool Result 字符上限 | `12000` |

### Telegram（可选）

| 变量 | 说明 |
|---|---|
| `TELEGRAM_BOT_TOKEN` | Telegram Bot Token |
| `OWNER_ID` | Telegram User ID |

### WeChat（可选）

| 变量 | 说明 |
|---|---|
| `WECHAT_BOT_TOKEN` | WeChat Bot Token |
| `WECHAT_OWNER_ID` | WeChat Owner ID |

### Embedding / Knowledge（可选）

| 变量 | 说明 |
|---|---|
| `EMBEDDING_BASE_URL` | OpenAI-compatible Embedding Endpoint |
| `EMBEDDING_API_KEY` | Embedding API Key |
| `EMBEDDING_MODEL` | Embedding Model |

未配置 Embedding 时，Knowledge 回退到 FTS/Text Search。

### Scheduler

| 变量 | 说明 | 默认 |
|---|---|---|
| `SCHEDULER_INTERVAL` | Legacy Heartbeat 秒数 | `60` |
| `HEARTBEAT_INTERVAL` | Heartbeat 秒数 | `300` |
| `HEARTBEAT_LOTTERY_INTERVAL` | 主动消息 Lottery 秒数 | `1800` |
| `DAYTIME_START` | 白天开始小时 | `6` |
| `DAYTIME_END` | 白天结束小时 | `22` |

### Steward 与 Pattern Learning

| 变量 | 说明 | 默认 |
|---|---|---|
| `STEWARD_INTERVAL` | SOUL Steward 间隔 | `3600` |
| `PATTERN_DETECTION_INTERVAL` | 行为 Pattern 扫描间隔 | `600` |
| `LOTTERY_DELTA` | Lottery 基础增量 | `0.15` |
| `LOTTERY_MAX` | Lottery 上限 | `0.85` |

### Search

| 变量 | 说明 | 默认 |
|---|---|---|
| `SEARXNG_AUTO_START` | 自动启动 SimpleXNG | `1` |
| `SEARXNG_PORT` | Listen Port | `8888` |
| `SEARXNG_HOST` | Bind Address | `127.0.0.1` |
| `SEARXNG_URL` | 外部 SearXNG URL | — |
| `SEARCH_PROXY` | Search HTTP 手动代理 | — |

Cyrene 只使用 SimpleXNG。旧 DDG/Bing/Baidu Scraper 已从主路径移除。

### Web 与 Map

| 变量 | 说明 | 默认 |
|---|---|---|
| `WEB_PORT` | Web UI Port | `4242` |
| `AMAP_API_KEY` | AMap Tile/Geocoding Key | — |

## Runtime Settings

Settings 页面可以不重启更新：

- API Key、Endpoint、Model、Telegram、WeChat、Map、Embedding；
- Model List；
- 完整 Tool Package；Direct Tool 保持固定 Wire Contract；
- Main Agent Tool Round 与 Execution Subagent Safety Fuse；
- SimpleXNG；
- MCP Server；
- `SOUL.md`。

## Browser 配置

详见 [Browser Live View](browser-live-view.zh-CN.md)。Browser Key 使用
`CYRENE_BROWSER_*` Namespace。

Electron 会给 Child Runtime 注入 `CYRENE_AUTH_TOKEN`、
`CYRENE_ELECTRON_RPC_PORT`、`CYRENE_ELECTRON_RPC_TOKEN`。这些是每次启动
生成的内部安全值，不应手工持久化。

## Model Price

明确保存的 Price 优先，否则使用内置已知 Model Price。未知且未配置的模型
Cost 记录为 0。

| Model | Input / 1M Token | Output / 1M Token |
|---|---:|---:|
| DeepSeek Chat | $0.14 | $0.28 |
| Claude Haiku 4.5 | $0.25 | $1.25 |
| Claude Sonnet 4.6 | $3.00 | $15.00 |
| Claude Opus 4.7 | $15.00 | $75.00 |
