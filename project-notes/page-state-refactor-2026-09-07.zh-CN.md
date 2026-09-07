# 聊天页状态归属重构（2026-09-07）

## 本轮改动

从 WorkbenchChatPage 拆出 page-state.jsx 的三个独立 Hook：

- useWbcChatProjections：会话列表、当前详情、列表引用及归属项目、缓存同步。保持对象引用，不新增复制、排序、网络请求或订阅。数据变化才触发缓存同步，原有项目归属条件和 onChatsChange 通知规则保持原样。
- useWbcDraftAgentBinding：待发送 Agent 绑定、存储和插件禁用时清理。项目加载时的恢复、首次发送后的状态消费仍由原调用点触发，避免改变项目加载与发送时序。
- useWbcSplitSide：分栏方向偏好的读取、切换、拖动设置和写入失败容错。重复指定同方向不重复写存储。

会话选择的同步 ref 更新、请求序列检查、终端与面板恢复、运行时订阅均保留原逻辑。缓存同步 effect 现在在其状态 Hook 内注册，早于终端初始化 effect；两者没有数据依赖，缓存同步仍先于项目加载和会话 hydration。没有删除产品功能或测试。

## 复杂度检查

WorkbenchChatPage：函数行数 3370 → 3304；直接 Hook 调用 137 → 129；直接决策点 192 → 190。这是职责和状态归属的缩减，不代表总 React Hook 数或所有业务分支消失。

新增三个 Hook 均满足默认复杂度预算。只收紧 page.jsx 的预算；匿名作用域因迁移发生编号变化，通过原函数源码完全一致匹配，记录见 page-budget-relocations-2026-09-07.json。没有提高任何预算。

## 验证及 review

完成所有源代码修改并自查后集中运行：

- npm test：106 项通过（含测试辅助模块加载项）；新增四项状态行为测试覆盖跨项目缓存隔离、旧数据、混合归属列表、Agent 消费与禁用、分栏存储及异常。
- uv run pytest -q tests/test_workbench_frontend_logic.py tests/test_webui_consolidation_contract.py tests/test_architecture_boundaries.py tests/test_status_api.py：380 项通过。
- JavaScript 复杂度：11178 个作用域通过，无预算增长。
- WebUI 构建和 git diff --check 通过。
- 实际后端配合 Playwright：会话 A/B 切换后草稿分别恢复；组合刷新不改变选中会话；整页刷新并重新选择会话后草稿恢复；最终场景没有 pageerror。

浏览器脚本最初在切换完成前写入旧输入框，加入目标会话面板就绪条件后通过。另一个最初假设是普通聊天在整页刷新后自动选回上一会话；检查确认 wbcLastChatByProject 原本是内存对象，持久化面板仅保留包含终端的布局，因此改为检验重新选择后的草稿恢复，没有修改产品行为。Agent 插件状态和分栏偏好本轮通过 Hook 行为测试，未宣称真实浏览器覆盖所有 Agent/分屏流程。

## 边界

本轮没有新增运行时扫描或请求，没有进行新的端到端性能基准，不能据此承诺所有环境下绝对零性能波动。前轮 ABBA 性能结论仍见 composer-refresh-refactor-2026-09-07.zh-CN.md；旧性能差异尚不能可靠归因到变更代码。

聊天页仍有 129 个直接 Hook 和大量面板状态，后续还需要按持久化布局、拖动生命周期、加载/选择状态等具体职责逐步处理。不能把本轮说成整个项目的全方位重构已经全部完成。

改动尚未提交或推送。并行工作留下的 shell.css 改动被保留，未计入本轮重构成果。
