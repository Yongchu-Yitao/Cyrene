# Cyrene 去任务化与通用 Dynamic Agent 研究

## 结论

Cyrene 可以移除“Task 是另一种一级会话”的产品模型，但不应该直接删除 Task 目录后重写。当前 Chat 已经具备可恢复运行、计划、工具事件、分屏、文件编辑、文件树、PDF 预览和 Agent 绑定；Task 真正独有且值得保留的是持续执行到验收通过的 Goal Loop、较完整的计划状态和验收/反思策略。

建议将产品收敛为“一个 Conversation 宿主 + 一组可选 PluginPack 能力”：

```text
Conversation（唯一会话）
├── AgentRun                 一次可恢复的 Agent 运行
├── PluginSessionState       各 PluginPack 的隔离持久状态
│   ├── cyrene_control.plan  对话内计划
│   └── cyrene_goal.goal     持续目标与循环状态
└── DynamicSurfaceState      Workbench 拥有的分屏工作区
```

“Dynamic Agent”不应是一种新的会话类型，也不应成为一套绕开现有 Plugin Runtime 的新框架。它是 Conversation Agent 使用 Plugin 的通用运行模式：Plugin 工具通过统一事件描述资源访问和运行活动，Workbench 根据已启用 PluginPack 的 Surface contribution 决定如何分屏展示。

> 当前范围：只设计 Cyrene 原生 Agent 与原生插件工具链。

核心判断：

- Conversation、AgentRun、事件 envelope、Pane 布局和焦点策略属于宿主；
- 文件和通用 Workspace Execution 属于 `core`/`cyrene_code`，计划属于 `cyrene_control`，目标循环属于新的 `cyrene_goal`；具体技术栈通过 Workspace Action contribution 接入；
- Plugin 只能声明资源语义和 Surface 意图，不能直接写 Pane Store；
- 高频、可信的编辑器/文件树/PDF 使用 Workbench 原生 renderer；普通插件 UI 继续使用现有 sandboxed `frontend_views` iframe，不把第三方代码加载进主 React 上下文。

## 现状与可复用能力

| 能力 | 当前实现 | 可复用部分 | 主要缺口 |
|---|---|---|---|
| 分屏 | `features/chat/page.jsx`、`pane-layout-controller.jsx`、`split-pane.jsx` | 左右列、上下卡片、拖拽、调整大小、会话内布局状态、独立窗口 | Pane 内容布局尚未持久化；Pane kind 硬编码；自动打开会覆盖一侧现有卡片；没有 Agent Activity 到 Pane 的仲裁层 |
| 文件编辑 | `viewer.jsx`、`resource-splits.jsx`、`code/editor.jsx` | CodeMirror、自动保存、乐观锁、冲突提示、Markdown/HTML 预览 | `.tex/.bib/.sty/.cls` 不可编辑；Agent 写文件后没有实时刷新已打开 buffer 的统一协议 |
| 文件结构 | `rail.jsx`、`ProjectFileService.list_files()` | 安全的工作区目录浏览、搜索、忽略目录、文件拖拽 | 只在左侧 rail；不能作为 Pane；不能显示 Agent 当前扫描/聚焦的目录 |
| 对话计划 | `enter_plan_mode`、`update_plan_progress`、Chat `activePlan`、`WbcPlanTab` | 对话内提议/批准计划、实时步骤进度、持久恢复 | Schema 比 Task plan 简单；Plan 仍是右侧 accordion tab，不是一级分屏 Surface |
| Goal Loop | `workbench/goals/*` | 草稿确认、租约、崩溃恢复、逐步执行、独立验证、反思、修复轮次、预算/时限 | 完全绑定 Task session、`TaskAgentRuntime`、Task projection 和 `task` RunCoordinator namespace |
| TeX | TeX 扩展目录、TinyTeX/TeX Live 安装、PDF.js Viewer | 已能提供 `pdflatex/xelatex/lualatex` PATH；已有 PDF 查看器 | 没有 TeX 编辑类型、构建服务、诊断协议、编译按钮、源码/PDF 联动 |

### 现有插件框架给出的边界

当前代码已经具备以下可直接利用的机制：

| 插件机制 | 当前能力 | 本方案如何复用 |
|---|---|---|
| `PluginRegistry` / `PluginPack` | pack 注册、启停、canonical name、standalone plugin、目录刷新 | 作为所有动态能力的唯一发现源，不再建立第二套 Agent capability registry |
| `ExtensionPoint` | application/session/run 三种 scope，pack contribution | 新增 Workbench Surface、workspace file type 和 workspace action 三类正式 extension point |
| `PluginApplicationHost` | route、application service、startup/shutdown、frontend method、启停隔离 | Goal 恢复、Build/Run provider 和插件 View RPC 都走这里 |
| `PluginContext` / runtime events | 工具参数、运行上下文、`publish_runtime_event()`、tool progress | 产生资源位置、文件变更、计划、目标和构建事件 |
| Plugin session state | `_plugin_session_state`、`public_snapshot`、`child_context_ids` | 保存 Plan/Goal 的插件私有状态，Chat DTO 只投影公开字段 |
| Workbench plugin UI | `frontend_views`、`project_tools`、sandbox iframe、JSON RPC | 继续服务普通插件 UI；不承载高频原生编辑器 |
| Pane | 已支持 `plugin-view`，但其他 kind 在 `page.jsx` 硬编码 | 增加统一 `surface` card 和 `SurfaceHost`，旧 kind 用 adapter 渐进迁移 |

当前真正缺少的不是新的 Agent 层，而是四个插件契约：

1. Plugin 工具如何声明“它读取、写入或扫描了什么资源”；
2. PluginPack 如何声明可编辑的文件类型；
3. PluginPack 如何贡献 Build、Run、Test、Preview 等 Workspace Action；
4. 已启用 PluginPack 如何声明 Surface，而 Workbench 在不破坏用户布局的情况下展示它。

因此应在 `Plugin.metadata` 增加宿主无关的 `resource_effects`，由服务端事件投影补充标准化 `presentation.locations`。Workbench 再通过 typed Surface contribution 做展示映射，不根据 Agent 自然语言或可变工具显示名猜测资源。

## 插件优先的目标架构

```text
PluginRegistry（唯一能力目录）
  ├── core                  Read / Write / Bash / Toolbox
  ├── cyrene_code           文件、结构、终端、Workspace Execution
  ├── cyrene_control        Ask / Plan / Reflect
  ├── cyrene_goal（新增）   Goal state + GoalController
  └── provider packs        Build / Run / Test / Preview actions
          │
          ├── Plugin.metadata.resource_effects
          ├── WORKBENCH_SURFACE contributions
          ├── WORKSPACE_FILE_TYPE contributions
          └── WORKSPACE_ACTION contributions
          ▼
Plugin Runtime + Unified AgentEvent
          ▼
Activity Normalizer
  - 以 canonical plugin identity 查 resource_effects
  - 校验 workspace-relative location
  - 生成标准化 activity / surface intent
          ▼
Dynamic Surface Broker（Workbench host policy）
  - 去重、节流、优先级、生命周期
  - 尊重用户 pin/close/dirty/focus 决策
          ▼
SurfaceHost + Pane Store
  ├── native renderer：editor / tree / plan / goal / execution / pdf / browser
  └── sandbox renderer：现有 PluginView iframe
```

### 新增插件契约，而不是新增平行 Registry

建议在 Workbench adapter 层定义两个 typed extension point：

```text
WORKBENCH_SURFACE       cyrene.workbench.surface       application scope
WORKSPACE_FILE_TYPE     cyrene.workspace.file_type     application scope
WORKSPACE_ACTION        cyrene.workspace.action        application scope
```

`WORKBENCH_SURFACE` contribution 只包含可序列化描述：`id`、`renderer`、`acceptedActivities`、`resourceKinds`、默认优先级和生命周期。`renderer` 只能是宿主预注册的 native renderer id，或当前 pack 自己的 `frontend_view` id，不能是任意模块路径或脚本。

`WORKSPACE_FILE_TYPE` contribution 声明扩展名、MIME、是否可编辑、语言 id、默认 viewer，以及可选的复合 workspace。这样 `.tex/.bib/.sty/.cls/.bst` 不再继续硬编码进 `ProjectFileService`。

`WORKSPACE_ACTION` contribution 声明 `id`、`kind: build|run|test|preview`、适用文件/项目条件、owning pack RPC method、输出类型和默认 Surface。描述中不允许出现可直接执行的 shell 字符串；实际执行由 owning pack 的已注册 handler 完成，并经过现有权限和 pack operational guard。

现有 `frontend_views` 和 `project_tools` 保持兼容；`PluginApplicationHost.frontend_contributions()` 将新 contribution 一起序列化给 `PluginFrontendService`。禁用 pack 后 contribution 立即从可用目录消失，已打开 Pane 进入 unavailable 状态而不是继续调用失效服务。

工具资源语义保留在工具自身：

```json
{
  "resource_effects": [
    {"from": "arguments.path", "kind": "file", "access": "write"}
  ]
}
```

这类 metadata 不包含 UI 名称，因此可放在 host-neutral `Plugin`。事件投影使用 canonical plugin identity 解析它，并在 `tool.started/completed` 中加入 `presentation.locations`。自定义 Plugin 只要声明资源效果，就能自动获得通用文件/目录 Surface；只有需要专用复合 UI 时才贡献自己的 Surface。

### Surface Intent 协议

建议把 `surface.intent` 定义为 Workbench 内部归一化结果，而不是要求每个工具手写 UI 事件。常规工具由 `resource_effects + tool.*` 自动生成 intent；只有 Build/Run 产生复合输出或可预览 endpoint 时，才由 owning Plugin 显式发布：

```json
{
  "schemaVersion": 1,
  "type": "surface.intent",
  "chatId": "chat_x",
  "runId": "run_x",
  "actorId": "primary",
  "payload": {
    "intent": "reveal",
    "surface": "cyrene.code.editor",
    "resource": {
      "projectId": "project_x",
      "path": "src/app.py",
      "line": 120
    },
    "activity": "editing",
    "priority": "normal",
    "lifetime": "while-active",
    "focus": false
  }
}
```

关键规则：

- `surface` 必须来自当前启用 PluginPack 的 contribution；Pane controller 不再用大段 `kind === ...` 分支决定渲染。
- `resource` 必须经过服务端工作区 containment 校验；前端不能接受任意本地绝对路径。
- `focus: false` 是 Agent 的默认值。Agent 可以打开分屏，但不应抢走用户键盘焦点。
- 用户 pin 的 Pane 是用户拥有的；Agent 只能更新同一资源，不能替换它。
- 用户主动关闭某个自动 Surface 后，本次 run 内记为 suppressed，除非出现错误、权限请求等高优先级状态，否则不反复弹回。
- dirty editor 永远不能被 Agent 外部写入静默覆盖。
- 当前布局最多容纳四张卡片时，Broker 优先复用同资源 Pane、替换上一个未 pin 的自动 Pane；仍无位置则在资源栏显示活动徽标。
- intent 带 `packId` 与 `surfaceId`；pack 被禁用或启动失败时，Broker 不再创建该 Surface。

### Pane 状态应拆成用户层和 Agent 层

当前 pane layout 只保存卡片位置。建议每张卡片增加来源和所有权：

```text
PaneCard
  id, kind: surface, payload { schemaVersion, surfaceId, packId, resource, resourceKey }
  meta { origin, claimedByUser, pinned, autoClosePolicy, createdAt, lastIntentAt }
```

`dirty` 不应复制进 PaneCard，因为它会与编辑器 draft store 的真实状态失同步；Broker 通过 renderer 提供的 `canReplace(card)` 查询实时 dirty/运行状态。`origin` 记录创建来源，用户 pin、拖动或显式接管后设置 `claimedByUser`，防止后续 Agent intent 抢回所有权。

这样自动布局不会破坏用户已经整理好的分屏。迁移期保留现有 `file/viewer/plugin-view/...` card kind，通过 compatibility adapter 转成 Surface descriptor；新能力只创建 `kind: surface`，不再增加新的顶层 kind。

### 能力归属矩阵

| 能力 | Owning PluginPack | Plugin 提供 | Workbench 宿主提供 |
|---|---|---|---|
| 通用文件写入 | `core` | Write tool、`file/write` resource effect | 事件 enrich、版本检测 |
| 编辑器与文件树 | `cyrene_code` | Surface/file-type contribution、项目文件 service | 原生 renderer、Pane 策略、dirty buffer |
| 对话计划 | `cyrene_control` | plan tools、plugin session state、Plan Surface contribution | public snapshot 投影、交互容器 |
| 持续目标 | `cyrene_goal` | Goal tools、controller、恢复、Goal Surface contribution | Conversation execution port、run lease 基础设施 |
| 一键构建/启动 | `cyrene_code` + action provider pack | Action descriptor、执行 handler、诊断/产物/endpoint | 通用 execution service、终端进程、复合 Workspace renderer |
| 普通第三方 UI | 对应第三方 pack | `frontend_view`、project tool、RPC、可选 Surface contribution | sandbox iframe、启停 guard、Pane 策略 |

这个拆分避免两种反模式：一是把所有新逻辑继续堆进 `features/chat/page.jsx`；二是为了“插件化”把编辑器、文件树和 PDF 各复制一份到 iframe 插件中。

## 六项功能的具体设计

### 1. 复用 Cyrene 分屏

保留现有两列、每列最多两卡片的布局和拖拽交互，把 `wbcOpenPaneContent()` 上方增加 Dynamic Surface Broker。Broker 是 Workbench 宿主服务，不属于任何业务 Plugin：用户手动打开、拖拽、关闭的行为仍直接更新 Pane Store；Agent/Plugin 活动只能提交 intent，由 Broker 决定如何展示。

短期继续使用现有 `wbcPaneCard()` 和持久化格式，新增 `SurfaceHost`、`origin/pinned/dirty` 元数据和不覆盖用户卡片的 `revealSurface()`。`SurfaceHost` 读取 `PluginFrontendService` 已有的 pack snapshot，并维护一张编译时 native renderer 表；它不是新的后端能力目录。

迁移顺序是：先让 `file/viewer/plugin-view` 通过 adapter 进入 `SurfaceHost`，再迁移 plan/tree/goal/execution。Conversation、terminal、browser 等复杂现有 Pane 不需要为了本项目一次性重写。

### 2. 文件编辑 Agent 实时分屏

触发链路：

1. `core.Write` 和后续编辑工具在 `Plugin.metadata.resource_effects` 声明 `file/write`，不自行操作 UI。
2. 服务端事件投影根据 canonical plugin identity 和参数生成已校验的 file location。
3. `cyrene_code` 提供 `cyrene.code.editor` Surface contribution，Activity Normalizer 生成 reveal intent。
4. Broker 查找相同 `projectId + path` 的 Pane；存在则只更新 activity/highlight，不重复打开。
5. 首次写入或编辑开始时，在不抢焦点的前提下打开编辑器；工具完成后从服务端重新读取 version。
6. 若用户 buffer 未修改，应用外部版本并保持光标/滚动位置；若 buffer dirty，显示“Agent 已修改磁盘版本”三方冲突条，允许比较、合并、采用磁盘版，绝不自动覆盖。

推荐的刷新协议不是轮询文件，而是新增 `workspace.file_changed` 事件：

```json
{
  "projectId": "project_x",
  "path": "src/app.py",
  "version": "sha256",
  "modifiedNs": 123,
  "source": "agent",
  "runId": "run_x",
  "toolCallId": "tool_x"
}
```

保存服务已有 SHA-256 乐观锁，可以直接复用。`resource_effects.access=write` 的 Plugin 成功完成后，由 Workbench 的 PostToolUse/event adapter 计算并发布相同 version，使用户编辑和 Agent 编辑走同一个一致性模型。不要要求每个写文件 Plugin 重复实现通知逻辑。

### 3. 处理文件结构时显示文件树

把 Rail 里的目录读取逻辑抽成 `ProjectFileTreeSurface`，作为 `cyrene_code` 的 native Surface renderer；Rail 和 Pane 共用一个 `useProjectFiles(projectId, path/query)` 数据层。`cyrene_code` contribution 只负责声明 Surface 和匹配的 resource activity，Workbench 仍负责实际 React renderer 与布局策略。

`Grep`、目录列举、symbol/index 和 `analyze_structure` 等 Plugin 使用 `directory/read|scan` resource effect，不按工具显示名匹配。Surface 应：

- 以 Agent 当前操作路径的公共祖先目录为 root；
- 高亮当前扫描目录和最近访问文件，而不是不停跳转选择项；
- 展示 loading/扫描进度和结果数量；
- 允许用户点击文件后在相邻 Pane 打开 editor/viewer；
- 默认保持展开到当前路径，Agent 改变扫描范围时不清掉用户手动展开的节点；
- 大目录按需加载，不一次递归渲染整棵树。

### 4. Goal Loop 整合到对话

#### 对话模型

新增内置可选 PluginPack `cyrene_goal`，而不是给 Chat 聚合根继续增加 Goal 专用字段。目标状态写入 Conversation ContextTree 根节点的插件隔离区：

```text
_plugin_session_state.cyrene_goal
  schema_version
  goal
  controller_state
  child_context_ids[]
  public_snapshot { activeGoal, goalStatus }
```

其中 `goal` 的领域模型为：

```text
ConversationGoal
  id, chatId, objective
  status: discussing | proposed | active | paused | blocked | completed | cancelled
  acceptanceCriteria[]
  revision, approvedRevision
  limits { maxActiveSeconds, maxRepairRounds, token/cost budget }
  createdAt, updatedAt, completedAt
```

`cyrene_goal` 提供 main-only 工具 `set_goal`、`update_goal`，application service `conversation_goal`，以及 `cyrene.goal` Surface contribution。交互上新增“设定目标”入口和 `/goal` 命令；Workbench 仅在 pack operational 时显示入口。用户与 Agent 的讨论仍是普通消息，只有用户确认某一 revision 后才进入 `active`，确认前不启动持续循环。

Goal 卡片应作为对话中的消息级事件出现，同时有独立 Goal Surface 供查看当前目标、验收标准、限制和停止按钮。修改目标会创建新 revision，不静默改写已确认目标。禁用 `cyrene_goal` 时，宿主暂停 active controller 并保留插件状态；Conversation 本身仍可正常聊天。

#### 执行模型

不要让 ChatRunManager 和 GoalLoopManager 各自争抢同一个 Chat。`cyrene_goal` 的 application service 持有 `RunCoordinator("chat", chatId)` 的 goal-loop lease，再通过宿主提供的 `ConversationExecutionPort` 启动/恢复一次 Conversation Agent turn：

```text
讨论目标 → 确认 → 生成/确认计划
                    ↓
              执行一个步骤
                    ↓
               独立验证步骤
          ┌─────────┴─────────┐
        通过                  未通过
          │              反思并重试/修复
          ▼                    │
    还有步骤？──是──────────────┘
          │否
          ▼
       验收整个目标
          ├─通过 → completed
          └─失败 → 生成修复步骤 → 继续循环
```

现有 Goal Loop 的租约、重启恢复、逐步验证、反思、修复轮次、预算和等待用户输入逻辑迁入 `cyrene_goal` 的端口化 engine；宿主只提供通用 Conversation 执行能力：

```text
GoalOwnerPort       读写 Goal/Plan/事件 projection
ExecutionPort       start/resume/cancel Agent turn
PlanningPort        生成和修订计划
VerificationPort    验证步骤与最终验收
NotificationPort    通知 paused/blocked/completed
```

`GoalOwnerPort` 使用 plugin session state，`ExecutionPort` 由 Workbench 注入，Planning/Verification 默认通过正常 Plugin Agent run 实现。`cyrene_goal` 在启动 Goal 时延迟解析 `cyrene_control` 提供的 plan service；服务不可用则将 Goal 标记为 blocked/capability-unavailable，而不是在 pack attach 顺序上形成隐式依赖。`cyrene_task` 在迁移期只保留 legacy adapter 和数据转换工具，不再承载新目标；迁移完成后删除整个 pack。

因为 `cyrene_goal` 有 application lifecycle，它应在 `on_startup` 扫描并恢复 active/paused controller，在 `on_shutdown` 停止调度并持久化稳定边界。pack 启停直接复用 `PluginApplicationHost` 的 operational/running 语义，不另造后台服务管理器。

“完成前一直循环”必须有安全出口：用户停止、等待权限/回答、预算耗尽、最大运行时间、最大修复轮次、连续无法验证、基础设施错误。除这些明确出口外，普通一轮回复结束不能终止 active Goal；Controller 应自动启动下一轮。

用户在循环期间发消息时，需要分类为：回答 pending request、给当前步骤 guidance、修订目标、暂停/停止。不能按普通新 run 处理，否则会与 loop lease 冲突。

### 5. 将任务计划步骤整合为对话分屏

Chat 已有 `activePlan`、`enter_plan_mode`、`update_plan_progress` 和 `WbcPlanTab`；这些能力的 owning pack 已经是 `cyrene_control`，因此应扩展该 pack，而不是移植 Task 页面或在 Goal 插件里复制一套计划工具。

当前 `update_plan_progress` 通过回溯 `enter_plan_mode` 的 tool result 寻找计划，适合迁移但不适合作为长期状态源。应把计划写入：

```text
_plugin_session_state.cyrene_control
  plan
  public_snapshot { activePlan }
```

Chat DTO 继续暴露 `activePlan` 作为兼容 projection，因此大部分前端不需要同步重写。

Plan schema 建议补齐：

```text
planId, goalId, revision, approvedRevision, status
steps[]:
  id, title, description, status, dependsOn[]
  currentAction, relatedFiles[], progressEvents[]
  error, startedAt, completedAt
acceptanceCriteria[]
```

`cyrene_control` 注册 `cyrene.control.plan` native Surface。Plan Surface 是一级 Pane：

- plan 提议时自动 reveal，但不抢焦点；
- 用户可在对话内批准/要求修改，也可在 Surface 编辑未开始步骤；
- 正在执行的步骤固定高亮，并显示 currentAction、相关文件和验证状态；
- `plan_progress` 只 patch 对应 step，不替换整份 plan，避免乱序事件回退状态；
- 用户关闭后只保留顶栏/资源栏进度徽标，本 run 不反复弹出；
- Goal active 时 Plan Surface 默认与 Conversation 各占一列。

### 6. 通用一键构建/启动工作区

不要为 TeX 创建专用执行框架。TeX 编译、Web 应用开发服务器、Python 服务、桌面应用、测试和静态站点预览，本质上都可以建模为 Workspace Action。

#### Workspace Action 契约

每个 provider pack 通过 `WORKSPACE_ACTION` 贡献动作：

```text
WorkspaceActionDescriptor
  id, packId
  kind: build | run | test | preview
  title, icon
  matches { fileExtensions[], projectMarkers[] }
  method                    owning pack 的 frontend RPC method
  capabilities { cancel, restart, watch }
  outputs[]                 artifact | endpoint | diagnostics | terminal
  defaultSurface
```

descriptor 只能引用 owning pack 已注册的方法，不能包含 shell command、任意模块路径或前端脚本。provider handler 负责解析项目、验证工作区边界、选择 executable 和构造 argv；用户点击动作、Agent 请求动作都继续经过现有权限审查。

项目扫描只生成“建议动作”。第一次执行需要用户确认，确认后的 action profile 作为项目级持久状态保存。不要看见 `package.json` 就静默启动服务器，也不要执行未经确认的项目配置命令。

#### 通用 Execution 模型

`cyrene_code` 提供 `workspace_execution` application service，统一管理有限构建任务和长驻运行进程：

```text
WorkspaceExecution
  id, projectId, actionId, packId
  status: queued | running | ready | succeeded | failed | stopping | stopped
  entry, cwd, startedAt, completedAt
  diagnostics[], artifacts[], endpoints[], terminalId

runtime events:
workspace.execution.started
workspace.execution.progress
workspace.execution.diagnostics
workspace.execution.ready
workspace.execution.artifact
workspace.execution.completed | failed | stopped
```

- Build/Test 通常是有限任务，结束后保留 diagnostics 和 artifacts；
- Run/Preview 可以成为长驻进程，复用 `cyrene_code` 已有 managed terminal，支持 stop/restart 和日志；
- provider pack 负责技术栈检测、argv 和日志解析，execution service 负责状态、并发、取消、事件和恢复边界；
- 同一个 action profile 默认只有一个 active execution，重复点击转为 reveal、restart 或明确询问；
- Agent 修改关键文件后可以提示重新 build/restart，但除非用户启用 watch/auto-run，不自动执行。

#### 通用 Workspace Surface

使用一个数据驱动的 `cyrene.code.execution` native Surface，而不是 `tex-workspace`、`web-workspace` 等多个硬编码 Pane：

```text
┌─────────────────────────────┬─────────────────────────────┐
│ Source / File tree          │ Output                      │
│ Editor                      │ PDF / Browser / Image/File  │
│                             │                             │
├─────────────────────────────┴─────────────────────────────┤
│ Build/Run/Test · status · diagnostics · terminal/log     │
└───────────────────────────────────────────────────────────┘
```

输出路由完全根据 execution result：

- PDF artifact → 现有 PDF.js；
- HTTP endpoint → 现有 Browser Surface；
- 图片或普通文件 → 现有 Viewer；
- diagnostics → 通用问题列表，点击跳转 `file:line`；
- 没有图形输出 → Terminal/log；
- 多种输出 → 使用标签页，不增加新的 Pane kind。

工具栏从可用 contribution 动态生成 Build、Run、Test、Preview、Stop、Restart。点击前先保存当前 dirty buffer；窄窗口把 Source/Output 切成标签页，底部日志可折叠。

#### TeX 和应用开发只是 provider 示例

| 项目类型 | Action | 输出 | 复用的 Surface |
|---|---|---|---|
| TeX | Build | PDF artifact、diagnostics | Editor + PDF.js + diagnostics |
| Web app | Run / Build | HTTP endpoint、dist artifacts、terminal | Editor/tree + Browser + terminal |
| Python service | Run / Test | endpoint 或 terminal、test diagnostics | Editor + Browser/terminal + diagnostics |
| Rust/C/C++ | Build / Run / Test | binary、terminal、compiler diagnostics | Editor + terminal + diagnostics |

TeX 仍可由 provider 声明 `.tex/.bib/.sty/.cls/.bst` file type、解析编译日志，并通过 `cyrene_extensions` 获取 `pdflatex/xelatex/lualatex`；但它使用与应用开发完全相同的 Action、Execution、artifact、diagnostics 和 Surface 协议。SyncTeX 等格式特有增强可以作为可选 provider capability，不能进入通用执行核心。

## 去除 Task 的迁移策略

### 不能直接删除的部分

以下名称虽然带 Task，但能力应先泛化或迁移：

- `workbench/goals/*`：迁入 `cyrene_goal` 的通用 engine；
- `TaskAgentRuntime` 中的规划、验证、反思逻辑：拆到通用 ports；
- Task plan 的 dependencies、acceptance、repair 数据结构；
- RunCoordinator、租约、崩溃恢复和 budget 处理；
- Task artifacts/file changes 的历史数据。

迁移后 Workbench 不 import `cyrene_goal` 的实现模块，只通过 application service、runtime events、public snapshot 和 Surface contribution 使用它。反向也一样：`cyrene_goal` 不 import Chat HTTP/UI，实现只依赖注入的 Conversation ports。

### 最终可删除的产品壳

在 Conversation 达到功能对等后，可以删除或下线：

- `frontend/features/task/*`；
- Chat rail 的 task/chat 切换、`rail-tasks.jsx`、Task pane controller；
- Task Board、Create Task modal、Convert to Task；
- `/api/task-sessions/*` 写接口和 Task session route；
- `plugins/builtin/cyrene_task`，由 `cyrene_goal` 和现有 `cyrene_control` 替代；
- `workbench/tasks/*` 中只负责 Task UI projection 和 workflow 的代码；
- `workbench_task_sessions` 主写模型。

### 历史数据迁移

建议采用一版“读旧写新”的过渡：

1. 新版本只创建 Conversation，不再创建 Task。
2. 首次打开旧 Task 时生成对应 Chat，记录 `legacyTaskId`。
3. 映射 `goal → ConversationGoal`，`plan/acceptance → ActivePlan + Goal`，`artifacts/fileChanges → Chat artifacts/change sets`。
4. Task event timeline 生成一条只读迁移摘要和可展开原始记录；不要伪造为用户/Agent 对话消息。
5. 原 Task ContextTree 和 session row 保留只读一个兼容周期，确保审计和失败回滚。
6. Goal Loop 正在运行的旧 Task 不在线迁移；先恢复/暂停到稳定边界，再显式迁移。
7. 完成迁移验证和备份后，下一主版本才删除旧表与只读 API。

旧 Task 的迁移器可以暂时放在 `cyrene_task` pack 中，并通过 application service 调用 `cyrene_goal` 的导入命令。它不应进入 Conversation 核心，也不应让新 Goal 写回旧 Task 表。

## 阶段一详细实现方案

### 阶段目标与明确边界

阶段一只建立 Dynamic Surface 的插件契约和宿主底座，完成以下闭环：

```text
PluginPack typed contribution
        ↓
PluginApplicationHost（只发布 operational pack）
        ↓
/api/plugins 单一 snapshot
        ↓
PluginFrontendService
        ↓
Activity Normalizer（消费标准化 location/显式 intent）
        ↓
Dynamic Surface Broker（纯布局策略）
        ↓
kind: surface → SurfaceHost → native renderer / PluginView
```

这一阶段不做真实的 Write/Grep 自动分屏，不改 Goal/Task，不实现 Workspace Execution，也不改 `ProjectFileService` 的硬编码文件类型判断。真实工具事件 enrich、编辑器刷新、文件树和 Plan renderer 进入阶段二；Action 执行进入阶段四。阶段一通过测试用 synthetic intent 跑通整个前端闭环。

### 1. Python 插件契约

新契约放在 Workbench 插件 adapter，而不是 `cyrene.core.plugin.extensions`。Core 只保留通用 `ExtensionPoint` 机制和 `Plugin.metadata.resource_effects`；Surface、文件类型和 Action 都是 Workbench 对插件的解释。

建议在 `cyrene.plugins.contributions` 定义三个不可变 dataclass 和 extension point：

```python
WORKBENCH_SURFACE = ExtensionPoint[WorkbenchSurfaceContribution](
    "cyrene.workbench.surface", PluginScope.APPLICATION, ...
)
WORKSPACE_FILE_TYPE = ExtensionPoint[WorkspaceFileTypeContribution](
    "cyrene.workspace.file_type", PluginScope.APPLICATION, ...
)
WORKSPACE_ACTION = ExtensionPoint[WorkspaceActionContribution](
    "cyrene.workspace.action", PluginScope.APPLICATION, ...
)
```

`WorkbenchSurfaceContribution` 的最小字段：

```text
id                    pack 内局部 id
title, i18n           展示文本
renderer.kind         native | plugin_view
renderer.id           宿主 renderer id，或本 pack frontend_view id
acceptedActivities[]  read | write | scan | plan | goal | build | run | test | preview
resourceKinds[]       file | directory | plan | goal | execution | endpoint | artifact
priority              background | normal | urgent
lifetime              while-active | run | sticky
preferredSide         left | right | either
```

`WorkspaceFileTypeContribution` 的最小字段：

```text
id                    pack 内局部 id
extensions[]          统一小写、必须以 . 开头
mimeTypes[]           可选
languageId            可选编辑器语言 id
editable              boolean
defaultSurface        可选 canonical surface id
```

`WorkspaceActionContribution` 在阶段一只进入能力目录，不可执行：

```text
id                    pack 内局部 id
title, i18n
kind                  build | run | test | preview
method                owning pack 已注册的 frontend RPC method
appliesTo             fileTypeIds / extensions / markerFiles
outputs[]             diagnostics | artifact | endpoint | terminal
defaultSurface        可选 canonical surface id
```

Action descriptor 不能包含 command、shell、argv 或 environment。阶段四执行时只能用 `pack_id + method` 调用 owning pack 的 handler，并再次检查 pack operational 和权限。

所有 contribution id 在作者侧是局部 id；服务端发布时生成 canonical id：`<pack_id>/<local_id>`，同时保留 `pack_id` 和 `local_id`。跨 pack 引用只能使用 canonical id，避免两个插件都声明 `editor` 或 `build` 时互相覆盖。

`renderer.kind=plugin_view` 必须引用当前 pack 已存在的 `frontend_views`；`renderer.kind=native` 只允许一个短 renderer id，是否可渲染由前端编译时 allowlist 决定。后端不接受任意 JS 模块路径，前端遇到未知 native renderer 显示 unavailable，不动态 import 插件脚本。

现有 `metadata.frontend_views` 和 `metadata.project_tools` 不迁移、不改返回结构。新 typed contribution 使用 `PluginPack.contributions`，`validate_workbench_contributions()` 同时校验旧 metadata 和新 contribution 的交叉引用。这样已安装插件不需要升级。

### 2. `resource_effects` 契约

资源语义属于工具本身，继续放在 host-neutral `Plugin.metadata`。第一版只支持确定、可静态校验的参数路径，不实现 JSONPath 或表达式：

```python
metadata={
    "resource_effects": ({
        "argument_path": ("path",),
        "kind": "file",
        "access": "write",
        "phase": "started",
    },),
}
```

字段约束：

- `argument_path` 是一到四段的字符串数组，只从工具参数对象取值；
- `kind` 第一版只允许 `file | directory`；
- `access` 只允许 `read | write | scan | execute`；
- `phase` 允许 `started | completed | both`；
- metadata 只是 presentation hint，不授予文件访问、执行或 UI 权限。

校验放到 `Plugin.__post_init__()` 调用的独立 helper 中，保证 pack load 时失败，而不是工具运行后才发现 schema 无效。阶段一只验证、暴露解析 helper；阶段二再把解析结果注入 `tool.started/completed.payload.presentation.locations`。

后续 Activity Normalizer 还必须先解开 `toolbox(operation=invoke)`：用 `arguments.name` 解析真实 canonical Plugin，并以 `arguments.arguments` 作为 effect 输入。不能按被用户改过的工具显示名、活动文案或自然语言猜测资源。

### 3. 服务端发现与序列化

不新增 `/api/plugin-contributions`，继续使用现有 `/api/plugins` 作为原子 snapshot：

```json
{
  "frontend_views": [],
  "project_tools": [],
  "workbench_surfaces": [],
  "workspace_file_types": [],
  "workspace_actions": []
}
```

`PluginApplicationHost.frontend_contributions()` 扩成更中性的 `workbench_contributions()`，旧方法保留为兼容别名一个版本。聚合规则：

1. 只遍历 `pack_operational(pack.id)` 为真的 pack；
2. 在服务端添加 `pack_id/local_id/canonical id`，忽略插件作者伪造的 ownership 字段；
3. 校验同一 snapshot 内 canonical id 唯一；
4. `plugin_view` renderer 必须关联本 pack 的有效 view；
5. pack disable、startup failure 或 reload 后，下一次 snapshot 立即移除其新 contribution；
6. 已打开 Surface 描述仍保留，前端渲染 unavailable 状态，不再调用该 pack RPC。

`plugin_registry_status()` 只增加三个数组，不改变 Settings 里的 pack/plugin 启停模型。第一阶段也不把 file type 注入 `ProjectFileService`，避免同时改写文件读取安全边界；阶段二建立统一 file-type catalog 后再替换前后端两套扩展名判断。

### 4. 前端能力目录与 `SurfaceHost`

扩展 `PluginFrontendService` snapshot：

```text
workbenchSurfaces[]
workspaceFileTypes[]
workspaceActions[]
```

并增加纯查询方法 `surface(id)`、`fileTypeFor(path, mime)`、`actionsFor(resource)`。查询只读当前 snapshot，不复制另一套全局 registry。`cyrene:plugins-changed` 后 SurfaceHost 重新解析 descriptor；找不到 contribution 时进入 unavailable。

新增 `features/chat/dynamic-surfaces.jsx`，包含：

- `NATIVE_SURFACE_RENDERERS`：编译时 renderer allowlist；
- `wbcNormalizeSurfaceDescriptor()`：验证来自 snapshot/intent 的可序列化描述；
- `wbcSurfaceResourceKey()`：生成稳定去重 key；
- `WbcSurfaceHost`：统一选择 native renderer 或现有 `PluginView`；
- `WbcUnavailableSurface`：pack 禁用、renderer 未注册或 descriptor 损坏时的安全降级。

SurfaceHost 不负责打开/关闭 Pane，不访问布局 state。它只接收 descriptor、当前 plugin snapshot 和 renderer props。阶段一可先注册两个低成本 native renderer：`legacy-file`、`legacy-viewer`，直接复用现有 `WbcArtifactSplit`；`plugin_view` 继续复用 sandboxed `PluginView`。这证明 native 和 iframe 两条渲染路径都能工作，同时避免立即迁移 terminal/browser/task 等复杂 Pane。

主窗口 `page.jsx` 和脱离窗口 `context-panel.jsx` 都只增加一个 `kind === "surface"` 分支并调用同一个 `WbcSurfaceHost`。不要分别实现两个 renderer switch。Electron 的 `normalizeDetachedPaneDescriptor()` 已允许任意合法 kind，新增的只是小型 `meta` 字段白名单、大小限制和返回窗口恢复透传。

### 5. Surface card 与布局兼容

`wbcPaneCard()` 增加可选 `meta`，旧调用结果保持兼容：

```json
{
  "id": "surface:cyrene_code/editor:project-1:src/app.py",
  "kind": "surface",
  "payload": {
    "schemaVersion": 1,
    "surfaceId": "cyrene_code/editor",
    "packId": "cyrene_code",
    "resource": {"kind": "file", "projectId": "project-1", "path": "src/app.py"},
    "resourceKey": "project-1:file:src/app.py",
    "activity": "write"
  },
  "ownerChatId": "chat-1",
  "meta": {
    "origin": "agent",
    "claimedByUser": false,
    "pinned": false,
    "autoClosePolicy": "run-end",
    "createdAt": 0,
    "lastIntentAt": 0
  }
}
```

旧卡片没有 `meta` 时一律视为用户拥有，绝不能被 Agent 自动替换。`wbcNormalizePaneLayout()` 保留未知字段但对 `meta` 做白名单归一化。Surface payload 带 `schemaVersion: 1`，因为当前 layout 本身只存在 React 内存，不值得先引入完整持久化迁移；脱离窗口则按相同版本传输。

`dirty` 和长驻执行状态由 renderer 的 live state 决定，不写进 card。Broker 接收 `canReplace(card)` callback；editor draft dirty、execution running、permission pending 时返回 false。阶段一的 legacy renderer 用现有 `WBC_PROJECT_FILE_DRAFTS` 实现 dirty guard。

给自动 Surface 的 grip menu 增加 Pin/Unpin。用户拖动自动卡片时设置 `claimedByUser=true`；用户关闭时由 Broker suppression store 记录 `{runId, resourceKey}`。suppression 第一版只保存在页面内存，随 run 结束清理，不跨会话永久屏蔽。

### 6. Dynamic Surface Broker

Broker 放在独立 `features/chat/dynamic-surface-broker.mjs`，实现为无 React、无 DOM、无 fetch 的纯函数，便于 Node 原生测试。手动 `wbcOpenPaneContent()` 保持原行为；只有 Agent/Plugin intent 调用 `wbcRevealSurface()`。

输入：

```text
layout, intent, surfaceCatalog
isSuppressed(runId, resourceKey)
canReplace(card)
now
```

输出：

```text
{ layout, outcome, cardId, reason }
outcome = updated | opened | replaced | suppressed | deferred | unavailable
```

确定性的仲裁顺序：

1. surface 不存在或 owning pack 不 operational → `unavailable`；
2. 当前 run 已 suppress → `suppressed`；
3. 已有相同 `resourceKey + compatible surface` → 更新 activity/时间，不移动、不聚焦；
4. 有空列/空槽 → 按 `preferredSide` 放入，但不重排现有卡片；
5. 已满四卡 → 只在 `origin=agent && !claimedByUser && !pinned && canReplace` 的卡片中选择最久未活动者替换；
6. 没有安全位置 → `deferred`，由资源栏/活动提示显示，不改变布局；
7. `focus` 默认且最高只能为 false；阶段一不允许 intent 抢键盘焦点。

自动关闭也走 Broker：`run-end` 只关闭仍为 Agent 所有、未 pin、未 dirty、未被用户接管的 Surface；`idle` 在第一阶段只定义，不启用 timer；`never` 等同用户保留。

Activity Normalizer 在阶段一只接受两种可信输入：服务端已带 `presentation.locations` 的标准 tool event，或 owning pack 发布且通过 catalog 校验的显式 `surface.intent`。它输出 broker intent，不直接调用 `setPaneLayoutsByChat`。阶段二再接入实际工具 event enrich；第一阶段用 synthetic event 验证 normalizer/broker/host 的组合。

### 7. 文件级修改顺序

建议分成五个可独立 review 的提交：

| 提交 | 主要文件 | 结果 |
|---|---|---|
| 1. typed contracts | `src/cyrene/plugins/contributions.py`、`src/cyrene/plugins/__init__.py`、`src/cyrene/core/plugin/resource_effects.py`、`src/cyrene/core/plugin/plugin.py` | dataclass、extension point、resource effect 校验与导出 |
| 2. discovery snapshot | `src/cyrene/plugins/application.py`、`src/cyrene/workbench/http/plugins.py`、`src/cyrene/workbench/webui/frontend/platform/plugins.jsx` | operational contribution 通过现有 `/api/plugins` 到达前端 |
| 3. pure surface core | 新增 `dynamic-surface-broker.mjs`、`dynamic-surfaces.jsx`、对应 `.test.mjs` | descriptor、resource key、normalizer、broker、SurfaceHost |
| 4. Pane integration | `drag-layout.jsx`、`pane-layout-controller.jsx`、`page.jsx`、`split-pane.jsx`、`pane-card-drag-controller.jsx`、`pane-detachment.jsx`、`context-panel.jsx`、`electron/main.js` | `surface` 卡片、pin/claim、脱离与恢复、unavailable 状态 |
| 5. compatibility/docs | `cyrene_plugin_development/tools.py`、`tests/conftest.py`、插件示例与开发文档 | 脚手架可生成新 contribution，旧 view/project tool 不回归 |

提交 3 必须先于 `page.jsx` 接入，避免继续扩大已经很长的 `renderPaneCard()`。新增行为进入小模块；`page.jsx` 只负责把 React state updater 和 renderer props 接上。

### 8. 一次性测试与验收

完成全部修改和自审后，再运行一组聚焦测试：

```bash
uv run pytest \
  tests/test_plugin_frontend_views.py \
  tests/test_workbench_frontend_logic.py \
  tests/test_workbench_bridge.py

cd src/cyrene/workbench/webui
npm test
npm run build
```

其中新增测试至少覆盖：

- contribution dataclass 非法 id、重复 id、错误 renderer、跨 pack 伪造 ownership 会在加载/聚合时失败；
- `/api/plugins` 只返回 operational pack 的 surface/file type/action，旧字段保持不变；
- pack disable/reload 后 snapshot 移除 contribution，已打开 Surface 渲染 unavailable；
- `resource_effects` 参数路径和枚举严格校验，metadata 不获得任何权限；
- 相同 resource intent 去重且不移动 Pane；
- 空位打开、满位只替换可替换 Agent 卡、用户卡/pin/claimed/dirty 卡均受保护；
- 用户关闭后的同 run intent 被 suppressed，下一 run 可重新 reveal；
- `focus=true` 不会造成 DOM focus 变化；
- `surface` 在主窗口和 detached window 使用同一 host，往返后 descriptor/meta 不丢；
- `file/viewer/plugin-view` 的现有手动分屏、拖拽和脱离行为不回归；
- frontend build 不新增 architecture complexity baseline 中的大函数。

阶段一最终验收用一个测试插件完成：它声明一个 sandbox view、一个 native legacy-file Surface、一种文件类型和一个不可执行 Action。测试页面可提交 synthetic `file/write` intent，确认首次在空位打开、重复 intent 只更新、pin 后不会被替换、禁用 pack 后显示 unavailable。这个闭环通过后，阶段二才接入真实 Write/Grep/Plan 数据源。

### 9. 阶段一不应做的事情

- 不把 Agent 的自然语言、工具显示名或 CSS class 当作资源识别协议；
- 不允许 Plugin 直接 import Workbench React module 或写 `paneLayoutsByChat`；
- 不用 Action descriptor 传 shell 命令；
- 不在 `workbench_events()` 这个纯投影函数里访问全局 Plugin host；
- 不同时重构所有旧 Pane kind；
- 不先做 layout 持久化。若产品后续确认需要跨重启恢复整个分屏，再单独设计 versioned Pane repository；阶段一只保证 Surface descriptor 可序列化、detached round-trip 稳定。

## 推荐实施顺序

### 阶段一：插件契约与 Dynamic Surface 底座

- 定义 `WORKBENCH_SURFACE`、`WORKSPACE_FILE_TYPE`、`WORKSPACE_ACTION` 和 `resource_effects`。
- 扩展 `/api/plugins` snapshot 与 `PluginFrontendService`，继续兼容现有 views/project tools。
- 实现 `SurfaceHost`、Activity Normalizer、Dynamic Surface Broker 和唯一的新 `surface` card kind。
- 给 PaneCard 增加 origin/pinned/dirty/autoClosePolicy，并接入 pack operational 状态。
- 为旧 file/viewer/plugin-view 建 compatibility adapter，保证现有手动分屏不回归。

完成标准：Plugin 能声明资源、文件类型、Action 和 Surface；Agent/Plugin 只能提交 intent，无法覆盖用户拥有的 Pane。

### 阶段二：动态文件工作区与对话计划

- 为 `core.Write`、`Grep` 和 `cyrene_code` 结构工具补 resource effect metadata。
- 实现 `workspace.file_changed`、editor 实时刷新和 dirty-buffer 冲突处理。
- 抽取 Rail/Pane 共用的文件树，并根据 scan/read activity 自动 reveal。
- 将 `cyrene_control` plan 迁入 plugin session state，保留 `activePlan` public projection。
- 注册 `cyrene.control.plan` Surface，支持批准、patch 进度、相关文件和恢复。

完成标准：Agent 编辑文件、浏览结构和执行计划时，分屏实时跟随且不抢焦点、不抖动、不丢状态。

### 阶段三：Goal Loop 与去 Task 化

- 新建 `cyrene_goal`，提供 Goal tools、状态、application lifecycle、恢复和 Goal Surface。
- 通过 Conversation execution port 跑通规划、执行、验证、反思、修复、预算和等待用户输入。
- `cyrene_task` 降级为只读 legacy/migration pack；停止创建新 Task。
- 迁移旧 Task 为 Conversation + `cyrene_goal`/`cyrene_control` state。
- 移除 Task rail、Board、页面、创建和转换入口，保留一版只读兼容数据。

完成标准：用户只使用 Conversation；active Goal 在验收前持续循环，旧 Task 可安全迁移、失败可回滚。

### 阶段四：通用一键构建/启动与最终收尾

- 在 `cyrene_code` 实现通用 `workspace_execution` service，支持 Build、Run、Test、Preview、Stop、Restart。
- 允许 provider pack 通过 `WORKSPACE_ACTION` 贡献技术栈动作，不允许 descriptor 携带可执行 shell。
- 复用 managed terminal 承载长驻进程，统一发布 progress、ready、diagnostics、artifact 和 endpoint 事件。
- 实现 `cyrene.code.execution` 通用复合 Surface，根据输出自动组合 Editor/File tree、Browser、PDF.js、Viewer、Terminal 和 Diagnostics。
- 首批 provider 覆盖 TeX build、Web app run/build、Python run/test，用同一套协议验证通用性。
- 完成插件启停、权限、取消/重启、崩溃恢复、自动运行策略和旧 Task 数据清理验收。

完成标准：支持从当前文件或项目一键编译、测试、启动、停止和预览；TeX 与应用开发不存在两套执行或分屏体系。

## 首个可交付版本的验收标准

1. 用户只有 Conversation，不需要选择 Chat 或 Task。
2. Agent 编辑文件时，相同文件自动在分屏出现；重复编辑不抖动、不抢焦点、不覆盖用户 dirty buffer。
3. Agent 扫描目录或项目结构时，文件树 Pane 显示当前范围和最近访问路径。
4. 用户可在对话中讨论、确认、修订和停止 Goal；确认后普通 turn 完成不会终止 Goal Loop。
5. Plan 在分屏中实时更新，刷新应用或进程重启后状态不丢失。
6. Goal 只有在验收通过时自动 completed；预算、权限、阻塞和用户停止会进入明确的 paused/blocked/cancelled 状态。
7. 项目可从统一工具栏一键 Build/Run/Test/Preview；长驻进程可停止和重启，有限任务能流式显示 diagnostics/artifacts/endpoints。
8. 旧 Task 可迁移且历史可追溯，迁移失败不会删除原数据。
9. 所有自动 Surface 都遵守键盘焦点、可见 focus ring、reduced motion 和用户关闭/pin 决策。
10. 禁用 `cyrene_goal` 后普通 Conversation 不受影响，active Goal 安全暂停；重新启用并重启到 operational 后可以恢复。
11. 禁用/启动失败的 Surface owning pack 不会继续调用 RPC；已打开 Pane 显示 unavailable，用户可关闭或等待恢复。
12. 第三方 Plugin 无法直接修改 Pane Store，也不能用 contribution 加载主上下文脚本或打开工作区外路径。

## 最需要优先解决的四个技术风险

1. **双运行协调器冲突**：Goal Loop 不能作为另一个普通 ChatRun 叠在 ChatRunManager 上；必须由一个 owner 持有 chat lease，并通过明确 execution port 驱动每轮。
2. **用户编辑与 Agent 写入冲突**：实时展示不是简单重新 fetch。必须以当前 SHA-256 version、dirty buffer 和三方冲突状态为准。
3. **自动分屏破坏用户布局**：当前 `wbcOpenPaneContent()` 会把目标 side 直接替换成新卡片。Agent 自动行为必须全部经过 Broker，不能直接调用它的覆盖路径。
4. **Plugin contribution 与运行状态失配**：application setup 的 Python 变化当前可能要求重启，而 HTML 资源可直接刷新。SurfaceHost 必须以 `pack_operational` 为准，不能只缓存启动时 snapshot；pack disable/reload/startup failure 都要让 Surface 失效但保留可恢复 Pane 描述。

总体判断：第一步不是实现某个 Surface，而是补齐 Plugin 的 `resource_effects`、Surface、file-type 和 Workspace Action contribution。之后由 Workbench 实现 Broker/SurfaceHost，再把 `cyrene_control` Plan、新的 `cyrene_goal` 和通用 Workspace Execution 接入，最后移除 Task。这样 Task 删除最终只是移除 legacy pack、compatibility adapter 和 UI 壳；TeX、Web 与 Python 项目则共同验证一键构建/启动体系是否真正通用。
