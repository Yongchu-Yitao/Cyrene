# 可编辑交互式对话分支导图：完整实施方案

日期：2026-09-21。本文件替代此前研究中“第一版/后续再补”的范围划分。以下保留完整目标与实现设计；当前落地范围、架构调整及验证记录见 [实现记录](context-mindmap-implementation-2026-09-21.zh-CN.md)。仓库路径均相对 Cyrene 根目录。

## 1. 再次核查后的结论与修正

目标是在现有产品中提供可编辑、可操作、可持久化的消息级分支导图，将上下文检查器、分支导航与实际执行统一起来。不是另建演示页面，也不是只画当前消息列表。

必须纠正此前两个不够准确的判断：

1. 底层不只是“每个分支一个 chat”。编辑用户消息产生跨 chat fork；`AgentSession.prepare_retry()` 已在同一树中保留重试兄弟路径。完整导图必须展示这两种历史。
2. 恢复路径并非一律只取最新节点。`_select_restore_leaf()` 还会结合终止祖先、retry 元数据和 `committed_state` 恢复已提交的旧答案。上下文查询和 fork 源路径目前仍使用较简单的最新节点规则，必须统一路径解析。这里是静态代码发现的规则差异，尚未通过 UI 复现特定错误。

完整架构采用“统一逻辑分支 + 独立执行上下文”。复用跨 chat 隔离能力，是长期存储决策，不是临时削减功能。旧的同树 retry 分支仍完整索引和展示；从旧路径继续时将所选历史恢复成独立执行上下文，不靠修改当前树指针复用可变根状态。

不把所有旧树强行合并到一棵共享可变树。用户看到的是一个分支族、一张图；物理 chat/tree 是后端执行单元。

## 2. 代码核查依据

| 代码位置 | 已确认事实 | 对方案的影响 |
| --- | --- | --- |
| `frontend/features/chat/context-indicator.jsx:591`，位于 webui 下 | 截图对应 `WbcContextInspectorDialog`；`WbcContextTimeline` 展示有效上下文列表 | 直接升级现有入口，保留输入顺序视图 |
| 同文件 `WbcContextBlockDetails` | 已有正文编辑、草稿、保存、错误提示 | 提取公共编辑器，不重复实现 |
| `frontend/features/chat/split-pane.jsx:20` | `WbcBranchTab` 已有 Git 式树线、当前高亮、点击 `onSelectChat` | 导图和紧凑导航共享选择和分支数据 |
| `frontend/features/chat/composer.jsx:1446` | `wbcBranchLineage` 按 `forkedFromChatId` 聚合，`wbcBranchRows` 只输出起点/最新摘要 | 不含完整消息和同树 retry 历史，需要新图投影 |
| `frontend/features/chat/messages.jsx:549` | 编辑保存会忽略空内容和未变化内容 | 补“原文分叉”独立动作，不冒充编辑 |
| `frontend/features/chat/chat-action-controller.jsx:167` | fork 后切换 chat，再用 forkReplay 生成；当前运行中禁止此入口 | 创建、激活、生成三个动作需可分别调用 |
| `http/workbench/chat_routes/fork_routes.py` | 仅内置 Agent 的普通用户消息可 fork；按用户序号定位；复制配置、前缀、附件引用 | 扩展为显式源节点/源路径和操作模式，保留兼容接口 |
| `core_adapter/conversation_runtime.py:273` | `fork_context` 复制路径和附件目录，重建 task state；源 leaf 按最新时间选择 | 抽出可指定历史路径的快照服务 |
| `core/session.py:3004` | prepare_retry 回退到用户消息之前，再创建兄弟路径；旧历史保留 | 索引 retry，不只读取 chat lineage |
| `core/session.py:1831` | 恢复会参考 committed leaf 和 retry 状态 | 统一“公开已提交/运行中/历史预览”语义 |
| `core/context/store.py:278` | 有 committed_leaf_id/committed_run_id；commit_state 原子更新 | 在现有提交机制上扩展，不建立相互矛盾的权威指针 |
| `http/workbench/chat_routes/run_send_routes.py:844` 附近 | 用户 turn_id 写入运行元数据；结果有 runId/contextNodeId/turnId | 用 ID 关联，禁止正文/时间猜测匹配 |
| `workbench/chat/conversation_context_service.py:793` | 编辑读取节点后比较 expectedUpdatedAt，再调用 store 更新；参数可选 | 需事务内 CAS；当前检查不等于完整并发控制 |
| 同文件 `update_node_content` 查询服务层 | 写入 ContextTree，未在该链路同步 chat transcript 或发布 chat_changed | 编辑必须统一投影、事件和运行协调 |
| `core/context/tasks.py:110,485` | task state 位于可变 root；fork_task_state 根据保留事件重建，跳过压缩状态重放 | 只切 leaf 不足以恢复历史任务状态 |
| `core/context/projection.py` | 模型消息经过 task、挂载、压缩等投影 | 导图不能自己拼模型请求 |
| `workbench/chat/context_read_cache.py` | 缓存按数据库版本与 variant 失效 | 历史路径/图分页要加入缓存键 |
| `http/workbench/chat_routes/collection_routes.py` | 当前 list chats 按 project 过滤，代码没有分页参数 | 修正此前分页推测；族查询为语义完整与效率设计，不是修复已证实的分页 bug |
| `http/workbench/chat_routes/delete_routes.py` | 删除来源后清理子 chat 的 fork 元数据 | 新分支族须持久化，不能依赖可能消失的父 chat |

路径说明：表中 `frontend` 位于 `src/cyrene/workbench/webui/`，`http/core_adapter` 位于 `src/cyrene/workbench/`，`core` 位于 `src/cyrene/`。

## 3. 完整产品体验

### 3.1 入口与布局

- 保留上下文指标入口，打开可全屏的导图检查器；右侧现有“分支”页签保留紧凑导航，提供“展开导图”。两者使用同一分支索引。
- 顶栏：会话族名称、当前模型、上下文使用、消息数、最近调用缓存命中率；区分“当前执行分支”和“正在预览分支”。
- 主视图区分“分支导图”“模型输入”。前者看结构与历史；后者看所选路径经后端投影得到的有效输入及来源。
- 导图由左向右，分叉向上下展开。默认折叠内部工具步骤，允许展开到节点级；上下文挂载和任务文档单独分组。
- 右侧详情：正文、附件、角色、来源、模型、时间、Token 估算、操作、修订记录；技术 ID 收入展开区。
- 窄屏使用详情抽屉或底部面板，保留可访问的列表导航。

### 3.2 节点、关系和选中

节点类型：用户消息、助手消息、工具调用/结果组、系统提示词、会话/轮次/模型级上下文、任务文档、压缩记录、分支入口、草稿、已删除来源占位。

关系类型：`sequence`（先后）、`fork`（派生）、`retry`（同问题重新生成）、`mount`（上下文依赖）、`reference`（引用）。用线型、标签及颜色共同区分，不单靠颜色。

不将同一个工具调用与多个结果拆成可任意乱序的消息。内部节点依然可以查看，执行操作按完整调用组约束。

单击选中只改变预览；双击正文或编辑按钮进入编辑；“设为当前并继续”才改变输入框目的地。图上当前路径、聊天正文和发送目标必须一致。任何历史预览不得悄悄改执行指针。

### 3.3 操作全集

| 操作 | 明确行为 |
| --- | --- |
| 新建子分支 | 从所选已完成边界派生新上下文，允许先保存草稿，或添加新问题并发送 |
| 原文分叉 | 不修改用户消息也能创建变体，独立选择是否生成 |
| 编辑用户消息并生成 | 在该消息之前截断派生路径，保存新用户消息，再生成；原分支完整保留 |
| 编辑助手文本 | 保存为人工修订的分支内容；不伪装为原模型输出；可继续追加用户问题 |
| 编辑系统/记忆/上下文 | 明确目标分支及作用范围，显示保存后的模型输入预览 |
| 新增上下文或备注 | 上下文通过规范挂载进入投影；备注只属于图，不发送给模型 |
| 重新生成 | 创建可见的 retry 分支，原答案保持可访问；与现有 retry 语义衔接 |
| 切换/续聊 | 执行中分支保持绑定；切换显示不迁移运行；历史只读路径需要先恢复为可执行分支 |
| 重命名/颜色/收藏/折叠 | 只更新分支或视图元数据 |
| 拖动节点 | 改位置；自动布局不覆盖已固定节点，提供“恢复自动排列” |
| 拖到其他父节点 | 发起“基于此处复制分支”操作，展示内容与上下文差异，生成新历史；不原地改执行记录 parent_id |
| 隐藏节点 | 仅改变视图 |
| 从上下文移除节点 | 创建新的修订分支，校验工具配对、挂载、任务状态；不等于隐藏 |
| 归档/删除分支 | 归档可恢复；删除保留来源占位与子分支，先处理该分支运行；不连带删除子分支 |
| 撤销/重做 | 布局和草稿直接撤销；已保存内容创建反向修订；已发起模型/工具执行不能作为本地编辑撤销 |
| 分支对比 | 按共同来源对齐消息、上下文与模型配置差异，不只对比标题 |
| 引用另一分支 | 选择文本或摘要作为显式引用上下文；不合并运行队列、权限和工具状态 |
| 搜索/导出 | 搜索分支/节点并定位；导出 Markdown、带版本的 JSON、当前图 SVG/PNG；导出明确是否含附件和完整正文 |

这些是同一个完整交付范围，不以“之后补上”为验收条件。任意原始节点都可检查，但不承诺每个工具执行中间态都能直接续跑：非完成边界展示最近合法边界及原因，由用户选择。已有待答问题走原来的回答/取消协议，不能复制挂起的权限回调到新分支。

## 4. 分支数据与权威状态

### 4.1 分支族与逻辑分支

在 Workbench 持久层增加逻辑表，字段示例（拟新增，不是现有 schema）：

- `conversation_families(id, project_id, title, revision, created_at)`。
- `conversation_branches(id, family_id, parent_branch_id, source_tree_id, fork_node_id, fork_position, chat_id, tree_id, history_leaf_id, kind, execution_state, visibility, revision)`。kind 为 fork/retry/edit；execution_state 区分历史快照、待发送、运行中、待答、已完成、失败；归档是独立 visibility，避免与执行状态混淆。
- `context_node_origins(tree_id, node_id, source_tree_id, source_node_id, turn_id, run_id, revision)`，保留复制与修订来源。
- `context_edit_revisions(operation_id, branch_id, target, before_ref, after_ref, scope, created_at)`，支持内容对比与反向修订。
- `context_graph_views(owner_id, family_id, version, viewport, positions, collapsed, pinned)`，与模型内容完全分离。
- `branch_operations(idempotency_key, operation_id, source_revision, target_ids, phase, result_ref)`，用于跨库创建/提交恢复。

节点主键始终为 `(treeId,nodeId)`；前端再生成稳定 displayId。相同 nodeId 在 fork 的多棵树中合法存在，不能用 nodeId 单独覆盖 Map 条目。

图可以合并完全相同的共同前缀，但聚合节点保留多个明确来源引用。修订后内容/作用域不同即拆分。编辑聚合节点前定位所选分支，不允许一次写入所有物理副本。

### 4.2 当前状态唯一解析

新增共享的路径解析服务，供会话恢复、context summary、context blocks、graph、checkpoint、fork snapshot 调用。返回：

`treeId, committedLeafId, runningLeafId, selectedPreviewLeafId, runId, state, revision, reason`。

- 正在运行的 owner 与 runId 确定 live 状态；公开已提交结果沿用 committed_state/outbox。
- 明确选择的历史 leaf 仅用于历史读取，验证属于允许访问的树及路径。
- 兼容旧树时复用现有 retry/终止祖先恢复规则，并将推导结果记录为可核查的分支索引；不使用一个新的“最大时间节点”规则覆盖现有逻辑。
- 缺失或损坏的引用返回错误或降级到可确认的已提交路径，不能自动切到另一条最新分支。
- 逻辑分支索引的 leaf 摘要是可重建投影；运行提交指针仍由 core 管理，避免双主状态。

## 5. 历史恢复、编辑与运行一致性

### 5.1 通用历史快照服务

扩展现有 `fork_context` 为 `materialize_branch(sourceTreeId, sourceLeafId, boundaryNodeId, position, edit, operationId)`。sourceLeafId 用于限定确切路径，boundary 决定 before/after；校验节点是该路径祖先。

快照复制包含：对话前缀、有效挂载、附件和持久产物、任务状态、明确的模型/配置继承策略。执行队列、打开的权限请求、浏览器/终端实时连接、一次性运行状态不随历史复制；通过正常新会话初始化创建。

task state 使用并扩展 `fork_task_state`，按成功历史事件重建。新增的人工文档编辑也必须成为可重放修订事件，否则后续 fork 会丢失它。压缩状态不作为真实共同历史强行复用；依据保留路径重新投影，必要时重新压缩并标记。

用户映射优先使用 `metadata.turn_id`；助手使用 `contextNodeId/runId/turnId`；同一 turn_id 可能对应多次 retry，因此联合源树/路径/runId 定位。旧数据无法唯一映射时只读展示，提供明确的可选边界，不猜测后执行。

### 5.2 编辑策略

草稿可原位编辑；已执行内容的正文修改默认创建新修订分支，从变更处重新产生后续。现有后续答案留在原分支，避免让旧答案看起来由新问题生成。如复制旧后续作为参考，只作为标记明确的参考节点，不自动进入执行输入。

上下文编辑区分：

1. 该历史快照的修订，生成独立分支；
2. 当前分支后续轮次的覆盖，保存 branch-scoped override；
3. 修改原始提供源，打开对应提示词/记忆/文件编辑入口。

会话/轮次/model 生命周期影响覆盖位置与持续时间。override 在上下文组装的指定层应用并标记来源，不能简单更新旧 mount 后假设下一轮 provider 不会重新生成。任务文档写入通过任务服务的同一 gate 和成功事件记录，不绕过其状态模型。

工具结果与压缩结构采用结构化编辑器及 schema 校验；需要改工具历史时创建标记为人工修订的上下文快照。人工结果不产生“工具实际执行成功”的 effect receipt，也不触发旧 Hook 或工具重放。

### 5.3 并发、原子性与失败

- 所有变更要求 expectedRevision；事务内 compare-and-swap，冲突返回 409 并保留草稿。不能用“先 get 再 update”的时间戳比较替代。
- 执行修改通过会话 gate/运行 owner 协调。A 运行中可以查看 B，也可以基于 A 已提交前缀派生 B；修改 A 正在执行的输入需先停止 A 或选择新分支。
- 新分支创建和发送使用不同的幂等请求；按钮连击和网络重试不会创建两个分支或重复运行工具。
- chat 存储、树库、附件目录不是一个事务：采用操作日志和补偿，经历 prepared → copied → registered → committed。未提交目标不出现在可发送列表；重启补完或清理，不留下孤儿树。文件复制用临时目录完成后再就位。
- 当前 fork route 的整库 read/write 调用改为 repository 中有冲突控制的定向写入，沿用既有 write_one/提交事件机制，避免并发写丢失。
- 成功后发布带 branchId/operationId/revision 的 chat_changed/context_changed；更新聊天正文、统计、图与其他窗口。乱序旧响应不能覆盖新数据。
- 修改历史、切换分支、撤销内容都不回滚磁盘、外部系统或已完成工具副作用。只读预览不能执行 Hook。

## 6. API 契约

以下均为拟定接口；最终命名服从现有路由规范。

| 接口 | 用途 |
| --- | --- |
| GET `/api/workbench/chats/{id}/context-graph` | 解析当前族，按游标/分支/层级读取节点摘要、typed edges、当前状态、权限能力、版本 |
| GET `/api/workbench/context-families/{id}/branches` | 获取完整分支目录，不依赖侧栏加载状态 |
| GET `/api/workbench/context-branches/{id}/nodes/{nodeId}` | 正文、附件、来源、修订记录；branch 决定 tree/path |
| GET `/api/workbench/context-branches/{id}/projection` | 指定 leaf 的有效模型输入与来源映射、Token 估算；纯读取 |
| POST `/api/workbench/context-branches` | mode=continue/edit/retry/copy，从显式边界创建，返回目标 chat/tree/branch 及状态；默认不生成 |
| POST `/api/workbench/context-branches/{id}/activate` | 校验可执行或恢复历史快照，返回可切换 chat；不隐式发送 |
| POST `/api/workbench/context-branches/{id}/edits` | 强类型正文/挂载/移除修订，校验 scope、schema、revision |
| PATCH `/api/workbench/context-branches/{id}` | 标题、颜色、收藏、归档 |
| DELETE `/api/workbench/context-branches/{id}` | 删除指定逻辑分支/独立执行树，保留来源占位与其他分支 |
| POST `/api/workbench/context-branches/{id}/restore` | 恢复归档；硬删除不能伪称恢复 |
| GET/PATCH `/api/workbench/context-families/{id}/view` | 保存与恢复视口、布局、折叠 |
| GET `/api/workbench/context-families/{id}/compare` | 指定两个分支返回消息与上下文差异 |
| POST `/api/workbench/context-families/{id}/export` | 导出所选范围，不改变内容 |

保留旧 `/fork` 与 node PATCH 路由作为兼容 adapter，逐步将调用统一到新服务，不保留绕过版本和运行检查的第二写入口。兼容旧 PATCH 响应时增加 targetBranchId/targetChatId，让前端正确跳到修订分支；需要更新所有旧调用者并测试。

发送复用现有 run pipeline，追加 branchId、expectedRevision、operationId 绑定，服务端校验 chat/tree 对应关系。外部 Agent 根据实际能力显示操作；不能仅因适配器声称 fork 支持，就假定存在可读取的本地 ContextTree。

## 7. 前端模块与图形实现

采用 `@xyflow/react` 自定义 React 节点；完整布局使用 ELK layered（异步/Worker），支持分组、不同节点尺寸和上下文侧枝。相比此前 Dagre 建议，这里选 ELK 是因为完整范围包含工具组与多类关联边；无需同时维护两套默认布局。

模块建议：

- `context-graph-model.mjs`：typed graph 投影、聚合、去重、可见子图与路径高亮。
- `context-graph.jsx`：画布、控件、选择与键盘导航。
- `context-graph-layout.mjs` / worker：布局请求版本、固定节点、局部重排。
- `context-node-inspector.jsx`：复用并改造现有内容编辑器。
- `context-branch-controller.mjs`：创建、激活、修订与生成动作；不在 React 节点内部直接调用 fetch。
- `context-graph-store.mjs`：server graph、选中、草稿、运行状态与视图分开存储。
- `context-graph.css`：复用现有颜色、边框、字体和 Tabler 图标。

运行更新只 patch 变化节点，完成一个消息/展开子树后再局部布局；不随 token 重排，不自动把用户拖回当前节点。旧布局 Worker 返回值按版本丢弃。

保留用户固定位置，新增分支局部避让。保存布局与保存正文不同命令；编辑器使用 nodrag/nowheel 区域，IME 输入不触发快捷键。Escape 先退出局部编辑，第二层才关闭模态窗；关闭有草稿时提供保存/丢弃/继续编辑选择。

按需加载图组件和布局 Worker。构建核查 React UMD/ESM 单实例、CSS 输出与 Worker URL、Electron CSP 和生产资源路径、现有 3.5 MiB 主入口限制。不能仅在开发服务器成功就算接入完成。

官方依据：[自定义节点](https://reactflow.dev/learn/customization/custom-nodes)、[布局比较](https://reactflow.dev/learn/layouting/layouting)、[ELK 示例](https://reactflow.dev/examples/layout/elkjs)、[性能建议](https://reactflow.dev/learn/advanced-use/performance)。具体版本在实现时锁定并构建验证。

## 8. 数据迁移与兼容

1. 备份并记录 schema version，创建增量表；旧 chat/tree ID 不变。
2. 根据存活的 forkedFromChatId/forkedAtMessageId 建族；孤立来源形成占位。已经被清理的历史来源无法凭空恢复，应明确显示未知。
3. 扫描树的多子节点、retry 元数据和 runId，建立历史分支索引；区分正常对话、Hook 附属节点、失败审计路径，不把每个叶子都算用户分支。
4. 以现有恢复规则确定当前公开路径，校验 committed_state；旧数据缺映射时保留只读历史，不自动运行或压缩来“补齐”。
5. 回填节点来源/turn 映射，按需补充分支摘要；记录 migration cursor，幂等可续跑。
6. 为旧查询/写入增加适配，索引采用事务更新/提交 outbox；定期校验能从核心数据重建。
7. 通过开关回退旧 UI 时仍能打开新增独立 chat；不删除新数据，不让旧代码原地写坏新版本。数据库降级只支持显式备份恢复，不能假定旧二进制完全理解新历史。

## 9. 验证计划和完整交付标准

本轮已运行 48 项现有测试并全部通过：`test_conversation_context_service.py`、`test_task_contexts.py`、`test_context_read_cache.py`，两个 retry 恢复测试和现有分支布局源码测试。它们证明当前基础行为，不证明新图功能，也不替代浏览器验证。

实现必须新增/完成以下有行为意义的验证：

| 范围 | 验收结果 |
| --- | --- |
| 分支全集 | 跨 chat fork、同树 retry、多层子分支和失败路径可区分；删除来源不丢子分支 |
| 正确来源 | 重复 nodeId、同 turn 多 run、旧数据缺映射均不选错节点 |
| 历史续聊 | 所选前缀、任务文档、挂载及附件准确；后续状态不泄漏到过去 |
| 路径一致 | summary、模型输入、实际 send、checkpoint 和重启恢复指向同一执行路径 |
| 编辑 | 原分支保留、正文与 transcript 一致、下一轮 provider 不吞掉 override、无效结构被拒绝 |
| 并发 | 两窗口编辑冲突可恢复、运行目标锁定、幂等提交、乱序事件与响应不倒退 |
| 崩溃恢复 | 在复制/注册/提交/发事件各步骤故障注入；无可发送的半成品、无重复执行 |
| 工具与待答 | 调用结果配对完整，历史人工修订不会重新执行工具，pending 问题不串分支 |
| 交互 | 选择、编辑、创建、切换、续聊、折叠、布局、撤销、搜索、对比、引用、导出都完成真实闭环 |
| 持久化 | 刷新、关闭窗口、应用重启后恢复图、当前分支、内容及视图状态 |
| 无障碍 | 键盘、IME、焦点返回、读屏标签、非颜色状态表达、小屏可用 |
| 大图 | 100/500/1000 节点及长文本、多层 fork 实测；默认摘要和按需加载避免全文大包 |
| 生产构建 | React 单实例、资源/Worker 可加载、无明显包体积回退，Web/Electron 均验证 |

性能目标建议：在记录明确的测试设备上，500 个可见摘要节点拖动平移保持可用的接近 60 FPS 体验，普通选择/展开反馈 P95 不超过 100ms（不含网络读取）；超出时通过折叠和可见区域渲染控制节点量。目标是待实测的验收指标，不是已测结果。另记录 1000 节点首次加载、布局耗时与峰值内存，形成可复现基线。

## 10. 实施依赖顺序与完成定义

实施顺序是：路径解析与分支索引 → 历史快照/修订/运行一致性 → 图与编辑器 → 迁移和兼容 → 故障与交互验收。这是工作依赖，不是多个缩减版本。

完成意味着用户能在同一张导图上查看全部可恢复分支、编辑内容与上下文、从合法历史节点建立新分支、切换并真实续聊；操作在刷新和重启后保持一致，旧历史、附件与任务状态可靠保留；完整操作矩阵、迁移与验收均落地。只有可缩放的静态树、只能跳转的分支列表，或无法影响实际模型输入的编辑器，都不满足交付标准。
