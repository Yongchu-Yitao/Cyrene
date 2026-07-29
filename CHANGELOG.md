# Changelog

[中文](CHANGELOG.md) · [English](CHANGELOG.en.md)

## [0.7.0b7] - 2026-07-29

这是 `0.7.0` 的第七个测试版，包含 `v0.7.0-beta.6` 之后的全部改动。本版将
OpenAI Codex OAuth 正式接入模型配置、首次设置、Workbench 对话与任务执行，
新增会话级模型/推理强度选择器，并系统性强化 Agent 流式事件、终止语义、工具
路由、模型降级和交互式 CLI。模型供应商层改用锁定版本的 Codex SDK，用户无需
把 OAuth Token 交给 Cyrene；Codex 配额与货币预算也分别呈现。

### 功能更新

- **直接使用 OpenAI 账户中的 Codex 模型**：首次设置和“设置 → 模型”现在都可
  登录 OpenAI。登录后会自动列出当前账户能使用的 Codex 模型，无需复制 API Key，
  也无需把 OAuth Token 交给 Cyrene 保存。
- **在发送消息前选择本轮模型**：对话和任务输入框左侧都新增紧凑的模型按钮，
  点击即可浏览所有已配置模型，并为当前模型选择合适的推理强度。
- **不同对话可以使用不同模型**：在一个 Chat 或 Task 中切换模型，不会改动其他
  对话和全局默认设置。重新打开、刷新或 Fork 对话后，原来的选择仍会恢复。
- **只看到合理的推理档位**：Codex 会显示该模型实际提供的档位；普通自定义模型
  没有能力信息时只提供 Low、Medium、High，避免出现看似可选但实际无效的档位。
- **任务页的模型按钮立即出现**：进入任务后不再等待较慢的 Codex 能力查询；
  已配置模型加载完成就会显示按钮，详细推理档位随后自动补齐。
- **图片仍会交给正确的模型处理**：如果当前主模型不能看图，Cyrene 会先调用已
  配置的视觉模型分析附件，再继续当前对话，不会把图片直接发给纯文本 Codex。
- **随时查看 Codex 剩余额度**：设置页和账户菜单会显示账户实际提供的 5 小时或
  每周额度、剩余比例与重置时间。缺少的窗口不会显示，Codex 额度也不会和 API
  金额预算混在一起。
- **模型不可用时提示更清楚**：额度耗尽、登录过期或模型下线时，Workbench 会
  明确说明原因和下一步操作；如果配置了备用模型，会继续尝试备用模型，而不是
  表现为长时间无响应。
- **长回复和工具任务更顺畅**：思考与回复流的保存开销更低，取消、恢复、多个
  工具并行以及完成前收到的新指令都有更可靠的处理，减少卡住、重复卡片和回复
  提前结束。
- **思考过程更容易理解**：Workbench 会把“理解请求”和后续工具执行分开显示，
  最终回复展示完整处理时长；Codex 不再出现没有实际意义的内部详情展开区。
- **终端版更接近桌面体验**：CLI 新增本地化的模型/项目/工作区信息、输入提示和
  权限状态；会话列表更易读，设置支持左右切换分类，`Ctrl+O` 可临时查看思考详情，
  关闭后不会把大段内容留在终端滚屏中。
- **Workbench 小界面更稳定**：已显示的工作标签不会因为重复点击而乱序；记忆页
  的“相关/历史”标签在窄布局下更紧凑，长内容的对齐和留白也得到改善。

### 详细变更与兼容性说明

#### OpenAI Codex OAuth、模型发现与配额

- **锁定官方 Codex SDK 运行时** — 核心依赖新增
  `openai-codex==0.144.4`，`uv.lock` 同步锁定 Python Adapter 与各平台
  `openai-codex-cli-bin`，开发环境、普通安装和发布构建使用相同协议版本。
- **OAuth 凭据由 Codex App Server 持有** — Cyrene 通过 SDK/App Server 完成
  Login、Logout、Account、Model Discovery、Turn 和 Rate Limit 调用，不读取
  `~/.codex/auth.json`，也不直接保存 Access/Refresh Token。
- **新增完整设置 API** — `/api/settings/openai-oauth` 返回连接状态、账户和模型；
  Login/Logout Route 管理登录；独立 `/limits` Route 获取配额，避免慢速额度请求
  阻塞“已连接”和模型列表的显示。
- **首次设置支持 OpenAI 登录** — Onboarding 可以在自定义 OpenAI-compatible
  Endpoint 与 OpenAI OAuth 之间选择；保存前校验登录状态、模型可用性和所选
  Reasoning Effort，完成后写入正式模型候选和 Onboarding State。
- **Codex 只允许作为主文本模型** — Route 明确拒绝把 OAuth Candidate 放入
  Fallback、Secondary 或 Vision 列表；当前 Adapter 不宣称图像能力，避免把
  未转发的图片输入误报为可用。
- **图片附件不会错误发给文本 Codex** — 主模型具备 Vision 能力时直接发送图片；
  Codex 等纯文本主模型收到图片时，Workbench 先调用正式 Attachment Analysis
  路径并使用已配置视觉模型提取内容，再把可用结果交给当前会话。
- **自定义模型与 OAuth 模型共存** — 模型设置保留现有 OpenAI-compatible
  Candidate、Endpoint、API Key、Fallback、Secondary 和 Vision 流程，同时为
  Candidate 增加 `provider` 与 `reasoning_effort` 元数据。
- **主模型来源切换器重新整理** — 主模型区域使用紧凑 Source Menu 切换
  Custom/OpenAI OAuth；Selected、Hover、Focus 和 Escape/Click-outside 状态
  遵循既有设置页视觉语言，不再出现重复嵌套卡片。
- **模型配置布局更紧凑** — Primary 保持直接可编辑；Fallback、Secondary 和
  Vision 收入带摘要的折叠区；Save and Apply 保持在文档流中，设置面板高度、
  Responsive Label 与可访问状态保持稳定。
- **推理强度来自模型能力目录** — Low、Medium、High、Extra High 等选项不再
  使用全局硬编码全集，而是按当前 Codex 模型的
  `supportedReasoningEfforts` 过滤并保存到 Candidate。
- **配额窗口统一解析** — 300 分钟窗口显示为 5 小时额度，10080 分钟窗口显示为
  每周额度；不存在的窗口不会制造空进度条，剩余比例、进度和重置时间由设置页与
  账户菜单共享同一规范化逻辑。
- **配额读取使用 Stale-while-revalidate** — 新鲜缓存直接返回；过期但可用的额度
  先返回并后台刷新。临时查询失败不会禁用模型，但已经缓存的“额度耗尽”状态仍被
  保守执行，避免请求风暴绕过配额。
- **账户菜单保持原有结构** — Codex 摘要仅在主模型使用 `codex_oauth` 且账户已
  连接时显示，放在现有 Action 之上，不改变 Logout、Settings 等操作的间距、
  图标、圆角和 Footer。
- **Codex 配额开关独立持久化** — `codex_budget_enabled` 与货币 Budget 分开
  保存；登录时启用 Codex 配额监控，但普通 API 预算设置不受影响。

#### Codex Provider、工具路由与可恢复降级

- **Provider 改为隔离的 SDK Client** — Codex Turn 使用临时 Thread、只读
  Sandbox 和 `approvalPolicy=never`；Cyrene 的权限、工具执行和用户确认仍由
  自己的 Agent Loop 控制，不把宿主工具直接暴露给 Codex App Server。
- **对话历史按 Thread 正确重放** — System/Developer 指令与 Conversation
  Message 分开组装，不重复注入 System Prompt；并发 Session 使用各自的
  Thread/Turn Notification Queue，不会串流或互相抢 Event。
- **Transport 遵循系统代理并可提前失败** — SDK 启用系统 Proxy；连接失败、
  Provider Stop 或长期无上游信号时会中断当前 Turn 并进入候选回退，而不是一直
  等到通用请求超时。
- **Reasoning Summary 与 Usage 完整转发** — Provider 保留模型公开的思考摘要、
  Effort、Input/Output/Total Token 与 Prompt Cache 命中量，供 CLI、Workbench
  和预算/诊断路径复用。
- **宿主插件和技能不会污染 Provider** — Provider 启动时显式隔离工作目录，
  禁用宿主 Plugin、App、Browser、Computer Use、Image Generation、Shell、
  Unified Exec、Web Search、Multi-agent 与已发现 Skill，避免用户本机 Codex
  配置改变 Cyrene 内部模型行为。
- **使用结构化 Action Contract** — 模型输出通过 JSON Schema 表达可见回复与
  一个或多个工具调用；参数使用严格 `arguments_json` 校验，非法 JSON、未知
  Tool 或泄漏到正文的 Tool Markup 会被拒绝或进入安全恢复路径。
- **Phase 1 与执行阶段工具边界明确** — 初始理解阶段只暴露进入执行或结束所需的
  控制 Action；进入执行后再按 Cyrene Catalog 提供已授权工具，降低模型提前
  选择不可执行 Tool 的概率。
- **工具发现支持更自然的查询** — Capability Search 对 Browser 等详细意图改善
  排序，并在中文简称或未知词未命中时回退 Package Catalog，不再返回空发现结果。
- **Codex 错误被分类为可操作状态** — Quota Exhausted、Authentication
  Expired 和 Model Unavailable 从 SDK ErrorInfo、HTTP Context 与错误消息中
  保守识别；模糊 401/403 不再被武断归类，明确的 Model Error 优先于状态码猜测。
- **Workbench 显示模型可用性警告** — 模型失败会发布带 Provider、
  Failure Kind、翻译 Key 和 Model 参数的 Phase Event，界面显示可操作的中英文
  提示，再继续候选模型降级。
- **Cooldown 只用于真正需要冷却的错误** — 额度耗尽等持续性错误可以暂时跳过；
  认证过期、模型不可用等用户可立即修复的问题不会错误地让 Candidate 长时间
  失活。
- **停止和取消更加可靠** — Provider 停止通知、Turn Interrupt、Reader 结束、
  Pending Request 和 Notification Queue 都有明确清理；取消不会遗留后台 Turn
  或把旧通知送入下一轮。
- **模型事件携带 Provider 身份** — LLM Start/Delta/Done 与失败事件保留
  `provider`、Model、Phase 和 Usage，使 Workbench 能区别 Codex 与普通
  OpenAI-compatible 模型并采用正确展示方式。

#### Agent 流式事件、终止语义与运行恢复

- **Delta 写入改为批处理** — `reasoning_delta` 与 `reply_delta` 最多按
  50ms/128 条合并到一次 SQLite Transaction，同时保留每条 Event 的 Sequence
  和 Cursor，可重放语义不变但显著降低 Token Stream 的数据库背压。
- **终态前强制持久化** — Finalize、Interrupt、Error 和 Run Completion 会先
  Flush Pending Batch；取消中的后台写入可幂等重排，避免终态越过尚未落盘的
  Delta。
- **Reasoning Event 生命周期补齐** — Workbench 与 CLI 消费
  `reasoning_start/delta/done`；Phase 1 Reasoning 与同阶段 LLM Activity
  合并，不再生成跳动或重复的卡片。
- **执行卡片区分理解与工具阶段** — Phase 1 显示“正在理解/已理解请求”和紧凑
  Reasoning Preview；后续卡片按真实 Tool Call 数量汇总，Codex Provider 不展示
  不适用的可展开内部 Trace。
- **回复显示完整处理时长** — 最终消息使用统一 Formatter 显示亚秒、秒、分钟或
  小时级总耗时，且不把 Reasoning、Tool 或排队时间遗漏在外。
- **`quit` 成为不可逆终止信号** — 完整答案必须位于普通 Assistant Content；
  `quit` 只负责控制。当同一批次混入其他 Tool 时，其他调用全部跳过，Run 不会在
  已终止后重新进入执行。
- **终止后的回复恢复更安全** — 空回复、Placeholder 或 Tool Markup 只允许通过
  无工具 Final Reply Path 修复；已经执行过工具时不会凭空重建模型没有写出的
  答案。
- **Legacy/DSML Tool Markup 不再泄漏** — Terminal Reply 和 Workbench Message
  会压制旧式 Tool Block、DSML 标记及塞进 `quit` 参数的伪回复，只接受正常
  Assistant Content；空 Enter 也不会创建无意义 Turn。
- **晚到 Guidance 不丢失** — `quit` 或 Final Reply 正在收尾时会等待活动 Tool，
  再检查 Inbox；新 Guidance 会建立 Continuation，而不是复活已经终止的 Tool
  Batch。
- **隐藏 Session 命名任务被移除** — Chat Run 不再在后台偷偷调度标题生成；
  兼容的 Label Refresh 成为 No-op，避免额外模型调用、事件交叉和结束延迟。
- **Agent/System Prompt 收敛** — Main Agent、Subagent、Deep Reflection 与
  Runtime Guidance 对“何时使用工具、如何发送进度、如何结束”使用一致语义，
  并修复 `quit` 与 Tool Result 配对、Search 和 Stop 路径的回归。

#### Composer 模型选择与会话级偏好

- **Chat Composer 新增紧凑模型按钮** — 按钮展示友好模型名、当前推理强度和
  Chevron；Root Menu 可进入模型或推理强度子菜单，支持 Escape、
  Click-outside、ARIA Expanded 和当前项 Check。
- **Task Composer 使用同一组件语言** — 任务界面也列出全部已配置 Candidate，
  与 Chat 使用相同的菜单宽度、Row Density、Typography、Icon、交互和中英文
  文案。
- **浅色与深色状态分别校准** — 浅色 Normal 使用 `#eaf0f4` 基础色并提供更明显
  的 Hover/Active；深色 Normal 适当提亮，Hover/Active 保留层级但不出现突兀
  蓝绿色。
- **菜单密度与参考界面一致** — 菜单收紧到约 `260px`，长模型名可安全截断，
  Secondary Value 与 Chevron 对齐，按钮靠近 Composer 左侧且不挤压 Send。
- **推理档位按模型规范化** — Codex 使用当前模型声明的能力，顺序统一为 Low →
  Medium → High → Extra High → Max → Ultra；自定义模型没有能力目录时仅回退到
  Low/Medium/High，不会显示 Max/Ultra 等未经声明的高档位。
- **Task 选择器不再延迟出现** — `/api/settings/models` 返回后立即渲染按钮；
  较慢的 OAuth Capability Catalog 只做异步增强，不再阻塞整个选择器。
- **请求契约传递模型偏好** — Chat/Task Body 新增可选 `model` 与
  `reasoningEffort`，Route 校验 Candidate、规范化 Effort，并在启动 Agent 前
  写入会话偏好。
- **Runtime 按 Session 解析 Candidate** — 每个 Session 可以覆盖 Candidate ID、
  Model、Base URL 和 Reasoning Effort；未设置时仍使用全局顺序，避免选择一个
  会话模型后影响其他对话。
- **Session Payload 可恢复选择** — Chat/Task Response 返回
  `modelSelectionId` 和 `reasoningEffort`；刷新、切换和 Fork 后 Composer 能恢复
  正确状态。
- **认证失败不会误触发长期冷却** — 选择器切到需要重新登录的 Codex 模型时，
  Runtime 可以回退到下一候选，同时保留该 Candidate 供用户登录后立即重试。

#### 交互式 CLI、Workbench 标签与界面细节

- **CLI Header 提供完整上下文** — 单行 Brand Mark 下显示当前 Model、
  Project、Workspace/Git Branch 和版本，长路径按终端显示宽度裁切。
- **输入区全面本地化** — Placeholder、Bottom Toolbar、Permission Mode、
  Exit Hint、Settings Label 和 Value Preview 随 `language` 切换中英文。
- **Session 恢复列表改为两行卡片** — 第一行显示 Title 与 Project，第二行显示
  Preview，卡片之间留空行；中英文宽字符都使用显示宽度安全裁切。
- **`/config` 使用两轴导航** — Left/Right 在 General、Models、Tools、
  Connections、Data、About 间切换，Up/Down 选择当前 Tab 项，Enter 打开；
  普通字段和 CLI Preference 也使用一致的键盘选择。
- **思考详情改为临时 Viewer** — `Ctrl+O` 打开全屏、可滚动的公开 Reasoning；
  再按 Ctrl+O、Escape、Q 或 Ctrl+C 关闭并恢复 Prompt，内容不残留在 Shell
  Scrollback。
- **思考活动复用 App 话术池** — CLI 约每四秒随机切换本地化活动短语且避免连续
  重复，完成后仍保留紧凑的“思考了 Ns”摘要。
- **Ctrl+C 与 Escape 职责分离** — Ctrl+C 保持全局两次确认退出，Escape 用于
  取消当前 Modal/Editor；关闭设置或 Viewer 不会意外终止 CLI 或后台 Run。
- **Workbench 最近标签顺序更稳定** — 点击已经可见的 Session 不再重排整个
  Topbar；只有打开尚未显示的 Session 才更新最近列表，并保留最多 20 个稳定 Key。
- **Memory Compact Tab 文案补齐** — Related/History 在窄布局使用更短的中英文
  Label，Detail Hero 改进 Grid、Padding 和长内容对齐。

#### 契约、测试、版本与 beta7 发布

- **OpenAPI 契约同步模型字段** — Chat/Task 新增可选字段后重新锁定 Schema
  SHA256，Operation Count 和 FastAPI/Pydantic Generator 版本保持不变。
- **Codex Provider 回归覆盖扩大** — 测试 Login、Account、Models、Limits、
  Turn Stream、Usage、Tool Action、取消、Provider Stop、Host Isolation、
  Quota/Auth/Model Error 分类和 Cooldown。
- **模型选择回归覆盖端到端路径** — 覆盖前端菜单、Session Preference、
  Chat Fork、Task Dispatch、Route Validation、Candidate Override、Reasoning
  Effort 与异步 Capability Enrichment。
- **Agent/CLI 回归补齐** — 覆盖 Durable Delta Batch、Cursor Replay、Phase
  Activity、Total Duration、Quit Mixed Batch、Late Guidance、No-tool Reply
  Recovery、CLI Localization、Viewer、Config Navigation 和 Session Card。
- **干净 CI 环境与本地结果一致** — Python Job 在运行契约测试前构建 WebUI
  Fixture；CLI Help、临时知识数据库和本地时间测试不再依赖开发机已有设置、
  数据目录或时区，严格线程告警模式下也能稳定完成。
- **本地完整测试通过** — 项目 `.venv` 中完整 pytest 共 `1,605` 项通过；
  beta7 前端生产构建、OpenAPI 单项契约、相关 Codex/Workbench 回归和
  `git diff --check` 均通过。
- **全部版本面升级到 beta7** — Python Package/`uv.lock` 使用 `0.7.0b7`，
  Electron Package/Lock 使用 `0.7.0-beta.7`；README Badge、Docs Sidebar、
  WeChat Header、Workbench/PDF Cache Key 和版本契约测试同步更新。
- **Tag 驱动 Prerelease** — `v0.7.0-beta.7` 触发现有 Release Workflow，构建
  macOS DMG、Windows x64/ARM64 Installer 和 Linux AppImage/deb/rpm，执行
  Frozen 与真实 Desktop Smoke，并提取本节作为 GitHub Prerelease Notes。

---

## [0.7.0b6] - 2026-07-28

这是 `0.7.0` 的第六个测试版，包含 `v0.7.0-beta.5` 之后的全部改动。本版新增
交互式 CLI、Workbench 工作标签和固定资源 Shelf，增强记忆、配置与通知体验，
并修复 Linux AppImage 白屏问题。Linux 预发布现在同时提供 AppImage、
Debian `.deb` 和 Red Hat/Fedora 系 `.rpm`。

### 功能更新

- **新增交互式 CLI**：直接运行 `cyrene` 即可在终端中创建、选择和继续
  Workbench 对话，并使用附件、上下文、配置、权限确认和运行恢复等功能。
- **升级 Workbench 顶栏**：最多保留 3 个 Task/Chat 工作标签，并新增可持久化的
  固定资源 Shelf，方便跨对话使用文件、知识条目、选中文字和 Browser 页面。
- **增强记忆与配置可靠性**：改进项目记忆、实体提取、搜索范围和历史兼容；
  加密配置改用安装级本地密钥，避免开发版与安装版切换后无法解密。
- **完善通知与界面体验**：通知可返回对应 Project、Chat、Task 或资源；
  同时补充键盘操作、本地化、模型回退提示和后台页面节能处理，并修复记忆详情
  长文本撑宽面板的问题。
- **修复 Linux AppImage 白屏**：Linux 默认使用更兼容的软件渲染，并增加窗口
  故障日志与真实界面烟测。
- **新增 Linux 系统安装包**：预发布同时提供 AppImage、Debian `.deb` 和
  Red Hat/Fedora 系 `.rpm`。

### 技术细节

#### 与 Workbench 共享的正式交互式 CLI

- **裸 `cyrene` 成为推荐终端入口** — 未带子命令时会发现健康 Daemon，必要时
  后台启动并等待服务就绪，然后进入交互式 Chat；`cyrene chat` 提供等价的显式
  入口。原 `start/status/stop/do/session` 等管理命令继续兼容。
- **复用正式 Workbench Conversation** — CLI 不建立第三套 Agent Loop，也不
  使用孤立的临时 Session。它可以创建、列出、选择和继续持久 Chat，显示所属
  Project，并与 Web/Electron 看到相同的消息、任务、记忆和 Run 状态。
- **新增按 Run 隔离的 NDJSON 流** — Workbench Chat Route 向当前客户端流式
  发送公开 `run_start`、Phase、Tool、Plan、Reasoning、Reply、Pending
  Question、Finalize、Interrupt 和 Error Event。CLI 不依赖会被多客户端竞争
  消费的全局 SSE Queue，也不会混入其他会话事件。
- **断线与恢复使用持久 Cursor** — `cyrene chat --chat <id> --resume
  --cursor <n>` 可以从事件序号继续当前或最近 Run；`--list` 列出可恢复对话，
  非交互调用也能明确选择 Chat，而不是表面接受任意 Session、实际固定落到
  `run_live`。
- **实时终端渲染完整 Agent 进度** — Rich 行式 UI 显示随机且不连续重复的
  `✶ ✸ ✹ ✺ ✷ ◌` 活动符号、当前阶段、Tool Start/Progress/Finish、Plan 与
  Step 状态、流式回复和最终总耗时。它保留 Shell Scrollback，不引入全屏 TUI。
- **模型思考默认紧凑展示** — Reasoning 默认折叠为“思考了 Ns”，`Ctrl+O`
  可以展开或再次折叠本轮公开思考内容；`/config` 的 CLI Preferences 可持久化
  `thinking=compact|expanded`，且不会输出隐藏推理、Credential 或未脱敏参数。
- **权限与问题在终端内闭环** — 收到 Pending Question 或 Permission Choice
  时暂停动态状态、显示选项或文本输入，并通过正式 Answer Route 继续同一运行。
  CLI 不自动批准；非交互模式遇到确认会返回明确的机器可读失败。
- **附件与输入草稿可管理** — `/attach`、`/attachments` 和 `/detach` 使用
  Workbench Attachment Contract 排队、查看和移除文件，发送后由同一 Chat
  Run 消费；文件错误、大小限制和服务端拒绝会显示为可操作错误。
- **新增完整会话命令面** — `/new`、`/resume`、`/mode`、`/status`、
  `/deep-reflect`、`/deep-research`、`/context`、`/config`、`/mcp`、
  `/help` 与 `/exit` 均由正式交互客户端处理；新的对话式 CLI 使用 `/new`
  建立独立 Chat，不继承 Legacy REPL 的 `/clear` 语义。
- **`/context` 对齐 App Context Card** — 读取同一份 Context Composition 与
  Context Blocks，展示消息 Token、彩色占比条，以及 System Prefix、
  Ephemeral Injection 和 Conversation Message 分组；User、Assistant、
  Tool 与系统注入保持一致缩进和语义颜色。
- **`/config` 覆盖常用管理面** — 可以查看或更新 Backend Settings、Model、
  Capability Package/Tool、Key、SOUL、Integration、MCP、Skill、Remote、
  Profile、Budget、Data 与 CLI Preferences，而不要求另开浏览器完成基础配置。
- **终端交互边界更可靠** — Prompt Toolkit 提供异步输入、历史、补全、方向键
  选择和 `Alt/Esc+Enter` 多行输入。第一次 `Ctrl+C` 只提示确认，两秒内再次
  按下才退出；退出 CLI 不会误杀 Daemon 持有的后台 Run。
- **支持自动化与管道** — `cyrene chat --json <text>` 逐行输出稳定公共 Event，
  非 TTY 模式不启动 ANSI Live Renderer，NDJSON Decoder 可处理拆包、多行同包
  和无结尾换行，适合脚本、日志采集与 CI。
- **Electron 与 CLI 复用同一 Backend** — Electron 启动后在独立临时目录发布
  当前 URL、Token、Electron PID 与 Backend PID。Unix 文件权限固定为 `0600`，
  写入使用临时文件原子替换并在所属进程退出时清理，CLI 不再启动第二个会争用
  Runtime DB、Scheduler 或端口的服务。
- **本地认证自动衔接** — CLI 对独立 Daemon 读取显式
  `CYRENE_AUTH_TOKEN`，对 Electron 读取上述本地 Connection Capability，
  所有请求继续发送 `X-Cyrene-Token`；认证失败会说明连接方式，而不是只返回
  模糊 HTTP Error。
- **新依赖进入正式锁定环境** — `prompt-toolkit>=3.0.52` 与 `rich>=15.0.0`
  加入核心依赖和 `uv.lock`，确保普通安装、PyInstaller 与开发环境获得相同的
  CLI，而不是运行时静默降级。

#### Workbench 顶栏工作集与固定资源

- **Breadcrumb 替换为最多 3 个实时 Work Tab** — 手动打开、新建或切换
  Task/Chat 会更新 MRU；Tab 混合显示 Task 与 Conversation，刷新 Chat 列表时
  保持当前选择和最近顺序同步。
- **Tab 状态可固定也可移出顶栏** — Context Menu 支持置顶/取消置顶、复制标题、
  查看 Chat Browser/File 资源和移除。移除只影响顶栏集合，不删除 Chat、
  不停止 Agent Run，也不修改底层 Task。
- **新增独立 Pinned Resource Shelf** — Shelf 位于 Session Tabs 与 Search
  之间，可接收 Chat File Card、Knowledge/Library Row 或 Card、macOS 原生
  选中文字，以及浮动/最小化 Electron Browser。
- **固定项保持紧凑但可访问** — File/Browser 默认仅显示 SVG，Hover 或键盘
  Focus 才展开名称；空 Shelf 的 `+` 提供 Hover Hint，Search 收紧到 `168px`，
  Shelf 与右侧 Action 保持 `10px` 安全间距。
- **Selected Text 和 Knowledge 可固化为 Markdown** — 无 Attachment 的
  Knowledge Item 与原生选中文字会写成可持久文件；新 Export 使用 ASCII
  Storage Key，同时 Route 继续解析旧版 Unicode Export Name。
- **资源可以投递到其他 Chat Draft** — File/Text 拖到另一个 Conversation Tab
  会进入目标 Composer 的 Attachment Draft，不会自动发送，也不会改变源资源。
- **Browser 可跨 Conversation 复制页面** — PiP、favicon 最小化按钮或固定
  Browser 拖到另一 Chat 后，目标 Session 的独立 `BrowserTabManager` 新建同
  URL 页面。两边共享登录 Partition，但不共享 DOM、Navigation 或控制权。
- **PiP 固定命中不再依赖原生 View DOM** — Electron `WebContentsView` 覆盖
  页面时 `elementFromPoint` 不稳定，现改为直接检测 Shelf Rectangle；
  Body-level Drag Proxy 可以越过标题栏和对话裁切边界。
- **最小化 Browser 改为 favicon 圆形按钮** — favicon 缺失或加载失败时立即
  回退 Browser SVG；点击恢复，拖过阈值后可以移动、固定或投递到其他 Chat，
  并继续复用 PiP 的消息避让逻辑。
- **固定 File 进入后续 Agent Context** — Registry 只注入紧凑的全局用户资源
  索引，正文按需读取，不把全部文件内容常驻 Prompt。
- **固定 Browser 有明确 Owner 权限** — Owner Session 保留完整控制；其他
  Session 即使知道 Resource ID，也在 Tool Execution Layer 只能调用
  Snapshot/Screenshot，不能导航、点击、输入、刷新、上传或静默夺取页面。
- **资源 Registry 正式持久化** — Upsert 去重、删除、Library Source Metadata、
  Selected-text Materialization、Global File Context 和 Browser Read-only
  Resolution 均进入 Workbench Document Store 与专门 API。
- **完整键盘控制** — Focus 后用方向键和 Home/End 遍历 Session/Resource，
  Enter/Space 打开，Delete/Backspace 移除；`Cmd/Ctrl+1…3` 直达 Work Tab，
  `Ctrl+Tab` / `Ctrl+Shift+Tab` 循环，`Cmd/Ctrl+W` 移出当前 Tab，Project
  Shortcut 调整为 `Cmd/Ctrl+Shift+1`。

#### 记忆捕获、Entity 与配置数据可靠性

- **Workbench Run 结束时携带可验证证据** — Memory Capture 接收当前
  Session/Chat、用户语言和 Verified Tool Evidence；只纳入本轮成功结果，
  排除失败、陈旧或未完成调用，减少把错误 Tool Output 固化成长期事实的风险。
- **项目记忆遵循用户语言** — `SaveProjectMemory` Contract 明确要求目标语言，
  英文占优的混合文本会按用户设置规范化；中性 Path/Identifier 不做无意义翻译。
- **默认 Project Scope 不再混同 Global Short-term Memory** — Workspace
  Resolver 区分默认项目、显式 Workspace 与全局存储，避免默认项目事实写入或
  查询到错误作用域。
- **Memory Search 更准确且有界** — 多关键词使用 OR 召回，过滤 Stale Item，
  对大结果集限制数量和字符；对外 Search Payload 不暴露内部 History 字段，
  Workbench Payload 也隐藏内部 Task Report。
- **Citation 与变更历史兼容回填** — 序列化为新旧 Memory Item 补充 Citation、
  Created/Updated History；旧记录可从 Timestamp 回填，不要求破坏性迁移。
- **Steward 同时读取 Legacy 与 Workbench Archive** — 后台记忆整理扫描默认
  Workspace 与各 Project 的最近 Session Markdown，按文件数、单文件字符和
  总字符设置边界；按修改时间判断新内容并跳过旧式每日文件的重复读取。
- **Foreground Entity Extraction 优先** — Agent Prompt 和 Entity Store 在
  当前 Turn 主动提取确定实体，Steward 作为后台补偿；Stored Entity 更新补充
  Source、Confidence 与重复合并测试，降低只依赖定时后台扫描造成的遗漏。
- **Capture 调度接口可扩展** — `schedule_capture` 接受附加关键字参数，使
  Route 可以传递 Evidence、Language 和 Session Metadata，同时保持旧调用兼容。
- **配置密钥不再因进程身份变化被误判** — OS Keyring 与开发/安装进程身份可能
  不一致，曾导致共享 `DATA_DIR` 的有效 `config.enc` 被当作损坏。现在使用与
  配置同目录、权限为 `0600` 的 Installation-local Fernet Key，并用独占创建
  解决首次启动竞态。
- **加密配置失败时保留现场** — 已有 `config.enc` 缺 Key、Key 格式无效或
  `InvalidToken` 时明确失败，绝不生成替代 Key、绝不从陈旧 Legacy Backup
  覆盖现有数据。Portable Backup 继续导出逻辑 Snapshot，并在目标安装重新加密。
- **移除不再使用的 Keyring 打包负担** — Python Dependency、`uv.lock` 与
  PyInstaller Collection 不再包含 `keyring`，Headless Linux 也不会在启动或
  Smoke Test 时打印无可用 Secret Service 的长 Traceback。

#### 通知、Agent 响应与界面细节

- **通知可以回到准确上下文** — Notification Row/Action 保存 Project、Chat、
  Task 或相关 Resource 信息；点击后切换对应 Workspace/Session，而不是只标记
  已读。Workspace Display Name 通过统一 Helper 生成。
- **通知交互与无障碍补齐** — 新增中英文 Action Translation、Hover/Focus
  样式、可点击状态与键盘路径，通知项在高对比和不同字号下保持清晰。
- **Agent 等待优先使用 Inbox Wakeup** — Prompt 与 Subagent Monitoring 避免
  固定两秒 Sleep/Busy Poll；有新 Guidance、Question 或 Completion 时通过
  正式事件唤醒，降低空转和延迟。
- **Learning Skill 保持 Progressive Disclosure** — Learned Skill 不自动塞入
  Router；Capability Package 与 Tool Metadata 不污染 Model Context，
  Catalog Snapshot 继续冻结当轮可用边界。
- **后台 Renderer 保持节能** — Electron Browser/Quick Chat 等后台页面使用
  适当的 `backgroundThrottling`，避免隐藏窗口持续以前台刷新频率消耗资源。
- **记忆详情不再被长文本撑宽** — Detail、Tab、Metadata、正文、Citation 和
  Footer Button 补齐 `min-width: 0`、横向 Overflow 隔离与任意位置换行；
  长 URL、Path、连续 Identifier 和放大字号不会再产生横向滚动或裁切操作按钮。
- **Model Fallback Progress 本地化** — Workbench 在模型回退期间显示明确的
  中英文进度，不把可恢复切换表现为无响应。
- **Project Rail 英文按钮更紧凑** — “New project” 收敛为 “New”，在窄 Rail
  与大字号下减少挤压；README 的 Current Limitations Link 恢复到正式文档索引。

#### Electron Browser View 与 Linux 白屏修复

- **修复原生 Browser 从 PiP 恢复后 Viewport 未收敛** — Electron 35 可能接受
  Hidden `WebContentsView.setBounds()`，却不向 Chromium Layout Viewport
  发送 Resize。结果会把 PiP 尺寸页面包进全屏 Shell，并在过渡中暴露白面。
- **尺寸切换现在主动验证** — 每次 Transition 读取 `window.innerWidth/Height`
  与目标 Bounds 比对；首次未命中时执行 1px Geometry Pulse、Invalidate 和
  有界重试，View 重新 Attach 后再次校验，最终失败会写明确 Warning。
- **Bitmap Proxy 延长到真实 Frame 就绪** — 原生 View 在最终 Viewport
  `capturePage` 完成前继续由 Renderer Bitmap 遮挡，不再因为 Native
  Compositor 提前显示而闪出白色中间帧。
- **Linux 默认关闭硬件加速** — AppImage 纯白窗口的主因是 Chromium GPU
  Compositor 在部分 Wayland/Mesa、虚拟 GPU 和旧驱动组合下启动但不输出有效
  Surface。Linux 现在在 `app.ready` 前调用软件渲染回退；确认驱动正常时可用
  `CYRENE_ENABLE_HARDWARE_ACCELERATION=1` 显式恢复。
- **主窗口失败不再静默变白** — `did-fail-load`、`render-process-gone` 和
  `unresponsive` 写入 `cyrene_error.log`；Cache Clear 与 `loadURL` 改为严格
  Await，失败显示可定位日志路径的 Window Error。
- **新增桌面界面烟测模式** — `--desktop-smoke-test` 使用隔离 Electron Profile，
  等待 React Root 真正挂载和 Launch Screen 移除，再 `capturePage` 检查至少
  100 个非白像素；空 Root、永久 Launch Screen、空截图或纯白 Surface 都以
  非零状态阻止发布。
- **Release 在真实 AppImage Runtime 下测试** — Linux CI 通过 `xvfb-run` 和
  `--appimage-extract-and-run` 启动最终 AppImage，不再只运行内部 PyInstaller
  Binary 的 Import Smoke。由于临时解包目录无法保留 Root-owned SUID Sandbox，
  仅该隔离 CI 烟测使用 `--no-sandbox`；正式 AppImage 的正常启动参数不变。

#### Linux 安装包与发布链路

- **保留通用 AppImage** — x64 AppImage 继续作为无需安装的便携包，适合不同
  Linux Distribution；使用说明补充执行权限和软件渲染开关。
- **正式发布 Debian Package** — `electron-builder` 原本已经生成 `.deb`，
  但 Artifact Step 只上传 AppImage，导致 Release 丢包。本版把 `.deb` 纳入
  强制 Artifact Match 和最终 Prerelease Assets。
- **新增 RPM Package** — Linux Target 增加 x64 `rpm`，覆盖 Fedora、RHEL、
  CentOS Stream、Rocky Linux 与 AlmaLinux 的常规包管理安装路径。
- **三种 Linux 产物统一发布** — Workflow 使用 `linux-packages` Artifact 同时
  携带 `Cyrene-*-x64.AppImage`、`.deb` 和 `.rpm`；任一目标缺失都会因
  `if-no-files-found: error` 或 Release Gate 失败，而不是悄悄发布不完整版本。
- **中英文安装文档同步** — 分别给出 `chmod +x`、`apt install ./...deb` 与
  `dnf install ./...rpm` 的可复制命令，并解释 Linux Software Renderer 默认值。

#### 契约、测试、文档与 beta6 发布

- **CLI 回归覆盖完整协议面** — 覆盖 NDJSON 拆包、认证错误、Parser、单次与交互
  模式、Chat/Project 选择、Run Cursor 恢复、附件、Pending Question、
  Ctrl+C/Ctrl+O、Spinner、Context、Config 与裸 `cyrene` 自动启动。
- **Topbar/Resource 权限回归** — 覆盖 MRU 合并与上限、Pin/Remove、Context
  Menu、跨 Chat Draft、Browser Copy、键盘控制、持久 File Context、Browser
  Owner Read-only Boundary、去重、Library Source 和 Selected-text Markdown。
- **Memory/Config 回归扩充** — 覆盖 Verified Evidence、Language Normalization、
  Scope、Search Bound、Citation/History、Workbench Archive、Foreground
  Entity、0600 Local Key、Missing Key、Invalid Token 与并发首次创建。
- **Linux Packaging Contract 固化** — 新测试锁定 AppImage/deb/rpm Target、
  Artifact 路径、真实 AppImage UI Smoke、软件渲染开关和 Renderer Diagnostic。
- **设计与使用文档完整更新** — Architecture、Usage、Development、Browser
  Live View、Limitations、Project Progress、CLI Handoff、Topbar Handoff 和
  Design QA 中英文内容同步当前实现，并保留交互原型与视觉对比图作为审计材料。
- **本地发布前门禁通过** — 完整 pytest 共 `1,540` 项通过；Electron 主进程
  `node --check`、`44` 项 App Use Node Test、beta5 以来变更 Python 文件的
  Ruff、Workflow YAML 解析和 `git diff --check` 通过。桌面烟测实际挂载
  Workbench、移除 Launch Screen，并捕获 `2,063,466` 个非白像素后正常退出。
- **全部版本面升级到 beta6** — Python Package/`uv.lock` 使用 `0.7.0b6`，
  Electron Package/Lock 使用 `0.7.0-beta.6`；README Badge、Docs Sidebar、
  WeChat Header、Workbench/PDF Cache Key 和版本契约测试同步更新。
- **Tag 驱动 Prerelease** — `v0.7.0-beta.6` 继续触发现有 Release Workflow，
  构建 macOS DMG、Windows x64/ARM64 Installer 和 Linux
  AppImage/deb/rpm，执行 Frozen 与真实 Desktop Smoke，并提取本节作为
  GitHub Prerelease Notes。

---

## [0.7.0b5] - 2026-07-27

这是 `0.7.0` 的第五个测试版，完整包含 `v0.7.0-beta.4` 之后的全部改动。
本版重新设计 Cyrene-to-Cyrene 的 Agent 远程控制主路径：控制端 Agent 不再
默认通过“创建远程对话 → 输入自然语言 → 启动第二个 Agent”间接操作目标设备，
而是直接发现、描述并调用被控端明确授权的 Harness 工具包。每次实际调用都在
控制端当前对话中按精确设备、Project、Capability 和参数完成本地审批；被控端
仍执行独立的信任、Project Scope、工具包 Grant、Schema、幂等与审计校验。

同时，本版修复远程 `202 Accepted` 被误判为失败、运行状态高频轮询、远程创建
对话无法实时出现在被控电脑列表、默认兼容运行权限导致审批停滞、监听端口冲突
导致整套远程功能不可用，以及 Workbench“添加上下文”菜单无法点击外部关闭和
显示设备指纹等问题。

本次 beta5 重发进一步修复了安装版真实会话中暴露出的直接 Harness 授权误判：
设备列表向 Agent 返回 `toolpack:<wire_name>` Grant，而初版控制器只接受裸
`<wire_name>`，重复拼接前缀后把已经保存并同步的授权错误报告为未授权。重发版
同时将兼容能力改为协议层始终开启、将工具包选择改为可持久保存的复选框，并
修正远程设置列表圆角裁切和“浏览器工具”命名。

### 直接远程 Harness：新的首选控制路径

- **新增 `RemoteHarness` Agent Tool** — 控制端可以对当前对话明确选择的配对
  设备执行 `discover`、`describe` 和 `invoke`。它复用 Cyrene 现有 Progressive
  Tool Gateway 与稳定 Capability ID，不创建远程 Chat、不启动第二个 Agent，
  也不需要把用户请求重新输入远端对话。
- **Agent 默认优先直接调用** — Main Agent Prompt 明确要求普通远程工作先使用
  `remote.harness`：读取设备收到的工具包 Grant，发现相关 Capability，描述
  精确 Schema 后调用。`RemoteCyreneAction` 与 `RunRemoteCyrene` 只在用户明确
  需要远程对话，或目标端不支持直接 Harness 时作为兼容回退。
- **控制端执行精确本地审批** — `invoke` 在发送跨设备命令前调用当前控制端
  对话的 Permission Resolver；审批元数据绑定 Device ID、Project ID、
  Tool Package、Capability ID 和完整参数。`default` 模式可由用户批准，
  `auto` 模式由本地 Reviewer 判断，发现和描述操作保持只读。
- **被控端仍保留最终验证边界** — 目标端只接受固定的
  `harness.discover`、`harness.describe`、`harness.invoke` Command；必须先通过
  配对身份、签名与 E2EE Envelope、方向性 Grant 和共享 Project Scope。随后
  目标端再次校验工具包、Capability 所属关系、输入 Schema、本机启用状态和
  Runtime 可用性。
- **调用绑定目标 Project Workspace** — Remote Command Executor 由 Composition
  Root 注入 Bot 与 Runtime DB，并为每次调用建立独立 `remote_harness` Run
  Context、稳定 Session/Call ID、目标 Project Workspace 和不可泄漏到后续
  回合的 Catalog Snapshot。绑定在 `finally` 中复位，避免权限或 Workspace
  Context 污染其他会话。
- **没有任意执行后门** — 协议不接受任意 HTTP Method/URL、Python 函数、
  数据库语句、原始 Shell RPC 或隐藏 Concrete Tool Name；只能调用目标端正式
  Catalog 中属于已授权工具包的稳定 Capability ID。`remote_tools` 本身不可
  远程授权，避免设备间递归控制链。
- **结构化结果与错误保留** — 目标 Harness 的 JSON Result 会保留
  `status`、`capability_id` 和结果正文；协议区分 Unsupported Pack、Grant
  Denied、Project Missing、Schema/Capability Error、Transport Error 和
  Timeout，控制端不再需要从远端自然语言回复猜测真实执行状态。
- **工具包 Grant 名称兼容归一化** — `RemoteHarness` 同时接受 Catalog 使用的
  裸 Wire Name（如 `browser_tools`）和设备列表返回的完整 Grant（如
  `toolpack:browser_tools`），在控制端授权检查和跨设备 Payload 发送前统一为
  裸 Wire Name。已授权工具包不再因重复生成
  `toolpack:toolpack:<wire_name>` 而被误判为拒绝。

### 按设备授权的远程工具包开关

- **兼容能力改为协议层始终开启** — Chat、Run、Task、Approval 和 Artifact
  等固定兼容命令不再显示独立开关，也不能被普通设置请求关闭。Pairing、
  Grant Update、Received Grant Sync 和历史 Peer Migration 都会强制合并
  完整兼容能力集合，避免控制链因误关基础命令而失效。
- **工具包改为紧凑复选框列表** — 配对邀请和每台可信设备的授权编辑器使用与
  原兼容能力一致的双列 Checkbox；去掉占用大量高度的 Field Row Toggle，
  保留本地化名称、Accessible Label 和 Hover Description。
- **修复圆角边界裁切** — 工具包 Grid 增加安全内边距，位于左上角的“代码工具”
  和左下角的“技能工具”选择框不再被滚动容器的圆角与 `overflow` 裁切。
- **浏览器工具命名统一** — 设置页和工具包授权页的“浏览器自动化工具”统一简化
  为“浏览器工具”，英文同步为 “Browser tools”。
- **授权按可信设备独立保存** — 工具包 Grant 使用稳定
  `toolpack:<wire_name>` 标识并进入既有签名 Pairing Bundle、方向性 Peer
  Grant、加密 Grant Sync 和审计流程。修改某台设备不会扩大其他控制端权限。
- **配对默认工具包真正持久化** — `remote_settings` 新增可迁移的
  `default_tool_packs_json`；配对页勾选工具包后立即串行写入设置，关闭再打开
  不会复位。快速连续点击使用稳定 Ref/函数式状态更新，不会因 React 闭包读取
  旧数组而丢失选择。
- **直接工具包仍不静默扩权** — 新的直接工具包默认关闭，用户必须显式开启；
  升级已有 Peer 时只补齐始终开启的兼容能力，不会自动获得任何
  `toolpack:*` Harness Grant。
- **支持十二类可选工具包** — Code、Browser、Desktop、Memory、Knowledge、
  Task、Entity、Map、Subagent、Delivery、Skill 和 Integration 可以分别授权。
  即使远程 Grant 已开启，本机“设置 → 能力”关闭的包仍不会被 Harness 执行。
- **发现与执行双层过滤** — 未授权工具包不能通过目标端 Discovery 暴露其
  Capability，也不能通过已知 ID 直接 Invoke；控制端 Tool 在发送前和被控端
  Executor 在执行前都会检查 Grant。
- **中英文权限文案完整** — 新增兼容能力、直接工具包、授权说明和 Accessible
  Toggle Label 的中英文翻译；Tool Trace 也能把 `remote.harness` 映射回
  `RemoteHarness` 的本地化名称。

### 远程运行、审批与实时状态可靠性

- **修复 `202 Accepted` 成功响应误判** — Remote Adapter 过去把所有 FastAPI
  `JSONResponse` 强制标记为 `ok:false`，导致已经返回有效 `run_id` 的远程
  `chats.send` 被审计为失败。现在按 HTTP `2xx` 判定成功，同时尊重 Payload
  中显式的 `ok:false`。
- **新增事件驱动 `runs.wait`** — 控制端可以带 Cursor 和有界 Timeout 等待
  Run 的下一个公开事件。被控端订阅 `ChatRun.subscribers`，先检查 Backlog，
  再等待 Queue，并在退出时移除订阅，替代连续调用 `runs.events` 的忙轮询。
- **兼容远程对话默认使用 `auto`** — `RunRemoteCyrene` 和远程
  `chats.send` 兼容路径现在允许 `auto/default/plan` 并默认选择 `auto`，
  避免安装版默认模式在远端产生无人能够完成的 Approval Loop。
- **审批不再成为远程控制主循环** — 普通操作的批准发生在控制端
  `RemoteHarness` 精确 Invoke 前；控制端无需创建远程 Chat、读取 Pending
  Question，再通过另一次需要审批的 `approvals.respond` 去“审批一个审批”。
- **远程 Agent 回退仍可监督** — 必须使用旧路径时仍可读取 Run、发送 Guidance、
  Interrupt、回答问题和下载 Attachment/Artifact；Agent Prompt 明确要求优先
  使用 `runs.wait`，只把 `runs.events` 留作即时增量读取。

### 被控电脑对话列表实时同步

- **新增 `workbench_chat_changed` SSE Event** — 创建 Chat、开始 Run 和 Run
  Settle 时发布 Project/Chat-scoped Event；事件进入正式前端 Event Allowlist。
- **远程创建对话立即可见** — 被控电脑 Workbench 订阅事件后按 Project 过滤，
  使用 `80ms` Debounce 刷新列表。远程 Chat 不再必须手动刷新页面才能出现。
- **完成、等待与错误状态会收敛** — Run 最终 `finally` 在持久状态 Settle 后
  发布更新，使列表中的 Running/Idle、时间和 Preview 回到真实后端状态。
- **不会抢走当前对话焦点** — 后台远程 Chat 只刷新列表，不自动 Select 新项；
  用户正在查看或输入的对话不会被远程控制流程切走。
- **订阅生命周期完整清理** — 页面卸载时同时取消 SSE Listener 和待执行的
  Refresh Timer，防止页面切换后的幽灵刷新。

### LAN 监听端口冲突自动恢复

- **默认端口占用不再禁用远程控制** — `37841` 无法 Bind 时，Listener 会在
  `37841..37940` 的有界区间内轮转选择可用端口，仅对
  `EADDRINUSE` 类冲突回退；其他 Socket Error 仍立即暴露。
- **实际端口持久保存** — `remote_settings` 新增可迁移的 `listen_port`，
  Runtime 保存成功绑定端口并在后续启动优先复用；端口范围继续限制为
  `1024..65535`。
- **配对页展示真实地址** — Settings API 和 Local Pairing Address 使用
  Runtime 实际监听端口；连接状态在发生回退时显示明确的中英文提示和端口号，
  不再宣称固定监听 `37841`。
- **已配对设备自动发现新端口** — LAN Delivery 对保存地址先正常投递；若保存
  端口属于 Cyrene 回退区间且不可达，则以短 Connect Timeout 有界扫描其余端口，
  只接受返回 `202` 且 `accepted:true` 的真实 Cyrene Envelope Endpoint。
- **发现成功后修正持久地址** — 命中新端口会更新 Peer LAN Address，后续请求
  直接使用正确端口，不会每次重复扫描。
- **Grant 与 Response 同步监听端口** — 加密 Grant Update 和 Command Response
  携带发送端当前 Listener Port；Peer 验证 Envelope 后更新保存地址，使正常
  双向通信主动收敛，而不是只能依赖失败后的探测。
- **IPv4/IPv6 地址重写安全** — 更新 Peer Port 时分别保留普通 Host 和方括号
  IPv6 Host，不通过未校验字符串拼接改变目标地址边界。

### Workbench 远程上下文交互与隐私

- **“添加上下文”菜单支持点击外部关闭** — Composer 为 Popover Anchor 增加
  Ref，并只在菜单打开期间注册 `pointerdown` Listener；点击菜单外部会关闭，
  卸载或关闭时立即移除 Listener。
- **菜单内部操作不被误关** — `contains(event.target)` 保证设备、Persona 和
  Workspace 选项仍可正常点击，不会在 Toggle 生效前被全局 Listener 截断。
- **远程设备列表不再显示指纹** — Context Picker 只显示设备名和已授予能力
  数量；Fingerprint 继续保留在可信设备管理和安全验证界面，不再占用日常
  Composer 菜单空间或无必要暴露。

### 契约、测试、文档与发布

- **Tool Registry 合同更新** — `RemoteHarness` 注册到 Native Module、
  Main-only Tool Set、Resource Metadata、Progressive `remote_tools` Binding
  和 i18n Alias；锁定的 Registry Count 与 SHA-256 Contract 同步更新。
- **远程安全回归扩充** — 覆盖工具包 Grant 正规化、拒绝递归
  `toolpack:remote_tools`、未授权包双端拒绝、目标 Project/Workspace Context、
  Full-access 单次绑定与复位、控制端只对 Invoke 审批、Discovery 不申请审批、
  完整/裸工具包名兼容、兼容能力强制保留、默认工具包设置持久化、`202` 成功
  语义、Event Wait、监听端口迁移/回退/发现/同步和真实双 Gateway 往返。
- **Workbench 回归扩充** — 覆盖直接工具包设置、复选框与 i18n、回退端口
  状态文案、复选框持久化与圆角安全内边距、浏览器工具命名、远程 Chat Event
  Allowlist/刷新、Context Picker 外部关闭和设备指纹移除。
- **设计文档更新到直接 Harness 架构** — 远程控制 Handoff 记录首选调用链、
  本地审批边界、工具包授权、兼容回退、Event Wait 和实时列表语义。
- **本地 beta5 发布门禁通过** — 使用 `uv sync --locked --all-extras` 的锁定
  环境完成 Python `compileall`，并以未处理线程警告提升为 Error 的配置运行
  `1,474` 项 pytest；同时重建全部 `32` 个 WebUI JSX Entry、验证生成资源与
  Frontend Source 一致、通过 `44` 项 Electron App Use Node Test、本次变更
  Python 文件的 Ruff 检查和 `git diff --check`。
- **全部版本面升级到 beta5** — Python Package、Electron Package/Lock、
  README Badge、Docs Sidebar、WeChat Channel Header、Workbench/PDF Cache
  Key、`uv.lock` 和版本契约测试统一为 `0.7.0b5` /
  `0.7.0-beta.5`。
- **Tag 驱动预发布** — `v0.7.0-beta.5` 继续触发现有 Release Workflow：
  构建 macOS DMG、Windows x64/ARM64 Installer 和 Linux AppImage，执行 Frozen
  Smoke，并提取本节作为 GitHub Prerelease Notes。

---

## [0.7.0b4] - 2026-07-27

这是 `0.7.0` 的第四个测试版，完整包含 `v0.7.0-beta.3` 之后的全部改动。
本版重点把“连接另一台 Cyrene”从可用的远程命令能力推进为可持续运行的完整
远端 Agent 工作流：远程状态迁出主运行数据库，远端 Agent 可以一键创建对话并
开始工作，Artifact 与 Chat Attachment 可以无总大小上限地分块传输并显示实时
进度。同时修复 Workbench 对话中断竞态，完成记忆、日程和知识库的中英文适配，
并修正知识库结果区与记忆来源卡片在不同语言和大字号下的布局。

### 独立远程控制数据库与升级迁移

- **远程状态迁入独立 SQLite Sidecar** — Pairing、Peer、Grant、Replay Nonce、
  Command Idempotency 和 Audit Event 不再与高频 Workbench Run Event 共用主
  Runtime 数据库，而是写入 `<runtime-db>.remote-control`。Sidecar 使用 WAL、
  `30s` Busy Timeout 和独立连接锁，避免主运行流持有写事务时阻塞远程命令的
  审计、加密投递或响应处理。
- **旧数据一次性兼容迁移** — 首次打开 beta4 时会检测主库中的历史远程表，
  只复制目标库实际存在的共同列，并按表使用 `INSERT OR IGNORE/REPLACE`。
  `remote_store_migrations` 保存 `split_remote_control_store_v1` 标记，保证迁移
  可重入且只执行一次；旧表暂不删除，便于测试版回滚。
- **设备身份保持稳定** — Device Identity 仍从原逻辑数据库路径派生，升级前后
  Device ID、Fingerprint 和已有信任关系不会因为数据文件拆分而改变。
- **默认授权安全升级** — 新的默认 Remote Capability 加入
  `approval:respond`，让远端 Agent 的 Pending Question 可以完成闭环。升级器
  只在现有 Grant 精确等于 beta3 旧默认集合时补入该能力；用户手动收窄或定制
  过的授权不会被静默扩权。
- **审计区分完成与失败来源** — Remote Gateway 记录 Command 完成和失败，
  Tool Error 统一携带稳定 `code`、`error_origin` 与 `retryable`。控制端数据库
  Busy、控制端权限、传输不可达、超时和远端领域错误不再被压缩成同一个模糊
  Error String。
- **本地测试数据库配套更新** — `.gitignore` 新增根目录
  `*.remote-control`、WAL 和 SHM 忽略规则，回归 Fixture 与现有测试数据库同步
  到新的 Sidecar 存储约定。

### 完整远端 Agent 工作流

- **新增 `RunRemoteCyrene` Agent Tool** — 对当前对话明确选择的可信设备执行
  一次受监督操作：先在共享 Project 中创建 Remote Chat，再发送用户级任务启动
  被控端 Agent，并返回 `chat_id`、`run_id`、Cursor、状态和幂等信息。
- **远端仍运行自己的完整 Harness** — 被控端 Agent 可以使用该设备本地已经
  安装并授权的模型、工具、Skill、Browser、Computer Use、文件和集成；控制端
  不会获得任意 HTTP、Shell 或底层 Tool 旁路，也不会绕过远端 Sandbox、审批、
  Credential 和 Permission Mode。
- **远程运行模式保持最小权限** — `RunRemoteCyrene` 只接受 `default` 或
  `plan`，禁止跨设备请求 `auto/full_access`。创建和发送分别使用派生的稳定
  Idempotency Key，重试不会重复创建对话或重复启动同一任务。
- **远程操作描述更明确** — `RemoteCyreneAction` 现在完整列出 Chat、Run、
  Task 和 Approval 的 Typed Payload；文案明确建议通过创建 Chat、发送指令、
  跟踪 `runs.events`、补充 `runs.guide` 和回答 Pending Question 来使用远端
  Cyrene，而不是尝试调用任意命令。
- **Tool Catalog 与 Progressive Package 完整注册** — 新 Tool 加入 Native
  Module、Catalog、Main-only Set、Resource Key 和 `remote_control`
  Capability Binding，继续遵循“只有当前对话显式选择远端设备才披露能力”的
  Progressive Tool 原则。
- **设计与操作文档同步扩充** — Remote Control 设计文档补充 Sidecar 数据库、
  四个 Remote Agent Tool、完整远端 Agent 使用语义、权限边界、分块协议、
  Approval Loop、Attachment/Artifact 下载以及最新 Route/OpenAPI 契约数量。

### 无总大小上限的 Artifact 与 Attachment 分块传输

- **移除完整文件 10 MiB 上限** — `artifacts.read` 改为 Offset-based Chunk
  Protocol：默认每块 `512 KiB`，远端单块最大 `1 MiB`，但完整文件总大小不再
 受限制。每次响应返回 `offset`、`chunk_size`、`next_offset`、`size`、
  `eof`、`progress` 和 Base64 Chunk。
- **新增 `attachments.read` Remote Command** — 可以读取目标 Chat 消息中
  明确引用的 Attachment，保留 Filename、Media Type、Kind、Width、Height 和
  Size 等元数据，Capability 继续复用 `artifact:read`。
- **新增 Control API Attachment 下载端点** —
  `GET /v1/control/chats/{chat_id}/attachments/{attachment_id}` 返回真实文件，
  Chat Detail 同时为每个有效 Attachment 暴露 `download_url`。OpenAPI、
  Operation List、Schema 和 Route Structure Contract 已同步更新。
- **附件读取绑定对话引用** — Attachment ID 必须出现在目标 Chat Transcript
  中。Cyrene 托管 Upload/Export 路径继续限制在受管根目录；对话明确引用的本机
  绝对文件也可传输，但不能借该接口探测或读取未被 Chat 引用的任意路径。
- **控制端自动流式组装** — `RemoteCyreneStatus` 对 Artifact/Attachment Read
  自动循环拉取连续 Chunk，验证 Offset 单调前进，在本机
  `remote_transfers` 临时目录组装，完成后注册为标准 Generated Attachment。
  临时 `.part` 和中间文件在成功或失败后都会清理。
- **Base64 不进入 Agent 上下文** — Tool 最终只返回本地 Attachment 描述、
  Filename 和 Size，不把每块 Base64 或整个文件内容交给模型，降低上下文成本
  并避免大文件破坏 Agent 回合。
- **实时传输进度** — Tool Executor 新增 `tool_call_progress` Event，携带当前
  Bytes、总 Bytes、比例和文件名。Workbench Trace Card 显示 Accent Progress
  Bar 与百分比，并在后续 Lifecycle Event 合并时保留已经解析出的 Tool 名称。

### Workbench 对话可靠性与上下文菜单

- **修复中断后的“仍在回复”竞态** — `/api/chat/interrupt` 在响应前等待
  Workbench Chat 的持久状态完成 `running → idle`，前端则在服务端接受中断后
  才 Detach Event Stream，避免重新同步读回尚未落盘的旧状态。
- **中断状态立即一致** — Runtime 新增专用 `onInterrupted` 回调，立即清理当前
  Chat 的 Live Runtime 并刷新列表。会话信息只以真实 Runtime 判断“回复中”，
  不再让残留的 `chat.status === "running"` 永久污染 UI。
- **中断失败可以被看见** — Model Interrupt 现在校验 HTTP Status；Runtime
  捕获错误并发送现有 Error Feedback，随后仍执行安全的 Stream Cleanup。
- **Tool Lifecycle 合并保留 richer identity** — 非终态更新不再用空文本覆盖
  已解析出的 Tool Name；Progress、Started 和 Finished Event 可以稳定合并为
  同一 Trace Entry。
- **修复添加上下文菜单窄窗口裁切** — Context Chips Row 成为定位容器，
  Popover Anchor 不再固定到最右侧“添加”按钮；菜单从整行左侧定位，并同时限制
  `min-width`、`width` 和 `max-width`，在窄窗口和长中英文标签下保持可见。

### 记忆、日程与知识库 i18n

- **记忆界面完整接入 Workbench i18n** — 页面标题、分类、来源、统计、搜索、
  排序、空状态、详情、引用、关联、历史、编辑 Modal、删除确认和相对时间均使用
  统一 Translation Key；英文日期和相对时间使用自然的本地化格式。
- **记忆来源卡片重排** — 圆环图改为居中显示，图例使用占满卡片宽度的
  Dot/Label/Percentage 三列布局。中文、英文、窄侧栏和放大 UI 字号下都不会再
  出现百分比重叠、英文逐字断行或中英文标签被挤成竖排。
- **日程日期完成中英文格式切换** — 日、月、区间、全天事件和事件详情会根据
  Workbench Language 使用中文或 `en-US` Month/Weekday 格式，页面挂载时订阅
  i18n 变化并即时刷新。
- **知识库实际入口完成 i18n** — Workbench 当前路由使用
  `workbench-library.jsx`，beta4 直接在这个真实入口接入翻译，而不是只修改未被
  当前路由采用的备用 Knowledge Page。侧栏、Toolbar、筛选、排序、表格、卡片、
  Empty State、Batch Action、Metadata、Notes 和 Tags 的核心文案均可切换。
- **修复知识库结果区空白** — 文案替换期间被错误嵌套的 Header、Add Menu、
  Sort Menu 和 Batch Action JSX 层级已恢复；Toolbar、Result Table/Card、
  Workspace 和 Right Detail Panel 重新成为正确的兄弟节点，已有条目正常渲染。
- **知识库类型与文件类型统一翻译** — Bibliography Type、File Type、
  Reading Status、Untitled Fallback、Author Overflow、Column Header、
  Attachment/Abstract/Note/Tag 元数据均使用一致的 `library.*` Key。

### 设置、Subagent 与兼容性修正

- **高级 Subagent Guardrail 不再暴露为普通设置表单** — Agents Settings
  移除 Execution Safety、Discussion Limit 等内部资源熔断输入项，避免用户把
  实现级安全阈值误认为日常可调 Agent 行为；现有配置兼容性不受影响。
- **成本熔断单位修正为人民币** — Execution Worker Prompt 显示 `¥`，实际
  Estimated USD Cost 按 `7.25` 换算后再与人民币上限比较，修正此前 UI 单位和
  执行判定不一致的问题。
- **版本与缓存键统一到 beta4** — Python Package、Electron Package/Lock、
  README Badge、Docs Sidebar、WeChat Agent Header、WebUI/PDF Asset Cache
  Key、`uv.lock` 和对应测试全部同步为 `0.7.0b4` /
  `0.7.0-beta.4`。
- **README 当前限制独立成文档** — 中英文 README 只保留精简入口，完整的单用户
  安全边界、模型与数据要求、API 生命周期、未实现功能、Windows 源码限制和
  Release/Manual Gate 分别迁入 `docs/limitations.md` 与
  `docs/limitations.zh-CN.md`，便于后续独立维护。

### 测试与发布门禁

- **远程数据库回归** — 覆盖旧库迁移、Migration Marker、默认 Grant 精确升级、
  主 Runtime DB 持写锁时远程命令仍可完成、Command Audit 与错误来源分类。
- **远程 Agent 回归** — 双 Gateway 场景验证 `RunRemoteCyrene` 创建 Chat、
  启动 Agent、传递 Permission/Language、保持幂等并返回可供后续状态查询的
  Run Metadata。
- **文件传输回归** — 覆盖 Control Attachment 下载、只读 Chat 引用文件、
  超过 10 MiB 的外部引用文件、首尾 Chunk、连续 Offset、控制端组装、本地
  Attachment 注册、Base64 隔离和实时 Progress Event。
- **Workbench 契约回归** — 覆盖中断等待服务端、持久状态归零、Trace Progress、
  Tool Identity 合并、上下文菜单边界、记忆/日程/知识库 i18n、知识库组件层级
  和记忆来源卡片布局。
- **本地 beta4 发布门禁通过** — 锁定 Python 环境完整执行 `1,466` 项 pytest，
  未处理 Thread Warning 提升为 Error；同时通过 `44` 项 Electron App Use
  Node Test、32 个 WebUI JSX Entry 重建、Python `compileall`、版本一致性和
  `git diff --check`。
- **发布工作流保持不变** — `v0.7.0-beta.4` Tag 将触发现有 Release Workflow，
  为 macOS、Windows x64/ARM64 和 Linux 构建 PyInstaller + Electron 安装包，
  执行 Frozen Smoke，并把本节 Changelog 作为 GitHub Prerelease Notes。

---

## [0.7.0b3] - 2026-07-27

这是 `0.7.0` 的第三个测试版，集中完成 `v0.7.0-beta.2` 之后的 Cyrene
设备直连收尾：Tailscale 地址正式进入直接配对范围，配对完成后的可信关系可以
跨重启复用，“连接”设置页改为自动保存，并对短密钥复制、连接事件、错误提示、
中英文文案和视觉层级进行了完整整理。

### Tailscale 直连与地址安全边界

- **Tailscale IPv4 地址可以直接配对** — 直接地址校验明确允许
  `100.64.0.0/10` 共享地址空间，因此该网段内的 Tailnet 地址不再被误判为
  公网地址。未填写端口时仍自动使用 Cyrene LAN Listener 默认端口 `37841`。
- **放行范围保持最小化** — 本次只把 Tailscale 使用的
  `100.64.0.0/10` 加入既有 Loopback、Private 和 Link-local Allowlist；
  `100.63.255.255`、`100.128.0.1`、普通公网地址、URL 形式、非法端口和超出
  `1024..65535` 的端口仍被拒绝，避免把 Pairing API 扩展为任意网络请求入口。
- **Tailscale 仍使用完整 Cyrene 安全协议** — 地址放行只决定是否允许发起
  TCP/HTTP 直连，不会绕过一次性短密钥、Ed25519 Device Identity、X25519
  密钥交换、ChaCha20-Poly1305 E2EE、Capability、Project Scope、Nonce、
  Timestamp、Replay Protection、Revocation 或 Audit。
- **识别远端旧版本拒绝** — 当本机已通过 Tailscale 到达另一台 Cyrene，但远端
  仍以 beta2 的 Local-network 校验拒绝 Pairing Completion 时，控制端返回稳定
  `remote_pairing_peer_update_required` 错误码。Workbench 会明确提示用户更新
  并重启远端 Cyrene、重新生成短密钥，不再把远端 `409` 原样显示成误导性的
  英文“本机地址不受支持”错误。
- **双端升级要求更加明确** — Tailscale Pairing Completion 会让被控端保存
  控制端的 Tailnet 来源地址，所以控制端和被控端都需要 beta3 或更新版本。
  如果旧被控端已经领取过短密钥但在 Completion 阶段失败，该短密钥不得复用，
  更新远端后需要重新生成。

### 可信设备持久化与直接复用

- **配对成功即自动加入可信设备** — 不再需要额外“保存”或二次确认。成功完成
  双向公钥证明后，Device ID、Display Name、Signing/Exchange Public Key、
  Fingerprint、LAN/Tailscale Address、双向 Capability 和 Project Scope 会
  原子写入 `remote_peers`。
- **下次使用无需再次输入短密钥** — 短密钥只承担第一次建立信任的职责；后续
  Agent 从对话“添加上下文”选择该设备时，Remote Gateway 会直接读取持久化的
  Peer Identity、Grant 和地址发送 E2EE Command。只有设备被撤销、身份改变或
  重新配对时才需要新的短密钥。
- **可信关系跨 Cyrene 重启保留** — 新增数据库重开回归测试，分别重新创建控制端
  和被控端 `RemoteControlStore`，验证双方仍能读取对方设备、已保存地址、
  `chat:read`/`chat:send` Capability 与 Project Scope，而不是只在当前进程
  内存中可用。
- **真实直连往返继续覆盖** — 本机双实例测试仍通过两个隔离 SQLite 数据库、
  两个真实 Listener 和双向 Remote Gateway 完成短密钥配对，发送
  `chats.send`，在被控端执行并从反向直连返回加密响应；持久化断言在网络往返
  完成并关闭 Listener 后执行。

### “连接”设置页自动保存与配对交互

- **移除“保存并应用”按钮** — 远程访问开关改为立即保存，设备名称在输入停止
  `600ms` 后自动保存，并在失焦时立即 Flush 尚未提交的草稿。用户不再需要猜测
  修改是否已经生效。
- **自动保存请求按顺序串行化** — 快速输入、立即切换开关或前一个请求失败时，
  保存队列仍保持提交顺序；Version Guard 会忽略过期响应，防止旧 Response
  覆盖较新的本地草稿。只有最新操作控制 Busy State 和错误反馈。
- **成功保存保持安静，失败清晰可见** — 普通自动保存不会持续生成大面积成功
  Banner；失败通过共享 Feedback Service 显示非阻塞 Error Toast，并保留后端
  的稳定错误文本。
- **短密钥整块点击即可复制** — 配对短密钥本身是具有 Accessible Label 的
  Button。Electron 优先调用 Preload 暴露的原生 Clipboard，普通浏览器使用
  Async Clipboard API，不支持时退回隐藏 Textarea 与 `execCommand("copy")`；
  成功和失败都会显示明确 Toast，不再静默失败。
- **配对成功提示说明后续行为** — 成功文案现在明确指出设备已自动进入“可信设备”，
  下次无需再次输入短密钥，可以直接添加到对话上下文并使用授予的能力。

### 连接事件、i18n 与视觉整理

- **“远程审计”改名为“连接事件”** — 设置页以用户可理解的事件流呈现网关启动/
  停止、设置更新、短密钥领取、邀请接受、配对完成、授权同步、设备撤销、
  Command 发送/完成和 Envelope 拒绝，不再直接暴露内部 Snake Case Event
  Name。
- **事件名称和结果完整双语化** — 为当前 16 类 Remote Event 和 17 类 Outcome
  增加英文与简体中文文案；未知值仍会安全转换为可读标题。空 Outcome 使用
  “已记录/Recorded”，不再显示孤立圆点。
- **时间使用本机 Locale** — ISO 8601 UTC 时间转换为系统本地日期时间；非法或
  缺失时间保留可诊断的安全回退。Command 和 Peer Device ID 继续作为辅助信息
  展示。
- **状态列真正居中** — 左侧绿色/红色 Outcome 使用独立固定列、Flex 水平与垂直
  居中、自动换行和统一 Line Height，长状态不会再贴边、截断或上下漂移。
- **事件列表降低视觉噪声** — Event Title、Timestamp 和 Outcome 分别收紧到
  `12px`、`10px` 和 `9.5px`，降低字重、行高、Padding、Gap 和列宽，使连接
  记录回到辅助信息层级，不再压过“可信设备”和配对主流程。
- **移除设置页底部的大块粉色通知** — Invitation、Copy、Pairing、Grant、
  Revocation 和错误反馈统一复用 Workbench Shared Toast；旧 Sticky
  `remote-notice` 样式与渲染节点已删除。
- **前端契约测试同步加强** — 自动保存 Debounce/Blur Flush、无保存按钮、
  Electron Clipboard、Accessible Label、Toast、Event/Outcome i18n、本地时间、
  状态居中和旧通知移除均纳入 Source-level Regression Contract。

### Workbench 对话重命名

- **对话重命名不再调用浏览器原生 Prompt** — Conversation Rail 的菜单动作改为
  Workbench 原生 Modal，与现有主题、圆角、Button、Focus Ring 和明暗模式保持
  一致，不再出现样式突兀、无法控制或被 Electron 阻断的系统输入框。
- **输入校验与保存状态完整** — Dialog 自动带入当前标题并全选，标题限制为
  60 个字符；空标题、只包含空格、未发生变化或正在保存时禁用提交。提交前统一
  Trim，避免持久化不可见空格。
- **键盘和无障碍行为补齐** — Modal 提供 `role="dialog"`、
  `aria-modal="true"`、关联标题和 Label；支持 Escape 与点击 Scrim 关闭，
  保存期间禁止误关闭，关闭按钮有独立 Accessible Name。
- **错误与成功反馈遵循 Workbench 规范** — API 错误保留在 Dialog 内并使用
  Alert 语义，用户继续输入时自动清除；成功后使用 Shared Toast 提示并关闭
  Modal，不再让错误从 `window.prompt` 调用链静默丢失。
- **重命名持久化增加后端回归测试** — `PATCH /api/workbench/chats/{chat_id}`
  会保存 Trim 后标题、更新 `updatedAt` 并写回 Workbench Chat Store；Frontend
  Contract 同时锁定不再出现 `window.prompt`。

### 兼容性与验证

- **不改变 Control API 和远程领域命令** — beta3 没有扩大任意 HTTP、Shell、
  Tool 或远程桌面权限；现有 23 个 Control API Operation、22 个固定 Remote
  Command、显式 Capability/Project Grant 和 Agent Context Selection 继续
  保持 beta2 契约。
- **局域网地址继续兼容** — `127.0.0.1`、RFC 1918 IPv4、Link-local 和既有
  IPv6 Local Address 行为不变。Tailscale IPv6 使用的 Unique-local Address
  仍通过既有 Private IPv6 规则。
- **专项回归覆盖** — 新增 Tailscale Allowlist 边界、网段内地址、
  邻接地址拒绝、可信设备跨重启持久化以及完整设置页交互契约测试；beta3
  本地发布门禁通过完整 `1,456` 项 pytest、`44` 项 Electron
  Node Test、32 个 WebUI JSX Entry 重建、Python `compileall`、版本一致性与
  `git diff --check`。各平台安装包与 Frozen Smoke 继续由 beta3 Tag 触发的
  GitHub Release Workflow 验证。
- **Windows ARM64 附件发布更加可靠** — 实验性 ARM64 安装包的后置上传任务
  现在显式绑定当前 GitHub 仓库，不再依赖任务目录中存在 Git Checkout；
  主平台 Release 创建完成后可以稳定附加 ARM64 产物。

---

## [0.7.0b2] - 2026-07-27

这是 `0.7.0` 的第二个测试版，完整包含 `v0.7.0-beta.1` 之后的终端唤醒、
Subagent 执行模型、安全权限收口、端到端远程设备控制、运行时/路由/Workbench
重构、数据库迁移、CI 与发布链路优化、主题色自定义，以及新的默认源码启动入口。

### 远程 Cyrene、设备配对与控制 API

- **新增端到端远程 Cyrene 控制协议** — 每台设备生成独立 Device Identity，
  使用 Ed25519 签名、X25519 密钥交换和 ChaCha20-Poly1305 加密；Envelope
  绑定 Sender、Recipient、Message ID、Timestamp 与 Nonce，校验时限制时间窗、
  拒绝重放、验证签名并对 Header 做认证加密。Relay 只负责转发密文，不能读取
  Command Payload。
- **支持局域网短码配对与 WSS Relay 配对** — 设置页可以生成限时短码、选择授权
  Capability 与 Project Scope，并通过直接地址或 Relay 完成双向 Trust 建立。
  Pairing Payload、Identity、Peer、Grant、Revocation、Nonce 和 Audit Event
  均持久化；密钥优先写入 OS Keyring，不可用时使用受保护的本地存储。
- **授权模型同时限制 Capability、Project 与方向** — 读取 Capability、项目、
  Chat、Run、Task、Approval 和 Artifact 的每条远程 Command 都映射到明确权限；
  Project-scoped Command 缺少 Project ID 时 Fail Closed。授权更新定期同步，
  Revocation 即时传播，Side-effect Command 必须携带 Idempotency Key。
- **新增完整远程 Command Surface** — 支持列出项目，列出/创建/读取 Chat，
  发送消息，读取 Run 与游标化 Event，Guide/Interrupt 运行，列出/创建/读取/
  Dispatch Task，批准 Plan、执行 Step、Pause/Resume/Cancel Task，响应
  Approval，以及列出和读取 Artifact。所有跨设备操作复用本机 Workbench
  领域服务和权限边界，不另造旁路执行器。
- **新增外部 Control API** — `/api/control` 暴露版本化、结构化的 Project、
  Chat、Run、Task、Approval 和 Artifact 接口，包含严格 Request/Response
  Schema、Run Event Cursor、Project Ownership 校验、Task State Transition
  校验、Artifact Path/Size 限制和稳定错误响应，可供受信任客户端与远程命令层
  共同调用。
- **新增 Remote Agent Tools 与 Progressive Package** — Agent 可以显式查看
  已配对设备、查询连接状态，并对用户选择的设备执行 Remote Action；Tool
  Catalog、Package Disclosure、Runtime Support 与 Metadata 都纳入现有渐进式
  工具体系，避免默认把远程能力暴露给所有运行。
- **新增可独立运行的 `cyrene-relay`** — WebSocket Relay 提供签名注册、在线
  路由、Delivery Receipt、连接恢复、Size Limit 和 Backoff；Windows/macOS/
  Linux Source Install 均可通过同一 Console Script 启动测试 Relay。
- **Workbench 设置页新增“连接”管理面板** — 可查看本机 Device ID、Fingerprint、
  Local Address、Relay 状态、已配对设备、双向 Grant、Project Scope、最近在线
  时间和 Audit Log，并可更新授权或撤销设备。Chat Composer 支持选择一个或多个
  Remote Device，选择结果按 Chat 持久化并在 Fork/Delete/Reload 中保持一致。

### 持久运行记录、远程恢复与可移植数据

- **Workbench Run Event 改为 SQLite 持久化** — Run Metadata 和带 Sequence 的
  Event History 保留七天；重连可以按 Run ID 或 Chat ID 恢复并从 Cursor 续读，
  已完成运行仍可回放。进程重启会把未终止 Run 标记为
  `process_restarted`，补写 Durable Error Event，而不是让前端永远等待。
- **运行中的 Chat 可以接受 Guidance** — Composer 在 Agent 运行时保持可编辑；
  空输入仍显示 Interrupt，非空输入改为 Guide 当前 Run。Control API 与远程
  Command 使用同一 Inbox/Run ID 语义，避免重发原始用户消息。
- **Attachment、Knowledge 和 Learned Skill 路径支持安装位置迁移** —
  Backup/Restore 或 App Data Root 改变后，会在新的 Managed Upload、Export、
  Library 和 Learned Script Root 下安全重定位旧绝对路径；解析必须保持在受管
  根目录内，删除与去重不会越界处理任意文件。
- **Backup/Config Store 恢复更稳健** — 补齐 Keyring 不可用、Encryption Key
  Fallback、旧配置迁移、数据库与附件恢复、学习脚本引用和跨平台路径格式测试，
  同时保留损坏配置的可诊断错误与安全回退。
- **Chat 删除采用 Optimistic UI 并可完整回滚** — 删除中的 Active Chat、
  Fork Source 和 Selection 会立即更新；请求失败时恢复原顺序、Fork Metadata
  与 Active State，避免出现幽灵分支或丢失当前对话。

### 外观、搜索与设置体验

- **主题色支持任意颜色** — 保留八个紧凑预设，并新增单一透明自定义入口；
  自定义取色器支持 Saturation/Value 面板、纵向 Hue Color Strip、HEX 输入、
  系统 Color Input、Current/New Preview、恢复默认、取消和应用。
- **主题色选中态重新设计** — 使用白色勾选、单层 Accent Ring 与克制 Halo；
  色块尺寸和间距避免相邻覆盖，自定义入口与预设圆点同尺寸且垂直居中，Popover、
  HEX 与 Color Preview 均压缩为紧凑布局，并移除浏览器原生黑色 Focus/Range
  Border。
- **Settings 与 Search Overlay 完成独立样式收口** — 新增共享 Search Overlay
  Asset Contract，设置页补齐 Remote、Theme、快捷键和长表单的中英文文案、
  Focus State、窄窗口布局与错误保留；Committed WebUI Output 与 Source Build
  继续由测试锁定。

### Agent、Subagent 与长任务

- **长时间 Shell 任务可以在退出当前 Agent 回合后自动续跑** —
  `StartShell(wake_on_exit=true)` 会持久化受监控进程、Chat、Project 和 Run
  关联；进程退出后把 Exit Code 与截断后的 Terminal Tail 组成新的 Workbench
  Chat Turn。若原 Chat 正忙，唤醒会先进入 Pending Queue，等当前 Run 完成后
  再派发，避免并发覆盖现有回复。
- **Subagent 明确区分 Execution 与 Discussion 两种模式** — Execution Worker
  以成功条件、证据和完成状态驱动，不再错误继承主 Agent 的普通 Tool Round
  上限；Discussion Agent 使用独立的 Round、每 Agent Message、总 Message、
  Message Length、Tool Call、Wall Time 和信息增益预算，防止讨论无限循环。
- **Execution Worker 增加 Lease、Checkpoint 和多层 Safety Fuse** —
  Checkpoint 会重新核验成功条件；连续无进展会以 `incomplete` 保留部分结果；
  Tool Call、Wall Time、Cost 和 Context 上限触发可解释的资源耗尽收尾。
  Agent Reactivation 会续租当前执行，但保留整个生命周期累计指标。
- **Subagent 状态和通信更可靠** — 重复 Active Agent ID 会被拒绝而不是覆盖；
  `done`、`timeout`、`incomplete` 和取消结果具有显式语义；Discussion State
  按 Discussion ID 隔离，跨 Agent 共享管理预算；Parent Monitor 到达安全
  Deadline 会终止仍在运行的 Worker，而不会无限等待。
- **直接进度消息成为稳定 Direct Tool** — `send_message` 不再依赖 Delivery
  Tool Package 是否开启，主 Agent 在非平凡工具任务开始和重要里程碑后都能
  发送简洁进度。Tool Lifecycle 补齐 Started/Finished/Failed/Cancelled 配对，
  Progressive Gateway 的 `discover`、`describe` 和实际执行都不会留下悬空
  Activity Card。
- **完整记忆清单成为 Agent 能力** — 新增 `memory.list`，可以返回跨 Session
  和当前 Project 的完整 Memory Inventory 与准确数量，同时保留 Recall、
  Search、Save 和 Retire 的职责边界。

### 权限、安全与可审计性

- **自动审批不再扩大成整轮全权限** — 自动审查通过后只生成绑定 Tool、
  Operation、Permission Kind、Canonical Path、Command/External Arguments
  和 Reason 的一次性 Fingerprint；Grant 只能消费一次，不能被另一条路径、
  命令、MCP 调用或并发 Tool Task 复用。
- **Shell 与外部 MCP 调用按真实风险提权** — Auto Mode 会审查 Process
  Execution；Default Mode 允许保持在 Workspace 内的简单命令，但包含外部
  Working Directory、绝对外部路径、Command Substitution、Opaque Shell/
  Interpreter 或 Network Executable 的命令必须显式批准。未知 MCP Tool
  Fail Closed，已批准调用也只放行精确参数。
- **Permission Decision 全量持久化并显示在 Workbench** — 新增
  `permission_decisions` 表，记录 Session、Round、来源、Tool、Operation、
  Path、批准结果、Rationale 和 Fingerprint；前端 Activity Timeline 显示批准
  或拒绝及其 Scope，字符串 `"false"` 不再被误判为批准。
- **Chat Permission Mode 在 Retry、Fork 和恢复中保持一致** — Mode 经过
  Allowlist 规范化，非法值 Fail Closed；Retry/Fork Replay 使用已持久化 Mode，
  不会悄悄退回更宽松权限。Fork 继续保留 Message Prefix、Attachment 和
  State Boundary，失败 Retry 在新回复成功前不会删除旧的 Public Reply。

### 性能、调度与 Workbench 工作流

- **Scheduler 拆分高频 Due-task Poll 与低频维护** — Scheduled Task、
  Proactive Heartbeat、Behavior Learning、SOUL Steward 和 Short-term Cleanup
  使用独立 Cadence 与 Single-instance/Coalescing 约束；重型维护不再挂在每次
  Due-task Poll 上。Steward 默认和最小间隔提高到一小时。
- **Behavior Learning 合并空闲期工作** — 正常 Server Scheduler 存在时不再
  每个 Agent Turn 额外创建 LLM Job；无 Scheduler 的运行只保留一个延迟任务，
  多次完成会合并。仅含单个 Tool 的无信息 Turn 会跳过 Learning LLM。
- **Token Usage 与 Latency 写入合并，Workspace Diff 避免重复读盘** —
  Usage/Latency 可以共享一次 Database Batch；Run Finalization 复用
  mtime/ctime/size 未变化的 Snapshot。并发 Run 的 Change Set 标记
  `exclusive` 或 `overlapping` 并记录重叠 Run ID，避免把共享改动错误归因给
  单个 Agent。
- **Electron Browser 输入改用可信原生事件路径** — 新增独立
  `browser-input.js`，兼容 React Controlled Input 的 Native Setter，并覆盖
  Type/Keyboard Event、Session Tab 隔离、共享 Login State、Closed-tab
  恢复防护和 User-event Learning Telemetry；后台 Renderer 明确节流。
- **Workbench Chat 的恢复与状态呈现更完整** — Tool Activity 原位完成，
  Finalizing 在 Workspace Save 前显示；SSE 断开后通过显式 Reconnect 恢复而
  不会重发用户消息；Retry 只在 Durable Terminal Event 后截断；LLM Activity、
  Plan Step、Inbox、Browser Trace 与 Tool 参数预览保持独立且支持中英文。
- **Workbench 交互细节补齐** — 包括上次 User Message Retry、手动 Context
  Compaction、项目/用户覆盖 Workspace Chip、长路径 Picker、全局和可自定义
  Shortcut、Clipboard/Paste File、Drag-and-drop、窄窗口 Rail、原生 Linux
  Frame/Directory Picker、Settings 表单错误保留，以及 Knowledge Tag、
  Markdown Chunk、Memory Citation/History/Related 和 Skill Learning 状态。
- **文献库修复筛选、批量删除和媒体行为** — Project Isolation、CRUD/Stats、
  Trash/Permanent Delete、General Knowledge File-type Filter、Existing
  Knowledge Bridge、Source Abstract 修复、Zotero 幂等同步、Raw Media Inline
  与 Unique Read Event 均有专门回归覆盖；Library Frontend 会在 Filter 改变
  时清理无效 Selection。

### 架构、兼容性与数据

- **所有 HTTP API 适配器集中到 `src/route/`** — Agent、Workbench、Settings、
  Task、Knowledge、Memory、Learning、Map、Channel、System 和 Code Route
  通过 Registry 统一装配；领域 Service 不再反向依赖 FastAPI/WebUI。
- **核心源码按领域完成重组** — `src/cyrene/` 现在以 `agent/`、
  `workbench/`、`model_runtime/`、`learning/`、`runtime/`、
  `observability/`、`knowledge/`、`channels/`、`tooling/` 和
  `tool_impl/` 为正式所有权边界。`call_llm`、`browser`、`subagent`、
  `memory` 和 `tools` 保持稳定公共入口。
- **旧 Python Import 仍解析到同一个 Canonical Module Object** —
  `runtime/module_compat.py` 使用 Lazy Import Finder 保留 Monkeypatch、
  Module Metadata 和 `python -m` Alias 语义；PyInstaller Smoke Test 会导入
  全部历史 Alias 并验证对象 Identity。
- **旧数据库在首次启动时安全迁移** — 仅当新
  `store/cyrene.runtime.database` 未承载数据时，使用 SQLite Backup API
  复制旧 `store/cyrene.db`（含 WAL 一致快照），执行 `quick_check`、写入幂等
  Migration Marker 并原子启用；旧库保留作回滚，已有新库绝不覆盖。
- **Web、CLI、Electron、PyInstaller 与 Daemon 共用生命周期** —
  `RuntimeContext`、Application Bootstrap、External Service、Scheduler、
  Update Check 和 Shielded Shutdown 统一管理。Electron 仍通过物理
  `src/cyrene/local_cli.py` 启动，并会自动切换到 Checkout 的 `.venv`。
- **源码默认启动命令简化为 `uv run python -m cyrene`** — 无参数模块入口现在
  默认启动唯一正式 Workbench；`--workbench` 保留兼容，Telegram 改为显式
  `--telegram`。`cyrene start` 的后台子进程同步采用新入口。

### 单一 Workbench、构建与文档

- **WebUI 收敛为单一 Workbench Source Tree** — 正式源码全部位于
  `src/webui/frontend/`，按 Entry、Platform、Shared 与 Workbench Feature
  分层；构建输出统一写入 `src/webui/static/app`。旧 Classic UI、重复
  `workbench-webui`、Legacy Selector、重复 Vendor Asset 和无效 Preload API
  已删除。
- **共享前端基础设施统一** — Bootstrap Readiness、Navigation、SSE Event、
  API/Data Store、Theme、i18n、Markdown/Math/Highlight、Diff、PDF、Search、
  Feedback 和 Browser View 不再由多个 UI Surface 各自维护；Electron 永远
  加载同一个 Workbench，并根据 Python 输出的动态端口连接。
- **常规 CI 与 Release Gate 分离** — Pull Request、`main` Push 和手工 CI
  使用锁定的 All-extras Environment 执行 Python Compile、完整 pytest、
  WebUI Build/Committed-output Diff 和 Electron App Use Test；Release
  Workflow 继续负责 macOS、Windows x64/ARM64、Linux 与 Frozen Smoke。
- **Release Workflow 缩短主发布关键路径** — Python Pip 与 Node npm 依赖按
  Lockfile 缓存，Electron/WebUI 统一使用 `npm ci --prefer-offline`；上传
  Installer/AppImage/DMG 时关闭二次压缩，避免浪费 CPU。macOS、Windows x64
  与 Linux 完成后立即创建 Prerelease，实验性的 Windows ARM64 改为独立
  Non-blocking Job，构建成功后再以 `gh release upload --clobber` 附加。
- **Windows ARM64 构建缓存原生依赖** — vcpkg 改用 Files Binary Provider
  持久化静态 OpenSSL Package，Cryptography Source Build 保留 Pip Wheel
  Cache；Cache Key 绑定 OS、Architecture、Lockfile 与 Workflow，减少重复
  编译并保留安装包的静态 OpenSSL 运行时约束。
- **OpenAPI Contract 改用锁定 Generator** — 审查 10 个 Generator-level
  Schema Delta 后，259 个 Operation 的严格 Hash 在 FastAPI 0.136.1 /
  Pydantic 2.13.4 下重新采集，并将 Generator Version 纳入 Contract；没有
  忽略任何 Schema Field。
- **双语文档按最终实现重新核对** — README、安装、使用、配置、架构、开发、
  Browser Live View、Project Notes、Refactor Handoff、Research Workbench
  Roadmap 与 Design QA 统一到唯一 Workbench、当前包结构、数据库名称、
  Managed Child Process、Literature/Zotero 边界、WeChat QR、Budget/Backup/
  Keyring 边界和 Windows SimpleXNG 限制；过时的本地 QA Screenshot Artifact
  已清理。
- **beta2 本地发布基线通过** — 锁定的 Python 3.12 Environment 完整运行
  1,449 项 pytest；Electron App Use 与 Browser Input 共 49 项 Node Test
  通过；WebUI 32 个 JSX Entry 全部重建；Python `compileall`、Version
  Consistency 与 `git diff --check` 通过。平台安装包和 Frozen Smoke 继续由
  `v0.7.0-beta.2` Tag 触发的 Release Workflow 执行。

---

## [0.7.0b1] - 2026-07-23

这是 `0.7.0` 的第一个测试版，完整包含 `v0.6.17` 之后的项目文献库、主动工作行为调整，以及渐进式工具包协议重构和缓存收尾。

- **Workbench 新增项目隔离的文献库** — 知识页现在提供收藏夹、标签云、表格/卡片列表、检索与筛选、可调整高度的详情工作区和右侧检查器。可以管理题名、作者、摘要、DOI、ISBN、期刊/出版社、卷期页码、年份、语言、引用键、阅读状态、星标、标签、笔记、附件和条目关系；列表、详情、空态、错误态、响应式布局及明暗主题均保持既有 Workbench 风格。
- **原有知识库文档无需迁移即可进入文献工作流** — 每个项目现有的 `kb_documents` 会在同一份 `kb_<project>.db` 中幂等映射为结构化文献条目，并复用原附件和索引记录，不复制文件、不跨项目读取、不制造重复条目。只有来源元数据明确提供的 abstract 才会显示为文献摘要，自动索引摘要与正文仍留在内容和检索路径中，不再被错误标成原文摘要。
- **导入、同步和引用链路完整可用** — 支持上传普通文档/PDF、导入 CSL JSON、RIS 与 BibTeX，支持 Zotero Local API 连接测试、项目级同步、集合/笔记/批注/附件及删除状态同步，并可选择复制附件。条目可以生成 IEEE/APA/MLA/Chicago 文本引用和 BibTeX；引用菜单支持直接复制纯文本或 BibTeX。
- **阅读器与文献状态联动** — 从 Workbench 打开文献附件会记录最后阅读时间并更新阅读状态；内容检查器优先显示原附件，图片内嵌、音视频原生播放、PDF 内嵌阅读、Markdown 安全渲染，其他二进制文件提供明确的打开文件入口。切换条目或内容页签会回到顶部，长列表和详情区域分别滚动，互不遮挡。
- **Agent 可以按需检索文献证据并维护元数据** — 新增结构化文献列举、混合检索和元数据更新能力。检索将文献字段、项目知识全文/向量结果与附件关联起来；Agent 被要求先使用项目知识，缺失元数据才通过公开网页核验，并且只能写回已经验证的字段。
- **工具不再把上百个完整 schema 一次性塞给模型** — 主 Agent 现在始终看到固定的直接工具，以及最多 12 个稳定工具包入口；进入某个包后按 `discover → describe → invoke` 渐进披露能力 ID、选中能力的参数 schema 和实际调用。代码、浏览器、桌面、记忆、知识、任务、实体、地图、子代理、交付、技能和集成各自成为独立模块，MCP 和已学习技能也通过适配器按需进入目录。
- **Phase 1 与 Phase 2 保持缓存稳定，同时保留渐进披露** — 对普通主 Agent，同一轮的两个阶段使用字节稳定、顺序确定且完全相同的 wire tool 数组；Phase 1 通过运行时策略只允许决策动作，Phase 2 再执行模块能力。启用设置不变时，历史前缀和工具前缀可持续复用；Deep Research 的篇幅选择握手继续使用专用轻量工具集，不受这条缓存约束。
- **工具包开关真正控制 schema、提示词和执行权限** — 设置 → 能力改为每个工具包一个与现有浏览器开关一致的开关，移除单个工具开关。关闭工具包后，其 gateway 不会出现在 Phase 1/Phase 2 的工具数组中，相应的系统提示词段落也不会被拼接；运行时仍会拒绝旧会话或重放中的过期调用。`AnalyzeAttachment` 与文件、Shell、网页搜索等直接工具始终暴露，不受工具包开关影响。
- **对话侧栏现在显示“已使用的工具包”** — 右侧 Context 面板不再把设置中打开的全部工具包误称为已披露能力，而是从持久化消息、当前运行进度、活动和流式片段中汇总 Agent 实际调用过的 gateway；没有使用时显示明确空态。工具包和各具体工具名称/描述均复用中英文 i18n，设置页和运行卡片不再显示未经翻译的内部名称。
- **最终回复少一次不必要的全历史重建** — Phase 2 已经返回完整正文或有效 `quit(reply)` 时，会直接交付并持久化该终止回复，不再无条件删除它并发起第二次 full-history wrap-up。空回复、占位回复或疑似 DSML 的输出仍进入受保护的收尾路径，并继续允许模型在确有需要时重新打开工具执行。
- **DSML 防泄漏覆盖流式、终止参数和持久化路径** — 既有流式过滤器继续阻止工具标记进入界面；完整及被截断的 DSML 前缀现在也会在直接终止、`quit(reply)`、无工具重试和最终持久化前统一拦截。收尾模型仍输出标记时会重试纯文本，无法安全清理时不会把残留协议文本回显给用户。
- **本地 Electron 开发启动更直接** — 在 `electron/` 目录执行 `npm run dev` 即可完整启动。`local_cli.py` 会识别源码直跑，补入 checkout 的 `src/`，并在仓库 `.venv` 可用时自动切换到该解释器；不再要求手工设置 `PYTHONPATH` 或拼接 Python 路径。
- **主动 Agent 更像受约束的自主工作轮** — 主动触发会优先推进一个有依据、可验证的小任务，而不是生成社交式问候；不会抢占正在运行的最新 Workbench 对话，写入权限限制为新建增量文件/记录，禁止修改、覆盖、移动、重命名或删除现有文件，并继续遵循用户语言和未回复退避策略。

### 技术细节

- 新建 `cyrene.tooling` 控制平面：`types` 定义工具、快照与执行上下文；`catalog` 合并原生/MCP/已学习技能能力；`packs` 声明 12 个稳定模块及 capability 归属；`wire` 生成确定性主 Agent/子代理工具数组和 hash；`gateway` 解析 `discover`、`describe`、`invoke`；`validation`、`results`、`observability` 和 `executor` 统一参数校验、稳定错误协议、遥测及具体执行。
- 每个 Agent run 会冻结一份 actor-specific capability snapshot。发现、描述、调用和并发调度都读取同一份快照，避免 MCP 连接完成、设置变化或动态技能注册在执行中途改变 schema；主 Agent 与子代理按 actor policy 返回不同能力，main-only 工具不会因为兼容旧名称而越权。
- 直接工具契约固定为决策/控制工具、`Read`、`Write`、`Edit`、`Glob`、`Grep`、`Bash`、`WebSearch`、`WebFetch` 与 `AnalyzeAttachment`；其中 `AnalyzeAttachment` 的提示会在知识工具包启用时追加“先从 knowledge_tools 获取准确路径”，关闭知识工具包时自动删除这一依赖说明。
- 工具实现从扁平 `tool_impl/`、`code_tools/`、`registry_tools.py`、`tool_executor.py` 和 `tool_legacy.py` 重组为 `tool_impl/{control,core,code,browser,desktop,memory,knowledge,task,entity,map,subagent,delivery,skills}`。公共 `cyrene.tools` 保持薄兼容门面，已保存对话、旧技能重放及现有导入点仍可解析 concrete tool name，但 concrete schema 不再暴露给模型。
- 原有 `work_tools` 语义统一为 `task_tools`，`collaboration_tools` 统一为 `subagent_tools`，`research_tools` 统一为覆盖知识库和文献库的 `knowledge_tools`；实体与地图从混合分组拆为 `entity_tools` 和 `map_tools`。Deep Research 内部流程、篇幅确认和研究专用提示保持独立。
- 主提示词改为带工具包边界的模板。渲染时只保留启用包的 inventory 和专属规则，关闭浏览器、桌面、记忆、知识、任务、实体、地图、子代理、交付、技能或集成包会同步移除其名称、能力 ID 和操作说明；主 Agent、内部执行 Agent、子代理、Deep Reflection、Planning、Auto Review 和行为学习涉及的旧工具名均已迁移或经兼容层处理。
- Phase 1 仍接收完整稳定 wire 数组，但通过显式 phase override 记录为 `phase1`；普通执行和流式收尾按实际 tools 数组记录为 `phase2`，不再出现“携带 27 个工具却标成 `no_tools`”的错误遥测。token usage 记录补齐 session ID，便于按 Workbench 对话核对缓存命中。
- 终止回复重构保留了原设计中“写最终答案时发现缺少来源，可以重新调用工具”的能力；只有当前响应已经是安全、非占位的最终文本时才跳过 wrap-up。终止正文优先于同时存在的 `quit(reply)`，避免较短 reply 覆盖完整回答；`quit(reply)` 仅作为无正文 tool-only 终止的兜底。
- `_DsmlStreamFilter`、DSML tool-call 规范化和最终文本校验共同构成三层防护：实时 delta 不显示协议标记，provider 返回的文本工具调用可恢复为结构化调用，无法恢复的完整/半截标记不会进入终止回复或会话历史。
- 工具包设置保存在独立 `enabled_tool_packs` 配置中；API 返回稳定的 package group、成员数、配置状态和实际状态，并对未知包、非布尔值及部分更新做原子校验。旧的单工具设置字段仅保留兼容读取/写入，当前 UI 不再提供单工具启停入口。
- Classic WebUI 与 Workbench 设置页使用同一套 12 包顺序、标题与描述 i18n；浏览器开关不再是特殊配置字段，而是直接控制整个 `browser_tools`。所有 switch 带可访问名称，交互视觉、间距、动效和明暗主题沿用现有样式。
- Workbench Context 面板的使用记录不读取设置 API，而是只接受实际消息和 runtime activity 中的 12 个 gateway 名称，去重后按首次使用顺序显示，避免把“可用”“启用”“已披露”和“已使用”混为一谈。
- 文献库在项目知识 SQLite 中新增条目、作者、集合、成员关系、附件、笔记、批注、关系、同步状态和全文索引表；删除默认软删除并支持恢复，Zotero provider key、library version 与删除流用于幂等增量同步。
- 文献搜索同时覆盖结构化字段、FTS 和现有 knowledge retrieval，并将命中的 `kb_document_id` 反向关联到条目；Agent 工具对返回数量、查询范围、元数据写入字段和错误结果做边界限制。
- Zotero 与 Embedding 配置移入 General 设置的标准 field-row，支持连接测试、保存、密钥清除和当前项目导入；密钥只返回“已配置”状态。Embedding 支持 OpenAI-compatible 与 Ollama 端点、模型和维度设置。
- 文献 UI 使用项目级 API client 和 generation guard，切换项目会取消/忽略过期请求；独立列表滚动、可调整详情高度、折叠分类组、固定表头、附件感知内容渲染、安全 Markdown、引用复制、元数据编辑和删除/恢复均有中英文文案及回归覆盖。
- 主动轮使用统一主 Agent loop，但通过 system-initiated policy 禁止询问用户、拒绝对现有文件的 Edit/破坏性 Shell 写入，只允许在新路径创建增量产物；静默 `quit` 不会被普通 final-reply reconstruction 人为扩写成未请求的消息。
- 全仓 Ruff 基线已清零：移除真实的未使用导入、局部变量和无意义 f-string，整理延迟导入位置；`cyrene.agent` 的兼容门面和测试在安装依赖 stub 后再导入被测模块的顺序采用窄范围显式例外，避免为了“消警告”破坏历史导入接口或测试初始化语义。
- macOS 重签名安装包统一采用 Electron 的 SemVer 版本串；当 Python 的 PEP 440 预发布写法（如 `0.7.0b1`）与 Electron 写法（如 `0.7.0-beta.1`）不同时，构建会覆盖重签名前产物并清理当前版本别名，发布页只保留一份经过重签名的 DMG。
- 开发文档、架构图、工具扩展指南、使用说明、README、本地启动排障、设计 QA 和文献库同视口对比证据已同步；WebUI 静态资源缓存戳、微信通道、Python 元数据、Electron 元数据及 lockfile 均更新到本测试版。
- 发布前验证包括 1,227 项完整 pytest、全仓 Ruff 零告警、Python 编译、43 个 Workbench JSX 模块与 PDF.js 资源构建、44 项 Node App Use 测试、Electron JavaScript 语法检查、Python wheel/sdist 构建、macOS 原生 Electron 安装包构建与打包后二进制冒烟，以及 lockfile 和版本一致性检查。

---

## [0.6.17] - 2026-07-23

本版本完整包含 `0.6.16-fix` 的全部修复，并继续完善浏览器浮窗交互与 Workbench 长对话浏览体验。

- **长对话现在可以快速定位每一条用户消息** — 当用户消息超过 5 条时，对话左侧会出现轻量导航。默认收拢为居中的小尺寸半透明胶囊，不遮挡正文；鼠标悬停或键盘聚焦时展开为紧凑消息列表，显示用户实际发送的内容、序号和当前位置，点击即可平滑跳转到对应消息。
- **只有附件的消息会显示真实类型** — 没有正文的用户消息不再用“你”占位，而是根据内容显示图片、PDF、文档、音频、视频、文件或附件等类型；混合附件和长文本也会生成清晰、可截断的定位摘要。
- **离开底部后可以一键回到最新消息** — 用户向上阅读历史内容时，对话底部显示符合 Workbench 风格的返回按钮；点击后平滑滚动到最新消息，并遵循系统的减少动态效果设置。处于底部时按钮自动隐藏，不干扰正常输入。
- **浏览器浮窗会主动为正在阅读的内容让路** — 画中画浏览器只让与浮窗纵向相交的消息避到空间更宽的一侧，其他历史内容保持原位；浮窗居中、窗口过窄或两侧均不可读时保留覆盖式布局，避免把正文挤成狭长列。
- **浮窗拖动、缩放和页面重载更顺滑** — 截图代理会等待图像真正解码并完成绘制后再接管画面，减少拖动起始时的闪烁和空白；3px 手势阈值避免普通点击误触拖动，快速甩动也会等待预览就绪。原生页面边界、内缩和圆角保持一致，重载后会可靠恢复原生页面。
- **滚动阅读不再与浮窗避让互相拉扯** — 触控板或滚轮连续滚动期间暂缓避让重排，手势结束后再统一计算；流式回复、代码块、附件、侧栏和窗口尺寸变化仍会及时刷新布局，同时保留可见消息锚点或贴底状态。
- **浏览器导航继续执行更严格的安全约束** — `browser_navigate` 的 `ui_unreachable` 理由必须携带最近快照签发、与标签页和 URL 绑定、两分钟过期且只能使用一次的凭证；页面已有可见链接时仍要求点击，当前已在目标地址时明确返回 `ALREADY_AT_TARGET`。
- **点击新标签页与页面快照更可靠** — Electron 和 Playwright 会自动接管点击打开的新标签页，并返回来源与活动标签页信息；快照优先保留输入框、按钮、链接等可交互元素，运行中没有回复文本时不再显示空白消息卡。
- **模型上下文上限不再被旧配置误降级** — 计算指定模型或待保存模型列表的上下文窗口时，不再混入进程中旧的全局模型配置；MiMo v2.5 等已知模型会保留正确的 1M 默认窗口，未知模型仍安全回退到候选项中最小的已知上限。

### 技术细节

- Workbench 新增长对话导航模型和可访问交互：仅统计用户消息，超过阈值后启用；通过稳定消息锚点、`IntersectionObserver` / 滚动位置同步当前项，支持 hover、focus-within、键盘操作、平滑定位与 reduced-motion。折叠态使用窄幅半透明胶囊并在可用侧边空间内居中，展开态限制宽高并独立滚动。
- 用户消息摘要统一读取文本与附件元数据，附件专用消息按 MIME、扩展名和媒体类别生成本地化标签；导航列表对长内容做单行截断，活动项保持可见且不会以浮层常驻覆盖对话。
- 返回底部控件复用对话滚动容器的贴底判断和既有视觉变量，只在用户确实离开底部时显示；新消息到达、切换对话和布局变化时同步更新状态。
- 浏览器画中画交互增加截图加载/解码/绘制握手、3px 拖动阈值、快速手势兜底、稳定 surface bounds 与重载遮挡恢复；对话避让在滚动手势结束后批量重算，避免读取历史内容时发生跳位。
- `0.6.16-fix` 中的动态避让、短期 snapshot credential、重复导航阻止、新标签页接管、交互元素优先快照与空运行消息修复全部纳入本正式版本。
- 上下文窗口解析拆分为“显式配置”和“内置模型族默认值”两层；`effective_ctx_limit_for_model` 全程使用调用方提供的同一份配置快照，避免读取全局设置造成跨配置污染。
- Python 包、`uv.lock`、Electron 应用与 lockfile、README、文档站、微信通道、WebUI 静态资源缓存戳及相关测试统一更新到 `0.6.17`；同时归档 Research Workbench 报告来源说明并完善审计产物忽略规则。
- 发布前全量 pytest 通过 1174 项，Node App Use 通过 44 项；Workbench 42 个 JSX 模块与 PDF.js 资源构建、Electron JavaScript 语法检查、Python 编译检查、sdist/wheel 构建及 Python/Electron lockfile 校验全部通过。

---

## [0.6.16-fix] - 2026-07-22

- **浏览器浮窗不再挡住正在阅读的消息** — 浏览器以画中画浮在对话上方时，只会让与浮窗纵向相交的消息行动态避到空间更宽的一侧；其他历史消息保持原宽度和位置。浮窗在中间或窗口太窄、两侧都不可读时会保留覆盖式布局，避免把文字挤成细长列。
- **拖动和缩放浮窗时排版更稳** — 浮窗左右移动、八向缩放、消息流式增长、代码块和附件改变高度、侧栏切换或窗口缩放时，避让区域都会及时重算；重排前后保留当前阅读锚点，位于底部时继续贴底，减少内容跳动。
- **Agent 不能再用理由字段绕过网页交互** — `browser_navigate` 的 `ui_unreachable` 现在必须携带最近一次 `browser_snapshot` 签发的短期凭证。凭证与当前标签页和 URL 绑定、两分钟过期且一次性使用；点击、输入、滚动、导航或切换标签页后立即失效。若页面已有可见目标链接，仍会要求 Agent 点击页面元素。
- **重复导航会被明确阻止** — 当当前标签页已经位于目标 URL 时，导航守卫直接返回 `ALREADY_AT_TARGET`，不会重复刷新页面或丢失页面状态。用户明确给出的精确 URL 仍可直接打开，SSRF 与浏览器运行时校验继续生效。
- **点击打开的新标签页会自动接管** — 网页点击在新标签页打开内容时，Electron 和 Playwright 都会切换到新的活动页，并把新标签页、来源标签页和来源 URL 一并返回给 Agent；后续快照与操作会落在正确页面，不再继续误操作旧页。
- **页面快照和运行中消息更准确** — 快照优先收集输入框、按钮、链接及其他真正可交互元素，减少大页面中装饰节点占满引用额度；只有运行中确实已有回复文本时才渲染实时消息卡，避免空白气泡。

### 技术细节

- Workbench 为每个 transcript 直接子项增加 `.wbc-thread-item` wrapper。纯函数根据对话区、浮窗矩形、间距和最小可读阈值选择左右阅读通道；只对纵向相交项写入逻辑方向 padding，保留用户消息右对齐与 Agent 消息左对齐。
- 避让调度使用 `requestAnimationFrame` 合并高频更新，监听 `workbench:browser-layout`、scroll、window resize、`ResizeObserver` 与 `MutationObserver`；有序消息行通过二分查找定位相交区，并在多轮高度稳定过程中恢复首个可见项的像素锚点或底部位置。
- Electron 与 Playwright 新增绑定活动页面的 snapshot credential 和统一 `navigation_guard`。令牌使用安全随机值与恒定时间比较，导航、页内跳转、点击、输入、滚动和标签页切换统一失效；`browser_navigate` 在工具实现层和 legacy 路径中都必须经过守卫。
- 浏览器点击结果统一规范 `opened_new_tab`、`active_tab_id`、`source_tab_id` 和 `source_url`；Electron 在点击后对实际活动 `WebContentsView` 取快照，Playwright 自动收养新 page 并等待 DOM ready，再把活动页信息写入工具结果。
- 补充导航凭证过期/复用/失效、重复 URL、可见链接、弹窗接管、元素优先级、浮窗左右/居中/窄屏规划、相交项避让和空运行消息的回归测试；设计可行性报告及现场截图保存在 `.codex-audit/browser-dynamic-layout/`。
- 应用、Electron、README、文档站、微信通道、WebUI 静态资源缓存戳及相关测试统一更新到 `0.6.16-fix`。因 Python PEP 440 不接受连字符后缀，`pyproject.toml` 与 `uv.lock` 使用等价构建版本 `0.6.16+fix`，运行时对外版本及 GitHub tag 规范为 `0.6.16-fix`；更新器比较版本时使用同一映射，确保后续版本仍可被识别。
- 发布前全量 pytest 通过 1172 项，Node App Use 通过 44 项；Workbench 42 个 JSX 模块与 PDF.js 资源构建、Electron JavaScript 语法检查、Python 编译检查、sdist/wheel 构建及 Python/Electron lockfile 校验全部通过。

---

## [0.6.16] - 2026-07-22

- **每轮 Agent 改了哪些文件，现在可以直接审阅** — 对话会按运行轮次记录工作区中新建、修改和删除的文件，并在回复下方显示文件数与增删行统计；点开即可查看逐文件 diff。记录不依赖 Git，取消、报错、等待用户补充和继续执行的轮次也会正确归档。
- **PDF 分析会自动挑选真正相关的页面** — 上传或打开 PDF 后，系统会先建立页面清单，再由 Agent 从整份文档中选择相关页；支持跨页、非相邻页上下文，自动去重并限制总量，避免只看当前页或把整份文档无边界塞进上下文。Workbench 两种 PDF 入口都使用同一套逻辑，并跟随界面语言生成分析提示。
- **内置 PDF 阅读器兼容性更稳** — PDF.js 的核心、Worker 和 Viewer 统一改用官方 legacy 构建，补齐 Electron 当前 Chromium 缺少的集合与 TypedArray API；加载流程使用可中断的 streaming task，切换文件时能及时取消旧请求，减少空白页和版本不匹配。
- **启动页更干净，也不会提前闪出内容** — 启动时先显示跟随明暗主题的 Cyrene 标识，等待 Workbench 初始项目数据及首屏请求稳定后再淡出；包含网络静默窗口、超时兜底和减少动效适配，避免加载过程中的界面闪烁。
- **Agent 会优先点击当前页面已有链接** — `browser_navigate` 现在检查目标 URL 是否已经作为可见链接存在；若存在，会返回可点击引用并要求使用页面内点击。只有起始入口、用户明确给出的精确 URL 或页面确实不可达时才允许直接导航。
- **全屏视频和浏览器指导操作更顺手** — 视频全屏后继续把键盘交给网页播放器，可直接使用空格、方向键等原生快捷键；最大化浏览器的 Agent 输入条上移以避开底部控件，完成状态停止闪烁并移除多余阴影。
- **细节体验进一步统一** — 聊天上传文件复用知识库的文件类型图标；技能学习参数名补充中英文显示与原始字段提示；尚未接通的退出登录入口暂时隐藏，避免出现无效操作。

### 技术细节

- 新增不依赖 Git 的 workspace snapshot/change-set 层，忽略 `.git`、支持 dotfile、二进制与超大文件降级、单文件及全局 diff 容量上限，并按 chat/run 持久化。相同工作区的并发轮次使用归属锁串行取基线，不同工作区仍可并行；列表接口只返回摘要，逐文件请求才返回完整 diff。
- Workbench 新增 change-set SSE 事件、历史读取及逐文件 diff API；聊天删除时同步清理变更历史。前端加入运行级变更卡片、文件筛选、增删行统计、按需 diff 加载、空态/错误态及中英文文案，并改进代码 diff 的行号、折行与窄屏布局。
- PDF 路由新增有界上下文规划、页面 inventory、Agent 选页与文本提取管线，明确把自动提取内容作为有边界的来源材料；同页内容去重，异常时保留安全 fallback。PDF.js 构建脚本统一复制 `legacy` core/worker/viewer，并清理上游生成文件的行尾空白。
- 静态 HTML 在 React 前绘制主题感知启动层；共享 bootstrap promise、首屏 fetch 追踪、300ms network-quiet gate、140ms 淡出与 20 秒安全截止共同控制 ready 状态，非 Workbench 和 quick-chat 路径也会正确结束启动态。
- Electron 与 Playwright 浏览器实现新增可见目标链接扫描，`browser_navigate` 工具新增必填 `reason` 枚举并在普通直接导航前返回 `VISIBLE_LINK_AVAILABLE`；Electron 全屏视频复用现有 `WebContentsView` 以保留网页键盘事件。
- Python 包、`uv.lock`、Electron 应用与 lockfile、README、文档站、微信通道、WebUI 静态资源缓存戳及相关测试统一更新到 `0.6.16`。
- 发布前全量 pytest 通过 1164 项，Node App Use 通过 44 项；Workbench 42 个 JSX 模块与 PDF.js 资源构建、Electron JavaScript 语法检查、Python 编译检查及 Python/Electron lockfile 校验全部通过。

---

## [0.6.15] - 2026-07-22

- **浏览器最大化后也能继续指挥 Agent** — 最大化内置浏览器时，页面底部会保留一个轻量输入框；可以直接发起任务、在运行中追加指导，输入为空时一键停止，不必先退出浏览器再回到对话。
- **浏览器操作进度始终看得见** — 从最大化浏览器发出指令后，会显示 Agent 当前回复或工具执行状态；任务结束后自动收起。原生 Electron 页面使用独立透明叠层，网页 fallback 使用同等的 DOM 控件，主题色和中英文文案保持一致。
- **浏览器画面切换更完整** — 浮窗拖动、缩放、最大化和恢复时的截图预览改为结构化状态，原生页面边界与圆角统一同步；浮窗模式隐藏根页面滚动条，避免滚动条切断圆角或在模式切换时闪现。
- **Context 面板更稳、更省资源** — 会话收件箱在运行时直接读取内存态，空闲时自动降低轮询频率，切换对话会取消旧请求；首次加载和请求失败不再短暂显示错误的零队列。上下文用量的 60% 压缩阈值固定使用自动压缩器的主模型预算，不会因单次 fallback 模型而跳动。
- **Agent 更坚持通过可见页面操作** — 浏览器导航规则进一步收紧：进入起始页后优先使用快照中的链接和按钮，只有用户明确给出精确 URL，或确认页面没有可达入口时才再次直接导航，减少跳过网站正常交互流程的情况。

### 技术细节

- Electron 新增隔离的 `browser-chat-overlay-preload.js` 和 `WebContentsView` 叠层，通过受限 IPC 转发发送、运行中指导与停止动作；叠层绑定浏览器 session，跟随窗口归属、遮挡、视频全屏、浏览器 bounds、主题和销毁生命周期。
- Workbench 浏览器最大化界面接入运行状态、发送、指导和中断回调；原生叠层不可用时渲染同功能 fallback composer。浏览器视图统一设置页面 bounds、原生圆角和根滚动条样式，交互预览由字符串改为结构化对象。
- 收件箱 API 在活动 run 存在时跳过完整 chats store 校验并返回处理耗时；前端使用单请求自调度轮询、`AbortController` 清理和活动/空闲 1 秒/5 秒间隔。上下文 API 显式传入当前压缩预算，避免以最新 assistant fallback 模型重算阈值。
- 主 Agent 与执行 Agent 提示词统一为“一次性入口导航 + 页面内可见点击”策略；补充 Electron 叠层 IPC、浏览器全屏输入、状态、圆角、轮询取消、收件箱热路径、上下文阈值及提示词约束的回归测试。
- Python 包、`uv.lock`、Electron 应用与 lockfile、README、文档站、微信通道、WebUI 静态资源缓存戳及相关测试统一更新到 `0.6.15`。
- 发布前全量 pytest 通过 1144 项，Node App Use 通过 44 项；Workbench JSX 构建、Electron JavaScript 语法检查与 Python/Electron lockfile 校验全部通过。

---

## [0.6.14] - 2026-07-18

- **Agent 现在可以滚动弹窗和页面内区域** — 内置浏览器的 `browser_scroll` 不再固定滚动最外层文档，而是在目标元素所在位置发送真实滚轮输入。小红书帖子详情、评论区、侧栏、模态框及其他使用 `overflow: auto/scroll` 的 SPA 内部区域都能像用户鼠标或触控板一样滚动。
- **滚动位置可以明确指定** — `browser_scroll` 新增可选的元素 `ref` 和视口 `x` / `y` 坐标；Agent 可以用 `browser_snapshot` 返回的引用指定要滚动的帖子、评论区或侧栏。未指定目标时使用浏览器视口中心，兼容原有调用。
- **滚动结果不再虚报成功** — Electron 和 Playwright 都会记录目标区域滚动前后的实际位置。发生滚动时返回实际位移和目标；位于边界或坐标未命中可滚动区域时明确返回“没有效果”，不再无条件报告 `Scrolled 500px`。
- **用户滚动记录能区分根页面与内部容器** — 浏览器行为事件现在记录触发滚动的真实元素、`scrollTop`、内容尺寸、可视尺寸以及根文档位置。开发者可以直接判断用户滚动的是页面、弹窗还是嵌套区域，不再把内部滚动误记为 `window.scrollY: 0`。

### 技术细节

- Electron 浏览器将 `window.scrollBy()` 替换为 Chromium 可信 `mouseWheel` 输入，通过目标坐标完成原生命中测试和滚动链；处理 Electron 原生滚轮与 Web/Playwright 正负方向相反的差异，并支持 Shadow DOM 宿主回溯。
- 滚动前给最近且在指定方向仍可移动的容器设置一次性探针，滚动后读取并移除探针，以返回 `actualDeltaX` / `actualDeltaY`、目标标签、ID 和引用。目标查找会跳过已到边界的内部容器，让浏览器自然向外层滚动链传递。
- Playwright fallback 改用 `page.mouse.move()` + `page.mouse.wheel()`，并使用同样的目标探测和实际位移验证，不再执行根文档级 JavaScript 滚动。
- 新增 Electron 真实 Chromium 冒烟测试，覆盖 `body` 禁止滚动、固定弹窗内部独立滚动及向下/向上反向滚动；补充 Electron RPC 参数、工具实际位移输出和无位移提示的 Python 回归测试。
- Python 包、`uv.lock`、Electron 应用与 lockfile、README、文档站、微信通道、WebUI 静态资源缓存戳及相关测试统一更新到 `0.6.14`。
- 发布前全量 pytest 通过 1140 项，Node App Use 通过 44 项；Electron 35 真实 Chromium 嵌套滚动冒烟测试、JavaScript 语法检查、Python 编译检查与 Python/Electron lockfile 校验全部通过。

---

## [0.6.13] - 2026-07-18

- **浏览器真正留在对话里** — Agent 打开浏览器但没有切到右侧浏览器 Tab 时，会在当前对话区域显示可拖动、可缩放的小窗；位置和最大尺寸严格限制在对话内容区域。小窗支持最小化为单一“浏览器”药丸、恢复和最大化为整个 Cyrene 浏览器界面，默认尺寸缩小为此前的一半。
- **浏览器切换不再白屏或掉帧** — 拖动时原生网页内容实时跟随；小窗、最大化和恢复之间切换时用稳定帧预览衔接 Chromium 合成器，修复短暂白屏、内容错位、阴影截断和右下角圆角失效。
- **视频全屏按平台正确工作** — macOS 会创建与 Cyrene 主窗口独立的全屏视频窗口；Windows 和 Linux 在 Cyrene 主窗口内全屏。小窗、右侧浏览器 Tab 和浏览器最大化界面会同步显示“已在全屏播放”，退出后恢复原来的窗口模式和尺寸。
- **网页文件上传更安全** — 新增 `browser_upload_files`。Agent 可以使用页面文件输入框或被拦截的原生文件选择器上传工作区文件，同时校验目标元素、页面身份、文件类型、大小、符号链接、SHA-256 和过期状态，避免把文件交给错误页面或已经变化的控件。
- **备份可以可靠迁移和回滚** — 备份改为带清单、摘要和容量限制的可验证格式；配置以可迁移快照保存，SQLite 使用一致性备份。恢复前完整校验并支持 dry-run，恢复失败会回滚文件、目录、数据库和配置，同时兼容旧版备份。
- **全局搜索更轻、更快** — 搜索不再为每次输入执行完整 Workbench 修复和工作区扫描；项目、聊天、记忆等读取移出事件循环并使用轻量存储路径，前端会取消过期请求，避免连续输入时卡住界面或被旧结果覆盖。
- **App Use 大幅滚动更稳定** — `scroll_at` 支持较大滚动量并拆成安全的原生滚轮事件，macOS 与 Windows 都会返回实际事件数；坐标校准只约束真正依赖坐标的操作，菜单、快捷键和聚焦不再被错误拦截。视觉描述默认改为简洁的任务相关摘要，减少无关 OCR 和 token 消耗。
- **最终回复不会只显示一次** — `quit(reply=...)` 的最终文本会作为普通 assistant 消息写入历史，并移除没有配对 tool result 的终止调用，刷新页面或下一轮对话后不再丢失。

### 技术细节

- Electron 浏览器改为会话级原生视图管理。浮窗边界使用对话内容坐标系，支持拖动、八向缩放、最小化/最大化/恢复；`WebContentsView` 边界更新按约 30fps 合并，模式切换用截图预览、`capturePage()` 和 `invalidate()` 衔接，并用原生 `setBorderRadius()` 修复白屏、错位和圆角问题。
- HTML Fullscreen 只放行浏览器分区的 `fullscreen` 权限。监听 `enter-html-full-screen` / `leave-html-full-screen`：macOS 将同一个 `WebContentsView` 移入独立全屏窗口，Windows/Linux 覆盖主窗口；系统退出、关闭标签和应用退出会同步恢复原状态。直接媒体页面的可恢复 `ERR_FAILED` 不再阻断标签挂载。
- 新增 `browser_upload_files`。Electron 通过 CDP 捕获文件选择器，Electron/Playwright 都支持文件输入引用；上传前后校验页面、控件、数量、大小、符号链接和 SHA-256，一次性目标与暂存文件按 TTL 失效。浏览器点击、输入、滚动和导航同时写入脱敏的行为学习事件；Agent 提示词改为页面内优先点击可见链接和按钮，仅在入口、用户明确指定地址或页面不可达时直接导航 URL。
- 备份使用版本化 manifest、逐项大小和 SHA-256、SQLite 在线快照及可迁移配置；恢复前限制条目数和解压体积，拒绝路径穿越、重复项、符号链接和篡改内容。staging 后原子替换，失败时逆序回滚文件、目录、数据库和配置，并保留 dry-run 和旧备份兼容。
- Workbench 搜索改用轻量存储读取，项目、聊天和记忆扫描移出事件循环；前端取消过期请求，避免旧结果覆盖。`quit(reply=...)` 物化为纯文本 assistant 历史，修复刷新或下一轮后最终回复丢失。
- App Use 的 macOS/Windows `scroll_at` 支持较大滚动量并拆成安全增量，返回实际事件数；坐标校准只约束点击、滑动、滚动和视觉输入，菜单、快捷键与聚焦不再误拦。默认视觉分析限制为 8 个短要点和 600 字符。
- Python 包、`uv.lock`、Electron 应用与 lockfile、README、文档站、微信通道、WebUI 缓存戳及测试统一更新到 `0.6.13`。本地全量 pytest 通过 1137 项，Node App Use 通过 44 项；发布标签构建 macOS、Windows x64/ARM64 与 Linux 正式安装包。

---

## [0.6.12] - 2026-07-17

- **桌面版体积大幅缩减** — 不再捆绑 Playwright 和 Chromium，安装包小了上百 MB。修了之前内置浏览器和 Playwright 各用各的会话的 bug，现在统一用 Electron 内置浏览器。
- **收件箱实时可见** — Context 面板加了实时收件箱，能看见工具执行到哪一步、队列里排了多少、Agent 消费了没有。
- **中途指导不再丢失** — 运行中发送的指导加了去重，重启或断线后不会重复注入。Agent 正在输出最终回复时发来的新指导也不会被吞掉。
- **分支树导航更清晰** — 改成类似 Git 历史图的紧凑布局，主线与分支独立着色，深层分叉也不容易看乱。

### 技术细节

- Electron 浏览器工具统一复用 `WebContentsView` + `persist:cyrene-browser`，RPC 错误直接上报不再回退 Playwright。默认构建跳过 playwright 及 Chromium 下载，PyInstaller 排除 playwright 包。新增 `build.py --bundle-playwright` 显式 opt-in。
- 收件箱支持 queued/claimed/running/ready/consumed/completed/failed/cancelled 多状态，`live_snapshot` 合并内存未持久化事件与 SQLite 记录。工具执行前发布带 `tool_call_id` 的 `started` 事件，与完成事件同 ID 驱动前端原位更新。
- 用户指导带稳定去重标识，内存 + SQLite 双检查；启动时修复"收件箱已落盘但聊天 transcript 未写入"的窄故障窗口。流式最终回复期间到达的指导不再被收尾吞掉，转为中间边界后继续注入。
- 分支树改为 Git 历史式布局（主线/分支独立色调、窄轨道、连接曲线），单行摘要固定高度，悬停/键盘焦点/减少动效适配。

---

## [0.6.11] - 2026-07-16

- **模型切换更稳** — 同一会话优先重复使用上次成功的模型，降低切换带来的波动。失败的模型会按会话隔离冷却，不会持续被跳過。
- **浏览器链接可直接点击** — `browser_navigate` 会返回带引用 ID 的链接列表，Agent 可以直接引用点击，不用猜资源 ID。
- **App Use 跨平台不乱暴露能力** — Windows 不再显示 macOS 专属功能，`visual_click` 只在底层支持时启用。
- **聊天时间线终于不乱跳了** — 修了工具调用和回复的顺序问题，实时流和刷新后的展示一致。

### 技术细节

- 模型候选选入会话亲和性（affinity），失败候选按会话隔离冷却。亲和 + 冷却信息参与排序，成功刷新亲和记录。
- `browser_navigate` 返回可交互引用 + URL 列表；Electron/Playwright 过滤隐藏/无尺寸/重复链接；HTTP 回退解析相对链接。
- App Use 平台能力按 gating 披露：`measure_coordinates` 保存目标描述，`visual_click/type` 复用同一描述。Windows `GetWindowRect` 获取稳定边界，聚焦增加 `BringWindowToTop` + `SetFocus` + `AttachThreadInput` 回退。
- 修复活动时间线顺序：可见回复关闭前一活动，为当前推理+工具开启新活动卡。实时流与持久化恢复保持一致。

---

## [0.6.10] - 2026-07-15

- **技能学习大改** — 不再搞什么模式指纹、相似度阈值那一套复杂的。现在每次执行直接记录"目的"，按目的归类，清晰多了。复杂工作流可以直接学成 Python/Shell 脚本。
- **费用计算终于准了** — 模型切来切去的时候，费用按实际调用的模型算，不是按配置里的。缓存命中/未命中也分开算。
- **页面切换不再白屏** — 聊天、知识、记忆、日程页面切来切去不再反复卸载重装，秒切。

### 技术细节

- 行为学习重写：每轮生成 purpose，学习 Agent 一次比较全部历史目的决定归入/新建候选。移除 fingerprint 桶、相似度阈值、词汇表、pattern review 层、shadow router、replay-test。复杂非交互工作流可合成 Python/Shell 实现，写入受限目录，记录来源+SHA-256，高风险技能需审批。旧 fingerprint/pattern/vocabulary/review/replay 表/列/索引启动时清理。
- 上下文窗口按模型配置→已知内置上限→候选最小已知上限三级解析。主模型超限时可选更大 fallback。fallback 通知去重。费用按实际响应模型汇总，正确处理多模型 fallback、缓存命中/未命中、CNY/USD 混合价格。
- SSE 新增 `reasoning_start/delta/done`。每次 LLM 调用独立活动卡，合并推理/工具事件。活动时间线按真实时间戳排序（非字符串）。乐观消息保留客户端时间锚点。纯推理无工具的卡不持久化。
- 主壳页面改用稳定挂载的隐藏 surface，不再卸载组件。知识/记忆/日程加入内存缓存 + 并发去重。

---

## [0.6.9] - 2026-07-14

- **App Use 操作可审计** — 现在先让你看截图、选坐标、再执行点击，每一步都有截图和标记可以回头查。
- **项目切换不再卡** — 切项目只保存选择状态，不再读整个 store。聊天列表秒切，不再空白闪烁。

### 技术细节

- App Use 改为 `visual_describe` → `measure_coordinates` → `focus_window` → `click_at`。`visual_describe` 保存截图产物，`measure_coordinates` 校验边界并换算三套坐标，`click_at` 必须复用最新 `window_point`。`visual_click`/`virtual_click_at` 降为 fallback。重置状态规则细化。
- Workbench 激活改为 SQLite 字段原子补丁写入，`/api/workbench/activate` 仅返回确认字段。前端不再把小响应归一化为完整 store。聊天按项目缓存列表、按聊天缓存详情。代次+ID 校验防止过期响应覆盖。

---

## [0.6.8] - 2026-07-14

- **App Use 操作更安全** — 每次操作必须重新测量坐标，不能用旧的。提交前校验边界，超出窗口直接拒绝。操作结果明确告诉你是执行成功了还是不确定。
- **技能学习不用手工了** — 同一个工作流重复做两次就会提示你学成技能，第三次自动学。参数自己提取。

### 技术细节

- `measure_coordinates`/`visual_click`/`visual_type` 坐标优先工作流。`visual_click` 最多 2 次视觉定位。macOS 新增 Swift 坐标命中 helper（arm64/x86_64），AX 超时细分。Windows 按坐标裁剪 UIA 遍历。`menu_command` 支持菜单名和快捷键匹配。
- 学习引擎新增重复候选状态机：首次记录、第二次提示、第三次自动学习。从重复调用中提取稳定参数/类型/默认值。旧生成脚本自动迁移到声明式格式。
- 工具调用硬性 wall-clock 超时。文件 IO 移出事件循环。Workbench 写入加进程内重入锁，持久化冲突有界重试。

---

## [0.6.7] - 2026-07-13

- **App Use 补齐点击能力** — 单击、双击、右键、悬停、拖拽、滑动、滚动全有了。新增原子键盘序列（快捷键组合）和文本选区操作。
- **安装包不抽风了** — 之前打包后 provider 脚本路径不对导致 App Use 打不开，现在修好了。
- **聊天体验改进** — 可以粘贴文件/图片到输入框。新消息立即显示，不再等服务端确认。运行中的指导也即时展示。

### 技术细节

- 修复 provider 脚本路径（`extraResources` 放真实文件系统，不在 `app.asar` 内）。缺失时返回 `provider_unavailable` 不可重试错误，Agent 禁止 shell 回退。
- macOS Quartz `CGEvent` / Windows `SendInput` 坐标操作，支持移动时长、双击间隔、滚轮幅度。`key_sequence` 原子组合。`select_text`/`set_selection_range` 按内容/次数/范围选区。
- 文件粘贴支持 `DataTransferItemList`。乐观消息插入 + 服务端确认替换。指导同样即时显示，持久化后确认。新建聊天跳过冗余 hydration。

---

## [0.6.6] - 2026-07-13

- **App Use 桌面控制正式落地** — 支持 macOS 和 Windows 桌面应用自动化：发现窗口、连接、读界面结构、点击、输入，都在后台完成。Safari 可以不抢前台焦点就操作。
- **延迟大幅降低** — 工具结果到了先唤醒 Agent，再异步写数据库。聊天读取移出主线程。SQLite 不再卡住其他请求。

### 技术细节

- 统一 `app_use` 工具网关（list_targets/connect/call/status/disconnect），能力动态披露。macOS JXA + Windows PowerShell UIA helper。会话绑定进程启动时间和窗口身份，支持 TTL、陈旧引用检测、自身窗口排除。
- 结果优先唤醒：先投递 Agent → 异步 SQLite。遥测异步化。聊天读取移出事件循环（工作线程）。前端请求支持取消和代次校验。

---

## [0.6.5] - 2026-07-13

- **Agent 执行中可中途引导** — 工具执行时发消息，Agent 会收到并按新指令调整。收件箱持久化，重启后指导还在。
- **模型故障转移看得见** — 主模型失败切到备用模型时，界面上会显示。后续请求优先用最近成功的模型。
- **图片附件可直接分析** — 浏览器截图如果模型支持视觉，直接交给模型识别。

### 技术细节

- 运行级 Inbox（SQLite 持久化 + 去重）。工具结果投递到对话，用户指导高优先级打断。重启恢复有效指导。工具按资源键并行/串行调度。
- 模型失败发布 fallback 可见事件。候选排序引入成功亲和性。视觉能力探测持久化 `vision_capable`。Agent 循环从 Inbox 消费结果与指导。

---

## [0.6.4] - 2026-07-11

- **后台任务管理** — 退出时不再残留线程和数据库链接。内置 PDF 查看器，直接在 Workbench 里看 PDF。
- **技能学习更聪明** — 按项目完全隔离，不会混淆。多轮交互才学习，一次操作不学，减少噪音。
- **验收通过自动完成** — 任务所有验收标准通过后自动标记完成，不用手动点。

### 技术细节

- `task_lifecycle.py` 统一管理 asyncio 任务生命周期。`LifespanManager` 替代装饰器。PDF.js 集成。
- 行为学习引擎重写：`project_id` 贯穿所有查询，`_learn_step_core` → `_run_learning_review` 管道，review 决定批准/驳回/修正。`_AUTO_LEARN_MIN_TURNS` 门槛防单轮噪音。
- subagent `wait_until_settled()` 原语。验收通过 → 自动 `status=completed`。Goal Loop 并发锁与失败回滚。

---

## [0.6.3] - 2026-07-07

- **技能可以学了** — Agent 重复做某件事时，可以学成技能存下来。下次直接调用，不用一步步教。
- **浏览器操作能回溯** — Agent 在浏览器里的点击、输入、滚动都有记录，方便回放和学习。
- **多语言支持** — 现在有中/英文界面了。

### 技术细节

- `GetLearnedSkill`/`RunLearnedSkill` 工具。学习 Agent 构建技能块注入 prompt。浏览器 `beforeinput/click/scroll/keydown/popstate/hashchange` 六类事件捕获+持久化。
- 3 张新表：`behavior_turn_tool_chains`、`behavior_learning_agent_reviews`、`behavior_browser_user_events`。`pattern.py` 重写移除旧 scripts 系统。
- i18n 支持。知识库分页（每页 80 条）+ 惰性加载。自适应预算分配比例 30% → 40%。

---

## [0.6.2] - 2026-07-05

- **省钱模式** — 新增预算控制和"经济模式"，自动清理历史工具结果，减少 token 消耗。设了月限额后超支可以自动切便宜模型。
- **macOS 原生菜单** — 中文/英文完整应用菜单，快捷键直接操作。
- **文件发送有回音了** — `send_file` 成功后会在聊天里说"文件已发给你"，不再是冷冰冰的 "Done."

### 技术细节

- `AdaptiveBudgetController` 规则引擎（月/周/5小时三层窗口），`/api/budget/status` 和 `models` 端点。Economy Mode 自动清理已完成工具结果。`BudgetPanel` UI 组件。
- macOS 原生菜单栏（文件/编辑/视图/窗口/帮助），`menu:action` IPC 桥接。
- `send_file` 自动构建确认文本。记忆注入改为 top20 + 随机5条混合。项目查询 `?detail=summary` 支持。

---

## [0.6.1] - 2026-07-01

- **浏览器能力补全** — 按坐标、按文本、按引用点击、输入、等待、网络日志、多标签管理都有了。
- **Quick Chat 常驻** — Electron 后台托盘驻留，关窗后 Agent 继续跑。

### 技术细节

- 浏览器新增坐标点击/引用点击/文本点击/输入/等待/网络日志/结构化快照/多标签管理。Electron Quick Chat 增强后台驻留、托盘、窗口复用。
- Chat-only 流式回复路径简化，复用 phase-1 回复不再额外 LLM 调用。行为学习遥测后台化。

---

## [0.6.0] - 2026-06-29

首个正式版本。汇总 0.5.x 以来全部累积更新和 beta 迭代。

- **Workbench 工作台（全新）** — 以项目为中心的全新桌面 UI。每个项目独立的对话、任务、知识、记忆、日程页面。计划基于实际工作区生成，任务按步骤执行，中途可干预。断线后 Agent 在后台继续跑，回来追上就行。
- **Quick Chat 全局快捷对话（全新）** — 任意界面 `Ctrl/Cmd+Shift+Space` 呼出浮动窗口，支持截图粘贴，与主对话体验一致。
- **三层记忆体系** — 上下文记忆 + 短期跨会话摘要 + 长期 `SOUL.md`。记忆可精准按 ID 退役。新增 `RecallConversation` 和 `search_project_memory` 工具。
- **浏览器实时直播** — 在聊天里看浏览器实时画面，遇到登录墙可以直接上手操作，完了继续。
- **提示词缓存大幅优化** — Agent 响应更快了，尤其是长对话。
- **Windows on ARM 支持** — ARM64 和 x64 安装包都提供。

### 技术细节

**Workbench 架构：**
- SQLite 事务存储（`BEGIN IMMEDIATE` + WAL + 三方合并）。Workspace 路径安全校验。聊天编辑与分叉。产物一键下载（服务端校验不逃逸）。上下文 token 仪表 + 手动压缩。
- 诚实的逐步任务执行（计划预探索、意图分流、finalize 收尾）。Chat 运行由进程级管理器持有，断线/切页不中断。subagent 状态独立面板。
- 对话编辑（从修改点分叉新对话）和重新生成（事务式替换）。

**Quick Chat：**
- 全局快捷键 + 截图 + 独立窗口复用 `window.WbcComposer`。共享运行管理器和消息卡片渲染。

**Agent 运行时：**
- 提示词缓存优化：`static_system_extra` 前缀化、`fixed_ephemeral_system` 置于用户消息前、temporal 移尾部、统一 phase1/2 工具集、`quit(reply=)` 直接交付。
- 运行打断、LLM 瞬时错误重试、DSML 流式工具标记抑制。跨平台 Shell 识别 + 高危命令守卫。

**记忆体系：**
- 三层：上下文 → 短期跨会话摘要 → SOUL.md。记忆分类 `habit/conversation/preference/reflection`。`retire_project_memory` 工具。短期记忆按最近 20 条用户/助手消息窗口提取。

**浏览器：**
- `/ws/browser` 二进制 JPEG 帧传输，SSE 只走元数据。面板内 CDP 输入 + 登录接管。

**打包与更新：**
- Windows ARM64 + x64 CI。运行时目录统一。更新器按平台/架构匹配 + SHA256 校验 + beta 通道。

**旧 beta 版（0.6.0b0–b16）汇总：**
- b0: Windows ARM 支持、Workbench UI 默认壳
- b1: Beta 更新通道、Subagent UI、计划修订流程
- b2: 修复 Windows 更新器 asset 匹配、Workspace-scoped agent context、验收标准验证、LLM 错误分类与脱敏
- b3: `ListKnowledgeDocuments` 工具、步骤自动归档知识库、Linux 原生目录选择器、流式运行引擎模块化
- b4: 跨平台 Shell 运行时、语言偏好持久化
- b5: HTML artifact 沙盒隔离、Artifact 下载、SQLite WAL 模式
- b6: `RecallConversation`/`search_project_memory` 工具、提示词缓存优化（`static_system_extra`）、temporal 移尾部
- b7: 聊天编辑与分叉、Profile 页、快捷键管理器、SQLite 事务存储、DSML 流式过滤、Context window 仪表
- b8: 修复新项目 workspace 路径、Glob/Grep 硬编码全局 workspace
- b9: Workbench API 封装层、心跳、LLM 蒸馏上限、Code block 语法高亮修复、Profile 仪表盘实时刷新
- b10: 可恢复 Chat 运行、手动压缩对话、记忆退役工具、回复期间可编辑草稿
- b11: Quick Chat 全局快捷键、截图支持
- b12: Quick Chat 升级完整对话体验、事务追踪、`quit(reply=)` 直接交付、分段渲染修复
- b13: 项目共享上下文、计划预探索、记忆 Session 基线快照、Prompt 缓存优化（`fixed_ephemeral_system`）
- b14: 修复 30s 启动超时（知识迁移后台化）、Vision 候选链顺序、反思记忆污染 fact bucket
- b15: 无 Git diff 捕获、Workbench 会话召回进 workspace、多词记忆 OR 匹配
- b16: 应用路径集中管理、破坏性操作二次确认、浏览器侧栏、浏览器直播二进帧

---

## [0.5.0] - 2026-06-07

- **浏览器直播** — 在聊天里直接看浏览器画面，登录墙可切换有头窗口操作。
- **深度反思** — Agent 遇到模糊问题会多轮自我审视，回答更靠谱。
- **桌面认证** — 本地认证 + 系统密钥环（macOS/Windows/Linux）。
- **SSRF 防护** — 用户提供的 URL 不再被用于 SSRF 攻击。

### 技术细节

- WebSocket 浏览器 screencasting，headless→headed takeover。Deep Reflection 多轮上下文重构。
- 本地认证中间件 + OS keyring。SSRF 拦截 + 截图临时文件清理。内容哈希去重。PDF viewer。权限快照 + 高危工具确认。

---

## [0.4.7] - 2026-05-24

- 技能安装支持目录和压缩包，不只是单文件。
- 更新脚本不再写死路径，能自动找到实际安装位置。

### 技术细节

- pattern learning compact args 持久化。Skill 安装支持 directory/archive。Electron 传递 `CYRENE_APP_EXECUTABLE`。UI flat surface 设计重构。更新脚本跨平台路径修复。

---

## [0.4.2] - 2026-05-24

- 终端颜色修复 — Claude Code 终端不再颜色诡异。

### 技术细节

- tmux 切到 `tmux-256color` 真彩色。C1→7bit 控制字符转换。Shell card 预览跨会话缓存。终端布局 ResizeObserver 自适应。

---

## [0.4.1] - 2026-05-23

- 深度研究报告可以生成超长报告了，逐段生成不受单次输出限制。生成前会问你想要多长。
- PDF 导出支持中文了。

### 技术细节

- Phase 3 逐段报告生成：模板定义骨架 → LLM 生成 JSON 大纲 → 逐 section 独立 LLM 调用 → 引用去重 → 薄 section 扩展。`ask_user` 选择长度。CJK PDF 排版（Noto Sans CJK SC）。
