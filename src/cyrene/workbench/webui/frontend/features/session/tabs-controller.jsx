import { wbRecentSessionTabs, wbRememberOpenedSessionKey } from "./activity.jsx"

var { useState } = React;

function readSessionKeys(storageKey, pattern, limit) {
  try {
    var stored = JSON.parse(localStorage.getItem(storageKey) || "[]");
    return Array.isArray(stored) ? stored.filter(function (key) {
      return pattern.test(String(key || ""));
    }).slice(0, limit) : [];
  } catch (e) {
    return [];
  }
}

function writeSessionKeys(storageKey, values) {
  try { localStorage.setItem(storageKey, JSON.stringify(values)); } catch (e) {}
}

function useWorkbenchSessionTabs(projects, recentChatsByProject) {
  var [recentOpenedSessionKeys, setRecentOpenedSessionKeys] = useState(function () {
    return readSessionKeys("wb-recent-opened-sessions", /^chat:.+/, 20);
  });
  var [pinnedSessionKeys, setPinnedSessionKeys] = useState(function () {
    return readSessionKeys("wb-pinned-sessions", /^chat:.+/, 20);
  });
  var [hiddenSessionKeys, setHiddenSessionKeys] = useState(function () {
    return readSessionKeys("wb-hidden-session-tabs", /^chat:.+/, 100);
  });

  function rememberOpenedSession(kind, sessionId) {
    if (kind !== "chat") return;
    var normalizedId = String(sessionId || "");
    if (!normalizedId) return;
    var key = "chat:" + normalizedId;
    setRecentOpenedSessionKeys(function (prev) {
      var visibleKeys = wbRecentSessionTabs(
        projects,
        recentChatsByProject,
        prev,
        pinnedSessionKeys,
        hiddenSessionKeys,
        3
      ).map(function (item) { return item.kind + ":" + item.id; });
      var next = wbRememberOpenedSessionKey(prev, visibleKeys, key, 20);
      if (next === prev) return prev;
      writeSessionKeys("wb-recent-opened-sessions", next);
      return next;
    });
    setHiddenSessionKeys(function (prev) {
      if (!Array.isArray(prev) || prev.indexOf(key) < 0) return prev;
      var next = prev.filter(function (item) { return item !== key; });
      writeSessionKeys("wb-hidden-session-tabs", next);
      return next;
    });
  }

  function togglePinnedSession(item, forcePinned) {
    if (!item || !item.id) return;
    var key = item.kind + ":" + item.id;
    var shouldPin = typeof forcePinned === "boolean" ? forcePinned : pinnedSessionKeys.indexOf(key) < 0;
    setPinnedSessionKeys(function (prev) {
      var list = Array.isArray(prev) ? prev : [];
      var next = shouldPin
        ? [key].concat(list.filter(function (entry) { return entry !== key; })).slice(0, 20)
        : list.filter(function (entry) { return entry !== key; });
      writeSessionKeys("wb-pinned-sessions", next);
      return next;
    });
    if (shouldPin) {
      setHiddenSessionKeys(function (prev) {
        if (!Array.isArray(prev) || prev.indexOf(key) < 0) return prev;
        var next = prev.filter(function (entry) { return entry !== key; });
        writeSessionKeys("wb-hidden-session-tabs", next);
        return next;
      });
    }
  }

  function removeSessionTab(item) {
    if (!item || !item.id) return;
    var key = item.kind + ":" + item.id;
    setPinnedSessionKeys(function (prev) {
      var next = (Array.isArray(prev) ? prev : []).filter(function (entry) { return entry !== key; });
      writeSessionKeys("wb-pinned-sessions", next);
      return next;
    });
    setRecentOpenedSessionKeys(function (prev) {
      var next = (Array.isArray(prev) ? prev : []).filter(function (entry) { return entry !== key; });
      writeSessionKeys("wb-recent-opened-sessions", next);
      return next;
    });
    setHiddenSessionKeys(function (prev) {
      var next = [key].concat((Array.isArray(prev) ? prev : []).filter(function (entry) {
        return entry !== key;
      })).slice(0, 100);
      writeSessionKeys("wb-hidden-session-tabs", next);
      return next;
    });
  }

  return {
    recentOpenedSessionKeys: recentOpenedSessionKeys,
    pinnedSessionKeys: pinnedSessionKeys,
    hiddenSessionKeys: hiddenSessionKeys,
    rememberOpenedSession: rememberOpenedSession,
    togglePinnedSession: togglePinnedSession,
    removeSessionTab: removeSessionTab,
  };
}

export { useWorkbenchSessionTabs }
