import { workbenchServices } from "../../shared/runtime/services.jsx"

function wbT(key, fallback, params) {
  return workbenchServices.i18n().t(key, params, fallback);
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
        title: String(session.title || wbT("task.newTask", "New task")),
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
        title: String(chat.title || wbT("chat.newChat", "New chat")),
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
  // A tool/phase event can use the model runtime's `run_*` id while the
  // answer-resume endpoint persists a synthetic `resume_*` id. Treat a
  // different id as a newer run only when a fresh active lifecycle event says
  // so; id inequality by itself is not chronological evidence.
  var liveBelongsToNewerRun = !!liveRunKey
    && (!sourceRunKey || liveRunKey !== sourceRunKey)
    && liveStatusIsFresh
    && statusIsActive(live && live.status);
  // Network delivery time can be a few milliseconds later than the durable
  // completion timestamp. A lingering tool/phase event from the SAME run must
  // never resurrect a completed, failed, cancelled, or awaiting conversation.
  // A fresh active lifecycle for a different run is still allowed to become
  // live before the next list summary arrives, preserving background feedback.
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
      return {
        kind: "phase",
        label: String(entry.text || entry.detailKey || ""),
        labelKey: String(entry.detailKey || ""),
        labelParams: entry.detailParams || {},
        detail: String(entry.preview || ""),
      };
    }
  }
  var activities = Array.isArray(runtime.activities) ? runtime.activities : [];
  var latest = activities.length ? activities[activities.length - 1] : null;
  if (latest && latest.reasoningActive) return {
    kind: "reasoning", label: "Thinking", labelKey: "workbench.sessionStatus.thinking", detail: "",
  };
  if (runtime.finalizing) return {
    kind: "finalizing", label: "Finalizing", labelKey: "workbench.sessionStatus.finalizing", detail: "",
  };
  return null;
}

function wbLocalizedSessionActivity(activity) {
  if (!activity) return null;
  var localized = Object.assign({}, activity);
  var label = String(localized.label || "");
  if (localized.labelKey) {
    localized.label = wbT(localized.labelKey, label, localized.labelParams || {});
  } else if (localized.kind === "tool" && label) {
    localized.label = wbT("toolName." + label, label);
  }
  return localized;
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
  activity = wbLocalizedSessionActivity(activity);
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
      labelKey: String(data.detail_key || ""),
      labelParams: data.detail_params && typeof data.detail_params === "object" ? data.detail_params : {},
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
      detail: data.failed && data.error && data.error.message ? String(data.error.message) : wbArgsPreview(data.args),
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
  if (raw === "main_agent") return wbT("workbench.actor.mainAgent", "Main Agent");
  return raw || wbT("workbench.actor.agent", "Agent");
}

function wbSubagentStatusText(status) {
  var map = {
    running: ["workbench.subagentStatus.running", "Running"],
    resumed: ["workbench.subagentStatus.resumed", "Resumed"],
    waiting: ["workbench.subagentStatus.waiting", "Waiting for other subagents"],
    done: ["workbench.subagentStatus.done", "Completed"],
    timeout: ["workbench.subagentStatus.timeout", "Timed out"],
    error: ["workbench.subagentStatus.error", "Failed"],
  };
  var raw = String(status || "").trim();
  return map[raw]
    ? wbT(map[raw][0], map[raw][1])
    : wbT("workbench.statusUnknown", "Unknown status: {status}", { status: raw || "—" });
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
      body: wbT("workbench.activity.toolCall", "{actor} called tool {tool}", { actor: actor, tool: toolName }),
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
      body: llmStatus === "started"
        ? wbT("workbench.activity.thinking", "{actor} is thinking…", { actor: actor2 })
        : wbT("workbench.activity.thoughtComplete", "{actor} completed a reasoning turn", { actor: actor2 }),
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
        + (data.message ? ": " + String(data.message).slice(0, 180) : (task ? ": " + task.slice(0, 120) : "")),
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

export {
  wbActivityEventRunKey,
  wbActivityEventTimestamp,
  wbActivityStatusIsActive,
  wbActivityStatusIsTerminal,
  wbActorLabel,
  wbCopyBrowserToChat,
  wbDeliverResourceToChat,
  wbLatestRuntimeActivity,
  wbLiveActivityLines,
  wbLiveEventFromSse,
  wbMergeLiveEventIntoSession,
  wbNotificationNavigationTarget,
  wbNotificationOnScreen,
  wbOverflowSessionTime,
  wbRecentSessionTabs,
  wbReduceSessionActivity,
  wbRememberOpenedSessionKey,
  wbSessionActivityPhase,
  wbSessionActivityRank,
  wbSessionActivitySnapshot,
  wbSessionPlanProgress,
  wbSplitOverflowSessions,
  wbSubagentStatusText,
  wbSuppressOnScreenNotifications,
  wbVisibleSessionTabs,
}
