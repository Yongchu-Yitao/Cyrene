# Changelog

## [0.6.12] - 2026-07-17

0.6.12 是 `0.6.11` 之后的正式功能与体积优化版本，完整收录其后已经合入 `main` 的实时会话收件箱、工具生命周期展示和分支树导航改进，以及本次 Electron 浏览器运行时与桌面打包瘦身。本版本为稳定正式版，不是 beta 或 prerelease。

### Electron 浏览器与桌面包瘦身

- Electron 桌面版的浏览器工具现在严格复用应用内嵌 Chromium、原生 `WebContentsView` 标签页和 `persist:cyrene-browser` 持久分区；用户可见页面与 Agent 的导航、快照、点击、输入、等待、网络日志、截图、滚动和标签页操作保持在同一会话中。
- Electron RPC 返回业务错误或发生连接异常时会直接向调用方报告，不再静默启动 Playwright 的第二套浏览器与独立登录配置，避免界面所见页面和 Agent 实际操作页面分叉。
- 正式桌面构建默认跳过 Playwright 安装和 Chromium 下载，PyInstaller 同时排除 `playwright` Python 包、浏览器可执行文件和 headless shell，消除每次更新附带的数百 MB 重复浏览器运行时。
- 保留非 Electron Web UI / CLI 的既有兼容路径：可选安装 `.[browser]` 使用持久 Playwright 会话，缺少 Playwright 时 `browser_navigate` 仍可退回只读 `httpx` 获取。
- 新增 `build/build.py --bundle-playwright`，供确实需要完整浏览器能力的独立 PyInstaller 构建显式打包 Playwright + Chromium；显式请求但运行时准备失败时构建会立即失败，避免产生表面成功但浏览器不可用的包。
- 更新 README、安装、架构、浏览器实况和文档站说明，明确 Electron 与非 Electron 两套运行路径及各自的安装要求。

### 实时会话收件箱与工具状态

- Workbench Context 面板新增实时“会话收件箱”，展示当前运行的队列深度、用户指导、工具结果和工具活动，并区分 queued、claimed、running、ready、consumed、completed、failed、cancelled 等状态。
- 新增会话收件箱查询接口和运行时 `live_snapshot`，把内存中尚未完成持久化的事件与 SQLite 记录合并显示；Context 面板在打开期间持续刷新，工具完成、指导认领和恢复状态可及时出现。
- 工具执行在处理器启动前发布带 `tool_call_id` 的 `tool_call_started`，完成事件沿用同一 ID，使前端原位更新同一工具行而不是生成重复记录；工具参数和结果继续经过敏感信息脱敏。
- 并行/串行工具批次维护 running → ready → consumed 的实时状态，收件箱元数据同时保留经过脱敏的参数和调度信息，便于检查当前任务究竟在执行、等待还是已经被 Agent 消费。
- 新增中英文状态、空态、错误态与骨架屏文案和样式，并扩充工具事件、实时收件箱和前端渲染回归测试。

### 用户指导持久化与运行恢复

- 用户在任务运行中发送的指导加入稳定去重标识；活动运行会同时检查内存与 SQLite，应用重启或并发请求后仍能识别已经接收的指导，避免重复注入。
- 收件箱持久记录携带公开消息 ID、创建时间和事件 ID；启动时会修复“收件箱已落盘、聊天 transcript 尚未写入”这一窄故障窗口，使指导不会因进程中断而从可见对话中消失。
- 最终回复流式生成期间到达的新指导不再被任务收尾吞掉：当前回复会转为中间边界，同一运行继续注入并处理最新指令。
- 已结束的聊天运行在保留窗口内可通过独立的 replayable 查询继续回放终止事件，改善 SSE 重连和页面恢复时的完整性。
- 加强异步持久化、后台任务排空、重复请求、崩溃修复、延迟指导和收件箱故障路径测试。

### 分支树导航

- 将 Workbench 对话分支树改为紧凑的 Git 历史式布局：主线与分支使用独立色调和更窄轨道，连接曲线、节点与内容卡在深层分叉中保持对齐。
- 分支行改为单行摘要、固定高度和统一列布局，当前节点标识更简洁；悬停、键盘焦点、减少动态效果和深度自适应轨道同步完善。
- 更新设计 QA 记录并增加布局结构、连接线样式、固定高度和文本截断回归断言。

### 版本、构建与验证

- Python 包、`uv.lock`、Electron 应用与 lockfile、README 徽章、文档站、微信通道、WebUI 静态资源缓存戳及对应测试统一更新为 `0.6.12`。
- 新增桌面默认不安装/不打包 Playwright、独立构建显式 opt-in，以及 Electron RPC 错误绝不回退 Playwright 的回归测试。
- 本地完整验证通过 1107 项 pytest；PyInstaller macOS 构建和冻结程序 smoke test 通过，冻结 Python 产物扫描确认不含 Playwright 包、`ms-playwright` 及其 Chromium / Chromium headless shell 目录。

## [0.6.11] - 2026-07-16

0.6.11 是 `0.6.10` 之后的正式功能与可靠性版本，完整收录其后已经合入 `main` 的模型候选会话亲和、聊天活动展示和滚动布局改进，以及本次尚未发布的浏览器链接引用、App Use 跨平台能力约束、Windows 桌面控制可靠性和 Workbench 时间线/分支导航更新。本版本为稳定正式版，不是 beta 或 prerelease。

### 模型路由与会话稳定性

- 为模型候选选择加入会话亲和：同一会话优先继续使用近期成功的 provider/model，降低跨候选切换带来的上下文缓存失效和响应波动。
- 对失败候选维护按会话隔离的冷却状态，并在候选排序时综合亲和与冷却信息；成功响应会刷新亲和记录，避免已恢复候选长期失去机会。
- 扩充候选排序、成功迁移、失败冷却与会话隔离测试，覆盖亲和命中和回退路径。

### 浏览器导航与链接引用

- `browser_navigate` 现在同时返回页面正文、可读链接文本、真实 HTTP(S) URL，以及在可交互浏览器中可直接传给 `browser_click_ref` 的引用，不再要求 Agent 猜测页面资源 ID 或拼接链接。
- Electron 与 Playwright 浏览器会过滤隐藏、无尺寸、重复或不可导航的链接，并为图片链接使用替代文本；HTTP 回退路径也会解析相对链接和纯图片锚点。
- 工具输出将链接清单置于正文之前，工具描述与 Agent 提示同步强调复用返回的引用/URL；新增 DOM、HTML、Electron 和输出格式回归测试。

### App Use 桌面控制

- App Use 仅披露目标平台实际可用的组合能力：Windows 不再暴露 macOS 专属 PID 定向输入和菜单命令，`visual_type` 只在底层定向输入能力存在时启用。
- `measure_coordinates` 支持并保存目标描述，后续 `visual_click` / `visual_type` 必须复用同一描述，避免自然语言回退与已校准坐标指向不同控件；会话断开时同步清理能力和测量状态。
- Windows 目标枚举改用原生 `GetWindowRect` 获取稳定窗口边界，即使应用未提供 UI Automation 树也能进行视觉定位。
- Windows 聚焦增加 `BringWindowToTop`、`SetFocus` 和 `AttachThreadInput` 回退，失败时返回可重试诊断与同完整性级别运行建议；Windows 不再错误进入 macOS PID 事件回退。
- Release 工作流在两个 Windows 构建任务中新增 Electron App Use 单元测试和 PowerShell provider 冒烟测试，提前阻断不可用安装包。

### Workbench 聊天与界面

- 修正“可见工具前言 + 推理 + 工具调用”的活动时间线顺序：可见回复会关闭前一活动，并为当前模型调用的推理和工具开启新的可点击活动；实时流与持久化恢复保持一致，临时边界标记不会写入最终对话。
- 改进普通聊天消息可见性并移除不应持久化的活动 trace，避免刷新后出现重复或位置错误的工具活动。
- 重新设计分支谱系导航：使用语义化按钮、当前分支与当前节点的分层状态、键盘焦点、悬停箭头、连接线和减少动态效果支持，提升可读性与无障碍交互。
- 扩大设置面板可用高度，修复滚动容器布局并补齐样式断言，减少长内容区域截断。

### CLI 生命周期

- 交互式 CLI 现在会在其 asyncio 事件循环关闭前排空后台任务和异步资源，避免退出过程中仍有数据库线程或后台回调访问已关闭的事件循环。
- 新增 CLI 入口回归测试，验证交互循环与后台关闭逻辑运行在同一尚未关闭的事件循环中，并保持帮助命令无运行时副作用。

### 版本与测试

- Python 包、lockfile、Electron 应用、README、文档站、微信通道、WebUI 静态资源缓存戳及对应测试统一更新为 `0.6.11`。
- 新增并扩充 Electron、Python 与前端回归测试，覆盖 Windows provider、平台能力过滤、坐标目标绑定、浏览器链接引用、聊天时间线拆分、分支导航和滚动样式。

## [0.6.10] - 2026-07-15

0.6.10 是 `0.6.9` 之后的正式功能与可靠性版本，重点重构行为学习为更直接的“目的 + 完整工具链”学习流程，允许学习 Agent 为复杂、连续、非交互工作流生成受审批约束的 Python 或 Shell 技能；同时让模型故障转移、上下文窗口、实时活动展示和费用统计都以实际响应模型为准，并显著减少 Workbench 在聊天、知识、记忆与日程页面切换时的空白、重复请求和后台扫描。

### 技能学习：从模式指纹改为目的驱动

- 每个真实执行轮次现在先生成一个简短、类似 Skill 名称的 `purpose`，并连同 Agent 工具调用和用户浏览器操作的完整细节链持久化；浏览器目标新增稳定 selector、ARIA label 与 placeholder 元数据，提升可复用步骤的语义质量。
- 学习 Agent 会在一次调用中比较当前目的与项目内全部历史目的及候选目录，按项目隔离地决定归入已有候选或创建新候选；不同工具实现可归并到同一目的，同时仍用详细工具链消除歧义。
- 保留“首次观察、第二次提示、第三次自动学习”的三次出现状态机，但移除自动路径中的指纹桶、相似度阈值、词汇表、模式审阅层、shadow router 与 replay-test 依赖，减少重复分类和本地启发式分叉。
- 复杂、连续且非交互的工作流可由学习 Agent 合成 Python 或 Shell 实现；生成脚本写入受限目录、记录来源与 SHA-256，并在保存前进行语法/结构校验、执行前再次校验路径和哈希。
- 生成的可执行技能统一标记为高风险并要求新的运行时审批，不会扩大原工具权限；低风险声明式工具链仍可通过 `RunLearnedSkill` 执行，并作为复杂技能的来源与 fallback 保留。
- 学习提示中的密码、令牌、Cookie、授权头、银行卡信息等敏感内容会被脱敏；缺少语义目标的浏览器文本也会在写入学习证据前清理，避免凭据进入模型上下文或生成脚本。
- 行为学习数据库启动迁移会删除不再使用的 fingerprint、pattern、vocabulary、review 与 replay 表/列/索引，把旧 `shadow` 技能转为 `active`，同时保留技能版本、补丁、运行记录和公开兼容 API 所需的数据。
- 学习 API 简化为 `/api/learning/process` 与 `/api/learning/rebuild`，学习页面移除旧模式、词汇表、回放测试和手工从 pattern 生成技能的界面，改为围绕目的、候选、已激活技能和详细工具链展示。

### 模型上下文、故障转移与费用

- 每个模型候选在调用前独立检查有效上下文窗口；主模型超限时可直接选择窗口更大的 fallback，所有候选都超限时返回明确错误，而不会把模型错误地放入失败冷却。
- 上下文上限按“该模型显式配置 → 已知模型内置上限 → 已配置候选中的最小已知上限”解析，避免一个较小备用模型错误压低已知主模型的窗口；完全未知且没有可参考候选时保持未配置状态。
- 同一运行轮次中的相同主模型 → fallback 模型通知会去重；亲和性排序本身不再产生伪 fallback 提示，只有真实主模型失败、冷却或上下文不足时才展示转换原因。
- Workbench 保存每次 Assistant 响应的实际模型，并把它传播到中间消息、待用户回答消息、最终回复、聊天 `lastModel`、上下文仪表和概览；模型切换后界面不再继续显示配置中的旧模型。
- 会话费用改为逐条响应按实际模型定价后汇总，正确处理一次会话内多模型 fallback、缓存命中/未命中 token 和 CNY/USD 混合价格；用户显式价格仍优先，已知未配置模型使用内置价，未知模型明确显示零费用。
- 为规划、初始化表单、验收标准、步骤验证/修复、记忆抽取、聊天摘要等结构化生成任务提高输出 token 预算，减少长 JSON 或完整方案被截断；这些调用仍保留原有超时和次级模型约束。

### Workbench 聊天与实时活动

- SSE 新增并回放 `reasoning_start`、`reasoning_delta`、`reasoning_done`，中间 Agent 调用只把推理流发送到 Workbench，不会把内部回答错误混入最终聊天回复。
- 每次 LLM 调用拥有独立的实时活动卡片，按顺序合并该调用的推理、工具开始/结束事件和模型信息；重连、重复事件、fallback 重试以及推理流与生命周期事件并行到达时会去重并保持稳定顺序。
- 实时推理可展开/收起并自动跟随最新内容，工具活动与回复文本并行显示；完成后的活动保留可检查轨迹，避免把多次模型调用压成一个持续旋转的笼统状态。
- 实时活动卡、运行心跳、用户消息、中间回复与最终回复现在进入同一条按真实时间解析的聊天时间线；不同 ISO 时区/精度的时间戳会按实际时刻比较，不再用字符串顺序造成消息错位。
- 乐观用户消息被服务端确认时保留本轮渲染的客户端时间锚点，并另存服务端时间，避免确认响应稍晚到达后把用户消息移动到已经挂载的思考卡下方。
- 推理与工具调用会持续归入同一活动卡，直到可见 Assistant 消息或用户指导形成明确边界；持久化时活动卡仍停留在产生它的时间点，不再把累计工具轨迹全部挂到最后回复或待回答问题上。
- 仅含推理、没有工具活动的已完成卡不会写入持久聊天记录；包含工具的活动卡会保留推理与执行轨迹，确保刷新页面后仍能按原始因果顺序查看。
- 修复活动卡在滚动 flex 容器中被压缩成空白细条、展开推理后新增工具导致高度锁失效、已完成推理从末尾空行打开，以及无推理卡仍可点击切换的问题；完成卡不再显示运行中 spinner。
- Composer、聊天概览和上下文仪表优先显示运行时或最近真实响应模型；上下文接口同时返回当前实际模型与聚合 usage，使 token 进度在 fallback 后继续准确更新。
- Workbench 主壳为聊天、知识、记忆和日程使用稳定挂载的隐藏 surface，切换页面时不再反复卸载组件；隐藏聊天不会继续为不可见任务创建订阅或触发无意义渲染。

### 知识、记忆、日程与加载性能

- 知识、记忆/学习和日程页面新增按项目或时间范围的内存缓存与并发请求去重：切回页面时同步显示最近数据，再在后台重新验证，避免空白占位、闪烁和重复网络请求。
- 页面在重新激活、窗口聚焦、恢复可见以及 Agent 工具/消息/会话/Goal Loop 事件后会防抖刷新；缓存仅用于快速首屏，不会被当成永久新鲜数据。
- 深链选择的知识文档、记忆、技能候选和日程事件会在缓存恢复或后台加载完成后继续应用，不再因项目切换或异步响应时序丢失选中项。
- 项目列表 `detail=summary`、知识与日程的 canonical project id 解析统一使用轻量 Workbench store 读取，跳过任务 invariant 修复和历史文件回填扫描；无效旧数据仍安全回退到完整修复路径。
- 知识文档列表与总数并行读取；日程任务与实体截止日期并行读取，并保持实体存储故障不会拖垮整个月历响应。

### 测试、文档与发布

- 重写行为学习测试，覆盖旧 schema 清理、完整目的目录比较、项目隔离、三次学习、敏感信息脱敏、Python/Shell 生成、语法与哈希校验、审批要求及单工具流程过滤。
- 新增模型候选上下文门禁、fallback 通知去重、已知/未知模型上限、内置/自定义价格、混合模型会话费用和实际响应模型传播测试。
- 扩充 Workbench 前端与运行时测试，覆盖实时推理分段、多 LLM 活动卡、重连竞态、页面稳定挂载、知识/记忆/日程缓存、轻量项目读取和上下文仪表。
- 新增持久活动时间线、乐观消息时间锚点、时区时间戳排序、可见回复边界、纯推理卡过滤、工具链合并以及活动卡高度/滚动状态回归测试。
- 更新架构、配置与设计 QA 文档，使技能学习、模型定价和 Workbench 页面生命周期与当前实现一致。
- 项目、锁文件、Electron、微信通道、README 版本徽章、WebUI 静态资源缓存戳及对应测试统一更新为 `0.6.10`。

## [0.6.9] - 2026-07-14

0.6.9 是 `0.6.8` 之后的正式维护版本，重点把 App Use 的可见目标激活流程改为 **先观察、再由 Agent 选点校准、最后执行主点击**，并为每次坐标决策保留可检查的截图与标记裁剪；同时优化 Workbench 项目/会话切换的持久化和聊天恢复路径，减少大型项目数据下的阻塞、重复传输与界面闪烁。本版本也恢复了所有正式版本标签的自动构建发布触发。

### App Use：可审计的坐标校准

- 可见目标激活的第一步由直接 `measure_coordinates` 改为 `visual_describe`：连接后先捕获并分析新鲜窗口截图，再由 Agent 根据截图像素选择候选中心点，避免在未观察当前界面时盲目定位。
- `visual_describe` 会把原始窗口捕获保存为可检查的本地图片产物，并在视觉分析超时或失败时仍返回捕获路径；默认视觉提示会提取控件、文本、布局和候选像素中心，同时明确把界面文字视为不可信数据。
- 重构 `measure_coordinates`：接受 `x`、`y`、可选裁剪宽高及 `captured` / `window` / `screen` 坐标空间，校验有限数值、范围、截图解码和坐标边界，并统一换算截图、窗口与全局屏幕坐标。
- 坐标测量会围绕候选点裁剪最新截图，绘制红白十字标记，返回标记位置、裁剪范围、三套坐标和映射信息；主模型支持视觉时还会描述十字下方控件及其是否居中，便于 Agent 检查后重新校准。
- 交互顺序调整为 `visual_describe` → `measure_coordinates` → `focus_window` → `click_at`；`click_at` 必须复用最新的 `window_point` 并显式允许前台输入，执行后恢复 Cyrene 焦点并保留主点击结果。
- `visual_click` 与 `virtual_click_at` 明确降为 fallback：主 `click_at` 尚未尝试时拒绝运行；主点击成功、结果不确定或可能已经派发动作时禁止再次点击，避免重复触发按钮或产生双重副作用。
- Electron 能力描述、Python 工具定义和 Agent prompt 同步更新新的观察、校准、前台点击与 fallback 规则；连接、重新测量和断开时会重置相应的视觉就绪、焦点与主点击状态。

### Workbench：切换性能与聊天恢复

- 新增 SQLite 顶层字段原子补丁写入，只在事务内更新 `activeProjectId` / `activeSessionId`，保留并发写入的其它项目状态，并继续维护 JSON 导出，不再为一次选择切换触发完整项目修复或工作区扫描。
- `/api/workbench/activate` 改为在线程中执行轻量选择持久化，支持显式清空活动会话，并只返回选择确认字段，避免每次切换都读取和回传可能达到数 MB 的完整 Workbench store。
- 前端模型不再把轻量激活响应当作完整 store 归一化，避免小响应覆盖当前项目状态。
- Workbench Chat 新增按项目缓存聊天列表、按聊天缓存详情和 subagent 数据；切回项目或会话时先立即恢复精确匹配的缓存内容，再在后台刷新，减少空白加载、闪烁和重复请求带来的等待。
- 增加项目代次与聊天 ID 校验：过期的后台列表或详情响应不会覆盖当前项目，缓存缺失时也会先清除旧会话内容，避免把上一段对话短暂显示到新会话中。

### 发布与测试

- Release workflow 恢复对全部 `v*` 标签的构建发布触发，移除仅针对 `v0.6.8` 的临时排除规则，确保 `v0.6.9` 及后续正式标签能够自动进入发布流水线。
- 扩充 App Use 回归测试，覆盖观察前置门槛、旧参数拒绝、截图产物、三坐标空间换算、标记裁剪、主点击优先级、fallback 门禁以及成功点击后的重复操作抑制。
- 新增 Workbench 轻量激活 API、SQLite 字段补丁与 JSON 导出测试，并补充聊天缓存优先恢复和后台刷新逻辑的前端回归断言。
- 项目、Electron、微信通道、README 版本徽章、WebUI 静态资源缓存戳及对应测试统一更新为 `0.6.9`。

## [0.6.8] - 2026-07-14

0.6.8 是 `0.6.7` 之后的正式维护版本，重点提升 **App Use 后台桌面控制的安全性、坐标可靠性与可验证性**，并把 **重复工具流程的技能学习** 从手工创建扩展为可审阅的参数化候选流程。此次版本还为工具执行、文件扫描、Workbench 聊天与本地存储补齐硬超时、事件循环隔离、并发保护和恢复逻辑。

### App Use：后台控制与视觉坐标

- 新增坐标优先的 `measure_coordinates`、`visual_click` 和 `visual_type` 工作流：Agent 必须先以最新窗口截图测量同一目标，统一完成截图像素、逻辑窗口和屏幕坐标映射，避免手工改写坐标或复用过期测量。
- `visual_click` 会在最多两次视觉定位后选择明确配置的语义 fallback；无障碍树不可用时不会错误降级到语义点击。每次操作都明确返回请求动作、实际执行动作、坐标与验证状态。
- `visual_type` 可定位可编辑目标、使用 macOS 进程定向 Unicode 事件或辅助功能写入，并以准确文本和截图变化验证结果；事件已投递但未确认文字写入时不会误报成功。
- 强化 `virtual_click_at`：过滤只覆盖整窗的无意义 AX `Group` 命中，转而使用应用 PID 定向事件 fallback；全程核验前台 PID 和真实指针位置不变。Windows 改为按坐标裁剪的 UI Automation 遍历，并补齐 `press`、`select`、`toggle` 后台动作。
- macOS 新增原生 Swift 坐标命中 helper（通用 arm64/x86_64），开发启动与打包前自动构建，并随 Electron 安装包作为资源发布；辅助功能消息限制为 750 ms，坐标命中另有 5 秒硬上限。
- macOS 连接时请求 `AXManualAccessibility`，新增不抢焦点的 `menu_command`（可按菜单名或如 Cmd+T 的快捷键匹配）和 `virtual_type_at`；点击 AX 失败时可通过 `CGEventPostToPid` 仅向目标进程投递事件。
- App Use 严格拒绝未知参数和无法生效的 fallback 参数，区分 `requested_action` 与 `executed_action`；`find` 支持 subrole、原生 action、automation id、class name、help 等更多定位字段。
- 细分 `accessibility_hit_test_timeout`、`accessibility_tree_timeout` 和 `vision_timeout`，并在截图成功但视觉分析超时时保留可审计的捕获结果与安全恢复建议。

### 参数化技能学习

- 新增重复工作流候选状态机：首次成功的多工具链仅记录；第二次匹配时让用户选择“立即学习 / 第三次自动学习 / 忽略”；选择延后后第三次匹配会自动生成技能。
- 从重复调用中提取稳定参数、类型和默认值，生成声明式、可参数化的工具脚本；执行器会校验输入、应用类型化默认值，并拒绝不安全的旧式 Python wrapper。
- 启动时自动迁移既有生成脚本到声明式格式；候选仅基于真实成功的多工具链，采用结构桶加语义比对，避免把内部学习消息或不相同的流程误合并。
- 新增候选查询与决策 API，Workbench 记忆页提供候选卡片、出现次数/参数数、学习状态、下一步说明及生成脚本预览；中英文文案和窄屏渐进式布局同步完善。

### 可靠性与性能

- 为所有工具调用设定硬性 wall-clock 超时并在关闭前有界清理；超时会变成结构化工具结果而非阻塞 Agent 循环。
- 文件读写、编辑、Glob/Grep 扫描、Workbench 聊天存储与项目加载移出事件循环；扫描会跳过常见依赖/元数据目录并设定时间、候选数和单文件大小上限。
- Workbench 文档写入新增进程内重入锁，聊天运行出现异常时始终发布终态事件、唤醒等待方并修复遗留的 `running` 状态；持久化提交冲突加入有界重试。
- 优化 App Use、Agent prompt 与附件视觉调用的超时和错误传播，避免长时间模型/IO 工作把交互请求拖入无响应状态。

### 测试

- 扩充 App Use 单元与集成测试，覆盖坐标测量、视觉点击/输入、缩放映射、后台语义与 PID fallback、参数拒绝、真实前台操作门槛、截图验证、菜单命令和资源打包路径。
- 新增重复流程候选、第三次自动学习、参数化脚本迁移/执行和不安全 wrapper 拒绝测试。
- 新增工具超时结构化结果、文件系统非阻塞扫描、聊天运行终态恢复、存储事件循环隔离、Workbench API 响应体超时与窄屏学习界面回归测试。

## [0.6.7] - 2026-07-13

0.6.7 在 0.6.6 的 App Use 与 Workbench Agent 基础上，补齐桌面应用的坐标级交互、原子键盘序列和文本选区能力，同时集中修复聊天创建、消息展示和运行中指导的延迟与一致性问题。此次正式版覆盖 macOS、Windows、Electron 工具桥、Workbench 后端、主聊天与 Quick Chat 前端，并新增相应的性能、并发与交互回归测试。

### App Use 桌面控制

- 修复 0.6.7 安装包中的 App Use provider 无法启动：macOS JXA 与 Windows PowerShell 脚本改由 electron-builder `extraResources` 放到真实文件系统，运行时优先从 `Resources/app-use/` 解析，不再把 `app.asar` 虚拟路径交给外部解释器。
- provider 资源缺失时改为返回不可重试的 `provider_unavailable` 结构化错误及明确的更新/重装建议；Agent 不再用 Bash、osascript、PowerShell 或直接编辑文件绕过用户要求的 App Use 操作。
- macOS 和 Windows 新增窗口内坐标操作：单击、双击、右键、悬停、拖拽、滑动和定点滚动；默认使用连接窗口的逻辑坐标，并校验所有目标点均位于窗口边界内，也支持显式屏幕坐标。
- macOS 通过 Quartz `CGEvent`、Windows 通过 `SendInput` 注入真实鼠标事件，支持移动时长、双击间隔、滚轮方向与幅度，并回报实际指针位置和可验证状态。
- 新增原子 `key_sequence`，可在一次前台会话内组合快捷键、文本输入、单键和暂停，避免多次调用之间焦点丢失。
- 新增 `select_text` 与 `set_selection_range`，可按文本内容、出现次数或字符范围选择可编辑控件中的文本，并在辅助功能 API 可用时核验选区。
- 工具能力描述、参数校验和 Agent 提示词同步更新；语义滚动不可用时明确引导使用定点滚动，不再用方向键伪装语义滚动。
- 强化前台聚焦和执行结果验证：区分 `verified`、`uncertain`、截图变化预期与无需截图的结果，避免把未观察到变化的操作误报为成功或盲目重试。
- 扩充 Electron App Use 单元测试，覆盖新能力暴露、参数转发、窗口坐标约束、焦点要求、截图差异和不确定结果处理。

### Workbench 聊天体验

- 主 Workbench 任务输入框与 Workbench Chat/Quick Chat 输入框支持直接粘贴剪贴板中的文件或图片作为附件；兼容仅通过 `DataTransferItemList` 暴露文件的 WebView，并保持普通文本粘贴的浏览器默认行为。
- 新用户消息在请求发出时立即以 optimistic 状态插入消息流，确保它显示在实时思考与工具轨迹之前；服务端确认后原位替换为持久化消息，失败时正确回滚。
- Quick Chat 与主 Workbench Chat 共用确认语义，只有服务端接受消息后才通知主窗口，减少重复消息和跨窗口顺序错乱。
- 运行中的用户指导同样即时显示并唤醒 Agent，持久化完成后返回确认；失败时移除临时消息，同时取消前端超时限制，避免长任务期间指导请求被过早判定失败。
- 新建或选择聊天时复用已有响应并跳过一次冗余 hydration 请求，减少闪烁、重复加载和刚创建聊天被旧响应覆盖的风险。
- 已完成的工具轨迹不再继续显示运行中转圈状态。

### 性能与可靠性

- 新增轻量级项目查询路径，聊天列表归属判断和聊天创建不再触发完整 Workbench 项目修复、任务回填及工作区文件扫描。
- 将项目查询和聊天存储读写移出异步请求主线程，并为超过 250 ms 的慢速聊天创建增加诊断日志。
- 工具结果与用户指导统一采用“先实时投递、后后台持久化”的收件箱路径；即使 SQLite 暂时阻塞，Agent 也能立即收到事件并继续运行。
- 加强实时事件的去重、提前 claim/complete、关闭期间终止及持久化竞态处理，避免重复指导、丢失确认和数据库写入时序造成的卡住。
- 收件箱遥测改为后台记录，降低指导领取与确认的关键路径延迟。

### 测试

- 新增打包资源路径回归测试，覆盖 macOS/Windows 外置 provider 解析、拒绝 `app.asar` 脚本路径、资源缺失诊断和 Agent 禁止 shell 回退规则。
- 新增 Workbench 两类输入框的剪贴板文件粘贴回归测试，覆盖 `clipboard.files`、`clipboard.items` 回退、普通文本保留与附件上传入口。
- 新增轻量项目查询和聊天创建性能回归测试，确保请求不会退回完整项目修复路径。
- 新增 Workbench optimistic 消息排序、附件保留、服务端确认、运行中指导、完成轨迹状态和新聊天免重复拉取测试。
- 新增收件箱在指导持久化阻塞时仍可立即唤醒 Agent 的并发回归测试，并覆盖事件去重与确认时序。

## [0.6.6] - 2026-07-13

0.6.6 在 0.6.5 的 Workbench Agent 基础上新增完整的 **App Use 桌面应用控制能力**，并进一步降低工具结果、聊天读取和模型故障转移路径上的延迟与误报。此次正式版同时补齐 macOS、Windows、Electron 打包、Python 工具网关、前端交互和回归测试。

### Added

- **统一 App Use 工具网关** — 新增单一、缓存稳定的 `app_use` 主 Agent 工具，通过 `list_targets`、`connect`、`call`、`status`、`disconnect` 五个操作完成桌面应用发现、连接、调用和会话管理；具体能力由连接结果动态披露，避免工具 schema 随应用变化。
- **macOS 桌面自动化** — 新增 JXA 辅助程序，基于系统辅助功能读取前台或后台窗口的语义结构，支持快照、查找、检查、按压、值写入、文本输入、键盘快捷键、窗口聚焦与焦点恢复。
- **Windows 桌面自动化** — 新增 PowerShell UI Automation 辅助程序，提供目标枚举、语义元素树读取和控件操作，并统一到与 macOS 相同的 App Use 协议。
- **后台应用连接与会话安全** — App Use 会话绑定进程启动时间和窗口身份，支持 TTL、陈旧会话/元素引用检测、自身窗口排除、多显示器全局坐标以及按需临时聚焦。
- **应用窗口截图与视觉描述** — Electron 可捕获已连接应用窗口，并通过 Cyrene 当前配置的视觉模型生成描述；返回截图尺寸、MIME 类型和可审计的视觉结果。
- **Safari 原生后台能力** — 对 Safari 动态提供 `browser_state`、`navigate` 和 `reload`，无需抢占前台焦点即可读取或改变当前标签页。
- **Quick Chat 来源捕获** — 打开快速聊天时记录此前的外部应用窗口，可通过最近外部窗口选择连接目标，并在操作完成后恢复原焦点。
- **App Use Agent 指南与默认配置** — 主 Agent prompt 增加发现、连接、语义引用、能力约束和操作后验证规则；工具默认启用、仅限主 Agent，并以独占桌面资源键参与调度。

### Changed

- **工具结果优先唤醒** — Workbench Inbox 在工具完成后先把结果投递给运行中的 Agent，再异步写入 SQLite；持久化、完成确认与运行终止之间增加协调状态，避免数据库忙等待阻塞已经完成的工具结果。
- **Inbox 遥测异步化与有序化** — 工具结果排队、消费和运行终止遥测移出关键唤醒路径，同时用串行尾任务保持事件顺序；关闭流程不再同步阻塞于持久化清理。
- **Workbench 聊天读取移出事件循环** — 聊天列表、详情、旧会话迁移和项目数据键读取改由工作线程执行，并为超过一秒的读取记录慢请求日志，避免 SQLite 或 JSON 读取冻结其它 API。
- **聊天前端请求生命周期** — 聊天详情与 subagent 请求支持取消和代次校验；切换会话、刷新或组件卸载时主动中止过期请求，减少旧响应覆盖新状态和 30 秒超时堆积。
- **运行状态与错误展示** — 前端更准确地区分运行中、取消、超时和工具错误；后端可识别 JSON 结果中的 `error`、`failed`、`failure`、`uncertain` 状态。
- **桌面端 RPC 扩展** — Electron 本地 RPC 服务新增受同一令牌保护的 `/app/rpc` 入口，并在应用启动/退出时统一启动和停止 App Use Manager。

### Fixed

- **模型亲和性误报 fallback** — 最近成功的非首位候选因亲和性排序被优先使用时，不再被误判为主模型故障转移；只有配置主模型确实失败或处于失败冷却期时才发送 fallback 提示并记录 `fallback_used`。
- **工具结果持久化竞态** — 修复结果被消费或 run 关闭早于后台 INSERT 时可能留下 queued 记录的问题；重复去重键会解析到已有持久事件并正确完成或取消。
- **Workbench 批量超时与卡顿** — 修复同步聊天存储读取阻塞 uvicorn 事件循环、导致多个 Workbench 请求一起达到客户端超时的问题。
- **过期聊天响应覆盖** — 修复快速切换聊天时较早的详情或 subagent 响应晚到并覆盖当前会话的问题。
- **Electron 发布包缺失 App Use 辅助文件** — App Use 主模块以及 macOS、Windows 辅助脚本已显式加入 electron-builder 文件清单。

### Tests

- 新增 `tests/test_app_use.py`，覆盖稳定工具 schema、主 Agent 可见性、RPC 参数校验、错误格式化、视觉描述和 Electron host 调用。
- 新增 `electron/app-use.test.js`，覆盖目标发现与过滤、连接能力、语义引用、写入和按压验证、焦点策略、Safari 后台操作、截图、陈旧会话及多显示器坐标。
- 扩充 `tests/test_workbench_inbox_guidance.py`，覆盖工具结果先投递后持久化、早期消费、关闭竞态、持久化失败和异步遥测顺序。
- 扩充 `tests/test_call_llm_candidates.py`，覆盖亲和性重排不产生 fallback、主模型冷却和真实失败仍正确产生 fallback 的场景。
- 扩充聊天分段与前端逻辑测试，覆盖结构化工具错误、请求取消、过期响应抑制、加载状态和 0.6.6 静态资源缓存版本。

## [0.6.5] - 2026-07-13

0.6.5 聚焦于**可中途引导的 Workbench Agent 执行模型**、**更可靠的模型故障转移**、**实体与视觉能力增强**，并补齐首次配置、聊天分段、桌面端布局与相关回归测试。

### Added

- **Workbench Agent Inbox** — 新增运行级、会话隔离的持久化事件收件箱。工具调用可在后台执行并把终态结果投递回对话；用户在工具运行期间发送的新指引会以更高优先级进入队列，能够打断尚未开始的批次并在下一轮模型调用中生效。
- **Inbox 恢复与审计** — Inbox 使用 SQLite 持久化事件、去重键、认领与完成状态，并记录独立生命周期遥测；进程重启后会恢复仍有效的用户指引，同时把无法恢复的孤立工具结果安全标记为失败。
- **工具批次并发调度** — 工具注册新增执行元数据与资源键，允许无资源冲突的工具并行执行；存在读写冲突的调用保持顺序执行。批次等待期间可响应新用户指引，并明确标记被跳过的调用。
- **模型故障转移事件** — 主模型失败并切换候选模型时，向 Workbench 发布可见的 fallback 状态，包含失败模型、后备模型与恢复信息，减少长请求期间的无反馈等待。
- **模型与端点成功亲和性** — 记忆最近成功的候选模型和端点，在后续请求中优先复用，同时保留原始配置顺序作为稳定回退；调用遥测新增候选排名、端点排名、延迟和是否发生 fallback 等字段。
- **图像附件与视觉调用** — 新增本地图像附件读取与多模态消息构造；浏览器截图在主模型已通过视觉能力探测时可直接交给模型分析，并返回所用模型与截断后的视觉结果。
- **视觉能力探测** — 模型配置验证流程新增轻量图片输入探测，持久化 `vision_capable`、探测时间与错误原因；设置页展示视觉能力状态。
- **实体精确查找** — 新增按完整标题和短 ID 前缀查询实体的数据库能力，方便工具在更新或删除前准确解析目标。
- **首次使用引导** — 新增 onboarding 引导逻辑与 WebUI 接口，帮助首次启动的用户完成基础配置，并通过测试覆盖状态切换与返回内容。
- **Electron 标题栏布局测试** — 新增桌面端标题栏对齐回归测试，覆盖窗口控件与内容区的关键间距。

### Changed

- **Agent 工具执行循环重构** — Workbench 对话不再必须同步阻塞在单个工具调用上；Agent 循环统一从 Inbox 消费工具结果与用户指引，并在收到中途指引后重新评估后续动作。
- **用户指引语义增强** — guidance 消息在 Agent message、prompt 与 API 层贯通，支持客户端请求 ID 去重、运行状态判断、应用确认和前端即时展示。
- **聊天运行管理增强** — Workbench chat run 增加 run ID 与 Inbox 生命周期绑定，完善取消、异常退出、正常完成和新指引到达时的收尾路径，避免跨会话串扰或遗留后台任务。
- **并行工具安全性增强** — Registry 为工具补充默认只读/写入元数据和规范化资源键；同一文件、实体或其他共享资源上的冲突调用不会被错误并行化。
- **实体工具错误处理改进** — 删除、列举、查询与追踪实体时更可靠地解析名称和短 ID；对不存在、歧义或不可删除目标返回更清晰的提示，并保持旧工具适配层兼容。
- **Workbench 对话 UI** — 优化运行中指引的提交与显示、消息分段合并、工具状态呈现和快速聊天行为；中英文文案同步补齐。
- **Profile 与工具能力展示** — Profile 页面适配新增工具元数据和视觉能力，使工具集与当前模型能力的展示更一致。
- **桌面端窗口布局** — 调整 Electron 主窗口相关布局与标题栏对齐，减少不同平台窗口控件造成的偏移。

### Fixed

- **聊天分段去重与顺序** — 修复流式中间消息、工具结果和最终回复在重连或快速更新时可能重复、乱序或丢段的问题。
- **中途指引丢失** — 修复工具长时间运行时新增用户消息只能等待、无法可靠进入当前 run 的问题；已持久化的 guidance 会被认领、应用并明确确认。
- **取消与收尾竞态** — 修复聊天终止时后台工具仍可能继续写入、Inbox 事件悬挂以及任务取消异常泄漏的问题。
- **候选端点重复冷启动** — 修复每次请求都从已知失败的首个端点重新尝试造成的额外延迟；成功亲和性会优先选择最近可用路径。
- **浏览器截图降级** — 当主模型不支持视觉或视觉探测失败时保持原有文本路径，不再误发不受支持的图片输入。
- **实体短 ID 操作** — 修复界面只展示 UUID 前缀时，实体工具无法据此准确查询或删除的问题。
- **Workbench 搜索与快速聊天回归** — 修复相关前端逻辑、缓存版本断言与搜索交互测试中的回归。

### Tests

- 新增 `tests/test_workbench_inbox_guidance.py`，覆盖 Inbox 持久化、去重、优先级、崩溃恢复、批次并发、资源冲突、取消和中途指引的完整生命周期。
- 扩充 `tests/test_call_llm_candidates.py`，覆盖最近成功候选/端点排序、fallback 事件、遥测字段与失败回退。
- 扩充 `tests/test_workbench_chat_segments.py`，覆盖 guidance、工具结果与流式消息的分段顺序和去重。
- 新增并扩充实体、视觉与浏览器测试：`tests/test_entity_tools.py`、`tests/test_browser_session.py`。
- 新增 `tests/test_onboarding_webui.py` 与 `tests/test_electron_titlebar_alignment.py`。
- 更新 Workbench 前端、搜索、Profile 与快速聊天测试，覆盖新增交互和 `0.6.5` 静态资源缓存版本。

## [0.6.4] - 2026-07-11

0.6.4 是一个以**任务生命周期管理**与**工作台打磨**为核心的更新，引入全局后台任务追踪与优雅关闭、内置 PDF 查看器、行为学习引擎深度增强，以及大量 Workbench UI 与运行时稳定性修复。

### Added

- **全局后台任务生命周期管理** — 全新 `task_lifecycle.py` 模块，提供 `track_task` / `cancel_and_wait` 原语统一管理 asyncio 任务生命周期。`SessionContext` 新增 `pending_housekeeping` 追踪，session 关闭时确保所有 owned task finalizer 被等待。`adaptive_budget.py` / `call_llm.py` / `search.py` 等模块接入，实现优雅关闭。
- **内置 PDF 查看器** — 集成 PDF.js (`routes_pdf.py` + `pdfjs/` 静态资源)，在 Workbench 中直接渲染 PDF 文档，支持缩放、搜索、页面导航。配套 `pdf-setup.js` 嵌入逻辑。
- **运行时生命周期管理** — 新增 `runtime_lifecycle.py` 模块，提供 `get_lifespan_manager()` 作为 Web 应用单一生命周期管理器，server shutdown 时统一清理后台任务与资源。
- **行为学习数据库增强** — `behavior_learning.py` 新增 `project_id` 列的数据库迁移逻辑，支持旧 DB 自动升级；新增 `pending_housekeeping` 关联，后台任务完成时自动触发学习评估。
- **Workbench 会话管理增强** — 阻止未启动 session 被暂停的错误路径；`workbench_store.py` 支持数据库重初始化时无损迁移数据。
- **运行时帮助信息增强** — `__main__.py` 扩展 CLI 帮助输出，包含 verbose mode 支持、后端服务状态列表（SearXNG、MCP 等）。
- **后台备份导出** — `backup.py` 将阻塞性文件压缩操作迁移到 `asyncio.to_thread`，不阻塞事件循环。
- **LLM 候选端点日志** — `call_llm.py` 新增候选端点自动切换日志，便于调试模型端点故障。
- **浏览器访问接管信号** — 浏览器结果新增统一的 `page_signal`，识别“内容暂不可用 / 请打开 App / 登录后查看”等临时访问门槛，并明确恢复冷却时间、单次重试和用户接管路径。
- **浏览器交互观察输出** — 新增统一的浏览器页面观察格式，导航、快照和点击结果会携带页面信号与受限访问提示，减少 Agent 对页面状态的误判。

### Changed

- **行为学习引擎重构（第二阶段）** — `behavior_learning.py` 核心变化：
  - 技能学习流程重写：从基于 `internal_review_tokens` 的自循环改为基于 LLM review 决策的管道（`_learn_step_core` → `_run_learning_review`），review 结果决定批准/驳回/修正；
  - 新增 `project_id` 参数贯穿所有查询，技能、工具链和 review 数据按项目完全隔离；
  - 提示词净化 (`sanitize_legacy_prompts`) 自动移除含 `system_reminder` 标签的旧 prompt 片段，避免历史数据污染新学习流程；
  - 截图产物保留优化：学习评估不再删除运行过程中的截图文件，仅做引用清理；
  - 单轮技能不再自动学习：`_AUTO_LEARN_MIN_TURNS` 门槛确保至少需要多轮交互才能形成技能，减少噪声。
- **Subagent 增强** — `subagent.py`：
  - 新增 `wait_until_settled()` 会话级原语，等 subagent 完全稳定后再收尾，解决"步骤假完成"问题（subagent 还在跑却标 completed）；
  - 新增 `dispatch_acceptance_repair` 支持：验收标准验证失败时自动触发修复子流程。
- **任务验收自动完成** — `routes.py` 新增 `_workbench_acceptance_fully_passed` / `_workbench_mark_completed_if_acceptance_passed`：任务的所有验收标准通过后自动标记 `status=completed`，无需手动操作。
- **Workbench UI 打磨** — `workbench.jsx` / `workbench.css`：
  - 任务计划编辑权限细化：步骤执行中禁用添加/重新排序操作（`2d6e429`）；
  - 接受标准卡片（acceptance criteria）UI 重写：按钮文本更清晰（"标记为满足" → "我已验证"), 证据内容与标准正文视觉分离；
  - `workbench-create.jsx` 优化文件处理与 UI 交互。
- **Web 应用服务器重构** — `server.py`：
  - 从 `lifespan_ctx` 装饰器迁移到 `LifespanManager` 类，支持 `add_shutdown_handler` 注册；
  - SSE 事件总线的清理逻辑与 lifespan 管理器集成；
  - 端口绑定与 SIGTERM 处理更可靠。
- **WebUI 构建工具升级** — `build-jsx.mjs`：
  - 支持 watch 模式（`--watch`），开发时自动增量编译 JSX；
  - 支持 `--measure` 输出编译耗时；
  - 依赖 esbuild v0.25+。
- **文件交付优化** — `routes.py` 文件发送端点增强路径解析，支持更宽泛的临时文件路径；`index.html` 新增 PDF.js 资源引用。
- **LLM 意图分类增强** — `call_llm.py` 改进 dispatch 意图分类的上下文传递，子 agent 场景下分类更准确。
- **浏览器自动化稳定性增强** — 导航和 SPA 路由变化增加有界的页面稳定等待；点击操作统一等待内容结果、增加 800ms 防抖，并覆盖按选择器、引用、文本和坐标点击，避免重复点击和过早读取页面。
- **浏览器视图遮挡保护** — Workbench 覆盖层打开时立即通知浏览器视图停止发布可见边界，取消待处理的尺寸更新；覆盖层关闭后再恢复同步，避免原生浏览器视图在设置/搜索面板上方错误显示。
- **构建与打包流程增强** — PyInstaller 打包排除 `node_modules`，macOS 构建兼容多架构目录；Electron 生产环境支持通过 `CYRENE_USER_DATA_DIR`、`CYRENE_CACHE_DIR`、`CYRENE_TEMP_DIR` 指定隔离路径，便于便携安装、诊断和打包冒烟测试。
- **配置与 CLI 运行时增强** — 新增 `CYRENE_CONFIG_KEYRING=0/false/no/off` 禁用系统密钥环；Electron 后端退出时对清理任务提供取消保护，并正确记录用户通过 `Ctrl+C` 退出的情况。
- **Goal Loop 并发与恢复增强** — 启动流程增加短临界区锁与失败回滚，避免重复点击产生重复执行实例或残留幻影记录；服务重启后会将中断中的步骤重新排队，并恢复 Workbench 投影状态。
- **项目请求兼容性增强** — 项目创建接口继续接受 `workspace_path` 蛇形命名，同时保留 `workspacePath` 驼峰命名。

### Fixed

- **任务页面编辑权限** — 计划步骤执行中，添加和重新排序按钮被正确禁用，防止中间状态篡改。
- **Behavior Learning 旧 DB 兼容** — 缺少 `project_id` 列的旧数据库在启动时不再因 `CREATE INDEX` 引用了不存在的列而崩溃。
- **Workbench 前端空值守卫** — 多个 JSX 组件增加 `null`/`undefined` 安全访问（可选链、空值合并），防止部分加载状态下的白屏。
- **WebUI Nonce 处理** — 修复 Content-Security-Policy nonce 的生成与传播逻辑。
- **`workbench-memory.jsx` 缺少闭合括号** — 修复 JSX 语法错误导致 esbuild 构建失败。
- **其他运行时修复** — SSE 事件发布的 session_id 保证；知识库搜索的边界处理；通知已读状态的多 tab 一致性。
- **浏览器访问门槛处理** — 临时访问门槛只允许一次带冷却时间的恢复尝试；仍受阻时引导调用 `browser_request_takeover`，不再无限重试或尝试绕过站点限制。
- **浏览器点击竞态** — 修复多种点击入口可能快速连续触发、点击后过早返回旧页面内容的问题。
- **任务恢复一致性** — 进程恢复时清理已失效聊天的 `pendingQuestion`，并将崩溃后仍标记为 `running` 的计划步骤安全恢复为 `pending`。
- **构建环境污染** — 修复 `node_modules` 被错误枚举进 Python 打包模块列表的问题。

### Tests

- 新增 `tests/test_performance_stability.py` — 压力与稳定性测试（87 行）。
- 新增 `tests/test_call_llm_candidates.py` — 候选端点切换与日志验证（26 行）。
- 新增 `tests/test_cli_entrypoint.py` — CLI 入口点参数解析测试（31 行）。
- 新增 `tests/test_workbench_dispatch_finalize.py` — dispatch 意图收尾验收修复流程（47 行）。
- 更新 `tests/test_runtime_fixes.py` — event session_id 等运行时修复断言。
- 更新 `tests/test_workbench_api_validation.py` — 同步新增 API 校验场景。
- 更新 `tests/test_behavior_learning.py` — 大量新增：web 搜索学习、浏览器事件、review 决策、提示词净化、截图保留、单轮过滤。
- 更新 `tests/test_goal_loop.py` — 验收标准独立验证与任务完成判定。
- 更新 `tests/test_proactive_workbench.py` — 用户消息捕获与系统主动触发场景。
- 更新 `tests/test_workbench_frontend_logic.py` — 动态 i18n 标签与 HTML sandbox 断言。
- 更新 `tests/test_workbench_init_plan.py` — 初始化计划生成断言扩充。
- 新增 `tests/test_browser_session.py` 场景 — 覆盖访问门槛信号、Electron 字段归一化、点击防抖、恢复尝试和用户接管。
- 新增 `tests/test_workbench_chat_run_recovery.py` — 覆盖异常退出后的聊天运行状态恢复。
- 更新 `tests/test_goal_loop.py`、`tests/test_workbench_api_validation.py`、`tests/test_workbench_sqlite_store.py` 与 `tests/test_workbench_frontend_logic.py` — 覆盖 Goal Loop 并发启动/恢复、请求字段兼容、数据库恢复与版本化前端资源。
- 更新 `tests/test_config_store.py`、`tests/test_quick_chat_feature.py` 与 `tests/test_agent_pure.py` — 覆盖密钥环退出开关、版本缓存和浏览器访问门槛提示。

## [0.6.3] - 2026-07-07

0.6.3 是一个以**技能学习 (Behavior Learning)** 与**浏览器集成**为核心的更新，引入技能生命周期管理（构建、查询、执行、删除）、浏览器用户事件追踪、多语言支持，同时大幅重构 Workbench UI 与学习引擎。

### Fixed

- **启动崩溃 (Startup Crash)** — 旧数据库文件缺少 `project_id` 列，但 `CREATE INDEX` 引用了该列，导致 `executescript` 抛出 `no such column: project_id`。已将 project_id 索引从 DDL 中分离，在 ALTER TABLE 迁移之后创建，确保列存在后再建索引。

### Added

- **技能执行引擎** — 新增 `GetLearnedSkill` 与 `RunLearnedSkill` 两个 Agent 工具，支持通过名称查询已学技能详情并以安全沙箱执行技能。`RunLearnedSkill` 包含高危步骤确认机制与 30 秒脚本执行超时，防止恶意或失控操作。配套 `tool_legacy.py` 适配层，保持与旧注册系统的兼容性。
- **技能构建与删除** — 学习 Agent 在识别到可复用的工具链（tool chain）时可即时构建技能块，`coordinator.py` 注入 `build_learned_skill_chunks` 以支持结构化技能展示。前端记忆页（workbench-memory.jsx）新增删除按钮与确认流程，配套 `/api/behavior/learned-skills/{id}` DELETE 端点与 `pattern.py` 删除注册表。
- **浏览器用户事件追踪** — 全新 `browser_user_events` 工具，记录并查询用户在嵌入式浏览器中的点击、文本输入、滚动、导航等操作。Electron 端新增 `BrowserViewportPanel` session/round ID 上下文管理，`beforeinput`/`click`/`scroll`/`keydown`/`popstate`/`hashchange` 六类事件捕获与 IPC 中继。`/api/behavior/browser-events` 端点持久化事件到 `behavior_browser_user_events` 表。学习引擎将浏览器事件纳入特征指纹计算与模式识别。
- **浏览器视图交互优化** — 导航栏与搜索覆盖层在用户操作后自动隐藏（`autoHideOverlay`），避免遮挡页面内容。IPC 通道支持 `browser-nav:hide-overlay` / `browser-search:hide-overlay`。
- **多语言 (i18n) 支持** — 浏览器用户事件工具的输出文本与工具描述支持中/英双语言，根据 `app_language` 设置自动切换。`i18n.jsx` 新增翻译条目覆盖浏览器事件 UI。
- **模型价格新格式** — `model_prices.py` 支持新格式价格提示（如 `GPT-4.1-nano: $0.1/1M in, $0.4/1M out`），兼容旧格式解析。`electron/main.js` 价格菜单同步更新。Workbench 对话页 `model-pricing` 渲染逻辑支持新的定价展示结构。
- **工作台 Profile 功能标签** — `workbench-profile.jsx` 新增工具对应的 Feature Label 展示，直观提示用户当前启用的工具集。
- **呈现设置 (Presentation Settings)** — 全局支持文字大小 (`textSize`) 与密度 (`density`) 偏好设置。`app.jsx` 新增 `useEffect` 将偏好写入 localStorage 及 `document.documentElement.dataset`，并监听 `cyrene-tweak-density-change` / `cyrene-tweak-text-size-change` 事件。quick-chat 模式同步应用该设定。
- **i18n 工具名表清理** — `chat-surface.jsx` 移除遗留的脚本系统条目（`ApproveScript`/`RejectScript`/`RunScript`/`ListScripts`/`LearnPatterns`/`LearnSkill`），新增 `GetLearnedSkill` / `RunLearnedSkill` / `save_project_memory` / `retire_project_memory`。

### Changed

- **行为学习引擎全面重构** — `behavior_learning.py` 核心变化包括：
  - 新增 `behavior_turn_tool_chains`、`behavior_learning_agent_reviews`、`behavior_browser_user_events` 三张表，按 `project_id` 隔离数据；
  - 学习引擎新增 `project_id` / `project_key` / `session_kind` 多租户字段，所有查询支持项目维度过滤；
  - 特征指纹计算纳入浏览器用户事件维度；
  - `_INTERNAL_TOOLS` 移除 `LearnSkill`（使其对学习流程可见），`_HIGH_RISK_TOOLS` 新增浏览器自动化与 Shell 操作类工具；
  - 新增 `browser.user.*` 工具系列至 `_AUTO_REPLAY_BLOCKED_TOOLS` 防止因自动回放导致意外的浏览器操作；
  - 调度器与 replay 模块支持并发技能运行统计更新；
  - 新增 `behavior_replay_tests` 集成测试框架，覆盖回放链的端到端验证。
- **`pattern.py` 全面重写** — 移除旧 scripts 系统（`list_scripts`/`approve_script`/`reject_script`/`run_script` 及关联工具注册），替换为基于 learned_skills 的一致 API：`list_learned_skills(project_id)`、`activate_learned_skill`、`deprecate_learned_skill`、`run_learned_skill`、`delete_learned_skill`、`learn_from_turn`、`rebuild_learning_state(project_id)`、`list_tool_chains(project_id)`。所有函数新增 `project_id` 参数支持多项目过滤。
- **技能块注入 Agent 提示** — `coordinator.py` 中 `_run_chat_agent` 在构建 system prompt 时调用 `build_learned_skill_block()` 将已学技能名列表注入 agent 上下文，使 agent 感知可用技能。
- **配置文件读写权限增强** — `tool_executor.py` 新增工具级权限检查：`Write` / `Edit` / `read_file` / `write_file` 在读写前检测 workspace 边界，超出时弹权限提升请求。`scheduler.py` 增设 60 秒 shell 超时防止任务挂起。
- **Workbench UI 大幅重构** — 涉及学习板块布局、技能链卡片（`.learning-chain-cards`）、记忆页统计面板（`.memory-stats`）、更新笔记 Markdown 渲染、错误状态等。`workbench.css` 整体风格统一，侧栏折叠与面板隐藏按钮统一样式。对话组件 (`workbench-chat.jsx`) 增强消息去重与分段更新逻辑。
- **Workbench 核心运行时优化** — `workbench.jsx` 多项改进：
  - `reloadWorkbench` / `fetchAndMergeSession` 新增 `{showLoading: false}` 参数支持静默后台刷新（不显示加载壳）；
  - goal-loop 运行时事件改为轻量 in-place store 合并（原地 patch `goalLoop` + `status`），取代全量 store 重载，1.6s 尾随静默刷新拉取服务端持久化字段；
  - `setObscured` 改为计数器机制（`obscuredRef`），支持 settings 与 search 覆盖层同时打开时不互相覆盖；
  - SearchOverlay 改为 `ReactDOM.createPortal` 渲染到 `document.body`，解决 z-index 层级问题；
  - 任务区域加载壳（`loading shell`）仅在缺少 project 或 session 时显示，避免 goal-loop 静默刷新时闪现 loading 状态。
- **知识库分页与惰性加载** — `workbench-knowledge.jsx`：文档列表改为分页加载（每页 80 条，支持 `?limit=` + `?offset=`）；文档内容（chunks）仅当用户切换到「内容」页签时才发起懒加载请求（`include_chunks=false` → `true` 按需拉取，`chunksLimit=200`）。`mergeDocs` 按 ID 去重防止翻页重复。
- **数据库索引与工具名规范化** — `db.py`：新增 `_canonical_tool_for_stats()` 将工具名统一映射为稳定 feature key（含中文别名→英文键，如"浏览器"→`browser`），profile 使用量统计从按行聚合改为全量聚合后客户端截断；`kb_documents` 表新增 `idx_kb_documents_updated_at` 索引。
- **WebSocket 浏览器事件录制** — `routes.py` 中 WebSocket 处理器新增 `_record_browser_event` 异步函数，将 `control_start/stop`、`click`、`scroll`、`key`、`text_input` 事件经 `behavior_learning.record_browser_user_event` 持久化，附带当前页面 URL 与标题上下文。
- **直播分段语义去重** — `routes_workbench_chat.py`：新增 `_live_segment_dedupe_key` 基于内容+附件的稳定语义指纹，与 `_published_intermediate_message_ids` 联动，防止直播流扫描时同一段 prosa 因先后拥有不同 ID 被重复渲染。
- **孤立 Fork 元数据清理** — `routes_workbench_chat.py`：`_prune_orphaned_fork_metadata` 在读取会话 payload 时自动检测并擦除引用已不存在源聊天的 `forkedFromChatId` / `forkedAtMessageId` / `forkMessage` 字段。
- **更新笔记 Markdown 渲染** — `settings-overlay.jsx` 升级更新笔记渲染，支持 Markdown（标题、列表、代码块）与统一链接样式。关联链接重构为一致性按钮组。
- **设置覆盖层工具列表更新** — 新增 `browser_user_events` 等工具至工具列表 UI。
- **聊天 Fork 处理** — 删除的聊天来源自动从其子 Fork 分离（`detach_deleted_source_from_child_forks`），防止孤立引用。配套 `route_workbench_chat.py` 中 `PUT /api/workbench/chats/{id}/detach-source` 端点。
- **记忆页统计优化** — 阴影验证与真实使用量分别展示，按 workspace 隔离数据。
- **自适应预算分配调整** — `adaptive_budget.py`：5 小时窗口剩余预算分配比例从 30% 提升到 40%，加快短期预算消耗节奏。
- **提示词优化** — `prompts.py` 新增 `_PROACTIVE_EXECUTION_RULES`、`_ARCHIVED_KNOWLEDGE_REMINDER` 与 `_WORKBENCH_TASK_REPLY_PROMPT`，分别约束主动执行行为、提示已归档知识库的存在，以及 Workbench 任务页对话模式的回复纪律。`agent.py` 同步在 `workbench-task-reply` 命令下替换 `phase1_decision`，强制优先 `quit` 避免误入工具执行。`coordinator.py` 中 `run_heartbeat_agent` 新增 `workspace_dir` 参数与增量工作规则（只读+新建文件，禁止修改/覆盖/删除已有文件）。
- **项目初始化表单增强** — `routes.py` 新增 `_workbench_init_workspace_relationship_guidance()`，根据项目模板类型与 workspace 来源生成关系判断守卫语，防止初始化 Agent 将已有文件误当作已确认的项目事实。引导式问题第一组优先澄清已有文件与新项目的关系。
- **任务步骤删除限制** — `routes.py` `update_task_plan_for_session` 中 `delete` 操作从 `structure_operation` 移出并独立校验，仅允许删除 `pending` 状态的步骤；已开始（包括 running/completed）的步骤拒绝删除。

### Fixed

- **聊天 Fork 重连处理** — `workbench-chat.jsx` 中 context 重建时丢失分段列表导致 `srcSegments is null` 的异常，添加空值守卫。
- **意图分类与最终确认一致性** — `dispatch.ts` 最终确认 (finalize) 时不再触发重复计划生成，确保 `role=review` 状态的 session 保持预期行为。
- **Shell stderr 重定向解析**（延续修复）— 防止正则错误匹配 shell 元字符。

### Tests

- 新增 `test_behavior_learning.py` 测试用例：Web 搜索学习行为、浏览器用户事件记录与回放、即时技能创建、学习 Agent review 决策验证。
- 新增 `test_workbench_chat_fork.py`：Fork 创建、源删除与分离、workspace 隔离测试。
- 新增 `test_workbench_chat_segments.py`：对话分段完整性测试。
- 新增 `test_workbench_dispatch_finalize.py`：意图分发 finalize 流程测试。
- 新增 `test_workbench_init_plan.py`：初始化计划生成测试。
- 新增 `test_workbench_knowledge_archive.py`：知识文档分页与详情测试。
- 新增 `test_workbench_api_validation.py`：API 校验入口注册。
- 更新 `test_workbench_frontend_logic.py`：验证 index.html 已弃用脚本路由不再注入，新增 browser-view 样式与学习板块断言。
- 更新 `test_model_prices.py`：覆盖新价格格式解析。
- 更新 `test_profile_stats.py`：合并工具显示别名与正确工具计数验证。
- 更新 `test_proactive_workbench.py`：多项主动执行场景测试增强。

## [0.6.2] - 2026-07-05

0.6.2 是一个功能型更新，重点引入自适应预算控制系统与经济模式以优化 token 花费，同时补齐 macOS 原生菜单栏、Workbench UI 体验改进与多项运行时修复。

### Added

- **自适应预算控制系统** — 全新 `AdaptiveBudgetController` 规则引擎（纯规则驱动，无 ML 依赖）。按月 / 周 / 5 小时三层窗口动态分配预算，根据剩余预算、近期消费速率和历史活跃密度自适应调整每层额度。新增 `/api/budget/status` 与 `/api/budget/models` 端点提供实时状态与按模型分拆的花费明细。超支策略可配置：仅告警、自动切换到更便宜的模型、阻止新请求。所有计算以用户配置币种（CNY / USD）为准。
- **经济模式 (Economy Mode)** — `budget_mode` 设置控制开启后，自动清除已完成轮次的 tool 结果，只保留 user ↔ assistant 对话主干，显著降低 context 膨胀与 token 消耗。
- **预算面板 UI** — 设置页新增「预算」页签（`BudgetPanel` 组件），提供月度限额、币种、超支动作、经济 / 标准模式、结算日配置，以及按模型分拆的当前月花费明细。侧栏项目列表同步显示 5 小时 / 周预算窗口实时状态。
- **macOS 原生菜单栏** — 中英文双语完整应用菜单（文件 / 编辑 / 视图 / 窗口 / 帮助），含新建对话 / 项目 / 任务、切换主题 / 侧栏、全屏、缩放等操作，通过 `menu:action` IPC 桥接到 Workbench 前端。
- **账户菜单** — 侧栏底部新增用户头像入口与下拉菜单，提供设置、登出等快捷操作。

### Changed

- **文件交付反馈增强** — `send_file` / `send_wechat_file` 工具调用成功后，自动从工具返回值构建用户可见确认文本（例如「文件已发给你：report.pdf」），替换空泛的 `"Done."` 占位回复；最终回复提示词同步优化以避免无信息量的占位语。
- **记忆注入策略改进** — 注入列表改为 top 20（按 `mention_count`）+ 随机 5 条混合，兼顾高频与多样性；注入 ID 快照到 session 级别，单次会话内复用同一集合，保持 prompt cache 稳定。
- **工作台数据按需加载** — `GET /api/projects` 新增 `?detail=summary` 参数，返回项目壳体 + 会话摘要（不含完整历史 payload）；非活跃 session 仅传摘要，切换时才拉取完整数据，大幅减少首屏传输量。`/api/task-sessions/{id}` 返回的 project 同步压缩为 shell。
- **Workbench UI 优化** — 模块页面延迟挂载（`mountedPages`），减少首屏渲染量；侧栏折叠与侧面板隐藏按钮统一样式；HTML 预览 iframe 自动注入 `viewport` meta；对话页新增文件下载按钮。
- **更新确认改用非阻塞模态框** — 升级确认从原生 `window.confirm` 切换为 `window.confirmModal`，不再阻塞 UI。
- **成本统计切换到 CNY** — DB `token_usage` 表的 `estimated_cost` 默认币种从 USD 改为 CNY，与内置模型定价保持一致，减少不必要的汇率转换。

### Fixed

- **Shell stderr 重定向解析** — `2>/path` 和 `&>/path` 的正则不再错误匹配 `;` `&` `|` 等 shell 元字符，避免误判文件写入目标。
- **直播段 ID 稳定性** — 工具轮次中 assistant 中间回复在持久化分配 `message_id` 之前，使用 SHA1 内容指纹生成稳定 UI ID，避免每次扫描产生重复条目。

### Tests

- 新增 `tests/test_adaptive_budget.py`（630 行），覆盖自适应预算控制器的全部核心逻辑：新用户零历史、各窗口预算计算、压力系数、变化限幅、活跃密度估计等。
- 新增 `tests/test_quit_reply.py`、`tests/test_workbench_api_validation.py`。
- 更新 `tests/test_workbench_chat_segments.py`、`tests/test_workbench_frontend_logic.py` 与版本号一致。

## [0.6.1] - 2026-07-01

0.6.1 是 `0.6.0` 之后的正式维护版本，重点补齐浏览器自动化能力、桌面 Quick Chat 常驻体验、文档站与若干运行时稳定性修复。本版本为稳定 release，不是 beta / prerelease。

### Added

- **浏览器自动化工具扩展** — 新增按坐标点击、按元素引用点击、按文本点击、按元素引用输入、等待、网络日志、结构化快照与多标签页管理等工具；Workbench / Legacy UI 中的浏览器实况说明与工具注册同步更新。
- **桌面 Quick Chat 常驻入口** — 增强 Electron 后台驻留、托盘入口、窗口复用和主进程消息契约；补充托盘图标资源与背景运行测试。
- **文档站资源** — 新增 `docs/web` 静态文档站、Logo、交互脚本和样式，方便发布包外独立浏览项目说明。

### Changed

- **元素交互更稳** — Electron / BrowserTabManager 的元素操作改为更可靠的页面级交互路径，减少受页面自定义 JS click 限制影响的失败。
- **Chat-only 流式回复路径简化** — 流式请求不再额外发起一次 final reply LLM 调用，直接复用 phase-1 回复并保留用量统计，降低延迟与 token 消耗。
- **行为学习遥测后台化** — 工具执行后的 `record_action` 记录改为后台任务，不再阻塞工具结果返回；失败只记录 debug 日志。
- **发布版本元数据统一** — Python 包、Electron 应用、lockfile、README、文档站、WeChat client 标识、WebUI cache-busting 参数与前端断言统一到 `0.6.1`。

### Fixed

- **命令行生成的交付物提示** — Workbench 任务提示词明确要求 Bash / shell 生成最终文件后也必须调用 `send_file`，避免文件已生成但不进入「产物」面板。
- **地图 pin 视觉** — Leaflet pin 改为自定义可主题适配的地图标记，修复默认 marker 资源在部分打包场景下不稳定的问题。
- **设置页侧栏交互** — 调整设置浮层 tab 的 hover、active 与 focus-visible 样式，降低视觉抖动并改善键盘焦点反馈。
- **测试环境依赖 mock** — Quick Chat 和 runtime tests 对 PIL / pypdf 的处理更稳，避免真实依赖存在时被 MagicMock 污染。

### Tests

- 新增 / 更新浏览器会话、Quick Chat、托盘图标、后台驻留、行为学习、更新器平台匹配、Workbench 初始化计划、运行时修复与前端 cache-busting 等测试。

## [0.6.0] - 2026-06-29

首个正式版本，汇总自 **v0.5.1** 以来的累积更新。0.6.0 的主线是全新的 **Workbench 工作台**——以项目为中心的桌面工作环境，以及围绕它的可恢复任务执行、并发对话、记忆 / 知识体系，加上大量提示词缓存与稳定性优化。本版本把 `feat/workbench` 分支的全部工作合并进 `main`，并将版本号正式定为 `0.6.0`。

> 各 `0.6.0-beta.*` 预发布的逐版本明细见下方 beta 段。

### 🏗️ Workbench 工作台（全新）

- **以项目为中心的工作台** — 全新桌面 UI：每个项目独立的看板、日程、知识、记忆、对话与个人资料页，与经典单 agent UI 并存、共享后端。
- **诚实的逐步任务执行** — 计划基于工作区实况生成（计划模式提交前可只读预探索），任务按步骤推进、可跟随可干预、可中途修订并保留进度；意图分流（问题 / 指令 / 任务）与「任务完成」收尾（finalize）。
- **可恢复的对话运行** — Chat 运行由进程级运行管理器持有，断线、切页或网络抖动后 agent 仍在后台继续并持久化结果，前端经独立只读接口重连追上；支持并发多会话。
- **对话编辑与分叉** — 可编辑已发送消息并从该点分叉新对话（原对话保留、标注 Forked），也可从任意位置重新生成回复（事务式替换，失败不丢旧回复）。
- **SQLite 事务存储** — 以 SQLite 为单一真相源（`BEGIN IMMEDIATE` + 三方合并 + WAL），并发消息 / 会话 / 通知 / 记忆互不覆盖，取代旧的整文件 JSON 读改写。
- **工作区隔离与安全** — 每个项目限定到自身 `workspacePath`（主 agent 与 subagent 一致），新建项目走空工作区引导；workspace 路径安全校验防穿越。
- **产物与文件变更** — Artifact 一键下载（服务端校验不逃逸 workspace）；即使非 Git 仓库也能记录有界文本快照生成统一 diff；`send_file` 显式产物信号优先于 git 推断。
- **上下文可视化** — 上下文分段 token 仪表 + 手动压缩对话；Settings 浮层、平台感知的全局快捷键管理器、subagent 状态 / 载荷面板。

### 💬 Quick Chat 全局快捷对话（全新）

- **全局快捷键唤起** — `Ctrl/Cmd+Shift+Space` 任意界面呼出浮动窗口，支持活跃窗口截图粘贴、独立窗口复用、常驻托盘生命周期。
- **完整对话体验** — 与主对话页共享运行管理器与消息卡片：工具调用轨迹、生成附件、实时思考卡片；运行服务端持久化，关窗后 agent 仍在后台继续。

### 🧠 记忆与知识

- **三层记忆 + 退场** — 上下文 → 短期跨会话摘要 → 长期 `SOUL.md`；短期与项目记忆均可按精确 ID 退场（`retire_short_term_memory` / `retire_project_memory`），退场条目留档但不再注入与召回。
- **记忆分类重构** — 区分 `habit`（如何做事）/ `conversation`（如何沟通）/ `preference`（静态喜好），新增内部 `reflection` 分类（agent bookkeeping，不在用户页显示）；多词记忆召回支持分词 OR 匹配。
- **检索工具** — 新增 `RecallConversation`（按关键词 / session / 日期检索历史对话）与 `search_project_memory`（项目内记忆搜索）；`RecallMemory` 为每条短期记忆返回稳定 `memory_id`。
- **知识库** — `ListKnowledgeDocuments` 枚举文档与索引状态；Workbench 步骤摘要自动归档进项目知识库供后续检索；按 workspace 隔离；支持图片 vision 索引。
- **实体优先** — 涉及任务 / 计划 / 待办 / 决策的回答先查实体、以记录为准，执行计划前先拉取活跃事务与决策以复用。

### 🌐 浏览器自动化

- **实时直播不堵流** — 画面经 `/ws/browser` 以二进制 JPEG 帧传输，SSE 只走轻量元数据，避免 base64 截图挤占事件流。
- **面板内实时控制 + 登录接管** — 用户可在直播面板内经 CDP 注入鼠标 / 键盘 / IME 直接操作 headless 页面（agent 动作自动让位）；遇登录墙 / 验证码 / 2FA 可经 `browser_request_takeover` 或切换有头窗口完成，再回到同一已登录会话继续；Workbench 自动打开浏览器侧栏。

### ⚙️ Agent 运行时与提示词缓存

- **提示词缓存优化** — 静态系统块前缀化（`static_system_extra`）、run 级上下文前置于用户消息（`fixed_ephemeral_system`）、temporal 上下文移到尾部、统一 phase1/2 工具集、`quit(reply=)` 直接交付（消除收尾重建——曾占缓存 miss 约 53%），显著提升前缀缓存命中。
- **运行打断** — 跟踪当前运行任务，打断时连同在跑的 subagent 一并取消。
- **稳定性** — LLM 瞬时错误有限重试、蒸馏上限与压缩阈值预检、SSE 心跳保活；DSML 流式工具标记抑制，防止泄露到 UI。
- **跨平台 Shell 与破坏性操作确认** — 自动识别 Shell 类型并调整执行策略；`rm` / `git reset --hard` / `dd` 等高危命令即使 full/auto 模式也强制二次确认，外发文件纳入不可逆副作用确认。

### 📦 平台、打包与更新器

- **Windows on ARM** — CI 同时构建 ARM64 与 x64 安装包；Release 默认捆绑 Workbench UI。
- **运行时目录治理** — 新增 `cyrene.app_paths` 统一解析数据 / 缓存 / 临时目录，打包后不再把运行时数据写入只读资源或系统临时目录。
- **更新器** — 按平台 / 架构正确匹配安装包（修复 Windows 被推 macOS `.dmg`）、读取 release asset 的 sha256 校验、可选 beta 更新通道、后台检查并在工作台提示新版本与体积。
- **启动不阻塞** — 知识迁移 / vision 索引后台化，修复打包桌面端 30s 启动超时；修复更新重启的 launch guard。

### 🔒 安全

- **HTML artifact 隔离** — 用户生成的 HTML 仅在沙盒 `srcDoc` iframe 预览，不允许在继承后端 session 的子窗口打开。
- **路径校验与敏感信息脱敏** — workspace 路径穿越防护；错误文本中的 token / 密钥自动脱敏。

### ✅ 测试

- 全周期新增 / 更新数十个测试套件，覆盖 Workbench 任务 / 计划 / 对话 / 记忆 / 知识 / 存储并发、Quick Chat、浏览器接管与实时控制、更新器平台匹配、提示词缓存、Shell 守卫与破坏性操作确认等。前端 cache-bust 断言与各处版本元数据统一为 `0.6.0`。

## [0.6.0b16] - 2026-06-29

### Added

- **应用路径集中管理** — 新增 `cyrene.app_paths`，统一解析安装资源目录、用户数据目录、缓存目录和应用临时目录。打包运行时写入 `Application Support` / `%APPDATA%` / XDG data，临时与缓存产物写入平台缓存目录，避免把运行时数据混入安装资源。
- **破坏性操作二次确认** — Bash / SendShell / StartShell 现在会识别 `rm`、`git reset --hard`、`git clean -f`、`dd`、`truncate`、强制覆盖等高风险命令；即使处于 full access 或 auto 模式，也必须由用户确认。外发 WebUI / Telegram / WeChat 文件也纳入不可逆副作用确认。
- **Workbench 浏览器侧栏** — Workbench Chat 会在浏览器自动化或登录接管时自动打开 Browser 侧栏，并允许直接在侧栏点击“我已完成登录”继续任务。

### Fixed

- **打包应用运行时目录污染** — 配置、数据库、workspace、备份暂存、代码格式化暂存、技能安装暂存、通知脚本、SearXNG 日志、浏览器截图和更新下载现在都使用统一的用户数据 / 临时目录，减少 macOS / Windows 打包后写入只读资源目录或系统临时目录残留的问题。
- **浏览器直播不再堵塞 SSE** — `browser_frame` SSE 事件改为只发送 URL、title、action、target 等轻量元数据；实时画面通过 `/ws/browser` 发送二进制 JPEG 帧，前端用 object URL 渲染并及时释放，避免 base64 截图挤占共享事件流。
- **更新重启启动保护** — 修复更新重启路径的 launch guard，避免重启后误判已有实例或丢失启动状态。

### Changed

- **静态资源缓存版本统一** — WebUI 所有 JS/CSS cache-busting 参数统一为 `beta16`。
- **版本元数据统一** — Python 包、Electron 应用、Electron lockfile、README badge、WeChat client 标识与 `uv.lock` 统一到 beta16。

### Tests

- 新增/更新 `test_app_paths`、`test_browser_session`、`test_runtime_fixes`、`test_workbench_frontend_logic`，覆盖平台路径解析、临时产物清理、二进制浏览器帧传输、登录接管确认和破坏性操作确认。
- 前端 cache-bust 断言同步到 beta16。

## [0.6.0b15] - 2026-06-28

### Added

- **Workbench 无 Git diff 捕获** — Workbench 运行前后现在会记录有界 UTF-8 文本快照；即使项目目录不是 Git 仓库，也能为新增、修改、删除文件生成统一 diff，并把 diff 写入 run、step related files 与 artifact，避免后续查看文件变化时只能看到“整文件快照”或空 diff。
- **Workbench 会话召回进入项目 workspace** — `RecallConversation` 在 Workbench 任务/聊天上下文中优先搜索当前项目 `conversations/*.md`，返回 `scope=workbench_workspace`、session id 和 source file，避免跨项目或 legacy archive 召回不相关对话。

### Fixed

- **`quit(reply=...)` 历史不再丢失最终回复** — 直接通过 `quit(reply=...)` 收尾的回答现在会同步写入 assistant history content，确保用户可见 transcript 与下一轮 LLM 读取的会话历史一致。
- **多词记忆召回过窄** — `RecallMemory` 与 Workbench 项目记忆搜索支持空格分词 OR 匹配并按短语/term 命中排序，像“照片 人物 头像 识别”这类查询不再要求整段完全连续命中。
- **文件 diff 查看更稳** — Workbench diff API 会优先使用 recorded diff；Git diff 为空时补查 staged diff，对无内容变化的 timestamp-only 变动返回记录原因，避免误导性地把当前整文件当作变更。

### Changed

- **静态资源缓存版本统一** — WebUI 所有 JS/CSS cache-busting 参数统一为 `beta15`。
- **版本元数据统一** — Python 包、Electron 应用、Electron lockfile、README badge、WeChat client 标识与 `uv.lock` 统一到 beta15。

### Tests

- 新增/更新 `test_quit_reply`、`test_runtime_fixes`、`test_workbench_init_plan`、`test_workbench_memory_language` 覆盖 quit reply 持久化、workspace conversation recall、记忆 OR 查询和 Workbench diff 记录。
- 前端 cache-bust 断言同步到 beta15。

## [0.6.0b14] - 2026-06-28

### Fixed

- **Electron 30s 启动超时（知识迁移阻塞）** — `migrate_default_project_knowledge` 现在以 `asyncio.create_task` 后台化运行，不再在 lifespan `startup` 钩子里同步等待。迁移涉及逐文档的 vision/embedding LLM 调用，耗时无上界；之前它阻塞 uvicorn 全部 startup 事件，`PORT=` 迟迟无法打印，桌面端因超 30s 超时无法启动。现在服务器立即就绪，迁移在后台继续进行。
- **Vision 候选链顺序反转** — `_resolve_vision_candidates` 修复：视觉专属模型（用户配置的视觉端点）现在排在候选链首位，文本主模型（如 DeepSeek）作为兜底降级。此前文本主模型被先尝试，每张图片必然 400 失败一次再回落视觉模型，在大批量文档分析时累积拖慢启动。
- **反思记忆污染 `fact` bucket** — goal loop 反思产生的 `excluded_paths`（死路）和 `promising_directions`（有效方向）现在写入内部 `reflection` 分类，而非 `fact`，不再虚增用户记忆页的"事实信息"计数，且不在记忆页展示。反思条目仍注入每次 agent run（跨 session 传递学习成果）。

### Changed

- **记忆分类体系重构** — `conversation` 分类从"对话记忆"重定义为"**对话习惯**"（用户希望 agent 如何与其沟通的重复偏好，例如"用中文回复"、"直接给结论"），现在会注入每次 agent run（之前因认为高噪低价值而被排除）。新增内部 `reflection` 分类（agent bookkeeping 专用，不在用户页显示）。`_EXTRACT_PROMPT` 和 `save_project_memory` 工具描述同步重写，区分 `habit`（如何做事）、`conversation`（如何沟通）、`preference`（对产物/工具的静态喜好），提示模型选到最精确的分类。
- **Agent 实体查询铁律** — 提示词新增明确约束："任何涉及用户任务 / 项目 / 待办 / 决策 / 日程的回答，一律先查实体、以记录为准，不得凭记忆或印象作答。"同时新增规则：生成或执行项目任务计划前，先 `list_entities(status="active")` + 相关 `query_entities` 拉活跃任务与决策，复用已有结论、避免与既有事务冲突。
- **Cyrene 品牌按钮** — Workbench 顶栏 Cyrene Logo 按钮由跳转任务页改为打开"设置 → 关于"页。

### Tests

- 前端 cache-bust 断言同步到 beta14。

## [0.6.0b13] - 2026-06-28

### Added

- **Workbench 项目共享上下文（`workbench_task_context`）** — 新模块，专为 Workbench 任务 session 提供项目级共享上下文。`sharedContext` 字段挂载在 project 对象上，记录任务描述、最终目标和当前成果（`currentOutcome`）。主 agent 运行时自动注入项目固定块 + session 任务/计划/验收标准；子代理（subagent）同样获得项目上下文，并在完成后把最终回复追加到 `currentOutcome.entries` 供同一项目下所有 session 共享。
- **计划模式预探索** — 计划模式下，主 agent 在提交计划前现在可以调用只读探索工具（读文件、搜索项目记忆、查询外部信息），完成后再调用 `enter_plan_mode`。规划器接收到压缩后的工具调用摘要（`_history_context_text`），计划将真正基于工作区实际状态生成，而不再是纯推断。
- **项目记忆 Session 基线快照** — Session 启动时记录当前记忆 ID 集合作为固定前缀（cache-stable），Session 期间新增的记忆条目自动进入 volatile 尾部独立渲染，不影响已缓存的固定前缀。下一个新 Session 开始时再次快照，把 volatile 条目提升为固定块。

### Fixed

- **对话分段渲染 — 多文件交付顺序** — `_reorder_tool_produced_replies` 修复：同一回合内多个文件分段交付时，每段交付回复现在都能正确排列到其对应工具卡片之后（而不仅限于单文件场景）。
- **知识 workspace 隔离** — `_resolve_workspace_id` 修复：默认项目记忆不再跨项目泄漏；新增启动事件将默认项目知识从旧版共享数据库中解耦，写入项目专属存储。
- **Subagent 收尾超时** — goal loop 等待 subagent settle 时现在带超时，不再因 subagent 意外挂起而无限阻塞。

### Changed

- **Prompt 缓存优化 — `fixed_ephemeral_system`** — `run_agent` / coordinator / `_run_main_agent` 新增 `fixed_ephemeral_system` 参数。Run 级上下文（Workbench 任务简报、项目记忆快照、temporal context、conversation identity）现在插入到当前用户消息之前，而非 prompt 尾部；工具回合通过纯 append 演进，前一轮请求是下一轮请求的完整前缀，缓存命中显著提升。原 `ephemeral_system` 仅保留给真正需要每轮变化的 volatile 尾部内容。
- **Phase 1 首轮也使用完整工具集** — 移除首轮使用轻量 phase1 工具集的例外：常规对话的第一轮 Phase 1 decision 现在与后续轮次一样使用 `wire_tool_defs`，利用 DeepSeek 工具敏感前缀缓存，首轮不再因工具集差异导致额外 cache miss。
- **记忆注入 API 增强** — `render_memory_for_injection` 新增 `include_ids`、`exclude_ids`、`preserve_id_order`、`header` 参数，支持精确控制哪些记忆条目注入、以什么顺序、使用什么标题头部；新增 `memory_injection_ids` 辅助函数返回当前可注入的 ID 有序列表。
- **macOS 流量灯间距修复** — Workbench UI 在 macOS 上正确保留 traffic light 按钮空间，避免布局重叠。
- **静态资源缓存版本统一** — WebUI 所有 JS/CSS cache-busting 参数统一为 `beta13`。
- **版本元数据统一** — Python 包、Electron 应用、Electron lockfile、README badge、WeChat client 标识统一到 beta13。

### Tests

- 新增 `test_workbench_task_context`（共享上下文构建与 outcome 写入）、`test_workbench_chat_plan`（计划模式预探索与修订）、`test_cache_fixes`（fixed ephemeral 注入位置、首轮 phase1 工具集）。
- 更新 `test_runtime_fixes`；前端 cache-bust 断言同步到 beta13。

## [0.6.0b12] - 2026-06-27

### Added

- **Quick Chat 升级为完整对话体验** — 快捷对话窗口改用与主对话页相同的共享运行管理器（`WorkbenchChatRuntimes`）和消息卡片渲染。回复现在带工具调用轨迹（trace）、生成的文件附件，以及实时“思考 / 调用工具”卡片，与主界面完全一致，不再是简化的文本气泡。运行在服务端持久化：关闭或重新唤起快捷窗口只停止本地流消费，Agent 仍在后台继续执行，进度可在主窗口查看。
- **Quick Chat 窗口尺寸与滚动优化** — 空闲时保持紧凑但留足上方权限 / 命令菜单空间；首次发送后窗口一次性增高到对话所需高度，之后用户可自由调整、布局随之自适应。滚动具备粘性：仅在贴近底部时跟随最新消息，向上翻阅历史不再被拽回底部。

### Fixed

- **默认项目记忆数显示为 0** — 记忆页用项目 `dataKey` 作 workspace，但记忆按项目 id 存储；旧版默认项目两者不同（`dataKey = default`、`id = project_…`），仅按 id 匹配会落空到空的 “default” store。现先按 id、再按 dataKey 解析，任一标识都能命中同一项目记忆库。
- **WebUI / Workbench 通道误报“已发送微信文件”** — 当前通道不具备 `send_file` 能力时不再向模型暴露 `send_wechat_file` 工具，避免一次必然失败的调用、浪费一个回合并留下误导性的“已发送”卡片。
- **`send_file` 把整段回答塞进 caption** — 明确 `send_file` 的 `text` 只是文件旁的简短说明，模型仍需另写完整最终回复，避免一回合塌缩成裸 “Done.”。
- **心跳间隔被强制为 60 的倍数** — 设置项允许输入任意秒数（step 改为 1），不再在输入时强制取整或回退默认值。

### Changed

- **Agent 事务追踪：主动检索** — Agent 现在会在对话涉及用户个人事务、计划、项目时主动调用 `list_entities`/`query_entities` 获取当前状态再作答，而非等待用户明确要求查询。新增五条具体触发规则：首轮个人话题自动扫描活跃事务、指代词／项目名触发关键词检索、延续性工作开始前先确认待办、更新事务前先检索 ID、记录前去重检查。
- **Prompt 缓存优化 #7 — `quit(reply=)`** — `quit` 工具新增 `reply` 参数承载最终回复文本。模型收尾时把答案写进 `reply` 直接交付，省去原先 tools=None 的“收尾重建”调用——该调用因 `tools` 数组不在前缀最前端，与主链零共享缓存、需重处理整段历史，是此前缓存 miss 的头号来源（约占 53%）。各阶段 prompt 与工具定义同步更新，引导模型把完整答案写进 `reply`。
- **对话分段渲染改进** — 中途“让我查一下…”这类既有正文、又调用工具的回合现在单独成块呈现（此前正文被丢弃）；失败的工具调用在 trace 卡片中以 ✕ 标记；`send_file` / `send_wechat_file` 交付的文件回复被重排到其工具卡片之后，渲染顺序固定为 [工具卡片] → [交付文件]。
- **Chat 流式渲染性能** — 回复增量（reply delta）的重渲染按 `requestAnimationFrame` 合并，每帧最多一次；已完成消息的 Markdown 解析做记忆化，实时消息仅在文本变化时重解析，避免长回复每帧 O(n²) 重新解析整段会话。
- **静态资源缓存版本统一** — WebUI 所有 JS/CSS cache-busting 参数统一为 `beta12`。
- **版本元数据统一** — Python 包、Electron 应用、Electron lockfile、README badge、WeChat client 标识统一到 beta12（并补齐 beta.11 遗漏的 README badge 与 `uv.lock`）。

### Tests

- 新增 `test_quit_reply`（quit reply 直接交付、跳过收尾重建）、`test_workbench_chat_segments`（中途前导语成块、失败 trace 标记、交付回复重排序）、`test_workbench_memory_resolve`（默认项目按 id / dataKey 双重解析）。
- 更新 Quick Chat 契约测试以匹配共享 run-manager 架构；前端 cache-bust 断言同步到 beta12；WebUI 42 个 JSX 全量编译通过。

## [0.6.0b11] - 2026-06-26

### Added

- **全局快捷键快速对话（Quick Chat）** — 新增 `Ctrl+Shift+Space`（macOS `Cmd+Shift+Space`）全局快捷键，任意界面一键呼出浮动 Quick Chat 窗口。支持截图粘贴、独立窗口复用、常驻系统托盘的 app 生命周期（Electron）。由独立 `window.WbcComposer` 实例渲染，与主 Workbench 互不干扰。
- **Quick Chat 截图支持** — 快捷键唤起后自动捕获活跃窗口截图并粘贴到输入框；可粘贴多条后续截图，按发送自动清空。
- **更丰富的思考短语列表** — Agent 思考状态从 20 条扩展到 25 条随机短语，覆盖 more deliberate / 系统性推理表达。

### Fixed

- **Quick Chat 首轮消息带失效的先决条件** — `is_quick_chat` 标记与 `scene = 'quick_chat'` 对齐，确保初轮消息不走 workbench project 锚定路径。
- **Quick Chat 重复 target 匹配** — `/api/quick-chat/targets` 端点对同 socket 的去重返回，避免浮动窗口重复初始化。

### Changed

- **版本号更新至 0.6.0-beta.11** — Python 包、Electron 应用、lockfile、前端 cache-busting 参数统一更新。
- **静态资源缓存版本统一** — WebUI 所有 JS/CSS cache-busting 参数统一为 `beta11`。

### Tests

- 新增 Quick Chat targets 端点测试、全局快捷键前端测试覆盖。

## [0.6.0b10] - 2026-06-24

### Added

- **可恢复的 Workbench Chat 运行** — Chat Agent 运行由进程级 `ChatRunManager` 持有，不再依赖单个 HTTP 流的生命周期。浏览器断线、切换页面或短暂网络中断后，Agent 仍会继续执行并持久化结果；前端通过独立只读重连接口恢复事件流。
- **手动压缩对话上下文** — Workbench Chat 概览新增“压缩对话”操作，可在自动阈值前显式执行现有上下文折叠流程，并显示压缩前后的 context 占用比例。
- **项目记忆退役工具** — 新增 `retire_project_memory`，Agent 可按精确 memory ID 将错误、过时或已被取代的项目记忆标记为 retired。记录仍可在 Memory 页面恢复，但不会继续注入 Agent 上下文或出现在普通搜索结果中。
- **回复期间继续编辑草稿** — Agent 回复期间输入框保持可编辑，用户可提前准备下一条消息；发送仍被锁定，停止当前运行后才可提交。

### Fixed

- **新消息被错误当作运行重连而静默丢弃** — `POST /messages` 现在只负责创建新运行；已有运行时统一返回 `409 chat_run_in_progress`。重连改走独立 `GET /run-stream`，从协议层区分“发送新消息”和“恢复旧事件流”。
- **非流式请求可绕过单聊天运行锁** — 流式与非流式 Chat 请求现在都先在同一个运行注册表中原子占位，防止同一聊天并发启动多个 Agent、覆盖状态或打乱 transcript 顺序。
- **重试失败会删除旧回复** — 重新生成改为事务式替换：旧回复在新回复持久化前保持不动；模型调用失败时恢复原 Agent state，只有成功或进入 awaiting-user 状态后才提交截断。
- **重复附件上传导致当前会话文件失效** — Knowledge Base 内容去重不再删除 Chat transcript 正在引用的上传路径；缺失的 canonical KB 路径可由新的同内容文件恢复并重新索引。
- **附件缺失后误扫本机文件系统** — `AnalyzeAttachment` 对文件缺失返回终止型结构化错误，提示重新上传，并明确禁止使用 Glob/Grep/Bash/find 扫描设备寻找替代文件。
- **图片附件预览破图与溢出** — Composer 和历史消息中的图片加载失败时自动降级为文件 chip，预览容器限制溢出。
- **系统主动轮次可能调用 `ask_user`** — proactive/system-initiated 轮次不再暴露或执行 `ask_user`，必须自主完成检查或静默结束。
- **未知 context window 时按消息数丢历史** — 无法确定模型上下文窗口时不再执行有损的固定条数裁剪；仅在 token budget 可确定时压缩。
- **短期记忆提取长期停留在最早消息** — 记忆压缩窗口改为最近 20 条用户/助手消息，并过滤 retired 条目。
- **内部 task report 污染 Memory 页面和全局搜索** — `task_report` 继续作为内部规划上下文保存，但不再计入用户可见记忆列表、统计、来源图和 Workbench 搜索。

### Changed

- **静态资源缓存版本统一** — WebUI 所有 JS/CSS cache-busting 参数统一为 `beta10`，确保升级后加载同一版本的完整前端资源。
- **版本元数据统一** — Python 包、Electron 应用、Electron lockfile、README badge、WeChat client 标识统一到 beta 10。

### Tests

- 新增 Chat 运行断线继续执行、显式重连、非流式运行注册、重试回滚、重复附件保留、缺失附件终止、手动上下文压缩、记忆退役、内部记忆隐藏和 proactive 工具限制等回归测试。
- WebUI JSX 全量编译通过；相关 Python 测试覆盖 session persistence、runtime、knowledge、Workbench chat/memory/search 和前端逻辑。

## [0.6.0b9] - 2026-06-23

### Added

- **Workbench API 封装层（`workbench_chat_runs.py`）** — 新增独立模块封装 chat run 生命周期：创建/恢复 run、sse 心跳、中止、轮次历史与 run 元数据路由。前端通过 `workbench-api.jsx` 统一客户端调用，替换原先散落在 `workbench-chat.jsx` 里的 `fetch` 调用，降低耦合。
- **Chat 心跳（Heartbeat）** — Workbench Chat 流式响应新增 SSE 心跳帧，保持长连接活跃、防止中间代理/网关因空闲超时断流；前端在心跳到达时更新「正在思考」指示而不写入消息。
- **LLM 蒸馏上限与阈值预检** — `call_llm.py` 引入压缩块蒸馏最大尝试次数，防止增量压缩无限重试；新增 `exceeds_compaction_threshold` 预检，仅在消息上下文真正超过压缩阈值时才触发蒸馏。
- **LLM 瞬时错误重试** — 对 LLM 调用中的瞬时服务器错误加入有限次数自动重试，超限后才失败；新增针对瞬时错误与重试行为的测试。
- **Workbench 初始化任务优先级指示** — 初始化 UI 增加任务管理与优先级视觉指示，更清晰展示 init 阶段任务队列。

### Fixed

- **Workbench Chat 代码块语法高亮与复制按钮丢失** — marked v5+ 移除 `highlight` 选项后 `highlight.jsx` 沦为死代码，代码块不再带 hljs span。改用 marked v13 正确的渲染器经 `marked.use` 输出完整 `<pre><code>` 块（含行号与语言标签）。`actions.jsx` 原先只扫描 `.msg-list`（legacy chat），复制/编辑按钮无法触达 workbench 的 `.wbc-thread`；现同时监听 `.msg-list`、`.wbc-thread`、`.wbc-side-body`，并在 SPA 导航后以 2s 轮询重新挂载观察器。`highlight.css` 选择器由 `.msg-body-only` 扩展到 `.wbc-msg-body.markdown`，行号、语言标签、操作栏与 hljs 主题在 workbench 全部生效。渲染器顶层 try/catch 在 hljs 失败时回退为纯转义文本，避免 `marked.parse` 崩溃。
- **Workbench Profile 仪表盘统计数据不实时刷新** — Profile 页消费 `DATA.dashboard`（KPI、活跃度热图、insights、top tools），但该数据仅在 bootstrap 时获取一次，SSE 事件总线与 15s 轮询均未触及。新增轻量 `GET /api/dashboard?tz=` 路由只返回 `_build_dashboard`，避免重建完整 `ui-data`；`refreshDashboard()` 带请求序列守卫与 JSON 指纹跳过无变化重渲，经 3s 防抖订阅 SSE 事件并纳入 15s 全局轮询。
- **默认 Workbench 项目可被删除** — `DELETE /api/projects/{id}` 端点对默认项目（`dataKey === "default"`）缺少守卫，会清空其 sessions、chats、memory。现对默认项目返回 `400 default_project_protected`，前端隐藏默认项目的删除按钮并在调用失败时给出友好提示。

### Changed

- **Workbench 路由 JSON 响应与错误分类** — `routes.py` 统一处理 JSON 响应格式，并对解析失败加入错误分类，便于前端区分瞬时错误与永久错误。

### Tests

- 新增 `tests/test_call_llm_candidates.py` 瞬时服务器错误用例 — 覆盖重试行为与失败处理。
- 更新 `tests/test_workbench_api_validation.py` — 覆盖默认项目删除保护（`default_project_protected`）。
- 更新 `tests/test_workbench_init_plan.py` — 覆盖 init 任务管理与优先级指示。

### Docs

- **README / .gitignore 与项目状态同步** — Quick Start 切换到 uv（lock file 已提交）并附 pip 回退；补充 WebUI JSX 预编译步骤（`compiled/` 被 gitignore、由 `index.html` 加载）与 Node.js 20+ 前置；列出 `browser`/`dev` 可选 extras、细化 Testing 限制说明，tech stack 加入 uv + Ruff。`.gitignore` 不再忽略 `uv.lock`，新增 `.ruff_cache/`、`backups/` 与根锚定 `/db.sqlite3[-*]`；移除误提交的根 `db.sqlite3`（真正运行时 DB 在 `store/cyrene.db`）。

## [0.6.0b8] - 2026-06-23

### Fixed

- **新建空项目错误地读取默认 workspace 内容** — 在 Workbench 新建项目时若未显式选择 workspace 路径，后端会静默回退到全局默认 `WORKSPACE_DIR`（`<repo>/workspace/`，非空），导致 `_is_workspace_empty()` 返回 `False`，init 流程跳过"全新项目"引导分支，改为启动 explore-agent 去列默认 workspace 里的 `SOUL.md`、`conversations/` 等已有文件。现改为：缺失 `workspacePath` 时自动在 `WORKSPACE_DIR/projects/<project_id>` 下创建一个空的逐项目子目录，确保新项目走"全新项目，工作区还没有代码"的引导路径。
- **前端预填当前项目 workspace 路径** — "新建项目"弹窗原先用当前活动项目的 `workspacePath`（通常是非空的全局 `WORKSPACE_DIR`）预填路径输入框，用户若不修改就会误传默认 workspace。改为默认留空，强制用户选择或走自动创建路径。
- **Glob/Grep 工具硬编码全局 workspace** — `tool_impl/glob.py`、`tool_impl/grep.py`（及 `tool_legacy.py` 中的 legacy 副本）的 `Glob` 和 `Grep` 工具原先硬编码 `WORKSPACE_DIR.glob(...)` / `path.relative_to(WORKSPACE_DIR)`，忽略了 `active_workspace_dir()` ContextVar，导致即使项目正确设置了 `workspacePath`，主 agent 的文件搜索仍然读取全局默认 workspace。改为使用 `active_workspace_dir()`，与 `Read`、`Bash`、`git_tools` 等工具对齐。这也修复了 Grep 在非默认 workspace 下 `relative_to(WORKSPACE_DIR)` 抛 `ValueError` 的潜在崩溃。

## [0.6.0b7] - 2026-06-23

### Added

- **Chat 编辑与分叉（Edit & Branch）** — Workbench Chat 支持编辑已发送的用户消息并从该点分叉出新对话；原对话保持不变，分叉对话标注"Forked"标记并可点击回溯源对话。同时支持从任意位置重新生成回复。
- **Workbench 个人资料页** — 导航栏新增独立的 Profile 页面，展示对话轮数、活跃天数等统计数据，支持头像/emoji 自定义与功能开关。
- **键盘快捷键管理器** — 新增平台感知的全局快捷键模块（搜索、新对话、新任务、命令面板、切换项目、折叠侧栏、设置）。⌘ 在 macOS / Ctrl 在其他平台自动适配；用户可在 Settings → Shortcuts 中自定义绑定并持久化到 localStorage。
- **Settings 浮层面板** — 新增浮动式设置面板，含 Shortcuts 等多个标签页。
- **Workbench SQLite 事务存储** — 新增 `workbench_store.py` 模块，以 SQLite 为单一真相源，通过 `BEGIN IMMEDIATE` + 三方合并实现并发写入安全；实体列表按稳定 `id` 合并，并发消息、会话、通知与记忆互不覆盖，替代旧的整文件 JSON 读-改-写循环。
- **Workspace 路径安全校验** — 新增 `workspace_validation.py` 安全边界模块，限制用户选择的 workspace 目录必须在允许的根路径下（home、workspace dir、temp、挂载点等），防止路径穿越。
- **API 请求校验** — 新增 `api_models.py`（pydantic 校验请求体）与 `api_errors.py`（统一 HTTP 错误处理）覆盖 Workbench API 路由。
- **DSML 流式过滤** — `call_llm.py` 新增 `_DsmlStreamFilter`，在流式输出中增量剥离 DeepSeek 文本 DSML 工具调用标记，防止标记泄露到 UI；原始文本保留供流结束后恢复为真实工具调用。
- **Context window 仪表** — Workbench 新增上下文分段 token 追踪与 chat context payload 展示。

### Changed

- **Chat 房间样式重构** — 头像列表与 subagent chip 布局重构，视觉更紧凑；用户消息编辑态与 fork 标记样式完善；触屏设备下编辑按钮可见性改善。
- **Chat 状态管理增强** — 编辑流程中对话状态处理更稳健；流式中间消息（工具调用过程中的 partial assistant turns）处理改进。

### Tests

- 新增 `tests/test_workbench_chat_fork.py` — 覆盖对话分叉功能与状态管理，确保原对话不受影响。
- 新增 `tests/test_dsml_stream_filter.py` — 覆盖流式 DSML 标记抑制，防止工具调用标记泄露到 UI。
- 新增 `tests/test_workbench_context_gauge.py` — 覆盖上下文分段 token 与 chat context payload。
- 新增 `tests/test_workbench_sqlite_store.py` — 覆盖 SQLite 存储并发操作与三方合并。
- 新增 `tests/test_workbench_api_validation.py` — 覆盖 API 请求校验与错误处理。
- 新增 `tests/test_conversation_archive.py` — 覆盖对话归档。
- 更新 `test_profile_stats.py`、`test_runtime_fixes.py`、`test_workbench_frontend_logic.py`、`test_workbench_init_plan.py`、`test_workbench_knowledge_archive.py`、`test_workbench_memory_language.py` — 补充 profile 统计、运行时修复、前端逻辑、初始化计划、知识归档与记忆语言偏好场景。

## [0.6.0b6] - 2026-06-22

### Added

- **RecallConversation 工具** — Agent 现在可以通过 `RecallConversation` 工具按关键词、session ID 或日期检索历史对话轮次，返回匹配的用户消息与助手回复摘要，支持数量限制（最多 10 条）。
- **search_project_memory 工具** — Workbench 任务内新增 `search_project_memory` 工具，Agent 可按分类、来源、关键词搜索当前项目的持久化记忆条目；仅在 Workbench 项目 context 内可用，其他场景返回结构化错误。
- **右侧面板调整大小 & 导航栏折叠** — Workbench 右侧面板（查看器/地图/等）现在支持拖拽调整宽度；左侧导航 rail 支持折叠/展开，收起后只显示图标，节省水平空间。

### Changed

- **提示词缓存优化（`static_system_extra`）** — `run_agent` / `_run_chat_agent` 新增 `static_system_extra` 参数，将 Workbench 任务执行模式和产物交付规则从每轮重新注入的 `ephemeral_system` 尾部提升到字节稳定的 SYSTEM 前缀，减少重复 token 处理；Goal Loop 同步接入。
- **`temporal_context` 移到 ephemeral 尾部** — 日期/时区上下文从 SYSTEM 前缀移至每轮 ephemeral 块，防止日期翻转导致整个 system+history 前缀缓存失效。
- **对话中间消息处理** — Workbench Chat 现在正确处理工具调用过程中的中间文本块（partial assistant turns），流式展示更流畅；`workbench-model.jsx` runtime 快照新增 `watchRequestId` 字段供滚动锚定使用。
- **RecallMemory 增强** — `RecallMemory` 工具调用路径重构，统一使用 `recall_conversations` 后端；Workbench Chat 侧边栏展示记忆检索结果时支持折叠/展开查看。
- **SimpleXNG 子进程守护** — 新增 `simplexng_child.py` 模块，SimpleXNG 搜索子进程通过父进程 PID 存活检测（含 PID 复用防护）在主进程退出时自动退出，避免僵尸进程。
- **用户资料页增强** — 资料统计数据（对话轮数、活跃天数等）接入 DB 层；新增 i18n 翻译覆盖中英文；data 层新增对应查询接口。
- **WeChat 频道 Web 接口健壮性** — `wechat/web.py` 补充异常处理，防止网络抖动导致未捕获异常。

### Tests

- 新增 `tests/test_wechat_web.py` — 覆盖 WeChat web 接口异常路径。
- 新增 `tests/test_workbench_dispatch_finalize.py` — 覆盖意图分流与任务收尾（finalize）流程。
- 新增 `tests/test_workbench_memory_language.py` — 覆盖 Workbench 记忆写入的语言偏好透传。
- 新增 `tests/test_searxng_manager.py` — 覆盖 SearXNG/SimpleXNG 管理器启动与候选端点逻辑。
- 新增 `tests/test_profile_stats.py` — 覆盖用户资料统计数据查询。

## [0.6.0b5] - 2026-06-20

### Security

- **HTML artifact 隔离** — 用户生成的 HTML 文件不再允许通过「↗」按钮在普通 Electron 子窗口中打开（子窗口会继承已认证的本地后端 session，存在 API 滥用风险）。HTML 预览保留在沙盒 `srcDoc` iframe 内；Artifacts 列表和 Viewer 中的「↗」外链按钮对 HTML 文件隐藏。

### Added

- **Artifact 一键下载** — Workbench 侧边栏 Artifacts 标签页中，`file_change` 类产物现在可直接点击行下载；路径在服务端校验不得逃逸项目 workspace。
- **`send_file` 显式信号** — Agent 调用 `send_file` 工具时，该文件以 `produced` 状态记入 file-change 追踪，优先级高于 git 推断结果。
- **HTML 查看器 base href 注入** — 内嵌 HTML 预览器自动在文档头部注入 `<base href>` 标签，使相对路径的脚本和资源能正确解析。
- **Subagent 文件交付指引** — Subagent 系统提示明确：产物文件应写入 workspace 并在 `quit` 摘要中报告路径，由主 Agent 统一通过 `send_file` 交付，Subagent 不应自行调用该工具。

### Fixed

- **SQLite 并发锁死** — `behavior_learning.py`、`db.py`、`workbench_goal_loop.py` 均切换至 WAL journal 模式并设置 busy timeout（15 s），消除 goal loop 与工具/聊天写入并发时出现的 "database is locked" 错误。
- **Schema 初始化幂等** — `_ensure_schema` 引入进程级 `_SCHEMA_READY` 缓存，避免每次读写都重复执行建表事务。
- **文件路径显示** — `_workbench_display_path` 修复相对/绝对路径计算逻辑：workspace 外的路径返回空字符串而非泄漏绝对路径。
- **Git 推断不覆盖工具写入** — `_workbench_merge_file_changes` 新增保护：git 推断的状态无法降级已由 Write/Edit/send_file 明确写入的文件记录。
- **失效文件记录清理** — `_workbench_prune_invalid_file_records` 在每次状态不变量检查时移除 path 缺失或 name 为空的陈旧记录。
- **`send_file` 路径解析** — `_resolve_exportable_path` 现在以当前 Workbench 任务的 `workspacePath` 为首选根目录，修复在项目 workspace 内写入的文件无法发送的问题。

### Changed

- **步骤进展 UI 精简** — 展开步骤的详情面板由卡片网格改为紧凑的摘要行布局（进展文本与相关文件并列显示）。
- **PDF 缩放修复** — PDF embed 元素现在将缩放值编入 `key`，确保缩放变化时正确重新渲染。
- **CI pre-release 自动检测** — release workflow 中 `is_pre` 标志改为从 tag 名自动匹配（`alpha/beta/[0-9]b[0-9]/rc`），无需手动维护。

### Tests

- 新增 `tests/test_workbench_artifact_download.py` — 覆盖 artifact 下载路径解析与路径穿越防护。
- 更新 `tests/test_workbench_frontend_logic.py`、`tests/test_workbench_init_plan.py` — 补充 goal loop 及初始化计划场景。

## [0.6.0b4] - 2026-06-19

### Added

- **跨平台 Shell 运行时** — 新增 `shell_runtime` 模块，自动检测用户 Shell 类型（bash/zsh/fish/PowerShell/cmd）并据此调整命令执行策略；非 POSIX Shell 下写入/删除类命令触发安全拦截，防止误操作。
- **语言偏好持久化** — 新增 `app_language` 配置项；主动 Agent（心跳、proactive chat）现在以用户界面语言回复，语言设置跨重启保持一致。
- **附件分析增强** — 改进附件类型识别与文件名处理；文档提取支持更多格式，提升知识库 ingest 稳定性。
- **工作区搜索集成测试** — 新增 `test_workbench_search.py`、`test_shell_runtime.py`、`test_bash_nonbash_guard.py` 等测试套件，覆盖 Shell 守卫、proactive 语言、知识库归档流程。

### Changed

- **`workspace_scope_block` 增强** — 非 bash Shell 下自动追加额外警告提示，明确限制跨目录操作。
- **Proactive Agent 上下文** — `_run_chat_agent` 和 `run_heartbeat_agent` 接入检测到的 Shell 类型与语言偏好，生成更符合用户环境的回复。
- **知识库路由健壮性** — `routes_knowledge.py` / `routes_workbench_knowledge.py` 统一错误处理；`list_knowledge_documents` 工具返回更完整的文件元数据。

## [0.6.0b3] - 2026-06-19

### Added

- **ListKnowledgeDocuments 工具** — Agent 现在可以通过 `ListKnowledgeDocuments` 工具枚举当前 Workbench 会话的知识库文档，返回每个文件的名称、索引状态（可检索/未检索）和 chunk 数量，支持按 status 过滤与数量限制。
- **Workbench run 结果自动归档知识库** — 每次 Agent 完成一个 Workbench 步骤，任务摘要（目标、请求、结果、产出文件列表）将自动以 Markdown 形式归档到项目知识库，Agent 后续可通过 `SearchKnowledge` 检索历史执行记录。
- **Linux 原生目录选择器** — Electron `dialog.showOpenDialog` IPC 接口，Linux 用户在 Chat 和 Create 界面现在可以使用系统原生文件夹选择对话框（macOS/Windows 不受影响）。

### Changed

- **流式运行引擎 (module-level runtime engine)** — `WorkbenchChatRuntimes` 提升到模块级别，对话流在用户切换页面或视图时不再中断：streaming 状态（文本、工具进度、中断句柄）跨组件 mount/unmount 完整保留，切回时自动追上最新内容。
- **Follow-up 任务上下文传递** — 追踪任务通过新 API 端点创建，携带当前会话的完整 context，避免 follow-up 任务缺少背景信息导致重复探索。
- **聊天会话删除** — `DELETE /api/chats/:session_id` 正确处理 `run_live`（重置实时会话）和 `archive_*`（按日期和 session ID 定位历史会话）两类特殊会话，不再返回 400。

### Fixed

- **Workbench 依赖校验** — 计划步骤依赖图现在拒绝循环依赖、缺失依赖和非法顺序；依赖辅助函数保留可见顺序，阻止未满足依赖的步骤执行。
- **通知已读状态** — 未读通知按可见性正确管理，切换视图不再误重置已读状态。

## [0.6.0b2] - 2026-06-19

### Fixed

- **Windows updater asset selection** — `_platform_filter()` 返回的 `win64.exe` 与 CI 实际产出的 `Cyrene-<ver>-win-x64.exe` / `-win-arm64.exe` 不匹配，导致 Windows 用户被推送 macOS `.dmg`。现按 `platform.machine()` 正确区分 x64/ARM64，并修复了 Linux token 大小写不一致导致匹配失败的 latent bug。新增全套平台匹配回归测试。
- **Plan regeneration failure handling** — 重新生成计划失败时不再创建兜底计划（`model.buildPlanSteps`），而是保留原计划不变，避免数据丢失。
- **Acceptance criteria verification** — 验收模型返回的 results 数组会按标准校验是否覆盖了所有 criteria，若遗漏则抛出结构化错误而非静默接受。

### Changed

- **Workspace-scoped agent context** — `_WORKSPACE_SCOPE_BLOCK` 从编译时常量改为 `workspace_scope_block(active_workspace_dir())` 运行时函数，确保主 Agent 和 Subagent 都限定到项目实际的 workspacePath，而非全局默认目录。Chat run / answer 接口均已接入项目 workspace。
- **Init plan 生成重试** — `_workbench_generate_init_task_plan` 默认重试 5 次（原为 1 次），每次失败记录分类和原因，5 次均失败后返回结构化错误（`init_plan_generation_failed`），不再返回兜底空计划。UI 新增 `InitPlanError` 组件展示失败详情和「重新开始」按钮。
- **LLM 错误分类与脱敏** — 新增 `_WorkbenchGenerationError` 异常类和 `_workbench_generation_error()` 转换函数，将超时 / 鉴权 / 限流 / 上游 / 网络错误分类为友好提示；错误文本中的 Bearer token、sk- 密钥、api_key 等敏感信息自动脱敏。
- **JSON 解析健壮性** — `_workbench_parse_json_object` 跳过无效的花括号片段；explore agent 在 JSON 解析失败时自动重试一次修复。
- **Plan revision 的 replace 模式** — 明确要求「全新生成」时不会把旧计划步骤塞进 prompt，减少模型锚定。
- **Cache-bust 版本号更新** — `index.html` 中各 JS/CSS 资源版本戳统一更新。
- **测试覆盖新增** — updater 平台匹配、init plan 重试/恢复/错误报告、workspace scope、search workspace 集成、generation error 脱敏等场景全面覆盖。

## [0.6.0b1] - 2026-06-18

### Added

- **Beta update channel** — New "Receive beta releases" toggle on the About → Updates card. When enabled, the in-app updater scans GitHub pre-releases (via the releases list) instead of only the stable `latest`, so testers can opt into beta builds.
- **Subagent UI** — Workbench now surfaces subagent status, payload inspection, and page-independent rendering; new subagent-related i18n strings added.
- **Plan revision flow** — Agent can now detect conflicts with an existing plan and revise it in-place while preserving step progress, instead of always generating a fresh plan.
- **Proactive activity detection tests** — New test suite for proactive user-activity triggers in the workbench scheduler.
- **Knowledge data clearing** — New API and test coverage for resetting knowledge data per workspace.

### Changed

- **Acceptance criteria handling** — Criteria are normalised and reset correctly on each plan generation cycle; improved error handling in `WorkbenchModel`.
- **Workbench chat** — Parallel-conversation support: removed `lockedByOther` lock state; subagent chat snapshots handled separately from main chat stream.
- **Welcome / onboarding** — Timezone setting now uses long-value layout for readability; onboarding timezone selection covered by tests.
- **App icon refresh** — Updated icon assets across all platforms (macOS `.icns`, Windows `.ico`, Linux `.png`).
- **CSS layout** — New subagent panel styles; responsive layout fixes for project list with open context menus.

### Fixed

- `routes.py` task controller no longer silently drops plan-conflict errors; returns structured revision response instead.
- `set_task_goal.py` legacy tool updated to match revised subagent payload schema.

## [0.6.0b0] - 2026-06-17

### Added

- **Windows on ARM support** — CI now builds native ARM64 installers alongside x64.
- **Workbench UI by default** — GitHub Releases now bundle the workbench UI as the default shell.

### Changed

- Windows installer filenames now include architecture: `Cyrene-${version}-win-x64.exe` / `Cyrene-${version}-win-arm64.exe`.

## [0.5.0] - 2026-06-07

### Added

- **Browser live view** — WebSocket-based live browser screencasting directly in chat; headless→headed takeover for native-window login flows.
- **Deep Reflection** — New agent capability for multi-round context reframing, improving reasoning on ambiguous or complex queries.
- **Desktop authentication** — Local auth middleware with OS keyring integration (macOS/Windows/Linux); port persistence across restarts.
- **Session export** — Export full session history to file; tool round refactoring for cleaner agent state.
- **SSRF protection** — Blocks server-side request forgery on user-supplied URLs; screenshot temp-file cleanup.
- **Content hash deduplication** — Documents tracked by content hash to prevent duplicate uploads in knowledge base.

### Changed

- **PDF viewer** — Embedded panel with pinch-to-zoom, touch events, and iframe isolation for attachment previews.
- **Permission system** — Permission snapshot before skill execution; high-risk tool confirmation flow; workspace scope guard for read/write/shell ops.
- **MCP management** — Server restart button; per-server environment variable editing in settings UI.
- **WeChat channel** — Pending question formatting and improved response routing for group chats.
- **Chat interface** — `watchRequestId` in runtime snapshot for scroll anchoring; mutation diff for assistant reply updates; internal field stripping before LLM calls.
- **macOS notifications** — Desktop notifications via `terminal-notifier` on macOS.

### Fixed

- Model failure handling in streaming responses now surfaces errors to the user rather than silently dropping them.
- DSML tool markup in final reply with retry mechanism for malformed responses.

## [0.4.7] - 2026-05-24

### Added

- **Pattern learning improvements** — Action tracking now persists compact args, enabling more informative pattern replay. Added subsequence-based deduplication for cleaner pattern candidates. New `scan_for_manual_learn()` endpoint promotes high-confidence historical patterns immediately without waiting for a new session.
- **Skill installer overhaul** — Now supports installing skills from directories and zip archives in addition to single files. Includes validation for archives (size limits, entry count, path safety). Tracks `source_kind` (file/directory/archive) per skill record.
- **Self-aware updater** — Electron now passes `CYRENE_APP_EXECUTABLE` to the Python backend, so update scripts target the real install path instead of hardcoded locations on macOS, Windows, and Linux.
- **Evolution page enhancements** — Added new learning pattern metrics (exchanges, avg length, directive count, cadence, rounds) with i18n support. New `patternIntro` copy explaining the learn-now flow.

### Changed

- **Flat surface design refresh** — New CSS variables system (`--canvas-bg`, `--surface-*`, `--control-*`) with updated light/dark theme backgrounds. Sidebar, topbar, dashboard, cards, and settings panels migrated to flat surfaces with refined shadows.
- **Pattern scanner** — Scripts list now sorted by creation date descending. Candidate metadata expanded with `first_seen`, `last_seen`, `confidence`, and `round_ids` for better debugging.
- **Skills UI** — Install picker now accepts folders and zips (macOS `choose file or folder`). Skill detail shows source kind. Build skill prompt block uses `entrypoint_name` for display.
- **Update scripts** — macOS: no longer hardcodes `/Applications/Cyrene.app`. Windows: uses real install path with proper process creation flags (`CREATE_NEW_CONSOLE | CREATE_NEW_PROCESS_GROUP | DETACHED_PROCESS`). Linux: atomic replacement via `.new` + `mv`. All scripts now `set -e` and use shell quoting for safety.
- **Evolution page layout** — Migrated from inline styles to CSS classes. New scrollable container layout prevents overflow.

### Fixed

- **Deep research reports** — Compressed in session history to reduce token usage.

## [0.4.2] - 2026-05-24

### Fixed

- **Claude Code terminal colors** — Complete rewrite of the CC terminal color pipeline:
  - Switched tmux default-terminal from `xterm-256color` to `tmux-256color` for truecolor (24-bit) support
  - Added UTF-8-aware C1→7bit control character conversion (`_c1_to_7bit()`) to handle tmux-256color's 8-bit CSI/OSC/DCS sequences
  - Fixed shell card preview lines leaking across conversations by adding per-session CC preview caching
  - Fixed expanded terminal layout: terminal now overlays the chat area correctly, with ResizeObserver-based auto-resizing and raf-retry for xterm.js renderer readiness
  - Restored visible footer with "Cyrene learning" metadata
  - Removed duplicate title in expanded terminal

## [0.4.1] - 2026-05-23

### Added

- **Multi-turn deep research report generation** — Phase 3 now generates reports section by section:
  1. Loads a report template defining the fixed section skeleton
  2. LLM generates a JSON outline with dynamic subsections under "核心发现"
  3. Each writing unit (section/subsection) is generated in a separate LLM call, allowing arbitrarily long reports beyond single-turn output limits
  4. References are accumulated per-section via `## New References` markers and automatically deduplicated and assembled into the final References section
  5. An optional expansion pass thickens thin sections when total length is below threshold

- **User-selectable report length** — Before starting research, the agent asks the user for desired report length (long/medium/short/custom) via `ask_user` with structured options buttons. Length preference controls number of research tracks and per-section detail.

- **Default report template** (`report_template.md`) — 7-section skeleton (执行摘要, 背景, 核心发现, 分析与启示, 局限性, 结论, 参考文献) bundled in the package. The template defines fixed top-level sections while allowing dynamic subsections under "核心发现".

### Improved

- **PDF report formatting** — Complete rewrite of `report_export.py`:
  - CJK font support: Noto Sans CJK SC (fallback to STSong-Light)
  - Improved layout: larger margins (20mm), better line spacing (leading=20), increased heading spacing
  - Proper heading hierarchy with bold section titles and visual separation

- **Settings persistence** — Settings loading and saving now uses deep copy to prevent mutation issues, with atomic writes for crash safety.

### Fixed

- **Report length question resume** — When the agent pauses to ask about report length, the `deep-research` command context is now preserved through the pending question metadata, ensuring the agent resumes with full deep research prompts and spawn policy.

- **CSS cache invalidation** — Static asset version bumped to force browser cache refresh.
