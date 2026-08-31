import { wbRememberOpenedSessionKey } from "./activity.jsx"

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
    return readSessionKeys("wb-recent-opened-sessions", /^(chat|terminal|file|plugin-view):.+/, 40);
  });
  var [pinnedSessionKeys, setPinnedSessionKeys] = useState(function () {
    return readSessionKeys("wb-pinned-sessions", /^(chat|terminal|file|plugin-view):.+/, 40);
  });
  var [hiddenSessionKeys, setHiddenSessionKeys] = useState(function () {
    return readSessionKeys("wb-hidden-session-tabs", /^(chat|terminal|file|plugin-view):.+/, 100);
  });

  function rememberOpenedSession(kind, sessionId, visibleSessionKeys) {
    if (kind !== "chat") return;
    var normalizedId = String(sessionId || "");
    if (!normalizedId) return;
    var key = "chat:" + normalizedId;
    setRecentOpenedSessionKeys(function (prev) {
      var next = wbRememberOpenedSessionKey(prev, visibleSessionKeys, key, 40);
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
        ? [key].concat(list.filter(function (entry) { return entry !== key; })).slice(0, 40)
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

  function reorderPinnedSession(sourceKey, targetKey, after) {
    var source = String(sourceKey || "");
    var target = String(targetKey || "");
    if (!source || !target || source === target) return;
    setPinnedSessionKeys(function (previous) {
      var list = Array.isArray(previous) ? previous.slice() : [];
      if (list.indexOf(source) < 0 || list.indexOf(target) < 0) return previous;
      list.splice(list.indexOf(source), 1);
      var targetIndex = list.indexOf(target);
      list.splice(targetIndex + (after ? 1 : 0), 0, source);
      writeSessionKeys("wb-pinned-sessions", list);
      return list;
    });
  }

  function movePinnedSession(sourceKey, offset) {
    var source = String(sourceKey || "");
    var direction = Number(offset) < 0 ? -1 : 1;
    setPinnedSessionKeys(function (previous) {
      var list = Array.isArray(previous) ? previous.slice() : [];
      var sourceIndex = list.indexOf(source);
      var targetIndex = sourceIndex + direction;
      if (sourceIndex < 0 || targetIndex < 0 || targetIndex >= list.length) return previous;
      var target = list[targetIndex];
      list[targetIndex] = source;
      list[sourceIndex] = target;
      writeSessionKeys("wb-pinned-sessions", list);
      return list;
    });
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
    reorderPinnedSession: reorderPinnedSession,
    movePinnedSession: movePinnedSession,
    removeSessionTab: removeSessionTab,
  };
}

export { useWorkbenchSessionTabs }
