# 配置

[English](configuration.md) · [简体中文](configuration.zh-CN.md)

## 加密配置存储

Cyrene 将配置保存在 Fernet 加密的 JSON Blob 中，源码模式默认位置是
`data/config.enc`。首次启动向导和 Web UI Settings 可以写入并在运行时更新配置。

加密值位于 `data/config.enc`。其 Fernet Key 在可用时进入 OS Keyring；
Headless/Portable 环境没有可用 Keyring 时，会回退到权限为 `0600` 的
`data/.config_key` 并记录 Warning。该 Degraded Mode 只依靠 File Permission
保护 Key。

Portable Backup ZIP 不由 Cyrene 加密。为了让 Restore 能用目标 Installation
的 Key 重新加密，它会包含 Logical Config Snapshot（包括已配置 Credential）；
所有导出 Backup 都必须按 Secret 处理。

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

主数据库是 `store/cyrene.runtime.database`。

## 环境变量

多数变量也可在 Web UI 中编辑。

### 模型

模型不再通过环境变量配置。Settings → Models 只保存一套 canonical graph：
Provider Plugin 连接、模型 Profile，以及独立的 primary、secondary、vision、
embedding 路由。模型目录与推理操作由可编辑的 Model Plugin 提供。

### Agent

| 变量 | 说明 | 默认 |
|---|---|---|
| `ASSISTANT_NAME` | 显示名称 | `Cyrene` |
| `MAX_TOOL_OUTPUT_CHARS` | 发送给 LLM 的 Tool Result 可选字符上限（`0` 表示不设全局限制） | `0` |

### Telegram（可选）

| 变量 | 说明 |
|---|---|
| `TELEGRAM_BOT_TOKEN` | Telegram Bot Token |
| `OWNER_ID` | Telegram User ID |

### WeChat（可选）

| 变量 | 说明 |
|---|---|
| `WECHAT_BOT_TOKEN` | WeChat Bot Token |
| `WECHAT_OWNER_ID` | 可选的 WeChat Owner ID |

### Embedding / Knowledge（可选）

在 Settings → Models 中配置支持 Embedding 的 Profile，并加入 embedding 路由。
该路由为空时，Knowledge 回退到 FTS/Text Search。

### Scheduler

| 变量 | 说明 | 默认 |
|---|---|---|
| `SCHEDULER_INTERVAL` | Scheduled-task Polling 间隔（秒） | `60` |

### Steward 与 Pattern Learning

| 变量 | 说明 | 默认 |
|---|---|---|
| `STEWARD_INTERVAL` | SOUL Steward 间隔 | `3600` |
| `PATTERN_DETECTION_INTERVAL` | 行为 Pattern 扫描间隔 | `600` |

当前真正生效的 Proactive Cadence 是加密 Runtime Setting
`heartbeat_interval`，可在 Settings 中修改，默认 `1800` 秒；Scheduler 在启动
时读取它。

### Search

| 变量 | 说明 | 默认 |
|---|---|---|
| `SEARXNG_AUTO_START` | 自动启动 SimpleXNG | `1` |
| `SEARXNG_PORT` | Listen Port | `8888` |
| `SEARXNG_HOST` | Bind Address | `127.0.0.1` |
| `SEARXNG_URL` | 外部 SearXNG URL | — |
| `SEARCH_PROXY` | Search HTTP 手动代理 | — |

配置带 API Key 的官方 DeepSeek V4 模型（Endpoint 为
`https://api.deepseek.com`）后，Cyrene 会优先使用
[DeepSeek Responses API](https://api-docs.deepseek.com/zh-cn/guides/responses_api/)
提供的服务端联网搜索。搜索工作模型使用官方文档明确支持的
`deepseek-v4-flash`；未配置、调用失败或原生搜索不可用时会自动回退到
SimpleXNG。第三方 DeepSeek 兼容 Endpoint 不会启用原生搜索。旧
DDG/Bing/Baidu Scraper 仍已从主路径移除。

### Web 与 Map

| 变量 | 说明 | 默认 |
|---|---|---|
| `WEB_PORT` | Web UI Port | `4242` |
| `AMAP_API_KEY` | AMap Tile/Geocoding Key | — |

## Runtime Settings

Settings 页面可以不重启更新：

- Channel 与 Map Credential；
- Provider Plugin Connection、Model Profile 与 Role Route；
- Plugin Pack 与独立 Plugin；
- Main Agent Tool Round 与 Execution Subagent Safety Fuse；
- SimpleXNG；
- MCP Server；
- `SOUL.md`；
- Budget：Estimated Cost、CNY/USD、Billing Start Day、Adaptive Mode 和
  Warn/Block。

Plugin Center 是 Agent 组装的总控制面：插件包开关会整体加入或移除其
Application、Session、Hook、Context 与 Tool Contribution。每个非核心工具的菜单
可以编辑模型可见的名称和描述，选择“Agent 直接可见”或“Agent 寻找使用”，也可
删除用户拥有的 Source；必需 Kernel Plugin 固定保留。每个对话的 Workspace、MCP
与 Skills 选择则由输入框的 Composer Context 插件单独控制。详见
[架构说明](architecture.zh-CN.md#插件如何组成一个-agent)和
[自定义插件](project-plugins.zh-CN.md)。

### Agent 可见 Typed Settings

Main Agent 只能通过 `cyrene_application` Plugin Pack 使用
`CyreneSettingsDescribe`、`CyreneSettingsRead` 和 `CyreneSettingsUpdate`。
Namespace 为 `runtime`、`desktop`、`appearance`、`profile`、`shortcuts`。

Update 是原子 Compare-and-swap Patch，必须携带最新 `expected_revision`。旧 Revision
返回冲突并保留用户较新的修改。Shortcut Patch 保留未指定 Action，只有明确命名
Binding 的 `null` 才表示重置。Models、Secret、Secret Redaction 和
Settings Plugin 自身可用性不能通过该控制面修改。

## Browser 配置

详见 [Browser Live View](browser-live-view.zh-CN.md)。Browser Key 使用
`CYRENE_BROWSER_*` Namespace。

Electron 会给 Child Runtime 注入 `CYRENE_AUTH_TOKEN`、
`CYRENE_ELECTRON_RPC_PORT`、`CYRENE_ELECTRON_RPC_TOKEN`。这些是每次启动
生成的内部安全值，不应手工持久化。

## Model Price 与 Budget

Cyrene 按实际返回 Response 的 Model 记录 Token Usage 和估算 Cost。明确保存
的 Model Price 优先，其次使用 `cyrene.model_runtime.pricing` 内置 Catalog；
未知且未配置的 Model 记录 0。User Price 格式为每百万 Token 的
`input/output` 或 `input/cache-hit/output`；`$` 前缀表示 USD，`¥` 表示 CNY，
无前缀默认 CNY。

内置 Catalog 是代码数据，不是实时 Provider Quote。当前源码标记的核对日期为
2026-06-25；USD Provider 使用固定 `1 USD = 7.25 CNY` 换算。Catalog 覆盖
GPT 5.5、Claude Fable/Mythos 5、Gemini 3.5 Flash/3.1 Pro Preview、
DeepSeek V4 Flash/Pro、GLM 5.2、MiniMax M3、MiMo V2.5 和 Kimi K2.7 Code
Alias。依赖 Cost Estimate 前应检查 Source 和 Provider Invoice。

Budget Settings 会把 Monthly Amount 分配到 Adaptive Monthly、Weekly 和
Five-hour Window，并可 Warn 或 Block 新 Workbench Run。它是对本地估算值的
Gate，不是 Provider 侧 Quota 或 Billing Guarantee。
