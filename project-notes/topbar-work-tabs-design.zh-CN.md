> **PHASE 1 + 2 COMPLETED / 第一、二阶段已完成 — 2026-07-28：**
> 顶栏最近会话 Work Tabs、实时 MRU、置顶、右键资源预览、Pinned Resource
> Shelf、跨对话拖放、全局文件上下文与 Browser 跨会话只读共享均已实现。
> 本文是当前实现约束、验证基线和后续产品设计的正式交接文档。
>
> **当前验证提示：** 最近一次功能聚焦套件为 168 passed；文本导出兼容套件为
> 148 passed；标题栏布局套件为 13 passed。已运行代码仍是 source of truth，
> 文中明确列入后续阶段的内容才是提案。

# Cyrene 顶栏 Work Tabs Handoff

更新时间：2026-07-28

分支：`feature/project-literature-library`

代码基线：当前 `feature/project-literature-library` 工作区

## 1. 当前状态与实现交接

顶栏已经完成 **Work Tabs（工作标签）** 和 **Pinned Resource Shelf**：
显示最近打开的 3 个任务或对话，支持实时 MRU、置顶、移出顶栏、按会话显示
浏览器/文件资源，并可把文件、知识库条目、选中文字和 Browser 固定为全局资源。

长期仍应避免把“最近访问”和“全局共享”混成同一个概念。

- 顶栏只显示用户已经打开或固定的工作对象。
- Shelf 的 `+` 是空资源区和拖放落点；独立的最近对象菜单尚未实现。
- 任务/对话默认属于当前项目；浏览器和文件默认属于当前任务。
- 用户把资源拖到 Shelf 就是明确的全局共享动作。
- “能看到”与“能操作”分开授权：共享只授予发现和读取能力，写入、上传、提交表单等仍遵循原权限规则。

这样既能得到类似浏览器/IDE 的快速切换体验，又不会让全局 Agent 上下文无限膨胀。

### 1.1 已落地范围

- 原 `Cyrene / 类型 / 标题` 面包屑已替换为最近会话 tab strip；
- 同时聚合 Task session 和 Conversation，最多显示 3 个；
- 排序语义为：
  1. pinned，按置顶顺序；
  2. 最近被用户打开的对象，按 MRU 顺序；
  3. 没有打开记录时按 `updatedAt` 回填；
- 手动打开、新建或切换任务/对话会立即更新 MRU，不依赖刷新；
- 置顶、MRU 和“从顶栏移除”均持久化在 `localStorage`；
- 被移出的对象不会被删除；用户再次主动打开时会重新进入顶栏；
- active tab 只有背景与边框状态，不使用底部颜色条；
- tab 右键菜单复用 `.workbench-account-menu` 的现有 UI 样式；
- 右键菜单支持置顶/取消置顶、复制标题、从顶栏移除；
- 对话存在 Electron Browser session 时，菜单内直接显示当前浏览器页面的
  标题、URL 和即时截图缩略图，不显示一个“打开浏览器”的普通菜单项；
- 对话存在附件时，菜单显示去重后的文件列表，点击后切换到对应对话并打开 viewer；
- 文件名仅在实际截断时于 hover / keyboard focus 横向滚动显示完整内容，移开后复位；
- 点击浏览器预览会切换到对应对话并恢复其 PiP 浏览器。

### 1.2 当前数据与持久化

核心 helper：

```js
wbRecentSessionTabs(
  projects,
  chatsByProject,
  recentOpenedKeys,
  pinnedKeys,
  hiddenKeys,
  3
)
```

键格式统一为 `task:<id>` 或 `chat:<id>`。当前本地存储键：

| Key | 含义 | 上限 |
| --- | --- | --- |
| `wb-recent-opened-sessions` | 最近主动打开的 session MRU | 20 |
| `wb-pinned-sessions` | 置顶顺序 | 20 |
| `wb-hidden-session-tabs` | 从顶栏移除、等待再次主动打开 | 100 |

Conversation 列表由 `WorkbenchApp.reloadRecentChats()` 按项目读取，并通过
`WorkbenchChatPage.onChatsChange` 与 `cyrene:wbc-refresh-chats` 实时同步。新增对话、
外部 quick chat 更新和现有页面内的列表更新都会进入这条同步链路。

### 1.3 右键菜单资源协议

`loadSessionTabResources(item)` 在每次右键时即时查询，不缓存资源快照：

- 浏览器：
  1. `window.cyrene.browser.getState(item.id)` 判断该 conversation 是否已有 tabs；
  2. `window.cyrene.browser.screenshot({ sessionId, tabId })` 捕获 active page；
  3. 将 `pngBase64` 仅保存在当前菜单 state，关闭菜单后释放；
- 文件：
  1. `GET /api/workbench/chats/{chatId}`；
  2. 汇总所有 message attachments；
  3. 按 `id / url / name` 去重。

资源点击通过 navigation pending payload 传递：

```js
{
  type: "chat",
  projectId,
  chatId,
  topbarResource: { type: "browser" }
}

{
  type: "chat",
  projectId,
  chatId,
  topbarResource: { type: "file", file }
}
```

`WorkbenchChatPage` 使用 `pendingTopbarResourceRef` 等待目标 conversation 激活。
浏览器资源会设置 `browserActiveByChat[chatId] = true` 与 PiP mode；文件资源调用
现有 `openViewer(file)`。

### 1.4 Electron 原生浏览器层叠约束

这是当前实现最容易回归的部分：

- Browser page 是 Electron `WebContentsView`，位于普通 renderer CSS 之上；
- 右键菜单通过 `ReactDOM.createPortal(..., document.body)` 放到全局 renderer 层；
- 因 Portal 脱离 `.workbench-shell`，打开菜单时会从 shell 的 computed style
  复制现有 `--wb-*` 变量，确保菜单继续复用 Workbench 主题；
- **不要在 session 右键菜单打开时调用 `wbSetBrowserOverlayObscured(1)`**。
  该调用会隐藏所有原生 browser page，造成 PiP 外壳仍在但内容区变白；
- 设置、搜索等真正覆盖大面积界面的 modal 仍需要
  `wbSetBrowserOverlayObscured`，不要全局删除该协调器；
- 如果未来允许把 PiP 拖到顶栏菜单下方，应做“矩形相交时的局部 bitmap proxy”，
  不能重新采用“菜单一开就隐藏所有浏览器”的策略。

### 1.5 当前视觉参数

- 顶栏：58px；compact：50px；
- 品牌列：`minmax(170px, 188px)`；
- tab：`clamp(82px, 9.5vw, 136px)`，高 32px，间距 6px；
- tab 内 padding：6px；图标与标题 gap：4px；
- inactive 有边框；active 使用 accent 混合边框和背景，无底部颜色条；
- 置顶使用直立 thumbtack SVG，菜单和 tab 标记保持一致；
- 菜单宽 224px，浏览器缩略图为 16:10。
- Pinned resource 默认只显示 30px SVG 图标；hover/focus 时最大展开至 180px
  显示文件名或页面标题；
- Shelf 与搜索操作区保留 10px 间距；搜索按钮宽 168px；
- 资源菜单复用 `.workbench-account-menu`，固定 Browser 在其他 session 中只读。

### 1.6 代码落点

- `src/webui/frontend/workbench.jsx`
  - MRU / pinned / hidden 状态；
  - `wbRecentSessionTabs`；
  - `WorkbenchTopbar` 与右键菜单；
  - 浏览器截图、附件收集、资源导航、Shelf drop 和全局资源持久化。
- `src/webui/frontend/workbench-chat.jsx`
  - chat 列表实时回传；
  - `topbarResource` pending payload 的消费；
  - PiP browser 与 file viewer 的恢复；
  - 文件、原生 macOS selection 和 Browser drag payload；
  - 目标 conversation 的 pending composer resource。
- `src/webui/frontend/workbench-library.jsx`
  - 表格行和卡片作为 drag source；
  - 有附件条目按 File 处理，无附件条目生成 Markdown 摘要。
- `src/webui/frontend/workbench.css`
  - tab strip、active/inactive、置顶标记；
  - context menu 定位、资源预览增量样式；
  - 菜单主体继续复用 `.workbench-account-menu`；
  - Shelf、resource chip、drop feedback、搜索宽度与安全间距。
- `src/webui/frontend/workbench-i18n.jsx`
  - 最近会话与右键菜单中英文文案。
- `src/cyrene/workbench/pinned_resources.py`
  - 固定资源 Registry、Snippet → Markdown、Agent 全局索引；
  - Markdown 使用 ASCII 存储 key，显示名称保持 Unicode。
- `src/route/workbench/chat.py`
  - Pinned resource API 与知识库附件安全解析。
- `src/route/agent/chat.py`
  - 导出文件读取兼容历史 Unicode storage key。
- `src/cyrene/browser.py` 与 Browser tool implementations
  - 固定 Browser 的 owner-control / others-readonly 执行层约束。
- `tests/test_workbench_recent_session_tabs.py`
  - MRU 合并、置顶、隐藏、资源菜单与浏览器层叠防回归。
- `tests/test_electron_titlebar_alignment.py`
  - macOS traffic lights 与标题栏布局。
- `tests/test_workbench_pinned_resources.py`
  - 固定文件、Markdown、知识库来源和 Browser 只读语义。
- `tests/test_workbench_library.py`
  - 知识库表格/卡片 drag source。
- `tests/test_chat_attachment_flow.py`
  - 历史 Unicode 导出文件兼容。

### 1.7 验证与重启

```bash
cd src/webui
npm run build

cd ../..
uv run pytest -q \
  tests/test_workbench_recent_session_tabs.py \
  tests/test_electron_titlebar_alignment.py \
  tests/test_workbench_frontend_logic.py \
  tests/test_webui_consolidation_contract.py \
  tests/test_quick_chat_feature.py \
  tests/test_workbench_notifications.py

cd electron
npm run dev
```

当前最近的聚焦回归为 **168 passed + 148 passed + 13 passed**。这些套件有
重叠，不能相加为总测试数。人工验证至少覆盖：

1. 手动打开/新建对话后立即进入最近 3 个；
2. task 与 conversation 混排；
3. 置顶、取消置顶、移出及重新打开；
4. 有 Browser session 的 conversation 右键显示实际页面缩略图；
5. 右键菜单打开期间，原 PiP browser 内容仍正常显示；
6. 文件列表点击后进入目标 conversation 的 viewer；
7. light / dark 与 compact density；
8. macOS traffic lights、品牌与首个 tab 间距。
9. macOS 原生 selection 拖到 Shelf 后生成可打开的 Markdown；
10. 知识库表格行/卡片拖到 conversation tab 和 Shelf；
11. Browser PiP 与 minimized pill 均可拖到 Shelf；
12. Shelf drop 绿色边框不与 168px 搜索按钮重叠。

## 2. 要解决的问题

改造前顶栏中央显示 `项目 / 页面 / 对话` 面包屑，适合说明“我在哪里”，但不适合：

1. 在几个高频任务或对话之间来回切换；
2. 保留一个浏览器页面，跨对话继续使用；
3. 将关键文件固定为多个 Agent 都能发现的工作资料；
4. 看出某个资源正在被哪个 Agent 使用或修改；
5. 在关闭页面后快速找回最近工作。

Work Tabs 的目标不是把所有历史记录铺在顶栏，而是呈现“当前工作集”。

## 3. 长期信息模型

统一标签外观，但保留四种对象类型：

| 类型 | 代表对象 | 默认作用域 | 激活后的主内容 | 可否全局共享 |
| --- | --- | --- | --- | --- |
| Task | 一项任务 / session | Project | 任务工作台 | 否；任务本身保持项目归属 |
| Conversation | 独立对话 | Project | 对话线程 | 否；可分享其中的资源 |
| Browser | 一个真实浏览器 tab | Task | 浏览器视图或浮窗 | 是 |
| File | 一个文件、artifact 或预览 | Task | 文件预览 / 编辑器 | 是 |

所有标签使用统一数据结构：

```ts
type WorkTab = {
  id: string
  kind: "task" | "conversation" | "browser" | "file" | "snippet"
  resourceId: string
  title: string
  projectId: string
  ownerSessionId?: string
  scope: "private" | "task" | "workspace"
  pinned: boolean
  dirty?: boolean
  activity?: "idle" | "running" | "attention" | "error"
  lastOpenedAt: string
}
```

这里必须区分：

- `scope`：谁可以发现并读取资源；
- `ownerSessionId`：当前由谁持有主要控制权；
- `activity`：资源是否正在运行或需要处理；
- `pinned`：是否在重启后恢复。

不要用一个模糊的 `global: true` 同时表达以上状态。

## 4. 顶栏布局

### 推荐布局：保持单行 58px

```text
┌──────────────────────────────────────────────────────────────────────────┐
│ [Cyrene] [💬 对话] [✓ 任务] │ [📄 spec.md] [🌐 Docs] │ [搜索] ◐ ? ⚙ ◎ │
└──────────────────────────────────────────────────────────────────────────┘
```

- 左侧保留品牌与 macOS traffic-light 安全区。
- 中央原面包屑区替换为可横向滚动的 Work Tab strip。
- 在 session tabs 与搜索框之间增加 **Pinned Resource Shelf**。文件、选中文字
  和浏览器拖到这里后成为固定资源；它与“最近 3 个 session”是两个独立集合。
- 右侧保留搜索、通知、主题、帮助、设置和头像。
- 当前项目名不再常驻占用横向空间，放到：
  - tab tooltip；
  - `+` 菜单的分组标题；
  - 主内容区域的小型上下文标题；
  - 窄屏时的溢出菜单。

不建议把顶栏改成 88–96px 双层结构。双层可以同时容纳面包屑和 tabs，但会持续侵占对话的垂直空间，并增加 Electron 原生浏览器 surface、浮窗和紧凑模式的坐标适配成本。

### 当前面包屑的迁移

当前：

```text
Cyrene / 对话 / 打开 claude code
```

改为：

```text
[Cyrene]  [💬 打开 claude code ×]  [+]
```

- `Cyrene` 保留为固定的 Home / 品牌入口，不参与普通 tab 关闭和排序；
- `/` 分隔符全部移除；
- `对话` 不再占一个独立 tab，它是资源类型，收进气泡图标、tooltip 和无障碍名称；
- `打开 claude code` 成为真正的 active Conversation Tab；
- 当用户打开另一个任务、对话、浏览器或文件时，直接追加到它右侧；
- 点击 `Cyrene` 回到 Home，但不会关闭当前工作 tabs；
- 如果产品仍需显示所属项目，在 tab tooltip 中显示 `项目名 · 对话 · 标题`，不要重新引入面包屑。

这样不会形成 `[对话] [打开 claude code]` 两个相邻 tab。前者只是分类，后者才是可切换、可关闭、可恢复的工作对象。

### 长期目标尺寸（当前值见 1.5）

- 高度：34px；
- 最小宽度：96px；
- 理想宽度：152px；
- 最大宽度：220px；
- 圆角：8px；
- tab 间距：4px；
- 图标：16px；
- 关闭按钮只在 hover、focus 或 active 时出现；
- active tab 使用更明确的背景和边框，不增加底部颜色条。

### 每类图标与辅助状态

- Task：任务/清单图标；
- Conversation：气泡图标；
- Browser：favicon，加载不到时使用浏览器图标；
- File：按 MIME / 扩展名显示图标；
- `●`：Agent 正在执行；
- 蓝点：有未查看更新；
- 橙点：需要用户确认；
- 红点：执行失败；
- 文件名后的实心小点：有未保存修改；
- 地球/共享徽标：workspace scope。

共享徽标应始终可见，不能只藏在右键菜单里，因为它直接影响用户对数据边界的判断。

### Pinned Resource Shelf

固定资源区位于当前 session tabs 与搜索框之间：

```text
[最近 session tabs]  [固定资源 drop zone]  [搜索]
```

- 无拖拽时只显示已固定资源，空状态不占用明显宽度；
- 空 Shelf 的 `+` 提供本地化 hover/title 与可访问名称，说明支持的拖拽来源；
- 拖动有效资源时以 accent 混合边框和背景显示 drop zone；
- 固定 File / Markdown 默认只显示文件 SVG，hover/focus 时展开文件名；
- 固定 Browser 默认只显示 Browser SVG，hover/focus 时展开页面标题；
- 所有固定资源都复用 session menu 风格的右键菜单，可打开/复制引用/取消固定；
- session 的“置顶”仍只影响左侧最近 session 顺序，不等同于固定资源。

## 5. 核心交互

### 打开

- 点击侧栏中的任务或对话：在当前 tab 打开。
- `Cmd/Ctrl + 点击` 或上下文菜单“在新标签中打开”：新建 tab。
- 浏览器或文件被 Agent 创建时：
  - 默认不抢占当前 tab；
  - 在顶栏出现轻量新增动画和蓝点；
  - 用户主动点击后激活。
- 双击 tab：固定。
- 拖动：调整顺序；不同类型可以混排。

### 资源拖放（Phase 2）

支持四类拖拽源：

1. 用户在消息或文档预览中选中的文字；
2. 对话消息中的文件卡片；
3. 知识库表格行或卡片：
   - 带附件的条目作为 File resource；
   - 无附件的条目固化为 Markdown 摘要；
4. 浏览器的两种现有状态：
   - 展开的 PiP 小窗；
   - `mode = "minimized"` 的“浏览器”悬浮胶囊。

统一 drag payload，不直接暴露本机绝对路径：

```ts
type WorkResourceDragPayload = {
  kind: "file" | "snippet" | "browser"
  ownerSessionId: string
  ownerProjectId: string
  stableRef: string
  title: string
  url?: string
  file?: PublicAttachment
  text?: string
  sourceKind?: "library"
  libraryItemId?: string
}
```

使用内部 MIME：`application/x-cyrene-work-resource+json`。文件拖放不得把
本机绝对路径放入 renderer payload。

#### 拖到顶栏

- drop target 是 session tabs 与搜索框之间的 Pinned Resource Shelf；
- File、Browser drop 成功后立即显示对应 resource chip；Snippet drop 会先固化为
  UTF-8 Markdown 文件，再显示为 File chip；
- 固定资源列表持久化，重启后仍恢复；
- 固定文件后，所有 session 的下一次 Agent turn 都能看到它的资源索引；
- 固定文件的索引至少包含 resource ID、文件名、类型和可读取引用，不自动注入文件全文；
- 固定浏览器后，所有 session 的下一次 Agent turn 都能看到它的只读资源索引；
- 固定浏览器的其他 session Agent 只能查看标题、URL、截图和页面快照，不能操作页面；
- 固定选中文字会生成 `.md` 文件，因此沿用固定文件的全局 Agent 资源语义；
- 取消固定只移除顶栏引用，不删除原文件、文字来源或浏览器页面。

Browser 的两种状态必须生成同一个 `kind = "browser"` payload，且使用相同的
`resourceId / sourceSessionId / activeTabId`，避免从不同状态固定出两个重复资源：

- **PiP 小窗：** 整个标题栏复用现有移动手势，并直接用指针坐标与 Shelf 的
  `getBoundingClientRect()` 做命中，避免 Electron 原生 View / pointer capture
  令 `elementFromPoint()` 失真。指针未进入 Shelf 时仍是移动小窗；进入后显示
  “固定浏览器”反馈，松手固定资源；
- **最小化按钮：** 只显示当前网页 favicon，缺失时显示通用网页 SVG。整个按钮
  可拖；普通 click 仍恢复 PiP，只有移动超过 3px drag threshold 后才进入资源
  拖拽；按钮复用 PiP 的浮窗坐标提交与消息避让路径，可在对话区域内自由移动。
  favicon 加载失败时隐藏破图并立即露出 Browser SVG；拖向顶栏时使用挂在
  `body` 的 fixed 代理跨过对话标题栏裁剪层；
- 两种状态 drop 成功后都不关闭、不最小化、不转移原 Browser session；
- 本阶段只覆盖这两种状态，不增加 maximized 浏览器的拖放。

固定 Browser 的权限模型：

```ts
type PinnedBrowserRef = {
  resourceId: string
  ownerSessionId: string
  activeTabId: string
  title: string
  url: string
  access: "owner-control-others-readonly"
}
```

- `ownerSessionId` 对应的 Agent 与用户保留现有控制能力；
- 其他 session Agent 的允许操作仅为读取标题、URL、截图和页面快照；
- 其他 session Agent 的 `navigate / click / type / reload / back / forward /
  upload / setInputFiles` 必须在工具执行层拒绝；
- 只读限制不能仅写入 system prompt，必须由 resource resolver / tool policy 校验
  调用者 session ID；
- 其他 session 点击固定 Browser chip 时打开只读预览，不接管原
  `WebContentsView`。

#### 拖到其他 conversation tab

- conversation tab 在 drag-over 时高亮；
- drop 后不必先切换页面，资源直接进入目标 conversation 的 composer：
  - File → attachment chip；
  - Snippet → quoted-context chip；
- Browser 不进入 composer；目标 Conversation 的独立 `BrowserTabManager`
  直接新建同 URL Tab，并显示为 PiP。登录 Partition 共享，但页面控制权、历史
  和 Browser Session 不共享；
- 如果目标 conversation 未挂载，将操作写入
  `pendingComposerResources[chatId]`，在 composer 初始化时消费；
- Task tab 没有对话输入框，不接受该 drop，必须显示明确的不可放置光标和提示；
- drop 只加入草稿，不自动发送消息。

#### 必要拖拽反馈

- drag image 使用与源卡片一致的紧凑 ghost；
- 顶栏 drop zone 和 conversation tab 必须有清晰高亮状态；
- 选中文字依赖浏览器原生 selection drag，不能给消息行或祖先补
  `draggable`，以免破坏普通文字选择、复制和链接拖动。
- macOS 的 selection 使用 Chromium 原生 `text/plain` drag，不给消息行设置
  `draggable`；Shelf 与 conversation tab 直接接收原生文字 payload。

#### 本阶段明确不做

- Browser 拖到其他 conversation 的输入框（当前语义是复制到目标 Browser）；
- maximized 浏览器拖放；
- Browser 控制权转移或其他 session Agent 的页面操作；
- 把选中文字全文直接复制进所有 Agent prompt（当前只注入 Markdown 文件索引）；
- 资源拖到系统桌面、Finder 或其他应用；
- 拖拽排序、控制租约、审计日志或其他共享范围管理；
- drop 后自动发送消息。

### 切换

- `Cmd/Ctrl + 1…3`：切到当前顶栏对应位置；
- `Ctrl + Tab` / `Ctrl + Shift + Tab`：在三个最近 Session 间前后切换；
- `Cmd/Ctrl + W`：从顶栏移除当前 Session，不终止任务；
- Tab/Resource 获得 Focus 后，`←/→`、`Home/End` 遍历，`Enter/Space`
  打开，`Delete/Backspace` 移除；
- `Cmd/Ctrl + Shift + 1`：切换到第一个项目，避免与 Session 快捷键冲突；
- `Cmd/Ctrl + K`：全局搜索，同时可搜索未打开的最近对象；
- 中键点击：关闭；
- active tab 再次点击不做折叠，避免含义不稳定。

### 关闭

关闭 tab 只关闭“视图引用”，不默认删除底层对象：

- Task / Conversation：从工作集移除，历史仍保留；
- Browser：若只有该 tab 持有浏览器页面，提示“关闭视图”或“同时关闭页面”；
- File：未保存时提示保存、放弃或取消；
- 正在运行的 Agent：关闭 tab 不终止任务，tab 进入 `+ > 运行中`；
- `Cmd/Ctrl + Shift + T`：恢复最近关闭的 tab。

### `+` 菜单

```text
新建
  新任务
  新对话
  新浏览器页
  打开文件…

运行中
  ● 调研 OAuth 方案              Agent 2
  ● Stripe Docs                  Browser

最近
  今天
    API 错误处理                 Conversation
    auth-spec.md                 File
  昨天
    登录回归测试                 Task

共享资源
  🌐 Stripe Docs                 2 个 Agent 正在查看
  📄 product-brief.md            只读
```

这里才承载“最近”，避免顶栏自动产生十几个不可控 tab。

## 6. 浏览器 tab 的设计

项目已有真实浏览器 tab，但当前浏览器管理器以 session 为边界。建议引入两级概念：

1. **Work Tab**：Cyrene 顶栏里的工作入口；
2. **Browser Page**：Electron 浏览器管理器中的真实页面。

一个 Work Tab 指向一个 Browser Page。不要在顶栏 tab 内再嵌一排小浏览器 tab；这会出现双重 tab 导航和关闭语义冲突。

### 默认行为

- Agent 在任务 A 中打开网页，`scope = task`，`ownerSessionId = A`；
- 切到任务 B 时仍可在顶栏看到该 Work Tab，但显示“属于任务 A”；
- B 中的 Agent 不能自动读取它，除非用户选择：
  - “共享给当前任务”；
  - “共享给所有 Agent”；
  - “复制到当前任务”（创建独立页面，不共享浏览状态）。

本次 Phase 2 中，“拖到 Pinned Resource Shelf”就是用户明确执行的只读分享动作：
其他 session Agent 可以查看该 Browser 的当前内容，但不能获得控制权。

### 共享后的并发

建议采用“多人可见，单一控制者”的租约模型：

- 所有 Agent 可读取 URL、标题和快照；
- 同一时刻只有一个 Agent 或用户拥有交互控制权；
- 其他 Agent 需要“请求控制”；
- 控制者、等待者和最后操作时间显示在 tab tooltip / 详情菜单；
- 用户接管始终优先；
- 敏感页面可随时切回私有。

不要默认让多个 Agent 同时点击同一个 DOM 页面，竞态会使结果难以解释和复现。

## 7. 文件 tab 的设计

文件 tab 需要区分三种来源：

- workspace 文件；
- Agent 生成的 artifact；
- 外部只读附件。

共享时建议记录稳定引用，而不是只保存本机绝对路径：

```ts
type SharedFileRef = {
  resourceId: string
  source: "workspace" | "artifact" | "attachment"
  uri: string
  revision?: string
  access: "read" | "write"
}
```

规则：

- workspace 内文件可授予 read 或 write；
- workspace 外文件默认只读，另行批准后才可复制进入 workspace；
- artifact 默认只读，用户选择“编辑副本”后生成 workspace 文件；
- 文件被外部修改时显示“已更新”，而不是静默覆盖当前预览；
- 多 Agent 编辑同一文件时，顶栏只显示活动状态，不用它代替真正的冲突检测。

### 选中文字资源

选中文字以原生 `text/plain` 进入统一 payload。拖到 conversation tab 时仍作为
quoted context；拖到 Shelf 时由服务端固化为 UTF-8 Markdown：

```ts
type SelectionDragPayload = {
  kind: "snippet"
  text: string
  title: string
  ownerSessionId: string
  ownerProjectId: string
  stableRef: string
}
```

- 固化后的顶栏项显示 File SVG，hover/focus 显示 `.md` 文件名；
- 拖到 conversation tab 时作为 quoted-context chip 加入输入框；
- 拖到顶栏后按固定文件处理，所有 session 的下一次 Agent turn 可发现并按需读取；
- 存储 key 使用 32 位十六进制 UUID + `.md`，显示名称保留 Unicode；
- 导出路由兼容早期包含中文显示名的 storage key。

## 8. 权限与隐私

“共享给所有 Agent”建议使用清晰的三级范围：

| 范围 | 谁能发现 | 谁能读取 | 谁能操作 |
| --- | --- | --- | --- |
| 私有 | 用户与创建它的 Agent | 同左 | 遵循现有权限 |
| 当前任务 | 当前任务内 Agent | 当前任务内 Agent | 遵循现有权限 |
| Workspace | 当前 workspace 所有 Agent | 所有 Agent | 仍需各工具权限与控制租约 |

浏览器页面包含密码、支付、邮箱、内部后台等敏感内容时：

- 登录页、密码框、支付页不允许自动提升为 workspace；
- 分享动作使用确认弹层，明确列出 URL、当前登录状态和可见范围；
- 切到 workspace 后给 tab 增加常驻共享徽标；
- 提供“一键停止共享”，停止后不删除原页面；
- Agent 上下文中只注入资源索引，真正读取时再按需获取快照或文件内容。

最后一条很重要：全局可发现不等于把所有页面全文塞进每个 Agent prompt。

## 9. 空间不足与响应式

优先级如下：

1. active tab 始终完整可见；
2. pinned tabs 保留图标和最短标题；
3. 非 pinned tabs 压缩到最小宽度；
4. 更旧的 tabs 收入 `»` 溢出菜单；
5. 右侧系统操作不被 tabs 挤走。

建议阈值：

- 宽度 ≥ 1180px：完整 tabs；
- 900–1179px：缩短标题，搜索框变图标；
- 720–899px：只保留 active + pinned + `»`；
- < 720px：顶栏只显示 active tab，其他全部进入切换器。

紧凑模式保持 50px 顶栏时，tab 高度降到 30px，关闭按钮继续保持至少 28×28px 的可点击区域。

## 10. 视觉状态示意

```text
普通      [💬 API 设计]
激活      [💬 API 设计 ×]
运行中    [✓ 登录修复  ●]
有更新    [🌐 OpenAI Docs  •]
需确认    [✓ 发布版本  ●橙]
共享      [📄 brief.md  🌍]
未保存    [📄 notes.md  ●]
失效      [🌐 Page closed  !]
```

颜色仅作为增强；图标、文字 tooltip 和可访问名称必须同时表达状态。

## 11. 与现有结构的衔接

当前代码落点以 1.6 为准。Phase 2 已完成以下衔接；后续修改必须保留 session
tabs 与 resource shelf 两个独立集合：

- `src/webui/frontend/workbench.jsx`
  - `WorkbenchTopbar` 渲染 task / conversation tabs、Pinned Resource Shelf、
    drop target、resource chip 和全局 Registry 同步；
  - 当前 session item 不应直接混入 resource item，保持两个相邻但独立的数据集合。
- `src/webui/frontend/workbench-chat.jsx`
  - 文件卡片、selection snippet、Browser PiP chrome 与 minimized pill 提供 drag source；
  - conversation tab drop 写入目标 chat 的 pending composer resource queue；
  - composer 将 File/Snippet 转换为现有 attachment/context chip。
- `src/webui/frontend/workbench.css`
  - `.workbench-topbar` 为品牌 / session tabs / resource shelf / actions 四列、
    58px 高；
  - 搜索位于 actions 左端，宽 168px，actions 与 Shelf 之间有 10px 间距；
  - 必须保留 `-webkit-app-region: drag`，drag source、drop target、tab 和按钮保持
    `no-drag`。
- `src/cyrene/workbench` 与 `src/route/workbench`
  - 已提供持久化 pinned file/browser Registry 与 stable resource ref；
  - Library File 在固定或发送时解析为受管理的真实附件路径。
- Agent context assembly
  - 每个 session 的下一次 Agent turn 注入 `<pinned_topbar_resources>` 索引；
  - File 只自动注入名称、类型、来源和 resource ID，正文按需读取；
  - Browser 只注入标题、URL、owner 和 resource ID，截图/页面快照按需读取；
  - Selection 已固化为 Markdown File，因此不保留独立 Snippet 注入语义。
- Browser resource resolver / tool policy
  - 比对调用者 session 与 `ownerSessionId`；
  - owner 保留现有能力；其他 session 只放行 snapshot/screenshot/read；
  - 对其他 session 的所有页面变更调用返回明确的 read-only 错误。
- `tests/test_electron_titlebar_alignment.py`
  - 顶栏保持 58px 可避免 macOS traffic light 坐标变化；
  - 已验证 tabstrip 的 no-drag、active 可见性、搜索间距和 compact 模式高度。

## 12. 分阶段实现

### Phase 1：最近任务 / 对话 Work Tabs（已实现）

- 将 `Cyrene / 类型 / 当前标题` 面包屑替换为品牌入口和最近 3 个 session tabs；
- 支持打开、切换、置顶、移出和再次打开；
- 右键显示 Browser snapshot 与 attachment 列表；
- tab 状态持久化到本地；
- 不改变浏览器和文件权限模型。

尚未实现：独立 `+` 最近菜单、拖拽排序、中键关闭、快捷键切换与恢复最近关闭。

### Phase 2：可拖拽共享资源与 Pinned Resource Shelf（已实现）

- 新增 session tabs 与搜索框之间的 Pinned Resource Shelf；
- 文件卡片、知识库条目、选中文字、Browser PiP 小窗和 minimized 胶囊成为 drag source；
- File、Browser、Selection/Library Markdown 资源拖到 Shelf 后固定并持久化；
- 固定文件写入 workspace Registry，所有 session 的下一次 Agent turn 获得文件索引；
- 固定 Browser 写入只读 workspace Registry，其他 session Agent 可查看但不可操作；
- 文件 / snippet 拖到其他 conversation tab 后进入其 composer 草稿；
- 支持取消固定但保留底层文件、文字来源和浏览器页面。

Phase 2 已按以下层次落地：

1. **2A — DnD 基础设施：** 内部 MIME、drag ghost、drop feedback、pending composer queue；
2. **2B — File/Snippet：** 文件卡片、知识库条目与原生 selection drag、跨对话
   composer drop；
3. **2C — Resource Shelf：** File/Browser resource chip、固定与持久化；
4. **2D — Browser：** PiP 指针矩形命中与 Shelf drop 协调、favicon-only
   minimized button drag、顶栏固定；
5. **2E — Agent awareness：** File 与只读 Browser 索引进入所有 session，并在
   Browser tool policy 强制 owner-control / others-readonly。

## 13. 成功指标

首版重点观测：

- 用户在两个任务间切换的中位操作次数；
- 从“最近”恢复工作所需时间；
- 平均打开 tab 数、固定 tab 数；
- tab 关闭后立即恢复的比例（可发现误关问题）；
- 超过 8 个 tab 的用户比例（判断溢出策略是否足够）；
- 顶栏切换后返回侧栏找同一对象的比例。

Phase 2 只增加与本次范围直接相关的指标：

- File/Snippet 拖到其他 conversation tab 的成功率；
- 拖放后未发送即删除 chip 的比例（用于发现误放）；
- 固定文件被其他 session 的 Agent 实际发现或读取的比例；
- 固定后立即取消固定的比例；
- 文件卡片拖拽与原点击打开 viewer 的冲突率。

## 14. 首版验收标准

1. 顶栏稳定显示最近 3 个 session tab、固定资源和右侧操作；
2. active tab 在任何窗口宽度下都不会被完全隐藏；
3. 关闭 task tab 不会终止正在运行的任务；
4. 重启后恢复 pinned tabs，普通 tabs 可配置是否恢复；
5. 键盘可以完成打开切换器、遍历、激活和关闭；
6. tab 可访问名称包含对象类型、标题、活动状态与共享范围；
7. macOS 标题栏拖动、traffic lights 与紧凑模式不回归；
8. Phase 1 不改变现有 session-scoped 浏览器隔离。

### Phase 2 拖放验收

1. 长按/拖动文件卡片不会触发文件 viewer，也不会丢失原 click 行为；
2. selection drag 不破坏文字复制、链接点击和 composer 输入选择；
3. File/Snippet drop 到 conversation tab 后只进入草稿，不自动发送；
4. drop 到未挂载 conversation 时，资源在下次打开该 tab 后准确恢复到 composer；
5. drop 到 Task tab 被拒绝并给出明确反馈；
6. 文件、selection Markdown、browser 固定后重启仍存在；
7. 其他 session 的下一次 Agent turn 能列出用户固定的文件及其来源；
8. 大文件不会被全文复制进所有 Agent prompt；
9. PiP 整个标题栏拖到 Shelf 可固定；拖到其他位置仍只移动小窗；
10. minimized 按钮只显示当前页 favicon；click 仍恢复 PiP，超过 3px drag
    threshold 后可自由移动或拖到 Shelf 固定；favicon 加载失败显示 Browser
    SVG，拖动代理可从对话标题栏上方穿过；
11. 两种状态固定得到同一个 Browser resource，不产生重复 chip；
12. 其他 session Agent 能读取固定 Browser 的标题、URL、截图和页面快照；
13. 其他 session Agent 的导航、点击、输入、刷新和上传调用均在执行层被拒绝；
14. owner session Agent 和用户仍可正常操作 Browser；
15. Browser 固定后原 session 的页面和当前显示状态继续可用；
16. unpin 后底层资源不被删除；
17. macOS titlebar drag、tab 点击、traffic lights 和 Browser WebContentsView 不回归。
18. 知识库有附件条目以 File 加入目标 conversation 或 Shelf；
19. 知识库无附件条目以 Markdown 摘要加入目标 conversation 或 Shelf；
20. 固定 Markdown 的 Unicode 显示名可打开，旧版 Unicode storage key 不返回 404；
21. resource chip 默认仅显示 SVG，hover/focus 才展开名称；
22. Shelf drop 指示框与 168px 搜索按钮之间始终保留可见间距。
23. minimized 浮窗移动时，与其纵向相交的消息复用 PiP 避让并实时重排。

## 后续建议

Phase 1/2 已完成。后续如继续扩展，应单独设计并验收：

- 最近对象 `+` 菜单、tab/resource 拖拽排序和恢复最近关闭；
- 窄屏 overflow switcher 与键盘切换；
- Browser 控制权租约或审计；
- 通用 workspace 分享范围管理。

在没有新的权限与生命周期设计前，仍不实现 Browser 拖到其他 conversation、
其他 session 操作固定 Browser，或 drop 后自动发送消息。
