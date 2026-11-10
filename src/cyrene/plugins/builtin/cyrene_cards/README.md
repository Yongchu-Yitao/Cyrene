# Cards / 卡片工具包

`cyrene_cards` 使用当前 `PluginPack` / `PluginRuntime` 技术框架实现助理专用的卡片数据工具。

## 使用范围

六个工具使用 `model_visible="assistant"`、`main_only=True`，由
`assistant_profile.personal_assistant_config()` 加入直接工具白名单。普通聊天、委派的
`assistant-task` 会话和 subagent 不能发现、描述或执行它们。全局工具自定义不会解除
assistant audience 限制；handler 另外验证宿主 `personal_assistant=True`。

不修改核心注册表权限协议，不增加第二套 Agent 执行循环。内置插件 seed 会复制整个包，
应用宿主加载 `application_setup` 后提供 `cards` 服务。用户停用本包时遵循宿主现有的
服务和路由停用机制。

## 工具

- `card.create(template, spec, pinned=false)`：新建，宿主分配 ID。模板提供默认区块，
  `spec.blocks` 可以替换为符合 schema 的组合。创建和初始固定原子提交。
- `card.update(card_id, expected_revision, changes, in_response_to_event_id?)`：仅修改
  已有卡片，省略字段保留，提供的数组/对象整体替换。不能覆盖用户输入、来源或固定设置。
- `card.get(card_id, include_events=false, after_event_id?, limit=20)`：读取完整描述、
  用户草稿、最近提交快照及可选的事件页。
- `card.list(scope=current_task|pinned, status?, cursor?, limit=20)`：当前任务上下文的
  卡片或助理的常驻卡片摘要，默认每页 20 张。
- `card.pin(card_id, pinned, expected_placement_revision)`：固定/取消固定。
  用户显式取消固定后，Agent 不能自动重新固定；用户再次固定才能解除 override。
- `card.archive(card_id, expected_revision, reason?)`：保存只读结果；仍可读取或取消固定。

`spec`、`changes`、区块和动作的准确 schema 见 `definitions.py`。每次成功调用返回
`ok=true`。领域错误返回 `ok=false` 和稳定 `error.code`；不要仅凭工具调用完成就当作成功。
宿主执行层的 schema/权限错误仍由 Runtime 返回。

模板收敛为 **5 类原生卡片 + 1 类自定义卡片**。业务场景不再占用独立模板名：

| 模板 | 默认起点 | 可承载的内容示例 |
| --- | --- | --- |
| `content` 内容卡 | 文字 | 摘要、结论、消息草稿、实体资料、报告说明 |
| `collection` 集合卡 | 列表 | 搜索结果、任务清单、文件、日程、执行进度、地点 |
| `data` 数据卡 | 表格 | 方案对比、预算、指标、趋势、健康记录 |
| `input` 交互卡 | 表单 | 偏好、选择方案、补充条件、审批确认 |
| `map` 地图卡 | 地图 | 地点、备注、路线连线、空间选择 |
| `custom` 自定义卡 | 必填 iframe HTML | 专用编辑器、计算器、模拟器，及其周围的原生说明/表单 |

**默认起点不限制内容。** 五类原生模板都允许组合全部原生区块：文字、指标、字段、列表、
表格、表单、时间线、文件、图表、地图；无需为了添加表单而把数据卡改成另一种卡片。
区块类型、数量、顺序、可选 `title` / `description`、表格列、输入字段和底部操作由 Agent
按任务决定。`spec.layout=stack|grid` 提供纵向排列或响应式网格的展示提示，
`density=compact|comfortable` 控制密度；对话渲染器已支持这些布局提示。

例如，采购任务只需一张 `data` 卡：方案表格 + 预算指标 + 条件表单 + 确认按钮。
只需要两段说明时，用 `content` 卡放两个文字区块即可，不强制填满某个场景模板。
默认结构不含虚构业务数据，不会自动生成用户选择；输入校验与确认权限继续遵循统一协议。

```json
{
  "template": "content",
  "spec": {
    "title": "采购建议",
    "blocks": [{"block_id": "conclusion", "type": "text", "text": "先确认预算，再提交采购申请。"}]
  },
  "pinned": true
}
```

自定义卡片包含一个 embed 区块，可在前后增加原生区块，共用标题、来源、输入协议和操作：

创建和更新时，区块省略 `type` 默认按 `embed` 处理，必须且只能提供 `html` 或 `html_path` 之一；原生区块必须显式填写 `type`。已有 HTML 文件直接传 `html_path`，无需读回正文或手动转义。

`html_path` 读取 UTF-8 文件，相对路径基于助理工作区；助理所属执行任务的产物可使用绝对路径，权限边界与 `send_file` 一致。宿主保存读取时的 HTML 快照，持久化区块只包含 `html` 和明确的 `type`，不保留文件依赖。更新时重新提供路径才会重新读取；同一调用的重放返回原结果，不重复读取文件。读取失败不会创建卡片或修改已有版本。

HTML 不设字符数上限，卡片文档也不再受 256 KiB 总大小限制。用户输入事件及输入值仍保留 256 KiB 限制，其他字段的结构校验保持不变。

```json
{
  "template": "custom",
  "spec": {
    "title": "费用计算器",
    "blocks": [
      {"block_id": "intro", "type": "text", "title": "计算说明", "text": "调整人数后，再确认分摊方案。"},
      {"block_id": "calculator", "type": "embed", "html_path": "dist/calculator.html"}
    ]
  },
  "pinned": false
}
```

## 对话输出与交互

卡片在对话正文展示；用户可点击右上角固定图标 固定到右侧，也可使用 `pinned=true` 创建或通过 `card.pin` 固定。右侧复用普通分屏外壳，纵向滚动展示全部已固定卡片，按游标读取完整列表，不设置固定数量上限。切换任务和刷新后仍保留，并与对话引用同一 card_id。取消固定不会删除对话卡片。现有地图分屏保持原有行为。

成功 `card.create` 的工具结果携带 `presentation.kind=assistant_card`、`card_id` 和
`operation=card.create`。实时对话与历史重建均保留该引用，前端将其投影为独立的正文
卡片，不隐藏在工具记录折叠区。`card.update` 不追加重复卡片；可见卡片定期读取最新
版本，隐藏页面暂停刷新。载入失败显示重试，归档后输入只读。

原生区块支持文字、指标、字段、清单、表格、表单、时间线、文件条目信息、图表和地图。
地图沿用现有底图配置和坐标转换，支持备注、地点选择、路线聚焦；连线并非导航路线。
`routes` 通过 `from_place_id` / `to_place_id` 引用本区块地点，`selection` 绑定输入字段。

用户编辑生成 change 事件，点击声明的操作生成 submit/confirm 事件。事件包含内容和
输入版本，冲突后显示最新卡片并要求重新检查提交；默认值只用于显示，不能当成用户
已提交。change 仅保存草稿；submit/confirm 在保存事件的同一事务中写入待发消息，
随后通过宿主正常用户消息入口通知助理：空闲时启动一轮，运行时加入 guidance/inbox。
对话中显示一条用户消息，包含卡片标题、操作、来源任务、提交版本和本次值的固定快照。
助理回复沿用现有对话流。输入是用户消息，不标记为 Agent 来源，也不更改工具权限。

自定义 HTML 在 `sandbox="allow-scripts"` 的 iframe 内运行，不授予同源、弹窗和表单
提交权限。宿主注入 CSP 限制外部资源和网络请求。允许内联 CSS/JS、data/blob 图片。
HTML 仍以 JSON 存储，不在服务端执行。iframe 使用以下受限桥接（必须先声明
`input_schema` 和相应 `actions`）：

```js
window.cyreneCard.change({ people: 3 });
window.cyreneCard.submit("apply", { people: 3 });
window.cyreneCard.confirm("approve", { people: 3 });
window.addEventListener("cyrene-card-state", event => {
  // event.detail: card_id, revision, values, readonly
});
```

宿主验证消息来自当前 iframe，再经统一事件 API 校验输入及操作；不会开放任意工具调用。

## 存储与并发

数据在 `plugin_data/cyrene_cards/cards.sqlite3`：卡片、版本快照、用户事件、工具幂等回执与提交投递队列。
每次写入使用 SQLite 写事务，卡片、初始固定及调用回执一起提交。相同执行调用身份和
参数重放返回原回执；同身份不同参数返回 `replay_conflict`。同版本并发修改只有一个成功。

创建时从宿主推导会话、任务上下文、项目及节点来源，模型参数不能设置 owner。内容修改
和归档必须在来源 context 中执行；跨任务查看、固定和取消固定保留来源。区分描述版本、
用户输入版本和展示版本，更新卡片不会把表单默认值写成用户提交。

描述更新若使现有草稿字段或选项失效，返回 `input_conflict`。提交事件保存当时的描述与
用户值快照，后续编辑不改写历史确认。归档不由普通 turn 完成自动触发。

旧版场景模板不再出现在 `card.create` 的枚举中。已经保存的旧卡片继续读取、更新和固定，
更新时在内部解析到新的通用类别；数据库中的原模板名、卡片 ID、用户输入、历史快照和
固定设置均保留，不自动重排内容。旧名字不作为新的创建入口。

## 用户输入与上下文

应用层提供以下 UI 接入入口，owner 固定从真实个人助理会话取得，JSON 不能覆盖 actor：

- `GET /api/assistant/cards`：分页摘要；`scope=current_task` 时提供 `context_id`。
- `GET /api/assistant/cards/{card_id}`：卡片与可选事件页。
- `POST /api/assistant/cards/{card_id}/events`：change / submit / confirm 事件。
- `PUT /api/assistant/cards/{card_id}/pin`：用户固定偏好。
- `POST /api/assistant/cards/{card_id}/events/{event_id}/retry`：重试该次已保存提交的消息投递。

事件包含 `event_id`、`base_revision`、`base_input_revision`、`kind`、`values`；submit /
confirm 还需当前版本中已声明、kind 匹配的 `action_id`。change 合并字段草稿，submit /
confirm 校验完整值。重复 event ID 幂等，同 ID 改内容拒绝；过期描述/输入版本拒绝。
这些路由属于 Workbench 用户输入桥，不是模型工具，也不能替代业务工具的权限审批。

仅助理安装 `MODEL_START` Hook：当前 context 的卡片和已保存用户值在下一个模型边界
进入 reference 上下文。同内容不重复注入，切到无卡片 context 时清除旧快照。大输入按需
使用 `card.get`。其他任务的固定卡片不会自动进入当前任务正文。

## 当前实现边界

已实现六工具、六类模板、持久化、版本与幂等校验、助理隔离、上下文 Hook、对话正文
渲染及 iframe 输入桥。创建引用随工具结果持久保存，重新打开对话仍可恢复。

对话正文和右侧常驻区均已支持卡片；尚无卡片更新 SSE，可见卡片通过轮询刷新内容。
事件 `status="accepted"` 代表已保存，附带 `delivery.status=pending|sent|failed` 表示
消息投递状态；sent 只表示助理已接纳输入，不代表业务执行完成。网络重试复用同一个
event ID 和消息 ID，读取 inbox/ContextTree 的持久接纳证据避免重复发送。启动时恢复
待发提交；临时会话占用会自动重试，其他发送错误显示“重试发送”。重试发送原始快照，
不重新提交已编辑的表单。对话清空后旧队列不能投递到新一代对话。
iframe 的 submit/confirm 请求先在宿主展示提交值，需要
用户点击原生“确认提交”按钮才记为提交，不能由生成的脚本伪造用户确认。

自定义 HTML 的标准输入使用 `data-card-field="字段 ID"` 绑定 input_schema，宿主会在
加载时恢复已保存的值。自定义控件监听 `cyrene-card-state` 并读取 `event.detail.values`。
单字段、单输入的旧卡片兼容自动恢复。宿主重复发布相同值不会覆盖正在编辑的输入。
对话仅将提交呈现为可读摘要；Agent 和持久化记录仍保留完整的固定快照。

地图区块可声明最多 8 个 `groups: [{group_id, label}]`，地点和连线可引用 `group_id`。前端按分组顺序分配主题色及其深浅变体并显示图例；连线未指定分组时继承两端相同的分组，否则使用主题色。分组 ID 必须已声明。旧卡片无分组时标点、连线和列表色标统一使用主题色；不会根据说明文本推断分组。连线是地点关系示意，不代表道路规划或真实轨迹。

## 回访卡片

保留六类通用模板，通过 `spec.home_kind=overview|resume|results` 设置三个回访槽位。
每个助理每类最多一张活动卡；先 `card.list(scope="home")`、`card.get`，再
`card.update` 编辑。回访卡片允许跨任务上下文更新，普通卡片仍受来源上下文约束。
固定状态与用户输入沿用已有版本保护。home_kind 在创建后不可变更。

新增原生区块：
- `weather`：location、condition、temperature、range、advice、source、updated_at。
  所有值由助理根据实际查询填写，缺少地点或查询失败时使用文字说明，不捏造天气。
- `action_list`：items 为 item_id、label、可选 detail、action_ids；可选 collapsed。
  标题和条目数量构成折叠入口，计数来自条目；每行按钮引用 spec.actions，不重复显示在底部。
- action 可携带 target_context_id、task_id；提交时这两个字段随不可变快照交给助理，
  助理切回原任务并继续执行。输入表单仍使用 form/input_schema，复杂确认可由助理生成独立交互卡。

前端每 5 秒 GET `/api/assistant/cards/home`，直接读取持久化的完整卡片，不因
打开页面唤醒 Agent。展示另由 POST `/api/assistant/cards/home/display` 领取时段，
携带本地 timezone 与页面 client_id：每天首次打开、12:00、18:00、22:00；同一天
同一时段重启不重复，多窗口只展示一次，响应重试幂等。错过的时段不叠加显示。

后台启动 5 秒后开始，每 30 秒检查实际任务消息、任务文档和同项目任务状态；
变化触发助理整理待确认事项、进度和成果。天气按带时区 updated_at 判断，一小时
过期后自动请求新观测；查不到时保留原观测和时间，十分钟后重试。维护使用独立助理执行环境，前台运行或等待用户回答时仍可准备，不向前台发送
消息、不切换上下文，也不显示工具或处理过程。后台只获准查询天气和编辑首页草稿；
完成后统一发布，遇到前台卡片修改或提交冲突则保留前台版本并重试。失败或中断
两分钟后重试，准备过程不暴露半成品。
维护自身的消息不触发新一轮维护。

完全退出后没有后台进程，不能继续联网更新；恢复运行后补查。数据更新不改变
首页在对话中的位置，不新建卡片，也不触发新的展示；只有新展示时段移动首页。

三类槽位均可选，Agent 用 `spec.home_visible=false` 隐藏当前无用的卡片，true 恢复。
自动创建回访卡片强制 conversation，Agent 无法将回访卡片 pin；用户和 Agent 都无法固定回访内容。旧版所有回访固定引用在加载服务时移除，内容保留。
天气可指定 icon：clear、partly_cloudy、cloudy、rain、snow、storm、fog、night；
未指定时按条件文字选择图案，未知情况不假装晴天。动效尊重 prefers-reduced-motion。


首页展示使用独立布局，保留普通卡片的交互和数据协议。overview 的 spec.title 是
Agent 编写的问候主标题，spec.subtitle 是配套说明；两者都可通过 card.update 修改。
天气排列在问候右侧，隐藏展示层的来源/更新时间（底层数据保留），不显示页尾准备提示。
继续进行的时间线默认折叠，成果使用紧凑列表。所有 home_kind 均无固定入口，
服务端同时拒绝 UI 与 Agent 的固定请求。首页槽位不得归档后重建；用 home_visible
控制显示，正常短时间更新始终使用原 card_id。

维护会话使用 `cyrene_system_prompt/home_prompt.py` 的专用提示词，不再复用前台
助理的任务协调指令。刷新请求只携带原因、本地时间、已有卡片及任务证据。
会话的持久化 `context_projection` 策略仅允许 system-prompt 来源的上下文，
并关闭模型可见的通用任务上下文协议和目录；内部执行记录仍保留，回放遵守同一策略。
SOUL、记忆、工作区以及其他插件附加指令不进入维护模型请求。工具权限仍独立限制为
四个卡片读写工具和 WebSearch。前台保留用户显式编辑、卡片提交与继续原任务的行为。
