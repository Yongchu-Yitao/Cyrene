import { workbenchServices } from "../../shared/runtime/services.jsx"
var { useEffect: useWorkbenchEffect, useRef: useWorkbenchRef } = React;

var WB_RIGHT_MIN = 280;
// Width the flexible main/thread column must keep — keep this >= the CSS
// `minmax(320px, …)` floors so the right panel can never squeeze the grid past
// the viewport (which is what made text overflow when dragging).
var WB_MAIN_MIN = 340;
var WB_RIGHT_STORE = "wb-right-w";

// Viewport-based ceiling, used only as a safety clamp when restoring a stored
// width on load (the precise per-layout ceiling is computed live during drag).
function wbRightMaxWidth() {
  var vw = window.innerWidth || document.documentElement.clientWidth || 1280;
  return Math.max(WB_RIGHT_MIN, Math.min(640, Math.round(vw * 0.45)));
}

// A grid item can already be wider than the viewport when an old stored width
// is restored after the window shrinks. Measuring rect.width in that state
// makes the next drag inherit the overflow and permits an even larger width.
// Use only the part of the layout that is currently inside the viewport so the
// panel's maximum is derived from the visible workspace, not its overflow.
function wbVisibleLayoutWidth(layout) {
  var rect = layout.getBoundingClientRect();
  var viewportWidth = window.innerWidth || document.documentElement.clientWidth || rect.right;
  var visibleLeft = Math.max(0, rect.left);
  var visibleRight = Math.min(viewportWidth, rect.right);
  return Math.max(0, visibleRight - visibleLeft);
}

// Largest the right panel may grow without pushing the main column below
// WB_MAIN_MIN. Measures the actual conversation layout row so
// it works for both the collapsed and expanded rail.
function wbRightDynamicMax(panel) {
  var layout = panel.closest(".wbc-page") || panel.closest(".workbench-grid");
  if (!layout) return wbRightMaxWidth();
  var avail = wbVisibleLayoutWidth(layout);
  var leftFixed = 0;
  var mainMin = WB_MAIN_MIN;
  // Chat now renders its main conversation inside .wbc-pane-layout. Counting
  // every page child as fixed therefore deducted the entire main card and
  // collapsed the right panel's maximum to its 280px minimum. In the chat
  // grid only the rail track is fixed; the pane layout is the flexible lane.
  // Measure the pane's grid position instead of the visible rail element: the
  // compact rail is inset inside a wider track (48px inside 64px), so using its
  // border box lets the right panel overshoot the viewport by that difference.
  if (layout.classList.contains("wbc-page")) {
    var layoutRect = layout.getBoundingClientRect();
    var pane = Array.prototype.find.call(layout.children, function (child) {
      return child.classList && child.classList.contains("wbc-pane-layout");
    });
    var rail = Array.prototype.find.call(layout.children, function (child) {
      return child.classList && child.classList.contains("wbc-rail");
    });
    leftFixed = pane
      ? Math.max(0, pane.getBoundingClientRect().left - layoutRect.left)
      : (rail ? rail.getBoundingClientRect().width : 0);
    try {
      mainMin = parseFloat(window.getComputedStyle(layout).getPropertyValue("--wbc-main-min-width")) || WB_MAIN_MIN;
    } catch (err) {}
    return Math.max(WB_RIGHT_MIN, Math.round(avail - leftFixed - mainMin));
  }
  Array.prototype.forEach.call(layout.children, function (child) {
    if (child === panel) return;
    // the flexible main/thread column gets WB_MAIN_MIN reserved, not its current width
    if (child.classList.contains("workbench-main") || child.classList.contains("wbc-main")) return;
    leftFixed += child.getBoundingClientRect().width;
  });
  return Math.max(WB_RIGHT_MIN, Math.round(avail - leftFixed - mainMin));
}

// Stable ref callback (module scope = identity never changes, so React only
// runs it on mount/unmount of .workbench-grid — not on every re-render).
function wbApplyStoredRightWidth(node) {
  if (!node) return;
  try {
    var raw = localStorage.getItem(WB_RIGHT_STORE);
    if (!raw) return;
    var n = parseInt(raw, 10);
    if (!isFinite(n)) return;
    n = Math.max(WB_RIGHT_MIN, Math.min(wbRightMaxWidth(), n));
    var panel = node.querySelector(".wbc-page > .wbc-side") || node.querySelector(".workbench-right-panel");
    if (panel) n = Math.min(n, wbRightDynamicMax(panel));
    node.style.setProperty("--wb-right-w", n + "px");
  } catch (e) {}
}

// Drag handle pinned to the left edge of the rightmost panel. Shared by the
// conversation side panel (exposed on window for the
// separately-bundled workbench-chat.js).
function WbColResizer({ cardEdge, trackGutter, surfaceId }) {
  var handleRef = useWorkbenchRef(null);
  function resolvePanel(handle) {
    if (!handle) return null;
    var panel = handle.closest(".workbench-right-panel, .wbc-side");
    if (panel || !trackGutter) return panel;
    var page = handle.closest(".wbc-page");
    return page && page.querySelector(":scope > .wbc-side");
  }
  function emitResizePhase(phase, width) {
    try {
      window.dispatchEvent(new CustomEvent("workbench:right-resize", {
        detail: { phase: phase, width: Number.isFinite(width) ? width : undefined },
      }));
    } catch (err) {}
  }
  function onPointerDown(e) {
    if (e.button !== 0) return;
    e.preventDefault();
    var handle = e.currentTarget;
    var panel = resolvePanel(handle);
    var grid = handle.closest(".workbench-grid");
    if (!panel || !grid) return;
    var rightEdge = panel.getBoundingClientRect().right;
    var maxW = wbRightDynamicMax(panel);
    try { handle.setPointerCapture(e.pointerId); } catch (err) {}
    document.body.classList.add("wb-col-resizing");
    if (trackGutter) handle.classList.add("is-resizing");
    emitResizePhase("start");
    function onMove(ev) {
      var w = Math.round(rightEdge - ev.clientX);
      if (w < WB_RIGHT_MIN) w = WB_RIGHT_MIN;
      if (w > maxW) w = maxW;
      grid.style.setProperty("--wb-right-w", w + "px");
      // Keep dependants such as the browser PiP on the same pointer frame as
      // the context panel. ResizeObserver delivery alone trails fast drags.
      emitResizePhase("move", w);
    }
    function onUp() {
      handle.removeEventListener("pointermove", onMove);
      handle.removeEventListener("pointerup", onUp);
      handle.removeEventListener("pointercancel", onUp);
      document.body.classList.remove("wb-col-resizing");
      handle.classList.remove("is-resizing");
      emitResizePhase("end");
      try {
        var cur = parseInt(grid.style.getPropertyValue("--wb-right-w"), 10);
        if (isFinite(cur)) localStorage.setItem(WB_RIGHT_STORE, String(cur));
      } catch (err) {}
    }
    handle.addEventListener("pointermove", onMove);
    handle.addEventListener("pointerup", onUp);
    handle.addEventListener("pointercancel", onUp);
  }
  function onDoubleClick() {
    var grid = document.querySelector(".workbench-grid");
    if (grid) grid.style.removeProperty("--wb-right-w");
    try { localStorage.removeItem(WB_RIGHT_STORE); } catch (err) {}
  }
  function setSemanticWidth(input) {
    var handle = handleRef.current;
    var panel = resolvePanel(handle);
    var grid = handle && handle.closest(".workbench-grid");
    if (!panel || !grid) throw new Error("right panel separator is not available");
    var maxW = wbRightDynamicMax(panel);
    var minW = WB_RIGHT_MIN;
    var current = panel.getBoundingClientRect().width;
    var next;
    if (input && Number.isFinite(Number(input.value_ratio))) {
      var ratio = Math.max(0, Math.min(1, Number(input.value_ratio)));
      next = minW + ((maxW - minW) * ratio);
    } else {
      var delta = Number(input && input.delta_ratio);
      if (!Number.isFinite(delta)) throw new Error("delta_ratio or value_ratio is required");
      next = current + ((maxW - minW) * Math.max(-1, Math.min(1, delta)));
    }
    next = Math.max(minW, Math.min(maxW, Math.round(next)));
    grid.style.setProperty("--wb-right-w", next + "px");
    emitResizePhase("move", next);
    try { localStorage.setItem(WB_RIGHT_STORE, String(next)); } catch (err) {}
    return { width: next, minimum: minW, maximum: maxW };
  }
  useWorkbenchEffect(function () {
    if (!window.CyreneUI.has("uiSurface")) return undefined;
    var uiSurface = workbenchServices.uiSurface();
    return uiSurface.register({
      node_id: "right_panel_separator" + (surfaceId ? ":" + surfaceId : ""),
      parent_id: "root",
      scope: "main",
      get_node: function () {
        var handle = handleRef.current;
        var panel = resolvePanel(handle);
        return handle && handle.isConnected && panel ? {
          role: "separator",
          name: workbenchServices.i18n().t("rail.resizeHandle", null, "Right panel width"),
          value_summary: String(Math.round(panel.getBoundingClientRect().width)),
          state: { orientation: "vertical" },
        } : null;
      },
      actions: [
        { action_id: "adjust", kind: "adjust", risk: "R1", gesture_aliases: ["pointer_resize", "arrow_key"], input_schema: { delta_ratio: "-1..1" } },
        { action_id: "set_value", kind: "adjust", risk: "R1", gesture_aliases: ["pointer_resize"], input_schema: { value_ratio: "0..1" } },
        { action_id: "reset_size", kind: "invoke", risk: "R1", gesture_aliases: ["double_press"] },
      ],
      handlers: {
        adjust: setSemanticWidth,
        set_value: setSemanticWidth,
        reset_size: onDoubleClick,
      },
    });
  }, [cardEdge, trackGutter, surfaceId]);
  function emitResizeHint(active) {
    // The chat panel embeds the hit target in its floating card. Its own border
    // is the resize affordance, so do not draw the legacy full-height guide.
    if (cardEdge || trackGutter) return;
    document.body.classList.toggle("wb-col-resize-hover", active === true);
    try {
      window.dispatchEvent(new CustomEvent("workbench:right-resize-hint", {
        detail: { active: active === true },
      }));
    } catch (err) {}
  }
  var title = workbenchServices.i18n().t(
    "rail.resizeHandle",
    null,
    "Drag to resize",
  );
  return (
    <div
      ref={handleRef}
      className={"wb-col-resizer" + (cardEdge ? " card-edge" : "") + (trackGutter ? " track-gutter" : "")}
      role="separator"
      aria-orientation="vertical"
      title={title}
      onPointerDown={onPointerDown}
      onPointerEnter={function () { emitResizeHint(true); }}
      onPointerLeave={function () { emitResizeHint(false); }}
      onDoubleClick={onDoubleClick}
    />
  );
}

export { WbColResizer, wbApplyStoredRightWidth }
