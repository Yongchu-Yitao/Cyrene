# Cyrene 代码复杂度研究

研究日期：2026-09-05。基于 HEAD `2be5380a` 加当前未提交工作区；未修改产品代码。范围为本仓库，不包含相邻的 Cyrene-mobile。以下是静态分析与重点代码阅读的结论，不是性能分析或完整功能审计。

主要结论：保留现有 Core / Plugin / Workbench 方向，优先减少共享可变状态、重复投影和跨职责协调。下一轮重构应以“一个变化需要同步多少处状态”为主线。只缩短函数、增加 controller 文件，收益已经有限。

## 1. 实测现状

行数均为物理行，包含注释、空行、声明和内嵌文本，不等于有效逻辑行或圈复杂度。

| 范围 | 文件数 | 物理行数 | 口径 |
|---|---:|---:|---|
| Python | 736 | 210,975 | `src/**/*.py` |
| WebUI JavaScript | 158 | 80,693 | `frontend` 下 js/jsx/mjs，排除文件名含 `.test.` / `.spec.` 的文件 |
| Electron JavaScript | 31 | 15,796 | `electron` 下 js/jsx/mjs，排除 node_modules 和上述测试文件 |

WebUI 数字包含两份各 4,259 行的语言目录。未计入 `static/app` 构建产物、vendor 或 CSS；不能把这些文件全部当作可删除的重复代码。

复用现有复杂度采集器，当前 ≥100 行的函数：Python 180 个，基线 195 个；JavaScript 155 个，基线 154 个。嵌套函数和外层函数可能同时计入，不能相加解释为独立逻辑量。JavaScript 采集范围也包含测试、构建脚本与 Office static 源码，与上表不完全相同。

| 热点 | 实测 | 判断 |
|---|---|---|
| `electron/main.js` | 8,008 行 | 浏览器、窗口、RPC、后端进程、设置和退出流程集中 |
| `core/session.py` | 5,644 行；AgentSession 129 个方法、66 个被赋值的 self 字段 | 生命周期、权限、上下文、插件、恢复和执行共享同一对象 |
| `cyrene_code/terminal/manager.py` | 4,492 行 | 后续专项候选，本轮未深入审计 |
| `cyrene_browser/runtime.py` | 4,088 行 | 后续专项候选，当前正在修改 |
| `features/chat/page.jsx` | 文件 3,682 行；WorkbenchChatPage 3,427 行 | 页面承担状态协调与资源生命周期 |
| `features/chat/rail.jsx` | WbcRail 2,794 行 | 导航、分组、排序、交互与渲染集中 |
| `features/shell/topbar.jsx` | WorkbenchTopbar 1,740 行 | 后续组件拆分候选 |
| `features/chat/composer.jsx` | WbcComposer 1,619 行 | 后续输入与能力选择拆分候选 |
| `chat/chat_service.py` | dispatch_shell_wake_run 395 行 | 唤醒输入、运行、结果投影和收尾交织 |

WorkbenchChatPage 函数段文本中有 47 次 `useWbcState`、36 次 `useWbcRef`、48 次 `useWbcEffect` 和 1 次 `useWbcLayoutEffect` 调用。这是源码调用计数，包含嵌套代码，不是运行时 Hook 数量。

## 2. 最值得改的三处

### A. 聊天页：先收拢状态所有权，再拆组件

证据：

- `frontend/features/chat/page.jsx:250`：主页面组装大量状态、ref、事件订阅和资源协调。
- `frontend/features/chat/chat-action-controller.jsx:5`：guidance 同时修改 runtimeEngine 和 activeChat。
- 同文件 `wbcBeginAnswerRuntime` 同时维护 chats、chatCache.details、activeChat 和 runtimeEngine。
- `frontend/features/chat/pane-layout-controller.jsx:18`：controller 接收页面 context 并直接调用 setter；`wbcRestoreTerminalReplacement` 还协调布局恢复、终端选择和终端客户端。

因此，现有 controller 抽取改善了文件组织，却仍依赖页面持有和同步可变状态。这是下一步真正应减少的复杂度。

建议分三种所有权，逐条迁移现有操作：

1. 对话数据：按 chatId 维护规范化实体；列表、详情和运行投影明确各字段的权威来源。完整 transcript 可继续懒加载，避免为了统一而把全部消息加载到全局。
2. 工作区状态：selection、pane layout、surface intent、detach/return 由专门状态模块管理；移动、关闭、恢复作为明确动作处理。
3. 视图局部状态：菜单、hover、拖动动画等留在对应组件；SSE、IPC、网络与持久化由明确订阅者处理。

具体切片：先迁移“回答问题”的 optimistic update、服务端确认与回滚，再迁移 guidance；让各操作只提交一次领域更新，其余显示从投影读取。随后把纯布局变换从 terminal API / IPC 副作用中分开。利用已有 `behavior.mjs`、`dynamic-surface-broker.mjs` 和 request sequencer，不另建一个平行框架。

验收重点：切换聊天不受旧请求覆盖；等待用户状态在列表和详情一致；乐观消息确认不重复；项目面板与聊天面板隔离；detached pane 返回不丢草稿；订阅随所有者关闭。组件变短是结果，减少多处 setter 才是实质。

### B. Electron：按资源所有者拆主进程

证据：`electron/main.js:328` 起集中定义窗口、浏览器 manager、下载、拖动 session、Python 子进程、RPC server 等状态；`BrowserTabManager` 从 1397 行开始，仍调用外围模块状态。它的标签页按 session 区分，但常规浏览器身份有意共享 persistent partition，重构时必须保留这一差别。

建议逐步提取：

| 模块职责 | 自己拥有的资源 | 对外能力 |
|---|---|---|
| 后端进程管理 | Python 子进程、端口等待、重启状态 | start / restart / stop |
| 浏览器会话管理 | manager 集合、活动会话、发布 timer、下载 | dispatch / activate / close / dispose |
| 独立窗格管理 | detached windows、拖动 session、归还关系 | detach / return / close |
| 桌面设置与快捷键 | 设置读写、快捷键注册和错误 | apply / reset / dispose |

`main.js` 最终负责创建和连接这些对象。每次提取必须把状态、timer/listener 的创建与清理一起移动，依赖通过少量明确参数或回调传入。避免提取一个类后再把整个 main context 传回去。

已有 `backend-port-waiters.js`、`main-window-lifecycle.js`、`browser-popup-policy.js` 可作为切入点。第一刀建议选后端进程管理；浏览器正在增加本地文件预览，待该改动稳定后再拆它。

验收重点：只启动一个后端进程；重启与退出竞态；关闭窗口后的驻留行为；快捷键无重复注册；session 隔离；preview 清理；浏览器共享登录语义；窗口归还。涉及 Electron 打包文件移动时同步 `electron/package.json` 的 files 清单。

### C. AgentSession：保留唯一执行入口，逐步剥离职责

证据：`core/session.py:270` 的初始化覆盖插件、权限、Context Store、状态锁、事件、线程和 transition queue；同一类还包含 `_restore`、`_advance`、`_messages`、压缩、工具结果、权限与 close。

建议顺序：

1. 先提取消息投影与结果编解码等输入输出明确的逻辑，保留原执行顺序。
2. 再提取插件 session attachment / reconcile，把 attach、rollback、dispose 放在同一所有者中。
3. 再整理权限 grant 与指纹数据；保留一次性授权、取消、重试的持久化语义。
4. 最后考虑独立的 transition driver，显式拥有 queue、thread、task 和关闭协议。

恢复/取消/权限属于高风险边界，不能第一步就重写整个状态机。拆出的对象不应直接修改 AgentSession 的全部私有字段，也不要用 mixin 把同一对象拆成多个文件。

验收重点：恢复不重复已完成工具；取消与成功提交竞争时结果唯一；失败重试恢复正确分支；SessionStart/TurnStart 缓存语义；权限 grant 精确匹配；close 能回收任务和线程。现有 `test_agent_session.py` 已覆盖其中多个关键场景。

## 3. 低风险去重与中期整合

### 先做 Schedule / Proactive 投影去重

`plugins/builtin/cyrene_schedule/projection.py:24` 与 `cyrene_proactive/projection.py:30` 的 `_ensure_proactive_context` 经 AST 比较完全一致：检查 completed checkpoint，否则补写 assistant context record。两个 create-chat 流程也有重复的查重、存储和通知步骤，但并不完全相同，Proactive 还处理 usage。

第一步只把共同的上下文补写操作提到 Workbench 的公开应用服务，两个插件分别调用；保留插件各自的标题、usage、通知策略与业务决策。逐项核对后再决定是否统一 completed-turn 投影。不要让两个插件互相导入私有函数。

现有 `context_proactive_*`、run id、标记字段和幂等行为应原样保留；语义改名应作为独立数据迁移评估。验收包括已存在 chat、已完成 ContextTree、重复投递以及补写中断后的恢复。

### 再整合唤醒结果收尾

`ChatService.dispatch_shell_wake_run` 当前 395 行，内部 runner 处理上下文解析、执行、pending question、消息、usage、workspace finalize 和错误。HTTP send 路径已经使用 `ChatReplyFinalizationApplicationService`，但唤醒路径仍有自己的投影流程。

先比较 reply / awaiting / cancelled / failed 四类结果的合同，再扩大已有 finalization 服务的适用范围。保留 shell / media / agent 来源和通知差异，用明确输入数据表达；不要再创建第二套通用 Run Manager，也不要把不同输入流程强行合并成一个布满来源判断的大函数。

## 4. 改进现有复杂度护栏

已有护栏值得保留：大函数只减不增、Core 依赖限制、星号导入限制、私有导入预算、模块初始化静态环检查。当前 Python 架构测试全部通过，说明没有必要重新推翻目录结构。

但当前指标有边界：

- ≥100 行统计同时惩罚大 JSX、路由声明、CLI 参数和内嵌 HTML；不能直接表示理解难度。
- 一个 5,000 行类由很多 99 行方法组成，也能绕过大函数阈值。
- 私有跨包检查按模块名前两段划分，`cyrene.workbench` 内部领域、`cyrene.plugins` 内不同插件间的耦合不会体现在该项中；本轮测得的 2 项不能解释为全项目只有两个私有依赖。
- 静态环检查只看模块顶层直接 import，不包含函数内、条件内或动态导入，因此通过不代表所有运行期依赖无环。
- service locator 的字符串查询和 context setter 耦合也不由普通 import 图表达。

改进顺序：

1. 保留原长度阈值，新加逻辑分支与嵌套深度的报告，先观察再设阈值；将声明型组件和业务流程分别评审。
2. 对重点类/组件记录方法、可变字段、跨模块 setter 和副作用订阅者数量，避免只靠分文件达标。
3. 依赖检查逐步细化到 Workbench 领域和单个 builtin plugin；先列出实际边，再按公共服务合同分类，避免禁止合理的插件到 Host 调用。
4. 基线更新要求列明删除、缩小、改名和新增项；不能把当前全部值重新写入当作自动修复。

建议验收指标：重点操作的数据更新入口从多处收敛到一处；资源具备唯一生命周期所有者；共享代码只有一个实现；新模块不接收整个宿主 context；选定函数的分支与嵌套确实下降。总行数在引入清晰类型/接口时可以短期上升。

## 5. 执行顺序与不建议做的事

建议按独立、可回滚的小 PR 推进：

1. Schedule / Proactive 的共同上下文补写，先获得可验证的低风险去重收益。
2. Electron 后端进程管理提取，建立生命周期边界。
3. 聊天回答/guidance 的单一状态更新入口，再拆 pane 状态与副作用。
4. 聊天页稳定后拆 Rail / Composer / Topbar 的局部交互和展示。
5. AgentSession 先提纯投影，再逐步整理插件、权限、transition driver。
6. 比较现有 finalization 合同后整合唤醒路径。

每个切片完成全部修改和自审后，只运行一次相关测试选择；失败或新发现问题才扩大检查。跨平台资源提取还需相应安装包 smoke 验证。这里不提供未经实施验证的工期或百分比收益承诺。

暂不建议全量 TypeScript 迁移、React 框架替换、微服务化、全局万能状态库，或新建第二套插件/Runtime 机制。类型可以从新增公共合同开始局部补充，但类型迁移本身不会消除状态重复。

也不应仅凭行数清理迁移代码或去掉安装平台。`static/app` 由 build-jsx 生成，CI 已检查产物同步；维持 `frontend` 为源，不把生成产物当作另一套业务实现重构。

## 6. 本轮验证

只执行了一轮针对性测试：

```sh
uv run pytest tests/test_architecture_boundaries.py -q && node --test src/cyrene/workbench/webui/frontend/architecture/complexity.test.mjs
```

- Python：13 passed，13.75 秒，包含现有静态 import 无环检查。
- JavaScript：1 failed。`electron/main.js::handleBrowserRpc` 当前 104 行，新增进入 ≥100 行集合；没有其他已有大函数增长。
- 当前未提交 diff 为该分发函数增加 `openLocalFile` 分支。这是现有工作区状态的发现，本研究未改动该函数或放宽基线。后续可把普通 RPC 映射与会话/授权准备分离，保持精确白名单和特殊分支语义。
- 未执行完整功能测试、运行时性能分析或跨平台 smoke；不能由本次检查宣称产品功能全部正常。

数据来自 Python AST、仓库现有 Lezer 采集器、重点源码和现有测试；没有安装额外分析依赖，也没有更新任何复杂度基线。
