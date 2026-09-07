# Cyrene 复杂度重构与验收记录

日期：2026-09-07。比较起点：`30835bb6`（0.9.0-beta13）。所有修改保留在工作区，未提交、合并或发布。

前期研究列出的实施切片已逐项完成，并整合了共享工作区中更新、代理设置和面板缩放的新增代码。Python 和 JavaScript 全局复杂度门禁均已通过，没有提高已有预算。功能保持由代码等价性检查、行为回归和隔离桌面 smoke 支持；不将这些证据扩大为所有平台和性能的绝对保证。

## 1. 聊天状态与布局

### 回答和 guidance

`answer-projection.mjs` 集中了回答开始、列表收尾、详情确认，以及 guidance 的乐观消息、确认和失败投影。`chat-action-controller.jsx` 保留请求、错误呈现和 request sequencer 的协调。

保留的行为包括：

- 列表与详情的字段合同不混用：列表更新 `runStatus`，详情不新增原先没有的字段。
- 详情仍按需加载，不为了集中更新而加载所有 transcript。
- 回答开始只更新已经存在的详情缓存，不创建空白缓存条目。
- guidance 的 runtime 消息仍由 runtimeEngine 持有，确认时保留 optimistic ID 的替换语义。
- 切换对话后，旧请求不能覆盖当前详情；过期 hydration 不更新缓存。
- 回答成功仍先发 lifecycle、清 runtime、失效旧列表请求，再更新列表和拉取详情。
- guidance 失败遇到 `chat_not_running` 时仍使用原有延迟发送逻辑。

这次收拢的是研究中明确建议先迁移的回答/guidance 操作；没有引入第二套全局状态库或改变其他操作的数据加载策略。

### 工作区与布局

`workspace-surface-controller.jsx` 接管工作区 surface 的描述、意图订阅、动态卡片与资源观察生命周期。Hook 保持在原有调用位置，现有监听和清理策略不变。

`pane-layout-transforms.jsx` 提供纯布局操作：打开、关闭、左右移动、上下排序、交换、翻转和 source 提升。controller 继续负责 terminal API、IPC、选择切换和持久化等副作用。

保留了双层垂直面板的兄弟卡片、行比例、项目/聊天所有者、终端替换恢复和关闭后选择下一个聊天的规则。并行任务新增的 `workbench:pane-layout-change` 测量事件已保留。

### Rail / Composer / Topbar

- `rail-terminal-view.jsx` 独立承载终端卡片、终端/Agent 状态展示、拖动和菜单交互，使用明确的终端相关输入。
- `composer-attachment-view.jsx` 提取附件展示；附件、失败图片预览等状态仍属于现有附件 owner，移除和禁用行为保持不变。
- `topbar-session-view.jsx` 提取会话状态、活动预览与图标展示。
- `topbar-browser-subscription.jsx` 负责浏览器 manager 的订阅、菜单关闭和遮挡计数清理，保持原来三个 Effect 的顺序和依赖。

没有修改这些组件的 CSS、DOM 层级、快捷键、文案、动画时长或拖放规则。没有为了行数进一步把整页 context 传给新的大组件。

## 2. Electron 资源所有者

| 模块 | 拥有的资源/责任 |
|---|---|
| `backend-process.js` | Python 子进程、端口等待、重启状态、终止和超时升级 |
| `browser-sessions.js` | Browser manager 集合、活动会话、页面归属、下载记录、合并发布 timer |
| `detached-panes.js` | 独立窗口映射、browser surface 归属、拖动 session、timer、归还关系 |
| `desktop-settings-owner.js` | 桌面设置读写、revision CAS、快捷键注册/恢复/注销 |
| `browser-rpc.js` | 显式白名单分发和不同命令的参数合同 |

`main.js` 创建这些 owner 并连接窗口、后端和系统能力。保留原有 wrapper 作为 IPC 和生命周期回调的绑定边界；BrowserTabManager 本身仍负责单个会话内的标签页行为。

保留的高风险细节：

- 浏览器 session 隔离和常规浏览器共享 persistent partition 的登录语义。
- 下载的页面归属、完成清理和 100 ms 合并发布时机。
- 独立窗口延迟就绪、拖动取消、click-through 解除、归还时的 draft/meta 传输。
- 设置先持久化再尝试快捷键；注册失败时恢复旧快捷键，不丢其他设置。
- Windows 后端终止继续使用现有 taskkill 参数，不新增 `/T` 去杀独立终端进程。
- `electron/package.json` 的打包清单包含所有新增运行模块。

## 3. AgentSession

按研究建议的风险顺序提取，而非混入 mixin：

1. `context/mounts.py`：Context mount 的存储投影、去重和 contribution 合并。
2. `plugin/result_codec.py`：插件结果 JSON 转换、恢复、问题选项处理。
3. `plugin/session_attachment.py`：Hook setup 跟踪和 rollback 数据结构。
4. `plugin/session_plugins.py`：挂载集合、setup 失败、同步 token、应用服务覆盖、插件 attach/reconcile/detach，以及所提供 session driver 的所有权。
5. `plugin/permission_grants.py`：精确授权 fingerprint、一次性与会话授权集合、消费和持久化；使用原有 Session 状态锁。
6. `transition_driver.py`：queue、condition、pending key、线程、event loop、活动任务和停止协议。通过显式 callback 调用领域操作，不持有整个 AgentSession context。

Session 的 `_advance`、工具继续执行、恢复、重试、取消和模型失败逻辑仍保持原有语义。插件 owner 通过专用 setup-context 工厂取得插件公开合同，没有获得任意修改 Session 私有字段的权限。

## 4. 后台消息和回复收尾

- Schedule / Proactive 共享 `background_projection.py`：ContextTree 修复、completed chat 创建、幂等检查、原子写入及发布。
- 原插件入口保留标题、usage 和来源差异，不互相导入私有函数。
- 唤醒回复的投影、持久化和 `saved` / `awaiting_user` 事件发布进入已有 `ChatReplyFinalizationApplicationService`。
- shell、media、agent 来源仍使用各自的消息标记、轮次计数与通知策略。
- workspace finalize 的成功、等待、取消和失败分支保留原先的调用顺序；没有把不同来源塞进第二套 Run Manager。

## 5. 复杂度与依赖门禁

新增/强化：

- Python：函数长度、分支、嵌套、类方法数、可变字段数、模块长度。
- JavaScript：对应结构指标，以及 Hook 数、不同 setter 目标数、订阅数、解析恢复错误数。
- 嵌套函数、匿名函数、lambda 单独计数，不能通过藏进闭包绕过分支检查。
- 类字段去重计数，避免把大类拆成很多短方法就视为达标。
- Workbench 领域和单个 builtin plugin 的私有依赖/跨插件依赖检查；解析相对导入，也检查函数和条件内部的导入。
- CI 显式运行 Python / JavaScript 复杂度检查。
- `--initialize` 不覆盖已有基线；`--ratchet` 必须先通过检查，只能收紧。
- 移除了旧的未真正执行门禁的 Python 基线更新脚本，以及重复的旧 JavaScript 长度基线。

基线迁移不是重新接受当前树：移动的方法继承原方法预算，匿名函数更名核对原有源码；新增指标以 HEAD 初始化。迁移清单见 `complexity-budget-relocations-2026-09-07.json`。现有大型声明和历史复杂函数仍有明确预算，后续不能增长。

## 6. 测量结果

下表是结构变化，不把迁移出去的行数当成删除的业务逻辑。

| 对象 | 重构前 | 当前 |
|---|---:|---:|
| `electron/main.js` 模块行数 | 8037 | 6911 |
| `core/session.py` 模块行数 | 5601 | 4824 |
| AgentSession 方法数 | 133 | 116 |
| AgentSession 可变字段数 | 67 | 49 |
| `chat_service.py` 模块行数 | 1059 | 983 |
| `dispatch_shell_wake_run` 行数 | 395 | 316 |
| WbcRail 函数行数 | 2794 | 2654 |
| WbcComposer 函数行数 | 1622 | 1594 |
| WorkbenchTopbar 函数行数 | 1740 | 1697 |

不少原有分支是业务规则，提取后仍存在；这些指标没有被包装成“复杂度全部消失”或性能收益百分比。

## 7. Review 与验证

### 等价性 review

- 三个新 Electron owner 的原有方法：还原依赖引用后，与 HEAD 的 token 序列比较一致，忽略注释和空白。
- AgentSession：还原迁移的字段路径后，未拆分方法与 HEAD 的 AST 一致；有意改变的初始化、代理入口、关闭协议逐项 review。
- 保留了消息合并顺序、lazy hydration、ContextTree 幂等语义、下载和快捷键清理、平台进程终止参数。

测试发现并修复了真实提取错误：设置 owner 的展开表达式遗漏依赖引用；transition 提取误改部分失败分支的 `await`。修复后复验相关行为，不能只凭“机械搬移”假定安全。

### 测试结果

- 完整 Python 最终回归：2,722 passed、1 failed、2 skipped（153.10 秒）。唯一失败是新修正的分栏测试使用了星号导入，被架构门禁拦截；改为显式导入后，架构与复杂度集合 20 项复验通过，分栏 4 项复验通过。修正仅涉及测试导入，没有修改产品逻辑；未再次重复整个测试套件。
- 最后一轮整合检查 546 passed、10 个源码加载失败、2 skipped；修正翻译依赖加载后，整个前端相关 Python 文件 351 passed。
- 前端 Node 最终完整集合：88 passed、0 failed。
- Electron 最终完整集合：125 passed、0 failed。
- Python 架构与指标检查测试：20 passed；两种语言的全局复杂度门禁通过。
- Ruff、Python compileall、前端 build、最终 wheel build 通过。
- 最终隔离 macOS Electron smoke：`DESKTOP_SMOKE_TEST=ok nonWhitePixels=2048862 checks=desktop_settings_cas,shortcut_settings_sync`。

前期完整回归发现过源码位置断言和模块模拟加载问题，均已修正测试入口并保留原行为断言。Node 分栏回归验证了真实渲染与几何辅助函数，未把新提取的计算替换成无行为 stub。

测试遵循先完成一轮修改和 review、再统一回归；之后只为真实失败或新修改做针对性复验。使用隔离用户数据和空闲 Office 端口，清理测试启动的终端 daemon，没有终止用户原有应用。

没有删除行为测试。移除的是重复/失效的旧检查路径和不表达行为的格式断言。新测试覆盖权限精确匹配、任务去重/关闭、下载归属和清理、快捷键回滚、窗格归还、guidance 和 hydration 的过期保护。

## 8. 最终整合与验证边界

为保证整个当前工作区通过门禁，进一步完成：

- Windows 更新脚本模板提取到 `platform/update_scripts.py`。便携版、安装版两种脚本的重构前后渲染结果逐字节一致，平台判断、参数计算和执行顺序仍在 updater 中。
- 代理设置收拢到 `agent-proxy-settings.jsx`，拥有配置加载、状态与保存；保留包含更新代理在内的全部开关，以及原有 DOM 和条件展示。
- Doctor 中英文翻译提取为共享领域目录，原键、值和对象展开位置不变；更新源码测试的模块加载顺序。
- Transcript 缩放由单一 owner 管理冻结行、恢复帧、定时器和成对监听器；保留每帧 32 行恢复、滚动锚点、拖动取消、分栏切换时高度接续与布局读写顺序。
- 主对话和分栏的缩放 Hook 保持原调用位置与依赖，几何计算独立为纯函数。分栏测试继续验证高频移动只安排一帧、不反复读取布局、结束时只提交一次。
- About 格式化函数与右栏事件/目标解析函数提取，不修改展示结果或交互合同。

最终复杂度检查：Python 9,720 个作用域、JavaScript 11,163 个作用域，均无预算增长。门禁保留历史预算，后续只能收紧；通过不代表现有所有大函数已变为小函数。

未执行 Windows/Linux 的真实安装包 smoke，也未做覆盖所有交互的人工操作和端到端性能基准。因此，本次结果支持已执行路径的行为保持，**不支持“所有功能、性能和平台绝对正常”的承诺**。

## 后续验收

2026-09-07 已补充真实 Electron 聊天、问答、guidance/取消、独立窗格、代理设置，以及启动/内存/长对话缩放和聊天管线对照。详见 [交互与性能验收](complexity-acceptance-2026-09-07.zh-CN.md)。测得的部分并发场景存在耗时上升，性能不变仍不能标为通过。

后续最终本地回归：Python 2,723 passed、2 skipped；WebUI 88 passed；Electron 125 passed；3 个浏览器原生 smoke 通过；Ruff、编译、前端构建与两种语言复杂度门禁全部通过。
