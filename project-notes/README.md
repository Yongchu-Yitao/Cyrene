# Cyrene 开发记录

> 更新日期：2026-08-24
>
> 本文件是 `project-notes/` 中唯一的开发记录。它只记录三类内容：项目目的、
> 已经做了什么以及每个细节的目的、还剩什么没做以及为什么需要做。历史实施日志、
> 命令、提交号、测试数字、截图路径和重复的中英文版本不再保留。正式用户与开发者
> 文档仍位于 [`docs/`](../docs/)。

## 目的

- 让 Cyrene 只有一套可维护的 Runtime、Workbench 和 WebUI 正式架构，同时保护
  已有用户数据、公开 API、工具协议、历史导入、安装包和升级路径。
- 让 Agent Loop 在不破坏主动 `quit`、工具调用、DSML 防泄露、权限审核、恢复和
  既有 Provider 特性的前提下，减少重复调用、上下文膨胀和缓存前缀变化。
- 为 OpenAI-compatible Provider 维护互不继承 transcript 的 Decision Lane 与
  Execution Lane；Codex Provider 继续使用原有机制，两类 Provider 不自动
  fallback。
- 统一模型、网络、工具、持久化和可观测性边界，使延迟、Token、缓存、工具结果、
  重试和后台竞争可以测量、复现和逐项优化。
- 让 Workbench 支持连续对话、资源组织、项目记忆、研究、远程监督、CLI 和
  Cyrene 自控制，同时保持明确的作用域、权限、隐私、审计和可恢复性。

## 已完成：做了什么，以及目的

### 1. 后端架构、兼容与生命周期

- **正式领域目录已收敛。** Agent、Workbench、Model Runtime、Learning、Runtime、
  Observability、Knowledge、Channels、Tooling 和 Tool Implementations 分别拥有
  唯一实现位置。目的：让业务责任、依赖方向和维护所有权可判断，避免相同能力在
  顶层旧模块和新目录中形成两套实现。
- **Route 与业务服务分离。** FastAPI HTTP/WebSocket 适配器位于 `src/route`，只做
  请求校验、协议翻译和服务调用；领域服务不反向依赖 Route 或前端。目的：防止业务
  状态散落在路由中，也让 CLI、Electron、远程控制和测试能复用同一业务服务。
- **Web 生命周期与静态资源归入 `src/webui`。** 目的：让认证、应用装配、静态路径和
  前端构建只有一个 Owner，不与 Workbench 领域逻辑互相反向依赖。
- **公共门面继续保留。** `cyrene.config`、`call_llm`、`browser`、`subagent`、
  `memory`、`cli`、`tools` 和 `agent` 仍是稳定入口。目的：内部目录可以继续整理，
  外部调用方和历史代码不必同步大迁移。
- **历史 Python 路径改为惰性模块别名。** Loader 返回正式模块对象而不是复制
  Wrapper，同时恢复 `__name__`、`__spec__` 等元数据、支持 monkeypatch、虚拟
  `cyrene.modules` 和仍需 `python -m` 的别名。目的：保留动态导入、测试替换和冻结
  包行为，又不把旧实现恢复成死代码。
- **`local_cli.py` 只作为物理启动垫片保留。** 它委托正式 Runtime，不承载业务。
  目的：Electron 开发模式仍执行这一确切文件路径，删除它会破坏真实启动；把逻辑
  留在其中又会产生第二套 Runtime。
- **Runtime 有明确装配顺序。** 解析路径、创建目录、迁移旧库、初始化正式数据库、
  初始化 SOUL/Inbox/短期记忆/Learning、启动 Scheduler/集成，最后开放界面。
  目的：任何服务都不会在路径和数据迁移完成前抢先读写状态。
- **长生命周期资源归 Application Lifecycle。** Scheduler、后台任务、Browser、
  Search、MCP 和其他 Manager 统一注册关闭。目的：退出时能按顺序取消和清理，避免
  残留进程、SQLite 关闭后回调和 `event loop is closed`。
- **持久化位置保持分工。** 主 Runtime、项目 KB、加密配置、行为学习、SOUL、短期
  记忆、Debug Trace 和非 Electron Browser Profile 使用各自正式位置。目的：不同
  生命周期和备份语义的数据不会被一个临时数据库或 Route 内连接混在一起。

### 2. 数据库迁移与数据安全

- **旧 `cyrene.db` 在正式数据库初始化前迁移。** 目的：保证所有后续连接只看到一个
  已判定的数据源，不在新旧库之间随机选择。
- **迁移使用 SQLite Backup API。** 目的：把已经提交但仍位于 WAL 的数据一起复制，
  普通文件复制不能保证这一点。
- **先写临时目标并执行 `PRAGMA quick_check`。** 目的：只有可读且结构完整的副本才
  能替换正式目标，进程中断不会留下半个数据库。
- **迁移写入 Marker 且重复启动幂等。** 目的：区分“已迁移”与“碰巧同时存在两个
  文件”，避免每次启动重复复制。
- **只替换不存在或已初始化但无业务行的目标。** 目的：绝不覆盖已经有用户数据的
  新数据库。
- **保留旧源库作为回滚副本。** 目的：升级失败时仍有可恢复的原始数据。
- **新旧库都含数据但无 Marker 时明确报错。** 目的：冲突必须由用户或迁移流程决定，
  不能静默选择一个库造成数据丢失。
- **项目 Knowledge 兼容桥只在同一 `kb_<project>.db` 内建立引用。** 它把旧
  `kb_documents` 幂等映射为 Library 条目，复用原附件和索引，不复制文件、不跨项目
  读取。目的：旧资料立即可见，同时保持项目隔离、存储唯一性和重复运行安全。

### 3. Tooling 与公开协议

- **模型面对稳定控制面，不面对全部具体实现。** Direct Tool 保持固定 Wire Bundle，
  Tool Package 使用稳定 Gateway。目的：降低 Schema 体积与缓存抖动，并把实现移动
  与模型协议解耦。
- **Package 使用 `discover → describe → invoke`。** 目的：只有实际需要的能力 Schema
  才进入上下文，执行层仍能统一做权限和参数校验。
- **Catalog Snapshot 在每个 Run 冻结。** 目的：运行中修改工具开关不会令同一个
  Agent 的可用能力突然变化或让缓存前缀漂移。
- **Actor Policy 区分 Main、Execution 和 Subagent。** 目的：同一具体能力不能因为
  被发现就自动授予所有 Actor，避免子代理越权。
- **Capability ID、Concrete Name、Schema、Result Protocol 和错误类别保持稳定。**
  目的：工具搬家或内部重构不能破坏模型、权限、审计、前端卡片或旧会话重放。
- **关闭、过期或不属于当前 Snapshot 的调用由执行层拒绝。** 目的：Prompt 约束不是
  安全边界，最终权限必须由 Runtime 验证。

### 4. WebUI / Workbench 合并

- **这次合并按行为保持型重构完成，不是 UI 重写。** 唯一批准的产品变化是用户不再
  进入 classic shell。目的：把目录、入口和构建收敛与功能改写分开，回归时能够定位
  原因。
- **`src/webui/frontend` 是唯一前端源码根，`src/webui/static/app` 是唯一生成输出
  根。** 目的：生成物不再充当第二份可手工修改的源码，开发和打包读取同一来源。
- **classic 页面、样式、切换入口、双静态挂载和 `--agent` UI Selector 已删除。**
  目的：所有 Web/Electron 启动进入同一 Workbench，不再维护两个 Shell。
- **历史 build mode `agent` 只做 Workbench 规范化。** 目的：旧 Automation/Artifact
  不会因字符串失效而无法升级，但它也不会重新选择第二套 UI。
- **Workbench Data Store 取代散落全局数据。** 目的：初始数据、刷新、Chat 列表和
  UI Version 有明确 Owner，不再由多个 `window.*` 变量隐式耦合。
- **Typed Event Bridge 承接 SSE。** 目的：Chat、Tool、Browser、Goal 和 Notification
  的订阅、去重、重连与清理使用同一生命周期，避免重复 Listener 或漏事件。
- **唯一 Bootstrap 保留 Launch、Workbench/Quick Chat 和 Ready 协议。** 目的：
  Electron 后端探测、Quick Chat、首屏遮罩和打包启动不会因删旧 `app.jsx` 失效。
- **Browser、Search、Diff、Markdown、Math、Map 和 PDF 提升到 Shared/Feature 层。**
  目的：删除 classic 文件时保留 Workbench 正在使用的真实能力，而不是按文件名
  误删依赖。
- **Markdown 继续先解析再消毒，KaTeX/Highlight 不绕过 DOMPurify。** 目的：保留
  数学、代码和链接能力，同时不引入 XSS 回归。
- **PDF.js legacy bundle 继续保留。** 目的：这里的 legacy 是 Electron/Chromium
  兼容构建，不是待删除的 classic UI。
- **主题 Token、i18n/用量格式、Toast/确认框、Logo/Asset 进入公共层。** 目的：
  Workbench 与 Quick Chat 共享同一视觉、语言和反馈语义，不各自复制实现。
- **Quick Chat 是独立 Surface，但复用同一 API/Data/Event/Shared 层。** 目的：保留
  小窗口交互而不形成第二套前端。
- **没有借机升级 React/Electron/PDF.js，也没有引入 TypeScript、Vite、Next.js、
  Tailwind 或新组件库。** 目的：技术栈变化不会与源码合并叠加风险。
- **项目、Task、Chat、Agent、权限、Browser、Diff、Map、PDF、Knowledge、Library、
  Memory、Schedule、Settings、Integrations、Update、Channels 与 Profile 行为保持。**
  目的：判断“合并完成”的标准是整个产品合同，不是主页能打开。
- **用户数据、OpenAPI、Tool Wire、Actor Policy、SSE 语义和持久化格式未随 UI 合并
  改写。** 目的：前端重排不能成为后端协议或数据迁移的隐式发布。
- **未知事件和失败不能显示成成功。** 目的：视觉兼容不得吞掉权限问题、错误和终止
  状态来制造假通过。

### 5. Agent 双 Lane 与缓存

- **OpenAI-compatible Provider 使用两条独立 Lane。** Decision Lane 负责理解用户、
  判断是否执行、纯对话回答和必要澄清；Execution Lane 负责计划、完整工具循环、
  适应结果、验证和执行型最终回答。目的：保留对话路由能力，同时让执行上下文拥有
  连续且稳定的缓存前缀。
- **Decision Lane 只挂 `use_tools`、`ask_user`、`quit`。** 目的：Phase 1 不携带完整
  工具 Schema，也不会直接调用并“提升”具体工具，减少输入与职责重叠。
- **Execution Lane 挂完整工具集并允许 `ask_user`、`quit`。** 目的：计划、工具结果、
  重试和用户澄清始终留在真正执行任务的上下文中。
- **两条 Lane 不继承对方的 LLM transcript。** 目的：Phase 1 的推理和完整输出不会
  污染 Phase 2，Phase 2 的大工具轨迹也不会拖累下一轮对话路由。
- **Session Message Store 是唯一事实源。** Lane 只是可重建的模型投影，不是第三份
  消息库。目的：避免三套历史发生提交顺序、恢复和审计分歧。
- **Phase 1 通过 `ExecutionHandoff` 交接。** `request` 必须取原始用户消息，目的：
  执行不能依赖 Phase 1 可能失真的复述；`execution_brief` 只保留 300 字以内的意图、
  第一步和硬约束，目的：给执行方向但不复制规划；`attachment_refs` 只传稳定引用，
  目的：不重复附件正文；`conversation_delta` 只补 Phase 2 上次运行后错过的纯对话，
  目的：保持长期未运行 Lane 的语义连续性而不重建公共 transcript。
- **Handoff 固定字段顺序、稳定序列化且不写时间戳、延迟等随机值。** 目的：相同前缀
  能稳定命中 Provider Cache，也便于事件去重和回放。
- **Handoff 在 Phase 2 中是 Runtime 生成的隐藏 execution-request。** 目的：执行模型
  收到标准请求，但 Session 中不会复制 Phase 1 的 assistant/tool messages。
- **Phase 2 通过 `ExecutionOutcome` 闭合 Phase 1 的 `use_tools`。** `public_reply` 给
  用户，`state_summary` 保留会影响后续对话的状态，`artifacts` 保存文件/记录引用，
  `unresolved` 保留未解决项，`conversation_delta` 回传执行期间必要问答。目的：下一句
  “继续”或“改刚才文件”有足够上下文，但没有全量工具参数、输出、计划、重试、调试
  和 Telemetry。
- **Phase 2 进度只发 UI，不逐条同步 Phase 1。** 目的：用户能看到工作状态，但
  Decision Lane 的缓存和 transcript 不随每个进度事件变化。
- **等待状态归当前 Lane 所有。** Phase 1 自己提问则回答回 Phase 1；Phase 2 提问、
  等待或接受运行中指导时，下一条消息直接回 Phase 2。目的：避免多一次路由调用、
  丢失执行连续性或把澄清误当新任务。
- **Phase 2 完成时一次性同步 Outcome。** 目的：只有稳定终态进入 Decision Lane，
  中间计划和失败尝试不污染后续缓存。
- **Phase 2 异常、取消或恢复失败也产生结构化 Outcome。** 目的：Decision 的
  `use_tools` 始终闭合，UI 和下一轮不会停在永久“回复中”。
- **Decision `ask_user` 使用 Lane 内标准工具闭环。** 目的：澄清会改变该 Lane 的
  transcript，但不会通过临时拼接或重写历史制造非追加式前缀。
- **隐藏 Phase 1 调用计入 Usage。** 目的：用户看到的输入、输出、缓存命中和费用不
  会遗漏路由成本。
- **Session epoch 之外增加独立 Lane/cache epoch。** 目的：压缩、Prompt 或工具变化
  只冷启动受影响的 Lane，不让另一条 Lane 无故丢失缓存。
- **缓存身份包含 Provider Profile、Model、Lane、System Prompt Version、Tool Schema
  Hash、Context Policy Version 和 Lane Epoch。** 目的：真正影响前缀语义的变化会
  失效，普通用户消息和工具结果则只追加。
- **`cache_scope` 参与缓存隔离而不只是诊断标签。** 目的：Decision、Execution、
  后台调用和不同策略不会错误共享前缀。
- **重试复用原快照，失败尝试不提交。** 目的：Retry 不制造第二份历史，也不因随机
  错误永久破坏后续 Cache。
- **`phase1_decision_rules`、`fixed_ephemeral_system` 等动态块位于稳定前缀尾部。**
  目的：前面的 System/Tool 前缀仍可命中缓存；只有尾部变化的部分少命中，同时避免
  为固定前缀输入不必要内容。
- **运行中的 PowerPoint Episode 不再动态替换旧 Provider Message。** 普通模型投影
  只返回已提交历史；PowerPoint Tool Call/Result 只在显式 Lane 压缩时转换为带引用的
  Receipt，并同时推进该 Lane Epoch，仍处于协议尾部的 Episode 保持原样。目的：长
  PowerPoint 任务不会因每轮重算旧 Episode 而出现 `message_prefix_changed`，大参数和
  结果又能在明确的缓存冷启动边界释放。
- **Codex Provider 保留既有机制。** 目的：双 Lane 是 OpenAI-compatible 的定向
  改造，不重写已经稳定的 Codex Loop、OAuth、Quota 和 App-server 语义。
- **OpenAI-compatible 与 Codex 禁止自动 fallback。** 目的：认证、工具协议、缓存、
  Usage 和错误属于不同 Provider 家族；跨家族成功会掩盖真实配置错误并改变用户选择。
- **Economy 模式已取消。** 目的：不再为同一个 Loop 维护额外 Prompt、工具和终止
  分支，也避免诊断 Cache 时混入隐式行为档位。

### 6. Agent 专门行为与错误恢复

- **模型必须主动调用 `quit` 才算完成。** 目的：模型有时会在任务中途只输出纯文本，
  如果“无工具调用”自动结束，会把尚未执行或验证的任务误判为完成。
- **纯文本无工具响应继续进入既有控制逻辑。** 目的：允许模型解释当前状态，但不让
  这一段文本绕过完整任务终止协议。
- **无 Control/Tool 的纯文本会收到完整协议错误。** 错误明确说明该文本未发布、不能
  作为“上一条答案”引用；未完成时必须调用下一项真实工具，完成时必须重新形成完整
  终态并主动 `quit`。目的：不再把中途进度暂存成隐式最终答案，也不靠“见上一条”、
  “已汇报”等措辞匹配判断是否可交付。
- **空正文 `quit` 保留一次结构化 no-tools Finalization。** OpenAI-compatible Execution
  Lane 由 Coordinator 追加 `execution_finalization_request`，只传原始请求、`state_summary`、
  `artifacts`、`unresolved`、必要对话增量和“没有先前公开答案”的 Reply Contract；既有
  Execution transcript 不重写，Packet 也不持久化成第三条 Lane。目的：模型漏正文时仍
  能生成自包含答案，且不依赖被拒绝的进度文本或短语识别。Codex/Legacy 继续沿用原有
  有界恢复机制。
- **最终修复和 fallback 受限。** 目的：修复协议缺口，而不是用无界额外模型调用掩盖
  Loop 错误或吞掉已有结果。
- **DeepSeek/MiniMax 的 Reasoning 字段按工具回合保留。** 目的：Interleaved Thinking
  模型在多轮 Tool Call 中需要回放相关 Reasoning；普通回复的 Reasoning 则移除，避免
  上下文增长和私有推理泄露。
- **DSML 文本与流式过滤仍在。** 非标准文本工具调用会归一化为标准 `tool_calls`，
  DSML 标记从正文和 Stream 中剥离。目的：兼容模型偏差，同时不把内部工具协议展示
  给用户。
- **工具参数、Credential、绝对路径、内部 Trace 和私有 Reasoning 在公开事件前
  脱敏。** 目的：UI、CLI、Remote 和日志都只能看到用户可解释的安全投影。
- **Workbench 对意外停止生成本地化终态与 Retry。** 目的：后端异常不能留下无最终
  回答的永久“正在处理”状态，中文界面也不能落回硬编码英文错误。
- **中断、重试、Pending Question 和进程恢复保留稳定 Run/Turn 身份。** 目的：回答
  应恢复原执行，Retry 应替换失败轮次，不能新增重复用户消息或重复执行副作用。

### 7. 性能、上下文和搜索观测

- **建立八类确定性 Benchmark。** 覆盖纯对话、单工具、多工具、WebSearch、搜索加
  抓取、长历史、超大工具结果和后台竞争。目的：比较提交前后变化时不依赖真实凭据、
  网络抖动或生产会话。
- **Benchmark 输出端到端时间、模型回合、工具数、Prompt Token、事件数、RSS 增量和
  Trace 数。** 目的：优化不能只凭体感，也不能只看一个总耗时掩盖上下文或事件退化。
- **统一 `run_id → round_id → model_call_id/tool_call_id/search_id`。** 目的：任意慢
  Run 可以还原完整瀑布，并能解释每一次隐藏模型调用的原因。
- **阶段计时覆盖准备、排队、TTFT、生成、工具执行/消费、搜索子阶段和持久化。**
  目的：把 Provider 延迟、Cyrene 编排和 SQLite 开销分开。
- **Trace 采用追加式表与同 Run 批量写入。** 目的：保留可查询证据，同时避免每个
  阶段单独建立 SQLite 连接反过来放大热路径。
- **Trace 属性不保存 Prompt、URL、网页正文、工具正文或密钥。** 目的：性能证据不
  成为第二份敏感内容仓库。
- **Context Trace 覆盖主要模型调用点。** 内部 `_ctx` 在 Provider 调用和持久化前
  清理。目的：开发者可追踪上下文来源，而模型、会话和用户界面不会看到内部元数据。
- **大工具结果可外置到 Result/Artifact Store。** 模型只接收摘要、预览、大小、截断
  信息和引用。目的：完整证据仍可按需读取，但不会把数十万字符反复放进每轮 Prompt。
- **SimpleXNG 是唯一内置搜索 fallback，旧爬虫 fallback 已删除。** 目的：减少不可
  预测的多后端路径，并统一隐私、超时和调试语义。
- **SimpleXNG 本地流量不读取环境代理。** 目的：访问本机搜索服务不会被系统代理
  劫持或绕远；显式网络代理仍由集中策略控制。
- **网页抓取与 LLM 过滤并行。** 两者仍使用相同结果集、抓取数、过滤/综合 Prompt 和
  顺序。目的：只缩短关键路径，不用减少证据换速度；固定夹具中位延迟由 133.801 ms
  降到 93.379 ms，输出与综合输入保持一致。
- **WebSearch 返回原综合答案和已抓取摘录。** 目的：Agent 不必对相同 URL 默认再做
  WebFetch 和额外模型决策；摘录只是已获取证据的本地投影，不增加网络调用。
- **网络代理集中为显式策略。** 模型、Search、Extensions 和 Integrations 按各自配置
  选择代理。目的：避免 `HTTP_PROXY` 等环境状态让不同组件出现不可解释的行为差异。

### 8. 模型与 Provider 现有能力

- **候选配置统一归一化。** Model、Provider、Base URL、Key、Vision、Context Limit、
  Reasoning Effort、价格和缓存 Usage 使用同一配置图。目的：UI、调度、计费和实际
  请求不会各自猜测 Provider 能力。
- **候选支持去重、失败冷却、最后成功亲和和并发租约。** 目的：同一家族内的明确候选
  链可以稳定调度，不对故障端点形成请求风暴，也不让并发超过配置。
- **Provider Family 是强边界。** OpenAI-compatible 候选只能在本家族的显式候选中
  调度，Codex OAuth 只走 Codex。目的：Fallback 不能改变认证和协议语义。
- **OpenAI-compatible Preset 已覆盖常见自定义服务与 Kimi、GLM、OpenCode、
  OpenRouter、AMD、DeepSeek 和 MiniMax。** 目的：常见服务无需用户手工猜 Base URL、
  Model ID 和能力字段，同时仍保留自定义端点。
- **MiniMax/DeepSeek 协议偏差已有 Adapter。** Adaptive/Enabled Thinking、
  Reasoning Split、`max_completion_tokens`、Cache Token 字段、非标准响应容器、
  Reasoning 回放和 DSML 都在 Provider 归一化层处理。目的：Agent Loop 始终消费标准
  Message/Tool 形态，不散布厂商字符串判断。
- **Codex OAuth/CLI 保持独立。** OAuth 登录、模型发现、Quota、Reasoning Effort 和
  App-server 调用不被 OpenAI-compatible Adapter 重写。目的：保护已稳定的专门实现。
- **Cache Usage 会读取 Provider 的 cached token 明细。** 目的：缓存命中率和成本估算
  反映真实账单，而不是把命中 Token 都算成普通输入。
- **模型设置保持主模型立即可编辑。** Fallback、Secondary 和 Vision 收进带状态摘要的
  折叠区，去掉重复嵌套卡片，Save 位于正常文档流。目的：常用配置优先、低频配置不
  占空间，窄窗口和固定 540px Settings 高度仍可用。
- **OAuth Source Selector 一次只显示一个强调状态。** Outside Click/Escape 可关闭，
  Reasoning 只列当前模型声明支持的值。目的：不让用户选择模型实际不支持的 Effort。
- **Codex Quota 与货币 Budget 分离。** 只有首选模型为已连接 Codex OAuth 时显示；
  300 分钟窗口标为 5 小时、10080 分钟标为一周，缺失窗口不伪造。目的：Provider
  配额和用户预算是两种不同限制，不能混用。
- **Codex OAuth 不允许作为 Fallback、Vision 或 Secondary。** 目的：OAuth Session、
  App-server 和 Quota 不是可被普通候选池任意复用的 API Key 端点。

### 9. Work Tabs、固定资源与 Browser

- **面包屑已替换为最多三个 Task/Conversation Session Tab。** 排序先看 Pinned，再看
  用户主动打开的 MRU，最后才以 `updatedAt` 回填。目的：顶栏表示当前工作集，而不是
  把所有历史或后台更新时间误当作用户当前关注对象。
- **MRU 在打开、新建和切换时立即更新。** 目的：无需等待刷新，用户刚操作的对象就能
  成为最快返回入口。
- **Pinned、MRU 和“从顶栏移除”分别持久化。** 移除只隐藏视图引用，再次主动打开会
  恢复。目的：关闭工作入口不等于删除 Task/Chat，也不应终止运行。
- **Session Tab 与 Pinned Resource Shelf 是两个独立集合。** 目的：最近访问和显式
  Workspace 分享是不同语义，不能用一个模糊的 `global` 或 `pinned` 同时表达。
- **Task、Conversation、Browser、File/Snippet 保留不同默认 Scope。** Task/Chat 属于
  Project，Browser/File 默认属于 Owner Session；Scope、Owner、Activity 和 Pinned
  分开记录。目的：可见、可读、可操作、正在运行和是否恢复不能混为一个权限位。
- **Session 右键菜单即时读取 Browser 与 Attachment。** Screenshot 只存在当前 Menu
  State，关闭即释放；Attachment 按稳定 ID/URL/Name 去重。目的：菜单展示当前真实
  资源，不保留会过期或占内存的截图缓存。
- **点击资源先切到 Owner Chat，再恢复 PiP 或 Viewer。** 目的：Browser/File 的状态
  继续由原 Session 管理，顶栏只负责导航，不复制另一套 Viewer/Browser State。
- **Browser 是 Electron `WebContentsView`，菜单 Portal 不会全局隐藏它。** 只有真正
  覆盖大区域的 Modal 使用 `overlayObscured`。目的：右键菜单打开时 PiP 内容不白屏，
  同时保留 Settings/Search 对原生 View 的遮挡协调。
- **Pinned Resource Shelf 位于 Session Tabs 与 Search 之间。** 空状态只显示可访问的
  `+` Drop Zone，资源静止时只显示图标，Hover/Focus 才展开名称。目的：共享资源可达，
  但不持续挤占有限标题栏宽度。
- **Search 保持 168px，Shelf 与系统操作保留 10px 间距。** 目的：Accent Drop Outline
  不与 Search 粘连或重叠，用户能区分“固定资源”和“发起搜索”两个目标。
- **内部 Drag Payload 使用稳定引用，不携带本机绝对路径。** 目的：Renderer、其他
  Chat 和持久化记录只接触公开 Attachment/Resource 标识，不泄露文件系统结构。
- **File、Library Attachment、Selection Markdown 和 Browser 可固定。** 目的：用户能
  显式把关键工作资料变为所有 Session 可发现的资源，而不是把全文自动注入每个 Prompt。
- **无附件 Library 条目与选中文字先固化为 UTF-8 Markdown。** Storage Key 使用 ASCII
  UUID，显示名保留 Unicode，导出继续兼容旧 Unicode Key。目的：获得稳定可读取的文件
  语义，避免把临时 Selection 直接复制进所有上下文，也不破坏旧文件链接。
- **固定文件只注入名称、类型、来源和 Resource ID。** 正文按需读取。目的：全局可发现
  不等于全量内容进入每个 Agent Prompt。
- **固定 Browser 使用 `owner-control-others-readonly`。** Owner 可继续操作，其他 Session
  只能读取 Title、URL、Screenshot 和 Snapshot；Navigate/Click/Type/Reload/Upload 等
  在 Tool Policy 执行层拒绝。目的：共享页面证据但不制造多 Agent 同时操作同一 DOM 的
  竞态；限制不能只靠 Prompt。
- **PiP 与最小化 Browser 生成同一 Resource Identity。** 目的：同一页面从两种视觉状态
  固定时不会产生重复 Chip。
- **PiP 拖放用指针坐标与 Shelf 矩形相交。** 目的：Electron 原生 View 和 Pointer Capture
  会让 `elementFromPoint` 不可靠，几何命中能保留正常移动小窗与固定资源两种行为。
- **最小化 Browser 用 3px 阈值区分 Click 与 Drag。** Click 恢复 PiP，超过阈值才拖动；
  Favicon 失败时显示 Browser SVG。目的：不牺牲原有恢复行为，也不显示破图。
- **固定 Browser 不关闭、不转移原页面。** 目的：分享只增加引用，Owner 当前页面、历史、
  登录状态和控制权继续存在。
- **File/Snippet 拖到其他 Conversation 只进入 Composer 草稿。** 未挂载 Chat 使用 Pending
  Queue；Task Tab 明确拒绝；Drop 不自动发送。目的：跨对话传递上下文仍由用户检查，
  不因拖放触发不可逆消息。
- **Browser 拖到其他 Conversation 会创建同 URL 的独立页面。** 登录 Partition 可共享，
  但页面历史、控制权和 Session 不共享。目的：目标 Chat 可使用网页，又不会抢走原
  Browser。
- **原生文字 Selection Drag 不把消息行设为 `draggable`。** 目的：保留文字选择、复制、
  Link Drag 和 Composer 输入等浏览器原生交互。
- **键盘支持方向、Home/End、Enter/Space、Delete/Backspace 和 Session 快捷键。** 目的：
  Tab/Resource 不依赖鼠标，且与 Project Switch、Search 等既有快捷键不冲突。
- **58px 标题栏、50px Compact、Mac Traffic-light 安全区和 `no-drag` 区保持。** 目的：
  Work Tabs 不能破坏原生窗口拖动、控制按钮和紧凑密度。
- **响应式优先保持 Active、Pinned 和系统操作。** 旧普通 Tab 进入 Overflow，Search 在
  窄屏收缩。目的：有限宽度首先保护当前任务和退出/设置等关键操作。
- **共享状态同时用 Icon、Tooltip 和 Accessible Name 表达。** 目的：颜色只是增强，
  色觉差异和键盘/读屏用户仍能理解 Owner、Activity、Error 和 Scope。

### 10. Browser 浮窗与动态避让

- **PiP、最大化、最小化使用同一个 Browser Session。** 目的：模式切换改变呈现，不
  复制页面或登录状态。
- **默认 PiP 约 280×215，可在 240×180 与对话内容边界之间调整；最小化为 42px
  Favicon Button。** 目的：保留可操作网页的同时减少对消息区遮挡，最小状态仍可识别
  当前页面。
- **高频 Bounds 更新在 Renderer 去重、Main Process 约 32ms 合并。** 目的：降低 IPC 与
  原生 View 重排压力，同时保持拖动跟手。
- **拖动/缩放期间使用当前页面 Screenshot Proxy，释放后恢复原生 View。** 目的：避免
  `WebContentsView` 在高频 SetBounds 中闪白、错位或吞 Pointer，同时不伪造网页内容。
- **全屏往返保持原生 View Attached-but-hidden，并预设最终尺寸后重绘。** 目的：保留
  Browser 进程与页面状态，减少恢复时白屏。
- **消息按行避让，不给整个 Thread 加统一 Padding。** 只处理与浮窗纵向相交的少量
  Message Row，并选择更宽的一侧。目的：只收窄真正被遮挡的内容，避免整段历史变窄、
  滚动高度激增。
- **可用通道不足时保持覆盖，不压成不可读窄列。** 目的：中央浮窗或窄窗口下，可读性
  优先于强制避让。
- **避让使用 RAF 合并、Resize/Scroll/Layout 事件和可见区域定位。** 目的：拖动与流式
  回复时及时重排，但不扫描全部长历史。
- **重排保护滚动锚点。** 位于底部时继续贴底，阅读历史时恢复首个可见消息偏移。
  目的：文本换行增加高度时不把用户阅读位置跳走。
- **DOM 顺序不改变，Code Block 继续局部横向滚动。** 目的：保持阅读、Focus、ARIA 和
  Copy 顺序，并避免宽内容撑破对话。

### 11. 视觉与交互 QA 已固化的目的

- **分支树使用紧凑 Git 式轨道。** 主线用 Source-control Blue，分支/当前用 Accent，
  保留 56px 行节奏、44px 点击目标、完整 Fork Curve、截断、Hover、Focus 和
  `aria-current`。目的：在有限空间表达 Lineage，同时保证可点击和可读。
- **Launch Screen 在 React 前静态显示。** 使用现有 Logo、明暗主题、网络静默后淡出和
  20 秒安全期限。目的：首次数据加载时不闪现半成品 UI，也不会因失败请求永久困住用户。
- **Conversation Header 是可滚入内容之上的 Frosted Overlay。** 46px Blur、Saturation、
  Contrast 与约 66%→56%→32% 的 Surface Coverage 在主题 Token 上实现。目的：保留
  标题可读性和层次，同时让下方内容以柔和色块透出，底缘不会突然截断。
- **Conversation Rail 与 Transcript 共用一个 Top Glass。** 关闭两套伪元素背景并移除
  竖分隔线。目的：两列看起来是一个连续顶层 Surface，也避免重复 Backdrop Filter。
- **Conversation/Overview/Search/Composer 采用 Borderless + Shared Shadow。** Active
  通过背景识别，Focus Composer 不重染整个容器；Scrollbar 视觉隐藏但滚动保留。
  目的：减少卡中卡和焦点闪烁，不牺牲交互。
- **Theme Color 使用紧凑圆形 Preset、Check/Ring/Halo 和单一 Custom Entry。** Hue、HEX、
  Native Picker、Preview、Reset/Cancel/Apply 保留。目的：提高密度和对齐，又不减少
  自定义与撤销能力。
- **Model Picker 收敛到约 260px，并向上展开。** Label、Selected Value、Chevron 对齐，
  菜单支持 Escape/Outside Click；Reasoning 只显示模型声明值。目的：不遮挡 Composer
  和发送按钮，也不暴露无效选项。
- **Remote Sharing 默认折叠，但摘要说明默认授权项。** Tool Package 与 Project 采用
  两列，窄屏变一列；自定义 Chevron 与中性 Focus 不依赖浏览器默认样式。目的：完整
  权限可检查，但设置页初始不被大量 Checkbox 占满。
- **Settings 的 12 个导航项按组内数量共享剩余高度。** 目的：About 到达底部而不滚动、
  不留一整项高度的空白，并保持 Section Divider 与窄屏横向导航。
- **Generated Image 在对话中使用约 280px 正方形预览和 34px Footer。** `object-fit: cover`
  去除侧边空条，Footer 只保留 Filename、Open 和 Download，整卡继续可拖。目的：图片
  在 Thread 中可直接判断，又不占满宽度或破坏资源拖放。
- **Library 使用项目真实数据而非示例。** 分类栏、表格、检查器和详情区各自可滚动，
  表头固定、详情最高约中心区 50%、切换条目回顶。目的：长列表和长详情互不阻塞，
  用户始终知道当前 Selection。
- **Library 摘要只显示来源明确提供的 Abstract。** 生成描述和索引正文不冒充论文原文。
  目的：保护研究证据语义。
- **Library 内容按 Attachment 类型呈现。** Image、Audio/Video、PDF、安全 Markdown 和
  其他 Binary 使用各自 Viewer/Download。目的：复用原文件，不把索引文本当成附件。
- **Citation 支持 Plain Text 与 BibTeX，并与标题同一基线。** 目的：阅读与写作可以直接
  复用可靠引用，不增加只为装饰的边条或重复卡片。

### 12. 项目级 Memory Agent

- **每个 Project 有独立、可审计、可编辑、可恢复的 Memory Prompt。** 目的：记录本项目
  稳定习惯、明确偏好、已做工作/决策、验证有效的方法、已理解错误和未解决事项；它
  不是全局画像、不写 SOUL、不跨项目共享。
- **只有新建 Root Chat 注入创建时冻结的完整 Memory Snapshot。** 旧 Chat 不追随更新，
  Fork/Side Agent 继承 Parent Snapshot。目的：一个 Chat 的 System Prompt 在生命周期
  中保持稳定，后续修改不会重写历史或破坏缓存/可复现性。
- **空 Snapshot 也显式保存，部署前无字段的旧 Chat 不自动补注入。** 目的：区分“创建
  时确实为空”与“旧版本不知道”，不偷偷改变旧对话语义。
- **主 Agent 通过无自由文本的窄触发能力排队学习。** 目的：只表达学习原因，不允许把
  任意内容当作隐藏指令写入 Memory。
- **第 10 个完整 Turn 后每 5 个完整 Turn 自动学习。** Retry 替换不计数，Awaiting User
  半轮不计数，Side Agent/系统唤醒不计数。目的：以完成的 user→final 证据为单位，
  控制后台调用频率并避免半截内容。
- **菜单触发只读取最近完成的不可变 Context Snapshot。** 目的：流式回复期间不会读取
  半个答案，历史菜单操作也不重建可能不同的模型上下文。
- **同 Project/Chat/Round/Context Hash 去重，Project 内串行写入。** 目的：Tool、自动和
  菜单同时触发不会重复学习，同一 Project 不产生竞争版本。
- **Memory Agent 使用触发时主 Agent 实际成功的 Provider/Model/Candidate/Effort。**
  配置不可用时失败，不静默 fallback。目的：学习判断与原对话模型一致，也不会跨
  Provider 改变隐私、费用和输出语义。
- **Memory Agent 接收 Provider-normalized 完整 Messages 加最终 Assistant，再在尾部加
  唯一 Memory System 指令。** 目的：此前内容只是证据，不可覆盖学习规则；不重建、
  摘要或裁剪可以防止漏掉纠正和工具证据，超窗则明确失败。
- **Memory Agent 无工具、无公开回复、不修改触发 Chat。** 目的：后台学习不能执行
  副作用、打断用户或污染原 transcript。
- **习惯需两次独立证据，一次偶发行为不固化。** 明确偏好/纠正可立即保存，成功方法需
  结果/工具证据，错误需已理解，未验证结论进入 unresolved。目的：长期记忆只保存可信、
  可复用信息，不把猜测包装成事实。
- **Secret、Token、个人敏感数据、临时请求、寒暄和重复 Noise 被删除。** 目的：Memory
  不成为敏感信息或短期上下文的永久副本。
- **内容规范化 Hash 未变时不创建版本。** 恢复旧版在当前时间创建新版本并记录来源。
  目的：版本不可变、历史可审计，同时不产生无意义重复版本。
- **手工编辑使用 `baseModifiedAt` 乐观并发。** 异步学习冲突时带最新 Prompt 重学一次，
  再冲突则保留用户编辑。目的：后台任务永远不能静默覆盖用户刚做的修改。
- **版本对用户显示本地化修改时间，内部使用 UUID 与单调 UTC 毫秒。** 目的：界面易懂，
  内部仍能避免同毫秒碰撞并稳定排序。
- **编辑器只编辑项目 Prompt，结构化 Memory 留在原页面。** 当前/历史版本在同一 Textarea
  切换，历史只读并可恢复。目的：避免重复编辑入口和两套真源。
- **Project 顶栏菜单、Project Card 菜单和 Memory 页头部进入同一个编辑器；Chat 菜单在
  Compress 与 Delete 之间提供“生成记忆”。** 目的：入口位于对象相关位置，但所有入口
  最终操作同一个版本化服务，不复制状态。
- **Job 状态区分 queued/running/saved/unchanged/failed/conflict。** 错误区分无完成
  Context、模型不可用、超窗、无效输出、并发冲突和内部错误。目的：后台失败可诊断，
  但不记录 Key、完整 Secret 或 Chain-of-thought。

### 13. Cyrene App 自控制

- **主 Agent 只控制发起当前本地轮次的 Renderer 与非模型设置。** 目的：满足“让
  Cyrene 操作自己”的产品需求，但不授予任意 DOM、Selector、坐标、Script、IPC、
  本机 HTTP 或后台 Repository。
- **模型只看到 Main-only 的稳定 `cyrene_tools` Gateway。** Subagent 没有该包，具体
  Handler 名不进入 Wire。目的：控制能力按需披露，并防止子代理或模型绕过 Gateway。
- **Project、Chat、Data、Update、Lifecycle 和 Cross-session Message Handler 保持
  Internal-only。** 目的：产品内部可复用类型化服务，但 Agent 不能绕过当前可见 UI
  直接修改后台状态。
- **Model Settings、OAuth、Secret、QR、System Permission 和 File Picker 永不投影。**
  目的：凭据和用户仪式必须由人完成，不能被通用 UI 自动化代办。
- **Snapshot 只读当前最上层可操作 Surface，并真实分页。** 它不聚焦、不激活窗口，
  Chat List 只返回当前 Viewport。目的：模型只能看到用户此刻可见、可操作的范围，
  大列表通过 Scroll/Page 继续读取而不是硬编码截断。
- **稳定节点显式覆盖 Project Switcher、Search、Settings、New Chat、Chat Search/List、
  Composer Input/Submit 和 Browser Titlebar。** 目的：关键操作不依赖脆弱 DOM 顺序，
  其余标准控件再由通用 Projection 补充。
- **旧安装写入的全局 Tool Output 默认 12000 会迁移为不做统一字符截断。** Snapshot
  自身仍使用结构化分页。目的：完整语义树不会在分页之外再次被任意切半；这不代表
  普通大 Tool Result 可以无预算进入模型，后者仍由 Result Envelope/Context Budget 管理。
- **Snapshot Revision 只随可操作语义变化。** Streaming Text、Draft Length 和 Scroll
  Position 不让稳定节点过期，新 Approval/Menu/Overlay/Action 变化才推进。目的：高频
  重渲染不制造 Stale Storm，真正动作变化仍安全失效。
- **Inspect 绑定 Snapshot/Revision/Node，且不执行动作。** 节点语义未变时可跨全局
  Revision 继续，消失、换层或动作变化才 Stale。目的：模型能深入查看目标，又不能
  猜测未来 UI 或用错误中的新 Revision 强行重试。
- **Gesture 绑定 Snapshot、Revision、Node、Action 和 Input。** 执行前重读节点并检查
  Gesture Family、Risk、Scope 和节点级 Lease。目的：动作只能作用于模型实际看到且
  仍语义相同的控件。
- **Double Click 只接受显式声明的 `double_press/double_click`。** Browser Titlebar 通过
  Renderer Handler Maximize/Restore，不依赖 Focus、Pointer 或坐标。目的：专门手势不
  会误操作普通 Invoke Button。
- **DOM Projection 只补充标准可见控件。** Password、File、Hidden、Secret、User Ceremony
  和 Models Panel 被排除；显式节点与 DOM 去重。目的：获得通用覆盖但不重复节点或暴露
  敏感控件。
- **Composer Input 可按精确旧值 Set/Clear；Submit 是独立稳定节点。** Send/Guidance 为
  R2，Stop 为 R1，Disabled 不披露动作，实际点击真实 Button。目的：发送需要精确用户
  委托，停止可逆且不通过后台 Dispatcher 绕过 UI。
- **UI 命中显示短时流动描边，Reduced Motion 退化为静态弱描边。** 目的：用户能看见
  Agent 正在检查或操作哪个控件，动效不改布局、不阻塞输入，也尊重无障碍偏好。
- **Typed Settings 覆盖非模型设置并分类 Direct/Current UI/Existing Capability/User
  Ceremony/Presentation-only。** 目的：能安全表达的 Scalar 走 Schema，复杂或敏感设置
  继续使用专用界面/能力，不把所有配置压成字符串 Patch。
- **当前 Registry 明确覆盖 52 个 Scalar、31 个复杂 Control、5 个 Namespace 和 11 个
  非模型 Tab，Models 排除。** 目的：覆盖范围可核对，新增设置必须选择安全 Apply Mode，
  不能因未登记而被通用 Patch 猜测。
- **Settings Update 使用 Expected Revision/CAS，一次验证后原子持久化。** Shortcut Patch
  保留未指定动作，`null` 只重置指定动作；Agent 不能关闭自身工具或关闭 Secret Redaction。
  目的：不覆盖用户并发修改，也不允许 Agent 移除自己的安全边界。
- **权限分 R0–R4。** R2/R3 必须来自绑定的 `desktop_local` 用户轮次，经语义 Permission
  Reviewer 审核并签发参数/顺序/Operation 绑定的一次性票据；Remote/System/Generated UI
  不能制造票据。目的：高影响动作必须对应用户真实授权，不能靠关键词或重放扩权。
- **澄清续跑的授权上下文包含原始用户请求和后续用户澄清，不包含 Assistant 提问。**
  目的：短回答仍能结合原授权语义审核，又不会把模型自己的问题当授权文本。
- **审核失败或不明确默认拒绝。** Audit 记录参数 Hash、Risk、Source、Result、Diff 和
  Decision Source，Secret 脱敏。目的：Fail Closed 且所有 Mutation 可追溯。
- **Permission Card 由稳定 Meta 本地化生成。** 内部风险后缀/Fingerprint 不展示，旧
  纯文本卡只做显示兼容。目的：用户看到可理解的本地化动作，不泄露内部 Capability ID。
- **Renderer 用随机 Instance ID 注册 Host Bridge。** RPC 有独立 Token、Allowlist、
  Payload Limit、Timeout、Connection Cleanup 和 Window Ownership。目的：后端只控制
  本轮绑定窗口，页面卸载/导航后旧 Socket 不能继续操作。
- **Window Mutation 使用参数绑定 Idempotency Key。** 读操作不持久化，写操作缓存精确
  结果。目的：网络 Retry 不重复执行窗口副作用。
- **Snapshot 内容不进入稳定 Prompt 前缀。** 只有启用工具包集合变化才改变 Prompt/Cache
  Key。目的：动态 UI 状态按工具结果追加，不破坏主 Wire Cache。

### 14. 动态交互式 CLI

- **`cyrene chat` 是 Daemon 的薄客户端。** 它通过 HTTP + Per-run NDJSON 使用正式
  Workbench Conversation，不在 CLI 进程中直接调用 `run_agent()`。目的：Web、Electron
  和 CLI 共用同一 Run 生命周期、权限、恢复与持久化，不创建第二套 Agent Runtime。
- **模型回复、Phase、Plan 和 Tool 活动增量显示。** 目的：长任务期间持续有公开反馈，
  用户不必等完整 HTTP Body 才知道是否在工作。
- **NDJSON Decoder 支持一行跨 Chunk 和一 Chunk 多行。** 目的：Transport 不依赖网络
  分包边界，最终 `reply_done` 可校正累积 Delta。
- **关键运行状态使用 Per-run Stream，不订阅全局 SSE。** 目的：旧全局队列是竞争消费，
  CLI 不能抢走 WebUI 事件；每个 Run 的公开事件也更容易按 Session 和权限过滤。
- **Stream 只包含公开事件。** Tool 参数、Result、Reasoning、Credential 和绝对路径先
  脱敏/截断。目的：Terminal Scrollback 与 `--json` 常被长期保存，不能成为泄密通道。
- **Pending Question 与 Permission 在同一 Renderer 中续跑。** 有选项显示选择，无选项
  接收文本，回答绑定 Question ID 和 Client Request ID。目的：恢复同一 Round，不创建
  新任务或丢失执行上下文。
- **非交互环境不会自动批准。** 遇到 Awaiting User 时以非零状态和机器可读 Payload
  结束。目的：`--yes`、Pipe 或无 TTY 不能默认扩大权限。
- **`Ctrl+C` 是双击退出确认，不取消后台 Run；`Ctrl+D` 也只退出客户端。** 目的：终端
  断开不会破坏 Daemon 持有的长任务，稍后可以按 Cursor Resume。
- **`Ctrl+O` 临时查看折叠 Reasoning Surface，不写入滚屏。** 目的：保留交互观察能力，
  同时不把大量内部过程永久混入公开输出。
- **行式 UI 而不是全屏 TUI。** 目的：保留 Shell Scrollback、复制、SSH、CI Log 和
  Accessibility；不复制 Workbench 多栏 Dashboard。
- **非 TTY/`--json` 不启用 ANSI、Spinner 或 Rich Live。** 目的：管道和自动化获得稳定、
  可解析输出。
- **Browser/PDF/Image/Map/Diff 可以由 Agent 处理，但 CLI 不渲染图形。** 目的：Harness
  能力不缩水，CLI 也不承担 Viewer、Electron PiP 或系统应用调度。
- **不新增 WebSocket，不透传 Claude Code PTY，不实现 Raw Terminal Emulator。** 目的：
  复用已有 HTTP/NDJSON 契约，避免终端协议和安全面无边界扩张。
- **`python -m cyrene.runtime.host` 作为诊断兼容入口保留。** 目的：不因新增正式 CLI
  删除已有 Headless 使用方式；普通用户入口仍是 `cyrene chat`。
- **`/new`、`/resume`、`/status`、Mode、Research、Context、Config、MCP 和 Exit 走正式
  服务。** 目的：命令只做导航和薄协议，不在客户端重新实现 Project/Chat/Settings。
- **正式交互 CLI 不提供 `/clear`。** 目的：清空历史不是一个纯显示动作，避免终端命令
  以含糊语义删除或重写 Workbench Conversation；需要新上下文时使用 `/new`。
- **`/context` 区分 System Prefix、临时注入和 Conversation Messages。** 目的：用户能
  看懂 Token 与上下文来源，而不是只看到一个无法诊断的总数。
- **Renderer 异常不会中断响应读取或 Agent Run。** 目的：显示层故障不能升级为执行层
  的破坏性操作。

### 15. Cyrene-to-Cyrene 结构化远程控制

- **远程能力分 L1 使用、L2 监督、L3 接管。** Project/Chat/Task/Run/Artifact 和 Guidance/
  Approval/Pause/Resume/Cancel 已实现；桌面视频/键鼠未并入。目的：先用可审计领域命令
  覆盖绝大多数任务，把高风险桌面接管独立授权。
- **只开放版本化、严格 DTO 的 `/v1/control/*` 与固定领域命令。** 未知字段拒绝，
  Project 只返回 ID/Name/Status/Time/Task Count。目的：远端不能把内部 Workbench Store、
  Workspace Path、Model 或 Credential 固化成公共协议。
- **Chat Send 返回 `202` 与稳定 Run ID。** 后续 Status/Event/Guidance/Interrupt 都绑定
  Run ID。目的：Run 生命周期不依赖单个 HTTP 请求，也不能用任意 Chat ID 干扰其他执行。
- **Run Event 以 SQLite 单调 Cursor 持久化，提交后才唤醒读取者。** 进程内 Ring Buffer
  只负责低延迟，默认保留七天，Gap 明确 `truncated`。目的：断线和完成后可恢复，内存
  Buffer 不冒充 Durable Log。
- **硬重启把未完成 Chat Run 结算为 `process_restarted`。** 目的：已提交证据仍可读，
  但不会假装普通 Agent 进程跨重启继续；持久 Goal Loop 仍由自己的恢复器处理。
- **Guidance 和所有 Side-effect Command 使用 Idempotency Key。** SQLite 先原子占位，
  并发重复返回 In-progress，完成后重放相同结果，不同 Payload 冲突。目的：网络 Retry
  不重复发送消息、执行步骤或审批。
- **Remote Event 使用固定 Allowlist。** Reasoning、Workspace Change、Tool 参数、绝对
  路径和调试字段直接丢弃。目的：远程观察只包含 Reply、Plan、Progress、Pending
  Question、Artifact 和公开错误。
- **设备身份使用 Ed25519，密钥交换使用 X25519/HKDF，Envelope 使用 ChaCha20-Poly1305。**
  私钥优先存 OS Credential Store。目的：设备长期信任绑定公钥，不复用本机 Desktop
  Token 或可编辑设备名。
- **配对使用 LAN IP + 两分钟一次性短 Key。** 内部 Secret 至少 128 bit，短 Key 限速、
  首次领取绑定来源 IP、完成后立即失效。目的：用户输入简单，同时短码不能变成长效凭据
  或被暴力尝试。
- **Listener 只接受 Pairing Claim/Complete 和 E2EE Control Envelope。** 请求限长、来源
  校验、Rate Limit，未配对设备拒绝。目的：它不是 FastAPI 反向代理，也不能成为任意
  HTTP/SSRF 入口。
- **直连只接受 Private/Link-local/Loopback。** 端口在有限范围内选择并通过已认证
  Envelope 同步；离线换端口只扫描同一可信 IP 的有限范围。目的：支持局域网端口冲突，
  不扫描其他主机或任意公网地址。
- **Grant 是方向性的 Capability + Project Scope。** 最终权限取双方 Grant、本机 Policy
  和资源状态交集；撤销、过期或移除 Chat Context 立即失效。目的：控制端不能自行声明
  权限，也不能因为配对过一次永久访问所有 Project。
- **设备必须由用户加入当前 Chat Context。** 多设备调用显式提供 Device ID。目的：
  Agent 只能操作用户在这次对话中明确选择的远端，不做隐式自动发现或全局授权。
- **优先使用 `RemoteHarness` 调用已授权 Tool Package。** 控制端先 Discover/Describe
  精确 Capability 并按本机 Permission Mode 审批；只有用户明确要远程对话或 Harness
  不适用时才创建 Remote Chat。目的：简单操作少一层 Agent，同时两端权限都不绕过。
- **`RunRemoteCyrene` 只发送用户级任务。** 远端 Agent 使用它自己的 Model、Tool、Skill、
  Browser、Computer Use、文件、Integration、Sandbox 和审批。目的：获得完整 Cyrene
  能力，但控制端不直接获得任意 Shell、Python、HTTP 或 Concrete Tool RPC。
- **Remote Tool Package 有独立开关且不能递归授权 `remote_tools`。** 目的：用户逐包
  决定远端可发现能力，避免 Cyrene 通过另一台 Cyrene 无限转发。
- **`runs.wait` 使用订阅队列做有界 Long Poll。** 目的：等待远端完成不必高频轮询
  `runs.events`，同时仍可用 Cursor 恢复断线后的遗漏事件。
- **Remote Task 只用 `default` Permission。** Chat 可按兼容契约使用有限 Mode，但 Task
  调度/Step/Approval 不能借本机 Control API 的 Auto/Full Access。目的：远程工作不能
  因传输路径扩大执行权限。
- **Approval 必须匹配当前 Pending Question。** 普通澄清可远程回答，高风险 File Write、
  Shell、Credential 和桌面操作仍由被控端 Harness 决定。目的：Grant 不能替代目标设备
  的本地确认。
- **Artifact/Attachment 使用分块读取。** 单块有 Offset/Next/Size/EOF/Progress，完整文件
  不设总大小上限；控制端本地组装，Base64 不进入 Agent Context。目的：支持大文件并
  保持内存、Prompt 和进度可控。
- **Attachment 只能读取目标 Chat 明确引用的文件。** 即使来源在托管目录外，也不能
  借接口读取未引用路径。目的：文件访问由 Conversation Evidence 和 Project Scope
  双重约束。
- **Remote Store 与主 Runtime Store 分离为 Sidecar。** Device Identity 路径保持稳定，
  旧表一次迁移并暂留回滚。目的：远程 Audit/Nonce/Idempotency 不与高频 Agent Event
  争主库写锁，也不因升级改变 Device ID。
- **旧 `cyrene-relay`/WebSocket Relay 仅作为兼容代码保留，当前产品路径不实例化。**
  目的：历史集成不会因立即删除而断裂，但 LAN 产品不重新依赖 Relay URL；只有产品范围
  明确扩展到公网/NAT 时才可重新评估，不能把它误当默认 Fallback。
- **每个命令错误标记 Controller、Transport 或 Remote 来源。** 目的：本地数据库、网络
  和被控端执行错误不会互相冒充，便于恢复和审计。
- **公网 Relay/NAT、任意远程安装、Credential/SOUL/Memory/Global Permission 修改、
  Backup/Reset/Update/Restart/Shutdown 均不在当前授权面。** 目的：局域网结构化监督的
  边界不被高风险运维能力悄悄扩大。

### 16. Research Workbench 已有基础

- **Library 是 Project-isolated 的结构化文献库。** 条目、Collection、Tag、Reading
  Status、Star、Trash、Note、Annotation、Attachment、Relation、统计、字段/全文检索已
  实现。目的：论文不是只能塞入 Knowledge Metadata 的普通文档，而是可查询、可管理的
  一等对象。
- **CSL JSON 是引用元数据规范形态，RIS/BibTeX 是导入导出格式。** 普通 File/PDF 也可
  导入。目的：内部引用结构稳定，同时与常见文献工具互操作。
- **Zotero Local API 做 Project 级增量同步。** 目的：Zotero 是互操作来源而不是 Cyrene
  内部数据库，用户馆藏可复用但项目 Scope 不混合。
- **IEEE、APA、MLA、Chicago 与 BibTeX 输出已接入。** 目的：Library Evidence 能直接
  进入写作与复制工作流。
- **Attachment Reading State、Embedding 与 Agent Hybrid Search 已联动。** 目的：阅读、
  搜索和 Agent 使用同一条目身份，不各自建立文献副本。
- **数据库只保存结构化元数据和索引引用，原文件继续由 Workspace/Attachment/KB
  管理。** 目的：大文件不膨胀结构化数据库，数据库也不是论文内容的唯一副本。

## 还没做：剩余工作，以及目的

### 17. Agent append-only 与双 Lane 验证

- **归档报告恢复仍需改为纯请求投影。** 当前路径会在模型调用前临时替换旧的
  Provider-visible Message，调用后再还原。目的：报告正文可以按需恢复，但 Session
  Store 和 Lane 已提交前缀必须完全不变；应在本次 Request 的临时 Model Projection 中
  展开，不能修改旧消息。这是当前唯一已知的非 append-only 热路径。
- **真实 Provider 的长会话 Cache Benchmark 仍需持续。** 覆盖纯对话、连续执行、
  Decision→Execution、Phase 2 Awaiting User、运行中 Guidance、Retry、Compression、
  Process Recovery 和长期未运行 Execution Lane。目的：确认固定前缀、Lane Epoch、
  Conversation Delta 和失败快照在真实缓存计费中与设计一致。
- **Usage 需要继续做端到端核账。** 目的：所有隐藏 Decision、Recovery、Search、Memory、
  Permission 和 Final-repair 调用都应能归属 Run/Lane/Reason，避免 UI 命中率或费用看起来
  比实际更好。
- **Codex 需要保持非回归基准。** 目的：OpenAI-compatible 双 Lane、Cache Scope 和
  Transcript Policy 的修改不能间接改变 Codex History、Tool Loop、Quota 或 Resume。
- **Phase 1 的延迟和 Schema 仍要优化，但不再删除 Phase 1。** 原性能路线图希望消除
  普通请求的额外规划调用；当前决策改为精简 Decision Lane、轻量 Control Tools、稳定
  Cache 和纯对话直接回答。目的仍是减少首个反馈时间与输入量，但不能推翻已确定的
  两 Lane 架构。
- **Final Reply 的额外修复调用仍要减少，但不能取消主动 `quit`。** 目的：正常路径只用
  一次公开终态，异常路径最多做一次受限正文恢复；模型中途纯文本仍不能自然结束 Run。

### 18. Agent 预算、上下文和 Tool Result

- **P-003：补齐 Run Hard Budget。** 为 Model Round、Tool Call、Wall Clock 和连续无进展
  设置默认上限，Research/长任务用显式 Profile。目的：无界 `while` 或永远调用工具的
  模型必须以结构化 `budget_exhausted` 结束，并保留已有结果。
- **P-004：建立 No-progress Detector。** 对 Tool Name、规范化参数、Result Summary 和
  可见状态变化计算 Fingerprint。目的：重复 Search、Read、相同 Bash Failure 或页面抓取
  最多有限重试，之后必须换策略或结束。
- **P-005：所有模型调用使用 Purpose Profile。** Router、Tool Step、Final Reply、Search
  Filter、Naming、Repair、Research Section 等各自设置 Output Limit 与 Thinking Policy。
  目的：不再让 `max_tokens=None` 把小用途调用变成长生成。
- **P-006：建立 Performance Regression Gate。** 确定性 Fixture 断言 Round、Token、Tool、
  Event 和 Context Budget，CI 保存趋势但不使用易抖动的严格网络墙钟。目的：新增隐藏
  Round、无限 Result 或事件爆炸会稳定失败。
- **P-007：Tool Result 默认硬上限覆盖所有工具。** 旧“0 等于无限”不能作为通用默认；
  截断保留总大小、Summary 和 Artifact Ref。目的：任何未显式豁免的工具都不能把几十万
  字符直接塞入模型。
- **P-008：完成 `ToolResultEnvelope` 全面迁移。** 统一 Status、Summary、Preview、
  Content Ref/Type、Size、Truncated、Citation 和 Diagnostic，并保留旧 String Adapter 的
  有界迁移期。目的：Runtime 能区分模型摘要、完整证据、Binary、Error 与延迟读取内容。
- **P-009：大结果支持按需分页。** Read、Bash、WebFetch 和 MCP 通过稳定 Content Ref 按
  Line/Character/Section/JSON Pointer 读取。目的：读取 1 MB 内容时单次 Context 不超预算，
  下一页也不重复上一页。
- **P-010：每次模型调用前强制 Context Budget。** 目的：同一执行 Round 连续产生大结果
  时也受控，不能只在保存 Session 时事后压缩。
- **P-011：产品工作预算与模型物理窗口分离。** 即使模型支持 1M，普通 Agent 仍使用较小
  Soft/Hard Budget。目的：大物理窗口不是发送 600K 历史的理由。
- **P-012：历史预算按最近 Round、固定事实、任务状态和 Token 组合。** 目的：旧 Tool
  Episode 用 Summary/Ref 替代，但最近用户约束和未完成状态不能丢；单一 Message Count
  容易产生错误安全感。
- **P-013：上下文按内容 Fingerprint 去重并保留 Provenance。** 目的：System Block、Brief、
  Search Answer、Tool Output 和 Final Summary 的相同正文不会在一个 Request 中出现两份，
  Trace 还能说明去掉了什么。
- **P-014：Conversation、Audit 与 Model Context 投影彻底分离。** 目的：UI/审计可以保留
  完整工具活动，模型只收到预算后的语义视图；这也是修复归档报告临时改旧消息的基础。
- **P-016：继续缩小首轮工具 Schema。** 由 Decision/Task Context 选择最小稳定 Package，
  高频 Direct Capability 也按领域投影。目的：减少输入但不因每轮随意改变 Schema 破坏
  Provider Prefix Cache。
- **P-017：规划状态结构化为紧凑 Run Plan。** 保存 Goal、Acceptance、Known Facts、Next
  Step、Risk 和 Version，仅在变化时更新。目的：Plan 不作为长文本每轮重复和漂移。
- **P-018：合并细碎只读 Tool Step。** 常见审计可批量读取，独立调用同批提交，Shell
  多命令仍逐项权限校验。目的：减少围绕几十次 Bash 的模型往返，不以合并命令扩大权限。
- **P-019：优化渐进发现。** Run 内缓存已披露能力，高频低风险能力可预披露，Discover
  可带选中 Capability 的最小调用 Schema。目的：简单任务不必固定支付三段发现调用，
  执行层权限仍保留。
- **P-020：Scheduler 使用资源依赖 DAG。** Tool 声明读写资源、前置依赖和副作用。
  目的：独立只读/写不同文件可并行，冲突写入仍稳定串行，不用全局顺序屏障。
- **P-021：并行结果按完成顺序进入 UI/Agent。** 最终模型投影仍满足 Tool Protocol 顺序。
  目的：快速结果不必等待列表中更慢的 Search，降低 Result Queue Delay。
- **P-023：Guidance 区分追加约束、替换目标和立即停止。** 目的：非冲突补充在安全边界
  合并，不取消并重付完整模型调用；真正换目标才中断，原因进入 Trace。

### 19. Search、后台资源、事件和持久化

- **P-024：建立统一 Search Service。** 分离 Search Request/Evidence、Fetch Request 与
  Search Budget。目的：Provider Retrieval、Page Fetch、LLM Filter 和 Synthesis 分别
  计时计费，Main Agent 可以明确只要 Link、Summary 或正文证据。
- **P-025：普通 Search 默认单 Query。** Research Profile 才能扩展，并限制总数/并发。
  目的：同一事实问题不会发起 2–7 个高度重叠查询，扩展查询必须说明覆盖的新未知项。
- **P-026：Query 规范化、去重和 TTL Cache。** 区分 Provider、Language、Whitespace、
  Time Range、Domain Filter 和 Freshness Policy。目的：相同查询可复用，时效敏感请求可
  显式绕过且 Citation 保留检索时间。
- **P-027：原生 Search 使用 Output/Time Budget。** 连接、首字节和总时间分段超时，达到
  预算返回已有 Evidence 与 `timeout/partial`。目的：不等 120 秒长答案，也不因超时丢掉
  已获得来源。
- **P-028：复用 Search HTTP Client。** 按 Event Loop/Network Policy 共享连接池，限制
  Per-host 并发与 Keepalive，Lifecycle 负责关闭。目的：减少重复 TCP/TLS 建连并避免退出
  泄漏。
- **P-029：WebFetch 成为有界正文提取器。** 先验 Content Type/Length，Streaming 读到
  Hard Limit；HTML 提取正文/标题/日期/Canonical，JSON 提供 Path Summary，Binary 转
  Artifact 或拒绝。目的：导航、脚本和完整原始响应不进入模型，典型投影保持紧凑。
- **P-030：消除重复综合。** Provider 已生成答案时明确标注生成性质，Main Agent 默认不
  再抓取全部链接；需要核验只取关键来源。目的：普通 Search 最多一次答案生成。
- **P-031：SimpleXNG 默认零隐藏模型调用仍未实现。** 目标是默认返回少量标准化结果、
  规则过滤优先，只有调用方请求答案才做一次 Synthesis。目的：当前并行优化只缩短路径，
  仍保留 Filter + Synthesis 两次隐藏模型调用，必须由 Search Budget 显式授权。
- **P-032：建立前后台 Resource Governor。** 统一 Main、Permission、Search、Learning、
  Memory、Naming 和 Steward 的 Priority、Concurrency 与 Preemption。目的：前台 Run 活跃
  时低优先级任务不抢同一模型配额。
- **P-033：Learning 改为 Event-driven + Idle Batch。** 设置每日 Token/Call/Wall Budget，
  Failure 使用持久 Backoff。目的：无新证据时零模型调用，积压不会一次耗尽额度。
- **P-034：合并 Permission Review。** 确定性规则优先，同 Run/相同 Risk/Resource Scope
  使用短期、参数绑定的 Batch Ticket，高风险仍由人确认。目的：减少重复审核模型调用，
  绝不扩大授权范围。
- **P-035：Naming、Memory 和 Research 使用后台预算。** Naming 先本地规则后异步优化，
  Memory 只在 Idle 运行，Research 限 Section 并发。目的：首轮回复不等待命名，后台不抢
  前台，研究调用数在开始前可估算。
- **P-036：Reasoning Delta 合并。** UI 可用 50–100ms/字符阈值实时合并，持久审计使用
  更粗粒度或最终 Summary。目的：事件量显著下降，视觉流畅和断线收敛不受影响。
- **P-037：热路径改为增量持久化。** Message、Run、Event 和 Materialized View 分开，
  全量 JSON 只做低频导出/兼容镜像。目的：写一条中间消息的 I/O 不随历史长度增长，
  Crash Recovery 不依赖整份重写成功。
- **P-038：Workspace Change 增量检测。** 优先使用 Tool 记录的 Touched Paths，再用 FS
  Event/Git Status，无法归因才有界扫描。目的：无文件变化的 Chat 不遍历整个大型仓库。
- **P-039：内存、数据库与 Retention 收口。** Run 结束释放对象，大正文只存一份 Blob，
  Event/Learning DB 有 Archive、Checkpoint、Vacuum 和 Leak Benchmark。目的：连续运行后
  RSS 进入平台，过期数据空间可回收且恢复功能不受影响。

### 20. 可维护性与架构门禁

- **`agent.py` 仍需在原 Loop 内拆分。** Lane Coordinator、Run Budget、Progress Guard、
  Context Projection、Tool Settlement 和 Termination 应成为小服务。目的：降低约两千行
  编排文件的认知负担，但不能另起一套并行 Loop，也不能留下不可达旧路径。
- **Workbench 裸 Dict 要渐进替换为 Domain Model + Repository。** 从边界清晰的 Chat Run
  或 Memory 开始，序列化格式保持兼容。目的：字段漂移在类型/Repository 边界暴露，
  Route 与业务查询分离，不做高风险一次性全仓改写。
- **Browser 要拆为 Session State Machine、Electron/Playwright Transport、Action 和
  Snapshot/Ref Service。** 目的：生命周期、Takeover、Screencast、坐标和后端差异可独立
  测试，同时保持现有 Tool Signature/Result 不变。
- **Subagent 要拆为 Run State Machine、Message Transport 和 Settlement/Wait Service。**
  目的：运行、通信、工具和等待不再互相缠绕，已有外部工具合同不漂移。
- **Learning 要拆 Storage、Candidate Pipeline、Version 和 Execution。** `facade.py` 继续
  作为稳定 API。目的：每个服务可独立验证，旧 Skill 数据和 Route/Agent 调用不迁移。
- **Scheduler 后续按 Task Execution、Proactive、Steward、Heartbeat、Delivery 和 Cleanup
  拆分。** 目的：不同触发与资源语义不继续集中在一个调度器中。
- **Typed Exception 需要扩展。** 目的：Route、CLI、UI 和 Remote 使用稳定错误类别，
  不解析自由文本判断恢复方式。
- **导入时配置副作用仍需移除。** 配置改为 Lazy Accessor/Settings Object，Daemon/Host/
  CLI Bootstrap 显式加载，旧常量首次访问兼容。目的：`import cyrene.*` 不读加密配置、
  不修改 `os.environ`，测试与脚本不再受导入顺序影响。
- **Electron Main Process 仍需减责，前端大模块仍需拆分。** 目的：Native Window/IPC 与
  Product State 分离，Feature 不直接修改另一 Feature 的内部状态。
- **当前 Python Architecture Gate 未全绿。** 有 12 个新增大函数超出 Baseline、一个
  Static Import Cycle，`agent.prompts` Private Import Count 超预算。目的：这些需要通过
  原地拆分和依赖反转修复，不能扩大 Baseline 掩盖增长。
- **当前 JavaScript Complexity Gate 仍有超预算函数。** 重点是 Terminal Lifecycle Soak、
  Custom Plugins Panel 和 Plugins Page 匿名逻辑。目的：拆出有名字、可测试的小函数，
  不以移动到新文件或刷新 Baseline 规避门禁。
- **CI 仍需稳定的 Package Smoke。** 至少一平台完成产物启动、Daemon Health、最小 Agent
  调用和正常退出；Windows 按成本加入矩阵。目的：Source Test 全绿不能证明 Frozen/
  Electron Package 可启动。
- **Locked OpenAPI Snapshot 必须显式校验。** FastAPI/Pydantic 升级先更新 Lock，再单独
  审查 Snapshot。目的：不能用 Ambient Dependency 生成 Hash，也不能“顺手刷新”掩盖
  Route/Schema 漂移。
- **Release Gate 仍需覆盖新装、原地升级、Quick Chat、Browser/PDF Worker、SSE 清理、
  长会话、断网、权限拒绝、只读磁盘和空间不足。** 目的：Happy-path Unit Test 不能替代
  真正安装包和数据兼容。

### 21. Provider 可维护性

- **OpenAI-compatible 厂商逻辑仍需继续从 `client.py` 抽离。** 目的：Thinking、Endpoint、
  Cache Usage、Message Sanitize 和 Stream Parse 由 Adapter/Capability 表达，不继续增加
  Model Name 字符串分支。
- **Provider Request 应完整描述 Method、URL、Header、Body、Stream 和执行方式。** 目的：
  Dispatcher 只负责候选调度、HTTP Pool、Retry 与 Telemetry，不需要知道厂商字段。
- **Capabilities 应声明 Vision、Video、Structured Output、Context、Output Limit、Reasoning
  Effort 和 Cache Field Path。** 目的：UI、Cost 和 Runtime 消费同一事实，不从名称猜测。
- **归一化 Stream Event/Message 仍需完善。** 目的：Reasoning 位于不同 Delta/Content
  Block 时由 Adapter 解析，Agent Loop 始终消费标准 Tool/Content/Usage 形态。
- **纯 OpenAI-compatible Provider 可声明式注册，特殊协议使用小 Adapter。** 目的：Kimi、
  GLM、MiMo、Ollama、vLLM、Qwen 等不复制请求代码；MiniMax/DeepSeek 的特殊行为也不会
  污染通用路径。
- **Codex 不纳入这次 Provider 抽象重写。** 旧插件化设计中“薄包装 Codex”方案已被当前
  决策取代。保留的目的只有统一诊断身份，不允许自动 Fallback 或改造其内部 Loop。
- **Legacy Model Config Migration 必须先检测/预览/备份。** 目的：补 Provider 字段时不
  根据 Model Name 静默改用户配置；不认识的配置应明确报错并由用户选择。
- **Provider Fixture 仍需覆盖 Tool Call、DSML、Thinking、Reasoning Split、Anthropic/
  Native Content Block 和 Vision。** 目的：协议归一化用可回放的真实响应形状验证，CI
  不依赖在线 Key。
- **更广泛的 Anthropic/Gemini/Responses/PyPI Plugin 属于独立后续。** 目的：先完成当前
  OpenAI-compatible 维护边界，不把 Native Protocol、第三方加载和 Canonical Message
  大改一次叠加。

### 22. Workbench 与交互剩余项

- **Work Tabs 的 `+` 最近对象菜单尚未实现。** 目的：把 Running、Recent 和 Shared
  Resource 放在可搜索入口中，不把顶栏扩成十几个自动 Tab。
- **Tab/Resource 排序、恢复最近关闭和中键关闭仍需实现。** 目的：补齐浏览器/IDE 式
  工作集管理，但关闭仍只移除视图，不删除对象或停止 Run。
- **窄屏 Overflow Switcher 与完整键盘切换仍需验收。** 目的：在 720–1180px 不让 Active
  Tab 或右侧系统操作被挤没。
- **Browser Control Lease 与 Audit 尚未实现。** 目的：若未来允许其他 Session 请求控制，
  必须保持单一 Controller、User Priority、等待者和最后操作时间；当前仍是只读。
- **通用 Workspace 分享范围管理尚未实现。** 目的：Private、Task 与 Workspace Scope
  需要显式确认、常驻 Badge 和一键停止共享，尤其要保护 Login/Password/Payment Page。
- **Browser 拖动、Resize、Maximize/Restore、Minimize/Reopen 仍缺最新真实 Electron
  交互证据。** 目的：Static Review 无法证明 Screenshot Proxy、Attached-hidden 和 Redraw
  的时间顺序不白屏；这是已知 P1 验证缺口。
- **Browser 动态避让仍需长 Code/Table/Attachment、200% Zoom、Screen Reader 与 Keyboard
  回归。** 目的：文本重排不能导致 Overflow、Focus/Reading Order 或 ARIA Live 回归。
- **Canvas/WebGL/自绘控件需要显式 Semantic Adapter。** 目的：通用 DOM Projection 无法
  安全理解其动作，不能退回任意坐标/Script。
- **全局 SSE 需要真正 Fan-out。** 每个 Subscriber 独立有界 Queue、Session/Permission
  Filter、Finally Cleanup，慢消费者只丢自己的事件，Heartbeat 不进全局 History。目的：
  WebUI、其他 UI 和调试订阅者不会竞争消费；CLI 关键状态仍继续使用 Per-run NDJSON。
- **App Control 还需真实 Package/Credential/Upgrade/Platform Gate。** 目的：源码环境的
  Renderer/Host Bridge 验证不能替代签名应用、OS Ceremony 和安装器行为。
- **Current Surface 只适用于已注册 Electron Renderer。** 目的：CLI/Remote Turn 不能
  借用另一窗口的 Surface；未来若扩展必须重新定义绑定和用户可见性。

### 23. Remote 后续项

- **L3 Remote Desktop 尚未实现。** 若实施，应使用独立 WebRTC/TURN、Screen View、
  单独授权的 Keyboard/Mouse、不可隐藏 Indicator、User Preemption 和 Kill Switch。
  目的：桌面接管比结构化命令风险高，不能继承 L1/L2 Grant 或自动扩大 Tool Permission。
- **公网 Relay、NAT Traversal 和托管运维没有进入产品路径。** 目的：当前信任、地址和
  Abuse Protection 只对 LAN 成立；扩展公网前必须重做认证、可达性和运营边界。
- **Remote 后续修改仍需覆盖 Pairing Expiry/Reuse、Fingerprint、Grant Revoke/Scope、
  Signature/Nonce/Replay/Downgrade、Disconnect/Retry/Out-of-order、Cursor Gap 与泄密。**
  目的：安全和恢复性质必须作为持续合同，而不是只在首版测试一次。

### 24. Research Workbench 剩余项

- **Research Core 尚未建立。** 需要 Research Object、Provenance Edge、Artifact Manifest、
  Repository、Project Permission/Path Audit 与 Provider/Runner/Compiler Protocol。目的：
  Paper→Experiment→Run→Artifact→Manuscript 使用同一稳定 ID 和证据链，而不是三个互不
  认识的页面。
- **Library 还缺 DOI/Title 在线导入。** Crossref 用于 DOI 权威 Metadata，OpenAlex 用于
  跨学科关系/作者消歧，Semantic Scholar 只作为可选推荐源。目的：外部 ID 是补全来源，
  内部仍以规范化身份为真源。
- **Metadata Merge 与 Dedup 仍需完成。** DOI 优先，其次 Normalized Title + Author + Year，
  合并保留历史且不覆盖用户编辑。目的：同一论文不重复，也不把自动补全当更高权威。
- **Stable Citekey 冲突策略尚未完成。** 目的：文章中的 `@citekey` 必须跨同步和重命名
  稳定，冲突可解释而不是静默指向另一论文。
- **Zotero Web 增量与双向冲突语义未实现。** 目的：Zotero 是可替换 Adapter，云端版本
  变化不能覆盖 Cyrene 用户修改。
- **PDF Annotation 还缺 Page + Quote + Anchor。** 目的：笔记可回到原文位置，文章 Claim
  能引用真正证据，而不是只有无定位文本。
- **Saved Search 与 Literature Reminder 未实现。** 新结果应先进入 Candidate Inbox。
  目的：自动发现可复核，不直接污染正式 Library。
- **Experiment Runtime 未实现。** 需要独立 Durable Queue、Process Supervisor、SSE Log、
  Cancel/Restart Reconciliation、Command/Papermill、uv/Existing Venv、Parameter/Metric、
  Git/Lock/Data Fingerprint、Artifact Manifest、Compare 和 Failure Diagnostic。目的：
  Shell 只能执行命令，Agent Run 也不能回答代码、环境、数据、参数、Seed、Metric 和产物
  是否可重放。
- **Experiment Cancel 必须终止 Process Group，重启要 Reconcile。** 目的：关闭页面不应
  杀任务，取消也不能留下子进程；Orphan Run 必须有明确终态。
- **运行缓存、大权重和数据不默认进 Git。** Spec、Source、Lock 和小 Manifest 可版本化，
  大内容只保存 Hash/External Location。目的：可复现不等于把所有二进制提交仓库。
- **Manuscript Studio 未实现。** 需要 `.qmd`/Markdown 文件真源、Outline、Preview、
  `@citekey` Completion/Validation、Run Figure/Table 插入、Provenance、Quarto/Pandoc Compile、
  PDF/DOCX/HTML 与定位诊断。目的：文章可审查、可编译、可从引用/图表反查证据。
- **Agent 修改 Manuscript 必须走 Diff Review。** 目的：模型不能直接覆盖用户文稿，所有
  建议可接受或拒绝。
- **首版不自造 WYSIWYG、Kernel Protocol、排版引擎、MLflow UI、Remote Cluster 或多人
  实时协作。** 目的：先完成从可信 Paper 到可复现实验再到可核验文章的最小闭环。
- **Reproducibility Bundle 未实现。** 目的：文章、Citation、Experiment Spec、Environment
  Lock 与 Run Manifest 可以在干净环境检查，形成真正端到端交付。
- **产品仍需确认研究用户类型、Quarto/Pandoc 是否随包、Zotero 是否迁移必需以及近期是否
  需要团队/远程算力。** 目的：这些选择会改变 Experiment Spec 深度、跨平台包体、同步
  冲突和认证/Artifact Storage，不能由实现层擅自假设。
