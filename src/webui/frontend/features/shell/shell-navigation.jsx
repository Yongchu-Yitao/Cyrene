import { workbenchServices } from "../../shared/runtime/services.jsx"
import { wbNotificationNavigationTarget } from "../session/activity.jsx"

function wbNormalizeNavigationPayload(payload) {
  if (!payload || !payload.type) return null;
  if (payload.type !== "conversation") return payload;
  return {
    ...payload, type: "chat",
    chatId: payload.chatId || payload.sessionId || payload.id,
  };
}

function wbNavigateFromSearch(context, requestedPayload) {
  var payload = wbNormalizeNavigationPayload(requestedPayload);
  if (!payload) return;
  var type = payload.type;
  var projectId = payload.projectId;
  var project = projectId
    ? context.store.projects.find(function (item) { return item.id === projectId; })
    : context.store.activeProject;
  if (!project) return;
  var pageMap = { chat: "chat", knowledge: "knowledge", memory: "memory", schedule: "schedule" };
  if (type === "task" && project && payload.sessionId) {
    var session = project.sessions.find(function (item) { return item.id === payload.sessionId; });
    if (session) {
      context.setStore(function (previous) {
        return {
          ...previous, activeProjectId: project.id, activeProject: project,
          activeSessionId: session.id, activeSession: session,
        };
      });
      context.setExpandedStepId("");
      context.setTaskView("detail");
      context.setFullPage("chat");
      context.setTaskOpenRequest(function (current) {
        return { id: session.id, sequence: Number(current && current.sequence || 0) + 1 };
      });
      context.model.setActiveProject(project.id, session.id).catch(function () {});
    }
  } else if (type === "project" && project) {
    context.getSelectProject()(project.id);
    context.setTaskView("board");
    context.setFullPage(null);
  } else {
    if (project && project.id !== context.store.activeProjectId) context.getSelectProject()(project.id);
    if (pageMap[type] && (
      !Array.isArray(context.enabledModules)
      || context.enabledModules.indexOf(pageMap[type]) >= 0
    )) context.setFullPage(pageMap[type]);
  }
  var navigation = workbenchServices.navigation();
  navigation.setPending(payload);
  try { window.dispatchEvent(new CustomEvent("cyrene:workbench-navigate", { detail: payload })); } catch (error) {}
  setTimeout(function () { navigation.clearPending(payload); }, 5000);
}

function wbNavigateFromNotification(context, navigateFromSearch, item) {
  var meta = item && item.meta && typeof item.meta === "object" ? item.meta : {};
  if (meta.category === "app_update" || String(item && item.source || "") === "updater") {
    context.setSettingsTab("about");
    context.setSettingsScrollTo(null);
    context.setFullPage("settings");
    return true;
  }
  var target = wbNotificationNavigationTarget(item);
  if (!target) return false;
  navigateFromSearch(target);
  return true;
}

function createWorkbenchShellNavigation(context) {
  function navigateFromSearch(payload) { return wbNavigateFromSearch(context, payload); }
  function navigateFromNotification(item) {
    return wbNavigateFromNotification(context, navigateFromSearch, item);
  }
  return { navigateFromSearch, navigateFromNotification };
}

export { createWorkbenchShellNavigation }
