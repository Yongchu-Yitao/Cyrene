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

function SearchOverlay({ onClose }) {
  var { t, lang } = useI18n();
  var inputRef = useRefSr(null);
  var resultsRef = useRefSr(null);
  var [query, setQuery] = useStateSr("");
  var [activeType, setActiveType] = useStateSr("all");
  var [legacyMode, setLegacyMode] = useStateSr(false);
  var [results, setResults] = useStateSr([]);
  var [groups, setGroups] = useStateSr({});
  var [status, setStatus] = useStateSr("idle"); // idle | loading | done | error
  var debounceRef = useRefSr(null);
  var abortRef = useRefSr(null);
  var requestSeqRef = useRefSr(0);

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
      return Array.prototype.slice.call(resultsRef.current.querySelectorAll(".search-result-item"));
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
      var resultFocused = active && active.classList && active.classList.contains("search-result-item");
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
        var idx = nodes.indexOf(active);
        var flat = legacyMode ? results : flattenGroups(groups);
        var selected = flat[idx];
        if (selected) handleResultClick(selected);
      }
    }
    window.addEventListener("keydown", onKeyDown);
    return function () { window.removeEventListener("keydown", onKeyDown); };
  }, [onClose, groups, results, legacyMode]);

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
      if (legacyMode) {
        doLegacySearch(q, controller, requestId);
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
  }, [query, activeType, legacyMode]);

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

  async function doLegacySearch(q, controller, requestId) {
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
    SEARCH_TYPE_ORDER.forEach(function (type) {
      var arr = g[type];
      if (Array.isArray(arr)) {
        arr.forEach(function (item) { out.push(item); });
      }
    });
    return out;
  }

  function resultTypeLabel(type) {
    return t("search.result." + (type || "conversation"), {}, type || "");
  }

  function navigateWorkbench(result) {
    if (window.__workbenchNavigate) {
      window.__workbenchNavigate({
        type: result.type || "conversation",
        id: result.id,
        projectId: result.projectId,
        sessionId: result.sessionId,
        chatId: result.chatId,
        docId: result.docId,
        memId: result.memId,
        taskId: result.taskId,
        entityId: result.entityId,
      });
      onClose && onClose();
      return true;
    }
    return false;
  }

  function handleResultClick(result) {
    // Workbench-aware navigation for project/task/chat/knowledge/memory/schedule.
    if (!legacyMode && navigateWorkbench(result)) {
      return;
    }

    // Legacy fallback for conversation archives.
    if (window.selectUiSession) {
      var date = result.date;
      var session = (DATA.sessions || []).find(function (s) {
        return s.archiveDate === date || (s.id && s.id.indexOf(date) !== -1);
      });
      if (session) {
        window.selectUiSession(session.id);
      }
    }
    if (window.__setAppPage) {
      window.__setAppPage("sessions");
    }
    onClose && onClose();
  }

  function handleResultKeyDown(e, result) {
    if (e.key === "Enter" || e.key === " ") {
      e.preventDefault();
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

  function renderLegacyResult(result, index) {
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
    SEARCH_TYPE_ORDER.forEach(function (type) {
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

  var totalCount = results.length;
  var placeholder = legacyMode ? t("search.placeholderLegacy") : t("search.placeholder");
  var emptyText = legacyMode ? t("search.emptyStateLegacy") : t("search.emptyState");
  var noResultsText = legacyMode ? t("search.noResultsLegacy") : t("search.noResults");

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
      !legacyMode && React.createElement("div", { className: "search-overlay-filters", role: "tablist", "aria-label": t("search.filterLabel"), key: "filters" },
        SEARCH_TYPES.map(function (item) {
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
          className: "search-legacy-toggle" + (legacyMode ? " active" : ""),
          onClick: function () { setLegacyMode(!legacyMode); },
          title: t("search.legacyToggleHint"),
        }, t("search.legacyToggle"))
      ),

      // Legacy mode filter bar
      legacyMode && React.createElement("div", { className: "search-overlay-filters legacy", key: "filters-legacy" },
        React.createElement("button", {
          type: "button",
          className: "search-legacy-toggle active",
          onClick: function () { setLegacyMode(false); },
        }, "← " + t("search.legacyToggle")),
        React.createElement("span", { className: "search-filter-note" }, t("search.legacyToggleHint"))
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

        // Initial empty state
        status === "idle" && React.createElement("div", { className: "search-empty-state" },
          React.createElement("div", { className: "empty-icon" },
            React.createElement("svg", { width: "40", height: "40", viewBox: "0 0 24 24", fill: "none", stroke: "currentColor", strokeWidth: "1.2" },
              React.createElement("circle", { cx: "11", cy: "11", r: "6" }),
              React.createElement("path", { d: "M16.5 16.5 L21 21" })
            )
          ),
          React.createElement("span", null, emptyText)
        ),

        // No results
        status === "done" && totalCount === 0 && React.createElement("div", { className: "search-no-results" },
          noResultsText
        ),

        // Error state
        status === "error" && React.createElement("div", { className: "search-error-state" },
          t("search.error")
        ),

        // Results
        status === "done" && legacyMode && results.map(function (result, index) {
          return renderLegacyResult(result, index);
        }),

        status === "done" && !legacyMode && renderGroupedResults()
      )
    )
  );
}

window.SearchOverlay = SearchOverlay;
