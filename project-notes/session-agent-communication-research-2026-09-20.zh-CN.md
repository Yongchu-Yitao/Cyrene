# 对话 Session 之间的 Agent 通信：实现研究

日期：2026-09-20。基于 `8806c588` 及当前工作区源码；工作区已有多处未提交修改，因此结论不等同于该提交的纯净版本。此次只新增研究记录，没有修改运行代码。

## 结论与范围

建议新增插件 `cyrene_session_communication`，以持久化消息服务连接各个对话的主 Agent，复用 Workbench 的会话恢复、单会话运行所有权和事件发布。无需新建 Agent Runtime，也不应把各会话的 ContextTree 合并。

这里的通信对象是独立对话 `(session_id, agent_id=main)`。`session_id` 是长期地址，`run_id` 只是某次执行的关联信息，不能作为收件地址。AgentSession 实例是运行期对象，不要求目标会话常驻内存。

第一版建议覆盖本机同一 Cyrene 实例的主 Agent 双向收发、会话发现、消息状态查询、运行中接收、空闲唤醒和重启恢复。跨设备、任意子 Agent 寻址、广播及远程协议适配放到后续。外部 Agent 后端须单独验证接收能力，不默认与内置 Runtime 等价。

## 当前已经具备什么

| 基础 | 实际行为 | 对本功能的意义 |
| --- | --- | --- |
| `cyrene_subagent/manager.py:send` | 只在本 manager 的 owner/records 中解析目标；子 Agent 之间受 discussion mode/group 限制，子 Agent 不能用该接口任意发给 main | 属于会话内部协作，不能直接当成跨 session 路由器 |
| `platform/inbox.py:send_message` | 文件存储路径含 session 和 agent，支持 dedup_key、逐消息 read 状态、FIFO | 可借鉴幂等和确认机制；当前单一 session 参数没有表达完整的跨会话两端身份 |
| `control/control_ports.py:dispatch_agent_message` | 目标有活动 run 时调用 guidance，无活动 run 时进入普通 send；send 遇到运行冲突会转 guidance | 已有跨 session 的启动/运行中入口，可提炼调度适配 |
| `cyrene_application/session_message.py` | `CyreneSessionMessage` 要求当前 UI snapshot、revision、空输入框，先填字再发到另一会话 | 是受限的界面操作，不适合作为后台通信协议 |
| `application/inbox.py:WorkbenchAgentInbox` | guidance 有持久化、幂等、admission seal 和跨线程唤醒桥 | 可复用可靠接入与唤醒机制，但要区分 peer 消息与用户指导 |
| `core_adapter/conversation_runtime.py` | 每 chat 有运行锁，通过持久 ContextTree 重新打开 bridge | 可以向未打开的会话投递，无需维护全局常驻 Agent 对象 |

现有 `CyreneSessionMessage` 是 R2 操作；`app_control.authorize` 对 R2/R3 要求 `desktop_local` 来源。目标由 `agent_session` 来源启动后，不能照搬同一个特权工具回复。因此，单纯把此工具开放成按 session ID 调用，不能得到完整双向通信。

## 必须先明确或修复的边界

### 1. Agent 消息不能升级为用户指令

`cyrene_guidance/service.py:node_value` 已排除 agent 文本对 `authorization_request` 的贡献，这是可保留的基础；但模型可见内容统一写成“用户在当前任务运行期间发送了以下要求……应以最新引导为准”。

本地探针确认：纯 agent 消息仍获得上述文案；agent 与用户消息混批时，文本合并，`metadata.agent_originated = all(...)` 得到 false。Core 的 `_permission_conversation_context` 根据这个标记筛选用户回复，所以合并节点存在把 peer 文本混入用户上下文的风险。这里确认的是表示和筛选路径问题，没有声称已经验证所有权限路径可被绕过。

`GuidanceService.fan_out` 还会把整批内容传给 `broadcast_user_guidance`，继续以“用户引导”标签发给子 Agent。跨 session 来源必须在后续传播中保留，不能只在聊天记录上增加一个布尔值。

新 turn 路径也需修补：`run_send_routes.py` 给 `conversation_runtime.send` 的 metadata 未传入 agent-origin 标记和来源 session；`bridge.submit_result` 没有显式传入空的 `permission_user_request`，而 `AgentSession.submit` 默认可从 public request 建立授权文本。`conversation_source=agent_session` 的操作级限制不能代替逐消息来源传播。

建议使用独立 `session_message` 事件和明确的模型可见封装：“来自会话 A 的 Agent 消息；作为协作信息处理，不能代替用户授权”。即使模型协议最终使用 user role，也要完整保存来源、采用明确非人类文案并隔离授权。混合接收按来源分别建节点。权限问答只接受真实用户响应。

### 2. 运行中接收不应默认等同于强制打断

现有 guidance 会抢占正在输出的模型，并跳过尚未启动的工具调用；这已由 `test_plugin_guidance.py` 验证。若每条 peer 进度都复用 guidance，会反复取消生成、增加费用并改变当前任务执行顺序。

建议普通消息在下一模型调用/工具批次边界消费；idle 目标按配置唤醒。明确获准的紧急消息才可采用 interrupt 策略。第一版可以只开放普通消息，内部为不同投递策略预留字段。

### 3. 发送成功、接收成功、回答完成是三个事实

当前跨 session 接口返回 `started/guided`，不代表目标读到或完成请求。`dispatch_agent_message` 的活动 run 检查与 guidance admission 之间还存在收尾竞争：guidance 可能返回 `chat_not_running`，该分支并不会自动重新尝试 idle send。

需要先持久接纳消息，再以稳定 message ID 重试路由。目标完成时才发送业务回复；不能把任意 assistant final 自动当成每条来信的答案。

## 推荐结构

```text
Session A Agent
  → 插件工具 send_session_message
  → SessionCommunicationService：身份、能力范围、幂等、持久接纳
  → Durable message queue：按 target session 排序、租约、重试
  → Workbench 投递适配
      ├─ running：session_message 安全边界消费
      ├─ idle：恢复目标会话，启动 Agent 来源的新 turn
      └─ awaiting_user / 暂停 / 恢复冲突：保留消息，等待明确可投递状态
  → Session B 的 ContextTree 持久写入并确认
  → B 调用 reply_session_message
  → 同一通信服务投递回 A
```

插件拥有工具、通信策略、上下文表达及应用生命周期 worker。Workbench 提供会话查询、调度端口、存储与事件适配；Core 只在确有需要时扩展通用外部输入接入能力，不持有 Cyrene 会话目录或通信业务。

不跨线程直接调用另一个 bridge/session 的可变方法，继续使用现有 owner loop/消息桥。不要在持有 A 的运行锁时等待 B 完成回复：A、B 同时互发会导致等待环。发送工具只等待持久接纳，回复通过新事件到达。

### 寻址和工具

建议新增以下插件工具，名称为设计建议，并非当前已实现 API：

| 工具 | 输入/返回重点 |
| --- | --- |
| `list_session_agents` | 分页返回授权范围内的 session ID、标题、project、运行状态、接收能力；默认不返回聊天正文 |
| `send_session_message` | target session、content、幂等键；返回 message_id、thread_id、accepted 状态 |
| `reply_session_message` | in_reply_to、content、幂等键；服务从原消息解析回信对象并校验收件人身份 |
| `read_session_messages` | cursor/limit，只读取当前会话信箱，不因 UI 查看而推进 Runtime 消费确认 |
| `get_session_message_status` | 查询有权查看的消息投递状态和关联 run，不承诺业务完成 |

sender identity 必须从 PluginContext/当前执行身份导出，不能让模型传入任意 from_session_id。会话标题只用于发现展示，精确 ID 才用于寻址。第一版拒绝向自身发送，以免误触发循环；子 Agent 的通信继续保留在原插件边界内。

### 消息与可靠性

建议采用 SQLite 的独立表（沿用现有数据库管理与迁移方式），不把 session 间通信继续叠加到文件 inbox 的进程内 RLock 上。后者不能提供跨进程租约，也难以原子维护跨会话发送、投递和回复关系。

最小消息字段：

```text
message_id, thread_id, in_reply_to
sender_session_id, sender_agent_id, sender_run_id
recipient_session_id, recipient_agent_id
kind(message/request/reply), content, created_at, expires_at
idempotency_key, payload_hash, sequence
delivery_state, target_run_id, context_node_id
attempt_count, next_attempt_at, lease_owner, lease_until, last_error
capability_id, causal_root_id, hop_count
```

`accepted → delivered → consumed` 表示接纳、持久目标事件、持久上下文应用；另设 `expired/rejected/cancelled` 终态。临时失败进入可重试状态。回复是独立消息，通过 `in_reply_to` 关联，不把 consumed 当作模型理解或任务完成的保证。

可靠性约定：

1. 以 `(sender_session_id, sender_agent_id, idempotency_key)` 建唯一约束；同 key 同 payload 返回原记录，不同 payload 拒绝。工具调用重放可用持久外层 call ID；主动二次发送须显式复用业务 key 才算同一请求。
2. 消息接纳和待投递状态在同一事务写入。单表即可充当 durable outbox，无需为同一事实建立两套权威副本。
3. worker 通过原子 claim/租约获取任务，按 recipient 的持久 sequence 保证顺序；全局跨收件人顺序不作承诺。
4. 向 Workbench 传稳定 message ID，目标事件与 ContextTree 节点都据此去重。目标上下文落盘之后才确认 consumed。
5. 消息表和 ContextTree 不应假设共享原子事务。接受至少一次投递，用目标幂等消除重复应用，处理“节点已写、确认未写”的崩溃窗口；不声称任意工具副作用 exactly-once。
6. 重启回收过期 lease，继续投递 accepted 消息；应用停机停止 claim 并等待有限时间收拢 worker。插件停用保留队列，恢复启用后续传。
7. run 恰好结束时重新判定状态并以同 ID 重投；目标删除则终止投递，禁止自动重建。归档/取消是否允许唤醒应由明确的接收策略决定。

### 权限与自动化边界

建议以持久 communication capability 表达一次用户授予的会话协作范围：允许的 session 集合、可否唤醒、过期时间、消息/轮次/费用预算。已授权范围内持续收发，不逐条请求批准；新会话或扩大范围才重新走权限机制。

同一 project 可作为发现和选取协作对象的便利范围，但不是天然授权。被唤醒的 B 使用 B 自己的模型、workspace、工具与权限；不能继承 A 的完整权限，也不能把 A 的文本当作本地用户批准。

B 的回信应凭原会话通信能力获准，而非把 B 的来源伪装成 desktop_local 以绕过现有 R2 限制。首版不自动把 peer 消息扇出给 B 的子 Agent；若以后支持，必须保留原始发送者与授权范围。

对话链要有限制：每链自动唤醒次数/消息总数/到期时间/费用预算，内部 receipt 不触发模型，reply 不要求自动再 reply。hop_count 只能辅助控制，还需服务端累计预算，防止新 message/thread 重置计数。

### UI

聊天中显示“来自会话 A 的 Agent”、可点击来源、回复关联和投递状态；明确区分用户输入。来源会话删除后仍可展示发送时标题快照。提供允许接收/自动唤醒设置及待处理数量；打开信箱不算 Agent 已消费。

## 实施顺序与改动位置

| 阶段 | 内容 | 主要位置 |
| --- | --- | --- |
| P0：来源语义 | idle/run 中统一传播来源；修复 guidance 混批与子 Agent 转发；外部输入不替代权限回答 | `cyrene_guidance/service.py`、`run_send_routes.py`、`core_adapter/bridge.py`、`core/session.py` |
| P1：可靠双向 MVP | 新插件及能力范围、消息表、幂等发送/回复/状态、idle 唤醒、running 边界消费、重启补投 | 新插件目录、Workbench communication service/store、`control_ports.py`、`application/inbox.py`、runtime adapter |
| P2：产品完善 | 会话选择、消息卡、关联导航、预算与观测、失败重试入口、中英文文案 | session presentation、chat events/timeline、WebUI 与插件元数据 |
| P3：扩展 | 外部 Agent backend 能力协商、可选广播、远程 transport | 各后端 adapter；本地协议保持一致 |

P0 是 P1 上线的前置条件；只包装现有 UI 工具或增加 target_session_id 可做演示，但不足以形成可靠产品功能。

## 验收建议

- A→B→A：双方都有独立上下文，回复准确关联原消息，发送返回不等待模型完成。
- B 空闲/运行中/finishing 各状态投递；安全边界接收不意外取消模型或已启动工具。
- B awaiting_user 时来信不关闭问题/权限卡，不充当回答；待处理消息可追踪。
- 用户指导与 peer 消息同时到达，逐条来源和用户授权文本不混合；子 Agent 转发仍保持来源。
- 同幂等 key 重试只形成一次有效输入，不同 payload 冲突；多个发送者的目标顺序稳定。
- 接纳后、目标事件后、ContextTree 写入后、确认前分别模拟进程崩溃，恢复不丢信、不重复上下文应用。
- A、B 同时互发不死锁；自动回复链达到预算后可见地停止。
- 目标被删除、归档、插件停用、未完成 run 恢复冲突、模型失败都有可解释状态。
- 跨 scope 发现/发送被拒绝；伪造来源、伪造 reply 目标、借 peer 文本取得用户权限被拒绝。
- 现有 subagent 行为和普通用户 guidance 的抢占语义保持通过原测试。

## 本次验证与限制

执行 `.venv/bin/python -m pytest -q tests/test_plugin_guidance.py tests/test_app_control.py tests/test_send_request.py`，结果 **32 passed in 0.40s**。

额外用 `GuidanceService.node_value` 做纯 agent/混合来源探针，确认统一“用户引导”文案与混合标记行为。上述测试覆盖现有机制，不代表新通信方案已实现或已通过端到端验证。本次未调用真实模型，未发送跨会话消息，未运行全量测试。

核心建议来自当前仓库源码与本地验证，不依赖外部产品能力或协议版本假设。

## 进一步细化：功能合同

以下是待实现的功能要求，不表示已有功能。首版目标为现有本地内置主 Agent 的完整双向通信；side-agent、外部 Agent 和远程 Agent 分别声明支持范围。

### F01 对象识别与发现（首版）

- 地址包含稳定 chat/session ID、agent ID、session generation。generation 表示一次会话内容生命周期，用于隔离清空前后的输入。
- 区分普通对话、`kind=side-agent` 的独立侧边会话、subagent manager 中的子 Agent、外部后端 session ID。不能把这些 ID 混用。
- 列表支持 project、关键词、状态过滤与分页；返回名称、精确 ID、运行状态、通信开关和能力，不隐式读取整个对话。
- 模型输入目标有歧义时返回候选，不按同名标题猜测收件人。列举与实际发送都校验范围，防止列表以后权限变化。
- side-agent 实际有独立 chat ID 和 parentChatId，但父子关系不自动代表通信许可；支持它时需单列验收。

### F02 建立、修改与撤销协作范围（首版）

- 用户可以选定一组现有会话，授权它们互发；范围内持续通信，不要求每条消息重新确认。
- 每会话具有接收开关、自动唤醒开关；区分“允许收到消息”和“允许收到后自动花费模型资源”。
- 支持撤销协作关系。发送时与消费前均检查授权；撤销后尚未消费的消息按明确策略终止或挂起，不能继续自动唤醒。
- 停止当前 run、暂停自动接收、撤销关系是不同操作；停止不删除历史，也不应被下一条排队来信立即抵消。

### F03 发送与内容（首版文本）

- 普通信息 message、需要结果的 request、关联回复 reply 三种语义；request 带问题或任务说明与可选到期时间。
- 返回全局 message ID、协作 thread ID、接纳状态；不返回伪造的“目标已完成”。
- 消息正文做大小、空内容、编码等校验；失败返回明确原因。peer 文本中的 `/clear` 等命令字符串按普通内容处理，不能误走用户 Slash Command 入口。
- 第一版不自动附上发送者全部历史、系统提示词、模型推理或 workspace。必要上下文由发送者显式摘要。
- 文件/产物引用为后续功能：使用受权资源 ID、版本或内容摘要、可访问性检查；不能假设双方可读同一个绝对路径。

### F04 运行状态路由（首版）

| 目标状态/条件 | 行为 |
| --- | --- |
| idle 且允许自动唤醒 | 持久接纳后，以目标自己的配置恢复上下文并启动一个 run |
| idle 且关闭自动唤醒 | 保留待处理；用户手动处理或下一次正常运行时消费 |
| 模型生成中 | 普通 peer 消息排队，下一安全边界消费，不默认取消输出 |
| 工具执行中 | 不撤回已执行副作用，在允许的工具批次边界消费 |
| finishing/刚结束 | 重新判定可接入 run，以同 message ID 注入或启动后续 run |
| awaiting_user/权限等待 | 只存信，不当作用户回答，也不取消待答问题 |
| 用户显式停止 | 暂停该协作链自动唤醒，需用户恢复；其他队列记录保留 |
| 有未完成的持久 checkpoint | 先遵循恢复合同，不并发创建第二个 run，不静默取消旧 run |
| 目标删除或 generation 失效 | 终止投递并返回可查询原因，不创建替代会话 |
| 后端缺少所需能力 | 明确 unsupported，或按已声明能力排队到 idle，不能假装已经处理 |

需要逐事件 envelope 和独立接收策略。仅把新消息添加到现有高优先级 guidance 队列不满足上述行为。

### F05 回复、请求结果与等待（首版）

- B 通过原 message ID 回复 A，服务端解析回信地址；支持多条补充回复并保留顺序。
- request 的处理状态与投递状态分开：可表示 pending、in_progress、needs_user、completed、failed、cancelled；completed 需要显式结果提交。
- 普通消息不强制回复；普通 run 的最终文本不自动完成全部 request；拒绝/无法处理可以形成有原因的请求结果。
- 等待采用事件订阅或有期限的可恢复等待，不让发送工具一直占用运行锁。A 等待 B 时，仍允许 A 接收 B 发来的澄清问题。
- 来自多个 session 的回复可以交错，按 request/thread ID 关联；不能只用“上一个发信人”决定回复对象。
- 超时记录为请求等待超时，不等于对方执行已被取消。迟到结果仍可查询，并标为迟到；默认不为已经取消的链重新唤醒。

### F06 状态、可靠恢复与撤销（首版）

- UI 已读和 Agent consumed 分开；内部 ACK 不触发模型，不形成自动回信。
- 支持接纳、排队原因、目标持久接收、上下文应用、等待回复、失败/过期等可查询状态。投递和请求处理使用两个字段，避免一个状态机混淆含义。
- 同 key 重试不新增有效输入；消息写入与去重记录同事务；目标上下文以稳定 ID 去重，重启后检查目标证据再推进确认。
- worker 有限重试、退避、租约超时与失败队列；明确可重试错误和永久拒绝，禁止无限自动重试。
- 发送者可取消尚未投递的消息。已进入目标上下文的内容不可“撤回未发生”，只能发送更正/取消请求；这不代表回滚工具副作用，也不能停止目标无关的整个 run。
- 权限撤销、消息取消与 worker claim 并发时需事务化检查；界面要展示是否已经越过可取消边界。

### F07 上下文、权限和记忆（首版）

- 模型上下文明确标注“另一个 Agent 提供的信息”，保留发信 session、message ID、request ID、时间；文本不能成为用户授权证据。
- 混合用户指导与 peer 消息时分别持久化来源；压缩后的摘要保留来源类别和未完成 request ID。
- 不自动将 peer 信息广播给子 Agent；若转发，保留原来源和有界通信授权。
- 接收者用自身 workspace/model/tool 权限运行；通信许可不自动授予执行任意内容的许可。
- `ConversationTurnCommit`、archive 和 memory learning 全链路保留 agent 来源。已有 archive 会以 User 标签呈现 user_message，memory 会提取用户陈述证据，必须增加类型处理。
- 首版可保留协作记录，但排除 peer 文本作为用户画像/偏好证据；项目事实若允许写入，需保留来源并走现有证据验证规则。

### F08 会话变更（首版）

- 重命名只改变展示，不改变地址；历史卡保留发送时标题，跳转使用稳定 ID。
- 清空会话时提升 generation，并使旧 generation 的待投递和唤醒失效，避免清空后旧信重建上下文。运行 worker 的并发确认也必须校验 generation。
- 删除会话清理其可执行队列、授权与资源；另一端历史展示删除状态，不保存不必要的孤立正文副本。明确通信正文随会话删除的存储策略。
- fork 生成新地址；可保留历史通信的只读展示，但不复制未完成投递、自动回复订阅或通信权限。旧请求回复仍回原会话。
- 切换 project/workspace 时重验通信范围；切换模型/后端时重新检查接收和回复能力。
- 应用退出/重启、插件停用/恢复与升级迁移均有明确处理；UI 面板是否打开不影响持久投递。
- 归档状态若后续引入/暴露，必须约定其自动唤醒行为，不假设现有普通 chat 已实现归档。

### F09 防循环、资源与共享文件（首版基础限制）

- 按协作链限制消息数、自动唤醒次数、TTL；收件箱设置数量与字节上限、每轮上下文消费限额，避免占满目标上下文。
- 高频进度可以合批消费，但原始消息 ID 和逐条来源不丢；请求和回复不默默合并或丢弃。
- 全局及每会话有运行并发上限和公平队列，用户输入优先；不能让一个高频发送者长期阻塞其他会话。
- 记录消息关联的 run 和 token/费用。混合 run 的精确按消息费用归因通常不可得，应标记估算；硬费用上限依赖可用的使用数据与预算执行机制。
- 多会话可能共享 workspace。通信不提供文件写隔离；首版协作指令需明确文件/任务负责范围。自动工作树或资源锁属于后续能力。

### F10 界面与诊断（首版基础界面）

- 目标选择器、来源消息卡、回复链、跳转原会话、待处理计数、失败原因、手动重试/处理、协作启停。
- Agent 来信不得渲染成普通用户消息；内部 receipt 不刷聊天。通知策略区分重要结果、失败、需要用户操作与普通进度。
- 页面重连通过持久游标补齐状态；发送端与接收端基于同一 message ID 展示，避免各自生成无关联记录。
- 诊断记录 correlation IDs、状态转换、attempt、排队原因和延迟；常规日志不复制完整敏感正文。
- 中英文、键盘访问和导出均保留消息来源；信箱计数不能只依赖当前打开页面的内存状态。

### F11 外部 Agent 接入（后续独立阶段）

当前 `AgentConnection` 定义 prompt/steer/load/cancel 等接口，capabilities 对 unknown 保守处理；ACP 创建/加载会话的调用中 `mcpServers` 目前传空数组。因此“可以向外部 Agent 发送 prompt”不能证明它可以发现并调用 Cyrene 回信工具。

- 必须分别探测 receive_when_idle、receive_while_running、reply_tool、restore、preserve_origin 能力。
- 不支持 steer 时可按协议排队到下一 turn，但 UI 需显示等待原因。不能通过 cancel+prompt 假装实现无损运行中消息。
- 为回信提供受限的工具桥/MCP 或明确的后端适配，不从模型自由文本猜测目标和完成状态。
- 不支持可靠来源隔离或回信的后端只声明有限支持；第一版 UI 不展示为完整双向协作对象。

### 首版验收示例

用户选择 A“实现接口”和 B“检查兼容性”建立协作。A 发 request，得到持久 ID 后继续工作；B 空闲时被唤醒，或运行到安全边界时看到带来源的问题。B 可反问，A 在等待期间仍能接收并回答。B 显式提交关联结果，A 收到后继续整合。

中途重启不丢消息、不重复应用上下文；B 等待权限时消息排队；用户停止该链后新回复不立即唤醒。清空 B 后旧消息不能污染新上下文。所有通信都有可见来源和状态，用户可查询失败原因与停止协作。这一流程通过后，才能称为完整的 session Agent 通信。
