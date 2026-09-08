# 对话运行、执行回执和后处理

## 上下文挂载

ContextChange Hook 只投递 `context` transition。SessionStart / TurnStart 的上下文构建、上下文写入都由 Session transition 执行器拥有。
必需上下文构建失败时生成失败结果，不能把 `trigger_model` 直接放行为 true；缺失权限策略或系统指令时不调用模型。新的用户请求可以再次尝试构建。

## 工具执行协议

1. 审核通过后、handler 执行前，在 ContextStore 原子登记 `(assistant_node_id, call_id)` 执行栅栏。
2. 登记失败时不执行工具；已有栅栏且没有最终结果时，返回 `tool_execution_unknown`，禁止自动重放。
3. handler 返回后，执行结果与结果持久化是两个独立结果。持久化失败返回 `tool_result_not_persisted`，保留执行成功与否及可序列化的原始输出，不让回执异常中断其他并行调用。
4. 恢复优先使用已挂载的 tool_results，其次使用执行回执。回执随 assistant 节点保留，节点删除时级联清理，不在批次完成路径上同步删除。

这提供的是安全的“不盲目重放”，不是对任意外部系统的 exactly-once 保证。进程可能在登记后、执行前退出，也可能在外部操作成功后、结果落盘前退出；这两种情况都必须标记为结果未知。解除未知状态需要核对外部状态，或由工具提供幂等键/事务协议。整个存储不可用时，不能保证继续持久化对话，但已登记的操作不能因此被自动再执行。

## 最终答案与 SessionEnd

无工具调用的模型答案写入时设置 `answer_complete`；后来被用户引导取代的答案会标记为 intermediate 并撤销该标记。Workbench 的完成判断不再依赖 SessionEnd 全部成功。

SessionEnd 每个 Hook 使用独立 started / completed / failed 回执。恢复时不重复执行 completed 的 Hook，也不自动重放 started/failed 的 Hook；后两者需要核对其外部副作用。尚未开始的 Hook 可以继续执行。最终完成标记写入失败后，可在恢复时利用已有 Hook 回执补写，而不重跑 Hook。

SessionEnd 故障发出 `session.postprocess_pending` 事件，不生成覆盖答案的通用错误节点。`session_end_complete` 只描述后处理完成状态，不描述答案是否有效。

## 运行所有权

ConversationRuntime 持有独立 operation task，operation 拥有整个 bridge 生命周期和会话锁。调用方只是 shielded waiter；取消等待不会关闭 bridge。显式用户取消仍通过 Session 取消协议执行。进程关闭须等待 runtime.shutdown；事件循环或进程被强制结束不能保证后台运行继续。

新 run ID 的冲突检查在同一会话锁内、打开 bridge 之前读取持久化 checkpoint。不同 ID 不能隐式取消可恢复的运行；调用方应恢复原 ID，或先明确取消旧运行。

## 工作线程、超时和实时发布

Hook 工作线程异常退出时，在同一队列锁内退出所有权并取出未处理请求，对活动请求及排队 Future 明确返回异常。新请求可以启动新工作线程，不会继承永远不结束的屏障。

同步工具始终在独立线程执行，包括没有配置超时的工具。等待超时返回 `plugin_execution_unknown`，不可重试；调用方取消也不宣称线程已终止。进程内跟踪尚未结束的线程，阻止同一工作目录、同名插件在放弃等待后再次执行；即使线程随后结束，已经返回的未知结果也不自动改写为成功。异步工具和能自行确认进程已终止的 Bash 保留各自的超时协议。

权限边界计算、批量审核、执行前复核中的局部 CancelledError 都会转换为拒绝工具调用的结果。父任务真正被取消时继续传播取消，不绕过审核。

实时事件和 bridge 统计事件使用独立投递队列：每个队列最多 256 个未完成投递，关闭最多等待 2 秒，之后取消未完成的等待，不等待发布端确认取消。同步发布端使用后台守护线程，进程内最多 32 个同步发布线程；同步回调应可从后台线程调用，需要事件循环的发布端应提供异步回调。拥塞、失败或超时通过日志报告，丢失的实时展示由持久化对话状态恢复，不改变业务成功结果。此队列不是业务写入 outbox，不能用于要求必达的外部操作。
