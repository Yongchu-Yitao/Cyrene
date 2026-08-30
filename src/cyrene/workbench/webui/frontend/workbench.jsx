import { workbenchServices } from "./shared/runtime/services.jsx"
import { WbColResizer, wbApplyStoredRightWidth } from "./features/layout/right-panel-resizer.jsx"
import { WorkbenchFullPage, WorkbenchSidebarCollapseControl, WorkbenchSidebarDock } from "./features/shell/support.jsx"
import { WorkbenchAppModals, WorkbenchOnboardingShell, WorkbenchSearchPortal } from "./features/shell/app-overlays.jsx"
import { useWorkbenchModulePresentation } from "./features/shell/module-presentation.jsx"
import { createWorkbenchNavigationActions, useWorkbenchBoardNavigation, useWorkbenchNavigationSurface } from "./features/shell/navigation-controller.jsx"
import { useWorkbenchShellResources } from "./features/shell/resource-controller.jsx"
import { createWorkbenchShellNavigation } from "./features/shell/shell-navigation.jsx"
import { WorkbenchModuleSurfaces, WorkbenchShellTopbar } from "./features/shell/shell-composition.jsx"
import {
  useWorkbenchGlobalShortcuts,
  useWorkbenchLaunchOverlayLifecycle,
  useWorkbenchNativeMenuLifecycle,
  useWorkbenchNotificationLifecycle,
  useWorkbenchRecentChatLifecycle,
  useWorkbenchStartupLifecycle,
} from "./features/shell/app-lifecycle.jsx"
import { useWorkbenchLiveActivityState, useWorkbenchLiveActivitySubscriptions } from "./features/session/live-activity.jsx"
import { useWorkbenchSessionTabs } from "./features/session/tabs-controller.jsx"
import { wbErrorText } from "./shared/errors.jsx"
import { WorkbenchFileDropOverlay, useWorkbenchFileDrop } from "./shared/file-drop.jsx"

// Conversation-native project workbench.
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
  var pluginModules = Array.isArray(dataState.pluginModules) ? dataState.pluginModules : [];
  var enabledModules = ["schedule", "board", "work", "knowledge", "memory"].filter(function (module) {
    return ["schedule", "knowledge", "memory"].indexOf(module) < 0
      || pluginModules.indexOf(module) >= 0;
  });
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
      if (stored && stored !== "welcome" && stored !== "board") return stored;
      return "chat";
    } catch (e) { return "chat"; }
  });
  var sidebarModuleWheelRef = useWorkbenchRef({ delta: 0, direction: 0, lockedUntil: 0 });
  var [railCollapsed, setRailCollapsed] = useWorkbenchState(function () {
    // Default to collapsed (icon strip); honour the user's stored choice once set.
    try {
      var v = localStorage.getItem("wb-rail-collapsed");
      return v === null ? true : v === "1";
    } catch (e) { return true; }
  });
  var [searchOpen, setSearchOpen] = useWorkbenchState(false);
  var [settingsTab, setSettingsTab] = useWorkbenchState(function () {
    try { return localStorage.getItem("wb-active-page") === "profile" ? "profile" : ""; }
    catch (e) { return ""; }
  });
  var [settingsScrollTo, setSettingsScrollTo] = useWorkbenchState(null);
  var [newProjectOpen, setNewProjectOpen] = useWorkbenchState(false);
  var [newChatRequestId, setNewChatRequestId] = useWorkbenchState(0);
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
  var activeViewRef = useWorkbenchRef({ page: null, chatId: "" });
  var recentChatsLoadSeqRef = useWorkbenchRef(0);
  var launchReadyRef = useWorkbenchRef(false);
  var menuActionsRef = useWorkbenchRef({ createProject: function () {}, createChat: function () {}, onToggleTheme: function () {} });
  var navigationActions = createWorkbenchNavigationActions(
    fullPage, setFullPage, setSettingsTab, setSettingsScrollTo,
    setRailCollapsed, sidebarModuleWheelRef, function () { return activeDestination; },
    enabledModules
  );
  var handleOpenPage = navigationActions.openPage;
  var toggleWorkspaceSidebar = navigationActions.toggleSidebar;
  var handleSidebarModuleWheel = navigationActions.onModuleWheel;

  useWorkbenchEffect(function () {
    if (["schedule", "knowledge", "memory"].indexOf(fullPage) >= 0
        && enabledModules.indexOf(fullPage) < 0) {
      setFullPage(null);
    }
    if (enabledModules.indexOf("memory") < 0 && editMemoryProject) setEditMemoryProject(null);
  }, [fullPage, editMemoryProject, enabledModules.join("|")]);

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
    setFullPage: setFullPage,
    setSettingsTab: setSettingsTab, setSettingsScrollTo: setSettingsScrollTo,
    enabledModules: enabledModules,
    getSelectProject: function () { return selectProject; },
  });
  var navigateFromSearch = shellNavigation.navigateFromSearch;
  var navigateFromNotification = shellNavigation.navigateFromNotification;

  function openSessionTabResource(item, resource) {
    if (!item || item.kind !== "chat" || !resource) return;
    rememberOpenedSession("chat", item.id);
    var payload = { type: "chat", projectId: item.projectId, chatId: item.id };
    payload.topbarResource = resource;
    navigateFromSearch(payload);
  }

  function reloadWorkbench(_nextProjectId, _unused, options) {
    options = options || {};
    if (options.showLoading !== false) setLoading(true);
    setError("");
    return model.fetchProjects().then(function (next) {
      setStore(next);
      return next;
    }).catch(function (err) {
      setError(wbErrorText(err));
      return null;
    }).finally(function () {
      if (options.showLoading !== false) setLoading(false);
    });
  }

  function selectProject(projectId) {
    var project = store.projects.find(function (item) { return item.id === projectId; });
    if (!project) return;
    setStore(function (previous) {
      return Object.assign({}, previous, {
        activeProjectId: project.id,
        activeProject: project,
      });
    });
    model.setActiveProject(project.id).catch(function () {});
  }

  function openChatInWorkspace(chat) {
    if (!chat || !chat.id) return;
    rememberOpenedSession("chat", chat.id);
    navigateFromSearch({
      type: "chat",
      projectId: chat.projectId || (store.activeProject && store.activeProject.id) || "",
      chatId: chat.id,
    });
  }

  useWorkbenchStartupLifecycle(fullPage, reloadWorkbench, reloadNotifications);
  useWorkbenchRecentChatLifecycle(
    store.projects,
    reloadRecentChats,
    reloadWorkbench,
    setRecentChatsByProject
  );

  useWorkbenchLaunchOverlayLifecycle(
    loading, launchReadyRef, dataStore, searchOpen
  );

  useWorkbenchNativeMenuLifecycle(
    menuActionsRef, createProject, createChat, onToggleTheme,
    setSettingsTab, setSettingsScrollTo, setFullPage, toggleWorkspaceSidebar
  );

  useWorkbenchNotificationLifecycle(
    reloadNotifications, activeViewRef, fullPage, activeChatId,
    rememberOpenedSession
  );

  useWorkbenchGlobalShortcuts(
    searchOpen, newProjectOpen, store, setSearchOpen, createChat,
    setSettingsTab, setSettingsScrollTo, setFullPage,
    toggleWorkspaceSidebar, selectProject
  );

  useWorkbenchEffect(function () {
    return workbenchServices.navigation().setHandler(navigateFromSearch);
  }, [store.projects, store.activeProjectId]);

  function createProject() { setNewProjectOpen(true); }
  function createChat() {
    setFullPage("chat");
    setNewChatRequestId(function (value) { return value + 1; });
  }

  function handleCreateProject(input) {
    return model.createProject(input).then(function (next) {
      setStore(next);
      setFullPage("chat");
      setNewChatRequestId(function (value) { return value + 1; });
      return next;
    });
  }

  function handleUpdateProject(projectId, input) {
    return model.updateProject(projectId, input).then(function (next) {
      setStore(next);
      return next;
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
        setFullPage("chat");
        return next;
      }).catch(function (err) {
        setError(wbErrorText(err));
      });
    });
  }

  useWorkbenchBoardNavigation(setFullPage);

  useWorkbenchNavigationSurface(
    fullPage, settingsTab, railCollapsed, t, handleOpenPage,
    toggleWorkspaceSidebar, setSearchOpen, setSettingsTab, setSettingsScrollTo, setFullPage,
    enabledModules
  );

  function renderSidebarCollapseControl() {
    return <WorkbenchSidebarCollapseControl collapsed={railCollapsed} onToggle={toggleWorkspaceSidebar} />;
  }

  function renderSidebarDockSlot() {
    return <div className="workbench-sidebar-dock-slot" aria-hidden="true" />;
  }

  var modulePresentation = useWorkbenchModulePresentation(
    fullPage, setFullPage, mountedPages, setMountedPages, store, activeChatId,
    recentChatsByProject, {
      recent: recentOpenedSessionKeys, pinned: pinnedSessionKeys, hidden: hiddenSessionKeys,
    }, chatRuntimes, sessionActivityLive, dataState, t, enabledModules
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
    createChat();
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
      enabledModules: enabledModules,
      openPage: handleOpenPage, closePage: function () { setFullPage(null); },
      openSettings: openSettings, navigate: navigateFromSearch,
      navigateNotification: navigateFromNotification,
      toggleSidebar: toggleWorkspaceSidebar,
      renderCollapseControl: renderSidebarCollapseControl,
      renderDockSlot: renderSidebarDockSlot,
    },
    dialogs: {
      searchOpen: searchOpen, setSearchOpen: setSearchOpen,
      newProjectOpen: newProjectOpen,
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
        chatIds: pinnedSessionIds("chat"),
        onToggleChat: function (chat, pinned) {
          if (chat && chat.id) togglePinnedSession({ id: chat.id, kind: "chat" }, pinned);
        },
      },
    },
    chat: {
      module: chatModule, runtimeEngine: chatRuntimeEngine,
      newChatRequestId: newChatRequestId,
    },
    board: {
      loading: loading, error: error,
      openChat: openChatInWorkspace,
    },
    projectRail: {
      recentChatsByProject: recentChatsByProject,
    },
    actions: {
      createProject: createProject, createChat: createChat,
      selectProject: selectProject, deleteProject: handleDeleteProject,
    },
  };

  return (
    <div className="workbench-shell" data-screen-label="Cyrene · workbench">
      <WorkbenchShellTopbar context={shellContext} />
      {fullPageConfig ? (
        <WorkbenchFullPage config={fullPageConfig} onClose={function () { setFullPage(null); }} />
      ) : (
        <div ref={wbApplyStoredRightWidth} className={"workbench-grid integrated-sidebars" + (railCollapsed ? " rail-collapsed" : "") + (isKnowledge ? " is-knowledge" : "") + (isSchedule ? " is-schedule" : "") + (isMemory ? " is-memory" : "") + (isChat || modulePresentation.isBoard ? " is-chat" : "") + (modulePresentation.isBoard ? " is-conversation-board" : "") + (isSettings ? " is-settings" : "")} onWheel={handleSidebarModuleWheel}>
          <WorkbenchSidebarDock
            persistent={true}
            collapsed={railCollapsed}
            activePage={fullPage}
            activeDestination={activeDestination}
            onOpenPage={handleOpenPage}
            enabledModules={enabledModules}
            onSettings={function () { setSettingsTab(""); setSettingsScrollTo(null); setFullPage("settings"); }}
          />
          <WorkbenchModuleSurfaces context={shellContext} />
        </div>
      )}
      <WorkbenchSearchPortal
        open={searchOpen}
        onClose={function () { setSearchOpen(false); }}
        onOpenChatPage={function () { setFullPage("chat"); }}
        onCreateChat={createChat}
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
        memoryAvailable={enabledModules.indexOf("memory") >= 0}
        onCloseEditMemory={function () { setEditMemoryProject(null); }}
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
