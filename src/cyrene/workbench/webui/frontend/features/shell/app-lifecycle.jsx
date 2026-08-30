import { workbenchServices } from "../../shared/runtime/services.jsx"
import { WbVoiceCommand } from "../../workbench-chat.jsx"
import { wbResetBrowserOverlayObscured, wbSetBrowserOverlayObscured } from "../../shared/browser/overlays.jsx"
import { dispatchWorkbenchGlobalShortcut } from "./global-shortcuts.mjs"

var { useEffect } = React;

function useWorkbenchStartupLifecycle(fullPage, reloadWorkbench, reloadNotifications) {
  useEffect(function () {
    try {
      if (fullPage) localStorage.setItem("wb-active-page", fullPage);
      else localStorage.removeItem("wb-active-page");
    } catch (e) {}
  }, [fullPage]);

  useEffect(function () {
    reloadWorkbench();
    reloadNotifications();
  }, []);
}

function wbApplyRecentChatSummary(setRecentChatsByProject, event) {
  var summary = event && event.chatSummary;
  if (!summary || typeof summary !== "object") return false;
  var projectId = String(summary.projectId || event.project_id || "");
  var chatId = String(summary.id || event.chat_id || event.session_id || "");
  if (!projectId || !chatId) return false;
  var runStatus = String(event.run_status || "");
  var projected = { ...summary };
  if (runStatus) {
    projected.runStatus = runStatus;
    projected.status = runStatus === "running" ? "running" : "idle";
  }
  setRecentChatsByProject(function (current) {
    var list = Array.isArray(current[projectId]) ? current[projectId] : [];
    var found = false;
    var next = list.map(function (chat) {
      if (String(chat && chat.id || "") !== chatId) return chat;
      found = true;
      return { ...chat, ...projected };
    });
    if (!found) next.unshift(projected);
    return { ...current, [projectId]: next };
  });
  return true;
}

function useWorkbenchRecentChatLifecycle(projects, reloadRecentChats, reloadProjects, setRecentChatsByProject) {
  var recentProjectIds = (projects || []).map(function (project) {
    return String((project && project.id) || "");
  }).filter(Boolean).sort().join("|");

  useEffect(function () {
    reloadRecentChats(projects || []);
  }, [recentProjectIds]);

  useEffect(function () {
    function onChatsChanged() { reloadRecentChats(projects || []); }
    window.addEventListener("cyrene:wbc-refresh-chats", onChatsChanged);
    return function () { window.removeEventListener("cyrene:wbc-refresh-chats", onChatsChanged); };
  }, [recentProjectIds]);

  useEffect(function () {
    var timer = null;
    function refreshTopbarSessions() {
      if (timer) clearTimeout(timer);
      timer = setTimeout(function () {
        timer = null;
        reloadProjects(undefined, undefined, { showLoading: false });
        reloadRecentChats(projects || []);
      }, 420);
    }
    function onRuntimeEvent(data) {
      if (!data) return;
      if (data.type === "workbench_chat_changed" && wbApplyRecentChatSummary(setRecentChatsByProject, data)) {
        return;
      }
      if (["user_question", "user_question_answered", "workbench_chat_changed"].indexOf(data.type) >= 0) {
        refreshTopbarSessions();
      }
    }
    var unsubscribe = workbenchServices.events().subscribe(onRuntimeEvent);
    return function () {
      if (timer) clearTimeout(timer);
      unsubscribe();
    };
  }, [recentProjectIds]);
}

function useWorkbenchLaunchOverlayLifecycle(
  loading,
  launchReadyRef,
  dataStore,
  searchOpen
) {
  useEffect(function () {
    if (loading || launchReadyRef.current) return undefined;
    launchReadyRef.current = true;
    Promise.resolve(dataStore.ready)
      .catch(function () {})
      .then(function () { workbenchServices.readiness().markReady(); });
    return undefined;
  }, [loading]);

  useEffect(function () { wbResetBrowserOverlayObscured(); }, []);

  useEffect(function () {
    if (searchOpen) {
      wbSetBrowserOverlayObscured(1);
      return function () { wbSetBrowserOverlayObscured(-1); };
    }
  }, [searchOpen]);

  useEffect(function () {
    function onBeforeUnload() {
      var bridge = window.cyrene && window.cyrene.browser;
      if (bridge && typeof bridge.setObscured === "function") {
        bridge.setObscured(true).catch(function (err) {
          console.error("beforeunload setObscured failed", err);
        });
      }
    }
    window.addEventListener("beforeunload", onBeforeUnload);
    return function () { window.removeEventListener("beforeunload", onBeforeUnload); };
  }, []);
}

function useWorkbenchNativeMenuLifecycle(
  menuActionsRef,
  createProject,
  createChat,
  onToggleTheme,
  setSettingsTab,
  setSettingsScrollTo,
  setFullPage,
  toggleWorkspaceSidebar
) {
  useEffect(function () {
    menuActionsRef.current = {
      createProject: createProject,
      createChat: createChat,
      onToggleTheme: onToggleTheme,
    };
  });

  useEffect(function () {
    var bridge = window.cyrene;
    if (!bridge || typeof bridge.onMenuAction !== "function") return undefined;
    return bridge.onMenuAction(function (action) {
      var acts = menuActionsRef.current;
      var map = {
        "open-settings": function () { setSettingsTab(""); setSettingsScrollTo(null); setFullPage("settings"); },
        "open-about": function () { setSettingsTab("about"); setSettingsScrollTo(null); setFullPage("settings"); },
        "new-project": function () { acts.createProject(); },
        "new-chat": function () { acts.createChat(); },
        "toggle-theme": function () { acts.onToggleTheme(); },
        "toggle-sidebar": function () { toggleWorkspaceSidebar(); },
      };
      var fn = map[action];
      if (fn) fn();
    });
  }, []);
}

function useWorkbenchNotificationLifecycle(
  reloadNotifications,
  activeViewRef,
  fullPage,
  activeChatId,
  rememberOpenedSession
) {
  useEffect(function () {
    var timer = null;
    function scheduleReload() {
      if (timer) clearTimeout(timer);
      timer = setTimeout(function () { timer = null; reloadNotifications(); }, 80);
    }
    function handleEvent(data) {
      if (!data || ["notification", "notification_changed"].indexOf(data.type) < 0) return;
      scheduleReload();
    }
    function onVisibility() { if (!document.hidden) scheduleReload(); }
    var unsubscribe = workbenchServices.events().subscribe(handleEvent);
    document.addEventListener("visibilitychange", onVisibility);
    window.addEventListener("focus", scheduleReload);
    return function () {
      if (timer) clearTimeout(timer);
      unsubscribe();
      document.removeEventListener("visibilitychange", onVisibility);
      window.removeEventListener("focus", scheduleReload);
    };
  }, []);

  useEffect(function () {
    activeViewRef.current = {
      page: fullPage || null,
      chatId: activeChatId || "",
    };
  }, [fullPage, activeChatId]);

  useEffect(function () {
    if (fullPage === "chat" && activeChatId) {
      rememberOpenedSession("chat", activeChatId);
    }
  }, [fullPage, activeChatId]);
}

function useWorkbenchGlobalShortcuts(
  searchOpen,
  newProjectOpen,
  store,
  setSearchOpen,
  createChat,
  setSettingsTab,
  setSettingsScrollTo,
  setFullPage,
  toggleWorkspaceSidebar,
  selectProject
) {
  useEffect(function () {
    function onKey(event) {
      var sc = workbenchServices.shortcuts();
      dispatchWorkbenchGlobalShortcut(event, sc, {
        searchOpen: searchOpen,
        newProjectOpen: newProjectOpen,
        hasActiveProject: !!(store && store.activeProject),
        projects: (store && store.projects) || [],
      }, {
        openSearch: function () { setSearchOpen(true); },
        createChat: createChat,
        startVoice: WbVoiceCommand.start,
        openShortcutSettings: function () {
          setSettingsTab("shortcuts");
          setSettingsScrollTo(null);
          setFullPage("settings");
        },
        toggleSidebar: toggleWorkspaceSidebar,
        selectProject: selectProject,
      });
    }
    window.addEventListener("keydown", onKey);
    return function () { window.removeEventListener("keydown", onKey); };
  }, [searchOpen, newProjectOpen, store && store.activeProject, store && store.projects]);
}

export {
  useWorkbenchGlobalShortcuts,
  useWorkbenchLaunchOverlayLifecycle,
  useWorkbenchNativeMenuLifecycle,
  useWorkbenchNotificationLifecycle,
  useWorkbenchRecentChatLifecycle,
  useWorkbenchStartupLifecycle,
}
