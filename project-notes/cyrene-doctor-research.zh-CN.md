# Cyrene Doctor：诊断与可恢复修复方案

日期：2026-09-07。状态：基于当前仓库的设计研究，尚未实现功能。

## 结论

建议加入统一 Doctor 服务：从失败对话、记忆任务或设置页面进入，先收集结构化证据，给出明确原因，再执行有范围、可验证、可回滚的修复。第一版以确定性检查和受控修复为主，模型只可辅助解释，不能成为 Doctor 启动或诊断的前提。

Doctor 必须在自定义插件损坏、模型不可用甚至主服务无法启动时仍有最低限度的诊断能力。因此采用“独立基础诊断模块 + 在线应用适配器 + UI/CLI 入口”；普通插件可贡献检查项，但基础诊断不能依赖被诊断插件成功加载，也不需要给 Agent Kernel 增加工具。

截图只证明界面显示了“Agent 运行失败，请重试”，无法确定是 MiniMax、工具、自定义或记忆导致。截图内的 Phaser 修改建议是对话内容，不是本次任务指令，也不是根因证据。当前未读取用户运行数据库或日志，以下结论针对代码能力和缺口。

## 当前代码依据

| 领域 | 已有实现 | Doctor 应补齐的能力 |
| --- | --- | --- |
| 对话错误 | `src/cyrene/workbench/chat/chat_application.py` 的 `chat_run_error_message` 对未覆盖异常返回截图中的通用文案；`chat_error_metadata` 提取部分分类 | 保留贯穿模型、工具、Hook、持久化和界面的 incident ID、阶段、原因及重试范围 |
| 模型错误 | `src/cyrene/model/error_details.py` 已有 `ModelErrorDetails`、`retry_scope` 和内容无关的流式诊断白名单 | 复用分类，检查跨异常包装后是否仍可获取；不要再建一份独立字符串分类表 |
| 运行生命周期 | `src/cyrene/workbench/chat/chat_run_lifecycle_service.py` 管理运行、异常、重试恢复和最终投递 | 区分生成失败、结果保存失败、后台学习失败；关联 run 和故障，避免都解释为模型错误 |
| 工具自定义 | `src/cyrene/core/plugin/customization.py` 保存名称、描述、可见性和删除覆盖，含进程内 revision | 检查覆盖与有效工具目录是否匹配，显示修改来源和作用域；描述修改只能标为待验证原因 |
| 配置恢复 | `src/cyrene/platform/config_store.py` 已有快照、配置恢复及原子 revision 更新 | 使用现有写入接口和并发控制；离线检查避免调用可能初始化或恢复配置的读取路径 |
| 可编辑插件 | `src/cyrene/plugins/native_tools.py` 提供内置插件播种和恢复能力；`project-notes/README.md` 说明安装版使用用户目录 `plugin_impl/` | 对比当前发行版基线，备份后按插件恢复，不能把全部自定义视为损坏 |
| 记忆写入链路 | `src/cyrene/plugins/builtin/cyrene_memory/service.py` 从 committed turn 归档、取得 ContextTree 证据、判断写入开关和阈值，再提取和学习 | 每阶段记录 saved/skipped/failed 及原因，区分没有触发与执行失败 |
| 项目学习任务 | `src/cyrene/plugins/builtin/cyrene_memory/project_memory.py` 已有 jobs、快照、版本提交、乐观冲突、事件和恢复入口 | 保留更细的模型错误，支持按 job 重试，检查任务状态与 Hook 投递状态是否一致 |
| 学习错误分类 | 同文件 `_error_type` 识别模型不可用、冲突、输出错误，另以 `context windows` 文本识别溢出，其余归入 internal_error | 优先消费模型结构化错误，并沿 cause 链保留原始分类，避免认证/网络等被归为内部错误 |
| 可靠 Hook | `src/cyrene/core/hook/registry.py`、`storage.py`、`src/cyrene/core/context/hook_store.py` 提供持久投递、失败、阻塞和重试 | 增加有界只读查询和按 delivery/job 精确重试；现有 `retry_failed()` 无筛选参数 |
| 记忆使用 | `src/cyrene/plugins/builtin/cyrene_memory/application.py` 的快照方法明确要求已有对话保持冻结的记忆上下文；部分读取异常记录日志后置为空 | 展示存储版本和本对话快照版本，区别正常旧快照与读取失败，不能静默替换已有上下文 |
| 调试证据 | `src/cyrene/observability/debug_event_repository.py` 读取近期事件和部分历史 JSONL，跳过不可读文件与坏行 | 不把调试日志当完整故障账本；证据缺失应显示 unknown，而不是通过 |

这些是实现依据，不代表已证明用户遇到的故障来自其中某处。

## 用户流程

1. 对话错误卡新增“诊断此问题”，携带 chatId、runId、incidentId；保留适用的重试入口。
2. 记忆页的保存失败、学习失败或长期等待状态新增“检查原因”，携带 projectId、jobId。
3. 设置新增“Cyrene Doctor”，可检查全局配置或指定项目；不要进入页面即扫描所有聊天正文。
4. 结果优先回答：哪里失败、证据是什么、影响什么、下一步是什么。状态包括“发现问题”“正常”“未触发”“无法检查”。
5. 修复前显示实际变更和影响范围；修复后重新检查，分别展示“配置已修复”“连接验证成功”“学习任务完成”，不能写入成功就宣称故障解决。

例如：

> 项目记忆学习失败：模型鉴权未通过。
> 对话回复已保存；本次项目记忆未更新。
> 操作：打开相关模型配置 → 验证连接 → 重试该学习任务。

这是目标文案示例，不是截图故障的诊断结果。

## 第一版检查范围

### 自定义与配置

- 配置文件能否读取、解密与校验；有效值来自全局、项目还是会话，关联持久 revision。
- 插件 manifest/schema、必要文件、声明的依赖、加载错误、已注册 Hook 和工具目录一致性。
- 工具名称冲突、被删除或隐藏的目标、插件已关闭而任务仍引用它。
- 自定义实现与对应发行版基线的差异；普通修改记为信息，只有加载/契约错误才直接判为故障。
- SOUL、Skills、自定义描述导致的行为问题只能通过证据和隔离对比建立怀疑，不能静态断言因果。
- 隔离诊断在独立进程/临时配置中进行。静态检查不导入任意用户代码；动态检查设超时、资源和文件/网络权限边界。不能为诊断而直接关闭正在使用的全局插件。

### 对话错误

- 关联 run 的最后成功阶段、失败阶段、模型分类、流中断摘要、工具/Hook ID、提交结果和运行恢复状态。
- 区分模型额度、认证、超时、上下文过大、工具参数错误、插件异常、数据库写入、后台任务异常。
- 查看已有副作用：是否已执行写文件、外部请求、发送等工具。不能把“重试”默认实现成整轮重放。
- 被中断的任务先核对持久状态及活跃执行者，再通过已有恢复接口处理；仅凭运行时间长不能判为僵死。
- 模型连通测试作为显式操作，使用最小无工具请求，不携带真实对话，并提示可能产生少量用量。

### 记忆保存与使用

将状态拆为“触发 → 证据快照 → 提取 → 校验 → 持久化 → 注入”。

- 分开检查归档、短期记忆、结构化记忆和项目 prompt 的实际存储位置及初始化状态。
- 区分写入关闭、阈值未到、非项目/非主 Agent、没有已提交节点、无合格候选等正常跳过原因。
- 检查项目/会话 ID 与 ContextTree 锚点对应关系，不能拿其他对话作为学习来源。
- 数据库做有界只读检查；写入探针必须显式运行并清理，不能向真实记忆插入测试条目。
- 显示保存版本、当前会话快照版本、注入是否受开关/采样/长度限制影响。已保存但未注入不等于丢失。
- 读取异常导致空上下文需留下故障证据；合法空记忆应单独表示。

### 项目记忆学习

- 关联 job 与 Hook delivery，检查 queued/running/failed/conflict/superseded/saved/unchanged 状态。
- 检查快照存在、节点有效、项目未删除、模型绑定可用、上下文预算及输出契约。
- 输出契约沿用现有 submit_project_memory 校验；不通过削弱校验来“修好”学习。
- 冲突时基于最新版本重新计算，不能强行覆盖；saved/unchanged/superseded 任务不重复执行。
- 进程重启后的 running 状态需结合执行者和持久投递确认，缺少证据时报告不确定。

## 服务与数据设计

建议新增 `src/cyrene/platform/doctor/`，包含报告契约、检查注册、证据采集、规则匹配、修复计划与执行审计。领域模块提供只读投影和受控操作，Doctor 不复制记忆业务逻辑，也不直接改写业务表完成修复。

每个 Finding 至少包括：

```text
id, check_id, severity, status, scope
summary_key, reason_code, evidence_refs, confidence
project_id, chat_id, run_id, job_id, hook_delivery_id
config_revision, plugin_fingerprint, detected_at
suggested_actions, retry_scope
```

新增有界、可过期的故障记录，保留 incident ID、失败阶段和关联 ID。记录不依赖成功启动模型，也不以完整 prompt 或凭据作为必要数据。若主要存储故障，使用独立的小型本地故障记录文件，并显式报告记录失败。

建议接口（均为待实现）：

```text
POST /api/doctor/reports                  创建指定范围的诊断
GET  /api/doctor/reports/{id}             查询结果
POST /api/doctor/reports/{id}/repair-plan 生成具体变更计划
POST /api/doctor/repairs/{id}/apply       执行已选择的动作
POST /api/doctor/repairs/{id}/rollback    回滚适用的配置/文件动作
```

API 沿用现有认证与作用域校验，不接受任意文件路径或任意 shell 命令。可扩展检查器返回数据，修复动作由宿主白名单注册。

CLI 目标：`cyrene doctor`、`cyrene doctor --project <id>`、`cyrene doctor --chat <id>`、`cyrene doctor --offline --json`。当前 CLI 主要是 daemon HTTP 客户端，因此 offline 分支必须在连接 daemon 和加载插件前分派，只做无副作用检查。

## 修复边界

| 动作 | 执行规则 |
| --- | --- |
| 定位配置、生成报告、说明正常跳过原因 | 默认只读执行 |
| 恢复单项工具覆盖或内置插件实现 | 先生成 diff 和备份，用户选择后通过现有接口执行，校验 revision/文件指纹 |
| 重载目标插件 | 明确受影响会话和后台任务，活跃任务需等待安全边界 |
| 重试学习任务 | 新增按 job/delivery 精确重试；保留原证据与关联，防止重复写入和重放无关 Hook |
| 更新模型绑定 | 用户选择配置，测试后重试；不静默换 provider |
| 使用新记忆快照 | 优先创建采用新快照的对话；显式刷新需另行设计 ContextTree 版本边界 |
| 重放对话 | 根据已执行副作用决定恢复/重试方式，不自动重复外部动作 |
| 数据库损坏 | 提供证据及备份恢复计划，第一版不做自动表修补 |

每次修复记录前置条件、备份、执行结果和复检结果。配置/文件支持回滚；网络调用和模型调用不可撤销，记忆重试也不能承诺通用回滚。报告导出默认排除聊天全文、记忆正文、密钥、授权头和敏感路径；需要详细证据时让用户预览选择。

## 分期与验收

P0：打通错误与证据关联，统一结构化分类；将记忆正常跳过、提取失败、保存失败和冻结快照明确呈现。这一步直接改善截图中的模糊报错。

P1：上线 Doctor 服务、设置页、两类故障入口和离线 CLI；支持插件/配置检查、记忆链路报告、单项配置恢复、目标插件恢复、精确学习重试和复检。可作为第一版发布范围。

P2：增加独立进程隔离对比和历史配置差异关联。Agent 辅助分析纳入第一版，具体架构以下文整体审查后的决策为准。自动二分插件只在可重现且隔离的场景运行，不能在用户正在工作的环境中试错。

集中验收用例：

1. 修改插件造成语法错误，在线或离线报告指出目标文件，恢复仅影响该插件并保留备份。
2. 模型鉴权、流中断和工具错误得到不同分类；重新打开失败对话仍能定位 incident。
3. 记忆写入关闭、阈值未到、无合格内容显示正常跳过；存储异常显示失败。
4. 项目学习失败只重试目标任务，成功任务和无关 Hook 不重放；并发修改不会被覆盖。
5. 记忆已保存但旧对话使用冻结快照，显示两个版本而非建议清空记忆。
6. daemon 不可用时离线诊断不加载自定义插件、不修改配置；证据不足显示 unknown。
7. 修复期间配置发生变化则计划失效；备份可恢复；导出不包含测试注入的凭据或正文。

实现完成、自查后再集中执行相关测试，Python 使用 `uv run pytest ...`。本次仅新增研究文档，未修改运行代码，也未运行测试。

## 整体架构复查后的实施决策

本轮进一步审查了 Python/CLI 与 Electron 启动、HTTP 组装、Application/Session 插件注册、内置与外部 Agent、模型网关、ContextTree、配置与备份、日志脱敏、前端组织和测试入口。这里的“整体”指覆盖主要架构边界及关键执行路径，不表示逐行审计全部文件。未访问运行中的私有数据，也没有复现截图故障。

### 1. 三种运行条件，共用一个报告

| 条件 | 可执行内容 | 用户得到什么 |
| --- | --- | --- |
| 主服务和 Agent 可用 | 基础检查 + 独立诊断 Agent 查询证据 + 修复计划 | 有依据的深入分析和具体操作 |
| 主服务可用、Agent/模型不可用 | 全部在线确定性检查；Agent 失败仅影响分析层 | 可能原因、证据、手动处理入口与可执行修复 |
| 主服务无法启动 | 独立 Python 离线检查；Python 本身不可执行时由 Electron 提供最小检查 | 启动阶段、退出码、路径/运行时线索、日志位置和下一步 |

基础报告先完成并显示，再按用户启用情况启动 Agent。Agent 的启动、超时、取消、模型错误单独保存在 analysis 状态中，不得把已生成的基础报告变成失败。Agent 没有结论时仍展示已有 Finding；离线状态不得误称完成在线连通性验证。

### 2. Doctor 放在宿主服务层，插件贡献可选检查

最终选择 `src/cyrene/platform/doctor/` 作为宿主诊断包，内部区分无副作用基础模块和在线适配器：

```text
doctor/
  contracts.py           报告、Finding、证据引用与修复动作契约
  service.py             有界检查调度、部分结果和状态编排
  checks/                启动、配置、插件、模型、运行、记忆检查
  evidence.py            证据白名单、强制脱敏、大小和时间范围限制
  repository.py          报告与修复审计；不替代业务数据源
  repairs.py             计划、前置条件、目标级锁、备份与复检
  agent_analysis.py      现有 AgentSession 的专用诊断组装
  cli.py                 离线分派、文本/JSON 输出
```

在线由 `workbench/http/registry.py` 组装 `DoctorApplicationService`，新增 `workbench/http/system/doctor.py` 薄路由。在线检查通过领域查询端口访问运行、插件和记忆，不能直接修改这些模块的内部状态。

记忆等插件可通过可选服务提供更深入的诊断投影；插件不可用时，基础检查仍能报告加载失败，并按已知存储契约做版本兼容的只读检查。无法识别数据版本时停止该检查，不猜测字段。

### 3. 启动失败不能只靠 WebUI

`workbench/webui/server.py:create_app` 在配置路由之前调用 `PluginApplicationHost.load_user_plugins`；其 lifespan 又在提供服务前执行插件 startup。`platform/bootstrap.py:initialize_runtime` 会建目录、初始化数据库并执行启动协调。Doctor offline 不应经过这些路径，否则可能在诊断前重复触发故障或修改数据。

`pyproject.toml` 的命令入口实际为 `cyrene.__main__:main`，不是直接进入 `cli.py`。应在 `__main__.py` 的正常启动分派前识别 doctor，直接进入轻量入口；在线模式才延迟加载 HTTP 客户端。路径可使用 `platform/paths.py` 的解析逻辑，不能调用目录初始化。

`electron/main.js` 当前在 Python spawn 错误或非预期退出时显示错误框并退出。需改成打开打包在 Electron 内的本地恢复页，例如 `doctor-recovery.html`，通过专用 preload 暴露少量固定操作。恢复页无需 Python 端口，也不能依赖 `/api` 或插件前端资源。先运行离线 Doctor 子进程；若解释器/打包可执行文件本身不可用，则由 Electron 展示退出码、可执行文件存在性、启动超时和日志线索。正常退出、更新退出码 42、用户主动重启仍保持原流程，避免误开恢复页和无限重启。

### 4. Agent 分析复用内核，但显式隔离应用状态

`core/session.py:AgentSession` 已支持传入 registry、`load_plugins=False`、专用 tree/data/workspace、调用次数上限和会话级工具，可复用其运行、取消、事件和 ContextTree 机制，不另写一套 Agent Loop。

但当前默认值并不构成隔离：

- `PluginRegistry()` 默认安装 Kernel 工具，并可能继承进程级 activation/customizations。
- `AgentSession` 的 `application_scope or application_plugin_scope()` 使显式传入 None 仍可能采用全局 scope；同目录时又可能同步应用服务与插件。
- 普通 Agent 的必需基础提示词也是用户可编辑插件 `cyrene_system_prompt`，因此不能把它作为诊断的可信前提。

实现时增加显式的会话隔离策略：禁止全局 scope 回退和自动同步；独立 activation/customization；诊断专用数据目录和 ContextTree。采用 `include_core=False` 的最小 registry，通过现有 Plugin 契约注册诊断提示词、选定模型调用适配器和查询工具，并补充兼容测试确认不依赖默认 Kernel 工具。模型路由继续复用现有网关；不能因为诊断就绕过 provider 配置、额度或权限。

诊断提示词通过专用受控 Context Plugin 发布，保持现有上下文架构。它不挂载用户 SOUL、项目记忆、Skills、MCP、自定义 Hook 或普通工作工具。`read_only=True` 只是辅助条件，真正的边界是诊断 registry 不包含 Bash/Write/任意路径 Read，且宿主端只接受范围受限的查询。

建议工具只有 `get_report`、`get_evidence`、`run_check`、`propose_repair`。模型不能直接调用 apply；`propose_repair` 只能引用宿主已注册动作和合法目标 ID。证据文本可能包含用户提示词或日志中的指令，始终作为非指令数据输入。查询调用数、总耗时、输出大小和模型用量设上限；输出须引用真实 evidence ID，宿主拒绝不存在的证据及动作。

第一版优先使用内置 AgentSession。外部 ACP Agent 的生命周期可由 `agents/runtime_service.py` 与 `process_manager.py` 提供诊断证据，但其自身工具能力不一定受上述 registry 限制，因此暂不将任意外部 Agent 直接作为修复执行器。可允许用户显式选择可用模型进行分析；不静默切换服务或上传完整日志。

### 5. 先修正证据丢失，再做原因匹配

`core/plugin/plugin.py` 的结果已包含 `error_details`；`core/plugin/runtime.py` 会调用异常的 `as_error_details()`。但 `plugins/model_gateway.py:PluginModelGateway.complete` 和 `core/plugin/model.py:RuntimeModelGateway.complete` 在结果失败时均只用文本创建 RuntimeError。

应新增/复用携带结构化详情的异常，在这两处保留 `error_details` 和 failure 分类；聊天适配器、记忆 `_error_type` 消费同一错误契约。内部异常类型保留给本地证据，公开文案继续使用安全分类。该调整既服务 Doctor，也能减少现有“内部错误”或通用“Agent 运行失败”。

对话持久事件 `workbench/chat/conversation_commit.py:ConversationTurnCommit` 已有 chat/turn/run/node ID 和稳定 event_id；复用它串联学习来源。再给 Hook 投递到项目 job 的关联补齐 delivery ID，新增按目标查询/重试端口。不能仅为 Doctor 建另一套学习队列。

Incident 应在领域边界写入，并随 ChatRun 错误事件、HTTP 错误及前端持久错误投影传递；重载页面时仍可按 ID 查到。启动异常另记录 stage。记录失败不得掩盖原始错误，也不能无限递归写故障。

### 6. 恢复与脱敏有两处需要更正初版假设

**插件恢复不是现成的强制还原。** `native_tools.py:restore_builtin_plugin` 清除删除标记后调用播种；播种刻意保留修改过的实现。Doctor 需要新增目标级“恢复发行版实现”API：解析发行版基线 → 展示差异 → 备份目标与相关 manifest 状态 → 核对指纹 → 替换目标 → 更新对应基线记录 → 重载与复检。不能直接调用该旧函数并宣称自定义损坏已修好，也不能用全目录播种代替目标级恢复。

**现有脱敏器不能直接作为离线依赖。** `platform/secret_redaction.py` 依赖 settings_store，且是否脱敏受用户设置影响；`LogRepository.create_export` 直接打包日志。Doctor 的 evidence 层需提供不读取配置的纯函数脱敏与字段白名单，默认不受日志设置影响，并从结构化字段控制输出。不能把现有日志 zip 直接作为 Agent 输入或默认报告附件。

备份可参考 `platform/backup.py` 的 SQLite backup、校验、分阶段恢复和锁机制，但单项配置恢复不能调用全量 restore。配置变更通过现有 revision 原子更新；SQLite 活库不得只复制主文件而遗漏 WAL。

### 7. 前端与交付顺序

共享报告面板放在 `frontend/features/doctor/`，由 `features/settings/index.jsx`、聊天错误 UI 和记忆页面打开同一面板；不写入 `static/app` 生成文件。报告按“基础检查结果 / Agent 分析 / 可执行操作”展示，分析取消或失败不清空其他部分。UI 新文案提供中英文。

交付按以下依赖顺序进行：

1. 错误契约保留、incident 关联、记忆阶段/跳过原因和有界查询端口。
2. 无模型 Doctor、在线报告 API、离线 CLI 和共享面板；先保证任何分析失败都有方向。
3. 隔离诊断 Agent、证据查询工具、分析事件和失败降级；纳入第一版。
4. 目标级自定义恢复、学习精确重试、备份、并发校验和复检。
5. Electron 本地恢复页，以及安装包中离线入口/资源完整性验收。

每个可交付变更自查后集中测试。重点复用 `tests/test_cli_entrypoint.py`、`test_plugin_application.py`、`test_agent_model_gateway.py`、`test_agent_session.py`、`test_hooks.py` 和已有记忆/聊天测试组织，新增真正跨边界的 Doctor 用例；Electron 增加后端不可执行、超时、异常退出、正常重启的流程测试。无需为本次纯文档研究执行测试。

## 前置修复实现（后续代码变更）

- 两个辅助模型网关使用 `ModelGatewayError` 保留结构化详情、调用 ID 和 Plugin failure；记忆错误分类与聊天展示支持沿异常链读取模型分类。
- `plugins/plugin_restore.py` 提供 `plan_builtin_plugin_restore(directory, contribution_name)` 和 `apply_builtin_plugin_restore(plan)`。计划是只读快照，执行校验目标、清单和发行版指纹；仅替换目标，保留原目标及原清单备份，常规提交失败回滚文件。返回 `backup_directory`，其中 `original` 保存原目标、`upstream-manifest.json` 保存原清单、`restore.json` 保存恢复元数据。
- 旧 `restore_builtin_plugin` 的删除项恢复语义保持不变。该前置接口本身不重载运行中的插件；现已由 Doctor UI/HTTP 展示计划并协调插件停用/重载。进程强杀不保证自动回滚，留下的备份可用于恢复。

## Doctor 实现与验收

已完成基础检查、在线 API、离线 CLI、共享面板、隔离 Agent、模型探测、目标级修复/回滚、精确学习重试和 Electron 恢复入口。使用方式及边界见 [Doctor 使用说明](../docs/doctor.zh-CN.md)。

验证结果：146 个相关 Python 用例、15 个 Electron 用例通过，前端构建与新增 Python 模块静态检查通过。隔离浏览器环境使用实际面板及 Doctor 服务，验证了 Agent 不可用时保留结果、插件语法故障识别、方案预览、备份修复、复检通过、回滚后重新识别故障。测试未调用真实付费模型，未更改用户安装的插件和记忆；尚未执行各平台发行安装包的端到端启动验收。
