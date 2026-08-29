import { workbenchFullPageConfig } from "./support.jsx"
import { wbRecentSessionTabs, wbSessionActivitySnapshot, wbVisibleSessionTabs } from "../session/activity.jsx"

var { useEffect } = React;

function workbenchSessionTabsPresentation(
  store,
  recentChatsByProject,
  sessionKeys,
  chatRuntimes,
  sessionActivityLive,
  dataState,
  activeSessionKey,
  t
) {
  var candidates = wbRecentSessionTabs(
    store.projects,
    recentChatsByProject,
    sessionKeys.recent,
    sessionKeys.pinned,
    sessionKeys.hidden,
    1000
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
  var layout = wbVisibleSessionTabs(candidates, activeSessionKey, 3);
  return { candidates: candidates, browserOwners: browserOwners, layout: layout };
}

function useWorkbenchModulePresentation(
  fullPage,
  setFullPage,
  taskView,
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
  enabledModules
) {
  var knowledgeEnabled = !Array.isArray(enabledModules)
    || enabledModules.indexOf("knowledge") >= 0;
  var scheduleEnabled = !Array.isArray(enabledModules)
    || enabledModules.indexOf("schedule") >= 0;
  var memoryEnabled = !Array.isArray(enabledModules)
    || enabledModules.indexOf("memory") >= 0;
  var isKnowledge = knowledgeEnabled && fullPage === "knowledge";
  var isSchedule = scheduleEnabled && fullPage === "schedule";
  var isMemory = memoryEnabled && fullPage === "memory";
  var isChat = fullPage === "chat";
  var isSettings = fullPage === "settings";
  var isModulePage = isKnowledge || isSchedule || isMemory || isChat || isSettings;
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
          : (!isModulePage && taskView === "board" ? "board" : "work");
  var activeSessionKey = isChat && !activeChatId && taskView === "detail" && store.activeSessionId
    ? "task:" + store.activeSessionId
    : (isChat && activeChatId
      ? "chat:" + activeChatId
      : (!fullPage && taskView === "detail" && store.activeSessionId ? "task:" + store.activeSessionId : ""));
  var sessions = workbenchSessionTabsPresentation(
    store, recentChatsByProject, sessionKeys, chatRuntimes,
    sessionActivityLive, dataState, activeSessionKey, t
  );
  return {
    isKnowledge: isKnowledge,
    isSchedule: isSchedule,
    isMemory: isMemory,
    isChat: isChat,
    isSettings: isSettings,
    isModulePage: isModulePage,
    fullPageConfig: fullPageConfig,
    showChatPage: isChat || mountedPages.chat,
    showKnowledgePage: knowledgeEnabled && (isKnowledge || mountedPages.knowledge),
    showSchedulePage: scheduleEnabled && (isSchedule || mountedPages.schedule),
    showMemoryPage: memoryEnabled && (isMemory || mountedPages.memory),
    showSettingsPage: isSettings || mountedPages.settings,
    activeDestination: activeDestination,
    activeSessionKey: activeSessionKey,
    sessionTabCandidates: sessions.candidates,
    browserOwnerSessions: sessions.browserOwners,
    recentSessionTabs: sessions.layout.visible,
    overflowSessionTabs: sessions.layout.overflow,
  };
}

export { useWorkbenchModulePresentation }
