# Cyrene 无 Loop Agent Kernel 重构设计

> 状态：与当前设计和实现对齐的实施文档
>
> 更新日期：2026-08-26
>
> 新内核目录：[`src/agent`](../src/agent/)
>
> 当前阶段：Context Tree 与 Hook 基座已经实现；Plugin、Model、Tool 与前端兼容层尚待逐步接入

## 1. 重构后的设计与架构

### 1.1 一句话定义

重构后的 Cyrene Agent Kernel 只由三个概念组成：

> **Context Tree 保存状态，Hook 观察状态变化，Plugin 决定执行什么以及是否把结果重新挂载到树。**

模型、工具、Ask User、权限审批、计划确认、Subagent、Terminal、媒体、Browser、Office、Scheduler
都不是内核中的特殊状态。它们都是 Plugin；它们读取一条上下文路径，执行有限工作，并在需要继续主线时把结果挂载回 Context Tree。

Context 变化不会直接调用模型。准确关系是：

```text
Context Tree committed mutation
            ↓
durable Hook delivery
            ↓
Plugin callback
            ↓
Plugin decides whether/how to mount a result
            ↓
next committed mutation
```

只要为 `ContextChange` 注册 Model Plugin，就能形成“上下文被动更新后触发模型”的行为；Hook 本身不理解模型，也不会自动挂载 Plugin 返回值。

### 1.2 目标架构总图

```text
 User / Workbench / CLI / Scheduler / Channel / Resource callback
                              │
                              ▼
                         Plugin input
                              │
                              ▼
┌──────────────────── Context Tree ────────────────────┐
│ opaque JSON nodes │ parent/children │ path/subtree   │
│ time              │ one tree DB     │ one tree lock  │
│                                                     │
│ ordinary node mutation + matched Hook deliveries    │
│ are committed in the same SQLite transaction        │
└──────────────────────────┬───────────────────────────┘
                           │ durable queue
                           ▼
┌──────────────────── Tree-local HookSet ──────────────┐
│ persistent bindings │ ordered worker │ recovery      │
│ root_only           │ failed/blocked │ Plugin lookup │
└──────────────────────────┬───────────────────────────┘
                           │
                           ▼
┌──────────────────────── Plugins ─────────────────────┐
│ Model │ Tool │ Human input │ Permission │ Subagent   │
│ Terminal │ Media │ Browser │ Office │ Skill │ Hook   │
│                                                     │
│ Plugin owns interpretation, I/O, retry and mounting │
└──────────────────────────┬───────────────────────────┘
                           │ optional mount/update/delete
                           └──────────────► Context Tree

 Observation/streaming ──► frontend compatibility adapter
 External Agent / ACP ──► independent runtime boundary
```

### 1.3 内核中只有三个元素

| 元素 | 负责 | 明确不负责 |
|---|---|---|
| Context Tree | 保存通用 JSON 上下文、树关系、时间；事务化修改；在同一数据库记录 Hook 绑定和待执行通知 | 不识别 message/tool/subagent 类型；不决定谁被调用；不做跨树合并 |
| Hook | 将事件匹配到持久化绑定；按树串行执行；恢复未完成通知；提供 `root_only`、工具前后置和生命周期入口 | 不自动挂载 Plugin 返回值；不判断业务步骤；不保存 Python 函数 |
| Plugin | 解释上下文、调用模型或工具、等待外部结果、决定是否及如何修改树 | 不能绕过 Context Tree 直接伪造已提交状态；不能假设只会执行一次 |

这套设计不再引入统一 Reducer、Effect、Branch、Workflow DSL 或大量领域事件。分支直接由树结构表达，等待直接由未完成的 Plugin/外部资源表达，后续进展通过一次新的树修改恢复。

### 1.4 “无 loop”的准确含义

无 loop 不等于代码里不能出现循环。SQLite 消费、流式响应、PTY、Scheduler 和队列工作器当然需要循环。

这里的含义是：

- 不再由一个长期存活的 `while model wants tool` 业务调用栈拥有完整任务；
- 每个 Plugin 只处理一次明确输入；
- 后续步骤来自新的 Context 变更；
- 等待用户或长时资源时，不需要保留主 Agent Python 栈；
- 重启后可以根据树和 Hook 队列继续，而不是重建旧 loop 的局部变量。

当前 HookSet 使用每树一个惰性启动的串行工作器消费队列。这是基础设施执行循环，不是 Agent 业务 loop。

### 1.5 标准执行路径

#### 简单对话

```text
User Plugin mounts user input
  → ContextChange queued
  → Model Plugin receives path
  → model returns final text
  → Model Plugin mounts assistant result
  → Model Plugin sees result is terminal and does not call model again
```

#### 工具调用

```text
User input mounted
  → Model Plugin called
  → model returns tool call
  → Model Plugin mounts tool-call context
  → Tool Plugin executes
  → Tool Plugin mounts only the normalized result
  → ContextChange triggers Model Plugin again
  → model continues or returns final answer
```

“所有工具只返回结果”是 Plugin 合同，不是 Context Tree 的类型约束。工具结果仍是普通 JSON 节点。

#### 用户等待

Ask User、权限审批和计划确认都按工具处理：

```text
Model emits request
  → corresponding Plugin creates UI/pending request
  → user later supplies answer
  → answer is the original tool/request result
  → Plugin mounts result
  → Model Plugin continues
```

用户回答不是新 Agent Run，也不需要恢复旧 loop。

#### 长时资源和支线

Subagent、Terminal、媒体和 Browser 都可以产生独立支线。Plugin 可以先挂载 handle/pending node；资源完成后再挂载结果。Chat Subagent 默认另起一棵树，完成后由 Subagent Plugin 选择需要合并的结果并挂载到主树。

跨树读取、选择和合并属于 Plugin，不属于 Context Tree。

### 1.6 与 Cyrene 其他运行时的边界

- 新内核固定放在 [`src/agent`](../src/agent/)，不放进 `src/cyrene/agent_runtime`。
- 外部 Agent/ACP 的 `cyrene.agent_runtime` 独立存在，不属于这次内核重写。
- 旧 `cyrene.agent` 最终完全弃用，不作为新内核内部依赖或长期兼容层。
- 迁移期间通过前端/API adapter 保持现有 Workbench 合同，不要求前端一次性重写。
- Terminal、Media、Browser、Office、Knowledge 等业务 Manager 可以继续复用；它们通过 Plugin 接入。

### 1.7 当前实现状态

| 部分 | 状态 |
|---|---|
| 通用 Context Tree | 已实现 |
| 每树独立 SQLite、连接和锁 | 已实现 |
| Hook 绑定持久化 | 已实现 |
| ContextChange/ContextUsed 持久队列 | 已实现 |
| 每树串行 Hook 执行 | 已实现 |
| Plugin Registry 按 `plugin_id` 恢复 | 已实现基础版本 |
| 初始 root Hook 注册 | 已实现 `initial_hooks` |
| 自动 ContextUsed | 已实现估算器和可注入计量器 |
| 正式 Plugin 生命周期/配置管理 | 尚未实现 |
| Model Plugin | 尚未实现 |
| Tool Plugin 与现有 executor adapter | 尚未实现 |
| Ask User/审批/计划确认迁移 | 尚未实现 |
| Subagent/Terminal/Media 等迁移 | 尚未实现 |
| 前端兼容层和旧后端切换 | 尚未实现 |

## 2. 为什么从 Agent Loop 改成这个结构

当前 `cyrene.agent` 将模型调用、上下文拼装、工具解析、并发提交、用户等待、审批、计划、Subagent、重试、压缩和最终回答集中在一次持续执行中。随着能力增加，控制语义逐渐表现为局部变量、特殊返回值、消息内容、UI 状态和外部 Manager 回调的组合。

主要问题不是 Python 中存在 `while`，而是任务的进度由 loop 的局部控制流拥有：

- 工具结果必须找到原来的模型调用位置；
- Ask User 等待时需要特殊退出和重入；
- Terminal/媒体完成后要通过新 Turn 唤醒；
- 崩溃后只能终止、修补 transcript 或从较粗粒度重跑；
- 新能力需要修改 loop 的分支和 sentinel；
- 上下文压缩和深度反思与消息协议耦合。

Context Tree 把任务进度变成可持久化数据；Hook 把“数据变化后谁应该运行”变成配置；Plugin 把实际行为隔离出去。因此扩展新能力通常只需要新 Plugin 和 Hook 绑定，不需要修改中央 loop。

### 2.1 已核对的现有功能范围

这次方向判断建立在此前对以下实现的完整盘点上，而不只来自旧 `agent.py`：

| 领域 | 已核对范围 | 对新结构的约束 |
|---|---|---|
| 内置 Agent | Decision/Execution Lane、guidance、reflection、quit、重试 | Model Plugin 必须能从通用路径恢复这些行为 |
| Model Runtime | provider fallback、stream、usage、cache、compaction | 保留 Runtime，由 Model Plugin 适配 |
| Tooling | catalog、wire、pack、executor、permission、hook | 工具主体复用，外围改成 Tool Plugin |
| 人机等待 | Ask User、审批、计划确认、Browser takeover | 用户输入必须回到原请求分支 |
| Subagent | child execution、消息、结果返回 | Chat Subagent 使用独立树并由 Plugin 合并 |
| 长时资源 | Terminal、Media、Browser、Office、Remote | callback 只需挂载结果，不恢复主 loop |
| Workbench | Chat Run、Timeline、Task、Goal、前端事件 | 通过 adapter 投影树状态，保持现有合同 |
| 数据 | Memory、SOUL、Knowledge、Attachment、Library | 作为 Context source/artifact Plugin 接入 |
| 主动入口 | Scheduler、Telegram、WeChat、Remote | 外部触发统一表现为 Plugin 输入和树修改 |
| 个性化 | Skill、learning、behavior replay | 组合 Plugin、Hook 和上下文策略，不固化 workflow |
| 外部 Agent | ACP、process、permission、artifact | 保持 `cyrene.agent_runtime` 独立 |

### 2.2 最近 30 天双版本会话带来的约束

此前只读检查了 2026-07-26 至 2026-08-25 的安装版和开发版历史。以下数字是当时快照，开发版包含测试流量：

| 指标 | 安装版 | 开发版 | 对当前方案的影响 |
|---|---:|---:|---|
| 近期会话 | 25 | 112 | 迁移必须区分安装实例和开发实例 |
| 多轮会话 | 17 | 54 | Session tree 是主路径，不是一次性 request state |
| 消息不少于 50 的会话 | 9 | 26 | 压缩和局部路径重建必须可用 |
| 消息不少于 100 的会话 | 3 | 7 | 不能依赖长期 Python 栈 |
| 单会话最大消息数 | 149 | 216 | 节点增长、ContextUsed 和压缩需要 benchmark |
| Tool Result 数 | 573 | 2,090 | Tool Plugin/result node 是高频路径 |
| 最大单个 Tool Result | 约 540 KiB | 约 196 KiB | 大结果应外置为内容引用，不长期内联 JSON |
| 含附件的会话 | 10 | 11 | 节点值需要支持稳定 artifact reference |
| 使用环境指代的会话 | 9 | 45 | Terminal/窗口/文档绑定由资源 Plugin 冻结 |
| 主动或系统发起会话 | 4 | 23 | Scheduler/Channel 必须能在无前台 loop 时挂载输入 |

历史还显示：

- `awaiting_user` 曾与顶层完成状态混在一起，新结构必须把 pending request 保留在树中，不能伪装成终态；
- 权限拒绝和取消是常规结果，应挂载给模型改道，而不是当成 Runtime 崩溃；
- `reasoning_delta` 数量远高于稳定业务事件，不能把每个 stream delta 写成 ContextNode；
- 模型和资源调用可能持续数分钟，长时 Plugin 必须返回 handle/pending 后结束当前 callback；
- 历史消息 schema 已有 compaction、hidden record、lane ref、subagent snapshot 等差异，不能把旧 transcript 原样当作新树的可靠协议日志；
- 工具结果和附件可能很大，Context Tree 的 JSON 通用性不等于所有正文都应该内联保存。

因此，当前极简内核仍需要上层提供 ArtifactRef、Resource Binding、pending handle 和 observation stream，但这些应是节点值约定或 Plugin 能力，不应成为 Context Tree 的固定类型。

## 3. Context Tree 设计

### 3.1 树的粒度

设计约定：

- 每个普通对话 Session 对应一棵树；
- 一个 Project 中需要共享上下文的 Task Session 可以共用一棵多分支树；
- Chat Subagent 默认使用独立树；
- 跨树合并由 Plugin 完成；
- Session/Project 到 `tree_id` 的归属映射由上层服务维护，不塞进 Context Tree。

当前底层已经能支撑这两种粒度，但 Session/Project 映射服务尚未实现。

### 3.2 数据模型

```python
ContextTree:
    id
    root_id
    created_at

ContextNode:
    id
    tree_id
    parent_id
    value
    created_at
    updated_at
```

设计刻意不包含：

- message/tool/result 等节点类型；
- revision；
- active branch；
- branch kind；
- RunState；
- Context source taxonomy。

`value` 是任意可 JSON 序列化内容。语义由 Plugin 解释；Context Tree 只保证结构和持久性。

### 3.3 核心操作

当前已经实现：

- `create_tree`
- `get_tree`
- `delete_tree`
- `mount`
- `update_node`
- `delete_node`
- `get_node`
- `get_parent`
- `get_children`
- `get_path`
- `get_subtree`
- `is_root`
- `has_child`

删除 root 必须使用 `delete_tree`。删除有子节点的普通节点必须显式指定递归删除，避免意外丢失整条支线。

### 3.4 重试、压缩和反思

- 重试：Plugin 找到上一节点，在相同父节点下创建新的尝试分支；旧路径保留用于诊断。
- 压缩：Plugin 更新节点内容为摘要，或者删除已经被摘要覆盖的子树。
- 深度反思：Plugin 可以读取当前路径并挂载新的反思节点或替换摘要。
- 是否再次调用模型：由绑定到相应 ContextChange 的 Model Plugin 判断，不由 Tree 硬编码。

因为节点没有 revision，“替换”表现为更新当前值和 `updated_at`；历史版本若需要保留，应由 Plugin 在更新前挂载备份节点或外置归档。

### 3.5 每树独立数据库

目录结构：

```text
context-root/
  index.sqlite3                 # tree_id → database path
  trees/
    <hash-prefix>/
      <tree-id-hash>.sqlite3    # 一棵树的全部数据
```

每棵树数据库包含：

- `context_tree_metadata`
- `context_nodes`
- `hook_bindings`
- `hook_queue`

收益：

- 不同树写入不共享数据库写锁；
- 单棵树可独立搬迁、删除和检查；
- Hook 与 Context 生命周期一致；
- 单树故障和数据增长不直接阻塞所有 Session。

全局 `index.sqlite3` 只负责定位树，不承载节点或 Hook 事件。

### 3.6 锁和事务

每个已打开的 Tree Store 拥有：

- 一个 SQLite connection；
- 一个 `threading.RLock`；
- WAL 模式；
- foreign keys；
- `busy_timeout`。

普通节点的 mount/update/delete 和匹配到的 Hook delivery 在同一个 SQLite transaction 中提交。提交完成后才唤醒 Hook 工作器，Plugin 运行时不持有 Tree Store 锁。

初始 root 是特殊创建路径：root 数据先初始化，再在 Router 返回 tree 之前持久化 `initial_hooks` 并入队 root 通知。它对调用者不可见，但目前不是与 root INSERT 完全相同的 SQLite transaction；若未来需要抵御进程在 `create_tree` 内部硬退出，应把初始 binding/root/queue 收敛到一个 Tree Store 创建事务。

因此 Plugin 可以安全修改自己的树，不会因为 Hook 在数据库锁内回调而自锁。不同树的写入也不会被 Router 的全局锁包住。

### 3.7 ContextChange

```python
ContextChange:
    tree_id
    node_id
    action        # mount | update | delete
    time
    deleted_node_ids
    parent_id
```

ContextChange 只描述结构事实，不携带业务类型。删除事件保留原父节点和被递归删除的节点 ID，便于 Plugin 找到仍然存在的上游路径。

## 4. Hook 设计

### 4.1 已支持的 Hook

| Hook | 用途 | 是否持久队列 |
|---|---|---|
| `ContextChange` | 节点 mount/update/delete 后触发 Plugin | 是 |
| `ContextUsed` | 路径上下文 token 占用变化 | 是 |
| `PreToolUse` | 工具参数变换、允许或阻止 | 否，调用方等待结果 |
| `PostToolUse` | 工具执行后观察和处理 | 否，调用方等待结果 |
| `SessionStart` | Session 启动时提供上下文 | 否 |
| `SessionEnd` | Session 正常结束 | 否 |
| `Stop` | 取消或停止通知 | 否 |

所有 Hook 调用共享同一棵树的执行工作器，因此同树内不会并行调用 Plugin。只有 Context 状态通知需要跨进程恢复，所以它们进入 SQLite 队列；工具决策和生命周期调用仍由当前调用方等待，不作为可恢复业务状态。

Ask User、审批等跨进程等待不能依赖一次未持久化的 `PreToolUse` 调用；它们必须把 pending/result 表达为 Context，再通过 ContextChange 恢复。

### 4.2 Hook 持久化

`hook_bindings` 保存：

- `hook_id`
- `event`
- `plugin_id`
- `root_only`
- `matcher`
- `failure_policy`
- `config_json`
- `enabled`
- `created_at`

不保存 Python callable。持久化 binding 通过稳定 `plugin_id` 引用 Plugin Registry 中的实现。

这意味着重启流程是：

```text
open tree database
  → load hook_bindings
  → Plugin Registry resolves plugin_id
  → recover running queue rows
  → consume pending deliveries
```

### 4.3 持久队列

`hook_queue` 为每个匹配到的 Hook binding 保存一条 delivery：

- 单调递增 `sequence`；
- Hook 和 event identity；
- 序列化 payload；
- node/root 信息；
- `pending/running/blocked/failed`；
- attempts 和 last_error。

执行成功后 delivery 删除。队列目前不是长期审计日志；Context Tree 才是持续状态源。

### 4.4 执行顺序

每个 HookSet 有一个惰性启动的专用工作线程和 asyncio event loop：

- 同一棵树严格串行；
- 不同树可以并行；
- Context Plugin 中新挂载的节点排到当前 delivery 后面；
- `drain()` 可以等待当前队列稳定；
- Hook 工作器不持有 Context 数据库锁执行 Plugin。

这一点避免了旧实现中每次 ContextChange 创建独立 asyncio Task 所导致的完成顺序漂移。

### 4.5 恢复和失败

- 进程退出时处于 `running` 的 delivery，在重新建立 HookSet 时恢复为 `pending`；
- Plugin 未注册时 delivery 进入 `blocked`；
- 对应 `plugin_id` 重新绑定后，blocked delivery 自动回到 pending；
- Plugin 抛出异常时 delivery 进入 `failed`；
- `retry_failed()` 可以显式重新排队失败项；
- PreToolUse 支持 fail-open，以及仅限该事件的 fail-block。

当前提供的是 **at-least-once** 而不是 exactly-once。若 Plugin 的外部副作用在成功后、queue complete 前发生崩溃，delivery 可能重跑。所有会产生外部副作用的 Plugin 必须使用幂等键或先查询结果。

### 4.6 `root_only`

`root_only` 是 Hook binding 的过滤条件：只有 `event.node_id == tree.root_id` 时才执行。

它不表示“只重跑 root Plugin”，也不自动广播给所有分支。根节点更新后是否扫描或重算全部分支，由 root Plugin 决定。

### 4.7 初始 root 事件

旧 API 的问题是 `create_tree()` 先发布 root mount，再返回 tree，调用者没有机会注册 root Hook。

当前方案使用 `initial_hooks`：

```python
tree = router.create_tree(
    root_value={"system": "..."},
    initial_hooks=(
        HookRegistration(
            event=CONTEXT_CHANGE,
            plugin_id="model",
            plugin=model_plugin,
            hook_id="root-model",
            root_only=True,
        ),
    ),
)
```

创建顺序固定为：

```text
create root data
  → persist initial Hook bindings
  → enqueue root ContextChange/ContextUsed
  → return tree
```

因此初始 root 不需要补发或依赖竞态注册。

## 5. ContextUsed

### 5.1 语义

`ContextUsed` 表示某个节点路径作为模型上下文时占用多少 token：

```python
ContextUsed:
    tree_id
    node_id
    tokens
    token_limit
    usage_ratio
    node_tokens
    time
```

### 5.2 自动上报

当前 root 初始化、节点 mount 和节点 update 都会计算从 root 到当前节点的路径 token，并把 ContextUsed 与 ContextChange 一起写入 Hook 队列。删除节点不再存在有效目标路径，因此只上报 ContextChange。

默认计量器使用稳定近似值，Router 支持注入模型对应 tokenizer：

```python
ContextStoreRouter(
    path,
    token_counter=model_token_counter,
    token_limit=model_context_limit,
)
```

模型组件完成真实 prompt 编排后仍可调用 `report_context_used()` 上报精确结果。正式接入前，应确保自动计量器和模型 prompt tokenizer 使用同一种规则，避免压缩 Hook 根据估算和真实 usage 做出不同判断。

### 5.3 ContextUsed 不自动压缩

Hook 只提供信号。压缩 Plugin 可以根据 `usage_ratio`：

- 更新旧节点为摘要；
- 删除已经被摘要覆盖的节点；
- 创建新的压缩分支；
- 什么都不做。

压缩造成的 ContextChange 是否继续触发模型，仍由 Model Plugin 判断。内核不设置特殊“压缩事件”。

## 6. Plugin 模型

### 6.1 当前基础能力

目前 Plugin 是 `HookEvent → value/awaitable` callable，进程内由 `PluginRegistry` 通过 `plugin_id` 解析。

Context Hook 的普通返回值会被忽略。Plugin 必须显式调用 Context Router 挂载、更新或删除节点。这落实了“是否挂载由 Plugin 决定”。

PreToolUse 是例外，因为它需要把 `allow/modify/block` 同步返回给工具调用方。

### 6.2 正式 Plugin 组件仍需补充

后续 Plugin 层至少需要：

- 稳定 manifest 与版本；
- `plugin_id` 命名和冲突规则；
- 初始化、关闭和健康检查；
- 依赖与权限声明；
- 配置 schema；
- 超时和取消；
- 幂等键；
- 可观测性；
- 对 Context Router、前端事件和业务 Manager 的受控能力。

这些属于 Plugin Platform，不应继续扩张 Hook 或 Context Tree 的模型。

### 6.3 Model Plugin

Model Plugin 将承担：

1. 根据变更节点选择需要使用的树路径；
2. 过滤不需要再次调用模型的结果节点；
3. 编排 system、tool schema、memory 和附件上下文；
4. 调用 Model Runtime；
5. 处理文本或 tool call；
6. 显式挂载结果；
7. 上报真实 ContextUsed。

避免无限触发的关键不在 Hook 增加更多类型，而在 Model Plugin 对节点内容做终止判断。例如 assistant final 节点已经是终态时，它不应再次调用模型。

### 6.4 Tool Plugin

Tool Plugin 将现有工具适配为统一流程：

```text
read tool-call node
  → PreToolUse
  → schema validation
  → permission/approval
  → execute existing implementation
  → normalize result
  → PostToolUse
  → mount result
```

现有工具主体不需要全量重写；需要重写或适配的是执行外围、等待续体和 Context 挂载。

## 7. 当前 Cyrene 功能的承载方式

| 现有能力 | 新结构中的表达 | 归属 |
|---|---|---|
| 简单对话 | User node → Model Plugin → assistant node | Model Plugin |
| 普通工具 | tool-call node → Tool Plugin → result node | Tool Plugin |
| Ask User | request node → UI pending → answer result node | Human Plugin |
| 权限审批 | PreToolUse 或 approval request/result node | Permission Plugin |
| 计划确认 | plan request/result node | Plan Plugin |
| Guidance | 外部输入挂载到当前分支 | Guidance Plugin |
| Retry | 回到父节点创建 sibling attempt | Retry Plugin |
| Quit/Stop | Stop Hook + terminal node | Lifecycle Plugin |
| 压缩 | update/delete context nodes | Compression Plugin |
| Deep Reflect | 读取路径并挂载反思/摘要 | Reflection Plugin |
| Subagent | 独立树执行，结果选择性合并到主树 | Subagent Plugin |
| Terminal | handle/pending node，完成后 result node | Terminal Plugin |
| Media | job node，完成后 artifact/result node | Media Plugin |
| Browser Takeover | human pending/result node | Browser Plugin |
| Office | resource handle/revision 由值表达 | Office Plugin |
| Scheduler | 定时触发 Plugin 挂载新输入 | Scheduler Plugin |
| Telegram/WeChat | Channel Plugin 挂载用户输入并投影输出 | Channel Plugin |
| Skill | 可注册 Plugin、Hook binding 和上下文编排策略 | Skill/Plugin Platform |
| Memory/Knowledge | Context source Plugin 选择并挂载内容 | Context Plugin |
| Streaming | 短期 observation，不把每个 delta 挂成节点 | Frontend adapter |
| 外部 Agent/ACP | 独立 runtime，必要时由 adapter 与树交换结果 | `cyrene.agent_runtime` |

这个映射说明基座具有表达能力，但不等于上述 Plugin 已经实现。功能切换必须以端到端测试为准。

## 8. Session、Project 和分支

### 8.1 普通 Chat

一个 Session 一棵树。root 保存该 Session 的稳定基础上下文，主对话沿某条路径增长。重试或候选答案形成 sibling 分支。

### 8.2 Project Task

同一个 Project 内需要共享状态的 Task Session 可以挂在同一 root 下的不同一级分支：

```text
project root
├── task/session A
│   └── ...
├── task/session B
│   └── ...
└── shared summary
```

当 root 被更新时，root-only Plugin 可以决定重新计算全部任务摘要；普通分支变化只触发该分支相关处理。

Context Tree 不维护“当前活跃 Session”。上层 Session binding 必须明确保存 session → branch node。

### 8.3 Subagent

Chat Subagent 默认另起树，避免子任务的搜索、工具错误和压缩污染主对话路径。完成后 Subagent Plugin 读取子树，选择最终结果、证据或摘要，挂载到主树。

Context Tree 不提供 `merge_tree()`，因为“合并什么”是业务判断。

## 9. 并发、顺序和锁

### 9.1 保证

- 单树 Context 写入由该 Tree Store 的 RLock 和 SQLite transaction 串行化；
- 不同树使用不同数据库和锁；
- 同树 Hook delivery 按 `sequence` 串行执行；
- Context 事务提交后才执行 Plugin；
- Plugin 执行时不持有数据库锁；
- Hook binding 的注册、删除和队列状态变化使用同一个树数据库。

### 9.2 不保证

- 跨树全局顺序；
- 多个外部系统副作用的 exactly-once；
- Plugin 自身不会形成无限 Context 更新；
- 不同业务分支自动 join；
- 工具结果的业务 ordinal。

并行工具可以使用多个 sibling 节点表达。何时等待全部结果、按什么顺序交给模型，由 Tool/Model Plugin 决定，而不是 Context Tree 添加 join 类型。

### 9.3 锁顺序原则

1. Router 锁只保护 index/cache/lease；
2. Tree Store 锁只保护单树 connection；
3. Hook Registry 锁只保护绑定和 worker 状态；
4. Plugin callback 在所有上述数据库锁之外运行；
5. 不在持有 Tree Store 锁时等待用户、网络或其他树。

## 10. 持久性和恢复语义

### 10.1 已经具备

- Context Tree 和节点跨进程保存；
- Hook binding 与树共库保存；
- ContextChange/ContextUsed delivery 与节点修改同事务提交；
- `running` delivery 重启后恢复为 `pending`；
- 缺失 Plugin 不丢事件；
- 失败 delivery 可人工或策略重试。

### 10.2 当前边界

- 成功 delivery 会删除，尚无长期 Hook execution history；
- 没有 retry backoff、最大 attempts 或 dead-letter 管理 API；
- 没有 Plugin 超时；
- Router 关闭会等待当前 Plugin，长期阻塞 Plugin 可能延长关机；
- 当前每个真正执行过任务的 HookSet 会保留一个工作线程直到 Router/Tree 关闭，大量同时打开的树需要 idle worker 回收或共享执行器；
- Plugin 外部副作用仍需幂等；
- 删除整棵树会同时删除 Hook binding 和未完成队列；
- PreToolUse/PostToolUse/Session lifecycle 本身不跨进程恢复。
- `config_json` 已持久化，但当前 callable 回调只接收 HookEvent；正式 Plugin Runtime 仍需把 binding config 注入 Plugin 实例或执行上下文。

这些能力应优先放入 Plugin Runtime 和运维接口，不应改变 Context Tree 的通用节点模型。

## 11. 缓存和性能

这套结构对缓存具有潜在优势，但不是无条件自动获得：

- 树路径天然形成稳定前缀；
- 分支更新只需要重建该分支上下文；
- root 更新可以明确使全部分支缓存失效；
- 工具结果和模型结果分节点保存，便于内容哈希；
- 压缩可以替换局部节点，不必重写整个 transcript。

需要防止的成本：

- 每次 mount/update 都计算 ContextUsed；
- 每个匹配 Hook 都产生 queue row；
- 过细节点会增加 SQLite 操作和 prompt 拼装；
- Model Plugin 若不能正确识别终态，会产生额外模型调用；
- root 频繁更新会使共享 Project 的大量分支失效。

后续缓存键建议由以下内容组成：

```text
model/provider identity
+ ordered path node content hashes
+ tool catalog hash
+ system/plugin configuration hash
+ compaction policy version
```

不需要给 ContextNode 增加 revision；hash 和 `updated_at` 足以由缓存层判断变化。

## 12. Skill、自定义和 Harness 演进

### 12.1 Skill 友好性

Skill 可以组合：

- 一个或多个 Plugin；
- Hook binding；
- Model Context 编排策略；
- 工具清单和权限；
- 对特定节点值的解释规则；
- 压缩、反思和终止策略。

因此自定义程度很高，而且不需要把 Skill 编译成固定 workflow。

### 12.2 不把 Harness 优化伪装成 RL

当前目标不是训练策略或在线 RL，而是让从用户输入到最终结果的路径可观测、可替换：

- Context Tree 保留实际路径；
- Hook queue 记录尚未完成的触发；
- Plugin 日志记录选择和失败；
- 用户纠正可以成为新的 Context；
- 下一次运行可以由 Skill/Plugin 改变上下文选择和工具策略。

工具限流、网页不可访问、文件位置错误等语义仍主要由 Tool/Model Plugin 理解。内核不尝试从原始错误中推断“最优路径”。Plugin 可以把失败结果挂载到树，让后续模型避开相同选择。

## 13. 与其他 Agent 架构的比较

| 架构 | 主控抽象 | 持久点 | 与本方案的差异 |
|---|---|---|---|
| 传统 ReAct loop | model/action loop | transcript | 本方案由 Context 变化逐步唤醒，不让 loop 持有任务生命周期 |
| LangGraph | graph node + typed state | checkpoint | 本方案没有预定义图和 workflow；树结构与 Plugin 动态决定下一步 |
| Durable workflow | orchestrator + activity | event history | 本方案不要求确定性 orchestrator replay，模型仍是主要决策者 |
| Event-sourced actor | entity mailbox + journal | event log | HookSet 类似每树 mailbox，但 Context Tree 是当前状态，不是完整事件溯源 |
| Blackboard system | shared knowledge + independent specialists | blackboard | 最接近本方案；Context Tree 是结构化持久 blackboard，Hook 是触发机制 |
| DeepSeek Harness | event session + Plugin + ReActLoopAgent | session events | 都强调 Plugin 和 Session；本方案进一步去掉中心 ReactLoopAgent 生命周期 |

相关研究和工程参考：

- [ReAct](https://arxiv.org/abs/2210.03629)
- [Pregel](https://research.google/pubs/pregel-a-system-for-large-scale-graph-processing/)
- [LangGraph](https://langchain-ai.github.io/langgraph/index.html)
- [Microsoft Durable Task programming model](https://learn.microsoft.com/en-us/azure/durable-task/common-programming-model-overview)
- [Akka Persistence](https://doc.akka.io/libraries/akka-core/current/typed/persistence.html)
- [DeepSeek Harness Architecture](https://github.com/deepseek-ai/deepseek-harness/blob/master/docs/architecture.md)
- [DeepSeek Harness Agent Loop](https://github.com/deepseek-ai/deepseek-harness/blob/master/packages/core/agent-loop/README.md)
- [DeepSeek Harness Session Persistence](https://github.com/deepseek-ai/deepseek-harness/blob/master/docs/subsystems/persistence.md)
- [DeepSeek Harness Tool Execution Pipeline](https://github.com/deepseek-ai/deepseek-harness/blob/master/docs/tool-execution-pipeline.md)

状态机、事件驱动和持久队列本身不会让 Cyrene 自动与众不同。真正的差异可能来自：一个极小的通用内核，同时自然承载桌面资源、长时任务、人机等待、多树 Subagent 和高度自定义 Plugin，而不要求用户先定义 workflow graph。

## 14. 新内核目录和代码职责

```text
src/agent/
  __init__.py
  context/
    __init__.py
    errors.py
    hook_store.py       # 同树数据库中的 Hook binding/queue
    publisher.py        # 进程内 Context listener
    router.py           # tree → isolated store；HookSet ownership
    schema.py           # index/tree/hook SQLite schema
    serialization.py    # JSON 和时间
    store.py            # 单树数据操作、事务、ContextUsed
    tree.py             # ContextTree/Node/Change 值对象
  hook/
    __init__.py
    errors.py
    hook.py             # Hook/Event/Registration 值对象
    plugin.py           # process-local PluginRegistry
    registry.py         # HookSet、串行 worker、调用语义
    storage.py          # persistence protocol、event serialization
  tests/
    test_context_tree.py
    test_hooks.py
```

后续推荐新增，而不是塞回已有文件：

```text
src/agent/
  plugin/
    manifest.py
    registry.py
    runtime.py
    context.py
  model/
    plugin.py
    context_builder.py
    result.py
  tool/
    plugin.py
    adapter.py
    result.py
  adapters/
    workbench.py
    legacy_tooling.py
    model_runtime.py
```

是否最终采用这些具体文件名可以调整，但依赖方向必须保持：Plugin 依赖 Context/Hook 公共 API，Context Tree 不反向导入具体 Model/Tool/Human 实现。

## 15. 当前测试覆盖

新内核测试与现有 Cyrene 测试独立，当前覆盖：

- 树结构、时间顺序、父子路径和子树；
- JSON 值隔离；
- 删除约束；
- reopen 持久化；
- 每树独立数据库；
- LRU reopen；
- 不同树写入不共享 Router 锁；
- 每树 HookSet 隔离；
- root-only；
- Plugin 回调修改同树；
- ContextUsed；
- PreToolUse allow/modify/block；
- PostToolUse 和 lifecycle；
- Hook 失败不回滚 Context；
- 删除事件 parent；
- 跨树 Hook 阻塞隔离；
- 同树 Hook 顺序；
- Hook binding 与树共库；
- 重启恢复 Plugin binding；
- missing Plugin blocked/resume；
- 节点更新自动 ContextUsed；
- initial root Hook。

当前结果：`24 passed`，Ruff 检查通过。

仍需增加：

- 模拟进程在 `running` 状态崩溃；
- Plugin 外部副作用后的重复 delivery；
- 大队列背压；
- Router close 与超时 Plugin；
- 真实 Model/Tool/Ask User 端到端；
- Session/Project tree binding；
- 前端兼容回归。

## 16. 迁移策略

### Phase 1：Context Tree 与 Hook 基座

状态：已完成基础实现。

- 独立 `src/agent`；
- universal Context Tree；
- 每树数据库和锁；
- Hook persistence 和有序队列；
- ContextUsed；
- initial root；
- 独立测试。

### Phase 2：正式 Plugin Runtime

- Plugin manifest、配置、生命周期；
- timeout/cancellation/idempotency；
- queue diagnostics 和 failed management；
- Plugin capability boundary；
- 统一日志和 metrics。

### Phase 3：Model Plugin

- Context path builder；
- 与现有 Model Runtime 连接；
- final/tool-call 结果挂载；
- streaming frontend adapter；
- 真实 tokenizer；
- 简单对话闭环。

### Phase 4：Tool Plugin

- 适配现有 tool catalog/executor；
- PreToolUse/PostToolUse；
- 权限审批；
- tool result 挂载；
- 并行工具 sibling/join 策略。

### Phase 5：Human 和长时资源

- Ask User；
- 计划确认；
- Browser takeover；
- Subagent 独立树与 merge；
- Terminal、Media、Office 和 Remote callback。

### Phase 6：Session/Project 与前端兼容

- Session → tree/branch binding；
- Project 多分支共享树；
- Workbench timeline projection；
- retry、cancel、guidance、compaction；
- 安装版/开发版历史导入策略。

### Phase 7：切换与删除旧内核

- 按功能矩阵和会话级开关灰度；
- 新 Session 使用新内核；
- 旧 Session 只读或通过 importer 转换；
- 完整验收后删除 `cyrene.agent`；
- 不让新内核依赖旧 Agent loop 作为永久 fallback。

外部 Agent/ACP 不参与这次删除，继续由独立 `cyrene.agent_runtime` 管理。

## 17. 必须继续解决的问题

### 17.1 Plugin 幂等

当前队列是 at-least-once。Model 调用、文件写入、支付/发布等外部动作需要稳定 operation key。Plugin Runtime 必须提供统一幂等接口，不能让每个工具临时实现。

### 17.2 超时、取消和关机

工作器严格串行，因此一个永久等待的 Plugin 会阻塞整棵树。Ask User 和长时任务不能在 Hook callback 中无限 await；它们应创建 pending state 后返回，外部结果到达时再挂载 Context。

### 17.3 队列观测和失败管理

需要 API 查询：

- pending/running/blocked/failed 数量；
- 当前 delivery；
- attempts/last_error；
- retry one/all；
- discard/tombstone；
- queue age 和 no-progress。

### 17.4 ContextChange 自触发

Plugin 挂载结果会产生新的 ContextChange，这是设计核心，也可能产生无限链。每个会修改树的 Plugin 必须有明确终止条件。后续可以在 Plugin Runtime 增加最大连续步骤和 no-progress 保护，但不应给 Context Tree 增加业务节点类型。

### 17.5 ContextUsed 精度和成本

正式 Model Plugin 必须注入真实 tokenizer，并验证工具 schema、system prompt 等不在树中的内容如何计入。自动逐次计算在长路径上可能成为热点，需要 benchmark 后决定是否增量缓存。

### 17.6 Hook execution history

成功 delivery 当前被删除。如果审计、性能分析和学习需要完整轨迹，应由独立 observation/telemetry store 保存执行摘要，而不是无限膨胀每棵树的 hook_queue。

### 17.7 安全边界

Plugin 高度可自定义，但工具权限、凭据隔离、项目路径和外部网络策略不能仅靠普通 Hook 约定。正式 Plugin Runtime 必须区分可信内置 Plugin、Project Plugin、MCP/Custom Tool 和不可信外部内容。

## 18. 验收标准

只有满足以下条件，才能替换现有后端：

### 架构

- Agent 业务进度不依赖长期 loop 调用栈；
- Context Tree、Hook、Plugin 是唯一内核抽象；
- 新内核不调用旧 `cyrene.agent`；
- 外部 Agent/ACP 边界保持独立；
- Plugin 决定挂载，Hook 不隐式修改 Context。

### 持久性

- Context 修改和通知入队原子提交；
- 同树 Plugin 严格串行；
- 重启不丢 pending Context event；
- missing Plugin 不吞事件；
- failed delivery 可诊断和重试；
- 外部副作用有幂等策略。

### 功能

- 简单对话、普通工具、人机等待和长时资源全部闭环；
- Ask User/审批/计划回答作为原请求结果返回；
- Subagent 独立树结果可合并；
- retry/compaction/reflection 能用通用树操作表达；
- Scheduler、Channel 和 frontend 不需要调用旧 Agent loop。

### 性能

- 简单对话不产生多余模型调用；
- 分支变化不重建无关分支 Context；
- root 更新的全局重算可控；
- Hook queue 无无界增长；
- ContextUsed 对长会话没有不可接受开销；
- 缓存命中率不低于现有实现基线。

### 可维护性

- 新增普通工具不修改 Context Tree 或 HookSet；
- 新增长时资源只需要 Plugin 和外部 callback；
- 新增 Hook event 必须有无法由 ContextChange/现有 lifecycle 表达的充分理由；
- Context/Hook 测试继续独立于 legacy 测试；
- 旧后端删除后不存在双内核长期维护。

## 19. 最终建议

继续沿当前实现推进，不回到完整 Event Store/Reducer/Effect/Workflow 内核。对 Cyrene 来说，那个方案能表达全部功能，但会在真正迁移之前先建立一套与现有产品同样复杂的新领域模型。

当前方向更符合最初目标：

> 用最小的 Context Tree + Hook + Plugin 基座，让模型继续作为主要决策者；状态由树持久化，调用时机由 Hook 编排，具体行为和挂载由 Plugin 决定。

Context Tree 和 Hook 已经证明这条路径在持久化、锁隔离、有序执行、初始 root 和 ContextUsed 上可行。下一步不应继续给内核增加概念，而应实现正式 Plugin Runtime 和 Model Plugin，用一次真实的“用户输入 → 模型 → 工具 → 结果 → 模型 → 最终回答”闭环验证架构。
