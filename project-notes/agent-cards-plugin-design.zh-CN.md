# Agent 卡片工具包设计 V1

日期：2026-11-08。状态：待审核的实现设计，尚未注册工具、接入生产界面或连接真实 Agent。

目标：Agent 用同一套工具创建组合卡片、读取用户选择、更新任务结果、固定卡片；用户操作通过持久事件回到来源任务。19 类场景是内容模板，不是 19 套工具。自定义 HTML 使用同一个卡片外壳和事件协议。

## 1. 与当前架构对齐

已核对的现有实现：

- `src/cyrene/core/plugin/plugin.py`：`Plugin`、`PluginPack`、同步 `setup` / `application_setup`、工具 schema 与执行元数据。
- `src/cyrene/plugins/context.py`：应用服务、HTTP router、前端模块标记、前端方法、启动与停止回调。
- `src/cyrene/plugins/builtin/cyrene_entity/`：工具定义、handler、领域服务、应用路由的分层模式。
- `src/cyrene/plugins/builtin/pinned_topbar_context/context.py`：通过 `TURN_START` 挂载最新上下文，避免重复注入，清空时发布空快照。
- `src/cyrene/plugins/builtin/session_management/`：插件自有持久队列、调用回执、来源归属、应用生命周期；公开 `task_id` 实际映射到任务 Session。
- `src/cyrene/core/context/tasks.py`：一个 Session 内还存在独立的任务上下文 `context_id`，切换上下文不等于任务完成。
- `src/cyrene/workbench/chat/chat_guidance_service.py`：运行中的会话已有 guidance / inbox 接纳能力。

新增包建议命名 `cyrene_cards`，工具前缀 `card.`，应用服务名 `cards`。沿用已有 `PluginPack` 协议，不新增另一套插件清单协议或 Agent Loop。

需要新增的宿主接入点明确列在第 8 节；现有 `expose_frontend()` 是模块能力标记，不能据此声称已有任意组件动态加载或卡片渲染槽。

## 2. Agent 工具

V1 提供六个工具。`card.create`、`card.update` 和 `card.pin` 是主路径，其余三个用于恢复上下文和管理生命周期。创建与修改使用独立工具；创建时一次完成模板选择、内容填写和初始固定设置。具体 JSON Schema 放入插件的 `definitions.py`，通过现有工具发现机制获取。

| 工具 | 关键参数 | 返回 | 语义 |
| --- | --- | --- | --- |
| `card.create` | `template`, `spec`, `pinned=false` | `card_id`, `revision`, `input_revision`, `placement`, `placement_revision`, `status` | 选择预定义模板或自定义 iframe，填写内容并创建；可同时固定 |
| `card.update` | `card_id`, `expected_revision`, `changes`, `in_response_to_event_id?` | `card_id`, `revision`, `input_revision`, `status` | 修改已经创建的卡片；不创建新卡，也不覆盖用户输入与固定设置 |
| `card.get` | `card_id`, `include_events=false`, `after_event_id?`, `limit=20` | 描述、来源、用户草稿与已提交值、位置、状态；可选事件页 | 读取当前权威状态，不消费或确认事件 |
| `card.list` | `scope=current_task\|pinned`, `status?`, `cursor?`, `limit=20` | 卡片摘要与下一页游标 | 当前任务卡片目录，或当前用户可见的常驻卡片目录；默认不回传全部区块 |
| `card.pin` | `card_id`, `pinned`, `expected_placement_revision` | 位置、位置版本、修改者 | `pinned=true` 固定到右侧，`false` 取消固定；始终保留对话引用 |
| `card.archive` | `card_id`, `expected_revision`, `reason?` | `status=archived`, `revision` | 停止交互和自动更新，保留最终内容与原有固定位置；不做永久删除 |

`card.create` 不接受 `card_id`，宿主分配稳定 ID。工具重试使用执行层调用身份保存幂等回执，不能因重试创建第二张卡。`card.update` 必须同时提供已存在的精确 ID 和 `expected_revision`；未知 ID 返回 `card_not_found`，不隐式新建。

创建时，`template` 从第 4 节的 19 类预定义模板及 `custom`（新建 iframe）中选择。宿主将模板默认结构与 Agent 填写的 `spec` 合成为完整描述，校验通过后，将卡片和初始固定设置原子保存。`pinned=true` 无需再调用 `card.pin`；失败时不留下半张卡片。记录实际解析的模板版本，模板升级不会自动重写已经创建的卡片。

修改时，`changes` 只接受需要变更的描述字段：`title`、`subtitle`、`density`、`blocks`、`input_schema`、`actions`、`interaction`。省略字段保持原值；提供的数组或对象整体替换，不做隐式深合并或 JSON Patch。服务层合成完整描述后统一校验。不能通过 `changes` 改来源、模板身份、用户输入、固定位置或归档状态；这些仍由各自的状态入口负责。自定义 iframe 通过更新 `blocks` 中的 artifact 引用修改内容。

`card.get/list` 可以并行；写工具 `allow_parallel=False`，同时在存储层做版本检查，不能只依靠模型串行调用。工具返回短回执，完整内容按需读取。

不提供 `card.click`、`card.submit` 或 `card.confirm` 给模型。Agent 可以声明供用户操作的按钮，不能借工具伪造用户选择或确认。

## 3. 数据分层与来源

一张卡片保存四部分：

1. **描述 `spec`**：选定模板及版本、Agent 填写的标题、内容区块、输入定义、操作声明、密度；场景标签由模板解析。
2. **用户状态 `input_state`**：字段当前草稿、最近提交快照、逐字段版本；由经过校验的用户事件修改。
3. **展示 `placement`**：常驻与否、折叠与排序、修改者、位置版本；按用户及工作区/助理范围保存。
4. **来源与生命周期**：不可变来源、描述版本、状态、创建/更新时间、归档原因。

来源由宿主从可信运行上下文解析：`project_id`、`owner_session_id`、`session_id`、`context_id`、创建调用/消息锚点。模型不能通过参数随意声明另一个会话的归属。

`session_id` 标识接收运行的会话，`context_id` 标识该会话内的任务上下文；没有任务上下文能力的宿主可将后者记为 null。`session_management` 的公开 `task_id` 不能与 `context_id` 混用。跨任务固定不改变这些字段。

`revision` 只追踪 Agent 描述与生命周期更新，`input_revision` 追踪用户输入，`placement_revision` 追踪展示设置。改固定位置不会使采购确认失效；修改报价或确认所依赖的输入则会。

保留稳定 `block_id`、`field_id`、`row_id`、`item_id`、`action_id`；事件引用这些 ID，不用标题或数组位置识别目标。相同字段 ID 的兼容更新保留用户值；有未提交输入时删除字段或改变类型，返回 `input_conflict`，由界面明确处理草稿，不能静默丢弃。

所有读取和变更都校验来源、当前调用者、用户及项目范围。来源任务负责内容更新；有权查看该任务的助理所有者可以读取和安排展示。持有 `card_id` 本身不代表有权限。

## 4. 卡片描述示例

以下是提议的 `card.create` 参数：选择对比模板、填写采购内容，并在创建时固定到右侧。模板提供默认结构与场景标签，实际渲染由合成后的 `blocks` 决定。默认值不记录成用户选择。

```json
{
  "template": "comparison",
  "pinned": true,
  "spec": {
    "schema_version": 1,
    "title": "设备采购方案",
    "subtitle": "8 套设备 · 含税报价",
    "density": "comfortable",
    "blocks": [
      {
        "block_id": "supplier_options",
        "type": "table",
        "columns": [
          {"key": "name", "label": "供应商", "format": "text"},
          {"key": "amount", "label": "总价", "format": "currency", "currency": "CNY"}
        ],
        "rows": [
          {"row_id": "qinghe", "name": "青禾设备", "amount": 8640},
          {"row_id": "yuanjing", "name": "远景科技", "amount": 9280}
        ],
        "selection": {"mode": "single", "field_id": "supplier_id"}
      },
      {
        "block_id": "conditions",
        "type": "form",
        "fields": [
          {"field_id": "budget", "label": "预算上限", "type": "number", "unit": "CNY", "default": 10000, "minimum": 1},
          {"field_id": "expected_date", "label": "期望到货日期", "type": "date"}
        ]
      }
    ],
    "input_schema": {
      "type": "object",
      "properties": {
        "supplier_id": {"type": "string", "enum": ["qinghe", "yuanjing"]},
        "budget": {"type": "number", "minimum": 1},
        "expected_date": {"type": "string", "format": "date"}
      },
      "required": ["supplier_id", "budget", "expected_date"],
      "additionalProperties": false
    },
    "actions": [
      {
        "action_id": "prepare_application",
        "label": "生成申请预览",
        "kind": "submit",
        "fields": ["supplier_id", "budget", "expected_date"],
        "intent": "根据提交的采购条件生成申请预览",
        "continuation": "resume"
      }
    ],
    "interaction": {"change_delivery": "context"}
  }
}
```

输入 schema 是值校验的权威定义；表单字段声明用于展示，发布时检查字段引用、类型、选项与 schema 一致。金额、供应商名称、报价来源等权威事实从卡片当前版本解析，不信任浏览器额外上传的显示文本。

基础区块建议：`text`、`metrics`、`fields`、`list`、`table`、`form`、`timeline`、`files`、`chart`、`map`、`embed`。19 个场景只保存默认组合与示例。V1 可先落地前八种；图表、地图、embed 以宿主能力声明控制，不能接受描述后渲染空白。

`template` 使用以下稳定英文枚举，标题和显示标签支持中文/英文；`custom` 表示新建 iframe 卡片：

| 方向 | 场景枚举 |
| --- | --- |
| 理解与整理 | `summary` 摘要与结论、`recommendations` 搜索与推荐、`artifacts` 文件与成果 |
| 推进工作 | `checklist` 任务与清单、`schedule` 日程与排期、`activity` 执行与动态 |
| 数据与决策 | `comparison` 表格与对比、`metrics` 指标与预算、`trends` 趋势与图表、`approval` 审批与确认 |
| 信息与沟通 | `preferences` 表单与偏好、`message` 邮件与消息、`entity` 联系人与实体 |
| 地点与环境 | `places` 地图与地点、`context` 上下文与连接 |
| 健康管理 | `health_overview` 健康概况、`habit` 习惯与打卡、`medication` 用药提醒、`health_report` 检查与报告 |
| 自由扩展 | `custom`，使用 `embed` 区块 |

修改已有卡片示例（`card.update`）：

```json
{
  "card_id": "card_01",
  "expected_revision": 1,
  "changes": {
    "title": "设备采购方案 · 已更新报价",
    "subtitle": "等待供应商确认交付日期"
  }
}
```

取消固定示例（`card.pin`）；再次固定时将 `pinned` 设为 true，并使用最新位置版本：

```json
{
  "card_id": "card_01",
  "pinned": false,
  "expected_placement_revision": 1
}
```

典型调用路径：`card.create(template=..., spec=..., pinned=true)` 创建并固定 → 等待用户事件 → `card.get` 读取最新值 → `card.update` 发布申请预览及确认按钮 → 收到确认后调用原业务工具 → 更新结果或归档。已有卡片的固定状态由 `card.pin` 单独修改。

## 5. 用户操作进入 Agent

前端调用插件拥有的事件接口，不直接启动模型，也不把 DOM 文本拼成一条假用户消息。后端校验并持久化输入与事件，再投递。

```json
{
  "event_id": "evt_01",
  "card_id": "card_01",
  "base_revision": 3,
  "base_input_revision": 5,
  "action_id": "prepare_application",
  "kind": "submit",
  "values": {
    "supplier_id": "yuanjing",
    "budget": 10000,
    "expected_date": "2026-11-20"
  }
}
```

以上是客户端请求；`actor`、来源 Session/context、授权范围、服务端时间、实际 `input_revision` 和可信标签由后端补齐。浏览器不能自行提交这些权威字段。

| 操作 | 保存与投递策略 | Agent 行为 |
| --- | --- | --- |
| 单选、多选、勾选 | 即时保存，合并尚未投递的同字段 change | 下一个安全读取点可见；只有显式配置 `change_delivery=resume` 才唤醒 |
| 输入文字、数字、日期 | 本地即时草稿；失焦或短暂防抖后保存服务端草稿 | 作为草稿上下文，默认不逐键触发模型 |
| 提交表单 | 校验所有绑定字段，保存完整不可变提交快照 | 按 action 的 continuation 排队或恢复来源任务 |
| 点击确认 | 宿主绑定卡片版本、输入版本、动作与待执行摘要 | 仅继续所确认的具体操作；业务工具原有权限检查继续生效 |
| 折叠、排序、固定 | 保存展示偏好 | 默认不唤醒模型；需要时可读取位置状态 |

保存 change 必须让 Agent 能读到，但不要求每次变化都付出一次模型调用。`card.get`、当前任务上下文快照和被投递事件都能提供用户值。连续变化可合并；submit/confirm 不能合并丢失。

事件状态区分 `accepted`（已持久保存）、`queued`、`delivered`（运行入口已接纳）、`processed`（关联处理轮次成功结束）、`failed`。仅收到 HTTP 200 不能显示“Agent 已处理”。用户界面可以显示“已保存”“等待处理”“已更新”“处理失败，可重试”。

事件与输入写入及 outbox 入队应处于同一事务。以 `event_id` 去重；同 ID 不同内容返回冲突。投递采用至少一次交付和持久接纳回执，重启后继续，不能宣称所有下游业务动作天然 exactly-once。涉及外部副作用的业务工具仍需自己的幂等键和授权校验。

`card.update(in_response_to_event_id=...)` 用于关联卡片更新和事件，不作为模型自行制造用户确认的凭证。处理完成由宿主运行回执确认，而不是模型随意写入 `processed=true`。

## 6. 来源任务与上下文策略

- 来源 Session 正在运行相同 context：通过现有 inbox/guidance 能力在安全点接纳；处理时注入经过校验的结构化事件与当前卡片状态。
- 来源 Session 空闲且是相同 context：submit/confirm 可启动普通任务轮次；change 默认只保存。
- 来源 Session 正在运行另一个 context：事件排队，不能把采购操作插入旅行上下文，不能强行切换正在执行的任务。
- Session 空闲但来源 context 未激活：投递适配器请求正常的上下文恢复流程；恢复成功后再交付事件。该能力需补充宿主接口，在它实现前显示“等待返回来源任务”，不能声称已恢复。
- 有待回答问题或权限确认时：仅在 action 明确绑定当前 question/request ID 且校验通过时走原回答接口；其他操作排队。
- 来源 Session 被清空、替换、删除：校验 generation/版本，拒绝旧事件。不能把它投递到新会话或同名任务。

Session Hook 每轮挂载当前 context 的卡片目录、用户提交摘要和待处理事件。只注入必要数据，较大区块通过 `card.get` 按需读取；比较快照防止重复注入，清空状态也必须发出空快照。

跨任务常驻卡片可以显示在右侧，但其完整内容不因此自动进入所有任务的模型上下文。目录可显示来源和状态，读取仍受原有可见性控制。

一个模型 turn 完成不代表任务结束。V1 以显式 `card.archive` 或可信的任务归档/删除事件结束卡片；不监听所有 run completed 就自动只读。

## 7. 固定、历史与自定义内容

固定意味着右侧增加同一 `card_id` 的引用。对话通过 `card_ref` 找到当前卡片，常驻区按同一存储读取；更新广播到所有已打开视图。窄屏/常驻视图允许调整密度和折叠区块，不能重新计算一份独立业务数据。

用户取消固定具有优先权：记录用户 override，后续 Agent 内容更新不再自动固定。`card.pin(pinned=true)` 必须尊重该 override，返回 `user_placement_override`；用户再次固定或明确允许 Agent 管理后才恢复。`card.create` 默认 `pinned=false`，仅在对话显示；显式传 true 时创建并固定。不能为绕过用户取消固定而复制同一张卡片。

对话保留稳定引用，另在事件记录中保存当时的描述/提交快照或可解析版本；历史审计不能把今天的报价冒充过去确认的报价。V1 可以保存整份描述快照，无需内容级 diff。

自定义内容使用 `embed` 区块引用受管理的 HTML artifact：`artifact_id` + 内容版本/hash + 输入/动作 manifest。Agent 可通过已有文件生成能力准备 HTML，再通过 `card.create(template="custom", spec=..., pinned=...)` 新建 iframe 卡片；修改已创建的 iframe 时，使用 `card.update` 更新 artifact 引用与 manifest，保留原 `card_id`。不另造 HTML 生成工具。`custom` 模板要求一个有效 embed 区块，不能只选模板而不提供内容。

iframe 经宿主消息桥发送 `state.change` / `action.invoke`；壳层绑定卡片和实例身份，检查消息来源、动作白名单、数据 schema 与大小，再复用同一事件服务。不能由 iframe 任意指定接收 Session、伪造 actor 或直接调用宿主工具。没有网络需求时默认隔离网络与宿主资源；外部能力使用明确声明的宿主代理。确认操作由可信外壳展示并记录，不采信 iframe 自称“用户已确认”。这些隔离与桥接是待实现协议，不是当前演示已有能力。

## 8. 文件归属与新增接入点

```text
src/cyrene/plugins/builtin/cyrene_cards/
  __init__.py           PluginPack、setup、application_setup、中英文元数据
  definitions.py        六个工具的描述与 JSON Schema
  tools.py              薄 handler，解析调用身份并委托服务
  schemas.py            卡片、区块、动作、事件和输入校验
  service.py            版本、归属、用户输入、位置、归档规则
  store.py              卡片、输入、展示、事件、调用回执与 outbox
  context.py            TURN_START 上下文贡献
  routes.py             读取、用户事件、位置与订阅接口
  host.py               复用 Workbench 运行、inbox、上下文恢复、附件访问
  delivery.py           生命周期受控的事件投递与重试
  README.md             作者协议、场景示例、错误码、验收要求

src/cyrene/workbench/webui/frontend/features/cards/
  card-shell.jsx        统一标题、来源、状态、固定和主操作
  card-renderer.jsx     schema 到区块的分发
  card-store.js         按 card_id 维护所有视图共享的状态
  card-events.js        幂等事件、草稿、冲突及处理状态
  card-shelf.jsx        常驻区布局、折叠与取消固定
  iframe-bridge.js      自定义内容消息校验
  blocks/               复用内容区块
```

上述为建议结构，尚未创建。数据建议位于应用数据目录 `plugin_data/cyrene_cards/cards.sqlite3`，沿用 session_management 的插件私有存储习惯。正常启动不覆盖用户已编辑的插件实现。

`application_setup` 注册 `cards` 服务、路由、`cards` 前端模块标记和生命周期；`setup` 获取服务并注册 Context Hook。缺少可渲染宿主时明确返回 `card_surface_unavailable`，不能工具成功但用户看不到卡片。

待新增 Workbench 能力：

1. 对话消息中的持久 `card_ref` 记录与渲染分支，以及助理页的常驻区挂载点。
2. 卡片更新订阅端点（可使用独立 SSE）及断线后 cursor 恢复/完整重取。不要假定当前聊天 stream 会自动携带插件新事件。
3. 可信 `card_interaction` 接纳适配器：从存储读取事件，保留用户动作与 Agent 通知的区别；复用 inbox 和运行管理，不将其简单冒充现有 agent-originated 文本。
4. 来源 context 校验与安全恢复，运行回执到事件处理状态的关联。
5. 插件停用时撤销可操作 UI、停止投递、释放订阅；保留数据。重启/重启用后从持久位置恢复，不反复触发已接纳事件。

建议接口：`GET /api/cards`、`GET /api/cards/{id}`、`POST /api/cards/{id}/events`、`PUT /api/cards/{id}/placement`、`GET /api/cards/stream`。工具与 HTTP 入口共用服务层权限、版本及校验规则；主体身份来自宿主认证上下文。

## 9. V1 验收

- 经真实 Plugin registry / runtime 发现并调用六个工具；不能以直接调用 Python handler 代替宿主验收。
- 同一次创建调用重放只生成一张卡；未知 card_id 更新失败；过期 expected_revision 不覆盖新内容。
- 创建可选择 19 类预定义模板或新建 iframe，并原子保存初始 pinned 设置；模板或内容不合法时不创建卡片。更新省略字段保持原值，不能借修改隐式新建或更改固定位置。
- 单选、勾选、表单与确认都回传明确字段；默认值不产生用户确认；模型无伪造输入接口。
- 用户草稿不因 Agent 更新、切换任务或取消固定丢失；不兼容 schema 变更给出冲突。
- 对话和常驻区同步；重启后固定和输入仍保留；用户取消固定后 Agent 不自动重新固定。
- 另一个 context 的事件被排队；来源会话清空或替换后旧事件不可执行。
- 重复提交不重复接纳；accepted/delivered/processed 状态准确；投递失败可见且可重试。
- 旧版本确认无法授权新报价；已确认的提交快照可追溯；卡片确认不绕过业务工具权限。
- 归档后输入不可修改，仍可读和取消固定；普通轮次完成不自动归档。
- 自定义消息无法伪造来源、动作、用户确认或调用宿主工具；插件停用后不能继续投递。

建议实现顺序：先完成六工具、持久状态和原生区块；接上真实交互投递与双位置同步后，再加入 iframe。`custom` 在 iframe 能力可用前明确返回不支持，不能创建空白卡片；完整 V1 必须支持新建与修改 iframe。交互回传属于第一阶段的必要验收，不推迟为后续增强。
