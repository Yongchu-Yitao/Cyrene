# 运行稳定性：所有权与协议边界

## 会话退出

HTTP 调用方仍然只是 shielded waiter。ConversationRuntime operation 持有会话锁，拥有 bridge 从打开、运行到关闭的完整生命周期；关闭应用由这个所有者停止执行，不通过用户取消协议写入取消节点。

退出分为停止接收新会话、宽限期内完成回复与提交、停止剩余 operation、完成清理。ChatRunManager 在停止外层 waiter 前先停止 ConversationRuntime。未完成 ContextTree 继续由原有启动恢复逻辑处理，等待确认的 outbox 事件保留原状态，收到确认后才算提交成功。

bridge 的打开、关闭运行在工作线程，取消 asyncio 等待无法停止线程。因此 operation 必须收回打开结果、完成关闭，再释放会话锁。清理超过期限时抛出 TaskShutdownTimeout 并携带仍存活的任务；资源所有者保留任务和资源，不能将超时视为成功。取消只请求一次，避免第二次取消打断正在执行的 finally。

Python 无法安全强制终止任意吞掉取消的协程或阻塞线程。这种执行器违反退出协议时，系统明确报告未完成清理，不声称已安全停止。

## ACP 请求

请求 ID、Future、写入和响应等待属于同一个请求生命周期。请求期限覆盖写入与响应；长期 prompt 可以不设响应期限，但 stdin 写入始终有独立期限。成功、异常、超时和取消都清理 pending Future。

写入中断时，命令可能已经送达。此时停用连接，将结果标为未知，并关闭自动重试。连接仍由既有进程管理器执行退出和回收。

## ACP 事件

输出 delta 与权限请求都属于不可静默丢弃的协议数据。事件队列有容量上限；达到上限后明确失败当前连接及等待中的请求，不使用“丢最旧事件后继续成功”的策略，也不通过阻塞 stdout reader 妨碍 JSON-RPC 响应处理。

session/load 历史回放过滤只移除 notification，保留需要 Cyrene 响应的 peer request。队列已满时，用 `event_overflow` 结束本次等待。调用方据此报告回复中断或权限请求丢失。
