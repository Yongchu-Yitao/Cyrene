## 助理可见的任务接口

个人助理只看到任务操作，不收到如何创建子会话或调度其他 Agent 的说明：

| 模型工具 | 后台操作（仅实现使用） |
| --- | --- |
| `task_start` | `session_create` |
| `task_list` | `session_list` |
| `task_inspect` | `session_context_blocks` |
| `task_load_context` | `session_load_context_blocks` |
| `task_update` | `session_send_message` |
| `task_stop` | `session_stop` |
| `task_answer_question` | `session_answer_question` |

模型参数使用 `task_id`；返回值使用 `tasks`、`task_id`、`update_id` 和 `pending_updates`。
`task_start` 必须带初始 `message`，避免仅建立空容器却没有开始工作。
任务证据接口只提供任务记录块，不提供执行端系统提示或环境挂载块。
加载证据和状态通知采用任务措辞；原始证据正文不做全文替换或伪造。
后台的持久化键、所有权、队列、消息去重和人类授权机制保持不变。
仅注册新的 `task_*` 模型工具，不保留旧工具名兼容入口。
接口字段转换只作用于已知元数据层，不修改问题参数或证据对象。
新建任务在产生持久化副作用前拒绝纯空白的任务要求。
以下章节描述后台实现接口，不是模型工具清单。

# Session Management

`session_management` 管理助理创建的任务 Session。任务使用现有 Workbench / ContextTree 运行内核，类型为 `assistant-task`，不进入普通聊天列表，也不进入 `cyrene_sessions` 的普通会话通信范围。每个工具按调用会话 ID 检查任务归属。

## 暴露方式

七个工具使用 `model_visible="assistant"`、`main_only=True`。普通 Agent 的直接工具和 Toolbox 都看不到它们，注册表也拒绝普通 audience 执行。不新增助理身份认证机制；助理入口通过 `personal_assistant_config` 配置 assistant audience 和 `extra_direct_tool_names`。需先启用并加载本包及应用服务。不要全局改成可见，也不要把助理配置传给普通聊天或任务执行 Session。

```python
from cyrene.plugins.builtin.session_management import TOOL_NAMES

config = ConversationConfig(..., extra_direct_tool_names=TOOL_NAMES)
```

Cyrene 页面通过本包的 `/api/workbench/assistant` 入口持久化全软件唯一、与项目选择无关的个人助理会话（`personal-assistant`，固定 ID 为 `assistant_global`）。首次访问创建，后续访问和重启复用同一会话。不扫描、转换其他会话或迁移任务归属。内部执行 scope 仅适配聊天内核，不加入项目列表。普通聊天不转换为助理；任务会话继续使用 `assistant-task`。左侧展示任务，正文复用正常聊天组件。侧栏接口只返回任务状态与聊天摘要，不读取或返回上下文块目录。上下文读取和切换由 Agent 工具完成，不提供侧栏手动切换接口。

## 工具

- `session_create(title, workspace_path, message?, files?)`：创建独立任务，可同时提交初始消息和文件。必须指定已存在的绝对工作目录（不存在时先创建）；继承项目、上下文选择和模型，不复制对话、记忆或授权。工具重放不会重复创建。
- `session_list()`：返回当前助理创建的任务、状态、待处理消息数、投递错误与等待用户的问题，包括空闲和停止任务。
- `session_context_blocks(session_id, block_ids?, offset?, limit?, content_offset?, content_limit?)`：默认列上下文块目录；选 ID 后分页读取正文。只提供 shared、任务正文及其消息，不提供执行端系统提示和环境挂载。
- `session_load_context_blocks(session_id, block_ids)`：将所选块保存到上下文树中的单一跨 Session 引用位置，由 ModelStart Hook 挂入模型输入；成功加载新内容会自动卸载旧快照，不修改助理当前任务文档。附来源、版本、时间和参考资料提示。源 Session 不改变；不执行历史工具调用、不提升历史指令权限、不自动同步。上限 60000 字符；超过时先分块查看。
- `session_send_message(session_id, message?, files?)`：接受文字或文件，返回持久队列回执。每次最多 10 个非空文件、合计 20 MiB，文字最多 20000 字符。相对路径相对于助理工作区；文件内容保存为内容寻址的托管附件，排队后原文件变化不影响快照。
- `session_stop(session_id)`：暂停未投递消息，取消并等待正在运行的任务结束，保留会话和产物。之后一次新的发送会恢复队列；重放旧发送不会恢复。

- `session_answer_question(session_id, question_id, answer)`：回答当前澄清问题并异步恢复执行；拒绝过期问题和非澄清类授权请求。

## 运行与恢复

消息按目标会话顺序持久化投递。目标运行且准备就绪、没有更早的待投递消息时，`task_update` 在本次工具调用内直接通过现有 runtime guidance / steer 通道接纳文字和附件，不等待后台 worker 或这一轮结束；沿用用户运行中发送消息的模型唤醒与工具边界处理。空闲、已完成或已停止的任务仍使用正常消息入口继续执行。附件沿用不可变托管快照，保留到引导记录与恢复后的上下文；Agent 输入不会变成人类授权。运行尚未就绪或正在结束时，保留同一消息 ID 重试。等待问题回答时暂存更新；助理通过 `task_answer_question` 回答澄清问题并恢复原任务，缺少信息时才在主对话询问用户。权限请求仍走现有人类授权流程，不通过 Agent 的回答工具。返回 `queued` 或 `delivered` 均不代表任务完成；通过列表和上下文块查看结果。

队列、归属和工具回执位于应用数据目录 `plugin_data/session_management/sessions.sqlite3`。应用生命周期启动单个投递 worker；重启保留停止标记与消息。重试复用收件方持久接纳 ID；永久目标错误或 12 次投递异常后标记失败。插件停用停止投递。工作区仍可共享，本包不提供文件系统写隔离。

## 主动通知

`task_notification` 在助理运行时排队，在后续处理边界读取，不打断当前模型调用；重试和重启恢复保持相同语义。`task_update` 与 `send_session_message` 仍触发 steer。

Session 的 `ContextChange` Hook 在上下文树提交后，按变更节点读取运行检查点；完成、失败、取消和等待用户处理会生成带源 Session、run、节点 ID 的通知。复用现有 `dispatch_agent_message`：助理空闲时唤醒，运行时进入现有 guidance 通道；助理等待用户时暂存，不能代替人类授权。通知回执持久化，重启重试使用同一接纳 ID。运行失败通知携带错误详情；消息投递最终失败也生成通知。现有 worker 仅重试待投递通知，不轮询任务状态。

## 个人助理工具配置

入口可调用 `cyrene.workbench.core_adapter.assistant_profile.personal_assistant_config(config)`，每次启动、恢复和通知唤醒时使用返回的配置。该配置提供本包七个工具、`ask_user`、`send_message` 和 `WebSearch`（用户所称 web_search，来自 `cyrene_content`）。核心工具自动保留，其中已包含 `switch_context`、`load_context`、`unload_context`、`append_context`、`replace_context`、`edit_context_catalog` 和 `search_context`，无需重复添加动态上下文工具包。`switch_context` 是首选的上下文切换工具，可以在一次调用中为当前任务保存检查点，并恢复已有上下文或创建新上下文。宿主需加载相应插件和应用服务；普通聊天和助理创建的任务不应用此配置。ConversationRuntime 在每次打开个人助理会话时自动应用此配置，覆盖恢复和通知唤醒。个人助理使用 `cyrene_system_prompt/assistant_prompt.py` 的独立完整的英文 system prompt，不拼接普通 Agent 的提示词，由原有 SessionStart Hook 挂载并计算缓存指纹。普通 Agent 保持原有提示词。


### 动态上下文搜索

核心工具 `search_context(query, context_id?, limit?, cursor?)` 搜索当前会话的上下文标题、摘要、正文和历史对话，包括已归档上下文与 shared。query 为 1–200 字符的非空关键词，按大小写不敏感的子串匹配；context_id 可限定一个上下文。limit 默认 10、最大 30，按命中片段总数计算。

返回 `contexts` 数组，同一上下文的命中放在一个 `matches` 数组中。每组包含 context_id、title、archived；每条命中包含 source（title、summary、body、message）、snippet，消息命中另含 role。片段保留原文及少量前后文，相邻关键词合并显示。无命中时 contexts 为空。还有结果时返回 next_cursor，后续查询保持其他参数不变；内容变化导致游标失效时重新搜索。

搜索不创建、切换、加载或修改上下文，不检索其他 Session、系统提示或工具结果。历史片段仅为参考资料。该工具与其他动态上下文工具一样，必须独立调用并等待结果。

### 动态上下文目录

核心工具 `edit_context_catalog` 独立于侧栏的 `task_organize`，提供四种 action。

- `update` 按 context_id 修改 title（最多 40 字符）或 summary（最多 200 字符）；后续卸载检查点会更新摘要。
- `archive` 将非活跃上下文移出模型每轮收到的目录，保留原 ID、正文和历史。活跃上下文需先保存检查点并切走或卸载。
- `restore` 恢复目录显示。继续执行可使用 `switch_context` 或 `load_context`，成功激活时自动取消归档，失败时保持原状态。
- `search` 按 query 匹配 ID、标题或摘要，status 可选 all、archived、unarchived。每页 10 条，使用返回的 next_cursor 继续查询。搜索仅返回目录信息。

归档状态存储在现有上下文树的任务文档中，兼容已有会话，并随成功工具事件进行分支重放。`shared` 始终常驻。模型目录仅列出未归档条目及归档总数，查找旧任务后可按原 ID 恢复。

现有 ModelStart Hook 在估算输入达到模型配置上下文容量的 30% 和 50% 时，向上下文树挂载目录整理提醒。估算包括消息及工具定义。同一任务在当前分支上的每一级提醒只挂载一次；首次检查已超过 50% 时直接给出较高级提醒。原有容量告警、共享资料快照和自动压缩继续按既有规则运行。

## 助理会话工具范围

个人助理入口声明 `PluginToolScope`，通过会话局部的 `ScopedPluginRegistry`
限制直接工具、Toolbox 发现/描述/调用及直接执行。保留除 toolbox 外的核心工具，只额外允许
本包七个工具、ask_user、send_message、WebSearch；不开放这些工具所属包的其他工具。
视图跟随宿主插件更新，不改变全局启用状态、模型 Provider 或生命周期插件。
普通会话和委派任务仍使用原注册表；普通 Agent 的工具列表不会新增 session_management。
通知 Hook 仅安装到已登记的委派任务，普通会话不安装。

助理白名单内的工具均直接暴露；toolbox 在助理会话中既不可见也不可调用。普通 Agent 的 Toolbox 保持原有行为。

### 助理卡片工具

`cyrene_cards` 按相同的 assistant audience 与 main-only 机制提供 `card.create`、
`card.update`、`card.get`、`card.list`、`card.pin`、`card.archive`，由助理 profile 直接暴露。
普通聊天与委派任务不获得这些工具。创建时可选择模板或自定义 HTML，并设置初始固定；
后续读取、修改及用户输入协议见相邻 `cyrene_cards/README.md`。当前实现为后端数据工具包，
尚未接入生产前端渲染和用户事件自动唤醒。


### 个人助理定时任务

个人助理直接暴露 `schedule.create/list/edit/pause/resume/cancel/runs`，可直接设置提醒及周期任务。宿主需加载 `cyrene_schedule`。个人助理及其委派子会话创建的定时任务统一归属 `__personal_assistant__`；子会话通过运行上下文中的 `assistant_owner_session_id` 保留归属。来源会话仍单独记录，用于结果投递。

所有项目日程合并显示该全局范围的任务及日历事件；从任一项目编辑、暂停、删除或读取运行历史都操作同一条全局任务。全局汇总与搜索按任务 ID 去重，不复制任务，不增加调度执行次数。普通项目创建的任务仍只属于该项目。

### 助理侧栏任务组

每个已有执行 task 在左侧显示为普通对话组样式的可折叠分组，包含任务对话、交付文件、终端和所属浏览器标签。折叠偏好保存在客户端；搜索会临时展开。
`task_organize` 暂停注册，不向任何模型或工具发现入口提供；实现与历史数据保留。恢复注册后可更新组标题、position（较小值靠前）、archived、hidden_resources（kind:key）及 attachment_ids。附件 ID 必须来自主助理已交付的附件；数组参数替换原选择，省略的字段保留。`task_list` 返回 presentation 和已关联 files，供助理保留旧选择。展示设置持久化在同一插件数据库的 task_presentation 表，操作仍检查任务归属并复用工具回执。归档不停止执行，可通过“显示已归档任务”找回。组操作不切换动态上下文、不创建会话、不改变资源所有者。
