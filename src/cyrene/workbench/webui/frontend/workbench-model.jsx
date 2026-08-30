import { workbenchServices } from "./shared/runtime/services.jsx"
// Cyrene workbench data adapter.
// Conversation-native project data adapter.

// Pending-question kinds that are real permission / elevation requests and must
// render as a binary 确认/拒绝 authorization card. This MUST mirror the backend's
// _PERMISSION_ELEVATION_KINDS (cyrene/agent/session.py) exactly — matching by
// substring (e.g. anything containing "confirmation") wrongly captures
// plan_confirmation, which is an ordinary
// questions and should show their real options instead.
var WB_PERMISSION_QUESTION_KINDS = {
  scope_elevation: true,
  write_permission_request: true,
  read_elevation: true,
  subshell_elevation: true,
  external_delivery_request: true,
  external_upload_confirmation: true,
  delete_confirmation: true,
  destructive_confirmation: true,
  self_configuration_confirmation: true,
  host_lifecycle_confirmation: true,
  git_commit: true,
};
function wbIsPermissionQuestionKind(kind) {
  return Object.prototype.hasOwnProperty.call(WB_PERMISSION_QUESTION_KINDS, String(kind == null ? "" : kind).trim());
}

(function () {
  function wbModelT(key, fallback, params) {
    var i18n = workbenchServices.i18n();
    if (typeof i18n.t === "function") {
      return i18n.t(key, params, fallback);
    }
    if (params && fallback) {
      Object.keys(params).forEach(function (name) {
        fallback = fallback.split("{" + name + "}").join(String(params[name]));
      });
    }
    return fallback || key;
  }

  // Route through the shared wrapper (workbench-api.jsx) for one normalized fetch
  // path across the workbench. Default timeout:0 (no death timeout) because agent
  // endpoints may run for minutes; toast:false keeps each caller's error handling as
  // the single feedback channel. Quick CRUD callers may pass a `timeout` per call.
  function apiJson(url, options) {
    return workbenchServices.api().json(url, { toast: false, timeout: 0, ...(options || {}) });
  }

  function codexLimitBuckets(limits) {
    var raw = limits && limits.rateLimitsByLimitId;
    if (raw && typeof raw === "object") {
      return Object.keys(raw).map(function (id) {
        return { id: id, ...raw[id] };
      });
    }
    return limits && limits.rateLimits
      ? [{ id: "codex", ...limits.rateLimits }]
      : [];
  }

  function codexWindowLabel(windowData) {
    if (!windowData) return "";
    var minutes = Number(windowData.windowDurationMins || 0);
    if (minutes >= 10080) return "7d";
    if (minutes >= 1440) return Math.round(minutes / 1440) + "d";
    if (minutes >= 60) return Math.round(minutes / 60) + "h";
    return minutes ? minutes + "m" : "";
  }

  function codexQuotaWindows(limits) {
    var buckets = codexLimitBuckets(limits);
    var bucket = buckets.find(function (item) { return item.id === "codex"; }) || buckets[0];
    if (!bucket) return [];
    return [bucket.primary, bucket.secondary]
      .filter(Boolean)
      .map(function (windowData) {
        var durationMins = Number(windowData.windowDurationMins || 0);
        var usedPercent = Math.max(0, Math.min(100, Number(windowData.usedPercent || 0)));
        return {
          durationMins: durationMins,
          kind: durationMins === 300
            ? "five_hour"
            : durationMins >= 10080
              ? "weekly"
              : "other",
          label: codexWindowLabel(windowData),
          usedPercent: usedPercent,
          remainingPercent: Math.max(0, Math.round(100 - usedPercent)),
          resetsAt: Number(windowData.resetsAt || 0),
          raw: windowData,
        };
      })
      .sort(function (a, b) {
        return (a.durationMins || Number.MAX_SAFE_INTEGER)
          - (b.durationMins || Number.MAX_SAFE_INTEGER);
      });
  }

  var CODEX_QUOTA_CACHE_KEY = "cyrene-codex-quota-v1";

  function readCodexQuotaCache() {
    try {
      var cached = JSON.parse(localStorage.getItem(CODEX_QUOTA_CACHE_KEY) || "null");
      return cached && cached.payload && typeof cached.payload === "object"
        ? cached.payload
        : null;
    } catch (error) {
      return null;
    }
  }

  function writeCodexQuotaCache(payload) {
    try {
      if (!payload || payload.connected !== true) {
        localStorage.removeItem(CODEX_QUOTA_CACHE_KEY);
        return;
      }
      localStorage.setItem(CODEX_QUOTA_CACHE_KEY, JSON.stringify({
        savedAt: Date.now(),
        payload: {
          available: payload.available !== false,
          connected: true,
          account: payload.account || null,
          limits: payload.limits || {},
          quota_enabled: payload.quota_enabled !== false,
        },
      }));
    } catch (error) {}
  }

  function codexPlanLabel(account, limits) {
    var planType = String(account && (account.planType || account.plan_type) || "").trim();
    if (!planType) {
      var buckets = codexLimitBuckets(limits);
      var bucket = buckets.find(function (item) { return item.id === "codex"; }) || buckets[0];
      planType = String(bucket && (bucket.planType || bucket.plan_type) || "").trim();
    }
    var normalized = planType.toLowerCase().replace(/[\s_-]+/g, "");
    if (normalized === "plus") return "plus";
    if (normalized === "prolite") return "pro 5x";
    if (normalized === "pro") return "pro 20x";
    return planType ? planType.toLowerCase() : "";
  }

  function normalizeStore(payload) {
    var store = payload && typeof payload === "object" ? payload : {};
    var projects = Array.isArray(store.projects) ? store.projects : [];
    var activeProjectId = store.activeProjectId || (projects[0] && projects[0].id) || "";
    var activeProject = projects.find(function (project) { return project.id === activeProjectId; }) || projects[0] || null;
    return {
      projects: projects,
      activeProjectId: activeProject ? activeProject.id : "",
      activeProject: activeProject,
    };
  }

  function fetchProjects() {
    return apiJson("/api/projects?detail=summary").then(normalizeStore);
  }

  function createProject(input) {
    return apiJson("/api/projects", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(input || {}),
    }).then(normalizeStore);
  }

  function updateProject(projectId, input) {
    return apiJson("/api/projects/" + encodeURIComponent(projectId), {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(input || {}),
    }).then(normalizeStore);
  }

  function deleteProject(projectId) {
    return apiJson("/api/projects/" + encodeURIComponent(projectId), {
      method: "DELETE",
    }).then(normalizeStore);
  }

  function fetchNotifications(tab, limit, visibleView) {
    var qs = "?tab=" + encodeURIComponent(tab || "all") + "&limit=" + encodeURIComponent(limit || 80);
    if (visibleView && visibleView.chatId) {
      qs += "&visible_chat_id=" + encodeURIComponent(visibleView.chatId);
    } else if (visibleView && visibleView.sessionId) {
      qs += "&visible_session_id=" + encodeURIComponent(visibleView.sessionId);
    }
    return apiJson("/api/workbench/notifications" + qs);
  }

  function markNotificationsRead(ids, markAll) {
    return apiJson("/api/workbench/notifications/read", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        ids: Array.isArray(ids) ? ids : [],
        markAll: !!markAll,
      }),
    });
  }

  function setActiveProject(projectId) {
    var body = {};
    if (projectId != null) body.projectId = projectId;
    return apiJson("/api/workbench/activate", {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
  }

  function statusText(status) {
    var raw = String(status || "idle");
    var map = {
      idle: ["status.idle", "Not started"],
      pending: ["status.pending", "Pending"],
      initializing: ["status.initializing", "Initializing"],
      planning: ["status.planning", "Planning"],
      answered: ["status.answered", "Answered"],
      acted: ["status.acted", "Done"],
      running: ["status.running", "Running"],
      waiting_for_user: ["status.waiting", "Waiting"],
      waiting_for_approval: ["status.waiting", "Waiting"],
      blocked: ["status.blocked", "Blocked"],
      review: ["status.review", "In review"],
      failed: ["status.failed", "Failed"],
      paused: ["status.paused", "Paused"],
      cancelled: ["status.cancelled", "Cancelled"],
      done: ["status.done", "Done"],
      completed: ["status.done", "Done"],
      skipped: ["status.skipped", "Skipped"],
      draft: ["status.draft", "Draft"],
      created: ["status.created", "Created"],
      modified: ["status.modified", "Modified"],
    };
    return map[raw] ? wbModelT(map[raw][0], map[raw][1]) : raw;
  }

  function statusTone(status) {
    var raw = String(status || "idle");
    if (raw === "running" || raw === "planning" || raw === "review" || raw === "initializing" || raw === "answered") return "blue";
    if (raw === "acted") return "green";
    if (raw === "waiting_for_user" || raw === "waiting_for_approval" || raw === "blocked") return "amber";
    if (raw === "paused") return "amber";
    if (raw === "failed") return "red";
    if (raw === "done" || raw === "completed") return "green";
    return "muted"; // idle / pending / cancelled / skipped
  }

  // Short human-readable label for a run-log event type.
  function formatTime(value) {
    if (!value) return "—";
    try {
      var date = new Date(value);
      if (Number.isNaN(date.getTime())) return String(value).slice(0, 16);
      var now = new Date();
      var sameDay = date.toDateString() === now.toDateString();
      var locale = workbenchServices.i18n().getLang() === "zh" ? "zh-CN" : "en-US";
      if (sameDay) {
        return date.toLocaleTimeString(locale, { hour: "2-digit", minute: "2-digit" });
      }
      return date.toLocaleDateString(locale, { month: "2-digit", day: "2-digit" });
    } catch (e) {
      return String(value).slice(0, 16);
    }
  }

  function formatRelativeTime(value) {
    if (!value) return wbModelT("time.justNow", "Just now");
    try {
      var date = new Date(value);
      var diff = Date.now() - date.getTime();
      if (!Number.isFinite(diff)) return wbModelT("time.justNow", "Just now");
      var minute = 60 * 1000;
      var hour = 60 * minute;
      var day = 24 * hour;
      if (diff < minute) return wbModelT("time.justNow", "Just now");
      if (diff < hour) return wbModelT("time.minutesAgo", "{n}m ago", { n: Math.max(1, Math.floor(diff / minute)) });
      if (diff < day) return wbModelT("time.hoursAgo", "{n}h ago", { n: Math.max(1, Math.floor(diff / hour)) });
      if (diff < day * 2) return wbModelT("time.yesterday", "Yesterday");
      if (diff < day * 7) return wbModelT("time.daysAgo", "{n}d ago", { n: Math.max(1, Math.floor(diff / day)) });
      return formatTime(value);
    } catch (e) {
      return wbModelT("time.justNow", "Just now");
    }
  }

  function initials(name) {
    var source = String(name || "C").trim();
    if (!source) return "C";
    var parts = source.split(/\s+/).filter(Boolean);
    if (parts.length >= 2) return (parts[0][0] + parts[1][0]).toUpperCase();
    return source.slice(0, 1).toUpperCase();
  }

  function pathLabel(path, projectName) {
    var raw = String(path || "").trim();
    if (!raw) return wbModelT("path.unsetWorkspace", "Workspace not set");
    var home = "";
    try {
      home = (workbenchServices.data().state.user || {}).home || "";
    } catch (e) {}
    if (home && raw.indexOf(home + "/") === 0) raw = "~" + raw.slice(home.length);
    var parts = raw.split("/").filter(Boolean);
    if (raw[0] === "~" && parts.length) parts[0] = "~";
    var name = String(projectName || "").trim();
    if (name && parts.length >= 2 && parts[parts.length - 1] === "workspace" && parts[parts.length - 2] === name) {
      return name + "/workspace";
    }
    if (parts.length <= 3) return raw;
    return "..." + parts.slice(-3).join("/");
  }

  // Stable per-project icon gradient derived from a seed (project id or name),
  // so each project card gets its own color like the reference design.
  function projectGradient(seed) {
    var palette = [
      ["#8f5cff", "#5b7dff"],
      ["#3b82f6", "#2567e8"],
      ["#22b07a", "#149e63"],
      ["#fb7185", "#ef4d57"],
      ["#f2a51a", "#ef7e1a"],
      ["#06b6d4", "#0e8fb0"],
    ];
    var str = String(seed || "");
    var hash = 0;
    for (var i = 0; i < str.length; i++) {
      hash = (hash * 31 + str.charCodeAt(i)) >>> 0;
    }
    var pair = palette[hash % palette.length];
    return "linear-gradient(135deg, " + pair[0] + ", " + pair[1] + ")";
  }

  var service = {
    normalizeStore: normalizeStore,
    fetchProjects: fetchProjects,
    createProject: createProject,
    updateProject: updateProject,
    deleteProject: deleteProject,
    fetchNotifications: fetchNotifications,
    markNotificationsRead: markNotificationsRead,
    setActiveProject: setActiveProject,
    statusText: statusText,
    statusTone: statusTone,
    formatTime: formatTime,
    formatRelativeTime: formatRelativeTime,
    initials: initials,
    pathLabel: pathLabel,
    projectGradient: projectGradient,
    isPermissionQuestionKind: wbIsPermissionQuestionKind,
    codexLimitBuckets: codexLimitBuckets,
    codexWindowLabel: codexWindowLabel,
    codexQuotaWindows: codexQuotaWindows,
    codexPlanLabel: codexPlanLabel,
    readCodexQuotaCache: readCodexQuotaCache,
    writeCodexQuotaCache: writeCodexQuotaCache,
  };

  window.CyreneUI.model = window.CyreneUI.register("model", service);
})();
