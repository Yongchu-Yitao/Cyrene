import { useWbcEffect, useWbcState } from "./core.jsx"
import { wbcErrorText } from "./errors.jsx"
import { WorkbenchChatModel } from "./model-api.jsx"

var WBC_EMPTY_CHANGES = Object.freeze({
  changeSets: [],
  fileCount: 0,
  additions: 0,
  deletions: 0,
});
var wbcConversationChangesCache = new Map();
var wbcConversationChangesListeners = new Map();
var wbcConversationChangesRequests = new Map();
var wbcConversationChangesPending = new Set();
var wbcConversationChangesTimers = new Map();

function wbcEmptyConversationChanges(chatId) {
  return {
    chatId: String(chatId || ""),
    payload: WBC_EMPTY_CHANGES,
    hasChanges: false,
    loading: false,
    loaded: false,
    error: "",
  };
}

function wbcConversationChangesSnapshot(chatId) {
  var key = String(chatId || "");
  return wbcConversationChangesCache.get(key) || wbcEmptyConversationChanges(key);
}

function wbcPublishConversationChanges(chatId, next) {
  var key = String(chatId || "");
  if (!key) return;
  wbcConversationChangesCache.set(key, next);
  var listeners = wbcConversationChangesListeners.get(key);
  if (!listeners) return;
  listeners.forEach(function (listener) { listener(next); });
}

function wbcSubscribeConversationChanges(chatId, listener) {
  var key = String(chatId || "");
  if (!key) return function () {};
  var listeners = wbcConversationChangesListeners.get(key);
  if (!listeners) {
    listeners = new Set();
    wbcConversationChangesListeners.set(key, listeners);
  }
  listeners.add(listener);
  return function () {
    listeners.delete(listener);
    if (!listeners.size) wbcConversationChangesListeners.delete(key);
  };
}

function wbcNormalizeConversationChanges(payload) {
  var source = payload && typeof payload === "object" ? payload : {};
  return {
    ...source,
    changeSets: Array.isArray(source.changeSets) ? source.changeSets : [],
    fileCount: Number(source.fileCount || 0),
    additions: Number(source.additions || 0),
    deletions: Number(source.deletions || 0),
  };
}

export function wbcRefreshConversationChanges(chatId, options) {
  var key = String(chatId || "");
  if (!key) return Promise.resolve(wbcEmptyConversationChanges(""));
  var activeRequest = wbcConversationChangesRequests.get(key);
  if (activeRequest) {
    wbcConversationChangesPending.add(key);
    return activeRequest;
  }

  var current = wbcConversationChangesSnapshot(key);
  var background = !!(options && options.background);
  wbcPublishConversationChanges(key, {
    ...current,
    loading: background ? current.loading : true,
    error: "",
  });

  var request = WorkbenchChatModel.getChanges(key, { toast: false })
    .then(function (payload) {
      var normalized = wbcNormalizeConversationChanges(payload);
      var latest = wbcConversationChangesSnapshot(key);
      var next = {
        chatId: key,
        payload: normalized,
        // The event is emitted as soon as a snapshot is committed. Keep that
        // authoritative signal if a concurrently started request returns an
        // older, empty view of the same conversation.
        hasChanges: latest.hasChanges || normalized.changeSets.length > 0,
        loading: false,
        loaded: true,
        error: "",
      };
      wbcPublishConversationChanges(key, next);
      return next;
    })
    .catch(function (error) {
      var latest = wbcConversationChangesSnapshot(key);
      var next = {
        ...latest,
        loading: false,
        error: wbcErrorText(error),
      };
      wbcPublishConversationChanges(key, next);
      return next;
    })
    .finally(function () {
      if (wbcConversationChangesRequests.get(key) === request) {
        wbcConversationChangesRequests.delete(key);
      }
      if (wbcConversationChangesPending.has(key)) {
        wbcConversationChangesPending.delete(key);
        wbcRefreshConversationChanges(key, { background: true });
      }
    });
  wbcConversationChangesRequests.set(key, request);
  return request;
}

function wbcScheduleConversationChangesRefresh(chatId) {
  var key = String(chatId || "");
  if (!key) return;
  var currentTimer = wbcConversationChangesTimers.get(key);
  if (currentTimer) clearTimeout(currentTimer);
  wbcConversationChangesTimers.set(key, setTimeout(function () {
    wbcConversationChangesTimers.delete(key);
    wbcRefreshConversationChanges(key, { background: true });
  }, 80));
}

function wbcHandleConversationChangesEvent(event) {
  var detail = (event && event.detail) || {};
  var eventChatId = String(detail.chatId || detail.chat_id || detail.sessionId || detail.session_id || "");
  var chatIds = eventChatId ? [eventChatId] : Array.from(wbcConversationChangesCache.keys());
  chatIds.forEach(function (chatId) {
    if (Number(detail.fileCount || 0) > 0) {
      var current = wbcConversationChangesSnapshot(chatId);
      wbcPublishConversationChanges(chatId, { ...current, hasChanges: true });
    }
    wbcScheduleConversationChangesRefresh(chatId);
  });
}

if (typeof window !== "undefined") {
  window.addEventListener("workbench:workspace-changes", wbcHandleConversationChangesEvent);
}

export function useWbcConversationChanges(chatId) {
  var key = String(chatId || "");
  var [snapshot, setSnapshot] = useWbcState(function () {
    return wbcConversationChangesSnapshot(key);
  });

  useWbcEffect(function () {
    if (!key) {
      setSnapshot(wbcEmptyConversationChanges(""));
      return undefined;
    }
    var unsubscribe = wbcSubscribeConversationChanges(key, setSnapshot);
    var current = wbcConversationChangesSnapshot(key);
    setSnapshot(current);
    if (!current.loaded && !current.loading) {
      wbcRefreshConversationChanges(key, { background: false });
    }
    return unsubscribe;
  }, [key]);

  return {
    ...snapshot,
    refresh: function (background) {
      return wbcRefreshConversationChanges(key, { background: !!background });
    },
  };
}
