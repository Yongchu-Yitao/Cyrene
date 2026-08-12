// Four-column Project / Task Session workbench.
var {
  useState: useWorkbenchState,
  useEffect: useWorkbenchEffect,
  useMemo: useWorkbenchMemo,
  useRef: useWorkbenchRef,
} = React;
var WorkbenchModel = window.CyreneUI.require("model");

function wbErrorText(err) {
  try {
    var api = window.CyreneUI.require("api");
    if (api && typeof api.errorText === "function") return api.errorText(err);
  } catch (e) {}
  return String((err && err.message) || err || "");
}

function wbProjectStoreHasUserContent(store) {
  var projects = store && Array.isArray(store.projects) ? store.projects : [];
  return projects.some(function (project) {
    if (!project || typeof project !== "object") return false;
    // The legacy/default project and its blank "New task" session are created
    // automatically. A separately keyed project was explicitly created or
    // imported and therefore counts as user content even before its first run.
    var dataKey = String(project.dataKey || "").trim();
    if (dataKey && dataKey !== "default") return true;
    if (String(project.description || "").trim()) return true;
    if (Array.isArray(project.sharedArtifacts) && project.sharedArtifacts.length) return true;
    var context = project.context && typeof project.context === "object" ? project.context : {};
    if (Array.isArray(context.knowledgeDocumentIds) && context.knowledgeDocumentIds.length) return true;
    return (Array.isArray(project.sessions) ? project.sessions : []).some(function (session) {
      if (!session || typeof session !== "object") return false;
      var title = String(session.title || "").trim();
      var goal = String(session.goal || "").trim();
      if (title && title !== "新任务" && title !== "New task") return true;
      if (goal && goal !== "通过对话明确当前任务目标。") return true;
      return ["plan", "events", "runs", "artifacts", "acceptanceCriteria"].some(function (key) {
        return Array.isArray(session[key]) && session[key].length > 0;
      }) || !!String(session.agentReply || "").trim();
    });
  });
}

function wbRememberWelcomeHandled() {
  try { localStorage.setItem("cyrene-workbench-welcomed", "1"); } catch (e) {}
}

// Native WebContentsView instances live above the renderer's CSS stacking
// context. Keep a shared count of renderer overlays that must cover it, so a
// popover can safely overlap another modal without restoring the native view
// too early.
var wbBrowserOverlayCount = 0;
var wbBrowserOverlayObscured = false;
var wbBrowserOverlayTransition = 0;
function wbSetBrowserOverlayObscured(delta) {
  wbBrowserOverlayCount = Math.max(0, wbBrowserOverlayCount + delta);
  var obscured = wbBrowserOverlayCount > 0;
  var forceRestore = delta === 0 && wbBrowserOverlayCount === 0;
  if (!forceRestore && obscured === wbBrowserOverlayObscured) return;
  wbBrowserOverlayObscured = obscured;
  var transition = wbBrowserOverlayTransition + 1;
  wbBrowserOverlayTransition = transition;
  var bridge = window.cyrene && window.cyrene.browser;

  function setNativeObscured(value) {
    if (transition !== wbBrowserOverlayTransition) return Promise.resolve(null);
    if (!bridge || typeof bridge.setObscured !== "function") return Promise.resolve(null);
    return bridge.setObscured(value).catch(function (err) {
      console.error("setObscured failed", err);
      return null;
    });
  }

  if (obscured) {
    var captureStarted = false;
    var nativeHidden = false;
    function hideNativeAfterPreview() {
      if (nativeHidden || transition !== wbBrowserOverlayTransition) return;
      nativeHidden = true;
      setNativeObscured(true);
    }
    // A native WebContentsView cannot be covered by renderer CSS. Ask the
    // viewport to capture and paint its current frame first; only its onReady
    // callback may hide the native layer. This keeps the page visually stable
    // while renderer menus render above the bitmap proxy.
    window.dispatchEvent(new CustomEvent("workbench:browser-obscured", {
      detail: {
        obscured: true,
        preview: true,
        onCaptureStarted: function () { captureStarted = true; },
        onReady: hideNativeAfterPreview,
      },
    }));
    // No mounted native viewport accepted the preview request. There is no
    // frame to preserve, so fall back to the ordinary authoritative guard.
    if (!captureStarted) hideNativeAfterPreview();
    return;
  }

  // Re-enable the native compositor before asking the renderer proxy to fade
  // away. The viewport keeps its screenshot mounted until a live frame at the
  // current bounds is confirmed, avoiding a white flash in the other direction.
  setNativeObscured(false).finally(function () {
    if (transition !== wbBrowserOverlayTransition) return;
    window.dispatchEvent(new CustomEvent("workbench:browser-obscured", {
      detail: { obscured: false },
    }));
  });
}
// Other classic-script bundles (chat composer and shared feedback host) render
// overlays too. Register the reference-counted coordinator instead of creating
// an ad-hoc browser global or letting each surface race a boolean call.
window.CyreneUI.browserOverlays = window.CyreneUI.register("browser-overlays", {
  adjust: wbSetBrowserOverlayObscured,
});

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

function wbRecentSessionTabs(projects, chatsByProject, recentOpenedKeys, pinnedKeys, hiddenKeys, limit) {
  var items = [];
  (Array.isArray(projects) ? projects : []).forEach(function (project) {
    if (!project) return;
    var projectId = String(project.id || "");
    (Array.isArray(project.sessions) ? project.sessions : []).forEach(function (session) {
      if (!session || !session.id) return;
      items.push({
        id: String(session.id),
        kind: "task",
        title: String(session.title || "New task"),
        projectId: projectId,
        projectName: String(project.name || ""),
        updatedAt: String(session.updatedAt || session.createdAt || ""),
        source: session,
      });
    });
    var chats = chatsByProject && Array.isArray(chatsByProject[projectId])
      ? chatsByProject[projectId]
      : [];
    chats.forEach(function (chat) {
      if (!chat || !chat.id) return;
      items.push({
        id: String(chat.id),
        kind: "chat",
        title: String(chat.title || "New chat"),
        projectId: projectId,
        projectName: String(project.name || ""),
        updatedAt: String(chat.updatedAt || chat.createdAt || ""),
        source: chat,
      });
    });
  });
  var byKey = {};
  items.forEach(function (item) {
    byKey[item.kind + ":" + item.id] = item;
  });
  var ordered = [];
  var seen = {};
  var hidden = {};
  (Array.isArray(hiddenKeys) ? hiddenKeys : []).forEach(function (key) {
    hidden[String(key || "")] = true;
  });
  (Array.isArray(pinnedKeys) ? pinnedKeys : []).forEach(function (key) {
    var normalizedKey = String(key || "");
    var item = byKey[normalizedKey];
    if (!item || hidden[normalizedKey] || seen[normalizedKey]) return;
    seen[normalizedKey] = true;
    ordered.push(Object.assign({}, item, { pinned: true }));
  });
  var visiblePinnedCount = ordered.length;
  (Array.isArray(recentOpenedKeys) ? recentOpenedKeys : []).forEach(function (key) {
    var normalizedKey = String(key || "");
    var item = byKey[normalizedKey];
    if (!item || hidden[normalizedKey] || seen[normalizedKey]) return;
    seen[normalizedKey] = true;
    ordered.push(item);
  });
  items.sort(function (left, right) {
    var byTime = right.updatedAt.localeCompare(left.updatedAt);
    if (byTime) return byTime;
    return right.id.localeCompare(left.id);
  });
  items.forEach(function (item) {
    var key = item.kind + ":" + item.id;
    if (hidden[key] || seen[key]) return;
    seen[key] = true;
    ordered.push(item);
  });
  // A pinned session is a fixed topbar tab, so it must not disappear merely
  // because the ordinary recent-tab quota has already been filled.
  return ordered.slice(0, Math.max(visiblePinnedCount, Math.max(0, Number(limit) || 0)));
}

function wbVisibleSessionTabs(items, activeKey, limit) {
  var candidates = Array.isArray(items) ? items : [];
  var maxItems = Math.max(1, Number(limit) || 3);
  var visible = candidates.slice(0, maxItems);
  var active = String(activeKey || "");
  if (active && !visible.some(function (item) { return item.kind + ":" + item.id === active; })) {
    var activeItem = candidates.find(function (item) { return item.kind + ":" + item.id === active; });
    if (activeItem) visible = visible.slice(0, Math.max(0, maxItems - 1)).concat([activeItem]);
  }
  var visibleKeys = {};
  visible.forEach(function (item) { visibleKeys[item.kind + ":" + item.id] = true; });
  return {
    visible: visible,
    overflow: candidates.filter(function (item) { return !visibleKeys[item.kind + ":" + item.id]; }),
  };
}

function wbSessionPlanProgress(item) {
  var source = item && item.source || {};
  var plan = [];
  if (item && item.kind === "chat") {
    var activePlan = source.activePlan && typeof source.activePlan === "object" ? source.activePlan : null;
    plan = activePlan && Array.isArray(activePlan.steps) ? activePlan.steps : [];
  } else {
    plan = Array.isArray(source.plan) ? source.plan : [];
  }
  var total = plan.length || Math.max(0, Number(source.planStepCount) || 0);
  var resolved = { completed: true, done: true, skipped: true };
  var completed = plan.length
    ? plan.filter(function (step) { return step && resolved[String(step.status || "pending")]; }).length
    : Math.max(0, Number(source.planCompletedCount) || 0);
  var currentIndex = 0;
  var currentStep = null;
  for (var index = 0; index < plan.length; index += 1) {
    if (String(plan[index] && plan[index].status || "") === "running" || String(plan[index] && plan[index].status || "") === "in_progress") {
      currentIndex = index + 1;
      currentStep = plan[index];
      break;
    }
  }
  if (!currentIndex) {
    currentIndex = Math.max(0, Number(source.planCurrentIndex) || 0);
    currentStep = plan[currentIndex - 1] || null;
  }
  if (!currentIndex && total && completed < total) currentIndex = Math.min(total, completed + 1);
  return {
    current: currentIndex,
    completed: completed,
    total: total,
    title: String(currentStep && currentStep.title || source.planCurrentTitle || ""),
    action: String(currentStep && (currentStep.currentAction || currentStep.description) || source.planCurrentAction || ""),
  };
}

function wbActivityStatusIsActive(status) {
  return ["running", "resumed", "planning", "initializing", "finishing", "waiting"].indexOf(
    String(status || "").toLowerCase()
  ) >= 0;
}

function wbActivityStatusIsTerminal(status) {
  return [
    "done", "completed", "success", "failed", "error", "timeout", "paused",
    "blocked", "review", "waiting_for_user", "awaiting_user",
    "waiting_for_approval", "cancelled", "canceled", "interrupted", "stopped",
  ].indexOf(String(status || "").toLowerCase()) >= 0;
}

function wbSessionActivityPhase(item, runtime, live) {
  var source = item && item.source || {};
  function statusIsActive(status) {
    return ["running", "resumed", "planning", "initializing", "finishing", "waiting"].indexOf(
      String(status || "").toLowerCase()
    ) >= 0;
  }
  var sourceUpdatedAt = Date.parse(String(source.updatedAt || "")) || 0;
  var liveStatusAt = Number(live && live.statusAt) || 0;
  var liveStatusIsFresh = !!(live && live.status) && (!sourceUpdatedAt || liveStatusAt > sourceUpdatedAt);
  var livePresenceIsFresh = !!live && (!sourceUpdatedAt || Number(live.lastEventAt || 0) > sourceUpdatedAt);
  var persistedRaw = String(source.runStatus || source.status || "idle").toLowerCase();
  var sourceRunKey = String(source.lastRun && source.lastRun.id || "").trim();
  var liveRunKey = String(live && live.runKey || "").trim();
  var liveBelongsToNewerRun = !!liveRunKey && (!sourceRunKey || liveRunKey !== sourceRunKey);
  // Network delivery time can be a few milliseconds later than the durable
  // completion timestamp. A lingering tool/phase event from the SAME run must
  // never resurrect a completed, failed, cancelled, or awaiting conversation.
  // A different run id is still allowed to become live before the next list
  // summary arrives, preserving instant background-run feedback.
  var durableTerminalWins = wbActivityStatusIsTerminal(persistedRaw) && !runtime && !liveBelongsToNewerRun;
  var raw = String((liveStatusIsFresh && !durableTerminalWins && live.status) || persistedRaw).toLowerCase();
  var activeSignal = !!runtime || !!source.agentBusy || !!(live && live.active);
  var hasPendingQuestion = !!(source.pendingQuestion && source.pendingQuestion.id);
  var livePresenceIsCredible = !!(!durableTerminalWins && livePresenceIsFresh && live && (
    live.phaseActive
    || Object.keys(live.activeTools || {}).length
    || Object.keys(live.agents || {}).some(function (key) {
      return statusIsActive(live.agents[key] && live.agents[key].status);
    })
    || (liveStatusIsFresh && statusIsActive(live.status))
  ));
  // The module-level Chat runtime exists only while a stream is attached. It
  // is stronger evidence than a delayed/stale SSE summary, including a prior
  // tool-level failure cached before the next lifecycle update arrives.
  if (runtime) return { phase: "running", reason: "running", active: true };
  // Presence is independent from the last durable lifecycle result. A newer
  // tool/subagent event proves that work is happening even while the list
  // summary still describes the previous exchange.
  if (livePresenceIsCredible) return { phase: "running", reason: "running", active: true };
  if (source.agentBusy) return {
    phase: /plan/i.test(String(source.agentBusy && (source.agentBusy.type || source.agentBusy.label) || "")) ? "planning" : "running",
    reason: "running",
    active: true,
  };
  if (["failed", "error", "timeout"].indexOf(raw) >= 0) return { phase: "failed", reason: "failed", active: false };
  if (hasPendingQuestion || ["waiting_for_user", "awaiting_user"].indexOf(raw) >= 0) return { phase: "attention", reason: "input", active: false };
  if (raw === "waiting_for_approval") return { phase: "attention", reason: "approval", active: false };
  if (raw === "review") return { phase: "attention", reason: "review", active: false };
  if (raw === "blocked") return { phase: "attention", reason: "blocked", active: false };
  if (raw === "paused") return { phase: "paused", reason: "paused", active: false };
  if (["cancelled", "canceled", "interrupted", "stopped"].indexOf(raw) >= 0) return { phase: "cancelled", reason: "cancelled", active: false };
  if (["running", "resumed", "finishing", "answered", "acted"].indexOf(raw) >= 0) return { phase: "running", reason: "running", active: true };
  if (["planning", "initializing", "proposed"].indexOf(raw) >= 0) return { phase: "planning", reason: "planning", active: activeSignal };
  if (["done", "completed", "success"].indexOf(raw) >= 0) return { phase: "completed", reason: "completed", active: false };
  if (item && item.kind === "chat" && Number(source.messageCount || 0) > 0) return { phase: "completed", reason: "completed", active: false };
  return { phase: "idle", reason: "idle", active: false };
}

function wbLatestRuntimeActivity(runtime) {
  if (!runtime) return null;
  var progress = Array.isArray(runtime.progress) ? runtime.progress : [];
  for (var index = progress.length - 1; index >= 0; index -= 1) {
    var entry = progress[index];
    if (!entry) continue;
    if (entry.kind === "tool" && entry.status === "running") {
      return { kind: "tool", label: String(entry.text || ""), detail: String(entry.preview || "") };
    }
    if (entry.kind === "phase" && (entry.text || entry.detailKey)) {
      return { kind: "phase", label: String(entry.text || entry.detailKey || ""), detail: String(entry.preview || "") };
    }
  }
  var activities = Array.isArray(runtime.activities) ? runtime.activities : [];
  var latest = activities.length ? activities[activities.length - 1] : null;
  if (latest && latest.reasoningActive) return { kind: "reasoning", label: "Thinking", detail: "" };
  if (runtime.finalizing) return { kind: "finalizing", label: "Finalizing", detail: "" };
  return null;
}

function wbSessionActivitySnapshot(item, runtime, live, browserState) {
  var state = wbSessionActivityPhase(item, runtime, live);
  var source = item && item.source || {};
  var progress = wbSessionPlanProgress(item);
  // Activity events are ephemeral. The persisted session status is authoritative
  // once a run settles, so never surface an old tool/LLM event as "current" on
  // an idle, completed, paused, failed, or attention-waiting session.
  var activity = state.active
    ? (wbLatestRuntimeActivity(runtime) || (live && live.active ? live.activity : null) || null)
    : null;
  var browser = browserState && typeof browserState === "object" ? browserState : {};
  var tabs = Array.isArray(browser.tabs) ? browser.tabs : [];
  var browserTab = tabs.find(function (tab) {
    return String(tab && tab.id || "") === String(browser.activeTabId || "");
  }) || browser.activeTab || tabs[0] || null;
  if (state.phase === "running" && browserTab && browserTab.url && activity && /browser|browse|web|navigate|click/i.test(String(activity.label || ""))) {
    var domain = "";
    try { domain = new URL(browserTab.url).hostname.replace(/^www\./, ""); } catch (e) {}
    activity = { kind: "browser", label: domain || String(browserTab.title || browserTab.url), detail: "Browsing" };
  }
  var agentsById = live && live.agents || {};
  var agents = Object.keys(agentsById).map(function (id) { return agentsById[id]; });
  var sourceUpdatedAt = Date.parse(String(source.updatedAt || "")) || 0;
  var lastEventAt = Math.max(sourceUpdatedAt, Number(live && live.lastEventAt) || 0);
  return {
    phase: state.phase,
    reason: state.reason,
    isLive: !!state.active,
    progress: progress,
    activity: activity,
    agents: agents,
    activeAgentCount: agents.filter(function (agent) {
      return ["running", "resumed", "waiting"].indexOf(String(agent.status || "")) >= 0;
    }).length,
    morphUntil: state.phase === "completed" && lastEventAt ? lastEventAt + 8000 : 0,
    capabilities: {
      canPause: item && item.kind === "task" && state.phase === "running",
      canStop: item && item.kind === "chat" && state.phase === "running",
    },
  };
}

function wbSessionActivityRank(activity) {
  return { failed: 0, attention: 1, running: 2, planning: 3, paused: 4, cancelled: 5, completed: 6, idle: 7 }[
    String(activity && activity.phase || "idle")
  ];
}

function wbOverflowSessionTime(item) {
  var value = item && item.updatedAt;
  if (typeof value === "number" && Number.isFinite(value)) return value;
  return Date.parse(String(value || "")) || 0;
}

function wbSplitOverflowSessions(items) {
  var groups = { regular: [], exceptional: [] };
  (Array.isArray(items) ? items : []).slice().sort(function (left, right) {
    var timeOrder = wbOverflowSessionTime(right) - wbOverflowSessionTime(left);
    return timeOrder || String(left && left.title || "").localeCompare(String(right && right.title || ""));
  }).forEach(function (item) {
    var phase = String(item && item.activity && item.activity.phase || "idle");
    groups[phase === "attention" || phase === "failed" ? "exceptional" : "regular"].push(item);
  });
  return groups;
}

function wbRememberOpenedSessionKey(recentOpenedKeys, visibleSessionKeys, key, limit) {
  var list = Array.isArray(recentOpenedKeys) ? recentOpenedKeys : [];
  var visible = Array.isArray(visibleSessionKeys) ? visibleSessionKeys : [];
  var normalizedKey = String(key || "");
  var maxItems = Math.max(0, Number(limit) || 0);
  if (!normalizedKey || !maxItems) return list;

  // Selecting a tab that is already visible must not turn the strip into an
  // MRU carousel. Snapshot any fallback tabs at the end of the stored order so
  // later title/status/timestamp refreshes cannot reshuffle them either.
  if (visible.indexOf(normalizedKey) >= 0) {
    var stable = list.slice();
    visible.forEach(function (visibleKey) {
      var normalizedVisibleKey = String(visibleKey || "");
      if (normalizedVisibleKey && stable.indexOf(normalizedVisibleKey) < 0) {
        stable.push(normalizedVisibleKey);
      }
    });
    stable = stable.slice(0, maxItems);
    if (
      stable.length === list.length
      && stable.every(function (item, index) { return item === list[index]; })
    ) {
      return list;
    }
    return stable;
  }

  return [normalizedKey].concat(list.filter(function (item) {
    return item !== normalizedKey;
  })).slice(0, maxItems);
}

function wbDeliverResourceToChat(chatId, resource) {
  var target = String(chatId || "");
  if (!target || !resource || resource.kind === "browser") return false;
  try {
    if (resource.kind === "file") {
      var file = resource.file || resource;
      var attachKey = "cyrene-wbc-attach-" + target;
      var current = JSON.parse(localStorage.getItem(attachKey) || "[]");
      if (!Array.isArray(current)) current = [];
      var identity = String(file.id || file.path || file.url || file.name || "");
      if (!identity || !current.some(function (item) {
        return String(item.id || item.path || item.url || item.name || "") === identity;
      })) {
        current.push(file);
        localStorage.setItem(attachKey, JSON.stringify(current));
      }
    } else if (resource.kind === "snippet") {
      var draftKey = "cyrene-wbc-draft-" + target;
      var previous = localStorage.getItem(draftKey) || "";
      var quote = String(resource.text || "").trim().split("\n").map(function (line) {
        return "> " + line;
      }).join("\n");
      if (quote) localStorage.setItem(draftKey, previous ? previous + "\n\n" + quote : quote);
    } else {
      return false;
    }
    window.dispatchEvent(new CustomEvent("cyrene:add-chat-attachments", {
      detail: { targetChatId: target, resource: resource },
    }));
    return true;
  } catch (e) {
    return false;
  }
}

function wbCopyBrowserToChat(chatId, resource) {
  var target = String(chatId || "");
  var source = resource || {};
  var owner = String(source.ownerSessionId || "");
  var url = String(source.url || "").trim();
  var bridge = window.cyrene && window.cyrene.browser;
  if (!target || !url || target === owner || !bridge || typeof bridge.createTab !== "function") {
    return Promise.resolve(false);
  }
  return bridge.createTab({
    sessionId: target,
    url: url,
    activate: true,
  }).then(function (state) {
    if (!state || state.ok === false) throw new Error(state && state.error || "browser_copy_failed");
    window.dispatchEvent(new CustomEvent("cyrene:browser-copied-to-chat", {
      detail: {
        targetChatId: target,
        sourceChatId: owner,
        url: url,
        title: String(source.title || ""),
      },
    }));
    return true;
  }).catch(function () {
    return false;
  });
}

function WorkbenchSessionMenuFileName({ name }) {
  var labelRef = useWorkbenchRef(null);
  var [overflowWidth, setOverflowWidth] = useWorkbenchState(0);
  var text = String(name || "");

  useWorkbenchEffect(function () {
    var node = labelRef.current;
    if (!node) return undefined;
    var frame = 0;
    function measure() {
      cancelAnimationFrame(frame);
      frame = requestAnimationFrame(function () {
        var content = node.firstElementChild;
        var contentWidth = content ? content.scrollWidth : node.scrollWidth;
        var next = Math.max(0, Math.ceil(contentWidth - node.clientWidth));
        setOverflowWidth(function (current) { return current === next ? current : next; });
      });
    }
    measure();
    var observer = typeof ResizeObserver !== "undefined" ? new ResizeObserver(measure) : null;
    if (observer) observer.observe(node);
    window.addEventListener("resize", measure);
    return function () {
      cancelAnimationFrame(frame);
      if (observer) observer.disconnect();
      window.removeEventListener("resize", measure);
    };
  }, [text]);

  return (
    <span
      ref={labelRef}
      className={"workbench-session-menu-file-name" + (overflowWidth > 0 ? " is-overflowing" : "")}
      title={overflowWidth > 0 ? text : undefined}
      style={{
        "--wb-file-name-travel": overflowWidth + "px",
        "--wb-file-name-duration": Math.max(2.4, Math.min(8, overflowWidth / 34)) + "s",
      }}
    >
      <span>{text}</span>
    </span>
  );
}

// Keep an already-opened surface mounted so its local UI state (selection,
// scroll position, drafts, side panels) survives navigation, but stop parent
// Workbench updates from re-rendering it while hidden. In particular, changing
// project used to synchronously render every module the user had ever opened,
// plus the hidden task surface. A hidden surface receives the newest props the
// next time `active` becomes true; child-owned state updates still work because
// React.memo only filters parent-driven renders.
var WorkbenchStableSurface = React.memo(
  function WorkbenchStableSurface({ active, children }) {
    return <div style={{ display: active ? "contents" : "none" }}>{children}</div>;
  },
  function keepHiddenSurfaceStable(prev, next) {
    return !prev.active && !next.active;
  }
);

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

function wbActivityEventRunKey(data) {
  return String(data && (data.runId || data.run_id || data.round_id) || "").trim();
}

function wbActivityEventTimestamp(data) {
  return Date.parse(String(data && data.timestamp || "")) || Date.now();
}

function wbReduceSessionActivity(prior, data) {
  var previous = prior && typeof prior === "object" ? prior : {};
  var type = String(data && data.type || "");
  var eventAt = wbActivityEventTimestamp(data);
  var runKey = wbActivityEventRunKey(data);
  var previousRunKey = String(previous.runKey || "");
  var next = Object.assign({}, previous, {
    agents: Object.assign({}, previous.agents || {}),
    activeTools: Object.assign({}, previous.activeTools || {}),
    lastEventAt: eventAt,
  });

  var incomingLifecycleStatus = "";
  if (type === "goal_loop_update") {
    var loop = data.goal_loop && typeof data.goal_loop === "object" ? data.goal_loop : {};
    incomingLifecycleStatus = String(loop.status || "");
    runKey = runKey || String(loop.id || loop.runId || loop.run_id || "");
  } else if (type === "session_update") {
    incomingLifecycleStatus = String(data.status || "");
  } else if (type === "error") {
    incomingLifecycleStatus = "failed";
  } else if (type === "interrupted") {
    incomingLifecycleStatus = "cancelled";
  } else if (type === "awaiting_user") {
    incomingLifecycleStatus = "awaiting_user";
  }

  var startsNewRun = !!runKey && !!previousRunKey && runKey !== previousRunKey;
  if (!startsNewRun && incomingLifecycleStatus && wbActivityStatusIsActive(incomingLifecycleStatus)) {
    startsNewRun = wbActivityStatusIsTerminal(previous.status);
  }
  if (startsNewRun) {
    next.agents = {};
    next.activeTools = {};
    next.activity = null;
    next.phaseActive = false;
  }
  if (runKey) next.runKey = runKey;

  if (incomingLifecycleStatus) {
    next.status = incomingLifecycleStatus;
    next.statusAt = eventAt;
    if (wbActivityStatusIsTerminal(incomingLifecycleStatus)) {
      next.activeTools = {};
      next.phaseActive = false;
      next.activity = null;
      Object.keys(next.agents).forEach(function (key) {
        if (wbActivityStatusIsActive(next.agents[key] && next.agents[key].status)) {
          next.agents[key] = Object.assign({}, next.agents[key], { status: "done" });
        }
      });
    }
  } else if (type === "subagent_update") {
    var agentId = String(data.agent_id || data.caller || "agent");
    next.agents[agentId] = {
      id: agentId,
      name: String(data.name || agentId),
      task: String(data.task || data.message || ""),
      status: String(data.status || "running"),
    };
    if (wbActivityStatusIsActive(data.status || "running")) {
      next.activity = {
        kind: "subagent",
        label: String(data.name || agentId),
        detail: String(data.task || data.message || ""),
      };
    } else {
      var remainingAgentId = Object.keys(next.agents).reverse().find(function (key) {
        return wbActivityStatusIsActive(next.agents[key] && next.agents[key].status);
      });
      next.activity = remainingAgentId ? {
        kind: "subagent",
        label: String(next.agents[remainingAgentId].name || remainingAgentId),
        detail: String(next.agents[remainingAgentId].task || ""),
      } : null;
    }
  } else if (type === "phase_transition") {
    var phaseTarget = String(data.to || "");
    next.phaseActive = !/done|complete|finish|idle|cancel|error|fail/i.test(phaseTarget);
    next.activity = next.phaseActive ? {
      kind: "phase",
      label: String(data.detail || data.detail_key || phaseTarget),
      detail: "",
      failed: !!data.failed,
    } : null;
  } else if (type === "llm_call") {
    // `llm_call` is emitted as a completed accounting event. Live reasoning is owned by the
    // per-chat runtime and must not resurrect presence here.
  } else if (["tool_call", "tool_call_started", "tool_call_progress", "tool_call_finished"].indexOf(type) >= 0) {
    var toolName = String(data.tool || "");
    var toolId = String(data.tool_call_id || data.toolCallId || (data.caller || "agent") + ":" + toolName);
    var toolActivity = {
      kind: /browser|browse|web|navigate|click/i.test(toolName) ? "browser" : "tool",
      label: toolName,
      detail: wbArgsPreview(data.args),
      failed: !!data.failed,
    };
    if (type === "tool_call_started" || type === "tool_call_progress") {
      next.activeTools[toolId] = toolActivity;
      next.activity = toolActivity;
    } else {
      delete next.activeTools[toolId];
      var remainingToolIds = Object.keys(next.activeTools);
      next.activity = remainingToolIds.length
        ? next.activeTools[remainingToolIds[remainingToolIds.length - 1]]
        : null;
    }
  }

  var lifecycleActive = wbActivityStatusIsActive(next.status);
  var toolsActive = Object.keys(next.activeTools || {}).length > 0;
  var agentsActive = Object.keys(next.agents || {}).some(function (key) {
    return wbActivityStatusIsActive(next.agents[key] && next.agents[key].status);
  });
  next.active = lifecycleActive || toolsActive || agentsActive || !!next.phaseActive;
  if (next.active && !next.activity) {
    var fallbackToolIds = Object.keys(next.activeTools || {});
    if (fallbackToolIds.length) {
      next.activity = next.activeTools[fallbackToolIds[fallbackToolIds.length - 1]];
    } else {
      var fallbackAgentId = Object.keys(next.agents || {}).reverse().find(function (key) {
        return wbActivityStatusIsActive(next.agents[key] && next.agents[key].status);
      });
      if (fallbackAgentId) {
        next.activity = {
          kind: "subagent",
          label: String(next.agents[fallbackAgentId].name || fallbackAgentId),
          detail: String(next.agents[fallbackAgentId].task || ""),
        };
      }
    }
  }
  return next;
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

// Convert the stable locator stored with a notification into the same payload
// used by global-search navigation. Keeping one navigation path means a click
// can open an already-mounted module or wait for that module to finish loading.
function wbNotificationNavigationTarget(item) {
  if (!item) return null;
  var meta = item.meta && typeof item.meta === "object" ? item.meta : {};
  var base = {
    projectId: item.projectId || "",
    notificationId: item.id || "",
  };
  if (meta.chatId) return Object.assign(base, { type: "chat", chatId: meta.chatId });
  if (meta.sessionId) return Object.assign(base, { type: "task", sessionId: meta.sessionId, runId: meta.runId || "" });
  if (meta.taskId || meta.entityId) {
    return Object.assign(base, {
      type: "schedule",
      taskId: meta.taskId || "",
      entityId: meta.entityId || "",
      nextRun: meta.nextRun || "",
      dueDate: meta.dueDate || "",
    });
  }
  if (meta.documentId || meta.docId) {
    return Object.assign(base, { type: "knowledge", docId: meta.documentId || meta.docId });
  }
  return null;
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
function WbColResizer({ cardEdge }) {
  var handleRef = useWorkbenchRef(null);
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
  function setSemanticWidth(input) {
    var handle = handleRef.current;
    var panel = handle && handle.closest(".workbench-right-panel, .wbc-side");
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
    try { localStorage.setItem(WB_RIGHT_STORE, String(next)); } catch (err) {}
    return { width: next, minimum: minW, maximum: maxW };
  }
  useWorkbenchEffect(function () {
    if (!window.CyreneUI.has("uiSurface")) return undefined;
    var uiSurface = window.CyreneUI.require("uiSurface");
    return uiSurface.register({
      node_id: "right_panel_separator",
      parent_id: "root",
      scope: "main",
      get_node: function () {
        var handle = handleRef.current;
        var panel = handle && handle.closest(".workbench-right-panel, .wbc-side");
        return handle && handle.isConnected && panel ? {
          role: "separator",
          name: window.CyreneUI.require("i18n").t("rail.resizeHandle", null, "Right panel width"),
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
  }, [cardEdge]);
  function emitResizeHint(active) {
    // The chat panel embeds the hit target in its floating card. Its own border
    // is the resize affordance, so do not draw the legacy full-height guide.
    if (cardEdge) return;
    document.body.classList.toggle("wb-col-resize-hover", active === true);
    try {
      window.dispatchEvent(new CustomEvent("workbench:right-resize-hint", {
        detail: { active: active === true },
      }));
    } catch (err) {}
  }
  var title = window.CyreneUI.require("i18n").t(
    "rail.resizeHandle",
    null,
    "Drag to resize",
  );
  return (
    <div
      ref={handleRef}
      className={"wb-col-resizer" + (cardEdge ? " card-edge" : "")}
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
function WorkbenchApp({ theme, actualTheme, onToggleTheme, needsOnboarding }) {
  var dataStore = window.CyreneUI.require("data");
  dataStore.useVersion();
  var dataState = dataStore.state;
  var workbenchI18n = window.CyreneUI.require("i18n").use();
  var t = workbenchI18n.t;
  var model = window.CyreneUI.require("model");
  var [store, setStore] = useWorkbenchState(function () {
    return model.normalizeStore({ projects: [] });
  });
  var [loading, setLoading] = useWorkbenchState(true);
  var [error, setError] = useWorkbenchState("");
  var autoWelcomePendingRef = useWorkbenchRef(false);
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
      // Do not decide from origin-scoped localStorage alone. The desktop may
      // move to a fallback port, which creates a fresh storage origin even for
      // an established user. Wait for authoritative backend content first.
      if (!localStorage.getItem("cyrene-workbench-welcomed")) {
        autoWelcomePendingRef.current = true;
      }
      return null;
    } catch (e) { return null; }
  });
  var sidebarModuleWheelRef = useWorkbenchRef({ delta: 0, direction: 0, lockedUntil: 0 });
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
  var [newChatRequestId, setNewChatRequestId] = useWorkbenchState(0);
  var [mountedPages, setMountedPages] = useWorkbenchState({});
  var [editProject, setEditProject] = useWorkbenchState(null);
  var [editMemoryProject, setEditMemoryProject] = useWorkbenchState(null);
  var [notifications, setNotifications] = useWorkbenchState({ items: [], counts: { all: 0, mention: 0, comment: 0, system: 0 }, unreadByTab: { all: 0, mention: 0, comment: 0, system: 0 }, unreadCount: 0 });
  var [activeChatId, setActiveChatId] = useWorkbenchState("");
  var [recentChatsByProject, setRecentChatsByProject] = useWorkbenchState({});
  var [pinnedResources, setPinnedResources] = useWorkbenchState([]);
  var chatModule = window.CyreneUI.require("chat");
  var chatRuntimeEngine = chatModule && chatModule.Runtimes;
  var [chatRuntimes, setChatRuntimes] = useWorkbenchState(function () {
    return chatRuntimeEngine && chatRuntimeEngine.snapshot ? chatRuntimeEngine.snapshot() : {};
  });
  var [sessionActivityLive, setSessionActivityLive] = useWorkbenchState({});
  var [recentOpenedSessionKeys, setRecentOpenedSessionKeys] = useWorkbenchState(function () {
    try {
      var stored = JSON.parse(localStorage.getItem("wb-recent-opened-sessions") || "[]");
      return Array.isArray(stored) ? stored.filter(function (key) {
        return /^(task|chat):.+/.test(String(key || ""));
      }).slice(0, 20) : [];
    } catch (e) {
      return [];
    }
  });
  var [pinnedSessionKeys, setPinnedSessionKeys] = useWorkbenchState(function () {
    try {
      var stored = JSON.parse(localStorage.getItem("wb-pinned-sessions") || "[]");
      return Array.isArray(stored) ? stored.filter(function (key) {
        return /^(task|chat):.+/.test(String(key || ""));
      }).slice(0, 20) : [];
    } catch (e) {
      return [];
    }
  });
  var [hiddenSessionKeys, setHiddenSessionKeys] = useWorkbenchState(function () {
    try {
      var stored = JSON.parse(localStorage.getItem("wb-hidden-session-tabs") || "[]");
      return Array.isArray(stored) ? stored.filter(function (key) {
        return /^(task|chat):.+/.test(String(key || ""));
      }).slice(0, 100) : [];
    } catch (e) {
      return [];
    }
  });
  // Always-fresh snapshot of what the user is looking at, read inside async
  // notification callbacks (interval / SSE closures captured once on mount).
  var activeViewRef = useWorkbenchRef({ page: null, taskView: "board", chatId: "", sessionId: "" });
  var sessionLoadSeqRef = useWorkbenchRef(0);
  var launchReadyRef = useWorkbenchRef(false);
  var menuActionsRef = useWorkbenchRef({ createProject: function () {}, createSession: function () {}, createChat: function () {}, onToggleTheme: function () {} });

  useWorkbenchEffect(function () {
    if (!chatRuntimeEngine || typeof chatRuntimeEngine.subscribe !== "function") return undefined;
    setChatRuntimes(chatRuntimeEngine.snapshot());
    var subscribe = typeof chatRuntimeEngine.subscribeSummary === "function"
      ? chatRuntimeEngine.subscribeSummary
      : chatRuntimeEngine.subscribe;
    return subscribe(function (snapshot) { setChatRuntimes(snapshot); });
  }, [chatRuntimeEngine]);

  useWorkbenchEffect(function () {
    function onActivityEvent(data) {
      if (!data) return;
      var sessionId = String(data.session_id || data.chatId || data.chat_id || "").trim();
      if (!sessionId) return;
      var type = String(data.type || "");
      if (["tool_call", "tool_call_started", "tool_call_progress", "tool_call_finished", "llm_call", "phase_transition", "subagent_update", "goal_loop_update", "session_update", "error", "interrupted", "awaiting_user"].indexOf(type) < 0) return;
      setSessionActivityLive(function (previous) {
        var prior = previous[sessionId] || { agents: {} };
        // Tool failure is local to this call; the reducer keeps lifecycle and
        // parallel presence separate instead of promoting it to Session failure.
        var next = wbReduceSessionActivity(prior, data);
        return Object.assign({}, previous, { [sessionId]: next });
      });
    }
    function onChatLifecycle(event) {
      var detail = event && event.detail || {};
      var status = String(detail.status || "");
      var sessionId = String(detail.chatId || detail.sessionId || "");
      if (!sessionId || !status || status === "refresh") return;
      onActivityEvent({
        type: "session_update",
        session_id: sessionId,
        runId: String(detail.runId || ""),
        status: status,
        timestamp: String(detail.timestamp || new Date().toISOString()),
      });
    }
    var unsubscribe = window.CyreneUI.require("events").subscribe(onActivityEvent);
    window.addEventListener("cyrene:wbc-chat-lifecycle", onChatLifecycle);
    return function () {
      unsubscribe();
      window.removeEventListener("cyrene:wbc-chat-lifecycle", onChatLifecycle);
    };
  }, []);

  function projectForSession(snapshot, sessionId) {
    if (!snapshot || !sessionId) return null;
    var projects = Array.isArray(snapshot.projects) ? snapshot.projects : [];
    for (var i = 0; i < projects.length; i++) {
      var sessions = Array.isArray(projects[i].sessions) ? projects[i].sessions : [];
      if (sessions.some(function (item) { return item && String(item.id || "") === String(sessionId); })) {
        return projects[i];
      }
    }
    return null;
  }

  // Merge a task response without allowing a late response from an old task
  // to change the project the user is currently viewing. The response still
  // updates the project/session lists, so background work is not lost.
  function mergeTaskResponse(prev, nextStore, sourceSessionId) {
    if (!nextStore || typeof nextStore !== "object") return prev;
    var merged = Object.assign({}, prev);
    if (Array.isArray(nextStore.projects)) merged.projects = nextStore.projects;

    var sourceId = String(sourceSessionId || "");
    if (sourceId && String(prev.activeSessionId || "") !== sourceId) return merged;

    var responseSession = null;
    var responseProject = projectForSession(nextStore, sourceId);
    if (responseProject) {
      responseSession = (responseProject.sessions || []).find(function (item) {
        return item && String(item.id || "") === sourceId;
      }) || null;
    }
    if (!responseSession && nextStore.activeSession && (!sourceId || String(nextStore.activeSession.id || "") === sourceId)) {
      responseSession = nextStore.activeSession;
    }
    if (!responseProject && nextStore.activeProject && (!sourceId || !responseSession || String(nextStore.activeProject.id || "") === String(responseSession.projectId || ""))) {
      responseProject = nextStore.activeProject;
    }
    if (responseProject && String(responseProject.id || "") === String(prev.activeProjectId || "")) {
      merged.activeProject = responseProject;
      merged.activeProjectId = responseProject.id;
    }
    if (responseSession && String(responseSession.id || "") === String(prev.activeSessionId || "")) {
      merged.activeSession = responseSession;
      merged.activeSessionId = responseSession.id;
    }
    return merged;
  }

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

  function reloadRecentChats(projects) {
    var projectList = Array.isArray(projects) ? projects : [];
    if (!projectList.length) {
      setRecentChatsByProject({});
      return Promise.resolve({});
    }
    var api = window.CyreneUI.require("api");
    return Promise.all(projectList.map(function (project) {
      var projectId = String((project && project.id) || "");
      if (!projectId) return Promise.resolve({ projectId: "", chats: [] });
      return api.json("/api/workbench/chats?project=" + encodeURIComponent(projectId), { toast: false })
        .then(function (payload) {
          return {
            projectId: projectId,
            chats: payload && Array.isArray(payload.chats) ? payload.chats : [],
          };
        })
        .catch(function () {
          return { projectId: projectId, chats: [] };
        });
    })).then(function (results) {
      var next = {};
      results.forEach(function (result) {
        if (result.projectId) next[result.projectId] = result.chats;
      });
      setRecentChatsByProject(next);
      return next;
    });
  }

  function reloadPinnedResources() {
    return window.CyreneUI.require("api").json("/api/workbench/pinned-resources", { toast: false })
      .then(function (payload) {
        var resources = payload && Array.isArray(payload.resources) ? payload.resources : [];
        setPinnedResources(resources);
        return resources;
      })
      .catch(function () { return []; });
  }

  function pinTopbarResource(resource) {
    if (!resource || ["file", "browser", "snippet"].indexOf(resource.kind) < 0) return Promise.resolve(null);
    var enriched = Object.assign({}, resource);
    if (!enriched.ownerProjectId && enriched.ownerSessionId) {
      var owner = recentSessionTabs.find(function (item) {
        return item.kind === "chat" && String(item.id || "") === String(enriched.ownerSessionId || "");
      });
      if (owner) enriched.ownerProjectId = owner.projectId;
    }
    return window.CyreneUI.require("api").json("/api/workbench/pinned-resources", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(enriched),
      toast: false,
    }).then(function (payload) {
      var item = payload && payload.resource;
      if (item) {
        setPinnedResources(function (prev) {
          return [item].concat((prev || []).filter(function (entry) { return entry.id !== item.id; }));
        });
        window.CyreneUI.require("feedback").showToast(
          t("workbench.resourceShelf.pinned", "Pinned to topbar"),
          "success"
        );
      }
      return item;
    }).catch(function (err) {
      window.CyreneUI.require("feedback").showToast(err.message || String(err), "error");
      return null;
    });
  }

  function unpinTopbarResource(resource) {
    if (!resource || !resource.id) return Promise.resolve(false);
    return window.CyreneUI.require("api").fetch(
      "/api/workbench/pinned-resources/" + encodeURIComponent(resource.id),
      { method: "DELETE", toast: false }
    ).then(function (response) {
      if (!response.ok) throw new Error("HTTP " + response.status);
      setPinnedResources(function (prev) {
        return (prev || []).filter(function (item) { return item.id !== resource.id; });
      });
      return true;
    }).catch(function (err) {
      window.CyreneUI.require("feedback").showToast(err.message || String(err), "error");
      return false;
    });
  }

  useWorkbenchEffect(function () {
    reloadPinnedResources();
    function pinFromDrag(event) {
      if (event && event.detail) pinTopbarResource(event.detail);
    }
    window.addEventListener("cyrene:pin-topbar-resource", pinFromDrag);
    return function () {
      window.removeEventListener("cyrene:pin-topbar-resource", pinFromDrag);
    };
  }, []);

  function rememberOpenedSession(kind, sessionId) {
    var normalizedKind = kind === "chat" ? "chat" : "task";
    var normalizedId = String(sessionId || "");
    if (!normalizedId) return;
    var key = normalizedKind + ":" + normalizedId;
    setRecentOpenedSessionKeys(function (prev) {
      var visibleKeys = wbRecentSessionTabs(
        store.projects,
        recentChatsByProject,
        prev,
        pinnedSessionKeys,
        hiddenSessionKeys,
        3
      ).map(function (item) {
        return item.kind + ":" + item.id;
      });
      var next = wbRememberOpenedSessionKey(prev, visibleKeys, key, 20);
      if (next === prev) return prev;
      try {
        localStorage.setItem("wb-recent-opened-sessions", JSON.stringify(next));
      } catch (e) {}
      return next;
    });
    setHiddenSessionKeys(function (prev) {
      if (!Array.isArray(prev) || prev.indexOf(key) < 0) return prev;
      var next = prev.filter(function (item) { return item !== key; });
      try {
        localStorage.setItem("wb-hidden-session-tabs", JSON.stringify(next));
      } catch (e) {}
      return next;
    });
  }

  function togglePinnedSession(item, forcePinned) {
    if (!item || !item.id) return;
    var key = item.kind + ":" + item.id;
    var shouldPin = typeof forcePinned === "boolean"
      ? forcePinned
      : pinnedSessionKeys.indexOf(key) < 0;
    setPinnedSessionKeys(function (prev) {
      var list = Array.isArray(prev) ? prev : [];
      var next = shouldPin
        ? [key].concat(list.filter(function (entry) { return entry !== key; })).slice(0, 20)
        : list.filter(function (entry) { return entry !== key; });
      try {
        localStorage.setItem("wb-pinned-sessions", JSON.stringify(next));
      } catch (e) {}
      return next;
    });
    if (shouldPin) {
      setHiddenSessionKeys(function (prev) {
        if (!Array.isArray(prev) || prev.indexOf(key) < 0) return prev;
        var next = prev.filter(function (entry) { return entry !== key; });
        try {
          localStorage.setItem("wb-hidden-session-tabs", JSON.stringify(next));
        } catch (e) {}
        return next;
      });
    }
  }

  function removeSessionTab(item) {
    if (!item || !item.id) return;
    var key = item.kind + ":" + item.id;
    setPinnedSessionKeys(function (prev) {
      var next = (Array.isArray(prev) ? prev : []).filter(function (entry) { return entry !== key; });
      try {
        localStorage.setItem("wb-pinned-sessions", JSON.stringify(next));
      } catch (e) {}
      return next;
    });
    setRecentOpenedSessionKeys(function (prev) {
      var next = (Array.isArray(prev) ? prev : []).filter(function (entry) { return entry !== key; });
      try {
        localStorage.setItem("wb-recent-opened-sessions", JSON.stringify(next));
      } catch (e) {}
      return next;
    });
    setHiddenSessionKeys(function (prev) {
      var next = [key].concat((Array.isArray(prev) ? prev : []).filter(function (entry) {
        return entry !== key;
      })).slice(0, 100);
      try {
        localStorage.setItem("wb-hidden-session-tabs", JSON.stringify(next));
      } catch (e) {}
      return next;
    });
  }

  function loadSessionTabBrowserPreview(item) {
    if (!item || !item.id) return Promise.resolve(null);
    var bridge = window.cyrene && window.cyrene.browser;
    var browserStatePromise = item.kind === "chat" && bridge && typeof bridge.getState === "function"
      ? bridge.getState(item.id).catch(function () { return null; })
      : Promise.resolve(null);
    return browserStatePromise.then(function (browserState) {
      var hasBrowser = !!(
        browserState
        && Array.isArray(browserState.tabs)
        && browserState.tabs.length
      );
      if (!hasBrowser) return null;
      var activeTab = browserState.tabs.find(function (tab) {
        return String(tab && tab.id || "") === String(browserState.activeTabId || "");
      }) || browserState.tabs[0] || {};
      var fallback = {
        title: String(activeTab.title || ""),
        url: String(activeTab.url || ""),
        previewUrl: "",
      };
      if (!bridge || typeof bridge.screenshot !== "function") return fallback;
      return bridge.screenshot({
        sessionId: item.id,
        tabId: browserState.activeTabId || activeTab.id || "",
      }).then(function (shot) {
        if (!shot || !shot.ok || !shot.pngBase64) return fallback;
        return {
          title: String(shot.title || fallback.title),
          url: String(shot.url || fallback.url),
          previewUrl: "data:image/png;base64," + shot.pngBase64,
        };
      }).catch(function () { return fallback; });
    });
  }

  function loadSessionTabResources(item) {
    if (!item || !item.id) return Promise.resolve({ browser: false, files: [] });
    var browserPreviewPromise = loadSessionTabBrowserPreview(item);
    var filesPromise = item.kind === "chat"
      ? window.CyreneUI.require("api").json(
        "/api/workbench/chats/" + encodeURIComponent(item.id),
        { toast: false }
      ).then(function (payload) {
        var files = [];
        var seen = {};
        var messages = payload && payload.chat && Array.isArray(payload.chat.messages)
          ? payload.chat.messages
          : [];
        messages.forEach(function (message) {
          (Array.isArray(message && message.attachments) ? message.attachments : []).forEach(function (file) {
            if (!file) return;
            var key = String(file.id || file.url || file.name || "");
            if (!key || seen[key]) return;
            seen[key] = true;
            files.push(file);
          });
        });
        return files;
      }).catch(function () { return []; })
      : Promise.resolve([]);
    return Promise.all([browserPreviewPromise, filesPromise]).then(function (results) {
      return {
        browser: results[0],
        files: results[1],
      };
    });
  }

  function openSessionTabResource(item, resource) {
    if (!item || !resource) return;
    rememberOpenedSession(item.kind, item.id);
    var payload = item.kind === "chat"
      ? { type: "chat", projectId: item.projectId, chatId: item.id }
      : { type: "task", projectId: item.projectId, sessionId: item.id };
    payload.topbarResource = resource;
    navigateFromSearch(payload);
  }

  function reloadWorkbench(nextProjectId, nextSessionId, options) {
    options = options || {};
    var showLoading = options.showLoading !== false;
    if (showLoading) setLoading(true);
    setError("");
    var contentReady = autoWelcomePendingRef.current
      ? Promise.resolve(dataStore.ready).catch(function () {})
      : Promise.resolve();
    return Promise.all([model.fetchProjects(), contentReady])
      .then(function (results) {
        var next = results[0];
        if (autoWelcomePendingRef.current) {
          autoWelcomePendingRef.current = false;
          var onboardingState = dataStore.state.onboarding || {};
          var hasUserContent = !!onboardingState.hasExistingData
            || wbProjectStoreHasUserContent(next);
          if (hasUserContent) {
            wbRememberWelcomeHandled();
          } else {
            setFullPage(function (current) { return current == null ? "welcome" : current; });
          }
        }
        setStore(function (prev) {
          // Prefer an explicit target, then the already-visible UI selection.
          // This prevents the initial request from winning a race against a
          // click made while the project list is still loading.
          var projectId = nextProjectId || (prev && prev.activeProjectId) || next.activeProjectId;
          var sessionId = nextSessionId || (prev && prev.activeSessionId) || next.activeSessionId;
          var project = (next.projects || []).find(function (item) { return item.id === projectId; }) || next.activeProject;
          if (!project) return next;
          var session = (project.sessions || []).find(function (item) { return item.id === sessionId; }) || project.sessions[0] || null;
          return Object.assign({}, next, {
            activeProjectId: project.id,
            activeProject: project,
            activeSessionId: session ? session.id : "",
            activeSession: session,
          });
        });
        return next;
      })
      .catch(function (err) {
        setError(wbErrorText(err));
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
        }) || (prev && prev.activeProject) || next.activeProject;
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
    var updatedProject = nextProjects.find(function (project) { return String(project.id || "") === projectId; }) || null;
    var shouldActivate = String(prev.activeSessionId || "") === String(fullSession.id || "");
    // The fetched session may belong to an old project after the user has
    // switched projects. Keep that data in the list, but preserve the visible
    // project/session unless this response is for the current session.
    var activeProject = nextProjects.find(function (project) {
      return String(project.id || "") === String(prev.activeProjectId || "");
    }) || (shouldActivate ? updatedProject : prev.activeProject);
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
        if (seq === sessionLoadSeqRef.current) setError(wbErrorText(err));
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
    wbRememberWelcomeHandled();
  }, [fullPage]);

  useWorkbenchEffect(function () {
    reloadWorkbench();
    reloadNotifications();
  }, []);

  var recentProjectIds = (store.projects || []).map(function (project) {
    return String((project && project.id) || "");
  }).filter(Boolean).sort().join("|");
  useWorkbenchEffect(function () {
    reloadRecentChats(store.projects || []);
  }, [recentProjectIds]);
  useWorkbenchEffect(function () {
    function onChatsChanged() {
      reloadRecentChats(store.projects || []);
    }
    window.addEventListener("cyrene:wbc-refresh-chats", onChatsChanged);
    return function () {
      window.removeEventListener("cyrene:wbc-refresh-chats", onChatsChanged);
    };
  }, [recentProjectIds]);

  // The topbar stays visible outside the task board, so its persisted fallback
  // state must refresh globally as background sessions settle or pause. Live
  // deltas render immediately from sessionActivityLive; this trailing summary
  // pull repairs terminal state and supports runs started in another window.
  useWorkbenchEffect(function () {
    var timer = null;
    function refreshTopbarSessions() {
      if (timer) clearTimeout(timer);
      timer = setTimeout(function () {
        timer = null;
        refreshTaskBoard();
        reloadRecentChats(store.projects || []);
      }, 420);
    }
    function onRuntimeEvent(data) {
      if (!data) return;
      if (["goal_loop_update", "session_update", "user_question", "user_question_answered"].indexOf(data.type) >= 0) {
        refreshTopbarSessions();
      }
    }
    function onChatLifecycle() {
      refreshTopbarSessions();
    }
    var unsubscribe = window.CyreneUI.require("events").subscribe(onRuntimeEvent);
    window.addEventListener("cyrene:wbc-chat-lifecycle", onChatLifecycle);
    return function () {
      if (timer) clearTimeout(timer);
      unsubscribe();
      window.removeEventListener("cyrene:wbc-chat-lifecycle", onChatLifecycle);
    };
  }, [recentProjectIds]);

  // Keep the static launch screen above the renderer until the workbench's
  // initial project payload and the shared UI bootstrap have both settled.
  useWorkbenchEffect(function () {
    if (loading || launchReadyRef.current) return undefined;
    launchReadyRef.current = true;
    Promise.resolve(dataStore.ready)
      .catch(function () {})
      .then(function () {
        window.CyreneUI.require("readiness").markReady();
      });
    return undefined;
  }, [loading]);

  // A reload hides the native view from beforeunload so it cannot outlive the
  // renderer. The main-process manager survives that reload, so explicitly
  // publish the new renderer's empty overlay state before individual overlay
  // effects add themselves below. Without this reset the view stays obscured
  // indefinitely and only the drag-time screenshot proxy remains visible.
  useWorkbenchEffect(function () {
    wbBrowserOverlayCount = 0;
    wbSetBrowserOverlayObscured(0);
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
    menuActionsRef.current = { createProject: createProject, createSession: createSession, createChat: createChat, onToggleTheme: onToggleTheme };
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
        "new-chat":       function () { acts.createChat(); },
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
    return window.CyreneUI.require("events").subscribe(handleEvent);
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

  useWorkbenchEffect(function () {
    if (fullPage === "chat" && activeChatId) {
      rememberOpenedSession("chat", activeChatId);
    } else if (!fullPage && taskView === "detail" && store && store.activeSessionId) {
      rememberOpenedSession("task", store.activeSessionId);
    }
  }, [fullPage, taskView, activeChatId, store && store.activeSessionId]);

  // Global keyboard shortcuts (search, new chat/task, command palette,
  // switch project, toggle sidebar, settings). Bindings come from the
  // platform-aware WorkbenchShortcuts module so ⌘ on mac / Ctrl elsewhere is
  // handled automatically and user customizations in Settings → Shortcuts are
  // honoured. Composer Enter-to-send is handled locally in each composer's
  // onKeyDown; these are the shell-level ones.
  useWorkbenchEffect(function () {
    function onKey(event) {
      var sc = window.CyreneUI.require("shortcuts");
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
        createChat();
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
      if (sc.matches(event, "voice-command")) {
        event.preventDefault();
        WbVoiceCommand.start();
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
    var unsubscribe = window.CyreneUI.require("events").subscribe(onRuntimeEvent);
    return function () {
      if (timer) clearInterval(timer);
      if (trailing) clearTimeout(trailing);
      document.removeEventListener("visibilitychange", onVisibility);
      window.removeEventListener("focus", tick);
      unsubscribe();
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
    var unsubscribe = window.CyreneUI.require("events").subscribe(handleRuntimeEvent);
    return function () {
      unsubscribe();
      if (goalLoopReloadTimer) clearTimeout(goalLoopReloadTimer);
    };
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
    // A project switch changes the task collection, so return its hidden task
    // surface to the board as well. This avoids reopening a summary-only first
    // task when the user later leaves the current module.
    setTaskView("board");
    // The project summary already contains everything the task board needs.
    // Fetching the first task's full detail here made every project switch pay
    // for an invisible task while the user was on memory/schedule/knowledge.
    // Task detail remains lazy-loaded by selectSession when it is actually opened.
    window.CyreneUI.require("model").setActiveProject(project.id, nextSessionId).catch(function () {});
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
    window.CyreneUI.require("model").setActiveProject(project.id, sessionId).catch(function () {});
  }

  // Global search navigation: select the right project/session/page and tell
  // the target module page which item to highlight/open.
  function navigateFromSearch(payload) {
    if (!payload || !payload.type) return;
    var type = payload.type;
    if (type === "conversation") {
      type = "chat";
      payload = {
        ...payload,
        type: "chat",
        chatId: payload.chatId || payload.sessionId || payload.id,
      };
    }
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
        window.CyreneUI.require("model").setActiveProject(project.id, session.id).catch(function () {});
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

    var navigation = window.CyreneUI.require("navigation");
    navigation.setPending(payload);
    try {
      window.dispatchEvent(new CustomEvent("cyrene:workbench-navigate", { detail: payload }));
    } catch (e) {}
    // If no module consumes the pending selection within a few seconds, clear
    // it so it does not leak into unrelated navigation later.
    setTimeout(function () {
      navigation.clearPending(payload);
    }, 5000);
  }

  function navigateFromNotification(item) {
    var meta = item && item.meta && typeof item.meta === "object" ? item.meta : {};
    if (meta.category === "app_update" || String(item && item.source || "") === "updater") {
      setSettingsTab("about");
      setSettingsOpen(true);
      return true;
    }
    var target = wbNotificationNavigationTarget(item);
    if (!target) return false;
    navigateFromSearch(target);
    return true;
  }

  useWorkbenchEffect(function () {
    return window.CyreneUI.require("navigation").setHandler(navigateFromSearch);
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
  function createChat() {
    setFullPage("chat");
    setNewChatRequestId(function (value) { return value + 1; });
  }

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
    window.CyreneUI.require("feedback").confirmModal({
      body: t("task.confirmDelete", { name: session.title || t("task.thisTask") }),
      confirmLabel: t("common.delete"),
      danger: true,
    }).then(function (ok) {
      if (!ok) return;
      model.deleteSession(session.id).then(function (next) {
        setStore(next);
        setExpandedStepId("");
      }).catch(function (err) {
        setError(wbErrorText(err));
      });
    });
  }

  function handleDeleteProject(project) {
    if (!project) return Promise.resolve();
    if (project.dataKey === "default") {
      setError(wbT("project.cannotDeleteDefault", "The default project cannot be deleted."));
      return Promise.resolve();
    }
    return window.CyreneUI.require("feedback").confirmModal({
      body: wbT("project.confirmDelete", "Delete project \"{name}\"? Data inside the project will also be deleted.", { name: project.name }),
      confirmLabel: wbT("common.delete", "Delete"),
      danger: true,
    }).then(function (ok) {
      if (!ok) return undefined;
      window.dispatchEvent(new Event("cyrene:voice-stop"));
      return model.deleteProject(project.id).then(function (next) {
        setStore(next);
        setFullPage(null);
        setTaskView("board");
        setExpandedStepId("");
        return next;
      }).catch(function (err) {
        setError(wbErrorText(err));
      });
    });
  }

  function handleRunCreated(next, sourceSessionId) {
    setStore(function (prev) {
      return mergeTaskResponse(prev, next, sourceSessionId);
    });
    var visibleSessionId = activeViewRef.current && activeViewRef.current.sessionId;
    if (!sourceSessionId || String(sourceSessionId) === String(visibleSessionId || "")) {
      var currentSession = next && next.activeSession && String(next.activeSession.id || "") === String(sourceSessionId || visibleSessionId || "")
        ? next.activeSession : null;
      setExpandedStepId(currentSession && currentSession.plan && currentSession.plan[0] ? currentSession.plan[0].id : "");
      setRightTab("context");
      setTaskView("detail");
    }
  }

  function handleOpenPage(page) {
    /* The active Dock item is a location indicator, not a toggle. Re-clicking
       it must preserve the current page (and a task detail, when applicable)
       instead of falling through to the default Task surface. */
    if (page === "task") {
      if (!fullPage) return;
      setTaskView("board");
      setFullPage(null);
      return;
    }
    if (fullPage === page) return;
    setFullPage(page);
  }

  function handleSidebarModuleWheel(event) {
    var target = event.target;
    if (!target || !target.closest || !target.closest(".workbench-integrated-rail, .workbench-sidebar-dock.is-persistent")) return;
    var deltaX = Number(event.deltaX || 0);
    var deltaY = Number(event.deltaY || 0);
    if (Math.abs(deltaX) < 2 || Math.abs(deltaX) <= Math.abs(deltaY) * 1.15) return;

    event.preventDefault();
    var gesture = sidebarModuleWheelRef.current;
    var now = Date.now();
    var direction = deltaX < 0 ? -1 : 1;
    if (gesture.direction && gesture.direction !== direction) gesture.delta = 0;
    gesture.direction = direction;
    if (now < gesture.lockedUntil) return;
    gesture.delta += deltaX;
    if (Math.abs(gesture.delta) < 44) return;

    var moduleOrder = ["schedule", "task", "chat", "knowledge", "memory"];
    var activeModule = moduleOrder.indexOf(fullPage) >= 0 ? fullPage : "task";
    var activeIndex = moduleOrder.indexOf(activeModule);
    var nextIndex = (activeIndex + direction + moduleOrder.length) % moduleOrder.length;
    handleOpenPage(moduleOrder[nextIndex]);
    gesture.lockedUntil = now + 420;
    gesture.delta = 0;
  }

  function toggleWorkspaceSidebar() {
    setRailCollapsed(function (value) {
      var next = !value;
      try { localStorage.setItem("wb-rail-collapsed", next ? "1" : "0"); } catch (e) {}
      return next;
    });
  }

  useWorkbenchEffect(function () {
    if (!window.CyreneUI.has("uiSurface")) return undefined;
    var uiSurface = window.CyreneUI.require("uiSurface");
    uiSurface.setScope(settingsOpen ? "settings" : "main");
    var unregister = [];
    var modules = [
      ["task", t("rail.tasks", "Tasks")],
      ["chat", t("rail.chat", "Chat")],
      ["schedule", t("rail.schedule", "Schedule")],
      ["knowledge", t("rail.knowledge", "Knowledge")],
      ["memory", t("rail.memory", "Memory")],
      ["profile", t("rail.profile", "Profile")],
    ];
    modules.forEach(function (item) {
      var page = item[0];
      unregister.push(uiSurface.register({
        node_id: "navigation_" + page,
        parent_id: "root",
        scope: "main",
        get_node: function () {
          return {
            role: "navigation_item",
            name: item[1],
            state: { selected: page === "task" ? !fullPage : fullPage === page },
          };
        },
        actions: [{
          action_id: "open", kind: "invoke", risk: "R1", gesture_aliases: ["press", "keyboard"],
          outcome: { effect: "opens_surface", target_scope: page, inspect_after: true },
        }],
        handlers: { open: function () { handleOpenPage(page); } },
      }));
    });
    unregister.push(uiSurface.register({
      node_id: "workspace_sidebar",
      parent_id: "root",
      scope: "main",
      get_node: function () { return { role: "complementary", name: t("rail.sidebar", "Workspace sidebar"), state: { collapsed: railCollapsed } }; },
      actions: [{ action_id: "toggle", kind: "toggle", risk: "R1", gesture_aliases: ["press"] }],
      handlers: { toggle: toggleWorkspaceSidebar },
    }));
    unregister.push(uiSurface.register({
      node_id: "open_search",
      parent_id: "root",
      scope: "main",
      order: 20,
      get_node: function () { return { role: "button", name: t("topbar.search", "Search") }; },
      actions: [{
        action_id: "open", kind: "invoke", risk: "R1", gesture_aliases: ["press", "keyboard"],
        outcome: { effect: "opens_current_overlay", target_role: "dialog", inspect_after: true },
      }],
      handlers: { open: function () { setSearchOpen(true); } },
    }));
    unregister.push(uiSurface.register({
      node_id: "open_settings",
      parent_id: "root",
      scope: "main",
      order: 25,
      get_node: function () { return { role: "button", name: t("settings.title", "Settings") }; },
      actions: [{
        action_id: "open", kind: "invoke", risk: "R1", gesture_aliases: ["press", "keyboard"],
        outcome: { effect: "opens_overlay", target_node_id: "settings_dialog", target_scope: "settings", inspect_after: true },
      }],
      handlers: { open: function () { setSettingsTab(""); setSettingsOpen(true); } },
    }));
    if (settingsOpen) {
      unregister.push(uiSurface.register({
        node_id: "settings_dialog",
        parent_id: "root",
        scope: "settings",
        get_node: function () { return { role: "dialog", name: t("settings.title", "Settings"), state: { tab: settingsTab || "general" } }; },
        actions: [{ action_id: "dismiss", kind: "dismiss", risk: "R1", gesture_aliases: ["escape_key", "close_button"] }],
        handlers: { dismiss: function () { setSettingsOpen(false); } },
      }));
    }
    return function () { unregister.forEach(function (remove) { remove(); }); };
  }, [fullPage, settingsOpen, settingsTab, railCollapsed, t]);

  function renderSidebarCollapseControl() {
    return <WorkbenchSidebarCollapseControl collapsed={railCollapsed} onToggle={toggleWorkspaceSidebar} />;
  }

  function renderSidebarDockSlot() {
    return <div className="workbench-sidebar-dock-slot" aria-hidden="true" />;
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

  // Knowledge / schedule / memory keep the shared ProjectRail. Chat owns a
  // unified project + conversation navigator so the two related scopes do not
  // occupy separate columns there; other pages take over the full screen.
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
  var activeSessionKey = isChat && activeChatId
    ? "chat:" + activeChatId
    : (!fullPage && taskView === "detail" && store.activeSessionId ? "task:" + store.activeSessionId : "");
  var sessionTabCandidates = wbRecentSessionTabs(
    store.projects,
    recentChatsByProject,
    recentOpenedSessionKeys,
    pinnedSessionKeys,
    hiddenSessionKeys,
    1000
  ).map(function (item) {
    var runtime = item.kind === "chat" ? chatRuntimes[item.id] : null;
    var browserByChat = dataState.browserByChat || {};
    var browserState = browserByChat[item.id] || (
      dataState.browser && String(dataState.browser.sessionId || dataState.browser.chatId || "") === item.id
        ? dataState.browser
        : null
    );
    return Object.assign({}, item, {
      activity: wbSessionActivitySnapshot(item, runtime, sessionActivityLive[item.id], browserState),
    });
  });
  var liveTopbarChatKey = sessionTabCandidates.filter(function (item) {
    return item.kind === "chat" && item.activity && item.activity.isLive;
  }).map(function (item) { return item.id; }).sort().join("|");
  // Event delivery is the primary path. Poll only while at least one chat is
  // visibly live so runs completed in another app window also settle quickly,
  // then stop automatically as soon as the durable terminal summary arrives.
  useWorkbenchEffect(function () {
    if (!liveTopbarChatKey) return undefined;
    var inFlight = false;
    function refreshLiveChats() {
      if (document.hidden || inFlight) return;
      inFlight = true;
      reloadRecentChats(store.projects || []).finally(function () { inFlight = false; });
    }
    var timer = setInterval(refreshLiveChats, 2500);
    function onVisibility() { if (!document.hidden) refreshLiveChats(); }
    document.addEventListener("visibilitychange", onVisibility);
    return function () {
      clearInterval(timer);
      document.removeEventListener("visibilitychange", onVisibility);
    };
  }, [liveTopbarChatKey, recentProjectIds]);
  var sessionTabLayout = wbVisibleSessionTabs(sessionTabCandidates, activeSessionKey, 3);
  var recentSessionTabs = sessionTabLayout.visible;
  var overflowSessionTabs = sessionTabLayout.overflow;

  // First-run onboarding (LLM + personality). Driven by the backend onboarding
  // state — the workbench's own setup flow, independent of the legacy wizard.
  // It takes over the whole shell (no rails) until both are configured; once the
  // backend reports needsOnboarding=false the shell falls through to normal.
  var onboarding = dataState.onboarding || {};
  var onboardingActive = onboarding.needsOnboarding != null ? !!onboarding.needsOnboarding : !!needsOnboarding;
  function handleOnboardingComplete() {
    wbRememberWelcomeHandled();
    setFullPage("chat");
  }
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
        {React.createElement(window.CyreneUI.require("welcome").Page || function () { return <div className="workbench-empty">{t("workbench.welcomeLoading")}</div>; }, {
          onboarding: onboarding,
          onComplete: handleOnboardingComplete,
        })}
        {React.createElement(window.CyreneUI.require("feedback").Host)}
      </div>
    );
  }

  return (
    <div className="workbench-shell" data-screen-label="Cyrene · workbench">
      <WorkbenchTopbar
        projects={store.projects}
        activeProject={store.activeProject}
        activePage={fullPage}
        taskView={taskView}
        activeTaskId={store.activeSessionId}
        activeChatId={activeChatId}
        recentSessions={recentSessionTabs}
        overflowSessions={overflowSessionTabs}
        pinnedResources={pinnedResources}
        keyboardEnabled={!searchOpen && !settingsOpen && !newProjectOpen && !newTaskOpen && !editProject && !editMemoryProject}
        onPinResource={pinTopbarResource}
        onUnpinResource={unpinTopbarResource}
        onOpenPinnedResource={function (resource) {
          if (!resource) return;
          if (resource.kind === "snippet") {
            var snippetTarget = activePage === "chat" && activeChatId
              ? activeChatId
              : resource.ownerSessionId;
            if (snippetTarget) wbDeliverResourceToChat(snippetTarget, resource);
            return;
          }
          if (!resource.ownerSessionId) return;
          var owner = recentSessionTabs.find(function (item) {
            return item.kind === "chat" && String(item.id || "") === String(resource.ownerSessionId || "");
          });
          if (!owner) return;
          openSessionTabResource(owner, resource.kind === "file"
            ? { type: "file", file: resource.file && Object.keys(resource.file).length ? resource.file : resource }
            : { type: "browser" });
        }}
        onTogglePinnedSession={togglePinnedSession}
        onRemoveSessionTab={removeSessionTab}
        onLoadSessionResources={loadSessionTabResources}
        onLoadSessionBrowserPreview={loadSessionTabBrowserPreview}
        onOpenSessionResource={openSessionTabResource}
        onOpenSession={function (item) {
          if (!item) return;
          rememberOpenedSession(item.kind, item.id);
          if (item.kind === "chat") {
            navigateFromSearch({ type: "chat", projectId: item.projectId, chatId: item.id });
          } else {
            navigateFromSearch({ type: "task", projectId: item.projectId, sessionId: item.id });
          }
        }}
        onPauseSession={function (item) {
          if (!item || item.kind !== "task") return Promise.resolve(null);
          return model.fetchSession(item.id).then(function (payload) {
            var session = payload && payload.session;
            if (!session) throw new Error(t("workbench.sessionActivity.missing", "Session is unavailable"));
            if (session.goalLoop && session.goalLoop.status === "running") {
              return model.pauseGoalLoop(item.id);
            }
            return model.interruptSession(item.id).then(function () {
              var now = new Date().toISOString();
              var plan = Array.isArray(session.plan) ? session.plan.map(function (step) {
                if (!step || step.status !== "running") return step;
                return Object.assign({}, step, {
                  status: "pending",
                  startedAt: null,
                  currentAction: t("workbench.sessionActivity.stoppedAction", "Stopped; ready to run again."),
                  updatedAt: now,
                });
              }) : session.plan;
              return model.patchSession(item.id, {
                status: "paused",
                plan: plan,
                agentReply: t("workbench.sessionActivity.pausedReply", "Execution was paused from the topbar."),
                events: model.withEvent(session, "Paused", t("workbench.sessionActivity.pausedEvent", "Paused from the topbar.")),
              });
            });
          }).then(function (next) {
            if (next && next.projects) setStore(function (prev) { return mergeTaskResponse(prev, next, item.id); });
            return next;
          }).catch(function (err) {
            window.CyreneUI.require("feedback").showToast(wbErrorText(err), "error");
            return null;
          });
        }}
        onStopSession={function (item) {
          if (!item || item.kind !== "chat" || !chatRuntimeEngine) return Promise.resolve(null);
          return chatRuntimeEngine.interrupt(item.id, chatModule.Model).catch(function (err) {
            window.CyreneUI.require("feedback").showToast(wbErrorText(err), "error");
            return null;
          });
        }}
        notifications={notifications}
        onReloadNotifications={reloadNotifications}
        onOpenNotification={navigateFromNotification}
        onSearch={function () { setSearchOpen(true); }}
        onSettings={function (tab) { setSettingsTab(typeof tab === "string" ? tab : ""); setSettingsOpen(true); }}
        onNewProject={createProject}
        onSelectProject={selectProject}
        onEditProject={setEditProject}
        onEditMemory={setEditMemoryProject}
        onDeleteProject={handleDeleteProject}
        onNewTask={createSession}
        onOpenPage={handleOpenPage}
        theme={theme}
        actualTheme={actualTheme}
        onToggleTheme={onToggleTheme}
      />
      {fullPageConfig ? (
        <WorkbenchFullPage config={fullPageConfig} onClose={function () { setFullPage(null); }} />
      ) : (
        <div ref={wbApplyStoredRightWidth} className={"workbench-grid integrated-sidebars" + (railCollapsed ? " rail-collapsed" : "") + (isKnowledge ? " is-knowledge" : "") + (isSchedule ? " is-schedule" : "") + (isMemory ? " is-memory" : "") + (isChat ? " is-chat" : "") + (isWelcome ? " is-welcome" : "") + (isProfile ? " is-profile" : "") + (!isModulePage ? (taskView === "board" ? " is-task-board" : " is-task-detail") : "")} onWheel={handleSidebarModuleWheel}>
          <WorkbenchSidebarDock
            persistent={true}
            collapsed={railCollapsed}
            activePage={fullPage}
            onOpenPage={handleOpenPage}
            onSettings={function () { setSettingsTab(""); setSettingsOpen(true); }}
          />
          {showChatPage && (
            <WorkbenchStableSurface active={isChat}>
              {React.createElement(window.CyreneUI.require("chat").Page || function () { return <div className="workbench-empty">{t("workbench.chatLoading")}</div>; }, {
                active: isChat,
                project: store.activeProject,
                newChatRequestId: newChatRequestId,
                onOpenTask: handleChatToTask,
                onActiveChatIdChange: setActiveChatId,
                onChatsChange: function (projectId, chats) {
                  setRecentChatsByProject(function (prev) {
                    if (prev[projectId] === chats) return prev;
                    return Object.assign({}, prev, { [projectId]: chats });
                  });
                },
                pinnedChatIds: pinnedSessionKeys.filter(function (key) {
                  return String(key || "").indexOf("chat:") === 0;
                }).map(function (key) {
                  return String(key).slice(5);
                }),
                onTogglePinnedChat: function (chat, pinned) {
                  if (!chat || !chat.id) return;
                  togglePinnedSession({ id: chat.id, kind: "chat" }, pinned);
                },
                navCollapsed: railCollapsed,
                onToggleNavCollapsed: toggleWorkspaceSidebar,
                collapseControl: isChat ? renderSidebarCollapseControl() : null,
                moduleDock: isChat ? renderSidebarDockSlot() : null,
              })}
            </WorkbenchStableSurface>
          )}
          {showKnowledgePage && (
            <WorkbenchStableSurface active={isKnowledge}>
              {React.createElement(window.CyreneUI.require("library").Page || function () { return <div className="workbench-empty">{t("workbench.knowledgeLoading")}</div>; }, {
                active: isKnowledge,
                project: store.activeProject,
                onBack: function () { setFullPage(null); },
                onNavigate: navigateFromSearch,
                sidebarCollapsed: railCollapsed,
                collapseControl: isKnowledge ? renderSidebarCollapseControl() : null,
                moduleDock: isKnowledge ? renderSidebarDockSlot() : null,
              })}
            </WorkbenchStableSurface>
          )}
          {showSchedulePage && (
            <WorkbenchStableSurface active={isSchedule}>
              {React.createElement(window.CyreneUI.require("schedule").Page || function () { return <div className="workbench-empty">{t("workbench.scheduleLoading")}</div>; }, { active: isSchedule, project: store.activeProject, onBack: function () { setFullPage(null); }, sidebarCollapsed: railCollapsed, collapseControl: isSchedule ? renderSidebarCollapseControl() : null, moduleDock: isSchedule ? renderSidebarDockSlot() : null })}
            </WorkbenchStableSurface>
          )}
          {showMemoryPage && (
            <WorkbenchStableSurface active={isMemory}>
              {React.createElement(window.CyreneUI.require("memory").Page || function () { return <div className="workbench-empty">{t("workbench.memoryLoading")}</div>; }, { active: isMemory, project: store.activeProject, onBack: function () { setFullPage(null); }, onEditProjectMemory: function () { if (store.activeProject) setEditMemoryProject(store.activeProject); }, sidebarCollapsed: railCollapsed, collapseControl: isMemory ? renderSidebarCollapseControl() : null, moduleDock: isMemory ? renderSidebarDockSlot() : null })}
            </WorkbenchStableSurface>
          )}
          {showWelcomePage && (
            <WorkbenchStableSurface active={isWelcome}>
              {React.createElement(window.CyreneUI.require("welcome").Page || function () { return <div className="workbench-empty">{t("workbench.welcomeLoading")}</div>; }, {
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
            </WorkbenchStableSurface>
          )}
          {showProfilePage && (
            <WorkbenchStableSurface active={isProfile}>
              <>
                <WorkbenchProfileRail collapsed={railCollapsed} collapseControl={isProfile ? renderSidebarCollapseControl() : null} moduleDock={isProfile ? renderSidebarDockSlot() : null} />
                {window.CyreneUI.require("profile").Page
                  ? React.createElement(window.CyreneUI.require("profile").Page, { active: isProfile })
                  : <div className="workbench-empty">…</div>}
              </>
            </WorkbenchStableSurface>
          )}
          <WorkbenchStableSurface active={!isModulePage}>
          <>
          <TaskRail
            project={store.activeProject}
            activeSessionId={store.activeSessionId}
            onSelectSession={selectSession}
            onCreateSession={createSession}
            onDeleteSession={handleDeleteSession}
            loading={loading}
            collapsed={railCollapsed}
            collapseControl={renderSidebarCollapseControl()}
            moduleDock={!isModulePage ? renderSidebarDockSlot() : null}
          />
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
            onCreateRun={function (next) { handleRunCreated(next, store.activeSessionId); }}
            onRightTab={setRightTab}
            onSelectSession={selectSession}
            onBackToBoard={function () { setTaskView("board"); }}
            onCreateSession={createSession}
            onInitPatch={patchActiveInit}
            onLocalPatch={patchActiveSessionLocal}
            onRefresh={function (nextStore) {
              setStore(function (prev) {
                return mergeTaskResponse(prev, nextStore, store.activeSessionId);
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
                return mergeTaskResponse(prev, nextStore, store.activeSessionId);
              });
            }}
          />
          </>
          )}
          </>
          </WorkbenchStableSurface>
        </div>
      )}
      {searchOpen && typeof ReactDOM !== "undefined" && ReactDOM.createPortal(React.createElement(
        window.CyreneUI.require("search").Overlay,
        {
          onClose: function () { setSearchOpen(false); },
          onOpenSession: function () {
            setSearchOpen(false);
            setFullPage("chat");
          },
        }
      ), document.body)}
      {settingsOpen && React.createElement(
        window.CyreneUI.require("settings").Overlay,
        {
          onClose: function () { setSettingsOpen(false); },
          initialTab: settingsTab,
          theme: theme,
          actualTheme: actualTheme,
          onToggleTheme: onToggleTheme,
          project: store.activeProject,
        }
      )}
      {newProjectOpen && window.CyreneUI.require("create").NewProjectModal && React.createElement(
        window.CyreneUI.require("create").NewProjectModal,
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
      {editMemoryProject && (
        <WorkbenchProjectMemoryModal
          project={editMemoryProject}
          onClose={function () { setEditMemoryProject(null); }}
        />
      )}
      {newTaskOpen && window.CyreneUI.require("create").NewTaskModal && React.createElement(
        window.CyreneUI.require("create").NewTaskModal,
        {
          onClose: function () { setNewTaskOpen(false); },
          onCreate: function (input) {
            return handleCreateSession(input).then(function () { setNewTaskOpen(false); });
          },
        }
      )}
      {React.createElement(window.CyreneUI.require("feedback").Host)}
    </div>
  );
}

function wbSessionStatusLabel(activity, t) {
  var state = activity || {};
  if (state.phase === "attention") {
    return {
      input: t("workbench.sessionStatus.needsInput", "Needs input"),
      approval: t("workbench.sessionStatus.needsApproval", "Needs approval"),
      review: t("workbench.sessionStatus.needsReview", "Needs review"),
      blocked: t("workbench.sessionStatus.blocked", "Blocked"),
    }[state.reason] || t("workbench.sessionStatus.needsAttention", "Needs attention");
  }
  return {
    idle: t("workbench.sessionStatus.idle", "Idle"),
    planning: state.isLive
      ? t("workbench.sessionStatus.planning", "Planning")
      : t("workbench.sessionStatus.planningStage", "Planning stage"),
    running: t("workbench.sessionStatus.running", "Running"),
    paused: t("workbench.sessionStatus.paused", "Paused"),
    cancelled: t("workbench.sessionStatus.cancelled", "Stopped"),
    completed: t("workbench.sessionStatus.completed", "Completed"),
    failed: t("workbench.sessionStatus.failed", "Failed"),
  }[state.phase] || t("workbench.sessionStatus.idle", "Idle");
}

function wbSessionActivityCopy(activity, t) {
  var state = activity || {};
  if (state.phase === "attention" || state.phase === "failed" || state.phase === "paused" || state.phase === "cancelled" || state.phase === "completed") {
    return wbSessionStatusLabel(state, t);
  }
  if (state.phase === "planning") return wbSessionStatusLabel(state, t);
  if (state.phase === "running") {
    if (state.activity && state.activity.kind === "browser" && state.activity.label) return state.activity.label;
    if (state.progress && state.progress.current && state.progress.total) {
      return t("workbench.sessionStatus.step", {
        current: state.progress.current,
        total: state.progress.total,
      }, "Step {current}/{total}");
    }
    if (state.activity && state.activity.label) return state.activity.label;
  }
  return "";
}

function WorkbenchSessionStatusIcon({ phase, active }) {
  var state = String(phase || "idle");
  if (state === "attention") {
    return <svg className="workbench-session-status-svg" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round"><path d="M8 2 14 13H2Z"/><path d="M8 5.5v3.4M8 11.3h.01"/></svg>;
  }
  if (state === "completed") {
    return <svg className="workbench-session-status-svg" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"><path d="m3.2 8.2 3 3L12.8 4.8"/></svg>;
  }
  if (state === "failed") {
    return <svg className="workbench-session-status-svg" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round"><circle cx="8" cy="8" r="5.6"/><path d="m6 6 4 4m0-4-4 4"/></svg>;
  }
  if (state === "cancelled") {
    return <svg className="workbench-session-status-svg" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round"><circle cx="8" cy="8" r="5.6"/><path d="M5.7 8h4.6"/></svg>;
  }
  if (state === "paused") {
    return <svg className="workbench-session-status-svg" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round"><path d="M5.7 4.5v7M10.3 4.5v7"/></svg>;
  }
  return <span className={"workbench-session-status-dot " + state + (active ? " is-live" : "")} />;
}

function WorkbenchSessionActivityPreview({ preview, t }) {
  if (!preview) return null;
  var item = preview.item;
  var activity = preview.activity || {};
  var progress = activity.progress || {};
  var activeAgents = (activity.agents || []).filter(function (agent) {
    return ["running", "resumed", "waiting"].indexOf(String(agent.status || "")) >= 0;
  });
  var percent = progress.total ? Math.max(0, Math.min(100, Math.round((progress.completed / progress.total) * 100))) : 0;
  return (
    <div
      id="workbench-session-activity-preview"
      className="workbench-session-activity-preview"
      role="tooltip"
      style={{ left: preview.left, top: preview.top, ...preview.portalTheme }}
    >
      <div className="workbench-session-activity-preview-head">
        <WorkbenchSessionStatusIcon phase={activity.phase} active={activity.isLive} />
        <div><b>{item.title}</b><small>{wbSessionStatusLabel(activity, t)}</small></div>
      </div>
      {progress.total ? (
        <div className="workbench-session-activity-progress">
          <div><span>{t("workbench.sessionStatus.progress", "Progress")}</span><b>{progress.current || progress.completed}/{progress.total}</b></div>
          <span className="workbench-session-activity-progress-track"><i style={{ width: percent + "%" }} /></span>
          {progress.title || progress.action ? <p>{progress.action || progress.title}</p> : null}
        </div>
      ) : null}
      {activity.activity && (activity.activity.label || activity.activity.detail) ? (
        <div className="workbench-session-activity-current">
          <span>{activity.activity.kind === "browser" ? t("workbench.sessionStatus.browsing", "Browsing") : t("workbench.sessionStatus.currentActivity", "Current activity")}</span>
          <b>{activity.activity.label || activity.activity.detail}</b>
          {activity.activity.label && activity.activity.detail ? <small>{activity.activity.detail}</small> : null}
        </div>
      ) : null}
      {activeAgents.length ? <div className="workbench-session-activity-agents">{t("workbench.sessionStatus.agentsRunning", { count: activeAgents.length }, "{count} agents active")}</div> : null}
    </div>
  );
}

function WorkbenchTopbar({ projects, activeProject, activePage, taskView, activeTaskId, activeChatId, recentSessions, overflowSessions, pinnedResources, keyboardEnabled, onPinResource, onUnpinResource, onOpenPinnedResource, onOpenSession, onPauseSession, onStopSession, onTogglePinnedSession, onRemoveSessionTab, onLoadSessionResources, onLoadSessionBrowserPreview, onOpenSessionResource, notifications, onReloadNotifications, onOpenNotification, onSearch, onSettings, onNewProject, onSelectProject, onEditProject, onEditMemory, onDeleteProject, onNewTask, onOpenPage, theme, actualTheme, onToggleTheme }) {
  var { t } = window.CyreneUI.require("i18n").use();
  var dataState = window.CyreneUI.require("data").state;
  var tabs = Array.isArray(recentSessions) ? recentSessions : [];
  var overflowTabs = Array.isArray(overflowSessions) ? overflowSessions : [];
  var overflowGroups = wbSplitOverflowSessions(overflowTabs);
  var resources = Array.isArray(pinnedResources) ? pinnedResources : [];
  var [sessionMenu, setSessionMenu] = useWorkbenchState(null);
  var [resourceMenu, setResourceMenu] = useWorkbenchState(null);
  var [overflowMenu, setOverflowMenu] = useWorkbenchState(null);
  var [hoverPreview, setHoverPreview] = useWorkbenchState(null);
  var [activityClock, setActivityClock] = useWorkbenchState(function () { return Date.now(); });
  var [resourceDropActive, setResourceDropActive] = useWorkbenchState(false);
  var [chatSideHidden, setChatSideHidden] = useWorkbenchState(false);
  var [projectMenuOpen, setProjectMenuOpen] = useWorkbenchState(false);
  var [projectActionId, setProjectActionId] = useWorkbenchState("");
  var [voiceCommand, setVoiceCommand] = useWorkbenchState(function () { return WbVoiceCommand.snapshot(); });
  var topbarRef = useWorkbenchRef(null);
  var projectMenuRef = useWorkbenchRef(null);
  var sessionMenuSeqRef = useWorkbenchRef(0);
  var previewTimerRef = useWorkbenchRef(0);
  var terminalMorphKey = tabs.map(function (item) {
    return item.kind + ":" + item.id + ":" + Number(item.activity && item.activity.morphUntil || 0);
  }).join("|");

  useWorkbenchEffect(function () {
    return WbVoiceCommand.subscribe(setVoiceCommand);
  }, []);

  useWorkbenchEffect(function () {
    if (!projectMenuOpen) return undefined;
    function closeProjectMenu(event) {
      if (event.key && event.key !== "Escape") return;
      if (!event.key && projectMenuRef.current && projectMenuRef.current.contains(event.target)) return;
      setProjectMenuOpen(false);
      setProjectActionId("");
    }
    document.addEventListener("mousedown", closeProjectMenu);
    document.addEventListener("keydown", closeProjectMenu);
    return function () {
      document.removeEventListener("mousedown", closeProjectMenu);
      document.removeEventListener("keydown", closeProjectMenu);
    };
  }, [projectMenuOpen]);

  // Project switching is navigation on the user's current surface. Expose the
  // same menu/select handlers to the semantic surface; the agent cannot pass a
  // hidden project id directly to a renderer action.
  useWorkbenchEffect(function () {
    if (!window.CyreneUI.has("uiSurface")) return undefined;
    var uiSurface = window.CyreneUI.require("uiSurface");
    var unregister = [];
    unregister.push(uiSurface.register({
      node_id: "project_switcher",
      parent_id: "root",
      scope: "main",
      order: 10,
      get_node: function () {
        return {
          role: "button",
          name: t("rail.projects", "Projects"),
          value_summary: activeProject ? String(activeProject.name || "") : "",
          state: { expanded: projectMenuOpen, project_id: String(activeProject && activeProject.id || "") },
        };
      },
      actions: [{
        action_id: "open_menu", kind: "open_menu", risk: "R1", gesture_aliases: ["press", "keyboard"],
        outcome: { effect: "opens_menu", target_node_id: "project_menu", target_scope: "project_menu", inspect_after: true },
      }],
      handlers: { open_menu: function () { setProjectActionId(""); setProjectMenuOpen(true); } },
    }));
    if (projectMenuOpen) {
      uiSurface.setScope("project_menu");
      unregister.push(uiSurface.register({
        node_id: "project_menu",
        parent_id: "root",
        scope: "project_menu",
        get_node: function () { return { role: "menu", name: t("rail.projects", "Projects") }; },
        actions: [{ action_id: "dismiss", kind: "dismiss", risk: "R1", gesture_aliases: ["escape_key", "scrim"] }],
        handlers: { dismiss: function () { setProjectActionId(""); setProjectMenuOpen(false); } },
      }));
      (Array.isArray(projects) ? projects : []).forEach(function (project) {
        var projectId = String(project.id || "");
        unregister.push(uiSurface.register({
          node_id: "project_" + projectId.replace(/[^a-zA-Z0-9_-]/g, "").slice(0, 100),
          parent_id: "project_menu",
          scope: "project_menu",
          get_node: function () {
            return projectId ? {
              role: "menuitemradio",
              name: String(project.name || t("workbench.selectProject", "Select project")),
              value_summary: WorkbenchModel.pathLabel(project.workspacePath, project.name),
              state: {
                project_id: projectId,
                selected: String(activeProject && activeProject.id || "") === projectId,
              },
            } : null;
          },
          actions: [{ action_id: "select", kind: "select", risk: "R1", gesture_aliases: ["press", "keyboard"] }],
          handlers: {
            select: function () {
              setProjectActionId("");
              setProjectMenuOpen(false);
              return onSelectProject && onSelectProject(projectId);
            },
          },
        }));
      });
    } else if (uiSurface.getScope() === "project_menu") {
      uiSurface.setScope("main");
    }
    return function () { unregister.forEach(function (remove) { remove(); }); };
  }, [projects, activeProject && activeProject.id, activeProject && activeProject.name, projectMenuOpen, onSelectProject, t]);

  useWorkbenchEffect(function () {
    var now = Date.now();
    var nextExpiry = tabs.reduce(function (soonest, item) {
      var expiry = Number(item.activity && item.activity.morphUntil || 0);
      if (expiry <= now) return soonest;
      return !soonest || expiry < soonest ? expiry : soonest;
    }, 0);
    setActivityClock(now);
    if (!nextExpiry) return undefined;
    var timer = setTimeout(function () { setActivityClock(Date.now()); }, Math.max(16, nextExpiry - now + 20));
    return function () { clearTimeout(timer); };
  }, [terminalMorphKey]);
  function acceptsResourceDrag(event, resourceApi) {
    var transfer = event && event.dataTransfer;
    if (!transfer || !resourceApi) return false;
    var types = Array.prototype.slice.call(transfer.types || []);
    if (types.indexOf(resourceApi.mime) >= 0) return true;
    // Selected text on macOS uses Chromium's native text/plain drag. Files are
    // deliberately excluded because their cards have the richer custom type.
    return types.indexOf("text/plain") >= 0 && types.indexOf("Files") < 0;
  }
  var themeTitle = theme === "system" ? t("workbench.theme.system") : actualTheme === "dark" ? t("workbench.theme.dark") : t("workbench.theme.light");
  var themeIcon = theme === "system" ? (
    <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" strokeWidth="1.8"><circle cx="12" cy="12" r="9"/><path d="M12 3a9 9 0 0 1 0 18Z" fill="currentColor" stroke="none"/></svg>
  ) : actualTheme === "dark" ? (
    <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"><path d="M21 12.8A9 9 0 1 1 11.2 3 7 7 0 0 0 21 12.8Z"/></svg>
  ) : (
    <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"><circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4"/></svg>
  );

  function readTopbarPortalTheme() {
    var portalTheme = {};
    var themeSource = document.querySelector(".workbench-shell");
    if (themeSource && typeof getComputedStyle === "function") {
      var computedTheme = getComputedStyle(themeSource);
      [
        "--wb-surface", "--wb-card-bg", "--wb-card-bg-strong", "--wb-line", "--wb-line-2",
        "--wb-text", "--wb-muted", "--wb-faint",
        "--wb-control-bg", "--wb-control-hover-bg", "--wb-row-hover-bg",
        "--wb-green", "--wb-amber", "--wb-red", "--wb-accent", "--wb-ui-font-scale",
      ].forEach(function (name) { portalTheme[name] = computedTheme.getPropertyValue(name); });
      portalTheme.fontFamily = computedTheme.fontFamily;
    }
    return portalTheme;
  }

  function closeSessionPreview() {
    if (previewTimerRef.current) clearTimeout(previewTimerRef.current);
    previewTimerRef.current = 0;
    setHoverPreview(null);
  }

  function scheduleSessionPreview(event, item, activity, immediate) {
    if (previewTimerRef.current) clearTimeout(previewTimerRef.current);
    var node = event.currentTarget;
    var rect = node.getBoundingClientRect();
    previewTimerRef.current = setTimeout(function () {
      previewTimerRef.current = 0;
      var width = 300;
      setHoverPreview({
        item: item,
        activity: activity,
        left: Math.max(8, Math.min(rect.left + rect.width / 2 - width / 2, window.innerWidth - width - 8)),
        top: Math.min(window.innerHeight - 12, rect.bottom + 8),
        portalTheme: readTopbarPortalTheme(),
      });
    }, immediate ? 80 : 420);
  }

  function openOverflowMenu(event) {
    event.preventDefault();
    event.stopPropagation();
    closeSessionPreview();
    var rect = event.currentTarget.getBoundingClientRect();
    var width = 300;
    var height = Math.min(500, window.innerHeight - 16);
    setSessionMenu(null);
    setResourceMenu(null);
    setOverflowMenu({
      left: Math.max(8, Math.min(rect.left, window.innerWidth - width - 8)),
      top: Math.max(8, Math.min(rect.bottom + 8, window.innerHeight - height - 8)),
      portalTheme: readTopbarPortalTheme(),
    });
  }

  function closeOverflowMenu() { setOverflowMenu(null); }

  function renderOverflowSession(item) {
    var status = wbSessionStatusLabel(item.activity, t);
    var detail = wbSessionActivityCopy(item.activity, t);
    return (
      <button key={item.kind + ":" + item.id} type="button" role="menuitem" onClick={function () {
        closeOverflowMenu();
        if (onOpenSession) onOpenSession(item);
      }}>
        <span className={"workbench-session-overflow-icon " + String(item.activity.phase || "idle")}><WorkbenchSessionStatusIcon phase={item.activity.phase} active={item.activity.isLive} /></span>
        <span><b>{item.title}</b><small>{detail && detail !== status ? status + " · " + detail : status}</small></span>
      </button>
    );
  }

  function activeSessionIndex() {
    return tabs.findIndex(function (item) {
      return item.kind === "chat"
        ? activePage === "chat" && String(activeChatId || "") === String(item.id || "")
        : !activePage && taskView === "detail" && String(activeTaskId || "") === String(item.id || "");
    });
  }

  function openSessionAt(index) {
    if (!tabs.length) return;
    var normalized = ((Number(index) || 0) % tabs.length + tabs.length) % tabs.length;
    if (tabs[normalized] && onOpenSession) onOpenSession(tabs[normalized]);
  }

  function copyBrowserToConversation(targetChatId, resource) {
    return wbCopyBrowserToChat(targetChatId, resource).then(function (copied) {
      window.CyreneUI.require("feedback").showToast(
        copied
          ? t("workbench.resourceShelf.browserCopiedToChat", "Webpage copied to conversation browser")
          : t("workbench.resourceShelf.browserCopyFailed", "Could not copy webpage to conversation"),
        copied ? "success" : "error"
      );
      return copied;
    });
  }

  function handleTopbarItemKeyDown(event, onRemove) {
    var key = String(event.key || "");
    if (key === "Delete" || key === "Backspace") {
      if (onRemove) {
        event.preventDefault();
        onRemove();
      }
      return;
    }
    if (["ArrowLeft", "ArrowRight", "Home", "End"].indexOf(key) < 0) return;
    var root = topbarRef.current;
    var items = root ? Array.prototype.slice.call(root.querySelectorAll("[data-workbench-topbar-item]")) : [];
    if (!items.length) return;
    var current = items.indexOf(event.currentTarget);
    if (current < 0) return;
    var next = key === "Home"
      ? 0
      : key === "End"
        ? items.length - 1
        : (current + (key === "ArrowRight" ? 1 : -1) + items.length) % items.length;
    event.preventDefault();
    items[next].focus();
  }

  useWorkbenchEffect(function () {
    function handleChatSideVisibility(event) {
      var detail = event && event.detail || {};
      setChatSideHidden(!!detail.active && !!detail.hidden);
    }
    window.addEventListener("workbench:chat-side-visibility", handleChatSideVisibility);
    return function () {
      window.removeEventListener("workbench:chat-side-visibility", handleChatSideVisibility);
    };
  }, []);

  useWorkbenchEffect(function () {
    function handleBrowserCopy(event) {
      var detail = event && event.detail || {};
      if (!detail.targetChatId || !detail.resource) return;
      copyBrowserToConversation(detail.targetChatId, detail.resource);
    }
    window.addEventListener("cyrene:copy-browser-to-chat", handleBrowserCopy);
    return function () {
      window.removeEventListener("cyrene:copy-browser-to-chat", handleBrowserCopy);
    };
  }, []);

  useWorkbenchEffect(function () {
    function handleSessionShortcut(event) {
      if (!keyboardEnabled) return;
      var sc = window.CyreneUI.require("shortcuts");
      if (!sc) return;
      var direct = ["switch-session-1", "switch-session-2", "switch-session-3"];
      for (var index = 0; index < direct.length; index += 1) {
        if (sc.matches(event, direct[index])) {
          if (!tabs[index]) return;
          event.preventDefault();
          openSessionAt(index);
          return;
        }
      }
      if (sc.matches(event, "next-session") || sc.matches(event, "previous-session")) {
        event.preventDefault();
        var current = activeSessionIndex();
        var direction = sc.matches(event, "previous-session") ? -1 : 1;
        openSessionAt((current < 0 ? 0 : current) + direction);
        return;
      }
      if (sc.matches(event, "close-session-tab")) {
        var activeIndex = activeSessionIndex();
        if (activeIndex < 0 || !tabs[activeIndex] || !onRemoveSessionTab) return;
        event.preventDefault();
        onRemoveSessionTab(tabs[activeIndex]);
      }
    }
    window.addEventListener("keydown", handleSessionShortcut);
    return function () { window.removeEventListener("keydown", handleSessionShortcut); };
  }, [keyboardEnabled, tabs, activePage, taskView, activeTaskId, activeChatId, onOpenSession, onRemoveSessionTab]);

  useWorkbenchEffect(function () {
    function handlePointerShelfDrag(event) {
      setResourceDropActive(!!(event && event.detail && event.detail.active));
    }
    window.addEventListener("cyrene:resource-shelf-drag-state", handlePointerShelfDrag);
    return function () {
      window.removeEventListener("cyrene:resource-shelf-drag-state", handlePointerShelfDrag);
    };
  }, []);

  useWorkbenchEffect(function () {
    if (!sessionMenu && !resourceMenu && !overflowMenu) return undefined;
    function closeMenu() {
      sessionMenuSeqRef.current += 1;
      setSessionMenu(null);
      setResourceMenu(null);
      setOverflowMenu(null);
    }
    function handleKey(event) {
      if (event.key === "Escape") closeMenu();
      if (["ArrowDown", "ArrowUp", "Home", "End"].indexOf(event.key) < 0) return;
      var menu = document.querySelector(".workbench-session-context-menu[role='menu']");
      if (!menu) return;
      var items = Array.prototype.slice.call(menu.querySelectorAll("[role='menuitem']:not(:disabled)"));
      if (!items.length) return;
      var current = items.indexOf(document.activeElement);
      var nextIndex = event.key === "Home"
        ? 0
        : event.key === "End"
          ? items.length - 1
          : event.key === "ArrowUp"
            ? (current <= 0 ? items.length - 1 : current - 1)
            : (current < 0 || current >= items.length - 1 ? 0 : current + 1);
      event.preventDefault();
      items[nextIndex].focus();
    }
    function handleScroll(event) {
      var target = event && event.target;
      if (target && target.nodeType === 1 && target.closest && target.closest(
        ".workbench-session-overflow-menu, .workbench-session-menu"
      )) return;
      closeMenu();
    }
    window.addEventListener("resize", closeMenu);
    window.addEventListener("scroll", handleScroll, true);
    document.addEventListener("keydown", handleKey);
    return function () {
      window.removeEventListener("resize", closeMenu);
      window.removeEventListener("scroll", handleScroll, true);
      document.removeEventListener("keydown", handleKey);
    };
  }, [!!sessionMenu, !!resourceMenu, !!overflowMenu]);

  useWorkbenchEffect(function () {
    if (!sessionMenu && !resourceMenu) {
      if (!overflowMenu && !hoverPreview) return undefined;
    }
    wbSetBrowserOverlayObscured(1);
    return function () { wbSetBrowserOverlayObscured(-1); };
  }, [!!sessionMenu, !!resourceMenu, !!overflowMenu, !!hoverPreview]);

  useWorkbenchEffect(function () {
    if (!sessionMenu || !onLoadSessionBrowserPreview) return undefined;
    var item = sessionMenu.item;
    var cancelled = false;
    var inFlight = false;
    function refreshBrowserPreview() {
      if (cancelled || inFlight) return;
      inFlight = true;
      Promise.resolve(onLoadSessionBrowserPreview(item)).then(function (browser) {
        if (cancelled) return;
        setSessionMenu(function (current) {
          if (!current || current.item.id !== item.id || current.item.kind !== item.kind) return current;
          var nextBrowser = browser || null;
          var previous = current.resources && current.resources.browser;
          if (previous && nextBrowser
              && previous.previewUrl === nextBrowser.previewUrl
              && previous.title === nextBrowser.title
              && previous.url === nextBrowser.url) return current;
          if (!previous && !nextBrowser) return current;
          return Object.assign({}, current, {
            resources: Object.assign({}, current.resources, { browser: nextBrowser }),
          });
        });
      }).catch(function () {}).finally(function () {
        inFlight = false;
      });
    }
    var timer = setInterval(refreshBrowserPreview, 1200);
    return function () {
      cancelled = true;
      clearInterval(timer);
    };
  }, [sessionMenu ? sessionMenu.item.kind + ":" + sessionMenu.item.id : "", !!onLoadSessionBrowserPreview]);

  useWorkbenchEffect(function () {
    return function () {
      if (previewTimerRef.current) clearTimeout(previewTimerRef.current);
    };
  }, []);

  function openSessionMenu(event, item, activity, anchored) {
    event.preventDefault();
    event.stopPropagation();
    closeSessionPreview();
    setOverflowMenu(null);
    var menuWidth = Math.min(340, Math.max(0, window.innerWidth - 16));
    var menuHeight = 440;
    var rect = event.currentTarget && event.currentTarget.getBoundingClientRect ? event.currentTarget.getBoundingClientRect() : null;
    var left = anchored && rect ? rect.left + (rect.width - menuWidth) / 2 : event.clientX;
    var top = anchored && rect ? rect.bottom + 8 : event.clientY;
    left = Math.max(8, Math.min(left, window.innerWidth - menuWidth - 8));
    top = Math.max(8, Math.min(top, window.innerHeight - menuHeight - 8));
    var portalTheme = readTopbarPortalTheme();
    var seq = sessionMenuSeqRef.current + 1;
    sessionMenuSeqRef.current = seq;
    setSessionMenu({ item: item, activity: activity || item.activity || {}, left: left, top: top, portalTheme: portalTheme, loading: true, resources: { browser: false, files: [] } });
    Promise.resolve(onLoadSessionResources ? onLoadSessionResources(item) : null)
      .then(function (resources) {
        if (sessionMenuSeqRef.current !== seq) return;
        setSessionMenu(function (current) {
          if (!current || current.item.id !== item.id || current.item.kind !== item.kind) return current;
          return Object.assign({}, current, {
            loading: false,
            resources: {
              browser: resources && resources.browser ? resources.browser : null,
              files: resources && Array.isArray(resources.files) ? resources.files : [],
            },
          });
        });
      })
      .catch(function () {
        if (sessionMenuSeqRef.current !== seq) return;
        setSessionMenu(function (current) {
          return current ? Object.assign({}, current, { loading: false }) : current;
        });
      });
  }

  function closeSessionMenu() {
    sessionMenuSeqRef.current += 1;
    setSessionMenu(null);
  }

  function runSessionMenuAction(action) {
    closeSessionMenu();
    if (action) action();
  }

  function copySessionTitle(item) {
    var title = String((item && item.title) || "");
    var copy = navigator.clipboard && navigator.clipboard.writeText
      ? navigator.clipboard.writeText(title)
      : Promise.reject(new Error("Clipboard unavailable"));
    copy.then(function () {
      window.CyreneUI.require("feedback").showToast(t("workbench.sessionMenu.copied", "Title copied"), "success");
    }).catch(function () {
      window.CyreneUI.require("feedback").showToast(title, "info");
    });
  }

  function portalThemeAt(event, height) {
    var menuWidth = 224;
    var themeStyle = readTopbarPortalTheme();
    return {
      left: Math.max(8, Math.min(event.clientX, window.innerWidth - menuWidth - 8)),
      top: Math.max(8, Math.min(event.clientY, window.innerHeight - (height || 220) - 8)),
      portalTheme: themeStyle,
    };
  }

  function openResourceMenu(event, resource) {
    event.preventDefault();
    event.stopPropagation();
    closeSessionPreview();
    setSessionMenu(null);
    setOverflowMenu(null);
    setResourceMenu(Object.assign({ resource: resource }, portalThemeAt(event, 210)));
  }

  function closeResourceMenu() {
    setResourceMenu(null);
  }

  function copyResourceReference(resource) {
    var text = String(resource && (resource.path || resource.url || resource.title) || "");
    var promise = navigator.clipboard && navigator.clipboard.writeText
      ? navigator.clipboard.writeText(text)
      : Promise.reject(new Error("Clipboard unavailable"));
    promise.then(function () {
      window.CyreneUI.require("feedback").showToast(t("workbench.resourceShelf.copied", "Resource reference copied"), "success");
    }).catch(function () {
      window.CyreneUI.require("feedback").showToast(text, "info");
    });
  }

  var sessionMenuCurrentItem = sessionMenu && (tabs.concat(overflowTabs).find(function (item) {
    return item.kind === sessionMenu.item.kind && item.id === sessionMenu.item.id;
  }) || sessionMenu.item);
  var sessionMenuCurrentActivity = sessionMenuCurrentItem && sessionMenuCurrentItem.activity || (sessionMenu && sessionMenu.activity);
  var sessionMenuPortal = sessionMenu && typeof ReactDOM !== "undefined"
    ? ReactDOM.createPortal((
      <div className="workbench-session-menu-portal" style={sessionMenu.portalTheme}>
        <div className="workbench-session-menu-scrim" onPointerDown={closeSessionMenu} />
        <div
          className="workbench-account-menu workbench-session-menu workbench-session-context-menu"
          role="menu"
          aria-label={sessionMenuCurrentItem.title}
          style={{ ...sessionMenu.portalTheme, left: sessionMenu.left, top: sessionMenu.top }}
          onContextMenu={function (event) { event.preventDefault(); }}
        >
          <div className="workbench-session-activity-menu-head">
            <span className={"workbench-session-activity-menu-state " + String(sessionMenuCurrentActivity.phase || "idle")}>
              <WorkbenchSessionStatusIcon phase={sessionMenuCurrentActivity.phase} active={sessionMenuCurrentActivity.isLive} />
            </span>
            <div>
              <b>{t("workbench.sessionActivity.title", "Agent activity")}</b>
              <small>{wbSessionStatusLabel(sessionMenuCurrentActivity, t)}</small>
            </div>
          </div>
          <div className="workbench-session-activity-menu-list">
            <div className="workbench-session-activity-menu-row">
              <span className={"workbench-session-activity-agent-mark main " + (sessionMenuCurrentActivity.isLive ? "running" : "idle")} aria-hidden="true" />
              <span><b>{t("workbench.sessionActivity.mainAgent", "Main Agent")}</b><small>{sessionMenuCurrentActivity.isLive ? (wbSessionActivityCopy(sessionMenuCurrentActivity, t) || t("workbench.sessionStatus.running", "Running")) : wbSessionStatusLabel(sessionMenuCurrentActivity, t)}</small></span>
            </div>
            {(sessionMenuCurrentActivity.agents || []).slice(0, 5).map(function (agent) {
              return (
                <div className="workbench-session-activity-menu-row" key={agent.id}>
                  <span className={"workbench-session-activity-agent-mark " + String(agent.status || "idle")} aria-hidden="true" />
                  <span><b>{agent.name || agent.id}</b><small>{agent.task || String(agent.status || "")}</small></span>
                </div>
              );
            })}
          </div>
          {sessionMenuCurrentActivity.progress && sessionMenuCurrentActivity.progress.total ? (
            <div className="workbench-session-activity-menu-progress">
              <span>{t("workbench.sessionStatus.step", {
                current: sessionMenuCurrentActivity.progress.current || sessionMenuCurrentActivity.progress.completed,
                total: sessionMenuCurrentActivity.progress.total,
              }, "Step {current}/{total}")}</span>
              <b>{sessionMenuCurrentActivity.progress.title}</b>
            </div>
          ) : null}
          {sessionMenu.resources.browser ? (
            <button type="button" role="menuitem" className="workbench-session-browser-preview" onClick={function () {
              var item = sessionMenuCurrentItem;
              runSessionMenuAction(function () {
                if (onOpenSessionResource) onOpenSessionResource(item, { type: "browser" });
              });
            }}>
              <span className="workbench-session-browser-preview-head">
                <span className="workbench-session-menu-icon" aria-hidden="true">
                  <svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" strokeWidth="1.8"><rect x="3" y="4" width="18" height="16" rx="2"/><path d="M3 8h18M6 6h.01M9 6h.01"/></svg>
                </span>
                <span>
                  <b>{sessionMenu.resources.browser.title || t("workbench.resourceShelf.browser", "Browser")}</b>
                  <small>{sessionMenu.resources.browser.url}</small>
                </span>
                <svg className="workbench-session-resource-chevron" viewBox="0 0 16 16" width="14" height="14" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="m6 3.5 4.5 4.5L6 12.5" /></svg>
              </span>
              {sessionMenu.resources.browser.previewUrl ? (
                <img src={sessionMenu.resources.browser.previewUrl} alt="" draggable="false" />
              ) : (
                <span className="workbench-session-browser-preview-empty">
                  {t("workbench.sessionMenu.browserPreview", "Browser preview")}
                </span>
              )}
            </button>
          ) : null}
          {sessionMenu.resources.files.length ? (
            <div className="workbench-session-resource-section">
              <div className="wb-menu-head workbench-session-menu-label">{t("workbench.sessionMenu.files", "Files")}</div>
              {sessionMenu.resources.files.map(function (file, index) {
                var fileKey = String(file.id || file.url || file.name || index);
                return (
                  <button key={fileKey} type="button" role="menuitem" className="workbench-session-menu-file" onClick={function () {
                    var item = sessionMenuCurrentItem;
                    runSessionMenuAction(function () {
                      if (onOpenSessionResource) onOpenSessionResource(item, { type: "file", file: file });
                    });
                  }}>
                    <span className="workbench-session-menu-icon" aria-hidden="true">
                      <svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" strokeWidth="1.8"><path d="M6 2h8l4 4v16H6Z"/><path d="M14 2v5h5"/></svg>
                    </span>
                    <WorkbenchSessionMenuFileName name={file.name || t("workbench.sessionMenu.untitledFile", "Untitled file")} />
                  </button>
                );
              })}
            </div>
          ) : null}
          {sessionMenu.loading ? (
            <div className="workbench-session-menu-loading">{t("workbench.sessionMenu.loading", "Loading resources…")}</div>
          ) : null}
          <div className={"workbench-session-primary-actions" + (sessionMenuCurrentActivity.capabilities && (sessionMenuCurrentActivity.capabilities.canPause || sessionMenuCurrentActivity.capabilities.canStop) ? " has-runtime-control" : "")}>
            {sessionMenuCurrentActivity.capabilities && sessionMenuCurrentActivity.capabilities.canPause ? (
              <button type="button" role="menuitem" onClick={function () {
                var item = sessionMenuCurrentItem;
                runSessionMenuAction(function () { if (onPauseSession) onPauseSession(item); });
              }}>
                <span className="workbench-session-menu-icon" aria-hidden="true"><WorkbenchSessionStatusIcon phase="paused" /></span>
                <span>{t("workbench.sessionActivity.pause", "Pause")}</span>
              </button>
            ) : null}
            {sessionMenuCurrentActivity.capabilities && sessionMenuCurrentActivity.capabilities.canStop ? (
              <button type="button" role="menuitem" className="stop" onClick={function () {
                var item = sessionMenuCurrentItem;
                runSessionMenuAction(function () { if (onStopSession) onStopSession(item); });
              }}>
                <span className="workbench-session-menu-icon" aria-hidden="true">
                  <svg viewBox="0 0 16 16" width="13" height="13" fill="currentColor"><rect x="3.5" y="3.5" width="9" height="9" rx="1.5"/></svg>
                </span>
                <span>{t("workbench.sessionActivity.stop", "Stop")}</span>
              </button>
            ) : null}
            <button type="button" role="menuitem" className="open-session" onClick={function () {
              var item = sessionMenuCurrentItem;
              runSessionMenuAction(function () { if (onOpenSession) onOpenSession(item); });
            }}>
              <span className="workbench-session-menu-icon" aria-hidden="true">
                <svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"><path d="M5 12h14M13 6l6 6-6 6"/></svg>
              </span>
              <span>{t("workbench.sessionActivity.openSession", "Open Session")}</span>
            </button>
          </div>
          <div className="workbench-session-utility-actions">
            <button type="button" role="menuitem" onClick={function () {
              runSessionMenuAction(function () { if (onTogglePinnedSession) onTogglePinnedSession(sessionMenuCurrentItem); });
            }}>
              <span className="workbench-session-menu-icon" aria-hidden="true">
                <svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"><path d="M12 17v5"/><path d="M5 17h14"/><path d="M17 3a1 1 0 0 1 1 1v4.6a2 2 0 0 0 .6 1.4l1.7 1.7A1 1 0 0 1 19.6 13H4.4a1 1 0 0 1-.7-1.7l1.7-1.7A2 2 0 0 0 6 8.2V4a1 1 0 0 1 1-1Z"/></svg>
              </span>
              <span>{sessionMenuCurrentItem.pinned ? t("workbench.sessionMenu.unpin", "Unpin tab") : t("workbench.sessionMenu.pin", "Pin tab")}</span>
            </button>
            <button type="button" role="menuitem" onClick={function () {
              var item = sessionMenuCurrentItem;
              runSessionMenuAction(function () { copySessionTitle(item); });
            }}>
              <span className="workbench-session-menu-icon" aria-hidden="true"><svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" strokeWidth="1.8"><rect x="8" y="8" width="12" height="12" rx="2"/><path d="M16 8V6a2 2 0 0 0-2-2H6a2 2 0 0 0-2 2v8a2 2 0 0 0 2 2h2"/></svg></span>
              <span>{t("workbench.sessionMenu.copyTitle", "Copy title")}</span>
            </button>
            <button type="button" role="menuitem" className="danger" onClick={function () {
              var item = sessionMenuCurrentItem;
              runSessionMenuAction(function () { if (onRemoveSessionTab) onRemoveSessionTab(item); });
            }}>
              <span className="workbench-session-menu-icon" aria-hidden="true"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"><circle cx="12" cy="12" r="9"/><path d="m9 9 6 6m0-6-6 6"/></svg></span>
              <span>{t("workbench.sessionMenu.remove", "Remove")}</span>
            </button>
          </div>
        </div>
      </div>
    ), document.body)
    : null;

  var overflowMenuPortal = overflowMenu && typeof ReactDOM !== "undefined"
    ? ReactDOM.createPortal((
      <div className="workbench-session-menu-portal" style={overflowMenu.portalTheme}>
        <div className="workbench-session-menu-scrim" onPointerDown={closeOverflowMenu} />
        <div
          className={"workbench-session-overflow-menu workbench-session-context-menu" + (overflowGroups.regular.length && overflowGroups.exceptional.length ? " split-scroll" : "")}
          role="menu"
          aria-label={t("workbench.sessionOverflow.title", "More sessions")}
          style={{ ...overflowMenu.portalTheme, left: overflowMenu.left, top: overflowMenu.top }}
        >
          <div className="workbench-session-overflow-head">
            <b>{t("workbench.sessionOverflow.title", "All conversations")}</b>
            <small>{t("workbench.sessionOverflow.count", { count: overflowTabs.length }, "{count} more")}</small>
          </div>
          <div className={"workbench-session-overflow-list" + (overflowGroups.regular.length ? " has-regular" : "") + (overflowGroups.exceptional.length ? " has-exceptions" : "")}>
            {overflowGroups.regular.length ? (
              <div className="workbench-session-overflow-group" role="group" aria-label={t("workbench.sessionOverflow.other", "Other sessions")}>
                <div className="workbench-session-overflow-group-head"><span>{t("workbench.sessionOverflow.other", "Other sessions")}</span><small>{overflowGroups.regular.length}</small></div>
                <div className="workbench-session-overflow-group-items">{overflowGroups.regular.map(renderOverflowSession)}</div>
              </div>
            ) : null}
            {overflowGroups.regular.length && overflowGroups.exceptional.length ? <div className="workbench-session-overflow-divider" /> : null}
            {overflowGroups.exceptional.length ? (
              <div className="workbench-session-overflow-group exceptional" role="group" aria-label={t("workbench.sessionOverflow.exceptions", "Exceptions")}>
                <div className="workbench-session-overflow-group-head"><span>{t("workbench.sessionOverflow.exceptions", "Exceptions")}</span><small>{overflowGroups.exceptional.length}</small></div>
                <div className="workbench-session-overflow-group-items">{overflowGroups.exceptional.map(renderOverflowSession)}</div>
              </div>
            ) : null}
          </div>
        </div>
      </div>
    ), document.body)
    : null;

  var resourceMenuPortal = resourceMenu && typeof ReactDOM !== "undefined"
    ? ReactDOM.createPortal((
      <div className="workbench-session-menu-portal" style={resourceMenu.portalTheme}>
        <div className="workbench-session-menu-scrim" onPointerDown={closeResourceMenu} />
        <div
          className="workbench-account-menu workbench-session-menu workbench-session-context-menu workbench-resource-menu"
          role="menu"
          aria-label={resourceMenu.resource.title}
          style={{ ...resourceMenu.portalTheme, left: resourceMenu.left, top: resourceMenu.top }}
          onContextMenu={function (event) { event.preventDefault(); }}
        >
          <button type="button" role="menuitem" onClick={function () {
            var resource = resourceMenu.resource;
            closeResourceMenu();
            if (onOpenPinnedResource) onOpenPinnedResource(resource);
          }}>
            <span className="workbench-session-menu-icon" aria-hidden="true">
              {resourceMenu.resource.kind === "browser"
                ? <svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" strokeWidth="1.8"><rect x="3" y="4" width="18" height="16" rx="2"/><path d="M3 8h18M6 6h.01M9 6h.01"/></svg>
                : <svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" strokeWidth="1.8"><path d="M6 2h8l4 4v16H6Z"/><path d="M14 2v5h5"/></svg>}
            </span>
            <span>{resourceMenu.resource.kind === "browser"
              ? t("workbench.resourceShelf.openBrowser", "Open owner conversation")
              : resourceMenu.resource.kind === "snippet"
                ? t("workbench.resourceShelf.useSnippet", "Add to current conversation")
                : t("workbench.resourceShelf.openFile", "Open file")}</span>
          </button>
          {resourceMenu.resource.kind === "browser" ? (
            <div className="workbench-resource-readonly-note">
              {t("workbench.resourceShelf.readOnly", "Other sessions can only view this browser")}
            </div>
          ) : null}
          <button type="button" role="menuitem" onClick={function () {
            var resource = resourceMenu.resource;
            closeResourceMenu();
            copyResourceReference(resource);
          }}>
            <span className="workbench-session-menu-icon" aria-hidden="true">
              <svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" strokeWidth="1.8"><rect x="8" y="8" width="12" height="12" rx="2"/><path d="M16 8V6a2 2 0 0 0-2-2H6a2 2 0 0 0-2 2v8a2 2 0 0 0 2 2h2"/></svg>
            </span>
            <span>{t("workbench.resourceShelf.copyReference", "Copy reference")}</span>
          </button>
          <div className="workbench-session-menu-separator" />
          <button type="button" role="menuitem" className="danger" onClick={function () {
            var resource = resourceMenu.resource;
            closeResourceMenu();
            if (onUnpinResource) onUnpinResource(resource);
          }}>
            <span className="workbench-session-menu-icon" aria-hidden="true">
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"><circle cx="12" cy="12" r="9"/><path d="m9 9 6 6m0-6-6 6"/></svg>
            </span>
            <span>{t("workbench.resourceShelf.remove", "Remove from topbar")}</span>
          </button>
        </div>
      </div>
    ), document.body)
    : null;

  var hoverPreviewPortal = hoverPreview && typeof ReactDOM !== "undefined"
    ? ReactDOM.createPortal((
      <WorkbenchSessionActivityPreview preview={hoverPreview} t={t} />
    ), document.body)
    : null;

  var overflowActivity = overflowTabs.reduce(function (highest, item) {
    if (!highest || wbSessionActivityRank(item.activity) < wbSessionActivityRank(highest)) return item.activity;
    return highest;
  }, null) || { phase: "idle" };

  return (
    <div ref={topbarRef} className="workbench-topbar">
      <div className="workbench-brand" ref={projectMenuRef}>
        <div className="workbench-traffic-space"></div>
        <button
          data-cyrene-node-id="project_switcher"
          type="button"
          className={"workbench-brand-btn workbench-project-switcher-btn" + (projectMenuOpen ? " active" : "")}
          onClick={function () { setProjectActionId(""); setProjectMenuOpen(function (open) { return !open; }); }}
          title={t("rail.projects")}
          aria-label={t("rail.projects")}
          aria-haspopup="menu"
          aria-expanded={projectMenuOpen}
        >
          <span
            className={"workbench-top-project-icon" + (activeProject && (activeProject.dataKey === "default" || activeProject.name === "Cyrene") ? " logo" : "")}
            style={activeProject && activeProject.dataKey !== "default" && activeProject.name !== "Cyrene" ? { background: activeProject.color || WorkbenchModel.projectGradient(activeProject.id || activeProject.name) } : undefined}
            aria-hidden="true"
          >
            {activeProject && (activeProject.dataKey === "default" || activeProject.name === "Cyrene")
              ? <span className="brand-mark" />
              : WorkbenchModel.initials(activeProject && activeProject.name)}
          </span>
          <strong>{activeProject ? activeProject.name : t("workbench.selectProject", "Select project")}</strong>
          <span className="workbench-project-switcher-chevron" aria-hidden="true">
            <svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="m6 9 6 6 6-6"/></svg>
          </span>
        </button>
        {projectMenuOpen && (
          <div className="workbench-top-project-menu" role="menu" aria-label={t("rail.projects")}>
            <div className="workbench-top-project-menu-head">
              <strong>{t("rail.projects")}</strong>
              <button type="button" onClick={function () { setProjectMenuOpen(false); if (onNewProject) onNewProject(); }}>
                <svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round"><path d="M12 5v14M5 12h14"/></svg>
                <span>{t("rail.newProject")}</span>
              </button>
            </div>
            <div className="workbench-top-project-menu-list">
              {(Array.isArray(projects) ? projects : []).map(function (project) {
                var selected = activeProject && String(activeProject.id || "") === String(project.id || "");
                var isCyrene = project.dataKey === "default" || project.name === "Cyrene";
                var actionsOpen = projectActionId === project.id;
                return (
                  <div key={project.id} className={"workbench-top-project-row" + (selected ? " active" : "") + (actionsOpen ? " menu-open" : "")}>
                    <button type="button" data-cyrene-node-id={"project_" + String(project.id || "").replace(/[^a-zA-Z0-9_-]/g, "").slice(0, 100)} className="workbench-top-project-select" role="menuitemradio" aria-checked={selected} onClick={function () {
                      setProjectMenuOpen(false);
                      setProjectActionId("");
                      if (onSelectProject) onSelectProject(project.id);
                    }}>
                      <span
                        className={"workbench-top-project-icon" + (isCyrene ? " logo" : "")}
                        style={isCyrene ? undefined : { background: project.color || WorkbenchModel.projectGradient(project.id || project.name) }}
                        aria-hidden="true"
                      >{isCyrene ? <span className="brand-mark" /> : WorkbenchModel.initials(project.name)}</span>
                      <span className="workbench-top-project-copy"><b>{project.name}</b><small>{WorkbenchModel.pathLabel(project.workspacePath, project.name)}</small></span>
                    </button>
                    <button type="button" className="workbench-top-project-more" aria-label={t("rail.projectActions")} onClick={function (event) {
                      event.stopPropagation();
                      setProjectActionId(actionsOpen ? "" : project.id);
                    }}>
                      <svg viewBox="0 0 24 24" width="15" height="15" fill="currentColor"><circle cx="5" cy="12" r="1.7"/><circle cx="12" cy="12" r="1.7"/><circle cx="19" cy="12" r="1.7"/></svg>
                    </button>
                    {actionsOpen && (
                      <div className="workbench-top-project-actions" role="menu">
                        <button type="button" role="menuitem" onClick={function () { setProjectActionId(""); setProjectMenuOpen(false); if (onEditProject) onEditProject(project); }}>
                          <svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" strokeWidth="1.9" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="M12 20h9"/><path d="M16.5 3.5a2.1 2.1 0 0 1 3 3L7 19l-4 1 1-4Z"/></svg>
                          <span>{t("rail.editProject")}</span>
                        </button>
                        <button type="button" role="menuitem" onClick={function () { setProjectActionId(""); setProjectMenuOpen(false); if (onEditMemory) onEditMemory(project); }}>
                          <svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="M12 3 13.7 9.3 20 11l-6.3 1.7L12 19l-1.7-6.3L4 11l6.3-1.7Z"/><path d="M18.5 16.5 19 19l2.5.5L19 20l-.5 2.5L18 20l-2.5-.5L18 19Z"/></svg>
                          <span>{t("rail.editMemory")}</span>
                        </button>
                        {!isCyrene ? <button type="button" role="menuitem" className="danger" onClick={function () { setProjectActionId(""); setProjectMenuOpen(false); if (onDeleteProject) onDeleteProject(project); }}>
                          <svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="M3 6h18M8 6V4a1 1 0 0 1 1-1h6a1 1 0 0 1 1 1v2M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6"/></svg>
                          <span>{t("rail.deleteProject")}</span>
                        </button> : null}
                      </div>
                    )}
                  </div>
                );
              })}
            </div>
          </div>
        )}
      </div>
      <nav className="workbench-session-tabs" aria-label={t("workbench.recentSessions", "Recent sessions")}>
        {tabs.map(function (item) {
          var isActive = item.kind === "chat"
            ? activePage === "chat" && String(activeChatId || "") === item.id
            : !activePage && taskView === "detail" && String(activeTaskId || "") === item.id;
          var kindLabel = item.kind === "chat"
            ? t("workbench.page.chat", "Conversation")
            : t("workbench.page.task", "Task");
          var activity = item.activity || { phase: "idle" };
          var statusLabel = wbSessionStatusLabel(activity, t);
          var activityCopy = wbSessionActivityCopy(activity, t);
          var progress = activity.progress || {};
          var showActivityCopy = !!activityCopy && (
            activity.phase !== "completed" || Number(activity.morphUntil || 0) > activityClock
          );
          var replaceTitleForMorph = isActive && showActivityCopy && (
            (activity.activity && activity.activity.kind === "browser")
            || ["attention", "completed", "failed", "paused"].indexOf(activity.phase) >= 0
          );
          var morphTitle = replaceTitleForMorph && activity.activity && activity.activity.kind === "browser"
            ? String(activity.activity.label || activityCopy)
            : (replaceTitleForMorph ? activityCopy : "");
          var morphDetail = replaceTitleForMorph && activity.activity && activity.activity.kind === "browser"
            ? t("workbench.sessionStatus.browsing", "Browsing")
            : "";
          return (
            <div
              key={item.kind + ":" + item.id}
              className={"workbench-session-tab-group phase-" + String(activity.phase || "idle") + (isActive ? " active" : "")}
            >
              <button
                type="button"
                className={"workbench-session-tab" + (isActive ? " active" : "")}
                data-workbench-topbar-item="session"
                data-session-kind={item.kind}
                data-session-id={item.id}
                data-cyrene-context-menu="true"
                aria-current={isActive ? "page" : undefined}
                aria-describedby={hoverPreview && hoverPreview.item.id === item.id && hoverPreview.item.kind === item.kind
                  ? "workbench-session-activity-preview"
                  : undefined}
                aria-label={kindLabel + ": " + item.title + " · " + statusLabel}
                title={[item.projectName, kindLabel, item.title, statusLabel].filter(Boolean).join(" · ")}
                onClick={function () { if (onOpenSession) onOpenSession(item); }}
                onPointerEnter={function (event) { scheduleSessionPreview(event, item, activity, false); }}
                onPointerLeave={closeSessionPreview}
                onFocus={function (event) { scheduleSessionPreview(event, item, activity, true); }}
                onBlur={closeSessionPreview}
                onKeyDown={function (event) {
                  handleTopbarItemKeyDown(event, function () {
                    if (onRemoveSessionTab) onRemoveSessionTab(item);
                  });
                }}
                onContextMenu={function (event) { openSessionMenu(event, item, activity, false); }}
                onDragOver={item.kind === "chat" ? function (event) {
                  var resourceApi = window.CyreneUI.require("resources");
                  if (acceptsResourceDrag(event, resourceApi)) {
                    event.preventDefault();
                    event.dataTransfer.dropEffect = "copy";
                    event.currentTarget.classList.add("resource-drop-target");
                  }
                } : undefined}
                onDragLeave={item.kind === "chat" ? function (event) {
                  event.currentTarget.classList.remove("resource-drop-target");
                } : undefined}
                onDrop={item.kind === "chat" ? function (event) {
                  event.preventDefault();
                  event.currentTarget.classList.remove("resource-drop-target");
                  var resourceApi = window.CyreneUI.require("resources");
                  var resource = resourceApi && resourceApi.readDrag(event);
                  if (!resource) return;
                  if (resource.kind === "browser") {
                    copyBrowserToConversation(item.id, resource);
                    return;
                  }
                  if (wbDeliverResourceToChat(item.id, resource)) {
                    window.CyreneUI.require("feedback").showToast(
                      t("workbench.resourceShelf.addedToChat", "Added to conversation input"),
                      "success"
                    );
                  }
                } : undefined}
              >
                <span className={"workbench-session-tab-status " + String(activity.phase || "idle")} aria-hidden="true">
                  <WorkbenchSessionStatusIcon phase={activity.phase} active={activity.isLive} />
                </span>
                <span className="workbench-session-tab-copy">
                  {replaceTitleForMorph ? (
                    <span className="workbench-session-tab-title workbench-session-tab-morph-title">{morphTitle}</span>
                  ) : (
                    <WbcHoverMarquee text={item.title} className="workbench-session-tab-title" />
                  )}
                  {isActive && showActivityCopy && !replaceTitleForMorph ? (
                    <span className="workbench-session-tab-activity-copy">· {activityCopy}</span>
                  ) : isActive && morphDetail ? (
                    <span className="workbench-session-tab-activity-copy">· {morphDetail}</span>
                  ) : !isActive && progress.total && (activity.phase === "running" || activity.phase === "planning") ? (
                    <span className="workbench-session-tab-activity-copy">· {progress.current || progress.completed}/{progress.total}</span>
                  ) : null}
                </span>
                {item.pinned ? (
                  <span className="workbench-session-tab-pin" aria-label={t("workbench.sessionMenu.pinned", "Pinned")}>
                    <svg viewBox="0 0 24 24" width="10" height="10" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                      <path d="M12 17v5"/>
                      <path d="M5 17h14"/>
                      <path d="M17 3a1 1 0 0 1 1 1v4.6a2 2 0 0 0 .6 1.4l1.7 1.7A1 1 0 0 1 19.6 13H4.4a1 1 0 0 1-.7-1.7l1.7-1.7A2 2 0 0 0 6 8.2V4a1 1 0 0 1 1-1Z"/>
                    </svg>
                  </span>
                ) : null}
              </button>
              <button
                type="button"
                className="workbench-session-tab-more"
                aria-label={t("workbench.sessionActivity.moreActions", { title: item.title }, "More actions for {title}")}
                title={t("workbench.sessionActivity.title", "Agent activity")}
                onClick={function (event) { openSessionMenu(event, item, activity, true); }}
              >
                <svg viewBox="0 0 16 16" width="14" height="14" fill="currentColor" aria-hidden="true"><circle cx="3" cy="8" r="1.25"/><circle cx="8" cy="8" r="1.25"/><circle cx="13" cy="8" r="1.25"/></svg>
              </button>
            </div>
          );
        })}
        {overflowTabs.length ? (
          <button
            data-cyrene-node-id="open_search"
            type="button"
            className={"workbench-session-overflow-button " + String(overflowActivity.phase || "idle")}
            data-workbench-topbar-item="overflow"
            aria-label={t("workbench.sessionOverflow.buttonLabel", { count: overflowTabs.length }, "Show {count} more sessions") + " · " + wbSessionStatusLabel(overflowActivity, t)}
            title={t("workbench.sessionOverflow.title", "All conversations")}
            onClick={openOverflowMenu}
            onKeyDown={handleTopbarItemKeyDown}
          >
            <span className="workbench-session-overflow-stack" aria-hidden="true">
              <svg viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinejoin="round">
                <rect x="5.5" y="4" width="10.5" height="9" rx="2" />
                <path d="M13.5 16H5a2 2 0 0 1-2-2V7.5" />
              </svg>
              <span className={"workbench-session-overflow-indicator " + String(overflowActivity.phase || "idle")} />
            </span>
            <span className="workbench-session-overflow-count">{overflowTabs.length}</span>
            <svg className="workbench-session-overflow-chevron" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="m5 6.5 3 3 3-3" /></svg>
          </button>
        ) : null}
      </nav>
      <div
        className={"workbench-resource-shelf" + (resourceDropActive ? " drop-active" : "")}
        aria-label={t("workbench.resourceShelf.title", "Pinned resources")}
        onDragEnter={function (event) {
          var resourceApi = window.CyreneUI.require("resources");
          if (acceptsResourceDrag(event, resourceApi)) {
            event.preventDefault();
            setResourceDropActive(true);
          }
        }}
        onDragOver={function (event) {
          var resourceApi = window.CyreneUI.require("resources");
          if (acceptsResourceDrag(event, resourceApi)) {
            event.preventDefault();
            event.dataTransfer.dropEffect = "copy";
            setResourceDropActive(true);
          }
        }}
        onDragLeave={function (event) {
          if (!event.currentTarget.contains(event.relatedTarget)) setResourceDropActive(false);
        }}
        onDrop={function (event) {
          event.preventDefault();
          setResourceDropActive(false);
          var resourceApi = window.CyreneUI.require("resources");
          var resource = resourceApi && resourceApi.readDrag(event);
          if (resource && onPinResource) onPinResource(resource);
        }}
      >
        {resources.map(function (resource) {
          var label = resource.kind === "file"
            ? (resource.name || resource.title || "file")
            : resource.kind === "snippet"
              ? (resource.title || String(resource.text || "").slice(0, 48) || t("workbench.resourceShelf.snippet", "Text"))
              : (resource.title || resource.url || t("workbench.resourceShelf.browser", "Browser"));
          return (
            <button
              key={resource.id}
              type="button"
              className={"workbench-resource-chip " + resource.kind}
              data-workbench-topbar-item="resource"
              data-cyrene-context-menu="true"
              aria-label={label}
              title={label}
              onClick={function () { if (onOpenPinnedResource) onOpenPinnedResource(resource); }}
              draggable={resource.kind === "browser" ? "true" : undefined}
              onDragStart={resource.kind === "browser" ? function (event) {
                var resourceApi = window.CyreneUI.require("resources");
                if (resourceApi && resourceApi.setDrag) resourceApi.setDrag(event, resource);
              } : undefined}
              onKeyDown={function (event) {
                handleTopbarItemKeyDown(event, function () {
                  if (onUnpinResource) onUnpinResource(resource);
                });
              }}
              onContextMenu={function (event) { openResourceMenu(event, resource); }}
            >
              <span className="workbench-resource-chip-icon" aria-hidden="true">
                {resource.kind === "browser" ? (
                  <svg viewBox="0 0 24 24" width="17" height="17" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"><rect x="3" y="4" width="18" height="16" rx="2"/><path d="M3 8h18M6 6h.01M9 6h.01"/></svg>
                ) : resource.kind === "snippet" ? (
                  <svg viewBox="0 0 24 24" width="17" height="17" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round"><path d="M7 8h10M7 12h7M7 16h5"/><rect x="3" y="3" width="18" height="18" rx="3"/></svg>
                ) : (
                  <svg viewBox="0 0 24 24" width="17" height="17" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"><path d="M6 2h8l4 4v16H6Z"/><path d="M14 2v5h5"/></svg>
                )}
              </span>
              <span className="workbench-resource-chip-label">{label}</span>
            </button>
          );
        })}
        {!resources.length ? (
          <span
            className="workbench-resource-shelf-empty"
            role="img"
            aria-label={t("workbench.resourceShelf.dropHint", "Drag a file, selected text, browser, or knowledge item here to pin it")}
            title={t("workbench.resourceShelf.dropHint", "Drag a file, selected text, browser, or knowledge item here to pin it")}
          >
            <svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
              <path d="M12 17v5" />
              <path d="M5 17h14" />
              <path d="M17 3a1 1 0 0 1 1 1v4.6a2 2 0 0 0 .6 1.4l1.7 1.7A1 1 0 0 1 19.6 13H4.4a1 1 0 0 1-.7-1.7l1.7-1.7A2 2 0 0 0 6 8.2V4a1 1 0 0 1 1-1Z" />
            </svg>
          </span>
        ) : null}
      </div>
      <div className="workbench-top-actions">
        {chatSideHidden && (
          <button
            type="button"
            className="workbench-icon-btn"
            data-chat-side-show="true"
            onClick={function () { window.dispatchEvent(new CustomEvent("workbench:show-chat-side")); }}
            title={t("workbenchChat.showSidebar", "Show side panel")}
            aria-label={t("workbenchChat.showSidebar", "Show side panel")}
          >
            <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"><rect x="3" y="3" width="18" height="18" rx="2"/><path d="M15 3v18"/><path d="m9 10-2 2 2 2"/></svg>
          </button>
        )}
        <div
          className="workbench-top-action-group"
          role="group"
          aria-label={t("help.title") + " · " + t("workbench.search")}
        >
          <button
            type="button"
            className="workbench-icon-btn workbench-search-btn"
            onClick={onSearch}
            title={t("workbench.search")}
            aria-label={t("workbench.search")}
          >
            <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" strokeWidth="1.9" strokeLinecap="round" strokeLinejoin="round"><circle cx="11" cy="11" r="7"/><path d="m20 20-3.2-3.2"/></svg>
          </button>
          <WorkbenchHelpCenter onNewProject={onNewProject} onNewTask={onNewTask} onOpenPage={onOpenPage} onSettings={onSettings} />
        </div>
        <WorkbenchNotificationCenter notifications={notifications} onReload={onReloadNotifications} onOpenNotification={onOpenNotification} onSettings={onSettings} />
        <button type="button" className="workbench-icon-btn" onClick={onToggleTheme} title={themeTitle}>{themeIcon}</button>
        {voiceCommand.ready ? (
          <button
            type="button"
            className={"workbench-icon-btn workbench-voice-command-btn" + (voiceCommand.phase ? " " + voiceCommand.phase : "")}
            onClick={function () { WbVoiceCommand.start(); }}
            title={voiceCommand.phase === "recording"
              ? t("topbar.voiceCommandRecording", "Listening…")
              : voiceCommand.phase === "recognizing"
                ? t("topbar.voiceCommandRecognizing", "Recognizing…")
                : t("topbar.voiceCommand", "Voice command")}
            aria-label={t("topbar.voiceCommand", "Voice command")}
            aria-pressed={voiceCommand.phase === "recording"}
            disabled={!!voiceCommand.phase}
          >
            <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><rect x="9" y="3" width="6" height="11" rx="3"/><path d="M5.5 11.5a6.5 6.5 0 0 0 13 0M12 18v3M8.5 21h7"/></svg>
          </button>
        ) : null}
        <button type="button" data-cyrene-node-id="open_settings" className="workbench-icon-btn" onClick={function () { onSettings(); }} title={t("nav.settings")}>
          <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"><path d="M12.22 2h-.44a2 2 0 0 0-2 2v.18a2 2 0 0 1-1 1.73l-.43.25a2 2 0 0 1-2 0l-.15-.08a2 2 0 0 0-2.73.73l-.22.38a2 2 0 0 0 .73 2.73l.15.1a2 2 0 0 1 1 1.72v.51a2 2 0 0 1-1 1.74l-.15.09a2 2 0 0 0-.73 2.73l.22.38a2 2 0 0 0 2.73.73l.15-.08a2 2 0 0 1 2 0l.43.25a2 2 0 0 1 1 1.73V20a2 2 0 0 0 2 2h.44a2 2 0 0 0 2-2v-.18a2 2 0 0 1 1-1.73l.43-.25a2 2 0 0 1 2 0l.15.08a2 2 0 0 0 2.73-.73l.22-.39a2 2 0 0 0-.73-2.73l-.15-.08a2 2 0 0 1-1-1.74v-.5a2 2 0 0 1 1-1.74l.15-.09a2 2 0 0 0 .73-2.73l-.22-.38a2 2 0 0 0-2.73-.73l-.15.08a2 2 0 0 1-2 0l-.43-.25a2 2 0 0 1-1-1.73V4a2 2 0 0 0-2-2Z"/><circle cx="12" cy="12" r="3"/></svg>
        </button>
        <button type="button" className={"workbench-avatar-btn" + (activePage === "profile" ? " active" : "")} title={t("rail.profile")} onClick={function () { onOpenPage && onOpenPage("profile"); }}>
          {window.CyreneUI.require("profile").Avatar
            ? React.createElement(window.CyreneUI.require("profile").Avatar, { user: dataState.user, size: 30 })
            : <span className="workbench-avatar">{WorkbenchModel.initials(dataState.user && dataState.user.name)}</span>}
        </button>
      </div>
      {sessionMenuPortal}
      {overflowMenuPortal}
      {resourceMenuPortal}
      {hoverPreviewPortal}
    </div>
  );
}

function WorkbenchNotificationCenter({ notifications, onReload, onOpenNotification, onSettings }) {
  var { t } = window.CyreneUI.require("i18n").use();
  var model = window.CyreneUI.require("model");
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

  function openNotification(item) {
    if (!item) return;
    if (!item.read) markRead([item.id], false);
    if (onOpenNotification && onOpenNotification(item)) setOpen(false);
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
              return <WorkbenchNotificationItem key={item.id} item={item} onOpen={function () { openNotification(item); }} />;
            })}
          </div>
        </div>
      ) : null}
    </div>
  );
}

function WorkbenchNotificationItem({ item, onOpen }) {
  var { t } = window.CyreneUI.require("i18n").use();
  var target = wbNotificationNavigationTarget(item);
  var isUpdate = item && item.meta && item.meta.category === "app_update";
  var canNavigate = !!target || !!isUpdate || String(item && item.source || "") === "updater";
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
    <button
      type="button"
      className={"workbench-notif-item" + (item.read ? "" : " unread") + (canNavigate ? " navigable" : "")}
      onClick={onOpen}
      aria-label={canNavigate ? t("notifications.open", { title: item.title }) : item.title}
    >
      <span className={"workbench-notif-item-icon " + iconClass}>{icon}</span>
      <span className="workbench-notif-item-main">
        <span className="workbench-notif-item-top">
          <b>{item.title}</b>
          <time>{window.CyreneUI.require("model").formatRelativeTime(item.createdAt)}</time>
        </span>
        {item.body ? <span className="workbench-notif-item-body">{item.body}</span> : null}
        <span className="workbench-notif-item-footer">
          <span className="workbench-notif-item-meta">{item.linkLabel || item.sourceLabel || item.projectName || t("notifications.title")}</span>
          {canNavigate ? (
            <span className="workbench-notif-item-jump" aria-hidden="true">
              {t("notifications.view")}
              <svg viewBox="0 0 16 16" width="14" height="14" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"><path d="m6 3 5 5-5 5"/></svg>
            </span>
          ) : null}
        </span>
      </span>
    </button>
  );
}

function WorkbenchHelpCenter({ onNewProject, onNewTask, onOpenPage, onSettings }) {
  var { t } = window.CyreneUI.require("i18n").use();
  var dataState = window.CyreneUI.require("data").state;
  var [open, setOpen] = useWorkbenchState(false);
  var rootRef = useWorkbenchRef(null);
  var isMac = useWorkbenchMemo(wbIsMacPlatform, []);
  // Refresh the shortcut list every time the popover opens so it reflects any
  // rebinding done in Settings → Shortcuts. Mirror the module's glyph renderer
  // so the help center and the settings panel stay visually consistent.
  var shortcutList = useWorkbenchMemo(function () {
    var shortcuts = window.CyreneUI.require("shortcuts");
    var list = shortcuts.list();
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

  var version = dataState.appVersion || "1.0.0";

  return (
    <div className={"workbench-help-anchor" + (open ? " open" : "")} ref={rootRef}>
      <button type="button" className={"workbench-icon-btn" + (open ? " active" : "")} title={t("workbench.help")} aria-label={t("workbench.help")} aria-expanded={open} onClick={function () { setOpen(!open); }}>
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
  var { t } = window.CyreneUI.require("i18n").use();
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
      setError(wbErrorText(err));
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

function wbProjectMemoryDate(value) {
  if (!value) return "—";
  var parsed = new Date(value);
  return isNaN(parsed.getTime()) ? String(value) : parsed.toLocaleString();
}

function WorkbenchProjectMemoryModal({ project, onClose }) {
  var { t } = window.CyreneUI.require("i18n").use();
  var [payload, setPayload] = useWorkbenchState(null);
  var [draft, setDraft] = useWorkbenchState("");
  var [selectedModifiedAt, setSelectedModifiedAt] = useWorkbenchState("");
  var [loading, setLoading] = useWorkbenchState(true);
  var [busy, setBusy] = useWorkbenchState(false);
  var [error, setError] = useWorkbenchState("");
  var draftRef = useWorkbenchRef("");
  var payloadRef = useWorkbenchRef(null);
  var selectedModifiedAtRef = useWorkbenchRef("");
  draftRef.current = draft;
  payloadRef.current = payload;
  selectedModifiedAtRef.current = selectedModifiedAt;

  function load(options) {
    options = options || {};
    if (!options.background) setLoading(true);
    return window.CyreneUI.require("api").json(
      "/api/projects/" + encodeURIComponent(project.id) + "/memory-prompt?include_memories=false",
      { toast: false }
    ).then(function (next) {
      var previousPrompt = String(payloadRef.current && payloadRef.current.current && payloadRef.current.current.prompt || "");
      var hasLocalEdit = options.keepDraft && draftRef.current.trim() !== previousPrompt.trim();
      setPayload(next);
      if (!hasLocalEdit) setDraft(String(next && next.current && next.current.prompt || ""));
      var nextVersions = next && Array.isArray(next.versions) ? next.versions : [];
      if (selectedModifiedAtRef.current && !nextVersions.some(function (version) { return version.modifiedAt === selectedModifiedAtRef.current; })) {
        setSelectedModifiedAt("");
      }
      setError("");
      return next;
    }).catch(function (err) {
      setError(wbErrorText(err));
      return null;
    }).then(function (value) {
      if (!options.background) setLoading(false);
      return value;
    });
  }

  useWorkbenchEffect(function () { load(); }, [project.id]);
  var learningStatus = payload && payload.learningStatus;
  var learningPhase = String(learningStatus && learningStatus.status || "");
  useWorkbenchEffect(function () {
    if (learningPhase !== "queued" && learningPhase !== "running") return undefined;
    var timer = window.setInterval(function () { load({ background: true, keepDraft: true }); }, 2000);
    return function () { window.clearInterval(timer); };
  }, [project.id, learningPhase]);

  var current = payload && payload.current || { prompt: "", modifiedAt: "" };
  var versions = payload && Array.isArray(payload.versions) ? payload.versions : [];
  var historicalVersions = versions.filter(function (version) { return version.modifiedAt !== current.modifiedAt; });
  var selectedVersion = selectedModifiedAt
    ? versions.find(function (version) { return version.modifiedAt === selectedModifiedAt; }) || null
    : null;
  var displayedPrompt = selectedVersion ? String(selectedVersion.prompt || "") : draft;
  var displayedModel = selectedVersion && selectedVersion.model || {};
  var displayedTrigger = selectedVersion && selectedVersion.trigger || {};
  var displayedModifiedAt = selectedVersion ? selectedVersion.modifiedAt : current.modifiedAt;
  var displayedModifiedBy = selectedVersion ? selectedVersion.modifiedBy : current.modifiedBy;
  var promptChanged = draft.trim() !== String(current.prompt || "").trim();

  function savePrompt() {
    if (busy || !promptChanged) return;
    setBusy(true);
    setError("");
    window.CyreneUI.require("api").json(
      "/api/projects/" + encodeURIComponent(project.id) + "/memory-prompt",
      {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ prompt: draft, baseModifiedAt: current.modifiedAt || "" }),
        toast: false,
      }
    ).then(function (next) {
      setPayload(function (previous) { return { ...(previous || {}), ...next }; });
      setDraft(String(next && next.current && next.current.prompt || ""));
      setSelectedModifiedAt("");
      window.CyreneUI.require("feedback").showToast(t("projectMemory.saved"), "success");
    }).catch(function (err) {
      setError(wbErrorText(err));
      if (Number(err && err.status || 0) === 409) load({ keepDraft: true });
    }).then(function () { setBusy(false); });
  }

  function restoreVersion(version) {
    if (busy || !version) return;
    setBusy(true);
    setError("");
    window.CyreneUI.require("api").json(
      "/api/projects/" + encodeURIComponent(project.id) + "/memory-prompt/restore",
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ modifiedAt: version.modifiedAt, baseModifiedAt: current.modifiedAt || "" }),
        toast: false,
      }
    ).then(function (next) {
      setPayload(function (previous) { return { ...(previous || {}), ...next }; });
      setDraft(String(next && next.current && next.current.prompt || ""));
      setSelectedModifiedAt("");
      window.CyreneUI.require("feedback").showToast(t("projectMemory.restored"), "success");
    }).catch(function (err) {
      setError(wbErrorText(err));
      if (Number(err && err.status || 0) === 409) load({ keepDraft: true });
    }).then(function () { setBusy(false); });
  }

  return (
    <div className="workbench-modal-scrim workbench-project-memory-scrim" onMouseDown={function (event) { if (event.target === event.currentTarget && !busy) onClose(); }}>
      <div className="workbench-project-memory-modal" role="dialog" aria-modal="true" aria-label={t("projectMemory.title")}>
        <div className="workbench-project-edit-head workbench-project-memory-head">
          <span className="workbench-project-memory-title-copy">
            <b>{project.name}</b>
            <p>{selectedVersion ? t("projectMemory.historicalHint") : t("projectMemory.promptHint")}</p>
            {displayedModifiedAt ? <i>{wbProjectMemoryDate(displayedModifiedAt)} · {displayedModifiedBy === "memory_agent" ? t("projectMemory.byAgent") : t("projectMemory.byUser")}</i> : null}
          </span>
          <div className="workbench-project-memory-head-actions">
            {selectedVersion ? <button type="button" className="wb-btn primary" disabled={busy} onClick={function () { restoreVersion(selectedVersion); }}>{busy ? t("settings.saving") : t("projectMemory.restore")}</button> : <button type="button" className="wb-btn primary" disabled={busy || !promptChanged} onClick={savePrompt}>{busy ? t("settings.saving") : t("common.save")}</button>}
            <button type="button" className="workbench-icon-btn" disabled={busy} onClick={onClose} title={t("common.close")}>
              <svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round"><path d="m6 6 12 12M18 6 6 18" /></svg>
            </button>
          </div>
        </div>
        <div className="workbench-project-memory-overview">
          <span className="workbench-project-memory-overview-title">
            <svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><rect x="4" y="5" width="16" height="15" rx="2"/><path d="M8 3v4M16 3v4M8 11h8"/></svg>
            <span><b>{t("projectMemory.overview")}</b><em>{selectedVersion ? t("projectMemory.historyStatus") : current.modifiedAt ? t("projectMemory.currentStatus") : t("projectMemory.unsavedStatus")}</em></span>
          </span>
          <span className="workbench-project-memory-overview-metric"><small>{t("projectMemory.versionCount")}</small><b>{versions.length.toLocaleString()}</b></span>
          <span className="workbench-project-memory-overview-metric"><small>{t("projectMemory.characterCount")}</small><b>{displayedPrompt.length.toLocaleString()}</b></span>
          <div className="workbench-project-memory-head-version">
            <label htmlFor="workbench-project-memory-version">{t("projectMemory.versionSelector")}</label>
            <select id="workbench-project-memory-version" value={selectedModifiedAt} onChange={function (event) { setSelectedModifiedAt(event.target.value); }}>
              <option value="">{current.modifiedAt ? t("projectMemory.currentOption", { time: wbProjectMemoryDate(current.modifiedAt) }) : t("projectMemory.currentUnsavedOption")}</option>
              {historicalVersions.map(function (version) {
                return <option key={version.revisionId || version.modifiedAt} value={version.modifiedAt}>{t("projectMemory.historyOption", { time: wbProjectMemoryDate(version.modifiedAt) })}</option>;
              })}
            </select>
          </div>
        </div>
        <div className="workbench-project-memory-body">
          {loading ? <div className="workbench-project-memory-state"><span className="wbc-spinner" /> {t("common.loading")}</div> : null}
          {!loading ? (
            <section className="workbench-project-memory-prompt">
              <div className="workbench-project-memory-editor">
                {learningStatus ? <div className={"workbench-project-memory-learning-status " + learningPhase} title={learningStatus.error || ""}>{t("projectMemory.learningStatus." + learningPhase)}</div> : null}
                {selectedVersion ? <div className="workbench-project-memory-selected-version">
                  <b>{selectedVersion.changeSummary || t("projectMemory.versionChange")}</b>
                  <span>{selectedVersion.modifiedBy === "memory_agent" ? t("projectMemory.byAgent") : t("projectMemory.byUser")}</span>
                  {displayedModel.model ? <span>{t("projectMemory.model")}: {displayedModel.provider ? displayedModel.provider + " · " : ""}{displayedModel.model}{displayedModel.reasoningEffort ? " · " + displayedModel.reasoningEffort : ""}</span> : null}
                  {displayedTrigger.conversationId ? <span>{t("projectMemory.trigger")}: {displayedTrigger.conversationId}{displayedTrigger.roundId ? " · " + displayedTrigger.roundId : ""}{displayedTrigger.turn ? " · " + t("projectMemory.turn", { turn: displayedTrigger.turn }) : ""}</span> : null}
                  {selectedVersion.restoredFromModifiedAt ? <span>{t("projectMemory.restoredFrom", { time: wbProjectMemoryDate(selectedVersion.restoredFromModifiedAt) })}</span> : null}
                </div> : null}
                <div className="workbench-project-memory-editor-field">
                  <textarea className={selectedVersion ? "is-historical" : ""} value={displayedPrompt} readOnly={!!selectedVersion} maxLength={16000} onChange={function (event) { if (!selectedVersion) setDraft(event.target.value); }} placeholder={t("projectMemory.promptPlaceholder")} />
                  <div className="workbench-project-memory-count">{displayedPrompt.length.toLocaleString()} / 16,000</div>
                </div>
              </div>
            </section>
          ) : null}
          {error ? <div className="workbench-project-edit-error workbench-project-memory-inline-error">{error}</div> : null}
        </div>
      </div>
    </div>
  );
}

function WorkbenchSidebarCollapseControl({ collapsed, onToggle }) {
  var { t } = window.CyreneUI.require("i18n").use();
  var label = collapsed ? t("rail.expand", null, "Expand sidebar") : t("rail.collapse", null, "Collapse sidebar");
  return (
    <button
      type="button"
      className="workbench-sidebar-collapse-control"
      title={label}
      aria-label={label}
      aria-expanded={!collapsed}
      onClick={onToggle}
    >
      <svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
        <rect x="3" y="3" width="18" height="18" rx="2"/>
        <path d="M15 3v18"/>
        <path d={collapsed ? "m8 10 2 2-2 2" : "m9 10-2 2 2 2"}/>
      </svg>
    </button>
  );
}

function WorkbenchRailAccount({ activePage, onOpenPage, onSettings, docked }) {
  var { t } = window.CyreneUI.require("i18n").use();
  var dataStore = window.CyreneUI.require("data");
  dataStore.useVersion();
  var dataState = dataStore.state;
  var [accountMenuOpen, setAccountMenuOpen] = useWorkbenchState(false);
  var [budgetState, setBudgetState] = useWorkbenchState(null);
  var cachedCodexQuota = WorkbenchModel.readCodexQuotaCache();
  var [codexQuotaState, setCodexQuotaState] = useWorkbenchState({
    primary: false,
    connected: !!(cachedCodexQuota && cachedCodexQuota.connected),
    windows: cachedCodexQuota ? WorkbenchModel.codexQuotaWindows(cachedCodexQuota.limits) : [],
    plan: cachedCodexQuota ? WorkbenchModel.codexPlanLabel(cachedCodexQuota.account, cachedCodexQuota.limits) : "",
  });

  function fetchBudget() {
    fetch("/api/budget/status")
      .then(function (response) { return response.json(); })
      .then(function (payload) { setBudgetState(payload); })
      .catch(function () {});
  }

  function fetchCodexQuotaSummary() {
    return fetch("/api/settings/models")
      .then(function (response) { return response.json(); })
      .then(function (modelsPayload) {
        var primary = (modelsPayload.models || modelsPayload.primary_candidates || [])[0];
        if (!primary || primary.provider !== "codex_oauth") {
          setCodexQuotaState({ primary: false, connected: false, windows: [], plan: "" });
          return null;
        }
        var cached = WorkbenchModel.readCodexQuotaCache();
        if (cached) {
          setCodexQuotaState({
            primary: true,
            connected: cached.connected === true,
            windows: WorkbenchModel.codexQuotaWindows(cached.limits),
            plan: WorkbenchModel.codexPlanLabel(cached.account, cached.limits),
          });
        }
        return fetch("/api/settings/openai-oauth/limits")
          .then(function (response) { return response.json(); })
          .then(function (quotaPayload) {
            WorkbenchModel.writeCodexQuotaCache(quotaPayload);
            setCodexQuotaState({
              primary: true,
              connected: quotaPayload.connected === true,
              windows: WorkbenchModel.codexQuotaWindows(quotaPayload.limits),
              plan: WorkbenchModel.codexPlanLabel(quotaPayload.account, quotaPayload.limits),
            });
            return quotaPayload;
          });
      })
      .catch(function () {});
  }

  function formatTimeDiff(isoStr) {
    if (!isoStr) return "";
    var dt = new Date(isoStr);
    var now = new Date();
    if (dt - now <= 0) return "";
    var time = String(dt.getHours()).padStart(2, "0") + ":" + String(dt.getMinutes()).padStart(2, "0");
    if (dt.toDateString() === now.toDateString()) return time;
    var tomorrow = new Date(now);
    tomorrow.setDate(tomorrow.getDate() + 1);
    if (dt.toDateString() === tomorrow.toDateString()) return t("general.tomorrow", null, "Tomorrow") + " " + time;
    return (dt.getMonth() + 1) + "/" + dt.getDate() + " " + time;
  }

  function formatRefreshTime(isoStr) {
    var time = formatTimeDiff(isoStr);
    return time ? t("rail.refreshAt", { time: time }) : t("rail.budgetExhausted");
  }
  function currencySymbol(currency) { return currency === "CNY" ? "¥" : currency === "USD" ? "$" : currency === "EUR" ? "€" : currency === "GBP" ? "£" : currency || ""; }
  function formatBudgetAmount(value, currency) { return currencySymbol(currency) + Number(value || 0).toFixed(2); }
  function codexQuotaWindowName(windowData) {
    if (windowData.kind === "five_hour") return t("rail.budgetFiveHour");
    if (windowData.kind === "weekly") return t("rail.budgetWeekly");
    return windowData.label || t("settings.codexQuotaWindow");
  }
  function codexQuotaResetTime(windowData) {
    return windowData.resetsAt ? new Date(windowData.resetsAt * 1000).toLocaleString() : "—";
  }

  useWorkbenchEffect(function () {
    fetchBudget();
    fetchCodexQuotaSummary();
  }, []);
  useWorkbenchEffect(function () {
    if (!accountMenuOpen) return undefined;
    fetchBudget();
    fetchCodexQuotaSummary();
    function closeMenu(event) {
      if (event.key && event.key !== "Escape") return;
      if (!event.key && event.target && event.target.closest && event.target.closest(".workbench-rail-account")) return;
      setAccountMenuOpen(false);
    }
    document.addEventListener("mousedown", closeMenu);
    document.addEventListener("keydown", closeMenu);
    return function () {
      document.removeEventListener("mousedown", closeMenu);
      document.removeEventListener("keydown", closeMenu);
    };
  }, [accountMenuOpen]);
  useWorkbenchEffect(function () {
    function onBudgetSaved() { fetchBudget(); }
    try { window.addEventListener("budget-saved", onBudgetSaved); } catch (e) {}
    return function () { try { window.removeEventListener("budget-saved", onBudgetSaved); } catch (e) {} };
  }, []);

  var user = dataState.user || {};
  var modelLabel = (dataState.sessions && dataState.sessions[0] && dataState.sessions[0].model) || dataState.appVersion || "model";
  function openProfile() {
    setAccountMenuOpen(false);
    if (onOpenPage) onOpenPage("profile");
  }

  return (
    <div className={"workbench-rail-account" + (docked ? " is-docked" : "") + (activePage === "profile" ? " active" : "")}>
      <button
        type="button"
        className="workbench-rail-account-button"
        title={t("rail.profile")}
        aria-label={t("rail.profile")}
        aria-expanded={accountMenuOpen}
        onClick={function () { setAccountMenuOpen(function (open) { return !open; }); }}
      >
        <span className="workbench-account-avatar">
          {window.CyreneUI.require("profile").Avatar
            ? React.createElement(window.CyreneUI.require("profile").Avatar, { user: user, size: 34 })
            : <div className="workbench-avatar photo">{WorkbenchModel.initials(user.name)}</div>}
        </span>
        {docked && (
          <span className="workbench-rail-account-summary">
            <b>{user.name || "User"}</b>
            <small>{codexQuotaState.plan || modelLabel}</small>
          </span>
        )}
        {docked && <svg className="workbench-rail-account-chevron" viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="m9 6 6 6-6 6"/></svg>}
      </button>
      {accountMenuOpen && (
        <div className="workbench-account-menu workbench-module-account-menu" onClick={function (event) { event.stopPropagation(); }}>
          <button type="button" className="workbench-module-account-profile" onClick={openProfile}>
            <span className="workbench-account-avatar">
              {window.CyreneUI.require("profile").Avatar
                ? React.createElement(window.CyreneUI.require("profile").Avatar, { user: user, size: 38 })
                : <div className="workbench-avatar photo">{WorkbenchModel.initials(user.name)}</div>}
            </span>
            <span className="workbench-module-account-copy">
              <span><b>{user.name || "User"}</b>{codexQuotaState.plan && <em>{codexQuotaState.plan}</em>}</span>
              <small>{modelLabel}</small>
            </span>
          </button>
          <div className="wb-account-menu-divider"></div>
          {codexQuotaState.primary && codexQuotaState.connected && codexQuotaState.windows.length > 0 && (
            <>
              <div className="wb-account-menu-codex">
                <div className="wb-account-menu-codex-head">
                  <strong>{t("settings.codexQuota")}</strong>
                  <span>{t("settings.codexQuotaPlan", { plan: codexQuotaState.plan || "—" })}</span>
                </div>
                {codexQuotaState.windows.map(function (windowData) {
                  return (
                    <div className="wb-account-menu-codex-window" key={windowData.kind + "-" + windowData.durationMins}>
                      <div className="wb-account-menu-usage-row">
                        <span>{codexQuotaWindowName(windowData)}</span>
                        <span className={"wb-account-menu-usage-val" + (windowData.remainingPercent <= 0 ? " over" : "")}>
                          {t("settings.codexQuotaRemaining", { pct: windowData.remainingPercent })}
                        </span>
                      </div>
                      <div className="wb-budget-progress-bar">
                        <div className={"wb-budget-progress-fill" + (windowData.usedPercent >= 100 ? " over" : windowData.usedPercent >= 80 ? " high" : "")} style={{ width: Math.round(windowData.usedPercent) + "%" }} />
                      </div>
                      <small>{t("settings.codexQuotaResets", { time: codexQuotaResetTime(windowData) })}</small>
                    </div>
                  );
                })}
              </div>
              <div className="wb-account-menu-divider"></div>
            </>
          )}
          {budgetState && budgetState.monthly_budget > 0 && (
            <>
              <div className="wb-account-menu-usage">
                <div className="wb-account-menu-usage-row">
                  <span>{t("rail.budgetFiveHour")}</span>
                  <span className={"wb-account-menu-usage-val" + (budgetState.five_hour_remaining <= 0 ? " over" : "")}>
                    {budgetState.five_hour_remaining > 0
                      ? (budgetState.five_hour_remaining / budgetState.five_hour_budget * 100).toFixed(0) + "% · " + formatBudgetAmount(budgetState.five_hour_remaining, budgetState.currency) + " / " + formatBudgetAmount(budgetState.five_hour_budget, budgetState.currency)
                      : formatRefreshTime(budgetState.five_hour_next_refresh_at)}
                  </span>
                </div>
                <div className="wb-account-menu-usage-row">
                  <span>{t("rail.budgetWeekly")}</span>
                  <span className={"wb-account-menu-usage-val" + (budgetState.weekly_remaining <= 0 ? " over" : "")}>
                    {budgetState.weekly_remaining > 0
                      ? (budgetState.weekly_remaining / budgetState.weekly_budget * 100).toFixed(0) + "% · " + formatBudgetAmount(budgetState.weekly_remaining, budgetState.currency) + " / " + formatBudgetAmount(budgetState.weekly_budget, budgetState.currency)
                      : formatRefreshTime(budgetState.weekly_next_refresh_at)}
                  </span>
                </div>
              </div>
              {(budgetState.weekly_remaining <= 0 || budgetState.five_hour_remaining <= 0 || budgetState.monthly_remaining <= 0) && <div className="wb-account-menu-usage-blocked">{t("rail.budgetBlocked")}</div>}
              <div className="wb-account-menu-divider"></div>
            </>
          )}
          <button type="button" onClick={function () { setAccountMenuOpen(false); if (onSettings) onSettings(); }}>
            <svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round"><circle cx="12" cy="12" r="3"/><path d="M12 1v2M12 21v2M4.2 4.2l1.4 1.4M18.4 18.4l1.4 1.4M1 12h2M21 12h2M4.2 19.8l1.4-1.4M18.4 5.6l1.4-1.4"/></svg>
            {t("rail.settings")}
          </button>
          <button type="button" onClick={openProfile}>
            <svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round"><path d="M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2"/><circle cx="12" cy="7" r="4"/></svg>
            {t("rail.profile")}
          </button>
        </div>
      )}
    </div>
  );
}

function WorkbenchSidebarDock({ activePage, onOpenPage, onSettings, collapsed, persistent }) {
  var { t } = window.CyreneUI.require("i18n").use();
  var items = [
    { id: "schedule", label: t("workbench.page.schedule"), icon: (
      <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round"><rect x="3" y="4.5" width="18" height="17" rx="2.5"/><path d="M3 9.5h18M8 2.5v4M16 2.5v4"/></svg>
    ) },
    { id: "task", label: t("workbench.page.task"), icon: (
      <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round"><path d="M9 5H7a2 2 0 0 0-2 2v12a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V7a2 2 0 0 0-2-2h-2"/><rect x="9" y="3" width="6" height="4" rx="1.5"/><path d="M9 14 10.5 15.5 15 11"/></svg>
    ) },
    { id: "chat", label: t("workbench.page.chat"), icon: (
      <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round"><path d="M21 11.5a8.5 8.5 0 0 1-12.2 7.6L3 21l1.9-5.8A8.5 8.5 0 1 1 21 11.5Z"/></svg>
    ) },
    { id: "knowledge", label: t("workbench.page.knowledge"), icon: (
      <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round"><path d="M5 4.5A2.5 2.5 0 0 1 7.5 2H20v15H7.5A2.5 2.5 0 0 0 5 19.5Z"/><path d="M5 19.5A2.5 2.5 0 0 0 7.5 22H20"/></svg>
    ) },
    { id: "memory", label: t("workbench.page.memory"), icon: (
      <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round"><path d="M12 4 13.6 10.4 20 12 13.6 13.6 12 20 10.4 13.6 4 12 10.4 10.4Z"/></svg>
    ) },
  ];
  return (
    <div className={"workbench-sidebar-dock" + (persistent ? " is-persistent" : "") + (collapsed ? " is-collapsed" : "")}>
      <WorkbenchRailAccount docked={true} activePage={activePage} onOpenPage={onOpenPage} onSettings={onSettings} />
      <nav className="workbench-sidebar-dock-nav" aria-label={t("workbench.navigation", "Workbench navigation")}>
        {items.map(function (item) {
          var active = item.id === "task" ? !activePage : activePage === item.id;
          return (
            <button
              key={item.id}
              type="button"
              className={active ? "active" : ""}
              title={item.label}
              aria-label={item.label}
              aria-current={active ? "page" : undefined}
              onClick={function () { if (onOpenPage) onOpenPage(item.id); }}
            >
              <span aria-hidden="true">{item.icon}</span>
              <b>{item.label}</b>
            </button>
          );
        })}
      </nav>
    </div>
  );
}

function WorkbenchProfileRail({ collapsed, collapseControl, moduleDock }) {
  var { t } = window.CyreneUI.require("i18n").use();
  var [budgetState, setBudgetState] = useWorkbenchState(null);
  var cachedCodexQuota = WorkbenchModel.readCodexQuotaCache();
  var [codexQuotaState, setCodexQuotaState] = useWorkbenchState({
    connected: !!(cachedCodexQuota && cachedCodexQuota.connected),
    windows: cachedCodexQuota ? WorkbenchModel.codexQuotaWindows(cachedCodexQuota.limits) : [],
    plan: cachedCodexQuota ? WorkbenchModel.codexPlanLabel(cachedCodexQuota.account, cachedCodexQuota.limits) : "",
  });

  function fetchProfileBudget() {
    fetch("/api/budget/status")
      .then(function (response) { return response.json(); })
      .then(function (payload) { setBudgetState(payload); })
      .catch(function () {});
  }

  function fetchProfileCodexQuota() {
    fetch("/api/settings/openai-oauth/limits")
      .then(function (response) { return response.json(); })
      .then(function (payload) {
        WorkbenchModel.writeCodexQuotaCache(payload);
        setCodexQuotaState({
          connected: payload.connected === true,
          windows: WorkbenchModel.codexQuotaWindows(payload.limits),
          plan: WorkbenchModel.codexPlanLabel(payload.account, payload.limits),
        });
      })
      .catch(function () {});
  }

  useWorkbenchEffect(function () {
    fetchProfileBudget();
    fetchProfileCodexQuota();
    function onBudgetSaved() { fetchProfileBudget(); }
    function onCodexAuthChanged() { fetchProfileCodexQuota(); }
    try { window.addEventListener("budget-saved", onBudgetSaved); } catch (e) {}
    try { window.addEventListener("cyrene:codex-auth-changed", onCodexAuthChanged); } catch (e) {}
    return function () {
      try { window.removeEventListener("budget-saved", onBudgetSaved); } catch (e) {}
      try { window.removeEventListener("cyrene:codex-auth-changed", onCodexAuthChanged); } catch (e) {}
    };
  }, []);

  function currencySymbol(currency) {
    return currency === "CNY" ? "¥" : currency === "USD" ? "$" : currency === "EUR" ? "€" : currency === "GBP" ? "£" : currency || "";
  }

  function formatBudgetAmount(value) {
    return currencySymbol(budgetState && budgetState.currency) + Number(value || 0).toFixed(2);
  }

  var budgetEnabled = !!(budgetState && budgetState.monthly_budget > 0);
  var monthlyUsedPercent = budgetEnabled
    ? Math.max(0, Math.min(100, Number(budgetState.monthly_spent || 0) / Number(budgetState.monthly_budget) * 100))
    : 0;

  function codexQuotaWindowName(windowData) {
    if (windowData.kind === "five_hour") return t("rail.budgetFiveHour");
    if (windowData.kind === "weekly") return t("rail.budgetWeekly");
    return windowData.label || t("settings.codexQuotaWindow");
  }

  function codexQuotaResetTime(windowData) {
    return windowData.resetsAt ? new Date(windowData.resetsAt * 1000).toLocaleString() : "—";
  }

  return (
    <aside className={"workbench-profile-rail workbench-integrated-rail" + (collapsed ? " is-collapsed" : "")}>
      <header className="workbench-integrated-rail-head"><b>{t("rail.profile")}</b>{collapseControl}</header>
      <div className="workbench-profile-rail-spacer">
        <div className="workbench-profile-budget-stack" aria-live="polite">
          {codexQuotaState.connected && (
            <section className="workbench-profile-budget workbench-profile-codex-card">
              <div className="workbench-profile-budget-head">
                <span className="workbench-profile-budget-icon" aria-hidden="true">
                  <svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
                    <path d="M8.5 4.5 12 2.5l3.5 2 3.5 2v4l2 3.5-2 3.5-3.5 2L12 21.5l-3.5-2-3.5-2v-4L3 10l2-3.5Z"/>
                    <circle cx="12" cy="12" r="3.5"/>
                  </svg>
                </span>
                <b>{t("settings.codexQuota")}</b>
              </div>
              <div className="workbench-profile-codex-quota">
                <div className="workbench-profile-codex-head">
                  <strong>Codex</strong>
                  <small>{t("settings.codexQuotaPlan", { plan: codexQuotaState.plan || "—" })}</small>
                </div>
                {codexQuotaState.windows.length > 0 ? codexQuotaState.windows.map(function (windowData) {
                  return (
                    <div className="workbench-profile-codex-window" key={windowData.kind + "-" + windowData.durationMins}>
                      <div className="workbench-profile-codex-row">
                        <span>{codexQuotaWindowName(windowData)}</span>
                        <b>{t("settings.codexQuotaRemaining", { pct: windowData.remainingPercent })}</b>
                      </div>
                      <div className="workbench-profile-budget-progress" aria-hidden="true">
                        <span className={windowData.usedPercent >= 100 ? "over" : windowData.usedPercent >= 80 ? "high" : ""} style={{ width: windowData.usedPercent + "%" }} />
                      </div>
                      <small>{t("settings.codexQuotaResets", { time: codexQuotaResetTime(windowData) })}</small>
                    </div>
                  );
                }) : (
                  <div className="workbench-profile-codex-empty">{t("settings.codexQuotaUnavailable")}</div>
                )}
              </div>
            </section>
          )}
          <section className="workbench-profile-budget workbench-profile-currency-card">
            <div className="workbench-profile-budget-head">
              <span className="workbench-profile-budget-icon" aria-hidden="true">
                <svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
                  <rect x="3" y="5" width="18" height="14" rx="3"/>
                  <path d="M16 9h5v6h-5a3 3 0 0 1 0-6Z"/><path d="M7 5V3.5h10V5"/>
                </svg>
              </span>
              <b>{t("profile.apiBudgetTitle")}</b>
            </div>
            {!budgetState ? (
              <div className="workbench-profile-budget-empty">{t("common.loading")}</div>
            ) : !budgetEnabled ? (
              <div className="workbench-profile-budget-empty">{t("profile.budgetDisabled")}</div>
            ) : (
              <div className="workbench-profile-budget-content">
                <div className="workbench-profile-budget-monthly">
                  <span>{t("profile.budgetMonthlyRemaining")}</span>
                  <strong>{formatBudgetAmount(budgetState.monthly_remaining)}</strong>
                  <small>{t("profile.budgetOfLimit", { limit: formatBudgetAmount(budgetState.monthly_budget) })}</small>
                </div>
                <div className="workbench-profile-budget-progress" aria-hidden="true">
                  <span className={monthlyUsedPercent >= 100 ? "over" : monthlyUsedPercent >= 80 ? "high" : ""} style={{ width: monthlyUsedPercent + "%" }} />
                </div>
                <div className="workbench-profile-budget-rows">
                  <div><span>{t("profile.budgetWeeklyRemaining")}</span><b>{formatBudgetAmount(budgetState.weekly_remaining)}</b></div>
                  <div><span>{t("profile.budgetFiveHourRemaining")}</span><b>{formatBudgetAmount(budgetState.five_hour_remaining)}</b></div>
                </div>
              </div>
            )}
          </section>
        </div>
      </div>
      {moduleDock}
    </aside>
  );
}

// Temporarily keep sign-out unavailable until the authentication flow is ready.
var WB_ACCOUNT_LOGOUT_VISIBLE = false;

function ProjectRail({ projects, activeProjectId, activePage, collapsed, onToggleCollapse, onSelectProject, onCreateProject, onEditProject, onEditMemory, onDeleteProject, onOpenPage, onSettings }) {
  var { t } = window.CyreneUI.require("i18n").use();
  var dataStore = window.CyreneUI.require("data");
  dataStore.useVersion();
  var dataState = dataStore.state;
  var [menuProjectId, setMenuProjectId] = useWorkbenchState("");
  var [accountMenuOpen, setAccountMenuOpen] = useWorkbenchState(false);
  var [budgetState, setBudgetState] = useWorkbenchState(null);
  var cachedCodexQuota = WorkbenchModel.readCodexQuotaCache();
  var [codexQuotaState, setCodexQuotaState] = useWorkbenchState({
    primary: false,
    connected: !!(cachedCodexQuota && cachedCodexQuota.connected),
    windows: cachedCodexQuota
      ? WorkbenchModel.codexQuotaWindows(cachedCodexQuota.limits)
      : [],
    plan: cachedCodexQuota
      ? WorkbenchModel.codexPlanLabel(cachedCodexQuota.account, cachedCodexQuota.limits)
      : "",
  });

  // Fetch budget status from API (also pinged when the account menu opens)
  function fetchBudget() {
    fetch("/api/budget/status")
      .then(function (r) { return r.json(); })
      .then(function (d) { setBudgetState(d); })
      .catch(function () {});
  }

  function fetchCodexQuotaSummary() {
    return fetch("/api/settings/models")
      .then(function (r) { return r.json(); })
      .then(function (modelsPayload) {
        var primary = (modelsPayload.models || modelsPayload.primary_candidates || [])[0];
        if (!primary || primary.provider !== "codex_oauth") {
          setCodexQuotaState({ primary: false, connected: false, windows: [], plan: "" });
          return null;
        }
        var cached = WorkbenchModel.readCodexQuotaCache();
        if (cached) {
          setCodexQuotaState({
            primary: true,
            connected: cached.connected === true,
            windows: WorkbenchModel.codexQuotaWindows(cached.limits),
            plan: WorkbenchModel.codexPlanLabel(cached.account, cached.limits),
          });
        }
        return fetch("/api/settings/openai-oauth/limits")
          .then(function (r) { return r.json(); })
          .then(function (quotaPayload) {
            WorkbenchModel.writeCodexQuotaCache(quotaPayload);
            setCodexQuotaState({
              primary: true,
              connected: quotaPayload.connected === true,
              windows: WorkbenchModel.codexQuotaWindows(quotaPayload.limits),
              plan: WorkbenchModel.codexPlanLabel(quotaPayload.account, quotaPayload.limits),
            });
            return quotaPayload;
          });
      })
      .catch(function () {});
  }
  useWorkbenchEffect(function () { fetchBudget(); function onFocus() { fetchBudget(); } try { window.addEventListener("wb-focus-composer", onFocus); } catch (e) {} return function () { try { window.removeEventListener("wb-focus-composer", onFocus); } catch (e) {} }; }, []);
  useWorkbenchEffect(function () { fetchCodexQuotaSummary(); }, []);

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
  function codexQuotaWindowName(windowData) {
    if (windowData.kind === "five_hour") return t("rail.budgetFiveHour");
    if (windowData.kind === "weekly") return t("rail.budgetWeekly");
    return windowData.label || t("settings.codexQuotaWindow");
  }
  function codexQuotaResetTime(windowData) {
    if (!windowData.resetsAt) return "—";
    return new Date(windowData.resetsAt * 1000).toLocaleString();
  }
  // Re-fetch whenever the menu opens so the user sees fresh data
  useWorkbenchEffect(function () {
    if (accountMenuOpen) {
      fetchBudget();
      fetchCodexQuotaSummary();
    }
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
              onContextMenu={function (event) {
                event.preventDefault();
                event.stopPropagation();
                setAccountMenuOpen(false);
                setMenuProjectId(project.id);
              }}
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
                  <button type="button" onClick={function () { setMenuProjectId(""); if (onEditMemory) onEditMemory(project); }}>{t("rail.editMemory")}</button>
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
            {window.CyreneUI.require("profile").Avatar
              ? React.createElement(window.CyreneUI.require("profile").Avatar, { user: dataState.user, size: 34 })
              : <div className="workbench-avatar photo">{WorkbenchModel.initials(dataState.user && dataState.user.name)}</div>}
          </span>
          <div className="workbench-account-meta">
            <div className="workbench-account-name">
              <b>{dataState.user && dataState.user.name || "User"}</b>
              {codexQuotaState.primary && codexQuotaState.connected && codexQuotaState.plan && (
                <span className="workbench-pro-badge">{codexQuotaState.plan}</span>
              )}
            </div>
            <small>{(dataState.sessions && dataState.sessions[0] && dataState.sessions[0].model) || dataState.appVersion || "model"}</small>
          </div>
        </div>
        {accountMenuOpen && (
          <div className="workbench-account-menu" onClick={function (e) { e.stopPropagation(); }}>
            {codexQuotaState.primary && codexQuotaState.connected && codexQuotaState.windows.length > 0 && (
              <>
                <div className="wb-account-menu-codex">
                  <div className="wb-account-menu-codex-head">
                    <strong>{t("settings.codexQuota")}</strong>
                    <span>{t("settings.codexQuotaPlan", { plan: codexQuotaState.plan || "—" })}</span>
                  </div>
                  {codexQuotaState.windows.map(function (windowData) {
                    return (
                      <div className="wb-account-menu-codex-window" key={windowData.kind + "-" + windowData.durationMins}>
                        <div className="wb-account-menu-usage-row">
                          <span>{codexQuotaWindowName(windowData)}</span>
                          <span className={"wb-account-menu-usage-val" + (windowData.remainingPercent <= 0 ? " over" : "")}>
                            {t("settings.codexQuotaRemaining", { pct: windowData.remainingPercent })}
                          </span>
                        </div>
                        <div className="wb-budget-progress-bar">
                          <div
                            className={"wb-budget-progress-fill" + (windowData.usedPercent >= 100 ? " over" : windowData.usedPercent >= 80 ? " high" : "")}
                            style={{ width: Math.round(windowData.usedPercent) + "%" }}
                          />
                        </div>
                        <small>{t("settings.codexQuotaResets", { time: codexQuotaResetTime(windowData) })}</small>
                      </div>
                    );
                  })}
                </div>
                <div className="wb-account-menu-divider"></div>
              </>
            )}
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
            {WB_ACCOUNT_LOGOUT_VISIBLE && (
              <>
                <div className="wb-account-menu-divider"></div>
                <button type="button" className="danger" onClick={function () { setAccountMenuOpen(false); }}>
                  <svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round"><path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4"/><polyline points="16 17 21 12 16 7"/><line x1="21" y1="12" x2="9" y2="12"/></svg>
                  {t("rail.logout")}
                </button>
              </>
            )}
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
  var { t } = window.CyreneUI.require("i18n").use();
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
  var { t } = window.CyreneUI.require("i18n").use();
  var tone = WorkbenchModel.statusTone(session.status);
  var summary = sessionSummaryText(session);
  var stepCount = Number(session.planStepCount != null ? session.planStepCount : (Array.isArray(session.plan) ? session.plan.length : 0));
  return (
    <article
      role="button"
      tabIndex={0}
      className={"wb-board-card is-" + column + (menuOpen ? " menu-open" : "")}
      onClick={onOpen}
      onContextMenu={function (event) {
        event.preventDefault();
        event.stopPropagation();
        if (!menuOpen) onMenu();
      }}
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

function TaskRail({ project, activeSessionId, onSelectSession, onCreateSession, onDeleteSession, loading, collapsed, collapseControl, moduleDock }) {
  var { t } = window.CyreneUI.require("i18n").use();
  var sessions = project && Array.isArray(project.sessions) ? project.sessions : [];
  var [menuId, setMenuId] = useWorkbenchState("");

  return (
    <aside className={"workbench-task-rail workbench-integrated-rail" + (collapsed ? " is-collapsed" : "")}>
      <div className="workbench-rail-head workbench-integrated-rail-head">
        <span>{t("rail.tasks")}</span>
        <div className="workbench-integrated-rail-actions">
          <button type="button" className="workbench-integrated-rail-primary-action" onClick={onCreateSession} disabled={!project}>+ {t("rail.newTask")}</button>
          {collapseControl}
        </div>
      </div>
      {menuId && <div className="wb-card-menu-scrim" onClick={function () { setMenuId(""); }} />}
      <div className={"workbench-task-list workbench-integrated-rail-body" + (!loading && sessions.length === 0 ? " is-empty" : "")}>
        {loading && <div className="workbench-muted wb-task-rail-loading">{t("rail.loadingTasks")}</div>}
        {!loading && sessions.length === 0 && (
          <div className="wb-task-rail-empty">
            <span className="wb-task-rail-empty-icon" aria-hidden="true">
              <svg viewBox="0 0 24 24" width="22" height="22" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
                <rect x="5" y="4" width="14" height="17" rx="2.5"/>
                <path d="M9 4.5h6M9 9h6M9 13h6"/>
              </svg>
            </span>
            <b>{t("rail.noTasks")}</b>
            <p>{t("rail.emptyTasksHint", null, "Create your first task to start planning and execution.")}</p>
            <button type="button" onClick={onCreateSession} disabled={!project}>+ {t("rail.newTask")}</button>
          </div>
        )}
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
              onContextMenu={function (event) {
                event.preventDefault();
                event.stopPropagation();
                setMenuId(session.id);
              }}
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
      {moduleDock}
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
  model: <svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"><rect x="6" y="6" width="12" height="12" rx="3"/><circle cx="12" cy="12" r="2.5"/><path d="M9 2v4M15 2v4M9 18v4M15 18v4M2 9h4M2 15h4M18 9h4M18 15h4"/></svg>,
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
  chevronRight: <svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="m9 18 6-6-6-6"/></svg>,
  chevronDown: <svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="m6 9 6 6 6-6"/></svg>,
  chevronLeft: <svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="m15 18-6-6 6-6"/></svg>,
};

function wbT(key, fallback, params) {
  return window.CyreneUI.require("i18n").t(key, params, fallback);
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

var WB_REASONING_EFFORT_ORDER = ["low", "medium", "high", "xhigh", "max", "ultra"];

function wbSupportedReasoningEfforts(model) {
  var raw = model && (
    model.supportedReasoningEfforts
    || model.supported_reasoning_efforts
  );
  var efforts = (Array.isArray(raw) ? raw : []).map(function (option) {
    return String(
      option && (option.reasoningEffort || option.reasoning_effort)
      || option
      || ""
    ).trim().toLowerCase();
  }).filter(function (effort) {
    return WB_REASONING_EFFORT_ORDER.indexOf(effort) >= 0;
  });
  if (!efforts.length && model) efforts = ["low", "medium", "high"];
  return Array.from(new Set(efforts)).sort(function (a, b) {
    return WB_REASONING_EFFORT_ORDER.indexOf(a) - WB_REASONING_EFFORT_ORDER.indexOf(b);
  });
}

function wbFriendlyModelName(model, fallback) {
  var configuredName = String(model && model.name || "").trim();
  var modelId = String(model && model.model || fallback || "").trim();
  if (configuredName && configuredName !== modelId) return configuredName;
  if (!modelId) return configuredName;
  var words = modelId.replace(/^gpt-/i, "").split(/[-_]+/).filter(Boolean);
  return words.map(function (word) {
    if (/^\d/.test(word)) return word.toUpperCase();
    if (word.toLowerCase() === "deepseek") return "DeepSeek";
    return word.charAt(0).toUpperCase() + word.slice(1);
  }).join(" ");
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
  var model = window.CyreneUI.require("model");
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
  function fail(err) { window.CyreneUI.require("feedback").showToast((err && err.message) || String(err), "error"); }
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
    window.CyreneUI.require("feedback").showToast(wbT("task.plan.addAtLeastOneStep", "Add at least one step before approval or execution."), "warning");
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
          model: (runtime && runtime.model) || undefined,
          reasoningEffort: (runtime && runtime.reasoningEffort) || "",
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
        // /runs owns the durable run + step transition in one server-side write.
        // Never issue a second completion PATCH here: losing that request after
        // the tools already ran used to strand the task in `running` forever.
        var completedStep = stepById(s2.plan, stepId);
        if (!completedStep || !isDoneStepStatus(completedStep.status)) {
          throw new Error("服务端未能提交步骤完成状态，请刷新后重试。");
        }
        if (runtime && runtime.clearAttachments) runtime.clearAttachments();
        return next;
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
        model: (runtime && runtime.model) || undefined,
        reasoningEffort: (runtime && runtime.reasoningEffort) || "",
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
          var codes = window.CyreneUI.require("chat").budgetCodes || {};
          var i18nKey = "budget.error." + (codes[code] || "5h");
          window.CyreneUI.require("feedback").showToast(wbT(i18nKey, err.message || ""), "error");
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
      interruptedRef.current = false;
      setBusy(true);
      setAgentBusy({ kind: "answer", label: "正在继续…", startedAt: new Date().toISOString() });
      return model.answer(sid, qid, ans)
        .then(apply)
        .then(function (store) {
          if (store && store.continuePlanExecution) {
            return ctrl.executeAll({ continuing: true, baseSession: store.activeSession });
          }
          return store;
        })
        .catch(function (err) {
          if (interruptedRef.current || (err && err.name === "AbortError")) return null;
          fail(err);
          return null;
        })
        .finally(function () { setBusy(false); setAgentBusy(null); });
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
      return window.CyreneUI.require("feedback").confirmModal({
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
        model: (runtime && runtime.model) || undefined,
        reasoningEffort: (runtime && runtime.reasoningEffort) || "",
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
      return window.CyreneUI.require("feedback").confirmModal({ body: "确定取消这个任务吗？当前进度会被保留。", danger: true }).then(function (ok) {
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
  var model = window.CyreneUI.require("model");
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
      .catch(function (err) { setError(wbErrorText(err)); })
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
      .catch(function (err) { setError(wbErrorText(err)); })
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
  var model = window.CyreneUI.require("model");
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
      .catch(function (err) { setError(wbErrorText(err)); })
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
  var [configuredModels, setConfiguredModels] = useWorkbenchState([]);
  var [selectedModelId, setSelectedModelId] = useWorkbenchState("");
  var [reasoningEffort, setReasoningEffort] = useWorkbenchState("");
  var [goalLoopOpen, setGoalLoopOpen] = useWorkbenchState(false);
  var [goalLoopLimitsOpen, setGoalLoopLimitsOpen] = useWorkbenchState(false);
  var sid = session ? session.id : "";
  // Pending attachments belong to the task being composed — reset on switch.
  useWorkbenchEffect(function () { setAttachments([]); }, [sid]);
  useWorkbenchEffect(function () {
    var cancelled = false;
    window.CyreneUI.require("api").json("/api/settings/models", { toast: false })
      .then(function (payload) {
        var options = Array.isArray(payload.models) ? payload.models : [];
        function applyInitialModels(items) {
          if (cancelled) return;
          setConfiguredModels(items);
          var sessionSelection = String(
            session && (session.modelSelectionId || session.model) || ""
          ).trim();
          var selected = items.find(function (item) {
            return sessionSelection && [
              String(item.id || ""),
              String(item.model || ""),
              String(item.name || ""),
            ].indexOf(sessionSelection) >= 0;
          }) || items.find(function (item) {
            return String(item.id || "") === String(payload.active || "");
          }) || items[0];
          if (selected) {
            setSelectedModelId(String(selected.id || selected.model || ""));
            setReasoningEffort(String(
              session && session.reasoningEffort
              || selected.reasoning_effort
              || ""
            ).trim().toLowerCase());
          } else {
            setSelectedModelId("");
            setReasoningEffort("");
          }
        }
        // Render the picker as soon as the configured model list arrives.
        // Codex capability metadata is optional enrichment and must not delay UI.
        applyInitialModels(options);
        var needsCodexCatalog = options.some(function (item) {
          return String(item.provider || "") === "codex_oauth";
        });
        var catalogRequest = needsCodexCatalog
          ? window.CyreneUI.require("api").json("/api/settings/openai-oauth", { toast: false }).catch(function () { return {}; })
          : Promise.resolve({});
        return catalogRequest.then(function (catalog) {
          if (cancelled) return;
          var codexModels = Array.isArray(catalog.models) ? catalog.models : [];
          options = options.map(function (item) {
            if (String(item.provider || "") !== "codex_oauth") return item;
            var match = codexModels.find(function (entry) {
              var id = String(entry.model || entry.id || entry.slug || "").trim();
              return id === String(item.model || "").trim();
            });
            return match ? Object.assign({}, item, {
              supportedReasoningEfforts: match.supportedReasoningEfforts || match.supported_reasoning_efforts || [],
            }) : item;
          });
          setConfiguredModels(options);
        });
      })
      .catch(function () {
        if (!cancelled) setConfiguredModels([]);
      });
    return function () { cancelled = true; };
  }, [sid]);
  var controller = useTaskController(session, props.onRefresh, {
    attachments: attachments,
    mode: mode,
    model: selectedModelId,
    reasoningEffort: reasoningEffort,
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
  if (session.kind === "init" && window.CyreneUI.require("create").InitView) {
    return (
      <main className="workbench-main">
        {React.createElement(window.CyreneUI.require("create").InitView, {
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
        configuredModels={configuredModels}
        selectedModelId={selectedModelId}
        onSelectedModelIdChange={setSelectedModelId}
        reasoningEffort={reasoningEffort}
        onReasoningEffortChange={setReasoningEffort}
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
    window.CyreneUI.require("model").patchSession(session.id, { title: nextTitle })
      .then(function (next) {
        if (controller && controller.applyStore) controller.applyStore(next);
      })
      .catch(function (err) {
        window.CyreneUI.require("feedback").showToast((err && err.message) || String(err), "error");
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
            onClick={function () { status === "running" || session.agentBusy ? controller.interrupt() : controller.pause(); }}
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
  return window.CyreneUI.require("markdown").render(text, {
    fallback: "raw-breaks",
    errorFallback: "raw",
  });
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
  var isPermission = window.CyreneUI.require("model").isPermissionQuestionKind(kind);
  var permissionText = isPermission
    ? window.CyreneUI.require("i18n").permissionQuestionText(pq)
    : "";
  var treeOptions = isPermission && !options.length ? ["确认", "拒绝"] : options;
  var customState = useWorkbenchState("");
  var customText = customState[0], setCustomText = customState[1];
  var optionSignature = JSON.stringify(treeOptions);
  useWorkbenchEffect(function () {
    if (!pq.id || controller.busy || !window.CyreneUI.has("uiSurface")) return undefined;
    var uiSurface = window.CyreneUI.require("uiSurface");
    var risk = isPermission ? "R3" : "R2";
    var actions = treeOptions.map(function (_opt, index) {
      return {
        action_id: "answer_option_" + index,
        kind: "invoke",
        risk: risk,
        gesture_aliases: ["press"],
        input_schema: {},
      };
    });
    if (pq.allowCustom && !isPermission) {
      actions.push({
        action_id: "answer_custom",
        kind: "set_value",
        risk: "R2",
        gesture_aliases: ["text_input"],
        input_schema: { value: "text<=20000" },
      });
    }
    var handlers = {};
    treeOptions.forEach(function (opt, index) {
      handlers["answer_option_" + index] = function () {
        return Promise.resolve(controller.answer(pq.id, opt)).then(function () {
          return { question_id: String(pq.id), answered: true, option_index: index };
        });
      };
    });
    if (pq.allowCustom && !isPermission) {
      handlers.answer_custom = function (input) {
        var answer = String(input.value || "").trim();
        if (!answer) throw new Error("answer is empty");
        return Promise.resolve(controller.answer(pq.id, answer)).then(function () {
          return { question_id: String(pq.id), answered: true, custom: true };
        });
      };
    }
    return uiSurface.register({
      node_id: "task_question_" + String(pq.id).replace(/[^a-zA-Z0-9_-]/g, "").slice(0, 100),
      parent_id: "root",
      scope: "main",
      get_node: function () {
        if (controller.busy) return null;
        return {
          role: isPermission ? "approval" : "question",
          name: String(permissionText || pq.text || wbT("workbenchChat.questionFallback", "Agent needs your confirmation to continue.")),
          value_summary: treeOptions.length + " options",
          state: {
            session_id: String(session.id || ""),
            session_kind: "task",
            question_id: String(pq.id),
            question_kind: kind,
            permission: isPermission,
            allow_custom: !!pq.allowCustom && !isPermission,
          },
        };
      },
      actions: actions,
      handlers: handlers,
    });
  }, [session.id, pq.id, pq.allowCustom, kind, controller.busy, controller.answer, optionSignature, isPermission, permissionText]);
  function submitCustom() {
    var t = String(customText || "").trim();
    if (!t || controller.busy) return;
    setCustomText("");
    controller.answer(pq.id, t);
  }
  return (
    <WbCard tone="confirm" icon={ICONS.shield} title={isPermission ? wbT("workbenchChat.permissionTitle", "Authorization needed") : wbT("workbenchChat.questionTitle", "Confirmation needed")}>
      <AgentReplyBlock text={permissionText || pq.text || wbT("workbenchChat.questionFallback", "Agent needs your confirmation to continue.")} />
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
  var summary = window.CyreneUI.require("model").confirmSummary(session);
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
  var model = window.CyreneUI.require("model");
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
    window.CyreneUI.require("feedback").confirmModal({
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
      window.CyreneUI.require("feedback").showToast(wbT("task.plan.invalidOrder", "This move would place a step before one of its prerequisites."), "warning");
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
function TaskComposer({
  session,
  controller,
  onRightTab,
  attachments,
  onAttachmentsChange,
  mode,
  onModeChange,
  configuredModels,
  selectedModelId,
  onSelectedModelIdChange,
  reasoningEffort,
  onReasoningEffortChange,
}) {
  var model = window.CyreneUI.require("model");
  var [draft, setDraft] = useWorkbenchState("");
  var [scopePrompt, setScopePrompt] = useWorkbenchState(null);
  var [modeOpen, setModeOpen] = useWorkbenchState(false);
  var [modelOpen, setModelOpen] = useWorkbenchState(false);
  var [modelPanel, setModelPanel] = useWorkbenchState("root");
  var [uploading, setUploading] = useWorkbenchState(false);
  var [voiceSnapshot, setVoiceSnapshot] = useWorkbenchState({ status: {}, activeKey: "" });
  var [voicePhase, setVoicePhase] = useWorkbenchState("");
  var taRef = useWorkbenchRef(null);
  var draftRef = useWorkbenchRef(draft);
  var fileRef = useWorkbenchRef(null);
  var modelPickerRef = useWorkbenchRef(null);
  var uploadCountRef = useWorkbenchRef(0);
  var voiceRecorderRef = useWorkbenchRef(null);
  var voiceSessionIdRef = useWorkbenchRef(String(session.id || ""));
  var voiceFeedbackRef = useWorkbenchRef(null);
  var ComposerBrowserIcon = window.CyreneUI.require("browser").Icon;
  if (!voiceFeedbackRef.current) voiceFeedbackRef.current = wbcCreateComposerVoiceFeedback();
  var status = String(session.status || "idle");
  var running = status === "running";
  // No plan yet → the composer is a free chat: every send goes through the
  // intent-aware dispatch so the agent itself decides whether to answer, act, or
  // draft a plan. Once a plan exists, the composer refines that plan instead.
  var hasPlan = Array.isArray(session.plan) && session.plan.length > 0;
  attachments = attachments || [];

  useWorkbenchEffect(function () { draftRef.current = draft; }, [draft]);

  useWorkbenchEffect(function () {
    return WbcVoice.subscribe(setVoiceSnapshot);
  }, []);

  useWorkbenchEffect(function () {
    voiceSessionIdRef.current = String(session.id || "");
    setVoicePhase("");
    return function () {
      var recorder = voiceRecorderRef.current;
      voiceRecorderRef.current = null;
      voiceFeedbackRef.current.dismiss();
      if (recorder && typeof recorder.stop === "function") recorder.stop().catch(function () {});
    };
  }, [session.id]);

  useWorkbenchEffect(function () {
    function onFocus() { if (taRef.current) taRef.current.focus(); }
    window.addEventListener("wb-focus-composer", onFocus);
    return function () { window.removeEventListener("wb-focus-composer", onFocus); };
  }, []);

  useWorkbenchEffect(function () {
    if (!modelOpen) return undefined;
    wbSetBrowserOverlayObscured(1);
    return function () { wbSetBrowserOverlayObscured(-1); };
  }, [modelOpen]);

  // Reset transient composer state when switching tasks.
  useWorkbenchEffect(function () {
    setScopePrompt(null);
    setModeOpen(false);
    setModelOpen(false);
    setModelPanel("root");
  }, [session.id]);

  useWorkbenchEffect(function () {
    if (!modelOpen) return undefined;
    function closeModelPicker(event) {
      if (modelPickerRef.current && !modelPickerRef.current.contains(event.target)) {
        setModelOpen(false);
        setModelPanel("root");
      }
    }
    document.addEventListener("pointerdown", closeModelPicker);
    return function () { document.removeEventListener("pointerdown", closeModelPicker); };
  }, [modelOpen]);

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

  function submit(overrideText) {
    if (running) { controller.interrupt(); return; }
    var text = typeof overrideText === "string" ? overrideText.trim() : draft.trim();
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
    var sc = window.CyreneUI.require("shortcuts");
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
    if (event.key === "Escape") {
      setModeOpen(false);
      setModelOpen(false);
      setModelPanel("root");
    }
  }

  function pickFiles() { if (fileRef.current) fileRef.current.click(); }

  function showVoiceError(error) {
    voiceFeedbackRef.current.error(error);
  }

  function transcribeVoiceBlob(blob) {
    return wbcTranscribeVoiceBlob(blob).then(function (transcript) {
      if (transcript === false) {
        voiceFeedbackRef.current.noSpeech();
        return false;
      }
      var current = String(draftRef.current || "");
      var combined = current && !/\s$/.test(current) ? current + " " + transcript : current + transcript;
      setDraft(combined);
      draftRef.current = combined;
      voiceFeedbackRef.current.complete();
      if (voiceSnapshot.status.auto_send_after_asr === true) {
        submit(combined);
        return true;
      }
      requestAnimationFrame(function () {
        syncHeight();
        if (taRef.current) taRef.current.focus();
      });
      return true;
    });
  }

  function finishVoiceInput(recorder) {
    if (!recorder || voiceRecorderRef.current !== recorder) return;
    voiceRecorderRef.current = null;
    setVoicePhase("transcribing");
    voiceFeedbackRef.current.transcribing();
    recorder.stop()
      .then(transcribeVoiceBlob)
      .catch(showVoiceError)
      .finally(function () { setVoicePhase(""); });
  }

  function toggleVoiceInput() {
    if (disabled || voicePhase === "starting" || voicePhase === "transcribing") return;
    if (voicePhase === "recording") {
      var recorder = voiceRecorderRef.current;
      if (!recorder) {
        setVoicePhase("");
        return;
      }
      finishVoiceInput(recorder);
      return;
    }
    WbcVoice.stop();
    setVoicePhase("starting");
    voiceFeedbackRef.current.starting();
    var startedForSession = String(session.id || "");
    wbcStartVoiceRecorder({
      autoStopOnSilence: voiceSnapshot.status.auto_stop_on_silence !== false,
      onSilence: finishVoiceInput,
    })
      .then(function (recorder) {
        if (voiceSessionIdRef.current !== startedForSession) {
          recorder.stop().catch(function () {});
          return;
        }
        voiceRecorderRef.current = recorder;
        setVoicePhase("recording");
        voiceFeedbackRef.current.listening();
      })
      .catch(function (error) {
        setVoicePhase("");
        showVoiceError(error);
      });
  }

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
      .catch(function (err) { window.CyreneUI.require("feedback").showToast(wbT("workbenchChat.uploadFailed", "Upload failed: {error}", { error: err.message || String(err) }), "error"); })
      .finally(function () {
        uploadCountRef.current = Math.max(0, uploadCountRef.current - 1);
        if (uploadCountRef.current === 0) setUploading(false);
        if (fileRef.current) fileRef.current.value = "";
      });
  }
  function onFilePick(event) {
    addFiles(event.target.files);
  }
  function onPaste(event) {
    if (running || controller.busy) return;
    var clipboard = event && (event.clipboardData || (event.nativeEvent && event.nativeEvent.clipboardData));
    if (!clipboard) return;
    var files = Array.prototype.slice.call(clipboard.files || []).filter(function (file) { return !!file; });
    // Some WebViews expose pasted files only through DataTransferItemList.
    if (!files.length) {
      files = Array.prototype.slice.call(clipboard.items || []).map(function (item) {
        return item && item.kind === "file" ? item.getAsFile() : null;
      }).filter(function (file) { return !!file; });
    }
    if (!files.length) return; // Preserve the browser's normal text paste.
    event.preventDefault();
    addFiles(files);
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
  configuredModels = Array.isArray(configuredModels) ? configuredModels : [];
  selectedModelId = String(selectedModelId || "");
  var selectedModel = configuredModels.find(function (item) {
    return String(item.id || item.model || "") === selectedModelId;
  });
  var modelName = wbFriendlyModelName(
    selectedModel,
    session && (session.model || session.lastModel) || ""
  );
  reasoningEffort = String(reasoningEffort || "").trim().toLowerCase();
  var effortLabel = reasoningEffort
    ? wbT("settings.reasoningEffortValue." + reasoningEffort, reasoningEffort)
    : "";
  var modelButtonLabel = wbT("workbenchChat.chooseModel", "Choose model")
    + ": " + modelName + (effortLabel ? " · " + effortLabel : "");
  var supportedReasoningEfforts = wbSupportedReasoningEfforts(selectedModel);
  var sendDisabled = running ? false : (disabled || (!draft.trim() && attachments.length === 0));

  useWorkbenchEffect(function () {
    if (!window.CyreneUI.has("uiSurface")) return undefined;
    var uiSurface = window.CyreneUI.require("uiSurface");
    return uiSurface.register({
      node_id: "task_composer_input",
      parent_id: "root",
      scope: "main",
      get_node: function () {
        if (disabled || awaitingAnswer) return null;
        var currentDraft = String(draftRef.current || "");
        return {
          role: "textbox",
          name: composerPlaceholder(status),
          value_summary: currentDraft ? "Draft present" : "Empty draft",
          state: {
            session_id: String(session.id || ""),
            session_kind: "task",
            draft_empty: !currentDraft,
            draft_length: currentDraft.length,
            running: running === true,
            submit_exposed: false,
          },
        };
      },
      actions: [{
        action_id: "set_value",
        kind: "set_value",
        risk: "R1",
        gesture_aliases: ["text_input"],
        input_schema: { value: "text<=20000" },
      }, {
        action_id: "clear_value",
        kind: "set_value",
        risk: "R1",
        gesture_aliases: ["semantic_clear"],
        input_schema: { expected_value: "text<=20000" },
      }],
      handlers: {
        set_value: function (input) {
          var currentDraft = String(draftRef.current || "");
          var nextDraft = String(input.value || "");
          if (currentDraft && currentDraft !== nextDraft) {
            throw new Error("composer draft is not empty");
          }
          draftRef.current = nextDraft;
          setDraft(nextDraft);
          return { draft_length: nextDraft.length, submitted: false };
        },
        clear_value: function (input) {
          var currentDraft = String(draftRef.current || "");
          if (currentDraft !== String(input.expected_value || "")) {
            throw new Error("composer draft changed");
          }
          draftRef.current = "";
          setDraft("");
          return { draft_length: 0, cleared: true, submitted: false };
        },
      },
    });
  }, [session.id, status, disabled, awaitingAnswer]);

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
          onPaste={onPaste}
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
            <button type="button" className={"wb-composer-icon mode" + (modeOpen ? " active" : "")} title={wbT("workbenchChat.permissionMode", "Permission mode")} onClick={function () {
              setModeOpen(!modeOpen);
              setModelOpen(false);
              setModelPanel("root");
            }}>
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
          {modelName ? (
            <span className="wbc-pop-anchor wbc-model-anchor" ref={modelPickerRef}>
              <button
                type="button"
                className={"wbc-model-button" + (modelOpen ? " active" : "")}
                title={modelButtonLabel}
                aria-label={modelButtonLabel}
                aria-haspopup="menu"
                aria-expanded={modelOpen}
                disabled={running}
                onClick={function () {
                  setModelOpen(!modelOpen);
                  setModelPanel("root");
                  setModeOpen(false);
                }}
              >
                <span className="wbc-model-button-icon" aria-hidden="true">{ICONS.model}</span>
                <span className="wbc-model-button-name">{modelName}</span>
                {effortLabel ? <span className="wbc-model-button-effort">{effortLabel}</span> : null}
                <span className="wbc-model-button-chevron">{ICONS.chevronDown}</span>
              </button>
              {modelOpen && (
                <div className="wbc-popmenu wbc-model-menu" role="menu">
                  {modelPanel === "root" && (
                    <>
                      <button type="button" className="wbc-model-menu-row" onClick={function () { setModelPanel("models"); }}>
                        <span className="wbc-model-menu-key">{wbT("workbenchChat.model", "Model")}</span>
                        <span className="wbc-model-menu-value wbc-model-menu-model-name">{modelName}</span>
                        <span className="wbc-model-menu-chevron">{ICONS.chevronRight}</span>
                      </button>
                      {supportedReasoningEfforts.length > 0 && (
                        <button type="button" className="wbc-model-menu-row" onClick={function () { setModelPanel("effort"); }}>
                          <span className="wbc-model-menu-key">{wbT("workbenchChat.reasoningEffort", "Reasoning effort")}</span>
                          <span className="wbc-model-menu-value">{effortLabel || "—"}</span>
                          <span className="wbc-model-menu-chevron">{ICONS.chevronRight}</span>
                        </button>
                      )}
                    </>
                  )}
                  {modelPanel === "models" && (
                    <>
                      <button type="button" className="wbc-model-menu-back" onClick={function () { setModelPanel("root"); }}>
                        <span>{ICONS.chevronLeft}</span>
                        <span>{wbT("workbenchChat.model", "Model")}</span>
                      </button>
                      {configuredModels.map(function (item) {
                        var id = String(item.id || item.model || "");
                        var active = id === selectedModelId;
                        return (
                          <button key={id} type="button" className={active ? "active" : ""} onClick={function () {
                            onSelectedModelIdChange(id);
                            onReasoningEffortChange(String(item.reasoning_effort || "").trim().toLowerCase());
                            setModelPanel("root");
                          }}>
                            <span className="wbc-popmenu-label">{item.name || item.model}</span>
                            {item.desc ? <span className="wbc-popmenu-desc">{item.desc}</span> : null}
                            {active ? <span className="wbc-popmenu-check">{ICONS.checkSmall}</span> : null}
                          </button>
                        );
                      })}
                    </>
                  )}
                  {modelPanel === "effort" && (
                    <>
                      <button type="button" className="wbc-model-menu-back" onClick={function () { setModelPanel("root"); }}>
                        <span>{ICONS.chevronLeft}</span>
                        <span>{wbT("workbenchChat.reasoningEffort", "Reasoning effort")}</span>
                      </button>
                      {supportedReasoningEfforts.map(function (effort) {
                        var active = effort === reasoningEffort;
                        return (
                          <button key={effort} type="button" className={active ? "active" : ""} onClick={function () {
                            onReasoningEffortChange(effort);
                            setModelPanel("root");
                          }}>
                            <span className="wbc-popmenu-label">{wbT("settings.reasoningEffortValue." + effort, effort)}</span>
                            {active ? <span className="wbc-popmenu-check">{ICONS.checkSmall}</span> : null}
                          </button>
                        );
                      })}
                    </>
                  )}
                </div>
              )}
            </span>
          ) : null}
          {voiceSnapshot.status.asr_ready ? (
            <button
              type="button"
              className={"wb-composer-icon wbc-voice-input" + (voicePhase ? " " + voicePhase : "")}
              onClick={toggleVoiceInput}
              disabled={disabled || voicePhase === "starting" || voicePhase === "transcribing"}
              title={voicePhase === "recording"
                ? (voiceSnapshot.status.auto_stop_on_silence !== false
                    ? wbT("workbenchChat.voiceInputAutoStop", "Recording · pauses automatically start recognition")
                    : wbT("workbenchChat.voiceInputStop", "Stop recording"))
                : voicePhase === "starting"
                  ? wbT("workbenchChat.voiceInputStarting", "Accessing microphone…")
                  : voicePhase === "transcribing"
                  ? wbT("workbenchChat.voiceTranscribing", "Recognizing speech…")
                  : wbT("workbenchChat.voiceInputStart", "Voice input")}
              aria-label={voicePhase === "recording"
                ? wbT("workbenchChat.voiceInputStop", "Stop recording")
                : wbT("workbenchChat.voiceInputStart", "Voice input")}
              aria-pressed={voicePhase === "recording"}
              aria-busy={voicePhase === "starting" || voicePhase === "transcribing"}
            >
              {voicePhase === "starting" || voicePhase === "transcribing"
                ? <span className="wb-spinner small" />
                : ComposerBrowserIcon ? <ComposerBrowserIcon name="microphone" size={16} /> : null}
            </button>
          ) : null}
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
  var t = window.CyreneUI.require("i18n").use().t;
  return <div className="wb-composer-disclaimer">{t("workbench.composerDisclaimer")}</div>;
}

function RightContextPanel({ project, session, expandedStepId, tab, onTabChange, onRefresh }) {
  var activeBodyRef = useWorkbenchRef(null);
  var steps = session && Array.isArray(session.plan) ? session.plan : [];
  var artifacts = WorkbenchModel.ensureArtifacts(session);
  var activeStep = steps.find(function (step) { return step.id === expandedStepId; }) || null;
  var isInit = !!(session && session.kind === "init");
  var tabs = isInit ? [
    { id: "context", label: wbT("task.side.context", "Context") },
  ] : [
    { id: "context", label: wbT("task.side.context", "Context") },
    { id: "files", label: wbT("task.side.fileChanges", "File changes") },
    { id: "logs", label: wbT("task.side.runLogs", "Run logs") },
    { id: "acceptance", label: wbT("task.side.acceptance", "Acceptance") },
  ].concat(artifacts.length ? [{ id: "artifacts", label: wbT("workbenchChat.artifacts", "Artifacts") }] : []);
  useWorkbenchEffect(function () {
    if (activeBodyRef.current) activeBodyRef.current.scrollTop = 0;
  }, [tab, session && session.id]);
  useWorkbenchEffect(function () {
    if (tab === "artifacts" && !artifacts.length) onTabChange("acceptance");
  }, [tab, artifacts.length]);
  if (!session) {
    return (
      <aside className="workbench-right-panel wb-floating-detail-shell wb-task-detail-shell">
        <div className="wb-floating-detail-card wb-task-detail-card empty">
          <WbColResizer cardEdge />
          <div className="wb-detail-empty-state">
            {ICONS.target}
            <p>{wbT("task.noTaskSelected", "Select a task.")}</p>
          </div>
        </div>
      </aside>
    );
  }
  var tabIcons = {
    context: ICONS.target,
    files: ICONS.attach,
    logs: ICONS.cmdReflect,
    acceptance: ICONS.check,
    artifacts: ICONS.cmdCode,
  };
  function tabBody(id) {
    if (id === "context") return <ContextTab project={project} session={session} activeStep={activeStep} />;
    if (id === "files") return <FilesTab session={session} activeStep={activeStep} />;
    if (id === "logs") return <LogsTab session={session} />;
    if (id === "acceptance") return <AcceptanceTab session={session} onRefresh={onRefresh} />;
    if (id === "artifacts") return <ArtifactsTab session={session} />;
    return null;
  }
  return (
    <aside className="workbench-right-panel wb-floating-detail-shell wb-task-detail-shell" aria-label={wbT("task.side.detailPanel", "Task details")}>
      <div className="wb-floating-detail-card wb-task-detail-card">
        <WbColResizer cardEdge />
        <nav className="wb-detail-accordion wb-task-detail-tabs" aria-label={wbT("task.side.detailPanel", "Task details")}>
          <div className="wb-detail-accordion-head wb-task-detail-head">
            <span>{wbT("task.side.detailPanel", "Task details")}</span>
          </div>
          <div className="wb-detail-accordion-list wb-task-detail-tab-list">
            {tabs.map(function (item) {
              var expanded = tab === item.id;
              var panelId = "wb-task-detail-panel-" + item.id;
              return (
                <React.Fragment key={item.id}>
                  <button
                    type="button"
                    className={"wb-detail-accordion-trigger wb-task-detail-tab" + (expanded ? " active" : "")}
                    aria-expanded={expanded}
                    aria-controls={panelId}
                    onClick={function () { onTabChange(expanded ? "" : item.id); }}
                  >
                    <span className="wb-detail-accordion-icon wb-task-detail-tab-icon" aria-hidden="true">{tabIcons[item.id]}</span>
                    <span>{item.label}</span>
                    {ICONS.chevronRight}
                  </button>
                  <div
                    id={panelId}
                    className={"wb-detail-accordion-panel wb-task-detail-tab-panel" + (expanded ? " open" : "")}
                    aria-hidden={!expanded}
                  >
                    <div className="wb-detail-accordion-panel-inner">
                      <div ref={expanded ? activeBodyRef : null} className="workbench-right-body">{tabBody(item.id)}</div>
                    </div>
                  </div>
                </React.Fragment>
              );
            })}
          </div>
        </nav>
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
    <SideSection title={wbT("task.reflection.title", "Deep reflection")} className="wb-task-context-reflection">
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
  if (isInit && window.CyreneUI.require("create").InitProgress) {
    return (
      <div className="workbench-side-stack">
        <SideSection title={wbT("init.progress.title", "Initialization progress")}>
          {React.createElement(window.CyreneUI.require("create").InitProgress, { session: session })}
        </SideSection>
      </div>
    );
  }
  return (
    <div className="workbench-side-stack wb-task-context-tab">
      <SideSection title={wbT("task.side.overview", "Task overview")} className="wb-task-context-overview">
        <div className="wb-task-overview-meta">
          <div className="wb-kv"><span>{wbT("workbenchChat.statusLabel", "Status")}</span><b>{WorkbenchModel.statusText(session.status)}</b></div>
          {!isInit && <div className="wb-kv"><span>{wbT("create.task.priority", "Priority")}</span><b>{priorityText(session.priority)}</b></div>}
        </div>
        <p className="wb-task-context-goal">{wbRealGoal(session) || wbT("task.noGoal", "No task goal yet")}</p>
      </SideSection>
      <ReflectionSection session={session} />
      <SideSection title={wbT("task.side.projectContext", "Project context")} className="wb-task-context-project">
        {project && project.context && project.context.summary && !isInit && <div className="wb-agent-body markdown" dangerouslySetInnerHTML={{ __html: wbRenderMarkdown(project.context.summary) }} />}
      </SideSection>
      <SideSection title={wbT("task.side.constraintsCount", "Constraints ({count})", { count: constraints.length })} className="wb-task-context-constraints">
        {constraints.length
          ? constraints.map(function (item, i) { return <div className="workbench-check wb-constraint-row" key={i}><span className="workbench-status-dot amber"></span><span className="wb-constraint-text">{item}</span></div>; })
          : <p className="workbench-muted wb-task-context-empty">{wbT("task.noConstraints", "No constraints yet. Phrases like \"do not\" or \"only\" in the task are recognized as constraints automatically.")}</p>}
      </SideSection>
      {isInit && window.CyreneUI.require("create").InitProgress ? (
        <SideSection title={wbT("init.progress.title", "Initialization progress")}>
          {React.createElement(window.CyreneUI.require("create").InitProgress, { session: session })}
        </SideSection>
      ) : (
        <SideSection title={wbT("task.side.taskRelations", "Task relations")} className="wb-task-context-relations">
          {parentSession ? (
            <div className="wb-brief-row">
              <label>{wbT("task.followUpSource", "Source task")}</label>
              <p>{parentSession.title || wbT("task.thisTask", "this task")}</p>
            </div>
          ) : (
            <p className="workbench-muted wb-task-context-empty">{wbT("task.noDependencies", "No dependent tasks yet.")}</p>
          )}
        </SideSection>
      )}
      {!isInit && (
        <SideSection title={wbT("task.side.stepDependencies", "Step dependencies")} className="wb-task-context-dependencies">
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
            <p className="workbench-muted wb-task-context-empty">
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
    <div className="workbench-side-stack wb-task-tab-content">
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
                ) : window.CyreneUI.require("diff").Panel ? (
                  <div className="wb-file-diff-panel">
                    {React.createElement(window.CyreneUI.require("diff").Panel, { diff: diffState.diff, mode: "text" })}
                  </div>
                ) : (
                  <pre className="wb-file-diff-fallback">{diffState.diff}</pre>
                )}
              </div>
            )}
          </div>
        );
      }) : <p className="workbench-muted">{wbT("task.files.empty", "No file changes recorded for this task yet.")}</p>}
    </div>
  );
}

function LogsTab({ session }) {
  var events = session && Array.isArray(session.events) ? session.events : [];
  return (
    <div className="workbench-side-stack wb-task-tab-content">
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
    </div>
  );
}

function AcceptanceTab({ session, onRefresh }) {
  var [busy, setBusy] = useWorkbenchState(false);
  var items = session && Array.isArray(session.acceptanceCriteria) ? session.acceptanceCriteria : [];
  var passedCount = items.filter(function (item) { return item.status === "passed" || item.status === "done"; }).length;
  var failedCount = items.filter(function (item) { return item.status === "failed"; }).length;
  var acceptanceFailure = hasAcceptanceFailure(session);
  var [editing, setEditing] = useWorkbenchState(acceptanceFailure);
  var [draft, setDraft] = useWorkbenchState(items.map(function (item) { return String((item && item.text) || ""); }));
  useWorkbenchEffect(function () {
    setDraft(items.map(function (item) { return String((item && item.text) || ""); }));
    if (acceptanceFailure) setEditing(true);
  }, [session && session.id, JSON.stringify(items.map(function (item) { return [item && item.id, item && item.text, item && item.status]; }))]);
  function generate() {
    setBusy(true);
    window.CyreneUI.require("model").generateAcceptance(session.id)
      .then(function (next) { onRefresh && onRefresh(next); })
      .catch(function (err) { window.CyreneUI.require("feedback").showToast(err.message || String(err), "error"); })
      .finally(function () { setBusy(false); });
  }
  // Verify a criterion by clicking it — cycle 待验证 → 已通过 → 未通过 → 待验证.
  function toggle(id) {
    var nextStatus = { pending: "passed", passed: "failed", failed: "pending", done: "pending" };
    var next = items.map(function (a) {
      return a.id === id ? Object.assign({}, a, { status: nextStatus[a.status] || "passed" }) : a;
    });
    setBusy(true);
    window.CyreneUI.require("model").patchSession(session.id, { acceptanceCriteria: next })
      .then(function (n) { onRefresh && onRefresh(n); })
      .catch(function (err) { window.CyreneUI.require("feedback").showToast(err.message || String(err), "error"); })
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
      window.CyreneUI.require("feedback").showToast(wbT("task.acceptance.minimumOne", "Keep at least one acceptance criterion."), "warning");
      return;
    }
    setBusy(true);
    window.CyreneUI.require("model").patchSession(session.id, { acceptanceCriteria: next })
      .then(function (n) { setEditing(false); onRefresh && onRefresh(n); })
      .catch(function (err) { window.CyreneUI.require("feedback").showToast(err.message || String(err), "error"); })
      .finally(function () { setBusy(false); });
  }
  function cancelEdits() {
    setDraft(items.map(function (item) { return String((item && item.text) || ""); }));
    setEditing(false);
  }
  return (
    <div className="workbench-side-stack wb-task-tab-content wb-acceptance-panel">
        {items.length ? (
          <React.Fragment>
            <div className="wb-acceptance-summary">
              <div className="wb-acceptance-summary-copy">
                <span>{wbT("task.acceptance.progress", "Verification progress")}</span>
                <b>{passedCount}<small> / {items.length}</small></b>
                <p>{failedCount
                  ? wbT("task.acceptance.failedSummary", "{count} criteria need attention", { count: failedCount })
                  : wbT("task.acceptance.progressHint", "Verify each criterion against the result")}</p>
              </div>
              <div className="wb-acceptance-progress" aria-label={wbT("task.acceptance.progress", "Verification progress")}>
                <span style={{ width: ((passedCount / Math.max(items.length, 1)) * 100) + "%" }}></span>
              </div>
            </div>
            {acceptanceFailure && (
              <div className="wb-acceptance-edit-hint">{wbT("task.acceptance.editHint", "Changed criteria return to pending verification; passed criteria remain unchanged.")}</div>
            )}
            <div className="wb-acceptance-list">{items.map(function (item, index) {
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
                    }} aria-label={wbT("task.acceptance.criterionNumber", "Acceptance criterion {number}", { number: index + 1 })} />
                    <span className={"wb-accept-state " + dot}>{label}</span>
                  </div>
                );
              }
                return (
                  <button type="button" className="workbench-check wb-accept-toggle" key={item.id} disabled={busy} onClick={function () { toggle(item.id); }} title={wbT("task.acceptance.toggleTitle", "Click to verify this acceptance criterion")}>
                  <span className={"workbench-status-dot " + dot}></span>
                  <span className="wb-accept-copy">
                    <span className="wb-accept-text">{item.text}</span>
                    {item.evidence ? <small className="wb-accept-evidence">{wbT("task.acceptance.evidence", "Evidence: {evidence}", { evidence: item.evidence })}</small> : null}
                  </span>
                  <span className={"wb-accept-state " + dot}>{label}</span>
                </button>
              );
            })}</div>
            {editing ? (
              <div className="wb-accept-edit-actions">
                <button type="button" className="wb-btn ghost compact" disabled={busy} onClick={cancelEdits}>{wbT("common.cancel", "Cancel")}</button>
                <button type="button" className="wb-btn primary compact" disabled={busy} onClick={saveEdits}>{wbT("task.acceptance.save", "Save criteria")}</button>
              </div>
            ) : (
              <button type="button" className="wb-btn ghost compact wb-accept-edit-trigger" disabled={busy} onClick={function () { setEditing(true); }}>{wbT("task.acceptance.edit", "Edit criteria")}</button>
            )}
          </React.Fragment>
        ) : (
          <div className="wb-empty-action wb-acceptance-empty">
            <span className="wb-acceptance-empty-icon" aria-hidden="true">{ICONS.check}</span>
            <div>
              <b>{wbT("task.acceptance.empty", "No acceptance criteria yet.")}</b>
              <p>{wbT("task.acceptance.emptyHint", "Generate clear, verifiable criteria from the current task goal.")}</p>
            </div>
            <button type="button" className="wb-btn primary" disabled={busy} onClick={generate}>{busy ? wbT("init.generating", "Generating...") : wbT("task.acceptance.generate", "Ask Agent to generate acceptance criteria")}</button>
          </div>
        )}
    </div>
  );
}

function ArtifactsTab({ session }) {
  var artifacts = WorkbenchModel.ensureArtifacts(session);
  return (
    <div className="wbc-artifact-list wb-task-artifact-list">
      {artifacts.length ? artifacts.map(function (artifact, i) {
        var downloadUrl = "/api/task-sessions/" + encodeURIComponent(session.id) + "/artifacts/" + encodeURIComponent(artifact.id) + "/download";
        var artifactPath = String(artifact.path || "").trim();
        return (
          <a
            className="wbc-artifact-list-row wb-task-artifact-download"
            href={downloadUrl}
            download={artifact.name || true}
            title={wbT("task.artifact.download", "Download {name}", { name: artifact.name || "" })}
            key={artifact.id || i}
          >
            <span className="wbc-artifact-list-icon" aria-hidden="true">
              <svg viewBox="0 0 24 24">
                <path d="M7 3.75h6.4L18 8.35v11.9H7z"></path>
                <path d="M13.25 3.9v4.7h4.7"></path>
              </svg>
            </span>
            <span className="wbc-artifact-list-copy">
              <b>{artifact.name}</b>
              {artifactPath && artifactPath !== artifact.name ? <small>{artifactPath}</small> : null}
            </span>
            <span className="wbc-artifact-list-chevron" aria-hidden="true">{ICONS.chevronRight}</span>
          </a>
        );
      }) : <p className="workbench-muted wb-task-artifact-empty">{wbT("task.artifacts.empty", "No artifacts generated for this task yet.")}</p>}
    </div>
  );
}

function SideSection({ title, children, className }) {
  return (
    <section className={"workbench-side-section" + (className ? " " + className : "")}>
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

window.CyreneUI.shell = window.CyreneUI.register("shell", {
  App: WorkbenchApp,
  ColResizer: WbColResizer,
});
