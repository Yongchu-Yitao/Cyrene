import { DATA } from "../../platform/data-store.jsx"
import { workbenchServices } from "../runtime/services.jsx"

// Cyrene — global Workbench search overlay
const { useState: useStateSr, useEffect: useEffectSr, useRef: useRefSr, useCallback: useCallbackSr, useMemo: useMemoSr } = React;

var SEARCH_TYPES = [
  { id: "all", labelKey: "search.allTypes" },
  { id: "project", labelKey: "search.type.project" },
  { id: "task", labelKey: "search.type.task" },
  { id: "chat", labelKey: "search.type.chat" },
  { id: "knowledge", labelKey: "search.type.knowledge" },
  { id: "memory", labelKey: "search.type.memory" },
  { id: "schedule", labelKey: "search.type.schedule" },
];

// Command-palette entries: matched against the translated label/hint/keywords
// and executed through the onCommand prop (wired in the workbench shell).
var SEARCH_COMMANDS = [
  { id: "new-chat", labelKey: "search.command.newChat", hintKey: "search.command.newChatHint", keywords: ["新对话", "对话", "chat", "new chat"] },
  { id: "new-task", labelKey: "search.command.newTask", hintKey: "search.command.newTaskHint", keywords: ["新任务", "任务", "task", "new task"] },
  { id: "new-project", labelKey: "search.command.newProject", hintKey: "search.command.newProjectHint", keywords: ["新项目", "项目", "project", "new project"] },
  { id: "open-settings", labelKey: "search.command.openSettings", hintKey: "search.command.openSettingsHint", keywords: ["设置", "settings", "偏好"] },
  { id: "open-shortcuts", labelKey: "search.command.openShortcuts", hintKey: "search.command.openShortcutsHint", keywords: ["快捷键", "shortcuts", "按键"] },
  { id: "open-plugin-registry", labelKey: "search.command.openPluginRegistry", hintKey: "search.command.openPluginRegistryHint", keywords: ["插件", "plugin", "registry", "MCP"] },
  { id: "open-budget", labelKey: "search.command.openBudget", hintKey: "search.command.openBudgetHint", keywords: ["预算", "budget", "额度"] },
  { id: "open-about", labelKey: "search.command.openAbout", hintKey: "search.command.openAboutHint", keywords: ["关于", "about", "版本", "更新"] },
  { id: "toggle-theme", labelKey: "search.command.toggleTheme", hintKey: "search.command.toggleThemeHint", keywords: ["主题", "theme", "深色", "浅色"] },
  { id: "toggle-sidebar", labelKey: "search.command.toggleSidebar", hintKey: "search.command.toggleSidebarHint", keywords: ["侧边栏", "sidebar", "边栏"] },
];

var SETTINGS_INDEX = (function () {
  try {
    return workbenchServices.settingsIndex();
  } catch (e) {
    return null;
  }
})();

var SEARCH_TYPE_ORDER = ["project", "task", "chat", "knowledge", "memory", "schedule"];
var SEARCH_REQUEST_TIMEOUT_MS = 10000;
var SEARCH_GROUP_KEYS = {
  project: "search.group.projects",
  task: "search.group.tasks",
  chat: "search.group.chats",
  knowledge: "search.group.knowledge",
  memory: "search.group.memory",
  schedule: "search.group.schedule",
};

var NEW_ACTION_ICONS = {
  "new-chat": React.createElement(React.Fragment, null,
    React.createElement("path", { d: "M21 11.5a8.38 8.38 0 0 1-.9 3.8 8.5 8.5 0 0 1-7.6 4.7 8.38 8.38 0 0 1-3.8-.9L3 21l1.9-5.7a8.38 8.38 0 0 1-.9-3.8 8.5 8.5 0 0 1 4.7-7.6 8.38 8.38 0 0 1 3.8-.9h.5a8.48 8.48 0 0 1 8 8v.5z" }),
  ),
  "new-task": React.createElement(React.Fragment, null,
    React.createElement("path", { d: "M9 11l3 3L22 4M21 12v7a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11" }),
  ),
  "new-project": React.createElement(React.Fragment, null,
    React.createElement("path", { d: "M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z" }),
    React.createElement("path", { d: "M12 11v6M9 14h6" }),
  ),
};

function SearchOverlay({ onClose, onCommand, onOpenSettings }) {
  var { t, lang } = workbenchServices.i18n().use();
  var dataStore = workbenchServices.data();
  dataStore.useVersion();
  var pluginModules = Array.isArray(dataStore.state.pluginModules)
    ? dataStore.state.pluginModules : [];
  var knowledgeEnabled = pluginModules.indexOf("knowledge") >= 0;
  var memoryEnabled = pluginModules.indexOf("memory") >= 0;
  var scheduleEnabled = pluginModules.indexOf("schedule") >= 0;
  var searchTypes = SEARCH_TYPES.filter(function (item) {
    return (item.id !== "knowledge" || knowledgeEnabled)
      && (item.id !== "memory" || memoryEnabled)
      && (item.id !== "schedule" || scheduleEnabled);
  });
  var searchTypeOrder = SEARCH_TYPE_ORDER.filter(function (type) {
    return (type !== "knowledge" || knowledgeEnabled)
      && (type !== "memory" || memoryEnabled)
      && (type !== "schedule" || scheduleEnabled);
  });
  var inputRef = useRefSr(null);
  var resultsRef = useRefSr(null);
  var [query, setQuery] = useStateSr("");
  var [activeType, setActiveType] = useStateSr("all");
  var [archiveMode, setArchiveMode] = useStateSr(false);
  var [results, setResults] = useStateSr([]);
  var [groups, setGroups] = useStateSr({});
  var [status, setStatus] = useStateSr("idle"); // idle | loading | done | error
  var debounceRef = useRefSr(null);
  var abortRef = useRefSr(null);
  var requestSeqRef = useRefSr(0);
  var flatListRef = useRefSr([]); // flat list of the last committed render (see Enter handler)

  useEffectSr(function () {
    if (!knowledgeEnabled && activeType === "knowledge") setActiveType("all");
    if (!memoryEnabled && activeType === "memory") setActiveType("all");
    if (!scheduleEnabled && activeType === "schedule") setActiveType("all");
  }, [knowledgeEnabled, memoryEnabled, scheduleEnabled, activeType]);

  // Auto-focus input on mount and restore focus on close.
  useEffectSr(function () {
    var prevActive = document.activeElement;
    inputRef.current?.focus();
    return function () {
      if (prevActive && typeof prevActive.focus === "function") {
        prevActive.focus();
      }
    };
  }, []);

  // Keyboard: Escape to close, ArrowDown from input moves to results, Arrow
  // keys navigate results, Enter/Space activates the focused result.
  useEffectSr(function () {
    function getResultNodes() {
      if (!resultsRef.current) return [];
      return Array.prototype.slice.call(resultsRef.current.querySelectorAll(".search-result-item, .search-new-action"));
    }
    function onKeyDown(e) {
      if (e.key === "Escape") {
        e.preventDefault();
        onClose && onClose();
        return;
      }
      var nodes = getResultNodes();
      if (!nodes.length) return;
      var active = document.activeElement;
      var inputFocused = active === inputRef.current;
      var resultFocused = active && active.classList && (
        active.classList.contains("search-result-item")
        || active.classList.contains("search-new-action")
      );
      if (e.key === "ArrowDown") {
        if (inputFocused) {
          e.preventDefault();
          nodes[0].focus();
        } else if (resultFocused) {
          e.preventDefault();
          var idx = nodes.indexOf(active);
          var next = idx + 1;
          if (next >= nodes.length) next = 0;
          nodes[next].focus();
        }
        return;
      }
      if (e.key === "ArrowUp" && resultFocused) {
        e.preventDefault();
        var idx = nodes.indexOf(active);
        var prev = idx - 1;
        if (prev < 0) prev = nodes.length - 1;
        nodes[prev].focus();
        return;
      }
      if (e.key === "Enter" && resultFocused) {
        e.preventDefault();
        // Only activate rows of the current committed render; during the
        // debounce/loading window the old rows are stale or unmounted, and a
        // freshly recomputed flat list can disagree with what is focused.
        if (status !== "done") return;
        var idx = nodes.indexOf(active);
        var flat = flatListRef.current;
        var selected = flat[idx];
        if (selected) handleResultClick(selected);
      }
    }
    window.addEventListener("keydown", onKeyDown);
    return function () { window.removeEventListener("keydown", onKeyDown); };
  }, [onClose, status, groups, results, archiveMode]);

  // Debounced search with request cancellation.
  useEffectSr(function () {
    var requestId = ++requestSeqRef.current;
    if (debounceRef.current) clearTimeout(debounceRef.current);
    if (abortRef.current) {
      if (abortRef.current.__cyreneTimeoutId) {
        clearTimeout(abortRef.current.__cyreneTimeoutId);
      }
      try { abortRef.current.abort(); } catch (e) {}
      abortRef.current = null;
    }
    var q = query.trim();
    if (!q) {
      setResults([]);
      setGroups({});
      setStatus("idle");
      return;
    }
    setStatus("loading");
    debounceRef.current = setTimeout(function () {
      var controller = new AbortController();
      controller.__cyreneTimedOut = false;
      controller.__cyreneTimeoutId = setTimeout(function () {
        if (requestSeqRef.current !== requestId || controller.signal.aborted) return;
        controller.__cyreneTimedOut = true;
        controller.abort();
      }, SEARCH_REQUEST_TIMEOUT_MS);
      abortRef.current = controller;
      if (archiveMode) {
        doConversationSearch(q, controller, requestId);
      } else {
        doWorkbenchSearch(q, controller, requestId);
      }
    }, 250);
    return function () {
      if (requestSeqRef.current === requestId) requestSeqRef.current += 1;
      if (debounceRef.current) clearTimeout(debounceRef.current);
      if (abortRef.current) {
        if (abortRef.current.__cyreneTimeoutId) {
          clearTimeout(abortRef.current.__cyreneTimeoutId);
        }
        try { abortRef.current.abort(); } catch (e) {}
        abortRef.current = null;
      }
    };
  }, [query, activeType, archiveMode]);

  function finishSearchRequest(controller) {
    if (controller.__cyreneTimeoutId) {
      clearTimeout(controller.__cyreneTimeoutId);
      controller.__cyreneTimeoutId = null;
    }
    if (abortRef.current === controller) abortRef.current = null;
  }

  function shouldIgnoreSearchResponse(controller, requestId) {
    if (requestSeqRef.current !== requestId) return true;
    if (!controller.signal.aborted) return false;
    // A timeout can race with a response that has already resolved.  In that
    // case fetch may not reject with AbortError, so settle the loading state
    // here instead of returning and leaving the overlay spinning forever.
    if (controller.__cyreneTimedOut) setStatus("error");
    return true;
  }

  async function doWorkbenchSearch(q, controller, requestId) {
    var signal = controller.signal;
    try {
      var typesParam = activeType === "all" ? "" : activeType;
      var url = "/api/workbench/search?q=" + encodeURIComponent(q) +
        "&types=" + encodeURIComponent(typesParam) +
        "&limit=50";
      var r = await fetch(url, { signal: signal });
      if (shouldIgnoreSearchResponse(controller, requestId)) return;
      if (!r.ok) throw new Error("HTTP " + r.status);
      var data = await r.json();
      if (shouldIgnoreSearchResponse(controller, requestId)) return;
      if (data.ok && data.groups) {
        setGroups(data.groups);
        setResults(flattenGroups(data.groups));
        setStatus("done");
      } else {
        setGroups({});
        setResults([]);
        setStatus("done");
      }
    } catch (e) {
      if (requestSeqRef.current !== requestId) return;
      if (e && e.name === "AbortError" && !controller.__cyreneTimedOut) return;
      console.error("Workbench search failed:", e);
      setStatus("error");
    } finally {
      finishSearchRequest(controller);
    }
  }

  async function doConversationSearch(q, controller, requestId) {
    var signal = controller.signal;
    try {
      var r = await fetch("/api/search/conversations?q=" + encodeURIComponent(q) + "&limit=50", { signal: signal });
      if (shouldIgnoreSearchResponse(controller, requestId)) return;
      if (!r.ok) throw new Error("HTTP " + r.status);
      var data = await r.json();
      if (shouldIgnoreSearchResponse(controller, requestId)) return;
      if (data.ok && Array.isArray(data.results)) {
        setResults(data.results);
        setGroups({});
        setStatus("done");
      } else {
        setResults([]);
        setGroups({});
        setStatus("done");
      }
    } catch (e) {
      if (requestSeqRef.current !== requestId) return;
      if (e && e.name === "AbortError" && !controller.__cyreneTimedOut) return;
      console.error("Search failed:", e);
      setStatus("error");
    } finally {
      finishSearchRequest(controller);
    }
  }

  function flattenGroups(g) {
    var out = [];
    searchTypeOrder.forEach(function (type) {
      var arr = g[type];
      if (Array.isArray(arr)) {
        arr.forEach(function (item) { out.push(item); });
      }
    });
    return out;
  }

  // Command-palette results: commands first, then settings entries, then the
  // data results. Every entry carries kind so clicks dispatch correctly.
  function queryMatches(text, q) {
    return String(text || "").toLowerCase().indexOf(q) >= 0;
  }

  function matchCommands() {
    var q = query.trim().toLowerCase();
    if (!q) return [];
    return SEARCH_COMMANDS.filter(function (cmd) {
      if (queryMatches(t(cmd.labelKey), q)) return true;
      if (cmd.hintKey && queryMatches(t(cmd.hintKey), q)) return true;
      return (cmd.keywords || []).some(function (kw) { return queryMatches(kw, q); });
    }).map(function (cmd) {
      return { kind: "command", id: cmd.id, label: t(cmd.labelKey), hint: cmd.hintKey ? t(cmd.hintKey) : "" };
    });
  }

  function matchSettings() {
    var q = query.trim().toLowerCase();
    if (!q || !SETTINGS_INDEX) return [];
    // Guard the shape: a stale/partial settings-index build may register the
    // module without items/tabs; treat missing keys as empty lists (same
    // guard pattern as settingTabLabel).
    var tabs = SETTINGS_INDEX.tabs || [];
    var items = SETTINGS_INDEX.items || [];
    var tabsById = {};
    tabs.forEach(function (tab) { tabsById[tab.id] = tab; });
    var itemHits = items.filter(function (item) {
      if (queryMatches(t(item.labelKey), q)) return true;
      if (item.hintKey && queryMatches(t(item.hintKey), q)) return true;
      return (item.keywords || []).some(function (kw) { return queryMatches(kw, q); });
    }).slice(0, 8).map(function (item) {
      var tab = tabsById[item.tab];
      return {
        kind: "setting",
        id: item.id,
        tab: item.tab,
        label: t(item.labelKey),
        hint: item.hintKey ? t(item.hintKey) : (tab ? t(tab.labelKey) : ""),
      };
    });
    // Tab-level entries let a query jump straight to a settings tab; they
    // carry no anchor (id === null) so the overlay only switches tabs.
    var tabHits = tabs.filter(function (tab) {
      return queryMatches(t(tab.labelKey), q);
    }).slice(0, 4).map(function (tab) {
      return {
        kind: "setting",
        id: null,
        tab: tab.id,
        label: t(tab.labelKey),
        hint: "",
      };
    });
    // Prefer concrete items over bare tabs when both match (the tab row adds
    // nothing once an item row already lands the user on that tab).
    var hitTabIds = {};
    itemHits.forEach(function (item) { hitTabIds[item.tab] = true; });
    return itemHits.concat(tabHits.filter(function (tabHit) { return !hitTabIds[tabHit.tab]; }));
  }

  function resultTypeLabel(type) {
    return t("search.result." + (type || "conversation"), {}, type || "");
  }

  function navigateWorkbench(result) {
    if (workbenchServices.navigation().navigate({
        type: result.type || "conversation",
        id: result.id,
        projectId: result.projectId,
        sessionId: result.sessionId,
        chatId: result.chatId,
        docId: result.docId,
        memId: result.memId,
        taskId: result.taskId,
        entityId: result.entityId,
      })) {
      onClose && onClose();
      return true;
    }
    return false;
  }

  function handleResultClick(result) {
    // Command-palette entries: run the action, then close the overlay.
    if (result.kind === "command") {
      if (onCommand) onCommand(result.id);
      onClose && onClose();
      return;
    }
    // Settings entries: open the settings overlay on the owning tab and let
    // it scroll to the matching anchor (id) once rendered. Tab-level hits
    // carry id === null, so only the tab switch happens.
    if (result.kind === "setting") {
      if (onOpenSettings) onOpenSettings(result.tab, result.id || null);
      onClose && onClose();
      return;
    }
    // Workbench-aware navigation for project/task/chat/knowledge/memory/schedule.
    if (navigateWorkbench(result)) {
      return;
    }
    onClose && onClose();
  }

  function handleResultKeyDown(e, result) {
    if (e.key === "Enter" || e.key === " ") {
      e.preventDefault();
      // Stop the event reaching the window keydown listener, which would
      // otherwise activate the same result a second time (double dispatch).
      e.stopPropagation();
      handleResultClick(result);
    }
  }

  function highlightSnippet(text) {
    if (!query.trim() || !text) return text;
    var q = query.trim();
    var escaped = q.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
    var re = new RegExp("(" + escaped + ")", "gi");
    var parts = String(text).split(re);
    return parts.map(function (part, i) {
      if (part.toLowerCase() === q.toLowerCase()) {
        return React.createElement("mark", { key: i }, part);
      }
      return part;
    });
  }

  function renderMeta(result) {
    var parts = [];
    if (result.projectName && result.projectName !== "Workspace") {
      parts.push(result.projectName);
    }
    var label = resultTypeLabel(result.type);
    if (label) parts.push(label);
    var time = result.updatedAt || result.nextRun || result.createdAt || result.dueDate;
    if (time) {
      try {
        var d = new Date(time);
        if (!isNaN(d.getTime())) {
          parts.push(d.toLocaleDateString(lang === "zh" ? "zh-CN" : undefined));
        }
      } catch (e) {}
    }
    return parts.join(" · ");
  }

  function renderArchiveResult(result, index) {
    var isLast = index === results.length - 1;
    var assistantLabel = t("search.assistantLabel", { name: DATA.assistantName || "Cyrene" });
    var snippet = result.snippet;
    if (!snippet) {
      var user = result.user_body || "";
      var assistant = result.assistant_body || "";
      snippet = (user + " " + assistant).trim();
    }
    return React.createElement(React.Fragment, { key: result.date + "_" + result.timestamp + "_" + index },
      React.createElement("div", {
        className: "search-result-item",
        tabIndex: 0,
        role: "button",
        "aria-label": (result.session_title || result.date) + ", " + snippet,
        onClick: function () { handleResultClick(result); },
        onKeyDown: function (e) { handleResultKeyDown(e, result); },
        title: result.date + " " + result.timestamp,
      },
        React.createElement("div", { className: "search-result-meta" },
          React.createElement("span", { className: "search-result-date" }, result.date),
          result.session_title && React.createElement("span", { className: "search-result-title" }, result.session_title),
        ),
        React.createElement("div", { className: "search-result-snippet" },
          highlightSnippet(snippet)
        ),
        React.createElement("div", { className: "search-result-excerpt" },
          React.createElement("span", { className: "search-result-tag" }, t("search.userLabel")),
          React.createElement("span", { className: "search-result-tag" }, assistantLabel),
        )
      ),
      !isLast && React.createElement("div", { className: "search-result-divider" })
    );
  }

  function renderWorkbenchResult(result, index, arr) {
    var isLast = index === arr.length - 1;
    return React.createElement(React.Fragment, { key: (result.type || "item") + "_" + (result.id || index) + "_" + index },
      React.createElement("div", {
        className: "search-result-item",
        tabIndex: 0,
        role: "button",
        "aria-label": (result.title || resultTypeLabel(result.type)) + ", " + (result.snippet || ""),
        onClick: function () { handleResultClick(result); },
        onKeyDown: function (e) { handleResultKeyDown(e, result); },
      },
        React.createElement("div", { className: "search-result-meta" },
          React.createElement("span", { className: "search-result-type" }, resultTypeLabel(result.type)),
          React.createElement("span", { className: "search-result-context" }, renderMeta(result)),
        ),
        React.createElement("div", { className: "search-result-title-line" }, result.title || "—"),
        React.createElement("div", { className: "search-result-snippet" },
          highlightSnippet(result.snippet || "")
        )
      ),
      !isLast && React.createElement("div", { className: "search-result-divider" })
    );
  }

  function renderGroupedResults() {
    var out = [];
    searchTypeOrder.forEach(function (type) {
      var arr = groups[type];
      if (!Array.isArray(arr) || !arr.length) return;
      out.push(
        React.createElement("div", { className: "search-result-group", role: "group", "aria-label": t(SEARCH_GROUP_KEYS[type], {}, type), key: "group_" + type },
          React.createElement("div", { className: "search-result-group-header" }, t(SEARCH_GROUP_KEYS[type], {}, type)),
          arr.map(function (item, idx) { return renderWorkbenchResult(item, idx, arr); })
        )
      );
    });
    return out;
  }

  function renderCommandResult(cmd, index, arr) {
    var isLast = index === arr.length - 1;
    return React.createElement(React.Fragment, { key: "cmd_" + cmd.id },
      React.createElement("div", {
        className: "search-result-item search-result-command",
        tabIndex: 0,
        role: "button",
        "aria-label": cmd.label + (cmd.hint ? ", " + cmd.hint : ""),
        onClick: function () { handleResultClick(cmd); },
        onKeyDown: function (e) { handleResultKeyDown(e, cmd); },
      },
        React.createElement("div", { className: "search-result-meta" },
          React.createElement("span", { className: "search-result-type" }, t("search.commandGroup")),
        ),
        React.createElement("div", { className: "search-result-title-line" }, cmd.label),
        cmd.hint && React.createElement("div", { className: "search-result-snippet" }, cmd.hint),
      ),
      !isLast && React.createElement("div", { className: "search-result-divider" })
    );
  }

  // Idle-state quick actions: three New buttons, hidden as soon as the user
  // starts typing.
  function renderNewAction(cmd, index, arr) {
    return React.createElement(React.Fragment, { key: "new_" + cmd.id },
      React.createElement("div", {
        className: "search-new-action",
        tabIndex: 0,
        role: "button",
        "aria-label": cmd.label + (cmd.hint ? ", " + cmd.hint : ""),
        onClick: function () { handleResultClick(cmd); },
        onKeyDown: function (e) { handleResultKeyDown(e, cmd); },
      },
        React.createElement("span", { className: "search-new-action-icon", "aria-hidden": "true" },
          React.createElement("svg", { width: "17", height: "17", viewBox: "0 0 24 24", fill: "none", stroke: "currentColor", strokeWidth: "1.8", strokeLinecap: "round", strokeLinejoin: "round" },
            NEW_ACTION_ICONS[cmd.id] || NEW_ACTION_ICONS["new-chat"]
          )
        ),
        React.createElement("div", { className: "search-new-action-copy" },
          React.createElement("div", { className: "search-new-action-title" }, cmd.label),
          cmd.hint && React.createElement("div", { className: "search-new-action-hint" }, cmd.hint),
        ),
        React.createElement("svg", { className: "search-new-action-chevron", width: "16", height: "16", viewBox: "0 0 24 24", fill: "none", stroke: "currentColor", strokeWidth: "2", strokeLinecap: "round", strokeLinejoin: "round", "aria-hidden": "true" },
          React.createElement("path", { d: "m9 18 6-6-6-6" })
        )
      ),
      index < arr.length - 1 && React.createElement("div", { className: "search-new-action-gap", key: "gap_" + cmd.id })
    );
  }

  function settingTabLabel(item) {
    if (!SETTINGS_INDEX) return "";
    var tab = (SETTINGS_INDEX.tabs || []).find(function (entry) { return entry.id === item.tab; });
    return tab ? t(tab.labelKey) : "";
  }

  function renderSettingResult(item, index, arr) {
    var isLast = index === arr.length - 1;
    return React.createElement(React.Fragment, { key: "setting_" + (item.id || "tab_" + item.tab) },
      React.createElement("div", {
        className: "search-result-item search-result-setting",
        tabIndex: 0,
        role: "button",
        "aria-label": item.label + ", " + t("search.settingsGroup") + ", " + settingTabLabel(item) + (item.hint ? ", " + item.hint : ""),
        onClick: function () { handleResultClick(item); },
        onKeyDown: function (e) { handleResultKeyDown(e, item); },
      },
        React.createElement("div", { className: "search-result-meta" },
          React.createElement("span", { className: "search-result-type" }, t("search.settingsGroup")),
          React.createElement("span", { className: "search-result-context" }, settingTabLabel(item)),
        ),
        React.createElement("div", { className: "search-result-title-line" }, item.label),
        item.hint && React.createElement("div", { className: "search-result-snippet" }, item.hint),
      ),
      !isLast && React.createElement("div", { className: "search-result-divider" })
    );
  }

  function renderCommandPaletteGroups(cmds, settings) {
    var out = [];
    if (cmds.length) {
      out.push(
        React.createElement("div", { className: "search-result-group", role: "group", "aria-label": t("search.commandGroup"), key: "group_commands" },
          React.createElement("div", { className: "search-result-group-header" }, t("search.commandGroup")),
          cmds.map(function (cmd, idx) { return renderCommandResult(cmd, idx, cmds); })
        )
      );
    }
    if (settings.length) {
      out.push(
        React.createElement("div", { className: "search-result-group", role: "group", "aria-label": t("search.settingsGroup"), key: "group_settings" },
          React.createElement("div", { className: "search-result-group-header" }, t("search.settingsGroup")),
          settings.map(function (item, idx) { return renderSettingResult(item, idx, settings); })
        )
      );
    }
    return out;
  }

  // Flat list of the rows this render commits, in render order: command
  // palette (commands then settings), then data results. Computed here at
  // render time — with the same inputs and in the same order as
  // renderCommandPaletteGroups + the results blocks below — so the window
  // Enter handler indexes into the exact list the rendered rows came from,
  // instead of recomputing from a closure query that may no longer match
  // the committed DOM (the keydown listener re-registers only after a
  // re-render).
  var paletteCommands = matchCommands();
  var paletteSettings = matchSettings();
  flatListRef.current = []
    .concat(paletteCommands)
    .concat(paletteSettings)
    .concat(archiveMode ? results : flattenGroups(groups));

  var totalCount = results.length;
  var placeholder = archiveMode ? t("search.placeholderArchive") : t("search.placeholder");
  var emptyText = archiveMode ? t("search.emptyStateArchive") : t("search.emptyState");
  var noResultsText = archiveMode ? t("search.noResultsArchive") : t("search.noResults");

  return React.createElement("div", { className: "search-overlay", onClick: function (e) { if (e.target === e.currentTarget) onClose && onClose(); } },
    React.createElement("div", { className: "search-overlay-panel", onClick: function (e) { e.stopPropagation(); } },
      // Header with search input
      React.createElement("div", { className: "search-overlay-header" },
        React.createElement("svg", { className: "search-icon", width: "16", height: "16", viewBox: "0 0 20 20", fill: "none", stroke: "currentColor", strokeWidth: "1.6" },
          React.createElement("circle", { cx: "9", cy: "9", r: "5" }),
          React.createElement("path", { d: "M13 13 L17 17" })
        ),
        React.createElement("input",
          {
            ref: inputRef,
            type: "text",
            value: query,
            onChange: function (e) { setQuery(e.target.value); },
            placeholder: placeholder,
            "aria-label": placeholder,
          }
        ),
        React.createElement("button", { type: "button", className: "search-overlay-close", onClick: onClose, title: t("search.close") },
          t("search.closeShortcut")
        )
      ),

      // Type filters (Workbench mode)
      !archiveMode && React.createElement("div", { className: "search-overlay-filters", role: "tablist", "aria-label": t("search.filterLabel"), key: "filters" },
        searchTypes.map(function (item) {
          return React.createElement("button", {
            type: "button",
            role: "tab",
            "aria-selected": activeType === item.id,
            key: item.id,
            className: "search-type-chip" + (activeType === item.id ? " active" : ""),
            onClick: function () { setActiveType(item.id); },
          }, t(item.labelKey));
        }),
        React.createElement("span", { className: "search-filter-separator" }),
        React.createElement("button", {
          type: "button",
          className: "search-archive-toggle" + (archiveMode ? " active" : ""),
          onClick: function () { setArchiveMode(!archiveMode); },
          title: t("search.archiveToggleHint"),
        }, t("search.archiveToggle"))
      ),

      // Plugin-owned conversation archive filter bar
      archiveMode && React.createElement("div", { className: "search-overlay-filters archive", key: "filters-archive" },
        React.createElement("button", {
          type: "button",
          className: "search-archive-toggle active",
          onClick: function () { setArchiveMode(false); },
        }, "← " + t("search.archiveToggle")),
        React.createElement("span", { className: "search-filter-note" }, t("search.archiveToggleHint"))
      ),

      // Body
      React.createElement("div", {
        className: "search-overlay-body",
        ref: resultsRef,
        "aria-label": t("search.resultsLabel"),
        "aria-live": "polite",
        "aria-atomic": "false",
        key: "body"
      },
        // Results count (inside body so it shares the solid background)
        status === "done" && totalCount > 0 && React.createElement("div", { className: "search-results-count", key: "count" },
          t("search.resultsCount", { n: totalCount })
        ),

        // Loading state
        status === "loading" && React.createElement("div", { className: "search-loading-state" },
          React.createElement("span", { style: { opacity: 0.5 } }, t("search.loading"))
        ),

        // Initial empty state — the three New actions only show while the
        // query is empty; typing switches to search results and hides them.
        status === "idle" && React.createElement("div", { className: "search-empty-state" },
          React.createElement("div", { className: "empty-icon" },
            React.createElement("svg", { width: "40", height: "40", viewBox: "0 0 24 24", fill: "none", stroke: "currentColor", strokeWidth: "1.2" },
              React.createElement("circle", { cx: "11", cy: "11", r: "6" }),
              React.createElement("path", { d: "M16.5 16.5 L21 21" })
            )
          ),
          React.createElement("span", null, emptyText),
          React.createElement("div", { className: "search-empty-actions", role: "group", "aria-label": t("search.newButton") },
            SEARCH_COMMANDS.slice(0, 3).map(function (cmd, idx, arr) {
              return renderNewAction(
                { kind: "command", id: cmd.id, label: t(cmd.labelKey), hint: cmd.hintKey ? t(cmd.hintKey) : "" },
                idx, arr
              );
            })
          ),
          React.createElement("div", { className: "search-empty-tip" }, t("search.emptyTip"))
        ),

        // No results
        status === "done" && totalCount === 0 && paletteCommands.length === 0 && paletteSettings.length === 0 && React.createElement("div", { className: "search-no-results" },
          noResultsText
        ),

        // Error state
        status === "error" && React.createElement("div", { className: "search-error-state" },
          t("search.error")
        ),

        // Results
        status === "done" && renderCommandPaletteGroups(paletteCommands, paletteSettings),

        status === "done" && archiveMode && results.map(function (result, index) {
          return renderArchiveResult(result, index);
        }),

        status === "done" && !archiveMode && renderGroupedResults()
      )
    )
  );
}

window.CyreneUI.search = window.CyreneUI.register("search", {
  Overlay: SearchOverlay,
});
