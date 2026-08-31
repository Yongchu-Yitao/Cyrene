import { workbenchServices } from "../../shared/runtime/services.jsx"

var sessionTabResourceCache = {};
var SESSION_TAB_RESOURCE_CACHE_TTL = 30000;

function sessionTabResourceKey(item) {
  return String(item && item.kind || "") + ":" + String(item && item.id || "");
}

function peekSessionTabResources(item) {
  var entry = sessionTabResourceCache[sessionTabResourceKey(item)];
  return entry && entry.value ? entry.value : null;
}

function loadSessionTabBrowserPreview(item) {
  if (!item || !item.id) return Promise.resolve(null);
  var bridge = window.cyrene && window.cyrene.browser;
  var browserStatePromise = item.kind === "chat" && bridge && typeof bridge.getState === "function"
    ? bridge.getState(item.id).catch(function () { return null; })
    : Promise.resolve(null);
  return browserStatePromise.then(function (browserState) {
    var hasBrowser = !!(browserState && Array.isArray(browserState.tabs) && browserState.tabs.length);
    if (!hasBrowser) return null;
    var activeTab = browserState.tabs.find(function (tab) {
      return String(tab && tab.id || "") === String(browserState.activeTabId || "");
    }) || browserState.tabs[0] || {};
    var fallback = { title: String(activeTab.title || ""), url: String(activeTab.url || ""), previewUrl: "" };
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
  var cacheKey = sessionTabResourceKey(item);
  var cached = sessionTabResourceCache[cacheKey];
  if (cached && cached.promise) return cached.promise;
  if (cached && cached.value && Date.now() - cached.updatedAt < SESSION_TAB_RESOURCE_CACHE_TTL) {
    return Promise.resolve(cached.value);
  }
  var browserPreviewPromise = loadSessionTabBrowserPreview(item);
  var filesPromise = item.kind === "chat"
    ? workbenchServices.api().json("/api/workbench/chats/" + encodeURIComponent(item.id), { toast: false })
      .then(function (payload) {
        var files = [];
        var seen = {};
        var messages = payload && payload.chat && Array.isArray(payload.chat.messages) ? payload.chat.messages : [];
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
  var promise = Promise.all([browserPreviewPromise, filesPromise]).then(function (results) {
    var value = { browser: results[0], files: results[1] };
    sessionTabResourceCache[cacheKey] = { value: value, updatedAt: Date.now(), promise: null };
    return value;
  }).catch(function (error) {
    if (cached && cached.value) {
      sessionTabResourceCache[cacheKey] = cached;
      return cached.value;
    }
    delete sessionTabResourceCache[cacheKey];
    throw error;
  });
  sessionTabResourceCache[cacheKey] = {
    value: cached && cached.value || null,
    updatedAt: cached && cached.updatedAt || 0,
    promise: promise,
  };
  return promise;
}

loadSessionTabResources.peek = peekSessionTabResources;

export { loadSessionTabBrowserPreview, loadSessionTabResources, peekSessionTabResources }
