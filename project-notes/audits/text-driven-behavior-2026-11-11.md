# Cyrene 文字匹配行为排查

排查日期：2026 年 11 月 11 日。首页问候替换已在上一轮移除。本次只排查其余同类机制，没有修改这些业务逻辑。

共整理 49 组匹配机制，覆盖后端、Web 前端、Electron、移动端、构建与首页原型。按实际调用链区分会改变执行/持久化状态、错误恢复、展示推断、安全规则、遗留函数及明确协议。

扫描包括正则、字符串包含/前后缀、比较与词表分支，并补查 Python AST 比较表达式以及 PowerShell/JXA/Swift/Kotlin。生成产物和第三方代码不作为独立问题重复计算；测试和历史实验不等同于生产行为。完整源码文件清单和逐项结果保存在同名 JSON。

## 优先修复顺序

1. 权限选项按 label 授权、终端密码提示文字决定检查豁免。
2. 工具成功/失败猜测、纠错学习、按错误文案切换 CLI 或重试、按窗口标题选择捕获源。
3. 文案驱动的记忆分数、诊断拒收、浏览器/桌面风险推断和界面状态。
4. 清理未调用入口；保留正常的命令、格式、安全过滤与搜索能力。

## 已复现的判断结果

以下检查只调用原始纯函数，权限使用内存中的虚拟记录器；没有真实授权、网络重试、下载、桌面点击或终端输入。

| 输入 | 当前结果 | 问题 |
|---|---|---|
| 提供自定义权限选项“不同意”，随后选中它 | 记录单次授权 | 拒绝词表遗漏，且其他 label 默认肯定 |
| 终端普通输出最后一行为 `Password:` | 允许 sensitive 模式检查通过 | 可见文字不等于可信输入状态 |
| 用户说“做得不错” | `correction` | 正面评价被当成纠错 |
| 工具返回“错误：文件不存在” | `is_error=false` | 中文失败漏判 |
| 工具返回 `Error count: 0` | `is_error=true` | 成功报告误判失败 |
| 控件名“发送”与 `Send` | 分别 R2、R3 | 风险受语言影响 |
| 进度“未发现错误” | `error` 样式 | 否定表达误判 |

## 实际行为与持久化状态

### 1 权限选项文字决定授权范围

[src/cyrene/core/plugin/permission_grants.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/core/plugin/permission_grants.py:86)

只验证答案属于提供的 label，再用拒绝词表判断否定；含“本次会话”“始终”则保存会话授权，否则保存单次授权。

优先修复。隔离复现：如果提供的自定义选项是“不同意”，选择它仍会记录单次授权。默认“拒绝”选项不受这个例子影响。应使用 decision 与 scope 字段，未知值拒绝。

### 2 密码提示文字决定敏感终端输入是否放行

[src/cyrene/plugins/builtin/cyrene_code/send_shell.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/plugins/builtin/cyrene_code/send_shell.py:65)、[src/cyrene/plugins/builtin/cyrene_code/send_shell.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/plugins/builtin/cyrene_code/send_shell.py:85)

sensitive=true 跳过普通命令的工作区写入目标检查，再匹配屏幕末四行的 password、PIN、密码等词来决定能否输入。

优先修复。普通输出最后一行为 Password: 也通过判断；屏幕文字不能证明终端真的处于凭据输入状态。应绑定可信输入状态或明确授权，不把显示文字作为检查豁免依据。

### 3 自然语言被记录为上一轮纠错

[src/cyrene/plugins/builtin/cyrene_skills/orchestrator.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/plugins/builtin/cyrene_skills/orchestrator.py:208)、[src/cyrene/plugins/builtin/cyrene_skills/lifecycle.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/plugins/builtin/cyrene_skills/lifecycle.py:36)、[src/cyrene/plugins/builtin/cyrene_skills/capture.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/plugins/builtin/cyrene_skills/capture.py:490)

匹配“错”“重新”“fix”“retry”等片段后，将上一轮 behavior_turns 写成 correction。

已复现“做得不错”也得到 correction。应使用明确反馈事件或语义判断，不能按单字决定负面学习证据。

### 4 工具输出文字决定失败状态

[src/cyrene/workbench/application/inbox.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/workbench/application/inbox.py:213)、[src/cyrene/workbench/application/inbox.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/workbench/application/inbox.py:972)、[src/cyrene/plugins/builtin/cyrene_memory/structured.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/plugins/builtin/cyrene_memory/structured.py:966)、[src/cyrene/plugins/builtin/cyrene_memory/structured.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/plugins/builtin/cyrene_memory/structured.py:954)

非结构化工具结果以 error、failed 等英文开头则算失败；同时影响收件箱工具事件及能否作为 verified result 供记忆提取。

已复现“错误：文件不存在”未被识别为失败，而 Error count: 0 被识别为失败。统一使用工具执行结果的 ok、failure、error_code；无法判定的文本不能标为 verified。

### 5 内部学习任务由提示词前缀识别

[src/cyrene/plugins/builtin/cyrene_skills/orchestrator.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/plugins/builtin/cyrene_skills/orchestrator.py:938)、[src/cyrene/plugins/builtin/cyrene_skills/orchestrator.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/plugins/builtin/cyrene_skills/orchestrator.py:1310)

除了 system_initiated 字段，还按固定内部提示词前缀排除学习证据，并重写旧 behavior_turns 的内容和元数据。

属于历史兼容，但普通用户文字若恰好匹配也会受影响。迁移应按记录版本和来源一次性处理，运行时只看来源字段。

### 6 短期记忆情绪分数由英文词片段决定

[src/cyrene/plugins/builtin/cyrene_memory/short_term.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/plugins/builtin/cyrene_memory/short_term.py:313)

frustrat、stress、angry 赋 -2；happy、love、excit 赋 +2，然后持久化 emotional_valence。

不理解否定和中文。not happy 按规则会得到正值。应由结构化提取结果给出情绪，或不保存推测分数。

### 7 Doctor 依据建议文字拒收诊断

[src/cyrene/platform/doctor/agent_analysis.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/platform/doctor/agent_analysis.py:74)

next_steps 出现“写入测试”或 write test 时，返回 accepted=false，要求模型重交诊断。

会连带拦住 write tests 这类正常测试建议，也可因同义表达漏判。应验证结构化修复动作和能力，而不是建议措辞。

### 8 ComfyUI 文本提取任务编号和下载地址

[src/cyrene/plugins/builtin/cyrene_media/providers/comfyui.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/plugins/builtin/cyrene_media/providers/comfyui.py:73)、[src/cyrene/plugins/builtin/cyrene_media/providers/comfyui.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/plugins/builtin/cyrene_media/providers/comfyui.py:107)、[src/cyrene/plugins/builtin/cyrene_media/providers/comfyui.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/plugins/builtin/cyrene_media/providers/comfyui.py:605)、[src/cyrene/plugins/builtin/cyrene_media/providers/comfyui.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/plugins/builtin/cyrene_media/providers/comfyui.py:690)

对结果中的 prompt/job/task id 作正则提取；没有目录或内嵌产物时，从输出文本枚举 URL 并下载为媒体产物。

会把说明或示例 URL 当成产物候选。应要求适配器返回明确的 job_id 和 artifacts；现有下载校验仍保留。

### 9 Linux 原生动作名称决定实际执行哪个动作

[electron/automation/app-use-linux.js](/Users/syw/Documents/playground/Cyrene/electron/automation/app-use-linux.js:38)、[electron/automation/app-use-linux.js](/Users/syw/Documents/playground/Cyrene/electron/automation/app-use-linux.js:142)、[electron/automation/app-use-linux.js](/Users/syw/Documents/playground/Cyrene/electron/automation/app-use-linux.js:302)

将 AT-SPI 动作名按 click、select、toggle 等词映射，再选对应 descriptor.index 调用 DoAction。

这是系统兼容层，但匹配过宽或本地化动作名会错分。优先使用平台动作契约和显式映射，未知动作不得猜成另一种动作。

### 10 RDP 按窗口标题回退选择捕获源

[electron/remote-desktop/remote-desktop-rdp-sidecar.js](/Users/syw/Documents/playground/Cyrene/electron/remote-desktop/remote-desktop-rdp-sidecar.js:352)

按窗口 ID 找不到捕获源后，取名称匹配 freerdp 或 remote desktop 的第一个窗口。

有多个远程窗口时可能选错捕获对象。应绑定当前会话的窗口与进程身份，失败时明确报错。

### 11 终端名称从用户句子中猜取

[src/cyrene/plugins/builtin/cyrene_code/services.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/plugins/builtin/cyrene_code/services.py:14)、[src/cyrene/plugins/builtin/cyrene_code/services.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/plugins/builtin/cyrene_code/services.py:38)、[src/cyrene/plugins/builtin/cyrene_code/start_shell.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/plugins/builtin/cyrene_code/start_shell.py:55)

没有显式 title 时，匹配“名为”“叫作”或 terminal named 等表达，作为新终端标题。

只补全已调用的建终端操作，不会仅凭句子创建终端；仍会误取否定句或无关名称。让调用方显式提供 title。

### 12 通用对话组标题触发重新生成

[src/cyrene/workbench/chat/chat_service.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/workbench/chat/chat_service.py:544)、[src/cyrene/workbench/chat/chat_service.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/workbench/chat/chat_service.py:595)

去标点后匹配 Chat group、新对话组等黑名单，将标题清空并触发最多两次元数据生成；仍失败则选已有标题。

这是文案质量规则，确实会增加模型调用。应与标题有效性字段分开，保留人工锁定标题规则。

### 13 通用模型错误分类与重试

[src/cyrene/model/error_details.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/model/error_details.py:211)、[src/cyrene/model/error_details.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/model/error_details.py:267)

从错误字符串提取 HTTP 状态和 quota、authentication、context window、timeout 等词，决定错误码、retryable 与 retry_scope。

已有结构化信息应始终优先；文本仅作明确标记的兼容回退。会影响停止、延迟重试或调整参数后重试。

### 14 Codex 可用性由错误文字推断

[src/cyrene/model/codex_provider.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/model/codex_provider.py:140)

quota、unsupported model、token expired 等文字可转成额度耗尽、模型不可用、登录过期错误。

和通用错误分类同类，应优先使用 codexErrorInfo 与 HTTP 状态。

### 15 Codex 协议错误文字触发 CLI 版本回退

[src/cyrene/model/codex_provider.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/model/codex_provider.py:407)、[src/cyrene/model/codex_provider.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/model/codex_provider.py:415)

匹配版本冲突词，排除 TLS 等词后，下载 SDK 固定版本 CLI；已安装固定版本则不做。

实际会下载和切换运行时，优先级高于一般错误提示。应通过明确的协议版本能力或错误类型触发。

### 16 媒体生成错误文字决定自动重试

[src/cyrene/plugins/builtin/cyrene_media/providers/minimax.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/plugins/builtin/cyrene_media/providers/minimax.py:68)、[src/cyrene/plugins/builtin/cyrene_media/providers/google_media.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/plugins/builtin/cyrene_media/providers/google_media.py:40)、[src/cyrene/plugins/builtin/cyrene_media/providers/comfyui.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/plugins/builtin/cyrene_media/providers/comfyui.py:119)、[src/cyrene/plugins/builtin/cyrene_media/manager.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/plugins/builtin/cyrene_media/manager.py:525)

MiniMax、Google、ComfyUI 按 busy、rate、unavailable 等文本判断 retryable，作业管理器在剩余次数内据此重试。

MiniMax 已有业务 status_code，应建立错误码表；Google 优先异常状态；ComfyUI 保留结构化 MCP 错误。文本判断可能重复昂贵请求。

### 17 SQLite 错误文字决定重试或兼容路径

[src/cyrene/workbench/persistence/schema.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/workbench/persistence/schema.py:240)、[src/cyrene/plugins/builtin/cyrene_remote/common.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/plugins/builtin/cyrene_remote/common.py:33)、[src/cyrene/workbench/application/inbox.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/workbench/application/inbox.py:1820)、[src/cyrene/workbench/application/inbox.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/workbench/application/inbox.py:1873)、[src/cyrene/workbench/control/control_ports.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/workbench/control/control_ports.py:193)、[src/cyrene/plugins/builtin/cyrene_schedule/migrations.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/plugins/builtin/cyrene_schedule/migrations.py:67)、[src/cyrene/plugins/builtin/cyrene_knowledge/store.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/plugins/builtin/cyrene_knowledge/store.py:609)

locked 决定 schema 重试；busy 决定远程错误类别；no such table、duplicate column name、fts5 决定忽略错误或跳过兼容分支。

技术兼容逻辑，不是用户措辞识别。可用 sqlite_errorcode、schema introspection 和功能探测替代可替代部分。

### 18 移动端串口错误文字决定继续等待

[mobile/runtime-app/src/main/kotlin/ai/cyrene/mobile/runtime/QemuVirtualMachine.kt](/Users/syw/Documents/playground/Cyrene/mobile/runtime-app/src/main/kotlin/ai/cyrene/mobile/runtime/QemuVirtualMachine.kt:261)

IOException 消息含 Try again 或 EAGAIN 时继续 readUntil 循环，否则抛出错误。

可能因平台文案改变而中断。优先用 errno 或明确的可重试异常类型；仍受超时限制。

### 19 导航错误文字决定等待替代导航

[electron/desktop/main-window-lifecycle.js](/Users/syw/Documents/playground/Cyrene/electron/desktop/main-window-lifecycle.js:32)、[electron/desktop/main-window-lifecycle.js](/Users/syw/Documents/playground/Cyrene/electron/desktop/main-window-lifecycle.js:101)

在 errno/code 不足时用 ERR_ABORTED (-3) 文本识别取消，转去等待替代导航。

已有代码优先结构化字段；字符串仅是兼容回退，可通过统一导航错误对象消除。

### 20 服务错误文字决定 HTTP 状态与错误类别

[src/cyrene/plugins/builtin/cyrene_cli/application.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/plugins/builtin/cyrene_cli/application.py:80)、[src/cyrene/plugins/builtin/cyrene_cli/application.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/plugins/builtin/cyrene_cli/application.py:129)、[src/cyrene/plugins/builtin/cyrene_schedule/workbench_service.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/plugins/builtin/cyrene_schedule/workbench_service.py:103)、[src/cyrene/plugins/builtin/cyrene_remote/pairing.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/plugins/builtin/cyrene_remote/pairing.py:585)、[src/cyrene/plugins/builtin/cyrene_remote/services.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/plugins/builtin/cyrene_remote/services.py:202)、[src/cyrene/core/plugin/core_impl/toolbox.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/core/plugin/core_impl/toolbox.py:417)

not found 决定 404/400；配对异常的英文整句决定错误码；Invalid arguments 或中文等价文案决定插件参数错误类型。

应用内部错误应使用专用异常类或 code，避免修改提示文案改变 API 行为。

### 21 模型目录与项目记忆错误兼容回退

[src/cyrene/plugins/model_catalog.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/plugins/model_catalog.py:401)、[src/cyrene/plugins/builtin/cyrene_model/routes.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/plugins/builtin/cyrene_model/routes.py:730)、[src/cyrene/plugins/builtin/cyrene_memory/project_memory.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/plugins/builtin/cyrene_memory/project_memory.py:1125)、[src/cyrene/plugins/builtin/cyrene_memory/project_memory.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/plugins/builtin/cyrene_memory/project_memory.py:1202)

精确英文错误文字控制模型配置缺失时的回退、无效目录识别、项目记忆模型不可用转换及上下文溢出分类。

主要是内部接口耦合；应传递稳定异常类型和错误码。

### 22 计划是否被拒绝由答案词表推断

[src/cyrene/workbench/core_adapter/bridge.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/workbench/core_adapter/bridge.py:334)

投影 pending plan 时，将词表中的“拒绝”“不同意”等标成 rejected，其余答案标成 active。

这是 active_plan 投影，不等同于实际权限授权。应传递结构化 decision，未知回答不能展示成同意。

### 23 进度标签由文字决定颜色

[src/cyrene/workbench/webui/frontend/features/chat/assistant-card-model.mjs](/Users/syw/Documents/playground/Cyrene/src/cyrene/workbench/webui/frontend/features/chat/assistant-card-model.mjs:59)、[src/cyrene/workbench/webui/frontend/features/chat/assistant-cards.jsx](/Users/syw/Documents/playground/Cyrene/src/cyrene/workbench/webui/frontend/features/chat/assistant-cards.jsx:61)

匹配失败、已完成等文案输出 tone。

已复现“未发现错误”显示 error；只改变样式，不结束任务。应使用状态枚举和独立 label。

### 24 天气描述决定默认图标

[src/cyrene/workbench/webui/frontend/features/chat/card-weather-model.mjs](/Users/syw/Documents/playground/Cyrene/src/cyrene/workbench/webui/frontend/features/chat/card-weather-model.mjs:14)

icon 不是有效枚举时，从 condition 的中英文天气词猜图标；有效 icon 优先。

只影响图标。天气条件应提供有效 icon，缺失可直接显示 unknown；按时间切换日夜本身不是文字匹配。

### 25 运行阶段与活动标签决定忙碌展示

[src/cyrene/workbench/webui/frontend/features/session/activity.jsx](/Users/syw/Documents/playground/Cyrene/src/cyrene/workbench/webui/frontend/features/session/activity.jsx:234)、[src/cyrene/workbench/webui/frontend/features/session/activity.jsx](/Users/syw/Documents/playground/Cyrene/src/cyrene/workbench/webui/frontend/features/session/activity.jsx:309)、[src/cyrene/workbench/webui/frontend/features/session/activity.jsx](/Users/syw/Documents/playground/Cyrene/src/cyrene/workbench/webui/frontend/features/session/activity.jsx:549)

plan 推断 planning；browser/web/click 推断浏览器活动；done/complete 等片段推断阶段是否结束。

改变 UI 活跃状态及活动内容。incomplete 也包含 complete。应使用阶段枚举；后端同名旧 helper 另列为遗留代码。

### 26 工具名称决定图标分类与失败提示

[src/cyrene/workbench/webui/frontend/features/chat/messages.jsx](/Users/syw/Documents/playground/Cyrene/src/cyrene/workbench/webui/frontend/features/chat/messages.jsx:865)、[src/cyrene/workbench/webui/frontend/features/chat/messages.jsx](/Users/syw/Documents/playground/Cyrene/src/cyrene/workbench/webui/frontend/features/chat/messages.jsx:878)、[src/cyrene/workbench/webui/frontend/features/chat/file-resources.jsx](/Users/syw/Documents/playground/Cyrene/src/cyrene/workbench/webui/frontend/features/chat/file-resources.jsx:186)、[src/cyrene/workbench/webui/frontend/workbench-memory.jsx](/Users/syw/Documents/playground/Cyrene/src/cyrene/workbench/webui/frontend/workbench-memory.jsx:575)

通过工具名关键词选类别和图标；tool failed: 前缀可给事件标失败。

显示层，不能作为执行结果来源。工具应声明 presentation kind，失败应使用事件字段。

### 27 权限按钮文案与位置决定显示标签

[src/cyrene/workbench/webui/frontend/features/chat/behavior.mjs](/Users/syw/Documents/playground/Cyrene/src/cyrene/workbench/webui/frontend/features/chat/behavior.mjs:51)

综合 optionId/id/kind/value/label/title 匹配 allow_once/session 等值，匹配不上时还按按钮数量和位置推断。

只改变显示标签，但可能让用户看到的授权范围与提交的原值不一致。应优先明确 optionId，未知选项原样显示。

### 28 外部 Agent 警告文本被移出正文

[src/cyrene/agents/notices.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/agents/notices.py:17)、[src/cyrene/agents/notices.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/agents/notices.py:60)、[src/cyrene/workbench/chat/chat_external_turn_service.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/workbench/chat/chat_external_turn_service.py:123)

识别开头 warning:/notice: 和网络术语，分离为运行通知，不再当作普通回答正文。

当前 ACP 兼容处理；不会按文字重试。普通回答若讨论相同句式可能被误归类，应通过事件通道携带通知。

### 29 浏览器正文推断访问限制信号

[src/cyrene/plugins/builtin/cyrene_browser/runtime.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/plugins/builtin/cyrene_browser/runtime.py:374)、[electron/main.js](/Users/syw/Documents/playground/Cyrene/electron/main.js:1015)

正文同时含“暂时无法访问”与“登录后查看”等词时返回 access_gate、retry_allowed、冷却时间等提示。

给助理提供恢复建议；这两个函数本身不执行重试。文章引用这些词也可能误判，应结合 DOM 和响应状态并标为启发式。

### 30 桌面控件文字推断风险等级

[src/cyrene/plugins/builtin/cyrene_desktop/_app_semantic_backend.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/plugins/builtin/cyrene_desktop/_app_semantic_backend.py:125)、[src/cyrene/plugins/builtin/cyrene_desktop/_app_semantic_backend.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/plugins/builtin/cyrene_desktop/_app_semantic_backend.py:191)

名称/描述/role 含 delete、pay、send 等英文片段则标 R3，否则点击类标 R2。

已复现“发送”R2 而 Send 为 R3。这里只返回风险元数据，不能说它单独放行执行。应结合声明的操作语义，并对未知风险保守处理。

### 31 系统提示词和内部任务按文案分组

[src/cyrene/workbench/chat/conversation_context_service.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/workbench/chat/conversation_context_service.py:1140)、[src/cyrene/workbench/webui/frontend/workbench-memory.jsx](/Users/syw/Documents/playground/Cyrene/src/cyrene/workbench/webui/frontend/workbench-memory.jsx:438)、[src/cyrene/workbench/webui/frontend/platform/ui-surface.jsx](/Users/syw/Documents/playground/Cyrene/src/cyrene/workbench/webui/frontend/platform/ui-surface.jsx:448)

按 You are、workspace、toolbox 等分上下文视图；按主动检查提示词识别记忆 UI 标题；按 action_id 前缀推断界面影响描述。

分组或描述层，不会把 prose 变成工具调用。应优先用已有来源、block kind 和 action outcome。

### 32 安装和平台错误文字决定诊断提示

[src/cyrene/plugins/builtin/cyrene_extensions/extension_service.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/plugins/builtin/cyrene_extensions/extension_service.py:251)、[src/cyrene/plugins/builtin/cyrene_extensions/extension_service.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/plugins/builtin/cyrene_extensions/extension_service.py:839)、[src/cyrene/workbench/webui/frontend/features/chat/errors.jsx](/Users/syw/Documents/playground/Cyrene/src/cyrene/workbench/webui/frontend/features/chat/errors.jsx:57)、[src/cyrene/workbench/webui/frontend/platform/api.jsx](/Users/syw/Documents/playground/Cyrene/src/cyrene/workbench/webui/frontend/platform/api.jsx:71)、[src/cyrene/workbench/webui/frontend/settings-model-configuration.jsx](/Users/syw/Documents/playground/Cyrene/src/cyrene/workbench/webui/frontend/settings-model-configuration.jsx:401)、[src/cyrene/plugins/builtin/cyrene_desktop/_app_use_backend.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/plugins/builtin/cyrene_desktop/_app_use_backend.py:316)、[electron/automation/app-use-macos.jxa](/Users/syw/Documents/playground/Cyrene/electron/automation/app-use-macos.jxa:582)、[electron/automation/app-use-windows.ps1](/Users/syw/Documents/playground/Cyrene/electron/automation/app-use-windows.ps1:677)、[electron/remote-desktop/remote-desktop-rdp-sidecar.js](/Users/syw/Documents/playground/Cyrene/electron/remote-desktop/remote-desktop-rdp-sidecar.js:66)

从错误文字生成原因类别、翻译或修复提示；安装任务的失败已由异常决定，不是由文案决定。

提示容易错类或漏翻译。应在源头携带原因码。与会触发重试的分支分开修复。

### 33 原生控件的通用文字参与语义树裁剪

[electron/automation/app-use-linux.js](/Users/syw/Documents/playground/Cyrene/electron/automation/app-use-linux.js:251)、[electron/automation/app-use-windows.ps1](/Users/syw/Documents/playground/Cyrene/electron/automation/app-use-windows.ps1:372)、[electron/automation/app-use.js](/Users/syw/Documents/playground/Cyrene/electron/automation/app-use.js:907)

group、pane、未知等 role/description 和控件名被用于透明容器判断、可操作节点统计与语义覆盖率判断。

影响助理看到的元素和覆盖率提示。平台结构信息优先，不能仅因通用文案隐藏有效目标。

### 34 模型名称决定兼容协议与请求约束

[src/cyrene/model/protocol_adapters.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/model/protocol_adapters.py:81)、[src/cyrene/model/protocol_adapters.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/model/protocol_adapters.py:751)、[src/cyrene/plugins/builtin/cyrene_media/model_catalog.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/plugins/builtin/cyrene_media/model_catalog.py:219)、[src/cyrene/plugins/builtin/cyrene_media/providers/registry.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/plugins/builtin/cyrene_media/providers/registry.py:148)、[src/cyrene/plugins/builtin/cyrene_media/providers/openai_image.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/plugins/builtin/cyrene_media/providers/openai_image.py:88)、[src/cyrene/plugins/builtin/cyrene_media/providers/google_media.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/plugins/builtin/cyrene_media/providers/google_media.py:171)、[src/cyrene/plugins/builtin/cyrene_media/providers/seedance.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/plugins/builtin/cyrene_media/providers/seedance.py:48)、[src/cyrene/plugins/builtin/cyrene_media/providers/minimax.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/plugins/builtin/cyrene_media/providers/minimax.py:478)

模型 ID 的前缀或子串用于协议、媒体能力、参数允许范围等兼容分支。

这是技术型号匹配，不是问候文案，但也有改名后失效的问题。能力和约束应来自模型目录元数据，保留明确版本兼容表。

### 35 模型名称决定上下文与计费估算

[src/cyrene/platform/config_store.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/platform/config_store.py:891)、[src/cyrene/workbench/projects/project_runtime.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/workbench/projects/project_runtime.py:116)、[src/cyrene/platform/attachments.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/platform/attachments.py:299)、[src/cyrene/model/image_tokens.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/model/image_tokens.py:35)、[src/cyrene/model/pricing.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/model/pricing.py:72)

通过 claude、deepseek、qwen 等名称猜上下文或视觉能力，通过模型别名匹配价格。

能力元数据不足时的回退；不应覆盖显式配置。模型名称变化可能影响压缩阈值、附件处理及估算。

### 36 记忆文本安全规则拒绝持久化

[src/cyrene/plugins/builtin/cyrene_memory/project_memory.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/plugins/builtin/cyrene_memory/project_memory.py:106)、[src/cyrene/plugins/builtin/cyrene_memory/project_memory.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/plugins/builtin/cyrene_memory/project_memory.py:890)、[src/cyrene/plugins/builtin/cyrene_memory/project_memory.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/plugins/builtin/cyrene_memory/project_memory.py:1029)

密钥格式、提示词注入语句和有限否定前缀决定是否拒绝模型生成的项目记忆。

有意的防护，不应直接删掉；不能把有限词表当成完整语义保证。应保留来源边界并用结构化学习结果减少歧义。

### 37 文本工具协议泄漏触发撤回与重试

[src/cyrene/plugins/builtin/cyrene_model/response_guard.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/plugins/builtin/cyrene_model/response_guard.py:25)、[src/cyrene/plugins/builtin/cyrene_model/response_guard.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/plugins/builtin/cyrene_model/response_guard.py:56)

回答正文出现 DSML 协议标记会抛模型错误、发 stream_cancelled，走有限重试路径。

这是协议完整性检查，不把文本提升为可执行工具。讨论协议标记的正常答案也可能命中；可按 provider/通道限定，而不是无条件删除检查。

### 38 远程能力名称与 Shell 文本触发额外边界

[src/cyrene/plugins/builtin/cyrene_remote/permission.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/plugins/builtin/cyrene_remote/permission.py:190)、[src/cyrene/plugins/builtin/cyrene_remote/commands.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/plugins/builtin/cyrene_remote/commands.py:2208)、[src/cyrene/core/plugin/core_impl/permission_boundaries.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/core/plugin/core_impl/permission_boundaries.py:184)、[src/cyrene/plugins/native_runtime.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/plugins/native_runtime.py:256)

删除类能力名触发破坏性确认；长 Shell 输入带 base64 解码词被要求改走文件通道；命令结构匹配用于工作区与破坏性操作检查。

确实决定允许/拒绝，但属于命令与能力安全检查。应使用声明的 resource effects 和可靠命令分析，不能因为它们使用匹配就整体移除。

### 39 字段名与凭据格式决定脱敏

[src/cyrene/platform/secret_redaction.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/platform/secret_redaction.py:13)、[src/cyrene/model/errors.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/model/errors.py:13)、[src/cyrene/agents/acp_events.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/agents/acp_events.py:51)、[src/cyrene/core/observability.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/core/observability.py:73)、[src/cyrene/workbench/application/app_control.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/workbench/application/app_control.py:273)、[src/cyrene/plugins/builtin/cyrene_extensions/extension_service.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/plugins/builtin/cyrene_extensions/extension_service.py:230)、[src/cyrene/plugins/builtin/cyrene_extensions/extension_agent_runtime.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/plugins/builtin/cyrene_extensions/extension_agent_runtime.py:71)、[src/cyrene/plugins/builtin/cyrene_skills/capture.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/plugins/builtin/cyrene_skills/capture.py:230)、[src/cyrene/platform/doctor/evidence.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/platform/doctor/evidence.py:7)

secret/token/password 等键名及密钥、用户路径格式触发内容替换或不记录。

有意的隐私保护。会误遮普通字段，也可能漏掉非标准密钥；优先采用字段级敏感声明，文本扫描保留为补充。

### 40 技能参数正则提取的遗留入口

[src/cyrene/plugins/builtin/cyrene_skills/lifecycle.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/plugins/builtin/cyrene_skills/lifecycle.py:510)、[src/cyrene/plugins/builtin/cyrene_skills/lifecycle.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/plugins/builtin/cyrene_skills/lifecycle.py:549)、[src/cyrene/plugins/builtin/cyrene_skills/orchestrator.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/plugins/builtin/cyrene_skills/orchestrator.py:1103)

取第一个路径、数字、日期、URL 或示例匹配作为参数；仅缺少必填项才调用 LLM。

仓库生产调用搜索只找到定义和包装，没有实际调用入口。属于潜在问题，不列为当前自动执行链；若恢复使用，应改为显式参数绑定。

### 41 按失败文字生成技能修补类型的遗留入口

[src/cyrene/plugins/builtin/cyrene_skills/lifecycle.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/plugins/builtin/cyrene_skills/lifecycle.py:338)、[src/cyrene/plugins/builtin/cyrene_skills/orchestrator.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/plugins/builtin/cyrene_skills/orchestrator.py:1047)

missing、parameter、参数决定提议更新输入 schema，否则替换步骤。

只找到定义和包装，未找到生产调用者；不要把它描述成当前自动修补行为。即使启用也只是提出补丁，不直接执行补丁。

### 42 旧后端活动推断 helper

[src/cyrene/workbench/sessions/session_view.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/workbench/sessions/session_view.py:75)

phase_transition.to 含 done/complete 等片段则认为阶段不活跃。

全仓库引用搜索只找到定义。前端同类逻辑仍活跃，已单列；该后端 helper 可考虑删除。

### 43 首页原型直接按用户文字触发演示动作

[prototypes/assistant-home/src/App.jsx](/Users/syw/Documents/playground/Cyrene/prototypes/assistant-home/src/App.jsx:35)

旅行关键词切换 travel；创建任务关键词新增模拟任务；日程、进度、总结等词选择固定回复与文档。

仅 prototypes 演示代码，不是正式 Cyrene 首页实现。若未来复用，必须移除关键词动作分派。

### 44 原型服务按句子选择流程与卡片

[prototypes/assistant-home/server/demo-engine.mjs](/Users/syw/Documents/playground/Cyrene/prototypes/assistant-home/server/demo-engine.mjs:26)、[prototypes/assistant-home/server/demo-engine.mjs](/Users/syw/Documents/playground/Cyrene/prototypes/assistant-home/server/demo-engine.mjs:41)、[prototypes/assistant-home/server/resident-cards.mjs](/Users/syw/Documents/playground/Cyrene/prototypes/assistant-home/server/resident-cards.mjs:18)

按预算、旅行天数、重选资料、风险等词设置流程状态、内容、地图和推荐卡片。

独立演示服务的规则引擎，属于用户所说的同类机制；不应混入生产逻辑。

### 45 显式命令与输入语法

[src/cyrene/workbench/application/commands.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/workbench/application/commands.py:68)、[src/cyrene/workbench/webui/frontend/features/chat/composer-submission.jsx](/Users/syw/Documents/playground/Cyrene/src/cyrene/workbench/webui/frontend/features/chat/composer-submission.jsx:12)、[src/cyrene/cli_chat.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/cli_chat.py:2756)、[src/cyrene/workbench/http/settings/profile_data.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/workbench/http/settings/profile_data.py:84)

解析 /命令、参数、布尔输入和 RESET CYRENE DATA 确认短语。

用户明确使用的命令/确认协议，不是从自由文案猜意图。保留，仍需正常权限校验。

### 46 显式文字定位与等待条件

[src/cyrene/plugins/builtin/cyrene_browser/runtime.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/plugins/builtin/cyrene_browser/runtime.py:2588)、[electron/browser/browser-target.js](/Users/syw/Documents/playground/Cyrene/electron/browser/browser-target.js:31)、[electron/automation/app-use.js](/Users/syw/Documents/playground/Cyrene/electron/automation/app-use.js:1417)、[mobile/app/src/main/assets/browser/page-commands.js](/Users/syw/Documents/playground/Cyrene/mobile/app/src/main/assets/browser/page-commands.js:26)、[src/cyrene/plugins/builtin/cyrene_code/workspace_execution.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/plugins/builtin/cyrene_code/workspace_execution.py:555)

调用方明确给定 text/contains/readyPattern 时匹配目标或标记服务 ready。

这是工具定义的查询与等待功能。保留；多个命中、否定匹配和目标身份应由工具契约处理。

### 47 Markdown 和持久化文本格式协议

[src/cyrene/workbench/chat/chat_application.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/workbench/chat/chat_application.py:1495)、[src/cyrene/workbench/webui/frontend/shared/markdown/renderer.jsx](/Users/syw/Documents/playground/Cyrene/src/cyrene/workbench/webui/frontend/shared/markdown/renderer.jsx:170)、[src/cyrene/plugins/builtin/cyrene_soul/store.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/plugins/builtin/cyrene_soul/store.py:168)、[src/cyrene/plugins/builtin/cyrene_memory/steward.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/plugins/builtin/cyrene_memory/steward.py:25)、[src/cyrene/plugins/builtin/cyrene_memory/archive_format.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/plugins/builtin/cyrene_memory/archive_format.py:10)

解析按钮指令块、SOUL 编辑命令、ENTITY 和历史归档格式。

属于有明确定义的语法，不能等同于看到“删除”就删除。若重构，采用结构化数据迁移，而非只删正则。

### 48 语音标点静音与朗读分段

[src/cyrene/plugins/builtin/cyrene_voice/engine.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/plugins/builtin/cyrene_voice/engine.py:504)、[src/cyrene/workbench/webui/frontend/features/chat/voice-input.jsx](/Users/syw/Documents/playground/Cyrene/src/cyrene/workbench/webui/frontend/features/chat/voice-input.jsx:61)、[src/cyrene/workbench/webui/frontend/features/chat/voice-playback.jsx](/Users/syw/Documents/playground/Cyrene/src/cyrene/workbench/webui/frontend/features/chat/voice-playback.jsx:147)、[src/cyrene/workbench/webui/frontend/features/chat/voice-playback.jsx](/Users/syw/Documents/playground/Cyrene/src/cyrene/workbench/webui/frontend/features/chat/voice-playback.jsx:283)

过滤静音标记/无效字符、判断句子与 Markdown 边界后分段朗读。

文本处理本身就是功能；并不据自由文字启动任务。保留，需针对语言和分段误差验证。

### 49 技术格式与搜索匹配

[src/cyrene/model/tool_names.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/model/tool_names.py:11)、[src/cyrene/plugins/builtin/cyrene_knowledge/store.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/plugins/builtin/cyrene_knowledge/store.py:312)、[src/cyrene/workbench/webui/frontend/features/chat/context-graph-model.mjs](/Users/syw/Documents/playground/Cyrene/src/cyrene/workbench/webui/frontend/features/chat/context-graph-model.mjs:53)、[electron/backend/backend-process.js](/Users/syw/Documents/playground/Cyrene/electron/backend/backend-process.js:24)

ID、路径、MIME、URL、版本号、HTTP/SSE/OSC、PORT 输出、关键字搜索与测试输出断言。

正常协议/格式处理。扫描中的同类命中归入此类，不列为业务状态误判。构建脚本、测试与历史实验的匹配也按用途归类。

## 错误文本决定恢复与降级

### 1 权限选项文字决定授权范围

[src/cyrene/core/plugin/permission_grants.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/core/plugin/permission_grants.py:86)

只验证答案属于提供的 label，再用拒绝词表判断否定；含“本次会话”“始终”则保存会话授权，否则保存单次授权。

优先修复。隔离复现：如果提供的自定义选项是“不同意”，选择它仍会记录单次授权。默认“拒绝”选项不受这个例子影响。应使用 decision 与 scope 字段，未知值拒绝。

### 2 密码提示文字决定敏感终端输入是否放行

[src/cyrene/plugins/builtin/cyrene_code/send_shell.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/plugins/builtin/cyrene_code/send_shell.py:65)、[src/cyrene/plugins/builtin/cyrene_code/send_shell.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/plugins/builtin/cyrene_code/send_shell.py:85)

sensitive=true 跳过普通命令的工作区写入目标检查，再匹配屏幕末四行的 password、PIN、密码等词来决定能否输入。

优先修复。普通输出最后一行为 Password: 也通过判断；屏幕文字不能证明终端真的处于凭据输入状态。应绑定可信输入状态或明确授权，不把显示文字作为检查豁免依据。

### 3 自然语言被记录为上一轮纠错

[src/cyrene/plugins/builtin/cyrene_skills/orchestrator.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/plugins/builtin/cyrene_skills/orchestrator.py:208)、[src/cyrene/plugins/builtin/cyrene_skills/lifecycle.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/plugins/builtin/cyrene_skills/lifecycle.py:36)、[src/cyrene/plugins/builtin/cyrene_skills/capture.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/plugins/builtin/cyrene_skills/capture.py:490)

匹配“错”“重新”“fix”“retry”等片段后，将上一轮 behavior_turns 写成 correction。

已复现“做得不错”也得到 correction。应使用明确反馈事件或语义判断，不能按单字决定负面学习证据。

### 4 工具输出文字决定失败状态

[src/cyrene/workbench/application/inbox.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/workbench/application/inbox.py:213)、[src/cyrene/workbench/application/inbox.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/workbench/application/inbox.py:972)、[src/cyrene/plugins/builtin/cyrene_memory/structured.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/plugins/builtin/cyrene_memory/structured.py:966)、[src/cyrene/plugins/builtin/cyrene_memory/structured.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/plugins/builtin/cyrene_memory/structured.py:954)

非结构化工具结果以 error、failed 等英文开头则算失败；同时影响收件箱工具事件及能否作为 verified result 供记忆提取。

已复现“错误：文件不存在”未被识别为失败，而 Error count: 0 被识别为失败。统一使用工具执行结果的 ok、failure、error_code；无法判定的文本不能标为 verified。

### 5 内部学习任务由提示词前缀识别

[src/cyrene/plugins/builtin/cyrene_skills/orchestrator.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/plugins/builtin/cyrene_skills/orchestrator.py:938)、[src/cyrene/plugins/builtin/cyrene_skills/orchestrator.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/plugins/builtin/cyrene_skills/orchestrator.py:1310)

除了 system_initiated 字段，还按固定内部提示词前缀排除学习证据，并重写旧 behavior_turns 的内容和元数据。

属于历史兼容，但普通用户文字若恰好匹配也会受影响。迁移应按记录版本和来源一次性处理，运行时只看来源字段。

### 6 短期记忆情绪分数由英文词片段决定

[src/cyrene/plugins/builtin/cyrene_memory/short_term.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/plugins/builtin/cyrene_memory/short_term.py:313)

frustrat、stress、angry 赋 -2；happy、love、excit 赋 +2，然后持久化 emotional_valence。

不理解否定和中文。not happy 按规则会得到正值。应由结构化提取结果给出情绪，或不保存推测分数。

### 7 Doctor 依据建议文字拒收诊断

[src/cyrene/platform/doctor/agent_analysis.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/platform/doctor/agent_analysis.py:74)

next_steps 出现“写入测试”或 write test 时，返回 accepted=false，要求模型重交诊断。

会连带拦住 write tests 这类正常测试建议，也可因同义表达漏判。应验证结构化修复动作和能力，而不是建议措辞。

### 8 ComfyUI 文本提取任务编号和下载地址

[src/cyrene/plugins/builtin/cyrene_media/providers/comfyui.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/plugins/builtin/cyrene_media/providers/comfyui.py:73)、[src/cyrene/plugins/builtin/cyrene_media/providers/comfyui.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/plugins/builtin/cyrene_media/providers/comfyui.py:107)、[src/cyrene/plugins/builtin/cyrene_media/providers/comfyui.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/plugins/builtin/cyrene_media/providers/comfyui.py:605)、[src/cyrene/plugins/builtin/cyrene_media/providers/comfyui.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/plugins/builtin/cyrene_media/providers/comfyui.py:690)

对结果中的 prompt/job/task id 作正则提取；没有目录或内嵌产物时，从输出文本枚举 URL 并下载为媒体产物。

会把说明或示例 URL 当成产物候选。应要求适配器返回明确的 job_id 和 artifacts；现有下载校验仍保留。

### 9 Linux 原生动作名称决定实际执行哪个动作

[electron/automation/app-use-linux.js](/Users/syw/Documents/playground/Cyrene/electron/automation/app-use-linux.js:38)、[electron/automation/app-use-linux.js](/Users/syw/Documents/playground/Cyrene/electron/automation/app-use-linux.js:142)、[electron/automation/app-use-linux.js](/Users/syw/Documents/playground/Cyrene/electron/automation/app-use-linux.js:302)

将 AT-SPI 动作名按 click、select、toggle 等词映射，再选对应 descriptor.index 调用 DoAction。

这是系统兼容层，但匹配过宽或本地化动作名会错分。优先使用平台动作契约和显式映射，未知动作不得猜成另一种动作。

### 10 RDP 按窗口标题回退选择捕获源

[electron/remote-desktop/remote-desktop-rdp-sidecar.js](/Users/syw/Documents/playground/Cyrene/electron/remote-desktop/remote-desktop-rdp-sidecar.js:352)

按窗口 ID 找不到捕获源后，取名称匹配 freerdp 或 remote desktop 的第一个窗口。

有多个远程窗口时可能选错捕获对象。应绑定当前会话的窗口与进程身份，失败时明确报错。

### 11 终端名称从用户句子中猜取

[src/cyrene/plugins/builtin/cyrene_code/services.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/plugins/builtin/cyrene_code/services.py:14)、[src/cyrene/plugins/builtin/cyrene_code/services.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/plugins/builtin/cyrene_code/services.py:38)、[src/cyrene/plugins/builtin/cyrene_code/start_shell.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/plugins/builtin/cyrene_code/start_shell.py:55)

没有显式 title 时，匹配“名为”“叫作”或 terminal named 等表达，作为新终端标题。

只补全已调用的建终端操作，不会仅凭句子创建终端；仍会误取否定句或无关名称。让调用方显式提供 title。

### 12 通用对话组标题触发重新生成

[src/cyrene/workbench/chat/chat_service.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/workbench/chat/chat_service.py:544)、[src/cyrene/workbench/chat/chat_service.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/workbench/chat/chat_service.py:595)

去标点后匹配 Chat group、新对话组等黑名单，将标题清空并触发最多两次元数据生成；仍失败则选已有标题。

这是文案质量规则，确实会增加模型调用。应与标题有效性字段分开，保留人工锁定标题规则。

### 13 通用模型错误分类与重试

[src/cyrene/model/error_details.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/model/error_details.py:211)、[src/cyrene/model/error_details.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/model/error_details.py:267)

从错误字符串提取 HTTP 状态和 quota、authentication、context window、timeout 等词，决定错误码、retryable 与 retry_scope。

已有结构化信息应始终优先；文本仅作明确标记的兼容回退。会影响停止、延迟重试或调整参数后重试。

### 14 Codex 可用性由错误文字推断

[src/cyrene/model/codex_provider.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/model/codex_provider.py:140)

quota、unsupported model、token expired 等文字可转成额度耗尽、模型不可用、登录过期错误。

和通用错误分类同类，应优先使用 codexErrorInfo 与 HTTP 状态。

### 15 Codex 协议错误文字触发 CLI 版本回退

[src/cyrene/model/codex_provider.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/model/codex_provider.py:407)、[src/cyrene/model/codex_provider.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/model/codex_provider.py:415)

匹配版本冲突词，排除 TLS 等词后，下载 SDK 固定版本 CLI；已安装固定版本则不做。

实际会下载和切换运行时，优先级高于一般错误提示。应通过明确的协议版本能力或错误类型触发。

### 16 媒体生成错误文字决定自动重试

[src/cyrene/plugins/builtin/cyrene_media/providers/minimax.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/plugins/builtin/cyrene_media/providers/minimax.py:68)、[src/cyrene/plugins/builtin/cyrene_media/providers/google_media.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/plugins/builtin/cyrene_media/providers/google_media.py:40)、[src/cyrene/plugins/builtin/cyrene_media/providers/comfyui.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/plugins/builtin/cyrene_media/providers/comfyui.py:119)、[src/cyrene/plugins/builtin/cyrene_media/manager.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/plugins/builtin/cyrene_media/manager.py:525)

MiniMax、Google、ComfyUI 按 busy、rate、unavailable 等文本判断 retryable，作业管理器在剩余次数内据此重试。

MiniMax 已有业务 status_code，应建立错误码表；Google 优先异常状态；ComfyUI 保留结构化 MCP 错误。文本判断可能重复昂贵请求。

### 17 SQLite 错误文字决定重试或兼容路径

[src/cyrene/workbench/persistence/schema.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/workbench/persistence/schema.py:240)、[src/cyrene/plugins/builtin/cyrene_remote/common.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/plugins/builtin/cyrene_remote/common.py:33)、[src/cyrene/workbench/application/inbox.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/workbench/application/inbox.py:1820)、[src/cyrene/workbench/application/inbox.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/workbench/application/inbox.py:1873)、[src/cyrene/workbench/control/control_ports.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/workbench/control/control_ports.py:193)、[src/cyrene/plugins/builtin/cyrene_schedule/migrations.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/plugins/builtin/cyrene_schedule/migrations.py:67)、[src/cyrene/plugins/builtin/cyrene_knowledge/store.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/plugins/builtin/cyrene_knowledge/store.py:609)

locked 决定 schema 重试；busy 决定远程错误类别；no such table、duplicate column name、fts5 决定忽略错误或跳过兼容分支。

技术兼容逻辑，不是用户措辞识别。可用 sqlite_errorcode、schema introspection 和功能探测替代可替代部分。

### 18 移动端串口错误文字决定继续等待

[mobile/runtime-app/src/main/kotlin/ai/cyrene/mobile/runtime/QemuVirtualMachine.kt](/Users/syw/Documents/playground/Cyrene/mobile/runtime-app/src/main/kotlin/ai/cyrene/mobile/runtime/QemuVirtualMachine.kt:261)

IOException 消息含 Try again 或 EAGAIN 时继续 readUntil 循环，否则抛出错误。

可能因平台文案改变而中断。优先用 errno 或明确的可重试异常类型；仍受超时限制。

### 19 导航错误文字决定等待替代导航

[electron/desktop/main-window-lifecycle.js](/Users/syw/Documents/playground/Cyrene/electron/desktop/main-window-lifecycle.js:32)、[electron/desktop/main-window-lifecycle.js](/Users/syw/Documents/playground/Cyrene/electron/desktop/main-window-lifecycle.js:101)

在 errno/code 不足时用 ERR_ABORTED (-3) 文本识别取消，转去等待替代导航。

已有代码优先结构化字段；字符串仅是兼容回退，可通过统一导航错误对象消除。

### 20 服务错误文字决定 HTTP 状态与错误类别

[src/cyrene/plugins/builtin/cyrene_cli/application.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/plugins/builtin/cyrene_cli/application.py:80)、[src/cyrene/plugins/builtin/cyrene_cli/application.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/plugins/builtin/cyrene_cli/application.py:129)、[src/cyrene/plugins/builtin/cyrene_schedule/workbench_service.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/plugins/builtin/cyrene_schedule/workbench_service.py:103)、[src/cyrene/plugins/builtin/cyrene_remote/pairing.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/plugins/builtin/cyrene_remote/pairing.py:585)、[src/cyrene/plugins/builtin/cyrene_remote/services.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/plugins/builtin/cyrene_remote/services.py:202)、[src/cyrene/core/plugin/core_impl/toolbox.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/core/plugin/core_impl/toolbox.py:417)

not found 决定 404/400；配对异常的英文整句决定错误码；Invalid arguments 或中文等价文案决定插件参数错误类型。

应用内部错误应使用专用异常类或 code，避免修改提示文案改变 API 行为。

### 21 模型目录与项目记忆错误兼容回退

[src/cyrene/plugins/model_catalog.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/plugins/model_catalog.py:401)、[src/cyrene/plugins/builtin/cyrene_model/routes.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/plugins/builtin/cyrene_model/routes.py:730)、[src/cyrene/plugins/builtin/cyrene_memory/project_memory.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/plugins/builtin/cyrene_memory/project_memory.py:1125)、[src/cyrene/plugins/builtin/cyrene_memory/project_memory.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/plugins/builtin/cyrene_memory/project_memory.py:1202)

精确英文错误文字控制模型配置缺失时的回退、无效目录识别、项目记忆模型不可用转换及上下文溢出分类。

主要是内部接口耦合；应传递稳定异常类型和错误码。

### 22 计划是否被拒绝由答案词表推断

[src/cyrene/workbench/core_adapter/bridge.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/workbench/core_adapter/bridge.py:334)

投影 pending plan 时，将词表中的“拒绝”“不同意”等标成 rejected，其余答案标成 active。

这是 active_plan 投影，不等同于实际权限授权。应传递结构化 decision，未知回答不能展示成同意。

### 23 进度标签由文字决定颜色

[src/cyrene/workbench/webui/frontend/features/chat/assistant-card-model.mjs](/Users/syw/Documents/playground/Cyrene/src/cyrene/workbench/webui/frontend/features/chat/assistant-card-model.mjs:59)、[src/cyrene/workbench/webui/frontend/features/chat/assistant-cards.jsx](/Users/syw/Documents/playground/Cyrene/src/cyrene/workbench/webui/frontend/features/chat/assistant-cards.jsx:61)

匹配失败、已完成等文案输出 tone。

已复现“未发现错误”显示 error；只改变样式，不结束任务。应使用状态枚举和独立 label。

### 24 天气描述决定默认图标

[src/cyrene/workbench/webui/frontend/features/chat/card-weather-model.mjs](/Users/syw/Documents/playground/Cyrene/src/cyrene/workbench/webui/frontend/features/chat/card-weather-model.mjs:14)

icon 不是有效枚举时，从 condition 的中英文天气词猜图标；有效 icon 优先。

只影响图标。天气条件应提供有效 icon，缺失可直接显示 unknown；按时间切换日夜本身不是文字匹配。

### 25 运行阶段与活动标签决定忙碌展示

[src/cyrene/workbench/webui/frontend/features/session/activity.jsx](/Users/syw/Documents/playground/Cyrene/src/cyrene/workbench/webui/frontend/features/session/activity.jsx:234)、[src/cyrene/workbench/webui/frontend/features/session/activity.jsx](/Users/syw/Documents/playground/Cyrene/src/cyrene/workbench/webui/frontend/features/session/activity.jsx:309)、[src/cyrene/workbench/webui/frontend/features/session/activity.jsx](/Users/syw/Documents/playground/Cyrene/src/cyrene/workbench/webui/frontend/features/session/activity.jsx:549)

plan 推断 planning；browser/web/click 推断浏览器活动；done/complete 等片段推断阶段是否结束。

改变 UI 活跃状态及活动内容。incomplete 也包含 complete。应使用阶段枚举；后端同名旧 helper 另列为遗留代码。

### 26 工具名称决定图标分类与失败提示

[src/cyrene/workbench/webui/frontend/features/chat/messages.jsx](/Users/syw/Documents/playground/Cyrene/src/cyrene/workbench/webui/frontend/features/chat/messages.jsx:865)、[src/cyrene/workbench/webui/frontend/features/chat/messages.jsx](/Users/syw/Documents/playground/Cyrene/src/cyrene/workbench/webui/frontend/features/chat/messages.jsx:878)、[src/cyrene/workbench/webui/frontend/features/chat/file-resources.jsx](/Users/syw/Documents/playground/Cyrene/src/cyrene/workbench/webui/frontend/features/chat/file-resources.jsx:186)、[src/cyrene/workbench/webui/frontend/workbench-memory.jsx](/Users/syw/Documents/playground/Cyrene/src/cyrene/workbench/webui/frontend/workbench-memory.jsx:575)

通过工具名关键词选类别和图标；tool failed: 前缀可给事件标失败。

显示层，不能作为执行结果来源。工具应声明 presentation kind，失败应使用事件字段。

### 27 权限按钮文案与位置决定显示标签

[src/cyrene/workbench/webui/frontend/features/chat/behavior.mjs](/Users/syw/Documents/playground/Cyrene/src/cyrene/workbench/webui/frontend/features/chat/behavior.mjs:51)

综合 optionId/id/kind/value/label/title 匹配 allow_once/session 等值，匹配不上时还按按钮数量和位置推断。

只改变显示标签，但可能让用户看到的授权范围与提交的原值不一致。应优先明确 optionId，未知选项原样显示。

### 28 外部 Agent 警告文本被移出正文

[src/cyrene/agents/notices.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/agents/notices.py:17)、[src/cyrene/agents/notices.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/agents/notices.py:60)、[src/cyrene/workbench/chat/chat_external_turn_service.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/workbench/chat/chat_external_turn_service.py:123)

识别开头 warning:/notice: 和网络术语，分离为运行通知，不再当作普通回答正文。

当前 ACP 兼容处理；不会按文字重试。普通回答若讨论相同句式可能被误归类，应通过事件通道携带通知。

### 29 浏览器正文推断访问限制信号

[src/cyrene/plugins/builtin/cyrene_browser/runtime.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/plugins/builtin/cyrene_browser/runtime.py:374)、[electron/main.js](/Users/syw/Documents/playground/Cyrene/electron/main.js:1015)

正文同时含“暂时无法访问”与“登录后查看”等词时返回 access_gate、retry_allowed、冷却时间等提示。

给助理提供恢复建议；这两个函数本身不执行重试。文章引用这些词也可能误判，应结合 DOM 和响应状态并标为启发式。

### 30 桌面控件文字推断风险等级

[src/cyrene/plugins/builtin/cyrene_desktop/_app_semantic_backend.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/plugins/builtin/cyrene_desktop/_app_semantic_backend.py:125)、[src/cyrene/plugins/builtin/cyrene_desktop/_app_semantic_backend.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/plugins/builtin/cyrene_desktop/_app_semantic_backend.py:191)

名称/描述/role 含 delete、pay、send 等英文片段则标 R3，否则点击类标 R2。

已复现“发送”R2 而 Send 为 R3。这里只返回风险元数据，不能说它单独放行执行。应结合声明的操作语义，并对未知风险保守处理。

### 31 系统提示词和内部任务按文案分组

[src/cyrene/workbench/chat/conversation_context_service.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/workbench/chat/conversation_context_service.py:1140)、[src/cyrene/workbench/webui/frontend/workbench-memory.jsx](/Users/syw/Documents/playground/Cyrene/src/cyrene/workbench/webui/frontend/workbench-memory.jsx:438)、[src/cyrene/workbench/webui/frontend/platform/ui-surface.jsx](/Users/syw/Documents/playground/Cyrene/src/cyrene/workbench/webui/frontend/platform/ui-surface.jsx:448)

按 You are、workspace、toolbox 等分上下文视图；按主动检查提示词识别记忆 UI 标题；按 action_id 前缀推断界面影响描述。

分组或描述层，不会把 prose 变成工具调用。应优先用已有来源、block kind 和 action outcome。

### 32 安装和平台错误文字决定诊断提示

[src/cyrene/plugins/builtin/cyrene_extensions/extension_service.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/plugins/builtin/cyrene_extensions/extension_service.py:251)、[src/cyrene/plugins/builtin/cyrene_extensions/extension_service.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/plugins/builtin/cyrene_extensions/extension_service.py:839)、[src/cyrene/workbench/webui/frontend/features/chat/errors.jsx](/Users/syw/Documents/playground/Cyrene/src/cyrene/workbench/webui/frontend/features/chat/errors.jsx:57)、[src/cyrene/workbench/webui/frontend/platform/api.jsx](/Users/syw/Documents/playground/Cyrene/src/cyrene/workbench/webui/frontend/platform/api.jsx:71)、[src/cyrene/workbench/webui/frontend/settings-model-configuration.jsx](/Users/syw/Documents/playground/Cyrene/src/cyrene/workbench/webui/frontend/settings-model-configuration.jsx:401)、[src/cyrene/plugins/builtin/cyrene_desktop/_app_use_backend.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/plugins/builtin/cyrene_desktop/_app_use_backend.py:316)、[electron/automation/app-use-macos.jxa](/Users/syw/Documents/playground/Cyrene/electron/automation/app-use-macos.jxa:582)、[electron/automation/app-use-windows.ps1](/Users/syw/Documents/playground/Cyrene/electron/automation/app-use-windows.ps1:677)、[electron/remote-desktop/remote-desktop-rdp-sidecar.js](/Users/syw/Documents/playground/Cyrene/electron/remote-desktop/remote-desktop-rdp-sidecar.js:66)

从错误文字生成原因类别、翻译或修复提示；安装任务的失败已由异常决定，不是由文案决定。

提示容易错类或漏翻译。应在源头携带原因码。与会触发重试的分支分开修复。

### 33 原生控件的通用文字参与语义树裁剪

[electron/automation/app-use-linux.js](/Users/syw/Documents/playground/Cyrene/electron/automation/app-use-linux.js:251)、[electron/automation/app-use-windows.ps1](/Users/syw/Documents/playground/Cyrene/electron/automation/app-use-windows.ps1:372)、[electron/automation/app-use.js](/Users/syw/Documents/playground/Cyrene/electron/automation/app-use.js:907)

group、pane、未知等 role/description 和控件名被用于透明容器判断、可操作节点统计与语义覆盖率判断。

影响助理看到的元素和覆盖率提示。平台结构信息优先，不能仅因通用文案隐藏有效目标。

### 34 模型名称决定兼容协议与请求约束

[src/cyrene/model/protocol_adapters.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/model/protocol_adapters.py:81)、[src/cyrene/model/protocol_adapters.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/model/protocol_adapters.py:751)、[src/cyrene/plugins/builtin/cyrene_media/model_catalog.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/plugins/builtin/cyrene_media/model_catalog.py:219)、[src/cyrene/plugins/builtin/cyrene_media/providers/registry.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/plugins/builtin/cyrene_media/providers/registry.py:148)、[src/cyrene/plugins/builtin/cyrene_media/providers/openai_image.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/plugins/builtin/cyrene_media/providers/openai_image.py:88)、[src/cyrene/plugins/builtin/cyrene_media/providers/google_media.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/plugins/builtin/cyrene_media/providers/google_media.py:171)、[src/cyrene/plugins/builtin/cyrene_media/providers/seedance.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/plugins/builtin/cyrene_media/providers/seedance.py:48)、[src/cyrene/plugins/builtin/cyrene_media/providers/minimax.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/plugins/builtin/cyrene_media/providers/minimax.py:478)

模型 ID 的前缀或子串用于协议、媒体能力、参数允许范围等兼容分支。

这是技术型号匹配，不是问候文案，但也有改名后失效的问题。能力和约束应来自模型目录元数据，保留明确版本兼容表。

### 35 模型名称决定上下文与计费估算

[src/cyrene/platform/config_store.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/platform/config_store.py:891)、[src/cyrene/workbench/projects/project_runtime.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/workbench/projects/project_runtime.py:116)、[src/cyrene/platform/attachments.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/platform/attachments.py:299)、[src/cyrene/model/image_tokens.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/model/image_tokens.py:35)、[src/cyrene/model/pricing.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/model/pricing.py:72)

通过 claude、deepseek、qwen 等名称猜上下文或视觉能力，通过模型别名匹配价格。

能力元数据不足时的回退；不应覆盖显式配置。模型名称变化可能影响压缩阈值、附件处理及估算。

### 36 记忆文本安全规则拒绝持久化

[src/cyrene/plugins/builtin/cyrene_memory/project_memory.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/plugins/builtin/cyrene_memory/project_memory.py:106)、[src/cyrene/plugins/builtin/cyrene_memory/project_memory.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/plugins/builtin/cyrene_memory/project_memory.py:890)、[src/cyrene/plugins/builtin/cyrene_memory/project_memory.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/plugins/builtin/cyrene_memory/project_memory.py:1029)

密钥格式、提示词注入语句和有限否定前缀决定是否拒绝模型生成的项目记忆。

有意的防护，不应直接删掉；不能把有限词表当成完整语义保证。应保留来源边界并用结构化学习结果减少歧义。

### 37 文本工具协议泄漏触发撤回与重试

[src/cyrene/plugins/builtin/cyrene_model/response_guard.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/plugins/builtin/cyrene_model/response_guard.py:25)、[src/cyrene/plugins/builtin/cyrene_model/response_guard.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/plugins/builtin/cyrene_model/response_guard.py:56)

回答正文出现 DSML 协议标记会抛模型错误、发 stream_cancelled，走有限重试路径。

这是协议完整性检查，不把文本提升为可执行工具。讨论协议标记的正常答案也可能命中；可按 provider/通道限定，而不是无条件删除检查。

### 38 远程能力名称与 Shell 文本触发额外边界

[src/cyrene/plugins/builtin/cyrene_remote/permission.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/plugins/builtin/cyrene_remote/permission.py:190)、[src/cyrene/plugins/builtin/cyrene_remote/commands.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/plugins/builtin/cyrene_remote/commands.py:2208)、[src/cyrene/core/plugin/core_impl/permission_boundaries.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/core/plugin/core_impl/permission_boundaries.py:184)、[src/cyrene/plugins/native_runtime.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/plugins/native_runtime.py:256)

删除类能力名触发破坏性确认；长 Shell 输入带 base64 解码词被要求改走文件通道；命令结构匹配用于工作区与破坏性操作检查。

确实决定允许/拒绝，但属于命令与能力安全检查。应使用声明的 resource effects 和可靠命令分析，不能因为它们使用匹配就整体移除。

### 39 字段名与凭据格式决定脱敏

[src/cyrene/platform/secret_redaction.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/platform/secret_redaction.py:13)、[src/cyrene/model/errors.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/model/errors.py:13)、[src/cyrene/agents/acp_events.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/agents/acp_events.py:51)、[src/cyrene/core/observability.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/core/observability.py:73)、[src/cyrene/workbench/application/app_control.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/workbench/application/app_control.py:273)、[src/cyrene/plugins/builtin/cyrene_extensions/extension_service.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/plugins/builtin/cyrene_extensions/extension_service.py:230)、[src/cyrene/plugins/builtin/cyrene_extensions/extension_agent_runtime.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/plugins/builtin/cyrene_extensions/extension_agent_runtime.py:71)、[src/cyrene/plugins/builtin/cyrene_skills/capture.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/plugins/builtin/cyrene_skills/capture.py:230)、[src/cyrene/platform/doctor/evidence.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/platform/doctor/evidence.py:7)

secret/token/password 等键名及密钥、用户路径格式触发内容替换或不记录。

有意的隐私保护。会误遮普通字段，也可能漏掉非标准密钥；优先采用字段级敏感声明，文本扫描保留为补充。

### 40 技能参数正则提取的遗留入口

[src/cyrene/plugins/builtin/cyrene_skills/lifecycle.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/plugins/builtin/cyrene_skills/lifecycle.py:510)、[src/cyrene/plugins/builtin/cyrene_skills/lifecycle.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/plugins/builtin/cyrene_skills/lifecycle.py:549)、[src/cyrene/plugins/builtin/cyrene_skills/orchestrator.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/plugins/builtin/cyrene_skills/orchestrator.py:1103)

取第一个路径、数字、日期、URL 或示例匹配作为参数；仅缺少必填项才调用 LLM。

仓库生产调用搜索只找到定义和包装，没有实际调用入口。属于潜在问题，不列为当前自动执行链；若恢复使用，应改为显式参数绑定。

### 41 按失败文字生成技能修补类型的遗留入口

[src/cyrene/plugins/builtin/cyrene_skills/lifecycle.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/plugins/builtin/cyrene_skills/lifecycle.py:338)、[src/cyrene/plugins/builtin/cyrene_skills/orchestrator.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/plugins/builtin/cyrene_skills/orchestrator.py:1047)

missing、parameter、参数决定提议更新输入 schema，否则替换步骤。

只找到定义和包装，未找到生产调用者；不要把它描述成当前自动修补行为。即使启用也只是提出补丁，不直接执行补丁。

### 42 旧后端活动推断 helper

[src/cyrene/workbench/sessions/session_view.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/workbench/sessions/session_view.py:75)

phase_transition.to 含 done/complete 等片段则认为阶段不活跃。

全仓库引用搜索只找到定义。前端同类逻辑仍活跃，已单列；该后端 helper 可考虑删除。

### 43 首页原型直接按用户文字触发演示动作

[prototypes/assistant-home/src/App.jsx](/Users/syw/Documents/playground/Cyrene/prototypes/assistant-home/src/App.jsx:35)

旅行关键词切换 travel；创建任务关键词新增模拟任务；日程、进度、总结等词选择固定回复与文档。

仅 prototypes 演示代码，不是正式 Cyrene 首页实现。若未来复用，必须移除关键词动作分派。

### 44 原型服务按句子选择流程与卡片

[prototypes/assistant-home/server/demo-engine.mjs](/Users/syw/Documents/playground/Cyrene/prototypes/assistant-home/server/demo-engine.mjs:26)、[prototypes/assistant-home/server/demo-engine.mjs](/Users/syw/Documents/playground/Cyrene/prototypes/assistant-home/server/demo-engine.mjs:41)、[prototypes/assistant-home/server/resident-cards.mjs](/Users/syw/Documents/playground/Cyrene/prototypes/assistant-home/server/resident-cards.mjs:18)

按预算、旅行天数、重选资料、风险等词设置流程状态、内容、地图和推荐卡片。

独立演示服务的规则引擎，属于用户所说的同类机制；不应混入生产逻辑。

### 45 显式命令与输入语法

[src/cyrene/workbench/application/commands.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/workbench/application/commands.py:68)、[src/cyrene/workbench/webui/frontend/features/chat/composer-submission.jsx](/Users/syw/Documents/playground/Cyrene/src/cyrene/workbench/webui/frontend/features/chat/composer-submission.jsx:12)、[src/cyrene/cli_chat.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/cli_chat.py:2756)、[src/cyrene/workbench/http/settings/profile_data.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/workbench/http/settings/profile_data.py:84)

解析 /命令、参数、布尔输入和 RESET CYRENE DATA 确认短语。

用户明确使用的命令/确认协议，不是从自由文案猜意图。保留，仍需正常权限校验。

### 46 显式文字定位与等待条件

[src/cyrene/plugins/builtin/cyrene_browser/runtime.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/plugins/builtin/cyrene_browser/runtime.py:2588)、[electron/browser/browser-target.js](/Users/syw/Documents/playground/Cyrene/electron/browser/browser-target.js:31)、[electron/automation/app-use.js](/Users/syw/Documents/playground/Cyrene/electron/automation/app-use.js:1417)、[mobile/app/src/main/assets/browser/page-commands.js](/Users/syw/Documents/playground/Cyrene/mobile/app/src/main/assets/browser/page-commands.js:26)、[src/cyrene/plugins/builtin/cyrene_code/workspace_execution.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/plugins/builtin/cyrene_code/workspace_execution.py:555)

调用方明确给定 text/contains/readyPattern 时匹配目标或标记服务 ready。

这是工具定义的查询与等待功能。保留；多个命中、否定匹配和目标身份应由工具契约处理。

### 47 Markdown 和持久化文本格式协议

[src/cyrene/workbench/chat/chat_application.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/workbench/chat/chat_application.py:1495)、[src/cyrene/workbench/webui/frontend/shared/markdown/renderer.jsx](/Users/syw/Documents/playground/Cyrene/src/cyrene/workbench/webui/frontend/shared/markdown/renderer.jsx:170)、[src/cyrene/plugins/builtin/cyrene_soul/store.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/plugins/builtin/cyrene_soul/store.py:168)、[src/cyrene/plugins/builtin/cyrene_memory/steward.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/plugins/builtin/cyrene_memory/steward.py:25)、[src/cyrene/plugins/builtin/cyrene_memory/archive_format.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/plugins/builtin/cyrene_memory/archive_format.py:10)

解析按钮指令块、SOUL 编辑命令、ENTITY 和历史归档格式。

属于有明确定义的语法，不能等同于看到“删除”就删除。若重构，采用结构化数据迁移，而非只删正则。

### 48 语音标点静音与朗读分段

[src/cyrene/plugins/builtin/cyrene_voice/engine.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/plugins/builtin/cyrene_voice/engine.py:504)、[src/cyrene/workbench/webui/frontend/features/chat/voice-input.jsx](/Users/syw/Documents/playground/Cyrene/src/cyrene/workbench/webui/frontend/features/chat/voice-input.jsx:61)、[src/cyrene/workbench/webui/frontend/features/chat/voice-playback.jsx](/Users/syw/Documents/playground/Cyrene/src/cyrene/workbench/webui/frontend/features/chat/voice-playback.jsx:147)、[src/cyrene/workbench/webui/frontend/features/chat/voice-playback.jsx](/Users/syw/Documents/playground/Cyrene/src/cyrene/workbench/webui/frontend/features/chat/voice-playback.jsx:283)

过滤静音标记/无效字符、判断句子与 Markdown 边界后分段朗读。

文本处理本身就是功能；并不据自由文字启动任务。保留，需针对语言和分段误差验证。

### 49 技术格式与搜索匹配

[src/cyrene/model/tool_names.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/model/tool_names.py:11)、[src/cyrene/plugins/builtin/cyrene_knowledge/store.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/plugins/builtin/cyrene_knowledge/store.py:312)、[src/cyrene/workbench/webui/frontend/features/chat/context-graph-model.mjs](/Users/syw/Documents/playground/Cyrene/src/cyrene/workbench/webui/frontend/features/chat/context-graph-model.mjs:53)、[electron/backend/backend-process.js](/Users/syw/Documents/playground/Cyrene/electron/backend/backend-process.js:24)

ID、路径、MIME、URL、版本号、HTTP/SSE/OSC、PORT 输出、关键字搜索与测试输出断言。

正常协议/格式处理。扫描中的同类命中归入此类，不列为业务状态误判。构建脚本、测试与历史实验的匹配也按用途归类。

## 展示或向助理提供的推断

### 1 权限选项文字决定授权范围

[src/cyrene/core/plugin/permission_grants.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/core/plugin/permission_grants.py:86)

只验证答案属于提供的 label，再用拒绝词表判断否定；含“本次会话”“始终”则保存会话授权，否则保存单次授权。

优先修复。隔离复现：如果提供的自定义选项是“不同意”，选择它仍会记录单次授权。默认“拒绝”选项不受这个例子影响。应使用 decision 与 scope 字段，未知值拒绝。

### 2 密码提示文字决定敏感终端输入是否放行

[src/cyrene/plugins/builtin/cyrene_code/send_shell.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/plugins/builtin/cyrene_code/send_shell.py:65)、[src/cyrene/plugins/builtin/cyrene_code/send_shell.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/plugins/builtin/cyrene_code/send_shell.py:85)

sensitive=true 跳过普通命令的工作区写入目标检查，再匹配屏幕末四行的 password、PIN、密码等词来决定能否输入。

优先修复。普通输出最后一行为 Password: 也通过判断；屏幕文字不能证明终端真的处于凭据输入状态。应绑定可信输入状态或明确授权，不把显示文字作为检查豁免依据。

### 3 自然语言被记录为上一轮纠错

[src/cyrene/plugins/builtin/cyrene_skills/orchestrator.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/plugins/builtin/cyrene_skills/orchestrator.py:208)、[src/cyrene/plugins/builtin/cyrene_skills/lifecycle.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/plugins/builtin/cyrene_skills/lifecycle.py:36)、[src/cyrene/plugins/builtin/cyrene_skills/capture.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/plugins/builtin/cyrene_skills/capture.py:490)

匹配“错”“重新”“fix”“retry”等片段后，将上一轮 behavior_turns 写成 correction。

已复现“做得不错”也得到 correction。应使用明确反馈事件或语义判断，不能按单字决定负面学习证据。

### 4 工具输出文字决定失败状态

[src/cyrene/workbench/application/inbox.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/workbench/application/inbox.py:213)、[src/cyrene/workbench/application/inbox.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/workbench/application/inbox.py:972)、[src/cyrene/plugins/builtin/cyrene_memory/structured.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/plugins/builtin/cyrene_memory/structured.py:966)、[src/cyrene/plugins/builtin/cyrene_memory/structured.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/plugins/builtin/cyrene_memory/structured.py:954)

非结构化工具结果以 error、failed 等英文开头则算失败；同时影响收件箱工具事件及能否作为 verified result 供记忆提取。

已复现“错误：文件不存在”未被识别为失败，而 Error count: 0 被识别为失败。统一使用工具执行结果的 ok、failure、error_code；无法判定的文本不能标为 verified。

### 5 内部学习任务由提示词前缀识别

[src/cyrene/plugins/builtin/cyrene_skills/orchestrator.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/plugins/builtin/cyrene_skills/orchestrator.py:938)、[src/cyrene/plugins/builtin/cyrene_skills/orchestrator.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/plugins/builtin/cyrene_skills/orchestrator.py:1310)

除了 system_initiated 字段，还按固定内部提示词前缀排除学习证据，并重写旧 behavior_turns 的内容和元数据。

属于历史兼容，但普通用户文字若恰好匹配也会受影响。迁移应按记录版本和来源一次性处理，运行时只看来源字段。

### 6 短期记忆情绪分数由英文词片段决定

[src/cyrene/plugins/builtin/cyrene_memory/short_term.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/plugins/builtin/cyrene_memory/short_term.py:313)

frustrat、stress、angry 赋 -2；happy、love、excit 赋 +2，然后持久化 emotional_valence。

不理解否定和中文。not happy 按规则会得到正值。应由结构化提取结果给出情绪，或不保存推测分数。

### 7 Doctor 依据建议文字拒收诊断

[src/cyrene/platform/doctor/agent_analysis.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/platform/doctor/agent_analysis.py:74)

next_steps 出现“写入测试”或 write test 时，返回 accepted=false，要求模型重交诊断。

会连带拦住 write tests 这类正常测试建议，也可因同义表达漏判。应验证结构化修复动作和能力，而不是建议措辞。

### 8 ComfyUI 文本提取任务编号和下载地址

[src/cyrene/plugins/builtin/cyrene_media/providers/comfyui.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/plugins/builtin/cyrene_media/providers/comfyui.py:73)、[src/cyrene/plugins/builtin/cyrene_media/providers/comfyui.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/plugins/builtin/cyrene_media/providers/comfyui.py:107)、[src/cyrene/plugins/builtin/cyrene_media/providers/comfyui.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/plugins/builtin/cyrene_media/providers/comfyui.py:605)、[src/cyrene/plugins/builtin/cyrene_media/providers/comfyui.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/plugins/builtin/cyrene_media/providers/comfyui.py:690)

对结果中的 prompt/job/task id 作正则提取；没有目录或内嵌产物时，从输出文本枚举 URL 并下载为媒体产物。

会把说明或示例 URL 当成产物候选。应要求适配器返回明确的 job_id 和 artifacts；现有下载校验仍保留。

### 9 Linux 原生动作名称决定实际执行哪个动作

[electron/automation/app-use-linux.js](/Users/syw/Documents/playground/Cyrene/electron/automation/app-use-linux.js:38)、[electron/automation/app-use-linux.js](/Users/syw/Documents/playground/Cyrene/electron/automation/app-use-linux.js:142)、[electron/automation/app-use-linux.js](/Users/syw/Documents/playground/Cyrene/electron/automation/app-use-linux.js:302)

将 AT-SPI 动作名按 click、select、toggle 等词映射，再选对应 descriptor.index 调用 DoAction。

这是系统兼容层，但匹配过宽或本地化动作名会错分。优先使用平台动作契约和显式映射，未知动作不得猜成另一种动作。

### 10 RDP 按窗口标题回退选择捕获源

[electron/remote-desktop/remote-desktop-rdp-sidecar.js](/Users/syw/Documents/playground/Cyrene/electron/remote-desktop/remote-desktop-rdp-sidecar.js:352)

按窗口 ID 找不到捕获源后，取名称匹配 freerdp 或 remote desktop 的第一个窗口。

有多个远程窗口时可能选错捕获对象。应绑定当前会话的窗口与进程身份，失败时明确报错。

### 11 终端名称从用户句子中猜取

[src/cyrene/plugins/builtin/cyrene_code/services.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/plugins/builtin/cyrene_code/services.py:14)、[src/cyrene/plugins/builtin/cyrene_code/services.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/plugins/builtin/cyrene_code/services.py:38)、[src/cyrene/plugins/builtin/cyrene_code/start_shell.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/plugins/builtin/cyrene_code/start_shell.py:55)

没有显式 title 时，匹配“名为”“叫作”或 terminal named 等表达，作为新终端标题。

只补全已调用的建终端操作，不会仅凭句子创建终端；仍会误取否定句或无关名称。让调用方显式提供 title。

### 12 通用对话组标题触发重新生成

[src/cyrene/workbench/chat/chat_service.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/workbench/chat/chat_service.py:544)、[src/cyrene/workbench/chat/chat_service.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/workbench/chat/chat_service.py:595)

去标点后匹配 Chat group、新对话组等黑名单，将标题清空并触发最多两次元数据生成；仍失败则选已有标题。

这是文案质量规则，确实会增加模型调用。应与标题有效性字段分开，保留人工锁定标题规则。

### 13 通用模型错误分类与重试

[src/cyrene/model/error_details.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/model/error_details.py:211)、[src/cyrene/model/error_details.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/model/error_details.py:267)

从错误字符串提取 HTTP 状态和 quota、authentication、context window、timeout 等词，决定错误码、retryable 与 retry_scope。

已有结构化信息应始终优先；文本仅作明确标记的兼容回退。会影响停止、延迟重试或调整参数后重试。

### 14 Codex 可用性由错误文字推断

[src/cyrene/model/codex_provider.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/model/codex_provider.py:140)

quota、unsupported model、token expired 等文字可转成额度耗尽、模型不可用、登录过期错误。

和通用错误分类同类，应优先使用 codexErrorInfo 与 HTTP 状态。

### 15 Codex 协议错误文字触发 CLI 版本回退

[src/cyrene/model/codex_provider.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/model/codex_provider.py:407)、[src/cyrene/model/codex_provider.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/model/codex_provider.py:415)

匹配版本冲突词，排除 TLS 等词后，下载 SDK 固定版本 CLI；已安装固定版本则不做。

实际会下载和切换运行时，优先级高于一般错误提示。应通过明确的协议版本能力或错误类型触发。

### 16 媒体生成错误文字决定自动重试

[src/cyrene/plugins/builtin/cyrene_media/providers/minimax.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/plugins/builtin/cyrene_media/providers/minimax.py:68)、[src/cyrene/plugins/builtin/cyrene_media/providers/google_media.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/plugins/builtin/cyrene_media/providers/google_media.py:40)、[src/cyrene/plugins/builtin/cyrene_media/providers/comfyui.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/plugins/builtin/cyrene_media/providers/comfyui.py:119)、[src/cyrene/plugins/builtin/cyrene_media/manager.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/plugins/builtin/cyrene_media/manager.py:525)

MiniMax、Google、ComfyUI 按 busy、rate、unavailable 等文本判断 retryable，作业管理器在剩余次数内据此重试。

MiniMax 已有业务 status_code，应建立错误码表；Google 优先异常状态；ComfyUI 保留结构化 MCP 错误。文本判断可能重复昂贵请求。

### 17 SQLite 错误文字决定重试或兼容路径

[src/cyrene/workbench/persistence/schema.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/workbench/persistence/schema.py:240)、[src/cyrene/plugins/builtin/cyrene_remote/common.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/plugins/builtin/cyrene_remote/common.py:33)、[src/cyrene/workbench/application/inbox.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/workbench/application/inbox.py:1820)、[src/cyrene/workbench/application/inbox.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/workbench/application/inbox.py:1873)、[src/cyrene/workbench/control/control_ports.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/workbench/control/control_ports.py:193)、[src/cyrene/plugins/builtin/cyrene_schedule/migrations.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/plugins/builtin/cyrene_schedule/migrations.py:67)、[src/cyrene/plugins/builtin/cyrene_knowledge/store.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/plugins/builtin/cyrene_knowledge/store.py:609)

locked 决定 schema 重试；busy 决定远程错误类别；no such table、duplicate column name、fts5 决定忽略错误或跳过兼容分支。

技术兼容逻辑，不是用户措辞识别。可用 sqlite_errorcode、schema introspection 和功能探测替代可替代部分。

### 18 移动端串口错误文字决定继续等待

[mobile/runtime-app/src/main/kotlin/ai/cyrene/mobile/runtime/QemuVirtualMachine.kt](/Users/syw/Documents/playground/Cyrene/mobile/runtime-app/src/main/kotlin/ai/cyrene/mobile/runtime/QemuVirtualMachine.kt:261)

IOException 消息含 Try again 或 EAGAIN 时继续 readUntil 循环，否则抛出错误。

可能因平台文案改变而中断。优先用 errno 或明确的可重试异常类型；仍受超时限制。

### 19 导航错误文字决定等待替代导航

[electron/desktop/main-window-lifecycle.js](/Users/syw/Documents/playground/Cyrene/electron/desktop/main-window-lifecycle.js:32)、[electron/desktop/main-window-lifecycle.js](/Users/syw/Documents/playground/Cyrene/electron/desktop/main-window-lifecycle.js:101)

在 errno/code 不足时用 ERR_ABORTED (-3) 文本识别取消，转去等待替代导航。

已有代码优先结构化字段；字符串仅是兼容回退，可通过统一导航错误对象消除。

### 20 服务错误文字决定 HTTP 状态与错误类别

[src/cyrene/plugins/builtin/cyrene_cli/application.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/plugins/builtin/cyrene_cli/application.py:80)、[src/cyrene/plugins/builtin/cyrene_cli/application.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/plugins/builtin/cyrene_cli/application.py:129)、[src/cyrene/plugins/builtin/cyrene_schedule/workbench_service.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/plugins/builtin/cyrene_schedule/workbench_service.py:103)、[src/cyrene/plugins/builtin/cyrene_remote/pairing.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/plugins/builtin/cyrene_remote/pairing.py:585)、[src/cyrene/plugins/builtin/cyrene_remote/services.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/plugins/builtin/cyrene_remote/services.py:202)、[src/cyrene/core/plugin/core_impl/toolbox.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/core/plugin/core_impl/toolbox.py:417)

not found 决定 404/400；配对异常的英文整句决定错误码；Invalid arguments 或中文等价文案决定插件参数错误类型。

应用内部错误应使用专用异常类或 code，避免修改提示文案改变 API 行为。

### 21 模型目录与项目记忆错误兼容回退

[src/cyrene/plugins/model_catalog.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/plugins/model_catalog.py:401)、[src/cyrene/plugins/builtin/cyrene_model/routes.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/plugins/builtin/cyrene_model/routes.py:730)、[src/cyrene/plugins/builtin/cyrene_memory/project_memory.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/plugins/builtin/cyrene_memory/project_memory.py:1125)、[src/cyrene/plugins/builtin/cyrene_memory/project_memory.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/plugins/builtin/cyrene_memory/project_memory.py:1202)

精确英文错误文字控制模型配置缺失时的回退、无效目录识别、项目记忆模型不可用转换及上下文溢出分类。

主要是内部接口耦合；应传递稳定异常类型和错误码。

### 22 计划是否被拒绝由答案词表推断

[src/cyrene/workbench/core_adapter/bridge.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/workbench/core_adapter/bridge.py:334)

投影 pending plan 时，将词表中的“拒绝”“不同意”等标成 rejected，其余答案标成 active。

这是 active_plan 投影，不等同于实际权限授权。应传递结构化 decision，未知回答不能展示成同意。

### 23 进度标签由文字决定颜色

[src/cyrene/workbench/webui/frontend/features/chat/assistant-card-model.mjs](/Users/syw/Documents/playground/Cyrene/src/cyrene/workbench/webui/frontend/features/chat/assistant-card-model.mjs:59)、[src/cyrene/workbench/webui/frontend/features/chat/assistant-cards.jsx](/Users/syw/Documents/playground/Cyrene/src/cyrene/workbench/webui/frontend/features/chat/assistant-cards.jsx:61)

匹配失败、已完成等文案输出 tone。

已复现“未发现错误”显示 error；只改变样式，不结束任务。应使用状态枚举和独立 label。

### 24 天气描述决定默认图标

[src/cyrene/workbench/webui/frontend/features/chat/card-weather-model.mjs](/Users/syw/Documents/playground/Cyrene/src/cyrene/workbench/webui/frontend/features/chat/card-weather-model.mjs:14)

icon 不是有效枚举时，从 condition 的中英文天气词猜图标；有效 icon 优先。

只影响图标。天气条件应提供有效 icon，缺失可直接显示 unknown；按时间切换日夜本身不是文字匹配。

### 25 运行阶段与活动标签决定忙碌展示

[src/cyrene/workbench/webui/frontend/features/session/activity.jsx](/Users/syw/Documents/playground/Cyrene/src/cyrene/workbench/webui/frontend/features/session/activity.jsx:234)、[src/cyrene/workbench/webui/frontend/features/session/activity.jsx](/Users/syw/Documents/playground/Cyrene/src/cyrene/workbench/webui/frontend/features/session/activity.jsx:309)、[src/cyrene/workbench/webui/frontend/features/session/activity.jsx](/Users/syw/Documents/playground/Cyrene/src/cyrene/workbench/webui/frontend/features/session/activity.jsx:549)

plan 推断 planning；browser/web/click 推断浏览器活动；done/complete 等片段推断阶段是否结束。

改变 UI 活跃状态及活动内容。incomplete 也包含 complete。应使用阶段枚举；后端同名旧 helper 另列为遗留代码。

### 26 工具名称决定图标分类与失败提示

[src/cyrene/workbench/webui/frontend/features/chat/messages.jsx](/Users/syw/Documents/playground/Cyrene/src/cyrene/workbench/webui/frontend/features/chat/messages.jsx:865)、[src/cyrene/workbench/webui/frontend/features/chat/messages.jsx](/Users/syw/Documents/playground/Cyrene/src/cyrene/workbench/webui/frontend/features/chat/messages.jsx:878)、[src/cyrene/workbench/webui/frontend/features/chat/file-resources.jsx](/Users/syw/Documents/playground/Cyrene/src/cyrene/workbench/webui/frontend/features/chat/file-resources.jsx:186)、[src/cyrene/workbench/webui/frontend/workbench-memory.jsx](/Users/syw/Documents/playground/Cyrene/src/cyrene/workbench/webui/frontend/workbench-memory.jsx:575)

通过工具名关键词选类别和图标；tool failed: 前缀可给事件标失败。

显示层，不能作为执行结果来源。工具应声明 presentation kind，失败应使用事件字段。

### 27 权限按钮文案与位置决定显示标签

[src/cyrene/workbench/webui/frontend/features/chat/behavior.mjs](/Users/syw/Documents/playground/Cyrene/src/cyrene/workbench/webui/frontend/features/chat/behavior.mjs:51)

综合 optionId/id/kind/value/label/title 匹配 allow_once/session 等值，匹配不上时还按按钮数量和位置推断。

只改变显示标签，但可能让用户看到的授权范围与提交的原值不一致。应优先明确 optionId，未知选项原样显示。

### 28 外部 Agent 警告文本被移出正文

[src/cyrene/agents/notices.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/agents/notices.py:17)、[src/cyrene/agents/notices.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/agents/notices.py:60)、[src/cyrene/workbench/chat/chat_external_turn_service.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/workbench/chat/chat_external_turn_service.py:123)

识别开头 warning:/notice: 和网络术语，分离为运行通知，不再当作普通回答正文。

当前 ACP 兼容处理；不会按文字重试。普通回答若讨论相同句式可能被误归类，应通过事件通道携带通知。

### 29 浏览器正文推断访问限制信号

[src/cyrene/plugins/builtin/cyrene_browser/runtime.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/plugins/builtin/cyrene_browser/runtime.py:374)、[electron/main.js](/Users/syw/Documents/playground/Cyrene/electron/main.js:1015)

正文同时含“暂时无法访问”与“登录后查看”等词时返回 access_gate、retry_allowed、冷却时间等提示。

给助理提供恢复建议；这两个函数本身不执行重试。文章引用这些词也可能误判，应结合 DOM 和响应状态并标为启发式。

### 30 桌面控件文字推断风险等级

[src/cyrene/plugins/builtin/cyrene_desktop/_app_semantic_backend.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/plugins/builtin/cyrene_desktop/_app_semantic_backend.py:125)、[src/cyrene/plugins/builtin/cyrene_desktop/_app_semantic_backend.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/plugins/builtin/cyrene_desktop/_app_semantic_backend.py:191)

名称/描述/role 含 delete、pay、send 等英文片段则标 R3，否则点击类标 R2。

已复现“发送”R2 而 Send 为 R3。这里只返回风险元数据，不能说它单独放行执行。应结合声明的操作语义，并对未知风险保守处理。

### 31 系统提示词和内部任务按文案分组

[src/cyrene/workbench/chat/conversation_context_service.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/workbench/chat/conversation_context_service.py:1140)、[src/cyrene/workbench/webui/frontend/workbench-memory.jsx](/Users/syw/Documents/playground/Cyrene/src/cyrene/workbench/webui/frontend/workbench-memory.jsx:438)、[src/cyrene/workbench/webui/frontend/platform/ui-surface.jsx](/Users/syw/Documents/playground/Cyrene/src/cyrene/workbench/webui/frontend/platform/ui-surface.jsx:448)

按 You are、workspace、toolbox 等分上下文视图；按主动检查提示词识别记忆 UI 标题；按 action_id 前缀推断界面影响描述。

分组或描述层，不会把 prose 变成工具调用。应优先用已有来源、block kind 和 action outcome。

### 32 安装和平台错误文字决定诊断提示

[src/cyrene/plugins/builtin/cyrene_extensions/extension_service.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/plugins/builtin/cyrene_extensions/extension_service.py:251)、[src/cyrene/plugins/builtin/cyrene_extensions/extension_service.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/plugins/builtin/cyrene_extensions/extension_service.py:839)、[src/cyrene/workbench/webui/frontend/features/chat/errors.jsx](/Users/syw/Documents/playground/Cyrene/src/cyrene/workbench/webui/frontend/features/chat/errors.jsx:57)、[src/cyrene/workbench/webui/frontend/platform/api.jsx](/Users/syw/Documents/playground/Cyrene/src/cyrene/workbench/webui/frontend/platform/api.jsx:71)、[src/cyrene/workbench/webui/frontend/settings-model-configuration.jsx](/Users/syw/Documents/playground/Cyrene/src/cyrene/workbench/webui/frontend/settings-model-configuration.jsx:401)、[src/cyrene/plugins/builtin/cyrene_desktop/_app_use_backend.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/plugins/builtin/cyrene_desktop/_app_use_backend.py:316)、[electron/automation/app-use-macos.jxa](/Users/syw/Documents/playground/Cyrene/electron/automation/app-use-macos.jxa:582)、[electron/automation/app-use-windows.ps1](/Users/syw/Documents/playground/Cyrene/electron/automation/app-use-windows.ps1:677)、[electron/remote-desktop/remote-desktop-rdp-sidecar.js](/Users/syw/Documents/playground/Cyrene/electron/remote-desktop/remote-desktop-rdp-sidecar.js:66)

从错误文字生成原因类别、翻译或修复提示；安装任务的失败已由异常决定，不是由文案决定。

提示容易错类或漏翻译。应在源头携带原因码。与会触发重试的分支分开修复。

### 33 原生控件的通用文字参与语义树裁剪

[electron/automation/app-use-linux.js](/Users/syw/Documents/playground/Cyrene/electron/automation/app-use-linux.js:251)、[electron/automation/app-use-windows.ps1](/Users/syw/Documents/playground/Cyrene/electron/automation/app-use-windows.ps1:372)、[electron/automation/app-use.js](/Users/syw/Documents/playground/Cyrene/electron/automation/app-use.js:907)

group、pane、未知等 role/description 和控件名被用于透明容器判断、可操作节点统计与语义覆盖率判断。

影响助理看到的元素和覆盖率提示。平台结构信息优先，不能仅因通用文案隐藏有效目标。

### 34 模型名称决定兼容协议与请求约束

[src/cyrene/model/protocol_adapters.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/model/protocol_adapters.py:81)、[src/cyrene/model/protocol_adapters.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/model/protocol_adapters.py:751)、[src/cyrene/plugins/builtin/cyrene_media/model_catalog.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/plugins/builtin/cyrene_media/model_catalog.py:219)、[src/cyrene/plugins/builtin/cyrene_media/providers/registry.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/plugins/builtin/cyrene_media/providers/registry.py:148)、[src/cyrene/plugins/builtin/cyrene_media/providers/openai_image.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/plugins/builtin/cyrene_media/providers/openai_image.py:88)、[src/cyrene/plugins/builtin/cyrene_media/providers/google_media.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/plugins/builtin/cyrene_media/providers/google_media.py:171)、[src/cyrene/plugins/builtin/cyrene_media/providers/seedance.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/plugins/builtin/cyrene_media/providers/seedance.py:48)、[src/cyrene/plugins/builtin/cyrene_media/providers/minimax.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/plugins/builtin/cyrene_media/providers/minimax.py:478)

模型 ID 的前缀或子串用于协议、媒体能力、参数允许范围等兼容分支。

这是技术型号匹配，不是问候文案，但也有改名后失效的问题。能力和约束应来自模型目录元数据，保留明确版本兼容表。

### 35 模型名称决定上下文与计费估算

[src/cyrene/platform/config_store.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/platform/config_store.py:891)、[src/cyrene/workbench/projects/project_runtime.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/workbench/projects/project_runtime.py:116)、[src/cyrene/platform/attachments.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/platform/attachments.py:299)、[src/cyrene/model/image_tokens.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/model/image_tokens.py:35)、[src/cyrene/model/pricing.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/model/pricing.py:72)

通过 claude、deepseek、qwen 等名称猜上下文或视觉能力，通过模型别名匹配价格。

能力元数据不足时的回退；不应覆盖显式配置。模型名称变化可能影响压缩阈值、附件处理及估算。

### 36 记忆文本安全规则拒绝持久化

[src/cyrene/plugins/builtin/cyrene_memory/project_memory.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/plugins/builtin/cyrene_memory/project_memory.py:106)、[src/cyrene/plugins/builtin/cyrene_memory/project_memory.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/plugins/builtin/cyrene_memory/project_memory.py:890)、[src/cyrene/plugins/builtin/cyrene_memory/project_memory.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/plugins/builtin/cyrene_memory/project_memory.py:1029)

密钥格式、提示词注入语句和有限否定前缀决定是否拒绝模型生成的项目记忆。

有意的防护，不应直接删掉；不能把有限词表当成完整语义保证。应保留来源边界并用结构化学习结果减少歧义。

### 37 文本工具协议泄漏触发撤回与重试

[src/cyrene/plugins/builtin/cyrene_model/response_guard.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/plugins/builtin/cyrene_model/response_guard.py:25)、[src/cyrene/plugins/builtin/cyrene_model/response_guard.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/plugins/builtin/cyrene_model/response_guard.py:56)

回答正文出现 DSML 协议标记会抛模型错误、发 stream_cancelled，走有限重试路径。

这是协议完整性检查，不把文本提升为可执行工具。讨论协议标记的正常答案也可能命中；可按 provider/通道限定，而不是无条件删除检查。

### 38 远程能力名称与 Shell 文本触发额外边界

[src/cyrene/plugins/builtin/cyrene_remote/permission.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/plugins/builtin/cyrene_remote/permission.py:190)、[src/cyrene/plugins/builtin/cyrene_remote/commands.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/plugins/builtin/cyrene_remote/commands.py:2208)、[src/cyrene/core/plugin/core_impl/permission_boundaries.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/core/plugin/core_impl/permission_boundaries.py:184)、[src/cyrene/plugins/native_runtime.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/plugins/native_runtime.py:256)

删除类能力名触发破坏性确认；长 Shell 输入带 base64 解码词被要求改走文件通道；命令结构匹配用于工作区与破坏性操作检查。

确实决定允许/拒绝，但属于命令与能力安全检查。应使用声明的 resource effects 和可靠命令分析，不能因为它们使用匹配就整体移除。

### 39 字段名与凭据格式决定脱敏

[src/cyrene/platform/secret_redaction.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/platform/secret_redaction.py:13)、[src/cyrene/model/errors.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/model/errors.py:13)、[src/cyrene/agents/acp_events.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/agents/acp_events.py:51)、[src/cyrene/core/observability.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/core/observability.py:73)、[src/cyrene/workbench/application/app_control.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/workbench/application/app_control.py:273)、[src/cyrene/plugins/builtin/cyrene_extensions/extension_service.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/plugins/builtin/cyrene_extensions/extension_service.py:230)、[src/cyrene/plugins/builtin/cyrene_extensions/extension_agent_runtime.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/plugins/builtin/cyrene_extensions/extension_agent_runtime.py:71)、[src/cyrene/plugins/builtin/cyrene_skills/capture.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/plugins/builtin/cyrene_skills/capture.py:230)、[src/cyrene/platform/doctor/evidence.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/platform/doctor/evidence.py:7)

secret/token/password 等键名及密钥、用户路径格式触发内容替换或不记录。

有意的隐私保护。会误遮普通字段，也可能漏掉非标准密钥；优先采用字段级敏感声明，文本扫描保留为补充。

### 40 技能参数正则提取的遗留入口

[src/cyrene/plugins/builtin/cyrene_skills/lifecycle.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/plugins/builtin/cyrene_skills/lifecycle.py:510)、[src/cyrene/plugins/builtin/cyrene_skills/lifecycle.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/plugins/builtin/cyrene_skills/lifecycle.py:549)、[src/cyrene/plugins/builtin/cyrene_skills/orchestrator.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/plugins/builtin/cyrene_skills/orchestrator.py:1103)

取第一个路径、数字、日期、URL 或示例匹配作为参数；仅缺少必填项才调用 LLM。

仓库生产调用搜索只找到定义和包装，没有实际调用入口。属于潜在问题，不列为当前自动执行链；若恢复使用，应改为显式参数绑定。

### 41 按失败文字生成技能修补类型的遗留入口

[src/cyrene/plugins/builtin/cyrene_skills/lifecycle.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/plugins/builtin/cyrene_skills/lifecycle.py:338)、[src/cyrene/plugins/builtin/cyrene_skills/orchestrator.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/plugins/builtin/cyrene_skills/orchestrator.py:1047)

missing、parameter、参数决定提议更新输入 schema，否则替换步骤。

只找到定义和包装，未找到生产调用者；不要把它描述成当前自动修补行为。即使启用也只是提出补丁，不直接执行补丁。

### 42 旧后端活动推断 helper

[src/cyrene/workbench/sessions/session_view.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/workbench/sessions/session_view.py:75)

phase_transition.to 含 done/complete 等片段则认为阶段不活跃。

全仓库引用搜索只找到定义。前端同类逻辑仍活跃，已单列；该后端 helper 可考虑删除。

### 43 首页原型直接按用户文字触发演示动作

[prototypes/assistant-home/src/App.jsx](/Users/syw/Documents/playground/Cyrene/prototypes/assistant-home/src/App.jsx:35)

旅行关键词切换 travel；创建任务关键词新增模拟任务；日程、进度、总结等词选择固定回复与文档。

仅 prototypes 演示代码，不是正式 Cyrene 首页实现。若未来复用，必须移除关键词动作分派。

### 44 原型服务按句子选择流程与卡片

[prototypes/assistant-home/server/demo-engine.mjs](/Users/syw/Documents/playground/Cyrene/prototypes/assistant-home/server/demo-engine.mjs:26)、[prototypes/assistant-home/server/demo-engine.mjs](/Users/syw/Documents/playground/Cyrene/prototypes/assistant-home/server/demo-engine.mjs:41)、[prototypes/assistant-home/server/resident-cards.mjs](/Users/syw/Documents/playground/Cyrene/prototypes/assistant-home/server/resident-cards.mjs:18)

按预算、旅行天数、重选资料、风险等词设置流程状态、内容、地图和推荐卡片。

独立演示服务的规则引擎，属于用户所说的同类机制；不应混入生产逻辑。

### 45 显式命令与输入语法

[src/cyrene/workbench/application/commands.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/workbench/application/commands.py:68)、[src/cyrene/workbench/webui/frontend/features/chat/composer-submission.jsx](/Users/syw/Documents/playground/Cyrene/src/cyrene/workbench/webui/frontend/features/chat/composer-submission.jsx:12)、[src/cyrene/cli_chat.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/cli_chat.py:2756)、[src/cyrene/workbench/http/settings/profile_data.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/workbench/http/settings/profile_data.py:84)

解析 /命令、参数、布尔输入和 RESET CYRENE DATA 确认短语。

用户明确使用的命令/确认协议，不是从自由文案猜意图。保留，仍需正常权限校验。

### 46 显式文字定位与等待条件

[src/cyrene/plugins/builtin/cyrene_browser/runtime.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/plugins/builtin/cyrene_browser/runtime.py:2588)、[electron/browser/browser-target.js](/Users/syw/Documents/playground/Cyrene/electron/browser/browser-target.js:31)、[electron/automation/app-use.js](/Users/syw/Documents/playground/Cyrene/electron/automation/app-use.js:1417)、[mobile/app/src/main/assets/browser/page-commands.js](/Users/syw/Documents/playground/Cyrene/mobile/app/src/main/assets/browser/page-commands.js:26)、[src/cyrene/plugins/builtin/cyrene_code/workspace_execution.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/plugins/builtin/cyrene_code/workspace_execution.py:555)

调用方明确给定 text/contains/readyPattern 时匹配目标或标记服务 ready。

这是工具定义的查询与等待功能。保留；多个命中、否定匹配和目标身份应由工具契约处理。

### 47 Markdown 和持久化文本格式协议

[src/cyrene/workbench/chat/chat_application.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/workbench/chat/chat_application.py:1495)、[src/cyrene/workbench/webui/frontend/shared/markdown/renderer.jsx](/Users/syw/Documents/playground/Cyrene/src/cyrene/workbench/webui/frontend/shared/markdown/renderer.jsx:170)、[src/cyrene/plugins/builtin/cyrene_soul/store.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/plugins/builtin/cyrene_soul/store.py:168)、[src/cyrene/plugins/builtin/cyrene_memory/steward.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/plugins/builtin/cyrene_memory/steward.py:25)、[src/cyrene/plugins/builtin/cyrene_memory/archive_format.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/plugins/builtin/cyrene_memory/archive_format.py:10)

解析按钮指令块、SOUL 编辑命令、ENTITY 和历史归档格式。

属于有明确定义的语法，不能等同于看到“删除”就删除。若重构，采用结构化数据迁移，而非只删正则。

### 48 语音标点静音与朗读分段

[src/cyrene/plugins/builtin/cyrene_voice/engine.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/plugins/builtin/cyrene_voice/engine.py:504)、[src/cyrene/workbench/webui/frontend/features/chat/voice-input.jsx](/Users/syw/Documents/playground/Cyrene/src/cyrene/workbench/webui/frontend/features/chat/voice-input.jsx:61)、[src/cyrene/workbench/webui/frontend/features/chat/voice-playback.jsx](/Users/syw/Documents/playground/Cyrene/src/cyrene/workbench/webui/frontend/features/chat/voice-playback.jsx:147)、[src/cyrene/workbench/webui/frontend/features/chat/voice-playback.jsx](/Users/syw/Documents/playground/Cyrene/src/cyrene/workbench/webui/frontend/features/chat/voice-playback.jsx:283)

过滤静音标记/无效字符、判断句子与 Markdown 边界后分段朗读。

文本处理本身就是功能；并不据自由文字启动任务。保留，需针对语言和分段误差验证。

### 49 技术格式与搜索匹配

[src/cyrene/model/tool_names.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/model/tool_names.py:11)、[src/cyrene/plugins/builtin/cyrene_knowledge/store.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/plugins/builtin/cyrene_knowledge/store.py:312)、[src/cyrene/workbench/webui/frontend/features/chat/context-graph-model.mjs](/Users/syw/Documents/playground/Cyrene/src/cyrene/workbench/webui/frontend/features/chat/context-graph-model.mjs:53)、[electron/backend/backend-process.js](/Users/syw/Documents/playground/Cyrene/electron/backend/backend-process.js:24)

ID、路径、MIME、URL、版本号、HTTP/SSE/OSC、PORT 输出、关键字搜索与测试输出断言。

正常协议/格式处理。扫描中的同类命中归入此类，不列为业务状态误判。构建脚本、测试与历史实验的匹配也按用途归类。

## 模型名称与安全内容规则

### 1 权限选项文字决定授权范围

[src/cyrene/core/plugin/permission_grants.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/core/plugin/permission_grants.py:86)

只验证答案属于提供的 label，再用拒绝词表判断否定；含“本次会话”“始终”则保存会话授权，否则保存单次授权。

优先修复。隔离复现：如果提供的自定义选项是“不同意”，选择它仍会记录单次授权。默认“拒绝”选项不受这个例子影响。应使用 decision 与 scope 字段，未知值拒绝。

### 2 密码提示文字决定敏感终端输入是否放行

[src/cyrene/plugins/builtin/cyrene_code/send_shell.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/plugins/builtin/cyrene_code/send_shell.py:65)、[src/cyrene/plugins/builtin/cyrene_code/send_shell.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/plugins/builtin/cyrene_code/send_shell.py:85)

sensitive=true 跳过普通命令的工作区写入目标检查，再匹配屏幕末四行的 password、PIN、密码等词来决定能否输入。

优先修复。普通输出最后一行为 Password: 也通过判断；屏幕文字不能证明终端真的处于凭据输入状态。应绑定可信输入状态或明确授权，不把显示文字作为检查豁免依据。

### 3 自然语言被记录为上一轮纠错

[src/cyrene/plugins/builtin/cyrene_skills/orchestrator.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/plugins/builtin/cyrene_skills/orchestrator.py:208)、[src/cyrene/plugins/builtin/cyrene_skills/lifecycle.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/plugins/builtin/cyrene_skills/lifecycle.py:36)、[src/cyrene/plugins/builtin/cyrene_skills/capture.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/plugins/builtin/cyrene_skills/capture.py:490)

匹配“错”“重新”“fix”“retry”等片段后，将上一轮 behavior_turns 写成 correction。

已复现“做得不错”也得到 correction。应使用明确反馈事件或语义判断，不能按单字决定负面学习证据。

### 4 工具输出文字决定失败状态

[src/cyrene/workbench/application/inbox.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/workbench/application/inbox.py:213)、[src/cyrene/workbench/application/inbox.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/workbench/application/inbox.py:972)、[src/cyrene/plugins/builtin/cyrene_memory/structured.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/plugins/builtin/cyrene_memory/structured.py:966)、[src/cyrene/plugins/builtin/cyrene_memory/structured.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/plugins/builtin/cyrene_memory/structured.py:954)

非结构化工具结果以 error、failed 等英文开头则算失败；同时影响收件箱工具事件及能否作为 verified result 供记忆提取。

已复现“错误：文件不存在”未被识别为失败，而 Error count: 0 被识别为失败。统一使用工具执行结果的 ok、failure、error_code；无法判定的文本不能标为 verified。

### 5 内部学习任务由提示词前缀识别

[src/cyrene/plugins/builtin/cyrene_skills/orchestrator.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/plugins/builtin/cyrene_skills/orchestrator.py:938)、[src/cyrene/plugins/builtin/cyrene_skills/orchestrator.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/plugins/builtin/cyrene_skills/orchestrator.py:1310)

除了 system_initiated 字段，还按固定内部提示词前缀排除学习证据，并重写旧 behavior_turns 的内容和元数据。

属于历史兼容，但普通用户文字若恰好匹配也会受影响。迁移应按记录版本和来源一次性处理，运行时只看来源字段。

### 6 短期记忆情绪分数由英文词片段决定

[src/cyrene/plugins/builtin/cyrene_memory/short_term.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/plugins/builtin/cyrene_memory/short_term.py:313)

frustrat、stress、angry 赋 -2；happy、love、excit 赋 +2，然后持久化 emotional_valence。

不理解否定和中文。not happy 按规则会得到正值。应由结构化提取结果给出情绪，或不保存推测分数。

### 7 Doctor 依据建议文字拒收诊断

[src/cyrene/platform/doctor/agent_analysis.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/platform/doctor/agent_analysis.py:74)

next_steps 出现“写入测试”或 write test 时，返回 accepted=false，要求模型重交诊断。

会连带拦住 write tests 这类正常测试建议，也可因同义表达漏判。应验证结构化修复动作和能力，而不是建议措辞。

### 8 ComfyUI 文本提取任务编号和下载地址

[src/cyrene/plugins/builtin/cyrene_media/providers/comfyui.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/plugins/builtin/cyrene_media/providers/comfyui.py:73)、[src/cyrene/plugins/builtin/cyrene_media/providers/comfyui.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/plugins/builtin/cyrene_media/providers/comfyui.py:107)、[src/cyrene/plugins/builtin/cyrene_media/providers/comfyui.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/plugins/builtin/cyrene_media/providers/comfyui.py:605)、[src/cyrene/plugins/builtin/cyrene_media/providers/comfyui.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/plugins/builtin/cyrene_media/providers/comfyui.py:690)

对结果中的 prompt/job/task id 作正则提取；没有目录或内嵌产物时，从输出文本枚举 URL 并下载为媒体产物。

会把说明或示例 URL 当成产物候选。应要求适配器返回明确的 job_id 和 artifacts；现有下载校验仍保留。

### 9 Linux 原生动作名称决定实际执行哪个动作

[electron/automation/app-use-linux.js](/Users/syw/Documents/playground/Cyrene/electron/automation/app-use-linux.js:38)、[electron/automation/app-use-linux.js](/Users/syw/Documents/playground/Cyrene/electron/automation/app-use-linux.js:142)、[electron/automation/app-use-linux.js](/Users/syw/Documents/playground/Cyrene/electron/automation/app-use-linux.js:302)

将 AT-SPI 动作名按 click、select、toggle 等词映射，再选对应 descriptor.index 调用 DoAction。

这是系统兼容层，但匹配过宽或本地化动作名会错分。优先使用平台动作契约和显式映射，未知动作不得猜成另一种动作。

### 10 RDP 按窗口标题回退选择捕获源

[electron/remote-desktop/remote-desktop-rdp-sidecar.js](/Users/syw/Documents/playground/Cyrene/electron/remote-desktop/remote-desktop-rdp-sidecar.js:352)

按窗口 ID 找不到捕获源后，取名称匹配 freerdp 或 remote desktop 的第一个窗口。

有多个远程窗口时可能选错捕获对象。应绑定当前会话的窗口与进程身份，失败时明确报错。

### 11 终端名称从用户句子中猜取

[src/cyrene/plugins/builtin/cyrene_code/services.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/plugins/builtin/cyrene_code/services.py:14)、[src/cyrene/plugins/builtin/cyrene_code/services.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/plugins/builtin/cyrene_code/services.py:38)、[src/cyrene/plugins/builtin/cyrene_code/start_shell.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/plugins/builtin/cyrene_code/start_shell.py:55)

没有显式 title 时，匹配“名为”“叫作”或 terminal named 等表达，作为新终端标题。

只补全已调用的建终端操作，不会仅凭句子创建终端；仍会误取否定句或无关名称。让调用方显式提供 title。

### 12 通用对话组标题触发重新生成

[src/cyrene/workbench/chat/chat_service.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/workbench/chat/chat_service.py:544)、[src/cyrene/workbench/chat/chat_service.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/workbench/chat/chat_service.py:595)

去标点后匹配 Chat group、新对话组等黑名单，将标题清空并触发最多两次元数据生成；仍失败则选已有标题。

这是文案质量规则，确实会增加模型调用。应与标题有效性字段分开，保留人工锁定标题规则。

### 13 通用模型错误分类与重试

[src/cyrene/model/error_details.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/model/error_details.py:211)、[src/cyrene/model/error_details.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/model/error_details.py:267)

从错误字符串提取 HTTP 状态和 quota、authentication、context window、timeout 等词，决定错误码、retryable 与 retry_scope。

已有结构化信息应始终优先；文本仅作明确标记的兼容回退。会影响停止、延迟重试或调整参数后重试。

### 14 Codex 可用性由错误文字推断

[src/cyrene/model/codex_provider.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/model/codex_provider.py:140)

quota、unsupported model、token expired 等文字可转成额度耗尽、模型不可用、登录过期错误。

和通用错误分类同类，应优先使用 codexErrorInfo 与 HTTP 状态。

### 15 Codex 协议错误文字触发 CLI 版本回退

[src/cyrene/model/codex_provider.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/model/codex_provider.py:407)、[src/cyrene/model/codex_provider.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/model/codex_provider.py:415)

匹配版本冲突词，排除 TLS 等词后，下载 SDK 固定版本 CLI；已安装固定版本则不做。

实际会下载和切换运行时，优先级高于一般错误提示。应通过明确的协议版本能力或错误类型触发。

### 16 媒体生成错误文字决定自动重试

[src/cyrene/plugins/builtin/cyrene_media/providers/minimax.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/plugins/builtin/cyrene_media/providers/minimax.py:68)、[src/cyrene/plugins/builtin/cyrene_media/providers/google_media.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/plugins/builtin/cyrene_media/providers/google_media.py:40)、[src/cyrene/plugins/builtin/cyrene_media/providers/comfyui.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/plugins/builtin/cyrene_media/providers/comfyui.py:119)、[src/cyrene/plugins/builtin/cyrene_media/manager.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/plugins/builtin/cyrene_media/manager.py:525)

MiniMax、Google、ComfyUI 按 busy、rate、unavailable 等文本判断 retryable，作业管理器在剩余次数内据此重试。

MiniMax 已有业务 status_code，应建立错误码表；Google 优先异常状态；ComfyUI 保留结构化 MCP 错误。文本判断可能重复昂贵请求。

### 17 SQLite 错误文字决定重试或兼容路径

[src/cyrene/workbench/persistence/schema.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/workbench/persistence/schema.py:240)、[src/cyrene/plugins/builtin/cyrene_remote/common.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/plugins/builtin/cyrene_remote/common.py:33)、[src/cyrene/workbench/application/inbox.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/workbench/application/inbox.py:1820)、[src/cyrene/workbench/application/inbox.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/workbench/application/inbox.py:1873)、[src/cyrene/workbench/control/control_ports.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/workbench/control/control_ports.py:193)、[src/cyrene/plugins/builtin/cyrene_schedule/migrations.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/plugins/builtin/cyrene_schedule/migrations.py:67)、[src/cyrene/plugins/builtin/cyrene_knowledge/store.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/plugins/builtin/cyrene_knowledge/store.py:609)

locked 决定 schema 重试；busy 决定远程错误类别；no such table、duplicate column name、fts5 决定忽略错误或跳过兼容分支。

技术兼容逻辑，不是用户措辞识别。可用 sqlite_errorcode、schema introspection 和功能探测替代可替代部分。

### 18 移动端串口错误文字决定继续等待

[mobile/runtime-app/src/main/kotlin/ai/cyrene/mobile/runtime/QemuVirtualMachine.kt](/Users/syw/Documents/playground/Cyrene/mobile/runtime-app/src/main/kotlin/ai/cyrene/mobile/runtime/QemuVirtualMachine.kt:261)

IOException 消息含 Try again 或 EAGAIN 时继续 readUntil 循环，否则抛出错误。

可能因平台文案改变而中断。优先用 errno 或明确的可重试异常类型；仍受超时限制。

### 19 导航错误文字决定等待替代导航

[electron/desktop/main-window-lifecycle.js](/Users/syw/Documents/playground/Cyrene/electron/desktop/main-window-lifecycle.js:32)、[electron/desktop/main-window-lifecycle.js](/Users/syw/Documents/playground/Cyrene/electron/desktop/main-window-lifecycle.js:101)

在 errno/code 不足时用 ERR_ABORTED (-3) 文本识别取消，转去等待替代导航。

已有代码优先结构化字段；字符串仅是兼容回退，可通过统一导航错误对象消除。

### 20 服务错误文字决定 HTTP 状态与错误类别

[src/cyrene/plugins/builtin/cyrene_cli/application.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/plugins/builtin/cyrene_cli/application.py:80)、[src/cyrene/plugins/builtin/cyrene_cli/application.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/plugins/builtin/cyrene_cli/application.py:129)、[src/cyrene/plugins/builtin/cyrene_schedule/workbench_service.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/plugins/builtin/cyrene_schedule/workbench_service.py:103)、[src/cyrene/plugins/builtin/cyrene_remote/pairing.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/plugins/builtin/cyrene_remote/pairing.py:585)、[src/cyrene/plugins/builtin/cyrene_remote/services.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/plugins/builtin/cyrene_remote/services.py:202)、[src/cyrene/core/plugin/core_impl/toolbox.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/core/plugin/core_impl/toolbox.py:417)

not found 决定 404/400；配对异常的英文整句决定错误码；Invalid arguments 或中文等价文案决定插件参数错误类型。

应用内部错误应使用专用异常类或 code，避免修改提示文案改变 API 行为。

### 21 模型目录与项目记忆错误兼容回退

[src/cyrene/plugins/model_catalog.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/plugins/model_catalog.py:401)、[src/cyrene/plugins/builtin/cyrene_model/routes.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/plugins/builtin/cyrene_model/routes.py:730)、[src/cyrene/plugins/builtin/cyrene_memory/project_memory.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/plugins/builtin/cyrene_memory/project_memory.py:1125)、[src/cyrene/plugins/builtin/cyrene_memory/project_memory.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/plugins/builtin/cyrene_memory/project_memory.py:1202)

精确英文错误文字控制模型配置缺失时的回退、无效目录识别、项目记忆模型不可用转换及上下文溢出分类。

主要是内部接口耦合；应传递稳定异常类型和错误码。

### 22 计划是否被拒绝由答案词表推断

[src/cyrene/workbench/core_adapter/bridge.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/workbench/core_adapter/bridge.py:334)

投影 pending plan 时，将词表中的“拒绝”“不同意”等标成 rejected，其余答案标成 active。

这是 active_plan 投影，不等同于实际权限授权。应传递结构化 decision，未知回答不能展示成同意。

### 23 进度标签由文字决定颜色

[src/cyrene/workbench/webui/frontend/features/chat/assistant-card-model.mjs](/Users/syw/Documents/playground/Cyrene/src/cyrene/workbench/webui/frontend/features/chat/assistant-card-model.mjs:59)、[src/cyrene/workbench/webui/frontend/features/chat/assistant-cards.jsx](/Users/syw/Documents/playground/Cyrene/src/cyrene/workbench/webui/frontend/features/chat/assistant-cards.jsx:61)

匹配失败、已完成等文案输出 tone。

已复现“未发现错误”显示 error；只改变样式，不结束任务。应使用状态枚举和独立 label。

### 24 天气描述决定默认图标

[src/cyrene/workbench/webui/frontend/features/chat/card-weather-model.mjs](/Users/syw/Documents/playground/Cyrene/src/cyrene/workbench/webui/frontend/features/chat/card-weather-model.mjs:14)

icon 不是有效枚举时，从 condition 的中英文天气词猜图标；有效 icon 优先。

只影响图标。天气条件应提供有效 icon，缺失可直接显示 unknown；按时间切换日夜本身不是文字匹配。

### 25 运行阶段与活动标签决定忙碌展示

[src/cyrene/workbench/webui/frontend/features/session/activity.jsx](/Users/syw/Documents/playground/Cyrene/src/cyrene/workbench/webui/frontend/features/session/activity.jsx:234)、[src/cyrene/workbench/webui/frontend/features/session/activity.jsx](/Users/syw/Documents/playground/Cyrene/src/cyrene/workbench/webui/frontend/features/session/activity.jsx:309)、[src/cyrene/workbench/webui/frontend/features/session/activity.jsx](/Users/syw/Documents/playground/Cyrene/src/cyrene/workbench/webui/frontend/features/session/activity.jsx:549)

plan 推断 planning；browser/web/click 推断浏览器活动；done/complete 等片段推断阶段是否结束。

改变 UI 活跃状态及活动内容。incomplete 也包含 complete。应使用阶段枚举；后端同名旧 helper 另列为遗留代码。

### 26 工具名称决定图标分类与失败提示

[src/cyrene/workbench/webui/frontend/features/chat/messages.jsx](/Users/syw/Documents/playground/Cyrene/src/cyrene/workbench/webui/frontend/features/chat/messages.jsx:865)、[src/cyrene/workbench/webui/frontend/features/chat/messages.jsx](/Users/syw/Documents/playground/Cyrene/src/cyrene/workbench/webui/frontend/features/chat/messages.jsx:878)、[src/cyrene/workbench/webui/frontend/features/chat/file-resources.jsx](/Users/syw/Documents/playground/Cyrene/src/cyrene/workbench/webui/frontend/features/chat/file-resources.jsx:186)、[src/cyrene/workbench/webui/frontend/workbench-memory.jsx](/Users/syw/Documents/playground/Cyrene/src/cyrene/workbench/webui/frontend/workbench-memory.jsx:575)

通过工具名关键词选类别和图标；tool failed: 前缀可给事件标失败。

显示层，不能作为执行结果来源。工具应声明 presentation kind，失败应使用事件字段。

### 27 权限按钮文案与位置决定显示标签

[src/cyrene/workbench/webui/frontend/features/chat/behavior.mjs](/Users/syw/Documents/playground/Cyrene/src/cyrene/workbench/webui/frontend/features/chat/behavior.mjs:51)

综合 optionId/id/kind/value/label/title 匹配 allow_once/session 等值，匹配不上时还按按钮数量和位置推断。

只改变显示标签，但可能让用户看到的授权范围与提交的原值不一致。应优先明确 optionId，未知选项原样显示。

### 28 外部 Agent 警告文本被移出正文

[src/cyrene/agents/notices.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/agents/notices.py:17)、[src/cyrene/agents/notices.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/agents/notices.py:60)、[src/cyrene/workbench/chat/chat_external_turn_service.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/workbench/chat/chat_external_turn_service.py:123)

识别开头 warning:/notice: 和网络术语，分离为运行通知，不再当作普通回答正文。

当前 ACP 兼容处理；不会按文字重试。普通回答若讨论相同句式可能被误归类，应通过事件通道携带通知。

### 29 浏览器正文推断访问限制信号

[src/cyrene/plugins/builtin/cyrene_browser/runtime.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/plugins/builtin/cyrene_browser/runtime.py:374)、[electron/main.js](/Users/syw/Documents/playground/Cyrene/electron/main.js:1015)

正文同时含“暂时无法访问”与“登录后查看”等词时返回 access_gate、retry_allowed、冷却时间等提示。

给助理提供恢复建议；这两个函数本身不执行重试。文章引用这些词也可能误判，应结合 DOM 和响应状态并标为启发式。

### 30 桌面控件文字推断风险等级

[src/cyrene/plugins/builtin/cyrene_desktop/_app_semantic_backend.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/plugins/builtin/cyrene_desktop/_app_semantic_backend.py:125)、[src/cyrene/plugins/builtin/cyrene_desktop/_app_semantic_backend.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/plugins/builtin/cyrene_desktop/_app_semantic_backend.py:191)

名称/描述/role 含 delete、pay、send 等英文片段则标 R3，否则点击类标 R2。

已复现“发送”R2 而 Send 为 R3。这里只返回风险元数据，不能说它单独放行执行。应结合声明的操作语义，并对未知风险保守处理。

### 31 系统提示词和内部任务按文案分组

[src/cyrene/workbench/chat/conversation_context_service.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/workbench/chat/conversation_context_service.py:1140)、[src/cyrene/workbench/webui/frontend/workbench-memory.jsx](/Users/syw/Documents/playground/Cyrene/src/cyrene/workbench/webui/frontend/workbench-memory.jsx:438)、[src/cyrene/workbench/webui/frontend/platform/ui-surface.jsx](/Users/syw/Documents/playground/Cyrene/src/cyrene/workbench/webui/frontend/platform/ui-surface.jsx:448)

按 You are、workspace、toolbox 等分上下文视图；按主动检查提示词识别记忆 UI 标题；按 action_id 前缀推断界面影响描述。

分组或描述层，不会把 prose 变成工具调用。应优先用已有来源、block kind 和 action outcome。

### 32 安装和平台错误文字决定诊断提示

[src/cyrene/plugins/builtin/cyrene_extensions/extension_service.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/plugins/builtin/cyrene_extensions/extension_service.py:251)、[src/cyrene/plugins/builtin/cyrene_extensions/extension_service.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/plugins/builtin/cyrene_extensions/extension_service.py:839)、[src/cyrene/workbench/webui/frontend/features/chat/errors.jsx](/Users/syw/Documents/playground/Cyrene/src/cyrene/workbench/webui/frontend/features/chat/errors.jsx:57)、[src/cyrene/workbench/webui/frontend/platform/api.jsx](/Users/syw/Documents/playground/Cyrene/src/cyrene/workbench/webui/frontend/platform/api.jsx:71)、[src/cyrene/workbench/webui/frontend/settings-model-configuration.jsx](/Users/syw/Documents/playground/Cyrene/src/cyrene/workbench/webui/frontend/settings-model-configuration.jsx:401)、[src/cyrene/plugins/builtin/cyrene_desktop/_app_use_backend.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/plugins/builtin/cyrene_desktop/_app_use_backend.py:316)、[electron/automation/app-use-macos.jxa](/Users/syw/Documents/playground/Cyrene/electron/automation/app-use-macos.jxa:582)、[electron/automation/app-use-windows.ps1](/Users/syw/Documents/playground/Cyrene/electron/automation/app-use-windows.ps1:677)、[electron/remote-desktop/remote-desktop-rdp-sidecar.js](/Users/syw/Documents/playground/Cyrene/electron/remote-desktop/remote-desktop-rdp-sidecar.js:66)

从错误文字生成原因类别、翻译或修复提示；安装任务的失败已由异常决定，不是由文案决定。

提示容易错类或漏翻译。应在源头携带原因码。与会触发重试的分支分开修复。

### 33 原生控件的通用文字参与语义树裁剪

[electron/automation/app-use-linux.js](/Users/syw/Documents/playground/Cyrene/electron/automation/app-use-linux.js:251)、[electron/automation/app-use-windows.ps1](/Users/syw/Documents/playground/Cyrene/electron/automation/app-use-windows.ps1:372)、[electron/automation/app-use.js](/Users/syw/Documents/playground/Cyrene/electron/automation/app-use.js:907)

group、pane、未知等 role/description 和控件名被用于透明容器判断、可操作节点统计与语义覆盖率判断。

影响助理看到的元素和覆盖率提示。平台结构信息优先，不能仅因通用文案隐藏有效目标。

### 34 模型名称决定兼容协议与请求约束

[src/cyrene/model/protocol_adapters.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/model/protocol_adapters.py:81)、[src/cyrene/model/protocol_adapters.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/model/protocol_adapters.py:751)、[src/cyrene/plugins/builtin/cyrene_media/model_catalog.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/plugins/builtin/cyrene_media/model_catalog.py:219)、[src/cyrene/plugins/builtin/cyrene_media/providers/registry.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/plugins/builtin/cyrene_media/providers/registry.py:148)、[src/cyrene/plugins/builtin/cyrene_media/providers/openai_image.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/plugins/builtin/cyrene_media/providers/openai_image.py:88)、[src/cyrene/plugins/builtin/cyrene_media/providers/google_media.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/plugins/builtin/cyrene_media/providers/google_media.py:171)、[src/cyrene/plugins/builtin/cyrene_media/providers/seedance.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/plugins/builtin/cyrene_media/providers/seedance.py:48)、[src/cyrene/plugins/builtin/cyrene_media/providers/minimax.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/plugins/builtin/cyrene_media/providers/minimax.py:478)

模型 ID 的前缀或子串用于协议、媒体能力、参数允许范围等兼容分支。

这是技术型号匹配，不是问候文案，但也有改名后失效的问题。能力和约束应来自模型目录元数据，保留明确版本兼容表。

### 35 模型名称决定上下文与计费估算

[src/cyrene/platform/config_store.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/platform/config_store.py:891)、[src/cyrene/workbench/projects/project_runtime.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/workbench/projects/project_runtime.py:116)、[src/cyrene/platform/attachments.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/platform/attachments.py:299)、[src/cyrene/model/image_tokens.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/model/image_tokens.py:35)、[src/cyrene/model/pricing.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/model/pricing.py:72)

通过 claude、deepseek、qwen 等名称猜上下文或视觉能力，通过模型别名匹配价格。

能力元数据不足时的回退；不应覆盖显式配置。模型名称变化可能影响压缩阈值、附件处理及估算。

### 36 记忆文本安全规则拒绝持久化

[src/cyrene/plugins/builtin/cyrene_memory/project_memory.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/plugins/builtin/cyrene_memory/project_memory.py:106)、[src/cyrene/plugins/builtin/cyrene_memory/project_memory.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/plugins/builtin/cyrene_memory/project_memory.py:890)、[src/cyrene/plugins/builtin/cyrene_memory/project_memory.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/plugins/builtin/cyrene_memory/project_memory.py:1029)

密钥格式、提示词注入语句和有限否定前缀决定是否拒绝模型生成的项目记忆。

有意的防护，不应直接删掉；不能把有限词表当成完整语义保证。应保留来源边界并用结构化学习结果减少歧义。

### 37 文本工具协议泄漏触发撤回与重试

[src/cyrene/plugins/builtin/cyrene_model/response_guard.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/plugins/builtin/cyrene_model/response_guard.py:25)、[src/cyrene/plugins/builtin/cyrene_model/response_guard.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/plugins/builtin/cyrene_model/response_guard.py:56)

回答正文出现 DSML 协议标记会抛模型错误、发 stream_cancelled，走有限重试路径。

这是协议完整性检查，不把文本提升为可执行工具。讨论协议标记的正常答案也可能命中；可按 provider/通道限定，而不是无条件删除检查。

### 38 远程能力名称与 Shell 文本触发额外边界

[src/cyrene/plugins/builtin/cyrene_remote/permission.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/plugins/builtin/cyrene_remote/permission.py:190)、[src/cyrene/plugins/builtin/cyrene_remote/commands.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/plugins/builtin/cyrene_remote/commands.py:2208)、[src/cyrene/core/plugin/core_impl/permission_boundaries.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/core/plugin/core_impl/permission_boundaries.py:184)、[src/cyrene/plugins/native_runtime.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/plugins/native_runtime.py:256)

删除类能力名触发破坏性确认；长 Shell 输入带 base64 解码词被要求改走文件通道；命令结构匹配用于工作区与破坏性操作检查。

确实决定允许/拒绝，但属于命令与能力安全检查。应使用声明的 resource effects 和可靠命令分析，不能因为它们使用匹配就整体移除。

### 39 字段名与凭据格式决定脱敏

[src/cyrene/platform/secret_redaction.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/platform/secret_redaction.py:13)、[src/cyrene/model/errors.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/model/errors.py:13)、[src/cyrene/agents/acp_events.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/agents/acp_events.py:51)、[src/cyrene/core/observability.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/core/observability.py:73)、[src/cyrene/workbench/application/app_control.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/workbench/application/app_control.py:273)、[src/cyrene/plugins/builtin/cyrene_extensions/extension_service.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/plugins/builtin/cyrene_extensions/extension_service.py:230)、[src/cyrene/plugins/builtin/cyrene_extensions/extension_agent_runtime.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/plugins/builtin/cyrene_extensions/extension_agent_runtime.py:71)、[src/cyrene/plugins/builtin/cyrene_skills/capture.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/plugins/builtin/cyrene_skills/capture.py:230)、[src/cyrene/platform/doctor/evidence.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/platform/doctor/evidence.py:7)

secret/token/password 等键名及密钥、用户路径格式触发内容替换或不记录。

有意的隐私保护。会误遮普通字段，也可能漏掉非标准密钥；优先采用字段级敏感声明，文本扫描保留为补充。

### 40 技能参数正则提取的遗留入口

[src/cyrene/plugins/builtin/cyrene_skills/lifecycle.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/plugins/builtin/cyrene_skills/lifecycle.py:510)、[src/cyrene/plugins/builtin/cyrene_skills/lifecycle.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/plugins/builtin/cyrene_skills/lifecycle.py:549)、[src/cyrene/plugins/builtin/cyrene_skills/orchestrator.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/plugins/builtin/cyrene_skills/orchestrator.py:1103)

取第一个路径、数字、日期、URL 或示例匹配作为参数；仅缺少必填项才调用 LLM。

仓库生产调用搜索只找到定义和包装，没有实际调用入口。属于潜在问题，不列为当前自动执行链；若恢复使用，应改为显式参数绑定。

### 41 按失败文字生成技能修补类型的遗留入口

[src/cyrene/plugins/builtin/cyrene_skills/lifecycle.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/plugins/builtin/cyrene_skills/lifecycle.py:338)、[src/cyrene/plugins/builtin/cyrene_skills/orchestrator.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/plugins/builtin/cyrene_skills/orchestrator.py:1047)

missing、parameter、参数决定提议更新输入 schema，否则替换步骤。

只找到定义和包装，未找到生产调用者；不要把它描述成当前自动修补行为。即使启用也只是提出补丁，不直接执行补丁。

### 42 旧后端活动推断 helper

[src/cyrene/workbench/sessions/session_view.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/workbench/sessions/session_view.py:75)

phase_transition.to 含 done/complete 等片段则认为阶段不活跃。

全仓库引用搜索只找到定义。前端同类逻辑仍活跃，已单列；该后端 helper 可考虑删除。

### 43 首页原型直接按用户文字触发演示动作

[prototypes/assistant-home/src/App.jsx](/Users/syw/Documents/playground/Cyrene/prototypes/assistant-home/src/App.jsx:35)

旅行关键词切换 travel；创建任务关键词新增模拟任务；日程、进度、总结等词选择固定回复与文档。

仅 prototypes 演示代码，不是正式 Cyrene 首页实现。若未来复用，必须移除关键词动作分派。

### 44 原型服务按句子选择流程与卡片

[prototypes/assistant-home/server/demo-engine.mjs](/Users/syw/Documents/playground/Cyrene/prototypes/assistant-home/server/demo-engine.mjs:26)、[prototypes/assistant-home/server/demo-engine.mjs](/Users/syw/Documents/playground/Cyrene/prototypes/assistant-home/server/demo-engine.mjs:41)、[prototypes/assistant-home/server/resident-cards.mjs](/Users/syw/Documents/playground/Cyrene/prototypes/assistant-home/server/resident-cards.mjs:18)

按预算、旅行天数、重选资料、风险等词设置流程状态、内容、地图和推荐卡片。

独立演示服务的规则引擎，属于用户所说的同类机制；不应混入生产逻辑。

### 45 显式命令与输入语法

[src/cyrene/workbench/application/commands.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/workbench/application/commands.py:68)、[src/cyrene/workbench/webui/frontend/features/chat/composer-submission.jsx](/Users/syw/Documents/playground/Cyrene/src/cyrene/workbench/webui/frontend/features/chat/composer-submission.jsx:12)、[src/cyrene/cli_chat.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/cli_chat.py:2756)、[src/cyrene/workbench/http/settings/profile_data.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/workbench/http/settings/profile_data.py:84)

解析 /命令、参数、布尔输入和 RESET CYRENE DATA 确认短语。

用户明确使用的命令/确认协议，不是从自由文案猜意图。保留，仍需正常权限校验。

### 46 显式文字定位与等待条件

[src/cyrene/plugins/builtin/cyrene_browser/runtime.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/plugins/builtin/cyrene_browser/runtime.py:2588)、[electron/browser/browser-target.js](/Users/syw/Documents/playground/Cyrene/electron/browser/browser-target.js:31)、[electron/automation/app-use.js](/Users/syw/Documents/playground/Cyrene/electron/automation/app-use.js:1417)、[mobile/app/src/main/assets/browser/page-commands.js](/Users/syw/Documents/playground/Cyrene/mobile/app/src/main/assets/browser/page-commands.js:26)、[src/cyrene/plugins/builtin/cyrene_code/workspace_execution.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/plugins/builtin/cyrene_code/workspace_execution.py:555)

调用方明确给定 text/contains/readyPattern 时匹配目标或标记服务 ready。

这是工具定义的查询与等待功能。保留；多个命中、否定匹配和目标身份应由工具契约处理。

### 47 Markdown 和持久化文本格式协议

[src/cyrene/workbench/chat/chat_application.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/workbench/chat/chat_application.py:1495)、[src/cyrene/workbench/webui/frontend/shared/markdown/renderer.jsx](/Users/syw/Documents/playground/Cyrene/src/cyrene/workbench/webui/frontend/shared/markdown/renderer.jsx:170)、[src/cyrene/plugins/builtin/cyrene_soul/store.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/plugins/builtin/cyrene_soul/store.py:168)、[src/cyrene/plugins/builtin/cyrene_memory/steward.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/plugins/builtin/cyrene_memory/steward.py:25)、[src/cyrene/plugins/builtin/cyrene_memory/archive_format.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/plugins/builtin/cyrene_memory/archive_format.py:10)

解析按钮指令块、SOUL 编辑命令、ENTITY 和历史归档格式。

属于有明确定义的语法，不能等同于看到“删除”就删除。若重构，采用结构化数据迁移，而非只删正则。

### 48 语音标点静音与朗读分段

[src/cyrene/plugins/builtin/cyrene_voice/engine.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/plugins/builtin/cyrene_voice/engine.py:504)、[src/cyrene/workbench/webui/frontend/features/chat/voice-input.jsx](/Users/syw/Documents/playground/Cyrene/src/cyrene/workbench/webui/frontend/features/chat/voice-input.jsx:61)、[src/cyrene/workbench/webui/frontend/features/chat/voice-playback.jsx](/Users/syw/Documents/playground/Cyrene/src/cyrene/workbench/webui/frontend/features/chat/voice-playback.jsx:147)、[src/cyrene/workbench/webui/frontend/features/chat/voice-playback.jsx](/Users/syw/Documents/playground/Cyrene/src/cyrene/workbench/webui/frontend/features/chat/voice-playback.jsx:283)

过滤静音标记/无效字符、判断句子与 Markdown 边界后分段朗读。

文本处理本身就是功能；并不据自由文字启动任务。保留，需针对语言和分段误差验证。

### 49 技术格式与搜索匹配

[src/cyrene/model/tool_names.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/model/tool_names.py:11)、[src/cyrene/plugins/builtin/cyrene_knowledge/store.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/plugins/builtin/cyrene_knowledge/store.py:312)、[src/cyrene/workbench/webui/frontend/features/chat/context-graph-model.mjs](/Users/syw/Documents/playground/Cyrene/src/cyrene/workbench/webui/frontend/features/chat/context-graph-model.mjs:53)、[electron/backend/backend-process.js](/Users/syw/Documents/playground/Cyrene/electron/backend/backend-process.js:24)

ID、路径、MIME、URL、版本号、HTTP/SSE/OSC、PORT 输出、关键字搜索与测试输出断言。

正常协议/格式处理。扫描中的同类命中归入此类，不列为业务状态误判。构建脚本、测试与历史实验的匹配也按用途归类。

## 遗留函数与演示代码

### 1 权限选项文字决定授权范围

[src/cyrene/core/plugin/permission_grants.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/core/plugin/permission_grants.py:86)

只验证答案属于提供的 label，再用拒绝词表判断否定；含“本次会话”“始终”则保存会话授权，否则保存单次授权。

优先修复。隔离复现：如果提供的自定义选项是“不同意”，选择它仍会记录单次授权。默认“拒绝”选项不受这个例子影响。应使用 decision 与 scope 字段，未知值拒绝。

### 2 密码提示文字决定敏感终端输入是否放行

[src/cyrene/plugins/builtin/cyrene_code/send_shell.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/plugins/builtin/cyrene_code/send_shell.py:65)、[src/cyrene/plugins/builtin/cyrene_code/send_shell.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/plugins/builtin/cyrene_code/send_shell.py:85)

sensitive=true 跳过普通命令的工作区写入目标检查，再匹配屏幕末四行的 password、PIN、密码等词来决定能否输入。

优先修复。普通输出最后一行为 Password: 也通过判断；屏幕文字不能证明终端真的处于凭据输入状态。应绑定可信输入状态或明确授权，不把显示文字作为检查豁免依据。

### 3 自然语言被记录为上一轮纠错

[src/cyrene/plugins/builtin/cyrene_skills/orchestrator.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/plugins/builtin/cyrene_skills/orchestrator.py:208)、[src/cyrene/plugins/builtin/cyrene_skills/lifecycle.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/plugins/builtin/cyrene_skills/lifecycle.py:36)、[src/cyrene/plugins/builtin/cyrene_skills/capture.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/plugins/builtin/cyrene_skills/capture.py:490)

匹配“错”“重新”“fix”“retry”等片段后，将上一轮 behavior_turns 写成 correction。

已复现“做得不错”也得到 correction。应使用明确反馈事件或语义判断，不能按单字决定负面学习证据。

### 4 工具输出文字决定失败状态

[src/cyrene/workbench/application/inbox.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/workbench/application/inbox.py:213)、[src/cyrene/workbench/application/inbox.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/workbench/application/inbox.py:972)、[src/cyrene/plugins/builtin/cyrene_memory/structured.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/plugins/builtin/cyrene_memory/structured.py:966)、[src/cyrene/plugins/builtin/cyrene_memory/structured.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/plugins/builtin/cyrene_memory/structured.py:954)

非结构化工具结果以 error、failed 等英文开头则算失败；同时影响收件箱工具事件及能否作为 verified result 供记忆提取。

已复现“错误：文件不存在”未被识别为失败，而 Error count: 0 被识别为失败。统一使用工具执行结果的 ok、failure、error_code；无法判定的文本不能标为 verified。

### 5 内部学习任务由提示词前缀识别

[src/cyrene/plugins/builtin/cyrene_skills/orchestrator.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/plugins/builtin/cyrene_skills/orchestrator.py:938)、[src/cyrene/plugins/builtin/cyrene_skills/orchestrator.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/plugins/builtin/cyrene_skills/orchestrator.py:1310)

除了 system_initiated 字段，还按固定内部提示词前缀排除学习证据，并重写旧 behavior_turns 的内容和元数据。

属于历史兼容，但普通用户文字若恰好匹配也会受影响。迁移应按记录版本和来源一次性处理，运行时只看来源字段。

### 6 短期记忆情绪分数由英文词片段决定

[src/cyrene/plugins/builtin/cyrene_memory/short_term.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/plugins/builtin/cyrene_memory/short_term.py:313)

frustrat、stress、angry 赋 -2；happy、love、excit 赋 +2，然后持久化 emotional_valence。

不理解否定和中文。not happy 按规则会得到正值。应由结构化提取结果给出情绪，或不保存推测分数。

### 7 Doctor 依据建议文字拒收诊断

[src/cyrene/platform/doctor/agent_analysis.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/platform/doctor/agent_analysis.py:74)

next_steps 出现“写入测试”或 write test 时，返回 accepted=false，要求模型重交诊断。

会连带拦住 write tests 这类正常测试建议，也可因同义表达漏判。应验证结构化修复动作和能力，而不是建议措辞。

### 8 ComfyUI 文本提取任务编号和下载地址

[src/cyrene/plugins/builtin/cyrene_media/providers/comfyui.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/plugins/builtin/cyrene_media/providers/comfyui.py:73)、[src/cyrene/plugins/builtin/cyrene_media/providers/comfyui.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/plugins/builtin/cyrene_media/providers/comfyui.py:107)、[src/cyrene/plugins/builtin/cyrene_media/providers/comfyui.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/plugins/builtin/cyrene_media/providers/comfyui.py:605)、[src/cyrene/plugins/builtin/cyrene_media/providers/comfyui.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/plugins/builtin/cyrene_media/providers/comfyui.py:690)

对结果中的 prompt/job/task id 作正则提取；没有目录或内嵌产物时，从输出文本枚举 URL 并下载为媒体产物。

会把说明或示例 URL 当成产物候选。应要求适配器返回明确的 job_id 和 artifacts；现有下载校验仍保留。

### 9 Linux 原生动作名称决定实际执行哪个动作

[electron/automation/app-use-linux.js](/Users/syw/Documents/playground/Cyrene/electron/automation/app-use-linux.js:38)、[electron/automation/app-use-linux.js](/Users/syw/Documents/playground/Cyrene/electron/automation/app-use-linux.js:142)、[electron/automation/app-use-linux.js](/Users/syw/Documents/playground/Cyrene/electron/automation/app-use-linux.js:302)

将 AT-SPI 动作名按 click、select、toggle 等词映射，再选对应 descriptor.index 调用 DoAction。

这是系统兼容层，但匹配过宽或本地化动作名会错分。优先使用平台动作契约和显式映射，未知动作不得猜成另一种动作。

### 10 RDP 按窗口标题回退选择捕获源

[electron/remote-desktop/remote-desktop-rdp-sidecar.js](/Users/syw/Documents/playground/Cyrene/electron/remote-desktop/remote-desktop-rdp-sidecar.js:352)

按窗口 ID 找不到捕获源后，取名称匹配 freerdp 或 remote desktop 的第一个窗口。

有多个远程窗口时可能选错捕获对象。应绑定当前会话的窗口与进程身份，失败时明确报错。

### 11 终端名称从用户句子中猜取

[src/cyrene/plugins/builtin/cyrene_code/services.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/plugins/builtin/cyrene_code/services.py:14)、[src/cyrene/plugins/builtin/cyrene_code/services.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/plugins/builtin/cyrene_code/services.py:38)、[src/cyrene/plugins/builtin/cyrene_code/start_shell.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/plugins/builtin/cyrene_code/start_shell.py:55)

没有显式 title 时，匹配“名为”“叫作”或 terminal named 等表达，作为新终端标题。

只补全已调用的建终端操作，不会仅凭句子创建终端；仍会误取否定句或无关名称。让调用方显式提供 title。

### 12 通用对话组标题触发重新生成

[src/cyrene/workbench/chat/chat_service.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/workbench/chat/chat_service.py:544)、[src/cyrene/workbench/chat/chat_service.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/workbench/chat/chat_service.py:595)

去标点后匹配 Chat group、新对话组等黑名单，将标题清空并触发最多两次元数据生成；仍失败则选已有标题。

这是文案质量规则，确实会增加模型调用。应与标题有效性字段分开，保留人工锁定标题规则。

### 13 通用模型错误分类与重试

[src/cyrene/model/error_details.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/model/error_details.py:211)、[src/cyrene/model/error_details.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/model/error_details.py:267)

从错误字符串提取 HTTP 状态和 quota、authentication、context window、timeout 等词，决定错误码、retryable 与 retry_scope。

已有结构化信息应始终优先；文本仅作明确标记的兼容回退。会影响停止、延迟重试或调整参数后重试。

### 14 Codex 可用性由错误文字推断

[src/cyrene/model/codex_provider.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/model/codex_provider.py:140)

quota、unsupported model、token expired 等文字可转成额度耗尽、模型不可用、登录过期错误。

和通用错误分类同类，应优先使用 codexErrorInfo 与 HTTP 状态。

### 15 Codex 协议错误文字触发 CLI 版本回退

[src/cyrene/model/codex_provider.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/model/codex_provider.py:407)、[src/cyrene/model/codex_provider.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/model/codex_provider.py:415)

匹配版本冲突词，排除 TLS 等词后，下载 SDK 固定版本 CLI；已安装固定版本则不做。

实际会下载和切换运行时，优先级高于一般错误提示。应通过明确的协议版本能力或错误类型触发。

### 16 媒体生成错误文字决定自动重试

[src/cyrene/plugins/builtin/cyrene_media/providers/minimax.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/plugins/builtin/cyrene_media/providers/minimax.py:68)、[src/cyrene/plugins/builtin/cyrene_media/providers/google_media.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/plugins/builtin/cyrene_media/providers/google_media.py:40)、[src/cyrene/plugins/builtin/cyrene_media/providers/comfyui.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/plugins/builtin/cyrene_media/providers/comfyui.py:119)、[src/cyrene/plugins/builtin/cyrene_media/manager.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/plugins/builtin/cyrene_media/manager.py:525)

MiniMax、Google、ComfyUI 按 busy、rate、unavailable 等文本判断 retryable，作业管理器在剩余次数内据此重试。

MiniMax 已有业务 status_code，应建立错误码表；Google 优先异常状态；ComfyUI 保留结构化 MCP 错误。文本判断可能重复昂贵请求。

### 17 SQLite 错误文字决定重试或兼容路径

[src/cyrene/workbench/persistence/schema.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/workbench/persistence/schema.py:240)、[src/cyrene/plugins/builtin/cyrene_remote/common.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/plugins/builtin/cyrene_remote/common.py:33)、[src/cyrene/workbench/application/inbox.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/workbench/application/inbox.py:1820)、[src/cyrene/workbench/application/inbox.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/workbench/application/inbox.py:1873)、[src/cyrene/workbench/control/control_ports.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/workbench/control/control_ports.py:193)、[src/cyrene/plugins/builtin/cyrene_schedule/migrations.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/plugins/builtin/cyrene_schedule/migrations.py:67)、[src/cyrene/plugins/builtin/cyrene_knowledge/store.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/plugins/builtin/cyrene_knowledge/store.py:609)

locked 决定 schema 重试；busy 决定远程错误类别；no such table、duplicate column name、fts5 决定忽略错误或跳过兼容分支。

技术兼容逻辑，不是用户措辞识别。可用 sqlite_errorcode、schema introspection 和功能探测替代可替代部分。

### 18 移动端串口错误文字决定继续等待

[mobile/runtime-app/src/main/kotlin/ai/cyrene/mobile/runtime/QemuVirtualMachine.kt](/Users/syw/Documents/playground/Cyrene/mobile/runtime-app/src/main/kotlin/ai/cyrene/mobile/runtime/QemuVirtualMachine.kt:261)

IOException 消息含 Try again 或 EAGAIN 时继续 readUntil 循环，否则抛出错误。

可能因平台文案改变而中断。优先用 errno 或明确的可重试异常类型；仍受超时限制。

### 19 导航错误文字决定等待替代导航

[electron/desktop/main-window-lifecycle.js](/Users/syw/Documents/playground/Cyrene/electron/desktop/main-window-lifecycle.js:32)、[electron/desktop/main-window-lifecycle.js](/Users/syw/Documents/playground/Cyrene/electron/desktop/main-window-lifecycle.js:101)

在 errno/code 不足时用 ERR_ABORTED (-3) 文本识别取消，转去等待替代导航。

已有代码优先结构化字段；字符串仅是兼容回退，可通过统一导航错误对象消除。

### 20 服务错误文字决定 HTTP 状态与错误类别

[src/cyrene/plugins/builtin/cyrene_cli/application.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/plugins/builtin/cyrene_cli/application.py:80)、[src/cyrene/plugins/builtin/cyrene_cli/application.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/plugins/builtin/cyrene_cli/application.py:129)、[src/cyrene/plugins/builtin/cyrene_schedule/workbench_service.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/plugins/builtin/cyrene_schedule/workbench_service.py:103)、[src/cyrene/plugins/builtin/cyrene_remote/pairing.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/plugins/builtin/cyrene_remote/pairing.py:585)、[src/cyrene/plugins/builtin/cyrene_remote/services.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/plugins/builtin/cyrene_remote/services.py:202)、[src/cyrene/core/plugin/core_impl/toolbox.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/core/plugin/core_impl/toolbox.py:417)

not found 决定 404/400；配对异常的英文整句决定错误码；Invalid arguments 或中文等价文案决定插件参数错误类型。

应用内部错误应使用专用异常类或 code，避免修改提示文案改变 API 行为。

### 21 模型目录与项目记忆错误兼容回退

[src/cyrene/plugins/model_catalog.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/plugins/model_catalog.py:401)、[src/cyrene/plugins/builtin/cyrene_model/routes.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/plugins/builtin/cyrene_model/routes.py:730)、[src/cyrene/plugins/builtin/cyrene_memory/project_memory.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/plugins/builtin/cyrene_memory/project_memory.py:1125)、[src/cyrene/plugins/builtin/cyrene_memory/project_memory.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/plugins/builtin/cyrene_memory/project_memory.py:1202)

精确英文错误文字控制模型配置缺失时的回退、无效目录识别、项目记忆模型不可用转换及上下文溢出分类。

主要是内部接口耦合；应传递稳定异常类型和错误码。

### 22 计划是否被拒绝由答案词表推断

[src/cyrene/workbench/core_adapter/bridge.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/workbench/core_adapter/bridge.py:334)

投影 pending plan 时，将词表中的“拒绝”“不同意”等标成 rejected，其余答案标成 active。

这是 active_plan 投影，不等同于实际权限授权。应传递结构化 decision，未知回答不能展示成同意。

### 23 进度标签由文字决定颜色

[src/cyrene/workbench/webui/frontend/features/chat/assistant-card-model.mjs](/Users/syw/Documents/playground/Cyrene/src/cyrene/workbench/webui/frontend/features/chat/assistant-card-model.mjs:59)、[src/cyrene/workbench/webui/frontend/features/chat/assistant-cards.jsx](/Users/syw/Documents/playground/Cyrene/src/cyrene/workbench/webui/frontend/features/chat/assistant-cards.jsx:61)

匹配失败、已完成等文案输出 tone。

已复现“未发现错误”显示 error；只改变样式，不结束任务。应使用状态枚举和独立 label。

### 24 天气描述决定默认图标

[src/cyrene/workbench/webui/frontend/features/chat/card-weather-model.mjs](/Users/syw/Documents/playground/Cyrene/src/cyrene/workbench/webui/frontend/features/chat/card-weather-model.mjs:14)

icon 不是有效枚举时，从 condition 的中英文天气词猜图标；有效 icon 优先。

只影响图标。天气条件应提供有效 icon，缺失可直接显示 unknown；按时间切换日夜本身不是文字匹配。

### 25 运行阶段与活动标签决定忙碌展示

[src/cyrene/workbench/webui/frontend/features/session/activity.jsx](/Users/syw/Documents/playground/Cyrene/src/cyrene/workbench/webui/frontend/features/session/activity.jsx:234)、[src/cyrene/workbench/webui/frontend/features/session/activity.jsx](/Users/syw/Documents/playground/Cyrene/src/cyrene/workbench/webui/frontend/features/session/activity.jsx:309)、[src/cyrene/workbench/webui/frontend/features/session/activity.jsx](/Users/syw/Documents/playground/Cyrene/src/cyrene/workbench/webui/frontend/features/session/activity.jsx:549)

plan 推断 planning；browser/web/click 推断浏览器活动；done/complete 等片段推断阶段是否结束。

改变 UI 活跃状态及活动内容。incomplete 也包含 complete。应使用阶段枚举；后端同名旧 helper 另列为遗留代码。

### 26 工具名称决定图标分类与失败提示

[src/cyrene/workbench/webui/frontend/features/chat/messages.jsx](/Users/syw/Documents/playground/Cyrene/src/cyrene/workbench/webui/frontend/features/chat/messages.jsx:865)、[src/cyrene/workbench/webui/frontend/features/chat/messages.jsx](/Users/syw/Documents/playground/Cyrene/src/cyrene/workbench/webui/frontend/features/chat/messages.jsx:878)、[src/cyrene/workbench/webui/frontend/features/chat/file-resources.jsx](/Users/syw/Documents/playground/Cyrene/src/cyrene/workbench/webui/frontend/features/chat/file-resources.jsx:186)、[src/cyrene/workbench/webui/frontend/workbench-memory.jsx](/Users/syw/Documents/playground/Cyrene/src/cyrene/workbench/webui/frontend/workbench-memory.jsx:575)

通过工具名关键词选类别和图标；tool failed: 前缀可给事件标失败。

显示层，不能作为执行结果来源。工具应声明 presentation kind，失败应使用事件字段。

### 27 权限按钮文案与位置决定显示标签

[src/cyrene/workbench/webui/frontend/features/chat/behavior.mjs](/Users/syw/Documents/playground/Cyrene/src/cyrene/workbench/webui/frontend/features/chat/behavior.mjs:51)

综合 optionId/id/kind/value/label/title 匹配 allow_once/session 等值，匹配不上时还按按钮数量和位置推断。

只改变显示标签，但可能让用户看到的授权范围与提交的原值不一致。应优先明确 optionId，未知选项原样显示。

### 28 外部 Agent 警告文本被移出正文

[src/cyrene/agents/notices.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/agents/notices.py:17)、[src/cyrene/agents/notices.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/agents/notices.py:60)、[src/cyrene/workbench/chat/chat_external_turn_service.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/workbench/chat/chat_external_turn_service.py:123)

识别开头 warning:/notice: 和网络术语，分离为运行通知，不再当作普通回答正文。

当前 ACP 兼容处理；不会按文字重试。普通回答若讨论相同句式可能被误归类，应通过事件通道携带通知。

### 29 浏览器正文推断访问限制信号

[src/cyrene/plugins/builtin/cyrene_browser/runtime.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/plugins/builtin/cyrene_browser/runtime.py:374)、[electron/main.js](/Users/syw/Documents/playground/Cyrene/electron/main.js:1015)

正文同时含“暂时无法访问”与“登录后查看”等词时返回 access_gate、retry_allowed、冷却时间等提示。

给助理提供恢复建议；这两个函数本身不执行重试。文章引用这些词也可能误判，应结合 DOM 和响应状态并标为启发式。

### 30 桌面控件文字推断风险等级

[src/cyrene/plugins/builtin/cyrene_desktop/_app_semantic_backend.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/plugins/builtin/cyrene_desktop/_app_semantic_backend.py:125)、[src/cyrene/plugins/builtin/cyrene_desktop/_app_semantic_backend.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/plugins/builtin/cyrene_desktop/_app_semantic_backend.py:191)

名称/描述/role 含 delete、pay、send 等英文片段则标 R3，否则点击类标 R2。

已复现“发送”R2 而 Send 为 R3。这里只返回风险元数据，不能说它单独放行执行。应结合声明的操作语义，并对未知风险保守处理。

### 31 系统提示词和内部任务按文案分组

[src/cyrene/workbench/chat/conversation_context_service.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/workbench/chat/conversation_context_service.py:1140)、[src/cyrene/workbench/webui/frontend/workbench-memory.jsx](/Users/syw/Documents/playground/Cyrene/src/cyrene/workbench/webui/frontend/workbench-memory.jsx:438)、[src/cyrene/workbench/webui/frontend/platform/ui-surface.jsx](/Users/syw/Documents/playground/Cyrene/src/cyrene/workbench/webui/frontend/platform/ui-surface.jsx:448)

按 You are、workspace、toolbox 等分上下文视图；按主动检查提示词识别记忆 UI 标题；按 action_id 前缀推断界面影响描述。

分组或描述层，不会把 prose 变成工具调用。应优先用已有来源、block kind 和 action outcome。

### 32 安装和平台错误文字决定诊断提示

[src/cyrene/plugins/builtin/cyrene_extensions/extension_service.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/plugins/builtin/cyrene_extensions/extension_service.py:251)、[src/cyrene/plugins/builtin/cyrene_extensions/extension_service.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/plugins/builtin/cyrene_extensions/extension_service.py:839)、[src/cyrene/workbench/webui/frontend/features/chat/errors.jsx](/Users/syw/Documents/playground/Cyrene/src/cyrene/workbench/webui/frontend/features/chat/errors.jsx:57)、[src/cyrene/workbench/webui/frontend/platform/api.jsx](/Users/syw/Documents/playground/Cyrene/src/cyrene/workbench/webui/frontend/platform/api.jsx:71)、[src/cyrene/workbench/webui/frontend/settings-model-configuration.jsx](/Users/syw/Documents/playground/Cyrene/src/cyrene/workbench/webui/frontend/settings-model-configuration.jsx:401)、[src/cyrene/plugins/builtin/cyrene_desktop/_app_use_backend.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/plugins/builtin/cyrene_desktop/_app_use_backend.py:316)、[electron/automation/app-use-macos.jxa](/Users/syw/Documents/playground/Cyrene/electron/automation/app-use-macos.jxa:582)、[electron/automation/app-use-windows.ps1](/Users/syw/Documents/playground/Cyrene/electron/automation/app-use-windows.ps1:677)、[electron/remote-desktop/remote-desktop-rdp-sidecar.js](/Users/syw/Documents/playground/Cyrene/electron/remote-desktop/remote-desktop-rdp-sidecar.js:66)

从错误文字生成原因类别、翻译或修复提示；安装任务的失败已由异常决定，不是由文案决定。

提示容易错类或漏翻译。应在源头携带原因码。与会触发重试的分支分开修复。

### 33 原生控件的通用文字参与语义树裁剪

[electron/automation/app-use-linux.js](/Users/syw/Documents/playground/Cyrene/electron/automation/app-use-linux.js:251)、[electron/automation/app-use-windows.ps1](/Users/syw/Documents/playground/Cyrene/electron/automation/app-use-windows.ps1:372)、[electron/automation/app-use.js](/Users/syw/Documents/playground/Cyrene/electron/automation/app-use.js:907)

group、pane、未知等 role/description 和控件名被用于透明容器判断、可操作节点统计与语义覆盖率判断。

影响助理看到的元素和覆盖率提示。平台结构信息优先，不能仅因通用文案隐藏有效目标。

### 34 模型名称决定兼容协议与请求约束

[src/cyrene/model/protocol_adapters.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/model/protocol_adapters.py:81)、[src/cyrene/model/protocol_adapters.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/model/protocol_adapters.py:751)、[src/cyrene/plugins/builtin/cyrene_media/model_catalog.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/plugins/builtin/cyrene_media/model_catalog.py:219)、[src/cyrene/plugins/builtin/cyrene_media/providers/registry.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/plugins/builtin/cyrene_media/providers/registry.py:148)、[src/cyrene/plugins/builtin/cyrene_media/providers/openai_image.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/plugins/builtin/cyrene_media/providers/openai_image.py:88)、[src/cyrene/plugins/builtin/cyrene_media/providers/google_media.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/plugins/builtin/cyrene_media/providers/google_media.py:171)、[src/cyrene/plugins/builtin/cyrene_media/providers/seedance.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/plugins/builtin/cyrene_media/providers/seedance.py:48)、[src/cyrene/plugins/builtin/cyrene_media/providers/minimax.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/plugins/builtin/cyrene_media/providers/minimax.py:478)

模型 ID 的前缀或子串用于协议、媒体能力、参数允许范围等兼容分支。

这是技术型号匹配，不是问候文案，但也有改名后失效的问题。能力和约束应来自模型目录元数据，保留明确版本兼容表。

### 35 模型名称决定上下文与计费估算

[src/cyrene/platform/config_store.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/platform/config_store.py:891)、[src/cyrene/workbench/projects/project_runtime.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/workbench/projects/project_runtime.py:116)、[src/cyrene/platform/attachments.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/platform/attachments.py:299)、[src/cyrene/model/image_tokens.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/model/image_tokens.py:35)、[src/cyrene/model/pricing.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/model/pricing.py:72)

通过 claude、deepseek、qwen 等名称猜上下文或视觉能力，通过模型别名匹配价格。

能力元数据不足时的回退；不应覆盖显式配置。模型名称变化可能影响压缩阈值、附件处理及估算。

### 36 记忆文本安全规则拒绝持久化

[src/cyrene/plugins/builtin/cyrene_memory/project_memory.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/plugins/builtin/cyrene_memory/project_memory.py:106)、[src/cyrene/plugins/builtin/cyrene_memory/project_memory.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/plugins/builtin/cyrene_memory/project_memory.py:890)、[src/cyrene/plugins/builtin/cyrene_memory/project_memory.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/plugins/builtin/cyrene_memory/project_memory.py:1029)

密钥格式、提示词注入语句和有限否定前缀决定是否拒绝模型生成的项目记忆。

有意的防护，不应直接删掉；不能把有限词表当成完整语义保证。应保留来源边界并用结构化学习结果减少歧义。

### 37 文本工具协议泄漏触发撤回与重试

[src/cyrene/plugins/builtin/cyrene_model/response_guard.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/plugins/builtin/cyrene_model/response_guard.py:25)、[src/cyrene/plugins/builtin/cyrene_model/response_guard.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/plugins/builtin/cyrene_model/response_guard.py:56)

回答正文出现 DSML 协议标记会抛模型错误、发 stream_cancelled，走有限重试路径。

这是协议完整性检查，不把文本提升为可执行工具。讨论协议标记的正常答案也可能命中；可按 provider/通道限定，而不是无条件删除检查。

### 38 远程能力名称与 Shell 文本触发额外边界

[src/cyrene/plugins/builtin/cyrene_remote/permission.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/plugins/builtin/cyrene_remote/permission.py:190)、[src/cyrene/plugins/builtin/cyrene_remote/commands.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/plugins/builtin/cyrene_remote/commands.py:2208)、[src/cyrene/core/plugin/core_impl/permission_boundaries.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/core/plugin/core_impl/permission_boundaries.py:184)、[src/cyrene/plugins/native_runtime.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/plugins/native_runtime.py:256)

删除类能力名触发破坏性确认；长 Shell 输入带 base64 解码词被要求改走文件通道；命令结构匹配用于工作区与破坏性操作检查。

确实决定允许/拒绝，但属于命令与能力安全检查。应使用声明的 resource effects 和可靠命令分析，不能因为它们使用匹配就整体移除。

### 39 字段名与凭据格式决定脱敏

[src/cyrene/platform/secret_redaction.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/platform/secret_redaction.py:13)、[src/cyrene/model/errors.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/model/errors.py:13)、[src/cyrene/agents/acp_events.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/agents/acp_events.py:51)、[src/cyrene/core/observability.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/core/observability.py:73)、[src/cyrene/workbench/application/app_control.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/workbench/application/app_control.py:273)、[src/cyrene/plugins/builtin/cyrene_extensions/extension_service.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/plugins/builtin/cyrene_extensions/extension_service.py:230)、[src/cyrene/plugins/builtin/cyrene_extensions/extension_agent_runtime.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/plugins/builtin/cyrene_extensions/extension_agent_runtime.py:71)、[src/cyrene/plugins/builtin/cyrene_skills/capture.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/plugins/builtin/cyrene_skills/capture.py:230)、[src/cyrene/platform/doctor/evidence.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/platform/doctor/evidence.py:7)

secret/token/password 等键名及密钥、用户路径格式触发内容替换或不记录。

有意的隐私保护。会误遮普通字段，也可能漏掉非标准密钥；优先采用字段级敏感声明，文本扫描保留为补充。

### 40 技能参数正则提取的遗留入口

[src/cyrene/plugins/builtin/cyrene_skills/lifecycle.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/plugins/builtin/cyrene_skills/lifecycle.py:510)、[src/cyrene/plugins/builtin/cyrene_skills/lifecycle.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/plugins/builtin/cyrene_skills/lifecycle.py:549)、[src/cyrene/plugins/builtin/cyrene_skills/orchestrator.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/plugins/builtin/cyrene_skills/orchestrator.py:1103)

取第一个路径、数字、日期、URL 或示例匹配作为参数；仅缺少必填项才调用 LLM。

仓库生产调用搜索只找到定义和包装，没有实际调用入口。属于潜在问题，不列为当前自动执行链；若恢复使用，应改为显式参数绑定。

### 41 按失败文字生成技能修补类型的遗留入口

[src/cyrene/plugins/builtin/cyrene_skills/lifecycle.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/plugins/builtin/cyrene_skills/lifecycle.py:338)、[src/cyrene/plugins/builtin/cyrene_skills/orchestrator.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/plugins/builtin/cyrene_skills/orchestrator.py:1047)

missing、parameter、参数决定提议更新输入 schema，否则替换步骤。

只找到定义和包装，未找到生产调用者；不要把它描述成当前自动修补行为。即使启用也只是提出补丁，不直接执行补丁。

### 42 旧后端活动推断 helper

[src/cyrene/workbench/sessions/session_view.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/workbench/sessions/session_view.py:75)

phase_transition.to 含 done/complete 等片段则认为阶段不活跃。

全仓库引用搜索只找到定义。前端同类逻辑仍活跃，已单列；该后端 helper 可考虑删除。

### 43 首页原型直接按用户文字触发演示动作

[prototypes/assistant-home/src/App.jsx](/Users/syw/Documents/playground/Cyrene/prototypes/assistant-home/src/App.jsx:35)

旅行关键词切换 travel；创建任务关键词新增模拟任务；日程、进度、总结等词选择固定回复与文档。

仅 prototypes 演示代码，不是正式 Cyrene 首页实现。若未来复用，必须移除关键词动作分派。

### 44 原型服务按句子选择流程与卡片

[prototypes/assistant-home/server/demo-engine.mjs](/Users/syw/Documents/playground/Cyrene/prototypes/assistant-home/server/demo-engine.mjs:26)、[prototypes/assistant-home/server/demo-engine.mjs](/Users/syw/Documents/playground/Cyrene/prototypes/assistant-home/server/demo-engine.mjs:41)、[prototypes/assistant-home/server/resident-cards.mjs](/Users/syw/Documents/playground/Cyrene/prototypes/assistant-home/server/resident-cards.mjs:18)

按预算、旅行天数、重选资料、风险等词设置流程状态、内容、地图和推荐卡片。

独立演示服务的规则引擎，属于用户所说的同类机制；不应混入生产逻辑。

### 45 显式命令与输入语法

[src/cyrene/workbench/application/commands.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/workbench/application/commands.py:68)、[src/cyrene/workbench/webui/frontend/features/chat/composer-submission.jsx](/Users/syw/Documents/playground/Cyrene/src/cyrene/workbench/webui/frontend/features/chat/composer-submission.jsx:12)、[src/cyrene/cli_chat.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/cli_chat.py:2756)、[src/cyrene/workbench/http/settings/profile_data.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/workbench/http/settings/profile_data.py:84)

解析 /命令、参数、布尔输入和 RESET CYRENE DATA 确认短语。

用户明确使用的命令/确认协议，不是从自由文案猜意图。保留，仍需正常权限校验。

### 46 显式文字定位与等待条件

[src/cyrene/plugins/builtin/cyrene_browser/runtime.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/plugins/builtin/cyrene_browser/runtime.py:2588)、[electron/browser/browser-target.js](/Users/syw/Documents/playground/Cyrene/electron/browser/browser-target.js:31)、[electron/automation/app-use.js](/Users/syw/Documents/playground/Cyrene/electron/automation/app-use.js:1417)、[mobile/app/src/main/assets/browser/page-commands.js](/Users/syw/Documents/playground/Cyrene/mobile/app/src/main/assets/browser/page-commands.js:26)、[src/cyrene/plugins/builtin/cyrene_code/workspace_execution.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/plugins/builtin/cyrene_code/workspace_execution.py:555)

调用方明确给定 text/contains/readyPattern 时匹配目标或标记服务 ready。

这是工具定义的查询与等待功能。保留；多个命中、否定匹配和目标身份应由工具契约处理。

### 47 Markdown 和持久化文本格式协议

[src/cyrene/workbench/chat/chat_application.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/workbench/chat/chat_application.py:1495)、[src/cyrene/workbench/webui/frontend/shared/markdown/renderer.jsx](/Users/syw/Documents/playground/Cyrene/src/cyrene/workbench/webui/frontend/shared/markdown/renderer.jsx:170)、[src/cyrene/plugins/builtin/cyrene_soul/store.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/plugins/builtin/cyrene_soul/store.py:168)、[src/cyrene/plugins/builtin/cyrene_memory/steward.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/plugins/builtin/cyrene_memory/steward.py:25)、[src/cyrene/plugins/builtin/cyrene_memory/archive_format.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/plugins/builtin/cyrene_memory/archive_format.py:10)

解析按钮指令块、SOUL 编辑命令、ENTITY 和历史归档格式。

属于有明确定义的语法，不能等同于看到“删除”就删除。若重构，采用结构化数据迁移，而非只删正则。

### 48 语音标点静音与朗读分段

[src/cyrene/plugins/builtin/cyrene_voice/engine.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/plugins/builtin/cyrene_voice/engine.py:504)、[src/cyrene/workbench/webui/frontend/features/chat/voice-input.jsx](/Users/syw/Documents/playground/Cyrene/src/cyrene/workbench/webui/frontend/features/chat/voice-input.jsx:61)、[src/cyrene/workbench/webui/frontend/features/chat/voice-playback.jsx](/Users/syw/Documents/playground/Cyrene/src/cyrene/workbench/webui/frontend/features/chat/voice-playback.jsx:147)、[src/cyrene/workbench/webui/frontend/features/chat/voice-playback.jsx](/Users/syw/Documents/playground/Cyrene/src/cyrene/workbench/webui/frontend/features/chat/voice-playback.jsx:283)

过滤静音标记/无效字符、判断句子与 Markdown 边界后分段朗读。

文本处理本身就是功能；并不据自由文字启动任务。保留，需针对语言和分段误差验证。

### 49 技术格式与搜索匹配

[src/cyrene/model/tool_names.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/model/tool_names.py:11)、[src/cyrene/plugins/builtin/cyrene_knowledge/store.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/plugins/builtin/cyrene_knowledge/store.py:312)、[src/cyrene/workbench/webui/frontend/features/chat/context-graph-model.mjs](/Users/syw/Documents/playground/Cyrene/src/cyrene/workbench/webui/frontend/features/chat/context-graph-model.mjs:53)、[electron/backend/backend-process.js](/Users/syw/Documents/playground/Cyrene/electron/backend/backend-process.js:24)

ID、路径、MIME、URL、版本号、HTTP/SSE/OSC、PORT 输出、关键字搜索与测试输出断言。

正常协议/格式处理。扫描中的同类命中归入此类，不列为业务状态误判。构建脚本、测试与历史实验的匹配也按用途归类。

## 保留的明确文本协议和正常处理

### 1 权限选项文字决定授权范围

[src/cyrene/core/plugin/permission_grants.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/core/plugin/permission_grants.py:86)

只验证答案属于提供的 label，再用拒绝词表判断否定；含“本次会话”“始终”则保存会话授权，否则保存单次授权。

优先修复。隔离复现：如果提供的自定义选项是“不同意”，选择它仍会记录单次授权。默认“拒绝”选项不受这个例子影响。应使用 decision 与 scope 字段，未知值拒绝。

### 2 密码提示文字决定敏感终端输入是否放行

[src/cyrene/plugins/builtin/cyrene_code/send_shell.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/plugins/builtin/cyrene_code/send_shell.py:65)、[src/cyrene/plugins/builtin/cyrene_code/send_shell.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/plugins/builtin/cyrene_code/send_shell.py:85)

sensitive=true 跳过普通命令的工作区写入目标检查，再匹配屏幕末四行的 password、PIN、密码等词来决定能否输入。

优先修复。普通输出最后一行为 Password: 也通过判断；屏幕文字不能证明终端真的处于凭据输入状态。应绑定可信输入状态或明确授权，不把显示文字作为检查豁免依据。

### 3 自然语言被记录为上一轮纠错

[src/cyrene/plugins/builtin/cyrene_skills/orchestrator.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/plugins/builtin/cyrene_skills/orchestrator.py:208)、[src/cyrene/plugins/builtin/cyrene_skills/lifecycle.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/plugins/builtin/cyrene_skills/lifecycle.py:36)、[src/cyrene/plugins/builtin/cyrene_skills/capture.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/plugins/builtin/cyrene_skills/capture.py:490)

匹配“错”“重新”“fix”“retry”等片段后，将上一轮 behavior_turns 写成 correction。

已复现“做得不错”也得到 correction。应使用明确反馈事件或语义判断，不能按单字决定负面学习证据。

### 4 工具输出文字决定失败状态

[src/cyrene/workbench/application/inbox.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/workbench/application/inbox.py:213)、[src/cyrene/workbench/application/inbox.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/workbench/application/inbox.py:972)、[src/cyrene/plugins/builtin/cyrene_memory/structured.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/plugins/builtin/cyrene_memory/structured.py:966)、[src/cyrene/plugins/builtin/cyrene_memory/structured.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/plugins/builtin/cyrene_memory/structured.py:954)

非结构化工具结果以 error、failed 等英文开头则算失败；同时影响收件箱工具事件及能否作为 verified result 供记忆提取。

已复现“错误：文件不存在”未被识别为失败，而 Error count: 0 被识别为失败。统一使用工具执行结果的 ok、failure、error_code；无法判定的文本不能标为 verified。

### 5 内部学习任务由提示词前缀识别

[src/cyrene/plugins/builtin/cyrene_skills/orchestrator.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/plugins/builtin/cyrene_skills/orchestrator.py:938)、[src/cyrene/plugins/builtin/cyrene_skills/orchestrator.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/plugins/builtin/cyrene_skills/orchestrator.py:1310)

除了 system_initiated 字段，还按固定内部提示词前缀排除学习证据，并重写旧 behavior_turns 的内容和元数据。

属于历史兼容，但普通用户文字若恰好匹配也会受影响。迁移应按记录版本和来源一次性处理，运行时只看来源字段。

### 6 短期记忆情绪分数由英文词片段决定

[src/cyrene/plugins/builtin/cyrene_memory/short_term.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/plugins/builtin/cyrene_memory/short_term.py:313)

frustrat、stress、angry 赋 -2；happy、love、excit 赋 +2，然后持久化 emotional_valence。

不理解否定和中文。not happy 按规则会得到正值。应由结构化提取结果给出情绪，或不保存推测分数。

### 7 Doctor 依据建议文字拒收诊断

[src/cyrene/platform/doctor/agent_analysis.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/platform/doctor/agent_analysis.py:74)

next_steps 出现“写入测试”或 write test 时，返回 accepted=false，要求模型重交诊断。

会连带拦住 write tests 这类正常测试建议，也可因同义表达漏判。应验证结构化修复动作和能力，而不是建议措辞。

### 8 ComfyUI 文本提取任务编号和下载地址

[src/cyrene/plugins/builtin/cyrene_media/providers/comfyui.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/plugins/builtin/cyrene_media/providers/comfyui.py:73)、[src/cyrene/plugins/builtin/cyrene_media/providers/comfyui.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/plugins/builtin/cyrene_media/providers/comfyui.py:107)、[src/cyrene/plugins/builtin/cyrene_media/providers/comfyui.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/plugins/builtin/cyrene_media/providers/comfyui.py:605)、[src/cyrene/plugins/builtin/cyrene_media/providers/comfyui.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/plugins/builtin/cyrene_media/providers/comfyui.py:690)

对结果中的 prompt/job/task id 作正则提取；没有目录或内嵌产物时，从输出文本枚举 URL 并下载为媒体产物。

会把说明或示例 URL 当成产物候选。应要求适配器返回明确的 job_id 和 artifacts；现有下载校验仍保留。

### 9 Linux 原生动作名称决定实际执行哪个动作

[electron/automation/app-use-linux.js](/Users/syw/Documents/playground/Cyrene/electron/automation/app-use-linux.js:38)、[electron/automation/app-use-linux.js](/Users/syw/Documents/playground/Cyrene/electron/automation/app-use-linux.js:142)、[electron/automation/app-use-linux.js](/Users/syw/Documents/playground/Cyrene/electron/automation/app-use-linux.js:302)

将 AT-SPI 动作名按 click、select、toggle 等词映射，再选对应 descriptor.index 调用 DoAction。

这是系统兼容层，但匹配过宽或本地化动作名会错分。优先使用平台动作契约和显式映射，未知动作不得猜成另一种动作。

### 10 RDP 按窗口标题回退选择捕获源

[electron/remote-desktop/remote-desktop-rdp-sidecar.js](/Users/syw/Documents/playground/Cyrene/electron/remote-desktop/remote-desktop-rdp-sidecar.js:352)

按窗口 ID 找不到捕获源后，取名称匹配 freerdp 或 remote desktop 的第一个窗口。

有多个远程窗口时可能选错捕获对象。应绑定当前会话的窗口与进程身份，失败时明确报错。

### 11 终端名称从用户句子中猜取

[src/cyrene/plugins/builtin/cyrene_code/services.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/plugins/builtin/cyrene_code/services.py:14)、[src/cyrene/plugins/builtin/cyrene_code/services.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/plugins/builtin/cyrene_code/services.py:38)、[src/cyrene/plugins/builtin/cyrene_code/start_shell.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/plugins/builtin/cyrene_code/start_shell.py:55)

没有显式 title 时，匹配“名为”“叫作”或 terminal named 等表达，作为新终端标题。

只补全已调用的建终端操作，不会仅凭句子创建终端；仍会误取否定句或无关名称。让调用方显式提供 title。

### 12 通用对话组标题触发重新生成

[src/cyrene/workbench/chat/chat_service.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/workbench/chat/chat_service.py:544)、[src/cyrene/workbench/chat/chat_service.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/workbench/chat/chat_service.py:595)

去标点后匹配 Chat group、新对话组等黑名单，将标题清空并触发最多两次元数据生成；仍失败则选已有标题。

这是文案质量规则，确实会增加模型调用。应与标题有效性字段分开，保留人工锁定标题规则。

### 13 通用模型错误分类与重试

[src/cyrene/model/error_details.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/model/error_details.py:211)、[src/cyrene/model/error_details.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/model/error_details.py:267)

从错误字符串提取 HTTP 状态和 quota、authentication、context window、timeout 等词，决定错误码、retryable 与 retry_scope。

已有结构化信息应始终优先；文本仅作明确标记的兼容回退。会影响停止、延迟重试或调整参数后重试。

### 14 Codex 可用性由错误文字推断

[src/cyrene/model/codex_provider.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/model/codex_provider.py:140)

quota、unsupported model、token expired 等文字可转成额度耗尽、模型不可用、登录过期错误。

和通用错误分类同类，应优先使用 codexErrorInfo 与 HTTP 状态。

### 15 Codex 协议错误文字触发 CLI 版本回退

[src/cyrene/model/codex_provider.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/model/codex_provider.py:407)、[src/cyrene/model/codex_provider.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/model/codex_provider.py:415)

匹配版本冲突词，排除 TLS 等词后，下载 SDK 固定版本 CLI；已安装固定版本则不做。

实际会下载和切换运行时，优先级高于一般错误提示。应通过明确的协议版本能力或错误类型触发。

### 16 媒体生成错误文字决定自动重试

[src/cyrene/plugins/builtin/cyrene_media/providers/minimax.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/plugins/builtin/cyrene_media/providers/minimax.py:68)、[src/cyrene/plugins/builtin/cyrene_media/providers/google_media.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/plugins/builtin/cyrene_media/providers/google_media.py:40)、[src/cyrene/plugins/builtin/cyrene_media/providers/comfyui.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/plugins/builtin/cyrene_media/providers/comfyui.py:119)、[src/cyrene/plugins/builtin/cyrene_media/manager.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/plugins/builtin/cyrene_media/manager.py:525)

MiniMax、Google、ComfyUI 按 busy、rate、unavailable 等文本判断 retryable，作业管理器在剩余次数内据此重试。

MiniMax 已有业务 status_code，应建立错误码表；Google 优先异常状态；ComfyUI 保留结构化 MCP 错误。文本判断可能重复昂贵请求。

### 17 SQLite 错误文字决定重试或兼容路径

[src/cyrene/workbench/persistence/schema.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/workbench/persistence/schema.py:240)、[src/cyrene/plugins/builtin/cyrene_remote/common.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/plugins/builtin/cyrene_remote/common.py:33)、[src/cyrene/workbench/application/inbox.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/workbench/application/inbox.py:1820)、[src/cyrene/workbench/application/inbox.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/workbench/application/inbox.py:1873)、[src/cyrene/workbench/control/control_ports.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/workbench/control/control_ports.py:193)、[src/cyrene/plugins/builtin/cyrene_schedule/migrations.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/plugins/builtin/cyrene_schedule/migrations.py:67)、[src/cyrene/plugins/builtin/cyrene_knowledge/store.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/plugins/builtin/cyrene_knowledge/store.py:609)

locked 决定 schema 重试；busy 决定远程错误类别；no such table、duplicate column name、fts5 决定忽略错误或跳过兼容分支。

技术兼容逻辑，不是用户措辞识别。可用 sqlite_errorcode、schema introspection 和功能探测替代可替代部分。

### 18 移动端串口错误文字决定继续等待

[mobile/runtime-app/src/main/kotlin/ai/cyrene/mobile/runtime/QemuVirtualMachine.kt](/Users/syw/Documents/playground/Cyrene/mobile/runtime-app/src/main/kotlin/ai/cyrene/mobile/runtime/QemuVirtualMachine.kt:261)

IOException 消息含 Try again 或 EAGAIN 时继续 readUntil 循环，否则抛出错误。

可能因平台文案改变而中断。优先用 errno 或明确的可重试异常类型；仍受超时限制。

### 19 导航错误文字决定等待替代导航

[electron/desktop/main-window-lifecycle.js](/Users/syw/Documents/playground/Cyrene/electron/desktop/main-window-lifecycle.js:32)、[electron/desktop/main-window-lifecycle.js](/Users/syw/Documents/playground/Cyrene/electron/desktop/main-window-lifecycle.js:101)

在 errno/code 不足时用 ERR_ABORTED (-3) 文本识别取消，转去等待替代导航。

已有代码优先结构化字段；字符串仅是兼容回退，可通过统一导航错误对象消除。

### 20 服务错误文字决定 HTTP 状态与错误类别

[src/cyrene/plugins/builtin/cyrene_cli/application.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/plugins/builtin/cyrene_cli/application.py:80)、[src/cyrene/plugins/builtin/cyrene_cli/application.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/plugins/builtin/cyrene_cli/application.py:129)、[src/cyrene/plugins/builtin/cyrene_schedule/workbench_service.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/plugins/builtin/cyrene_schedule/workbench_service.py:103)、[src/cyrene/plugins/builtin/cyrene_remote/pairing.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/plugins/builtin/cyrene_remote/pairing.py:585)、[src/cyrene/plugins/builtin/cyrene_remote/services.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/plugins/builtin/cyrene_remote/services.py:202)、[src/cyrene/core/plugin/core_impl/toolbox.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/core/plugin/core_impl/toolbox.py:417)

not found 决定 404/400；配对异常的英文整句决定错误码；Invalid arguments 或中文等价文案决定插件参数错误类型。

应用内部错误应使用专用异常类或 code，避免修改提示文案改变 API 行为。

### 21 模型目录与项目记忆错误兼容回退

[src/cyrene/plugins/model_catalog.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/plugins/model_catalog.py:401)、[src/cyrene/plugins/builtin/cyrene_model/routes.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/plugins/builtin/cyrene_model/routes.py:730)、[src/cyrene/plugins/builtin/cyrene_memory/project_memory.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/plugins/builtin/cyrene_memory/project_memory.py:1125)、[src/cyrene/plugins/builtin/cyrene_memory/project_memory.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/plugins/builtin/cyrene_memory/project_memory.py:1202)

精确英文错误文字控制模型配置缺失时的回退、无效目录识别、项目记忆模型不可用转换及上下文溢出分类。

主要是内部接口耦合；应传递稳定异常类型和错误码。

### 22 计划是否被拒绝由答案词表推断

[src/cyrene/workbench/core_adapter/bridge.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/workbench/core_adapter/bridge.py:334)

投影 pending plan 时，将词表中的“拒绝”“不同意”等标成 rejected，其余答案标成 active。

这是 active_plan 投影，不等同于实际权限授权。应传递结构化 decision，未知回答不能展示成同意。

### 23 进度标签由文字决定颜色

[src/cyrene/workbench/webui/frontend/features/chat/assistant-card-model.mjs](/Users/syw/Documents/playground/Cyrene/src/cyrene/workbench/webui/frontend/features/chat/assistant-card-model.mjs:59)、[src/cyrene/workbench/webui/frontend/features/chat/assistant-cards.jsx](/Users/syw/Documents/playground/Cyrene/src/cyrene/workbench/webui/frontend/features/chat/assistant-cards.jsx:61)

匹配失败、已完成等文案输出 tone。

已复现“未发现错误”显示 error；只改变样式，不结束任务。应使用状态枚举和独立 label。

### 24 天气描述决定默认图标

[src/cyrene/workbench/webui/frontend/features/chat/card-weather-model.mjs](/Users/syw/Documents/playground/Cyrene/src/cyrene/workbench/webui/frontend/features/chat/card-weather-model.mjs:14)

icon 不是有效枚举时，从 condition 的中英文天气词猜图标；有效 icon 优先。

只影响图标。天气条件应提供有效 icon，缺失可直接显示 unknown；按时间切换日夜本身不是文字匹配。

### 25 运行阶段与活动标签决定忙碌展示

[src/cyrene/workbench/webui/frontend/features/session/activity.jsx](/Users/syw/Documents/playground/Cyrene/src/cyrene/workbench/webui/frontend/features/session/activity.jsx:234)、[src/cyrene/workbench/webui/frontend/features/session/activity.jsx](/Users/syw/Documents/playground/Cyrene/src/cyrene/workbench/webui/frontend/features/session/activity.jsx:309)、[src/cyrene/workbench/webui/frontend/features/session/activity.jsx](/Users/syw/Documents/playground/Cyrene/src/cyrene/workbench/webui/frontend/features/session/activity.jsx:549)

plan 推断 planning；browser/web/click 推断浏览器活动；done/complete 等片段推断阶段是否结束。

改变 UI 活跃状态及活动内容。incomplete 也包含 complete。应使用阶段枚举；后端同名旧 helper 另列为遗留代码。

### 26 工具名称决定图标分类与失败提示

[src/cyrene/workbench/webui/frontend/features/chat/messages.jsx](/Users/syw/Documents/playground/Cyrene/src/cyrene/workbench/webui/frontend/features/chat/messages.jsx:865)、[src/cyrene/workbench/webui/frontend/features/chat/messages.jsx](/Users/syw/Documents/playground/Cyrene/src/cyrene/workbench/webui/frontend/features/chat/messages.jsx:878)、[src/cyrene/workbench/webui/frontend/features/chat/file-resources.jsx](/Users/syw/Documents/playground/Cyrene/src/cyrene/workbench/webui/frontend/features/chat/file-resources.jsx:186)、[src/cyrene/workbench/webui/frontend/workbench-memory.jsx](/Users/syw/Documents/playground/Cyrene/src/cyrene/workbench/webui/frontend/workbench-memory.jsx:575)

通过工具名关键词选类别和图标；tool failed: 前缀可给事件标失败。

显示层，不能作为执行结果来源。工具应声明 presentation kind，失败应使用事件字段。

### 27 权限按钮文案与位置决定显示标签

[src/cyrene/workbench/webui/frontend/features/chat/behavior.mjs](/Users/syw/Documents/playground/Cyrene/src/cyrene/workbench/webui/frontend/features/chat/behavior.mjs:51)

综合 optionId/id/kind/value/label/title 匹配 allow_once/session 等值，匹配不上时还按按钮数量和位置推断。

只改变显示标签，但可能让用户看到的授权范围与提交的原值不一致。应优先明确 optionId，未知选项原样显示。

### 28 外部 Agent 警告文本被移出正文

[src/cyrene/agents/notices.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/agents/notices.py:17)、[src/cyrene/agents/notices.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/agents/notices.py:60)、[src/cyrene/workbench/chat/chat_external_turn_service.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/workbench/chat/chat_external_turn_service.py:123)

识别开头 warning:/notice: 和网络术语，分离为运行通知，不再当作普通回答正文。

当前 ACP 兼容处理；不会按文字重试。普通回答若讨论相同句式可能被误归类，应通过事件通道携带通知。

### 29 浏览器正文推断访问限制信号

[src/cyrene/plugins/builtin/cyrene_browser/runtime.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/plugins/builtin/cyrene_browser/runtime.py:374)、[electron/main.js](/Users/syw/Documents/playground/Cyrene/electron/main.js:1015)

正文同时含“暂时无法访问”与“登录后查看”等词时返回 access_gate、retry_allowed、冷却时间等提示。

给助理提供恢复建议；这两个函数本身不执行重试。文章引用这些词也可能误判，应结合 DOM 和响应状态并标为启发式。

### 30 桌面控件文字推断风险等级

[src/cyrene/plugins/builtin/cyrene_desktop/_app_semantic_backend.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/plugins/builtin/cyrene_desktop/_app_semantic_backend.py:125)、[src/cyrene/plugins/builtin/cyrene_desktop/_app_semantic_backend.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/plugins/builtin/cyrene_desktop/_app_semantic_backend.py:191)

名称/描述/role 含 delete、pay、send 等英文片段则标 R3，否则点击类标 R2。

已复现“发送”R2 而 Send 为 R3。这里只返回风险元数据，不能说它单独放行执行。应结合声明的操作语义，并对未知风险保守处理。

### 31 系统提示词和内部任务按文案分组

[src/cyrene/workbench/chat/conversation_context_service.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/workbench/chat/conversation_context_service.py:1140)、[src/cyrene/workbench/webui/frontend/workbench-memory.jsx](/Users/syw/Documents/playground/Cyrene/src/cyrene/workbench/webui/frontend/workbench-memory.jsx:438)、[src/cyrene/workbench/webui/frontend/platform/ui-surface.jsx](/Users/syw/Documents/playground/Cyrene/src/cyrene/workbench/webui/frontend/platform/ui-surface.jsx:448)

按 You are、workspace、toolbox 等分上下文视图；按主动检查提示词识别记忆 UI 标题；按 action_id 前缀推断界面影响描述。

分组或描述层，不会把 prose 变成工具调用。应优先用已有来源、block kind 和 action outcome。

### 32 安装和平台错误文字决定诊断提示

[src/cyrene/plugins/builtin/cyrene_extensions/extension_service.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/plugins/builtin/cyrene_extensions/extension_service.py:251)、[src/cyrene/plugins/builtin/cyrene_extensions/extension_service.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/plugins/builtin/cyrene_extensions/extension_service.py:839)、[src/cyrene/workbench/webui/frontend/features/chat/errors.jsx](/Users/syw/Documents/playground/Cyrene/src/cyrene/workbench/webui/frontend/features/chat/errors.jsx:57)、[src/cyrene/workbench/webui/frontend/platform/api.jsx](/Users/syw/Documents/playground/Cyrene/src/cyrene/workbench/webui/frontend/platform/api.jsx:71)、[src/cyrene/workbench/webui/frontend/settings-model-configuration.jsx](/Users/syw/Documents/playground/Cyrene/src/cyrene/workbench/webui/frontend/settings-model-configuration.jsx:401)、[src/cyrene/plugins/builtin/cyrene_desktop/_app_use_backend.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/plugins/builtin/cyrene_desktop/_app_use_backend.py:316)、[electron/automation/app-use-macos.jxa](/Users/syw/Documents/playground/Cyrene/electron/automation/app-use-macos.jxa:582)、[electron/automation/app-use-windows.ps1](/Users/syw/Documents/playground/Cyrene/electron/automation/app-use-windows.ps1:677)、[electron/remote-desktop/remote-desktop-rdp-sidecar.js](/Users/syw/Documents/playground/Cyrene/electron/remote-desktop/remote-desktop-rdp-sidecar.js:66)

从错误文字生成原因类别、翻译或修复提示；安装任务的失败已由异常决定，不是由文案决定。

提示容易错类或漏翻译。应在源头携带原因码。与会触发重试的分支分开修复。

### 33 原生控件的通用文字参与语义树裁剪

[electron/automation/app-use-linux.js](/Users/syw/Documents/playground/Cyrene/electron/automation/app-use-linux.js:251)、[electron/automation/app-use-windows.ps1](/Users/syw/Documents/playground/Cyrene/electron/automation/app-use-windows.ps1:372)、[electron/automation/app-use.js](/Users/syw/Documents/playground/Cyrene/electron/automation/app-use.js:907)

group、pane、未知等 role/description 和控件名被用于透明容器判断、可操作节点统计与语义覆盖率判断。

影响助理看到的元素和覆盖率提示。平台结构信息优先，不能仅因通用文案隐藏有效目标。

### 34 模型名称决定兼容协议与请求约束

[src/cyrene/model/protocol_adapters.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/model/protocol_adapters.py:81)、[src/cyrene/model/protocol_adapters.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/model/protocol_adapters.py:751)、[src/cyrene/plugins/builtin/cyrene_media/model_catalog.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/plugins/builtin/cyrene_media/model_catalog.py:219)、[src/cyrene/plugins/builtin/cyrene_media/providers/registry.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/plugins/builtin/cyrene_media/providers/registry.py:148)、[src/cyrene/plugins/builtin/cyrene_media/providers/openai_image.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/plugins/builtin/cyrene_media/providers/openai_image.py:88)、[src/cyrene/plugins/builtin/cyrene_media/providers/google_media.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/plugins/builtin/cyrene_media/providers/google_media.py:171)、[src/cyrene/plugins/builtin/cyrene_media/providers/seedance.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/plugins/builtin/cyrene_media/providers/seedance.py:48)、[src/cyrene/plugins/builtin/cyrene_media/providers/minimax.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/plugins/builtin/cyrene_media/providers/minimax.py:478)

模型 ID 的前缀或子串用于协议、媒体能力、参数允许范围等兼容分支。

这是技术型号匹配，不是问候文案，但也有改名后失效的问题。能力和约束应来自模型目录元数据，保留明确版本兼容表。

### 35 模型名称决定上下文与计费估算

[src/cyrene/platform/config_store.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/platform/config_store.py:891)、[src/cyrene/workbench/projects/project_runtime.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/workbench/projects/project_runtime.py:116)、[src/cyrene/platform/attachments.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/platform/attachments.py:299)、[src/cyrene/model/image_tokens.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/model/image_tokens.py:35)、[src/cyrene/model/pricing.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/model/pricing.py:72)

通过 claude、deepseek、qwen 等名称猜上下文或视觉能力，通过模型别名匹配价格。

能力元数据不足时的回退；不应覆盖显式配置。模型名称变化可能影响压缩阈值、附件处理及估算。

### 36 记忆文本安全规则拒绝持久化

[src/cyrene/plugins/builtin/cyrene_memory/project_memory.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/plugins/builtin/cyrene_memory/project_memory.py:106)、[src/cyrene/plugins/builtin/cyrene_memory/project_memory.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/plugins/builtin/cyrene_memory/project_memory.py:890)、[src/cyrene/plugins/builtin/cyrene_memory/project_memory.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/plugins/builtin/cyrene_memory/project_memory.py:1029)

密钥格式、提示词注入语句和有限否定前缀决定是否拒绝模型生成的项目记忆。

有意的防护，不应直接删掉；不能把有限词表当成完整语义保证。应保留来源边界并用结构化学习结果减少歧义。

### 37 文本工具协议泄漏触发撤回与重试

[src/cyrene/plugins/builtin/cyrene_model/response_guard.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/plugins/builtin/cyrene_model/response_guard.py:25)、[src/cyrene/plugins/builtin/cyrene_model/response_guard.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/plugins/builtin/cyrene_model/response_guard.py:56)

回答正文出现 DSML 协议标记会抛模型错误、发 stream_cancelled，走有限重试路径。

这是协议完整性检查，不把文本提升为可执行工具。讨论协议标记的正常答案也可能命中；可按 provider/通道限定，而不是无条件删除检查。

### 38 远程能力名称与 Shell 文本触发额外边界

[src/cyrene/plugins/builtin/cyrene_remote/permission.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/plugins/builtin/cyrene_remote/permission.py:190)、[src/cyrene/plugins/builtin/cyrene_remote/commands.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/plugins/builtin/cyrene_remote/commands.py:2208)、[src/cyrene/core/plugin/core_impl/permission_boundaries.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/core/plugin/core_impl/permission_boundaries.py:184)、[src/cyrene/plugins/native_runtime.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/plugins/native_runtime.py:256)

删除类能力名触发破坏性确认；长 Shell 输入带 base64 解码词被要求改走文件通道；命令结构匹配用于工作区与破坏性操作检查。

确实决定允许/拒绝，但属于命令与能力安全检查。应使用声明的 resource effects 和可靠命令分析，不能因为它们使用匹配就整体移除。

### 39 字段名与凭据格式决定脱敏

[src/cyrene/platform/secret_redaction.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/platform/secret_redaction.py:13)、[src/cyrene/model/errors.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/model/errors.py:13)、[src/cyrene/agents/acp_events.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/agents/acp_events.py:51)、[src/cyrene/core/observability.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/core/observability.py:73)、[src/cyrene/workbench/application/app_control.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/workbench/application/app_control.py:273)、[src/cyrene/plugins/builtin/cyrene_extensions/extension_service.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/plugins/builtin/cyrene_extensions/extension_service.py:230)、[src/cyrene/plugins/builtin/cyrene_extensions/extension_agent_runtime.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/plugins/builtin/cyrene_extensions/extension_agent_runtime.py:71)、[src/cyrene/plugins/builtin/cyrene_skills/capture.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/plugins/builtin/cyrene_skills/capture.py:230)、[src/cyrene/platform/doctor/evidence.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/platform/doctor/evidence.py:7)

secret/token/password 等键名及密钥、用户路径格式触发内容替换或不记录。

有意的隐私保护。会误遮普通字段，也可能漏掉非标准密钥；优先采用字段级敏感声明，文本扫描保留为补充。

### 40 技能参数正则提取的遗留入口

[src/cyrene/plugins/builtin/cyrene_skills/lifecycle.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/plugins/builtin/cyrene_skills/lifecycle.py:510)、[src/cyrene/plugins/builtin/cyrene_skills/lifecycle.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/plugins/builtin/cyrene_skills/lifecycle.py:549)、[src/cyrene/plugins/builtin/cyrene_skills/orchestrator.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/plugins/builtin/cyrene_skills/orchestrator.py:1103)

取第一个路径、数字、日期、URL 或示例匹配作为参数；仅缺少必填项才调用 LLM。

仓库生产调用搜索只找到定义和包装，没有实际调用入口。属于潜在问题，不列为当前自动执行链；若恢复使用，应改为显式参数绑定。

### 41 按失败文字生成技能修补类型的遗留入口

[src/cyrene/plugins/builtin/cyrene_skills/lifecycle.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/plugins/builtin/cyrene_skills/lifecycle.py:338)、[src/cyrene/plugins/builtin/cyrene_skills/orchestrator.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/plugins/builtin/cyrene_skills/orchestrator.py:1047)

missing、parameter、参数决定提议更新输入 schema，否则替换步骤。

只找到定义和包装，未找到生产调用者；不要把它描述成当前自动修补行为。即使启用也只是提出补丁，不直接执行补丁。

### 42 旧后端活动推断 helper

[src/cyrene/workbench/sessions/session_view.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/workbench/sessions/session_view.py:75)

phase_transition.to 含 done/complete 等片段则认为阶段不活跃。

全仓库引用搜索只找到定义。前端同类逻辑仍活跃，已单列；该后端 helper 可考虑删除。

### 43 首页原型直接按用户文字触发演示动作

[prototypes/assistant-home/src/App.jsx](/Users/syw/Documents/playground/Cyrene/prototypes/assistant-home/src/App.jsx:35)

旅行关键词切换 travel；创建任务关键词新增模拟任务；日程、进度、总结等词选择固定回复与文档。

仅 prototypes 演示代码，不是正式 Cyrene 首页实现。若未来复用，必须移除关键词动作分派。

### 44 原型服务按句子选择流程与卡片

[prototypes/assistant-home/server/demo-engine.mjs](/Users/syw/Documents/playground/Cyrene/prototypes/assistant-home/server/demo-engine.mjs:26)、[prototypes/assistant-home/server/demo-engine.mjs](/Users/syw/Documents/playground/Cyrene/prototypes/assistant-home/server/demo-engine.mjs:41)、[prototypes/assistant-home/server/resident-cards.mjs](/Users/syw/Documents/playground/Cyrene/prototypes/assistant-home/server/resident-cards.mjs:18)

按预算、旅行天数、重选资料、风险等词设置流程状态、内容、地图和推荐卡片。

独立演示服务的规则引擎，属于用户所说的同类机制；不应混入生产逻辑。

### 45 显式命令与输入语法

[src/cyrene/workbench/application/commands.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/workbench/application/commands.py:68)、[src/cyrene/workbench/webui/frontend/features/chat/composer-submission.jsx](/Users/syw/Documents/playground/Cyrene/src/cyrene/workbench/webui/frontend/features/chat/composer-submission.jsx:12)、[src/cyrene/cli_chat.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/cli_chat.py:2756)、[src/cyrene/workbench/http/settings/profile_data.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/workbench/http/settings/profile_data.py:84)

解析 /命令、参数、布尔输入和 RESET CYRENE DATA 确认短语。

用户明确使用的命令/确认协议，不是从自由文案猜意图。保留，仍需正常权限校验。

### 46 显式文字定位与等待条件

[src/cyrene/plugins/builtin/cyrene_browser/runtime.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/plugins/builtin/cyrene_browser/runtime.py:2588)、[electron/browser/browser-target.js](/Users/syw/Documents/playground/Cyrene/electron/browser/browser-target.js:31)、[electron/automation/app-use.js](/Users/syw/Documents/playground/Cyrene/electron/automation/app-use.js:1417)、[mobile/app/src/main/assets/browser/page-commands.js](/Users/syw/Documents/playground/Cyrene/mobile/app/src/main/assets/browser/page-commands.js:26)、[src/cyrene/plugins/builtin/cyrene_code/workspace_execution.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/plugins/builtin/cyrene_code/workspace_execution.py:555)

调用方明确给定 text/contains/readyPattern 时匹配目标或标记服务 ready。

这是工具定义的查询与等待功能。保留；多个命中、否定匹配和目标身份应由工具契约处理。

### 47 Markdown 和持久化文本格式协议

[src/cyrene/workbench/chat/chat_application.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/workbench/chat/chat_application.py:1495)、[src/cyrene/workbench/webui/frontend/shared/markdown/renderer.jsx](/Users/syw/Documents/playground/Cyrene/src/cyrene/workbench/webui/frontend/shared/markdown/renderer.jsx:170)、[src/cyrene/plugins/builtin/cyrene_soul/store.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/plugins/builtin/cyrene_soul/store.py:168)、[src/cyrene/plugins/builtin/cyrene_memory/steward.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/plugins/builtin/cyrene_memory/steward.py:25)、[src/cyrene/plugins/builtin/cyrene_memory/archive_format.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/plugins/builtin/cyrene_memory/archive_format.py:10)

解析按钮指令块、SOUL 编辑命令、ENTITY 和历史归档格式。

属于有明确定义的语法，不能等同于看到“删除”就删除。若重构，采用结构化数据迁移，而非只删正则。

### 48 语音标点静音与朗读分段

[src/cyrene/plugins/builtin/cyrene_voice/engine.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/plugins/builtin/cyrene_voice/engine.py:504)、[src/cyrene/workbench/webui/frontend/features/chat/voice-input.jsx](/Users/syw/Documents/playground/Cyrene/src/cyrene/workbench/webui/frontend/features/chat/voice-input.jsx:61)、[src/cyrene/workbench/webui/frontend/features/chat/voice-playback.jsx](/Users/syw/Documents/playground/Cyrene/src/cyrene/workbench/webui/frontend/features/chat/voice-playback.jsx:147)、[src/cyrene/workbench/webui/frontend/features/chat/voice-playback.jsx](/Users/syw/Documents/playground/Cyrene/src/cyrene/workbench/webui/frontend/features/chat/voice-playback.jsx:283)

过滤静音标记/无效字符、判断句子与 Markdown 边界后分段朗读。

文本处理本身就是功能；并不据自由文字启动任务。保留，需针对语言和分段误差验证。

### 49 技术格式与搜索匹配

[src/cyrene/model/tool_names.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/model/tool_names.py:11)、[src/cyrene/plugins/builtin/cyrene_knowledge/store.py](/Users/syw/Documents/playground/Cyrene/src/cyrene/plugins/builtin/cyrene_knowledge/store.py:312)、[src/cyrene/workbench/webui/frontend/features/chat/context-graph-model.mjs](/Users/syw/Documents/playground/Cyrene/src/cyrene/workbench/webui/frontend/features/chat/context-graph-model.mjs:53)、[electron/backend/backend-process.js](/Users/syw/Documents/playground/Cyrene/electron/backend/backend-process.js:24)

ID、路径、MIME、URL、版本号、HTTP/SSE/OSC、PORT 输出、关键字搜索与测试输出断言。

正常协议/格式处理。扫描中的同类命中归入此类，不列为业务状态误判。构建脚本、测试与历史实验的匹配也按用途归类。

## 本次变更

本次新增本报告和机器可读清单。未执行批量修复，也未改动上一轮以外的生产代码。检查结果支持按上述优先级逐项改为结构化状态、来源与动作元数据。
