# Cyrene App 自管理控制面 Handoff

更新时间：2026-08-11

状态：当前实现说明；以工作树为准，不包含发布打包结论

## 1. 最终边界

Cyrene 主 Agent 可以操作发起当前本地轮次的 Cyrene Renderer 和非模型设置，
但不能获得任意 DOM、任意坐标、任意 IPC、任意本机 HTTP 或后台业务仓库访问权。

控制面分为两类：

1. **当前 UI Surface 操作**：`snapshot → inspect → click/double_click/type/scroll/drag`；
2. **持久设置操作**：`settings.describe → settings.read → settings.update`。

窗口状态和窗口级动作由 `cyrene.app.status`、`cyrene.app.window` 提供。
Project、Chat、Data、Update、Lifecycle 和 Cross-session Message 的类型化实现仍可供
产品内部复用，但不会进入 Agent Catalog。

模型设置明确排除。OAuth、秘密输入、二维码、系统权限和文件选择仍由用户亲自完成。

## 2. Agent 暴露面

模型只看到一个稳定、Main-only 的 `cyrene_tools` Gateway。它与其他工具包使用
相同的 `discover → describe → invoke` 协议，具体实现名不进入 Wire Schema。

| Capability | Concrete handler | 作用 |
|---|---|---|
| `cyrene.app.status` | `CyreneAppStatus` | Backend、Host、Window、Surface、Settings revision 状态 |
| `cyrene.app.window` | `CyreneWindowControl` | 当前窗口和 Quick Chat 窗口动作 |
| `cyrene.ui.snapshot` | `CyreneUISnapshot` | 当前层分页语义快照 |
| `cyrene.ui.inspect` | `CyreneUIInspect` | 指定节点状态、动作、Outcome 和子树 |
| `cyrene.ui.click` | `CyreneUIClick` | Invoke、Toggle、Open menu、Dismiss |
| `cyrene.ui.double_click` | `CyreneUIDoubleClick` | 只执行显式声明 Double press/Double click 的 Invoke |
| `cyrene.ui.type` | `CyreneUIType` | Set value、Select |
| `cyrene.ui.scroll` | `CyreneUIScroll` | 语义滚动 |
| `cyrene.ui.drag` | `CyreneUIDrag` | Move、Set frame、Adjust |
| `cyrene.settings.describe` | `CyreneSettingsDescribe` | 设置 Schema、风险和 Apply mode |
| `cyrene.settings.read` | `CyreneSettingsRead` | 公开或脱敏值 |
| `cyrene.settings.update` | `CyreneSettingsUpdate` | Revision/CAS 原子 Patch |

子 Agent 没有 `cyrene_tools`。主 Agent 也不能直接调用上述 Concrete handler 名称。

### Internal-only handler

- `CyreneSessionMessage`
- `CyreneProjectControl`
- `CyreneChatControl`
- `CyreneDataControl`
- `CyreneUpdateControl`
- `CyreneLifecycleControl`

它们由 `INTERNAL_ONLY_CONCRETE_TOOL_NAMES` 阻止进入所有 Actor 的可调用目录。

## 3. Current UI Surface 协议

### 3.1 Snapshot

`cyrene.ui.snapshot` 只读取当前绑定 Renderer 的最上层可操作界面：

- 默认 `include=[interactive,text]`；
- 支持 `cursor`、`page_size`、`max_depth`；
- `include` 会真实过滤结果；
- 节点包含 `children_page`，根响应包含分页元数据；
- 不主动聚焦、不激活窗口；
- Chat 列表只包含当前视口里的对话，不设置数量上限。

全局工具输出默认不再截断（`MAX_TOOL_OUTPUT_CHARS=0`）。旧安装曾把历史默认值
`12000` 写入加密配置；启动时会将这一非 UI 可编辑的旧默认迁移为 `0`，避免完整
结构化快照在进入模型上下文前再次被统一裁切。结构化分页仍用于按容器继续读取，
不等同于字符截断。

Revision 只跟随可操作语义变化。消息正文、流式输出及消息区通用控件的重渲染仍可
被 Snapshot/Inspect 读取，但不会使已取得的稳定节点（例如 `new_chat`）过期；新出现
的审批、问题、菜单、Overlay 或动作集合变化仍会推进 Revision。

当前 UI 的显式稳定节点位于 DOM 投影之前，包括：

- `project_switcher`
- `open_search`
- `open_settings`
- `new_chat`
- `chat_search_input`
- `chat_list`
- `chat_composer_input`
- `chat_composer_submit`
- `browser_window_titlebar`

### 3.2 Inspect

`cyrene.ui.inspect` 绑定 `snapshot_id + revision + node_id`，读取一个节点和分页子树，
不会执行动作。Renderer 为每个 Revision 保存独立的节点语义租约；即使会话输出、
列表刷新或其他组件令全局 Revision 前进，只要目标节点的 Parent、Scope、Role、Name、
Value、State 和 Actions 未改变，Inspect 仍会返回该节点的当前子树和最新 Revision。
目标节点消失、切换 UI 层、自身语义变化或租约被淘汰时才返回 `stale_snapshot`。
Agent 必须继续原样传入 Snapshot 返回的 Revision，不能根据错误中的当前 Revision 猜测重试。

Inspect 可以描述声明的下一步效果，例如打开 Menu、Overlay 或切换 Layout；它不能
伪造尚未 Render 的未来界面内容。成功读取目标节点时，Renderer 会把 Agent 光标移动
到该组件，并显示与 Gesture 相同的慢速流动描边；普通 Snapshot 不显示流光。执行动作
后必须重新 Snapshot。

### 3.3 Gesture

所有 Gesture 都绑定：

```text
snapshot_id + revision + node_id + action_id + input
```

执行前再次读取该节点。若全局 Revision 已变化，Renderer 会比较原 Revision 留存的
节点级动作租约：Node、Action、Risk、Scope 和关键状态均未变化时仍允许执行；目标
自身变化或租约已淘汰才返回 `stale_snapshot`。Agent 仍必须原样传入 Snapshot 返回的
Revision，不得猜测或改写。随后继续验证 Gesture family 与风险等级；禁止 Selector、
脚本、URL、原始 Event 和任意坐标。

| Tool | Action kind |
|---|---|
| `click` | `invoke`, `toggle`, `open_menu`, `dismiss` |
| `double_click` | `invoke`，且 Action 必须声明 `double_press` 或 `double_click` Gesture alias |
| `type` | `set_value`, `select` |
| `scroll` | `scroll` |
| `drag` | `move`, `set_frame`, `adjust` |

### 3.4 DOM 投影

通用投影只补充当前视口内的标准可见控件，并放入语义 Region/Group：

- Button、非秘密 Input、Textarea、Select、Menu item、Tab；
- Scrollable container；
- 带 `data-cyrene-context-menu=true` 的右键目标。

显式节点通过 `get_element` 或 `data-cyrene-node-id` 与投影去重。Password、File、
Hidden Input、`data-cyrene-secret`、`data-cyrene-user-ceremony` 和 Models Panel 不投影。

Revision 只在可操作语义、作用域或与动作有效性相关的稳定状态变化时增加；Draft
长度、流式文本和 Scroll position 等高频瞬态本身不会造成 Revision 抖动。

## 4. 对话、搜索和右键菜单

- `new_chat` 和 `chat_search_input` 不依赖侧栏展开状态；
- `chat_list` 提供 Scroll、Page previous/next、Search、Clear search；
- 对话节点只为当前视口内元素生成；
- 对话详情动作在 `open_menu` 后才披露；
- Menu 层只暴露当前 Menu，关闭后恢复 Main scope；
- 项目切换先打开 `project_menu`，随后选择当前 Menu 中的显式项目节点；
- Browser PiP 标题栏在小窗状态声明 `maximize + double_press`，最大化状态声明
  `restore + double_press`；Agent 先 Snapshot/Inspect `browser_window_titlebar`，再把原样的
  Snapshot ID、Revision、Node 和 Action 交给 `cyrene.ui.double_click`。该工具直接调用
  Renderer 中已注册的 Maximize/Restore Handler，不依赖窗口焦点、指针位置或坐标。
- 普通 `invoke` 按钮若未声明 `double_press` / `double_click`，会返回
  `gesture_not_available`，不能被双击工具误操作。

## 5. Composer 发送策略

`chat_composer_input` 是显式 Textbox 节点，允许设置空 Draft 或按精确旧值清空。
它通过 `get_element` 与 DOM 投影去重。

`chat_composer_submit` 是独立显式稳定节点：

- Send / Send guidance：R2；
- Stop current run：R1；
- Disabled 时不披露 Action；
- Handler 点击当前真实 Button，不使用后台 Dispatcher；
- Send 必须由同一真实本地用户轮次明确委托，或进入普通本机确认仪式。

后台 `CyreneSessionMessage` 仍为 Internal-only。Agent 若要向其他 Task/Chat 发送，
必须在当前 UI 中切换到目标 Session、填写 Composer、再调用显式 Submit。

每次成功的 `cyrene.ui.inspect` 或 `ui.gesture.execute_current` 到达 Renderer 后，UI
Surface 会为实际命中的组件显示 3.2 秒一周的低强度流动描边，覆盖 inspect、click、
double-click、type、select、toggle、scroll、drag、菜单和所有显式 Semantic Handler；
新操作会把单个中央高亮平滑转移到新目标，完成后保留约 3.6 秒。组件可用
`get_highlight_element` 指定更符合视觉边界的容器，例如 Composer 的 Textbox 动作高亮
整个输入框，而 DOM 去重仍绑定真实 textarea。

通过语义控制面新建对话后，目标 Composer 还会显示约 4.2 秒的创建提示。活动状态按
Chat ID 短时缓存，所以新对话的 Composer 即使在创建事件之后才挂载，也能恢复剩余
动效。Rail 卡片保留更弱的同步提示。所有状态只由 Agent 的 UI Surface Action 触发，
用户自己的点击和输入不会触发；动效不改变布局或阻塞输入，并在
`prefers-reduced-motion` 下退化为静态弱描边。

## 6. 设置控制

设置 Registry 当前包含：

- 52 个 Typed scalar setting；
- 31 个复杂 Control coverage entry；
- 5 个 Namespace：`runtime`、`desktop`、`appearance`、`profile`、`shortcuts`；
- 11 个非模型 Tab 全覆盖；
- Models 明确排除。

复杂设置按 `direct`、`current_ui`、`existing_capability`、`user_ceremony` 或
`presentation_only` 分类。Soul、Voice、MCP、Channel、Remote、Integration、Data
等不能安全表达为普通 Scalar 的设置通过当前 UI 或已有专用 Capability 操作。

`settings.update` 使用 `expected_revision` 做 Compare-and-swap：

- 验证完整 Patch 后一次持久化；
- 成功后 Revision 单调加一；
- 并发冲突返回 `revision_conflict`，不会覆盖用户较新的更改；
- Shortcut map 保留未指定动作，`null` 只重置明确命名的动作；
- Agent 不能关闭 `cyrene_tools` 或修改自身 Concrete tool 开关；
- `redact_secrets` 不能由 Agent 修改。

快捷键 Registry 包含 Search、New Chat、New Task、Command Palette、Project switch、
三个 Session、Next/Previous Session、Close tab、Sidebar、Settings、Send 和 Newline，
并验证冲突与 Accelerator 格式。

## 7. 权限、委托和审计

| Risk | 含义 |
|---|---|
| R0 | 只读 |
| R1 | 当前 Surface 可逆操作 |
| R2 | 全局设置、发送、问题回答等高影响动作 |
| R3 | 删除、审批、生命周期等破坏性或敏感动作 |
| R4 | 永不暴露 |

R2/R3 的 Agent 代办必须满足：

1. 当前轮来源为绑定 Electron Surface 的 `desktop_local`；
2. Agent 可传真实用户原文的精确 `delegation_quote`；若省略，系统把当前完整本地
   用户请求交给同一个 Permission Reviewer，不做固定词匹配；
3. 权限审核 Agent 判断语义确实授权该精确动作；
4. 单动作票据只能消费一次；
5. 多动作使用完全一致的有序 `delegation_operations`，整批审核后逐项消费；
6. 参数、顺序或 Operation ID 改变都会失效；`client_request_id` 用于关联但不是
   授权硬条件，缺失时使用受信任的 Session + Round 身份；
7. Remote、System、Agent-forwarded、生成 UI 内容、Auto/Full Access 不能制造票据。

普通澄清问题续跑时，授权上下文由同一 Round 内的真实用户原始请求和后续用户澄清
组成，不包含 Assistant 的提问文本；续跑会重新创建本轮的一次性委托票据容器。
因此“原始请求要求执行，后续回答只澄清目标界面”的场景仍会进入 Permission Reviewer，
而不是因最后一句过短直接回退到真人弹窗。Self-configuration / Lifecycle 的人工
单次票据绑定 Tool、Kind、Operation 和已含 Canonical argument hash 的 Path，展示用
Reason 改写不会让相同操作在重试时再次弹窗。

审核 LLM 失败、输出不合约或语义不明确时默认拒绝。每个 Mutation 记录参数 Hash、
风险、来源、结果、Diff、Decision source 和 Delegation receipt；秘密字段脱敏。

Permission Card 使用结构化 `meta` 在 Renderer 侧生成文案：Capability/Operation
先去除 `.r2` / `.r3` 风险后缀，再走 `toolName` i18n；内部
`cyrene-setting:<fingerprint>` / `cyrene-lifecycle:<fingerprint>` 不展示给用户。
按钮标签也随当前界面语言本地化，后端保留稳定 ID 供审计。公开 Question Shape
只允许输出 `kind/tool_name/operation/path_hint/reason`；旧版本已持久化的纯文本权限卡
由 Renderer 做一次仅用于显示的兼容解析，确保升级后原始 Capability ID 也不会裸露。

## 8. Host Bridge 与窗口绑定

Renderer 以随机 `ui_instance_id` 注册到 Electron Host。Backend 只向本轮绑定的
实例发送 `ui.snapshot.current` 和 `ui.gesture.execute_current`，不依赖当前键盘焦点。

Electron Host RPC 使用单独 Token、Allowlist、Payload limit、Timeout、Connection
cleanup 和窗口归属校验。失效页面的 Socket、轮询和事件连接在 unload、导航或
断线后清理。

Window action 包括 Status、Reveal、Focus、Hide、Minimize、Maximize、Restore、
Fullscreen、Set frame 和 Quick Chat。所有请求 schema 都要求 argument-bound
`idempotency_key`；读操作不会持久化它，Mutation 会持久化结果用于精确重试。

## 9. Prompt 与渐进披露

`_MAIN_CYRENE_PROMPT` 同时进入 Main 和 Execution system prompt，但只有
`cyrene_tools` 启用时保留。Prompt 要求：

- Snapshot 后按需 Inspect；
- 只执行当前 Revision 的声明动作；
- 只有动作显式声明 `double_press` / `double_click` 时才调用专用 Double-click Tool；
- 状态变化后重新 Snapshot；
- 禁止 App Use、Selector、Script、Raw coordinate/event 和配置文件旁路；
- 后台业务 Service 为 Internal-only；
- Composer Send 是 R2 精确委托动作；
- R2/R3 批量操作使用参数绑定的有序票据。

主 Wire 只保留稳定 Gateway Schema；Capability 输入 Schema 只在 Describe 时加入
上下文。启用工具包集合变化才改变系统 Prompt 前缀和缓存键，Snapshot 内容不会
污染稳定前缀。

## 10. 关键文件

```text
src/cyrene/tooling/packs.py
src/cyrene/tooling/catalog.py
src/cyrene/tooling/gateway.py
src/cyrene/agent/prompts.py
src/cyrene/agent/auto_review.py
src/cyrene/runtime/host_bridge.py
src/cyrene/runtime/settings_service.py
src/cyrene/tool_impl/application/
src/cyrene/workbench/app_control.py
src/cyrene/workbench/app_operations.py
src/cyrene/workbench/ui_actions.py
src/cyrene/workbench/ui_surface.py
src/route/app_control.py
src/webui/frontend/platform/ui-surface.jsx
src/webui/frontend/workbench.jsx
src/webui/frontend/workbench-chat.jsx
electron/host-control.js
electron/main.js
electron/preload.js
tests/test_app_control.py
electron/ui-surface.test.js
electron/host-control.test.js
```

## 11. 验证基线

聚焦验证应至少包含：

```bash
node --test electron/ui-surface.test.js electron/host-control.test.js
uv run pytest -q \
  tests/test_app_control.py \
  tests/test_progressive_tool_packages.py \
  tests/test_tool_package_settings.py \
  tests/test_webui_consolidation_contract.py \
  tests/test_remote_settings_live_refresh.py
```

不应以 PyInstaller/Electron 打包代替直接测试。发布前仍需单独完成真实平台、视觉、
升级、Credential 和安装器 Gate。

## 12. 已知边界

- 未 Render 的未来界面只能返回声明 Outcome，不能预读真实内容；
- Canvas/WebGL/自绘控件需要显式语义 Adapter；
- Drag 是语义 Move/Adjust/Set frame，不是任意坐标拖动；
- Models、秘密和 OS Ceremony 永不进入通用投影；
- Current Surface 控制依赖 Electron Renderer 注册，CLI/Remote 轮次不能借用它；
- DOM 投影 ID 只保证当前 Renderer 生命周期内稳定；重启后必须重新 Snapshot。
