# 审查清单完成记录

本记录接续 `full-project-refactor-review-2026-09-07.zh-CN.md` 和第一批 `refactor-implementation-2026-09-07.zh-CN.md`。对照用户确认的八个方向，已完成其建议的职责拆分与检查增强。完成指下表中的具体范围；保留既有锁、协议、线程和事务边界是设计约束。

| 清单项 | 最终落地 | 状态 |
| --- | --- | --- |
| 1 模型配置与设置 | 连接、profile、route 规范化分离；旧密钥、清除、迁移、校验顺序保留；快捷键规范化独立 | 完成于第一批 |
| 2 Send 准备 | `SendInput` 明确请求输入；命令解析返回完整输入；`PreparedSendEnvironment` 明确环境；`PreparedUserTurn` 包含历史/新消息输入、用户消息、时间、标题决定、重试截断及被替换消息 ID。普通发送与重试分别选择来源，后续从完成结果读取 | 本批补齐 |
| 3 恢复逻辑 | 决策与状态/存储/排队执行分离，惰性查询与 reflection 查询顺序保持 | 完成于第一批 |
| 4 消息投影与提交 | ContextTree 活动卡 owner；Send、Answer、外部回复、后台唤醒、频道回复共享用量/身份/生成指标字段投影。HTTP 正值筛选、后台零值省略、频道零值保留仍各自执行；pending、计数、通知、重试和原子提交不合并 | 本批补齐 |
| 5 Rail / Topbar | Rail 集合资源生命周期独立；Topbar 预览及菜单 owner 负责状态、定位、资源响应序号、截图轮询、键盘/滚动/缩放、浏览器遮挡和计时器清理。拖动与排序仍保留原实现 | 本批补齐 |
| 6 聊天页 / Composer | `floating-pane-handoff.jsx` 集中快照、移交、恢复、切换后放弃事务；保留宽度、聊天快照身份及同步提交。`composer-submission.jsx` 管理命令解析、可用上下文激活与载荷构造；输入组件仍在原位置执行 guidance、乐观清空和失败恢复 | 本批补齐 |
| 7 供应商适配 | Codex 输出累计与 action 结果转换分离；MiniMax H3/v1 请求构造分离；超时、取消、轮询和已落盘 api_version 兼容边界保持 | 完成于第一批 |
| 8 终端 / Electron | scrollback、历史 SQL 查询/投影、screen 解析及缓存各有 owner；仍复用原主/查询线程、条件锁、队列及优先级。Electron 视频全屏生命周期独立，目录整理及开发/打包/CI 路径同步完成 | 本批补齐 |

## 检查与测试清理

- Python 和 JavaScript 默认阈值没有提高；原有预算随源代码身份迁移后收紧。抽取出的 Topbar 生命周期、面板移交预算明确记录来源，未按新文件重置。
- 新增 Python ownership 报告，按稳定领域聚合显式状态写入、集合变异与资源创建/释放调用候选，列出所在函数和行号。不能静态确认的接收者标为 unresolved；报告不把调用数差额等同于资源泄漏，也不推断框架或 context manager 隐式清理。
- CI 在复杂度检查中生成报告，并上传 artifact 供 review。可本地执行：`uv run python build/python_complexity.py --ownership-report /tmp/cyrene-python-ownership.json`。
- 菜单遮挡的源码位置断言改为实际验证打开后的遮挡计数、关闭/卸载归零；Send 测试使用新输入/准备结果合同；终端性能测试在真实执行 owner 上继续记录线程、阻塞与公平调度。未删除恢复、竞争、提交原子性、旧数据兼容或性能行为测试。

## 自审重点

1. Send 验证/副作用顺序保留：解析及预算 → 加载会话与命令 → 环境/模型 → 历史重试或追加消息 → 持久化 → 工作流 → 附件/标题任务 → 执行。重试保留原动态命令继承兼容行为。
2. 消息投影只合并确实相同的字段构造，明确保留零值与负值、可选属性、模型身份复制的入口差异；未修改 commit/outbox/锁。
3. 菜单请求序号、1200ms 截图轮询、140ms 关闭动画、监听器捕获参数保留；新 owner 不向每个 tab 添加订阅。
4. 面板保留 source/active 两份分屏快照、首次事务快照、临时内容归属、宽度交换和恢复；渲染 DOM key、窗口 webContents/partition 未修改。
5. 终端查询在原工作线程执行，屏幕解析在原调度器中执行。测试用的延迟历史读取仍通过动态回调进入原 writer 边界；没有新增锁或线程。

## 验证结果

- Python 全量：**2777 通过、4 失败、2 跳过**，175.51 秒。4 项均为旧内部字段/源码位置假设；修正后相关三个测试模块 **53 全部通过**。未重复跑全量。
- WebUI：**126 全部通过**；最终补强菜单遮挡行为断言后相关 **4 全部通过**。
- Electron + 根目录浏览器输入：**138 全部通过**。
- Send 重构前后 **96 组**输入、历史、重试标记及聊天记录变更对照完全一致。第一批另有模型配置 2000 组、MiniMax 4800 组对照。
- Ruff、Python 编译、两种语言复杂度检查、架构测试、WebUI 构建和 `git diff --check` 均通过；WebUI 生成资源已更新。
- 使用隔离数据目录完成真实 macOS Electron 冒烟：`DESKTOP_SMOKE_TEST=ok nonWhitePixels=333117 checks=desktop_settings_cas,shortcut_settings_sync`。首次启动被正在运行的另一实例占用 Office 4243 端口阻止；仅给测试实例设置 `CYRENE_OFFICE_PORT=44243` 后通过，没有修改运行代码或停止用户实例。
- 已关闭本任务的测试 Electron 与隔离 Terminal Daemon。

当前验证范围内未发现功能回归。仍未在本机实际验收 Windows/Linux 原生安装包或所有外部服务，不能承诺任意平台/配置都经过绝对证明。性能相关终端回归保持通过；本次没有重新做所有场景的端到端性能基准。

此记录写成时改动仍在本地。后续已提交并推送 `d7abbe59`，本轮 GitHub CI 成功；真实服务、性能对照和独立复审结果见 [交付验证补充记录](refactor-release-validation-2026-09-07.zh-CN.md)。

关键指标相对 `212dcfaa`：Send operation 字段 **43 → 29**、`_load_chat` 决策 **64 → 32**；终端 writer 方法 **43 → 34**、字段 **36 → 28**。这些是职责和隐式依赖的变化，不代表业务状态或功能被删除。详细指标及预算迁移见 `refactor-completion-metrics-2026-09-07.json`。
