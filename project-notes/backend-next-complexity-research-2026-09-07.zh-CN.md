# 下一阶段后端复杂度研究

基于 2026-09-07 当前工作区，包括尚未提交的上一轮重构。研究方式：读取真实实现、重新采集 Python AST 指标、追踪状态赋值与现有测试。未修改业务代码、未调整预算、未运行测试；以下测试名称表示已有覆盖线索，不表示本轮新验收通过。用户已确认分屏人工验证通过，补足上一轮报告中的该项交互验证缺口。

## 结论

后端下一轮最值得做的是：发送阶段结果显式化、恢复决策与副作用分离、提交投影规则收敛，最后再收敛会话运行状态的写入入口。不要以文件行数排序直接重写 AgentSession，也不要再次提取已经存在的 TransitionDriver 或回复 finalization 服务。

性能继续采用用户确认的取舍：允许有明确维护收益的微小开销；不以减少功能、降低刷新频率、改变回调背压或放宽持久化保证换取指标。对高频模型片段和数据库写入次数仍需单独约束。

## 当前证据

| 对象 | 当前指标 | 需要解决的问题 |
|---|---|---|
| AgentSession | 116 方法、46 个被直接赋值的字段 | 运行状态在多个业务入口协调 |
| _restore | 250 行、42 决策点 | 判断恢复路径、写节点、发布状态、排队混合 |
| _advance | 208 行、46 决策点 | 模型尝试、指导、取消检查与提交交织 |
| _continue_tools | 190 行、48 决策点 | 工具结果、权限等待、指导与下一跳交织 |
| _finish_terminal | 135 行、47 决策点 | SessionEnd、后续指导与最终状态共同决定结束 |
| _SendOperation | 37 方法、43 字段 | 大量字段在构造后才被前序阶段初始化 |
| _load_chat | 155 行、64 决策点 | 读取、命令解析、绑定、权限及上下文规则集中 |
| _dispatch_builtin | 116 行、13 决策点 | 长度不等于规则复杂；不应优先只为拆行数而拆 |

赋值面扫描发现：`_current_user_request` 在 9 个方法赋值，`_leaf_id` 和 `_run_permission_user_request` 各 6 个，`_model_calls` 5 个，`_current_run_id` 4 个（包括构造）。Send 的 `message/public_message/command` 各在 3 个阶段赋值。

这些是 AST 中直接 `self.x = ...` 的方法计数，包含增强赋值，不覆盖集合 add/pop、字典下标、别名或 setattr，不能等同于完整写入面，也不是内存或性能指标。

## 1. 第一优先：发送流程的阶段合同

源码：`src/cyrene/workbench/http/workbench/chat_routes/run_send_routes.py:170`。

现有流程按顺序执行：解析请求 → 读取会话 → 项目/模型准备 → 用户消息准备 → 落盘 → 插件工作流启动 → 附件注册/标题任务 → 分发。这个顺序本身清楚，应保留；问题是阶段通过大量随时增加或覆盖的 self 字段交流。

具体例子：`_load_chat` 将原始消息解释为命令；`_prepare_user_turn` 在重试时重新从历史用户消息恢复正文、命令与附件。这不是单纯的重复解析，重试的权威输入来源不同。项目阶段还会修改 chat 中的 workspaceOverride、上下文激活和远端设备。

建议分两步：

- 先建立 `ResolvedSendContext` 一类明确结果，包含绑定、项目、工作区、模型选择、解析后的上下文。阶段函数返回完整结果；错误沿用当前错误码与语言，不用宽泛 Optional 字段填充“半准备完成”的对象。
- 再建立 `PreparedUserTurn`，明确有效正文、公开正文、命令、附件、用户消息引用和 retry cut 信息。正常发送与历史重试各自产生该结果，再进入共同的后续阶段。

名字仅为设计建议。只读 dataclass 不自动保证嵌套 dict/list 不变；必须说明哪些保留旧对象引用、哪些确实需要复制。不要为了宣称不可变而对整段聊天历史重复深拷贝。

第一刀不改变提交和回滚。现有工作流失败先恢复 base_chat，再恢复重试状态文件；回滚失败有专门 `workflow_rollback_failed` 响应。标题任务与附件注册在工作流成功之后发生，不能前移。

验收要求：阶段消费者不再依赖未声明的 self 属性；同一命令规则只在确实共用语义的地方复用；普通/重试/动态命令/外部 Agent/附件/模型恢复/上下文不可用逐项对比结果、错误和副作用顺序。已有 `test_failed_plugin_workflow_atomically_restores_the_user_turn` 可作回滚合同基础。

## 2. 第二优先：恢复决策与执行分开

源码：`src/cyrene/core/session.py:1711`、`:1778`、`:1848`。

恢复包含至少这些有顺序的路径：取消、待用户答复、压缩/反思、未完成工具批次、未完成上下文挂载、未触发模型的用户输入、未完成 SessionEnd、继续模型、空闲。该顺序是行为合同，不能随意改成按 role 查表。

建议在现有叶节点选择和运行上下文恢复之后，提取只读恢复判断，返回带类型的 `RestoreDecision`。它可以表达状态、原因、所需动作（advance/tools/finish_success、重触发挂载、无需动作）；执行层仍调用原 `_set_state`、store.update_node 和 `_enqueue_transition`。

注意：判断本身依赖 store 查询，例如是否已有对应 assistant、工具 batch 结果、上下文源节点。不能假设仅看 leaf.value 就能决定一切；也不应一次性多读整棵树“换纯函数”。可先按旧顺序读取必要事实，再将有足够输入的判断部分纯化。

特别保留：assistant 有工具调用但已存在结果节点时的原有返回行为；context source 缺失与存在时更新不同节点；反思恢复目标文本；已取消运行绝不复活；已完成 transition 不重复执行。

验收：构造每种恢复分支的持久化树，对比状态事件、节点更新、排队类型及次数，而不只比 snapshot。已有 `test_session_restores_tree_and_does_not_repeat_completed_transition` 是起点，不足以单独证明全部恢复分支。

## 3. 第三优先：统一提交数据，保留入口策略

源码：send `:1209`、answer `:634` / `:665`、`chat_reply_finalization_service.py:68` / `:131`，以及 `chat_repository.py:118`。

当前已有 `ConversationTurnCommit`、`ChatReplyFinalizationApplicationService` 和 repository 的聊天写入/commit outbox 原子边界。应使用这些已有边界，不建立第二套事务或提交总线。

Send 内置回复、Answer、外部回复和后台唤醒重复处理部分时间线、模型/用量字段、pending、activePlan、公开消息，但有重要差异：

| 入口 | 已确认差异 |
|---|---|
| 普通 Send | 重试截断；计数基于发送前值；command 与 side-agent 参与计数 |
| Answer | 原问题 turn_id 延续；清除已回答 pending；计数及 host origin 收尾有自己的路径 |
| 外部回复 finalization | 外部投影去重与 runtime timeline 兼容处理 |
| 后台唤醒 | pending/outcome 发布方式不同；agent-originated 对轮次计数有特别规则 |

第一步只统一确认相同的模型/用量和提交事件投影。后续采用明确的调用入口或有限策略记录来承载差异，不增加大量布尔参数的万能 finalize。`saved/awaiting_user/settled` 必须保留原发布顺序与落盘先后关系。

验收包括聊天写入与 outbox 原子性、失败不留下半条消息、retry 不额外计数、pending 问题身份、消息 ID 去重、通知次数。已有测试：`test_chat_write_and_conversation_commit_outbox_are_atomic`、`test_invalid_commit_event_rolls_back_the_public_chat_write`、`test_retry_question_identity_survives_resume_without_incrementing_turn_count`，以及 finalization 的历史双投影兼容测试。

## 4. 第四优先：收敛运行状态写入，保留锁边界

源码：`session.py:1247`、`:1270`、`:4269`；`transition_driver.py`。

已有 `_linearized_context_commit` 将持久化提交与取消串行化，并延后本会话的节点事件投影到锁释放之后，防止监听器重入取消打断提交。已有 TransitionDriver 管理线程、队列和活跃 task；它没有持有整个宿主对象，方向是合理的。

下一步先给 begin/restore/retry/cancel/complete 列出“会一起变化的字段”和合法状态转换，逐个收口到现有 state lock 内的语义操作。判断同步生命周期一致之前，不把全部字段塞进单一对象并新增锁。`current_user_request` 与权限授权文本并不总相同，不能合并。

可单独为 TransitionDriver 增加取消指定 run 的窄操作，减少 AgentSession 对 loop/task/pending 的直接读取；但必须先对照现有 condition 获取、取消投递和 pending 检查的时点，不能顺手扩大临界区或改变两把锁的顺序。

这项最后做，风险高于前两项。已有 `test_cancel_between_assistant_commit_and_success_finish_is_terminal` 必須保留，并补事件监听器重入、旧 run 取消不影响新 run、close 与排队竞争、取消后 late result 不覆盖终态的确定性同步测试。避免靠 sleep 等概率触发竞争。

## 更广的后端热点

全 src 扫描还发现 legacy knowledge migration 114 决策点、desktop execute_app_use 110、MiniMax video 107、模型配置规范化 90、CodexAppServer.complete 78。这是后续清单，不是本轮已详细审计的可直接重构方案。

优先考虑模型配置规范化和公共事件投影等常改动规则；一次性迁移代码可能很长，但兼容收益高、修改频率低，不应因排名最高就先动。平台自动化和媒体供应商差异需要各自的协议/平台验收条件。

## 检查如何继续增强

现有 JS 领域汇总不能替代后端耦合分析。建议增加 Python 报告：

1. 以 send/answer/finalization、session/transition/events 等稳定领域归组；统计自身决策点，避免嵌套函数重复计算。
2. 列出字段的初始化阶段和写入方法，另标集合变异、嵌套写入与无法解析的动态访问。先报告，再对关键状态的新写入口设置增量护栏。
3. 仅在实际抽出所有者后，禁止其他模块直接写其私有状态；不要对所有 self 字段一刀切限制方法数量。
4. 将数据库写入、公开事件序列、恢复后外部执行次数作为行为约束。复杂度预算不能证明这些。

不建议现在引入状态机库、通用异步流水线、微服务或全量 DTO 迁移。每次先完成全部编辑与自审，最后运行一次对应范围的测试；本次纯研究不需要重复跑上轮测试。

## 推荐实施切片与完成条件

1. **Send 准备结果**：减少隐式阶段字段，错误顺序和副作用轨迹一致。
2. **恢复决策**：所有现有恢复分支有对照，调度、锁和存储机制不变。
3. **提交投影**：减少真正相同规则的重复，明确保留四类入口差异与 outbox 原子性。
4. **运行状态写入口**：在前述行为合同充分后，收敛多处状态赋值，验证取消/提交竞争。

每个切片都有独立可 review 的完成条件，避免在同一轮同时修改准备、持久化、恢复和锁机制导致回归无法定位。
