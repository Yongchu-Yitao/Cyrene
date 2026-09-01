import { workbenchFullPageConfig } from "./support.jsx"
import { wbRecentSessionTabs, wbSessionActivitySnapshot } from "../session/activity.jsx"

var { useEffect } = React;

function workbenchSessionTabsPresentation(
  store,
  recentChatsByProject,
  sessionKeys,
  chatRuntimes,
  sessionActivityLive,
  dataState,
  activeSessionKey,
  t,
  workspaceTabState
) {
  var workspaceItems = workspaceTabState && Array.isArray(workspaceTabState.items)
    ? workspaceTabState.items : [];
  var workspaceKeys = workspaceItems.map(function (item) {
    return String(item.kind || "") + ":" + String(item.id || "");
  });
  var candidates = wbRecentSessionTabs(
    store.projects,
    recentChatsByProject,
    workspaceKeys.concat(sessionKeys.recent.filter(function (key) {
      return workspaceKeys.indexOf(String(key || "")) < 0;
    })),
    sessionKeys.pinned,
    sessionKeys.hidden,
    1000,
    workspaceItems
  ).map(function (item) {
    var runtime = item.kind === "chat" ? chatRuntimes[item.id] : null;
    var browserByChat = dataState.browserByChat || {};
    var browserState = browserByChat[item.id] || (
      dataState.browser && String(dataState.browser.sessionId || dataState.browser.chatId || "") === item.id
        ? dataState.browser
        : null
    );
    return Object.assign({}, item, {
      activity: wbSessionActivitySnapshot(item, runtime, sessionActivityLive[item.id], browserState),
    });
  });
  var browserOwners = [];
  (Array.isArray(store.projects) ? store.projects : []).forEach(function (project) {
    var projectId = String(project && project.id || "");
    var chats = recentChatsByProject && Array.isArray(recentChatsByProject[projectId])
      ? recentChatsByProject[projectId]
      : [];
    chats.forEach(function (chat) {
      if (!chat || !chat.id) return;
      browserOwners.push({
        id: String(chat.id), kind: "chat",
        title: String(chat.title || t("workbench.page.chat", "Conversation")),
        projectId: projectId, projectName: String(project.name || ""),
      });
    });
  });
  return {
    candidates: candidates,
    browserOwners: browserOwners,
    activeTabKey: String(workspaceTabState && workspaceTabState.activeKey || activeSessionKey || ""),
  };
}

function useWorkbenchModulePresentation(
  fullPage,
  setFullPage,
  mountedPages,
  setMountedPages,
  store,
  activeChatId,
  recentChatsByProject,
  sessionKeys,
  chatRuntimes,
  sessionActivityLive,
  dataState,
  t,
  enabledModules,
  workspaceTabState
) {
  var knowledgeEnabled = !Array.isArray(enabledModules)
    || enabledModules.indexOf("knowledge") >= 0;
  var scheduleEnabled = !Array.isArray(enabledModules)
    || enabledModules.indexOf("schedule") >= 0;
  var memoryEnabled = !Array.isArray(enabledModules)
    || enabledModules.indexOf("memory") >= 0;
  var boardEnabled = !Array.isArray(enabledModules)
    || enabledModules.indexOf("board") >= 0;
  var isKnowledge = knowledgeEnabled && fullPage === "knowledge";
  var isSchedule = scheduleEnabled && fullPage === "schedule";
  var isMemory = memoryEnabled && fullPage === "memory";
  var isChat = fullPage === "chat";
  var isSettings = fullPage === "settings";
  var isBoard = boardEnabled && !fullPage;
  var isModulePage = isKnowledge || isSchedule || isMemory || isChat || isBoard || isSettings;
  var fullPageConfig = fullPage && !isModulePage ? workbenchFullPageConfig(fullPage, setFullPage, store) : null;

  useEffect(function () {
    if (!isModulePage || !fullPage) return;
    setMountedPages(function (previous) {
      if (previous[fullPage]) return previous;
      return Object.assign({}, previous, { [fullPage]: true });
    });
  }, [fullPage, isModulePage]);

  var activeDestination = isSchedule ? "schedule"
    : isKnowledge ? "knowledge"
      : isMemory ? "memory"
        : isChat ? "work"
          : isBoard ? "board" : "work";
  var activeSessionKey = isChat && activeChatId ? "chat:" + activeChatId : "";
  var sessions = workbenchSessionTabsPresentation(
    store, recentChatsByProject, sessionKeys, chatRuntimes,
    sessionActivityLive, dataState, activeSessionKey, t, workspaceTabState
  );
  return {
    isKnowledge: isKnowledge,
    isSchedule: isSchedule,
    isMemory: isMemory,
    isChat: isChat,
    isSettings: isSettings,
    isBoard: isBoard,
    isModulePage: isModulePage,
    fullPageConfig: fullPageConfig,
    showChatPage: isChat || mountedPages.chat,
    showKnowledgePage: knowledgeEnabled && (isKnowledge || mountedPages.knowledge),
    showSchedulePage: scheduleEnabled && (isSchedule || mountedPages.schedule),
    showMemoryPage: memoryEnabled && (isMemory || mountedPages.memory),
    showSettingsPage: isSettings || mountedPages.settings,
    activeDestination: activeDestination,
    activeSessionKey: activeSessionKey,
    activeTabKey: isChat ? sessions.activeTabKey : "",
    sessionTabCandidates: sessions.candidates,
    browserOwnerSessions: sessions.browserOwners,
    recentSessionTabs: sessions.candidates,
    overflowSessionTabs: [],
  };
}

export { useWorkbenchModulePresentation }
