# Cyrene 当前能力基线：Agent 与用户功能

核查日期：2026-09-20。项目版本：0.9.0-beta20。

后续专项验证发现：子 Agent 无进展检测虽然有实现，但会误停正常工具发现，并存在漏判及全量持久化开销。详见[行为与性能实验](experiments/subagent-progress-2026-09-20/REPORT.zh-CN.md)。本文中的“已实现”不能作为该机制效果可靠的证明。

后续实施：已按用户要求移除执行型子 Agent 的无进展检测、结果指纹、计数和配置。下面涉及该机制的内容保留为移除前调查记录；当前仅保留工具次数、时间、费用和上下文资源限制。旧检查点的 `execution_no_progress` 执行限制在读取时解除，终态历史保留。

## 范围与判定方法

本记录用于后续功能决策，防止把已有能力换个名字再次提出。依据当前工作区，而不是只依据 README 或发布版本。核查时工作区已有未提交修改，包括 `core/retry.py`、运行恢复和前端变更；这些仅代表本地当前实现，不能直接宣称已经发布。

- **实现已核对**：读取了核心实现、调用关系或协议，不只是看到名字。
- **入口/定义已核对**：发现插件注册、工具声明或文档，但不代表所有平台和真实服务已验证。
- **定向测试通过**：本次实际运行的测试；大量测试使用隔离状态和可控响应，不等同真实模型端到端成功率。
- **未确认**：未实测或未完成跨模块追踪；不能直接归类为缺失。

已扫描内置插件目录、核心运行时、相关 Workbench 服务、工具定义、近期更新记录和测试。附录列出全部内置插件包入口。没有逐行审计全部源码，也没有连接真实账号、远程设备、Office 或 Android 真机。

## 1. Agent 执行闭环

| 能力 | 当前实现 | 主要依据 |
|---|---|---|
| 连续运行 | 模型、工具、等待用户、恢复、取消在 AgentSession 中组织；状态持久化 | `src/cyrene/core/session.py`、`restore_decision.py` |
| 工具发现 | 核心直接工具及可选直接暴露；其余通过 toolbox list/describe/invoke 发现 | `src/cyrene/core/plugin/core_impl/toolbox.py`、`plugins/builtin/cyrene_system_prompt/system_prompt.py` |
| 工具批量执行 | 批次内允许并行的工具并行执行，顺序工具形成边界；并行上限受控 | `src/cyrene/core/plugin/batch_runner.py` |
| 中途纠正 | 持久化用户引导，未执行批次可因新指导跳过，后续按新要求继续 | `plugins/builtin/cyrene_guidance/`、`core/plugin/batch_runner.py` |
| 提问和授权等待 | 独立提问/权限路径，恢复时识别未解决问题 | `plugins/builtin/ask_user.py`、`core/restore_decision.py` |
| 有依赖的计划 | 进入计划模式；批准计划持久化；每步重读最新计划，未满足前置依赖禁止启动 | `plugins/builtin/cyrene_control/enter_plan_mode.py`、`update_plan_progress.py` |
| Goal 持续执行 | 协商目标、约束、范围与验收标准；确认、暂停、恢复、终止、手动接受 | `plugins/builtin/cyrene_goal/service.py` |
| 独立验收 | 新的只读 Reviewer 上下文，逐项匹配标准和证据；不接受执行者仅口头宣布完成 | 同上 `_review_prompt`、`_normalize_review` |
| 审查失败后反思 | fail → reflecting → DeepReflect 已提交 → executing；反思没有持久提交不会直接跳过 | 同上 `_apply_review`、`_reflect` |

重要边界：Goal Reviewer 检查已有文件和执行证据，提示词明确不让其重新运行命令、测试或构建。“独立审查”不等于另起一个环境复现实验。

## 2. 纠错、换策略与异常恢复

这不是待新增的一块空白能力。

### DeepReflect 的实际内容

`plugins/builtin/cyrene_control/deep_reflect.py` 定义了独立反思工作过程，输出结构包括：

- 用户实际目标和硬约束；
- 已验证事实和仍有效的已完成工作；
- 带证据、置信度的失败诊断；
- 应丢弃的假设；
- 一个实质更好的方向和可执行下一步；
- 可观察的完成检查；
- 先前尝试、结果与教训。

它保留用户原话，重构提供给 Agent 的上下文；测试覆盖反思后继续工作且不改写公开历史。反思工作者有意只读取用户可见对话，不把工具轨迹和内部推理直接全部送进去。因此不能宣称它独立核验了所有工具证据。

### 各层恢复不能混为一谈

| 层次 | 已有机制 | 边界 |
|---|---|---|
| 网络、模型和协议 | 分类错误、无效或截断响应的有界恢复 | 技术恢复不等于改换业务方法 |
| 内部执行异常 | 当前工作区新增有界 runtime retry，嵌套重试共享耗尽状态，取消和类型化异常保留自己的策略 | 本地未提交实现，不视为已发布 |
| 工具不可用 | 结构化 PluginFailure 和运行内熔断；恢复后可以从持久结果重建 | 熔断用于避免重复调用已失败能力 |
| 方法失效 | DeepReflect 诊断与上下文重构；Goal 审查失败自动接入 | 不能由此推断任意普通对话都有同一个全局自动触发器 |
| 子 Agent 无进展 | 工具签名和结果指纹去重、no-progress 计数、达到上限后要求 partial/blocked 收尾 | 这是启发式检测，不是语义上判断所有工作的进展 |
| 多 Agent 讨论重复 | 消息指纹、无新信息轮数和讨论预算 | 字面新颖不等于论证有效 |
| 应用或插件故障 | Doctor 收集证据、分析责任、生成修复、验证、应用及回滚 | 不等于自动重放整个原任务 |

主要源码：`core/retry.py`、`core/plugin/circuit.py`、`plugins/builtin/cyrene_subagent/manager.py`、`platform/doctor/`。

## 3. 上下文与长期工作

- ContextTree 持久化上下文、工具结果、Hook 和执行节点。
- 按工作目标管理任务上下文，支持暂停、恢复和共享约定；`unload_context`、`load_context`、编辑上下文等已有核心工具。
- 切换任务隔离消息；恢复保留工具名称和裁剪后的参数，不把旧工具结果重新当成待执行操作。
- 容量提醒、上下文压缩、只读快照和后续分段；大资料可以以可校验文件引用保留。
- DeepReflect 与压缩是不同机制：前者调整理解和行动方向，后者控制上下文体积。
- 输入框选中的工作区、附件、MCP、Skill，以及当前分屏和固定资源，会参与上下文组装。
- 固定资源有全局分享语义，不能把所有资料一概描述成完全项目隔离。

依据：`core/context/tasks.py`、`capacity.py`、`compaction.py`、`core/plugin/core_impl/context.py`，以及 composer/split/pinned 上下文插件。

## 4. 多 Agent 协作

已核对 `cyrene_subagent/definitions.py`、`contracts.py`、`manager.py`、`runtime_policy.py`。

- **Execution**：独立执行任务，可传明确成功标准，可选择 secondary 模型路线。
- **Discussion**：主持人与参与者、定向消息及广播、按轮次和消息限制讨论。
- **Summary**：内部汇总工作者，保留证据与分歧，不开启新的任务。
- 每个子 Agent 有独立 ContextTree 和持久状态；Inbox 有恢复、去重和通信边界。
- 执行模式限制工具次数、耗时、费用估算、上下文和无进展轮数；周期性要求复查验收条件。
- 讨论模式限制总轮数、单人和总消息数、消息长度、工具次数、耗时、无新信息轮数。
- `quit` 是明确终止协议；区分 completed、partial、blocked。存在成功标准时，完成需逐项提交证据。
- 主 Agent 取消或失败可联动收拢子 Agent；恢复避免重复注入任务。

关键边界：当前构造子 Agent 时传入主 Agent 的同一个 workspace。上下文隔离不等于文件系统隔离。`resource_effects` 主要描述资源访问和展示，不应当被误读为 worktree 合并系统。

另需纠正：并非所有模式都允许自由互发消息，Execution 模式的运行策略禁止 peer messaging。

## 5. 记忆与技能学习

### 记忆

- SOUL 人格、短期记忆、项目记忆、结构化记忆、历史对话归档与召回。
- 用户可以搜索、编辑、停用记忆；项目记忆有修订、恢复和学习任务状态。
- 学习与成功提交的用户轮次关联；重试需处理原轮次学习依据，避免旧结论重复沉淀。
- 项目记忆学习处理并发冲突、失败重试和来源撤销。
- 主动工作的合成输入与输出可关闭学习，避免系统自我强化。

依据：`cyrene_memory/project_memory.py`、`structured.py`、`steward.py`、`application.py`。

### 技能学习

已存在的范围明显超过“安装一份 SKILL.md”：

- 生命周期 Hook 捕获工具操作和轮次；工具执行后持久保存，轮次结束再索引。
- 浏览器用户事件可参与学习；有敏感行为过滤。
- 提取工作流候选、归并和参数化、候选决策。
- 安装技能与已学习技能均可检索、读取、调用。
- 支持工具链和脚本形式；输入参数校验、运行记录、成功失败统计。
- 技能版本、修订补丁、批准/拒绝、回滚、激活与废弃。
- 用户纠正反馈可被识别；脚本或高风险步骤不会无条件自动重放，而是回到正常权限路径。

依据：`cyrene_skills/learning_capture.py`、`orchestrator.py`、`candidate.py`、`lifecycle.py`、`application_service.py`、`run_learned_skill.py`。

因此，“任务变模板”“失败后积累经验”“给技能加版本回滚”不能未经进一步区分就称为新增功能。

## 6. 主动工作、事项和自动触发

- Entity 可以持久跟踪任务、问题、决策、项目、事件、习惯等，具有状态、截止时间、优先级、关联和来源。
- 主动工作围绕明确登记且仍活动的 task/problem；不是把所有记忆和兴趣自动变为任务。
- 执行前核实是否已经完成、取消或被替代；同一事项版本去重，阻塞时说明具体缺失信息。
- 主动完成可明确选择报告或静默结束，具有冷却与去重机制。
- Schedule 支持 once/interval/cron，时区、运行历史、租约、暂停/恢复/取消和结果投递。
- Hook 包括工具前后、会话开始结束、每轮开始、模型开始、上下文变化与用量、轮次提交和停止事件。
- 用户 Hook 有自然语言配置生成、测试、编辑、启停、审核和记录入口。

依据：`cyrene_entity/`、`cyrene_proactive/service.py`、`cyrene_schedule/`、`core/hook/hook.py`、`cyrene_plugin_development/`。

边界：生命周期 Hook 不自动等于邮箱、目录变化、外部 Webhook 都有现成触发器。需要逐种验证。Schedule 的执行成功和渠道投递成功也是不同状态；此前发现调用方未使用 `deliver()` 的状态返回值，属于交付可靠性核查项，不能抹掉已有调度能力。

## 7. 工具与用户功能全貌

| 领域 | 当前功能 | 验证程度/主要条件 |
|---|---|---|
| 编程与本地执行 | 读写编辑、glob/grep、持久终端、命令输出、Git/变更、符号与代码分析、构建测试运行预览 | 注册/定义与相关服务已查；未逐语言实跑 |
| 项目类型 | JavaScript/TypeScript、Python、Go、Rust、Java、TeX、Make、GitHub | 插件入口已查 |
| 内容与联网 | 多源搜索、自定义搜索源、正文读取、附件分析、大结果分页读取 | 定义/文档已查 |
| 知识与文献 | 解析/OCR、混合检索、文献元数据、集合标签、笔记批注、导入导出、Zotero 本地导入 | 检索实现已查、后端定向测试通过 |
| 浏览器 | 标签、导航、snapshot/ref、可信操作、滚动等待截图、上传、接管；子 frame/开放 Shadow DOM 补充路径 | Electron deep DOM 与 Python 路径已查，复杂站点未实测 |
| 本机桌面 | app_use 与语义 snapshot/inspect/click/type/scroll/drag | 注册已查；系统权限与平台依赖未逐一实测 |
| Cyrene 自控制 | 可见界面操作、部分类型化设置、项目/聊天/窗口/数据/更新相关控制入口 | 受 UI 路径、人类专属操作和权限规则约束 |
| Office | 实时 PowerPoint 和 PPTX 文件模式；图形、原生图表表格、页面、检查渲染与撤销 | 文档和插件入口已查；未连接 Office |
| 媒体 | 图片生成编辑、视频、音乐，后台任务、重试、附件投递和完成后唤醒 | 管理器定向测试通过；未调用付费服务 |
| 语音 | ASR/TTS、语音设置、语音指令进入对话 | 实现入口已查；未测麦克风与模型 |
| 地图 | 地点搜索、路线计算 | 插件定义已查 |
| 回答呈现 | details/card/chart/button/layout 合同，声明式交互图表与操作按钮 | `cyrene_renderer/load_contract.py` 已查 |
| 消息与交付 | 对话消息、文件、桌面和 Webhook 通知、Telegram/微信 | 工具声明与调度投递已查；未连接账号 |
| 远程 Agent | 设备配对、共享项目、文件传输、远程任务、聊天/Goal 操作、直接调用获授权插件 | 工具实现及远程控制定向测试已查 |
| 远程桌面 | 已配对设备桌面查看控制、多显示器、音频/文件等平台能力 | 文档与入口已查；非真机验证 |
| 扩展 | MCP 动态工具、CLI 环境、插件创建校验安装重载、模型 Provider、用户 Hook | 注册和管理定向测试已查 |
| 运行维护 | Doctor、备份恢复、更新、预算、模型检查、日志与 Context/Run 可观测性 | 相关实现/文档已查；未完整恢复真实数据 |
| 客户端 | Workbench/Electron/CLI；Android 本地运行、原生浏览器、主动休眠恢复 | Android 为实验能力，不能承诺桌面等价性能 |

远程控制不仅是看屏幕：`RemoteHarness` 能直接调用远端已授权插件，`RemoteCyreneAction` 能管理远端对话和 Goal。远端执行也已经存在，不能作为新的 Agent 层功能重复建议。

## 8. 已知文档偏差与尚未确认项

1. 限制文档写浏览器主要覆盖顶层 DOM，但 `electron/browser/browser-deep-dom.js` 与 Python browser runtime 已有开放 Shadow DOM 和嵌套路径。应依据具体后端和动作说明边界，不能笼统说“不支持 iframe”。
2. 限制文档写 Chat 需要 OpenAI-compatible endpoint，但模型插件注册已有 Anthropic、Gemini、Ollama、local ONNX 等独立适配。不能照抄为现状。
3. 历史架构文字容易把所有子 Agent 都描述为同一种消息协作模式；当前 contracts/runtime_policy 已明确区分三种模式。
4. 文献 DOI/标题查找、Zotero Web 双向同步、Experiments/Manuscripts 的未实现状态来自文档；若后续作为选题，需要再做具体代码入口核验。
5. 没有实测全局“任何卡住均自动识别并换方法”，也不能反过来断言缺失；已明确实现的 DeepReflect、Goal 反思链和子 Agent 熔断应作为分析前提。
6. 未验证所有真实服务、平台、授权和长时故障组合；实现存在与用户成功率是两种不同证据。

## 9. 本次实际测试

第一组：76 passed，3.22s。

`test_deep_reflection.py`、`test_conversation_goal.py`、`test_task_contexts.py`、`test_context_capacity.py`、`test_subagents.py`、`test_behavior_learning.py`、`test_runtime_retry.py`。

第二组：112 passed，3.43s。

`test_plugin_failure_circuit.py`、`test_plugin_guidance.py`、`test_proactive_workbench.py`、`test_schedule_plugin_runtime.py`、`test_memory_plugin.py`、`test_knowledge_plugin_backend.py`、`test_media_manager.py`、`test_remote_control.py`、`test_plugin_authoring_management.py`、`test_split_context_plugin.py`。

合计 188 项定向测试通过。没有运行全部测试、前端交互测试、真机测试或真实模型评测。测试针对本次工作区快照，其他正在进行的改动可能使后续结果变化。

## 10. 后续功能建议的约束

不得再将反思换策略、独立验收、多 Agent 讨论、任务上下文切换、技能学习与版本回滚、主动跟进、事件 Hook、远端 Agent 执行，笼统当成 Cyrene 尚缺的功能。

新的建议必须同时说明：现有能力在哪里结束；用户遇到什么具体不能完成的情况；新增的是哪个机制；如何用可复现任务证明价值。当前记录不据此强行提出下一项功能。

## 附录：全部内置插件包入口

静态枚举得到 45 个包含 `__init__.py` 的内置插件包目录。下表描述来自 PluginPack 声明；仅表明源码入口，不保证当前用户配置已启用。

| 插件包 | 声明职责 |
|---|---|
| [cyrene_application](../src/cyrene/plugins/builtin/cyrene_application/__init__.py) | Inspect and control the local Cyrene application. |
| [cyrene_browser](../src/cyrene/plugins/builtin/cyrene_browser/__init__.py) | Navigate and interact with browser sessions. |
| [cyrene_channels](../src/cyrene/plugins/builtin/cyrene_channels/__init__.py) | Configure and run Telegram and WeChat messaging channels. |
| [cyrene_cli](../src/cyrene/plugins/builtin/cyrene_cli/__init__.py) | Install CLI tools and connect approved commands through tree-local Hooks. |
| [cyrene_code](../src/cyrene/plugins/builtin/cyrene_code/__init__.py) | Persistent terminal sessions and durable command jobs, workspace build/test actions, code analysis, Git, and symbol indexing. |
| [cyrene_composer_context](../src/cyrene/plugins/builtin/cyrene_composer_context/__init__.py) | Validate and mount context explicitly selected in the message composer. |
| [cyrene_content](../src/cyrene/plugins/builtin/cyrene_content/__init__.py) | Attachment, paged-result, and web content access. |
| [cyrene_context](../src/cyrene/plugins/builtin/cyrene_context/__init__.py) | Mount required run-scoped ephemeral conversation metadata. |
| [cyrene_control](../src/cyrene/plugins/builtin/cyrene_control/__init__.py) | Plan, reflect, and update plan progress. |
| [cyrene_delivery](../src/cyrene/plugins/builtin/cyrene_delivery/__init__.py) | Deliver messages, files, and notifications. |
| [cyrene_desktop](../src/cyrene/plugins/builtin/cyrene_desktop/__init__.py) | Inspect and interact with desktop applications. |
| [cyrene_entity](../src/cyrene/plugins/builtin/cyrene_entity/__init__.py) | Track, search, update and delete durable entities. |
| [cyrene_extensions](../src/cyrene/plugins/builtin/cyrene_extensions/__init__.py) | Inspect and manage external integrations and runtime environments. |
| [cyrene_goal](../src/cyrene/plugins/builtin/cyrene_goal/__init__.py) | Propose durable Goals and submit completed Goal results and evidence for independent review. |
| [cyrene_guidance](../src/cyrene/plugins/builtin/cyrene_guidance/__init__.py) | Apply durable user guidance to an active Agent run. |
| [cyrene_image](../src/cyrene/plugins/builtin/cyrene_image/__init__.py) | Generate image assets and attach their results. |
| [cyrene_knowledge](../src/cyrene/plugins/builtin/cyrene_knowledge/__init__.py) | List and search project knowledge documents and literature, and maintain verified library metadata. |
| [cyrene_map](../src/cyrene/plugins/builtin/cyrene_map/__init__.py) | Search places and calculate map routes. |
| [cyrene_mcp](../src/cyrene/plugins/builtin/cyrene_mcp/__init__.py) | Connect configured MCP servers and expose each server as a Plugin pack. |
| [cyrene_media](../src/cyrene/plugins/builtin/cyrene_media/__init__.py) | Start asynchronous media generation. |
| [cyrene_memory](../src/cyrene/plugins/builtin/cyrene_memory/__init__.py) | Inject, learn, recall, save, search, and retire memories. |
| [cyrene_model](../src/cyrene/plugins/builtin/cyrene_model/__init__.py) | Editable model providers and discovery adapters. |
| [cyrene_office](../src/cyrene/plugins/builtin/cyrene_office/__init__.py) | Inspect, edit, render, and compose PowerPoint presentations. |
| [cyrene_plugin_development](../src/cyrene/plugins/builtin/cyrene_plugin_development/__init__.py) | Scaffold, validate, install, reload, manage, and edit Cyrene Plugins and PluginPacks, including standalone tools, providers, application integrations, source files, and Hooks. |
| [cyrene_proactive](../src/cyrene/plugins/builtin/cyrene_proactive/__init__.py) | Run optional proactive Agent work on an editable background policy. |
| [cyrene_project_github](../src/cyrene/plugins/builtin/cyrene_project_github/__init__.py) | GitHub repository integration for workspace projects. |
| [cyrene_project_go](../src/cyrene/plugins/builtin/cyrene_project_go/__init__.py) | Go project detection and workspace actions. |
| [cyrene_project_java](../src/cyrene/plugins/builtin/cyrene_project_java/__init__.py) | Java project detection and Maven or Gradle workspace actions. |
| [cyrene_project_javascript](../src/cyrene/plugins/builtin/cyrene_project_javascript/__init__.py) | JavaScript and TypeScript project detection and workspace actions. |
| [cyrene_project_make](../src/cyrene/plugins/builtin/cyrene_project_make/__init__.py) | Makefile project detection and workspace actions. |
| [cyrene_project_python](../src/cyrene/plugins/builtin/cyrene_project_python/__init__.py) | Python project detection and workspace actions. |
| [cyrene_project_rust](../src/cyrene/plugins/builtin/cyrene_project_rust/__init__.py) | Rust project detection and workspace actions. |
| [cyrene_project_tex](../src/cyrene/plugins/builtin/cyrene_project_tex/__init__.py) | TeX project detection and PDF build actions. |
| [cyrene_remote](../src/cyrene/plugins/builtin/cyrene_remote/__init__.py) | Inspect paired Cyrene devices, transfer files, run remote jobs, invoke granted Plugins, and start or control remote Agent work. |
| [cyrene_remote_desktop](../src/cyrene/plugins/builtin/cyrene_remote_desktop/__init__.py) | Secure cross-platform remote desktop sessions for paired Cyrene devices. |
| [cyrene_renderer](../src/cyrene/plugins/builtin/cyrene_renderer/__init__.py) | Load Workbench renderer contracts. |
| [cyrene_schedule](../src/cyrene/plugins/builtin/cyrene_schedule/__init__.py) | Create, manage, execute, and inspect durable scheduled tasks. |
| [cyrene_skills](../src/cyrene/plugins/builtin/cyrene_skills/__init__.py) | Install, inspect, load, and run Cyrene skills. |
| [cyrene_soul](../src/cyrene/plugins/builtin/cyrene_soul/__init__.py) | Mount the enabled SOUL.md persona directly below the system prompt. |
| [cyrene_split_context](../src/cyrene/plugins/builtin/cyrene_split_context/__init__.py) | Mount a bounded description of the current conversation's visible Workbench split when each user turn starts. |
| [cyrene_subagent](../src/cyrene/plugins/builtin/cyrene_subagent/__init__.py) | Spawn and coordinate Cyrene subagents. |
| [cyrene_system_prompt](../src/cyrene/plugins/builtin/cyrene_system_prompt/__init__.py) | Mount the base Agent instructions at the start of system context. |
| [cyrene_user_language](../src/cyrene/plugins/builtin/cyrene_user_language/__init__.py) | Mount the user's current language preference for Agent replies. |
| [cyrene_voice](../src/cyrene/plugins/builtin/cyrene_voice/__init__.py) | Provide speech recognition, synthesis, and voice settings. |
| [pinned_topbar_context](../src/cyrene/plugins/builtin/pinned_topbar_context/__init__.py) | Mount the latest globally pinned Workbench resources when each user message starts a turn. |

另有目录外独立工具：`ask_user.py`、`edit.py`、`glob.py`、`grep.py`；核心工具与上下文操作位于 `src/cyrene/core/plugin/core_impl/`。MCP 动态服务、用户安装插件和远端授予能力不属于固定内置包清单。
