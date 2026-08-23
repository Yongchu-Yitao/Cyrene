import { workbenchServices } from "../../shared/runtime/services.jsx"

function createWorkbenchSelectionActions(
  store,
  setStore,
  setExpandedStepId,
  setTaskView,
  fetchAndMergeSession,
  setFullPage,
  setTaskOpenRequest,
  rememberOpenedSession,
  navigateFromSearch
) {
  function selectProject(projectId) {
    var project = store.projects.find(function (item) { return item.id === projectId; });
    if (!project) return;
    var nextSession = project.sessions[0] || null;
    var nextSessionId = nextSession ? nextSession.id : "";
    setStore(function (previous) {
      return Object.assign({}, previous, {
        activeProjectId: project.id,
        activeProject: project,
        activeSession: nextSession,
        activeSessionId: nextSessionId,
      });
    });
    setExpandedStepId("");
    setTaskView("board");
    workbenchServices.model().setActiveProject(project.id, nextSessionId).catch(function () {});
  }

  function selectSession(sessionId) {
    var project = store.activeProject;
    if (!project) return;
    var session = project.sessions.find(function (item) { return item.id === sessionId; });
    if (!session) return;
    setStore(function (previous) {
      return Object.assign({}, previous, { activeSessionId: session.id, activeSession: session });
    });
    setTaskView("detail");
    setExpandedStepId("");
    if (session.isSummary) fetchAndMergeSession(session.id);
    workbenchServices.model().setActiveProject(project.id, sessionId).catch(function () {});
  }

  function openTask(sessionId) {
    var id = String(sessionId || "");
    if (!id) return;
    selectSession(id);
    setFullPage("chat");
    setTaskOpenRequest(function (current) {
      return { id: id, sequence: Number(current && current.sequence || 0) + 1 };
    });
  }

  function openChat(chat) {
    if (!chat || !chat.id) return;
    rememberOpenedSession("chat", chat.id);
    navigateFromSearch({
      type: "chat",
      projectId: chat.projectId || (store.activeProject && store.activeProject.id) || "",
      chatId: chat.id,
    });
  }

  return { selectProject: selectProject, selectSession: selectSession, openTask: openTask, openChat: openChat };
}

export { createWorkbenchSelectionActions }
