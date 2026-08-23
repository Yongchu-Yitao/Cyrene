import { workbenchServices } from "../../shared/runtime/services.jsx"
import { wbSuppressOnScreenNotifications } from "../session/activity.jsx"

var useWorkbenchEffect = React.useEffect;

function wbVisibleNotificationView(activeView) {
  if (typeof document !== "undefined" && document.hidden) return null;
  if (activeView.page === "chat" && activeView.chatId) return { chatId: activeView.chatId };
  if (!activeView.page && activeView.sessionId) return { sessionId: activeView.sessionId };
  return null;
}

function wbReloadNotifications(context, tab, limit) {
  var visibleView = wbVisibleNotificationView(context.activeViewRef.current);
  return context.model.fetchNotifications(tab || "all", limit || 80, visibleView).then(function (payload) {
    payload = wbSuppressOnScreenNotifications(payload, context.activeViewRef.current, context.model);
    context.setNotifications({
      items: Array.isArray(payload.items) ? payload.items : [],
      counts: payload.counts || { all: 0, mention: 0, comment: 0, system: 0 },
      unreadByTab: payload.unreadByTab || { all: 0, mention: 0, comment: 0, system: 0 },
      unreadCount: Number(payload.unreadCount || 0),
    });
    return payload;
  }).catch(function () {});
}

function wbReloadRecentChats(context, projects) {
  var projectList = Array.isArray(projects) ? projects : [];
  var requestSequence = context.recentChatsLoadSeqRef.current + 1;
  context.recentChatsLoadSeqRef.current = requestSequence;
  if (!projectList.length) {
    context.setRecentChatsByProject({});
    return Promise.resolve({});
  }
  var api = workbenchServices.api();
  return Promise.all(projectList.map(function (project) {
    var projectId = String((project && project.id) || "");
    if (!projectId) return Promise.resolve({ projectId: "", chats: [] });
    return api.json("/api/workbench/chats?project=" + encodeURIComponent(projectId), { toast: false })
      .then(function (payload) {
        return { projectId: projectId, chats: payload && Array.isArray(payload.chats) ? payload.chats : [] };
      })
      .catch(function () { return { projectId: projectId, chats: [] }; });
  })).then(function (results) {
    if (context.recentChatsLoadSeqRef.current !== requestSequence) return null;
    var next = {};
    results.forEach(function (result) { if (result.projectId) next[result.projectId] = result.chats; });
    context.setRecentChatsByProject(next);
    return next;
  });
}

function wbReloadPinnedResources(context) {
  return workbenchServices.api().json("/api/workbench/pinned-resources", { toast: false })
    .then(function (payload) {
      var resources = payload && Array.isArray(payload.resources) ? payload.resources : [];
      context.setPinnedResources(resources);
      return resources;
    })
    .catch(function () { return []; });
}

function wbPinTopbarResource(context, resource) {
  if (!resource || ["file", "browser", "snippet", "conversation"].indexOf(resource.kind) < 0) return Promise.resolve(null);
  var enriched = Object.assign({}, resource);
  if (enriched.ownerSessionId && (!enriched.ownerProjectId || !enriched.ownerProjectName)) {
    var owner = context.getSessionTabCandidates().find(function (item) {
      return item.kind === "chat" && String(item.id || "") === String(enriched.ownerSessionId || "");
    });
    if (owner) {
      if (!enriched.ownerProjectId) enriched.ownerProjectId = owner.projectId;
      enriched.ownerProjectName = owner.projectName || "";
    }
  }
  return workbenchServices.api().json("/api/workbench/pinned-resources", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify(enriched), toast: false,
  }).then(function (payload) {
    var item = payload && payload.resource;
    if (item) {
      context.setPinnedResources(function (previous) {
        return [item].concat((previous || []).filter(function (entry) { return entry.id !== item.id; }));
      });
      workbenchServices.feedback().showToast(context.t("workbench.resourceShelf.pinned", "Pinned to topbar"), "success");
    }
    return item;
  }).catch(function (error) {
    workbenchServices.feedback().showToast(error.message || String(error), "error");
    return null;
  });
}

function wbUnpinTopbarResource(context, resource) {
  if (!resource || !resource.id) return Promise.resolve(false);
  return workbenchServices.api().fetch(
    "/api/workbench/pinned-resources/" + encodeURIComponent(resource.id),
    { method: "DELETE", toast: false }
  ).then(function (response) {
    if (!response.ok) throw new Error("HTTP " + response.status);
    context.setPinnedResources(function (previous) {
      return (previous || []).filter(function (item) { return item.id !== resource.id; });
    });
    return true;
  }).catch(function (error) {
    workbenchServices.feedback().showToast(error.message || String(error), "error");
    return false;
  });
}

function useWorkbenchShellResources(context) {
  function reloadNotifications(tab, limit) { return wbReloadNotifications(context, tab, limit); }
  function reloadRecentChats(projects) { return wbReloadRecentChats(context, projects); }
  function reloadPinnedResources() { return wbReloadPinnedResources(context); }
  function pinTopbarResource(resource) { return wbPinTopbarResource(context, resource); }
  function unpinTopbarResource(resource) { return wbUnpinTopbarResource(context, resource); }
  wbUsePinnedResourceLifecycle(reloadPinnedResources, pinTopbarResource);
  return { reloadNotifications, reloadRecentChats, pinTopbarResource, unpinTopbarResource };
}

function wbUsePinnedResourceLifecycle(reloadPinnedResources, pinTopbarResource) {
  useWorkbenchEffect(function () {
    reloadPinnedResources();
    function pinFromDrag(event) { if (event && event.detail) pinTopbarResource(event.detail); }
    window.addEventListener("cyrene:pin-topbar-resource", pinFromDrag);
    return function () { window.removeEventListener("cyrene:pin-topbar-resource", pinFromDrag); };
  }, []);
}

export { useWorkbenchShellResources }
