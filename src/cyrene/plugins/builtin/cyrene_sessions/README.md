# 对话通信 / Session communication

这是可编辑、可停用的内置插件包，沿用 Cyrene 的插件发现、工具箱、中英文元数据、应用服务与生命周期机制。没有另建 Agent Runtime。

## 工具

- `list_sessions()`：无参数。返回正在运行且可接收消息的其他内置 Cyrene 对话（含独立侧边对话）的 `session_id` 和 `title`，不包含自身或外部 Agent 后端。
- `send_session_message(target_session_id, content)`：发送 1–20000 字符文本，自动附带来源 ID、发送时标题和 Agent 来源标记。向空闲目标发送会唤醒它；回复调用相同工具。

发送返回 `message_id`、`target_session_id`、`status` 和 `error`。`queued` 表示已持久接纳，不表示目标已经处理或会回复。工具重放使用运行 ID 和工具调用 ID 去重。

## 运行行为

- 运行中的普通消息在模型/运行结束检查边界消费，不主动打断模型或取消已启动工具。用户指导仍保留原有打断行为。
- 等待用户回答或权限批准、有未完成的 checkpoint、运行尚未准备好时，保持排队；绝不把 Agent 来信当作用户回答。失败或取消的旧运行已结束，允许来信启动新的运行。
- 消息正文包含可用于回信的来源 ID，聊天记录另外保存 `agentOriginated` 和 `originSessionId`。来信不能提供用户授权或作为用户记忆证据，也不会自动转发为子 Agent 的用户指导。
- 队列位于应用数据目录的 `plugin_data/cyrene_sessions/messages.sqlite3`。单收件人按顺序投递，独立收件人并行；阻塞的收件人轮转到后面，不占满每批投递名额；进程重启后继续扫描，数据库租约防止多个投递器同时发送同一条消息。
- 停用插件停止 worker，已接纳未投递消息保留。重新启用遵循现有应用插件生命周期；首次启用尚未组装的应用插件可能需要重启。
- 目标删除、类型不支持或清空造成内容代次变化时不再投递。暂时失败最多尝试 12 次，消息 24 小时后过期。每个收件人最多排队 100 条，每个发送会话每分钟最多发送 20 条。
- `delivered` 以持久收件箱或 ContextTree 输入节点为依据；聊天记录和 HTTP 202 都不算接纳凭据。崩溃重试复用原消息 ID，不追加重复聊天记录。
- 接收与清空在 Workbench 内共享跨线程互斥边界，接收端在边界内核验内容代次；清空同时清理旧收件箱，防止重启恢复已清空的来信。
- `delivered` 只表示目标已接纳；不代表模型完成，也不保证已执行副作用的 exactly-once。用户取消目标运行可能使其尚未处理的输入一并取消。
- 原生 Chat 调度已有的权限、模型、工作目录与恢复机制继续适用。双方共享工作目录时，消息功能不提供文件写隔离。

## English

This editable Plugin exposes only `list_sessions()` and `send_session_message(target_session_id, content)`. Listing includes other running built-in Cyrene conversations; known idle recipients may be awakened. Replies use the same send tool. External Agent backends are not advertised as messaging-capable.

Messages are durably queued with automatic sender provenance, replay deduplication, recipient ordering, bounded retries and expiry. Peer input is consumed at a model/terminal boundary without preempting generation. Pending human questions are preserved. Disabling the Plugin stops delivery without deleting queued messages. Delivery acknowledgement is not task completion.
