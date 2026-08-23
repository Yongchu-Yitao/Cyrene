import { workbenchServices } from "../../shared/runtime/services.jsx"
import { WbVoiceCommand } from "../../workbench-chat.jsx"
import { wbLiveEventFromSse, wbMergeLiveEventIntoSession } from "../session/activity.jsx"
import { wbResetBrowserOverlayObscured, wbSetBrowserOverlayObscured } from "../../shared/browser/overlays.jsx"
import { dispatchWorkbenchGlobalShortcut } from "./global-shortcuts.mjs"

var { useEffect } = React;

function useWorkbenchStartupLifecycle(fullPage, rememberWelcomeHandled, reloadWorkbench, reloadNotifications) {
  useEffect(function () {
    try {
      if (fullPage && fullPage !== "welcome") localStorage.setItem("wb-active-page", fullPage);
      else localStorage.removeItem("wb-active-page");
    } catch (e) {}
  }, [fullPage]);

  useEffect(function () {
    if (fullPage !== "welcome") return;
    rememberWelcomeHandled();
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

function useWorkbenchRecentChatLifecycle(projects, reloadRecentChats, refreshTaskBoard, setRecentChatsByProject) {
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
        refreshTaskBoard();
        reloadRecentChats(projects || []);
      }, 420);
    }
    function onRuntimeEvent(data) {
      if (!data) return;
      if (data.type === "workbench_chat_changed" && wbApplyRecentChatSummary(setRecentChatsByProject, data)) {
        return;
      }
      if (["goal_loop_update", "session_update", "user_question", "user_question_answered", "workbench_chat_changed", "task_board_changed"].indexOf(data.type) >= 0) {
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
  pythonPromptCheckedRef,
  t,
  setSettingsTab,
  setSettingsScrollTo,
  setFullPage,
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
    if (pythonPromptCheckedRef.current) return undefined;
    pythonPromptCheckedRef.current = true;
    workbenchServices.api().json("/api/extensions", { toast: false })
      .then(function (payload) {
        if (!payload || payload.python_prompt_required !== true) return;
        workbenchServices.feedback().confirmModal({
          title: t("settings.extensionPythonMissingTitle"),
          body: t("settings.extensionPythonMissingBody"),
          confirmLabel: t("settings.extensionViewInstall"),
          cancelLabel: t("settings.close"),
        }).then(function (open) {
          if (open) {
            setSettingsTab("extensions");
            setSettingsScrollTo(null);
            setFullPage("settings");
          }
        });
      })
      .catch(function () {});
    return undefined;
  }, []);

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
  createSession,
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
      createSession: createSession,
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
        "new-task": function () { acts.createSession(); },
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
  taskView,
  activeChatId,
  activeSessionId,
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
      taskView: taskView,
      chatId: activeChatId || "",
      sessionId: activeSessionId || "",
    };
  }, [fullPage, taskView, activeChatId, activeSessionId]);

  useEffect(function () {
    if (fullPage === "chat" && activeChatId) {
      rememberOpenedSession("chat", activeChatId);
    } else if (taskView === "detail" && activeSessionId && (!fullPage || (fullPage === "chat" && !activeChatId))) {
      rememberOpenedSession("task", activeSessionId);
    }
  }, [fullPage, taskView, activeChatId, activeSessionId]);
}

function useWorkbenchGlobalShortcuts(
  searchOpen,
  newProjectOpen,
  newTaskOpen,
  store,
  setSearchOpen,
  createChat,
  setNewTaskOpen,
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
        newTaskOpen: newTaskOpen,
        hasActiveProject: !!(store && store.activeProject),
        projects: (store && store.projects) || [],
      }, {
        openSearch: function () { setSearchOpen(true); },
        createChat: createChat,
        createTask: function () { setNewTaskOpen(true); },
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
  }, [searchOpen, newProjectOpen, newTaskOpen, store && store.activeProject, store && store.projects]);
}

function useWorkbenchTaskRuntimeLifecycle(fullPage, taskView, refreshTaskBoard, activeViewRef, setStore, fetchAndMergeSession) {
  useEffect(function () {
    if (fullPage || taskView !== "board") return undefined;
    var trailing = null;
    var inFlight = false;
    function tick() {
      if (document.hidden || inFlight) return;
      inFlight = true;
      refreshTaskBoard().finally(function () { inFlight = false; });
    }
    function scheduleTick() {
      if (trailing) clearTimeout(trailing);
      trailing = setTimeout(function () { trailing = null; tick(); }, 220);
    }
    function onVisibility() { if (!document.hidden) scheduleTick(); }
    function onRuntimeEvent(data) {
      if (data && ["goal_loop_update", "session_update", "task_board_changed"].indexOf(data.type) >= 0) scheduleTick();
    }
    document.addEventListener("visibilitychange", onVisibility);
    window.addEventListener("focus", scheduleTick);
    var unsubscribe = workbenchServices.events().subscribe(onRuntimeEvent);
    return function () {
      if (trailing) clearTimeout(trailing);
      document.removeEventListener("visibilitychange", onVisibility);
      window.removeEventListener("focus", scheduleTick);
      unsubscribe();
    };
  }, [fullPage, taskView]);

  useEffect(function () {
    var goalLoopReloadTimer = null;
    function handleRuntimeEvent(data) {
      if (!data) return;
      if (data.type === "goal_loop_update") {
        var activeSessionId = String((activeViewRef.current && activeViewRef.current.sessionId) || "");
        var eventSessionId = String(data.session_id || "");
        if (eventSessionId && eventSessionId !== activeSessionId) return;
        var publicLoop = data.goal_loop && typeof data.goal_loop === "object" ? data.goal_loop : null;
        if (activeSessionId && publicLoop) {
          setStore(function (prev) {
            var active = prev && prev.activeSession;
            if (!active || String(active.id || "") !== activeSessionId) return prev;
            var loopStatus = String(publicLoop.status || "");
            var nextSessionPatch = { goalLoop: publicLoop };
            if (["running", "waiting_for_user", "paused", "blocked", "review", "cancelled"].indexOf(loopStatus) >= 0) nextSessionPatch.status = loopStatus;
            function mergeSession(session) {
              return session && String(session.id || "") === activeSessionId ? Object.assign({}, session, nextSessionPatch) : session;
            }
            var nextProjects = (prev.projects || []).map(function (project) {
              if (!project || project.id !== prev.activeProjectId) return project;
              return Object.assign({}, project, { sessions: (project.sessions || []).map(mergeSession) });
            });
            return Object.assign({}, prev, {
              projects: nextProjects,
              activeProject: nextProjects.find(function (project) { return project.id === prev.activeProjectId; }) || prev.activeProject,
              activeSession: Object.assign({}, active, nextSessionPatch),
            });
          });
        }
        if (goalLoopReloadTimer) clearTimeout(goalLoopReloadTimer);
        goalLoopReloadTimer = setTimeout(function () {
          goalLoopReloadTimer = null;
          fetchAndMergeSession(eventSessionId || activeSessionId, { showLoading: false });
        }, 1600);
        return;
      }
      if (["tool_call", "llm_call", "subagent_update"].indexOf(data.type) < 0) return;
      setStore(function (prev) {
        var active = prev && prev.activeSession;
        if (!active || (active.status !== "running" && !active.agentBusy)) return prev;
        var dataSessionId = String(data.session_id || "").trim();
        if (dataSessionId && dataSessionId !== active.id) return prev;
        if (!dataSessionId && String(data.caller || "").indexOf("subagent_") !== 0) return prev;
        var event = wbLiveEventFromSse(data);
        if (!event) return prev;
        var nextSession = wbMergeLiveEventIntoSession(active, event);
        if (nextSession === active) return prev;
        function mergeSession(session) { return session && session.id === active.id ? nextSession : session; }
        var nextProjects = (prev.projects || []).map(function (project) {
          if (!project || project.id !== prev.activeProjectId) return project;
          return Object.assign({}, project, { sessions: (project.sessions || []).map(mergeSession) });
        });
        return Object.assign({}, prev, {
          projects: nextProjects,
          activeProject: nextProjects.find(function (project) { return project.id === prev.activeProjectId; }) || prev.activeProject,
          activeSession: nextSession,
        });
      });
    }
    var unsubscribe = workbenchServices.events().subscribe(handleRuntimeEvent);
    return function () {
      unsubscribe();
      if (goalLoopReloadTimer) clearTimeout(goalLoopReloadTimer);
    };
  }, []);
}

export {
  useWorkbenchGlobalShortcuts,
  useWorkbenchLaunchOverlayLifecycle,
  useWorkbenchNativeMenuLifecycle,
  useWorkbenchNotificationLifecycle,
  useWorkbenchRecentChatLifecycle,
  useWorkbenchStartupLifecycle,
  useWorkbenchTaskRuntimeLifecycle,
}
