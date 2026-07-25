# Workbench Browser PiP Dynamic Avoidance: Feasibility Study

[中文](browser-dynamic-layout-feasibility.md) ·
[English](browser-dynamic-layout-feasibility.en.md)

Date: 2026-07-22

> This is a historical pre-implementation feasibility study. The feature was
> subsequently implemented; see [Design QA](design-qa.md) for its latest
> acceptance status.

## Conclusion

The behavior was feasible without adding Electron IPC or changing the native
`WebContentsView` coordinate protocol.

The React layer already owned a DOM shell matching the native browser view.
During drag and resize, `WbcBrowserFloatingSurface` continuously updated
`{x, y, width, height}` and emitted `workbench:browser-layout`. `WbcMain` could
therefore compare the floating shell with the conversation viewport and narrow
only message rows that vertically intersected the browser.

Per-message avoidance was preferred over adding padding to the entire thread:
it affects only covered rows, while whole-thread padding narrows all history
and unnecessarily increases scroll height.

## Existing implementation anchors

- `src/workbench-webui/workbench-chat.jsx`
  - `wbcClampBrowserWindowFrame` defined the floating-window geometry.
  - `commitFrame` synchronized the DOM shell during drag/resize and emitted
    `workbench:browser-layout`.
  - `WbcMain` owned both the scroll container and the PiP host.
- `src/workbench-webui/workbench.css`
  - `.wbc-thread-stage` provided the positioning context.
  - `.wbc-thread` and `.wbc-browser-movement-region` shared content insets.
  - User messages were right-aligned and Agent messages left-aligned, so logical
    inline padding on a row wrapper could preserve alignment.
- `src/webui/static/app/browser-view.jsx`
  - `getBoundingClientRect()` already synchronized the browser host to Electron.
  - Drag events covered position-only updates that `ResizeObserver` cannot see.
- `electron/main.js`
  - The native layer only needed to continue consuming the existing
    `setBounds`; message layout did not belong in the main process.

## Recommended interaction rules

1. Enable avoidance only for visible PiP mode; clear it when minimized,
   maximized, or shown in the browser tab.
2. Measure the available width on both sides and choose the wider reading lane.
3. Enable avoidance only if that lane meets
   `min(360px, 45% of conversation width)`.
4. Affect only rows that vertically intersect the PiP, with 12–16px extra gap.
5. For a right-side PiP, add `padding-inline-end`; for a left-side PiP, add
   `padding-inline-start`.
6. Keep overlay behavior when the PiP is centered or both lanes are too narrow.
7. Preserve the first visible message and pixel offset while reading history;
   preserve bottom pinning when already at the bottom.

## Recommended implementation

- Wrap each direct conversation child in a lightweight `.wbc-thread-item` flex
  column so existing left/right message alignment remains intact.
- Add a `requestAnimationFrame`-coalesced scheduler in `WbcMain` watching:
  - `workbench:browser-layout`;
  - thread scrolling;
  - conversation-area `ResizeObserver`;
  - message count, streaming-height changes, and mode changes.
- Clear previous horizontal padding before each calculation and use unavoided
  geometry to prevent boundary oscillation.
- For long conversations, binary-search ordered row offsets and process only
  rows near the floating window.

## Rejected approaches

- Padding the entire `.wbc-thread`: simple but narrows every message.
- CSS `float`/`shape-outside`: incompatible with the flex-column conversation
  and the viewport-relative PiP without a risky layout rewrite.
- Electron-main-process avoidance: the native layer lacks message geometry and
  would require unnecessary two-way synchronization.
- CSS `order` or moving DOM nodes: changes reading and assistive-technology
  order.

## Risks and validation

- Narrower text increases row height, so scroll anchoring is required.
- Streaming Markdown, code, tables, and attachments must retain safe overflow;
  code blocks should keep local horizontal scrolling.
- Drag-time work needs RAF coalescing and visible-neighborhood limits.
- Narrow windows and 200% zoom should fall back to overlay mode.
- DOM order remains unchanged, but keyboard focus, screen-reader order, and
  `aria-live` streaming still require verification.

Recommended tests covered pure geometry (left/right/center/out-of-bounds,
narrow viewport, gap, and thresholds), frontend alignment regression, and
manual/E2E drag, eight-way resize, scrolling, streaming, long code, attachments,
sidebar toggling, minimize/maximize, and 200% zoom.

The original field evidence showed the right-side PiP covering Agent messages
and the covered region moving when the PiP was dragged left, without message
reflow. Those image files were local audit artifacts and are not part of the
repository.
