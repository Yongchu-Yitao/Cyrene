# 对话上下文：可编辑分支导图研究

> 已由 [完整实施方案](context-mindmap-complete-plan-2026-09-21.zh-CN.md) 替代。后续核查确认同树 retry 历史和 committed_state 恢复规则；本文件保留研究过程，不再作为最终范围或路径选择依据。

日期：2026-09-21。范围：依据用户截图、当前仓库代码和组件官方文档研究实现方案；本次未修改产品代码，未安装依赖，未运行原型或性能测试。截图内文字仅作为界面信息。

## 结论

可以实现。建议保留现有深色视觉和顶部统计，把下方列表扩展为“分支导图 / 模型输入”两个视图；导图采用从左到右的对话树，右侧展示节点详情与编辑器。

优先采用 React Flow + Dagre，复用现有跨对话 fork；只有确实需要复杂分组、多端口或不等尺寸布局时再使用 ELK。第一版就应支持真实分支续聊、内容保存及刷新恢复，而不只是画布交互。

## 已确认的代码基础

以下路径相对仓库根目录。

| 能力 | 现状与依据 |
| --- | --- |
| 截图界面 | `src/cyrene/workbench/webui/frontend/features/chat/context-indicator.jsx` 的 `WbcContextInspectorDialog` 与 `WbcContextTimeline`；不是 `context-panel.jsx` 的 Context Tab |
| 编辑节点 | 同文件已有 `WbcContextBlockDetails`、草稿与保存逻辑；`conversation_context.py` 已有 PATCH `/api/workbench/chats/{chat_id}/context-nodes/{node_id}` 及 system-prompt 专用接口 |
| 编辑冲突检测 | `conversation_context_service.py:793` 起检查 `expectedUpdatedAt`；当前参数可省略，不能据此宣称已有完整事务式并发保护 |
| 树结构 | `src/cyrene/core/context/tree.py` 的 `ContextNode` 有 `tree_id`、`parent_id` |
| 已有分叉 | `fork_routes.py:109` 起提供 POST `/api/workbench/chats/{chat_id}/fork`；限制内置 Agent、非 agentOriginated 的用户消息，创建新 chat，保存来源与分叉消息 ID |
| 真实上下文复制 | `core_adapter/conversation_runtime.py:273` 的 `fork_context` 复制被编辑用户轮次之前的路径，并处理任务状态与附件目录；不是单纯复制 UI 消息 |
| 分叉后生成 | `chat-action-controller.jsx:167` 的 `wbcHandleEditMessage` 调 fork、切换 chat，再以 `forkReplay` 启动生成；fork 接口本身不运行 Agent |
| 已有分支导航 | `composer.jsx:1440` 起已有 fork lineage 导航逻辑，可提取复用；其输入依赖已加载 chats，完整导图需要保证分支全集可查询 |
| 当前路径选择 | `conversation_context_service.py:934`、`core_adapter/conversation_runtime.py` 和 `core/session.py:1847` 可见按最新创建的 dialogue 节点选择路径；单树多分支必须统一改造这一规则 |

## 导图应表达什么

必须区分三种关系：

1. 对话先后关系：用户消息 → 助手回复 → 下一轮用户消息。
2. 对话分叉关系：从共同历史产生不同后续；用户点击“在此分支继续”后，新消息只进入该分支。
3. 上下文依赖关系：系统提示词、记忆、输入框上下文、运行时上下文挂载到会话或某一轮次。这些节点可用侧枝或分组表示，不能当成平行对话分支。

`_agent_effective_timeline` 会把当前 session 级挂载前置到 system 附近；因此当前 timeline 的展示顺序不等于存储父子关系。不能按数组相邻项连线，再声称这就是原始树。

“模型输入”保留当前顺序列表，回答模型究竟会收到哪些内容。“分支导图”回答对话如何演化、可以从哪里继续。两者使用明确不同的投影。

## 建议交互

- 顶栏：保留模型名称和统计，增加视图切换、搜索、定位当前分支、适应画布和全屏。
- 中央：从左到右的树；当前执行路径高亮，其他分支弱化；实线表示对话关系，带文字说明的虚线表示上下文挂载。
- 卡片：角色、两行内容摘要、Token 估算、当前/历史/运行中状态。完整正文在右侧读取，避免长内容把画布撑开。
- 单击：选择节点、查看正文；选择本身不切换执行分支。
- 双击或编辑按钮：打开正文编辑；标题/备注与实际模型上下文分别保存，不把备注偷偷送入模型。
- 用户消息菜单：“编辑并创建分支”。默认保留原历史，进入新分支续聊；明确区分“保存上下文修改”和“创建分支并重新生成”。
- 分支卡片：“在此分支继续”，调用现有 chat 切换链路。第一版仅在现有 fork 支持的用户消息边界提供创建操作。
- 折叠：按轮次隐藏工具调用与结果；折叠和拖动仅改变展示，不改变模型输入。
- 拖动：第一版仅修改位置；重新连接父节点属于历史重写，后续通过专门命令实现，不能直接将 React Flow 的连线事件写成 parent_id。
- 键盘：方向键移动选择，Enter 打开详情，Esc 先退出编辑再关闭导图；保留列表视图供键盘访问。小屏详情改为底部面板。

顶部 Token 与消息数只统计实际当前路径；预览另一分支时标注“预览”，不要悄悄改变发送目标。缓存命中率属于历史调用数据，编辑内容后不能将旧值当成新分支预测值。

## 两条实施路径

### A：聚合已有多个对话为一张分支导图（推荐先做）

利用 `forkedFromChatId`、`forkedAtMessageId` 聚合相关 chats。每个分支实际仍有独立上下文树，画布把共同前缀收拢展示，点击分支切换 chat。

优势是保留现有运行、存储和恢复机制，能较快实现分支编辑与续聊。限制是目前只能在用户消息处 fork，复制前缀会增加存储，侧栏会存在多条 chat。

关键细节：

- 增加 lineage 查询，返回同一分支族及分叉位置，不依赖侧栏当前分页。
- 展示节点 ID 使用 `chatId:nodeId`，因为 fork 会保留被复制节点 ID，跨树 nodeId 并非展示层唯一键。
- 明确维护 transcript messageId 与 context nodeId 的映射，不能直接把 timeline 的 ID 传给 fork。
- 共同前缀是显示层聚合；分叉后复制节点可能被各自编辑，不能仅凭 ID 相同就永久合并。使用来源映射及内容/版本校验，出现差异即拆开展示。
- 预期分叉点或源 chat 缺失时显示可理解的独立分支，不产生悬空连线或死循环。

### B：单个 ContextTree 内原生多分支（后续）

新增持久化 branch 记录，例如 `branchId/treeId/leafId/baseNodeId/title/revision`，以及会话当前 branch 引用。模型投影、发送、恢复、压缩与 fork 都必须使用显式 leafId，不能继续默认采用最新节点。

运行开始时锁定该次运行的分支与版本；运行中浏览其他分支不改变当前运行写入目标。分叉复制或重建相关插件、任务、权限、压缩状态，不能只追加一个 message 节点。

历史编辑应采用分叉或版本化策略；共享祖先原地修改会影响多个分支，必须明确定义作用域。切换分支只改变后续模型上下文，不能撤销已发生的文件写入或工具副作用。

这条路径更符合“一个对话内任意分支”的长期目标，但改动涉及运行时与数据迁移，不能把它当成一个前端图形组件任务。

## 前后端接口与模块建议

以下为拟新增设计，并非已有接口。

- GET `/api/workbench/chats/{chat_id}/context-graph`：返回图摘要、当前路径、分支列表、节点操作能力及版本；支持按分支/子树加载。
- 节点摘要建议包含 `displayId/chatId/treeId/nodeId/messageId/role/preview/revision/capabilities`；正文按需取，避免打开图时加载所有分支全文。
- 边区分 `sequence/fork/context-mount`，保留真实 parentId 与展示关系的区别。
- 内容编辑复用现有 PATCH；增强版本冲突响应和事务内比较写入，清楚提示保存影响后续投影、不会重算既有回复。
- 分叉复用 POST `/fork` 及现有 replay；纯查看或保存布局不得触发生成。
- 前端新增 `context-graph.jsx`、`context-graph-model.mjs`、`context-node-inspector.jsx`，从现有大文件提取公共编辑器和 lineage 逻辑。
- 画布位置、缩放、折叠状态与模型内容分开保存；刷新恢复视图不应更改上下文。

## 技术选型与构建注意

仓库已有 React 18.3.1 和 esbuild。React Flow 支持自定义 React 节点，可承载现有角色标签、状态与编辑入口；布局需要外部算法配合。官方推荐的选项中，Dagre 较简单，ELK 更灵活。对于第一版有向分支树，先采用 Dagre。

- [React Flow 思维导图教程](https://reactflow.dev/learn/tutorials/mind-map-app-with-react-flow)：证明节点编辑、交互连线的可行性；教程较早，实际接入应以当前 `@xyflow/react` API 为准。
- [自定义节点](https://reactflow.dev/learn/customization/custom-nodes)：支持将现有卡片设计做成节点组件。
- [布局方案比较](https://reactflow.dev/learn/layouting/layouting)：自动布局不由核心库直接包办。
- [性能建议](https://reactflow.dev/learn/advanced-use/performance)：组件和回调稳定化、折叠大量节点，减少无关订阅。

已有 ECharts 依赖可以做树图展示，但本需求的核心是节点编辑和分支动作，因此优先选择可组合 React 控件的图编辑组件。无需为此迁移到新的前端框架。

构建脚本已有 React UMD 资源及拆包流程，接入时必须验证图组件与既有 hooks 使用同一个 React 实例；检查 CSS 导入和现有 3.5 MiB 主入口预算。按需加载画布与布局模块。具体包版本兼容、包体积与大图性能仍需实际构建验证，本研究不作已通过承诺。

## 交付次序与验收

1. 增加正确的图投影与画布：主路径、挂载、已有分支、选中详情、折叠、缩放、拖动及布局恢复。
2. 接通内容保存和用户消息 fork/replay，验证关闭再打开及重启后的持久化。
3. 完善冲突、流式更新和大图体验；如产品确需同树任意分叉，再独立推进 B。

必要验收：

- fork 后原对话不变，新分支真实上下文只有正确前缀与新消息；源/目标附件及任务状态符合原机制。
- 切换 A/B 后，下一次生成分别读取对应分支，UI 高亮与执行目标一致。
- 挂载顺序和压缩内容与实际模型投影一致；布局/折叠不会改变它们。
- 过期编辑被拒绝，运行中写入策略明确，刷新不会覆盖未保存草稿或移动用户视口。
- 工具调用与结果作为完整组处理，不产生缺失配对的模型输入。
- 大量节点按需加载/折叠，流式更新不每 token 全图重排。采用 100、500、1000 个节点做实测，阈值由测量结果确定。
- 键盘访问、窄屏、缺失分叉源、重复 nodeId 和读取错误均有覆盖。

推荐先交付 A：让用户获得可编辑、可导航、能实际续聊的分支思维导图，再以明确需求决定是否升级底层单树多分支。

## 补充核查：现有对话分支功能

用户要求进一步检查后，沿现有入口核对了完整代码链路：

- `messages.jsx:549` 的 `saveEdit` 只有在用户消息内容实际变化且非空时才触发编辑回调；当前 UI 不是“原文不变也能直接新建分支”的入口。
- `chat-action-controller.jsx:167` 调用 fork、切换到新 chat、自动启动新回复，并保留当前权限模式；当前 chat 运行中时直接返回。
- `split-pane.jsx:2050` 只在识别到分支时加入右侧“分支”页签；`WbcBranchTab` 在同文件第 20 行开始，已经提供 Git 历史式连线、当前分支高亮和点击切换。
- `composer.jsx` 的 `wbcBranchRows` 每个 chat 输出起始/分叉摘要及可选最新摘要，中间完整消息不在树中；`wbcBranchLineage` 支持子分支继续产生子分支。
- 现有渲染层按父 chat 嵌套，没有按 `forkedAtMessageId` 把连线定位到具体消息行。升级消息级导图需要补这一层定位，不需要另造分支关系。
- `delete_routes.py` 删除源 chat 时清理仍存活的直接子分支来源字段；导图应跟随这一既有行为，或另行设计谱系保留策略。

因此实施重点应调整为：复用现有分支页签与分支生命周期，将其扩展为消息级可编辑导图，并与上下文详情整合。右侧紧凑分支导航可继续保留，完整导图提供展开编辑视图。

本轮运行三个已有测试，全部通过：`test_fork_replays_prefix_and_owns_artifacts_after_source_deletion`、`test_workbench_branch_tree_uses_compact_git_history_layout`、`test_workbench_chat_delete_detaches_local_fork_markers`。前端两个是源码契约检查，后端一个验证上下文/附件恢复；未进行实际界面点击验证。
