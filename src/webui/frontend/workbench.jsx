import { workbenchServices } from "./shared/runtime/services.jsx"
import { WbColResizer, wbApplyStoredRightWidth } from "./features/layout/right-panel-resizer.jsx"
import { WorkbenchFullPage, WorkbenchSidebarCollapseControl, WorkbenchSidebarDock } from "./features/shell/support.jsx"
import { WorkbenchAppModals, WorkbenchOnboardingShell, WorkbenchSearchPortal } from "./features/shell/app-overlays.jsx"
import { useWorkbenchModulePresentation } from "./features/shell/module-presentation.jsx"
import { createWorkbenchNavigationActions, useWorkbenchBoardNavigation, useWorkbenchNavigationSurface } from "./features/shell/navigation-controller.jsx"
import { createWorkbenchProjectRailActions } from "./features/shell/project-rail-controller.jsx"
import { useWorkbenchShellResources } from "./features/shell/resource-controller.jsx"
import { createWorkbenchShellNavigation } from "./features/shell/shell-navigation.jsx"
import { WorkbenchModuleSurfaces, WorkbenchShellTopbar, WorkbenchTaskModuleSurface } from "./features/shell/shell-composition.jsx"
import {
  useWorkbenchGlobalShortcuts,
  useWorkbenchLaunchOverlayLifecycle,
  useWorkbenchNativeMenuLifecycle,
  useWorkbenchNotificationLifecycle,
  useWorkbenchRecentChatLifecycle,
  useWorkbenchStartupLifecycle,
  useWorkbenchTaskRuntimeLifecycle,
} from "./features/shell/app-lifecycle.jsx"
import { createWorkbenchProjectDataActions, patchWorkbenchActiveInit, patchWorkbenchActiveSession } from "./features/task/project-controller.jsx"
import { createWorkbenchSelectionActions } from "./features/task/selection-controller.jsx"
import { useWorkbenchLiveActivityState, useWorkbenchLiveActivitySubscriptions } from "./features/session/live-activity.jsx"
import { useWorkbenchSessionTabs } from "./features/session/tabs-controller.jsx"
import { mergeTaskResponse } from "./features/task/store-merge.jsx"
import { wbErrorText } from "./shared/errors.jsx"
import { WorkbenchFileDropOverlay, useWorkbenchFileDrop } from "./shared/file-drop.jsx"

// Four-column Project / Task Session workbench.
var {
  useState: useWorkbenchState,
  useEffect: useWorkbenchEffect,
  useRef: useWorkbenchRef,
} = React;

// Apply the persisted low-overhead visual profile before React mounts. The
// server value is authoritative, while localStorage prevents a flash of glass
// effects and motion on subsequent launches.
function wbApplyPerformanceMode(enabled) {
  var next = enabled === true;
  try {
    document.documentElement.dataset.performanceMode = next ? "on" : "off";
    localStorage.setItem("cyrene-performance-mode", next ? "1" : "0");
  } catch (e) {}
  window.dispatchEvent(new CustomEvent("cyrene:performance-mode", {
    detail: { enabled: next },
  }));
}

(function initializeWorkbenchPerformanceMode() {
  var cached = false;
  try { cached = localStorage.getItem("cyrene-performance-mode") === "1"; } catch (e) {}
  wbApplyPerformanceMode(cached);
  fetch("/api/settings/config", { credentials: "same-origin" })
    .then(function (response) { return response.ok ? response.json() : Promise.reject(new Error("HTTP " + response.status)); })
    .then(function (payload) { wbApplyPerformanceMode(payload.performance_mode === true); })
    .catch(function () {});
})();

window.CyreneUI.performanceMode = {
  apply: wbApplyPerformanceMode,
  enabled: function () {
    return document.documentElement.dataset.performanceMode === "on";
  },
};

// Tag the host platform on <html> so CSS can reserve the macOS traffic-light
// gutter only where it actually exists. window.cyrene.platform comes from the
// Electron preload ('darwin' | 'win32' | 'linux'); fall back to 'web' in a
// plain browser. Runs at script load, before React paints the topbar, so macOS
// gets the gutter with no flash and other platforms never reserve dead space.
(function tagWorkbenchPlatform() {
  try {
    document.documentElement.dataset.platform =
      (window.cyrene && window.cyrene.platform) || "web";
  } catch (e) {}
})();



function WorkbenchApp({ theme, actualTheme, onToggleTheme, needsOnboarding }) {
  var dataStore = workbenchServices.data();
  dataStore.useVersion();
  var dataState = dataStore.state;
  var workbenchI18n = workbenchServices.i18n().use();
  var t = workbenchI18n.t;
  var model = workbenchServices.model();
  var [store, setStore] = useWorkbenchState(function () {
    return model.normalizeStore({ projects: [] });
  });
  var [loading, setLoading] = useWorkbenchState(true);
  var [error, setError] = useWorkbenchState("");
  var [fullPage, setFullPage] = useWorkbenchState(function () {
    try {
      var stored = localStorage.getItem("wb-active-page");
      if (stored === "profile") return "settings";
      if (stored && stored !== "welcome") return stored;
      return null;
    } catch (e) { return null; }
  });
  var sidebarModuleWheelRef = useWorkbenchRef({ delta: 0, direction: 0, lockedUntil: 0 });
  // The task entry point is a project-wide board. A task detail is opened only
  // after the user selects a card (or follows a direct task link/search hit).
  var [taskView, setTaskView] = useWorkbenchState("board");
  var [projectRailMode, setProjectRailMode] = useWorkbenchState("task");
  var [projectRailToTaskBusy, setProjectRailToTaskBusy] = useWorkbenchState(false);
  var [rightTab, setRightTab] = useWorkbenchState("context");
  var [railCollapsed, setRailCollapsed] = useWorkbenchState(function () {
    // Default to collapsed (icon strip); honour the user's stored choice once set.
    try {
      var v = localStorage.getItem("wb-rail-collapsed");
      return v === null ? true : v === "1";
    } catch (e) { return true; }
  });
  var [expandedStepId, setExpandedStepId] = useWorkbenchState("");
  var [searchOpen, setSearchOpen] = useWorkbenchState(false);
  var [settingsTab, setSettingsTab] = useWorkbenchState(function () {
    try { return localStorage.getItem("wb-active-page") === "profile" ? "profile" : ""; }
    catch (e) { return ""; }
  });
  var [settingsScrollTo, setSettingsScrollTo] = useWorkbenchState(null);
  var pythonPromptCheckedRef = useWorkbenchRef(false);
  var [newProjectOpen, setNewProjectOpen] = useWorkbenchState(false);
  var [newTaskOpen, setNewTaskOpen] = useWorkbenchState(false);
  var [newChatRequestId, setNewChatRequestId] = useWorkbenchState(0);
  var [taskOpenRequest, setTaskOpenRequest] = useWorkbenchState({ id: "", sequence: 0 });
  var [mountedPages, setMountedPages] = useWorkbenchState({});
  var [editProject, setEditProject] = useWorkbenchState(null);
  var [editMemoryProject, setEditMemoryProject] = useWorkbenchState(null);
  var [notifications, setNotifications] = useWorkbenchState({ items: [], counts: { all: 0, mention: 0, comment: 0, system: 0 }, unreadByTab: { all: 0, mention: 0, comment: 0, system: 0 }, unreadCount: 0 });
  var [activeChatId, setActiveChatId] = useWorkbenchState("");
  var [recentChatsByProject, setRecentChatsByProject] = useWorkbenchState({});
  var [pinnedResources, setPinnedResources] = useWorkbenchState([]);
  var chatModule = workbenchServices.chat();
  var chatRuntimeEngine = chatModule && chatModule.Runtimes;
  var liveActivity = useWorkbenchLiveActivityState(chatRuntimeEngine);
  var chatRuntimes = liveActivity.chatRuntimes;
  var sessionActivityLive = liveActivity.sessionActivityLive;
  var sessionTabs = useWorkbenchSessionTabs(store.projects, recentChatsByProject);
  var recentOpenedSessionKeys = sessionTabs.recentOpenedSessionKeys;
  var pinnedSessionKeys = sessionTabs.pinnedSessionKeys;
  var hiddenSessionKeys = sessionTabs.hiddenSessionKeys;
  var rememberOpenedSession = sessionTabs.rememberOpenedSession;
  var togglePinnedSession = sessionTabs.togglePinnedSession;
  var removeSessionTab = sessionTabs.removeSessionTab;
  // Always-fresh snapshot of what the user is looking at, read inside async
  // notification callbacks (interval / SSE closures captured once on mount).
  var activeViewRef = useWorkbenchRef({ page: null, taskView: "board", chatId: "", sessionId: "" });
  var sessionLoadSeqRef = useWorkbenchRef(0);
  var recentChatsLoadSeqRef = useWorkbenchRef(0);
  var launchReadyRef = useWorkbenchRef(false);
  var menuActionsRef = useWorkbenchRef({ createProject: function () {}, createSession: function () {}, createChat: function () {}, onToggleTheme: function () {} });
  var navigationActions = createWorkbenchNavigationActions(
    fullPage, taskView, setFullPage, setTaskView, setSettingsTab, setSettingsScrollTo,
    setRailCollapsed, sidebarModuleWheelRef, function () { return activeDestination; }
  );
  var handleOpenPage = navigationActions.openPage;
  var toggleWorkspaceSidebar = navigationActions.toggleSidebar;
  var handleSidebarModuleWheel = navigationActions.onModuleWheel;

  useWorkbenchEffect(function () {
    function openSettings(event) {
      var detail = event && event.detail && typeof event.detail === "object" ? event.detail : {};
      setSettingsTab(String(detail.tab || ""));
      setSettingsScrollTo(null);
      setFullPage("settings");
    }
    window.addEventListener("cyrene:open-settings", openSettings);
    return function () { window.removeEventListener("cyrene:open-settings", openSettings); };
  }, []);

  useWorkbenchLiveActivitySubscriptions(
    chatRuntimeEngine,
    liveActivity.setChatRuntimes,
    liveActivity.setSessionActivityLive
  );

  var shellResources = useWorkbenchShellResources({
    model: model, t: t, activeViewRef: activeViewRef,
    recentChatsLoadSeqRef: recentChatsLoadSeqRef,
    setNotifications: setNotifications,
    setRecentChatsByProject: setRecentChatsByProject,
    setPinnedResources: setPinnedResources,
    getSessionTabCandidates: function () { return sessionTabCandidates || []; },
  });
  var reloadNotifications = shellResources.reloadNotifications;
  var reloadRecentChats = shellResources.reloadRecentChats;
  var pinTopbarResource = shellResources.pinTopbarResource;
  var unpinTopbarResource = shellResources.unpinTopbarResource;
  var shellNavigation = createWorkbenchShellNavigation({
    model: model, store: store, setStore: setStore,
    setExpandedStepId: setExpandedStepId, setTaskView: setTaskView,
    setFullPage: setFullPage, setTaskOpenRequest: setTaskOpenRequest,
    setSettingsTab: setSettingsTab, setSettingsScrollTo: setSettingsScrollTo,
    getSelectProject: function () { return selectProject; },
  });
  var navigateFromSearch = shellNavigation.navigateFromSearch;
  var navigateFromNotification = shellNavigation.navigateFromNotification;

  function openSessionTabResource(item, resource) {
    if (!item || !resource) return;
    rememberOpenedSession(item.kind, item.id);
    var payload = item.kind === "chat"
      ? { type: "chat", projectId: item.projectId, chatId: item.id }
      : { type: "task", projectId: item.projectId, sessionId: item.id };
    payload.topbarResource = resource;
    navigateFromSearch(payload);
  }

  var projectDataActions = createWorkbenchProjectDataActions(
    model, sessionLoadSeqRef, setStore, setLoading, setError
  );
  var reloadWorkbench = projectDataActions.reloadWorkbench;
  var refreshTaskBoard = projectDataActions.refreshTaskBoard;
  var fetchAndMergeSession = projectDataActions.fetchAndMergeSession;
  var selectionActions = createWorkbenchSelectionActions(
    store, setStore, setExpandedStepId, setTaskView, fetchAndMergeSession,
    setFullPage, setTaskOpenRequest, rememberOpenedSession, navigateFromSearch
  );
  var selectProject = selectionActions.selectProject;
  var selectSession = selectionActions.selectSession;
  var openTaskInWorkspace = selectionActions.openTask;
  var openChatInWorkspace = selectionActions.openChat;

  useWorkbenchStartupLifecycle(fullPage, reloadWorkbench, reloadNotifications);
  useWorkbenchRecentChatLifecycle(
    store.projects,
    reloadRecentChats,
    refreshTaskBoard,
    setRecentChatsByProject
  );

  useWorkbenchLaunchOverlayLifecycle(
    loading, launchReadyRef, dataStore, pythonPromptCheckedRef, t,
    setSettingsTab, setSettingsScrollTo, setFullPage, searchOpen
  );

  useWorkbenchNativeMenuLifecycle(
    menuActionsRef, createProject, createSession, createChat, onToggleTheme,
    setSettingsTab, setSettingsScrollTo, setFullPage, toggleWorkspaceSidebar
  );

  useWorkbenchNotificationLifecycle(
    reloadNotifications, activeViewRef, fullPage, taskView, activeChatId,
    store && store.activeSessionId, rememberOpenedSession
  );

  useWorkbenchGlobalShortcuts(
    searchOpen, newProjectOpen, newTaskOpen, store, setSearchOpen, createChat,
    setNewTaskOpen, setSettingsTab, setSettingsScrollTo, setFullPage,
    toggleWorkspaceSidebar, selectProject
  );

  useWorkbenchTaskRuntimeLifecycle(
    fullPage, taskView, refreshTaskBoard, activeViewRef, setStore, fetchAndMergeSession
  );

  useWorkbenchEffect(function () {
    return workbenchServices.navigation().setHandler(navigateFromSearch);
  }, [store.projects, store.activeProjectId]);

  function patchActiveInit(initPatch) { patchWorkbenchActiveInit(setStore, initPatch); }
  function patchActiveSessionLocal(partial) { patchWorkbenchActiveSession(setStore, partial); }

  // New project / task creation now goes through dedicated workbench modals
  // (WorkbenchNewProjectModal / WorkbenchNewTaskModal). These handlers perform
  // the actual API calls; the rail buttons just open the modals.
  function createProject() { setNewProjectOpen(true); }
  function createSession() { if (store.activeProject) setNewTaskOpen(true); }
  function createChat() {
    setFullPage("chat");
    setNewChatRequestId(function (value) { return value + 1; });
  }

  function handleCreateProject(input) {
    // The backend opens the new project onto its agent-led init session and
    // returns it as the active session, so we just adopt the new store.
    return model.createProject(input).then(function (next) {
      setStore(next);
      setExpandedStepId("");
      setRightTab("context");
      setTaskView("detail");
      // Land in the freshly-created project's task view — important when the
      // project was created from the welcome page, so we leave it behind.
      setFullPage("chat");
      if (next && next.activeSessionId) {
        setTaskOpenRequest(function (current) {
          return { id: String(next.activeSessionId), sequence: Number(current && current.sequence || 0) + 1 };
        });
      }
      return next;
    });
  }

  function handleCreateSession(input) {
    if (!store.activeProject) return Promise.resolve();
    return model.createSession(store.activeProject.id, input).then(function (next) {
      setStore(next);
      setExpandedStepId("");
      setTaskView("detail");
      setFullPage("chat");
      if (next && next.activeSessionId) {
        setTaskOpenRequest(function (current) {
          return { id: String(next.activeSessionId), sequence: Number(current && current.sequence || 0) + 1 };
        });
      }
      return next;
    });
  }

  function handleUpdateProject(projectId, input) {
    return model.updateProject(projectId, input).then(function (next) {
      setStore(next);
      return next;
    });
  }

  function handleDeleteSession(session) {
    if (!session) return;
    workbenchServices.feedback().confirmModal({
      body: t("task.confirmDelete", { name: session.title || t("task.thisTask") }),
      confirmLabel: t("common.delete"),
      danger: true,
    }).then(function (ok) {
      if (!ok) return;
      model.deleteSession(session.id).then(function (next) {
        setStore(next);
        setExpandedStepId("");
      }).catch(function (err) {
        setError(wbErrorText(err));
      });
    });
  }

  function handleDeleteProject(project) {
    if (!project) return Promise.resolve();
    if (project.dataKey === "default") {
      setError(t("project.cannotDeleteDefault", "The default project cannot be deleted."));
      return Promise.resolve();
    }
    return workbenchServices.feedback().confirmModal({
      body: t("project.confirmDelete", { name: project.name }, "Delete project \"{name}\"? Data inside the project will also be deleted."),
      confirmLabel: t("common.delete", "Delete"),
      danger: true,
    }).then(function (ok) {
      if (!ok) return undefined;
      window.dispatchEvent(new Event("cyrene:voice-stop"));
      return model.deleteProject(project.id).then(function (next) {
        setStore(next);
        setFullPage(null);
        setTaskView("board");
        setExpandedStepId("");
        return next;
      }).catch(function (err) {
        setError(wbErrorText(err));
      });
    });
  }

  function handleRunCreated(next, sourceSessionId) {
    setStore(function (prev) {
      return mergeTaskResponse(prev, next, sourceSessionId);
    });
    var visibleSessionId = activeViewRef.current && activeViewRef.current.sessionId;
    if (!sourceSessionId || String(sourceSessionId) === String(visibleSessionId || "")) {
      var currentSession = next && next.activeSession && String(next.activeSession.id || "") === String(sourceSessionId || visibleSessionId || "")
        ? next.activeSession : null;
      setExpandedStepId(currentSession && currentSession.plan && currentSession.plan[0] ? currentSession.plan[0].id : "");
      setRightTab("context");
      setTaskView("detail");
    }
  }

  useWorkbenchBoardNavigation(setTaskView, setFullPage);

  useWorkbenchNavigationSurface(
    fullPage, taskView, settingsTab, railCollapsed, t, handleOpenPage,
    toggleWorkspaceSidebar, setSearchOpen, setSettingsTab, setSettingsScrollTo, setFullPage
  );

  function renderSidebarCollapseControl() {
    return <WorkbenchSidebarCollapseControl collapsed={railCollapsed} onToggle={toggleWorkspaceSidebar} />;
  }

  function renderSidebarDockSlot() {
    return <div className="workbench-sidebar-dock-slot" aria-hidden="true" />;
  }

  var projectRailActions = createWorkbenchProjectRailActions(
    store, setStore, recentChatsByProject, setRecentChatsByProject, chatModule, model, t,
    projectRailToTaskBusy, setProjectRailToTaskBusy, openChatInWorkspace, handleChatToTask, setFullPage
  );
  var selectProjectRailChat = projectRailActions.selectChat;
  var renameProjectRailChat = projectRailActions.renameChat;
  var renameProjectRailTask = projectRailActions.renameTask;
  var deleteProjectRailChat = projectRailActions.deleteChat;
  var promoteProjectRailChat = projectRailActions.promoteChat;
  var openProjectRailResource = projectRailActions.openResource;

  // Conversation → task promotion: the chat page returns the refreshed store
  // (active = the new task session); adopt it and jump back to the task view.
  function handleChatToTask(payload) {
    var next = model.normalizeStore(payload);
    setStore(next);
    setFullPage("chat");
    setTaskView("detail");
    setExpandedStepId("");
    setRightTab("context");
    if (next && next.activeSessionId) {
      setTaskOpenRequest(function (current) {
        return { id: String(next.activeSessionId), sequence: Number(current && current.sequence || 0) + 1 };
      });
    }
  }

  var modulePresentation = useWorkbenchModulePresentation(
    fullPage, setFullPage, taskView, mountedPages, setMountedPages, store, activeChatId,
    recentChatsByProject, {
      recent: recentOpenedSessionKeys, pinned: pinnedSessionKeys, hidden: hiddenSessionKeys,
    }, chatRuntimes, sessionActivityLive, dataState, t
  );
  var isKnowledge = modulePresentation.isKnowledge;
  var isSchedule = modulePresentation.isSchedule;
  var isMemory = modulePresentation.isMemory;
  var isChat = modulePresentation.isChat;
  var isSettings = modulePresentation.isSettings;
  var isModulePage = modulePresentation.isModulePage;
  var fullPageConfig = modulePresentation.fullPageConfig;
  var showChatPage = modulePresentation.showChatPage;
  var showKnowledgePage = modulePresentation.showKnowledgePage;
  var showSchedulePage = modulePresentation.showSchedulePage;
  var showMemoryPage = modulePresentation.showMemoryPage;
  var showSettingsPage = modulePresentation.showSettingsPage;
  var activeDestination = modulePresentation.activeDestination;
  var activeSessionKey = modulePresentation.activeSessionKey;
  var sessionTabCandidates = modulePresentation.sessionTabCandidates;
  var browserOwnerSessions = modulePresentation.browserOwnerSessions;
  var recentSessionTabs = modulePresentation.recentSessionTabs;
  var overflowSessionTabs = modulePresentation.overflowSessionTabs;

  // First-run onboarding (LLM + personality). Driven by the backend onboarding
  // state — the workbench's own setup flow, independent of the legacy wizard.
  // It takes over the whole shell (no rails) until both are configured; once the
  // backend reports needsOnboarding=false the shell falls through to normal.
  var onboarding = dataState.onboarding || {};
  var onboardingActive = onboarding.needsOnboarding != null ? !!onboarding.needsOnboarding : !!needsOnboarding;
  function handleOnboardingComplete() {
    setFullPage("chat");
  }
  if (onboardingActive) {
    return <WorkbenchOnboardingShell
      onboarding={onboarding}
      theme={theme}
      actualTheme={actualTheme}
      onToggleTheme={onToggleTheme}
      onComplete={handleOnboardingComplete}
      t={t}
    />;
  }

  function pinnedSessionIds(kind) {
    var prefix = kind + ":";
    return pinnedSessionKeys.filter(function (key) {
      return String(key || "").indexOf(prefix) === 0;
    }).map(function (key) { return String(key).slice(prefix.length); });
  }
  function openSettings(tab) {
    setSettingsTab(typeof tab === "string" ? tab : "");
    setSettingsScrollTo(null);
    setFullPage("settings");
  }
  var shellContext = {
    model: model, t: t, store: store, setStore: setStore,
    appearance: { theme: theme, actualTheme: actualTheme, onToggleTheme: onToggleTheme },
    presentation: modulePresentation,
    navigation: {
      fullPage: fullPage, railCollapsed: railCollapsed,
      openPage: handleOpenPage, closePage: function () { setFullPage(null); },
      openSettings: openSettings, navigate: navigateFromSearch,
      navigateNotification: navigateFromNotification,
      toggleSidebar: toggleWorkspaceSidebar,
      renderCollapseControl: renderSidebarCollapseControl,
      renderDockSlot: renderSidebarDockSlot,
    },
    dialogs: {
      searchOpen: searchOpen, setSearchOpen: setSearchOpen,
      newProjectOpen: newProjectOpen, newTaskOpen: newTaskOpen,
      editProject: editProject, setEditProject: setEditProject,
      editMemoryProject: editMemoryProject, setEditMemoryProject: setEditMemoryProject,
      settingsTab: settingsTab, settingsScrollTo: settingsScrollTo,
      editActiveProjectMemory: function () {
        if (store.activeProject) setEditMemoryProject(store.activeProject);
      },
    },
    resources: {
      pinned: pinnedResources, notifications: notifications,
      pin: pinTopbarResource, unpin: unpinTopbarResource,
      reloadNotifications: reloadNotifications,
    },
    sessions: {
      activeChatId: activeChatId, setActiveChatId: setActiveChatId,
      recent: recentSessionTabs, overflow: overflowSessionTabs,
      browserOwners: browserOwnerSessions, candidates: sessionTabCandidates,
      rememberOpened: rememberOpenedSession, togglePinned: togglePinnedSession,
      removeTab: removeSessionTab, openResource: openSessionTabResource,
      updateRecentChats: function (projectId, chats) {
        setRecentChatsByProject(function (previous) {
          return previous[projectId] === chats
            ? previous : Object.assign({}, previous, { [projectId]: chats });
        });
      },
      pinnedView: {
        chatIds: pinnedSessionIds("chat"), taskIds: pinnedSessionIds("task"),
        onToggleChat: function (chat, pinned) {
          if (chat && chat.id) togglePinnedSession({ id: chat.id, kind: "chat" }, pinned);
        },
        onToggleTask: function (task, pinned) {
          if (task && task.id) togglePinnedSession({ id: task.id, kind: "task" }, pinned);
        },
      },
    },
    chat: {
      module: chatModule, runtimeEngine: chatRuntimeEngine,
      newChatRequestId: newChatRequestId,
    },
    task: {
      view: taskView, openRequest: taskOpenRequest,
      loading: loading, error: error,
      expandedStepId: expandedStepId, rightTab: rightTab,
      selectSession: selectSession, renameRailTask: renameProjectRailTask,
      openTask: openTaskInWorkspace, openChat: openChatInWorkspace,
      chatToTask: handleChatToTask, setRightTab: setRightTab,
      toggleStep: function (stepId) { setExpandedStepId(expandedStepId === stepId ? "" : stepId); },
      runCreated: function (next) { handleRunCreated(next, store.activeSessionId); },
      backToBoard: function () { setTaskView("board"); },
      patchInit: patchActiveInit, patchLocal: patchActiveSessionLocal,
      refresh: function (nextStore) {
        setStore(function (previous) {
          return mergeTaskResponse(previous, nextStore, store.activeSessionId);
        });
      },
      mergeStore: function (nextStore, sourceSessionId) {
        setStore(function (previous) {
          return mergeTaskResponse(previous, nextStore, sourceSessionId);
        });
      },
    },
    projectRail: {
      mode: projectRailMode, setMode: setProjectRailMode,
      toTaskBusy: projectRailToTaskBusy,
      recentChatsByProject: recentChatsByProject,
      selectChat: selectProjectRailChat, renameChat: renameProjectRailChat,
      renameTask: renameProjectRailTask, deleteChat: deleteProjectRailChat,
      promoteChat: promoteProjectRailChat, openResource: openProjectRailResource,
    },
    actions: {
      createProject: createProject, createSession: createSession,
      createChat: createChat,
      selectProject: selectProject, deleteProject: handleDeleteProject,
      deleteSession: handleDeleteSession,
    },
  };

  return (
    <div className="workbench-shell" data-screen-label="Cyrene · workbench">
      <WorkbenchShellTopbar context={shellContext} />
      {fullPageConfig ? (
        <WorkbenchFullPage config={fullPageConfig} onClose={function () { setFullPage(null); }} />
      ) : (
        <div ref={wbApplyStoredRightWidth} className={"workbench-grid integrated-sidebars" + (railCollapsed ? " rail-collapsed" : "") + (isKnowledge ? " is-knowledge" : "") + (isSchedule ? " is-schedule" : "") + (isMemory ? " is-memory" : "") + (isChat ? " is-chat" : "") + (isSettings ? " is-settings" : "") + (!isModulePage ? (taskView === "board" ? " is-task-board" : " is-task-detail") : "")} onWheel={handleSidebarModuleWheel}>
          <WorkbenchSidebarDock
            persistent={true}
            collapsed={railCollapsed}
            activePage={fullPage}
            activeDestination={activeDestination}
            onOpenPage={handleOpenPage}
            onSettings={function () { setSettingsTab(""); setSettingsScrollTo(null); setFullPage("settings"); }}
          />
          <WorkbenchModuleSurfaces context={shellContext} />
          <WorkbenchTaskModuleSurface context={shellContext} />
        </div>
      )}
      <WorkbenchSearchPortal
        open={searchOpen}
        onClose={function () { setSearchOpen(false); }}
        onOpenChatPage={function () { setFullPage("chat"); }}
        onCreateChat={createChat}
        onCreateTask={createSession}
        onCreateProject={createProject}
        onToggleTheme={onToggleTheme}
        onToggleSidebar={toggleWorkspaceSidebar}
        onOpenSettings={function (tab, anchorId) {
          setSettingsTab(tab || "");
          setSettingsScrollTo(anchorId || null);
          setFullPage("settings");
        }}
      />
      <WorkbenchAppModals
        newProjectOpen={newProjectOpen}
        onCloseNewProject={function () { setNewProjectOpen(false); }}
        onCreateProject={handleCreateProject}
        editProject={editProject}
        onCloseEditProject={function () { setEditProject(null); }}
        onUpdateProject={handleUpdateProject}
        editMemoryProject={editMemoryProject}
        onCloseEditMemory={function () { setEditMemoryProject(null); }}
        newTaskOpen={newTaskOpen}
        onCloseNewTask={function () { setNewTaskOpen(false); }}
        onCreateTask={handleCreateSession}
        onOpenPage={handleOpenPage}
        onOpenSettings={function (tab) {
          setSettingsTab(typeof tab === "string" ? tab : "");
          setSettingsScrollTo(null);
          setFullPage("settings");
        }}
      />
    </div>
  );
}

window.CyreneUI.shell = window.CyreneUI.register("shell", {
  App: WorkbenchApp,
  ColResizer: WbColResizer,
});

export { WorkbenchApp, WorkbenchFileDropOverlay, useWorkbenchFileDrop }
