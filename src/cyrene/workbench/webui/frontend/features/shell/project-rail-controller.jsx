import { workbenchServices } from "../../shared/runtime/services.jsx"

function createWorkbenchProjectRailActions(
  store,
  setStore,
  recentChatsByProject,
  setRecentChatsByProject,
  chatModule,
  model,
  t,
  projectRailToTaskBusy,
  setProjectRailToTaskBusy,
  openChatInWorkspace,
  handleChatToTask,
  setFullPage
) {
  function selectChat(chatId) {
    var chats = store.activeProject && recentChatsByProject[store.activeProject.id] || [];
    var chat = chats.find(function (item) { return String(item && item.id || "") === String(chatId || ""); });
    if (chat) openChatInWorkspace(chat);
  }

  function renameChat(chatId, title) {
    return chatModule.Model.renameChat(chatId, title).then(function (chat) {
      setRecentChatsByProject(function (current) {
        var projectId = String(store.activeProject && store.activeProject.id || "");
        var nextChats = (current[projectId] || []).map(function (item) {
          return String(item.id) === String(chat.id) ? Object.assign({}, item, chat) : item;
        });
        return Object.assign({}, current, { [projectId]: nextChats });
      });
      return chat;
    });
  }

  function renameTask(taskId, title) {
    return model.patchSession(taskId, { title: title }).then(function (next) {
      setStore(next);
      var project = next && next.projects && next.projects.find(function (item) {
        return (item.sessions || []).some(function (session) { return String(session.id) === String(taskId); });
      });
      return project && project.sessions.find(function (session) { return String(session.id) === String(taskId); });
    });
  }

  function deleteChat(chatId) {
    if (!chatId) return Promise.resolve(null);
    return workbenchServices.feedback().confirmModal({
      body: t("workbenchChat.confirmDelete", "Delete this chat? Its messages cannot be recovered."),
      confirmLabel: t("common.delete", "Delete"),
      danger: true,
    }).then(function (confirmed) {
      return confirmed ? chatModule.Model.deleteChat(chatId) : null;
    }).then(function (result) {
      if (!result) return null;
      setRecentChatsByProject(function (current) {
        var projectId = String(store.activeProject && store.activeProject.id || "");
        return Object.assign({}, current, {
          [projectId]: (current[projectId] || []).filter(function (item) { return String(item.id) !== String(chatId); }),
        });
      });
      return result;
    });
  }

  function promoteChat(chatId) {
    if (!chatId || projectRailToTaskBusy) return Promise.resolve(null);
    setProjectRailToTaskBusy(true);
    return chatModule.Model.toTask(chatId, {}).then(function (payload) {
      handleChatToTask(payload);
      return payload;
    }).finally(function () { setProjectRailToTaskBusy(false); });
  }

  function openResource(type, payload) {
    setFullPage("chat");
    window.setTimeout(function () {
      try {
        window.dispatchEvent(new CustomEvent("cyrene:workbench-navigate", {
          detail: type === "terminal"
            ? { type: "terminal", terminalId: String(payload || "") }
            : { type: "file", entry: payload },
        }));
      } catch (e) {}
    }, 0);
  }

  return {
    selectChat: selectChat,
    renameChat: renameChat,
    renameTask: renameTask,
    deleteChat: deleteChat,
    promoteChat: promoteChat,
    openResource: openResource,
  };
}

export { createWorkbenchProjectRailActions }
