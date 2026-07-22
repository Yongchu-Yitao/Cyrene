# Changelog

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
