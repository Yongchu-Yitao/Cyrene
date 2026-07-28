# Workbench 浏览器浮窗动态避让：可行性研究

[中文](browser-dynamic-layout-feasibility.md) ·
[English](browser-dynamic-layout-feasibility.en.md)

日期：2026-07-22

> 这是实现前的历史可行性研究。功能后续已经落地；最终设计验收状态见
> [设计 QA](design-qa.zh-CN.md)。

## 结论

可以实现，且不需要新增 Electron IPC 或修改原生 `WebContentsView` 的坐标协议。

当前 React 层已经持有与原生浏览器窗口一致的 DOM 外壳；拖动和缩放时，`WbcBrowserFloatingSurface` 会持续更新 `{x, y, width, height}` 并广播 `workbench:browser-layout`。因此，可以直接在 `WbcMain` 中根据浮窗外壳和对话区的矩形关系，只收窄与浮窗垂直相交的消息行。

推荐做“按消息行避让”，不推荐给整个对话区统一加左右内边距。前者只影响被遮住的几行；后者会让整段历史消息都变窄，并显著增加滚动高度。

## 当前结构中的实现支点

- `src/webui/frontend/workbench-chat.jsx`
  - `wbcClampBrowserWindowFrame` 已定义浮窗的几何模型。
  - `commitFrame` 已在拖动/缩放时同步 DOM 外壳，并派发 `workbench:browser-layout`。
  - `WbcMain` 同时拥有滚动容器和浮窗宿主，是计算避让的合适边界。
- `src/webui/frontend/workbench.css`
  - `.wbc-thread-stage` 是定位上下文。
  - `.wbc-thread` 与 `.wbc-browser-movement-region` 使用相同的内容 inset，坐标可直接换算。
  - 用户消息右对齐、Agent 消息左对齐，适合通过消息行 wrapper 的 `padding-inline-start/end` 保留原有对齐语义。
- `src/webui/frontend/shared/browser/viewport.jsx`
  - 已通过 `getBoundingClientRect()` 将浏览器宿主矩形同步到 Electron。
  - 拖动事件已经额外覆盖了仅发生位移、`ResizeObserver` 看不到的场景。
- `electron/main.js`
  - 原生层只需继续消费既有 `setBounds`；动态排版不必下沉到这里。

## 推荐交互规则

1. 在 PiP 或 42px 最小化浮窗可见时启用避让；最大化和右侧浏览器页签模式清空避让。
2. 计算浮窗左右两侧的可用宽度，选择更宽的一侧作为阅读通道。
3. 只有通道宽度达到最小可读阈值时才启用，建议阈值为 `min(360px, 对话区宽度的 45%)`。
4. 只处理与 PiP/最小化浮窗垂直范围相交的消息行，并额外保留 12–16px 间距。
5. 浮窗在右侧时，为相交消息行增加 `padding-inline-end`；浮窗在左侧时增加 `padding-inline-start`。
6. 浮窗接近正中、两侧都不够宽时保持覆盖式布局，不把文本压成过窄的列；后续可考虑自动最小化作为独立策略。
7. 用户正在阅读历史消息时，记录首个可见消息及像素偏移并在重排后恢复；位于底部时继续保持贴底。

## 推荐实现形态

- 为每个对话直接子项增加轻量的 `.wbc-thread-item` wrapper。wrapper 使用 flex column，因此内部用户消息仍然右对齐、Agent 消息仍然左对齐。
- 在 `WbcMain` 中增加一个基于 `requestAnimationFrame` 的排版调度器，监听：
  - `workbench:browser-layout`
  - 对话滚动
  - `ResizeObserver`（对话区尺寸）
  - 消息数量、流式回复高度变化、模式切换
- 每次计算先同步清空上一轮的横向 padding，再基于未避让时的稳定几何计算相交项，避免边界处来回抖动。
- 长对话可根据 `offsetTop/offsetHeight` 对有序消息行做二分定位，只处理浮窗纵向附近的少量行，避免拖动时扫描全部历史消息。

## 不推荐的方案

- 整个 `.wbc-thread` 统一加右侧或左侧 padding：实现简单，但所有消息都会变窄。
- CSS `float/shape-outside`：当前对话是 flex column，且浮窗相对滚动视口固定；需要改写主布局模型，风险高。
- 把避让逻辑放到 Electron 主进程：原生层不知道消息行几何，会制造不必要的双向 IPC 和同步问题。
- 使用 CSS `order` 或移动 DOM 节点：会影响阅读顺序与键盘/辅助技术体验。

## 风险与验证重点

- 文本收窄后高度增加，可能改变滚动位置；必须做滚动锚点保护。
- 流式 Markdown、代码块、表格和附件卡片要验证横向溢出；代码块应继续局部横向滚动。
- 拖动期间每帧重排要做 RAF 合并，并限制为可见附近消息。
- 200% 缩放或窄窗口下应触发“不避让”阈值，避免不可读的窄列。
- DOM 顺序不改变，但仍需验证键盘焦点、屏幕阅读顺序和 `aria-live` 流式状态。

## 建议测试

- 纯函数：浮窗在左、右、中央、越界、窗口过窄、不同 gap/阈值。
- 前端回归：用户消息和 Agent 消息的 wrapper 与原对齐样式仍然存在。
- 手动/E2E：左右拖动、八向缩放、对话滚动、流式回复、长代码块、附件、侧栏开关、最小化/最大化、200% 缩放。

## 现场证据

- `01-browser-pip-over-chat.jpeg`：浮窗位于右侧，覆盖多段 Agent 回复。
- `02-browser-pip-left.jpeg`：浮窗拖到左侧后，覆盖区域随位置变化，但对话仍没有重排。
