import { workbenchServices } from "../../shared/runtime/services.jsx"

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
  return Promise.all([browserPreviewPromise, filesPromise]).then(function (results) {
    return { browser: results[0], files: results[1] };
  });
}

export { loadSessionTabBrowserPreview, loadSessionTabResources }
