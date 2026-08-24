# Cyrene Durable Agent State Machine 重构设计

> 状态：设计提案，供架构评审与后续实施使用  
> 日期：2026-08-25  
> 目标版本：Cyrene Runtime V2  
> 适用范围：Main Agent、Decision/Execution Lane、工具、Ask User、权限审批、计划确认、Subagent、Terminal、媒体任务、Skill 与 Workbench 运行时

## 1. 摘要

Cyrene 当前的 Agent 能力已经覆盖双 Lane、工具协议、并发工具、权限审批、计划确认、
运行中指导、Subagent、Terminal、媒体任务、缓存、恢复和 Workbench UI，但这些能力的
编排大量集中在 Agent loop 及其外围的特殊分支中。结果是同一个业务事实经常同时表现为：

- Python 调用栈中的局部变量；
- `awaiting_user`、`spawned`、`quit_requested` 等控制标志；
- Session Message 中的模型协议；
- Workbench Run 状态；
- Pending Question 或 Wake Record；
- UI 事件和临时 System Prompt。

这些表示需要人工保持一致，导致恢复、重试、取消、并发和新能力接入越来越困难。

本提案建议重写 Cyrene 的 Agent 执行内核，将其从“一个持续运行并解释模型结果的
Agent loop”改造成“由持久事件驱动的状态机”。新内核不通过 `while` 循环持有完整任务
生命周期，而是在每个事件到达时执行一次确定性状态迁移，产生需要执行的 Effect；
Effect 完成后写回结果事件，再触发下一次迁移。

核心形式为：

```text
Event → Reducer(current state) → New State + Effects
                                      ↓
                              Component Executors
                                      ↓
                               Result Events
```

模型调用、普通工具、用户输入、审批、计划确认、Subagent、Terminal 和媒体任务都使用
同一套 Branch/Effect 协议。它们的执行方式不同，但对内核而言都是“发出请求、等待结果、
挂载结果、继续原 Run”。

这次重构不是对现有 `agent.py` 的局部拆分，也不以最大化旧代码复用率为目标。建议平行
建设 Runtime V2，保留已有产品合同和经过验证的底层能力，重写携带旧 loop 控制假设的
编排代码。在 V2 覆盖全部能力并通过验收后删除旧内核。

## 2. 为什么需要重写

### 2.1 当前实现的主要问题

当前 [`src/cyrene/agent/agent.py`](../src/cyrene/agent/agent.py) 中的主 Agent 实现同时负责：

- Decision Lane 和 Execution Lane 的选择与衔接；
- System Prompt、历史、运行时指导和工具 Schema 的拼装；
- 模型调用、流式输出、重试和 Provider 特殊协议；
- 工具调用解析、批量提交、执行、跳过和结果挂载；
- `ask_user`、`quit`、`DeepReflect`、Subagent 等控制语义；
- Session Message 持久化和最终结果同步；
- 缓存、Usage、错误修复和异常收尾。

主执行函数内部存在多层循环，并使用以下布尔变量表达隐式状态：

```text
awaiting_user
spawned
quit_requested
reflection_requested
guidance_supersedes_batch
paused
final_saved
```

单个布尔值并不是问题；问题在于多个布尔值组合后形成了一个没有正式定义的状态机。
合法组合、优先级和恢复语义只能从分支顺序推断。例如，同一批工具中出现 Ask User、
普通工具、Quit 和新用户指导时，哪些工具应执行、哪些需要补合成结果、什么内容应进入
模型 transcript，都由循环中的分支位置决定。

### 2.2 用户等待并不是真正的续体

Ask User、权限审批和计划确认本质上是未完成工具调用的异步输入，但当前部分流程通过：

1. 保存 Pending Question；
2. 退出当前 Agent 调用；
3. 用户回答后构造临时 System Prompt；
4. 再次调用 Agent；

来模拟恢复。这使“用户的回答是原工具结果”与“用户发起了一次新对话”之间的边界变得
模糊，也使原始 `tool_call_id`、Lane、权限快照和执行位置的恢复依赖额外约定。

### 2.3 异步资源通过新 Turn 唤醒

Terminal 和媒体任务已经有持久化 Wake Record、租约和后台 Manager，这些底层能力应
保留。但完成事件目前主要用于启动或唤醒一个新的 Workbench Turn。因果关系表现为：

```text
原工具调用 → 创建后台资源 → 原 Turn 结束
后台完成 → Wake Bridge → 新 Turn → 再次解释结果
```

目标语义应是：

```text
原 Branch → 创建后台资源 → Branch 等待
后台完成 → 完成原 Branch → 结果进入原 Run → 触发后继模型调用
```

### 2.4 恢复偏重“结束旧运行”，而不是“继续旧运行”

当前系统能够检测重启、清理陈旧问题、终结损坏 Run，并向用户提供 Retry。这对于避免
永久卡住是必要的，但尚不能普遍表达“这个 Run 正在等待哪个外部结果，结果回来后应该
恢复到哪里”。持久状态机需要把未完成工作本身写入存储，而不依赖原 Python Task 和调用栈。

### 2.5 新能力继续增加会放大组合复杂度

如果继续在 loop 内添加以下能力：

- 多个同时等待的用户输入；
- Subagent join、quorum 或 race；
- 可恢复 Workflow；
- Skill 自动学习；
- 外部 webhook；
- 长时间媒体和数据任务；
- 精细取消和补偿；

每项能力都需要修改循环中的多处分支。状态机把这些能力约束到统一协议中，使新增组件不必
理解整个 Agent 执行过程。

## 3. 目标与非目标

### 3.1 目标

1. Agent 的业务生命周期不再由一个长期存活的 loop 和 Python 调用栈拥有。
2. 所有模型、工具、人机交互和异步资源都由统一的 Event、Effect、Branch 表达。
3. Session Event Log 成为规范事实源；模型上下文、UI 时间线和审计信息是投影。
4. 进程重启后能够恢复等待中的 Run，而不是默认创建一个新 Turn。
5. 保留 Decision/Execution 双 Lane、稳定缓存前缀和 Provider Family 隔离。
6. 保留稳定 Tool Wire、Catalog Snapshot、Actor Policy、权限和公开 API 合同。
7. 支持组件替换和 Skill 子图，同时保护不可由普通插件覆盖的安全内核。
8. 使并发、取消、重试、恢复和结果提交具有明确、可测试的不变量。
9. 允许有边界地重写旧实现，不因兼容旧内部抽象而污染新内核。

### 3.2 非目标

- 不要求消灭进程中的所有 `while` 循环。队列消费者、SSE、PTY、Scheduler、租约续期和
  Provider Stream 仍然需要基础设施循环。“无 loop”只表示 Agent 业务编排不由单个
  run-owned loop 驱动。
- 不承诺跨任意外部系统的数学意义 exactly-once。没有幂等 API 的外部副作用在崩溃点
  可能只能确定为 `outcome_unknown`。
- 不在同一阶段重写 WebUI、所有 Provider、所有工具业务逻辑和数据库之外的产品功能。
- 不改变用户已有的项目、Task、Chat、附件、Knowledge、Memory 和公开工具名称。
- 不把模型推理过程持久化为可公开内容，也不削弱现有脱敏和 DSML 防泄露能力。
- 不直接照搬 DeepSeek Harness 的“Everything is a plugin”；安全和一致性内核不可替换。

## 4. 架构原则

### 4.1 一个事实源，多个投影

所有会影响运行语义的事实首先写入 append-only Event Store：

```text
Canonical Event Log
 ├─ Run State Snapshot
 ├─ Decision Lane Model Context
 ├─ Execution Lane Model Context
 ├─ Workbench Timeline
 ├─ Audit / Permission View
 ├─ Usage / Trace View
 └─ Recovery Queue
```

UI 事件、模型消息和 Run 状态不能各自成为相互竞争的事实源。Snapshot 只是可重建的性能
优化；如果 Snapshot 与事件日志不一致，以日志为准。

### 4.2 Reducer 不执行 I/O

Reducer 的接口必须近似纯函数：

```python
def reduce(state: RunState, event: DomainEvent) -> Transition:
    return Transition(new_state=..., effects=[...], emitted_events=[...])
```

Reducer 不调用模型、不访问网络、不读写文件、不等待用户，也不直接发送 UI 事件。
相同 State 和 Event 必须产生相同 Transition。

### 4.3 I/O 只能通过 Effect

所有不确定操作都先登记 Effect，再由组件执行：

```text
EffectRequested → EffectClaimed → EffectSucceeded
                               └→ EffectFailed
                               └→ EffectOutcomeUnknown
```

Effect Executor 不能直接修改 Run State，只能提交结果事件。

### 4.4 Branch 表达可等待的因果支线

Branch 是 Run 内一个具有独立生命周期的工作单元：

```text
CREATED → READY → RUNNING → WAITING → COMPLETED
                           ├────────→ FAILED
                           └────────→ CANCELLED
```

模型调用和同步工具可以是短 Branch；Ask User、审批、Subagent、Terminal 和媒体任务是
长 Branch。父 Run 通过 join policy 决定何时消费一个或多个 Branch 的结果。

### 4.5 工具只返回结果，不决定控制流

工具组件不得：

- 直接再次调用模型；
- 修改 Agent loop 标志；
- 创建临时 System Prompt 伪造恢复；
- 决定整个 Run 完成；
- 直接操作 Workbench Run 状态；
- 把 UI 通知当作持久业务状态。

工具可以返回结构化结果、资源引用、建议的后继上下文和可恢复句柄。是否继续模型调用由
Reducer 根据 Branch 状态和策略决定。

### 4.6 安全内核与扩展组件分离

以下能力属于固定内核：

- Event 顺序与 schema version；
- Effect 幂等、租约和提交规则；
- Branch 父子关系和只完成一次约束；
- 权限最终裁决；
- Actor、Workspace、Project 和 Session 隔离；
- 结果顺序、审计和敏感信息边界。

模型、Context、工具、Skill、Presenter、Provider 和 Branch Driver 可以组件化替换。

## 5. 核心领域模型

### 5.1 标识体系

每个实体都使用稳定标识，不从当前调用栈推断：

| 标识 | 含义 |
|---|---|
| `session_id` | 用户可持续对话的 Session |
| `run_id` | 一次可恢复的顶层运行 |
| `turn_id` | 用户或系统触发的一次语义回合 |
| `lane_id` | `decision` 或 `execution` 模型投影 |
| `branch_id` | Run 内的一条支线 |
| `effect_id` | 一次外部执行意图 |
| `model_call_id` | 一次模型请求 |
| `tool_call_id` | 模型协议中的工具调用身份 |
| `resource_id` | Terminal、Media、Child Run 等外部资源 |
| `event_id` | 全局唯一事件标识 |
| `sequence` | 同一 Run 内严格递增的事件序号 |

所有结果必须引用其请求标识。任何 UI 操作或回调只携带 `pending_question_id` 而没有
`run_id/branch_id/tool_call_id` 的协议都应被逐步淘汰。

### 5.2 RunState

建议顶层状态保持少量、稳定：

```python
class RunStatus(StrEnum):
    CREATED = "created"
    ACTIVE = "active"
    WAITING = "waiting"
    CANCELLING = "cancelling"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
```

`WAITING` 不细分成几十种顶层状态，具体等待对象由活动 Branch 表达。RunState 至少包含：

```python
@dataclass(frozen=True)
class RunState:
    run_id: str
    session_id: str
    status: RunStatus
    provider_family: str
    catalog_snapshot_id: str
    actor_policy_snapshot_id: str
    permission_snapshot_id: str
    lanes: Mapping[str, LaneState]
    branches: Mapping[str, BranchState]
    pending_effects: tuple[str, ...]
    terminal_outcome: RunOutcome | None
    last_sequence: int
    schema_version: int
```

### 5.3 BranchState

```python
@dataclass(frozen=True)
class BranchState:
    branch_id: str
    parent_branch_id: str | None
    kind: BranchKind
    status: BranchStatus
    request_ref: str
    result_ref: str | None
    resource_ref: str | None
    resume_policy: ResumePolicy
    join_group: str | None
    attempt: int
    created_sequence: int
    completed_sequence: int | None
```

建议的 `BranchKind`：

```text
MODEL_CALL
TOOL_CALL
HUMAN_INPUT
APPROVAL
PLAN_CONFIRMATION
CHILD_RUN
TERMINAL_JOB
MEDIA_JOB
WORKFLOW
SKILL
```

不同 Branch 不需要共享相同业务字段；类型特有数据放在版本化 Payload 中。

### 5.4 Effect

Effect 是“应该发生但尚未被确认的外部动作”，至少包含：

```python
@dataclass(frozen=True)
class Effect:
    effect_id: str
    run_id: str
    branch_id: str
    kind: str
    component_id: str
    payload_ref: str
    idempotency_key: str
    retry_policy: RetryPolicy
    deadline: datetime | None
    permission_claim: PermissionClaim | None
```

Effect 不直接嵌入超大工具结果、媒体二进制或完整网页正文。大数据写入 Artifact/Blob Store，
事件只保留内容摘要、类型、大小、敏感级别和稳定引用。

## 6. 事件模型

### 6.1 事件信封

```python
@dataclass(frozen=True)
class EventEnvelope:
    event_id: str
    run_id: str
    session_id: str
    sequence: int
    event_type: str
    schema_version: int
    occurred_at: datetime
    causation_id: str | None
    correlation_id: str
    actor: ActorRef
    payload: Mapping[str, Any]
    visibility: EventVisibility
```

重要字段语义：

- `causation_id`：直接导致本事件的 Event 或 Effect；
- `correlation_id`：同一 Turn、工具批次或跨 Run 工作流的关联标识；
- `actor`：User、Main Agent、Subagent、Scheduler、Remote Client 或 System；
- `visibility`：模型可见、用户可见、仅审计、仅内部；
- `schema_version`：支持事件升级和历史回放。

### 6.2 关键事件

#### Run 与 Turn

```text
RunCreated
RunStarted
RunCancellationRequested
RunCompleted
RunFailed
RunCancelled
TurnOpened
TurnClosed
GuidanceReceived
```

#### 模型

```text
ModelCallRequested
ModelCallStarted
ModelOutputDeltaObserved       # 可选、通常不参与业务归约
ModelCallCompleted
ModelCallFailed
ModelCallAborted
```

#### 工具

```text
ToolBatchProposed
ToolCallRequested
ToolCallAuthorized
ToolCallDenied
ToolCallStarted
ToolCallCompleted
ToolCallFailed
ToolCallSkipped
ToolResultCommitted
```

#### 人机交互

```text
HumanInputRequested
HumanInputReceived
HumanInputExpired
ApprovalRequested
ApprovalDecided
PlanConfirmationRequested
PlanConfirmationDecided
```

#### 异步资源

```text
ChildRunStarted
ChildRunCompleted
TerminalJobAttached
TerminalJobExited
MediaJobAttached
MediaJobCompleted
ResourceProgressObserved       # 通常只进入 UI 投影
ResourceOutcomeUnknown
```

#### Context 与缓存

```text
ContextProjectionBuilt
LaneEpochAdvanced
ContextCompacted
ExecutionHandoffCreated
ExecutionOutcomeCreated
```

### 6.3 事件可见性

不是所有事件都应进入模型上下文：

| 事件类别 | 模型 | UI | 审计 |
|---|---:|---:|---:|
| 用户消息 | 是 | 是 | 是 |
| Assistant 最终内容 | 是 | 是 | 是 |
| Tool Call/Result | 按 Lane 和协议 | 是 | 是 |
| Approval asked/decided | 通常否 | 是 | 是 |
| Effect lease/heartbeat | 否 | 否 | 是 |
| 模型 Delta | 否或临时 | 是 | 可选 |
| Permission Snapshot | 否 | 摘要 | 是 |
| Context compaction | 通过投影体现 | 可选 | 是 |

批准或拒绝的审计事件不必作为额外自然语言污染模型；模型只需要收到原 Tool Call 的最终
结构化结果。

## 7. 状态迁移与调度

### 7.1 单次迁移事务

处理一个业务事件时必须在同一数据库事务内：

1. 校验事件序号和幂等键；
2. 追加 Event；
3. 用 Reducer 生成新 State；
4. 更新 Snapshot；
5. 写入新 Effect Outbox；
6. 提交事务；
7. 事务提交后通知 Executor 和 UI Projection Worker。

这样不会出现“状态已经要求调用工具，但进程在 Effect 入队前崩溃”的窗口。

### 7.2 Effect 执行

Executor 使用租约领取 Effect：

```text
pending → claimed → running → succeeded
                           ├→ retryable_failed → pending
                           ├→ permanently_failed
                           └→ outcome_unknown
```

结果写回时使用 `effect_id` 去重。Executor 即使重复投递同一个结果，也只能产生一次有效
状态迁移。

### 7.3 恢复语义

启动恢复器扫描：

- 有未消费 Event 的 Run；
- Pending 或过期 Claim 的 Effect；
- WAITING Branch 对应的外部资源；
- 已终态但尚未完成 Projection 的 Run；
- `outcome_unknown` 且需要用户判断的副作用。

恢复器不重新创建用户消息，也不自动新建 Turn。它只重新投递已有 Effect、查询资源状态，
或把 Run 恢复到等待状态。

### 7.4 不宣称绝对 Exactly-once

内部事件和状态迁移可以做到 effectively-once；外部副作用根据能力分级：

| 类别 | 策略 |
|---|---|
| 只读、幂等查询 | 可以安全重试 |
| 支持 idempotency key 的写操作 | 使用 `effect_id` 派生稳定幂等键 |
| 可查询最终状态的异步任务 | 保存 resource handle，恢复时查询 |
| 不支持幂等且无法查询的写操作 | 崩溃后进入 `outcome_unknown`，禁止盲目重试 |
| 本地文件写入 | 临时文件＋原子替换＋内容摘要 |

对于 `outcome_unknown`，系统应要求用户选择查询、接受现状、补偿或再次执行，而不是伪装成
普通失败。

## 8. 组件模型

### 8.1 Component Registry

组件通过稳定的 `component_id` 和版本注册：

```python
class Component(Protocol):
    component_id: str
    version: str
    async def execute(self, effect: Effect, context: ExecutionContext) -> EffectResult: ...
```

Registry Snapshot 在 Run 开始时冻结。运行中的插件开关、工具升级或 Prompt 改动不能改变
已有 Run 的解释方式。新版本只影响新 Run，除非显式执行受审计的迁移。

### 8.2 ModelComponent

职责：

- 接收已经构建好的 Model Request；
- 调用指定 Provider；
- 产生 Stream Observation 和最终结构化 Response；
- 保留 Provider identity、Usage、cache metadata 和 Reasoning 协议；
- 不解释 Tool Call 的业务含义；
- 不决定下一次调用。

```python
class ModelComponent:
    async def invoke(self, request: ModelRequest) -> ModelResult: ...
```

`ModelRequest` 必须包含冻结的 Provider Family、Model、Lane、Prompt Version、Tool Schema
Hash、Context Policy Version、Lane Epoch 和 cache scope。

### 8.3 ContextComponent

Context Component 从规范事件和资源构建 `ContextFragment`：

```python
class ContextComponent:
    async def project(
        self,
        query: ContextQuery,
        source: ReadOnlyRunView,
    ) -> ContextFragment: ...
```

建议内置组件：

- `SystemPromptContext`；
- `ConversationContext`；
- `DecisionLaneContext`；
- `ExecutionLaneContext`；
- `ToolResultContext`；
- `RuntimeGuidanceContext`；
- `WorkspaceContext`；
- `MemoryContext`；
- `KnowledgeContext`；
- `SkillContext`；
- `PlanContext`；
- `ChildRunContext`；
- `ArtifactContext`。

Projection Builder 负责稳定顺序、Token Budget、去重、敏感信息过滤和压缩边界。组件不能
自行覆盖别的组件输出。

### 8.4 ToolComponent

工具建议拆成四个面：

```text
ToolDefinition   模型 Schema、稳定名称、能力 ID
ToolPolicy       Actor/Workspace/Permission 判定
ToolExecutor     业务执行
ToolPresenter    UI 卡片、参数摘要、结果摘要
```

统一结果：

```python
@dataclass(frozen=True)
class ToolResult:
    tool_call_id: str
    status: str
    model_content: ContentRef
    public_summary: str
    artifacts: tuple[ArtifactRef, ...]
    resource: ResourceRef | None
    additional_context: tuple[ContextRef, ...]
    error: StructuredError | None
```

工具抛出的异常在边界处标准化为 ToolResult/Event，不能令整个 Executor 消失且不留结果。

### 8.5 Branch Driver

长生命周期能力实现 Branch Driver：

```python
class BranchDriver(Protocol):
    async def start(self, request: BranchRequest) -> BranchStartResult: ...
    async def inspect(self, resource: ResourceRef) -> BranchInspection: ...
    async def cancel(self, resource: ResourceRef) -> BranchCancelResult: ...
```

Driver 不负责父 Run 的后继逻辑，只负责管理对应资源。

## 9. 典型执行序列

### 9.1 简单对话

```text
UserMessageReceived
→ RunCreated
→ ModelCallRequested(decision)
→ ModelCallCompleted(final text)
→ AssistantMessageCommitted
→ RunCompleted
```

简单对话只有一次模型调用，不进入工具组件，也不需要空转 loop。

### 9.2 普通工具调用

```text
ModelCallCompleted(tool calls)
→ ToolCallRequested × N
→ ToolCallStarted × N
→ ToolCallCompleted × N
→ ToolResultCommitted（按模型 call 顺序）× N
→ ModelCallRequested
→ ModelCallCompleted
```

工具执行可以并行，但模型可见的结果顺序必须稳定。结果未满足提交屏障前，不触发下一次
模型调用。

### 9.3 Ask User

```text
ModelCallCompleted(ask_user call_id=A)
→ HumanInputRequested(branch=H, tool_call=A)
→ RunWaiting
→ 用户回答
→ HumanInputReceived(branch=H)
→ ToolResultCommitted(tool_call=A, answer=...)
→ ModelCallRequested(original lane)
```

用户回答是原 `ask_user` 的工具输出，不新增一条普通任务消息。UI 可以把它显示成用户输入，
但 Model Projection 必须按工具协议恢复原调用。

### 9.4 权限审批

```text
ToolCallRequested(call_id=T)
→ PermissionEvaluationRequired
→ ApprovalRequested(branch=P, call_id=T)
→ 用户批准/拒绝
→ ApprovalDecided
→ 批准：ToolCallAuthorized → ToolCallStarted
→ 拒绝：ToolCallDenied → ToolResultCommitted(call_id=T)
```

审批永远附着在原 Tool Call 上。批准不能通过重新生成一次参数相似的新调用来实现。

### 9.5 计划确认

计划是一个可审计 Artifact，确认是 `PLAN_CONFIRMATION` Branch：

```text
PlanProposed(plan_ref)
→ PlanConfirmationRequested
→ accepted / revise / rejected
```

- `accepted`：关闭确认 Branch，创建计划执行 Branch；
- `revise`：把用户意见作为原确认结果，触发模型修订；
- `rejected`：关闭计划，不执行任何计划副作用，再触发模型产生取消确认。

### 9.6 Terminal 和媒体任务

```text
ToolCallRequested
→ TerminalJob/MediaJob Branch Started
→ resource_id 持久化
→ BranchWaiting
→ daemon/manager completion callback
→ ResourceCompleted(resource_id)
→ ToolResultCommitted(original tool_call_id)
→ ModelCallRequested
```

Wake Bridge 可以保留为基础设施适配器，但不再创建独立新 Turn；它只提交对应 Branch 的
完成事件。

对于“启动后立即返回句柄”的工具，使用 `resume_policy=immediate`；对于模型明确等待结果的
工具，使用 `on_completion`。后台任务不应仅因每个进度更新就触发模型调用。

### 9.7 Subagent

Subagent 是独立 Child Run，不把完整内部状态复制进父 Run：

```text
Parent ChildRunBranch
  └─ child_run_id → Child Session / Child Run
```

子 Run 完成后产生结构化 `ChildRunOutcome`：

```text
status
summary
artifacts
unresolved
usage
evidence_refs
```

多个 Subagent 支持：

- `all`：全部完成；
- `any`：任意成功；
- `quorum(n)`：达到指定数量；
- `manual`：由父 Agent 或用户决定；
- `race`：首个可接受结果，取消其余可取消 Branch。

Join 满足后只触发一次父模型调用。

### 9.8 运行中指导

用户在模型或工具执行期间发送的新指导时：

1. 写入 `GuidanceReceived`；
2. Reducer 判断当前 Effect 是否可取消；
3. 未开始的旧 Tool Branch 标记 `skipped_superseded`；
4. 已开始且不可取消的副作用继续记录，但其结果携带 `superseded` 元数据；
5. 在安全提交边界重新构建 Context；
6. 触发新的模型调用。

不能只取消 Python Task 而不记录模型或工具请求的终态。

### 9.9 Quit 与最终回答

`quit` 不应继续是循环局部变量。它是 `CompletionRequested` 控制事件：

```text
CompletionRequested
→ 校验活动 Branch、必要验证、公开回答和未解决项
→ 条件满足：RunCompleted
→ 缺少公开回答：ModelFinalizationRequested（有界一次）
→ 仍不合法：RunFailed(protocol_error)
```

这样保留 Cyrene“模型主动声明完成”的产品语义，同时消除 `quit_requested` 布尔状态。

## 10. 并发与结果提交

### 10.1 工具并发

保留现有 Workbench Inbox 中有价值的资源冲突思想，迁移为 `EffectScheduler`：

- 每个 Effect 声明读写资源键；
- 无冲突 Effect 可以并行；
- 有冲突 Effect 按确定顺序执行；
- 全局和组件级并发上限独立；
- 权限、timeout、retry 是 Pipeline 阶段，不散落在工具中。

### 10.2 有序提交屏障

模型一次返回多个 Tool Call 时，为每个调用记录 `ordinal`。工具 body 可以乱序完成，但
Model Projection 按 ordinal 提交 Tool Result。这样保持 Provider 工具协议和缓存前缀稳定。

UI 可以立即显示真实完成时间；模型可见顺序与 UI 时间顺序是两个不同投影。

### 10.3 取消

取消分三个层级：

- Run cancellation：停止创建新 Effect，向所有活动 Branch 广播取消；
- Branch cancellation：取消一个 Child Run、Terminal、Media 或工具；
- Effect cancellation：取消当前可取消的具体执行。

不可取消的 Effect 不能假装已停止。Run 进入 `CANCELLING`，等待它完成或记录
`outcome_unknown`，再形成终态。

## 11. Context、双 Lane 与缓存

### 11.1 保留 Decision/Execution Lane

状态机重写不等于删除双 Lane。OpenAI-compatible Provider 继续使用：

- Decision Lane：理解、直接回答、`use_tools`、`ask_user`、`quit`；
- Execution Lane：计划、完整工具集、执行、验证和最终回答。

Codex Provider 保留自身 Transcript Policy，不自动跨 Provider Family fallback。

### 11.2 Lane 是投影，不是独立消息库

两条 Lane 从同一 Event Log 派生不同的模型视图：

```text
Event Log
 ├─ project(DecisionPolicy)  → Decision transcript
 └─ project(ExecutionPolicy) → Execution transcript
```

`ExecutionHandoff` 和 `ExecutionOutcome` 继续作为显式领域事件存在，但不复制整个模型历史。

### 11.3 缓存身份

保留当前缓存身份因素：

```text
Provider Profile
Model
Lane
System Prompt Version
Tool Schema Hash
Context Policy Version
Lane Epoch
```

状态机本身不会自动提高缓存命中率。真正的优势来自：

- append-only Event Log；
- 稳定 Context Component 顺序；
- 固定 Tool Catalog Snapshot；
- 工具结果有序提交；
- Ask User/审批恢复不重写旧 Prompt；
- UI/租约/审计事件不进入模型投影；
- Context Compaction 只在明确 Lane Epoch 边界发生。

### 11.4 Context Compaction

压缩不能修改原 Event。建议记录：

```text
ContextCompactionCreated(
    lane,
    covered_sequence_range,
    summary_ref,
    receipt_refs,
    new_lane_epoch,
)
```

Projection 在新 Epoch 使用摘要遮蔽旧区间，审计和回放仍能访问原事件。

## 12. Skill 与自动学习

### 12.1 Skill 不只是 Prompt 文件

V2 Skill 建议分两层：

1. Instruction Skill：提供领域知识、操作规范和 Context；
2. Executable Skill：声明一个受版本控制的状态子图。

示例：

```yaml
id: research-report
version: 3
inputs:
  topic: string
components:
  context: [web-policy, citation-policy]
graph:
  - model: plan
  - parallel:
      - tool: search
      - child_run: literature-review
  - approval: confirm-outline
  - model: synthesize
permissions:
  network: read
  filesystem: workspace-write
recovery:
  retry_read_effects: true
  unknown_write_effect: ask_user
evals:
  - citations_present
  - requested_scope_covered
```

### 12.2 自动学习流程

自动学习不能直接修改安全内核或生产 Skill。建议流程：

```text
成功 Run 轨迹
→ 提取候选重复子图
→ 生成 Skill Draft
→ 脱敏与权限分析
→ Replay/Eval
→ 用户或策略审批
→ 发布新 Skill Version
→ 小流量启用
→ 指标回归与回滚
```

学习对象是稳定的事件和组件调用，而不是旧 loop 中无法重放的局部控制路径。这是状态机
方案对 Skill 自动学习最重要的优势。

### 12.3 自定义边界

Skill 可以高度自定义：Context、模型、工具、分支、Join、重试、审批点和评估。但不能：

- 绕过 Permission Kernel；
- 伪造历史 Event；
- 修改已冻结 Catalog；
- 删除审计信息；
- 让非幂等 Effect 无限制自动重试；
- 把私有 Context 提升到公开输出。

## 13. 持久化设计

建议在现有正式数据库中新增独立 V2 表，不复用含义不同的旧表：

```text
agent_runs_v2
agent_events_v2
agent_snapshots_v2
agent_branches_v2
agent_effects_v2
agent_effect_attempts_v2
agent_artifacts_v2
agent_projection_offsets_v2
agent_component_snapshots_v2
```

### 13.1 表的职责

- `agent_runs_v2`：Run 身份、顶层终态和兼容查询字段；
- `agent_events_v2`：append-only 规范事件；
- `agent_snapshots_v2`：可重建 Reducer Snapshot；
- `agent_branches_v2`：活动 Branch 的查询索引，不是第二事实源；
- `agent_effects_v2`：Outbox、Claim、租约、幂等和结果状态；
- `agent_effect_attempts_v2`：重试、错误、Provider identity 和耗时；
- `agent_artifacts_v2`：大内容、文件和外部资源引用；
- `agent_projection_offsets_v2`：UI、搜索和分析投影消费位置；
- `agent_component_snapshots_v2`：Run 冻结的组件、Prompt、Tool 和策略版本。

### 13.2 数据库约束

至少需要：

- `(run_id, sequence)` 唯一；
- `event_id` 唯一；
- `effect_id` 唯一；
- `(effect_id, result_kind)` 的有效终态唯一；
- `tool_call_id` 在对应 Model Call 内唯一；
- Branch 只能从非终态迁移到终态一次；
- Snapshot 的 `last_sequence` 不得超过 Event Log；
- Claim 使用租约 token 和 compare-and-swap 更新。

### 13.3 敏感数据

Event Payload 默认不保存：

- Credential；
- 未脱敏的环境变量；
- 私有 Reasoning；
- 无必要的绝对路径；
- 大段网页正文或媒体二进制；
- Remote Token。

敏感内容使用加密引用或短期 Blob，并由 Context Projection 按 Actor 权限读取。

## 14. UI、API 与远程控制

### 14.1 UI 使用投影，不驱动内核

Workbench 订阅 V2 Timeline Projection：

```text
RunStarted
AssistantDelta
ToolCallPresented
ApprovalPresented
BranchProgress
ArtifactAvailable
RunWaiting
RunCompleted/Failed/Cancelled
```

UI 的按钮操作提交 Command，例如 `DecideApproval`、`AnswerHumanInput`、`CancelBranch`，
Command Handler 校验后生成领域事件。UI 不直接写 Branch 或 Run 状态。

### 14.2 保留公开合同

迁移期间应保持：

- 现有 Task/Chat 查询 API；
- SSE 事件的用户可见语义；
- Tool Wire 名称和参数；
- Actor Policy；
- 附件和 Artifact 下载；
- Remote/CLI/Electron 的基本操作；
- 历史 Session 可读。

可以通过 V2 Projection 生成旧 API 需要的兼容字段，但不得让旧 DTO 反过来限制内部状态模型。

### 14.3 新的推荐 API 语义

长期建议逐步公开：

```text
POST /runs
GET  /runs/{run_id}
GET  /runs/{run_id}/events
POST /runs/{run_id}/guidance
POST /runs/{run_id}/cancel
POST /branches/{branch_id}/answer
POST /branches/{branch_id}/approve
POST /branches/{branch_id}/cancel
```

所有写请求带 client request id，服务端幂等。

## 15. 重写与复用边界

### 15.1 应重写

| 领域 | 决策 | 原因 |
|---|---|---|
| Agent 主执行 loop | 重写并最终删除 | 隐式状态和多职责的主要来源 |
| 工具调度控制层 | 重写 | 改成 Effect Pipeline、租约和有序提交 |
| Ask User 协议 | 重写 | 变成可恢复 HumanInput Branch |
| 权限审批编排 | 重写 | 附着原 Tool Call，不重新调用模拟恢复 |
| 计划确认编排 | 重写 | 使用 Plan Artifact 和确认 Branch |
| Subagent 父子协调 | 重写 | Child Run、Join、取消和恢复需要正式模型 |
| Terminal/媒体 Wake 上层 | 重写 | 完成原 Branch，不启动无关新 Turn |
| Context 总装配 | 重写 | 改成稳定、可预算的组件投影 |
| Run 恢复器 | 重写 | 从终止旧 Run 改成恢复 Effect/Branch |
| Workbench Agent 状态映射 | 重写 | 从事件投影生成，不再散布布尔判断 |

### 15.2 优先复用行为和测试，接口允许重写

| 领域 | 保留内容 |
|---|---|
| Model Runtime | Provider adapter、认证、Quota、Usage、Reasoning/DSML 兼容 |
| 双 Lane | Handoff/Outcome 语义、缓存隔离、Provider Family 边界 |
| Tool Catalog | Wire Bundle、Package Gateway、Snapshot、Actor Policy |
| Inbox | 资源冲突调度思想、并发限制和已有测试场景 |
| Terminal Daemon | PTY、资源句柄、持久 Wake、租约 |
| Media Manager | Job、Provider、持久结果和 Artifact |
| 文件/搜索/API 工具 | 经评审后的业务执行逻辑 |
| 数据迁移 | SQLite 安全迁移、Backup、Marker 和冲突保护原则 |
| WebUI | 用户交互、卡片、Timeline 和现有产品功能 |
| Observability | Run/Model/Tool 标识、Usage 和阶段计时 |

### 15.3 工具代码评审标准

每个旧工具按以下问题决定适配、拆分或重写：

1. 是否只依赖明确输入并返回明确输出？
2. 是否直接修改 Agent、Session 或 Workbench 全局状态？
3. 是否自行处理权限、重试、UI、持久化和恢复？
4. 是否持有无法序列化的长生命周期对象？
5. 是否有稳定的幂等或恢复语义？
6. 是否能在独立测试中运行而不启动完整 Agent？

处理原则：

- 业务纯净、测试充分：保留并加 Adapter；
- 业务有效但职责混杂：提取 Executor，重写外围；
- 控制流驱动或全局耦合严重：直接重写；
- 重复、无测试或已经被新能力替代：删除。

### 15.4 当前能力覆盖矩阵

这张表用于约束“新状态机能否支撑当前 Cyrene 的全部功能”。没有映射到 V2 原语的能力，
不能被视为迁移完成。

| 当前能力 | V2 表达 | 迁移说明 |
|---|---|---|
| 简单对话 | Decision Model Branch | 一次模型完成即可终结，不进入工具调度 |
| OpenAI-compatible 双 Lane | 两个 Model Context Projection | 保留 Handoff/Outcome 和独立 Epoch |
| Codex Provider | Provider-specific ModelComponent/Policy | 保留现有协议，禁止跨家族 fallback |
| Direct Tool | ToolDefinition + ToolExecutor | 保持 Wire Schema 和名称稳定 |
| Tool Package | Catalog/Package Component | 保持 `discover → describe → invoke` |
| Catalog Snapshot | Component Snapshot | Run 创建时冻结，版本变化不影响在途 Run |
| Actor Policy | Kernel Authorization | Main、Execution、Subagent 分别裁决 |
| Ask User | HumanInput Branch | 回答成为原 Tool Call Result |
| 权限审批 | Approval Branch | 附着原 call_id，批准后继续原 Effect |
| 计划确认 | Plan Artifact + Confirmation Branch | 接受、修订、拒绝都有明确结果 |
| Quit | CompletionRequested Event | 经过完成不变量检查后形成终态 |
| DeepReflect | Reflection Branch/Skill（待 Phase 0 决策） | 不再通过循环标志跳过同批工具 |
| 无正文 Finalization | Bounded Model Finalization Branch | 最多按策略补一次，不形成无界修复循环 |
| 运行中 Guidance | GuidanceReceived + Supersession Policy | 在安全边界取消、跳过或标记旧 Effect |
| 工具批量并行 | EffectScheduler + Commit Barrier | 保留资源冲突，按模型 ordinal 提交结果 |
| Tool timeout/retry | Effect Attempt Policy | 每次尝试持久化，非幂等操作限制重试 |
| Subagent | Child Run Branch | 子 Session 独立，父 Run 使用 Join Policy |
| Subagent 消息/指导 | Child Run Command/Event | 通过父子授权，不共享可变对象 |
| Goal Loop | Workflow Branch/Application（待 Phase 0 决策） | 计划步骤和等待子代理显式化 |
| Terminal/PTTY | TerminalJob Branch + Daemon Adapter | 复用 Daemon，完成原 Branch |
| Media 生成 | MediaJob Branch + Manager Adapter | 复用 Provider/Manager，结果成为 Artifact |
| Browser/Search/Fetch | Tool 或 Resource Branch | 短任务直接返回，长任务保存 resource handle |
| MCP | Tool Provider Component | 服务连接归生命周期，调用仍走 Tool Pipeline |
| PowerPoint/文档等长 Episode | Tool/Workflow Branch + Artifact | 压缩只在 Lane Epoch 边界生成 Receipt |
| Workspace/文件上下文 | Context Component | 按 Project/Actor 权限生成稳定片段 |
| Memory/SOUL/Learning | Context/Observer Component | 读取进入投影，写入作为受权限 Effect |
| Knowledge/Library | Context + Tool Components | 保留项目隔离和稳定 Artifact 引用 |
| 附件与文件资源 | Artifact Store + Context Component | Event 只保存引用、摘要和可见性 |
| 流式回复 | Observation Stream + Final Event | Delta 给 UI，最终结果才驱动核心状态 |
| Usage/Cache/Trace | Usage Projection | 所有隐藏模型调用也按 call_id 计量 |
| Scheduler/系统主动运行 | System Command → Run | 使用显式 Actor，按策略禁用 Ask User |
| Remote/CLI/Electron | Command/API Adapter | 共享 Run/Branch 服务，不复制业务状态 |
| Retry | 新 Attempt 或显式 Replacement Command | 不重复用户消息，不隐式重放副作用 |
| Cancel | Run/Branch/Effect Cancellation | 不可取消动作进入等待或 unknown outcome |
| 进程重启恢复 | Event Replay + Effect Recovery | 恢复原 Run，不默认创建新 Turn |
| Workbench Timeline | UI Projection | 显示状态但不拥有状态 |
| 脱敏、DSML、Reasoning | Model/Public Projection Policy | 内部内容不因事件化而扩大可见范围 |

这套映射说明状态机原语足以承载当前功能，但“足以表达”不等于“已经迁移”。每一行都必须
在相应 Phase 中有实现 Owner、协议测试和可观察的退出标准。

## 16. 与 DeepSeek Harness 的关系

DeepSeek Harness 证明了以下方向的可行性：

- Agent 能力通过插件、Service 和 Scope 组合；
- Session 使用 append-only typed event log；
- 模型上下文从 Session Event 派生；
- 工具执行采用分阶段 Pipeline；
- Tool Call identity 在审批和执行间保持不变；
- Subagent 使用独立 Child Session；
- 后台能力可以统一进入 Job Registry。

但其默认执行器仍是 `ReactLoopAgent`，主要通过 loop 持续执行 turn/step；冷恢复更偏向修复
未闭合 transcript 并中断旧 turn，而不是恢复任意未完成 Effect。其本地 Job 和活跃
Approval 也不等同于完整的跨进程 Branch。

Cyrene 应吸收插件边界、事件 Session、Tool Pipeline、Scope 和 Child Session 思想，
但形成自己的定位：

> 所有 Agent 行为都是持久、可审计、可等待、可恢复的 Branch/Effect；模型 loop 只是
> 可选 Driver，而不是系统的拥有者。

参考：

- [DeepSeek Harness Architecture](https://github.com/deepseek-ai/deepseek-harness/blob/master/docs/architecture.md)
- [DeepSeek Harness Agent Loop](https://github.com/deepseek-ai/deepseek-harness/blob/master/packages/core/agent-loop/README.md)
- [DeepSeek Harness Session Persistence](https://github.com/deepseek-ai/deepseek-harness/blob/master/docs/subsystems/persistence.md)
- [DeepSeek Harness Tool Execution Pipeline](https://github.com/deepseek-ai/deepseek-harness/blob/master/docs/tool-execution-pipeline.md)
- [DeepSeek Harness Approval](https://github.com/deepseek-ai/deepseek-harness/blob/master/docs/subsystems/approval.md)
- [DeepSeek Harness Subagent](https://github.com/deepseek-ai/deepseek-harness/blob/master/docs/subsystems/subagent.md)

## 17. 推荐目录结构

新内核与旧内核平行存在，避免在旧 loop 中逐层嵌套状态机：

```text
src/cyrene/runtime_v2/
  kernel/
    commands.py
    events.py
    state.py
    reducer.py
    transitions.py
    invariants.py
    branches.py
    effects.py
    scheduler.py
    recovery.py
  persistence/
    event_store.py
    snapshot_store.py
    effect_outbox.py
    migrations.py
  components/
    registry.py
    model.py
    context.py
    tools.py
    human_input.py
    approval.py
    plan.py
    child_run.py
    terminal.py
    media.py
    skills.py
  projections/
    model_context.py
    workbench_timeline.py
    audit.py
    usage.py
  adapters/
    legacy_tool.py
    model_runtime.py
    terminal_daemon.py
    media_manager.py
    workbench_api.py
  application/
    command_bus.py
    run_service.py
    branch_service.py
```

如果团队不希望使用 `runtime_v2` 作为最终命名，可以在切换完成后整体移动到
`src/cyrene/agent_runtime/`。迁移期间应保持物理隔离，以便明确判断依赖方向。

## 18. 分阶段实施计划

### Phase 0：规范冻结与基线

工作：

- 冻结 Event、State、Effect、Branch 的 V1 schema；
- 列出所有工具、控制工具和异步资源；
- 为当前八类 Benchmark 增加恢复、审批和异步任务样本；
- 记录公开 API、SSE、Tool Wire、数据库和 UI 行为合同；
- 建立架构依赖规则，禁止 V2 依赖旧 `agent.py` 控制函数。

退出标准：设计评审通过，关键不变量有测试描述，所有现有能力完成清单映射。

### Phase 1：纯内核与持久化

工作：

- 实现 Event Store、Snapshot、Reducer、Effect Outbox；
- 实现 Run/Branch 生命周期和恢复扫描；
- 使用 fake components 完成无网络回放；
- 验证重复事件、重复结果、租约过期和崩溃点。

退出标准：随机终止进程后，纯测试 Run 能从任意事件边界恢复并得到相同终态。

### Phase 2：简单对话与普通工具

工作：

- 接入 Model Runtime Adapter；
- 实现 Context Projection V1；
- 接入只读和幂等普通工具；
- 实现并行执行、资源冲突和有序提交；
- 为内部开发账号提供 Runtime V2 feature flag。

退出标准：纯对话、单工具、多工具、错误工具、取消和 Retry 的用户可见行为等价。

### Phase 3：Ask User、审批与计划确认

工作：

- 实现三类 Human Branch；
- 改造 Workbench UI Command；
- 保持原 Tool Call identity；
- 覆盖刷新页面、关闭客户端、后端重启、重复点击和过期回答。

退出标准：等待期间无常驻 Agent Task，重启后可以继续原 Run，重复回答不会执行两次。

### Phase 4：Terminal、媒体与 Subagent

工作：

- 将 Wake Record 映射到原 Branch；
- 接入资源查询、取消和 Outcome Unknown；
- 实现 Child Run、Join Policy 和父子取消；
- 保留现有 Daemon/Manager，删除 wake-to-new-turn 编排。

退出标准：长任务跨 Web/Electron 重启恢复；父 Run 只在满足 Resume/Join Policy 时调用模型。

### Phase 5：双 Lane、缓存与 Context Compaction

工作：

- 将 Decision/Execution 改为 Event Projection；
- 接入 Handoff/Outcome；
- 校验缓存 Key、Lane Epoch 和 Tool Snapshot；
- 迁移 Runtime Guidance、Finalization 和 Provider 特殊协议。

退出标准：缓存命中率和模型调用数不低于基线，Provider Family 与 transcript 不发生串线。

### Phase 6：Skill 子图、全量切换与删除旧内核

工作：

- 实现 Skill Graph V1 和 Eval/发布流程；
- 新 Run 默认使用 V2；
- 旧 Session 只读兼容，未完成旧 Run 明确中断或经受控导入；
- 删除旧 Agent loop、临时 Prompt 恢复和重复状态映射；
- 把 Runtime V2 移入最终正式目录。

退出标准：功能矩阵全部通过，旧内核无生产入口，依赖扫描证明新代码不反向调用旧控制流。

## 19. 迁移策略

### 19.1 不做双写状态机

同一个 Run 不应同时由旧 loop 和新 Reducer 驱动。Feature flag 只在 Run 创建时选择 Runtime，
选择结果写入 Run identity，运行中不能切换。

### 19.2 历史 Session

- 已完成的旧 Session 继续通过 Legacy Reader 展示；
- 新 Run 可以把必要历史投影成 `LegacyTranscriptImported` Context Source；
- 不把旧消息逐条伪造成原生 V2 Effect 历史；
- 未完成的旧 Run 默认标记为 legacy interrupted；
- 只有具备完整 call/result/resource identity 的旧 Run 才允许专门迁移器恢复。

### 19.3 工具渐进迁移

提供 `LegacyToolAdapter`，但它只适用于不控制 Agent 状态的工具。Adapter 有明确淘汰指标：

- 不允许识别 `awaiting_user` sentinel；
- 不允许回调 `_run_chat_agent`；
- 不允许直接发送 RunCompleted；
- 不允许持有 Workbench Session 可变对象；
- 不允许隐藏新的后台资源而只返回字符串。

违反任一条件的工具必须原生迁移或重写。

### 19.4 回滚

V2 发布初期保留“新 Run 使用旧 Runtime”的配置回滚，但已经由 V2 创建的 Run 不交给旧
Runtime 继续。必要时允许 V2 Run 安全暂停、导出诊断包和人工终结。

## 20. 测试策略

### 20.1 Reducer 测试

Reducer 使用事件序列驱动，不 mock 网络：

- 任意合法序列产生预期状态；
- 非法迁移被拒绝；
- 相同事件重复投递无副作用；
- Snapshot 重建等于完整回放；
- Event schema 升级保持语义；
- 终态不能被普通事件重新打开。

### 20.2 崩溃矩阵

对每个 Effect 在以下位置模拟进程终止：

```text
Effect 写入前
Effect 写入后、Claim 前
Claim 后、执行前
外部执行中
外部成功后、结果事件前
结果事件后、Snapshot 前
Snapshot 后、UI Projection 前
```

为幂等、可查询和不可查询副作用分别验证恢复策略。

### 20.3 协议测试

- Model Tool Call 与 Tool Result 一一匹配；
- Ask User 回答关闭原 call；
- Approval 决定不能改变已展示参数；
- 并行结果按 ordinal 进入模型；
- Decision 与 Execution transcript 不互相继承；
- Context Compaction 不删除审计事件；
- DSML、Reasoning 和敏感信息不进入公开投影。

### 20.4 集成场景

至少覆盖：

1. 简单对话；
2. 单工具；
3. 并行工具与资源冲突；
4. Ask User 后刷新页面再回答；
5. 权限批准、拒绝、重复点击和过期；
6. 计划接受、修订和拒绝；
7. Terminal 跨后端重启；
8. Media 跨后端重启；
9. 多 Subagent all/any/quorum；
10. 运行中 Guidance 抢占；
11. Cancel 与不可取消副作用；
12. Provider timeout/retry；
13. Lane compaction 与缓存；
14. 旧 Session 展示和新 Run 接续。

### 20.5 性能与缓存验收

对现有确定性 Benchmark 比较：

- 端到端延迟；
- TTFT；
- 模型调用次数；
- Prompt/Completion/Cached Token；
- Tool 排队和执行时间；
- Event 和数据库写入数量；
- Snapshot 重放时间；
- 重启恢复时间；
- 峰值 RSS；
- UI 事件延迟。

状态机允许多写事件，因此必须使用批量事务、Blob 引用和异步 Projection，避免可恢复性以
明显热路径退化为代价。

## 21. 可观测性

每次模型和工具调用应能沿以下关系追踪：

```text
session_id
  → run_id
    → turn_id
      → branch_id
        → effect_id
          → model_call_id / tool_call_id / resource_id
```

推荐指标：

- 活动、等待、取消中的 Run 数；
- Branch 按 kind/status 的数量和等待时长；
- Effect Claim 延迟、重试和租约失效；
- `outcome_unknown` 数量；
- 重复事件/结果去重数；
- 每个 Context Component 的 Token 和构建耗时；
- Lane 缓存命中率；
- Subagent join 等待；
- 人工审批响应时间；
- 恢复后成功继续率；
- 因协议错误终止的 Run 比例。

诊断界面应能从一个 Tool Result 回溯到原 Model Call、审批、Effect Attempt 和外部资源，
但默认用户视图只显示安全摘要。

## 22. 风险与控制

### 22.1 状态和事件数量膨胀

控制：顶层状态保持少量；类型差异放在 Payload；区分业务事件与仅观察事件；Delta 和
Progress 采用单独流或可压缩存储。

### 22.2 Event Schema 过早固化

控制：版本化 Envelope/Payload；Upcaster；先用代表性轨迹验证 V1；事件记录事实而不是
当前 UI DTO。

### 22.3 插件破坏安全不变量

控制：组件只能请求 Effect，不能直接写状态；最终权限在内核裁决；Run 冻结 Registry
Snapshot；插件输出经过 schema 和 visibility 校验。

### 22.4 数据库成为瓶颈

控制：单 Run 批量追加；WAL；大内容外置；Snapshot；Projection 异步消费；高频 Stream
Delta 与关键业务事件分层。

### 22.5 新旧 Runtime 长期并存

控制：每个 Phase 有删除清单和退出标准；禁止 V2 反向依赖旧控制函数；设置明确切换日期；
兼容 Adapter 必须有 Owner 和移除条件。

### 22.6 状态机变成另一种巨型条件树

控制：Reducer 按领域拆分；事件由明确 Aggregate 处理；Branch Driver 不参与全局归约；
用不变量和表驱动迁移替代散落 `if/elif`；架构测试限制模块依赖。

### 22.7 模型调用次数意外增加

控制：Resume Policy、Join Barrier 和 Context Policy 明确规定触发条件；Progress 不触发模型；
同一事务产生的多个完成事件合并调度；建立每种场景的调用次数预算。

## 23. 验收标准

只有满足以下条件，才可以认为内核重构完成：

### 架构

- Agent 任务生命周期不依赖单个长时间运行的业务 loop；
- Reducer 无 I/O 且事件可以完整回放；
- 所有外部动作都有 Effect Record；
- 所有等待能力都有 Branch 和稳定资源引用；
- 新内核不调用旧 `agent.py` 控制函数。

### 功能

- 当前 Cyrene Main Agent 的全部能力完成 V2 映射；
- Ask User、审批和计划确认跨进程恢复；
- Terminal、媒体和 Subagent 返回原 Run；
- Decision/Execution Lane、Codex Policy 和 Tool Wire 保持；
- Guidance、Cancel、Retry、Quit、Reflection 和 Finalization 有明确事件语义。

### 一致性与安全

- 重复回调不会重复执行后继动作；
- 非幂等未知结果不会被盲目重试；
- 权限最终裁决不可被工具或 Skill 绕过；
- UI、Remote、日志和模型 Context 使用各自安全投影；
- 历史数据升级不覆盖或伪造旧记录。

### 性能

- 简单对话不增加不必要模型调用；
- 常用工具场景的 P50/P95 延迟无不可接受退化；
- 缓存命中率不低于当前双 Lane 基线；
- 重启恢复时间和事件重放时间达到预设预算；
- 数据库事件增长有可预测的保留和压缩策略。

### 可维护性

- 新增一个同步工具不需要修改 Reducer；
- 新增一种异步资源只需要 Branch Driver、事件 Payload 和投影注册；
- 新增 Context Component 不修改 Model/Tool Executor；
- Skill 子图可以在不修改内核的情况下组合已有组件；
- 旧 loop、sentinel 和 wake-to-new-turn 路径已经删除。

## 24. 需要在实施前确认的设计决策

以下问题不阻碍架构方向，但必须在 Phase 0 固化：

1. Event Store 继续使用主 SQLite，还是为高频 Run Event 使用独立数据库；
2. Stream Delta 是否进入 Event Store，或只进入短期 Observation Stream；
3. Branch/Effect Payload 使用 JSON、MessagePack 还是版本化 typed JSON；
4. Snapshot 频率按事件数、时间还是关键边界触发；
5. Terminal/Media 的 daemon resource handle 统一格式；
6. 不可查询副作用的 `outcome_unknown` 用户交互规范；
7. Reflection 是 Model Branch、Skill 子图还是独立控制事件；
8. Goal Loop 在 V2 中作为 Workflow Branch 还是上层 Application；
9. Codex Provider 首期走 V2 Agent Driver，还是保留旧 Provider Driver 后再迁移；
10. Skill Draft 的发布审批主体和回滚策略。

## 25. 最终建议

Cyrene 应进行一次有边界的 Agent 内核重写，而不是继续对当前 loop 做局部抽取。目标不是
追求“代码中没有循环”，而是让每个业务状态、等待关系和外部副作用都有持久、显式、可回放
的表达。

推荐的最终定位是：

> Cyrene 是一个组件化、桌面原生、事件溯源、支持精确续接的 Durable Agent Runtime。
> 模型、用户、工具、Subagent、Terminal、媒体和 Skill 都通过统一 Branch/Effect 协议协作。

实现上应吸收 DeepSeek Harness 的插件边界、Session Event、Tool Pipeline 和 Child Session
经验，但不保留默认 ReAct loop 作为任务生命周期拥有者，也不把权限、一致性和幂等规则交给
任意插件。重写应保护 Cyrene 已经形成的双 Lane、缓存、Tool Wire、Actor Policy、桌面资源
和数据安全能力，同时删除旧控制流产生的偶然复杂性。
