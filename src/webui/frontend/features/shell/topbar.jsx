import { workbenchServices } from "../../shared/runtime/services.jsx"
import { WbVoiceCommand } from "../../workbench-chat.jsx"
import { WbcHoverMarquee } from "../chat/rail.jsx"
import {
  wbCopyBrowserToChat,
  wbDeliverResourceToChat,
  wbNotificationNavigationTarget,
  wbSessionActivityRank,
  wbSplitOverflowSessions,
} from "../session/activity.jsx"
import { wbSetBrowserOverlayObscured } from "../../shared/browser/overlays.jsx"
import { WorkbenchHelpCenter } from "./support.jsx"

var {
  useEffect: useWorkbenchEffect,
  useRef: useWorkbenchRef,
  useState: useWorkbenchState,
} = React;
var WorkbenchModel = workbenchServices.model();

function WorkbenchSessionMenuFileName({ name }) {
  var labelRef = useWorkbenchRef(null);
  var [overflowWidth, setOverflowWidth] = useWorkbenchState(0);
  var text = String(name || "");

  useWorkbenchEffect(function () {
    var node = labelRef.current;
    if (!node) return undefined;
    var frame = 0;
    function measure() {
      cancelAnimationFrame(frame);
      frame = requestAnimationFrame(function () {
        var content = node.firstElementChild;
        var contentWidth = content ? content.scrollWidth : node.scrollWidth;
        var next = Math.max(0, Math.ceil(contentWidth - node.clientWidth));
        setOverflowWidth(function (current) { return current === next ? current : next; });
      });
    }
    measure();
    var observer = typeof ResizeObserver !== "undefined" ? new ResizeObserver(measure) : null;
    if (observer) observer.observe(node);
    window.addEventListener("resize", measure);
    return function () {
      cancelAnimationFrame(frame);
      if (observer) observer.disconnect();
      window.removeEventListener("resize", measure);
    };
  }, [text]);

  return (
    <span
      ref={labelRef}
      className={"workbench-session-menu-file-name" + (overflowWidth > 0 ? " is-overflowing" : "")}
      title={overflowWidth > 0 ? text : undefined}
      style={{
        "--wb-file-name-travel": overflowWidth + "px",
        "--wb-file-name-duration": Math.max(2.4, Math.min(8, overflowWidth / 34)) + "s",
      }}
    >
      <span>{text}</span>
    </span>
  );
}
function wbSessionStatusLabel(activity, t) {
  var state = activity || {};
  if (state.phase === "attention") {
    return {
      input: t("workbench.sessionStatus.needsInput", "Needs input"),
      approval: t("workbench.sessionStatus.needsApproval", "Needs approval"),
      review: t("workbench.sessionStatus.needsReview", "Needs review"),
      blocked: t("workbench.sessionStatus.blocked", "Blocked"),
    }[state.reason] || t("workbench.sessionStatus.needsAttention", "Needs attention");
  }
  return {
    idle: t("workbench.sessionStatus.idle", "Idle"),
    planning: state.isLive
      ? t("workbench.sessionStatus.planning", "Planning")
      : t("workbench.sessionStatus.planningStage", "Planning stage"),
    running: t("workbench.sessionStatus.running", "Running"),
    paused: t("workbench.sessionStatus.paused", "Paused"),
    cancelled: t("workbench.sessionStatus.cancelled", "Stopped"),
    completed: t("workbench.sessionStatus.completed", "Completed"),
    failed: t("workbench.sessionStatus.failed", "Failed"),
  }[state.phase] || t("workbench.sessionStatus.idle", "Idle");
}

function wbSessionActivityCopy(activity, t) {
  var state = activity || {};
  if (state.phase === "attention" || state.phase === "failed" || state.phase === "paused" || state.phase === "cancelled" || state.phase === "completed") {
    return wbSessionStatusLabel(state, t);
  }
  if (state.phase === "planning") return wbSessionStatusLabel(state, t);
  if (state.phase === "running") {
    if (state.activity && state.activity.kind === "browser" && state.activity.label) return state.activity.label;
    if (state.progress && state.progress.current && state.progress.total) {
      return t("workbench.sessionStatus.step", {
        current: state.progress.current,
        total: state.progress.total,
      }, "Step {current}/{total}");
    }
    if (state.activity && state.activity.label) return state.activity.label;
  }
  return "";
}

function WorkbenchSessionStatusIcon({ phase, active }) {
  var state = String(phase || "idle");
  if (state === "attention") {
    return <svg className="workbench-session-status-svg" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round"><path d="M8 2 14 13H2Z"/><path d="M8 5.5v3.4M8 11.3h.01"/></svg>;
  }
  if (state === "completed") {
    return <svg className="workbench-session-status-svg" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"><path d="m3.2 8.2 3 3L12.8 4.8"/></svg>;
  }
  if (state === "failed") {
    return <svg className="workbench-session-status-svg" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round"><circle cx="8" cy="8" r="5.6"/><path d="m6 6 4 4m0-4-4 4"/></svg>;
  }
  if (state === "cancelled") {
    return <svg className="workbench-session-status-svg" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round"><circle cx="8" cy="8" r="5.6"/><path d="M5.7 8h4.6"/></svg>;
  }
  if (state === "paused") {
    return <svg className="workbench-session-status-svg" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round"><path d="M5.7 4.5v7M10.3 4.5v7"/></svg>;
  }
  return <span className={"workbench-session-status-dot " + state + (active ? " is-live" : "")} />;
}

function WorkbenchAssetIcon({ name, className }) {
  var assets = window.CyreneIconAssets;
  var markup = assets && assets.settings && assets.settings[name] || "";
  if (!markup) return null;
  return <span className={className || "workbench-asset-icon"} dangerouslySetInnerHTML={{ __html: markup }} aria-hidden="true" />;
}

function WorkbenchSessionActivityPreview({ preview, t }) {
  if (!preview) return null;
  var item = preview.item;
  var activity = preview.activity || {};
  var progress = activity.progress || {};
  var activeAgents = (activity.agents || []).filter(function (agent) {
    return ["running", "resumed", "waiting"].indexOf(String(agent.status || "")) >= 0;
  });
  var percent = progress.total ? Math.max(0, Math.min(100, Math.round((progress.completed / progress.total) * 100))) : 0;
  return (
    <div
      id="workbench-session-activity-preview"
      className="workbench-session-activity-preview"
      role="tooltip"
      style={{ left: preview.left, top: preview.top, ...preview.portalTheme }}
    >
      <div className="workbench-session-activity-preview-head">
        <WorkbenchSessionStatusIcon phase={activity.phase} active={activity.isLive} />
        <div><b>{item.title}</b><small>{wbSessionStatusLabel(activity, t)}</small></div>
      </div>
      {progress.total ? (
        <div className="workbench-session-activity-progress">
          <div><span>{t("workbench.sessionStatus.progress", "Progress")}</span><b>{progress.current || progress.completed}/{progress.total}</b></div>
          <span className="workbench-session-activity-progress-track"><i style={{ width: percent + "%" }} /></span>
          {progress.title || progress.action ? <p>{progress.action || progress.title}</p> : null}
        </div>
      ) : null}
      {activity.activity && (activity.activity.label || activity.activity.detail) ? (
        <div className="workbench-session-activity-current">
          <span>{activity.activity.kind === "browser" ? t("workbench.sessionStatus.browsing", "Browsing") : t("workbench.sessionStatus.currentActivity", "Current activity")}</span>
          <b>{activity.activity.label || activity.activity.detail}</b>
          {activity.activity.label && activity.activity.detail ? <small>{activity.activity.detail}</small> : null}
        </div>
      ) : null}
      {activeAgents.length ? <div className="workbench-session-activity-agents">{t("workbench.sessionStatus.agentsRunning", { count: activeAgents.length }, "{count} agents active")}</div> : null}
    </div>
  );
}

function WorkbenchTopbar({ projects, activeProject, activePage, taskView, activeTaskId, activeChatId, recentSessions, overflowSessions, browserOwners, pinnedResources, keyboardEnabled, onPinResource, onUnpinResource, onOpenPinnedResource, onOpenSession, onOpenBrowserPage, onPauseSession, onStopSession, onTogglePinnedSession, onRemoveSessionTab, onLoadSessionResources, onLoadSessionBrowserPreview, onOpenSessionResource, notifications, onReloadNotifications, onOpenNotification, onSearch, onSettings, onNewProject, onSelectProject, onEditProject, onEditMemory, onDeleteProject, onNewTask, onOpenPage, theme, actualTheme, onToggleTheme }) {
  var { t } = workbenchServices.i18n().use();
  var dataState = workbenchServices.data().state;
  var pluginModules = Array.isArray(dataState.pluginModules) ? dataState.pluginModules : [];
  var browserAvailable = pluginModules.indexOf("browser") >= 0;
  var memoryAvailable = pluginModules.indexOf("memory") >= 0;
  var tabs = Array.isArray(recentSessions) ? recentSessions : [];
  var overflowTabs = Array.isArray(overflowSessions) ? overflowSessions : [];
  var overflowGroups = wbSplitOverflowSessions(overflowTabs);
  var resources = (Array.isArray(pinnedResources) ? pinnedResources : []).filter(function (resource) {
    return browserAvailable || !resource || resource.kind !== "browser";
  });
  var [sessionMenu, setSessionMenu] = useWorkbenchState(null);
  var [resourceMenu, setResourceMenu] = useWorkbenchState(null);
  var [overflowMenu, setOverflowMenu] = useWorkbenchState(null);
  var [hoverPreview, setHoverPreview] = useWorkbenchState(null);
  var [activityClock, setActivityClock] = useWorkbenchState(function () { return Date.now(); });
  var [resourceDropActive, setResourceDropActive] = useWorkbenchState(false);
  var [chatSideHidden, setChatSideHidden] = useWorkbenchState(false);
  var [projectMenuOpen, setProjectMenuOpen] = useWorkbenchState(false);
  var [projectActionId, setProjectActionId] = useWorkbenchState("");
  var [voiceCommand, setVoiceCommand] = useWorkbenchState(function () { return WbVoiceCommand.snapshot(); });
  var [browserManagerState, setBrowserManagerState] = useWorkbenchState({ ok: true, pageCount: 0, downloadCount: 0, pages: [], downloads: [] });
  var [browserManagerMenu, setBrowserManagerMenu] = useWorkbenchState(null);
  var topbarRef = useWorkbenchRef(null);
  var projectMenuRef = useWorkbenchRef(null);
  var browserManagerRef = useWorkbenchRef(null);
  var sessionMenuSeqRef = useWorkbenchRef(0);
  var previewTimerRef = useWorkbenchRef(0);
  var terminalMorphKey = tabs.map(function (item) {
    return item.kind + ":" + item.id + ":" + Number(item.activity && item.activity.morphUntil || 0);
  }).join("|");

  useWorkbenchEffect(function () {
    return WbVoiceCommand.subscribe(setVoiceCommand);
  }, []);

  useWorkbenchEffect(function () {
    if (!browserAvailable) {
      setBrowserManagerState({ ok: true, pageCount: 0, downloadCount: 0, pages: [], downloads: [] });
      setBrowserManagerMenu(null);
      return undefined;
    }
    var bridge = window.cyrene && window.cyrene.browser;
    if (!bridge || typeof bridge.getManagerState !== "function") return undefined;
    var mounted = true;
    bridge.getManagerState().then(function (next) {
      if (mounted && next && next.ok !== false) setBrowserManagerState(next);
    }).catch(function () {});
    var unsubscribe = typeof bridge.onManagerState === "function"
      ? bridge.onManagerState(function (next) {
          if (mounted && next && next.ok !== false) setBrowserManagerState(next);
        })
      : function () {};
    return function () {
      mounted = false;
      unsubscribe();
    };
  }, [browserAvailable]);

  useWorkbenchEffect(function () {
    if (!browserManagerMenu) return undefined;
    wbSetBrowserOverlayObscured(1);
    function close(event) {
      if (event && event.key && event.key !== "Escape") return;
      setBrowserManagerMenu(null);
    }
    window.addEventListener("resize", close);
    document.addEventListener("keydown", close);
    return function () {
      window.removeEventListener("resize", close);
      document.removeEventListener("keydown", close);
      wbSetBrowserOverlayObscured(-1);
    };
  }, [!!browserManagerMenu]);

  useWorkbenchEffect(function () {
    if (browserManagerMenu && !browserManagerState.pageCount && !browserManagerState.downloadCount) {
      setBrowserManagerMenu(null);
    }
  }, [browserManagerState.pageCount, browserManagerState.downloadCount, !!browserManagerMenu]);

  useWorkbenchEffect(function () {
    if (!projectMenuOpen) return undefined;
    function closeProjectMenu(event) {
      if (event.key && event.key !== "Escape") return;
      if (!event.key && projectMenuRef.current && projectMenuRef.current.contains(event.target)) return;
      setProjectMenuOpen(false);
      setProjectActionId("");
    }
    document.addEventListener("mousedown", closeProjectMenu);
    document.addEventListener("keydown", closeProjectMenu);
    return function () {
      document.removeEventListener("mousedown", closeProjectMenu);
      document.removeEventListener("keydown", closeProjectMenu);
    };
  }, [projectMenuOpen]);

  // Project switching is navigation on the user's current surface. Expose the
  // same menu/select handlers to the semantic surface; the agent cannot pass a
  // hidden project id directly to a renderer action.
  useWorkbenchEffect(function () {
    if (!window.CyreneUI.has("uiSurface")) return undefined;
    var uiSurface = workbenchServices.uiSurface();
    var unregister = [];
    unregister.push(uiSurface.register({
      node_id: "project_switcher",
      parent_id: "root",
      scope: "main",
      order: 10,
      get_node: function () {
        return {
          role: "button",
          name: t("rail.projects", "Projects"),
          value_summary: activeProject ? String(activeProject.name || "") : "",
          state: { expanded: projectMenuOpen, project_id: String(activeProject && activeProject.id || "") },
        };
      },
      actions: [{
        action_id: "open_menu", kind: "open_menu", risk: "R1", gesture_aliases: ["press", "keyboard"],
        outcome: { effect: "opens_menu", target_node_id: "project_menu", target_scope: "project_menu", inspect_after: true },
      }],
      handlers: { open_menu: function () { setProjectActionId(""); setProjectMenuOpen(true); } },
    }));
    if (projectMenuOpen) {
      uiSurface.setScope("project_menu");
      unregister.push(uiSurface.register({
        node_id: "project_menu",
        parent_id: "root",
        scope: "project_menu",
        get_node: function () { return { role: "menu", name: t("rail.projects", "Projects") }; },
        actions: [{ action_id: "dismiss", kind: "dismiss", risk: "R1", gesture_aliases: ["escape_key", "scrim"] }],
        handlers: { dismiss: function () { setProjectActionId(""); setProjectMenuOpen(false); } },
      }));
      (Array.isArray(projects) ? projects : []).forEach(function (project) {
        var projectId = String(project.id || "");
        unregister.push(uiSurface.register({
          node_id: "project_" + projectId.replace(/[^a-zA-Z0-9_-]/g, "").slice(0, 100),
          parent_id: "project_menu",
          scope: "project_menu",
          get_node: function () {
            return projectId ? {
              role: "menuitemradio",
              name: String(project.name || t("workbench.selectProject", "Select project")),
              value_summary: WorkbenchModel.pathLabel(project.workspacePath, project.name),
              state: {
                project_id: projectId,
                selected: String(activeProject && activeProject.id || "") === projectId,
              },
            } : null;
          },
          actions: [{ action_id: "select", kind: "select", risk: "R1", gesture_aliases: ["press", "keyboard"] }],
          handlers: {
            select: function () {
              setProjectActionId("");
              setProjectMenuOpen(false);
              return onSelectProject && onSelectProject(projectId);
            },
          },
        }));
      });
    } else if (uiSurface.getScope() === "project_menu") {
      uiSurface.setScope("main");
    }
    return function () { unregister.forEach(function (remove) { remove(); }); };
  }, [projects, activeProject && activeProject.id, activeProject && activeProject.name, projectMenuOpen, onSelectProject, t]);

  useWorkbenchEffect(function () {
    var now = Date.now();
    var nextExpiry = tabs.reduce(function (soonest, item) {
      var expiry = Number(item.activity && item.activity.morphUntil || 0);
      if (expiry <= now) return soonest;
      return !soonest || expiry < soonest ? expiry : soonest;
    }, 0);
    setActivityClock(now);
    if (!nextExpiry) return undefined;
    var timer = setTimeout(function () { setActivityClock(Date.now()); }, Math.max(16, nextExpiry - now + 20));
    return function () { clearTimeout(timer); };
  }, [terminalMorphKey]);
  function acceptsResourceDrag(event, resourceApi, includeConversation) {
    var transfer = event && event.dataTransfer;
    if (!transfer || !resourceApi) return false;
    var types = Array.prototype.slice.call(transfer.types || []);
    if (types.indexOf(resourceApi.mime) >= 0) return true;
    if (includeConversation && resourceApi.chatMime && types.indexOf(resourceApi.chatMime) >= 0) return true;
    // Selected text on macOS uses Chromium's native text/plain drag. Files are
    // deliberately excluded because their cards have the richer custom type.
    return types.indexOf("text/plain") >= 0 && types.indexOf("Files") < 0;
  }
  var themeTitle = theme === "system" ? t("workbench.theme.system") : actualTheme === "dark" ? t("workbench.theme.dark") : t("workbench.theme.light");
  var themeIcon = theme === "system" ? (
    <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" strokeWidth="1.8"><circle cx="12" cy="12" r="9"/><path d="M12 3a9 9 0 0 1 0 18Z" fill="currentColor" stroke="none"/></svg>
  ) : actualTheme === "dark" ? (
    <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"><path d="M21 12.8A9 9 0 1 1 11.2 3 7 7 0 0 0 21 12.8Z"/></svg>
  ) : (
    <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"><circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4"/></svg>
  );

  function readTopbarPortalTheme() {
    var portalTheme = {};
    var themeSource = document.querySelector(".workbench-shell");
    if (themeSource && typeof getComputedStyle === "function") {
      var computedTheme = getComputedStyle(themeSource);
      [
        "--wb-surface", "--wb-card-bg", "--wb-card-bg-strong", "--wb-line", "--wb-line-2",
        "--wb-text", "--wb-muted", "--wb-faint",
        "--wb-control-bg", "--wb-control-hover-bg", "--wb-row-hover-bg",
        "--wb-flyout-bg", "--wb-flyout-border", "--wb-flyout-shadow",
        "--wb-green", "--wb-amber", "--wb-red", "--wb-accent", "--wb-ui-font-scale",
      ].forEach(function (name) { portalTheme[name] = computedTheme.getPropertyValue(name); });
      portalTheme.fontFamily = computedTheme.fontFamily;
    }
    return portalTheme;
  }

  function browserOwnerSession(page) {
    return (Array.isArray(browserOwners) ? browserOwners : tabs.concat(overflowTabs)).find(function (item) {
      return item.kind === "chat" && String(item.id || "") === String(page && page.sessionId || "");
    }) || null;
  }

  function browserPageDomain(page) {
    try { return new URL(String(page && page.url || "")).hostname.replace(/^www\./, ""); } catch (e) { return ""; }
  }

  function browserDownloadSize(value) {
    var bytes = Math.max(0, Number(value) || 0);
    if (bytes < 1024) return bytes + " B";
    if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(bytes < 10 * 1024 ? 1 : 0) + " KB";
    if (bytes < 1024 * 1024 * 1024) return (bytes / (1024 * 1024)).toFixed(bytes < 10 * 1024 * 1024 ? 1 : 0) + " MB";
    return (bytes / (1024 * 1024 * 1024)).toFixed(1) + " GB";
  }

  function pinnedBrowserResource(page) {
    if (!page) return null;
    return resources.find(function (resource) {
      if (!resource || resource.kind !== "browser") return false;
      if (String(resource.ownerSessionId || "") !== String(page.sessionId || "")) return false;
      var stableRef = String(resource.stableRef || "");
      return !stableRef || stableRef === String(page.sessionId || "") || stableRef === String(page.sessionId || "") + ":" + String(page.tabId || "");
    }) || null;
  }

  function openBrowserManagerMenu(event) {
    event.preventDefault();
    event.stopPropagation();
    var rect = event.currentTarget.getBoundingClientRect();
    var width = Math.min(390, window.innerWidth - 16);
    setBrowserManagerMenu({
      left: Math.max(8, Math.min(rect.left + rect.width / 2 - width / 2, window.innerWidth - width - 8)),
      top: Math.min(window.innerHeight - 12, rect.bottom + 8),
      width: width,
      portalTheme: readTopbarPortalTheme(),
    });
  }

  function openManagedBrowserPage(page) {
    if (!page || page.closed) return;
    var bridge = window.cyrene && window.cyrene.browser;
    if (bridge && typeof bridge.activateTab === "function") {
      bridge.activateTab({ sessionId: page.sessionId, tabId: page.tabId }).catch(function () {});
    }
    setBrowserManagerMenu(null);
    if (onOpenBrowserPage) onOpenBrowserPage(page, browserOwnerSession(page));
  }

  function reloadManagedBrowserPage(page, event) {
    if (event) event.stopPropagation();
    var bridge = window.cyrene && window.cyrene.browser;
    if (!bridge || !page || page.closed || typeof bridge.reload !== "function") return;
    bridge.reload({ sessionId: page.sessionId, tabId: page.tabId }).catch(function () {});
  }

  function muteManagedBrowserPage(page, event) {
    if (event) event.stopPropagation();
    var bridge = window.cyrene && window.cyrene.browser;
    if (!bridge || !page || page.closed || typeof bridge.setMuted !== "function") return;
    bridge.setMuted({ sessionId: page.sessionId, tabId: page.tabId, muted: !page.muted }).catch(function () {});
  }

  function closeManagedBrowserPage(page, event) {
    if (event) event.stopPropagation();
    var bridge = window.cyrene && window.cyrene.browser;
    if (!bridge || !page || page.closed || typeof bridge.closeTab !== "function") return;
    bridge.closeTab({ sessionId: page.sessionId, tabId: page.tabId }).catch(function () {});
  }

  function controlManagedBrowserDownload(download, action, event) {
    if (event) event.stopPropagation();
    var bridge = window.cyrene && window.cyrene.browser;
    if (!bridge || !download || typeof bridge.controlDownload !== "function") return;
    bridge.controlDownload({ downloadId: download.id, action: action }).then(function (result) {
      if (result && result.state && result.state.ok !== false) setBrowserManagerState(result.state);
    }).catch(function () {});
  }

  function toggleManagedBrowserPin(page, event) {
    if (event) event.stopPropagation();
    if (!page || page.closed) return;
    var pinned = pinnedBrowserResource(page);
    if (pinned) {
      if (onUnpinResource) onUnpinResource(pinned);
      return;
    }
    if (!onPinResource) return;
    var owner = browserOwnerSession(page);
    onPinResource({
      kind: "browser",
      ownerSessionId: String(page.sessionId || ""),
      ownerProjectId: String(owner && owner.projectId || ""),
      ownerProjectName: String(owner && owner.projectName || ""),
      stableRef: String(page.sessionId || ""),
      title: String(page.title || browserPageDomain(page) || page.url || t("workbench.resourceShelf.browser", "Browser")),
      url: String(page.url || ""),
      tabId: String(page.tabId || ""),
      favicon: String(page.favicon || ""),
    });
  }

  function closeSessionPreview() {
    if (previewTimerRef.current) clearTimeout(previewTimerRef.current);
    previewTimerRef.current = 0;
    setHoverPreview(null);
  }

  function scheduleSessionPreview(event, item, activity, immediate) {
    if (previewTimerRef.current) clearTimeout(previewTimerRef.current);
    var node = event.currentTarget;
    var rect = node.getBoundingClientRect();
    previewTimerRef.current = setTimeout(function () {
      previewTimerRef.current = 0;
      var width = 300;
      setHoverPreview({
        item: item,
        activity: activity,
        left: Math.max(8, Math.min(rect.left + rect.width / 2 - width / 2, window.innerWidth - width - 8)),
        top: Math.min(window.innerHeight - 12, rect.bottom + 8),
        portalTheme: readTopbarPortalTheme(),
      });
    }, immediate ? 80 : 420);
  }

  function openOverflowMenu(event) {
    event.preventDefault();
    event.stopPropagation();
    closeSessionPreview();
    var rect = event.currentTarget.getBoundingClientRect();
    var width = 300;
    var height = Math.min(500, window.innerHeight - 16);
    setSessionMenu(null);
    setResourceMenu(null);
    setOverflowMenu({
      left: Math.max(8, Math.min(rect.left, window.innerWidth - width - 8)),
      top: Math.max(8, Math.min(rect.bottom + 8, window.innerHeight - height - 8)),
      portalTheme: readTopbarPortalTheme(),
    });
  }

  function closeOverflowMenu() { setOverflowMenu(null); }

  function renderOverflowSession(item) {
    var status = wbSessionStatusLabel(item.activity, t);
    var detail = wbSessionActivityCopy(item.activity, t);
    return (
      <button key={item.kind + ":" + item.id} type="button" role="menuitem" onClick={function () {
        closeOverflowMenu();
        if (onOpenSession) onOpenSession(item);
      }}>
        <span className={"workbench-session-overflow-icon " + String(item.activity.phase || "idle")}><WorkbenchSessionStatusIcon phase={item.activity.phase} active={item.activity.isLive} /></span>
        <span><b>{item.title}</b><small>{detail && detail !== status ? status + " · " + detail : status}</small></span>
      </button>
    );
  }

  function activeSessionIndex() {
    return tabs.findIndex(function (item) {
      return item.kind === "chat"
        ? activePage === "chat" && String(activeChatId || "") === String(item.id || "")
        : !activePage && taskView === "detail" && String(activeTaskId || "") === String(item.id || "");
    });
  }

  function openSessionAt(index) {
    if (!tabs.length) return;
    var normalized = ((Number(index) || 0) % tabs.length + tabs.length) % tabs.length;
    if (tabs[normalized] && onOpenSession) onOpenSession(tabs[normalized]);
  }

  function copyBrowserToConversation(targetChatId, resource) {
    return wbCopyBrowserToChat(targetChatId, resource).then(function (copied) {
      workbenchServices.feedback().showToast(
        copied
          ? t("workbench.resourceShelf.browserCopiedToChat", "Webpage copied to conversation browser")
          : t("workbench.resourceShelf.browserCopyFailed", "Could not copy webpage to conversation"),
        copied ? "success" : "error"
      );
      return copied;
    });
  }

  function handleTopbarItemKeyDown(event, onRemove) {
    var key = String(event.key || "");
    if (key === "Delete" || key === "Backspace") {
      if (onRemove) {
        event.preventDefault();
        onRemove();
      }
      return;
    }
    if (["ArrowLeft", "ArrowRight", "Home", "End"].indexOf(key) < 0) return;
    var root = topbarRef.current;
    var items = root ? Array.prototype.slice.call(root.querySelectorAll("[data-workbench-topbar-item]")) : [];
    if (!items.length) return;
    var current = items.indexOf(event.currentTarget);
    if (current < 0) return;
    var next = key === "Home"
      ? 0
      : key === "End"
        ? items.length - 1
        : (current + (key === "ArrowRight" ? 1 : -1) + items.length) % items.length;
    event.preventDefault();
    items[next].focus();
  }

  useWorkbenchEffect(function () {
    function handleChatSideVisibility(event) {
      var detail = event && event.detail || {};
      setChatSideHidden(!!detail.active && !!detail.hidden);
    }
    window.addEventListener("workbench:chat-side-visibility", handleChatSideVisibility);
    return function () {
      window.removeEventListener("workbench:chat-side-visibility", handleChatSideVisibility);
    };
  }, []);

  useWorkbenchEffect(function () {
    if (!browserAvailable) return undefined;
    function handleBrowserCopy(event) {
      var detail = event && event.detail || {};
      if (!detail.targetChatId || !detail.resource) return;
      copyBrowserToConversation(detail.targetChatId, detail.resource);
    }
    window.addEventListener("cyrene:copy-browser-to-chat", handleBrowserCopy);
    return function () {
      window.removeEventListener("cyrene:copy-browser-to-chat", handleBrowserCopy);
    };
  }, [browserAvailable]);

  useWorkbenchEffect(function () {
    function handleSessionShortcut(event) {
      if (!keyboardEnabled) return;
      var sc = workbenchServices.shortcuts();
      if (!sc) return;
      var direct = ["switch-session-1", "switch-session-2", "switch-session-3"];
      for (var index = 0; index < direct.length; index += 1) {
        if (sc.matches(event, direct[index])) {
          if (!tabs[index]) return;
          event.preventDefault();
          openSessionAt(index);
          return;
        }
      }
      if (sc.matches(event, "next-session") || sc.matches(event, "previous-session")) {
        event.preventDefault();
        var current = activeSessionIndex();
        var direction = sc.matches(event, "previous-session") ? -1 : 1;
        openSessionAt((current < 0 ? 0 : current) + direction);
        return;
      }
      if (sc.matches(event, "close-session-tab")) {
        var activeIndex = activeSessionIndex();
        if (activeIndex < 0 || !tabs[activeIndex] || !onRemoveSessionTab) return;
        event.preventDefault();
        onRemoveSessionTab(tabs[activeIndex]);
      }
    }
    window.addEventListener("keydown", handleSessionShortcut);
    return function () { window.removeEventListener("keydown", handleSessionShortcut); };
  }, [keyboardEnabled, tabs, activePage, taskView, activeTaskId, activeChatId, onOpenSession, onRemoveSessionTab]);

  useWorkbenchEffect(function () {
    function handlePointerShelfDrag(event) {
      setResourceDropActive(!!(event && event.detail && event.detail.active));
    }
    window.addEventListener("cyrene:resource-shelf-drag-state", handlePointerShelfDrag);
    return function () {
      window.removeEventListener("cyrene:resource-shelf-drag-state", handlePointerShelfDrag);
    };
  }, []);

  useWorkbenchEffect(function () {
    if (!sessionMenu && !resourceMenu && !overflowMenu) return undefined;
    function closeMenu() {
      sessionMenuSeqRef.current += 1;
      setSessionMenu(null);
      setResourceMenu(null);
      setOverflowMenu(null);
    }
    function handleKey(event) {
      if (event.key === "Escape") closeMenu();
      if (["ArrowDown", "ArrowUp", "Home", "End"].indexOf(event.key) < 0) return;
      var menu = document.querySelector(".workbench-session-context-menu[role='menu']");
      if (!menu) return;
      var items = Array.prototype.slice.call(menu.querySelectorAll("[role='menuitem']:not(:disabled)"));
      if (!items.length) return;
      var current = items.indexOf(document.activeElement);
      var nextIndex = event.key === "Home"
        ? 0
        : event.key === "End"
          ? items.length - 1
          : event.key === "ArrowUp"
            ? (current <= 0 ? items.length - 1 : current - 1)
            : (current < 0 || current >= items.length - 1 ? 0 : current + 1);
      event.preventDefault();
      items[nextIndex].focus();
    }
    function handleScroll(event) {
      var target = event && event.target;
      if (target && target.nodeType === 1 && target.closest && target.closest(
        ".workbench-session-overflow-menu, .workbench-session-menu"
      )) return;
      closeMenu();
    }
    window.addEventListener("resize", closeMenu);
    window.addEventListener("scroll", handleScroll, true);
    document.addEventListener("keydown", handleKey);
    return function () {
      window.removeEventListener("resize", closeMenu);
      window.removeEventListener("scroll", handleScroll, true);
      document.removeEventListener("keydown", handleKey);
    };
  }, [!!sessionMenu, !!resourceMenu, !!overflowMenu]);

  useWorkbenchEffect(function () {
    if (!sessionMenu && !resourceMenu) {
      if (!overflowMenu && !hoverPreview) return undefined;
    }
    wbSetBrowserOverlayObscured(1);
    return function () { wbSetBrowserOverlayObscured(-1); };
  }, [!!sessionMenu, !!resourceMenu, !!overflowMenu, !!hoverPreview]);

  useWorkbenchEffect(function () {
    if (!browserAvailable || !sessionMenu || !onLoadSessionBrowserPreview) return undefined;
    var item = sessionMenu.item;
    var cancelled = false;
    var inFlight = false;
    function refreshBrowserPreview() {
      if (cancelled || inFlight) return;
      inFlight = true;
      Promise.resolve(onLoadSessionBrowserPreview(item)).then(function (browser) {
        if (cancelled) return;
        setSessionMenu(function (current) {
          if (!current || current.item.id !== item.id || current.item.kind !== item.kind) return current;
          var nextBrowser = browser || null;
          var previous = current.resources && current.resources.browser;
          if (previous && nextBrowser
              && previous.previewUrl === nextBrowser.previewUrl
              && previous.title === nextBrowser.title
              && previous.url === nextBrowser.url) return current;
          if (!previous && !nextBrowser) return current;
          return Object.assign({}, current, {
            resources: Object.assign({}, current.resources, { browser: nextBrowser }),
          });
        });
      }).catch(function () {}).finally(function () {
        inFlight = false;
      });
    }
    var timer = setInterval(refreshBrowserPreview, 1200);
    return function () {
      cancelled = true;
      clearInterval(timer);
    };
  }, [browserAvailable, sessionMenu ? sessionMenu.item.kind + ":" + sessionMenu.item.id : "", !!onLoadSessionBrowserPreview]);

  useWorkbenchEffect(function () {
    return function () {
      if (previewTimerRef.current) clearTimeout(previewTimerRef.current);
    };
  }, []);

  function openSessionMenu(event, item, activity, anchored) {
    event.preventDefault();
    event.stopPropagation();
    closeSessionPreview();
    setOverflowMenu(null);
    var menuWidth = Math.min(340, Math.max(0, window.innerWidth - 16));
    var menuHeight = 440;
    var rect = event.currentTarget && event.currentTarget.getBoundingClientRect ? event.currentTarget.getBoundingClientRect() : null;
    var left = anchored && rect ? rect.left + (rect.width - menuWidth) / 2 : event.clientX;
    var top = anchored && rect ? rect.bottom + 8 : event.clientY;
    left = Math.max(8, Math.min(left, window.innerWidth - menuWidth - 8));
    top = Math.max(8, Math.min(top, window.innerHeight - menuHeight - 8));
    var portalTheme = readTopbarPortalTheme();
    var seq = sessionMenuSeqRef.current + 1;
    sessionMenuSeqRef.current = seq;
    setSessionMenu({ item: item, activity: activity || item.activity || {}, left: left, top: top, portalTheme: portalTheme, loading: true, resources: { browser: false, files: [] } });
    Promise.resolve(onLoadSessionResources ? onLoadSessionResources(item) : null)
      .then(function (resources) {
        if (sessionMenuSeqRef.current !== seq) return;
        setSessionMenu(function (current) {
          if (!current || current.item.id !== item.id || current.item.kind !== item.kind) return current;
          return Object.assign({}, current, {
            loading: false,
            resources: {
              browser: browserAvailable && resources && resources.browser ? resources.browser : null,
              files: resources && Array.isArray(resources.files) ? resources.files : [],
            },
          });
        });
      })
      .catch(function () {
        if (sessionMenuSeqRef.current !== seq) return;
        setSessionMenu(function (current) {
          return current ? Object.assign({}, current, { loading: false }) : current;
        });
      });
  }

  function closeSessionMenu() {
    sessionMenuSeqRef.current += 1;
    setSessionMenu(null);
  }

  function runSessionMenuAction(action) {
    closeSessionMenu();
    if (action) action();
  }

  function copySessionTitle(item) {
    var title = String((item && item.title) || "");
    var copy = navigator.clipboard && navigator.clipboard.writeText
      ? navigator.clipboard.writeText(title)
      : Promise.reject(new Error("Clipboard unavailable"));
    copy.then(function () {
      workbenchServices.feedback().showToast(t("workbench.sessionMenu.copied", "Title copied"), "success");
    }).catch(function () {
      workbenchServices.feedback().showToast(title, "info");
    });
  }

  function portalThemeAt(event, height) {
    var menuWidth = 224;
    var themeStyle = readTopbarPortalTheme();
    return {
      left: Math.max(8, Math.min(event.clientX, window.innerWidth - menuWidth - 8)),
      top: Math.max(8, Math.min(event.clientY, window.innerHeight - (height || 220) - 8)),
      portalTheme: themeStyle,
    };
  }

  function openResourceMenu(event, resource) {
    event.preventDefault();
    event.stopPropagation();
    closeSessionPreview();
    setSessionMenu(null);
    setOverflowMenu(null);
    setResourceMenu(Object.assign({ resource: resource }, portalThemeAt(event, 210)));
  }

  function closeResourceMenu() {
    setResourceMenu(null);
  }

  function copyResourceReference(resource) {
    var text = String(resource && (resource.path || resource.url || resource.title) || "");
    var promise = navigator.clipboard && navigator.clipboard.writeText
      ? navigator.clipboard.writeText(text)
      : Promise.reject(new Error("Clipboard unavailable"));
    promise.then(function () {
      workbenchServices.feedback().showToast(t("workbench.resourceShelf.copied", "Resource reference copied"), "success");
    }).catch(function () {
      workbenchServices.feedback().showToast(text, "info");
    });
  }

  var sessionMenuCurrentItem = sessionMenu && (tabs.concat(overflowTabs).find(function (item) {
    return item.kind === sessionMenu.item.kind && item.id === sessionMenu.item.id;
  }) || sessionMenu.item);
  var sessionMenuCurrentActivity = sessionMenuCurrentItem && sessionMenuCurrentItem.activity || (sessionMenu && sessionMenu.activity);
  var sessionMenuPortal = sessionMenu && typeof ReactDOM !== "undefined"
    ? ReactDOM.createPortal((
      <div className="workbench-session-menu-portal" style={sessionMenu.portalTheme}>
        <div className="workbench-session-menu-scrim" onPointerDown={closeSessionMenu} />
        <div
          className="workbench-account-menu workbench-session-menu workbench-session-context-menu"
          role="menu"
          aria-label={sessionMenuCurrentItem.title}
          style={{ ...sessionMenu.portalTheme, left: sessionMenu.left, top: sessionMenu.top }}
          onContextMenu={function (event) { event.preventDefault(); }}
        >
          <div className="workbench-session-activity-menu-head">
            <span className={"workbench-session-activity-menu-state " + String(sessionMenuCurrentActivity.phase || "idle")}>
              <WorkbenchSessionStatusIcon phase={sessionMenuCurrentActivity.phase} active={sessionMenuCurrentActivity.isLive} />
            </span>
            <div>
              <b>{t("workbench.sessionActivity.title", "Agent activity")}</b>
              <small>{wbSessionStatusLabel(sessionMenuCurrentActivity, t)}</small>
            </div>
          </div>
          <div className="workbench-session-activity-menu-list">
            <div className="workbench-session-activity-menu-row">
              <span className={"workbench-session-activity-agent-mark main " + (sessionMenuCurrentActivity.isLive ? "running" : "idle")} aria-hidden="true" />
              <span><b>{t("workbench.sessionActivity.mainAgent", "Main Agent")}</b><small>{sessionMenuCurrentActivity.isLive ? (wbSessionActivityCopy(sessionMenuCurrentActivity, t) || t("workbench.sessionStatus.running", "Running")) : wbSessionStatusLabel(sessionMenuCurrentActivity, t)}</small></span>
            </div>
            {(sessionMenuCurrentActivity.agents || []).slice(0, 5).map(function (agent) {
              return (
                <div className="workbench-session-activity-menu-row" key={agent.id}>
                  <span className={"workbench-session-activity-agent-mark " + String(agent.status || "idle")} aria-hidden="true" />
                  <span><b>{agent.name || agent.id}</b><small>{agent.task || String(agent.status || "")}</small></span>
                </div>
              );
            })}
          </div>
          {sessionMenuCurrentActivity.progress && sessionMenuCurrentActivity.progress.total ? (
            <div className="workbench-session-activity-menu-progress">
              <span>{t("workbench.sessionStatus.step", {
                current: sessionMenuCurrentActivity.progress.current || sessionMenuCurrentActivity.progress.completed,
                total: sessionMenuCurrentActivity.progress.total,
              }, "Step {current}/{total}")}</span>
              <b>{sessionMenuCurrentActivity.progress.title}</b>
            </div>
          ) : null}
          {sessionMenu.resources.browser ? (
            <button type="button" role="menuitem" className="workbench-session-browser-preview" onClick={function () {
              var item = sessionMenuCurrentItem;
              runSessionMenuAction(function () {
                if (onOpenSessionResource) onOpenSessionResource(item, { type: "browser" });
              });
            }}>
              <span className="workbench-session-browser-preview-head">
                <span className="workbench-session-menu-icon" aria-hidden="true">
                  <svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" strokeWidth="1.8"><rect x="3" y="4" width="18" height="16" rx="2"/><path d="M3 8h18M6 6h.01M9 6h.01"/></svg>
                </span>
                <span>
                  <b>{sessionMenu.resources.browser.title || t("workbench.resourceShelf.browser", "Browser")}</b>
                  <small>{sessionMenu.resources.browser.url}</small>
                </span>
                <svg className="workbench-session-resource-chevron" viewBox="0 0 16 16" width="14" height="14" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="m6 3.5 4.5 4.5L6 12.5" /></svg>
              </span>
              {sessionMenu.resources.browser.previewUrl ? (
                <img src={sessionMenu.resources.browser.previewUrl} alt="" draggable="false" />
              ) : (
                <span className="workbench-session-browser-preview-empty">
                  {t("workbench.sessionMenu.browserPreview", "Browser preview")}
                </span>
              )}
            </button>
          ) : null}
          {sessionMenu.resources.files.length ? (
            <div className="workbench-session-resource-section">
              <div className="wb-menu-head workbench-session-menu-label">{t("workbench.sessionMenu.files", "Files")}</div>
              {sessionMenu.resources.files.map(function (file, index) {
                var fileKey = String(file.id || file.url || file.name || index);
                return (
                  <button key={fileKey} type="button" role="menuitem" className="workbench-session-menu-file" onClick={function () {
                    var item = sessionMenuCurrentItem;
                    runSessionMenuAction(function () {
                      if (onOpenSessionResource) onOpenSessionResource(item, { type: "file", file: file });
                    });
                  }}>
                    <span className="workbench-session-menu-icon" aria-hidden="true">
                      <svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" strokeWidth="1.8"><path d="M6 2h8l4 4v16H6Z"/><path d="M14 2v5h5"/></svg>
                    </span>
                    <WorkbenchSessionMenuFileName name={file.name || t("workbench.sessionMenu.untitledFile", "Untitled file")} />
                  </button>
                );
              })}
            </div>
          ) : null}
          {sessionMenu.loading ? (
            <div className="workbench-session-menu-loading">{t("workbench.sessionMenu.loading", "Loading resources…")}</div>
          ) : null}
          <div className={"workbench-session-primary-actions" + (sessionMenuCurrentActivity.capabilities && (sessionMenuCurrentActivity.capabilities.canPause || sessionMenuCurrentActivity.capabilities.canStop) ? " has-runtime-control" : "")}>
            {sessionMenuCurrentActivity.capabilities && sessionMenuCurrentActivity.capabilities.canPause ? (
              <button type="button" role="menuitem" onClick={function () {
                var item = sessionMenuCurrentItem;
                runSessionMenuAction(function () { if (onPauseSession) onPauseSession(item); });
              }}>
                <span className="workbench-session-menu-icon" aria-hidden="true"><WorkbenchSessionStatusIcon phase="paused" /></span>
                <span>{t("workbench.sessionActivity.pause", "Pause")}</span>
              </button>
            ) : null}
            {sessionMenuCurrentActivity.capabilities && sessionMenuCurrentActivity.capabilities.canStop ? (
              <button type="button" role="menuitem" className="stop" onClick={function () {
                var item = sessionMenuCurrentItem;
                runSessionMenuAction(function () { if (onStopSession) onStopSession(item); });
              }}>
                <span className="workbench-session-menu-icon" aria-hidden="true">
                  <svg viewBox="0 0 16 16" width="13" height="13" fill="currentColor"><rect x="3.5" y="3.5" width="9" height="9" rx="1.5"/></svg>
                </span>
                <span>{t("workbench.sessionActivity.stop", "Stop")}</span>
              </button>
            ) : null}
            <button type="button" role="menuitem" className="open-session" onClick={function () {
              var item = sessionMenuCurrentItem;
              runSessionMenuAction(function () { if (onOpenSession) onOpenSession(item); });
            }}>
              <span className="workbench-session-menu-icon" aria-hidden="true">
                <svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"><path d="M5 12h14M13 6l6 6-6 6"/></svg>
              </span>
              <span>{t("workbench.sessionActivity.openSession", "Open Session")}</span>
            </button>
          </div>
          <div className="workbench-session-utility-actions">
            <button type="button" role="menuitem" onClick={function () {
              runSessionMenuAction(function () { if (onTogglePinnedSession) onTogglePinnedSession(sessionMenuCurrentItem); });
            }}>
              <span className="workbench-session-menu-icon" aria-hidden="true">
                <svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"><path d="M12 17v5"/><path d="M5 17h14"/><path d="M17 3a1 1 0 0 1 1 1v4.6a2 2 0 0 0 .6 1.4l1.7 1.7A1 1 0 0 1 19.6 13H4.4a1 1 0 0 1-.7-1.7l1.7-1.7A2 2 0 0 0 6 8.2V4a1 1 0 0 1 1-1Z"/></svg>
              </span>
              <span>{sessionMenuCurrentItem.pinned ? t("workbench.sessionMenu.unpin", "Unpin tab") : t("workbench.sessionMenu.pin", "Pin tab")}</span>
            </button>
            <button type="button" role="menuitem" onClick={function () {
              var item = sessionMenuCurrentItem;
              runSessionMenuAction(function () { copySessionTitle(item); });
            }}>
              <span className="workbench-session-menu-icon" aria-hidden="true"><svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" strokeWidth="1.8"><rect x="8" y="8" width="12" height="12" rx="2"/><path d="M16 8V6a2 2 0 0 0-2-2H6a2 2 0 0 0-2 2v8a2 2 0 0 0 2 2h2"/></svg></span>
              <span>{t("workbench.sessionMenu.copyTitle", "Copy title")}</span>
            </button>
            <button type="button" role="menuitem" className="danger" onClick={function () {
              var item = sessionMenuCurrentItem;
              runSessionMenuAction(function () { if (onRemoveSessionTab) onRemoveSessionTab(item); });
            }}>
              <span className="workbench-session-menu-icon" aria-hidden="true"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"><circle cx="12" cy="12" r="9"/><path d="m9 9 6 6m0-6-6 6"/></svg></span>
              <span>{t("workbench.sessionMenu.remove", "Remove")}</span>
            </button>
          </div>
        </div>
      </div>
    ), document.body)
    : null;

  var overflowMenuPortal = overflowMenu && typeof ReactDOM !== "undefined"
    ? ReactDOM.createPortal((
      <div className="workbench-session-menu-portal" style={overflowMenu.portalTheme}>
        <div className="workbench-session-menu-scrim" onPointerDown={closeOverflowMenu} />
        <div
          className={"workbench-session-overflow-menu workbench-session-context-menu" + (overflowGroups.regular.length && overflowGroups.exceptional.length ? " split-scroll" : "")}
          role="menu"
          aria-label={t("workbench.sessionOverflow.title", "More sessions")}
          style={{ ...overflowMenu.portalTheme, left: overflowMenu.left, top: overflowMenu.top }}
        >
          <div className="workbench-session-overflow-head">
            <b>{t("workbench.sessionOverflow.title", "All conversations")}</b>
            <small>{t("workbench.sessionOverflow.count", { count: overflowTabs.length }, "{count} more")}</small>
          </div>
          <div className={"workbench-session-overflow-list" + (overflowGroups.regular.length ? " has-regular" : "") + (overflowGroups.exceptional.length ? " has-exceptions" : "")}>
            {overflowGroups.regular.length ? (
              <div className="workbench-session-overflow-group" role="group" aria-label={t("workbench.sessionOverflow.other", "Other sessions")}>
                <div className="workbench-session-overflow-group-head"><span>{t("workbench.sessionOverflow.other", "Other sessions")}</span><small>{overflowGroups.regular.length}</small></div>
                <div className="workbench-session-overflow-group-items">{overflowGroups.regular.map(renderOverflowSession)}</div>
              </div>
            ) : null}
            {overflowGroups.regular.length && overflowGroups.exceptional.length ? <div className="workbench-session-overflow-divider" /> : null}
            {overflowGroups.exceptional.length ? (
              <div className="workbench-session-overflow-group exceptional" role="group" aria-label={t("workbench.sessionOverflow.exceptions", "Exceptions")}>
                <div className="workbench-session-overflow-group-head"><span>{t("workbench.sessionOverflow.exceptions", "Exceptions")}</span><small>{overflowGroups.exceptional.length}</small></div>
                <div className="workbench-session-overflow-group-items">{overflowGroups.exceptional.map(renderOverflowSession)}</div>
              </div>
            ) : null}
          </div>
        </div>
      </div>
    ), document.body)
    : null;

  var resourceMenuPortal = resourceMenu && typeof ReactDOM !== "undefined"
    ? ReactDOM.createPortal((
      <div className="workbench-session-menu-portal" style={resourceMenu.portalTheme}>
        <div className="workbench-session-menu-scrim" onPointerDown={closeResourceMenu} />
        <div
          className="workbench-account-menu workbench-session-menu workbench-session-context-menu workbench-resource-menu"
          role="menu"
          aria-label={resourceMenu.resource.title}
          style={{ ...resourceMenu.portalTheme, left: resourceMenu.left, top: resourceMenu.top }}
          onContextMenu={function (event) { event.preventDefault(); }}
        >
          <button type="button" role="menuitem" onClick={function () {
            var resource = resourceMenu.resource;
            closeResourceMenu();
            if (onOpenPinnedResource) onOpenPinnedResource(resource);
          }}>
            <span className="workbench-session-menu-icon" aria-hidden="true">
              {resourceMenu.resource.kind === "conversation"
                ? <svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"><path d="M21 15a4 4 0 0 1-4 4H8l-5 3V7a4 4 0 0 1 4-4h10a4 4 0 0 1 4 4Z"/><path d="M8 9h8M8 13h5"/></svg>
                : resourceMenu.resource.kind === "browser"
                ? <svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" strokeWidth="1.8"><rect x="3" y="4" width="18" height="16" rx="2"/><path d="M3 8h18M6 6h.01M9 6h.01"/></svg>
                : <svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" strokeWidth="1.8"><path d="M6 2h8l4 4v16H6Z"/><path d="M14 2v5h5"/></svg>}
            </span>
            <span>{resourceMenu.resource.kind === "conversation"
              ? t("workbench.resourceShelf.openConversation", "Open conversation")
              : resourceMenu.resource.kind === "browser"
              ? t("workbench.resourceShelf.openBrowser", "Open owner conversation")
              : resourceMenu.resource.kind === "snippet"
                ? t("workbench.resourceShelf.useSnippet", "Add to current conversation")
                : t("workbench.resourceShelf.openFile", "Open file")}</span>
          </button>
          {resourceMenu.resource.kind === "browser" || resourceMenu.resource.kind === "conversation" ? (
            <div className="workbench-resource-readonly-note">
              {resourceMenu.resource.kind === "conversation"
                ? t("workbench.resourceShelf.conversationReadOnly", "Other agents receive a read-only conversation summary")
                : t("workbench.resourceShelf.readOnly", "Other sessions can only view this browser")}
            </div>
          ) : null}
          <button type="button" role="menuitem" onClick={function () {
            var resource = resourceMenu.resource;
            closeResourceMenu();
            copyResourceReference(resource);
          }}>
            <span className="workbench-session-menu-icon" aria-hidden="true">
              <svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" strokeWidth="1.8"><rect x="8" y="8" width="12" height="12" rx="2"/><path d="M16 8V6a2 2 0 0 0-2-2H6a2 2 0 0 0-2 2v8a2 2 0 0 0 2 2h2"/></svg>
            </span>
            <span>{t("workbench.resourceShelf.copyReference", "Copy reference")}</span>
          </button>
          <div className="workbench-session-menu-separator" />
          <button type="button" role="menuitem" className="danger" onClick={function () {
            var resource = resourceMenu.resource;
            closeResourceMenu();
            if (onUnpinResource) onUnpinResource(resource);
          }}>
            <span className="workbench-session-menu-icon" aria-hidden="true">
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"><circle cx="12" cy="12" r="9"/><path d="m9 9 6 6m0-6-6 6"/></svg>
            </span>
            <span>{t("workbench.resourceShelf.remove", "Remove from topbar")}</span>
          </button>
        </div>
      </div>
    ), document.body)
    : null;

  var browserManagerPages = Array.isArray(browserManagerState.pages)
    ? browserManagerState.pages.slice().sort(function (left, right) {
        var leftActive = left.sessionActive && left.active ? 1 : 0;
        var rightActive = right.sessionActive && right.active ? 1 : 0;
        return rightActive - leftActive;
      })
    : [];
  var browserManagerDownloads = Array.isArray(browserManagerState.downloads)
    ? browserManagerState.downloads.slice().sort(function (left, right) {
        return Number(right && right.startedAt || 0) - Number(left && left.startedAt || 0);
      })
    : [];
  var browserManagerGroups = [];
  var browserManagerGroupByKey = {};
  browserManagerPages.forEach(function (page) {
    var owner = browserOwnerSession(page);
    var groupKey = String(owner ? (owner.projectId || page.sessionId || "other") : "other");
    if (!browserManagerGroupByKey[groupKey]) {
      var group = {
        key: groupKey,
        label: String(owner && owner.projectName || t("workbench.browserManager.otherProject", "Other pages")),
        pages: [],
      };
      browserManagerGroupByKey[groupKey] = group;
      browserManagerGroups.push(group);
    }
    browserManagerGroupByKey[groupKey].pages.push({ page: page, owner: owner });
  });
  var browserManagerPreviewPages = [];
  var browserManagerPreviewDomains = {};
  browserManagerPages.forEach(function (page) {
    if (page.closed || browserManagerPreviewPages.length >= 4) return;
    var domain = browserPageDomain(page);
    var previewKey = domain || String(page.key || page.sessionId + ":" + page.tabId);
    if (browserManagerPreviewDomains[previewKey]) return;
    browserManagerPreviewDomains[previewKey] = true;
    browserManagerPreviewPages.push(page);
  });

  var browserManagerMenuPortal = browserAvailable && browserManagerMenu && typeof ReactDOM !== "undefined"
    ? ReactDOM.createPortal((
      <div className="workbench-browser-manager-layer" style={browserManagerMenu.portalTheme || {}}>
        <div className="workbench-browser-manager-scrim" onMouseDown={function () { setBrowserManagerMenu(null); }} />
        <section
          className="workbench-browser-manager-menu"
          role="dialog"
          aria-modal="false"
          aria-label={t("workbench.browserManager.title", "Browser pages")}
          style={{ left: browserManagerMenu.left + "px", top: browserManagerMenu.top + "px", width: browserManagerMenu.width + "px" }}
          onMouseDown={function (event) { event.stopPropagation(); }}
        >
          <header className="workbench-browser-manager-head">
            <span>
              <WorkbenchAssetIcon name="devices" className="workbench-browser-manager-title-icon" />
              <strong>{t("workbench.browserManager.title", "Browser pages")}</strong>
              <b>{browserManagerState.pageCount || 0}</b>
            </span>
            {browserManagerState.downloadCount ? (
              <em><WorkbenchAssetIcon name="download" />{t("workbench.browserManager.downloadingCount", { count: browserManagerState.downloadCount }, "{count} downloading")}</em>
            ) : null}
          </header>
          <div className="workbench-browser-manager-list">
            {!browserManagerGroups.length ? (
              <div className="workbench-browser-manager-empty">
                <WorkbenchAssetIcon name="browser" />
                <strong>{t("workbench.browserManager.empty", "No browser pages are open")}</strong>
                <span>{t("workbench.browserManager.emptyHint", "Pages opened by any project will appear here.")}</span>
              </div>
            ) : browserManagerGroups.map(function (group) {
              return (
                <div className="workbench-browser-manager-group" key={group.key}>
                  <div className="workbench-browser-manager-group-head"><span>{group.label}</span><b>{group.pages.length}</b></div>
                  {group.pages.map(function (entry) {
                    var page = entry.page;
                    var owner = entry.owner;
                    var pinnedResource = pinnedBrowserResource(page);
                    var pageTitle = String(page.title || browserPageDomain(page) || page.url || t("workbench.resourceShelf.browser", "Browser"));
                    var pageDetail = [String(owner && owner.title || ""), browserPageDomain(page)].filter(Boolean).join(" · ");
                    return (
                      <div key={page.key || page.sessionId + ":" + page.tabId} className={"workbench-browser-manager-page" + (page.sessionActive && page.active ? " active" : "") + (page.closed ? " closed" : "")}>
                        <div className="workbench-browser-manager-page-main">
                          <button type="button" className="workbench-browser-manager-page-select" disabled={!!page.closed} onClick={function () { openManagedBrowserPage(page); }}>
                            <span className="workbench-browser-manager-favicon" aria-hidden="true">
                              {page.favicon ? <img src={page.favicon} alt="" draggable="false" onError={function (event) { event.currentTarget.hidden = true; }} /> : null}
                              <WorkbenchAssetIcon name="browser" />
                            </span>
                            <span className="workbench-browser-manager-page-copy">
                              <b title={pageTitle}>{pageTitle}</b>
                              <small title={pageDetail || page.url}>{pageDetail || page.url}</small>
                            </span>
                          </button>
                          {!page.closed ? (
                            <span className="workbench-browser-manager-page-actions">
                              <button type="button" className={pinnedResource ? "active" : ""} onClick={function (event) { toggleManagedBrowserPin(page, event); }} title={pinnedResource ? t("workbench.browserManager.unpin", "Remove from topbar") : t("workbench.browserManager.pin", "Pin to topbar")} aria-label={pinnedResource ? t("workbench.browserManager.unpin", "Remove from topbar") : t("workbench.browserManager.pin", "Pin to topbar")}><WorkbenchAssetIcon name={pinnedResource ? "pinned-off" : "pin"} /></button>
                              <button type="button" onClick={function (event) { reloadManagedBrowserPage(page, event); }} title={t("browser.context.reload", "Reload")} aria-label={t("browser.context.reload", "Reload")}><WorkbenchAssetIcon name="reload" /></button>
                              <button type="button" className={page.muted ? "active" : ""} onClick={function (event) { muteManagedBrowserPage(page, event); }} title={page.muted ? t("browser.context.unmute", "Unmute") : t("browser.context.mute", "Mute")} aria-label={page.muted ? t("browser.context.unmute", "Unmute") : t("browser.context.mute", "Mute")}><WorkbenchAssetIcon name={page.muted ? "volume-off" : "volume"} /></button>
                              <button type="button" onClick={function (event) { closeManagedBrowserPage(page, event); }} title={t("browser.context.closeTab", "Close tab")} aria-label={t("browser.context.closeTab", "Close tab")}><WorkbenchAssetIcon name="x" /></button>
                            </span>
                          ) : null}
                        </div>
                      </div>
                    );
                  })}
                </div>
              );
            })}
          </div>
          {browserManagerDownloads.length ? (
            <section className="workbench-browser-manager-download-center" aria-label={t("workbench.browserManager.downloads", "Downloads")}>
              <header className="workbench-browser-manager-download-head">
                <span><WorkbenchAssetIcon name="download" /><strong>{t("workbench.browserManager.downloads", "Downloads")}</strong></span>
                <b>{browserManagerDownloads.length}</b>
              </header>
              <div className="workbench-browser-manager-download-list">
                {browserManagerDownloads.map(function (download) {
                  var total = Math.max(0, Number(download.totalBytes) || 0);
                  var received = Math.max(0, Number(download.receivedBytes) || 0);
                  var percent = total > 0 ? Math.max(0, Math.min(100, Math.round(received / total * 100))) : 0;
                  var byteProgress = total > 0
                    ? percent + "% · " + browserDownloadSize(received) + " / " + browserDownloadSize(total)
                    : browserDownloadSize(received);
                  var progressText = download.paused
                    ? t("workbench.browserManager.paused", "Paused") + " · " + byteProgress
                    : byteProgress;
                  var sourceTitle = String(download.pageTitle || browserPageDomain({ url: download.pageUrl }) || "");
                  return (
                    <article className={"workbench-browser-manager-download" + (download.paused ? " paused" : "")} key={download.id}>
                      <span className="workbench-browser-manager-download-icon" aria-hidden="true"><WorkbenchAssetIcon name="download" /></span>
                      <span className="workbench-browser-manager-download-copy">
                        <b title={download.filename}>{download.filename || t("workbench.browserManager.download", "Download")}</b>
                        <small title={[progressText, sourceTitle].filter(Boolean).join(" · ")}>{[progressText, sourceTitle].filter(Boolean).join(" · ")}</small>
                      </span>
                      <span className="workbench-browser-manager-download-actions">
                        <button type="button" onClick={function (event) { controlManagedBrowserDownload(download, download.paused ? "resume" : "pause", event); }} title={download.paused ? t("workbench.browserManager.resume", "Resume download") : t("workbench.browserManager.pause", "Pause download")} aria-label={download.paused ? t("workbench.browserManager.resume", "Resume download") : t("workbench.browserManager.pause", "Pause download")}><WorkbenchAssetIcon name={download.paused ? "player-play" : "player-pause"} /></button>
                        <button type="button" onClick={function (event) { controlManagedBrowserDownload(download, "cancel", event); }} title={t("workbench.browserManager.cancel", "Cancel download")} aria-label={t("workbench.browserManager.cancel", "Cancel download")}><WorkbenchAssetIcon name="x" /></button>
                      </span>
                      <span className={"workbench-browser-manager-progress" + (!total ? " indeterminate" : "")} role="progressbar" aria-label={download.filename || t("workbench.browserManager.download", "Download")} aria-valuemin={0} aria-valuemax={100} aria-valuenow={total ? percent : undefined}><i style={total ? { width: percent + "%" } : undefined} /></span>
                    </article>
                  );
                })}
              </div>
            </section>
          ) : null}
        </section>
      </div>
    ), document.body)
    : null;

  var hoverPreviewPortal = hoverPreview && typeof ReactDOM !== "undefined"
    ? ReactDOM.createPortal((
      <WorkbenchSessionActivityPreview preview={hoverPreview} t={t} />
    ), document.body)
    : null;

  var overflowActivity = overflowTabs.reduce(function (highest, item) {
    if (!highest || wbSessionActivityRank(item.activity) < wbSessionActivityRank(highest)) return item.activity;
    return highest;
  }, null) || { phase: "idle" };

  return (
    <div ref={topbarRef} className="workbench-topbar">
      <div className="workbench-brand" ref={projectMenuRef}>
        <div className="workbench-traffic-space"></div>
        <button
          data-cyrene-node-id="project_switcher"
          type="button"
          className={"workbench-brand-btn workbench-project-switcher-btn" + (projectMenuOpen ? " active" : "")}
          onClick={function () { setProjectActionId(""); setProjectMenuOpen(function (open) { return !open; }); }}
          title={t("rail.projects")}
          aria-label={t("rail.projects")}
          aria-haspopup="menu"
          aria-expanded={projectMenuOpen}
        >
          <span
            className={"workbench-top-project-icon" + (activeProject && (activeProject.dataKey === "default" || activeProject.name === "Cyrene") ? " logo" : "")}
            style={activeProject && activeProject.dataKey !== "default" && activeProject.name !== "Cyrene" ? { background: activeProject.color || WorkbenchModel.projectGradient(activeProject.id || activeProject.name) } : undefined}
            aria-hidden="true"
          >
            {activeProject && (activeProject.dataKey === "default" || activeProject.name === "Cyrene")
              ? <span className="brand-mark" />
              : WorkbenchModel.initials(activeProject && activeProject.name)}
          </span>
          <strong>{activeProject ? activeProject.name : t("workbench.selectProject", "Select project")}</strong>
          <span className="workbench-project-switcher-chevron" aria-hidden="true">
            <svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="m6 9 6 6 6-6"/></svg>
          </span>
        </button>
        {projectMenuOpen && (
          <div className="workbench-top-project-menu" role="menu" aria-label={t("rail.projects")}>
            <div className="workbench-top-project-menu-head">
              <strong>{t("rail.projects")}</strong>
              <button type="button" onClick={function () { setProjectMenuOpen(false); if (onNewProject) onNewProject(); }}>
                <svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round"><path d="M12 5v14M5 12h14"/></svg>
                <span>{t("rail.newProject")}</span>
              </button>
            </div>
            <div className="workbench-top-project-menu-list">
              {(Array.isArray(projects) ? projects : []).map(function (project) {
                var selected = activeProject && String(activeProject.id || "") === String(project.id || "");
                var isCyrene = project.dataKey === "default" || project.name === "Cyrene";
                var actionsOpen = projectActionId === project.id;
                return (
                  <div key={project.id} className={"workbench-top-project-row" + (selected ? " active" : "") + (actionsOpen ? " menu-open" : "")}>
                    <button type="button" data-cyrene-node-id={"project_" + String(project.id || "").replace(/[^a-zA-Z0-9_-]/g, "").slice(0, 100)} className="workbench-top-project-select" role="menuitemradio" aria-checked={selected} onClick={function () {
                      setProjectMenuOpen(false);
                      setProjectActionId("");
                      if (onSelectProject) onSelectProject(project.id);
                    }}>
                      <span
                        className={"workbench-top-project-icon" + (isCyrene ? " logo" : "")}
                        style={isCyrene ? undefined : { background: project.color || WorkbenchModel.projectGradient(project.id || project.name) }}
                        aria-hidden="true"
                      >{isCyrene ? <span className="brand-mark" /> : WorkbenchModel.initials(project.name)}</span>
                      <span className="workbench-top-project-copy"><b>{project.name}</b><small>{WorkbenchModel.pathLabel(project.workspacePath, project.name)}</small></span>
                    </button>
                    <button type="button" className="workbench-top-project-more" aria-label={t("rail.projectActions")} onClick={function (event) {
                      event.stopPropagation();
                      setProjectActionId(actionsOpen ? "" : project.id);
                    }}>
                      <svg viewBox="0 0 24 24" width="15" height="15" fill="currentColor"><circle cx="5" cy="12" r="1.7"/><circle cx="12" cy="12" r="1.7"/><circle cx="19" cy="12" r="1.7"/></svg>
                    </button>
                    {actionsOpen && (
                      <div className="workbench-top-project-actions" role="menu">
                        <button type="button" role="menuitem" onClick={function () { setProjectActionId(""); setProjectMenuOpen(false); if (onEditProject) onEditProject(project); }}>
                          <svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" strokeWidth="1.9" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="M12 20h9"/><path d="M16.5 3.5a2.1 2.1 0 0 1 3 3L7 19l-4 1 1-4Z"/></svg>
                          <span>{t("rail.editProject")}</span>
                        </button>
                        {memoryAvailable ? <button type="button" role="menuitem" onClick={function () { setProjectActionId(""); setProjectMenuOpen(false); if (onEditMemory) onEditMemory(project); }}>
                          <svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="M12 3 13.7 9.3 20 11l-6.3 1.7L12 19l-1.7-6.3L4 11l6.3-1.7Z"/><path d="M18.5 16.5 19 19l2.5.5L19 20l-.5 2.5L18 20l-2.5-.5L18 19Z"/></svg>
                          <span>{t("rail.editMemory")}</span>
                        </button> : null}
                        {!isCyrene ? <button type="button" role="menuitem" className="danger" onClick={function () { setProjectActionId(""); setProjectMenuOpen(false); if (onDeleteProject) onDeleteProject(project); }}>
                          <svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="M3 6h18M8 6V4a1 1 0 0 1 1-1h6a1 1 0 0 1 1 1v2M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6"/></svg>
                          <span>{t("rail.deleteProject")}</span>
                        </button> : null}
                      </div>
                    )}
                  </div>
                );
              })}
            </div>
          </div>
        )}
      </div>
      <nav className="workbench-session-tabs" aria-label={t("workbench.recentSessions", "Recent sessions")}>
        {tabs.map(function (item) {
          var isActive = item.kind === "chat"
            ? activePage === "chat" && String(activeChatId || "") === item.id
            : !activePage && taskView === "detail" && String(activeTaskId || "") === item.id;
          var kindLabel = item.kind === "chat"
            ? t("workbench.page.chat", "Conversation")
            : t("workbench.page.task", "Task");
          var activity = item.activity || { phase: "idle" };
          var statusLabel = wbSessionStatusLabel(activity, t);
          var activityCopy = wbSessionActivityCopy(activity, t);
          var progress = activity.progress || {};
          var showActivityCopy = !!activityCopy && (
            activity.phase !== "completed" || Number(activity.morphUntil || 0) > activityClock
          );
          var visibleStatusText = "";
          if (isActive && showActivityCopy) {
            visibleStatusText = activity.activity && activity.activity.kind === "browser"
              ? activityCopy + " · " + t("workbench.sessionStatus.browsing", "Browsing")
              : activityCopy;
          } else if (!isActive && progress.total && (activity.phase === "running" || activity.phase === "planning")) {
            visibleStatusText = t("workbench.sessionStatus.step", {
              current: progress.current || progress.completed,
              total: progress.total,
            }, "Step {current}/{total}");
          }
          return (
            <div
              key={item.kind + ":" + item.id}
              className={"workbench-session-tab-group phase-" + String(activity.phase || "idle") + (isActive ? " active" : "")}
            >
              <button
                type="button"
                className={"workbench-session-tab" + (isActive ? " active" : "")}
                data-workbench-topbar-item="session"
                data-session-kind={item.kind}
                data-session-id={item.id}
                data-cyrene-context-menu="true"
                aria-current={isActive ? "page" : undefined}
                aria-describedby={hoverPreview && hoverPreview.item.id === item.id && hoverPreview.item.kind === item.kind
                  ? "workbench-session-activity-preview"
                  : undefined}
                aria-label={kindLabel + ": " + item.title + " · " + statusLabel}
                title={[item.projectName, kindLabel, item.title, statusLabel].filter(Boolean).join(" · ")}
                onClick={function () { if (onOpenSession) onOpenSession(item); }}
                onPointerEnter={function (event) { scheduleSessionPreview(event, item, activity, false); }}
                onPointerLeave={closeSessionPreview}
                onFocus={function (event) { scheduleSessionPreview(event, item, activity, true); }}
                onBlur={closeSessionPreview}
                onKeyDown={function (event) {
                  handleTopbarItemKeyDown(event, function () {
                    if (onRemoveSessionTab) onRemoveSessionTab(item);
                  });
                }}
                onContextMenu={function (event) { openSessionMenu(event, item, activity, false); }}
                onDragOver={item.kind === "chat" ? function (event) {
                  var resourceApi = workbenchServices.resources();
                  if (acceptsResourceDrag(event, resourceApi)) {
                    event.preventDefault();
                    event.dataTransfer.dropEffect = "copy";
                    event.currentTarget.classList.add("resource-drop-target");
                  }
                } : undefined}
                onDragLeave={item.kind === "chat" ? function (event) {
                  event.currentTarget.classList.remove("resource-drop-target");
                } : undefined}
                onDrop={item.kind === "chat" ? function (event) {
                  event.preventDefault();
                  event.currentTarget.classList.remove("resource-drop-target");
                  var resourceApi = workbenchServices.resources();
                  var resource = resourceApi && resourceApi.readDrag(event);
                  if (!resource) return;
                  if (resource.kind === "browser") {
                    copyBrowserToConversation(item.id, resource);
                    return;
                  }
                  if (wbDeliverResourceToChat(item.id, resource)) {
                    workbenchServices.feedback().showToast(
                      t("workbench.resourceShelf.addedToChat", "Added to conversation input"),
                      "success"
                    );
                  }
                } : undefined}
              >
                <span className={"workbench-session-tab-status " + String(activity.phase || "idle")} aria-hidden="true">
                  <WorkbenchSessionStatusIcon phase={activity.phase} active={activity.isLive} />
                </span>
                <span className="workbench-session-tab-copy">
                  {visibleStatusText ? (
                    <WbcHoverMarquee text={visibleStatusText} className="workbench-session-tab-title workbench-session-tab-status-copy" auto={true} />
                  ) : (
                    <WbcHoverMarquee text={item.title} className="workbench-session-tab-title" />
                  )}
                </span>
                {item.pinned ? (
                  <span className="workbench-session-tab-pin" aria-label={t("workbench.sessionMenu.pinned", "Pinned")}>
                    <svg viewBox="0 0 24 24" width="10" height="10" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                      <path d="M12 17v5"/>
                      <path d="M5 17h14"/>
                      <path d="M17 3a1 1 0 0 1 1 1v4.6a2 2 0 0 0 .6 1.4l1.7 1.7A1 1 0 0 1 19.6 13H4.4a1 1 0 0 1-.7-1.7l1.7-1.7A2 2 0 0 0 6 8.2V4a1 1 0 0 1 1-1Z"/>
                    </svg>
                  </span>
                ) : null}
              </button>
              <button
                type="button"
                className="workbench-session-tab-more"
                aria-label={t("workbench.sessionActivity.moreActions", { title: item.title }, "More actions for {title}")}
                title={t("workbench.sessionActivity.title", "Agent activity")}
                onClick={function (event) { openSessionMenu(event, item, activity, true); }}
              >
                <svg viewBox="0 0 16 16" width="14" height="14" fill="currentColor" aria-hidden="true"><circle cx="3" cy="8" r="1.25"/><circle cx="8" cy="8" r="1.25"/><circle cx="13" cy="8" r="1.25"/></svg>
              </button>
            </div>
          );
        })}
        {overflowTabs.length ? (
          <button
            data-cyrene-node-id="open_search"
            type="button"
            className={"workbench-session-overflow-button " + String(overflowActivity.phase || "idle")}
            data-workbench-topbar-item="overflow"
            aria-label={t("workbench.sessionOverflow.buttonLabel", { count: overflowTabs.length }, "Show {count} more sessions") + " · " + wbSessionStatusLabel(overflowActivity, t)}
            title={t("workbench.sessionOverflow.title", "All conversations")}
            onClick={openOverflowMenu}
            onKeyDown={handleTopbarItemKeyDown}
          >
            <span className="workbench-session-overflow-stack" aria-hidden="true">
              <svg viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinejoin="round">
                <rect x="5.5" y="4" width="10.5" height="9" rx="2" />
                <path d="M13.5 16H5a2 2 0 0 1-2-2V7.5" />
              </svg>
              <span className={"workbench-session-overflow-indicator " + String(overflowActivity.phase || "idle")} />
            </span>
            <span className="workbench-session-overflow-count">{overflowTabs.length}</span>
            <svg className="workbench-session-overflow-chevron" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="m5 6.5 3 3 3-3" /></svg>
          </button>
        ) : null}
      </nav>
      <div
        data-tour="topbar_resources"
        className={"workbench-resource-shelf" + (resourceDropActive ? " drop-active" : "")}
        aria-label={t("workbench.resourceShelf.title", "Pinned resources")}
        onDragEnter={function (event) {
          var resourceApi = workbenchServices.resources();
          if (acceptsResourceDrag(event, resourceApi, true)) {
            event.preventDefault();
            setResourceDropActive(true);
          }
        }}
        onDragOver={function (event) {
          var resourceApi = workbenchServices.resources();
          if (acceptsResourceDrag(event, resourceApi, true)) {
            event.preventDefault();
            event.dataTransfer.dropEffect = "copy";
            setResourceDropActive(true);
          }
        }}
        onDragLeave={function (event) {
          if (!event.currentTarget.contains(event.relatedTarget)) setResourceDropActive(false);
        }}
        onDrop={function (event) {
          event.preventDefault();
          setResourceDropActive(false);
          var resourceApi = workbenchServices.resources();
          var resource = resourceApi && resourceApi.readDrag(event);
          if (resource && onPinResource) onPinResource(resource);
        }}
      >
        {resources.map(function (resource) {
          var label = resource.kind === "file"
            ? (resource.name || resource.title || "file")
            : resource.kind === "conversation"
              ? (resource.title || t("workbench.page.chat", "Conversation"))
            : resource.kind === "snippet"
              ? (resource.title || String(resource.text || "").slice(0, 48) || t("workbench.resourceShelf.snippet", "Text"))
              : (resource.title || resource.url || t("workbench.resourceShelf.browser", "Browser"));
          return (
            <button
              key={resource.id}
              type="button"
              className={"workbench-resource-chip " + resource.kind}
              data-workbench-topbar-item="resource"
              data-cyrene-context-menu="true"
              aria-label={label}
              title={label}
              onClick={function () { if (onOpenPinnedResource) onOpenPinnedResource(resource); }}
              draggable={resource.kind === "browser" ? "true" : undefined}
              onDragStart={resource.kind === "browser" ? function (event) {
                var resourceApi = workbenchServices.resources();
                if (resourceApi && resourceApi.setDrag) resourceApi.setDrag(event, resource);
              } : undefined}
              onKeyDown={function (event) {
                handleTopbarItemKeyDown(event, function () {
                  if (onUnpinResource) onUnpinResource(resource);
                });
              }}
              onContextMenu={function (event) { openResourceMenu(event, resource); }}
            >
              <span className="workbench-resource-chip-icon" aria-hidden="true">
                {resource.kind === "conversation" ? (
                  <svg viewBox="0 0 24 24" width="17" height="17" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"><path d="M21 15a4 4 0 0 1-4 4H8l-5 3V7a4 4 0 0 1 4-4h10a4 4 0 0 1 4 4Z"/><path d="M8 9h8M8 13h5"/></svg>
                ) : resource.kind === "browser" ? (
                  <><WorkbenchAssetIcon name="browser" />{resource.favicon ? <img src={resource.favicon} alt="" draggable="false" onError={function (event) { event.currentTarget.hidden = true; }} /> : null}</>
                ) : resource.kind === "snippet" ? (
                  <svg viewBox="0 0 24 24" width="17" height="17" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round"><path d="M7 8h10M7 12h7M7 16h5"/><rect x="3" y="3" width="18" height="18" rx="3"/></svg>
                ) : (
                  <svg viewBox="0 0 24 24" width="17" height="17" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"><path d="M6 2h8l4 4v16H6Z"/><path d="M14 2v5h5"/></svg>
                )}
              </span>
              <span className="workbench-resource-chip-label">{label}</span>
            </button>
          );
        })}
        {!resources.length ? (
          <span
            className="workbench-resource-shelf-empty"
            role="img"
            aria-label={t("workbench.resourceShelf.dropHint", "Drag a conversation, file, selected text, browser, or knowledge item here to pin it")}
            title={t("workbench.resourceShelf.dropHint", "Drag a conversation, file, selected text, browser, or knowledge item here to pin it")}
          >
            <svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
              <path d="M12 17v5" />
              <path d="M5 17h14" />
              <path d="M17 3a1 1 0 0 1 1 1v4.6a2 2 0 0 0 .6 1.4l1.7 1.7A1 1 0 0 1 19.6 13H4.4a1 1 0 0 1-.7-1.7l1.7-1.7A2 2 0 0 0 6 8.2V4a1 1 0 0 1 1-1Z" />
            </svg>
          </span>
        ) : null}
      </div>
      {window.cyrene && window.cyrene.browser && typeof window.cyrene.browser.getManagerState === "function" && (browserManagerPages.length || browserManagerDownloads.length) ? (
        <div ref={browserManagerRef} className="workbench-browser-manager-anchor">
          <button
            type="button"
            className={"workbench-browser-manager-button" + (browserManagerMenu ? " active" : "") + (browserManagerState.downloadCount ? " downloading" : "")}
            data-workbench-topbar-item="browser-manager"
            aria-haspopup="dialog"
            aria-expanded={!!browserManagerMenu}
            aria-label={t("workbench.browserManager.buttonLabel", { count: browserManagerState.pageCount || 0 }, "Manage {count} browser pages")}
            title={t("workbench.browserManager.title", "Browser pages")}
            onClick={function (event) {
              if (browserManagerMenu) setBrowserManagerMenu(null);
              else openBrowserManagerMenu(event);
            }}
            onKeyDown={handleTopbarItemKeyDown}
          >
            <span className="workbench-browser-manager-preview" aria-hidden="true">
              {browserManagerPreviewPages.length ? browserManagerPreviewPages.map(function (page) {
                return (
                  <span className="workbench-browser-manager-preview-icon" key={page.key || page.sessionId + ":" + page.tabId}>
                    {page.favicon ? <img src={page.favicon} alt="" draggable="false" onError={function (event) { event.currentTarget.hidden = true; }} /> : null}
                    <WorkbenchAssetIcon name="browser" />
                  </span>
                );
              }) : (
                <span className="workbench-browser-manager-preview-icon empty"><WorkbenchAssetIcon name="devices" /></span>
              )}
            </span>
            {browserManagerState.downloadCount ? <span className="workbench-browser-manager-download-dot" aria-hidden="true" /> : null}
          </button>
        </div>
      ) : null}
      <div className="workbench-top-actions">
        {chatSideHidden && (
          <button
            type="button"
            className="workbench-icon-btn"
            data-chat-side-show="true"
            onClick={function () { window.dispatchEvent(new CustomEvent("workbench:show-chat-side")); }}
            title={t("workbenchChat.showSidebar", "Show side panel")}
            aria-label={t("workbenchChat.showSidebar", "Show side panel")}
          >
            <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"><rect x="3" y="3" width="18" height="18" rx="2"/><path d="M15 3v18"/><path d="m9 10-2 2 2 2"/></svg>
          </button>
        )}
        <div
          className="workbench-top-action-group"
          role="group"
          aria-label={t("help.title") + " · " + t("workbench.search")}
        >
          <button
            type="button"
            data-cyrene-node-id="open_search"
            className="workbench-icon-btn workbench-search-btn"
            onClick={onSearch}
            title={t("workbench.search")}
            aria-label={t("workbench.search")}
          >
            <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" strokeWidth="1.9" strokeLinecap="round" strokeLinejoin="round"><circle cx="11" cy="11" r="7"/><path d="m20 20-3.2-3.2"/></svg>
          </button>
          <WorkbenchHelpCenter onNewProject={onNewProject} onNewTask={onNewTask} onOpenPage={onOpenPage} onSettings={onSettings} />
        </div>
        <WorkbenchNotificationCenter notifications={notifications} onReload={onReloadNotifications} onOpenNotification={onOpenNotification} onSettings={onSettings} />
        <button type="button" className="workbench-icon-btn" onClick={onToggleTheme} title={themeTitle}>{themeIcon}</button>
        {voiceCommand.ready ? (
          <button
            type="button"
            className={"workbench-icon-btn workbench-voice-command-btn" + (voiceCommand.phase ? " " + voiceCommand.phase : "")}
            onClick={function () { WbVoiceCommand.start(); }}
            title={voiceCommand.phase === "recording"
              ? t("topbar.voiceCommandRecording", "Listening…")
              : voiceCommand.phase === "recognizing"
                ? t("topbar.voiceCommandRecognizing", "Recognizing…")
                : t("topbar.voiceCommand", "Voice command")}
            aria-label={t("topbar.voiceCommand", "Voice command")}
            aria-pressed={voiceCommand.phase === "recording"}
            disabled={!!voiceCommand.phase}
          >
            <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><rect x="9" y="3" width="6" height="11" rx="3"/><path d="M5.5 11.5a6.5 6.5 0 0 0 13 0M12 18v3M8.5 21h7"/></svg>
          </button>
        ) : null}
        <button type="button" className={"workbench-avatar-btn" + (activePage === "profile" ? " active" : "")} title={t("rail.profile")} onClick={function () { onOpenPage && onOpenPage("profile"); }}>
          {workbenchServices.profile().Avatar
            ? React.createElement(workbenchServices.profile().Avatar, { user: dataState.user, size: 30 })
            : <span className="workbench-avatar">{WorkbenchModel.initials(dataState.user && dataState.user.name)}</span>}
        </button>
      </div>
      {sessionMenuPortal}
      {overflowMenuPortal}
      {resourceMenuPortal}
      {browserManagerMenuPortal}
      {hoverPreviewPortal}
    </div>
  );
}

function WorkbenchNotificationCenter({ notifications, onReload, onOpenNotification, onSettings }) {
  var { t } = workbenchServices.i18n().use();
  var model = workbenchServices.model();
  var [open, setOpen] = useWorkbenchState(false);
  var [tab, setTab] = useWorkbenchState("all");
  var [busy, setBusy] = useWorkbenchState(false);
  var rootRef = useWorkbenchRef(null);
  var items = notifications && Array.isArray(notifications.items) ? notifications.items : [];
  var unreadCount = notifications && notifications.unreadCount ? notifications.unreadCount : 0;
  var counts = notifications && notifications.counts ? notifications.counts : { all: 0, mention: 0, comment: 0, system: 0 };

  useWorkbenchEffect(function () {
    if (!open) return undefined;
    function handlePointer(event) {
      if (rootRef.current && !rootRef.current.contains(event.target)) setOpen(false);
    }
    function handleKey(event) {
      if (event.key === "Escape") setOpen(false);
    }
    document.addEventListener("mousedown", handlePointer);
    document.addEventListener("keydown", handleKey);
    return function () {
      document.removeEventListener("mousedown", handlePointer);
      document.removeEventListener("keydown", handleKey);
    };
  }, [open]);

  // A WebContentsView is a native sibling of the renderer, so it otherwise
  // paints over this popover regardless of its CSS z-index.
  useWorkbenchEffect(function () {
    if (!open) return undefined;
    wbSetBrowserOverlayObscured(1);
    return function () { wbSetBrowserOverlayObscured(-1); };
  }, [open]);

  useWorkbenchEffect(function () {
    if (!open) return;
    onReload && onReload(tab, 80);
  }, [open, tab]);

  function markRead(ids, markAll) {
    setBusy(true);
    return model.markNotificationsRead(ids, markAll).then(function (payload) {
      if (onReload) onReload(tab, 80);
      return payload;
    }).finally(function () {
      setBusy(false);
    });
  }

  function openNotification(item) {
    if (!item) return;
    if (!item.read) markRead([item.id], false);
    if (onOpenNotification && onOpenNotification(item)) setOpen(false);
  }

  return (
    <div className={"workbench-notif-anchor" + (open ? " open" : "")} ref={rootRef}>
      <button type="button" data-tour="topbar_notifications" className={"workbench-icon-btn workbench-notif-btn" + (open ? " active" : "")} title={t("notifications.title")} onClick={function () { setOpen(!open); }}>
        <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"><path d="M18 8a6 6 0 1 0-12 0c0 7-3 9-3 9h18s-3-2-3-9"/><path d="M10.3 21a1.9 1.9 0 0 0 3.4 0"/></svg>
        {unreadCount > 0 ? <span className="workbench-notif-badge">{unreadCount > 99 ? "99+" : unreadCount}</span> : null}
      </button>
      {open ? (
        <div className="workbench-notif-popover">
          <div className="workbench-notif-popover-arrow"></div>
          <div className="workbench-notif-head">
            <b>{t("notifications.title")}</b>
            <button type="button" className="workbench-notif-markread" disabled={busy || !unreadCount} onClick={function () { markRead([], true); }}>{t("notifications.markAllRead")}</button>
          </div>
          <div className="workbench-notif-tabs">
            {[
              { id: "all", label: t("notifications.tab.all") },
              { id: "mention", label: t("notifications.tab.mention") },
              { id: "comment", label: t("notifications.tab.comment") },
              { id: "system", label: t("notifications.tab.system") },
            ].map(function (item) {
              return (
                <button key={item.id} type="button" className={"workbench-notif-tab" + (tab === item.id ? " active" : "")} onClick={function () { setTab(item.id); }}>
                  <span>{item.label}</span>
                </button>
              );
            })}
            <button type="button" className="workbench-notif-settings" onClick={onSettings} title={t("notifications.settings")}>
              <svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"><path d="M12.22 2h-.44a2 2 0 0 0-2 2v.18a2 2 0 0 1-1 1.73l-.43.25a2 2 0 0 1-2 0l-.15-.08a2 2 0 0 0-2.73.73l-.22.38a2 2 0 0 0 .73 2.73l.15.1a2 2 0 0 1 1 1.72v.51a2 2 0 0 1-1 1.74l-.15.09a2 2 0 0 0-.73 2.73l.22.38a2 2 0 0 0 2.73.73l.15-.08a2 2 0 0 1 2 0l.43.25a2 2 0 0 1 1 1.73V20a2 2 0 0 0 2 2h.44a2 2 0 0 0 2-2v-.18a2 2 0 0 1 1-1.73l.43-.25a2 2 0 0 1 2 0l.15.08a2 2 0 0 0 2.73-.73l.22-.39a2 2 0 0 0-.73-2.73l-.15-.08a2 2 0 0 1-1-1.74v-.5a2 2 0 0 1 1-1.74l.15-.09a2 2 0 0 0 .73-2.73l-.22-.38a2 2 0 0 0-2.73-.73l-.15.08a2 2 0 0 1-2 0l-.43-.25a2 2 0 0 1-1-1.73V4a2 2 0 0 0-2-2Z"/><circle cx="12" cy="12" r="3"/></svg>
            </button>
          </div>
          <div className="workbench-notif-list">
            {!items.length ? <div className="workbench-notif-empty">{t("notifications.empty")}</div> : items.map(function (item) {
              return <WorkbenchNotificationItem key={item.id} item={item} onOpen={function () { openNotification(item); }} />;
            })}
          </div>
        </div>
      ) : null}
    </div>
  );
}

function WorkbenchNotificationItem({ item, onOpen }) {
  var { t } = workbenchServices.i18n().use();
  var target = wbNotificationNavigationTarget(item);
  var isUpdate = item && item.meta && item.meta.category === "app_update";
  var isHookApproval = item && item.meta && item.meta.category === "hook_approval";
  var canNavigate = !!target || !!isUpdate || !!isHookApproval || String(item && item.source || "") === "updater";
  var tab = String(item && item.tab || "system");
  var iconClass = "system";
  var icon = null;
  if (tab === "mention") {
    iconClass = "mention";
    icon = <svg viewBox="0 0 24 24" width="19" height="19" fill="none" stroke="currentColor" strokeWidth="1.9" strokeLinecap="round" strokeLinejoin="round"><circle cx="12" cy="12" r="4.5"/><path d="M16.5 12v1a2.5 2.5 0 0 0 5 0V12a9.5 9.5 0 1 0-3 6.9"/></svg>;
  } else if (tab === "comment") {
    iconClass = "comment";
    icon = <svg viewBox="0 0 24 24" width="19" height="19" fill="currentColor"><path d="M12 2.5 13.7 9 20 10.7 13.7 12.4 12 19l-1.7-6.6L4 10.7 10.3 9Z"/></svg>;
  } else {
    var src = String(item && item.source || "");
    if (src.indexOf("knowledge") === 0) {
      iconClass = "upload";
      icon = <svg viewBox="0 0 24 24" width="19" height="19" fill="none" stroke="currentColor" strokeWidth="1.9" strokeLinecap="round" strokeLinejoin="round"><path d="M12 16V6"/><path d="m8.5 9.5 3.5-3.5 3.5 3.5"/><path d="M20 16.5a4 4 0 0 1-4 4H8a4 4 0 1 1 .9-7.9A5 5 0 0 1 18 10a4 4 0 0 1 2 6.5Z"/></svg>;
    } else if (src.indexOf("schedule") === 0 || src.indexOf("scheduled") === 0) {
      iconClass = "schedule";
      icon = <svg viewBox="0 0 24 24" width="19" height="19" fill="none" stroke="currentColor" strokeWidth="1.9" strokeLinecap="round" strokeLinejoin="round"><rect x="3.5" y="5" width="17" height="15.5" rx="2.5"/><path d="M3.5 9.5h17M8 3v4M16 3v4"/></svg>;
    } else if (src.indexOf("task") === 0) {
      iconClass = "success";
      icon = <svg viewBox="0 0 24 24" width="19" height="19" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round"><circle cx="12" cy="12" r="9"/><path d="m8 12 2.7 2.7L16 9.4"/></svg>;
    } else {
      iconClass = "system";
      icon = <svg viewBox="0 0 24 24" width="19" height="19" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"><path d="M12.22 2h-.44a2 2 0 0 0-2 2v.18a2 2 0 0 1-1 1.73l-.43.25a2 2 0 0 1-2 0l-.15-.08a2 2 0 0 0-2.73.73l-.22.38a2 2 0 0 0 .73 2.73l.15.1a2 2 0 0 1 1 1.72v.51a2 2 0 0 1-1 1.74l-.15.09a2 2 0 0 0-.73 2.73l.22.38a2 2 0 0 0 2.73.73l.15-.08a2 2 0 0 1 2 0l.43.25a2 2 0 0 1 1 1.73V20a2 2 0 0 0 2 2h.44a2 2 0 0 0 2-2v-.18a2 2 0 0 1 1-1.73l.43-.25a2 2 0 0 1 2 0l.15.08a2 2 0 0 0 2.73-.73l.22-.39a2 2 0 0 0-.73-2.73l-.15-.08a2 2 0 0 1-1-1.74v-.5a2 2 0 0 1 1-1.74l.15-.09a2 2 0 0 0 .73-2.73l-.22-.38a2 2 0 0 0-2.73-.73l-.15.08a2 2 0 0 1-2 0l-.43-.25a2 2 0 0 1-1-1.73V4a2 2 0 0 0-2-2Z"/><circle cx="12" cy="12" r="3"/></svg>;
    }
  }
  return (
    <button
      type="button"
      className={"workbench-notif-item" + (item.read ? "" : " unread") + (canNavigate ? " navigable" : "")}
      onClick={onOpen}
      aria-label={canNavigate ? t("notifications.open", { title: item.title }) : item.title}
    >
      <span className={"workbench-notif-item-icon " + iconClass}>{icon}</span>
      <span className="workbench-notif-item-main">
        <span className="workbench-notif-item-top">
          <b>{item.title}</b>
          <time>{workbenchServices.model().formatRelativeTime(item.createdAt)}</time>
        </span>
        {item.body ? <span className="workbench-notif-item-body">{item.body}</span> : null}
        <span className="workbench-notif-item-footer">
          <span className="workbench-notif-item-meta">{item.linkLabel || item.sourceLabel || item.projectName || t("notifications.title")}</span>
          {canNavigate ? (
            <span className="workbench-notif-item-jump" aria-hidden="true">
              {t("notifications.view")}
              <svg viewBox="0 0 16 16" width="14" height="14" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"><path d="m6 3 5 5-5 5"/></svg>
            </span>
          ) : null}
        </span>
      </span>
    </button>
  );
}


export { WorkbenchTopbar }
