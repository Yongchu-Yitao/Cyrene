// Four-column Project / Task Session workbench.
var {
  useState: useWorkbenchState,
  useEffect: useWorkbenchEffect,
  useMemo: useWorkbenchMemo,
  useRef: useWorkbenchRef,
} = React;

// Native WebContentsView instances live above the renderer's CSS stacking
// context. Keep a shared count of renderer overlays that must cover it, so a
// popover can safely overlap another modal without restoring the native view
// too early.
var wbBrowserOverlayCount = 0;
function wbSetBrowserOverlayObscured(delta) {
  wbBrowserOverlayCount = Math.max(0, wbBrowserOverlayCount + delta);
  var bridge = window.cyrene && window.cyrene.browser;
  if (bridge && typeof bridge.setObscured === "function") {
    bridge.setObscured(wbBrowserOverlayCount > 0).catch(function (err) {
      console.error("setObscured failed", err);
    });
  }
}

// Tag the host platform on <html> so CSS can reserve the macOS traffic-light
// gutter only where it actually exists. window.cyrene.platform comes from the
// Electron preload ('darwin' | 'win32' | 'linux'); fall back to 'web' in a
// plain browser. Runs at script load, before React paints the topbar, so macOS
// gets the gutter with no flash and other platforms never reserve dead space.
(function tagWorkbenchPlatform() {
  try {
    document.documentElement.dataset.platform =
      (window.cyrene && window.cyrene.platform) || "web";
  } catch (e) {}
})();

// Document-level file drop target used by the task, conversation and knowledge
// pages. Listening on document makes the whole visible module accept files,
// including its rail and side panels, while the ref keeps the listener stable
// across renders and avoids stale upload callbacks.
function useWorkbenchFileDrop(onFiles, enabled) {
  var [active, setActive] = React.useState(false);
  var callbackRef = React.useRef(onFiles);
  var depthRef = React.useRef(0);
  callbackRef.current = onFiles;

  React.useEffect(function () {
    if (!enabled) {
      depthRef.current = 0;
      setActive(false);
      return undefined;
    }

    function hasFiles(event) {
      var transfer = event && event.dataTransfer;
      if (!transfer) return false;
      var types = Array.prototype.slice.call(transfer.types || []);
      return types.indexOf("Files") >= 0;
    }
    function onDragEnter(event) {
      if (!hasFiles(event)) return;
      event.preventDefault();
      depthRef.current += 1;
      setActive(true);
    }
    function onDragOver(event) {
      if (!hasFiles(event)) return;
      event.preventDefault();
      if (event.dataTransfer) event.dataTransfer.dropEffect = "copy";
      setActive(true);
    }
    function onDragLeave(event) {
      if (!hasFiles(event)) return;
      event.preventDefault();
      depthRef.current = Math.max(0, depthRef.current - 1);
      if (depthRef.current === 0) setActive(false);
    }
    function reset() {
      depthRef.current = 0;
      setActive(false);
    }
    function onDrop(event) {
      if (!hasFiles(event)) return;
      event.preventDefault();
      reset();
      var files = event.dataTransfer && event.dataTransfer.files;
      if (files && files.length && callbackRef.current) callbackRef.current(files);
    }

    document.addEventListener("dragenter", onDragEnter);
    document.addEventListener("dragover", onDragOver);
    document.addEventListener("dragleave", onDragLeave);
    document.addEventListener("drop", onDrop);
    window.addEventListener("blur", reset);
    return function () {
      document.removeEventListener("dragenter", onDragEnter);
      document.removeEventListener("dragover", onDragOver);
      document.removeEventListener("dragleave", onDragLeave);
      document.removeEventListener("drop", onDrop);
      window.removeEventListener("blur", reset);
    };
  }, [!!enabled]);

  return active;
}

function WorkbenchFileDropOverlay({ label, busy }) {
  return (
    <div className="wb-file-drop-overlay" role="status" aria-live="polite">
      <div className="wb-file-drop-card">
        <svg viewBox="0 0 24 24" width="30" height="30" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
          <path d="M12 16V4" />
          <path d="m7 9 5-5 5 5" />
          <path d="M5 14v4a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2v-4" />
        </svg>
        <b>{busy ? wbT("workbenchChat.uploading", "Uploading...") : label}</b>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Non-blocking feedback: toasts + confirm dialogs that replace window.alert /
// window.confirm. Native dialogs freeze the page (fatal while a task streams)
// and ignore the workbench dark theme. The store lives outside React so it can
// be driven imperatively from anywhere — including promise .catch() handlers
// far from any component — through window.showToast() / window.confirmModal().
// A single <WorkbenchFeedbackHost/> mounted in the shell renders the queue.
// ---------------------------------------------------------------------------
var WorkbenchFeedbackStore = (function () {
  var toasts = [];
  var confirms = [];
  var listeners = [];
  var seq = 0;
  function emit() {
    for (var i = 0; i < listeners.length; i++) {
      try { listeners[i](); } catch (e) { /* host unmounted */ }
    }
  }
  function subscribe(fn) {
    listeners.push(fn);
    return function () { listeners = listeners.filter(function (l) { return l !== fn; }); };
  }
  function showToast(message, type, opts) {
    opts = opts || {};
    var id = ++seq;
    var kind = type || "info";
    var duration = opts.duration != null ? opts.duration : (kind === "error" ? 6000 : 3200);
    toasts = toasts.concat([{ id: id, message: message == null ? "" : String(message), type: kind, duration: duration }]);
    emit();
    if (duration > 0) setTimeout(function () { dismissToast(id); }, duration);
    return id;
  }
  function dismissToast(id) {
    var next = toasts.filter(function (toast) { return toast.id !== id; });
    if (next.length !== toasts.length) { toasts = next; emit(); }
  }
  function confirmModal(opts) {
    if (typeof opts === "string") opts = { body: opts };
    opts = opts || {};
    return new Promise(function (resolve) {
      var id = ++seq;
      confirms = confirms.concat([{
        id: id,
        title: opts.title || "",
        body: opts.body || "",
        confirmLabel: opts.confirmLabel || "",
        cancelLabel: opts.cancelLabel || "",
        danger: !!opts.danger,
        resolve: resolve,
      }]);
      emit();
    });
  }
  function resolveConfirm(id, value) {
    var item = null;
    confirms = confirms.filter(function (c) { if (c.id === id) { item = c; return false; } return true; });
    if (item) { emit(); item.resolve(value); }
  }
  return {
    subscribe: subscribe,
    snapshot: function () { return { toasts: toasts, confirms: confirms }; },
    showToast: showToast,
    dismissToast: dismissToast,
    confirmModal: confirmModal,
    resolveConfirm: resolveConfirm,
  };
})();

// Imperative entry points usable from any script / promise handler.
window.showToast = function (message, type, opts) { return WorkbenchFeedbackStore.showToast(message, type, opts); };
window.confirmModal = function (opts) { return WorkbenchFeedbackStore.confirmModal(opts); };

function wbToastIcon(type) {
  if (type === "error") {
    return <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><circle cx="12" cy="12" r="9" /><path d="M12 8v5M12 16.5v.01" /></svg>;
  }
  if (type === "success") {
    return <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><circle cx="12" cy="12" r="9" /><path d="m8.5 12 2.5 2.5 4.5-5" /></svg>;
  }
  if (type === "warning") {
    return <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M10.3 4.3 2.5 18a1.8 1.8 0 0 0 1.6 2.7h15.8A1.8 1.8 0 0 0 21.5 18L13.7 4.3a1.9 1.9 0 0 0-3.4 0Z" /><path d="M12 9.5v4M12 17v.01" /></svg>;
  }
  return <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><circle cx="12" cy="12" r="9" /><path d="M12 11v5M12 7.5v.01" /></svg>;
}

function WorkbenchFeedbackHost() {
  var [, setTick] = useWorkbenchState(0);
  useWorkbenchEffect(function () {
    return WorkbenchFeedbackStore.subscribe(function () { setTick(function (n) { return (n + 1) % 1000000; }); });
  }, []);
  var snap = WorkbenchFeedbackStore.snapshot();
  var toasts = snap.toasts;
  var active = snap.confirms.length ? snap.confirms[0] : null;

  // Keyboard: Enter confirms, Esc cancels. Capture phase + stopImmediatePropagation
  // so an underlying overlay (e.g. settings) can't also react to the same Esc.
  useWorkbenchEffect(function () {
    if (!active) return undefined;
    function onKey(e) {
      if (e.key === "Escape") {
        e.preventDefault(); e.stopImmediatePropagation();
        WorkbenchFeedbackStore.resolveConfirm(active.id, false);
      } else if (e.key === "Enter") {
        e.preventDefault(); e.stopImmediatePropagation();
        WorkbenchFeedbackStore.resolveConfirm(active.id, true);
      }
    }
    document.addEventListener("keydown", onKey, true);
    return function () { document.removeEventListener("keydown", onKey, true); };
  }, [active ? active.id : 0]);

  return (
    <>
      {toasts.length ? (
        <div className="workbench-toast-host" aria-live="polite">
          {toasts.map(function (toast) {
            return (
              <div key={toast.id} className={"workbench-toast is-" + toast.type} role="status">
                <span className="workbench-toast-icon">{wbToastIcon(toast.type)}</span>
                <span className="workbench-toast-msg">{toast.message}</span>
                <button
                  type="button"
                  className="workbench-toast-close"
                  onClick={function () { WorkbenchFeedbackStore.dismissToast(toast.id); }}
                  aria-label={wbT("common.close", "Close")}
                >
                  <svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round"><path d="m6 6 12 12M18 6 6 18" /></svg>
                </button>
              </div>
            );
          })}
        </div>
      ) : null}
      {active ? (
        <div className="workbench-confirm-scrim" onMouseDown={function (e) { if (e.target === e.currentTarget) WorkbenchFeedbackStore.resolveConfirm(active.id, false); }}>
          <div className="workbench-confirm-modal" role="alertdialog" aria-modal="true">
            {active.title ? <div className="workbench-confirm-title">{active.title}</div> : null}
            <div className="workbench-confirm-body">{active.body}</div>
            <div className="workbench-confirm-foot">
              <button type="button" className="wb-btn ghost" onClick={function () { WorkbenchFeedbackStore.resolveConfirm(active.id, false); }}>
                {active.cancelLabel || wbT("common.cancel", "Cancel")}
              </button>
              <button type="button" className={"wb-btn " + (active.danger ? "danger" : "primary")} autoFocus onClick={function () { WorkbenchFeedbackStore.resolveConfirm(active.id, true); }}>
                {active.confirmLabel || wbT("common.confirm", "Confirm")}
              </button>
            </div>
          </div>
        </div>
      ) : null}
    </>
  );
}

// Help center external destinations (kept in sync with the About section in
// settings-overlay.jsx).
var WB_HELP_DOCS_URL = "https://github.com/ikerrrrrrrrrrr/Cyrene#readme";
var WB_HELP_FEEDBACK_URL = "https://github.com/ikerrrrrrrrrrr/Cyrene/issues/new";

// Platform-aware keyboard shortcut rendering. Mac shows ⌘/⇧/⌥/⌃ glyphs; other
// platforms fall back to Ctrl/Shift/Alt text so the help center mirrors whatever
// modifier the user's OS actually uses.
function wbIsMacPlatform() {
  try {
    var nav = window.navigator || {};
    var uaData = nav.userAgentData;
    if (uaData && uaData.platform) return /mac/i.test(uaData.platform);
    if (nav.platform) return /mac|iphone|ipad|ipod/i.test(nav.platform);
    return /mac|iphone|ipad|ipod/i.test(nav.userAgent || "");
  } catch (e) {
    return false;
  }
}

function wbShortcutKey(token, isMac) {
  if (token === "mod") return isMac ? "⌘" : "Ctrl";
  if (token === "shift") return isMac ? "⇧" : "Shift";
  if (token === "alt") return isMac ? "⌥" : "Alt";
  if (token === "ctrl") return isMac ? "⌃" : "Ctrl";
  return token;
}

function wbArgsPreview(args) {
  if (!args || typeof args !== "object") return "";
  var parts = [];
  Object.keys(args).forEach(function (key) {
    if (parts.length >= 2) return;
    var value = args[key];
    if (value == null || value === "") return;
    var text = String(value).replace(/\s+/g, " ").trim();
    if (!text) return;
    if (text.length > 50) text = text.slice(0, 47) + "...";
    parts.push(text);
  });
  return parts.join("  ").slice(0, 80);
}

function wbActorLabel(caller, agentId) {
  var aid = String(agentId || "").trim();
  if (aid) return aid;
  var raw = String(caller || "").trim();
  if (raw.indexOf("subagent_") === 0) return raw.slice("subagent_".length) || raw;
  if (raw === "main_agent") return "main agent";
  return raw || "agent";
}

function wbSubagentStatusText(status) {
  var map = {
    running: "正在执行",
    resumed: "恢复执行",
    waiting: "等待其他 subagent",
    done: "已完成",
    timeout: "已超时",
    error: "执行失败",
  };
  return map[String(status || "").trim()] || String(status || "状态更新");
}

function wbLiveEventFromSse(data) {
  if (!data || !data.type) return null;
  var createdAt = data.timestamp || new Date().toISOString();
  if (data.type === "tool_call") {
    var toolName = String(data.tool || "").trim();
    if (!toolName) return null;
    var actor = wbActorLabel(data.caller);
    return {
      id: data.event_id || ("live_tool_" + createdAt + "_" + toolName),
      type: "ToolCallEvent",
      createdAt: createdAt,
      tool: toolName,
      actor: actor,
      argsPreview: wbArgsPreview(data.args),
      body: actor + " 调用工具 " + toolName,
      live: true,
    };
  }
  if (data.type === "llm_call") {
    var actor2 = wbActorLabel(data.caller);
    var phase = String(data.phase || "").trim();
    var llmStatus = String(data.status || "completed").trim();
    return {
      id: data.event_id || ("live_llm_" + createdAt + "_" + actor2),
      type: "LlmCallEvent",
      createdAt: createdAt,
      actor: actor2,
      phase: phase,
      model: String(data.model || ""),
      body: llmStatus === "started" ? actor2 + " 正在思考…" : actor2 + " 完成一轮思考",
      live: true,
    };
  }
  if (data.type === "subagent_update") {
    var actor3 = wbActorLabel("", data.agent_id);
    var task = String(data.task || "").trim();
    return {
      id: data.event_id || ("live_subagent_" + actor3 + "_" + createdAt),
      type: "SubagentStatusEvent",
      createdAt: createdAt,
      actor: actor3,
      status: String(data.status || ""),
      body: actor3 + " " + wbSubagentStatusText(data.status)
        + (data.message ? "：" + String(data.message).slice(0, 180) : (task ? "：" + task.slice(0, 120) : "")),
      live: true,
    };
  }
  return null;
}

function wbMergeLiveEventIntoSession(session, event) {
  if (!session || !event) return session;
  var events = Array.isArray(session.events) ? session.events.slice() : [];
  if (!events.some(function (item) { return item && item.id === event.id; })) {
    events.push(event);
    if (events.length > 240) events = events.slice(events.length - 240);
  }
  var updatedPlan = Array.isArray(session.plan) ? session.plan.map(function (step) {
    if (!step || step.status !== "running") return step;
    var progressEvents = Array.isArray(step.progressEvents) ? step.progressEvents.slice() : [];
    if (!progressEvents.some(function (item) { return item && item.id === event.id; })) {
      progressEvents.push({ id: event.id, time: event.createdAt, body: event.body });
      if (progressEvents.length > 30) progressEvents = progressEvents.slice(progressEvents.length - 30);
    }
    return Object.assign({}, step, {
      currentAction: event.body || step.currentAction || "",
      progressEvents: progressEvents,
      updatedAt: event.createdAt || new Date().toISOString(),
    });
  }) : session.plan;
  return Object.assign({}, session, { events: events, plan: updatedPlan });
}

// The live activity feed shown inside the "Agent 正在处理" card. Two sources,
// unified to {id, time, body}: a running plan step accumulates its own
// progressEvents (step execution); a non-step background op (规划 / 反思 / 验收)
// has no running step, so we pull the session-level live events that arrived
// after the op began. Capped to the most recent lines so the feed stays tight.
function wbLiveActivityLines(session, runningStep, busyOp) {
  if (runningStep && Array.isArray(runningStep.progressEvents) && runningStep.progressEvents.length) {
    return runningStep.progressEvents.slice(-14);
  }
  var since = busyOp && busyOp.startedAt ? String(busyOp.startedAt) : "";
  var events = Array.isArray(session.events) ? session.events : [];
  var out = [];
  for (var i = 0; i < events.length; i++) {
    var e = events[i];
    if (!e || !e.live) continue;
    if (["ToolCallEvent", "LlmCallEvent", "SubagentStatusEvent"].indexOf(e.type) < 0) continue;
    if (since && String(e.createdAt || "") < since) continue;
    out.push({ id: e.id, time: e.createdAt, body: e.body });
  }
  return out.slice(-14);
}

// True when an unread notification points at whatever the user is *currently*
// looking at (the open conversation, or the active task session) and the window
// is actually visible — i.e. the user has already seen the underlying message,
// so it should not surface as a brand-new unread item.
function wbNotificationOnScreen(item, view) {
  if (!item || item.read || !view) return false;
  if (typeof document !== "undefined" && document.hidden) return false;
  var meta = (item && item.meta) || {};
  if (view.page === "chat") {
    return !!meta.chatId && meta.chatId === view.chatId;
  }
  if (!view.page) { // default task view
    return !!meta.sessionId && meta.sessionId === view.sessionId;
  }
  return false;
}

// Given a freshly-fetched notifications payload, silently mark-as-read any item
// the user is already seeing and return an adjusted payload whose unread counts
// exclude them — so the badge never blinks for on-screen content. Counts are
// decremented (not recomputed) because the server's totals span items beyond the
// returned page / active tab filter.
function wbSuppressOnScreenNotifications(payload, view, model) {
  if (!payload || !Array.isArray(payload.items)) return payload;
  var hidden = payload.items.filter(function (item) { return wbNotificationOnScreen(item, view); });
  if (!hidden.length) return payload;
  var hideIds = hidden.map(function (item) { return item.id; });
  try { if (model && model.markNotificationsRead) model.markNotificationsRead(hideIds, false); } catch (e) {}
  var hideSet = {};
  hideIds.forEach(function (id) { hideSet[id] = true; });
  var items = payload.items.map(function (item) {
    return hideSet[item.id] ? Object.assign({}, item, { read: true }) : item;
  });
  var unreadByTab = Object.assign({ all: 0, mention: 0, comment: 0, system: 0 }, payload.unreadByTab || {});
  unreadByTab.all = Math.max(0, Number(unreadByTab.all || 0) - hidden.length);
  hidden.forEach(function (item) {
    var key = String((item && item.tab) || "");
    if (key && key !== "all" && unreadByTab[key] != null) unreadByTab[key] = Math.max(0, unreadByTab[key] - 1);
  });
  return Object.assign({}, payload, {
    items: items,
    unreadCount: Math.max(0, Number(payload.unreadCount || 0) - hidden.length),
    unreadByTab: unreadByTab,
  });
}

// Right-panel resize plumbing -------------------------------------------------
// The rightmost column width is stored in --wb-right-w on .workbench-grid and
// consumed by both the task grid (column 4) and the chat .wbc-page (column 3).
// Width lives in the DOM + localStorage rather than React state so a streaming
// re-render never fights an in-progress drag.
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

// Largest the right panel may grow without pushing the main column below
// WB_MAIN_MIN. Measures the actual layout row (task grid OR chat .wbc-page) so
// it works for both the collapsed and expanded rail.
function wbRightDynamicMax(panel) {
  var layout = panel.closest(".wbc-page") || panel.closest(".workbench-grid");
  if (!layout) return wbRightMaxWidth();
  var avail = layout.getBoundingClientRect().width;
  var leftFixed = 0;
  Array.prototype.forEach.call(layout.children, function (child) {
    if (child === panel) return;
    // the flexible main/thread column gets WB_MAIN_MIN reserved, not its current width
    if (child.classList.contains("workbench-main") || child.classList.contains("wbc-main")) return;
    leftFixed += child.getBoundingClientRect().width;
  });
  return Math.max(WB_RIGHT_MIN, Math.round(avail - leftFixed - WB_MAIN_MIN));
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
    node.style.setProperty("--wb-right-w", n + "px");
  } catch (e) {}
}

// Drag handle pinned to the left edge of the rightmost panel. Shared by the
// task context panel and the chat side panel (exposed on window for the
// separately-bundled workbench-chat.js).
function WbColResizer() {
  function emitResizePhase(phase) {
    try {
      window.dispatchEvent(new CustomEvent("workbench:right-resize", { detail: { phase: phase } }));
    } catch (err) {}
  }
  function onPointerDown(e) {
    if (e.button !== 0) return;
    e.preventDefault();
    var handle = e.currentTarget;
    var panel = handle.closest(".workbench-right-panel, .wbc-side");
    var grid = handle.closest(".workbench-grid");
    if (!panel || !grid) return;
    var rightEdge = panel.getBoundingClientRect().right;
    var maxW = wbRightDynamicMax(panel);
    try { handle.setPointerCapture(e.pointerId); } catch (err) {}
    document.body.classList.add("wb-col-resizing");
    emitResizePhase("start");
    function onMove(ev) {
      var w = Math.round(rightEdge - ev.clientX);
      if (w < WB_RIGHT_MIN) w = WB_RIGHT_MIN;
      if (w > maxW) w = maxW;
      grid.style.setProperty("--wb-right-w", w + "px");
    }
    function onUp() {
      handle.removeEventListener("pointermove", onMove);
      handle.removeEventListener("pointerup", onUp);
      handle.removeEventListener("pointercancel", onUp);
      document.body.classList.remove("wb-col-resizing");
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
  var title = window.workbenchT
    ? window.workbenchT("rail.resizeHandle", null, "Drag to resize · double-click to reset")
    : "Drag to resize";
  return (
    <div
      className="wb-col-resizer"
      role="separator"
      aria-orientation="vertical"
      title={title}
      onPointerDown={onPointerDown}
      onDoubleClick={onDoubleClick}
    />
  );
}
window.WbColResizer = WbColResizer;

function WorkbenchApp({ theme, actualTheme, onToggleTheme, needsOnboarding }) {
  useDataVersion();
  var workbenchI18n = window.useWorkbenchI18n();
  var t = workbenchI18n.t;
  var model = window.WorkbenchModel;
  var [store, setStore] = useWorkbenchState(function () {
    return model.normalizeStore({ projects: [] });
  });
  var [loading, setLoading] = useWorkbenchState(true);
  var [error, setError] = useWorkbenchState("");
  var [fullPage, setFullPage] = useWorkbenchState(function () {
    try {
      // Returning users resume their last page. First-time users (no page ever
      // stored AND never welcomed) land on the welcome / get-started page — it
      // is auto-detected here rather than opened from a rail button.
      var stored = localStorage.getItem("wb-active-page");
      // "welcome" must never be treated as a resumable page. Older builds wrongly
      // persisted it here, trapping users on the welcome screen every relaunch —
      // ignore a stale "welcome" value so those installs fall through to the
      // workspace instead of re-opening onboarding's get-started page.
      if (stored && stored !== "welcome") return stored;
      if (!localStorage.getItem("cyrene-workbench-welcomed")) return "welcome";
      return null;
    } catch (e) { return null; }
  });
  // The task entry point is a project-wide board. A task detail is opened only
  // after the user selects a card (or follows a direct task link/search hit).
  var [taskView, setTaskView] = useWorkbenchState("board");
  var [rightTab, setRightTab] = useWorkbenchState("context");
  var [railCollapsed, setRailCollapsed] = useWorkbenchState(function () {
    // Default to collapsed (icon strip); honour the user's stored choice once set.
    try {
      var v = localStorage.getItem("wb-rail-collapsed");
      return v === null ? true : v === "1";
    } catch (e) { return true; }
  });
  var [expandedStepId, setExpandedStepId] = useWorkbenchState("");
  var [searchOpen, setSearchOpen] = useWorkbenchState(false);
  var [settingsOpen, setSettingsOpen] = useWorkbenchState(false);
  var [settingsTab, setSettingsTab] = useWorkbenchState("");
  var [newProjectOpen, setNewProjectOpen] = useWorkbenchState(false);
  var [newTaskOpen, setNewTaskOpen] = useWorkbenchState(false);
  var [mountedPages, setMountedPages] = useWorkbenchState({});
  var [editProject, setEditProject] = useWorkbenchState(null);
  var [chatCrumb, setChatCrumb] = useWorkbenchState("");
  var [notifications, setNotifications] = useWorkbenchState({ items: [], counts: { all: 0, mention: 0, comment: 0, system: 0 }, unreadByTab: { all: 0, mention: 0, comment: 0, system: 0 }, unreadCount: 0 });
  var [activeChatId, setActiveChatId] = useWorkbenchState("");
  // Always-fresh snapshot of what the user is looking at, read inside async
  // notification callbacks (interval / SSE closures captured once on mount).
  var activeViewRef = useWorkbenchRef({ page: null, taskView: "board", chatId: "", sessionId: "" });
  var sessionLoadSeqRef = useWorkbenchRef(0);
  var menuActionsRef = useWorkbenchRef({ createProject: function () {}, createSession: function () {}, onToggleTheme: function () {} });

  function reloadNotifications(tab, limit) {
    var activeView = activeViewRef.current;
    var visibleView = null;
    if (typeof document === "undefined" || !document.hidden) {
      if (activeView.page === "chat" && activeView.chatId) {
        visibleView = { chatId: activeView.chatId };
      } else if (!activeView.page && activeView.sessionId) {
        visibleView = { sessionId: activeView.sessionId };
      }
    }
    return model.fetchNotifications(tab || "all", limit || 80, visibleView).then(function (payload) {
      payload = wbSuppressOnScreenNotifications(payload, activeViewRef.current, model);
      setNotifications({
        items: Array.isArray(payload.items) ? payload.items : [],
        counts: payload.counts || { all: 0, mention: 0, comment: 0, system: 0 },
        unreadByTab: payload.unreadByTab || { all: 0, mention: 0, comment: 0, system: 0 },
        unreadCount: Number(payload.unreadCount || 0),
      });
      return payload;
    }).catch(function () {});
  }

  function reloadWorkbench(nextProjectId, nextSessionId, options) {
    options = options || {};
    var showLoading = options.showLoading !== false;
    if (showLoading) setLoading(true);
    setError("");
    return model.fetchProjects()
      .then(function (next) {
        if (nextProjectId) next.activeProjectId = nextProjectId;
        var project = next.projects.find(function (item) { return item.id === next.activeProjectId; }) || next.activeProject;
        if (project) {
          next.activeProject = project;
          if (nextSessionId) next.activeSessionId = nextSessionId;
          next.activeSession = project.sessions.find(function (item) { return item.id === next.activeSessionId; }) || project.sessions[0] || null;
          next.activeSessionId = next.activeSession ? next.activeSession.id : "";
        }
        setStore(next);
        return next;
      })
      .catch(function (err) {
        setError(err.message || String(err));
      })
      .finally(function () {
        if (showLoading) setLoading(false);
      });
  }

  // Board refreshes must not discard a fully-loaded active task. The summary
  // endpoint is intentionally lightweight, so merge its project/session list
  // around the user's current selection and let task detail lazy-loading keep
  // ownership of the full payload.
  function refreshTaskBoard() {
    return model.fetchProjects().then(function (next) {
      setStore(function (prev) {
        var activeProjectId = (prev && prev.activeProjectId) || next.activeProjectId;
        var activeProject = (next.projects || []).find(function (project) {
          return project && String(project.id || "") === String(activeProjectId || "");
        }) || next.activeProject;
        var activeSessionId = (prev && prev.activeSessionId) || next.activeSessionId;
        var activeSession = activeProject && (activeProject.sessions || []).find(function (session) {
          return session && String(session.id || "") === String(activeSessionId || "");
        });
        if (!activeSession && activeProject) activeSession = (activeProject.sessions || [])[0] || null;
        return Object.assign({}, next, {
          activeProjectId: activeProject ? activeProject.id : "",
          activeProject: activeProject || null,
          activeSessionId: activeSession ? activeSession.id : "",
          activeSession: activeSession || null,
        });
      });
      return next;
    }).catch(function () {
      // The board keeps the last known state during a transient refresh error;
      // explicit task actions still surface their own errors to the user.
      return null;
    });
  }

  function mergeSessionPayload(prev, payload) {
    if (!prev || !payload || !payload.session) return prev;
    var fullSession = Object.assign({}, payload.session, { isSummary: false });
    // A silent refresh can arrive while SSE activity is still streaming. Keep
    // live runtime entries so the run-log panel does not blink back to an old
    // snapshot between two subagent updates.
    var priorSession = prev.activeSession && String(prev.activeSession.id || "") === String(fullSession.id || "")
      ? prev.activeSession : null;
    if (priorSession && Array.isArray(priorSession.events)) {
      var persistedEvents = Array.isArray(fullSession.events) ? fullSession.events.slice() : [];
      var seenEventIds = {};
      persistedEvents.forEach(function (event) { if (event && event.id) seenEventIds[event.id] = true; });
      priorSession.events.forEach(function (event) {
        if (event && event.live && event.id && !seenEventIds[event.id]) {
          persistedEvents.push(event);
          seenEventIds[event.id] = true;
        }
      });
      persistedEvents.sort(function (a, b) { return String(a.createdAt || "").localeCompare(String(b.createdAt || "")); });
      fullSession.events = persistedEvents.slice(-240);
    }
    var projectPayload = payload.project && typeof payload.project === "object" ? payload.project : null;
    var projectId = String(
      (projectPayload && projectPayload.id)
      || fullSession.projectId
      || payload.projectId
      || prev.activeProjectId
      || ""
    );
    var foundProject = false;
    var nextProjects = (prev.projects || []).map(function (project) {
      if (!project || String(project.id || "") !== projectId) return project;
      foundProject = true;
      var projectPatch = {};
      if (projectPayload) {
        Object.keys(projectPayload).forEach(function (key) {
          if (key !== "sessions") projectPatch[key] = projectPayload[key];
        });
      }
      var foundSession = false;
      var sessions = (project.sessions || []).map(function (session) {
        if (session && String(session.id || "") === String(fullSession.id || "")) {
          foundSession = true;
          return fullSession;
        }
        return session;
      });
      if (!foundSession) sessions = sessions.concat([fullSession]);
      return Object.assign({}, project, projectPatch, { sessions: sessions });
    });
    if (!foundProject && projectPayload) {
      nextProjects = nextProjects.concat([Object.assign({}, projectPayload, { sessions: [fullSession] })]);
    }
    var activeProject = nextProjects.find(function (project) { return String(project.id || "") === projectId; }) || prev.activeProject;
    var shouldActivate = String(prev.activeSessionId || "") === String(fullSession.id || "");
    var activeSession = shouldActivate
      ? fullSession
      : (
        activeProject && (activeProject.sessions || []).find(function (session) {
          return session && String(session.id || "") === String(prev.activeSessionId || "");
        })
      ) || prev.activeSession;
    return Object.assign({}, prev, {
      projects: nextProjects,
      activeProjectId: activeProject ? activeProject.id : prev.activeProjectId,
      activeProject: activeProject,
      activeSessionId: shouldActivate ? (fullSession.id || prev.activeSessionId) : prev.activeSessionId,
      activeSession: activeSession,
    });
  }

  function fetchAndMergeSession(sessionId, options) {
    if (!sessionId) return Promise.resolve(null);
    options = options || {};
    var showLoading = options.showLoading !== false;
    var seq = ++sessionLoadSeqRef.current;
    if (showLoading) setLoading(true);
    return model.fetchSession(sessionId)
      .then(function (payload) {
        if (seq !== sessionLoadSeqRef.current) return null;
        setStore(function (prev) { return mergeSessionPayload(prev, payload); });
        return payload;
      })
      .catch(function (err) {
        if (seq === sessionLoadSeqRef.current) setError(err.message || String(err));
        return null;
      })
      .finally(function () {
        if (showLoading && seq === sessionLoadSeqRef.current) setLoading(false);
      });
  }

  useWorkbenchEffect(function () {
    try {
      // "welcome" is a one-time get-started page gated by `cyrene-workbench-welcomed`,
      // not a resumable work page. Persisting it as the active page would drag the
      // user back into the welcome screen on every relaunch (and an existing user
      // whose first session started on welcome would never escape it). Keep it out
      // of wb-active-page so relaunch falls through to the normal workspace.
      if (fullPage && fullPage !== "welcome") localStorage.setItem("wb-active-page", fullPage);
      else localStorage.removeItem("wb-active-page");
    } catch (e) {}
  }, [fullPage]);

  // Once the welcome page has been shown, remember it so it never auto-pops
  // again on subsequent launches. Re-entry stays available from the Help center.
  useWorkbenchEffect(function () {
    if (fullPage !== "welcome") return;
    try { localStorage.setItem("cyrene-workbench-welcomed", "1"); } catch (e) {}
  }, [fullPage]);

  useWorkbenchEffect(function () {
    reloadWorkbench();
    reloadNotifications();
  }, []);

  // Any renderer overlay must temporarily detach the native browser view.
  // The coordinator also covers topbar popovers, which cannot rely on CSS
  // z-index to appear above an Electron WebContentsView.
  useWorkbenchEffect(function () {
    if (settingsOpen) { wbSetBrowserOverlayObscured(1); return function () { wbSetBrowserOverlayObscured(-1); }; }
  }, [settingsOpen]);
  useWorkbenchEffect(function () {
    if (searchOpen) { wbSetBrowserOverlayObscured(1); return function () { wbSetBrowserOverlayObscured(-1); }; }
  }, [searchOpen]);

  // 页面刷新/卸载时隐藏原生浏览器窗口，防止 OS 级 BrowserView 残留。
  useWorkbenchEffect(function () {
    function onBeforeUnload() {
      var bridge = window.cyrene && window.cyrene.browser;
      if (bridge && typeof bridge.setObscured === "function") {
        bridge.setObscured(true).catch(function (err) {
          console.error("beforeunload setObscured failed", err);
        });
      }
    }
    window.addEventListener('beforeunload', onBeforeUnload);
    return function () {
      window.removeEventListener('beforeunload', onBeforeUnload);
    };
  }, []);

  // 原生菜单操作（macOS 菜单栏 → menu:action IPC）
  // 每次渲染更新 ref，避免菜单回调中捕获到 stale closure
  useWorkbenchEffect(function () {
    menuActionsRef.current = { createProject: createProject, createSession: createSession, onToggleTheme: onToggleTheme };
  });
  useWorkbenchEffect(function () {
    var bridge = window.cyrene;
    if (!bridge || typeof bridge.onMenuAction !== "function") return undefined;
    return bridge.onMenuAction(function (action) {
      var acts = menuActionsRef.current;
      var map = {
        "open-settings":  function () { setSettingsTab(""); setSettingsOpen(true); },
        "open-about":     function () { setSettingsTab("about"); setSettingsOpen(true); },
        "new-project":    function () { acts.createProject(); },
        "new-chat":       function () { acts.createSession(); },
        "new-task":       function () { acts.createSession(); },
        "toggle-theme":   function () { acts.onToggleTheme(); },
        "toggle-sidebar": function () { setRailCollapsed(function (v) { var n = !v; try { localStorage.setItem("wb-rail-collapsed", n ? "1" : "0"); } catch (e) {} return n; }); },
      };
      var fn = map[action];
      if (fn) fn();
    });
  }, []);

  useWorkbenchEffect(function () {
    function handleEvent(data) {
      if (!data || data.type !== "notification") return;
      reloadNotifications();
    }
    if (window.__sseHandlers && window.__sseHandlers.add) {
      window.__sseHandlers.add(handleEvent);
      return function () {
        window.__sseHandlers.delete(handleEvent);
      };
    }
    return undefined;
  }, []);

  // Keep the active-view snapshot current for the notification suppression /
  // polling closures (which capture refs, not state, to dodge stale values).
  useWorkbenchEffect(function () {
    activeViewRef.current = {
      page: fullPage || null,
      taskView: taskView,
      chatId: activeChatId || "",
      sessionId: (store && store.activeSessionId) || "",
    };
  }, [fullPage, taskView, activeChatId, store && store.activeSessionId]);

  // Global keyboard shortcuts (search, new chat/task, command palette,
  // switch project, toggle sidebar, settings). Bindings come from the
  // platform-aware WorkbenchShortcuts module so ⌘ on mac / Ctrl elsewhere is
  // handled automatically and user customizations in Settings → Shortcuts are
  // honoured. Composer Enter-to-send is handled locally in each composer's
  // onKeyDown; these are the shell-level ones.
  useWorkbenchEffect(function () {
    function onKey(event) {
      var sc = window.WorkbenchShortcuts;
      if (!sc) return;
      // Don't intercept while a modal/overlay that owns its own keys is open.
      if (searchOpen || settingsOpen || newProjectOpen || newTaskOpen) return;
      // Ignore typing inside inputs / textareas / contenteditable so a Cmd+K
      // still fires search but a plain "k" doesn't trigger anything.
      var target = event.target;
      var tag = target && target.tagName ? target.tagName.toLowerCase() : "";
      var isEditable = tag === "input" || tag === "textarea" || tag === "select" || !!(target && target.isContentEditable);
      // All current global shortcuts require a modifier (mod/shift/ctrl) so
      // they won't fire from plain typing — but still bail on plain keys when
      // the user is editing to avoid swallowing character entry.
      if (isEditable && !(event.metaKey || event.ctrlKey || event.altKey)) return;

      if (sc.matches(event, "search")) {
        event.preventDefault();
        setSearchOpen(true);
        return;
      }
      if (sc.matches(event, "new-chat")) {
        event.preventDefault();
        setFullPage("chat");
        return;
      }
      if (sc.matches(event, "new-task")) {
        event.preventDefault();
        if (store && store.activeProject) setNewTaskOpen(true);
        return;
      }
      if (sc.matches(event, "command-palette")) {
        // No dedicated palette yet — reuse search as the entry point so the
        // shortcut still does something useful.
        event.preventDefault();
        setSearchOpen(true);
        return;
      }
      if (sc.matches(event, "settings")) {
        event.preventDefault();
        setSettingsTab("shortcuts");
        setSettingsOpen(true);
        return;
      }
      if (sc.matches(event, "toggle-sidebar")) {
        event.preventDefault();
        setRailCollapsed(function (v) {
          var next = !v;
          try { localStorage.setItem("wb-rail-collapsed", next ? "1" : "0"); } catch (e) {}
          return next;
        });
        return;
      }
      if (sc.matches(event, "switch-project")) {
        // Mod+1..9 selects the nth project. The terminal key is the digit.
        var digit = String(event.key || "");
        if (/^[1-9]$/.test(digit)) {
          var projects = (store && store.projects) || [];
          var idx = parseInt(digit, 10) - 1;
          if (projects[idx]) {
            event.preventDefault();
            selectProject(projects[idx].id);
          }
        }
        return;
      }
    }
    window.addEventListener("keydown", onKey);
    return function () { window.removeEventListener("keydown", onKey); };
  }, [searchOpen, settingsOpen, newProjectOpen, newTaskOpen, store && store.activeProject, store && store.projects]);

  // Auto-refresh the notification center on a timer so new items (agent replies,
  // scheduled-task results, knowledge ingestion…) appear without a page reload —
  // workbench notifications are persisted server-side but never pushed over SSE.
  // Polling pauses while the tab is hidden and resumes (with an immediate pull)
  // on focus / visibility, so a backgrounded window costs nothing.
  useWorkbenchEffect(function () {
    var INTERVAL_MS = 30000;
    var timer = null;
    function tick() { reloadNotifications(); }
    function start() { if (!timer) timer = setInterval(tick, INTERVAL_MS); }
    function stop() { if (timer) { clearInterval(timer); timer = null; } }
    function onVisibility() {
      if (document.hidden) { stop(); }
      else { tick(); start(); }
    }
    if (!document.hidden) start();
    document.addEventListener("visibilitychange", onVisibility);
    window.addEventListener("focus", tick);
    return function () {
      stop();
      document.removeEventListener("visibilitychange", onVisibility);
      window.removeEventListener("focus", tick);
    };
  }, []);

  // Keep the board aligned with background task transitions. Goal-loop events
  // trigger an immediate summary pull; a short visibility-aware poll covers
  // status changes produced by other task endpoints or another app window.
  useWorkbenchEffect(function () {
    if (fullPage || taskView !== "board") return undefined;
    var INTERVAL_MS = 4000;
    var timer = null;
    var trailing = null;
    var inFlight = false;
    function tick() {
      if (document.hidden || inFlight) return;
      inFlight = true;
      refreshTaskBoard().finally(function () { inFlight = false; });
    }
    function scheduleTick() {
      if (trailing) clearTimeout(trailing);
      trailing = setTimeout(function () { trailing = null; tick(); }, 220);
    }
    function onVisibility() {
      if (!document.hidden) tick();
    }
    function onRuntimeEvent(data) {
      if (!data) return;
      if (["goal_loop_update", "session_update", "notification"].indexOf(data.type) >= 0) scheduleTick();
    }
    timer = setInterval(tick, INTERVAL_MS);
    document.addEventListener("visibilitychange", onVisibility);
    window.addEventListener("focus", tick);
    if (window.__sseHandlers && window.__sseHandlers.add) window.__sseHandlers.add(onRuntimeEvent);
    return function () {
      if (timer) clearInterval(timer);
      if (trailing) clearTimeout(trailing);
      document.removeEventListener("visibilitychange", onVisibility);
      window.removeEventListener("focus", tick);
      if (window.__sseHandlers && window.__sseHandlers.delete) window.__sseHandlers.delete(onRuntimeEvent);
    };
  }, [fullPage, taskView]);

  useWorkbenchEffect(function () {
    // Goal-loop runs emit a status event on every phase/step change. Reloading
    // the whole store on each one would briefly flip the task area into its
    // loading shell and pile concurrent fetches onto a server that is already busy
    // running the agent. Merge the lightweight event immediately, then do a silent
    // trailing session refresh for fields that are only persisted server-side.
    var goalLoopReloadTimer = null;
    function handleRuntimeEvent(data) {
      if (!data) return;
      if (data.type === "goal_loop_update") {
        var activeSessionId = String((activeViewRef.current && activeViewRef.current.sessionId) || "");
        var eventSessionId = String(data.session_id || "");
        if (eventSessionId && eventSessionId !== activeSessionId) return;
        var publicLoop = data.goal_loop && typeof data.goal_loop === "object" ? data.goal_loop : null;
        if (activeSessionId && publicLoop) {
          setStore(function (prev) {
            var active = prev && prev.activeSession;
            if (!active || String(active.id || "") !== activeSessionId) return prev;
            var loopStatus = String(publicLoop.status || "");
            var nextSessionPatch = { goalLoop: publicLoop };
            if (["running", "waiting_for_user", "paused", "blocked", "review", "cancelled"].indexOf(loopStatus) >= 0) {
              nextSessionPatch.status = loopStatus;
            }
            function mergeSession(session) {
              return session && String(session.id || "") === activeSessionId
                ? Object.assign({}, session, nextSessionPatch)
                : session;
            }
            var nextProjects = (prev.projects || []).map(function (project) {
              if (!project || project.id !== prev.activeProjectId) return project;
              return Object.assign({}, project, { sessions: (project.sessions || []).map(mergeSession) });
            });
            return Object.assign({}, prev, {
              projects: nextProjects,
              activeProject: nextProjects.find(function (project) { return project.id === prev.activeProjectId; }) || prev.activeProject,
              activeSession: Object.assign({}, active, nextSessionPatch),
            });
          });
        }
        if (goalLoopReloadTimer) clearTimeout(goalLoopReloadTimer);
        goalLoopReloadTimer = setTimeout(function () {
          goalLoopReloadTimer = null;
          fetchAndMergeSession(eventSessionId || activeSessionId, { showLoading: false });
        }, 1600);
        return;
      }
      if (["tool_call", "llm_call", "subagent_update"].indexOf(data.type) < 0) return;
      setStore(function (prev) {
        var active = prev && prev.activeSession;
        // Merge live activity while a plan step runs, OR while any background
        // agent op (规划 / 反思 / 验收) is in flight — both feed the activity card.
        if (!active || (active.status !== "running" && !active.agentBusy)) return prev;
        var dataSessionId = String(data.session_id || "").trim();
        if (dataSessionId && dataSessionId !== active.id) return prev;
        if (!dataSessionId && String(data.caller || "").indexOf("subagent_") !== 0) return prev;
        var event = wbLiveEventFromSse(data);
        if (!event) return prev;
        var nextSession = wbMergeLiveEventIntoSession(active, event);
        if (nextSession === active) return prev;
        function mergeSession(session) {
          return session && session.id === active.id ? nextSession : session;
        }
        var nextProjects = (prev.projects || []).map(function (project) {
          if (!project || project.id !== prev.activeProjectId) return project;
          return Object.assign({}, project, { sessions: (project.sessions || []).map(mergeSession) });
        });
        return Object.assign({}, prev, {
          projects: nextProjects,
          activeProject: nextProjects.find(function (project) { return project.id === prev.activeProjectId; }) || prev.activeProject,
          activeSession: nextSession,
        });
      });
    }
    if (window.__sseHandlers && window.__sseHandlers.add) {
      window.__sseHandlers.add(handleRuntimeEvent);
      return function () {
        window.__sseHandlers.delete(handleRuntimeEvent);
        if (goalLoopReloadTimer) clearTimeout(goalLoopReloadTimer);
      };
    }
    return undefined;
  }, []);

  function selectProject(projectId) {
    var project = store.projects.find(function (item) { return item.id === projectId; });
    if (!project) return;
    var nextSession = project.sessions[0] || null;
    var nextSessionId = nextSession ? nextSession.id : "";
    setStore(function (prev) {
      var next = { ...prev };
      next.activeProjectId = project.id;
      next.activeProject = project;
      next.activeSession = nextSession;
      next.activeSessionId = nextSessionId;
      return next;
    });
    setExpandedStepId("");
    if (!fullPage) setTaskView("board");
    if (nextSession && nextSession.isSummary) fetchAndMergeSession(nextSessionId);
    window.WorkbenchModel.setActiveProject(project.id, nextSessionId).catch(function () {});
  }

  function selectSession(sessionId) {
    var project = store.activeProject;
    if (!project) return;
    var session = project.sessions.find(function (item) { return item.id === sessionId; });
    if (!session) return;
    setStore(function (prev) {
      return { ...prev, activeSessionId: session.id, activeSession: session };
    });
    setTaskView("detail");
    setExpandedStepId("");
    if (session.isSummary) fetchAndMergeSession(session.id);
    window.WorkbenchModel.setActiveProject(project.id, sessionId).catch(function () {});
  }

  // Global search navigation: select the right project/session/page and tell
  // the target module page which item to highlight/open.
  function navigateFromSearch(payload) {
    if (!payload || !payload.type) return;
    var type = payload.type;
    var projectId = payload.projectId;
    var project = projectId
      ? store.projects.find(function (p) { return p.id === projectId; })
      : store.activeProject;
    if (!project && type !== "conversation") return;

    var pageMap = { chat: "chat", knowledge: "knowledge", memory: "memory", schedule: "schedule" };

    if (type === "task" && project && payload.sessionId) {
      var session = project.sessions.find(function (s) { return s.id === payload.sessionId; });
      if (session) {
        setStore(function (prev) {
          return {
            ...prev,
            activeProjectId: project.id,
            activeProject: project,
            activeSessionId: session.id,
            activeSession: session,
          };
        });
        setExpandedStepId("");
        setTaskView("detail");
        setFullPage(null);
        window.WorkbenchModel.setActiveProject(project.id, session.id).catch(function () {});
      }
    } else if (type === "project" && project) {
      selectProject(project.id);
      setTaskView("board");
      setFullPage(null);
    } else {
      if (project && project.id !== store.activeProjectId) {
        selectProject(project.id);
      }
      if (pageMap[type]) {
        setFullPage(pageMap[type]);
      }
    }

    window.__workbenchPendingSelection = payload;
    try {
      window.dispatchEvent(new CustomEvent("cyrene:workbench-navigate", { detail: payload }));
    } catch (e) {}
    // If no module consumes the pending selection within a few seconds, clear
    // it so it does not leak into unrelated navigation later.
    setTimeout(function () {
      if (window.__workbenchPendingSelection === payload) {
        window.__workbenchPendingSelection = null;
      }
    }, 5000);
  }

  useWorkbenchEffect(function () {
    window.__workbenchNavigate = navigateFromSearch;
    return function () {
      delete window.__workbenchNavigate;
    };
  }, [store.projects, store.activeProjectId]);

  // The quick-chat window (separate Electron window) sends straight into a
  // project. When it does, nudge the chat module to re-pull so the new
  // conversation / reply shows up here without a manual refresh. Non-disruptive:
  // it does not yank the user's current view to the chat.
  useWorkbenchEffect(function () {
    var bridge = window.cyrene && window.cyrene.quickChat;
    if (!bridge || typeof bridge.onSent !== "function") return undefined;
    return bridge.onSent(function (info) {
      if (!info || !info.chatId) return;
      window.dispatchEvent(new CustomEvent("cyrene:wbc-refresh-chats", {
        detail: { projectId: info.projectId || "", selectId: info.chatId },
      }));
    });
  }, []);

  // Optimistically merge fields into the active session's `init` object so the
  // init view and the right-panel 初始化进度 (siblings reading store data) stay
  // in sync between server writes. Used while answering onboarding questions.
  function patchActiveInit(initPatch) {
    if (!initPatch) return;
    setStore(function (prev) {
      if (!prev.activeSession) return prev;
      var activeId = prev.activeSession.id;
      function mergeSession(s) {
        if (!s || s.id !== activeId) return s;
        return { ...s, init: { ...(s.init || {}), ...initPatch } };
      }
      var nextProjects = (prev.projects || []).map(function (p) {
        if (!p || p.id !== prev.activeProjectId) return p;
        return { ...p, sessions: (p.sessions || []).map(mergeSession) };
      });
      return {
        ...prev,
        projects: nextProjects,
        activeProject: nextProjects.find(function (p) { return p.id === prev.activeProjectId; }) || prev.activeProject,
        activeSession: mergeSession(prev.activeSession),
      };
    });
  }

  // Optimistically merge top-level fields into the active session client-side
  // (no server round-trip) — used for transient UI state like the `agentBusy`
  // marker that drives the "Agent 正在处理" card while a background agent op
  // runs. Cleared the moment a server response replaces activeSession.
  function patchActiveSessionLocal(partial) {
    if (!partial) return;
    setStore(function (prev) {
      if (!prev.activeSession) return prev;
      var activeId = prev.activeSession.id;
      function mergeSession(s) {
        if (!s || s.id !== activeId) return s;
        return Object.assign({}, s, partial);
      }
      var nextProjects = (prev.projects || []).map(function (p) {
        if (!p || p.id !== prev.activeProjectId) return p;
        return Object.assign({}, p, { sessions: (p.sessions || []).map(mergeSession) });
      });
      return Object.assign({}, prev, {
        projects: nextProjects,
        activeProject: nextProjects.find(function (p) { return p.id === prev.activeProjectId; }) || prev.activeProject,
        activeSession: mergeSession(prev.activeSession),
      });
    });
  }

  // New project / task creation now goes through dedicated workbench modals
  // (WorkbenchNewProjectModal / WorkbenchNewTaskModal). These handlers perform
  // the actual API calls; the rail buttons just open the modals.
  function createProject() { setNewProjectOpen(true); }
  function createSession() { if (store.activeProject) setNewTaskOpen(true); }

  function handleCreateProject(input) {
    // The backend opens the new project onto its agent-led init session and
    // returns it as the active session, so we just adopt the new store.
    return model.createProject(input).then(function (next) {
      setStore(next);
      setExpandedStepId("");
      setRightTab("context");
      setTaskView("detail");
      // Land in the freshly-created project's task view — important when the
      // project was created from the welcome page, so we leave it behind.
      setFullPage(null);
      return next;
    });
  }

  function handleCreateSession(input) {
    if (!store.activeProject) return Promise.resolve();
    return model.createSession(store.activeProject.id, input).then(function (next) {
      setStore(next);
      setExpandedStepId("");
      setTaskView("detail");
      setFullPage(null);
      return next;
    });
  }

  function handleUpdateProject(projectId, input) {
    return model.updateProject(projectId, input).then(function (next) {
      setStore(next);
      return next;
    });
  }

  function handleDeleteSession(session) {
    if (!session) return;
    window.confirmModal({
      body: t("task.confirmDelete", { name: session.title || t("task.thisTask") }),
      confirmLabel: t("common.delete"),
      danger: true,
    }).then(function (ok) {
      if (!ok) return;
      model.deleteSession(session.id).then(function (next) {
        setStore(next);
        setExpandedStepId("");
      }).catch(function (err) {
        setError(err.message || String(err));
      });
    });
  }

  function handleDeleteProject(project) {
    if (!project) return Promise.resolve();
    if (project.dataKey === "default") {
      setError(wbT("project.cannotDeleteDefault", "The default project cannot be deleted."));
      return Promise.resolve();
    }
    return window.confirmModal({
      body: wbT("project.confirmDelete", "Delete project \"{name}\"? Data inside the project will also be deleted.", { name: project.name }),
      confirmLabel: wbT("common.delete", "Delete"),
      danger: true,
    }).then(function (ok) {
      if (!ok) return undefined;
      return model.deleteProject(project.id).then(function (next) {
        setStore(next);
        setFullPage(null);
        setTaskView("board");
        setExpandedStepId("");
        return next;
      }).catch(function (err) {
        setError(err.message || String(err));
      });
    });
  }

  function handleRunCreated(next) {
    setStore(next);
    setExpandedStepId(next.activeSession && next.activeSession.plan[0] ? next.activeSession.plan[0].id : "");
    setRightTab("context");
    setTaskView("detail");
  }

  function handleOpenPage(page) {
    if (page === "task") { setTaskView("board"); setFullPage(null); return; }
    setFullPage(function (prev) { return prev === page ? null : page; });
  }

  // Conversation → task promotion: the chat page returns the refreshed store
  // (active = the new task session); adopt it and jump back to the task view.
  function handleChatToTask(payload) {
    var next = model.normalizeStore(payload);
    setStore(next);
    setFullPage(null);
    setTaskView("detail");
    setExpandedStepId("");
    setRightTab("context");
  }

  // The 知识库 / 日程 / 记忆 / 对话 views keep the ProjectRail (so you can
  // navigate while viewing them); other pages take over the full screen.
  var isKnowledge = fullPage === "knowledge";
  var isSchedule = fullPage === "schedule";
  var isMemory = fullPage === "memory";
  var isChat = fullPage === "chat";
  var isWelcome = fullPage === "welcome";
  var isProfile = fullPage === "profile";
  var isModulePage = isKnowledge || isSchedule || isMemory || isChat || isWelcome || isProfile;
  var fullPageConfig = fullPage && !isModulePage ? workbenchFullPageConfig(fullPage, setFullPage, store) : null;
  useWorkbenchEffect(function () {
    if (!isModulePage || !fullPage) return;
    setMountedPages(function (prev) {
      if (prev[fullPage]) return prev;
      return Object.assign({}, prev, { [fullPage]: true });
    });
  }, [fullPage, isModulePage]);
  var showChatPage = isChat || mountedPages.chat;
  var showKnowledgePage = isKnowledge || mountedPages.knowledge;
  var showSchedulePage = isSchedule || mountedPages.schedule;
  var showMemoryPage = isMemory || mountedPages.memory;
  var showWelcomePage = isWelcome || mountedPages.welcome;
  var showProfilePage = isProfile || mountedPages.profile;

  // First-run onboarding (LLM + personality). Driven by the backend onboarding
  // state — the workbench's own setup flow, independent of the legacy wizard.
  // It takes over the whole shell (no rails) until both are configured; once the
  // backend reports needsOnboarding=false the shell falls through to normal.
  var onboarding = (window.DATA && window.DATA.onboarding) || {};
  var onboardingActive = onboarding.needsOnboarding != null ? !!onboarding.needsOnboarding : !!needsOnboarding;
  if (onboardingActive) {
    return (
      <div className="workbench-shell wb-ob-shell" data-screen-label="Cyrene · onboarding">
        <div className="wb-ob-topbar">
          <div className="workbench-brand">
            <div className="workbench-traffic-space"></div>
            <span className="brand-mark" aria-hidden="true"></span>
            <strong>Cyrene</strong>
          </div>
          <button type="button" className="workbench-icon-btn" onClick={onToggleTheme} title={t("workbench.theme." + (theme === "system" ? "system" : actualTheme === "dark" ? "dark" : "light"))}>
            {theme === "system" ? (
              <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" strokeWidth="1.8"><circle cx="12" cy="12" r="9"/><path d="M12 3a9 9 0 0 1 0 18Z" fill="currentColor" stroke="none"/></svg>
            ) : actualTheme === "dark" ? (
              <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"><path d="M21 12.8A9 9 0 1 1 11.2 3 7 7 0 0 0 21 12.8Z"/></svg>
            ) : (
              <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"><circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4"/></svg>
            )}
          </button>
        </div>
        {React.createElement(window.WorkbenchWelcomePage || function () { return <div className="workbench-empty">{t("workbench.welcomeLoading")}</div>; }, {
          onboarding: onboarding,
        })}
        <WorkbenchFeedbackHost />
      </div>
    );
  }

  return (
    <div className="workbench-shell" data-screen-label="Cyrene · workbench">
      <WorkbenchTopbar
        project={store.activeProject}
        session={store.activeSession}
        activePage={fullPage}
        taskView={taskView}
        chatCrumb={chatCrumb}
        notifications={notifications}
        onReloadNotifications={reloadNotifications}
        onSearch={function () { setSearchOpen(true); }}
        onSettings={function (tab) { setSettingsTab(typeof tab === "string" ? tab : ""); setSettingsOpen(true); }}
        onNewProject={createProject}
        onNewTask={createSession}
        onOpenPage={handleOpenPage}
        theme={theme}
        actualTheme={actualTheme}
        onToggleTheme={onToggleTheme}
      />
      {fullPageConfig ? (
        <WorkbenchFullPage config={fullPageConfig} onClose={function () { setFullPage(null); }} />
      ) : (
        <div ref={wbApplyStoredRightWidth} className={"workbench-grid" + (railCollapsed ? " rail-collapsed" : "") + (isKnowledge ? " is-knowledge" : "") + (isSchedule ? " is-schedule" : "") + (isMemory ? " is-memory" : "") + (isChat ? " is-chat" : "") + (isWelcome ? " is-welcome" : "") + (isProfile ? " is-profile" : "") + (!isModulePage ? (taskView === "board" ? " is-task-board" : " is-task-detail") : "")}>
          <ProjectRail
            projects={store.projects}
            activeProjectId={store.activeProjectId}
            activePage={fullPage}
            collapsed={railCollapsed}
            onToggleCollapse={function () {
              setRailCollapsed(function (v) {
                var next = !v;
                try { localStorage.setItem("wb-rail-collapsed", next ? "1" : "0"); } catch (e) {}
                return next;
              });
            }}
            onSelectProject={selectProject}
            onCreateProject={createProject}
            onEditProject={setEditProject}
            onDeleteProject={handleDeleteProject}
            onOpenPage={handleOpenPage}
            onSettings={function () { setSettingsTab(""); setSettingsOpen(true); }}
          />
          {showChatPage && (
            <div style={{ display: isChat ? "contents" : "none" }}>
              {React.createElement(window.WorkbenchChatPage || function () { return <div className="workbench-empty">{t("workbench.chatLoading")}</div>; }, {
                active: isChat,
                project: store.activeProject,
                onOpenTask: handleChatToTask,
                onActiveChatChange: setChatCrumb,
                onActiveChatIdChange: setActiveChatId,
              })}
            </div>
          )}
          {showKnowledgePage && (
            <div style={{ display: isKnowledge ? "contents" : "none" }}>
              {React.createElement(window.WorkbenchKnowledgePage || function () { return <div className="workbench-empty">{t("workbench.knowledgeLoading")}</div>; }, {
                active: isKnowledge,
                project: store.activeProject,
                onBack: function () { setFullPage(null); },
                onNavigate: navigateFromSearch,
              })}
            </div>
          )}
          {showSchedulePage && (
            <div style={{ display: isSchedule ? "contents" : "none" }}>
              {React.createElement(window.WorkbenchSchedulePage || function () { return <div className="workbench-empty">{t("workbench.scheduleLoading")}</div>; }, { active: isSchedule, project: store.activeProject, onBack: function () { setFullPage(null); } })}
            </div>
          )}
          {showMemoryPage && (
            <div style={{ display: isMemory ? "contents" : "none" }}>
              {React.createElement(window.WorkbenchMemoryPage || function () { return <div className="workbench-empty">{t("workbench.memoryLoading")}</div>; }, { active: isMemory, project: store.activeProject, onBack: function () { setFullPage(null); } })}
            </div>
          )}
          {showWelcomePage && (
            <div style={{ display: isWelcome ? "contents" : "none" }}>
              {React.createElement(window.WorkbenchWelcomePage || function () { return <div className="workbench-empty">{t("workbench.welcomeLoading")}</div>; }, {
                active: isWelcome,
                project: store.activeProject,
                hasProjects: Array.isArray(store.projects) && store.projects.length > 0,
                onNewProject: createProject,
                onOpenPage: handleOpenPage,
                onSettings: function (tab) { setSettingsTab(typeof tab === "string" ? tab : ""); setSettingsOpen(true); },
                theme: theme,
                actualTheme: actualTheme,
                onToggleTheme: onToggleTheme,
              })}
            </div>
          )}
          {showProfilePage && (
            <div style={{ display: isProfile ? "contents" : "none" }}>
              {window.WorkbenchProfilePage
                ? React.createElement(window.WorkbenchProfilePage, { active: isProfile })
                : <div className="workbench-empty">…</div>}
            </div>
          )}
          <div style={{ display: isModulePage ? "none" : "contents" }}>
          <>
          {taskView === "board" ? (
            <TaskBoard
              project={store.activeProject}
              loading={loading}
              error={error}
              onOpenSession={selectSession}
              onCreateSession={createSession}
              onDeleteSession={handleDeleteSession}
            />
          ) : (
          <>
          {/* Key by session id so each task gets its OWN controller instance:
              the controller's transient `busy` marker (and draft/attachments)
              must not bleed across tasks. Without this, switching to another
              task while one is running (its dispatch request still in flight)
              leaks busy=true onto the new task — its send button spins and is
              unclickable. Stable within a task (id unchanged on live merges),
              remounts only on a real switch; no unmount-abort, so the running
              task keeps going server-side. */}
          <TaskWorkArea
            key={store.activeSessionId || "none"}
            project={store.activeProject}
            session={store.activeSession}
            expandedStepId={expandedStepId}
            onToggleStep={function (stepId) { setExpandedStepId(expandedStepId === stepId ? "" : stepId); }}
            onCreateRun={handleRunCreated}
            onRightTab={setRightTab}
            onSelectSession={selectSession}
            onBackToBoard={function () { setTaskView("board"); }}
            onCreateSession={createSession}
            onInitPatch={patchActiveInit}
            onLocalPatch={patchActiveSessionLocal}
            onRefresh={function (nextStore) {
              setStore(function (prev) {
                // Preserve expandedStepId, rightTab, etc. from current UI state
                // but replace project/session data from the server response
                var merged = { ...prev };
                if (nextStore && nextStore.activeProject) {
                  merged.activeProject = nextStore.activeProject;
                  merged.activeProjectId = nextStore.activeProjectId || merged.activeProjectId;
                }
                if (nextStore && nextStore.activeSession) {
                  merged.activeSession = nextStore.activeSession;
                  merged.activeSessionId = nextStore.activeSessionId || merged.activeSessionId;
                }
                // Also refresh the projects + sessions lists
                if (nextStore && Array.isArray(nextStore.projects)) {
                  merged.projects = nextStore.projects;
                }
                return merged;
              });
            }}
            error={error}
            loading={loading}
            active={!isModulePage}
          />
          <RightContextPanel
            project={store.activeProject}
            session={store.activeSession}
            expandedStepId={expandedStepId}
            tab={rightTab}
            onTabChange={setRightTab}
            onRefresh={function (nextStore) {
              setStore(function (prev) {
                var merged = { ...prev };
                if (nextStore && nextStore.activeProject) merged.activeProject = nextStore.activeProject;
                if (nextStore && nextStore.activeSession) merged.activeSession = nextStore.activeSession;
                if (nextStore && Array.isArray(nextStore.projects)) merged.projects = nextStore.projects;
                return merged;
              });
            }}
          />
          </>
          )}
          </>
          </div>
        </div>
      )}
      {searchOpen && typeof ReactDOM !== "undefined" && ReactDOM.createPortal(React.createElement(
        window.SearchOverlay || function () { return null; },
        {
          onClose: function () { setSearchOpen(false); },
          onOpenSession: function () {
            setSearchOpen(false);
            setFullPage("chat");
          },
        }
      ), document.body)}
      {settingsOpen && React.createElement(
        window.SettingsOverlay || function () { return null; },
        {
          onClose: function () { setSettingsOpen(false); },
          initialTab: settingsTab,
          theme: theme,
          actualTheme: actualTheme,
          onToggleTheme: onToggleTheme,
        }
      )}
      {newProjectOpen && window.WorkbenchNewProjectModal && React.createElement(
        window.WorkbenchNewProjectModal,
        {
          defaultWorkspacePath: "",
          onClose: function () { setNewProjectOpen(false); },
          onCreate: function (input) {
            return handleCreateProject(input).then(function () { setNewProjectOpen(false); });
          },
        }
      )}
      {editProject && (
        <WorkbenchEditProjectModal
          project={editProject}
          onClose={function () { setEditProject(null); }}
          onSave={function (input) {
            return handleUpdateProject(editProject.id, input).then(function () { setEditProject(null); });
          }}
        />
      )}
      {newTaskOpen && window.WorkbenchNewTaskModal && React.createElement(
        window.WorkbenchNewTaskModal,
        {
          onClose: function () { setNewTaskOpen(false); },
          onCreate: function (input) {
            return handleCreateSession(input).then(function () { setNewTaskOpen(false); });
          },
        }
      )}
      <WorkbenchFeedbackHost />
    </div>
  );
}

function WorkbenchTopbar({ project, session, activePage, taskView, chatCrumb, notifications, onReloadNotifications, onSearch, onSettings, onNewProject, onNewTask, onOpenPage, theme, actualTheme, onToggleTheme }) {
  var { t } = window.useWorkbenchI18n();
  var title = project ? project.name : "Project";
  var pageLabels = { chat: t("workbench.page.chat"), knowledge: t("workbench.page.knowledge"), schedule: t("workbench.page.schedule"), memory: t("workbench.page.memory"), welcome: t("workbench.page.welcome"), profile: t("rail.profile") };
  var sessionTitle = activePage && pageLabels[activePage]
    ? pageLabels[activePage]
    : (taskView === "board" ? t("taskBoard.title") : (session ? session.title : t("workbench.page.task")));
  var chatTail = activePage === "chat" ? String(chatCrumb || "").trim() : "";
  var themeTitle = theme === "system" ? t("workbench.theme.system") : actualTheme === "dark" ? t("workbench.theme.dark") : t("workbench.theme.light");
  var themeIcon = theme === "system" ? (
    <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" strokeWidth="1.8"><circle cx="12" cy="12" r="9"/><path d="M12 3a9 9 0 0 1 0 18Z" fill="currentColor" stroke="none"/></svg>
  ) : actualTheme === "dark" ? (
    <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"><path d="M21 12.8A9 9 0 1 1 11.2 3 7 7 0 0 0 21 12.8Z"/></svg>
  ) : (
    <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"><circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4"/></svg>
  );
  return (
    <div className="workbench-topbar">
      <div className="workbench-brand">
        <div className="workbench-traffic-space"></div>
        <button
          type="button"
          className="workbench-brand-btn"
          onClick={function () { onSettings && onSettings("about"); }}
          title={t("nav.settings")}
          aria-label={t("nav.settings")}
        >
          <span className="brand-mark" aria-hidden="true"></span>
          <strong>Cyrene</strong>
        </button>
      </div>
      <div className="workbench-crumbs">
        <span>{title}</span>
        <span>/</span>
        {chatTail ? (
          <>
            <span>{sessionTitle}</span>
            <span>/</span>
            <b>{chatTail}</b>
          </>
        ) : (
          <b>{sessionTitle}</b>
        )}
      </div>
      <div className="workbench-top-actions">
        <button type="button" className="workbench-search-box" onClick={onSearch} title={t("workbench.search")}>
          <svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" strokeWidth="1.9" strokeLinecap="round" strokeLinejoin="round"><circle cx="11" cy="11" r="7"/><path d="m20 20-3.2-3.2"/></svg>
          <span>{t("workbench.search")}</span>
        </button>
        <WorkbenchNotificationCenter notifications={notifications} onReload={onReloadNotifications} onSettings={onSettings} />
        <button type="button" className="workbench-icon-btn" onClick={onToggleTheme} title={themeTitle}>{themeIcon}</button>
        <WorkbenchHelpCenter onNewProject={onNewProject} onNewTask={onNewTask} onOpenPage={onOpenPage} onSettings={onSettings} />
        <button type="button" className="workbench-icon-btn" onClick={function () { onSettings(); }} title={t("nav.settings")}>
          <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"><path d="M12.22 2h-.44a2 2 0 0 0-2 2v.18a2 2 0 0 1-1 1.73l-.43.25a2 2 0 0 1-2 0l-.15-.08a2 2 0 0 0-2.73.73l-.22.38a2 2 0 0 0 .73 2.73l.15.1a2 2 0 0 1 1 1.72v.51a2 2 0 0 1-1 1.74l-.15.09a2 2 0 0 0-.73 2.73l.22.38a2 2 0 0 0 2.73.73l.15-.08a2 2 0 0 1 2 0l.43.25a2 2 0 0 1 1 1.73V20a2 2 0 0 0 2 2h.44a2 2 0 0 0 2-2v-.18a2 2 0 0 1 1-1.73l.43-.25a2 2 0 0 1 2 0l.15.08a2 2 0 0 0 2.73-.73l.22-.39a2 2 0 0 0-.73-2.73l-.15-.08a2 2 0 0 1-1-1.74v-.5a2 2 0 0 1 1-1.74l.15-.09a2 2 0 0 0 .73-2.73l-.22-.38a2 2 0 0 0-2.73-.73l-.15.08a2 2 0 0 1-2 0l-.43-.25a2 2 0 0 1-1-1.73V4a2 2 0 0 0-2-2Z"/><circle cx="12" cy="12" r="3"/></svg>
        </button>
        <button type="button" className={"workbench-avatar-btn" + (activePage === "profile" ? " active" : "")} title={t("rail.profile")} onClick={function () { onOpenPage && onOpenPage("profile"); }}>
          {window.WorkbenchAvatar
            ? React.createElement(window.WorkbenchAvatar, { user: DATA.user, size: 30 })
            : <span className="workbench-avatar">{WorkbenchModel.initials(DATA.user && DATA.user.name)}</span>}
        </button>
      </div>
    </div>
  );
}

function WorkbenchNotificationCenter({ notifications, onReload, onSettings }) {
  var { t } = window.useWorkbenchI18n();
  var model = window.WorkbenchModel;
  var [open, setOpen] = useWorkbenchState(false);
  var [tab, setTab] = useWorkbenchState("all");
  var [busy, setBusy] = useWorkbenchState(false);
  var rootRef = useWorkbenchRef(null);
  var items = notifications && Array.isArray(notifications.items) ? notifications.items : [];
  var unreadCount = notifications && notifications.unreadCount ? notifications.unreadCount : 0;
  var counts = notifications && notifications.counts ? notifications.counts : { all: 0, mention: 0, comment: 0, system: 0 };

  useWorkbenchEffect(function () {
    if (!open) return undefined;
    function handlePointer(event) {
      if (rootRef.current && !rootRef.current.contains(event.target)) setOpen(false);
    }
    function handleKey(event) {
      if (event.key === "Escape") setOpen(false);
    }
    document.addEventListener("mousedown", handlePointer);
    document.addEventListener("keydown", handleKey);
    return function () {
      document.removeEventListener("mousedown", handlePointer);
      document.removeEventListener("keydown", handleKey);
    };
  }, [open]);

  // A WebContentsView is a native sibling of the renderer, so it otherwise
  // paints over this popover regardless of its CSS z-index.
  useWorkbenchEffect(function () {
    if (!open) return undefined;
    wbSetBrowserOverlayObscured(1);
    return function () { wbSetBrowserOverlayObscured(-1); };
  }, [open]);

  useWorkbenchEffect(function () {
    if (!open) return;
    onReload && onReload(tab, 80);
  }, [open, tab]);

  function markRead(ids, markAll) {
    setBusy(true);
    return model.markNotificationsRead(ids, markAll).then(function (payload) {
      if (onReload) onReload(tab, 80);
      return payload;
    }).finally(function () {
      setBusy(false);
    });
  }

  return (
    <div className={"workbench-notif-anchor" + (open ? " open" : "")} ref={rootRef}>
      <button type="button" className={"workbench-icon-btn workbench-notif-btn" + (open ? " active" : "")} title={t("notifications.title")} onClick={function () { setOpen(!open); }}>
        <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"><path d="M18 8a6 6 0 1 0-12 0c0 7-3 9-3 9h18s-3-2-3-9"/><path d="M10.3 21a1.9 1.9 0 0 0 3.4 0"/></svg>
        {unreadCount > 0 ? <span className="workbench-notif-badge">{unreadCount > 99 ? "99+" : unreadCount}</span> : null}
      </button>
      {open ? (
        <div className="workbench-notif-popover">
          <div className="workbench-notif-popover-arrow"></div>
          <div className="workbench-notif-head">
            <b>{t("notifications.title")}</b>
            <button type="button" className="workbench-notif-markread" disabled={busy || !unreadCount} onClick={function () { markRead([], true); }}>{t("notifications.markAllRead")}</button>
          </div>
          <div className="workbench-notif-tabs">
            {[
              { id: "all", label: t("notifications.tab.all") },
              { id: "mention", label: t("notifications.tab.mention") },
              { id: "comment", label: t("notifications.tab.comment") },
              { id: "system", label: t("notifications.tab.system") },
            ].map(function (item) {
              return (
                <button key={item.id} type="button" className={"workbench-notif-tab" + (tab === item.id ? " active" : "")} onClick={function () { setTab(item.id); }}>
                  <span>{item.label}</span>
                </button>
              );
            })}
            <button type="button" className="workbench-notif-settings" onClick={onSettings} title={t("notifications.settings")}>
              <svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"><path d="M12.22 2h-.44a2 2 0 0 0-2 2v.18a2 2 0 0 1-1 1.73l-.43.25a2 2 0 0 1-2 0l-.15-.08a2 2 0 0 0-2.73.73l-.22.38a2 2 0 0 0 .73 2.73l.15.1a2 2 0 0 1 1 1.72v.51a2 2 0 0 1-1 1.74l-.15.09a2 2 0 0 0-.73 2.73l.22.38a2 2 0 0 0 2.73.73l.15-.08a2 2 0 0 1 2 0l.43.25a2 2 0 0 1 1 1.73V20a2 2 0 0 0 2 2h.44a2 2 0 0 0 2-2v-.18a2 2 0 0 1 1-1.73l.43-.25a2 2 0 0 1 2 0l.15.08a2 2 0 0 0 2.73-.73l.22-.39a2 2 0 0 0-.73-2.73l-.15-.08a2 2 0 0 1-1-1.74v-.5a2 2 0 0 1 1-1.74l.15-.09a2 2 0 0 0 .73-2.73l-.22-.38a2 2 0 0 0-2.73-.73l-.15.08a2 2 0 0 1-2 0l-.43-.25a2 2 0 0 1-1-1.73V4a2 2 0 0 0-2-2Z"/><circle cx="12" cy="12" r="3"/></svg>
            </button>
          </div>
          <div className="workbench-notif-list">
            {!items.length ? <div className="workbench-notif-empty">{t("notifications.empty")}</div> : items.map(function (item) {
              return <WorkbenchNotificationItem key={item.id} item={item} onOpen={function () { markRead([item.id], false); }} />;
            })}
          </div>
        </div>
      ) : null}
    </div>
  );
}

function WorkbenchNotificationItem({ item, onOpen }) {
  var { t } = window.useWorkbenchI18n();
  var tab = String(item && item.tab || "system");
  var iconClass = "system";
  var icon = null;
  if (tab === "mention") {
    iconClass = "mention";
    icon = <svg viewBox="0 0 24 24" width="19" height="19" fill="none" stroke="currentColor" strokeWidth="1.9" strokeLinecap="round" strokeLinejoin="round"><circle cx="12" cy="12" r="4.5"/><path d="M16.5 12v1a2.5 2.5 0 0 0 5 0V12a9.5 9.5 0 1 0-3 6.9"/></svg>;
  } else if (tab === "comment") {
    iconClass = "comment";
    icon = <svg viewBox="0 0 24 24" width="19" height="19" fill="currentColor"><path d="M12 2.5 13.7 9 20 10.7 13.7 12.4 12 19l-1.7-6.6L4 10.7 10.3 9Z"/></svg>;
  } else {
    var src = String(item && item.source || "");
    if (src.indexOf("knowledge") === 0) {
      iconClass = "upload";
      icon = <svg viewBox="0 0 24 24" width="19" height="19" fill="none" stroke="currentColor" strokeWidth="1.9" strokeLinecap="round" strokeLinejoin="round"><path d="M12 16V6"/><path d="m8.5 9.5 3.5-3.5 3.5 3.5"/><path d="M20 16.5a4 4 0 0 1-4 4H8a4 4 0 1 1 .9-7.9A5 5 0 0 1 18 10a4 4 0 0 1 2 6.5Z"/></svg>;
    } else if (src.indexOf("schedule") === 0 || src.indexOf("scheduled") === 0) {
      iconClass = "schedule";
      icon = <svg viewBox="0 0 24 24" width="19" height="19" fill="none" stroke="currentColor" strokeWidth="1.9" strokeLinecap="round" strokeLinejoin="round"><rect x="3.5" y="5" width="17" height="15.5" rx="2.5"/><path d="M3.5 9.5h17M8 3v4M16 3v4"/></svg>;
    } else if (src.indexOf("task") === 0) {
      iconClass = "success";
      icon = <svg viewBox="0 0 24 24" width="19" height="19" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round"><circle cx="12" cy="12" r="9"/><path d="m8 12 2.7 2.7L16 9.4"/></svg>;
    } else {
      iconClass = "system";
      icon = <svg viewBox="0 0 24 24" width="19" height="19" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"><path d="M12.22 2h-.44a2 2 0 0 0-2 2v.18a2 2 0 0 1-1 1.73l-.43.25a2 2 0 0 1-2 0l-.15-.08a2 2 0 0 0-2.73.73l-.22.38a2 2 0 0 0 .73 2.73l.15.1a2 2 0 0 1 1 1.72v.51a2 2 0 0 1-1 1.74l-.15.09a2 2 0 0 0-.73 2.73l.22.38a2 2 0 0 0 2.73.73l.15-.08a2 2 0 0 1 2 0l.43.25a2 2 0 0 1 1 1.73V20a2 2 0 0 0 2 2h.44a2 2 0 0 0 2-2v-.18a2 2 0 0 1 1-1.73l.43-.25a2 2 0 0 1 2 0l.15.08a2 2 0 0 0 2.73-.73l.22-.39a2 2 0 0 0-.73-2.73l-.15-.08a2 2 0 0 1-1-1.74v-.5a2 2 0 0 1 1-1.74l.15-.09a2 2 0 0 0 .73-2.73l-.22-.38a2 2 0 0 0-2.73-.73l-.15.08a2 2 0 0 1-2 0l-.43-.25a2 2 0 0 1-1-1.73V4a2 2 0 0 0-2-2Z"/><circle cx="12" cy="12" r="3"/></svg>;
    }
  }
  return (
    <button type="button" className={"workbench-notif-item" + (item.read ? "" : " unread")} onClick={onOpen}>
      <span className={"workbench-notif-item-icon " + iconClass}>{icon}</span>
      <span className="workbench-notif-item-main">
        <span className="workbench-notif-item-top">
          <b>{item.title}</b>
          <time>{window.WorkbenchModel.formatRelativeTime(item.createdAt)}</time>
        </span>
        {item.body ? <span className="workbench-notif-item-body">{item.body}</span> : null}
        <span className="workbench-notif-item-meta">{item.sourceLabel || item.projectName || item.linkLabel || t("notifications.title")}</span>
      </span>
    </button>
  );
}

function WorkbenchHelpCenter({ onNewProject, onNewTask, onOpenPage, onSettings }) {
  var { t } = window.useWorkbenchI18n();
  var [open, setOpen] = useWorkbenchState(false);
  var rootRef = useWorkbenchRef(null);
  var isMac = useWorkbenchMemo(wbIsMacPlatform, []);
  // Refresh the shortcut list every time the popover opens so it reflects any
  // rebinding done in Settings → Shortcuts. Mirror the module's glyph renderer
  // so the help center and the settings panel stay visually consistent.
  var shortcutList = useWorkbenchMemo(function () {
    if (!window.WorkbenchShortcuts) return [];
    var list = window.WorkbenchShortcuts.list();
    // Show the same set the help center always showed (global actions only);
    // composer bindings live in the settings panel where they can be rebound.
    return list.filter(function (item) { return item.group === "global"; });
  }, [open]);

  useWorkbenchEffect(function () {
    if (!open) return undefined;
    function handlePointer(event) {
      if (rootRef.current && !rootRef.current.contains(event.target)) setOpen(false);
    }
    function handleKey(event) {
      if (event.key === "Escape") setOpen(false);
    }
    document.addEventListener("mousedown", handlePointer);
    document.addEventListener("keydown", handleKey);
    return function () {
      document.removeEventListener("mousedown", handlePointer);
      document.removeEventListener("keydown", handleKey);
    };
  }, [open]);

  // See WorkbenchNotificationCenter: native browser content cannot be layered
  // beneath a DOM popover with CSS alone.
  useWorkbenchEffect(function () {
    if (!open) return undefined;
    wbSetBrowserOverlayObscured(1);
    return function () { wbSetBrowserOverlayObscured(-1); };
  }, [open]);

  function run(action) {
    setOpen(false);
    if (typeof action === "function") action();
  }

  var quickItems = [
    {
      id: "get-started", tone: "purple", title: t("help.getStarted"), desc: t("help.getStartedDesc"),
      icon: <svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"><path d="M5 15c-1.5 1.5-2 5-2 5s3.5-.5 5-2M9 11a9 9 0 0 1 9-9c1.5 0 2 .5 2 2a9 9 0 0 1-9 9M9 11l4 4M9 11l-4-1 2.5-2.5M13 15l1 4 2.5-2.5"/></svg>,
      action: function () { onOpenPage && onOpenPage("welcome"); },
    },
    {
      id: "new-project", tone: "blue", title: t("help.newProject"), desc: t("help.newProjectDesc"),
      icon: <svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"><rect x="3" y="4" width="18" height="16" rx="2.5"/><path d="M3 9h18"/><path d="M7 14h7M7 17h4"/></svg>,
      action: function () { onNewProject && onNewProject(); },
    },
    {
      id: "new-task", tone: "green", title: t("help.createTask"), desc: t("help.createTaskDesc"),
      icon: <svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"><rect x="3.5" y="3.5" width="17" height="17" rx="4.5"/><path d="m8 12 2.8 2.8L16.5 9"/></svg>,
      action: function () { onNewTask && onNewTask(); },
    },
    {
      id: "knowledge", tone: "amber", title: t("help.uploadKnowledge"), desc: t("help.uploadKnowledgeDesc"),
      icon: <svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"><path d="M12 15V4"/><path d="m8 8 4-4 4 4"/><path d="M5 15v3a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2v-3"/></svg>,
      action: function () { onOpenPage && onOpenPage("knowledge"); },
    },
    {
      id: "agent", tone: "purple", title: t("help.connectAgent"), desc: t("help.connectAgentDesc"),
      icon: <svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"><rect x="5" y="8.5" width="14" height="11" rx="3.5"/><path d="M12 8.5V4.5M12 4.5a1.5 1.5 0 1 0 0-.01"/><path d="M3.5 13.5v3M20.5 13.5v3"/><circle cx="9.5" cy="13.5" r="1.1" fill="currentColor" stroke="none"/><circle cx="14.5" cy="13.5" r="1.1" fill="currentColor" stroke="none"/></svg>,
      action: function () { onSettings && onSettings("agents"); },
    },
  ];

  var shortcuts = shortcutList.map(function (item) {
    return {
      id: item.id,
      label: t(item.labelKey),
      keys: item.keys,
    };
  });

  var version = (window.DATA && window.DATA.appVersion) || "1.0.0";

  return (
    <div className={"workbench-help-anchor" + (open ? " open" : "")} ref={rootRef}>
      <button type="button" className={"workbench-icon-btn" + (open ? " active" : "")} title={t("workbench.help")} onClick={function () { setOpen(!open); }}>
        <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"><circle cx="12" cy="12" r="10"/><path d="M9.1 9a3 3 0 0 1 5.8 1c0 2-3 3-3 3"/><path d="M12 17h.01"/></svg>
      </button>
      {open ? (
        <div className="workbench-help-popover" role="dialog" aria-label={t("help.title")}>
          <div className="workbench-help-popover-arrow"></div>
          <div className="workbench-help-head">
            <b>{t("help.title")}</b>
            <button type="button" className="workbench-icon-btn" title={t("common.close")} onClick={function () { setOpen(false); }}>
              <svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round"><path d="m6 6 12 12M18 6 6 18"/></svg>
            </button>
          </div>
          <div className="workbench-help-body">
            <div className="workbench-help-section">
              <span className="workbench-help-section-title">{t("help.quickStart")}</span>
              <div className="workbench-help-quick">
                {quickItems.map(function (item) {
                  return (
                    <button key={item.id} type="button" className="workbench-help-quick-item" onClick={function () { run(item.action); }}>
                      <span className={"workbench-help-quick-icon " + item.tone}>{item.icon}</span>
                      <span className="workbench-help-quick-main">
                        <b>{item.title}</b>
                        <small>{item.desc}</small>
                      </span>
                      <svg className="workbench-help-quick-chevron" viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="m9 6 6 6-6 6"/></svg>
                    </button>
                  );
                })}
              </div>
            </div>
            <div className="workbench-help-divider"></div>
            <div className="workbench-help-section">
              <span className="workbench-help-section-title">{t("help.shortcuts")}</span>
              <div className="workbench-help-shortcuts">
                {shortcuts.map(function (item) {
                  return (
                    <div key={item.id} className="workbench-help-shortcut">
                      <span>{item.label}</span>
                      <span className="workbench-help-keys">
                        {item.keys.map(function (token, idx) {
                          return <kbd key={idx}>{wbShortcutKey(token, isMac)}</kbd>;
                        })}
                      </span>
                    </div>
                  );
                })}
              </div>
              <button type="button" className="workbench-help-customize" onClick={function () { run(function () { onSettings && onSettings("shortcuts"); }); }}>
                {t("help.customizeShortcuts", "Customize shortcuts")}
              </button>
            </div>
            <div className="workbench-help-divider"></div>
            <div className="workbench-help-links">
              <a className="workbench-help-link" href={WB_HELP_DOCS_URL} target="_blank" rel="noopener noreferrer">
                <span>{t("help.docs")}</span>
                <svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"><path d="M14 4h6v6"/><path d="M20 4 10 14"/><path d="M19 13.5V18a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V7a2 2 0 0 1 2-2h4.5"/></svg>
              </a>
              <a className="workbench-help-link" href={WB_HELP_FEEDBACK_URL} target="_blank" rel="noopener noreferrer">
                <span>{t("help.feedback")}</span>
                <svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"><path d="M14 4h6v6"/><path d="M20 4 10 14"/><path d="M19 13.5V18a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V7a2 2 0 0 1 2-2h4.5"/></svg>
              </a>
            </div>
          </div>
          <div className="workbench-help-foot">{t("help.version", { version: version })}</div>
        </div>
      ) : null}
    </div>
  );
}

function WorkbenchEditProjectModal({ project, onClose, onSave }) {
  var { t } = window.useWorkbenchI18n();
  var [name, setName] = useWorkbenchState(project.name || "");
  var [description, setDescription] = useWorkbenchState(project.description || "");
  var [workspacePath, setWorkspacePath] = useWorkbenchState(project.workspacePath || "");
  var [color, setColor] = useWorkbenchState(project.color || "#22b07a");
  var [busy, setBusy] = useWorkbenchState(false);
  var [error, setError] = useWorkbenchState("");
  function save() {
    var trimmed = name.trim();
    if (!trimmed) { setError(t("create.project.error.nameRequired")); return; }
    setBusy(true);
    setError("");
    Promise.resolve(onSave({
      name: trimmed,
      description: description.trim(),
      workspacePath: workspacePath.trim(),
      color: color,
    })).catch(function (err) {
      setBusy(false);
      setError(err.message || String(err));
    });
  }
  return (
    <div className="workbench-modal-scrim" onMouseDown={function (e) { if (e.target === e.currentTarget) onClose(); }}>
      <div className="workbench-project-edit-modal" role="dialog" aria-modal="true">
        <div className="workbench-project-edit-head">
          <b>{t("rail.editProject")}</b>
          <button type="button" className="workbench-icon-btn" onClick={onClose} title={t("common.close")}>
            <svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round"><path d="m6 6 12 12M18 6 6 18" /></svg>
          </button>
        </div>
        <div className="workbench-project-edit-body">
          <label>{t("create.project.name")}</label>
          <input value={name} maxLength={60} onChange={function (e) { setName(e.target.value); }} />
          <label>{t("create.project.description")}</label>
          <textarea value={description} rows={3} maxLength={240} onChange={function (e) { setDescription(e.target.value); }} />
          <label>{t("create.project.workspacePath")}</label>
          <input value={workspacePath} onChange={function (e) { setWorkspacePath(e.target.value); }} />
          <label>{t("create.project.color")}</label>
          <input className="workbench-project-color-input" type="color" value={color || "#22b07a"} onChange={function (e) { setColor(e.target.value); }} />
        </div>
        {error && <div className="workbench-project-edit-error">{error}</div>}
        <div className="workbench-project-edit-foot">
          <button type="button" className="wb-btn ghost" disabled={busy} onClick={onClose}>{t("common.cancel")}</button>
          <button type="button" className="wb-btn primary" disabled={busy} onClick={save}>{busy ? t("settings.saving") : t("common.save")}</button>
        </div>
      </div>
    </div>
  );
}

function ProjectRail({ projects, activeProjectId, activePage, collapsed, onToggleCollapse, onSelectProject, onCreateProject, onEditProject, onDeleteProject, onOpenPage, onSettings }) {
  var { t } = window.useWorkbenchI18n();
  window.useDataVersion();  // re-render chip when DATA.user changes (profile save); data.js loads before this bundle
  var [menuProjectId, setMenuProjectId] = useWorkbenchState("");
  var [accountMenuOpen, setAccountMenuOpen] = useWorkbenchState(false);
  var [budgetState, setBudgetState] = useWorkbenchState(null);

  // Fetch budget status from API (also pinged when the account menu opens)
  function fetchBudget() {
    fetch("/api/budget/status")
      .then(function (r) { return r.json(); })
      .then(function (d) { setBudgetState(d); })
      .catch(function () {});
  }
  useWorkbenchEffect(function () { fetchBudget(); function onFocus() { fetchBudget(); } try { window.addEventListener("wb-focus-composer", onFocus); } catch (e) {} return function () { try { window.removeEventListener("wb-focus-composer", onFocus); } catch (e) {} }; }, []);

  function formatTimeDiff(isoStr) {
    if (!isoStr) return "";
    var dt = new Date(isoStr);
    var now = new Date();
    var diff = dt - now;
    if (diff <= 0) return "";
    // Same day → show time only
    if (dt.toDateString() === now.toDateString()) {
      var hh = String(dt.getHours()).padStart(2, "0");
      var mm = String(dt.getMinutes()).padStart(2, "0");
      return hh + ":" + mm;
    }
    // Tomorrow → "明天 HH:MM"
    var tomorrow = new Date(now);
    tomorrow.setDate(tomorrow.getDate() + 1);
    if (dt.toDateString() === tomorrow.toDateString()) {
      var hh2 = String(dt.getHours()).padStart(2, "0");
      var mm2 = String(dt.getMinutes()).padStart(2, "0");
      return t("general.tomorrow", null, "Tomorrow") + " " + hh2 + ":" + mm2;
    }
    // Further → "M/D HH:MM"
    return (dt.getMonth() + 1) + "/" + dt.getDate() + " " + String(dt.getHours()).padStart(2, "0") + ":" + String(dt.getMinutes()).padStart(2, "0");
  }

  function formatRefreshTime(isoStr, tFn) {
    var time = formatTimeDiff(isoStr);
    return time ? tFn("rail.refreshAt", { time: time }) : tFn("rail.budgetExhausted");
  }
  function cs(curr) { return curr === "CNY" ? "¥" : curr === "USD" ? "$" : curr === "EUR" ? "€" : curr === "GBP" ? "£" : curr || ""; }
  function formatBudgetAmount(v, curr) { return cs(curr) + v.toFixed(2); }
  // Re-fetch whenever the menu opens so the user sees fresh data
  useWorkbenchEffect(function () {
    if (accountMenuOpen) fetchBudget();
  }, [accountMenuOpen]);
  // Re-fetch when budget settings are saved in the settings panel
  useWorkbenchEffect(function () {
    function onSaved() { fetchBudget(); }
    try { window.addEventListener("budget-saved", onSaved); } catch (e) {}
    return function () { try { window.removeEventListener("budget-saved", onSaved); } catch (e) {} };
  }, []);

  useWorkbenchEffect(function () {
    if (!menuProjectId) return undefined;
    function closeMenu(event) {
      if (event.key && event.key !== "Escape") return;
      if (!event.key && event.target && event.target.closest && event.target.closest(".workbench-project-card.menu-open")) return;
      setMenuProjectId("");
    }
    document.addEventListener("mousedown", closeMenu);
    document.addEventListener("keydown", closeMenu);
    return function () {
      document.removeEventListener("mousedown", closeMenu);
      document.removeEventListener("keydown", closeMenu);
    };
  }, [menuProjectId]);

  useWorkbenchEffect(function () {
    if (!accountMenuOpen) return undefined;
    function closeMenu(event) {
      if (event.key && event.key !== "Escape") return;
      if (!event.key && event.target && event.target.closest && event.target.closest(".workbench-account-outer")) return;
      setAccountMenuOpen(false);
    }
    document.addEventListener("mousedown", closeMenu);
    document.addEventListener("keydown", closeMenu);
    return function () {
      document.removeEventListener("mousedown", closeMenu);
      document.removeEventListener("keydown", closeMenu);
    };
  }, [accountMenuOpen]);

  var navItems = [
    { id: "task", label: t("workbench.page.task"), icon: (
      <svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round"><path d="M9 5H7a2 2 0 0 0-2 2v12a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V7a2 2 0 0 0-2-2h-2"/><rect x="9" y="3" width="6" height="4" rx="1.5"/><path d="M9 14 10.5 15.5 15 11"/></svg>
    ), action: function () { onOpenPage("task"); } },
    { id: "chat", label: t("workbench.page.chat"), icon: (
      <svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round"><path d="M21 11.5a8.5 8.5 0 0 1-12.2 7.6L3 21l1.9-5.8A8.5 8.5 0 1 1 21 11.5Z"/></svg>
    ), action: function () { onOpenPage("chat"); } },
    { id: "knowledge", label: t("workbench.page.knowledge"), icon: (
      <svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round"><path d="M5 4.5A2.5 2.5 0 0 1 7.5 2H20v15H7.5A2.5 2.5 0 0 0 5 19.5Z"/><path d="M5 19.5A2.5 2.5 0 0 0 7.5 22H20"/></svg>
    ), action: function () { onOpenPage("knowledge"); } },
    { id: "schedule", label: t("workbench.page.schedule"), icon: (
      <svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round"><rect x="3" y="4.5" width="18" height="17" rx="2.5"/><path d="M3 9.5h18M8 2.5v4M16 2.5v4"/></svg>
    ), action: function () { onOpenPage("schedule"); } },
    { id: "memory", label: t("workbench.page.memory"), icon: (
      <svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round"><path d="M12 4 13.6 10.4 20 12 13.6 13.6 12 20 10.4 13.6 4 12 10.4 10.4Z"/></svg>
    ), action: function () { onOpenPage("memory"); } },
  ];
  return (
    <aside className="workbench-project-rail" onMouseLeave={function () { if (collapsed) setAccountMenuOpen(false); }}>
      <div className="workbench-rail-head">
        <span className="wb-rail-title">{t("rail.projects")}</span>
        <div className="workbench-rail-head-actions">
          <button type="button" className="workbench-add-btn" onClick={onCreateProject} title={t("rail.newProject")}>
            <span>
              <svg viewBox="0 0 24 24" width="12" height="12" fill="none" stroke="currentColor" strokeWidth="3" strokeLinecap="round"><path d="M12 5v14M5 12h14"/></svg>
            </span>
            <span>{t("rail.newProject")}</span>
          </button>
          <button
            type="button"
            className="workbench-rail-collapse-btn"
            title={collapsed ? t("rail.expand", null, "Expand sidebar") : t("rail.collapse", null, "Collapse sidebar")}
            aria-label={collapsed ? t("rail.expand", null, "Expand sidebar") : t("rail.collapse", null, "Collapse sidebar")}
            onClick={function () { setAccountMenuOpen(false); onToggleCollapse(); }}
          >
            {collapsed ? (
              <svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"><path d="m13 7 5 5-5 5M6 7l5 5-5 5"/></svg>
            ) : (
              <svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"><path d="m11 7-5 5 5 5M18 7l-5 5 5 5"/></svg>
            )}
          </button>
        </div>
      </div>
      <div className={"workbench-project-list" + (menuProjectId ? " has-open-menu" : "")}>
        {projects.map(function (project) {
          var active = project.id === activeProjectId;
          var isCyrene = project.dataKey === "default" || project.name === "Cyrene";
          var menuOpen = menuProjectId === project.id;
          return (
            <div
              key={project.id}
              className={"workbench-project-card" + (active ? " active" : "") + (menuOpen ? " menu-open" : "")}
              title={project.workspacePath}
            >
              <button type="button" className="workbench-project-main" onClick={function () { onSelectProject(project.id); setMenuProjectId(""); }}>
                <span
                  className={"workbench-project-icon" + (isCyrene ? " logo" : "")}
                  style={isCyrene ? null : { background: project.color || WorkbenchModel.projectGradient(project.id || project.name) }}
                >{isCyrene ? <span className="brand-mark" aria-hidden="true"></span> : WorkbenchModel.initials(project.name)}</span>
                <span className="workbench-project-meta">
                  <b>{project.name}</b>
                  <small title={project.workspacePath || ""}>{WorkbenchModel.pathLabel(project.workspacePath, project.name)}</small>
                </span>
              </button>
              <button
                type="button"
                className="workbench-project-menu-btn"
                title={t("rail.projectActions")}
                onClick={function (e) {
                  e.stopPropagation();
                  setMenuProjectId(menuOpen ? "" : project.id);
                }}
              >
                <svg viewBox="0 0 24 24" width="16" height="16" fill="currentColor"><circle cx="5" cy="12" r="1.8" /><circle cx="12" cy="12" r="1.8" /><circle cx="19" cy="12" r="1.8" /></svg>
              </button>
              {menuOpen && (
                <div className="workbench-project-menu">
                  <button type="button" onClick={function () { setMenuProjectId(""); onEditProject(project); }}>{t("rail.editProject")}</button>
                  {project.dataKey !== "default" && (
                    <button type="button" className="danger" onClick={function () { setMenuProjectId(""); onDeleteProject(project); }}>{t("rail.deleteProject")}</button>
                  )}
                </div>
              )}
            </div>
          );
        })}
      </div>
      <div className="workbench-global-nav">
        {navItems.map(function (item) {
          return (
            <button key={item.id} type="button" title={item.label} className={"workbench-nav-button" + ((activePage === item.id || (item.id === "task" && !activePage)) ? " active" : "")} onClick={function (event) {
              item.action();
              // A mouse click should reveal the destination immediately. Keep
              // keyboard focus intact for keyboard navigation, but release the
              // collapsed rail's focus-within hover expansion after pointer use.
              if (collapsed && Number(event.detail || 0) > 0) event.currentTarget.blur();
            }}>
              <span className="workbench-nav-icon">{item.icon}</span>
              <span>{item.label}</span>
            </button>
          );
        })}
      </div>
      <div className="workbench-account-outer">
        <div
          className={"workbench-account" + (activePage === "profile" ? " active" : "")}
          onClick={function () { setAccountMenuOpen(function (v) { return !v; }); }}
        >
          <span
            className="workbench-account-avatar"
            title={t("rail.profile")}
            onClick={function (e) { e.stopPropagation(); setAccountMenuOpen(false); onOpenPage && onOpenPage("profile"); }}
            onKeyDown={function (e) { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); setAccountMenuOpen(false); onOpenPage && onOpenPage("profile"); } }}
            tabIndex={0}
            role="button"
          >
            {window.WorkbenchAvatar
              ? React.createElement(window.WorkbenchAvatar, { user: DATA.user, size: 34 })
              : <div className="workbench-avatar photo">{WorkbenchModel.initials(DATA.user && DATA.user.name)}</div>}
          </span>
          <div className="workbench-account-meta">
            <div className="workbench-account-name">
              <b>{DATA.user && DATA.user.name || "User"}</b>
              <span className="workbench-pro-badge">Pro</span>
            </div>
            <small>{(DATA.sessions && DATA.sessions[0] && DATA.sessions[0].model) || DATA.appVersion || "model"}</small>
          </div>
        </div>
        {accountMenuOpen && (
          <div className="workbench-account-menu" onClick={function (e) { e.stopPropagation(); }}>
            {budgetState && budgetState.monthly_budget > 0 && (
              <>
                <div className="wb-account-menu-usage">
                  <div className="wb-account-menu-usage-row">
                    <span>{t("rail.budgetFiveHour")}</span>
                    <span className={"wb-account-menu-usage-val" + (budgetState.five_hour_remaining <= 0 ? " over" : "")}>
                      {budgetState.five_hour_remaining > 0
                        ? (budgetState.five_hour_remaining / budgetState.five_hour_budget * 100).toFixed(0) + "% · " + formatBudgetAmount(budgetState.five_hour_remaining, budgetState.currency) + " / " + formatBudgetAmount(budgetState.five_hour_budget, budgetState.currency)
                        : formatRefreshTime(budgetState.five_hour_next_refresh_at, t)}
                    </span>
                  </div>
                  <div className="wb-account-menu-usage-row">
                    <span>{t("rail.budgetWeekly")}</span>
                    <span className={"wb-account-menu-usage-val" + (budgetState.weekly_remaining <= 0 ? " over" : "")}>
                      {budgetState.weekly_remaining > 0
                        ? (budgetState.weekly_remaining / budgetState.weekly_budget * 100).toFixed(0) + "% · " + formatBudgetAmount(budgetState.weekly_remaining, budgetState.currency) + " / " + formatBudgetAmount(budgetState.weekly_budget, budgetState.currency)
                        : formatRefreshTime(budgetState.weekly_next_refresh_at, t)}
                    </span>
                  </div>
                </div>
                {budgetState.weekly_remaining <= 0 || budgetState.five_hour_remaining <= 0 || budgetState.monthly_remaining <= 0 ? (
                  <div className="wb-account-menu-usage-blocked">{t("rail.budgetBlocked")}</div>
                ) : null}
                <div className="wb-account-menu-divider"></div>
              </>
            )}
            <button type="button" onClick={function () { setAccountMenuOpen(false); onSettings && onSettings(); }}>
              <svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round"><circle cx="12" cy="12" r="3"/><path d="M12 1v2M12 21v2M4.2 4.2l1.4 1.4M18.4 18.4l1.4 1.4M1 12h2M21 12h2M4.2 19.8l1.4-1.4M18.4 5.6l1.4-1.4"/></svg>
              {t("rail.settings")}
            </button>
            <button type="button" onClick={function () { setAccountMenuOpen(false); window.open("https://docs.cyrene.77497856.xyz/#overview", "_blank"); }}>
              <svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round"><circle cx="12" cy="12" r="10"/><path d="M9.1 9a3 3 0 0 1 5.8 1c0 2-3 3-3 3"/><path d="M12 17h0"/></svg>
              {t("rail.learnMore")}
            </button>
            <button type="button" onClick={function () { setAccountMenuOpen(false); onOpenPage && onOpenPage("profile"); }}>
              <svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round"><path d="M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2"/><circle cx="12" cy="7" r="4"/></svg>
              {t("rail.profile")}
            </button>
            <div className="wb-account-menu-divider"></div>
            <button type="button" className="danger" onClick={function () { setAccountMenuOpen(false); }}>
              <svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round"><path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4"/><polyline points="16 17 21 12 16 7"/><line x1="21" y1="12" x2="9" y2="12"/></svg>
              {t("rail.logout")}
            </button>
          </div>
        )}
      </div>
    </aside>
  );
}

var WB_TASK_BOARD_COLUMNS = [
  { id: "planning", labelKey: "taskBoard.column.planning" },
  { id: "executing", labelKey: "taskBoard.column.executing" },
  { id: "review", labelKey: "taskBoard.column.review" },
  { id: "completed", labelKey: "taskBoard.column.completed" },
  { id: "blocked", labelKey: "taskBoard.column.blocked" },
];

function wbTaskBoardColumnKey(status) {
  var raw = String(status || "idle");
  if (["running", "answered", "acted", "waiting_for_user", "waiting_for_approval", "paused"].indexOf(raw) >= 0) return "executing";
  if (["review", "done"].indexOf(raw) >= 0) return "review";
  if (["completed", "skipped"].indexOf(raw) >= 0) return "completed";
  if (["blocked", "failed", "cancelled"].indexOf(raw) >= 0) return "blocked";
  return "planning";
}

function TaskBoard({ project, loading, error, onOpenSession, onCreateSession, onDeleteSession }) {
  var { t } = window.useWorkbenchI18n();
  var sessions = project && Array.isArray(project.sessions) ? project.sessions : [];
  var [recentFirst, setRecentFirst] = useWorkbenchState(false);
  var [menuId, setMenuId] = useWorkbenchState("");
  var [completedOpen, setCompletedOpen] = useWorkbenchState(true);

  useWorkbenchEffect(function () {
    setMenuId("");
  }, [project && project.id]);

  var visibleSessions = sessions;
  if (recentFirst) {
    visibleSessions = visibleSessions.slice().sort(function (a, b) {
      return String(b.updatedAt || b.createdAt || "").localeCompare(String(a.updatedAt || a.createdAt || ""));
    });
  }
  var completedSessions = sessions.filter(function (session) {
    return wbTaskBoardColumnKey(session.status) === "completed";
  });

  if (!project) {
    return (
      <main className="workbench-task-board wb-board-no-project">
        <div className="wb-board-empty-overall">
          <b>{t("taskBoard.noProject")}</b>
          <span>{t("taskBoard.noProjectHint")}</span>
        </div>
      </main>
    );
  }

  return (
    <main className="workbench-task-board" aria-label={t("taskBoard.title")}>
      {menuId && <div className="wb-card-menu-scrim" onClick={function () { setMenuId(""); }} />}
      <header className="wb-board-header">
        <div className="wb-board-heading">
          <span className="wb-board-kicker">{t("taskBoard.title")}</span>
          <h1>{project.name}</h1>
          <p>{project.description || t("taskBoard.subtitle")}</p>
        </div>
        <div className="wb-board-toolbar">
          <button type="button" className={"wb-board-tool-btn" + (recentFirst ? " active" : "")} onClick={function () { setRecentFirst(!recentFirst); }}>
            {recentFirst ? t("taskBoard.sortRecent") : t("taskBoard.sortDefault")}
          </button>
          <button type="button" className="wb-board-new-btn" onClick={onCreateSession}>{t("taskBoard.newTask")}</button>
        </div>
      </header>
      {error && <div className="workbench-error wb-board-error">{error}</div>}
      {loading && sessions.length === 0 ? (
        <div className="wb-board-loading">{t("rail.loadingTasks")}</div>
      ) : (
        <div className="wb-board-scroll">
          <div className="wb-board-columns">
            {WB_TASK_BOARD_COLUMNS.map(function (column) {
              var cards = visibleSessions.filter(function (session) {
                return wbTaskBoardColumnKey(session.status) === column.id;
              });
              return (
                <section key={column.id} className={"wb-board-column is-" + column.id} aria-label={t(column.labelKey)}>
                  <header className="wb-board-column-head">
                    <span className="wb-board-column-title">{t(column.labelKey)}</span>
                    <span className="wb-board-column-count">{cards.length}</span>
                    <button type="button" onClick={onCreateSession} aria-label={t("taskBoard.addInColumn", { stage: t(column.labelKey) })}>{t("taskBoard.add")}</button>
                  </header>
                  <div className="wb-board-column-body">
                    {cards.map(function (session) {
                      return (
                        <TaskBoardCard
                          key={session.id}
                          session={session}
                          column={column.id}
                          menuOpen={menuId === session.id}
                          onMenu={function () { setMenuId(menuId === session.id ? "" : session.id); }}
                          onOpen={function () { setMenuId(""); onOpenSession(session.id); }}
                          onDelete={function () { setMenuId(""); onDeleteSession && onDeleteSession(session); }}
                        />
                      );
                    })}
                    {cards.length === 0 && (
                      <div className="wb-board-column-empty">
                        <b>{column.id === "blocked" ? t("taskBoard.emptyBlocked") : t("taskBoard.empty")}</b>
                        <span>{column.id === "blocked" ? t("taskBoard.emptyBlockedHint") : t("taskBoard.emptyHint")}</span>
                      </div>
                    )}
                  </div>
                  <button type="button" className="wb-board-column-add" onClick={onCreateSession}>{t("taskBoard.newTask")}</button>
                </section>
              );
            })}
          </div>
        </div>
      )}
      {completedSessions.length > 0 && (
        <section className="wb-board-completed-strip">
          <button type="button" className="wb-board-completed-toggle" onClick={function () { setCompletedOpen(!completedOpen); }} aria-expanded={completedOpen}>
            <span>{t("taskBoard.completedStrip", { count: completedSessions.length })}</span>
            <small>{completedOpen ? t("common.collapse") : t("common.expand")}</small>
          </button>
          {completedOpen && (
            <div className="wb-board-completed-list">
              {completedSessions.map(function (session) {
                return (
                  <button key={session.id} type="button" onClick={function () { onOpenSession(session.id); }} title={session.title}>
                    <span className="wb-board-completed-check">{ICONS.checkSmall}</span>
                    <span>{session.title}</span>
                  </button>
                );
              })}
            </div>
          )}
        </section>
      )}
    </main>
  );
}

function TaskBoardCard({ session, column, menuOpen, onMenu, onOpen, onDelete }) {
  var { t } = window.useWorkbenchI18n();
  var tone = WorkbenchModel.statusTone(session.status);
  var summary = sessionSummaryText(session);
  var stepCount = Number(session.planStepCount != null ? session.planStepCount : (Array.isArray(session.plan) ? session.plan.length : 0));
  return (
    <article
      role="button"
      tabIndex={0}
      className={"wb-board-card is-" + column + (menuOpen ? " menu-open" : "")}
      onClick={onOpen}
      onKeyDown={function (event) {
        if (event.key === "Enter" || event.key === " ") { event.preventDefault(); onOpen(); }
      }}
    >
      <div className="wb-board-card-title">
        <span className={"workbench-status-dot " + tone}></span>
        <b>{session.title}</b>
        <button type="button" className="wb-card-menu-btn wb-board-card-menu-btn" onClick={function (event) { event.stopPropagation(); onMenu(); }} aria-label={t("common.moreActions")}>{ICONS.dots}</button>
      </div>
      {summary && summary !== session.title && <p>{summary}</p>}
      <div className="wb-board-card-meta">
        <span className={"workbench-task-status " + tone}>{WorkbenchModel.statusText(session.status)}</span>
        {stepCount > 0 && <span>{t("taskBoard.steps", { count: stepCount })}</span>}
        <time>{WorkbenchModel.formatRelativeTime(session.updatedAt || session.createdAt)}</time>
      </div>
      {menuOpen && (
        <div className="wb-card-menu wb-board-card-menu" onClick={function (event) { event.stopPropagation(); }}>
          <button type="button" className="danger" onClick={onDelete}>{t("rail.deleteTask")}</button>
        </div>
      )}
    </article>
  );
}

function TaskRail({ project, activeSessionId, onSelectSession, onCreateSession, onDeleteSession, loading }) {
  var { t } = window.useWorkbenchI18n();
  var sessions = project && Array.isArray(project.sessions) ? project.sessions : [];
  var [menuId, setMenuId] = useWorkbenchState("");

  return (
    <aside className="workbench-task-rail">
      <div className="workbench-rail-head">
        <span>{t("rail.tasks")}</span>
        <button type="button" onClick={onCreateSession} disabled={!project}>+ {t("rail.newTask")}</button>
      </div>
      {menuId && <div className="wb-card-menu-scrim" onClick={function () { setMenuId(""); }} />}
      {loading && <div className="workbench-muted">{t("rail.loadingTasks")}</div>}
      {!loading && sessions.length === 0 && <div className="workbench-muted">{t("rail.noTasks")}</div>}
      <div className="workbench-task-list">
        {sessions.map(function (session) {
          var tone = WorkbenchModel.statusTone(session.status);
          var isMenuOpen = menuId === session.id;
          return (
            <div
              key={session.id}
              role="button"
              tabIndex={0}
              className={"workbench-task-card" + (session.id === activeSessionId ? " active" : "") + (isMenuOpen ? " menu-open" : "")}
              onClick={function () { setMenuId(""); onSelectSession(session.id); }}
              onKeyDown={function (e) { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); onSelectSession(session.id); } }}
            >
              <span className="workbench-task-top">
                <span className={"workbench-status-dot " + tone}></span>
                <b>{session.title}</b>
              </span>
              <span className="workbench-task-bottom">
                <span className={"workbench-task-status " + tone}>
                  {tone === "muted" && <i className="wb-status-ico">◷</i>}
                  {WorkbenchModel.statusText(session.status)}
                </span>
                <time>{WorkbenchModel.formatTime(session.updatedAt || session.createdAt)}</time>
              </span>
              <div className="wb-card-actions">
                <button
                  type="button"
                  className="wb-card-menu-btn"
                  title={t("common.moreActions")}
                  onClick={function (e) { e.stopPropagation(); setMenuId(isMenuOpen ? "" : session.id); }}
                >
                  {ICONS.dots}
                </button>
                {isMenuOpen && (
                  <div className="wb-card-menu">
                    <button type="button" className="danger" onClick={function (e) {
                      e.stopPropagation();
                      setMenuId("");
                      onDeleteSession && onDeleteSession(session);
                    }}>{t("rail.deleteTask")}</button>
                  </div>
                )}
              </div>
            </div>
          );
        })}
      </div>
    </aside>
  );
}

// ===================================================================
// Task execution console — the Subtask state machine.
// idle → planning → waiting_for_approval → running → review →
// completed, with paused / failed / cancelled branches. Driven from
// the client via model.patchSession(); real agent work via createRun().
// ===================================================================

var ICONS = {
  target: <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"><circle cx="12" cy="12" r="9"/><circle cx="12" cy="12" r="5"/><circle cx="12" cy="12" r="1.4" fill="currentColor"/></svg>,
  spark: <svg viewBox="0 0 24 24" width="18" height="18" fill="currentColor"><path d="M12 2.5 13.7 9 20 10.7 13.7 12.4 12 19l-1.7-6.6L4 10.7 10.3 9Z"/></svg>,
  shield: <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"><path d="M12 3 5 6v5c0 4.2 2.8 7.7 7 9 4.2-1.3 7-4.8 7-9V6Z"/><path d="m9.2 12 2 2 3.6-3.8"/></svg>,
  pause: <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" strokeWidth="1.9" strokeLinecap="round" strokeLinejoin="round"><path d="M9 5v14M15 5v14"/></svg>,
  dots: <svg viewBox="0 0 24 24" width="18" height="18" fill="currentColor"><circle cx="5.5" cy="12" r="1.7"/><circle cx="12" cy="12" r="1.7"/><circle cx="18.5" cy="12" r="1.7"/></svg>,
  edit: <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" strokeWidth="1.9" strokeLinecap="round" strokeLinejoin="round"><path d="M12 20h9"/><path d="M16.5 3.5a2.1 2.1 0 0 1 3 3L7 19l-4 1 1-4Z"/></svg>,
  alert: <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"><path d="M10.3 4 2.5 18a1.5 1.5 0 0 0 1.3 2.3h16.4A1.5 1.5 0 0 0 21.5 18L13.7 4a1.5 1.5 0 0 0-3.4 0Z"/><path d="M12 9v4.5M12 17h.01"/></svg>,
  check: <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" strokeWidth="1.9" strokeLinecap="round" strokeLinejoin="round"><circle cx="12" cy="12" r="9"/><path d="m8.5 12 2.4 2.4 4.6-4.8"/></svg>,
  x: <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" strokeWidth="1.9" strokeLinecap="round" strokeLinejoin="round"><circle cx="12" cy="12" r="9"/><path d="m9 9 6 6M15 9l-6 6"/></svg>,
  attach: <svg viewBox="0 0 24 24" width="17" height="17" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"><path d="m21.44 11.05-9.19 9.19a5 5 0 0 1-7.07-7.07l9.19-9.19a3.5 3.5 0 0 1 4.95 4.95l-9.2 9.19a2 2 0 0 1-2.83-2.83l8.49-8.48"/></svg>,
  slash: <svg viewBox="0 0 24 24" width="17" height="17" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"><rect x="3" y="4" width="18" height="16" rx="2.5"/><path d="m7.5 9.5 2.5 2.5-2.5 2.5"/><path d="M12.5 15h4"/></svg>,
  send: <svg viewBox="0 0 24 24" width="17" height="17" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M22 2 11 13M22 2l-7 20-4-9-9-4Z"/></svg>,
  stop: <svg viewBox="0 0 24 24" width="15" height="15" fill="currentColor" stroke="none"><rect x="5" y="5" width="14" height="14" rx="2.5"/></svg>,
  modeDefault: <svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"><path d="M12 3 5 6v5c0 4.2 2.8 7.7 7 9 4.2-1.3 7-4.8 7-9V6Z"/></svg>,
  modeAuto: <svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"><path d="M13 2 4 14h7l-1 8 9-12h-7l1-8Z"/></svg>,
  modePlan: <svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"><rect x="6" y="4" width="12" height="17" rx="2"/><path d="M9.5 3.5h5v3h-5z"/><path d="M9 11h6M9 15h4"/></svg>,
  modeFull: <svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"><rect x="5" y="11" width="14" height="9" rx="2"/><path d="M8 11V8a4 4 0 0 1 7.6-1.7"/></svg>,
  cmdQuick: <svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round"><path d="M13 2 4 14h7l-1 8 9-12h-7l1-8Z"/></svg>,
  cmdResearch: <svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round"><circle cx="11" cy="11" r="7"/><path d="m21 21-4.3-4.3"/></svg>,
  cmdReflect: <svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round"><path d="M9 18h6M10 22h4"/><path d="M12 2a7 7 0 0 0-4 12.7c.6.5 1 1.3 1 2.1v.2h6v-.2c0-.8.4-1.6 1-2.1A7 7 0 0 0 12 2Z"/></svg>,
  cmdDecide: <svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round"><path d="M12 3v18M5 7h14M8 21h8"/><path d="M5 7 2.5 13a3.5 3.5 0 0 0 5 0ZM19 7l-2.5 6a3.5 3.5 0 0 0 5 0Z"/></svg>,
  cmdLearn: <svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round"><path d="M4 5a2 2 0 0 1 2-2h13v16H6a2 2 0 0 0-2 2Z"/><path d="M4 19a2 2 0 0 0 2 2h13"/></svg>,
  cmdReview: <svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round"><path d="M21 12.8A9 9 0 1 1 11.2 3 7 7 0 0 0 21 12.8Z"/></svg>,
  cmdCompare: <svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round"><path d="M7 4 3 8l4 4M3 8h13M17 20l4-4-4-4M21 16H8"/></svg>,
  cmdCode: <svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round"><path d="m8 8-4 4 4 4M16 8l4 4-4 4"/></svg>,
  checkSmall: <svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round"><path d="m5 12.5 4.5 4.5L19 7"/></svg>,
};

function wbT(key, fallback, params) {
  if (window.WorkbenchI18n && typeof window.WorkbenchI18n.t === "function") {
    return window.WorkbenchI18n.t(key, params, fallback);
  }
  if (params && fallback) {
    Object.keys(params).forEach(function (name) {
      fallback = fallback.split("{" + name + "}").join(String(params[name]));
    });
  }
  return fallback || key;
}

// Permission modes for the composer mode-switcher (mirrors the legacy chat
// modes; the workbench default is "auto" since it executes tasks).
var WB_MODES = [
  { id: "default", labelKey: "workbenchChat.mode.default.label", descKey: "workbenchChat.mode.default.desc", icon: ICONS.modeDefault },
  { id: "auto", labelKey: "workbenchChat.mode.auto.label", descKey: "workbenchChat.mode.auto.desc", icon: ICONS.modeAuto },
  { id: "plan", labelKey: "workbenchChat.mode.plan.label", descKey: "workbenchChat.mode.plan.desc", icon: ICONS.modePlan },
  { id: "full_access", labelKey: "workbenchChat.mode.full_access.label", descKey: "workbenchChat.mode.full_access.desc", icon: ICONS.modeFull },
];

function wbModeMeta(id) {
  var meta = WB_MODES[1];
  for (var i = 0; i < WB_MODES.length; i++) {
    if (WB_MODES[i].id === id) meta = WB_MODES[i];
  }
  return { ...meta, label: wbT(meta.labelKey, meta.id), desc: wbT(meta.descKey, "") };
}

function isDoneStepStatus(status) {
  return status === "completed" || status === "done";
}

// A step the user no longer needs to act on: completed OR explicitly skipped.
// Used to find the "next" runnable step and to decide when the plan is finished.
function isResolvedStepStatus(status) {
  return isDoneStepStatus(status) || status === "skipped";
}

function isRunningStepStatus(status) {
  return status === "running";
}

function stepExecutionPrompt(session, step) {
  var lines = [
    "请为当前任务计划中的这个步骤生成一个 subagent 执行，并在完成后汇总结果。",
    "当前任务：" + String((session && (session.goal || session.title)) || "").trim(),
    "步骤：" + String((step && step.title) || "").trim(),
  ];
  if (step && step.description) lines.push("步骤说明：" + String(step.description).trim());
  return lines.filter(Boolean).join("\n");
}

// Pre-run context files the user pinned for a step, split by source: workspace
// path references (read by the subagent's file tools) vs uploaded attachments.
function splitStepContextFiles(step) {
  var files = (step && Array.isArray(step.contextFiles)) ? step.contextFiles : [];
  var workspace = [];
  var uploads = [];
  files.forEach(function (f) {
    if (!f) return;
    if (f.source === "upload") uploads.push(f);
    else workspace.push(f);
  });
  return { workspace: workspace, uploads: uploads };
}

// The prompt actually sent to the subagent: the user's edited override (or the
// default), plus a reference block for any workspace context files they pinned.
function effectiveStepPrompt(session, step) {
  var override = (step && typeof step.promptOverride === "string") ? step.promptOverride.trim() : "";
  var base = override || stepExecutionPrompt(session, step);
  var workspace = splitStepContextFiles(step).workspace;
  if (workspace.length) {
    var rows = workspace
      .map(function (f) { return "- " + String((f && (f.path || f.name)) || "").trim(); })
      .filter(function (row) { return row !== "- "; });
    if (rows.length) {
      base += "\n\n请重点参考以下工作区文件（先用 read_file 等工具阅读再动手）：\n" + rows.join("\n");
    }
  }
  return base;
}

function formatDurationSec(sec) {
  if (!Number.isFinite(sec) || sec < 1) return "";
  sec = Math.max(1, Math.round(sec));
  if (sec < 60) return sec + "s";
  var min = Math.floor(sec / 60);
  var rest = sec % 60;
  if (min < 60) return rest ? (min + "m " + rest + "s") : (min + "m");
  var hour = Math.floor(min / 60);
  var remMin = min % 60;
  return remMin ? (hour + "h " + remMin + "m") : (hour + "h");
}

// Duration of a step, in priority order: an explicit recorded `durationSec`,
// then the startedAt→completedAt/updatedAt span, then the first/last
// progress-event timestamps. Returns "" when nothing reliable is known.
function stepDurationText(step) {
  if (!step) return "";
  if (Number.isFinite(step.durationSec)) return formatDurationSec(step.durationSec);
  var startMs = step.startedAt ? Date.parse(step.startedAt) : NaN;
  var endMs = (step.completedAt || step.updatedAt) ? Date.parse(step.completedAt || step.updatedAt) : NaN;
  if (Number.isFinite(startMs) && Number.isFinite(endMs) && endMs > startMs) {
    return formatDurationSec((endMs - startMs) / 1000);
  }
  if (Array.isArray(step.progressEvents) && step.progressEvents.length >= 2) {
    var first = Date.parse(step.progressEvents[0] && step.progressEvents[0].time || "");
    var last = Date.parse(step.progressEvents[step.progressEvents.length - 1] && step.progressEvents[step.progressEvents.length - 1].time || "");
    if (Number.isFinite(first) && Number.isFinite(last) && last > first) {
      return formatDurationSec((last - first) / 1000);
    }
  }
  return "";
}

function stepMetaText(step) {
  var duration = stepDurationText(step);
  if (duration) return duration;
  if (!step) return "";
  if (isRunningStepStatus(step.status)) return "进行中";
  if (isDoneStepStatus(step.status)) return "已完成";
  if (step.status === "failed") return "需处理";
  if (step.status === "paused") return "已暂停";
  return "";
}

function useTaskController(session, onRefresh, runtime) {
  var model = window.WorkbenchModel;
  var [busy, setBusy] = useWorkbenchState(false);
  var runAbortRef = useWorkbenchRef(null);
  var interruptedRef = useWorkbenchRef(false);
  var sid = session ? session.id : "";

  function apply(next) { if (onRefresh && next && !next.__budgetBlock) onRefresh(next); return next; }
  function sessionFromStore(next, fallback) {
    if (!next || !sid) return fallback || session;
    var projects = Array.isArray(next.projects) ? next.projects : [];
    for (var i = 0; i < projects.length; i++) {
      var sessions = Array.isArray(projects[i].sessions) ? projects[i].sessions : [];
      for (var j = 0; j < sessions.length; j++) {
        if (sessions[j] && sessions[j].id === sid) return sessions[j];
      }
    }
    if (next.activeSession && next.activeSession.id === sid) return next.activeSession;
    return fallback || session;
  }
  function fail(err) { window.showToast((err && err.message) || String(err), "error"); }
  function rethrowPlanConflict(err) {
    if (err && err.code === "stale_plan_revision") throw err;
  }
  function patch(p) { return model.patchSession(sid, p); }
  function run(promise) {
    setBusy(true);
    return promise.then(apply).catch(fail).finally(function () { setBusy(false); });
  }
  // Client-only "agent is working" marker that drives the 「Agent 正在处理」card
  // for background ops that don't enter the `running` status (规划 / 反思 / 验收).
  function setAgentBusy(op) {
    if (runtime && runtime.onLocalPatch) runtime.onLocalPatch({ agentBusy: op || null });
  }
  // Like run(), but also flips on the activity card + opens the live feed window
  // (events after startedAt are this op's). Cleared when the server response lands.
  function runAgentic(op, promise) {
    setBusy(true);
    setAgentBusy(Object.assign({ startedAt: new Date().toISOString() }, op || {}));
    return promise.then(apply).catch(fail).finally(function () { setBusy(false); setAgentBusy(null); });
  }
  function stepById(plan, stepId) {
    var items = Array.isArray(plan) ? plan : [];
    return items.find(function (item) { return item && item.id === stepId; }) || null;
  }
  function ensurePlanApproved(baseSession) {
    var current = baseSession || session;
    var definitionRevision = Number(current && current.planDefinitionRevision || 0);
    if (
      current
      && current.approvedPlanDefinitionRevision != null
      && Number(current.approvedPlanDefinitionRevision) === definitionRevision
    ) {
      return Promise.resolve(current);
    }
    return model.patchSession(sid, {
      approvedPlanDefinitionRevision: definitionRevision,
      events: model.withEvent(current, "PlanApproved", "用户确认执行当前版本的计划。"),
    })
      .then(function (store) {
        apply(store);
        return sessionFromStore(store, current);
      });
  }
  function requirePlan(baseSession) {
    var plan = baseSession && Array.isArray(baseSession.plan) ? baseSession.plan : [];
    if (plan.length) return true;
    window.showToast(wbT("task.plan.addAtLeastOneStep", "Add at least one step before approval or execution."), "warning");
    return false;
  }
  function stepFailedPatch(baseSession, basePlan, stepTitle, stepId, msg) {
    return model.patchSession(sid, {
      status: "failed",
      plan: model.markStepById(basePlan, stepId, "failed", msg),
      agentReply: "步骤执行失败：" + msg,
      events: model.withEvent(baseSession, "ExecutionFailed", "步骤「" + stepTitle + "」执行失败：" + msg, { stepId: stepId || "" }),
    }).then(apply);
  }
  function runStepCore(baseSession, stepId, options) {
    options = options || {};
    var basePlan = Array.isArray(baseSession && baseSession.plan) ? baseSession.plan : [];
    var step = stepById(basePlan, stepId);
    if (!baseSession || !step || !stepId) return Promise.resolve(null);
    interruptedRef.current = false;
    var ac = (typeof AbortController !== "undefined") ? new AbortController() : null;
    runAbortRef.current = ac;
    var index = basePlan.findIndex(function (item) { return item && item.id === stepId; });
    var stepTitle = String(step.title || ("步骤 " + (index + 1))).trim();
    var startPlan = model.markStepById(basePlan, stepId, "running", "正在启动 subagent，等待模型思考…");
    var startEvents = model.withEvent(baseSession, "ExecutionStarted", "开始执行步骤：" + stepTitle, { stepId: step.id || "" });
    return model.patchSession(sid, { status: "running", plan: startPlan, agentReply: "正在执行步骤：" + stepTitle, events: startEvents })
      .then(apply)
      .then(function (patched) {
        var patchedSession = sessionFromStore(patched, baseSession);
        var uploadCtx = splitStepContextFiles(step).uploads;
        return model.createRun(sid, effectiveStepPrompt(patchedSession, step), {
          attachments: uploadCtx.concat((runtime && runtime.attachments) || []),
          mode: (runtime && runtime.mode) || undefined,
          stepId: step.id || undefined,
          stepTitle: stepTitle,
          action: "spawn_subagent",
          meta: { scope: "plan_step", continueAll: !!options.continueAll },
          planDefinitionRevision: Number(patchedSession.planDefinitionRevision || 0),
          signal: ac ? ac.signal : undefined,
        });
      })
      .then(function (next) {
        var s2 = sessionFromStore(next, baseSession);
        if (String(s2.status || "") === "waiting_for_user") return next;
        var returnedPlan = Array.isArray(s2.plan) && s2.plan.length ? s2.plan : basePlan;
        // Real tool activity from this step's run → show it on the step.
        var latestRun = (Array.isArray(s2.runs) && s2.runs.length) ? s2.runs[s2.runs.length - 1] : null;
        var stepToolCalls = (latestRun && Array.isArray(latestRun.toolCalls)) ? latestRun.toolCalls : [];
        var doneAction = stepToolCalls.length ? ("已完成，本步调用工具 " + stepToolCalls.length + " 次。") : "已完成该步骤。";
        var completedPlan = model.markStepById(returnedPlan, stepId, "completed", doneAction).map(function (st) {
          return st && st.id === stepId ? Object.assign({}, st, { toolCalls: stepToolCalls }) : st;
        });
        var doneCount = completedPlan.filter(function (item) { return isResolvedStepStatus(item && item.status); }).length;
        var fullyDone = doneCount >= completedPlan.length && completedPlan.length > 0;
        var events2 = model.withEvent(s2, "ExecutionFinished", "步骤「" + stepTitle + "」执行完成。", { stepId: step.id || "" });
        var finalPatch = {
          status: fullyDone ? "review" : (options.continueAll ? "running" : "paused"),
          plan: completedPlan,
          events: events2,
        };
        if (options.continueAll && !fullyDone) {
          finalPatch.agentReply = "步骤「" + stepTitle + "」已完成，继续执行下一步。";
        }
        if (fullyDone) {
          // Don't auto-pass acceptance — those weren't verified. The user
          // checks each criterion in the 验收标准 panel, or confirms via 标记完成.
          finalPatch.artifacts = model.ensureArtifacts(s2);
        }
        if (runtime && runtime.clearAttachments) runtime.clearAttachments();
        return model.patchSession(sid, finalPatch);
      })
      .then(apply)
      .catch(function (err) {
        if (interruptedRef.current || (err && err.name === "AbortError")) return null;
        if (err && ["stale_plan_revision", "plan_not_approved", "unmet_dependencies", "step_not_found"].indexOf(err.code) >= 0) {
          throw err;
        }
        var msg = (err && err.message) || String(err);
        return stepFailedPatch(baseSession, basePlan, stepTitle, step.id || "", msg).then(function (next) {
          throw err;
        });
      })
      .finally(function () { runAbortRef.current = null; });
  }

  var ctrl = {
    busy: busy,
    applyStore: apply,

    // Intent-aware composer entry (idle / answered / acted). The server decides:
    // a question → a direct answer (status `answered`); a one-shot instruction →
    // execute + report (status `acted`); a complex goal → a plan (status
    // `planning`). The reply card follows the returned status. On total failure,
    // degrade to an honest client-side plan rather than swallowing the input.
    send: function (text) {
      var input = (text != null ? String(text) : "").trim();
      var hasAttach = (((runtime && runtime.attachments) || []).length > 0);
      if (!input && !hasAttach) return Promise.resolve();
      return runAgentic({ kind: "dispatch", label: "正在理解你的输入…" }, model.dispatch(sid, input, {
        attachments: (runtime && runtime.attachments) || [],
        mode: (runtime && runtime.mode) || undefined,
        basePlanRevision: Number(session.planRevision || 0),
      }).then(function (store) {
        if (runtime && runtime.clearAttachments) runtime.clearAttachments();
        return store;
      }).catch(function (err) {
        rethrowPlanConflict(err);
        // Budget errors: show toast and return sentinel so the composer
        // keeps the user's input in the draft instead of clearing it.
        var code = err.code || (err.payload && err.payload.code) || "";
        if (code.startsWith("budget_")) {
          var codes = window.WORKBENCH_BUDGET_CODES || {};
          var i18nKey = "budget.error." + (codes[code] || "5h");
          window.showToast(wbT(i18nKey, err.message || ""), "error");
          return { __budgetBlock: true };
        }
        var goal = (session.goal || input).trim();
        return patch({
          status: "planning",
          goal: goal,
          plan: model.buildPlanSteps(goal, session.constraints || []),
          acceptanceCriteria: model.buildAcceptance(goal, session.constraints || []),
          agentReply: "处理服务暂时不可用，我先给出一份基础计划，你可以编辑后逐步执行，或稍后重试。",
          events: model.withEvent(session, "PlanGenerated", "生成基础执行计划（兜底）。"),
        });
      }));
    },

    // Answer a paused run's permission / clarification question → resume the
    // round. The server returns either the continued reply or a follow-up
    // question; apply() swaps the card accordingly.
    answer: function (questionId, optionText) {
      var qid = String(questionId || "").trim();
      var ans = String(optionText || "").trim();
      if (!qid || !ans) return Promise.resolve();
      return runAgentic({ kind: "answer", label: "正在继续…" }, model.answer(sid, qid, ans))
        .then(function (store) {
          if (store && store.continuePlanExecution) {
            return ctrl.executeAll({ continuing: true, baseSession: store.activeSession });
          }
          return store;
        });
    },

    // answered / acted → promote this exchange into a real, planned task.
    promoteToPlan: function () {
      var goal = (session.goal || "").trim();
      if (!goal) { focusComposer(); return Promise.resolve(); }
      return runAgentic({ kind: "plan", label: "正在把它整理成执行计划…" }, model.generatePlan(sid, goal, { basePlanRevision: Number(session.planRevision || 0) }).catch(function (err) {
        rethrowPlanConflict(err);
        return patch({ status: "planning", goal: goal, plan: model.buildPlanSteps(goal, session.constraints || []), acceptanceCriteria: model.buildAcceptance(goal, session.constraints || []), agentReply: "计划生成服务暂时不可用，已生成基础计划。", events: model.withEvent(session, "PlanGenerated", "生成基础执行计划（兜底）。") });
      }));
    },

    // idle → planning. Generate a REAL plan from the goal — the agent explores
    // the project workspace server-side ("执行前必须有计划"); no agent work runs
    // yet. On failure, fall back to an honest client-side template (all pending).
    start: function (goalText) {
      var goal = (goalText != null ? String(goalText) : (session.goal || "")).trim();
      if (!goal) return Promise.resolve();
      return runAgentic({ kind: "plan", label: "正在分析任务并生成执行计划…" }, model.generatePlan(sid, goal, { basePlanRevision: Number(session.planRevision || 0) }).catch(function (err) {
        rethrowPlanConflict(err);
        var constraints = session.constraints || [];
        return patch({
          status: "planning",
          goal: goal,
          plan: model.buildPlanSteps(goal, constraints),
          acceptanceCriteria: model.buildAcceptance(goal, constraints),
          agentReply: "计划生成服务暂时不可用，已生成基础计划，你可以编辑后逐步执行，或稍后重试。",
          events: model.withEvent(session, "PlanGenerated", "生成基础执行计划（兜底）。"),
        });
      }));
    },

    // Empty task (no real goal) → 「直接开始」. The agent reads the project
    // workspace + notes server-side and proposes a plan to kick things off, so the
    // user doesn't have to phrase a goal first. Same path as start(), but seeded
    // with a project-derived default goal (passed in from the card so it follows
    // the UI language).
    autoStart: function () {
      return runAgentic({ kind: "plan", label: "正在阅读项目并规划…" }, model.generatePlan(sid, "", { autoStart: true, basePlanRevision: Number(session.planRevision || 0) }).catch(function (err) {
        rethrowPlanConflict(err);
        var basis = (session.goal || "").trim() || "推进本项目当前最该做的工作";
        return patch({
          status: "planning",
          plan: model.buildPlanSteps(basis, session.constraints || []),
          acceptanceCriteria: model.buildAcceptance(basis, session.constraints || []),
          agentReply: "计划生成服务暂时不可用，已生成一份基础计划，你可以编辑后逐步执行，或稍后重试。",
          events: model.withEvent(session, "PlanGenerated", "自动生成执行计划（兜底）。"),
        });
      }));
    },

    // Revise the plan from natural-language feedback. While the plan is still
    // untouched or already fully handled, regenerate it with the feedback. While
    // execution is still in progress, just record the note — regenerating would
    // wipe completed steps' progress (use 重新生成 explicitly to start over).
    modifyPlan: function (text) {
      var goal = (session.goal || "").trim();
      var plan = Array.isArray(session.plan) ? session.plan : [];
      if (model.hasUnresolvedStartedSteps(plan)) {
        return run(patch({
          agentReply: "已记录你的补充：\n" + text + "\n（任务已在执行中，计划未重置；如需重排可点「重新生成」。）",
          events: model.withEvent(session, "PlanRevised", "执行中补充：" + text),
        }));
      }
      return runAgentic({ kind: "plan", label: "正在结合你的补充重新规划…" }, model.generatePlan(sid, goal, { feedback: text, operation: "auto", basePlanRevision: Number(session.planRevision || 0) }).catch(function (err) {
        rethrowPlanConflict(err);
        var keepPlan = Array.isArray(session.plan) && session.plan.length ? session.plan : model.buildPlanSteps(goal, session.constraints || []);
        var keepAcceptance = Array.isArray(session.acceptanceCriteria) && session.acceptanceCriteria.length
          ? session.acceptanceCriteria
          : model.buildAcceptance(goal, session.constraints || []);
        return patch({
          status: "planning",
          plan: keepPlan,
          acceptanceCriteria: keepAcceptance,
          agentReply: "计划生成服务暂时不可用，已保留原计划并记录你的调整：\n" + text,
          events: model.withEvent(session, "PlanRevised", "按用户要求调整计划：" + text),
        });
      }));
    },

    regeneratePlan: function () {
      var goal = (session.goal || "").trim();
      return runAgentic({ kind: "plan", label: "正在重新生成执行计划…" }, model.generatePlan(sid, goal, {
        feedback: "请基于当前任务目标生成一份全新的执行计划，不保留原计划步骤。",
        operation: "replace",
        basePlanRevision: Number(session.planRevision || 0),
      }).catch(function (err) {
        rethrowPlanConflict(err);
        return patch({
          status: "planning",
          plan: Array.isArray(session.plan) ? session.plan : [],
          acceptanceCriteria: Array.isArray(session.acceptanceCriteria) ? session.acceptanceCriteria : [],
          agentReply: "重新生成失败，原计划保持不变，请稍后重试。",
          events: model.withEvent(session, "PlanGenerated", "重新生成执行计划失败，保留原计划。"),
        });
      }));
    },

    // planning → waiting_for_approval — the 需要你确认 gate before any change.
    approvePlan: function () {
      if (!requirePlan(session)) return Promise.resolve();
      var events = model.withEvent(session, "PlanApproved", "用户批准执行计划。");
      return run(patch({
        status: "waiting_for_approval",
        approvedPlanDefinitionRevision: Number(session.planDefinitionRevision || 0),
        agentReply: "执行前请确认下面的操作。",
        events: events,
      }));
    },

    // planning → 跳过单独确认，直接连续执行全部步骤。
    approveAndRunAll: function () {
      if (!requirePlan(session)) return Promise.resolve();
      return model.patchSession(sid, {
        approvedPlanDefinitionRevision: Number(session.planDefinitionRevision || 0),
        events: model.withEvent(session, "PlanApproved", "用户批准计划并连续执行全部步骤。"),
      }).then(apply).then(function (store) {
        return ctrl.executeAll({ baseSession: sessionFromStore(store, session) });
      });
    },

    configureGoalLoop: function () {
      return window.confirmModal({
        title: wbT("goalLoop.risk.title", "持续执行到验收通过"),
        body: wbT(
          "goalLoop.risk.body",
          "Agent 会在后台反复执行计划、独立验收，并在验收失败时自动返工，直到验收通过或达到退出条件。\n\n这个模式通常会产生更多模型调用、工具调用和文件修改，成本明显高于普通执行。关闭页面不会停止任务，你可以随时暂停或取消。"
        ),
        confirmLabel: wbT("goalLoop.risk.confirm", "了解并继续"),
      }).then(function (ok) {
        if (ok && runtime && runtime.onOpenGoalLoop) runtime.onOpenGoalLoop();
        return ok;
      });
    },

    adjustGoalLoopLimits: function () {
      if (runtime && runtime.onOpenGoalLoopLimits) runtime.onOpenGoalLoopLimits();
    },

    reject: function () {
      var events = model.withEvent(session, "ActionRejected", "用户拒绝了当前操作。");
      return run(patch({ status: "planning", agentReply: "操作已取消。你可以修改要求，或让我重新规划。", events: events }));
    },

    // Honest execution: run the NEXT dependency-ready step for real. Delegates
    // to the per-step run, which executes one step and marks ONLY that step
    // done, with real timing + real tool data. Reused by resume / retry.
    execute: function () {
      if (!requirePlan(session)) return Promise.resolve();
      return ensurePlanApproved(session).then(function (approvedSession) {
        var plan = Array.isArray(approvedSession.plan) ? approvedSession.plan : [];
        var nextStep = model.findNextRunnableStep(plan);
        if (!nextStep) {
          var remaining = plan.filter(function (item) { return !isResolvedStepStatus(item && item.status); });
          if (!remaining.length) {
            return run(model.patchSession(sid, {
              status: "review",
              agentReply: "所有步骤已完成，请验收。",
              events: model.withEvent(approvedSession, "ExecutionFinished", "全部步骤已完成，等待你验收。"),
            }));
          }
          return run(model.patchSession(sid, {
            status: "blocked",
            agentReply: "没有可执行的步骤，请先完成或调整被阻塞步骤的前置依赖。",
            events: model.withEvent(approvedSession, "ExecutionBlocked", "步骤依赖尚未满足，任务已阻塞。"),
          }));
        }
        setBusy(true);
        return runStepCore(approvedSession, nextStep.id)
          .catch(function (err) {
            if (interruptedRef.current || (err && err.name === "AbortError")) return;
            fail(err);
          })
          .finally(function () { setBusy(false); });
      });
    },

    // Run every unresolved step in order. Each iteration starts from the latest
    // server-returned session so completed/failed/skipped state is preserved.
    executeAll: function (options) {
      options = options || {};
      var initialSession = options.baseSession || session;
      if (!requirePlan(initialSession)) return Promise.resolve();
      setBusy(true);
      interruptedRef.current = false;
      var currentSession = initialSession;
      var approvalPromise = options.continuing ? Promise.resolve(initialSession) : ensurePlanApproved(initialSession);
      return approvalPromise.then(function (approvedSession) {
        currentSession = approvedSession;
        if (options.continuing) return { activeSession: approvedSession };
        var startedEvents = model.withEvent(approvedSession, "ExecutionStarted", "开始连续执行全部剩余步骤。");
        return model.patchSession(sid, { status: "running", agentReply: "正在按依赖顺序执行全部剩余步骤。", events: startedEvents });
      })
        .then(apply)
        .then(function (next) {
          currentSession = sessionFromStore(next, currentSession);
          function loop() {
            if (interruptedRef.current) return null;
            var plan = Array.isArray(currentSession.plan) ? currentSession.plan : [];
            var nextStep = model.findNextRunnableStep(plan);
            if (!nextStep) {
              var remaining = plan.filter(function (item) { return !isResolvedStepStatus(item && item.status); });
              if (remaining.length) {
                return model.patchSession(sid, {
                  status: "blocked",
                  agentReply: "没有可执行的步骤，请先完成或调整被阻塞步骤的前置依赖。",
                  events: model.withEvent(currentSession, "ExecutionBlocked", "步骤依赖尚未满足，连续执行已停止。"),
                }).then(apply);
              }
              return model.patchSession(sid, {
                status: "review",
                agentReply: "所有步骤已完成，请验收。",
                artifacts: model.ensureArtifacts(currentSession),
                events: model.withEvent(currentSession, "ExecutionFinished", "全部步骤已完成，等待你验收。"),
              }).then(apply);
            }
            return runStepCore(currentSession, nextStep.id, { continueAll: true })
              .then(function (nextStore) {
                if (interruptedRef.current || !nextStore) return null;
                currentSession = sessionFromStore(nextStore, currentSession);
                if (String(currentSession.status || "") === "failed") return nextStore;
                if (String(currentSession.status || "") === "review") return nextStore;
                if (String(currentSession.status || "") === "waiting_for_user") return nextStore;
                if (String(currentSession.status || "") === "blocked") return nextStore;
                return loop();
              });
          }
          return loop();
        })
        .catch(function (err) {
          if (interruptedRef.current || (err && err.name === "AbortError")) return;
          fail(err);
        })
        .finally(function () { setBusy(false); });
    },

    // Stop the in-flight run (abort the fetch + server-side interrupt) → paused.
    // A running STEP must also drop out of "running" — otherwise the plan card
    // keeps the step spinning with a live 停止 button and the click looks dead.
    // Reset startedAt so a later re-run times the step fresh.
    interrupt: function () {
      if (session.goalLoop && session.goalLoop.status === "running") {
        interruptedRef.current = true;
        model.interruptSession(sid);
        return run(model.pauseGoalLoop(sid));
      }
      interruptedRef.current = true;
      if (runAbortRef.current) { try { runAbortRef.current.abort(); } catch (e) {} }
      model.interruptSession(sid);
      var now = new Date().toISOString();
      var stoppedPlan = Array.isArray(session.plan) ? session.plan.map(function (s) {
        if (!s || s.status !== "running") return s;
        return Object.assign({}, s, { status: "pending", startedAt: null, currentAction: "已停止，可重新执行。", updatedAt: now });
      }) : session.plan;
      return model.patchSession(sid, {
        status: "paused",
        plan: stoppedPlan,
        agentReply: "执行已被你中断，可继续或调整后重试。",
        events: model.withEvent(session, "Paused", "用户中断了执行。"),
      }).then(apply).catch(fail);
    },

    pause: function () {
      return run(patch({ status: "paused", events: model.withEvent(session, "Paused", "任务已暂停。") }));
    },

    runStep: function (step) {
      if (!step || !step.id) return Promise.resolve();
      setBusy(true);
      return ensurePlanApproved(session)
        .then(function (approvedSession) {
          return runStepCore(approvedSession, step.id);
        })
        .catch(function (err) {
          if (interruptedRef.current || (err && err.name === "AbortError")) return;
          fail(err);
        })
        .finally(function () { setBusy(false); });
    },

    // Merge fields into a single plan step and persist (used by the pre-run
    // command editor: prompt override + context files). Does not toggle busy —
    // these are lightweight edits that shouldn't disable the run buttons.
    mutatePlan: function (operation, input) {
      var payload = Object.assign({}, input || {}, {
        operation: operation,
        basePlanRevision: Number(session.planDefinitionRevision || 0),
      });
      return model.mutatePlan(sid, payload).then(apply).catch(function (err) {
        fail(err);
        return null;
      });
    },

    patchStep: function (stepId, fields) {
      if (!stepId) return Promise.resolve();
      return ctrl.mutatePlan("update", { stepId: stepId, fields: fields });
    },

    addStep: function (step) {
      return ctrl.mutatePlan("add", { step: step || {} });
    },

    deleteStep: function (stepId) {
      return ctrl.mutatePlan("delete", { stepId: stepId });
    },

    reorderSteps: function (orderedStepIds) {
      return ctrl.mutatePlan("reorder", { orderedStepIds: orderedStepIds });
    },

    resume: function () {
      if (session.goalLoop && ["paused", "blocked"].indexOf(session.goalLoop.status) >= 0) {
        return run(model.resumeGoalLoop(sid));
      }
      return model.patchSession(sid, { events: model.withEvent(session, "Resumed", "继续执行任务。") })
        .then(apply).then(function () { return ctrl.execute(); });
    },

    retry: function () { return ctrl.execute(); },

    // After independent acceptance fails, repair the current task in-place. The
    // server receives the explicit repair command and injects the latest failed
    // criteria/evidence into the same session's agent context.
    continueModify: function () {
      var criteria = Array.isArray(session.acceptanceCriteria) ? session.acceptanceCriteria : [];
      var failed = criteria.filter(function (item) { return item && item.status === "failed"; });
      var lines = ["请参考最近一次验收结果，继续修改并完成当前 session 的任务。请保留已通过的验收标准，优先修复未通过项。"];
      failed.slice(0, 8).forEach(function (item) {
        var text = String(item.text || "").trim();
        var evidence = String(item.evidence || "").trim();
        if (text) lines.push("- 未通过：" + text + (evidence ? "；验收依据：" + evidence : ""));
      });
      if (session.verifyReason) lines.push("验收结论：" + String(session.verifyReason));
      return runAgentic({ kind: "repair", label: "正在参考验收结果继续修改…" }, model.continueAcceptanceRepair(sid, lines.join("\n"), {
        attachments: (runtime && runtime.attachments) || [],
        mode: (runtime && runtime.mode) || undefined,
      }).then(function (store) {
        if (runtime && runtime.clearAttachments) runtime.clearAttachments();
        return store;
      }));
    },

    // The acceptance-failure action is intentionally the same in-session repair
    // path; the separate label makes the intent clearer than the old reflection
    // action while keeping the repair evidence hand-off identical.
    repairProblem: function () { return ctrl.continueModify(); },

    // Skip the failed step (or the first unresolved one) — only that step, not
    // the whole plan. Continue if work remains, else go to review.
    skipStep: function () {
      var plan = Array.isArray(session.plan) ? session.plan : [];
      var idx = -1;
      for (var i = 0; i < plan.length; i++) {
        if (plan[i] && plan[i].status === "failed") { idx = i; break; }
      }
      if (idx < 0) {
        for (var j = 0; j < plan.length; j++) {
          if (!isResolvedStepStatus(plan[j] && plan[j].status)) { idx = j; break; }
        }
      }
      var skippedStepId = idx >= 0 && plan[idx] ? plan[idx].id : "";
      var skipped = skippedStepId ? model.markStepById(plan, skippedStepId, "skipped", "已跳过该步骤。") : plan;
      var remaining = skipped.filter(function (s) { return !isResolvedStepStatus(s && s.status); }).length;
      var runnable = model.findNextRunnableStep(skipped);
      var events = model.withEvent(session, "StepSkipped", "跳过该步骤。");
      return run(patch({
        status: remaining > 0 ? (runnable ? "paused" : "blocked") : "review",
        plan: skipped,
        agentReply: remaining > 0
          ? (runnable ? "已跳过该步骤，可继续执行不依赖它的剩余步骤。" : "该步骤已跳过，其后续依赖步骤已被阻塞。")
          : "已跳过该步骤，剩余步骤已处理完，请验收。",
        events: events,
      }));
    },

    markComplete: function () {
      // Confirm the still-unverified criteria as passed, but respect any the user
      // explicitly marked 未通过 — don't silently flip them green.
      var items = Array.isArray(session.acceptanceCriteria) ? session.acceptanceCriteria : [];
      var passed = items.map(function (a) {
        return (a && a.status === "failed") ? a : Object.assign({}, a, { status: "passed" });
      });
      var events = model.withEvent(session, "TaskCompleted", "用户确认任务完成。");
      return run(patch({ status: "completed", acceptanceCriteria: passed, events: events }));
    },

    // Deep reflection over the task's accumulated history → session.reflection.
    reflect: function (focus) {
      return runAgentic({ kind: "reflect", label: "正在深度反思整个任务…" }, model.reflect(sid, { focus: focus || "" }));
    },
    // Independent acceptance agent verifies criteria against the real results.
    verify: function () {
      return runAgentic({ kind: "verify", label: "正在独立核验验收标准…" }, model.verify(sid));
    },
    // Reflect on a failed task, then fork a fresh session carrying the packet.
    reflectAndFork: function () {
      return runAgentic({ kind: "reflect", label: "正在反思并另起新任务…" }, model.reflectAndFork(sid));
    },
    // Accept a sibling-reflection hint → merge its packet into this session.
    acceptHint: function (hintId) {
      return run(model.acceptHint(sid, hintId));
    },
    // Dismiss a sibling-reflection hint (no change to this session).
    dismissHint: function (hintId) {
      return run(model.dismissHint(sid, hintId));
    },

    reopen: function () {
      var events = model.withEvent(session, "Reopened", "重新打开任务。");
      return run(patch({ status: "planning", agentReply: "任务已重新打开，请确认计划后继续。", events: events }));
    },

    cancel: function () {
      return window.confirmModal({ body: "确定取消这个任务吗？当前进度会被保留。", danger: true }).then(function (ok) {
        if (!ok) return undefined;
        if (session.goalLoop && ["running", "waiting_for_user", "paused", "blocked"].indexOf(session.goalLoop.status) >= 0) {
          return run(model.cancelGoalLoop(sid));
        }
        return run(patch({ status: "cancelled", events: model.withEvent(session, "Cancelled", "任务已取消。") }));
      });
    },

    createFollowUp: function (input) {
      var options = (input && typeof input === "object") ? input : {};
      return run(model.createFollowUp(sid, options));
    },
  };
  return ctrl;
}

function GoalLoopWizard({ session, onClose, onStarted }) {
  var model = window.WorkbenchModel;
  var [phase, setPhase] = useWorkbenchState("config");
  var [goal, setGoal] = useWorkbenchState(String(session.goal || ""));
  var [maxHours, setMaxHours] = useWorkbenchState(2);
  var [maxRepairs, setMaxRepairs] = useWorkbenchState(3);
  var [permissionMode, setPermissionMode] = useWorkbenchState("auto");
  var [reflectionMode, setReflectionMode] = useWorkbenchState("proactive");
  var [fullAccessConfirmed, setFullAccessConfirmed] = useWorkbenchState(false);
  var [preview, setPreview] = useWorkbenchState(null);
  var [busy, setBusy] = useWorkbenchState(false);
  var [error, setError] = useWorkbenchState("");

  function previewInput() {
    return {
      goal: goal.trim(),
      maxRuntimeHours: Number(maxHours),
      maxRepairRounds: Number(maxRepairs),
      permissionMode: permissionMode,
      reflectionMode: reflectionMode,
      fullAccessConfirmed: permissionMode !== "full_access" || fullAccessConfirmed,
      basePlanDefinitionRevision: Number(session.planDefinitionRevision || 0),
    };
  }

  function generatePreview() {
    setError("");
    if (goal.trim().length < 3) {
      setError(wbT("goalLoop.validation.goal", "请输入清晰的目标。"));
      return;
    }
    if (permissionMode === "full_access" && !fullAccessConfirmed) {
      setError(wbT("goalLoop.validation.fullAccess", "请先确认完全访问风险。"));
      return;
    }
    setBusy(true);
    model.previewGoalLoop(session.id, previewInput())
      .then(function (result) {
        setPreview(result);
        setPhase("preview");
      })
      .catch(function (err) { setError(err.message || String(err)); })
      .finally(function () { setBusy(false); });
  }

  function start() {
    if (!preview || !preview.draftId) return;
    setError("");
    setBusy(true);
    model.startGoalLoop(session.id, preview.draftId)
      .then(function (store) {
        if (onStarted) onStarted(store);
        if (onClose) onClose();
      })
      .catch(function (err) { setError(err.message || String(err)); })
      .finally(function () { setBusy(false); });
  }

  return (
    <div className="workbench-confirm-scrim wb-goal-loop-scrim" onMouseDown={function (event) { if (!busy && event.target === event.currentTarget) onClose(); }}>
      <div className="wb-goal-loop-modal" role="dialog" aria-modal="true" aria-labelledby="goal-loop-title">
        <div className="wb-goal-loop-head">
          <div>
            <span className="wb-goal-loop-eyebrow">{wbT("goalLoop.eyebrow", "持续执行模式")}</span>
            <h2 id="goal-loop-title">{phase === "config" ? wbT("goalLoop.configure.title", "确认目标和退出条件") : wbT("goalLoop.preview.title", "确认持续执行方案")}</h2>
          </div>
          <button type="button" className="workbench-toast-close" disabled={busy} onClick={onClose} aria-label={wbT("common.close", "关闭")}>{ICONS.x}</button>
        </div>
        <div className="wb-goal-loop-steps" aria-label={wbT("goalLoop.steps", "配置进度")}>
          <span className="done">1</span><i />
          <span className={phase === "preview" ? "done" : "active"}>2</span><i />
          <span className={phase === "preview" ? "active" : ""}>3</span>
        </div>

        {phase === "config" ? (
          <div className="wb-goal-loop-body">
            <label className="wb-goal-loop-field">
              <span>{wbT("goalLoop.field.goal", "目标")}</span>
              <small>{wbT("goalLoop.field.goalHint", "描述最终应达到的状态，不要只填写执行步骤。")}</small>
              <textarea value={goal} rows={5} onChange={function (event) { setGoal(event.target.value); }} />
            </label>
            <div className="wb-goal-loop-grid">
              <label className="wb-goal-loop-field">
                <span>{wbT("goalLoop.field.runtime", "最大运行时间")}</span>
                <small>{wbT("goalLoop.field.runtimeHint", "暂停和等待确认期间不计时。")}</small>
                <div className="wb-goal-loop-number"><input type="number" min="0.5" max="24" step="0.5" value={maxHours} onChange={function (event) { setMaxHours(event.target.value); }} /><b>{wbT("goalLoop.hours", "小时")}</b></div>
              </label>
              <label className="wb-goal-loop-field">
                <span>{wbT("goalLoop.field.repairs", "最大返工轮数")}</span>
                <small>{wbT("goalLoop.field.repairsHint", "一次验收失败并重新修复计为一轮。")}</small>
                <div className="wb-goal-loop-number"><input type="number" min="0" max="10" step="1" value={maxRepairs} onChange={function (event) { setMaxRepairs(event.target.value); }} /><b>{wbT("goalLoop.rounds", "轮")}</b></div>
              </label>
            </div>

            <fieldset className="wb-goal-loop-options">
              <legend>{wbT("goalLoop.field.permission", "权限模式")}</legend>
              <button type="button" className={permissionMode === "auto" ? "selected" : ""} onClick={function () { setPermissionMode("auto"); }}>
                <b>{wbT("goalLoop.permission.auto", "Auto（推荐）")}</b>
                <small>{wbT("goalLoop.permission.autoHint", "自动审核权限边界，必要时暂停等待你确认。")}</small>
              </button>
              <button type="button" className={permissionMode === "full_access" ? "selected danger" : ""} onClick={function () { setPermissionMode("full_access"); }}>
                <b>{wbT("goalLoop.permission.full", "完全访问")}</b>
                <small>{wbT("goalLoop.permission.fullHint", "减少权限中断，但可能修改工作区外的文件。")}</small>
              </button>
            </fieldset>
            {permissionMode === "full_access" && (
              <label className="wb-goal-loop-warning">
                <input type="checkbox" checked={fullAccessConfirmed} onChange={function (event) { setFullAccessConfirmed(event.target.checked); }} />
                <span>{wbT("goalLoop.permission.confirm", "我理解完全访问的风险，并同意在本次持续任务中授予该权限。")}</span>
              </label>
            )}

            <fieldset className="wb-goal-loop-options reflection">
              <legend>{wbT("goalLoop.field.reflection", "深度思考强度")}</legend>
              {[
                ["standard", wbT("goalLoop.reflection.standard", "标准"), wbT("goalLoop.reflection.standardHint", "验收失败或明显停滞时调用。")],
                ["proactive", wbT("goalLoop.reflection.proactive", "主动（推荐）"), wbT("goalLoop.reflection.proactiveHint", "在最终验收前和验收失败后主动检查方向。")],
                ["frequent", wbT("goalLoop.reflection.frequent", "高频"), wbT("goalLoop.reflection.frequentHint", "每个步骤完成后及返工时调用，成本最高。")],
              ].map(function (option) {
                return <button type="button" key={option[0]} className={reflectionMode === option[0] ? "selected" : ""} onClick={function () { setReflectionMode(option[0]); }}><b>{option[1]}</b><small>{option[2]}</small></button>;
              })}
            </fieldset>
            <p className="wb-goal-loop-cost">{wbT("goalLoop.costHint", "较高的深度思考强度会增加模型调用、运行时间和成本。首次启动不会调用深度反思。")}</p>
          </div>
        ) : (
          <div className="wb-goal-loop-body preview">
            {preview.goalChanged && <div className="wb-goal-loop-change">{wbT("goalLoop.goalChanged", "目标已改变，原计划已失效。下面是基于新目标重新生成的计划和验收条件。")}</div>}
            <section><h3>{wbT("goalLoop.preview.goal", "目标")}</h3><p>{preview.goal}</p></section>
            <div className="wb-goal-loop-summary">
              <span><small>{wbT("goalLoop.field.runtime", "最大运行时间")}</small><b>{preview.limits.maxRuntimeHours} {wbT("goalLoop.hours", "小时")}</b></span>
              <span><small>{wbT("goalLoop.field.repairs", "最大返工轮数")}</small><b>{preview.limits.maxRepairRounds} {wbT("goalLoop.rounds", "轮")}</b></span>
              <span><small>{wbT("goalLoop.field.permission", "权限模式")}</small><b>{preview.limits.permissionMode === "full_access" ? wbT("goalLoop.permission.full", "完全访问") : wbT("goalLoop.permission.autoShort", "自动")}</b></span>
              <span><small>{wbT("goalLoop.field.reflection", "深度思考强度")}</small><b>{preview.limits.reflectionMode === "frequent" ? wbT("goalLoop.reflection.frequent", "高频") : preview.limits.reflectionMode === "standard" ? wbT("goalLoop.reflection.standard", "标准") : wbT("goalLoop.reflection.proactive", "主动")}</b></span>
            </div>
            <section><h3>{wbT("goalLoop.preview.plan", "执行计划")}</h3><ol>{(preview.plan || []).map(function (step) { return <li key={step.id}><b>{step.title}</b>{step.description && <small>{step.description}</small>}</li>; })}</ol></section>
            <section><h3>{wbT("goalLoop.preview.acceptance", "验收条件")}</h3><ul>{(preview.acceptanceCriteria || []).map(function (item) { return <li key={item.id}>{item.text}</li>; })}</ul></section>
          </div>
        )}

        {error && <div className="wb-goal-loop-error">{error}</div>}
        <div className="wb-goal-loop-foot">
          <button type="button" className="wb-btn ghost" disabled={busy} onClick={phase === "preview" ? function () { setPhase("config"); setError(""); } : onClose}>{phase === "preview" ? wbT("goalLoop.back", "返回修改") : wbT("common.cancel", "取消")}</button>
          {phase === "preview" && <button type="button" className="wb-btn ghost" disabled={busy} onClick={generatePreview}>{wbT("goalLoop.regenerate", "重新生成")}</button>}
          <button type="button" className="wb-btn primary" disabled={busy} onClick={phase === "preview" ? start : generatePreview}>{busy ? wbT("goalLoop.working", "处理中…") : phase === "preview" ? wbT("goalLoop.start", "确认并开始持续执行") : wbT("goalLoop.generate", "生成计划和验收条件")}</button>
        </div>
      </div>
    </div>
  );
}

// Adjust-and-continue dialog for a paused goal loop. The loop pauses when it
// hits the runtime / repair-round budget; bumping the budget here and resuming
// is the only way to make progress past those limits (a plain resume would just
// re-pause). Reuses the wizard's field styling in a compact modal.
function GoalLoopLimitsDialog({ session, onClose, onSaved }) {
  var model = window.WorkbenchModel;
  var loop = (session && session.goalLoop) || {};
  var [maxHours, setMaxHours] = useWorkbenchState(Math.max(0.5, Math.round((Number(loop.maxActiveSeconds || 7200) / 3600) * 2) / 2));
  var [maxRepairs, setMaxRepairs] = useWorkbenchState(Number(loop.maxRepairRounds || 3));
  var [reflectionMode, setReflectionMode] = useWorkbenchState(String(loop.reflectionMode || "proactive"));
  var [busy, setBusy] = useWorkbenchState(false);
  var [error, setError] = useWorkbenchState("");
  var reasonHint = loop.stopReason === "max_runtime"
    ? wbT("goalLoop.limits.reasonRuntime", "已达到最大运行时间。增加运行时间后即可继续。")
    : loop.stopReason === "max_repair_rounds"
    ? wbT("goalLoop.limits.reasonRepairs", "已达到最大返工轮数。增加返工轮数后即可继续。")
    : wbT("goalLoop.limits.reason", "调整退出条件后继续持续执行。");

  function save() {
    setError("");
    setBusy(true);
    model.updateGoalLoopLimits(session.id, {
      maxRuntimeHours: Number(maxHours),
      maxRepairRounds: Number(maxRepairs),
      reflectionMode: reflectionMode,
    })
      .then(function () { return model.resumeGoalLoop(session.id); })
      .then(function (store) { if (onSaved) onSaved(store); if (onClose) onClose(); })
      .catch(function (err) { setError((err && err.message) || String(err)); })
      .finally(function () { setBusy(false); });
  }

  return (
    <div className="workbench-confirm-scrim wb-goal-loop-scrim" onMouseDown={function (event) { if (!busy && event.target === event.currentTarget) onClose(); }}>
      <div className="wb-goal-loop-modal compact" role="dialog" aria-modal="true" aria-labelledby="goal-loop-limits-title">
        <div className="wb-goal-loop-head">
          <div>
            <span className="wb-goal-loop-eyebrow">{wbT("goalLoop.eyebrow", "持续执行模式")}</span>
            <h2 id="goal-loop-limits-title">{wbT("goalLoop.limits.title", "调整限制并继续")}</h2>
          </div>
          <button type="button" className="workbench-toast-close" disabled={busy} onClick={onClose} aria-label={wbT("common.close", "关闭")}>{ICONS.x}</button>
        </div>
        <div className="wb-goal-loop-body">
          <div className="wb-goal-loop-change">{reasonHint}</div>
          <div className="wb-goal-loop-grid">
            <label className="wb-goal-loop-field">
              <span>{wbT("goalLoop.field.runtime", "最大运行时间")}</span>
              <div className="wb-goal-loop-number"><input type="number" min="0.5" max="24" step="0.5" value={maxHours} onChange={function (event) { setMaxHours(event.target.value); }} /><b>{wbT("goalLoop.hours", "小时")}</b></div>
            </label>
            <label className="wb-goal-loop-field">
              <span>{wbT("goalLoop.field.repairs", "最大返工轮数")}</span>
              <div className="wb-goal-loop-number"><input type="number" min="0" max="10" step="1" value={maxRepairs} onChange={function (event) { setMaxRepairs(event.target.value); }} /><b>{wbT("goalLoop.rounds", "轮")}</b></div>
            </label>
          </div>
          <fieldset className="wb-goal-loop-options reflection">
            <legend>{wbT("goalLoop.field.reflection", "深度思考强度")}</legend>
            {[
              ["standard", wbT("goalLoop.reflection.standard", "标准")],
              ["proactive", wbT("goalLoop.reflection.proactive", "主动（推荐）")],
              ["frequent", wbT("goalLoop.reflection.frequent", "高频")],
            ].map(function (option) {
              return <button type="button" key={option[0]} className={reflectionMode === option[0] ? "selected" : ""} onClick={function () { setReflectionMode(option[0]); }}><b>{option[1]}</b></button>;
            })}
          </fieldset>
        </div>
        {error && <div className="wb-goal-loop-error">{error}</div>}
        <div className="wb-goal-loop-foot">
          <button type="button" className="wb-btn ghost" disabled={busy} onClick={onClose}>{wbT("common.cancel", "取消")}</button>
          <button type="button" className="wb-btn primary" disabled={busy} onClick={save}>{busy ? wbT("goalLoop.working", "处理中…") : wbT("goalLoop.limits.save", "保存并继续")}</button>
        </div>
      </div>
    </div>
  );
}

function TaskWorkArea(props) {
  var project = props.project;
  var session = props.session;
  var active = props.active !== false;
  var [attachments, setAttachments] = useWorkbenchState([]);
  var [mode, setMode] = useWorkbenchState("auto");
  var [goalLoopOpen, setGoalLoopOpen] = useWorkbenchState(false);
  var [goalLoopLimitsOpen, setGoalLoopLimitsOpen] = useWorkbenchState(false);
  var sid = session ? session.id : "";
  // Pending attachments belong to the task being composed — reset on switch.
  useWorkbenchEffect(function () { setAttachments([]); }, [sid]);
  var controller = useTaskController(session, props.onRefresh, {
    attachments: attachments,
    mode: mode,
    clearAttachments: function () { setAttachments([]); },
    onLocalPatch: props.onLocalPatch,
    onOpenGoalLoop: function () { setGoalLoopOpen(true); },
    onOpenGoalLoopLimits: function () { setGoalLoopLimitsOpen(true); },
  });
  var taskDropEnabled = !!(active && project && session && session.kind !== "init");
  var taskFileDropActive = useWorkbenchFileDrop(function (files) {
    try {
      window.dispatchEvent(new CustomEvent("cyrene:add-task-attachments", { detail: { files: files } }));
    } catch (e) {}
  }, taskDropEnabled);
  if (props.loading && (!project || !session)) {
    return <main className="workbench-main"><div className="workbench-empty">正在加载工作台...</div></main>;
  }
  if (!project || !session) {
    return <main className="workbench-main"><div className="workbench-empty">请选择项目和任务。</div></main>;
  }
  // "初始化项目" onboarding sessions take over the whole work area with their
  // own agent-led question flow (WorkbenchInitView), bypassing the task state
  // machine, plan list and composer below.
  if (session.kind === "init" && window.WorkbenchInitView) {
    return (
      <main className="workbench-main">
        {React.createElement(window.WorkbenchInitView, {
          project: project,
          session: session,
          onRefresh: props.onRefresh,
          onInitPatch: props.onInitPatch,
          onBackToBoard: props.onBackToBoard,
        })}
      </main>
    );
  }
  var status = String(session.status || "idle");
  var showPlan = ["planning", "waiting_for_approval", "waiting_for_user", "running", "review", "paused", "failed", "blocked", "done", "completed"].indexOf(status) >= 0
    && Array.isArray(session.plan);
  return (
    <main className="workbench-main">
      {taskFileDropActive && <WorkbenchFileDropOverlay label={wbT("workbenchChat.dropToAttach", "Release to add files to the task input")} />}
      <TaskHeader project={project} session={session} controller={controller} onRightTab={props.onRightTab} onSelectSession={props.onSelectSession} onBackToBoard={props.onBackToBoard} />
      {props.error && <div className="workbench-error">{props.error}</div>}
      <div className="workbench-stage">
        <ReflectionHintBanner session={session} controller={controller} />
        <StateCard
          session={session}
          project={project}
          controller={controller}
          onRightTab={props.onRightTab}
          onSelectSession={props.onSelectSession}
        />
        {showPlan && (
          <TaskPlanList
            session={session}
            expandedStepId={props.expandedStepId}
            onToggleStep={props.onToggleStep}
            onRightTab={props.onRightTab}
            controller={controller}
          />
        )}
      </div>
      <TaskComposer
        session={session}
        controller={controller}
        onRightTab={props.onRightTab}
        attachments={attachments}
        onAttachmentsChange={setAttachments}
        mode={mode}
        onModeChange={setMode}
      />
      {goalLoopOpen && <GoalLoopWizard session={session} onClose={function () { setGoalLoopOpen(false); }} onStarted={props.onRefresh} />}
      {goalLoopLimitsOpen && <GoalLoopLimitsDialog session={session} onClose={function () { setGoalLoopLimitsOpen(false); }} onSaved={props.onRefresh} />}
    </main>
  );
}

// Banner above the task card: a sibling task's reflection produced an insight
// relevant to THIS task. Suggestion only — the user adopts (merges the packet
// into this session's reflection) or ignores it. Never auto-applied.
function ReflectionHintBanner({ session, controller }) {
  var hints = Array.isArray(session.pendingHints) ? session.pendingHints : [];
  var pending = hints.filter(function (h) { return h && h.status === "pending"; });
  if (pending.length === 0) return null;
  return (
    <div className="wb-hint-stack">
      {pending.map(function (h) {
        return (
          <div className="wb-hint-banner" key={h.id}>
            <span className="wb-hint-icon">{ICONS.spark}</span>
            <div className="wb-hint-body">
              <div className="wb-hint-label">
                {wbT("task.hint.label", "来自相关任务的启发")}
                {h.fromTitle ? " · 《" + h.fromTitle + "》" : ""}
              </div>
              <div className="wb-hint-text">{h.hint}</div>
            </div>
            <div className="wb-hint-actions">
              <WbBtn kind="primary" disabled={controller.busy} onClick={function () { controller.acceptHint(h.id); }}>
                {wbT("task.hint.accept", "纳入")}
              </WbBtn>
              <WbBtn kind="ghost" disabled={controller.busy} onClick={function () { controller.dismissHint(h.id); }}>
                {wbT("task.hint.dismiss", "忽略")}
              </WbBtn>
            </div>
          </div>
        );
      })}
    </div>
  );
}

// Picks the primary middle card for the current task status.
function StateCard(props) {
  var status = String(props.session.status || "idle");
  // A background agent op (规划 / 反思 / 验收) is in flight but the task status
  // hasn't moved to `running` — show the live activity card instead of the now
  // stale status card (otherwise a 待验收 task just keeps showing 「已完成」).
  if (props.session.agentBusy && status !== "running") return <AgentActivityCard {...props} />;
  // A run paused for a permission / clarification answer — show the question card
  // (with answer buttons) ahead of the status card, so the round can resume.
  var pq = props.session.pendingQuestion;
  if (pq && pq.id) return <AgentQuestionCard {...props} />;
  if (status === "planning") return <AgentPlanCard {...props} />;
  if (status === "answered") return <AgentReplyCard {...props} />;
  if (status === "acted") return <AgentReplyCard {...props} acted={true} />;
  if (status === "waiting_for_approval" || status === "waiting_for_user") return <ConfirmCard {...props} />;
  if (status === "running") return <AgentActivityCard {...props} />;
  if (status === "paused") return <PausedCard {...props} />;
  if (status === "blocked") return <BlockedCard {...props} />;
  if (status === "failed") return <FailedCard {...props} />;
  if (status === "review" || status === "done") return <CompletionCard {...props} />;
  if (status === "completed") return <CompletionCard {...props} confirmed={true} />;
  if (status === "cancelled") return <CancelledCard {...props} />;
  return <TaskBriefCard {...props} />; // idle / pending / unknown
}

function priorityText(p) {
  var raw = String(p || "medium");
  return ({ high: wbT("priority.high", "High"), medium: wbT("priority.medium", "Medium"), low: wbT("priority.low", "Low") })[raw] || raw;
}

function focusComposer() {
  window.dispatchEvent(new CustomEvent("wb-focus-composer"));
}

function hasAcceptanceFailure(session) {
  if (!session) return false;
  if (String(session.status || "") !== "failed") return false;
  var criteria = Array.isArray(session.acceptanceCriteria) ? session.acceptanceCriteria : [];
  return !!session.verifyReason || criteria.some(function (item) {
    return item && item.status === "failed";
  });
}

function openAcceptanceEditor(onRightTab) {
  if (onRightTab) onRightTab("acceptance");
}

function openNextSession(session, project, onSelectSession) {
  if (!project || !onSelectSession) return;
  var sessions = Array.isArray(project.sessions) ? project.sessions : [];
  var idx = sessions.findIndex(function (s) { return s.id === session.id; });
  var next = sessions[idx + 1] || sessions[0];
  if (next && next.id !== session.id) onSelectSession(next.id);
}

function compactText(value, limit) {
  var text = String(value || "").replace(/\s+/g, " ").trim();
  var max = limit || 120;
  if (!text) return "";
  return text.length > max ? text.slice(0, max - 1) + "..." : text;
}

function sessionSummaryText(session) {
  if (!session) return "";
  var summary = session.summary;
  if (summary && typeof summary === "object") {
    summary = summary.text || summary.body || summary.content || summary.summary || "";
  }
  return compactText(summary || wbRealGoal(session) || session.agentReply || wbT("task.summaryFallback", "Agent will generate a summary for this session during execution."), 128);
}

function canPauseTaskStatus(status) {
  return ["running", "waiting_for_user"].indexOf(String(status || "")) >= 0;
}

function TaskHeader({ project, session, controller, onRightTab, onSelectSession, onBackToBoard }) {
  var tone = WorkbenchModel.statusTone(session.status);
  var status = String(session.status || "idle");
  var [editing, setEditing] = useWorkbenchState(false);
  var [draftTitle, setDraftTitle] = useWorkbenchState(session.title || "");
  var [savingTitle, setSavingTitle] = useWorkbenchState(false);
  var [menuOpen, setMenuOpen] = useWorkbenchState(false);
  var titleInputRef = useWorkbenchRef(null);

  useWorkbenchEffect(function () {
    setDraftTitle(session.title || "");
    setEditing(false);
    setMenuOpen(false);
  }, [session.id]);

  useWorkbenchEffect(function () {
    if (editing && titleInputRef.current) {
      titleInputRef.current.focus();
      titleInputRef.current.select();
    }
  }, [editing]);

  function saveTitle() {
    var nextTitle = String(draftTitle || "").trim();
    if (!nextTitle || nextTitle === session.title) {
      setDraftTitle(session.title || "");
      setEditing(false);
      return;
    }
    setSavingTitle(true);
    window.WorkbenchModel.patchSession(session.id, { title: nextTitle })
      .then(function (next) {
        if (controller && controller.applyStore) controller.applyStore(next);
      })
      .catch(function (err) {
        window.showToast((err && err.message) || String(err), "error");
        setDraftTitle(session.title || "");
      })
      .finally(function () {
        setSavingTitle(false);
        setEditing(false);
      });
  }

  var menuActions = headerMenuActions(status, controller, session, project, onSelectSession, onRightTab);

  return (
    <div className="workbench-task-header">
      <div className="wb-th-main">
        <button type="button" className="wb-task-back-board" onClick={onBackToBoard}>{wbT("taskBoard.back", "Back to board")}</button>
        <div className="wb-th-title-row">
          {editing ? (
            <input
              ref={titleInputRef}
              className="wb-th-title-input"
              value={draftTitle}
              disabled={savingTitle}
              onChange={function (e) { setDraftTitle(e.target.value); }}
              onBlur={saveTitle}
              onKeyDown={function (e) {
                if (e.key === "Enter") saveTitle();
                if (e.key === "Escape") { setDraftTitle(session.title || ""); setEditing(false); }
              }}
              aria-label={wbT("task.titleLabel", "Task title")}
            />
          ) : (
            <h1 title={session.title}>{session.title}</h1>
          )}
          {!editing && (
            <button type="button" className="wb-th-iconbtn" onClick={function () { setEditing(true); }} title={wbT("task.editTitle", "Edit title")}>
              {ICONS.edit}
            </button>
          )}
        </div>
        <p className="wb-th-summary">
          <span className={"wb-th-inline-status " + tone}>{WorkbenchModel.statusText(session.status)}</span>
          <span className="wb-th-summary-text">{sessionSummaryText(session)}</span>
        </p>
        <div className="wb-th-meta">
          <span>{wbT("task.priorityPrefix", "Priority {priority}", { priority: priorityText(session.priority) })}</span>
          <span>{project.name}</span>
        </div>
      </div>
      <div className="wb-th-action-wrap">
        {canPauseTaskStatus(status) && (
          <button
            type="button"
            className="wb-th-control-btn wb-th-pause"
            disabled={controller.busy}
            onClick={function () { status === "running" ? controller.interrupt() : controller.pause(); }}
            title={wbT("task.action.pauseTask", "Pause task")}
            aria-label={wbT("task.action.pauseTask", "Pause task")}
          >
            {ICONS.pause}
          </button>
        )}
        <div className="wb-th-menu-wrap">
          <button type="button" className="wb-th-control-btn wb-th-menu-btn" onClick={function () { setMenuOpen(!menuOpen); }} title={wbT("task.detailMenu", "Details menu")} aria-label={wbT("task.detailMenu", "Details menu")}>
            {ICONS.dots}
          </button>
          {menuOpen && (
            <>
              <div className="wb-th-menu-scrim" onClick={function () { setMenuOpen(false); }}></div>
              <div className="wb-th-menu">
                {menuActions.map(function (a, i) {
                  return <button key={"act" + i} type="button" disabled={controller.busy} onClick={function () { setMenuOpen(false); a.onClick(); }}>{a.label}</button>;
                })}
                {menuActions.length > 0 && <div className="wb-th-menu-sep" />}
                <button type="button" onClick={function () { setMenuOpen(false); onRightTab && onRightTab("context"); }}>{wbT("task.menu.viewContext", "View context")}</button>
                <button type="button" onClick={function () { setMenuOpen(false); onRightTab && onRightTab("logs"); }}>{wbT("task.menu.runLogs", "Run logs")}</button>
                <button type="button" onClick={function () { setMenuOpen(false); onRightTab && onRightTab("acceptance"); }}>{wbT("task.menu.acceptance", "Acceptance criteria")}</button>
                <button type="button" onClick={function () { setMenuOpen(false); focusComposer(); }}>{wbT("task.menu.editTask", "Edit task content")}</button>
              </div>
            </>
          )}
        </div>
      </div>
    </div>
  );
}

// Status-dependent secondary actions, folded into the ⋯ menu. The primary action
// surface is the composer quick-chips below; this is overflow. Returns
// [{ label, onClick }]; running/idle add nothing (covered by chips + static items).
function headerMenuActions(status, controller, session, project, onSelectSession, onRightTab) {
  function openNext() { openNextSession(session, project, onSelectSession); }
  if (status === "answered" || status === "acted") {
    return [
      { label: wbT("task.action.promoteToTask", "Make it a task"), onClick: function () { controller.promoteToPlan(); } },
      { label: wbT("task.action.createFollowUp", "Create follow-up task"), onClick: function () { controller.createFollowUp(); } },
    ];
  }
  if (status === "planning") {
    return [
      { label: wbT("task.action.approveExecution", "Approve execution"), onClick: function () { controller.approvePlan(); } },
      { label: wbT("common.cancel", "Cancel"), onClick: function () { controller.cancel(); } },
    ];
  }
  if (status === "waiting_for_approval" || status === "waiting_for_user") {
    return [
      { label: wbT("task.action.approveExecution", "Approve"), onClick: function () { controller.execute(); } },
      { label: wbT("task.action.reject", "Reject"), onClick: function () { controller.reject(); } },
    ];
  }
  if (status === "blocked") {
    if (session && session.goalLoop) {
      return [
        { label: wbT("task.action.resumeTask", "Resume task"), onClick: function () { controller.resume(); } },
        { label: wbT("task.action.viewLogs", "View logs"), guard: false, onClick: function () { onRightTab && onRightTab("logs"); } },
        { label: wbT("task.action.cancelTask", "Cancel task"), onClick: function () { controller.cancel(); } },
      ];
    }
    return [
      { label: wbT("task.action.viewDetails", "View details"), guard: false, onClick: function () { onRightTab && onRightTab("context"); } },
      { label: wbT("task.action.viewLogs", "View logs"), guard: false, onClick: function () { onRightTab && onRightTab("logs"); } },
      { label: wbT("task.action.cancelTask", "Cancel task"), onClick: function () { controller.cancel(); } },
    ];
  }
  if (status === "paused") {
    return [
      { label: wbT("task.action.resumeTask", "Resume task"), onClick: function () { controller.resume(); } },
      { label: wbT("common.cancel", "Cancel"), onClick: function () { controller.cancel(); } },
    ];
  }
  if (status === "failed") {
    return [
      { label: wbT("task.action.retry", "Retry"), onClick: function () { controller.retry(); } },
      { label: wbT("common.cancel", "Cancel"), onClick: function () { controller.cancel(); } },
    ];
  }
  if (status === "review" || status === "done") {
    return [
      { label: wbT("task.action.markComplete", "Mark complete"), onClick: function () { controller.markComplete(); } },
      { label: wbT("task.action.createFollowUp", "Create follow-up task"), onClick: function () { controller.createFollowUp(); } },
      { label: wbT("task.action.openNext", "Open next task"), onClick: openNext },
    ];
  }
  if (status === "completed") {
    return [
      { label: wbT("task.action.reopen", "Reopen"), onClick: function () { controller.reopen(); } },
      { label: wbT("task.action.createFollowUp", "Create follow-up task"), onClick: function () { controller.createFollowUp(); } },
      { label: wbT("task.action.openNext", "Open next task"), onClick: openNext },
    ];
  }
  if (status === "cancelled") {
    return [{ label: wbT("task.action.reopen", "Reopen"), onClick: function () { controller.reopen(); } }];
  }
  return [];
}

// ---- Shared card primitives ------------------------------------------------

function WbCard({ tone, icon, title, badge, children }) {
  return (
    <section className={"wb-card" + (tone ? " " + tone : "")}>
      <div className="wb-card-head">
        <span className="wb-card-icon">{icon}</span>
        <b>{title}</b>
        {badge}
      </div>
      {children}
    </section>
  );
}

function WbActions({ children }) {
  return <div className="wb-card-actions">{children}</div>;
}

function WbBtn({ kind, onClick, disabled, children }) {
  return (
    <button type="button" className={"wb-btn" + (kind ? " " + kind : "")} onClick={onClick} disabled={disabled}>
      {children}
    </button>
  );
}

function wbRenderMarkdown(text) {
  var source = String(text == null ? "" : text);
  try {
    var raw = window.marked ? window.marked.parse(source) : source.replace(/\n/g, "<br>");
    return window.DOMPurify ? window.DOMPurify.sanitize(raw) : raw;
  } catch (e) {
    return source;
  }
}

function AgentReplyBlock({ text }) {
  var reply = String(text || "").trim();
  if (!reply) return null;
  return (
    <div className="wb-agent-body markdown" dangerouslySetInnerHTML={{ __html: wbRenderMarkdown(reply) }} />
  );
}

// ---- State cards -----------------------------------------------------------

// idle / pending — task detail + 开始执行.
// Legacy placeholder goal once stamped on blank 新任务 sessions (routes.py
// _workbench_new_session). New tasks now start with an empty goal; this still
// recognizes the old filler in already-stored sessions as "no real goal yet", so
// it is never shown as a goal or handed to the agent. MUST mirror the backend.
var WB_PLACEHOLDER_GOAL = "通过对话明确当前任务目标。";
function wbRealGoal(session) {
  var g = String((session && session.goal) || "").trim();
  return (g && g !== WB_PLACEHOLDER_GOAL) ? g : "";
}

function TaskBriefCard({ session, controller }) {
  var goal = wbRealGoal(session);
  var constraints = Array.isArray(session.constraints) ? session.constraints : [];
  var accept = Array.isArray(session.acceptanceCriteria) ? session.acceptanceCriteria : [];
  var hasGoal = !!goal;
  return (
    <WbCard tone="brief" icon={ICONS.target} title={wbT("task.card.details", "Task details")}>
      {hasGoal ? (
        <div className="wb-brief">
          <div className="wb-brief-row"><label>{wbT("task.field.goal", "Task goal")}</label><p>{goal}</p></div>
          {constraints.length > 0 && (
            <div className="wb-brief-row"><label>{wbT("task.field.constraints", "Constraints")}</label>
              <ul className="wb-bullet">{constraints.map(function (c, i) { return <li key={i}>{c}</li>; })}</ul>
            </div>
          )}
          {accept.length > 0 && (
            <div className="wb-brief-row"><label>{wbT("task.field.acceptance", "Acceptance criteria")}</label>
              <ul className="wb-bullet">{accept.map(function (a) { return <li key={a.id}>{a.text}</li>; })}</ul>
            </div>
          )}
        </div>
      ) : (
        <p className="wb-card-hint">{wbT("task.brief.emptyHint", "Just describe a goal or ask a question below. The agent decides whether to answer, take action, or draft a plan first — you don't have to generate a plan up front.")}</p>
      )}
      {/* One primary action per state. Real goal → hand it over (agent auto-judges
          answer/act/plan). No goal yet → 直接开始: the agent reads the project and
          proposes a plan. Either way the composer below stays open for free chat. */}
      {hasGoal ? (
        <React.Fragment>
          <p className="wb-card-hint">{wbT("task.brief.autoHint", "Clicking \"Hand to agent\" starts on this goal right away — the agent decides whether to answer, act, or propose a plan first. You can also keep refining or asking below.")}</p>
          <WbActions>
            <WbBtn kind="primary" disabled={controller.busy} onClick={function () { controller.send(goal); }}>{wbT("task.action.handToAgent", "Hand to agent")}</WbBtn>
          </WbActions>
        </React.Fragment>
      ) : (
        <WbActions>
          <WbBtn kind="primary" disabled={controller.busy} onClick={function () { controller.autoStart(); }}>{wbT("task.action.autoStart", "Start now")}</WbBtn>
        </WbActions>
      )}
    </WbCard>
  );
}

// planning — Agent 回复 with the proposed plan.
function AgentPlanCard({ session, controller, onRightTab }) {
  var plan = Array.isArray(session.plan) ? session.plan : [];
  return (
    <WbCard tone="agent" icon={ICONS.spark} title={wbT("task.card.agentReply", "Agent reply")}>
      <AgentReplyBlock text={session.agentReply || wbT("task.plan.defaultReply", "I will execute this task with the following steps.")} />
      <div className="wb-brief-row"><label>{wbT("task.field.steps", "Execution steps")}</label>
        <ol className="wb-ordered">{plan.map(function (s) { return <li key={s.id}>{s.title}</li>; })}</ol>
      </div>
      <p className="wb-card-hint">{wbT("task.plan.hint", "Continue? After approval, Cyrene will move to confirmation before execution starts.")}</p>
    </WbCard>
  );
}

// answered — a question the agent just answered. acted — a one-shot instruction
// the agent just carried out. Neither generated a plan: show the reply directly,
// and (for acted) what changed, plus a way to promote the exchange into a real
// planned task. Driven by the intent classifier behind /dispatch.
function AgentReplyCard({ session, controller, onRightTab, acted }) {
  var runs = Array.isArray(session.runs) ? session.runs : [];
  var lastRun = runs.length ? runs[runs.length - 1] : null;
  var fileChanges = (lastRun && Array.isArray(lastRun.fileChanges)) ? lastRun.fileChanges : [];
  var toolCalls = (lastRun && Array.isArray(lastRun.toolCalls)) ? lastRun.toolCalls : [];
  return (
    <WbCard tone={acted ? "done" : "agent"} icon={acted ? ICONS.check : ICONS.spark} title={acted ? wbT("task.card.agentActed", "Agent acted") : wbT("task.card.agentReply", "Agent reply")}>
      <AgentReplyBlock text={session.agentReply || (acted ? wbT("task.reply.acted", "Done as instructed.") : wbT("task.reply.answered", "Here is my answer."))} />
      {acted && fileChanges.length > 0 && (
        <div className="wb-done-grid">
          <button type="button" className="wb-done-stat" onClick={function () { onRightTab && onRightTab("files"); }}>
              <b>{fileChanges.length}</b><small>{wbT("task.stat.fileChanges", "File changes")}</small>
          </button>
          {toolCalls.length > 0 && (
            <button type="button" className="wb-done-stat" onClick={function () { onRightTab && onRightTab("logs"); }}>
              <b>{toolCalls.length}</b><small>{wbT("task.stat.toolCalls", "Tool calls")}</small>
            </button>
          )}
        </div>
      )}
      <p className="wb-card-hint">{acted ? wbT("task.reply.actedHint", "Keep chatting below and the agent will judge each message; turn this into a full task if you need structured steps.") : wbT("task.reply.answerHint", "Keep asking or give an instruction below — the agent decides how to handle each one. Promote it into a task if you need a plan.")}</p>
      <WbActions>
        <WbBtn kind="ghost" disabled={controller.busy} onClick={function () { controller.promoteToPlan(); }}>{wbT("task.action.promoteToTask", "Make it a task")}</WbBtn>
        <WbBtn kind="ghost" disabled={controller.busy} onClick={focusComposer}>{wbT("task.action.continueEditing", "Continue")}</WbBtn>
      </WbActions>
    </WbCard>
  );
}

// A run paused awaiting the user's answer to a permission-elevation request or a
// clarification question (ask_user). Renders the question + its options as
// buttons — each answer resumes the SAME round server-side; allowCustom adds a
// free-text reply for open questions.
function AgentQuestionCard({ session, controller }) {
  var pq = (session && session.pendingQuestion) || {};
  var options = Array.isArray(pq.options) ? pq.options : [];
  var kind = String(pq.kind || "");
  var isPermission = window.wbIsPermissionQuestionKind(kind);
  var customState = useWorkbenchState("");
  var customText = customState[0], setCustomText = customState[1];
  function submitCustom() {
    var t = String(customText || "").trim();
    if (!t || controller.busy) return;
    setCustomText("");
    controller.answer(pq.id, t);
  }
  return (
    <WbCard tone="confirm" icon={ICONS.shield} title={isPermission ? wbT("workbenchChat.permissionTitle", "Authorization needed") : wbT("workbenchChat.questionTitle", "Confirmation needed")}>
      <AgentReplyBlock text={pq.text || wbT("workbenchChat.questionFallback", "Agent needs your confirmation to continue.")} />
      {isPermission ? (
        // Authorization: a simple binary. Buttons read 确认/拒绝 but send the
        // backend-recognized option text (options[0] = allow, last = deny).
        <WbActions>
          <WbBtn kind="primary" disabled={controller.busy} onClick={function () { controller.answer(pq.id, options[0] || "确认"); }}>{wbT("workbenchChat.approve", "Confirm")}</WbBtn>
          <WbBtn kind="ghost" disabled={controller.busy} onClick={function () { controller.answer(pq.id, options.length ? options[options.length - 1] : "拒绝"); }}>{wbT("workbenchChat.reject", "Reject")}</WbBtn>
        </WbActions>
      ) : (
        <React.Fragment>
          {options.length > 0 && (
            <WbActions>
              {options.map(function (opt, i) {
                return <WbBtn key={i} kind={i === 0 ? "primary" : "ghost"} disabled={controller.busy} onClick={function () { controller.answer(pq.id, opt); }}>{opt}</WbBtn>;
              })}
            </WbActions>
          )}
          {pq.allowCustom && (
            <div className="wb-q-custom">
              <input type="text" className="wb-q-input" value={customText} placeholder={wbT("workbenchChat.customAnswer", "Or enter a custom reply...")} disabled={controller.busy}
                onChange={function (e) { setCustomText(e.target.value); }}
                onKeyDown={function (e) { if (e.key === "Enter") { e.preventDefault(); submitCustom(); } }} />
              <WbBtn kind="ghost" disabled={controller.busy || !String(customText).trim()} onClick={submitCustom}>{wbT("workbenchChat.send", "Send")}</WbBtn>
            </div>
          )}
        </React.Fragment>
      )}
    </WbCard>
  );
}

// waiting_for_approval — the 需要你确认 card before a sensitive run.
function ConfirmCard({ session, controller, onRightTab }) {
  var summary = window.WorkbenchModel.confirmSummary(session);
  var riskTone = summary.risk === "高" ? "red" : summary.risk === "中" ? "amber" : "green";
  return (
    <WbCard tone="confirm" icon={ICONS.shield} title={wbT("workbenchChat.questionTitle", "Confirmation needed")}
      badge={<span className={"wb-risk " + riskTone}>{wbT("task.risk", "Risk {risk}", { risk: summary.risk })}</span>}>
      <p className="wb-card-hint">{wbT("task.confirm.actionsIntro", "The agent plans to perform these actions:")}</p>
      <ol className="wb-ordered">{summary.actions.map(function (a, i) { return <li key={i}>{a}</li>; })}</ol>
      <div className="wb-brief-row"><label>{wbT("task.confirm.scope", "Scope")}</label>
        <ul className="wb-bullet">{summary.scope.map(function (s, i) { return <li key={i}>{s}</li>; })}</ul>
      </div>
    </WbCard>
  );
}

// running / busy — Agent 正在处理. For a running plan step the detailed call
// trace is shown in the expanded subtask below (执行计划), so this top card omits
// the inline feed to avoid duplication and instead shows the progress bar + mini
// step list. Non-step background ops (规划 / 反思 / 验收) have no subtask row, so
// they keep streaming the session-level live feed here instead of a silent spinner.
function AgentActivityCard({ session, controller, onRightTab }) {
  var plan = Array.isArray(session.plan) ? session.plan : [];
  var done = plan.filter(function (s) { return s.status === "completed" || s.status === "done"; }).length;
  var runningStep = plan.filter(function (s) { return s.status === "running"; })[0] || null;
  var busyOp = session.agentBusy || null;
  var pct = plan.length ? Math.round((done / plan.length) * 100) : 0;
  var lines = wbLiveActivityLines(session, runningStep, busyOp);
  var stage = runningStep ? runningStep.title : ((busyOp && busyOp.label) || wbT("status.running", "Running"));
  var goalLoop = session.goalLoop && typeof session.goalLoop === "object" ? session.goalLoop : null;
  var phaseLabels = {
    executing: wbT("goalLoop.phase.executing", "执行"),
    reflecting: wbT("goalLoop.phase.reflecting", "深度思考"),
    verifying: wbT("goalLoop.phase.verifying", "独立验收"),
    repairing: wbT("goalLoop.phase.repairing", "返工"),
    recovering: wbT("goalLoop.phase.recovering", "恢复"),
  };
  var feedRef = useWorkbenchRef(null);
  // Keep the newest activity line in view as it streams in.
  useWorkbenchEffect(function () {
    var el = feedRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [lines.length]);
  return (
    <WbCard tone="running" icon={<span className="wb-spinner" />} title={wbT("task.card.agentWorking", "Agent is working")}
      badge={runningStep
        ? <span className="wb-progress-badge">{done} / {plan.length}</span>
        : <span className="wb-progress-badge live">{wbT("task.processing", "Processing")}</span>}>
      <p className="wb-running-stage">{wbT("task.currentStage", "Current stage: {stage}", { stage: stage })}</p>
      {goalLoop && (
        <div className="wb-goal-loop-live">
          <span><small>{wbT("goalLoop.live.phase", "阶段")}</small><b>{phaseLabels[goalLoop.phase] || goalLoop.phase}</b></span>
          <span><small>{wbT("goalLoop.live.runtime", "运行时间")}</small><b>{formatDurationSec(goalLoop.activeSeconds || 0)} / {formatDurationSec(goalLoop.maxActiveSeconds || 0)}</b></span>
          <span><small>{wbT("goalLoop.live.repairs", "返工")}</small><b>{goalLoop.repairRound || 0} / {goalLoop.maxRepairRounds || 0}</b></span>
          <span><small>{wbT("goalLoop.live.permission", "权限")}</small><b>{goalLoop.permissionMode === "full_access" ? wbT("goalLoop.permission.full", "完全访问") : wbT("goalLoop.permission.autoShort", "自动")}</b></span>
        </div>
      )}
      {/* A running plan step shows its call details in the expanded subtask below,
          so we omit the inline feed here. Non-step ops (no runningStep) keep it. */}
      {!runningStep && (
        lines.length > 0 ? (
          <ul className="wb-live-feed" ref={feedRef}>
            {lines.map(function (ln, i) {
              var last = i === lines.length - 1;
              return (
                <li key={ln.id || i} className={"wb-live-line" + (last ? " latest" : "")}>
                  <span className="wb-live-dot" />
                  <span className="wb-live-body">{ln.body}</span>
                </li>
              );
            })}
          </ul>
        ) : (
          <AgentReplyBlock text={session.agentReply || wbT("task.workingFallback", "Processing the current task. Please wait...")} />
        )
      )}
      {runningStep && plan.length > 0 && (
        <div className="wb-progress"><span style={{ width: pct + "%" }} /></div>
      )}
      {runningStep && (
        <ul className="wb-step-mini">
          {plan.map(function (s, i) {
            var st = (s.status === "completed" || s.status === "done") ? "done" : s.status === "running" ? "active" : "todo";
            return <li key={s.id} className={st}>{i + 1}. {s.title}</li>;
          })}
        </ul>
      )}
    </WbCard>
  );
}

// paused.
function PausedCard({ session, controller }) {
  var plan = Array.isArray(session.plan) ? session.plan : [];
  var done = plan.filter(function (s) { return s.status === "completed" || s.status === "done"; }).length;
  var current = WorkbenchModel.findNextRunnableStep(plan)
    || plan.find(function (step) { return !isResolvedStepStatus(step && step.status); })
    || plan[plan.length - 1]
    || null;
  return (
    <WbCard tone="paused" icon={ICONS.pause} title={wbT("task.card.paused", "Task paused")}>
      {session.goalLoop && <AgentReplyBlock text={session.agentReply || wbT("goalLoop.paused", "持续执行已暂停，当前进度已保留。")} />}
      <p className="wb-card-hint">
        {plan.length > 0
          ? wbT("task.pausedAt", "Paused at step {n}{title}.", { n: Math.min(done + 1, plan.length), title: current ? ": " + current.title : "" })
          : wbT("task.pausedNoSteps", "This task has not started and has no execution steps yet.")}
      </p>
    </WbCard>
  );
}

function BlockedCard({ session }) {
  var plan = Array.isArray(session.plan) ? session.plan : [];
  var blocked = plan.filter(function (step) {
    return step && !isResolvedStepStatus(step.status) && WorkbenchModel.unmetDependencyIds(plan, step).length > 0;
  });
  return (
    <WbCard tone="confirm" icon={ICONS.alert} title={wbT("task.card.blocked", "Task blocked")}>
      <AgentReplyBlock text={session.agentReply || wbT("task.plan.blockedHint", "Complete or rerun the prerequisite steps before continuing.")} />
      {blocked.length > 0 && (
        <ul className="wb-bullet">
          {blocked.slice(0, 5).map(function (step) { return <li key={step.id}>{step.title}</li>; })}
        </ul>
      )}
    </WbCard>
  );
}

// failed.
function FailedCard({ session, controller }) {
  var plan = Array.isArray(session.plan) ? session.plan : [];
  var failedIdx = plan.findIndex(function (s) { return s.status === "failed"; });
  return (
    <WbCard tone="failed" icon={ICONS.alert} title={wbT("task.card.failed", "Task failed")}>
      <AgentReplyBlock text={session.agentReply || wbT("task.failedFallback", "An error occurred during execution.")} />
      {failedIdx >= 0 && <p className="wb-card-hint">{wbT("task.failedAt", "Failed at step {n}: {title}", { n: failedIdx + 1, title: plan[failedIdx].title })}</p>}
      {session.recommendReflection && (
        <p className="wb-card-hint">{wbT("task.failedReflectionHint", "Suggested review: deep reflect first, then create a new task to try a different approach, or continue in this task.")}</p>
      )}
    </WbCard>
  );
}

// review (awaiting confirm) / completed (confirmed) — 任务完成. The agent's reply
// carries the textual deliverable; downloadable file deliverables are listed
// inline below it so the user can review/grab them without leaving the card.
function CompletionCard({ session, controller, onRightTab, onSelectSession, project, confirmed }) {
  var accept = Array.isArray(session.acceptanceCriteria) ? session.acceptanceCriteria : [];
  var passed = accept.filter(function (a) { return a.status === "passed" || a.status === "done"; }).length;
  var artifacts = Array.isArray(session.artifacts) ? session.artifacts : [];
  return (
    <WbCard tone="done" icon={ICONS.check} title={confirmed ? wbT("task.card.completed", "Task completed") : wbT("task.card.awaitingConfirmation", "Agent finished; awaiting your confirmation")}>
      <AgentReplyBlock text={session.agentReply || wbT("task.completedFallback", "The current task is complete.")} />
      {artifacts.length > 0 && (
        <div className="wb-deliverables">
          <div className="wb-deliverables-label">{wbT("task.deliverables", "Deliverables")}</div>
          {artifacts.map(function (artifact, i) {
            var downloadUrl = "/api/task-sessions/" + encodeURIComponent(session.id) + "/artifacts/" + encodeURIComponent(artifact.id) + "/download";
            var artifactPath = String(artifact.path || "").trim();
            return (
              <a className="workbench-artifact-row wb-artifact-download" href={downloadUrl} download={artifact.name || true}
                title={wbT("task.artifact.download", "Download {name}", { name: artifact.name || "" })} key={artifact.id || i}>
                <span className="wb-artifact-file-icon" aria-hidden="true">
                  <svg viewBox="0 0 24 24">
                    <path d="M7 3.75h6.4L18 8.35v11.9H7z"></path>
                    <path d="M13.25 3.9v4.7h4.7"></path>
                  </svg>
                </span>
                <span className="wb-artifact-file-copy">
                  <b>{artifact.name}</b>
                  {artifactPath && artifactPath !== artifact.name ? <small>{artifactPath}</small> : null}
                </span>
                <span className="wb-artifact-download-icon" aria-hidden="true">
                  <svg viewBox="0 0 24 24">
                    <path d="M12 4v11"></path>
                    <path d="m8 11 4 4 4-4"></path>
                    <path d="M5 19h14"></path>
                  </svg>
                </span>
              </a>
            );
          })}
        </div>
      )}
      <div className="wb-done-grid">
        <button type="button" className="wb-done-stat" onClick={function () { onRightTab && onRightTab("acceptance"); }}>
          <b>{passed} / {accept.length || 0}</b><small>{wbT("task.stat.acceptancePassed", "Acceptance passed")}</small>
        </button>
        <button type="button" className="wb-done-stat" onClick={function () { onRightTab && onRightTab("artifacts"); }}>
          <b>{artifacts.length}</b><small>{wbT("workbenchChat.artifacts", "Artifacts")}</small>
        </button>
      </div>
    </WbCard>
  );
}

// cancelled.
function CancelledCard({ session, controller }) {
  return (
    <WbCard tone="cancelled" icon={ICONS.x} title={wbT("task.card.cancelled", "Task cancelled")}>
      <p className="wb-card-hint">{wbT("task.cancelledHint", "This task was cancelled. Current progress is kept, and you can reopen it to continue.")}</p>
    </WbCard>
  );
}

var ICON_CLOCK = (
  <svg viewBox="0 0 16 16" width="11" height="11" fill="none" stroke="currentColor" strokeWidth="1.5" style={{ flexShrink: 0 }}>
    <circle cx="8" cy="8" r="6.5" /><path d="M8 5v3.2l1.8 1.8" />
  </svg>
);

var ICON_CHEVRON = (
  <svg viewBox="0 0 16 16" width="12" height="12" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round">
    <path d="M5 7l3 3 3-3" />
  </svg>
);

// Pre-run editor shown in an expanded step BEFORE it executes: an editable
// command (the exact prompt handed to the subagent) + a context-file list the
// user can grow by referencing workspace paths or uploading files. Both persist
// onto the step (promptOverride / contextFiles) via controller.patchStep.
// Compact read-only summary for a not-yet-run step expanded in view mode —
// description, prerequisites, command and context files, no editing affordances.
function StepSummary({ session, step, steps }) {
  var prereqTitles = (Array.isArray(step.dependsOn) ? step.dependsOn : []).map(function (id) {
    var dep = steps.find(function (candidate) { return candidate.id === id; });
    return dep ? dep.title : id;
  });
  var ctxFiles = Array.isArray(step.contextFiles) ? step.contextFiles : [];
  var command = (typeof step.promptOverride === "string" && step.promptOverride.length > 0)
    ? step.promptOverride
    : stepExecutionPrompt(session, step);
  return (
    <div className="wbp-summary">
      {step.description ? (
        <div className="wbp-summary-row">
          <span className="wbp-summary-k">说明</span>
          <span className="wbp-summary-v">{step.description}</span>
        </div>
      ) : null}
      <div className="wbp-summary-row">
        <span className="wbp-summary-k">前置</span>
        <span className="wbp-summary-v">
          {prereqTitles.length ? (
            <span className="wbp-summary-chips">
              {prereqTitles.map(function (title, i) { return <span key={i}>{title}</span>; })}
            </span>
          ) : <em className="wbp-summary-none">无</em>}
        </span>
      </div>
      <div className="wbp-summary-row">
        <span className="wbp-summary-k">命令</span>
        <span className="wbp-summary-v wbp-summary-cmd">{command || "—"}</span>
      </div>
      {ctxFiles.length > 0 ? (
        <div className="wbp-summary-row">
          <span className="wbp-summary-k">文件</span>
          <span className="wbp-summary-v">
            <span className="wbp-summary-chips">
              {ctxFiles.map(function (f, i) {
                var isUpload = f && f.source === "upload";
                var label = isUpload ? (f.name || "file") : String((f && (f.path || f.name)) || "").split("/").pop();
                return <span key={i} className="wbp-summary-file">{label}</span>;
              })}
            </span>
          </span>
        </div>
      ) : null}
    </div>
  );
}

// Unified compact editor for a not-yet-run step (edit mode). Mirrors the
// read-only StepSummary's label/value layout so view and edit modes look
// consistent. Plan fields (title/description/prerequisites) save together via
// the Save button; the command persists on blur and context files on change.
function StepEditor({ session, step, steps, controller }) {
  var model = window.WorkbenchModel;
  var defaultPrompt = stepExecutionPrompt(session, step);
  function overrideOf(s) { return (s && typeof s.promptOverride === "string" && s.promptOverride.length > 0) ? s.promptOverride : ""; }
  var [title, setTitle] = useWorkbenchState(step.title || "");
  var [description, setDescription] = useWorkbenchState(step.description || "");
  var [dependsOn, setDependsOn] = useWorkbenchState(Array.isArray(step.dependsOn) ? step.dependsOn : []);
  var [saving, setSaving] = useWorkbenchState(false);
  var [draft, setDraft] = useWorkbenchState(overrideOf(step) || defaultPrompt);
  var [pathInput, setPathInput] = useWorkbenchState("");
  var [adding, setAdding] = useWorkbenchState(false);
  var [uploading, setUploading] = useWorkbenchState(false);
  var [hint, setHint] = useWorkbenchState("");
  var fileRef = useWorkbenchRef(null);

  var stepIndex = steps.findIndex(function (item) { return item && item.id === step.id; });
  var dependencyOptions = steps.slice(0, Math.max(0, stepIndex));
  var contextFiles = Array.isArray(step.contextFiles) ? step.contextFiles : [];
  var hasOverride = overrideOf(step).length > 0;

  useWorkbenchEffect(function () {
    setTitle(step.title || "");
    setDescription(step.description || "");
    setDependsOn(Array.isArray(step.dependsOn) ? step.dependsOn : []);
  }, [step.id, step.title, step.description, JSON.stringify(step.dependsOn || [])]);

  // Re-sync the command textarea when the expanded step changes (the editor
  // instance is reused across steps — the key is stable at .wbp-detail).
  useWorkbenchEffect(function () {
    setDraft(overrideOf(step) || stepExecutionPrompt(session, step));
    setPathInput("");
    setHint("");
  }, [step.id]);

  function toggleDependency(stepId) {
    setDependsOn(function (current) {
      return current.indexOf(stepId) >= 0
        ? current.filter(function (id) { return id !== stepId; })
        : current.concat([stepId]);
    });
  }
  function save() {
    var nextTitle = String(title || "").trim();
    if (!nextTitle || saving) return;
    setSaving(true);
    controller.patchStep(step.id, {
      title: nextTitle,
      description: String(description || "").trim(),
      dependsOn: dependsOn,
    }).finally(function () { setSaving(false); });
  }
  function remove() {
    window.confirmModal({
      body: wbT("task.plan.confirmDeleteStep", "Delete step \"{name}\"?", { name: step.title }),
      confirmLabel: wbT("common.delete", "Delete"),
      danger: true,
    }).then(function (ok) {
      if (ok) controller.deleteStep(step.id);
    });
  }
  function persistPrompt() {
    // Store an override only when it diverges from the default, so a step still
    // tracks a regenerated default prompt until the user actually edits it.
    var trimmed = draft.trim();
    var nextOverride = (trimmed && trimmed !== defaultPrompt.trim()) ? draft : "";
    if ((step.promptOverride || "") === nextOverride) return;
    controller.patchStep(step.id, { promptOverride: nextOverride });
  }
  function resetPrompt() {
    setDraft(defaultPrompt);
    if (step.promptOverride) controller.patchStep(step.id, { promptOverride: "" });
  }
  function addWorkspaceFile() {
    var p = pathInput.trim();
    if (!p || adding) return;
    setAdding(true);
    setHint("");
    model.checkWorkspacePath(session.id, p)
      .then(function (res) {
        if (!res || !res.exists) {
          setHint((res && res.error) ? res.error : "工作区中找不到该文件");
          return;
        }
        var rel = res.path || p;
        var dup = contextFiles.some(function (f) { return f && f.source !== "upload" && f.path === rel; });
        if (dup) { setHint("该文件已添加"); return; }
        controller.patchStep(step.id, { contextFiles: contextFiles.concat([{ source: "workspace", path: rel, name: rel.split("/").pop() }]) });
        setPathInput("");
      })
      .finally(function () { setAdding(false); });
  }
  function pickUpload() { if (fileRef.current) fileRef.current.click(); }
  function onUploadPick(e) {
    var files = e.target.files;
    if (!files || !files.length) return;
    setUploading(true);
    setHint("");
    model.uploadAttachments(files)
      .then(function (uploaded) {
        var tagged = (uploaded || []).map(function (u) { return Object.assign({}, u, { source: "upload" }); });
        controller.patchStep(step.id, { contextFiles: contextFiles.concat(tagged) });
      })
      .catch(function (err) { setHint("上传失败：" + ((err && err.message) || String(err))); })
      .finally(function () { setUploading(false); if (fileRef.current) fileRef.current.value = ""; });
  }
  function removeFile(target) {
    controller.patchStep(step.id, { contextFiles: contextFiles.filter(function (f) { return f !== target; }) });
  }

  return (
    <div className="wbp-summary wbp-summary-edit">
      <label className="wbp-summary-row">
        <span className="wbp-summary-k">标题</span>
        <input className="wbp-edit-input" value={title} disabled={saving} placeholder="步骤标题" onChange={function (e) { setTitle(e.target.value); }} />
      </label>
      <label className="wbp-summary-row">
        <span className="wbp-summary-k">说明</span>
        <textarea className="wbp-edit-input" rows={2} value={description} disabled={saving} placeholder="说明这个步骤要完成什么" onChange={function (e) { setDescription(e.target.value); }} />
      </label>
      <div className="wbp-summary-row">
        <span className="wbp-summary-k">前置</span>
        <div className="wbp-summary-v">
          {dependencyOptions.length ? (
            <div className="wbp-dependency-options">
              {dependencyOptions.map(function (candidate) {
                var checked = dependsOn.indexOf(candidate.id) >= 0;
                return (
                  <label key={candidate.id} className={"wbp-dependency-option" + (checked ? " selected" : "")}>
                    <input type="checkbox" checked={checked} disabled={saving} onChange={function () { toggleDependency(candidate.id); }} />
                    <span>{candidate.title}</span>
                  </label>
                );
              })}
            </div>
          ) : (
            <em className="wbp-summary-none">{wbT("task.plan.noEarlierSteps", "No earlier steps are available.")}</em>
          )}
        </div>
      </div>
      <div className="wbp-summary-row">
        <span className="wbp-summary-k">命令</span>
        <div className="wbp-summary-v">
          <textarea
            className="wbp-edit-input wbp-edit-cmd"
            value={draft}
            rows={5}
            spellCheck={false}
            placeholder="描述要交给 subagent 执行的指令…"
            onChange={function (e) { setDraft(e.target.value); }}
            onBlur={persistPrompt}
          />
          {hasOverride && (
            <div className="wbp-edit-cmd-actions">
              <button type="button" className="wbp-tiny-btn" onClick={resetPrompt}>恢复默认</button>
            </div>
          )}
        </div>
      </div>
      <div className="wbp-summary-row">
        <span className="wbp-summary-k">文件</span>
        <div className="wbp-summary-v">
          {contextFiles.length > 0 && (
            <div className="wbp-ctx-list">
              {contextFiles.map(function (f, i) {
                var isUpload = f && f.source === "upload";
                var label = isUpload ? (f.name || "file") : String((f && (f.path || f.name)) || "").split("/").pop();
                return (
                  <span key={(f && (f.path || f.id || f.name) || "") + "_" + i} className={"wbp-ctx-chip" + (isUpload ? " upload" : "")} title={(f && (f.path || f.name)) || ""}>
                    <span className="wbp-ctx-tag">{isUpload ? "上传" : "工作区"}</span>
                    <span className="wbp-ctx-name">{label}</span>
                    <button type="button" className="wbp-ctx-x" onClick={function () { removeFile(f); }} aria-label="移除文件">{ICONS.x}</button>
                  </span>
                );
              })}
            </div>
          )}
          <div className="wbp-ctx-add">
            <div className="wbp-ctx-add-row">
              <input
                type="text"
                className="wbp-ctx-input"
                value={pathInput}
                placeholder="工作区相对路径，如 src/app.py"
                onChange={function (e) { setPathInput(e.target.value); }}
                onKeyDown={function (e) { if (e.key === "Enter") { e.preventDefault(); addWorkspaceFile(); } }}
              />
              <button type="button" className="wbp-tiny-btn" disabled={adding || !pathInput.trim()} onClick={addWorkspaceFile}>{adding ? "校验中…" : "添加"}</button>
            </div>
            <button type="button" className="wbp-tiny-btn wbp-ctx-upload" disabled={uploading} onClick={pickUpload}>
              <svg viewBox="0 0 16 16" width="12" height="12" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round"><path d="M8 10.5V3.5" /><path d="M5 6l3-3 3 3" /><path d="M3 11v1.5A1.5 1.5 0 0 0 4.5 14h7a1.5 1.5 0 0 0 1.5-1.5V11" /></svg>
              {uploading ? "上传中…" : "上传文件"}
            </button>
            <input ref={fileRef} type="file" multiple style={{ display: "none" }} onChange={onUploadPick} />
          </div>
          {hint && <p className="wbp-ctx-hint">{hint}</p>}
        </div>
      </div>
      <div className="wbp-summary-actions">
        <button type="button" className="wbp-tiny-btn danger" onClick={remove}>{wbT("common.delete", "Delete")}</button>
        <button type="button" className="wb-btn primary compact" disabled={saving || !String(title || "").trim()} onClick={save}>
          {saving ? wbT("common.saving", "Saving...") : wbT("common.save", "Save")}
        </button>
      </div>
    </div>
  );
}

// The 执行计划 list — editable, dependency-aware and sortable before execution.
function TaskPlanList({ session, expandedStepId, onToggleStep, onRightTab, controller }) {
  var steps = Array.isArray(session.plan) ? session.plan : [];
  var [dragStepId, setDragStepId] = useWorkbenchState("");
  var [dragOverId, setDragOverId] = useWorkbenchState("");
  var [adding, setAdding] = useWorkbenchState(false);
  var [newTitle, setNewTitle] = useWorkbenchState("");
  var [newDescription, setNewDescription] = useWorkbenchState("");
  var [savingNew, setSavingNew] = useWorkbenchState(false);
  var [planEditing, setPlanEditing] = useWorkbenchState(false);
  var planStarted = steps.some(function (step) {
    return step && (
      String(step.status || "pending") !== "pending"
      || step.startedAt
      || step.completedAt
      || (Array.isArray(step.progressEvents) && step.progressEvents.length)
      || (Array.isArray(step.toolCalls) && step.toolCalls.length)
    );
  });
  var canEditStructure = !controller.busy
    && ["running", "waiting_for_user"].indexOf(String(session.status || "")) < 0;
  // Add/reorder are blocked by the backend once any step starts executing.
  var canAddReorder = canEditStructure && !planStarted;
  // Step-level editing (delete, update command/contextFiles) stays available
  // as long as the specific step is still pending.
  var editing = canEditStructure && planEditing;

  // Drop out of edit mode the moment the structure locks (execution begins).
  useWorkbenchEffect(function () {
    if (!canEditStructure && (planEditing || adding)) { setPlanEditing(false); setAdding(false); }
  }, [canEditStructure]);

  function exitEditMode() { setPlanEditing(false); setAdding(false); }

  function persistOrder(nextSteps) {
    var validation = WorkbenchModel.validatePlanGraph(nextSteps);
    if (!validation.valid) {
      window.showToast(wbT("task.plan.invalidOrder", "This move would place a step before one of its prerequisites."), "warning");
      return;
    }
    controller.reorderSteps(nextSteps.map(function (step) { return step.id; }));
  }

  function moveStep(sourceId, targetId, placeAfter) {
    if (!canAddReorder || !sourceId || !targetId || sourceId === targetId) return;
    var next = steps.slice();
    var sourceIndex = next.findIndex(function (step) { return step.id === sourceId; });
    if (sourceIndex < 0) return;
    var moved = next.splice(sourceIndex, 1)[0];
    var targetIndex = next.findIndex(function (step) { return step.id === targetId; });
    if (targetIndex < 0) return;
    if (placeAfter) targetIndex += 1;
    next.splice(targetIndex, 0, moved);
    persistOrder(next);
  }

  function moveBy(stepId, delta) {
    var index = steps.findIndex(function (step) { return step.id === stepId; });
    var target = steps[index + delta];
    if (index < 0 || !target) return;
    var next = steps.slice();
    var moved = next.splice(index, 1)[0];
    next.splice(index + delta, 0, moved);
    persistOrder(next);
  }

  function addStep() {
    var title = String(newTitle || "").trim();
    if (!title || savingNew) return;
    setSavingNew(true);
    controller.addStep({ title: title, description: String(newDescription || "").trim(), dependsOn: [] })
      .then(function (store) {
        if (!store) return;
        setNewTitle("");
        setNewDescription("");
        setAdding(false);
      })
      .finally(function () { setSavingNew(false); });
  }

  return (
    <section className="workbench-flow wbp">
      <div className="wbp-head">
        <div>
          <b>{wbT("task.plan.title", "Execution plan")}</b>
          <span>{steps.length}</span>
        </div>
        {canEditStructure && (
          <div className="wbp-head-actions">
            {planEditing ? (
              <>
                {canAddReorder && (
                  <button type="button" className="wb-btn ghost compact" onClick={function () { setAdding(!adding); }}>
                    {adding ? wbT("common.cancel", "Cancel") : wbT("task.plan.addStep", "Add step")}
                  </button>
                )}
                <button type="button" className="wb-btn ghost compact" onClick={exitEditMode}>
                  {wbT("common.done", "Done")}
                </button>
              </>
            ) : (
              <button type="button" className="wb-btn ghost compact wbp-edit-toggle" onClick={function () { setPlanEditing(true); }}>
                {ICONS.edit}<span>{wbT("common.edit", "Edit")}</span>
              </button>
            )}
          </div>
        )}
      </div>
      {adding && (
        <div className="wbp-add-step">
          <input
            autoFocus
            value={newTitle}
            placeholder={wbT("task.plan.newStepTitle", "New step title")}
            onChange={function (e) { setNewTitle(e.target.value); }}
            onKeyDown={function (e) { if (e.key === "Enter") { e.preventDefault(); addStep(); } }}
          />
          <textarea
            rows={2}
            value={newDescription}
            placeholder={wbT("task.plan.newStepDescription", "What should this step accomplish?")}
            onChange={function (e) { setNewDescription(e.target.value); }}
          />
          <div>
            <button type="button" className="wb-btn primary" disabled={savingNew || !String(newTitle || "").trim()} onClick={addStep}>
              {savingNew ? wbT("common.saving", "Saving...") : wbT("task.plan.addStep", "Add step")}
            </button>
          </div>
        </div>
      )}
      <div className="wbp-list">
        {steps.map(function (step, index) {
          var expanded = expandedStepId === step.id;
          var doneStep = isDoneStepStatus(step.status);
          var runningStep = isRunningStepStatus(step.status);
          var failedStep = step.status === "failed";
          var skippedStep = step.status === "skipped";
          var unmetDependencyIds = WorkbenchModel.unmetDependencyIds(steps, step);
          var blockedStep = !doneStep && !runningStep && !failedStep && !skippedStep && unmetDependencyIds.length > 0;
          var state = doneStep ? "done" : runningStep ? "current" : failedStep ? "failed" : skippedStep ? "skipped" : blockedStep ? "blocked" : "idle";
          var statusLabel = doneStep ? wbT("status.done", "Done")
            : runningStep ? wbT("status.running", "Running")
            : failedStep ? wbT("status.failed", "Failed")
            : skippedStep ? wbT("status.skipped", "Skipped")
            : blockedStep ? wbT("task.plan.waitingPrerequisites", "Waiting for prerequisites")
            : wbT("status.pending", "Pending");
          var doneStamp = step.completedAt || step.updatedAt || "";
          var time = doneStep && doneStamp ? WorkbenchModel.formatTime(doneStamp) : "";
          var duration = doneStep ? stepDurationText(step) : "";
          var estimate = runningStep && step.estimate ? String(step.estimate) : "";
          var hasFiles = Array.isArray(step.relatedFiles) && step.relatedFiles.length > 0;
          var progressText = step.currentAction || step.description || "";
          var beforeRun = !step.status || step.status === "pending";
          var isLast = index === steps.length - 1;
          return (
            <div
              key={step.id}
              className={"wbp-step " + state + (expanded ? " expanded" : "") + (dragStepId === step.id ? " dragging" : "") + (dragOverId === step.id ? " drag-over" : "")}
              onDragOver={function (e) { if (canAddReorder && dragStepId) { e.preventDefault(); setDragOverId(step.id); } }}
              onDragLeave={function () { if (dragOverId === step.id) setDragOverId(""); }}
              onDrop={function (e) {
                e.preventDefault();
                var sourceId = dragStepId || e.dataTransfer.getData("text/plain");
                var dropLine = e.currentTarget.querySelector(".wbp-line-main");
                var bounds = dropLine ? dropLine.getBoundingClientRect() : e.currentTarget.getBoundingClientRect();
                var placeAfter = e.clientY > bounds.top + bounds.height / 2;
                setDragStepId("");
                setDragOverId("");
                moveStep(sourceId, step.id, placeAfter);
              }}
            >
              <div className="wbp-rail">
                <button type="button" className={"wbp-node " + state} onClick={function () { onToggleStep(step.id); }} aria-label={expanded ? "收起步骤" : "展开步骤"}>
                  {doneStep ? ICONS.checkSmall : null}
                </button>
                {!isLast && <span className={"wbp-line" + (doneStep ? " done" : "")} />}
              </div>
              <div className="wbp-row" onClick={function () { onToggleStep(step.id); }} role="button" tabIndex={0}
                onKeyDown={function (e) { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); onToggleStep(step.id); } }}>
                <div className="wbp-line-main">
                  <div className="wbp-copy">
                    {canAddReorder && (
                      <button
                        type="button"
                        draggable
                        className="wbp-drag-handle"
                        title={wbT("task.plan.dragToReorder", "Drag to reorder")}
                        aria-label={wbT("task.plan.dragToReorder", "Drag to reorder")}
                        onClick={function (e) { e.stopPropagation(); }}
                        onKeyDown={function (e) {
                          e.stopPropagation();
                          if (e.altKey && e.key === "ArrowUp") { e.preventDefault(); moveBy(step.id, -1); }
                          if (e.altKey && e.key === "ArrowDown") { e.preventDefault(); moveBy(step.id, 1); }
                        }}
                        onDragStart={function (e) {
                          e.stopPropagation();
                          setDragStepId(step.id);
                          e.dataTransfer.effectAllowed = "move";
                          e.dataTransfer.setData("text/plain", step.id);
                        }}
                        onDragEnd={function () { setDragStepId(""); setDragOverId(""); }}
                      >
                        {ICONS.dots}
                      </button>
                    )}
                    <span className="wbp-idx">{index + 1}.</span>
                    <span className="wbp-title">{step.title}</span>
                  </div>
                  <span className={"wbp-status " + state}>{statusLabel}</span>
                  <time className="wbp-time">{time}</time>
                  <span className="wbp-dur">{duration ? <>{ICON_CLOCK}<span>{duration}</span></> : estimate ? <span className="wbp-estimate">预计 {estimate}</span> : null}</span>
                  <span className={"wbp-caret" + (expanded ? " open" : "")}>{ICON_CHEVRON}</span>
                </div>
                {expanded && (
                  <div className="wbp-detail" onClick={function (e) { e.stopPropagation(); }}>
                    {beforeRun ? (
                      editing ? (
                        <StepEditor session={session} step={step} steps={steps} controller={controller} />
                      ) : (
                        <StepSummary session={session} step={step} steps={steps} />
                      )
                    ) : (
                      <div className="wbp-summary">
                        <div className="wbp-summary-row">
                          <span className="wbp-summary-k">进展</span>
                          <span className="wbp-summary-v">
                            {progressText || "等待 Agent 更新这个步骤的进展。"}
                            {Array.isArray(step.progressEvents) && step.progressEvents.length > 0 && (
                              <ul className="wbp-events">
                                {step.progressEvents.slice(-3).map(function (ev, i) {
                                  return <li key={i}>{ev.body || ev.text || ev.message || String(ev)}</li>;
                                })}
                              </ul>
                            )}
                          </span>
                        </div>
                        <div className="wbp-summary-row">
                          <span className="wbp-summary-k">文件</span>
                          <span className="wbp-summary-v">
                            {hasFiles ? (
                              <div className="wbp-file-chips">
                                {step.relatedFiles.map(function (file) {
                                  return <button key={file.path || file.name} type="button" className="wbp-file-chip" onClick={function () { onRightTab("files"); }}>{(file.path || file.name || "").split("/").pop()}</button>;
                                })}
                              </div>
                            ) : <em className="wbp-summary-none">暂无相关文件</em>}
                          </span>
                        </div>
                      </div>
                    )}
                    {!doneStep && (
                      <div className="wbp-detail-actions">
                        {runningStep ? (
                          <button type="button" className="wb-btn danger" onClick={function () { controller.interrupt(); }}>停止执行</button>
                        ) : (
                          <button type="button" className="wb-btn primary" disabled={controller.busy || unmetDependencyIds.length > 0} onClick={function () { controller.runStep(step); }}>执行此步骤</button>
                        )}
                        <button type="button" className="wb-btn ghost" onClick={function () { onRightTab("logs"); }}>查看日志</button>
                        {unmetDependencyIds.length > 0 && <span className="wbp-blocked-hint">{wbT("task.plan.completePrerequisitesFirst", "Complete prerequisite steps first.")}</span>}
                      </div>
                    )}
                  </div>
                )}
              </div>
            </div>
          );
        })}
      </div>
    </section>
  );
}

function composerPlaceholder(status) {
  if (status === "idle" || status === "pending") return wbT("task.placeholder.idle", "Ask a question, give a direct instruction, or describe a task...");
  if (status === "answered" || status === "acted") return wbT("task.placeholder.reply", "Ask a follow-up, give the next instruction, or describe a fuller task...");
  if (status === "running") return wbT("task.placeholder.running", "The agent is running; input is temporarily disabled...");
  if (status === "planning") return wbT("task.placeholder.planning", "Add to or revise the execution plan...");
  if (status === "waiting_for_approval" || status === "waiting_for_user") return wbT("task.placeholder.waiting", "Revise requirements, or approve execution...");
  if (status === "failed") return wbT("task.placeholder.failed", "Explain how to fix it, or revise the request...");
  return wbT("task.placeholder.default", "Add requirements, request changes, or continue this task...");
}

// Quick-action chips below the composer; the set changes with status.
// `guard:false` chips stay enabled while the controller is busy (read-only).
function composerChips(status, controller, onRightTab, session) {
  if (status === "idle" || status === "pending") {
    return [];
  }
  if (status === "answered") {
    return [
      { label: wbT("task.action.promoteToTask", "Make it a task"), onClick: function () { controller.promoteToPlan(); } },
      { label: wbT("task.action.continueEditing", "Continue editing"), onClick: focusComposer },
    ];
  }
  if (status === "acted") {
    return [
      { label: wbT("task.action.viewChanges", "View changes"), guard: false, onClick: function () { onRightTab && onRightTab("files"); } },
      { label: wbT("task.action.promoteToTask", "Make it a task"), onClick: function () { controller.promoteToPlan(); } },
      { label: wbT("task.action.continueEditing", "Continue editing"), onClick: focusComposer },
    ];
  }
  if (status === "planning") {
    return [
      { label: wbT("task.action.approveExecution", "Approve execution"), onClick: function () { controller.approvePlan(); } },
      { label: wbT("task.action.approveRunAll", "Approve & run all"), onClick: function () { controller.approveAndRunAll(); } },
      { label: wbT("goalLoop.action.configure", "持续执行到验收通过"), className: "goal-loop", onClick: function () { controller.configureGoalLoop(); } },
      { label: wbT("task.action.editPlan", "Edit plan"), onClick: focusComposer },
      { label: wbT("task.action.regenerate", "Regenerate"), onClick: function () { controller.regeneratePlan(); } },
    ];
  }
  if (status === "waiting_for_approval" || status === "waiting_for_user" || status === "blocked") {
    if (session && session.goalLoop && status === "blocked") {
      return [
        { label: wbT("task.action.resumeTask", "Resume task"), onClick: function () { controller.resume(); } },
        { label: wbT("task.action.viewLogs", "View logs"), guard: false, onClick: function () { onRightTab && onRightTab("logs"); } },
        { label: wbT("task.action.cancelTask", "Cancel task"), onClick: function () { controller.cancel(); } },
      ];
    }
    return [
      { label: wbT("task.action.approveExecution", "Approve execution"), onClick: function () { controller.execute(); } },
      { label: wbT("task.action.reject", "Reject"), onClick: function () { controller.reject(); } },
    ];
  }
  if (status === "running") {
    return [
      { label: wbT("task.action.stopExecution", "Stop execution"), guard: false, onClick: function () { controller.interrupt(); } },
      { label: wbT("task.action.viewLogs", "View logs"), guard: false, onClick: function () { onRightTab && onRightTab("logs"); } },
      { label: wbT("task.action.viewChanges", "View changes"), guard: false, onClick: function () { onRightTab && onRightTab("files"); } },
    ];
  }
  if (status === "paused") {
    if (session && session.goalLoop) {
      // Budget-exhausted pauses can't be cleared by a plain resume (it would just
      // re-pause), so adjusting the limit is the only real "continue" path.
      var loopStop = session.goalLoop.stopReason || "";
      var budgetPaused = loopStop === "max_runtime" || loopStop === "max_repair_rounds";
      var pausedActions = [];
      if (!budgetPaused) pausedActions.push({ label: wbT("task.action.resumeTask", "Resume task"), onClick: function () { controller.resume(); } });
      pausedActions.push({ label: wbT("goalLoop.action.adjustLimits", "调整限制并继续"), onClick: function () { controller.adjustGoalLoopLimits(); } });
      pausedActions.push({ label: wbT("task.action.viewLogs", "View logs"), guard: false, onClick: function () { onRightTab && onRightTab("logs"); } });
      pausedActions.push({ label: wbT("task.action.viewChanges", "View changes"), guard: false, onClick: function () { onRightTab && onRightTab("files"); } });
      pausedActions.push({ label: wbT("task.action.cancelTask", "Cancel task"), onClick: function () { controller.cancel(); } });
      return pausedActions;
    }
    return [
      { label: wbT("task.action.resumeTask", "Resume task"), onClick: function () { controller.resume(); } },
      { label: wbT("task.action.runAll", "Run all"), onClick: function () { controller.executeAll(); } },
      { label: wbT("task.action.reflect", "深度反思"), onClick: function () { controller.reflect(); } },
      { label: wbT("task.action.reviseRequest", "Revise request"), onClick: focusComposer },
      { label: wbT("task.action.cancelTask", "Cancel task"), onClick: function () { controller.cancel(); } },
    ];
  }
  if (status === "failed") {
    if (hasAcceptanceFailure(session)) {
      return [
        { label: wbT("task.action.reflectFork", "深度反思+新建任务"), onClick: function () { controller.reflectAndFork(); } },
        { label: wbT("task.action.repairProblem", "修复问题"), onClick: function () { controller.repairProblem(); } },
        { label: wbT("task.action.continueModify", "继续修改"), onClick: function () { controller.continueModify(); } },
        { label: wbT("task.action.reviseRequest", "修改要求"), onClick: function () { openAcceptanceEditor(onRightTab); } },
      ];
    }
    return [
      { label: wbT("task.action.reflectFork", "深度反思+新建任务"), onClick: function () { controller.reflectAndFork(); } },
      { label: wbT("task.action.reflect", "深度反思"), onClick: function () { controller.reflect(); } },
      { label: wbT("task.action.retry", "Retry"), onClick: function () { controller.retry(); } },
      { label: wbT("task.action.reviseRequest", "Revise request"), onClick: focusComposer },
      { label: wbT("task.action.skipStep", "Skip this step"), onClick: function () { controller.skipStep(); } },
    ];
  }
  if (status === "review" || status === "done") {
    return [
      { label: wbT("task.action.markComplete", "Mark complete"), onClick: function () { controller.markComplete(); } },
      { label: wbT("task.action.verify", "验收"), onClick: function () { controller.verify(); } },
      { label: wbT("task.action.reflect", "深度反思"), onClick: function () { controller.reflect(); } },
      { label: wbT("task.action.continueEditing", "Continue editing"), onClick: focusComposer },
      { label: wbT("task.action.createFollowUp", "Create follow-up task"), onClick: function () { controller.createFollowUp(); } },
    ];
  }
  if (status === "completed") {
    return [
      { label: wbT("task.action.createFollowUp", "Create follow-up task"), onClick: function () { controller.createFollowUp(); } },
      { label: wbT("task.action.reopen", "Reopen"), onClick: function () { controller.reopen(); } },
    ];
  }
  if (status === "cancelled") {
    return [{ label: wbT("task.action.reopen", "Reopen"), onClick: function () { controller.reopen(); } }];
  }
  return [];
}

// Composer is always bound to the current task. Behaviour + quick-chips depend
// on the task status. Action row: attachments / permission mode / send · stop.
function TaskComposer({ session, controller, onRightTab, attachments, onAttachmentsChange, mode, onModeChange }) {
  var model = window.WorkbenchModel;
  var [draft, setDraft] = useWorkbenchState("");
  var [scopePrompt, setScopePrompt] = useWorkbenchState(null);
  var [modeOpen, setModeOpen] = useWorkbenchState(false);
  var [uploading, setUploading] = useWorkbenchState(false);
  var taRef = useWorkbenchRef(null);
  var fileRef = useWorkbenchRef(null);
  var uploadCountRef = useWorkbenchRef(0);
  var status = String(session.status || "idle");
  var running = status === "running";
  // No plan yet → the composer is a free chat: every send goes through the
  // intent-aware dispatch so the agent itself decides whether to answer, act, or
  // draft a plan. Once a plan exists, the composer refines that plan instead.
  var hasPlan = Array.isArray(session.plan) && session.plan.length > 0;
  attachments = attachments || [];

  useWorkbenchEffect(function () {
    function onFocus() { if (taRef.current) taRef.current.focus(); }
    window.addEventListener("wb-focus-composer", onFocus);
    return function () { window.removeEventListener("wb-focus-composer", onFocus); };
  }, []);

  // Reset transient composer state when switching tasks.
  useWorkbenchEffect(function () { setScopePrompt(null); setModeOpen(false); }, [session.id]);

  function syncHeight() {
    var ta = taRef.current;
    if (ta) { ta.style.height = "auto"; ta.style.height = Math.min(ta.scrollHeight, 160) + "px"; }
  }
  function resetDraft() {
    setDraft("");
    if (taRef.current) taRef.current.style.height = "";
  }

  function dispatch(text) {
    // Don't clear draft yet — wait for the send promise so the user's
    // input stays in the composer if the request is blocked (budget etc.).
    if (!running) controller.send(text).then(function (r) {
      if (!r || !r.__budgetBlock) resetDraft();
    });
  }

  function submit() {
    if (running) { controller.interrupt(); return; }
    var text = draft.trim();
    if ((!text && attachments.length === 0) || controller.busy) return;
    // Rule 2 — keep the agent inside the task only once a plan is committed.
    // Before that the task is still a free conversation, so don't gate it.
    if (hasPlan && model.looksOutOfScope(text)) {
      setScopePrompt({ text: text });
      return;
    }
    dispatch(text);
  }

  function onKeyDown(event) {
    var sc = window.WorkbenchShortcuts;
    // Enter sends; Shift+Enter (or the user's customized newline binding)
    // inserts a newline. IME composition is guarded so multi-keystroke input
    // (zh/ja/ko) does not submit mid-composition. Falls back to the default
    // Enter-to-send behavior if the shortcut module is unavailable.
    if (sc && sc.matches(event, "composer-send")) {
      if (event.nativeEvent && event.nativeEvent.isComposing) return;
      event.preventDefault();
      submit();
      return;
    }
    if (sc && sc.matches(event, "composer-newline")) {
      // Allow the textarea's default Shift+Enter behavior (insert newline).
      return;
    }
    if (!sc && event.key === "Enter" && !event.shiftKey && !event.metaKey && !event.ctrlKey) {
      if (event.nativeEvent && event.nativeEvent.isComposing) return;
      event.preventDefault();
      submit();
      return;
    }
    if (event.key === "Escape") { setModeOpen(false); }
  }

  function pickFiles() { if (fileRef.current) fileRef.current.click(); }
  function addFiles(files) {
    if (!files || !files.length) return;
    uploadCountRef.current += 1;
    setUploading(true);
    model.uploadAttachments(files)
      .then(function (uploaded) {
        onAttachmentsChange(function (current) {
          return (current || []).concat(uploaded || []);
        });
      })
      .catch(function (err) { window.showToast(wbT("workbenchChat.uploadFailed", "Upload failed: {error}", { error: err.message || String(err) }), "error"); })
      .finally(function () {
        uploadCountRef.current = Math.max(0, uploadCountRef.current - 1);
        if (uploadCountRef.current === 0) setUploading(false);
        if (fileRef.current) fileRef.current.value = "";
      });
  }
  function onFilePick(event) {
    addFiles(event.target.files);
  }
  useWorkbenchEffect(function () {
    function onDroppedFiles(event) {
      var files = event && event.detail && event.detail.files;
      addFiles(files);
    }
    window.addEventListener("cyrene:add-task-attachments", onDroppedFiles);
    return function () { window.removeEventListener("cyrene:add-task-attachments", onDroppedFiles); };
  }, []);
  function removeAttachment(index) {
    onAttachmentsChange(attachments.filter(function (_a, i) { return i !== index; }));
  }

  var translatedModes = WB_MODES.map(function (m) { return wbModeMeta(m.id); });

  // While a run is paused awaiting a permission / clarification answer, the only
  // valid actions are on the question card itself — suppress the composer's
  // status chips so no answer buttons sit above the input box.
  var awaitingAnswer = !!(session.pendingQuestion && session.pendingQuestion.id);
  var chips = awaitingAnswer ? [] : composerChips(status, controller, onRightTab, session);
  var disabled = controller.busy || running;
  var current = wbModeMeta(mode || "auto");
  var sendDisabled = running ? false : (disabled || (!draft.trim() && attachments.length === 0));

  return (
    <div className="workbench-composer compact">
      {scopePrompt && (
        <div className="wb-scope-prompt">
          <p>{wbT("task.scopePrompt", "This is outside the current task. Create it as a new follow-up task?")}</p>
          <div className="wb-card-actions">
            <button type="button" className="wb-btn primary" onClick={function () {
              var goal = scopePrompt.text.trim();
              controller.createFollowUp({ title: goal.slice(0, 40), goal: goal });
              setScopePrompt(null);
              resetDraft();
            }}>{wbT("task.createNewTask", "Create new task")}</button>
            <button type="button" className="wb-btn ghost" onClick={function () { var t = scopePrompt.text; setScopePrompt(null); dispatch(t); }}>{wbT("task.mergeCurrent", "Merge into current task")}</button>
            <button type="button" className="wb-btn ghost" onClick={function () { setScopePrompt(null); }}>{wbT("common.cancel", "Cancel")}</button>
          </div>
        </div>
      )}
      {chips.length > 0 && (
        <div className="wb-composer-chips">
          {chips.map(function (c, i) {
            return <button key={i} type="button" className={"wb-chip" + (c.className ? " " + c.className : "")} disabled={controller.busy && c.guard !== false} onClick={c.onClick}>{c.label}</button>;
          })}
        </div>
      )}
      <div className="workbench-composer-box">
        {attachments.length > 0 && (
          <div className="wb-attach-row">
            {attachments.map(function (file, i) {
              var isImg = file.kind === "image" || String(file.content_type || "").indexOf("image") === 0;
              return (
                <div className={"wb-attach-card" + (isImg ? " image" : "")} key={file.id || i}>
                  {isImg && file.url
                    ? <img src={file.url} alt={file.name || "image"} />
                    : <span className="wb-attach-name" title={file.name}>{file.name || "file"}</span>}
                  <button type="button" className="wb-attach-x" onClick={function () { removeAttachment(i); }} aria-label={wbT("workbenchChat.removeAttachment", "Remove attachment")}>{ICONS.x}</button>
                </div>
              );
            })}
          </div>
        )}
        <textarea
          ref={taRef}
          value={draft}
          onChange={function (event) { setDraft(event.target.value); syncHeight(); }}
          onKeyDown={onKeyDown}
          placeholder={composerPlaceholder(status)}
          rows={2}
          disabled={disabled}
        />
        <div className="workbench-composer-actions">
          <input ref={fileRef} type="file" multiple style={{ display: "none" }} onChange={onFilePick} />
          <button type="button" className="wb-composer-icon" title={uploading ? wbT("workbenchChat.uploading", "Uploading...") : wbT("workbenchChat.addAttachment", "Add attachment")} disabled={uploading || running} onClick={pickFiles}>
            {uploading ? <span className="wb-spinner" /> : ICONS.attach}
          </button>
          <span className="wb-popover-anchor">
            <button type="button" className={"wb-composer-icon mode" + (modeOpen ? " active" : "")} title={wbT("workbenchChat.permissionMode", "Permission mode")} onClick={function () { setModeOpen(!modeOpen); }}>
              <span className="wb-mode-ico">{current.icon}</span>
              <span className="wb-mode-label">{current.label}</span>
            </button>
            {modeOpen && (
              <div className="wb-popmenu wb-mode-menu">
                <div className="wb-menu-head">{wbT("workbenchChat.permissionMode", "Permission mode")}</div>
                {translatedModes.map(function (m) {
                  var active = (mode || "auto") === m.id;
                  return (
                    <button key={m.id} type="button" className={"wb-mode-item" + (active ? " active" : "")} onClick={function () { onModeChange(m.id); setModeOpen(false); }}>
                      <span className="wb-mode-item-ico">{m.icon}</span>
                      <span className="wb-mode-item-body">
                        <span className="wb-mode-item-label">{m.label}</span>
                        <span className="wb-mode-item-desc">{m.desc}</span>
                      </span>
                      <span className="wb-mode-item-check">{active ? ICONS.checkSmall : null}</span>
                    </button>
                  );
                })}
              </div>
            )}
          </span>
          <span className="wb-composer-spacer" />
          <button
            type="button"
            className={"wb-composer-send" + (running ? " stop" : "")}
            onClick={submit}
            disabled={sendDisabled}
            title={running ? wbT("workbenchChat.stop", "Stop") : wbT("workbenchChat.send", "Send")}
          >
            {running ? ICONS.stop : (controller.busy ? <span className="wb-spinner" /> : ICONS.send)}
            <span className="wb-composer-send-label">{running ? wbT("workbenchChat.stop", "Stop") : wbT("workbenchChat.send", "Send")}</span>
          </button>
        </div>
      </div>
      <ComposerDisclaimer />
    </div>
  );
}

// AI-generated content disclaimer shown under the composer (i18n).
function ComposerDisclaimer() {
  var t = window.useWorkbenchI18n().t;
  return <div className="wb-composer-disclaimer">{t("workbench.composerDisclaimer")}</div>;
}

function RightContextPanel({ project, session, expandedStepId, tab, onTabChange, onRefresh }) {
  var steps = session && Array.isArray(session.plan) ? session.plan : [];
  var activeStep = steps.find(function (step) { return step.id === expandedStepId; }) || null;
  var isInit = !!(session && session.kind === "init");
  var tabs = isInit ? [
    { id: "context", label: wbT("task.side.context", "Context") },
  ] : [
    { id: "context", label: wbT("task.side.context", "Context") },
    { id: "files", label: wbT("task.side.fileChanges", "File changes") },
    { id: "logs", label: wbT("task.side.runLogs", "Run logs") },
    { id: "acceptance", label: wbT("task.side.acceptance", "Acceptance") },
    { id: "artifacts", label: wbT("workbenchChat.artifacts", "Artifacts") },
  ];
  if (!session) {
    return <aside className="workbench-right-panel"><WbColResizer /><div className="workbench-right-body"><p className="workbench-muted">{wbT("task.noTaskSelected", "Select a task.")}</p></div></aside>;
  }
  return (
    <aside className="workbench-right-panel">
      <WbColResizer />
      <div className="workbench-right-tabs">
        {tabs.map(function (item) {
          return <button key={item.id} type="button" className={tab === item.id ? "active" : ""} onClick={function () { onTabChange(item.id); }}>{item.label}</button>;
        })}
      </div>
      <div className="workbench-right-body">
        {tab === "context" && <ContextTab project={project} session={session} activeStep={activeStep} />}
        {tab === "files" && <FilesTab session={session} activeStep={activeStep} />}
        {tab === "logs" && <LogsTab session={session} />}
        {tab === "acceptance" && <AcceptanceTab session={session} onRefresh={onRefresh} />}
        {tab === "artifacts" && <ArtifactsTab session={session} />}
      </div>
    </aside>
  );
}

// Renders the latest deep-reflection packet attached to a task session.
function ReflectionSection({ session }) {
  var reflection = session && session.reflection;
  var packet = reflection && reflection.packet;
  if (!packet || typeof packet !== "object") return null;
  function bullets(items) {
    var arr = Array.isArray(items) ? items.filter(Boolean) : [];
    if (!arr.length) return null;
    return <ul className="wb-bullet">{arr.map(function (x, i) { return <li key={i}>{String(x)}</li>; })}</ul>;
  }
  return (
    <SideSection title={wbT("task.reflection.title", "Deep reflection")}>
      {packet.goal_gap && <div className="wb-brief-row"><label>{wbT("task.reflection.goalGap", "Goal gap")}</label><p>{String(packet.goal_gap)}</p></div>}
      {Array.isArray(packet.excluded_paths) && packet.excluded_paths.length > 0 && (
        <div className="wb-brief-row"><label>{wbT("task.reflection.excludedPaths", "Avoid")}</label>{bullets(packet.excluded_paths)}</div>
      )}
      {Array.isArray(packet.promising_directions) && packet.promising_directions.length > 0 && (
        <div className="wb-brief-row"><label>{wbT("task.reflection.promisingDirections", "Promising directions")}</label>{bullets(packet.promising_directions)}</div>
      )}
      {packet.next_step && <div className="wb-brief-row"><label>{wbT("task.reflection.nextStep", "Next step")}</label><p>{String(packet.next_step)}</p></div>}
      {Array.isArray(packet.open_questions) && packet.open_questions.length > 0 && (
        <div className="wb-brief-row"><label>{wbT("task.reflection.openQuestions", "Open questions")}</label>{bullets(packet.open_questions)}</div>
      )}
    </SideSection>
  );
}

function ContextTab({ project, session, activeStep }) {
  var constraints = (session && session.constraints) || [];
  var plan = session && Array.isArray(session.plan) ? session.plan : [];
  var planById = {};
  plan.forEach(function (step) { if (step && step.id) planById[step.id] = step; });
  var prerequisites = activeStep
    ? (Array.isArray(activeStep.dependsOn) ? activeStep.dependsOn : []).map(function (id) { return planById[id]; }).filter(Boolean)
    : [];
  var dependents = activeStep
    ? plan.filter(function (step) { return step && Array.isArray(step.dependsOn) && step.dependsOn.indexOf(activeStep.id) >= 0; })
    : [];
  var dependencyCount = plan.reduce(function (count, step) {
    return count + (step && Array.isArray(step.dependsOn) ? step.dependsOn.length : 0);
  }, 0);
  var parentSession = project && session && session.parentSessionId
    ? (project.sessions || []).find(function (item) { return item.id === session.parentSessionId; })
    : null;
  var isInit = !!(session && session.kind === "init");
  if (isInit && window.WorkbenchInitProgress) {
    return (
      <div className="workbench-side-stack">
        <SideSection title={wbT("init.progress.title", "Initialization progress")}>
          {React.createElement(window.WorkbenchInitProgress, { session: session })}
        </SideSection>
      </div>
    );
  }
  return (
    <div className="workbench-side-stack">
      <SideSection title={wbT("task.side.overview", "Task overview")}>
        <div className="wb-kv"><span>{wbT("workbenchChat.statusLabel", "Status")}</span><b>{WorkbenchModel.statusText(session.status)}</b></div>
        {!isInit && <div className="wb-kv"><span>{wbT("create.task.priority", "Priority")}</span><b>{priorityText(session.priority)}</b></div>}
        <p>{wbRealGoal(session) || wbT("task.noGoal", "No task goal yet")}</p>
      </SideSection>
      <ReflectionSection session={session} />
      <SideSection title={wbT("task.side.projectContext", "Project context")}>
        {project && project.context && project.context.summary && !isInit && <div className="wb-agent-body markdown" dangerouslySetInnerHTML={{ __html: wbRenderMarkdown(project.context.summary) }} />}
      </SideSection>
      <SideSection title={wbT("task.side.constraintsCount", "Constraints ({count})", { count: constraints.length })}>
        {constraints.length
          ? constraints.map(function (item, i) { return <div className="workbench-check wb-constraint-row" key={i}><span className="workbench-status-dot amber"></span><span className="wb-constraint-text">{item}</span></div>; })
          : <p className="workbench-muted">{wbT("task.noConstraints", "No constraints yet. Phrases like \"do not\" or \"only\" in the task are recognized as constraints automatically.")}</p>}
      </SideSection>
      {isInit && window.WorkbenchInitProgress ? (
        <SideSection title={wbT("init.progress.title", "Initialization progress")}>
          {React.createElement(window.WorkbenchInitProgress, { session: session })}
        </SideSection>
      ) : (
        <SideSection title={wbT("task.side.taskRelations", "Task relations")}>
          {parentSession ? (
            <div className="wb-brief-row">
              <label>{wbT("task.followUpSource", "Source task")}</label>
              <p>{parentSession.title || wbT("task.thisTask", "this task")}</p>
            </div>
          ) : (
            <p className="workbench-muted">{wbT("task.noDependencies", "No dependent tasks yet.")}</p>
          )}
        </SideSection>
      )}
      {!isInit && (
        <SideSection title={wbT("task.side.stepDependencies", "Step dependencies")}>
          {activeStep ? (
            <div className="wb-step-dependency-side">
              <div className="wb-brief-row">
                <label>{wbT("task.plan.selectedStep", "Selected step")}</label>
                <p>{activeStep.title}</p>
              </div>
              <div className="wb-brief-row">
                <label>{wbT("task.plan.prerequisites", "Prerequisites")}</label>
                {prerequisites.length
                  ? <ul className="wb-bullet">{prerequisites.map(function (step) { return <li key={step.id}>{step.title}</li>; })}</ul>
                  : <p className="workbench-muted">{wbT("task.plan.noPrerequisites", "No prerequisite steps.")}</p>}
              </div>
              <div className="wb-brief-row">
                <label>{wbT("task.plan.dependents", "Dependent steps")}</label>
                {dependents.length
                  ? <ul className="wb-bullet">{dependents.map(function (step) { return <li key={step.id}>{step.title}</li>; })}</ul>
                  : <p className="workbench-muted">{wbT("task.plan.noDependents", "No steps depend on this step.")}</p>}
              </div>
            </div>
          ) : (
            <p className="workbench-muted">
              {dependencyCount
                ? wbT("task.plan.dependencySummary", "{count} dependencies. Select a step to inspect them.", { count: dependencyCount })
                : wbT("task.noDependencies", "No dependent tasks yet.")}
            </p>
          )}
        </SideSection>
      )}
    </div>
  );
}

function FilesTab({ session, activeStep }) {
  var files = [];
  var seen = {};
  var [selectedFile, setSelectedFile] = useWorkbenchState(null);
  var [diffState, setDiffState] = useWorkbenchState({ loading: false, diff: "", error: "", path: "" });
  function add(list) {
    (Array.isArray(list) ? list : []).forEach(function (file) {
      if (!file) return;
      var key = String(file.path || file.name || file.id || "").trim();
      if (!key || seen[key]) return;
      seen[key] = true;
      files.push(file);
    });
  }
  if (activeStep && Array.isArray(activeStep.relatedFiles)) add(activeStep.relatedFiles);
  (session && session.plan || []).forEach(function (step) {
    add(step && step.relatedFiles);
  });
  (session && session.runs || []).forEach(function (run) {
    add(run && run.fileChanges);
  });
  (session && session.artifacts || []).forEach(function (artifact) {
    if (artifact.type === "file_change") add([artifact]);
  });
  useWorkbenchEffect(function () {
    setSelectedFile(null);
    setDiffState({ loading: false, diff: "", error: "", path: "" });
  }, [session && session.id]);
  function openDiff(file) {
    var path = String((file && (file.path || file.name)) || "").trim();
    if (!path || !session || !session.id) return;
    var selectedPath = selectedFile && String(selectedFile.path || selectedFile.name || "");
    if (selectedPath === path) {
      setSelectedFile(null);
      setDiffState({ loading: false, diff: "", error: "", path: "" });
      return;
    }
    setSelectedFile(file);
    setDiffState({ loading: true, diff: "", error: "", path: path });
    WorkbenchModel.fetchFileDiff(session.id, path)
      .then(function (data) {
        setDiffState({
          loading: false,
          diff: data.diff || "",
          error: data.has_changes ? "" : wbT("task.files.noDiff", "No displayable diff in the current git worktree."),
          path: data.path || path,
        });
      })
      .catch(function (err) {
        setDiffState({ loading: false, diff: "", error: (err && err.message) || String(err), path: path });
      });
  }
  return (
    <div className="workbench-side-stack">
      <SideSection title={wbT("task.side.fileChangesCount", "File changes ({count})", { count: files.length })}>
        {files.length ? files.map(function (file, i) {
          var path = file.path || file.name || "";
          var selected = selectedFile && String(selectedFile.path || selectedFile.name || "") === String(path);
          return (
            <div
              className={"workbench-file-row wb-file-diff-card" + (selected ? " active" : "")}
              key={file.id || file.path || file.name || i}
            >
              <button
                type="button"
                className="wb-file-diff-trigger"
                onClick={function () { openDiff(file); }}
                title={selected ? wbT("task.files.collapseDiff", "Collapse file diff") : wbT("task.files.viewDiff", "View file diff")}
              >
                <span>{path}</span>
                <small>{file.status || file.changeType || file.type || ""}</small>
              </button>
              {selected && (
                <div className="wb-file-diff-inline">
                  {diffState.loading ? (
                    <p className="workbench-muted">{wbT("task.files.loadingDiff", "Loading diff...")}</p>
                  ) : diffState.error ? (
                    <p className="workbench-muted">{diffState.error}</p>
                  ) : typeof DiffViewerPanel !== "undefined" ? (
                    <div className="wb-file-diff-panel">
                      <DiffViewerPanel diff={diffState.diff} mode="text" />
                    </div>
                  ) : (
                    <pre className="wb-file-diff-fallback">{diffState.diff}</pre>
                  )}
                </div>
              )}
            </div>
          );
        }) : <p className="workbench-muted">{wbT("task.files.empty", "No file changes recorded for this task yet.")}</p>}
      </SideSection>
    </div>
  );
}

function LogsTab({ session }) {
  var events = session && Array.isArray(session.events) ? session.events : [];
  return (
    <div className="workbench-side-stack">
      <SideSection title={wbT("task.side.runLogsCount", "Run logs ({count})", { count: events.length })}>
        {events.length ? events.slice().reverse().slice(0, 60).map(function (event, i) {
          if (event.type === "ToolCallEvent") {
            return (
              <div className="workbench-log-row wb-log-tool" key={event.id || i}>
                <time>{WorkbenchModel.formatTime(event.createdAt)}</time>
                <span className="wb-log-tool-name">{event.body || event.tool || "tool"}</span>
                {event.argsPreview ? <small className="wb-log-tool-args">{event.argsPreview}</small> : null}
              </div>
            );
          }
          if (event.type === "LlmCallEvent" || event.type === "SubagentStatusEvent") {
            return (
              <div className="workbench-log-row" key={event.id || i}>
                <time>{WorkbenchModel.formatTime(event.createdAt)}</time>
                <span>{WorkbenchModel.eventLabel(event.type)}</span>
                <div className="wb-agent-body markdown wb-log-body" dangerouslySetInnerHTML={{ __html: wbRenderMarkdown(event.body || "") }} />
              </div>
            );
          }
          var logBody = event.body || (event.stepCount != null ? wbT("task.logs.stepCount", "Steps {count}", { count: event.stepCount }) : "");
          return <div className="workbench-log-row" key={event.id || i}><time>{WorkbenchModel.formatTime(event.createdAt)}</time><span>{WorkbenchModel.eventLabel(event.type)}</span>{logBody && <div className="wb-agent-body markdown wb-log-body" dangerouslySetInnerHTML={{ __html: wbRenderMarkdown(logBody) }} />}</div>;
        }) : <p className="workbench-muted">{wbT("task.logs.empty", "No run logs yet.")}</p>}
      </SideSection>
    </div>
  );
}

function AcceptanceTab({ session, onRefresh }) {
  var [busy, setBusy] = useWorkbenchState(false);
  var items = session && Array.isArray(session.acceptanceCriteria) ? session.acceptanceCriteria : [];
  var acceptanceFailure = hasAcceptanceFailure(session);
  var [editing, setEditing] = useWorkbenchState(acceptanceFailure);
  var [draft, setDraft] = useWorkbenchState(items.map(function (item) { return String((item && item.text) || ""); }));
  var passed = items.filter(function (a) { return a.status === "passed" || a.status === "done"; }).length;
  useWorkbenchEffect(function () {
    setDraft(items.map(function (item) { return String((item && item.text) || ""); }));
    if (acceptanceFailure) setEditing(true);
  }, [session && session.id, JSON.stringify(items.map(function (item) { return [item && item.id, item && item.text, item && item.status]; }))]);
  function generate() {
    setBusy(true);
    window.WorkbenchModel.generateAcceptance(session.id)
      .then(function (next) { onRefresh && onRefresh(next); })
      .catch(function (err) { window.showToast(err.message || String(err), "error"); })
      .finally(function () { setBusy(false); });
  }
  // Verify a criterion by clicking it — cycle 待验证 → 已通过 → 未通过 → 待验证.
  function toggle(id) {
    var nextStatus = { pending: "passed", passed: "failed", failed: "pending", done: "pending" };
    var next = items.map(function (a) {
      return a.id === id ? Object.assign({}, a, { status: nextStatus[a.status] || "passed" }) : a;
    });
    setBusy(true);
    window.WorkbenchModel.patchSession(session.id, { acceptanceCriteria: next })
      .then(function (n) { onRefresh && onRefresh(n); })
      .catch(function (err) { window.showToast(err.message || String(err), "error"); })
      .finally(function () { setBusy(false); });
  }
  function saveEdits() {
    var next = items.map(function (item, index) {
      var text = String(draft[index] || "").trim();
      var changed = text !== String((item && item.text) || "").trim();
      return Object.assign({}, item, {
        text: text,
        // A changed criterion needs a fresh verification; do not keep stale
        // failed evidence attached to the new wording.
        status: changed ? "pending" : item.status,
        evidence: changed ? "" : item.evidence,
      });
    }).filter(function (item) { return item.text; });
    if (!next.length) {
      window.showToast("至少保留一条验收条件。", "warning");
      return;
    }
    setBusy(true);
    window.WorkbenchModel.patchSession(session.id, { acceptanceCriteria: next })
      .then(function (n) { setEditing(false); onRefresh && onRefresh(n); })
      .catch(function (err) { window.showToast(err.message || String(err), "error"); })
      .finally(function () { setBusy(false); });
  }
  function cancelEdits() {
    setDraft(items.map(function (item) { return String((item && item.text) || ""); }));
    setEditing(false);
  }
  return (
    <div className="workbench-side-stack">
      <SideSection title={items.length ? wbT("task.side.acceptanceCount", "Acceptance criteria ({passed}/{count})", { passed: passed, count: items.length }) : wbT("task.field.acceptance", "Acceptance criteria")}>
        {items.length ? (
          <React.Fragment>
            {acceptanceFailure && (
              <div className="wb-acceptance-edit-hint">修改验收条件后会重新等待验收，已通过条件保持不变。</div>
            )}
            {items.map(function (item, index) {
              var done = item.status === "passed" || item.status === "done";
              var dot = done ? "green" : item.status === "failed" ? "red" : "muted";
              var label = done ? wbT("task.acceptance.passed", "Passed") : item.status === "failed" ? wbT("task.acceptance.failed", "Failed") : wbT("task.acceptance.pending", "Pending");
              if (editing) {
                return (
                  <div className="wb-accept-edit-row" key={item.id}>
                    <span className={"workbench-status-dot " + dot}></span>
                    <input type="text" autoFocus={index === 0} value={draft[index] || ""} disabled={busy} onChange={function (event) {
                      var value = event.target.value;
                      setDraft(function (current) { var next = current.slice(); next[index] = value; return next; });
                    }} aria-label={"验收条件 " + (index + 1)} />
                    <span className={"wb-accept-state " + dot}>{label}</span>
                  </div>
                );
              }
                return (
                  <button type="button" className="workbench-check wb-accept-toggle" key={item.id} disabled={busy} onClick={function () { toggle(item.id); }} title={wbT("task.acceptance.toggleTitle", "Click to verify this acceptance criterion")}>
                  <span className={"workbench-status-dot " + dot}></span>
                  <span className="wb-accept-copy">
                    <span className="wb-accept-text">{item.text}</span>
                    {item.evidence ? <small className="wb-accept-evidence">验收依据：{item.evidence}</small> : null}
                  </span>
                  <span className={"wb-accept-state " + dot}>{label}</span>
                </button>
              );
            })}
            {editing ? (
              <div className="wb-accept-edit-actions">
                <button type="button" className="wb-btn ghost compact" disabled={busy} onClick={cancelEdits}>取消</button>
                <button type="button" className="wb-btn primary compact" disabled={busy} onClick={saveEdits}>保存验收条件</button>
              </div>
            ) : (
              <button type="button" className="wb-btn ghost compact wb-accept-edit-trigger" disabled={busy} onClick={function () { setEditing(true); }}>修改验收条件</button>
            )}
          </React.Fragment>
        ) : (
          <div className="wb-empty-action">
            <p className="workbench-muted">{wbT("task.acceptance.empty", "No acceptance criteria yet.")}</p>
            <button type="button" className="wb-btn ghost" disabled={busy} onClick={generate}>{busy ? wbT("init.generating", "Generating...") : wbT("task.acceptance.generate", "Ask Agent to generate acceptance criteria")}</button>
          </div>
        )}
      </SideSection>
    </div>
  );
}

function ArtifactsTab({ session }) {
  var artifacts = WorkbenchModel.ensureArtifacts(session);
  return (
    <div className="workbench-side-stack">
      <SideSection title={wbT("task.side.artifactsCount", "Artifacts ({count})", { count: artifacts.length })}>
        {artifacts.length ? artifacts.map(function (artifact, i) {
          var downloadUrl = "/api/task-sessions/" + encodeURIComponent(session.id) + "/artifacts/" + encodeURIComponent(artifact.id) + "/download";
          var artifactPath = String(artifact.path || "").trim();
          return (
            <a
              className="workbench-artifact-row wb-artifact-download"
              href={downloadUrl}
              download={artifact.name || true}
              title={wbT("task.artifact.download", "Download {name}", { name: artifact.name || "" })}
              key={artifact.id || i}
            >
              <span className="wb-artifact-file-icon" aria-hidden="true">
                <svg viewBox="0 0 24 24">
                  <path d="M7 3.75h6.4L18 8.35v11.9H7z"></path>
                  <path d="M13.25 3.9v4.7h4.7"></path>
                </svg>
              </span>
              <span className="wb-artifact-file-copy">
                <b>{artifact.name}</b>
                {artifactPath && artifactPath !== artifact.name ? <small>{artifactPath}</small> : null}
              </span>
              <span className="wb-artifact-download-icon" aria-hidden="true">
                <svg viewBox="0 0 24 24">
                  <path d="M12 4v11"></path>
                  <path d="m8 11 4 4 4-4"></path>
                  <path d="M5 19h14"></path>
                </svg>
              </span>
            </a>
          );
        }) : <p className="workbench-muted">{wbT("task.artifacts.empty", "No artifacts generated for this task yet.")}</p>}
      </SideSection>
    </div>
  );
}

function SideSection({ title, children }) {
  return (
    <section className="workbench-side-section">
      <h3>{title}</h3>
      {children}
    </section>
  );
}

function WorkbenchFullPage({ config, onClose }) {
  return (
    <div className="workbench-fullscreen">
      <div className="workbench-fullscreen-head">
        <button type="button" onClick={onClose}>← 返回工作台</button>
        <b>{config.title}</b>
      </div>
      <div className="workbench-fullscreen-body">
        {config.render()}
      </div>
    </div>
  );
}

function workbenchFullPageConfig(page, setFullPage, store) {
  return { title: page, render: function () { return <div className="workbench-empty">未找到页面。</div>; } };
}

window.WorkbenchApp = WorkbenchApp;
