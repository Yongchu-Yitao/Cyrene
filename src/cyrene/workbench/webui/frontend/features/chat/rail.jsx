import { workbenchServices } from "../../shared/runtime/services.jsx"
import { pluginLocalizedField } from "../../platform/plugins.jsx"
import { WBC_AGENT_CHAT_FLOW_EVENT, WBC_ICONS, WorkbenchChatModel, useWbcEffect, useWbcLayoutEffect, useWbcMemo, useWbcRef, useWbcState, wbcBuildRailCardDragPreview, wbcErrorText, wbcFileViewKind, wbcFormatTime, wbcHasChatDrag, wbcHasChatRailDrag, wbcHasPluginViewDrag, wbcHideNativeDragImage, wbcNotifyAgentChatFlow, wbcSetChatDrag, wbcSetChatGroupDrag, wbcSetPluginViewDrag, wbcSetResourceDrag, wbcT } from "../../workbench-chat.jsx"
import { wbcPermissionOptionLabel, wbcPermissionQuestionText, wbcQuestionOptionValue } from "./conversation.jsx"
import { wbcStartFileDrag } from "./file-resources.jsx"

import { moveChatOrderBlock } from "./behavior.mjs"
import { WbcRenameDialog } from "./rename-dialog.jsx"
import { WBC_CHAT_GROUPS_PREFIX, WbcConversationStatusPreview, WbcHoverMarquee, wbcBuildChatRailItems, wbcConversationTrackIsCompleted, wbcConversationTrackIsRunning, wbcConversationTrackPositions, wbcConversationTrackState, wbcConversationTrackRuntimeText, wbcCreateChatGroup, wbcFindChatGroup, wbcLoadChatGroups, wbcLoadChatOrder, wbcMoveChatOrder, wbcMoveChatOrderBlock, wbcNormalizeChatGroups, wbcNormalizeChatOrder, wbcOrderChatsByPinned, wbcProjectFileResource, wbcProjectFileVisual, wbcRemoveChatFromGroups, wbcViewportChatIds } from "./rail-model.jsx"
import { useWbcRailOrdering, wbcDefaultRailOrder } from "./rail-ordering.jsx"
import { useWbcRailDropController } from "./rail-drop-controller.jsx"

// Workbench chat rail and project navigation.
var WBC_PROJECT_TOOL_VIEW_STORAGE_PREFIX = "wbc-project-tool-view:";

function wbcNormalizeProjectToolView(value) {
  return value === "file" || value === "terminal" ? value : "";
}

function wbcProjectToolViewStorageKey(projectId) {
  var id = String(projectId || "").trim();
  return id ? WBC_PROJECT_TOOL_VIEW_STORAGE_PREFIX + id : "";
}

function wbcReadProjectToolView(projectId) {
  var key = wbcProjectToolViewStorageKey(projectId);
  if (!key) return "";
  try { return wbcNormalizeProjectToolView(localStorage.getItem(key)); }
  catch (error) { return ""; }
}

function wbcHasStoredProjectToolView(projectId) {
  var key = wbcProjectToolViewStorageKey(projectId);
  if (!key) return false;
  try { return localStorage.getItem(key) !== null; }
  catch (error) { return false; }
}

function wbcWriteProjectToolView(projectId, value) {
  var key = wbcProjectToolViewStorageKey(projectId);
  if (!key) return;
  var normalized = wbcNormalizeProjectToolView(value);
  try {
    localStorage.setItem(key, normalized || "none");
  } catch (error) {}
}

function WbcRail({ codeAvailable, projectId, projectName, chats, terminals, terminalsLoading, activeTerminalId, railMode, pinnedChatIds, activeChatId, loading, runningChatIds, runtimeEngine, onSelect, onAnswer, onCreate, onRename, onDelete, onTogglePinned, onOpenFile, onOpenTerminal, onCreateTerminal, onRenameTerminal, onDeleteTerminal, onUpdateTerminalLayout, onOpenPluginView, onOpenSplit, collapsed, onToggleCollapsed, collapseControl, moduleDock }) {
  var [query, setQuery] = useWbcState("");
  var [projectToolView, setProjectToolViewState] = useWbcState(function () {
    return wbcReadProjectToolView(projectId);
  });
  var fileToolsExpanded = codeAvailable && projectToolView === "file";
  var terminalToolsExpanded = codeAvailable && projectToolView === "terminal";
  function setProjectToolView(next) {
    setProjectToolViewState(function (current) {
      var resolved = wbcNormalizeProjectToolView(
        typeof next === "function" ? next(current) : next
      );
      wbcWriteProjectToolView(projectId, resolved);
      return resolved;
    });
  }
  function setFileToolsExpanded(open) {
    setProjectToolView(function (current) {
      return open ? "file" : (current === "file" ? "" : current);
    });
  }
  function setTerminalToolsExpanded(open) {
    setProjectToolView(function (current) {
      return open ? "terminal" : (current === "terminal" ? "" : current);
    });
  }
  useWbcEffect(function () {
    var activeTerminalBelongsToProject = (Array.isArray(terminals) ? terminals : []).some(function (terminal) {
      return String(terminal && terminal.id || "") === String(activeTerminalId)
        && String(terminal && terminal.projectId || "") === String(projectId);
    });
    if (!projectId || !activeTerminalId || !activeTerminalBelongsToProject || wbcHasStoredProjectToolView(projectId)) return;
    setProjectToolViewState("terminal");
    wbcWriteProjectToolView(projectId, "terminal");
  }, [projectId, activeTerminalId, terminals]);
  var [pluginTools, setPluginTools] = useWbcState([]);
  var [pluginDragId, setPluginDragId] = useWbcState("");
  useWbcEffect(function () {
    return workbenchServices.plugins().subscribe(function (snapshot) {
      setPluginTools(Array.isArray(snapshot && snapshot.projectTools) ? snapshot.projectTools : []);
    });
  }, []);
  var [fileLocation, setFileLocation] = useWbcState(function () {
    return { projectId: String(projectId || ""), path: "." };
  });
  var currentFileProjectId = String(projectId || "");
  var filePath = fileLocation.projectId === currentFileProjectId
    ? String(fileLocation.path || ".")
    : ".";
  function setFilePath(nextPath) {
    setFileLocation({
      projectId: currentFileProjectId,
      path: String(nextPath || "."),
    });
  }
  var [fileEntries, setFileEntries] = useWbcState([]);
  var [filesLoading, setFilesLoading] = useWbcState(false);
  var [filesError, setFilesError] = useWbcState("");
  var [hasLoadedFiles, setHasLoadedFiles] = useWbcState(false);
  var [globalFileEntries, setGlobalFileEntries] = useWbcState([]);
  var [globalFilesLoading, setGlobalFilesLoading] = useWbcState(false);
  var [globalFilesError, setGlobalFilesError] = useWbcState("");
  var [fileDirection, setFileDirection] = useWbcState("forward");
  var fileDirectionRef = useWbcRef("forward");
  var [showAllRecent, setShowAllRecent] = useWbcState(false);
  var [menuId, setMenuId] = useWbcState("");
  var [renameChat, setRenameChat] = useWbcState(null);
  var [renameGroup, setRenameGroup] = useWbcState(null);
  var [renameTerminalItem, setRenameTerminalItem] = useWbcState(null);
  var terminalDefaultOrder = (codeAvailable && Array.isArray(terminals) ? terminals : []).slice().sort(function (left, right) {
    return Number(left && left.orderIndex || 0) - Number(right && right.orderIndex || 0);
  }).map(function (terminal) { return String(terminal.id); });
  var [terminalOrder, setTerminalOrder] = useWbcState([]);
  var [terminalPinnedIds, setTerminalPinnedIds] = useWbcState([]);
  var [terminalDragId, setTerminalDragId] = useWbcState("");
  var [collapsedGroups, setCollapsedGroups] = useWbcState({});
  var [groups, setGroups] = useWbcState([]);
  var [announcement, setAnnouncement] = useWbcState("");
  var ordering = useWbcRailOrdering({
    chats: chats,
    groups: groups,
    pinnedChatIds: pinnedChatIds,
    projectId: projectId,
    query: query,
    setAnnouncement: setAnnouncement,
  });
  var chatMap = ordering.chatMap;
  var commitOrder = ordering.commitOrder;
  var defaultOrder = ordering.defaultOrder;
  var defaultOrderKey = ordering.defaultOrderKey;
  var filtered = ordering.filtered;
  var groupRailItems = ordering.groupItems;
  var moveChatByKeyboard = ordering.moveByKeyboard;
  var order = ordering.order;
  var orderedChats = ordering.orderedChats;
  var orderRef = ordering.orderRef;
  var pinnedRailItems = ordering.pinnedItems;
  var recentRailItems = ordering.recentItems;
  var setOrder = ordering.setOrder;
  var [groupBackendReady, setGroupBackendReady] = useWbcState(false);
  var [groupMetadataPending, setGroupMetadataPending] = useWbcState({});
  var dropController = useWbcRailDropController(groups);
  var dragState = dropController.dragState;
  var setDragState = dropController.setDragState;
  var updateDragState = dropController.update;
  var chatDropMode = dropController.mode;
  var [previewChatId, setPreviewChatId] = useWbcState("");
  var [newResultChatIds, setNewResultChatIds] = useWbcState({});
  var [previewAnswerState, setPreviewAnswerState] = useWbcState({ chatId: "", busy: false, result: "", error: "" });
  var [trackGeometryByChatId, setTrackGeometryByChatId] = useWbcState({});
  var [agentFlowByChatId, setAgentFlowByChatId] = useWbcState({});
  var [railMotionPhase, setRailMotionPhase] = useWbcState("");
  var [uiViewportRevision, setUiViewportRevision] = useWbcState(0);
  var normalizedQuery = query.trim().toLowerCase();
  var visibleFiles = normalizedQuery ? fileEntries.filter(function (entry) {
    return String(entry && entry.name || "").toLowerCase().indexOf(normalizedQuery) !== -1
      || String(entry && entry.path || "").toLowerCase().indexOf(normalizedQuery) !== -1;
  }) : fileEntries;
  useWbcEffect(function () {
    setQuery("");
    setProjectToolViewState(wbcReadProjectToolView(projectId));
    setFileEntries([]);
    setGlobalFileEntries([]);
    setGlobalFilesLoading(false);
    setGlobalFilesError("");
    setFilesError("");
    setHasLoadedFiles(false);
    setFileDirection("forward");
    fileDirectionRef.current = "forward";
  }, [projectId]);
  useWbcEffect(function () {
    setTerminalOrder(terminalDefaultOrder);
    setTerminalPinnedIds((Array.isArray(terminals) ? terminals : []).filter(function (terminal) {
      return Boolean(terminal && terminal.pinned);
    }).map(function (terminal) { return String(terminal.id); }));
  }, [projectId, terminalDefaultOrder.join("|")]);
  useWbcEffect(function () {
    if (!codeAvailable || !fileToolsExpanded || !projectId) return undefined;
    var cancelled = false;
    setFilesLoading(true);
    setFilesError("");
    fetch("/api/projects/" + encodeURIComponent(projectId) + "/files?path=" + encodeURIComponent(filePath), { cache: "no-store" })
      .then(function (response) { return response.ok ? response.json() : Promise.reject(new Error(String(response.status))); })
      .then(function (payload) { if (!cancelled) { setFileDirection(fileDirectionRef.current); setFileEntries(Array.isArray(payload.entries) ? payload.entries : []); setHasLoadedFiles(true); } })
      .catch(function () { if (!cancelled) { setFileEntries([]); setFilesError(wbcT("rail.filesUnavailable", "Unable to load project files.")); } })
      .finally(function () { if (!cancelled) setFilesLoading(false); });
    return function () { cancelled = true; };
  }, [codeAvailable, fileToolsExpanded, filePath, projectId]);

  useWbcEffect(function () {
    var search = query.trim();
    if (!codeAvailable || !search || !projectId) {
      setGlobalFileEntries([]);
      setGlobalFilesLoading(false);
      setGlobalFilesError("");
      return undefined;
    }
    var cancelled = false;
    var controller = typeof AbortController === "function" ? new AbortController() : null;
    setGlobalFilesLoading(true);
    setGlobalFilesError("");
    var timer = window.setTimeout(function () {
      fetch("/api/projects/" + encodeURIComponent(projectId) + "/files?query=" + encodeURIComponent(search), {
        cache: "no-store",
        signal: controller ? controller.signal : undefined,
      })
        .then(function (response) { return response.ok ? response.json() : Promise.reject(new Error(String(response.status))); })
        .then(function (payload) {
          if (cancelled) return;
          var normalizedSearch = search.toLowerCase();
          setGlobalFileEntries((Array.isArray(payload.entries) ? payload.entries : []).filter(function (entry) {
            return String(entry && entry.name || "").toLowerCase().indexOf(normalizedSearch) >= 0
              || String(entry && entry.path || "").toLowerCase().indexOf(normalizedSearch) >= 0;
          }));
        })
        .catch(function (error) {
          if (!cancelled && (!error || error.name !== "AbortError")) {
            setGlobalFileEntries([]);
            setGlobalFilesError(wbcT("rail.filesUnavailable", "Unable to load project files."));
          }
        })
        .finally(function () { if (!cancelled) setGlobalFilesLoading(false); });
    }, 160);
    return function () {
      cancelled = true;
      window.clearTimeout(timer);
      if (controller) controller.abort();
    };
  }, [codeAvailable, query, projectId]);

  useWbcEffect(function () {
    if (!query.trim()) return;
    setFileToolsExpanded(false);
    setTerminalToolsExpanded(false);
  }, [query]);

  var railMotionCollapsedRef = useWbcRef(!!collapsed);
  /* Derive the phase during the first render that sees the new collapsed prop.
     Waiting for an Effect (or scheduling state while rendering) leaves one
     paint where `is-collapsed` has gone but `is-status-expanding` has not yet
     arrived. In that paint the marker snaps to the moving list geometry. */
  var renderedRailMotionPhase = railMotionCollapsedRef.current !== !!collapsed
    ? (collapsed ? "collapsing" : "expanding")
    : railMotionPhase;
  var trackLifecycleRef = useWbcRef({ projectId: "", chats: {} });
  var railRef = useWbcRef(null);
  var projectToolsRef = useWbcRef(null);
  var projectToolPullRef = useWbcRef({
    wheelDistance: 0,
    wheelAt: 0,
    touchStartX: null,
    touchStartY: null,
    touchActive: false,
  });
  var chatListRef = useWbcRef(null);
  var chatSearchRef = useWbcRef(null);
  var newChatButtonRef = useWbcRef(null);
  var trackRef = useWbcRef(null);
  var trackMeasuredExpandedRef = useWbcRef(false);
  var railDragWasActiveRef = useWbcRef(false);
  var railDragImageCleanupRef = useWbcRef(null);
  var previewCloseTimerRef = useWbcRef(null);
  var dragOriginOrderRef = useWbcRef([]);
  var dropCommittedRef = useWbcRef(false);
  var suppressClickRef = useWbcRef("");
  var pluginSuppressClickRef = useWbcRef("");
  var suppressGroupClickRef = useWbcRef("");
  var groupMetadataRequestRef = useWbcRef({ sequence: 0, active: {} });
  var agentFlowTimersRef = useWbcRef({});
  var groupBackendLoadRef = useWbcRef(0);
  var groupBackendWriteRef = useWbcRef({ projectId: String(projectId || ""), sequence: 0, chain: Promise.resolve(), baseGroups: [] });
  var groupMetadataLang = workbenchServices.i18n().getLang();

  function cancelPreviewClose() {
    if (!previewCloseTimerRef.current) return;
    window.clearTimeout(previewCloseTimerRef.current);
    previewCloseTimerRef.current = null;
  }

  function openStatusPreview(chatId) {
    cancelPreviewClose();
    setPreviewChatId(String(chatId || ""));
  }

  function closeStatusPreviewSoon() {
    cancelPreviewClose();
    previewCloseTimerRef.current = window.setTimeout(function () {
      previewCloseTimerRef.current = null;
      setPreviewChatId("");
    }, 250);
  }

  function activeProjectToolScroller() {
    var projectTools = projectToolsRef.current;
    if (!projectTools) return null;
    if (fileToolsExpanded) return projectTools.querySelector(".wbc-project-file-list");
    if (terminalToolsExpanded) return projectTools.querySelector(".wbc-project-terminal-list");
    return null;
  }

  function resetProjectToolPull() {
    var pull = projectToolPullRef.current;
    pull.wheelDistance = 0;
    pull.wheelAt = 0;
    pull.touchStartX = null;
    pull.touchStartY = null;
    pull.touchActive = false;
  }

  function collapseProjectToolFromPull() {
    resetProjectToolPull();
    setMenuId("");
    if (fileToolsExpanded) setFileToolsExpanded(false);
    if (terminalToolsExpanded) setTerminalToolsExpanded(false);
  }

  function openHoveredProjectToolFromPull(tool) {
    resetProjectToolPull();
    setMenuId("");
    if (tool === "file") {
      setTerminalToolsExpanded(false);
      setFileToolsExpanded(true);
    } else if (tool === "terminal") {
      setFileToolsExpanded(false);
      setTerminalToolsExpanded(true);
    }
  }

  function handleProjectToolWheel(event) {
    var pull = projectToolPullRef.current;
    var delta = Number(event.deltaY || 0);
    if (!fileToolsExpanded && !terminalToolsExpanded) {
      var eventTarget = event.target && event.target.closest
        ? event.target.closest("[data-project-tool]")
        : null;
      var projectTools = projectToolsRef.current;
      var hoveredTool = eventTarget && projectTools && projectTools.contains(eventTarget)
        ? String(eventTarget.getAttribute("data-project-tool") || "")
        : "";
      if (!hoveredTool || delta <= 0) {
        pull.wheelDistance = 0;
        pull.wheelAt = 0;
        return;
      }
      var openNow = Date.now();
      if (!pull.wheelAt || openNow - pull.wheelAt > 260) pull.wheelDistance = 0;
      pull.wheelAt = openNow;
      pull.wheelDistance += Math.abs(delta);
      if (pull.wheelDistance >= 72) openHoveredProjectToolFromPull(hoveredTool);
      return;
    }
    var scroller = activeProjectToolScroller();
    if (!scroller || scroller.scrollTop > 1 || delta >= 0) {
      pull.wheelDistance = 0;
      pull.wheelAt = 0;
      return;
    }
    var now = Date.now();
    if (!pull.wheelAt || now - pull.wheelAt > 260) pull.wheelDistance = 0;
    pull.wheelAt = now;
    pull.wheelDistance += Math.abs(delta);
    if (pull.wheelDistance >= 72) collapseProjectToolFromPull();
  }

  function handleProjectToolTouchStart(event) {
    var touch = event.touches && event.touches[0];
    var scroller = activeProjectToolScroller();
    var pull = projectToolPullRef.current;
    pull.touchActive = Boolean(touch && scroller && scroller.scrollTop <= 1);
    pull.touchStartX = pull.touchActive ? Number(touch.clientX) : null;
    pull.touchStartY = pull.touchActive ? Number(touch.clientY) : null;
  }

  function handleProjectToolTouchMove(event) {
    var pull = projectToolPullRef.current;
    if (!pull.touchActive || pull.touchStartY === null) return;
    var touch = event.touches && event.touches[0];
    var scroller = activeProjectToolScroller();
    if (!touch || !scroller || scroller.scrollTop > 1) {
      resetProjectToolPull();
      return;
    }
    var distanceY = Number(touch.clientY) - pull.touchStartY;
    var distanceX = Math.abs(Number(touch.clientX) - pull.touchStartX);
    if (distanceY >= 56 && distanceY > distanceX) collapseProjectToolFromPull();
  }

  function answerFromStatusPreview(chat, questionId, answerText, resumeMode) {
    if (!chat || !chat.id || !onAnswer || previewAnswerState.busy) return;
    var chatId = String(chat.id);
    setPreviewAnswerState({ chatId: chatId, busy: true, result: "", error: "" });
    Promise.resolve(onAnswer(chatId, questionId, answerText, resumeMode)).then(function () {
      setPreviewAnswerState({ chatId: chatId, busy: false, result: "sent", error: "" });
    }).catch(function (err) {
      setPreviewAnswerState({ chatId: chatId, busy: false, result: "", error: wbcErrorText(err) });
    });
  }

  useWbcEffect(function () {
    return function () { cancelPreviewClose(); };
  }, []);

  useWbcEffect(function () {
    function onAgentChatFlow(event) {
      var detail = event && event.detail && typeof event.detail === "object" ? event.detail : {};
      var chatId = String(detail.chatId || "").trim();
      var kind = String(detail.kind || "").trim();
      if (!chatId || ["created", "typing"].indexOf(kind) < 0) return;
      var expiresAt = Number(detail.expiresAt || 0);
      var remaining = Math.max(0, expiresAt - Date.now());
      if (!remaining) return;
      var timers = agentFlowTimersRef.current;
      if (timers[chatId]) window.clearTimeout(timers[chatId]);
      setAgentFlowByChatId(function (current) { return { ...current, [chatId]: kind }; });
      timers[chatId] = window.setTimeout(function () {
        delete timers[chatId];
        setAgentFlowByChatId(function (current) {
          if (!current[chatId]) return current;
          var next = { ...current };
          delete next[chatId];
          return next;
        });
      }, remaining);
    }
    window.addEventListener(WBC_AGENT_CHAT_FLOW_EVENT, onAgentChatFlow);
    return function () {
      window.removeEventListener(WBC_AGENT_CHAT_FLOW_EVENT, onAgentChatFlow);
      Object.keys(agentFlowTimersRef.current).forEach(function (chatId) {
        window.clearTimeout(agentFlowTimersRef.current[chatId]);
      });
      agentFlowTimersRef.current = {};
    };
  }, []);

  useWbcEffect(function () {
    Object.keys(agentFlowTimersRef.current).forEach(function (chatId) {
      window.clearTimeout(agentFlowTimersRef.current[chatId]);
    });
    agentFlowTimersRef.current = {};
    setAgentFlowByChatId({});
  }, [projectId]);

  useWbcEffect(function () {
    return function () { clearRailDragImage(); };
  }, []);

  useWbcLayoutEffect(function () {
    if (railMotionCollapsedRef.current === !!collapsed) return;
    railMotionCollapsedRef.current = !!collapsed;
    setRailMotionPhase(collapsed ? "collapsing" : "expanding");
  }, [collapsed]);

  useWbcEffect(function () {
    if (!railMotionPhase) return undefined;
    var phase = railMotionPhase;
    var timer = window.setTimeout(function () {
      setRailMotionPhase(function (current) { return current === phase ? "" : current; });
    /* Keep the expanding phase alive through the post-settle hold and the
       dot-to-glyph crossfade (440ms motion + 70ms hold + 90ms morph). */
    }, 630);
    return function () { window.clearTimeout(timer); };
  }, [railMotionPhase]);

  useWbcEffect(function () {
    if (!collapsed) setPreviewChatId("");
  }, [collapsed]);

  /* Blue is deliberately ephemeral: it records only an inactive conversation
     that transitioned from running to completed while this page is alive. */
  useWbcEffect(function () {
    var currentProjectId = String(projectId || "");
    var current = {};
    (Array.isArray(chats) ? chats : []).forEach(function (chat) {
      if (!chat || !chat.id) return;
      current[chat.id] = {
        running: wbcConversationTrackIsRunning(chat, runningChatIds),
        completed: wbcConversationTrackIsCompleted(chat),
      };
    });
    var lifecycle = trackLifecycleRef.current;
    if (lifecycle.projectId !== currentProjectId) {
      trackLifecycleRef.current = { projectId: currentProjectId, chats: current };
      setNewResultChatIds({});
      setPreviewChatId("");
      return;
    }
    var previous = lifecycle.chats || {};
    trackLifecycleRef.current = { projectId: currentProjectId, chats: current };
    setNewResultChatIds(function (existing) {
      var next = {};
      var changed = false;
      Object.keys(existing || {}).forEach(function (chatId) {
        if (current[chatId] && !current[chatId].running && current[chatId].completed && chatId !== String(activeChatId || "")) {
          next[chatId] = true;
        } else {
          changed = true;
        }
      });
      Object.keys(current).forEach(function (chatId) {
        if (
          previous[chatId]
          && previous[chatId].running
          && !current[chatId].running
          && current[chatId].completed
          && chatId !== String(activeChatId || "")
          && !next[chatId]
        ) {
          next[chatId] = true;
          changed = true;
        }
      });
      var existingKeys = Object.keys(existing || {});
      var nextKeys = Object.keys(next);
      if (!changed && existingKeys.length === nextKeys.length) return existing;
      return next;
    });
  }, [projectId, chats, runningChatIds, activeChatId]);

  useWbcEffect(function () {
    var activeId = String(activeChatId || "");
    if (!activeId) return;
    setNewResultChatIds(function (existing) {
      if (!existing[activeId]) return existing;
      var next = { ...existing };
      delete next[activeId];
      return next;
    });
  }, [activeChatId]);

  function revealRailMenu(actions) {
    if (!actions) return;
    requestAnimationFrame(function () {
      requestAnimationFrame(function () {
        if (!actions.isConnected) return;
        var menu = actions.querySelector(".wb-card-menu");
        if (menu) menu.scrollIntoView({ block: "nearest", inline: "nearest", behavior: "smooth" });
      });
    });
  }

  useWbcEffect(function () {
    setOrder(wbcLoadChatOrder(projectId, defaultOrder));
    var legacyGroups = wbcLoadChatGroups(projectId, defaultOrder);
    setGroups(legacyGroups);
    setGroupBackendReady(false);
    setCollapsedGroups(legacyGroups.reduce(function (state, group) {
      state[group.id] = true;
      return state;
    }, {}));
    setGroupMetadataPending({});
    groupMetadataRequestRef.current.active = {};
    setDragState(null);
    if (loading || !String(projectId || "").trim()) return;
    groupBackendLoadRef.current += 1;
    var loadToken = groupBackendLoadRef.current;
    var backendRef = groupBackendWriteRef.current;
    backendRef.projectId = String(projectId || "");
    backendRef.sequence = 0;
    backendRef.chain = Promise.resolve();
    backendRef.baseGroups = [];
    var loadPromise = WorkbenchChatModel.listChatGroups(projectId).then(function (payload) {
      if (groupBackendLoadRef.current !== loadToken) return null;
      if (payload && payload.migrationRequired) {
        return WorkbenchChatModel.migrateChatGroups({
          projectId: projectId,
          groups: legacyGroups,
        });
      }
      return payload;
    }).then(function (payload) {
      if (!payload || groupBackendLoadRef.current !== loadToken) return;
      var authoritative = storeNormalizedGroups(payload.groups || []);
      backendRef.baseGroups = authoritative;
      setGroups(authoritative);
      setCollapsedGroups(authoritative.reduce(function (state, group) {
        state[group.id] = true;
        return state;
      }, {}));
      setGroupBackendReady(true);
    }).catch(function () {
      // Keep the last-known browser cache for offline startup. The next load
      // or mutation retries against the authoritative backend.
    });
    backendRef.chain = loadPromise;
    return function () {
      if (groupBackendLoadRef.current === loadToken) groupBackendLoadRef.current += 1;
    };
  }, [projectId, defaultOrderKey, loading]);

  var groupMetadataRefreshKey = groups.map(function (group) {
    return [
      group.id,
      group.chatIds.join(","),
      group.metadataLang || "",
      group.metadataChatIds || "",
      group.summary ? "ready" : "empty",
    ].join(":");
  }).join("|");

  useWbcEffect(function () {
    if (!groupBackendReady) return;
    groups.forEach(function (group) {
      if (
        !group.summary
        || group.metadataLang !== groupMetadataLang
        || group.metadataChatIds !== group.chatIds.map(String).join("|")
      ) {
        refreshChatGroupMetadata(group);
      }
    });
  }, [projectId, groupBackendReady, groupMetadataLang, groupMetadataRefreshKey]);

  function storeNormalizedGroups(nextGroups) {
    var normalized = wbcNormalizeChatGroups(nextGroups, defaultOrder);
    try {
      localStorage.setItem(
        WBC_CHAT_GROUPS_PREFIX + String(projectId || ""),
        JSON.stringify(normalized)
      );
    } catch (e) {}
    return normalized;
  }

  function commitGroups(nextGroups, intent) {
    var normalized = storeNormalizedGroups(nextGroups);
    setGroups(normalized);
    persistGroups(normalized, intent);
    return normalized;
  }

  function persistGroups(normalized, intent) {
    var state = groupBackendWriteRef.current;
    var currentProjectId = String(projectId || "");
    if (state.projectId !== currentProjectId) {
      state.projectId = currentProjectId;
      state.sequence = 0;
      state.chain = Promise.resolve();
      state.baseGroups = [];
    }
    state.sequence += 1;
    var sequence = state.sequence;
    var desired = wbcNormalizeChatGroups(normalized, defaultOrder);
    var write = state.chain.catch(function () {}).then(function () {
      return WorkbenchChatModel.replaceChatGroups({
        projectId: currentProjectId,
        groups: desired,
        baseGroups: state.baseGroups || [],
        intent: intent || undefined,
      });
    });
    state.chain = write;
    return write.then(function (payload) {
      var live = groupBackendWriteRef.current;
      var serverGroups = wbcNormalizeChatGroups(payload.groups || [], defaultOrder);
      if (live.projectId === currentProjectId) live.baseGroups = serverGroups;
      if (
        live.projectId === currentProjectId
        && live.sequence === sequence
        && String(projectId || "") === currentProjectId
      ) {
        var authoritative = storeNormalizedGroups(serverGroups);
        setGroups(authoritative);
      }
      return payload;
    });
  }

  function refreshChatGroupMetadata(group) {
    if (!group || !Array.isArray(group.chatIds) || group.chatIds.length < 2) {
      return Promise.resolve(null);
    }
    var members = group.chatIds.map(function (chatId) {
      var chat = chatMap.get(String(chatId));
      return chat ? {
        id: String(chat.id || ""),
        title: String(chat.title || ""),
        preview: String(chat.preview || ""),
      } : null;
    }).filter(Boolean);
    if (members.length < 2) return Promise.resolve(null);
    var signature = group.chatIds.map(String).join("|");
    var requestState = groupMetadataRequestRef.current;
    requestState.sequence += 1;
    var token = requestState.sequence;
    requestState.active[group.id] = token;
    setGroupMetadataPending(function (current) { return { ...current, [group.id]: true }; });
    return groupBackendWriteRef.current.chain.catch(function () {}).then(function () {
      if (groupMetadataRequestRef.current.active[group.id] !== token) return null;
      return WorkbenchChatModel.generateChatGroupMetadata({
        projectId: projectId,
        groupId: group.id,
        signature: signature,
        members: members,
        currentTitle: group.title || "",
        titleLocked: !!group.titleLocked,
        lang: groupMetadataLang,
      });
    }).then(function (result) {
      if (!result) return null;
      var metadata = result.metadata || {};
      var persistedGroup = result.group;
      if (groupMetadataRequestRef.current.active[group.id] !== token) return null;
      setGroups(function (current) {
        var live = current.find(function (candidate) { return candidate.id === group.id; });
        if (!live || live.chatIds.map(String).join("|") !== signature) return current;
        var next = current.map(function (candidate) {
          if (candidate.id !== group.id) return candidate;
          if (
            persistedGroup
            && String(persistedGroup.id || "") === group.id
            && Array.isArray(persistedGroup.chatIds)
            && persistedGroup.chatIds.map(String).join("|") === signature
          ) {
            return {
              ...candidate,
              title: String(persistedGroup.title || candidate.title),
              summary: String(persistedGroup.summary || candidate.summary),
              titleLocked: !!persistedGroup.titleLocked,
              metadataLang: String(persistedGroup.metadataLang || metadata.lang || groupMetadataLang),
              metadataChatIds: String(persistedGroup.metadataChatIds || signature),
            };
          }
          return {
            ...candidate,
            title: candidate.titleLocked
              ? candidate.title
              : (String(metadata.title || "").trim().slice(0, 60) || candidate.title),
            summary: String(metadata.summary || "").trim().slice(0, 160) || candidate.summary,
            metadataLang: String(metadata.lang || groupMetadataLang),
            metadataChatIds: signature,
          };
        });
        var normalized = storeNormalizedGroups(next);
        if (groupBackendWriteRef.current.projectId === String(projectId || "")) {
          groupBackendWriteRef.current.baseGroups = normalized;
        }
        return normalized;
      });
      return result;
    }).catch(function () {
      return null;
    }).finally(function () {
      if (groupMetadataRequestRef.current.active[group.id] !== token) return;
      delete groupMetadataRequestRef.current.active[group.id];
      setGroupMetadataPending(function (current) {
        if (!current[group.id]) return current;
        var next = { ...current };
        delete next[group.id];
        return next;
      });
    });
  }

  function commitGroupDrop(movingId, targetId) {
    var nextOrder = wbcMoveChatOrder(dragOriginOrderRef.current, movingId, targetId, "after");
    commitOrder(nextOrder, movingId);
    var desiredGroups = wbcCreateChatGroup(
      groups,
      movingId,
      targetId,
      "group_" + Date.now().toString(36)
    );
    var created = wbcFindChatGroup(desiredGroups, targetId);
    commitGroups(desiredGroups, {
      type: "move",
      sessionId: String(movingId || ""),
      targetGroupId: created ? created.id : "",
    });
    if (created) {
      setCollapsedGroups(function (current) { return { ...current, [created.id]: false }; });
      setAnnouncement(wbcT("workbenchChat.groupCreated", "Created {title} with {count} chats.", {
        title: created.title,
        count: created.chatIds.length,
      }));
    }
  }

  function commitUngroupDrop(chatId) {
    var sourceGroup = wbcFindChatGroup(groups, chatId);
    if (!sourceGroup) return;
    commitGroups(wbcRemoveChatFromGroups(groups, chatId), {
      type: "remove_member",
      sessionId: String(chatId || ""),
    });
    setAnnouncement(wbcT("workbenchChat.removedFromGroup", "Removed {title} from {group}.", {
      title: (chatMap.get(String(chatId)) || {}).title || wbcT("workbenchChat.newChat", "New chat"),
      group: sourceGroup.title,
    }));
  }

  function renameChatGroup(groupId, title) {
    var next = groups.map(function (group) {
      return group.id === groupId ? {
        ...group,
        title: String(title || "").trim().slice(0, 60),
        titleLocked: true,
      } : group;
    });
    commitGroups(next, {
      type: "rename",
      groupId: groupId,
      title: String(title || "").trim().slice(0, 60),
    });
    return groupBackendWriteRef.current.chain;
  }

  function dissolveChatGroup(groupId) {
    var group = groups.find(function (candidate) { return candidate.id === groupId; });
    commitGroups(groups.filter(function (candidate) { return candidate.id !== groupId; }), {
      type: "dissolve",
      groupId: groupId,
    });
    setMenuId("");
    if (group) {
      setAnnouncement(wbcT("workbenchChat.groupDissolved", "Dissolved {title}.", { title: group.title }));
    }
  }

  function chatRailVisualState(chat) {
    var running = wbcConversationTrackIsRunning(chat, runningChatIds);
    var rawStatus = String(chat.runStatus || chat.status || "").trim().toLowerCase();
    var failed = !!chat.failed || !!chat.error || ["error", "failed", "failure", "timeout"].indexOf(rawStatus) >= 0;
    var attention = !!chat.awaitingUser || !!chat.pendingQuestion || [
      "awaiting_user", "waiting_for_user", "waiting_for_approval", "needs_input",
      "waiting_input", "requires_confirmation", "blocked", "review",
    ].indexOf(rawStatus) >= 0;
    var completed = ["completed", "complete", "done", "success", "succeeded"].indexOf(rawStatus) >= 0;
    return {
      running: running,
      tone: failed ? " status-failed" : attention ? " status-attention" : completed ? " status-completed" : running ? " status-running" : "",
      icon: failed ? WBC_ICONS.errorCircle : attention ? WBC_ICONS.alert : completed ? WBC_ICONS.check : running ? WBC_ICONS.running : WBC_ICONS.file,
      label: failed
        ? wbcT("status.failed", "Failed")
        : attention ? wbcT("workbenchChat.awaitingUser", "Needs input") : "",
    };
  }

  function prepareRailDragImage(root, transfer, clientX, clientY) {
    if (!root || !root.querySelectorAll || !transfer) return;
    if (railDragImageCleanupRef.current) railDragImageCleanupRef.current();
    /* Native drag-image capture timing differs across Chromium platforms.
       Hide that native image and move a real DOM clone with the pointer so the
       complete card surface and its status icon remain deterministic. */
    var builtPreview = wbcBuildRailCardDragPreview(root, "");
    if (!builtPreview) return;
    var rect = builtPreview.rect;
    var host = builtPreview.host;
    // Plugin rows keep their subtitle while resting, but their lifted preview
    // uses the exact single-line conversation-card geometry. This keeps the
    // drag shadow, radius, density and pointer handoff visually identical.
    if (root.classList.contains("wbc-project-plugin-tool")) {
      host.classList.add("wbc-plugin-tool-drag-image");
    }
    host.style.position = "fixed";
    host.style.left = rect.left + "px";
    host.style.top = rect.top + "px";
    host.style.zIndex = "2147483647";
    document.body.appendChild(host);
    var grabX = Math.max(0, Math.min(rect.width, clientX - rect.left));
    var grabY = Math.max(0, Math.min(rect.height, clientY - rect.top));
    function moveDragImage(event) {
      var x = Number(event && event.clientX);
      var y = Number(event && event.clientY);
      if (!Number.isFinite(x) || !Number.isFinite(y) || (x === 0 && y === 0)) return;
      host.style.left = (x - grabX) + "px";
      host.style.top = (y - grabY) + "px";
    }
    function finishDragImage() {
      clearRailDragImage();
    }
    document.addEventListener("drag", moveDragImage, true);
    document.addEventListener("dragover", moveDragImage, true);
    document.addEventListener("drop", finishDragImage, true);
    wbcHideNativeDragImage(transfer);
    railDragImageCleanupRef.current = function () {
      document.removeEventListener("drag", moveDragImage, true);
      document.removeEventListener("dragover", moveDragImage, true);
      document.removeEventListener("drop", finishDragImage, true);
      host.remove();
      railDragImageCleanupRef.current = null;
    };
  }

  function clearRailDragImage() {
    if (railDragImageCleanupRef.current) railDragImageCleanupRef.current();
  }

  function renderDropClone(chat) {
    if (!chat) return null;
    var visualState = chatRailVisualState(chat);
    return (
      <div className={"wbc-chat-card wbc-chat-group-drop-clone" + visualState.tone} aria-hidden="true">
        <span className="wbc-chat-card-top">
          <span className="wbc-chat-row-icon" aria-hidden="true">{visualState.icon}</span>
          <span className="wbc-chat-card-title">
            <b><WbcHoverMarquee text={chat.title || wbcT("workbenchChat.newChat", "New chat")} /></b>
          </span>
          <time className="wbc-chat-card-time">{wbcFormatTime(chat.updatedAt || chat.createdAt)}</time>
        </span>
        <span className="wbc-chat-card-preview">
          {visualState.running ? <i className="wbc-running-dot" /> : null}
          <WbcHoverMarquee text={chat.preview || wbcT("workbenchChat.noMessages", "No messages yet")} />
        </span>
      </div>
    );
  }

  function renderChatCard(chat, options) {
    options = options || {};
    var active = chat.id === activeChatId;
    /* Keep real rows and drag-preview clones on one status mapping so a chat
       cannot change icon merely because it is being grouped. */
    var visualState = chatRailVisualState(chat);
    var chatRunning = visualState.running;
    var chatStatusTone = visualState.tone;
    var chatStatusIcon = visualState.icon;
    var chatStatusLabel = visualState.label;
    var chatTrackState = wbcConversationTrackState(chat, runningChatIds, newResultChatIds);
    var chatTrackGeometry = trackGeometryByChatId[String(chat.id)] || null;
    var chatTrackMarkerReady = !!(
      !dragState
      && chatTrackState
      && chatTrackGeometry
      && Number.isFinite(Number(chatTrackGeometry.position))
      && Number.isFinite(Number(chatTrackGeometry.expandedX))
    );
    var isMenuOpen = menuId === chat.id;
    var isPinned = (Array.isArray(pinnedChatIds) ? pinnedChatIds : []).some(function (id) {
      return String(id || "") === String(chat.id || "");
    });
    var isDragging = dragState && dragState.movingId === String(chat.id);
    var isGroupTarget = dragState && dragState.mode === "group" && dragState.targetId === String(chat.id);
    var agentFlow = String(agentFlowByChatId[String(chat.id)] || "");
    var agentFlowLabel = agentFlow === "created"
      ? wbcT("workbenchChat.agentFlow.created", "Agent created this chat")
      : agentFlow === "typing"
        ? wbcT("workbenchChat.agentFlow.typing", "Agent is entering a message")
        : "";
    var chatLabel = chat.title || wbcT("workbenchChat.newChat", "New chat");
    var dragTitle = wbcT("workbenchChat.dragChat", "Drag to reorder, overlap another chat to group, or drop in the conversation area to open {title}.", {
      title: chatLabel,
    });
    return (
      <div
        key={chat.id}
        data-chat-id={String(chat.id)}
        data-cyrene-node-id={"chat_" + String(chat.id)}
        role="button"
        tabIndex={0}
        draggable="true"
        className={"wbc-chat-card"
          + (options.insideGroup ? " wbc-chat-group-child" : "")
          + (active ? " active" : "")
          + (isMenuOpen ? " menu-open" : "")
          + (isDragging ? " dragging" : "")
          + (isGroupTarget ? " group-drop-target" : "")
          + (chatTrackMarkerReady ? " track-marker-ready" : "")
          + (agentFlow ? (" agent-flow agent-flow-" + agentFlow) : "")
          + chatStatusTone}
        title={agentFlowLabel ? (dragTitle + " · " + agentFlowLabel) : dragTitle}
        aria-label={agentFlowLabel ? (chatLabel + " · " + agentFlowLabel) : chatLabel}
        data-agent-flow={agentFlow || undefined}
        data-cyrene-context-menu="true"
        onClick={function () {
          if (suppressClickRef.current === String(chat.id)) return;
          setMenuId("");
          onSelect(chat.id);
        }}
        onDoubleClick={function (event) {
          if (
            event.target
            && event.target.closest
            && event.target.closest("button, a, input, [role='menuitem'], .wbc-fork-marker")
          ) return;
          event.preventDefault();
          event.stopPropagation();
          setMenuId("");
          setRenameChat(chat);
        }}
        onContextMenu={function (event) {
          event.preventDefault();
          event.stopPropagation();
          setMenuId(chat.id);
        }}
        onKeyDown={function (e) {
          if (moveChatByKeyboard(e, chat.id)) return;
          if (e.key === "Enter" || e.key === " ") {
            e.preventDefault();
            onSelect(chat.id);
          }
        }}
        onDragStart={function (event) {
          if (event.target && event.target.closest && event.target.closest("button")) {
            event.preventDefault();
            return;
          }
          var id = String(chat.id);
          dragOriginOrderRef.current = order.slice();
          dropCommittedRef.current = false;
          suppressClickRef.current = id;
          setMenuId("");
          wbcSetChatDrag(event, chat);
          if (event.dataTransfer) {
            prepareRailDragImage(
              event.currentTarget,
              event.dataTransfer,
              event.clientX,
              event.clientY
            );
          }
          var sourceGroup = wbcFindChatGroup(groups, id);
          setDragState({
            dragKind: "chat",
            movingId: id,
            movingGroupId: "",
            movingIds: [id],
            targetId: "",
            targetGroupId: "",
            sourceGroupId: sourceGroup ? sourceGroup.id : "",
            edge: "before",
            mode: "reorder",
          });
        }}
        onDragOver={function (event) {
          if (!dragState || !wbcHasChatRailDrag(event)) return;
          if (dragState.dragKind === "group") {
            if ((dragState.movingIds || []).indexOf(String(chat.id)) >= 0) return;
            event.preventDefault();
            event.stopPropagation();
            if (event.dataTransfer) event.dataTransfer.dropEffect = "move";
            var railGroupTarget = wbcFindChatGroup(groups, chat.id);
            if (railGroupTarget && railGroupTarget.id === dragState.movingGroupId) return;
            var railTarget = railGroupTarget
              ? event.currentTarget.closest(".wbc-chat-group") || event.currentTarget
              : event.currentTarget;
            var railRect = railTarget.getBoundingClientRect();
            var railEdge = event.clientY < railRect.top + (railRect.height / 2) ? "before" : "after";
            var railTargetIds = railGroupTarget ? railGroupTarget.chatIds : [String(chat.id)];
            var groupOrder = wbcMoveChatOrderBlock(order, dragState.movingIds, railTargetIds, railEdge);
            if (groupOrder.join("|") !== order.join("|")) setOrder(groupOrder);
            updateDragState({
              movingId: dragState.movingId,
              targetId: String(chat.id),
              targetGroupId: railGroupTarget ? railGroupTarget.id : "",
              edge: railEdge,
              mode: "group-reorder",
            });
            return;
          }
          if (dragState.movingId === String(chat.id) || !wbcHasChatDrag(event)) return;
          event.preventDefault();
          event.stopPropagation();
          if (event.dataTransfer) event.dataTransfer.dropEffect = "move";
          var mode = chatDropMode(event, dragState.movingId, String(chat.id));
          var targetGroup = wbcFindChatGroup(groups, chat.id);
          if (mode === "group") {
            if (order.join("|") !== dragOriginOrderRef.current.join("|")) {
              setOrder(dragOriginOrderRef.current.slice());
            }
            updateDragState({
              movingId: dragState.movingId,
              targetId: String(chat.id),
              targetGroupId: targetGroup ? targetGroup.id : "",
              edge: "center",
              mode: "group",
            });
            return;
          }
          var rect = event.currentTarget.getBoundingClientRect();
          var edge = event.clientY < rect.top + (rect.height / 2) ? "before" : "after";
          var nextOrder = wbcMoveChatOrder(order, dragState.movingId, String(chat.id), edge);
          if (nextOrder.join("|") !== order.join("|")) setOrder(nextOrder);
          updateDragState({ movingId: dragState.movingId, targetId: String(chat.id), targetGroupId: "", edge: edge, mode: "reorder" });
        }}
        onDrop={function (event) {
          if (!dragState || !wbcHasChatRailDrag(event)) return;
          event.preventDefault();
          event.stopPropagation();
          if (dragState.dragKind === "group") {
            var droppedGroup = groups.find(function (candidate) {
              return candidate.id === dragState.movingGroupId;
            });
            dropCommittedRef.current = true;
            commitGroupOrder(order, droppedGroup);
            setDragState(null);
            return;
          }
          if (!wbcHasChatDrag(event)) return;
          var mode = chatDropMode(event, dragState.movingId, String(chat.id));
          dropCommittedRef.current = true;
          if (mode === "group") {
            commitGroupDrop(dragState.movingId, String(chat.id));
          } else {
            var nextOrder = dragState.movingId === String(chat.id)
              ? order
              : wbcMoveChatOrder(order, dragState.movingId, String(chat.id), dragState.edge);
            commitOrder(nextOrder, dragState.movingId);
            var reorderTargetGroup = wbcFindChatGroup(groups, chat.id);
            if (
              dragState.sourceGroupId
              && (!reorderTargetGroup || reorderTargetGroup.id !== dragState.sourceGroupId)
            ) commitUngroupDrop(dragState.movingId);
          }
          setDragState(null);
        }}
        onDragEnd={function () {
          clearRailDragImage();
          if (!dropCommittedRef.current) setOrder(dragOriginOrderRef.current);
          dropCommittedRef.current = false;
          setDragState(null);
          window.setTimeout(function () {
            if (suppressClickRef.current === String(chat.id)) suppressClickRef.current = "";
          }, 0);
        }}
      >
        <span className="wbc-chat-card-top">
          <span className="wbc-chat-row-icon" aria-hidden="true">{chatStatusIcon}</span>
          <span className="wbc-chat-card-title">
            <b><WbcHoverMarquee text={chat.title || wbcT("workbenchChat.newChat", "New chat")} /></b>
            {isPinned ? (
              <span className="wbc-chat-card-pin" title={wbcT("workbenchChat.pinned", "Pinned")} aria-label={wbcT("workbenchChat.pinned", "Pinned")}>
                {WBC_ICONS.pin}
              </span>
            ) : null}
            {chat.forkedFromChatId && (
              <span
                className="wbc-fork-marker"
                title={wbcT("workbenchChat.forkSource", "Forked from another chat — click to open the original")}
                onClick={function (e) { e.stopPropagation(); onSelect(chat.forkedFromChatId); }}
              >
                {WBC_ICONS.fork}
                {wbcT("workbenchChat.forked", "Forked")}
              </span>
            )}
          </span>
          <span className="wbc-chat-card-right">
            {!chatStatusLabel && (
              <time className="wbc-chat-card-time">{wbcFormatTime(chat.updatedAt || chat.createdAt)}</time>
            )}
            {chatStatusLabel && (
              <em className="wbc-chat-card-status">{chatStatusLabel}</em>
            )}
            <span className="wbc-chat-card-actions">
              <button
                type="button"
                className="wb-card-menu-btn"
                title={wbcT("common.moreActions", "More actions")}
                onClick={function (e) {
                  e.stopPropagation();
                  var actions = e.currentTarget.parentElement;
                  var opening = !isMenuOpen;
                  setMenuId(isMenuOpen ? "" : chat.id);
                  if (opening) revealRailMenu(actions);
                }}
              >
                {WBC_ICONS.dots}
              </button>
              {isMenuOpen && (
                <div className="wb-card-menu" role="menu" data-cyrene-node-id="chat_context_menu">
                  <button type="button" role="menuitem" data-cyrene-node-id="chat_menu_pin" className="wbc-chat-pin-action" onClick={function (e) {
                    e.stopPropagation();
                    setMenuId("");
                    if (onTogglePinned) onTogglePinned(chat, !isPinned);
                  }}>
                    <span className="wbc-chat-menu-icon" aria-hidden="true">{WBC_ICONS.pin}</span>
                    <span>{isPinned
                      ? wbcT("workbenchChat.unpin", "Unpin chat")
                      : wbcT("workbenchChat.pin", "Pin chat")}</span>
                  </button>
                  <button type="button" role="menuitem" data-cyrene-node-id="chat_menu_rename" className="wbc-chat-menu-action" onClick={function (e) {
                    e.stopPropagation();
                    setMenuId("");
                    setRenameChat(chat);
                  }}>
                    <span className="wbc-chat-menu-icon" aria-hidden="true">{WBC_ICONS.edit}</span>
                    <span>{wbcT("workbenchChat.rename", "Rename chat")}</span>
                  </button>
                  <button type="button" role="menuitem" data-cyrene-node-id="chat_menu_delete" className="wbc-chat-menu-action danger" onClick={function (e) {
                    e.stopPropagation();
                    setMenuId("");
                    onDelete && onDelete(chat.id);
                  }}>
                    <span className="wbc-chat-menu-icon" aria-hidden="true">{WBC_ICONS.trash}</span>
                    <span>{wbcT("workbenchChat.delete", "Delete chat")}</span>
                  </button>
                </div>
              )}
            </span>
          </span>
        </span>
        <span className="wbc-chat-card-preview">
          {chatRunning ? <i className="wbc-running-dot" /> : null}
          <WbcHoverMarquee text={chat.preview || wbcT("workbenchChat.noMessages", "No messages yet")} />
        </span>
      </div>
    );
  }

  function renderGroupFrame(group, groupChats) {
    var isCollapsed = !!collapsedGroups[group.id];
    var groupMenuId = "group:" + group.id;
    var isMenuOpen = menuId === groupMenuId;
    var isGroupDragging = !!(
      dragState
      && dragState.dragKind === "group"
      && dragState.movingGroupId === group.id
    );
    function openGroupMenu(event) {
      event.preventDefault();
      event.stopPropagation();
      var actions = event.currentTarget.querySelector(".wbc-chat-group-actions");
      setMenuId(groupMenuId);
      revealRailMenu(actions);
    }
    function toggleGroupMenu(event) {
      event.stopPropagation();
      var actions = event.currentTarget.parentElement;
      var opening = !isMenuOpen;
      setMenuId(isMenuOpen ? "" : groupMenuId);
      if (opening) revealRailMenu(actions);
    }
    var movingChat = dragState && chatMap.get(String(dragState.movingId));
    var groupDropReady = !!(
      dragState
      && dragState.dragKind !== "group"
      && dragState.mode === "group"
      && (dragState.targetGroupId === group.id || group.chatIds.indexOf(String(dragState.targetId)) >= 0)
      && group.chatIds.indexOf(String(dragState.movingId)) < 0
    );
    return (
      <section
        key={group.id}
        className={"wbc-chat-group" + (isCollapsed ? " collapsed" : " expanded") + (groupDropReady ? " drop-ready" : "") + (isMenuOpen ? " menu-open" : "") + (isGroupDragging ? " dragging" : "")}
        data-cyrene-context-menu="true"
        onContextMenu={openGroupMenu}
        onDragOver={function (event) {
          if (!dragState || !wbcHasChatRailDrag(event)) return;
          if (event.target && event.target.closest && event.target.closest(".wbc-chat-card")) return;
          if (dragState.dragKind === "group") {
            if (dragState.movingGroupId === group.id) return;
            event.preventDefault();
            event.stopPropagation();
            if (event.dataTransfer) event.dataTransfer.dropEffect = "move";
            var groupRect = event.currentTarget.getBoundingClientRect();
            var groupEdge = event.clientY < groupRect.top + (groupRect.height / 2) ? "before" : "after";
            var groupOrder = wbcMoveChatOrderBlock(order, dragState.movingIds, group.chatIds, groupEdge);
            if (groupOrder.join("|") !== order.join("|")) setOrder(groupOrder);
            updateDragState({
              movingId: dragState.movingId,
              targetId: String(group.chatIds[0] || ""),
              targetGroupId: group.id,
              edge: groupEdge,
              mode: "group-reorder",
            });
            return;
          }
          if (!wbcHasChatDrag(event) || group.chatIds.indexOf(String(dragState.movingId)) >= 0) return;
          event.preventDefault();
          event.stopPropagation();
          if (event.dataTransfer) event.dataTransfer.dropEffect = "move";
          if (order.join("|") !== dragOriginOrderRef.current.join("|")) setOrder(dragOriginOrderRef.current.slice());
          updateDragState({
            movingId: dragState.movingId,
            targetId: String(group.chatIds[0] || ""),
            targetGroupId: group.id,
            edge: "center",
            mode: "group",
          });
        }}
        onDrop={function (event) {
          if (!dragState || !wbcHasChatRailDrag(event)) return;
          if (dragState.dragKind === "group") {
            if (dragState.movingGroupId === group.id) return;
            event.preventDefault();
            event.stopPropagation();
            var reorderedGroup = groups.find(function (candidate) {
              return candidate.id === dragState.movingGroupId;
            });
            dropCommittedRef.current = true;
            commitGroupOrder(order, reorderedGroup);
            setDragState(null);
            return;
          }
          if (!wbcHasChatDrag(event) || group.chatIds.indexOf(String(dragState.movingId)) >= 0) return;
          event.preventDefault();
          event.stopPropagation();
          dropCommittedRef.current = true;
          commitGroupDrop(dragState.movingId, String(group.chatIds[group.chatIds.length - 1] || group.chatIds[0]));
          setDragState(null);
        }}
      >
        <header className="wbc-chat-group-head">
          <button
            type="button"
            className="wbc-chat-group-toggle"
            draggable="true"
            title={wbcT("workbenchChat.dragGroup", "Drag to move {title}.", { title: group.title })}
            onClick={function () {
              if (suppressGroupClickRef.current === group.id) return;
              setCollapsedGroups(function (current) { return { ...current, [group.id]: !isCollapsed }; });
            }}
            onDragStart={function (event) {
              dragOriginOrderRef.current = order.slice();
              dropCommittedRef.current = false;
              suppressGroupClickRef.current = group.id;
              setMenuId("");
              wbcSetChatGroupDrag(event, group, projectId);
              var frame = event.currentTarget.closest(".wbc-chat-group") || event.currentTarget;
              if (event.dataTransfer) {
                prepareRailDragImage(
                  frame,
                  event.dataTransfer,
                  event.clientX,
                  event.clientY
                );
              }
              setDragState({
                dragKind: "group",
                movingId: String(group.chatIds[0] || ""),
                movingGroupId: group.id,
                movingIds: group.chatIds.map(String),
                targetId: "",
                targetGroupId: "",
                sourceGroupId: group.id,
                edge: "before",
                mode: "group-reorder",
              });
            }}
            onDragEnd={function () {
              clearRailDragImage();
              if (!dropCommittedRef.current) setOrder(dragOriginOrderRef.current);
              dropCommittedRef.current = false;
              setDragState(null);
              window.setTimeout(function () {
                if (suppressGroupClickRef.current === group.id) suppressGroupClickRef.current = "";
              }, 0);
            }}
            aria-expanded={!isCollapsed}
          >
            <span className={"wbc-chat-group-leading-chevron" + (!isCollapsed ? " expanded" : "")} aria-hidden="true">{WBC_ICONS.chevronRight}</span>
            <span className="wbc-chat-group-count">{group.chatIds.length + (groupDropReady ? 1 : 0)}</span>
            <b><WbcHoverMarquee text={group.title} /></b>
          </button>
          <span className="wbc-chat-group-actions">
            <button
              type="button"
              className="wb-card-menu-btn"
              title={wbcT("common.moreActions", "More actions")}
              onClick={toggleGroupMenu}
            >{WBC_ICONS.dots}</button>
            {isMenuOpen && (
              <div className="wb-card-menu" role="menu">
                <button type="button" role="menuitem" onClick={function (event) {
                  event.stopPropagation();
                  setMenuId("");
                  setRenameGroup(group);
                }}>
                  <span className="wbc-chat-menu-icon" aria-hidden="true">{WBC_ICONS.edit}</span>
                  <span>{wbcT("workbenchChat.groupRename", "Rename group")}</span>
                </button>
                <button type="button" role="menuitem" className="danger" onClick={function (event) {
                  event.stopPropagation();
                  dissolveChatGroup(group.id);
                }}>
                  <span className="wbc-chat-menu-icon" aria-hidden="true">{WBC_ICONS.x}</span>
                  <span>{wbcT("workbenchChat.groupDissolve", "Dissolve group")}</span>
                </button>
              </div>
            )}
          </span>
        </header>
        <div className={"wbc-chat-group-summary" + (groupMetadataPending[group.id] ? " is-updating" : "")}>
          <WbcHoverMarquee text={group.summary || (groupMetadataPending[group.id]
            ? wbcT("workbenchChat.groupSummaryGenerating", "Generating summary…")
            : groupChats.map(function (chat) { return chat.title; }).join(" · "))} />
        </div>
        <div
          className={"wbc-chat-group-content" + (isCollapsed ? " collapsed" : " expanded")}
          aria-hidden={isCollapsed}
          inert={isCollapsed ? "" : undefined}
        >
          <div className="wbc-chat-group-content-inner">
            <div className="wbc-chat-group-children">
              {groupChats.map(function (chat) { return renderChatCard(chat, { insideGroup: true }); })}
              {groupDropReady ? renderDropClone(movingChat) : null}
            </div>
          </div>
        </div>
        {groupDropReady && (
          <span className="wbc-chat-group-drop-hint">{WBC_ICONS.copy}{wbcT("workbenchChat.releaseToExistingGroup", "Release to add to this chat group")}</span>
        )}
      </section>
    );
  }

  var recentOverviewLimit = 10;
  var visiblePinnedRailItems = pinnedRailItems;
  var visibleRecentRailItems = showAllRecent
    ? recentRailItems
    : recentRailItems.slice(0, recentOverviewLimit);
  var visibleGroupRailItems = groupRailItems;
  var recentOverflowCount = Math.max(0, recentRailItems.length - visibleRecentRailItems.length);
  var visibleRailItemCount = visiblePinnedRailItems.length + visibleRecentRailItems.length + visibleGroupRailItems.length;
  var visibleTrackLayoutKey = [
    visiblePinnedRailItems.map(function (item) { return String(item.chat && item.chat.id || ""); }).join(","),
    visibleRecentRailItems.map(function (item) { return String(item.chat && item.chat.id || ""); }).join(","),
    visibleGroupRailItems.map(function (item) {
      return String(item.group && item.group.id || "") + ":" + (collapsedGroups[item.group && item.group.id] ? "closed" : "open");
    }).join(","),
  ].join("|");
  /* Group-drop previews temporarily replace one flat row with a taller group
     frame. Suspend the floating status layer while that preview is visible;
     the cards keep showing their own status icons, and the dependency below
     remeasures the overlay as soon as the real list is restored. */
  var railDragActive = !!dragState;

  useWbcLayoutEffect(function () {
    var list = chatListRef.current;
    if (!list) return undefined;
    var frame = 0;
    function refreshViewport() {
      if (frame) return;
      frame = window.requestAnimationFrame(function () {
        frame = 0;
        setUiViewportRevision(function (value) { return value + 1; });
      });
    }
    list.addEventListener("scroll", refreshViewport, { passive: true });
    window.addEventListener("resize", refreshViewport);
    var observer = typeof ResizeObserver === "function" ? new ResizeObserver(refreshViewport) : null;
    if (observer) observer.observe(list);
    refreshViewport();
    return function () {
      if (frame) window.cancelAnimationFrame(frame);
      list.removeEventListener("scroll", refreshViewport);
      window.removeEventListener("resize", refreshViewport);
      if (observer) observer.disconnect();
    };
  }, [projectId, query, showAllRecent, visibleTrackLayoutKey, collapsed]);

  useWbcLayoutEffect(function () {
    if (railDragActive) {
      railDragWasActiveRef.current = true;
      return;
    }
    if (!railDragWasActiveRef.current) return;
    railDragWasActiveRef.current = false;
    /* Do not paint the pre-drag overlay coordinates for a frame while the
       restored flat list is waiting for its next measurement. */
    setTrackGeometryByChatId({});
  }, [railDragActive, projectId]);

  useWbcLayoutEffect(function () {
    trackMeasuredExpandedRef.current = false;
    setTrackGeometryByChatId({});
  }, [projectId]);

  /* Measure the expanded list itself instead of inferring geometry from the
     conversation index. Search, section headings, row height, groups, scroll,
     and the overview limit all affect the real Y coordinate. The hidden track
     keeps its collapsed bounds while expanded, while the collapsed list keeps
     its layout geometry even though it is not painted. Projecting each row
     centre into the track therefore works on initial load and in both states. */
  useWbcLayoutEffect(function () {
    /* Keep the last settled row geometry for the whole rail transition. A
       ResizeObserver fires while the hidden list is becoming visible; using
       those transient rectangles rewrites `top` for one paint and makes the
       marker flash before its transform animation can take over. */
    if (renderedRailMotionPhase || railDragActive) return undefined;
    if (collapsed && trackMeasuredExpandedRef.current) return undefined;
    if (!collapsed) trackMeasuredExpandedRef.current = true;
    var rail = railRef.current;
    var track = trackRef.current;
    if (!rail || !track) return undefined;
    var frame = 0;
    function measure() {
      if (frame) cancelAnimationFrame(frame);
      frame = requestAnimationFrame(function () {
        frame = 0;
        var trackRect = track.getBoundingClientRect();
        if (trackRect.height <= 0) return;
        var measured = {};
        rail.querySelectorAll(".wbc-chat-card[data-chat-id]").forEach(function (card) {
          var rect = card.getBoundingClientRect();
          var iconRect = card.querySelector(".wbc-chat-row-icon")?.getBoundingClientRect();
          var chatId = String(card.getAttribute("data-chat-id") || "");
          if (!chatId || rect.height <= 0) return;
          measured[chatId] = {
            position: ((rect.top + (rect.height / 2) - trackRect.top) / trackRect.height) * 100,
            trackHeight: trackRect.height,
            expandedX: !collapsed && !renderedRailMotionPhase && iconRect
              ? iconRect.left + (iconRect.width / 2) - trackRect.left
              : null,
          };
        });
        setTrackGeometryByChatId(function (current) {
          var next = { ...current };
          Object.keys(measured).forEach(function (chatId) {
            var previous = current[chatId] || {};
            var measuredExpandedX = measured[chatId].expandedX;
            next[chatId] = {
              position: measured[chatId].position,
              trackHeight: measured[chatId].trackHeight,
              expandedX: measuredExpandedX != null && Number.isFinite(Number(measuredExpandedX))
                ? Number(measuredExpandedX)
                : (Number.isFinite(Number(previous.expandedX)) ? Number(previous.expandedX) : 29),
            };
          });
          var keys = Object.keys(next);
          if (
            keys.length === Object.keys(current).length
            && keys.every(function (key) {
              return Math.abs(Number(next[key].position) - Number(current[key] && current[key].position)) < 0.05
                && Math.abs(Number(next[key].trackHeight) - Number(current[key] && current[key].trackHeight)) < 0.05
                && Math.abs(Number(next[key].expandedX) - Number(current[key] && current[key].expandedX)) < 0.05;
            })
          ) return current;
          return next;
        });
      });
    }
    measure();
    rail.addEventListener("scroll", measure, true);
    window.addEventListener("resize", measure);
    var observer = typeof ResizeObserver === "function" ? new ResizeObserver(measure) : null;
    if (observer) {
      observer.observe(rail);
      observer.observe(track);
      rail.querySelectorAll(".wbc-chat-list, .wbc-chat-list-primary, .wbc-chat-card[data-chat-id]").forEach(function (element) {
        observer.observe(element);
      });
    }
    return function () {
      if (frame) cancelAnimationFrame(frame);
      rail.removeEventListener("scroll", measure, true);
      window.removeEventListener("resize", measure);
      if (observer) observer.disconnect();
    };
  }, [collapsed, projectId, visibleTrackLayoutKey, renderedRailMotionPhase, railDragActive]);

  var statusTrackItems = wbcConversationTrackPositions(orderedChats.map(function (chat, index) {
    return {
      chat: chat,
      index: index,
      state: wbcConversationTrackState(chat, runningChatIds, newResultChatIds),
    };
  }).filter(function (item) { return !!item.state; }), orderedChats.length, trackGeometryByChatId);

  function renderRailItem(item) {
    if (item.kind === "group") return renderGroupFrame(item.group, item.chats);
    var chat = item.chat;
    var isNewGroupTarget = !!(
      dragState
      && dragState.mode === "group"
      && dragState.targetId === String(chat.id)
      && !wbcFindChatGroup(groups, chat.id)
    );
    if (!isNewGroupTarget) return renderChatCard(chat);
    var movingChat = chatMap.get(String(dragState.movingId));
    return (
      <section
        key={"drop-group:" + chat.id}
        className="wbc-chat-group wbc-chat-group-preview drop-ready"
        onDragOver={function (event) {
          if (!dragState || !wbcHasChatDrag(event)) return;
          event.preventDefault();
          event.stopPropagation();
          if (event.dataTransfer) event.dataTransfer.dropEffect = "move";
          updateDragState({
            movingId: dragState.movingId,
            targetId: String(chat.id),
            targetGroupId: "",
            edge: "center",
            mode: "group",
          });
        }}
        onDrop={function (event) {
          if (!dragState || !wbcHasChatDrag(event)) return;
          event.preventDefault();
          event.stopPropagation();
          dropCommittedRef.current = true;
          commitGroupDrop(dragState.movingId, String(chat.id));
          setDragState(null);
        }}
      >
        <header className="wbc-chat-group-head">
          <span className="wbc-chat-group-toggle">
            <span className="wbc-chat-group-leading-chevron expanded" aria-hidden="true">{WBC_ICONS.chevronRight}</span>
            <span className="wbc-chat-group-count">2</span>
            <b>{wbcT("workbenchChat.newGroup", "New chat group")}</b>
          </span>
        </header>
        <div className="wbc-chat-group-children">
          {renderChatCard(chat, { insideGroup: true })}
          {renderDropClone(movingChat)}
        </div>
        <span className="wbc-chat-group-drop-hint">{WBC_ICONS.copy}{wbcT("workbenchChat.releaseToGroup", "Release to create a chat group")}</span>
      </section>
    );
  }

  function renderRailSection(id, label, icon, items) {
    if (!items.length) return null;
    return (
      <section key={id} className={"wbc-rail-section wbc-rail-section-" + id}>
        <header className="wbc-rail-section-label">
          {icon ? <span aria-hidden="true">{icon}</span> : null}
          <b>{label}</b>
        </header>
        <div className="wbc-rail-section-items">
          {items.map(renderRailItem)}
        </div>
      </section>
    );
  }

  useWbcEffect(function () {
    if (!window.CyreneUI.has("uiSurface")) return undefined;
    var uiSurface = workbenchServices.uiSurface();
    var unregister = [];
    unregister.push(uiSurface.register({
        node_id: "new_chat",
        parent_id: "root",
        scope: "main",
        order: 30,
        get_element: function () { return newChatButtonRef.current; },
        get_node: function () { return { role: "button", name: wbcT("workbenchChat.newChat", "New chat") }; },
        actions: [{
          action_id: "invoke", kind: "invoke", risk: "R1", gesture_aliases: ["press", "keyboard"],
          outcome: { effect: "creates_and_opens_surface", target_scope: "chat", inspect_after: true },
        }],
        handlers: {
          invoke: function () {
            return Promise.resolve(onCreate && onCreate()).then(function (chat) {
              if (chat && chat.id) wbcNotifyAgentChatFlow("created", chat.id);
              return chat && chat.id ? { chat_id: String(chat.id), created_by_agent: true } : {};
            });
          },
        },
      }));
    unregister.push(uiSurface.register({
        node_id: "chat_search_input",
        parent_id: "root",
        scope: "main",
        order: 40,
        get_element: function () { return chatSearchRef.current; },
        get_node: function () {
          return {
            role: "searchbox",
            name: wbcT("workbenchChat.searchCurrentProject", "Search current project..."),
            value_summary: query,
            state: {
              project_id: String(projectId || ""),
              query_length: String(query || "").length,
              result_count: filtered.length,
            },
          };
        },
        actions: [
          { action_id: "set_value", kind: "set_value", risk: "R1", gesture_aliases: ["text_input"], input_schema: { value: "text<=500" } },
          { action_id: "clear_value", kind: "set_value", risk: "R1", gesture_aliases: ["semantic_clear"], input_schema: {} },
        ],
        handlers: {
          set_value: function (input) { setQuery(String(input.value || "")); },
          clear_value: function () { setQuery(""); },
        },
      }));
    var visibleChatIds = wbcViewportChatIds(chatListRef.current);
    var visibleChats = visibleChatIds.map(function (chatId) { return chatMap.get(chatId); }).filter(Boolean);
    unregister.push(uiSurface.register({
      node_id: "chat_list",
      parent_id: "root",
      scope: "main",
      order: 100,
      get_element: function () { return chatListRef.current; },
      get_node: function () {
        var list = chatListRef.current;
        var scrollTop = Number(list && list.scrollTop || 0);
        var scrollHeight = Number(list && list.scrollHeight || 0);
        var clientHeight = Number(list && list.clientHeight || 0);
        return {
          role: "list",
          name: wbcT("rail.chat", "Chats"),
          state: {
            total_count: filtered.length,
            visible_count: wbcViewportChatIds(list).length,
            query: query,
            scroll_top: scrollTop,
            scroll_height: scrollHeight,
            client_height: clientHeight,
            can_page_previous: scrollTop > 1,
            can_page_next: scrollTop + clientHeight < scrollHeight - 1 || recentOverflowCount > 0,
            hidden_result_count: recentOverflowCount,
            all_recent_rendered: showAllRecent,
          },
        };
      },
      actions: [
        { action_id: "scroll_page", kind: "scroll", risk: "R1", gesture_aliases: ["wheel", "keyboard"], input_schema: { delta: "-2000..2000" } },
        { action_id: "page_previous", kind: "invoke", risk: "R1", gesture_aliases: ["page_up", "keyboard"] },
        { action_id: "page_next", kind: "invoke", risk: "R1", gesture_aliases: ["page_down", "keyboard"] },
        { action_id: "search", kind: "set_value", risk: "R1", gesture_aliases: ["text_input"], input_schema: { value: "text<=500" } },
        { action_id: "clear_search", kind: "set_value", risk: "R1", gesture_aliases: ["semantic_clear"], input_schema: {} },
      ].concat(recentOverflowCount > 0 ? [
        { action_id: "show_all_results", kind: "invoke", risk: "R1", gesture_aliases: ["press", "keyboard"] },
      ] : []),
      handlers: {
        scroll_page: function (input) {
          var list = chatListRef.current;
          if (list && typeof list.scrollBy === "function") list.scrollBy({ top: Number(input.delta || 0), behavior: "auto" });
          setUiViewportRevision(function (value) { return value + 1; });
        },
        page_previous: function () {
          var list = chatListRef.current;
          if (list && typeof list.scrollBy === "function") list.scrollBy({ top: -Math.max(1, Number(list.clientHeight || 0) * 0.9), behavior: "auto" });
          setUiViewportRevision(function (value) { return value + 1; });
        },
        page_next: function () {
          var list = chatListRef.current;
          if (recentOverflowCount > 0 && list && Number(list.scrollTop || 0) + Number(list.clientHeight || 0) >= Number(list.scrollHeight || 0) - 1) {
            setShowAllRecent(true);
            setUiViewportRevision(function (value) { return value + 1; });
            return;
          }
          if (list && typeof list.scrollBy === "function") list.scrollBy({ top: Math.max(1, Number(list.clientHeight || 0) * 0.9), behavior: "auto" });
          setUiViewportRevision(function (value) { return value + 1; });
        },
        search: function (input) { setQuery(String(input.value || "")); },
        clear_search: function () { setQuery(""); },
        show_all_results: function () { setShowAllRecent(true); },
      },
    }));
    visibleChats.forEach(function (chat) {
      var chatId = String(chat.id || "");
      var nodeId = "chat_" + chatId;
      unregister.push(uiSurface.register({
        node_id: nodeId,
        parent_id: "chat_list",
        scope: "main",
        order: 1000 + visibleChats.indexOf(chat),
        get_node: function () {
          return chatId && wbcViewportChatIds(chatListRef.current).indexOf(chatId) >= 0 ? {
            role: "listitem",
            name: chat.title || wbcT("workbenchChat.newChat", "New chat"),
            value_summary: chat.preview || "",
            state: {
              selected: chatId === String(activeChatId || ""),
              pinned: (Array.isArray(pinnedChatIds) ? pinnedChatIds : []).map(String).indexOf(chatId) >= 0,
              grouped: !!wbcFindChatGroup(groups, chatId),
            },
          } : null;
        },
        actions: [
          { action_id: "open", kind: "invoke", risk: "R1", gesture_aliases: ["press", "keyboard"] },
          { action_id: "open_menu", kind: "open_menu", risk: "R1", gesture_aliases: ["context_menu", "more_button"] },
        ].concat(onOpenSplit ? [
          { action_id: "open_split", kind: "move", risk: "R1", gesture_aliases: ["drag_to_split"], input_schema: { side: "text<=5" } },
        ] : []),
        handlers: {
          open: function () { setMenuId(""); return onSelect(chatId); },
          open_menu: function () { setMenuId(chatId); },
          open_split: function (input) {
            var side = String(input && input.side || "right");
            if (side !== "left" && side !== "right") throw new Error("pane side must be left or right");
            return onOpenSplit(chatId, { side: side });
          },
        },
      }));
    });
    if (menuId && String(menuId).indexOf("group:") !== 0) {
      var menuChat = chatMap.get(String(menuId));
      if (menuChat) {
        var menuChatId = String(menuChat.id || "");
        var pinned = (Array.isArray(pinnedChatIds) ? pinnedChatIds : []).map(String).indexOf(menuChatId) >= 0;
        uiSurface.setScope("chat_menu");
        unregister.push(uiSurface.register({
          node_id: "chat_context_menu",
          parent_id: "root",
          scope: "chat_menu",
          order: 10,
          get_node: function () { return { role: "menu", name: menuChat.title || wbcT("workbenchChat.newChat", "New chat") }; },
          actions: [{ action_id: "dismiss", kind: "dismiss", risk: "R1", gesture_aliases: ["escape_key", "scrim"] }],
          handlers: { dismiss: function () { setMenuId(""); } },
        }));
        unregister.push(uiSurface.register({
          node_id: "chat_menu_pin",
          parent_id: "chat_context_menu",
          scope: "chat_menu",
          order: 20,
          get_node: function () { return { role: "menuitem", name: pinned ? wbcT("workbenchChat.unpin", "Unpin chat") : wbcT("workbenchChat.pin", "Pin chat") }; },
          actions: [{ action_id: "invoke", kind: "invoke", risk: "R1", gesture_aliases: ["press", "keyboard"] }],
          handlers: { invoke: function () { setMenuId(""); return onTogglePinned && onTogglePinned(menuChat, !pinned); } },
        }));
        unregister.push(uiSurface.register({
          node_id: "chat_menu_rename",
          parent_id: "chat_context_menu",
          scope: "chat_menu",
          order: 30,
          get_node: function () { return { role: "menuitem", name: wbcT("workbenchChat.rename", "Rename chat") }; },
          actions: [{ action_id: "invoke", kind: "invoke", risk: "R1", gesture_aliases: ["press", "keyboard"] }],
          handlers: { invoke: function () { setMenuId(""); setRenameChat(menuChat); } },
        }));
        unregister.push(uiSurface.register({
          node_id: "chat_menu_delete",
          parent_id: "chat_context_menu",
          scope: "chat_menu",
          order: 50,
          get_node: function () { return { role: "menuitem", name: wbcT("workbenchChat.delete", "Delete chat") }; },
          actions: [{ action_id: "invoke", kind: "invoke", risk: "R1", gesture_aliases: ["press", "keyboard"] }],
          handlers: { invoke: function () { setMenuId(""); return onDelete && onDelete(menuChatId); } },
        }));
      }
    } else if (uiSurface.getScope() === "chat_menu") {
      uiSurface.setScope("main");
    }
    return function () { unregister.forEach(function (remove) { remove(); }); };
  }, [projectId, defaultOrderKey, filtered, query, collapsed, groups, menuId, activeChatId, pinnedChatIds, onSelect, onOpenSplit, onCreate, onDelete, onTogglePinned, showAllRecent, recentOverflowCount, uiViewportRevision]);

  var terminalMap = new Map((Array.isArray(terminals) ? terminals : []).map(function (terminal) {
    return [String(terminal.id), terminal];
  }));
  var orderedTerminals = wbcNormalizeChatOrder(terminalDefaultOrder, terminalOrder).map(function (id) {
    return terminalMap.get(id);
  }).filter(Boolean).filter(function (terminal) {
    return !normalizedQuery
      || String(terminal.displayTitle || terminal.title || "").toLowerCase().indexOf(normalizedQuery) >= 0
      || String(terminal.cwd || "").toLowerCase().indexOf(normalizedQuery) >= 0;
  });
  var terminalPinnedSet = new Set(terminalPinnedIds.map(String));
  var pinnedTerminals = orderedTerminals.filter(function (terminal) { return terminalPinnedSet.has(String(terminal.id)); });
  var recentTerminals = orderedTerminals.filter(function (terminal) { return !terminalPinnedSet.has(String(terminal.id)); });

  function storeTerminalOrder(nextOrder) {
    var normalized = wbcNormalizeChatOrder(terminalDefaultOrder, nextOrder);
    setTerminalOrder(normalized);
    if (onUpdateTerminalLayout) onUpdateTerminalLayout(normalized, terminalPinnedIds);
  }

  function toggleTerminalPinned(terminalId) {
    var id = String(terminalId || "");
    var next = terminalPinnedSet.has(id)
      ? terminalPinnedIds.filter(function (candidate) { return String(candidate) !== id; })
      : terminalPinnedIds.concat([id]);
    setTerminalPinnedIds(next);
    if (onUpdateTerminalLayout) onUpdateTerminalLayout(terminalOrder, next);
  }

  function terminalRailVisualState(terminal) {
    var status = String(terminal && terminal.status || "").trim().toLowerCase();
    var commandState = String(terminal && terminal.commandState || "").trim().toLowerCase();
    var exitReason = String(terminal && terminal.exitReason || "").trim().toLowerCase();
    var processRunning = status === "running" || status === "starting";
    var activityRunning = status === "starting"
      || (status === "running" && ["command", "output"].indexOf(commandState) >= 0);
    var failed = ["failed", "error"].indexOf(status) >= 0
      || ["pty_lost", "signal", "recovery_failed", "restart_failed"].indexOf(exitReason) >= 0
      || (status === "exited" && terminal && terminal.exitCode != null && Number(terminal.exitCode) !== 0);
    /* A live interactive shell waiting at its prompt is healthy/ready, not an
       indefinitely-running job. Map activity to the same semantic tones as a
       conversation: in progress uses status-running, success/ready uses
       status-completed, and abnormal termination uses status-failed. */
    var completed = !failed && (
      (status === "running" && !activityRunning)
      || (status === "exited" && exitReason === "process_exit")
    );
    return {
      processRunning: processRunning,
      tone: failed ? " status-failed" : activityRunning ? " status-running" : completed ? " status-completed" : "",
    };
  }

  function renderTerminalCard(terminal) {
    var id = String(terminal.id || "");
    var isMenuOpen = menuId === "terminal:" + id;
    var isPinned = terminalPinnedSet.has(id);
    var visualState = terminalRailVisualState(terminal);
    var running = visualState.processRunning;
    return <div
      key={id}
      role="button"
      tabIndex={0}
      draggable="true"
      data-terminal-id={id}
      data-cyrene-context-menu="true"
      className={"wbc-chat-card wbc-terminal-card"
        + (String(activeTerminalId || "") === id ? " active" : "")
        + (isMenuOpen ? " menu-open" : "")
        + (terminalDragId === id ? " dragging" : "")
        + visualState.tone}
      onClick={function () { setMenuId(""); if (onOpenTerminal) onOpenTerminal(id); }}
      onKeyDown={function (event) {
        if (event.key === "Enter" || event.key === " ") {
          event.preventDefault();
          if (onOpenTerminal) onOpenTerminal(id);
        }
      }}
      onContextMenu={function (event) {
        event.preventDefault();
        event.stopPropagation();
        setMenuId("terminal:" + id);
      }}
      onDragStart={function (event) {
        if (event.target && event.target.closest && event.target.closest("button")) {
          event.preventDefault();
          return;
        }
        setMenuId("");
        setTerminalDragId(id);
        wbcSetResourceDrag(event, { kind: "terminal", terminalId: id, title: terminal.displayTitle || terminal.title || "Terminal" });
      }}
      onDragOver={function (event) {
        if (!terminalDragId || terminalDragId === id) return;
        event.preventDefault();
        event.stopPropagation();
        var rect = event.currentTarget.getBoundingClientRect();
        var edge = event.clientY < rect.top + rect.height / 2 ? "before" : "after";
        setTerminalOrder(function (current) { return wbcMoveChatOrder(current, terminalDragId, id, edge); });
      }}
      onDrop={function (event) {
        if (!terminalDragId) return;
        event.preventDefault();
        event.stopPropagation();
        storeTerminalOrder(terminalOrder);
        setTerminalDragId("");
      }}
      onDragEnd={function () { storeTerminalOrder(terminalOrder); setTerminalDragId(""); }}
    >
      <span className="wbc-chat-card-top">
        <span className="wbc-chat-row-icon" aria-hidden="true">{WBC_ICONS.slash}</span>
        <span className="wbc-chat-card-title">
          {isPinned && <span className="wbc-chat-card-pin" aria-hidden="true">{WBC_ICONS.pin}</span>}
          <b><WbcHoverMarquee text={terminal.displayTitle || terminal.title || wbcT("terminal.title", "Terminal")} /></b>
        </span>
        <span className="wbc-chat-card-right">
          <time className="wbc-chat-card-time">{wbcFormatTime(terminal.updatedAt || terminal.createdAt)}</time>
          <span className="wbc-chat-card-actions">
            <button type="button" className="wb-card-menu-btn wbc-chat-card-menu-btn" onClick={function (event) {
              event.stopPropagation();
              setMenuId(isMenuOpen ? "" : "terminal:" + id);
            }} aria-label={wbcT("common.moreActions", "More actions")}>{WBC_ICONS.dots}</button>
            {isMenuOpen && <div className="wb-card-menu" role="menu">
              <button type="button" role="menuitem" className="wbc-chat-pin-action" onClick={function (event) { event.stopPropagation(); setMenuId(""); toggleTerminalPinned(id); }}>
                <span className="wbc-chat-menu-icon" aria-hidden="true">{WBC_ICONS.pin}</span>
                <span>{isPinned ? wbcT("terminal.unpin", "Unpin terminal") : wbcT("terminal.pin", "Pin terminal")}</span>
              </button>
              <button type="button" role="menuitem" className="wbc-chat-menu-action" onClick={function (event) { event.stopPropagation(); setMenuId(""); setRenameTerminalItem(terminal); }}>
                <span className="wbc-chat-menu-icon" aria-hidden="true">{WBC_ICONS.edit}</span>
                <span>{wbcT("terminal.rename", "Rename terminal")}</span>
              </button>
              <button type="button" role="menuitem" className="wbc-chat-menu-action danger" onClick={function (event) { event.stopPropagation(); setMenuId(""); if (onDeleteTerminal) onDeleteTerminal(id); }}>
                <span className="wbc-chat-menu-icon" aria-hidden="true">{WBC_ICONS.trash}</span>
                <span>{wbcT("terminal.delete", "Delete terminal")}</span>
              </button>
            </div>}
          </span>
        </span>
      </span>
      <span className="wbc-chat-card-preview">
        {running ? <i className="wbc-running-dot" /> : null}
        <WbcHoverMarquee text={running ? String(terminal.cwd || "") : wbcT("terminal.exited", "Process exited")} />
      </span>
    </div>;
  }

  function renderTerminalSection(id, label, items) {
    if (!items.length) return null;
    return <section className={"wbc-rail-section wbc-rail-section-" + id + " wbc-terminal-section"}>
      <header className="wbc-rail-section-label">{id === "pinned" ? <span aria-hidden="true">{WBC_ICONS.pin}</span> : null}<b>{label}</b></header>
      <div className="wbc-rail-section-items">{items.map(renderTerminalCard)}</div>
    </section>;
  }

  function openUnifiedFileResult(entry) {
    if (!entry) return;
    if (entry.kind === "directory") {
      setQuery("");
      setMenuId("");
      setTerminalToolsExpanded(false);
      setFilePath(entry.path);
      setFileToolsExpanded(true);
      return;
    }
    if (onOpenFile) onOpenFile(entry);
  }

  function renderUnifiedFileResult(entry) {
    var visual = wbcProjectFileVisual(entry);
    return <button
      key={entry.path}
      type="button"
      className={"workbench-project-file-row wbc-unified-search-file is-" + visual.kind}
      onClick={function () { openUnifiedFileResult(entry); }}
    >
      <span className="workbench-project-file-icon" aria-hidden="true">{visual.icon}</span>
      <span className="wbc-unified-search-file-copy">
        <b>{entry.name}</b>
        <small>{entry.path}</small>
      </span>
      {entry.kind === "directory" && <span className="workbench-project-file-chevron" aria-hidden="true">{WBC_ICONS.chevronRight}</span>}
    </button>;
  }

  var unifiedSearchActive = normalizedQuery.length > 0;
  var unifiedSearchResultCount = filtered.length
    + (codeAvailable ? globalFileEntries.length + orderedTerminals.length : 0);

  return (
    <aside ref={railRef} className={"wbc-rail workbench-integrated-rail"
      + (collapsed ? " is-collapsed" : "")
      + (fileToolsExpanded || terminalToolsExpanded ? " has-expanded-project-tool" : "")
      + (renderedRailMotionPhase ? (" is-status-" + renderedRailMotionPhase) : "")}>
      <div className="wbc-rail-glass">
        <div className="wbc-nav-card">
          <div className="wbc-nav-card-head workbench-integrated-rail-head workbench-integrated-rail-search-head">
            {!collapsed && railMode === "chat" && (
              <button
                ref={newChatButtonRef}
                data-cyrene-node-id="new_chat"
                type="button"
                className="wbc-project-new-chat"
                onClick={onCreate}
                title={wbcT("workbenchChat.newChat", "New chat")}
                aria-label={wbcT("workbenchChat.newChat", "New chat")}
              >{WBC_ICONS.plus}</button>
            )}
            {!collapsed && (
              <div className="wbc-search">
                <span className="wbc-search-icon">{WBC_ICONS.search}</span>
                <input
                  ref={chatSearchRef}
                  data-cyrene-node-id="chat_search_input"
                  value={query}
                  onChange={function (e) {
                    var nextQuery = e.target.value;
                    setGlobalFilesLoading(codeAvailable && Boolean(nextQuery.trim()));
                    setQuery(nextQuery);
                  }}
                  placeholder={wbcT("rail.searchEverythingShort", "Search all")}
                  aria-label={wbcT("rail.searchEverything", "Search chats, files, and terminals")}
                />
              </div>
            )}
            {collapseControl || (
              <button
                type="button"
                className="workbench-sidebar-collapse-control"
                onClick={onToggleCollapsed}
                title={collapsed ? wbcT("rail.expand", "Expand sidebar") : wbcT("rail.collapse", "Collapse sidebar")}
                aria-label={collapsed ? wbcT("rail.expand", "Expand sidebar") : wbcT("rail.collapse", "Collapse sidebar")}
              >{WBC_ICONS.sidebar}</button>
            )}
          </div>

        </div>
      </div>
      {menuId && <div className="wb-card-menu-scrim" onClick={function () { setMenuId(""); }} />}
      {unifiedSearchActive ? (
        <div
          ref={chatListRef}
          data-cyrene-node-id="chat_list"
          className={"wbc-chat-list workbench-integrated-rail-body wbc-unified-search-results" + (globalFilesLoading ? " is-loading" : "") + (!globalFilesLoading && unifiedSearchResultCount === 0 ? " is-empty" : "") + (menuId ? " menu-active" : "")}
        >
          <div className="wbc-chat-list-primary">
            {globalFilesLoading ? (
              <div className="workbench-muted wbc-rail-loading" role="status">{wbcT("rail.searchingEverything", "Searching project...")}</div>
            ) : <>
              {filtered.length ? <section className="wbc-rail-section wbc-unified-search-section is-chat">
                <header className="wbc-rail-section-label"><b>{wbcT("workbench.page.chat", "Chats")}</b><span>{filtered.length}</span></header>
                <div className="wbc-rail-section-items">{filtered.map(function (chat) { return renderChatCard(chat); })}</div>
              </section> : null}
              {codeAvailable && globalFileEntries.length ? <section className="wbc-rail-section wbc-unified-search-section is-file">
                <header className="wbc-rail-section-label"><b>{wbcT("rail.files", "Files")}</b><span>{globalFileEntries.length}</span></header>
                <div className="wbc-rail-section-items wbc-unified-search-file-items">{globalFileEntries.map(renderUnifiedFileResult)}</div>
              </section> : null}
              {codeAvailable && orderedTerminals.length ? <section className="wbc-rail-section wbc-unified-search-section is-terminal">
                <header className="wbc-rail-section-label"><b>{wbcT("terminal.title", "Terminal")}</b><span>{orderedTerminals.length}</span></header>
                <div className="wbc-rail-section-items">{orderedTerminals.map(renderTerminalCard)}</div>
              </section> : null}
              {globalFilesError ? <div className="workbench-error wbc-unified-search-warning">{globalFilesError}</div> : null}
              {unifiedSearchResultCount === 0 ? <div className="workbench-muted wbc-rail-empty">{wbcT("rail.noUnifiedMatches", "No matching chats, files, or terminals.")}</div> : null}
            </>}
          </div>
        </div>
      ) : (
      <div
        ref={chatListRef}
        data-cyrene-node-id="chat_list"
        className={"wbc-chat-list workbench-integrated-rail-body" + (loading ? " is-loading" : "") + (!loading && visibleRailItemCount === 0 ? " is-empty" : "") + (menuId ? " menu-active" : "") + (!loading && visibleGroupRailItems.length ? " has-groups" : "")}
        onDragOver={function (event) {
          if (!dragState || !wbcHasChatRailDrag(event)) return;
          event.preventDefault();
          if (event.dataTransfer) event.dataTransfer.dropEffect = "move";
          var overPrimarySurface = !!(
            event.target.closest
            && event.target.closest(".wbc-chat-list-primary")
            && !event.target.closest(".wbc-chat-card, .wbc-chat-group, .wbc-rail-show-all")
          );
          if (event.target === event.currentTarget || overPrimarySurface) {
            if (dragState.dragKind === "group") {
              var trailingOrder = wbcMoveChatOrderBlock(order, dragState.movingIds, [], "after");
              if (trailingOrder.join("|") !== order.join("|")) setOrder(trailingOrder);
            }
            updateDragState({
              movingId: dragState.movingId,
              targetId: "",
              targetGroupId: "",
              edge: "after",
              mode: dragState.dragKind === "group" ? "group-reorder" : "reorder",
            });
          }
        }}
        onDrop={function (event) {
          if (!dragState || !wbcHasChatRailDrag(event)) return;
          event.preventDefault();
          dropCommittedRef.current = true;
          if (dragState.dragKind === "group") {
            commitGroupOrder(order, groups.find(function (candidate) {
              return candidate.id === dragState.movingGroupId;
            }));
            setDragState(null);
            return;
          }
          commitOrder(order, dragState.movingId);
          if (dragState.sourceGroupId) commitUngroupDrop(dragState.movingId);
          setDragState(null);
        }}
      >
        <div className="wbc-chat-list-primary">
          {loading && (
            <div className="workbench-muted wbc-rail-loading" role="status">
              {wbcT("workbenchChat.loading", "Loading chats...")}
            </div>
          )}
          {!loading && visibleRailItemCount === 0 && (
            <div className="workbench-muted wbc-rail-empty">{query
              ? wbcT("workbenchChat.noMatches", "No matching chats.")
              : wbcT("workbenchChat.emptyFilter", "No chats in this section.")}</div>
          )}
          {!loading && renderRailSection("pinned", wbcT("workbenchChat.pinnedSection", "Pinned"), WBC_ICONS.pin, visiblePinnedRailItems)}
          {!loading && renderRailSection("recent", wbcT("workbenchChat.recent", "Recent"), null, visibleRecentRailItems)}
          {!loading && recentOverflowCount > 0 && (
            <button type="button" className="wbc-rail-show-all" onClick={function () { setShowAllRecent(true); }}>
              {wbcT("workbenchChat.showAllRecent", "Show all {count}", { count: recentRailItems.length })}
              {WBC_ICONS.chevronRight}
            </button>
          )}
        </div>
        {!loading && visibleGroupRailItems.length > 0 && (
          <div className="wbc-chat-list-group-region">
            {renderRailSection("groups", wbcT("workbenchChat.groups", "Chat groups"), null, visibleGroupRailItems)}
          </div>
        )}
        <span className="wbc-sr-only" aria-live="polite">{announcement}</span>
      </div>
      )}
      {railMode === "chat" && !unifiedSearchActive && <div ref={trackRef} className="wbc-conversation-status-track" role="navigation" aria-label={wbcT("workbenchChat.track.label", "Conversation status map")}>
        {!railDragActive && statusTrackItems.map(function (item) {
          var chat = item.chat;
          var previewOpen = previewChatId === String(chat.id);
          var answerState = previewAnswerState.chatId === String(chat.id)
            ? previewAnswerState
            : { busy: false, result: "", error: "" };
          var markerGlyph = item.state.kind === "attention"
            ? WBC_ICONS.alert
            : item.state.kind === "failed"
              ? WBC_ICONS.errorCircle
              : item.state.kind === "result"
                ? WBC_ICONS.check
                : WBC_ICONS.running;
          var alignment = item.position < 24 ? " align-top" : item.position > 76 ? " align-bottom" : " align-center";
          var title = item.state.label + " · " + (chat.title || wbcT("workbenchChat.newChat", "New chat"));
          return (
            <div
              key={chat.id}
              className={"wbc-conversation-status-anchor" + alignment + (item.measured ? " is-measured" : "") + (previewOpen ? " preview-open" : "")}
              style={{
                "--wbc-track-position": item.position + "%",
                "--wbc-track-expanded-position": item.expandedPosition + "%",
                "--wbc-track-expanded-x": (Number.isFinite(item.expandedX) ? item.expandedX : 29) + "px",
                "--wbc-track-collapse-y": (Number.isFinite(item.collapseY) ? item.collapseY : 0) + "px",
              }}
              onMouseEnter={function () { openStatusPreview(chat.id); }}
              onMouseMove={function () { if (!previewOpen) openStatusPreview(chat.id); }}
              onMouseLeave={closeStatusPreviewSoon}
              onFocusCapture={function () { openStatusPreview(chat.id); }}
              onBlurCapture={function (event) {
                if (event.currentTarget.contains(event.relatedTarget)) return;
                closeStatusPreviewSoon();
              }}
              onKeyDown={function (event) {
                if (event.key !== "Escape") return;
                event.stopPropagation();
                setPreviewChatId("");
                if (event.currentTarget.querySelector(".wbc-conversation-status-marker")) {
                  event.currentTarget.querySelector(".wbc-conversation-status-marker").focus();
                }
              }}
            >
              <button
                type="button"
                className={"wbc-conversation-status-marker is-" + item.state.kind + (item.state.urgent ? " is-urgent" : "")}
                aria-label={title}
                aria-expanded={previewOpen}
                onClick={function () {
                  setNewResultChatIds(function (existing) {
                    if (!existing[chat.id]) return existing;
                    var next = { ...existing };
                    delete next[chat.id];
                    return next;
                  });
                  onSelect(chat.id);
                }}
              >
                <span className="wbc-conversation-status-glyph" aria-hidden="true">{markerGlyph}</span>
                <span className="wbc-conversation-status-dot" aria-hidden="true"></span>
              </button>
              {previewOpen && (
                <WbcConversationStatusPreview
                  chat={chat}
                  state={item.state}
                  runtime={runtimeEngine && runtimeEngine.get ? runtimeEngine.get(chat.id) : null}
                  busy={answerState.busy}
                  result={answerState.result}
                  error={answerState.error}
                  onAnswer={function (questionId, answerText, resumeMode) {
                    answerFromStatusPreview(chat, questionId, answerText, resumeMode);
                  }}
                />
              )}
            </div>
          );
        })}
      </div>}
      {codeAvailable && railMode === "chat" && !collapsed ? (
        <section
          ref={projectToolsRef}
          className={"wbc-project-tools"
            + (fileToolsExpanded || terminalToolsExpanded ? " has-expanded-tool" : "")
            + (fileToolsExpanded ? " expanded-file" : "")
            + (terminalToolsExpanded ? " expanded-terminal" : "")
            + (terminalToolsExpanded && String(menuId).indexOf("terminal:") === 0 ? " menu-active" : "")}
          aria-label={wbcT("rail.projectTools", "Tools")}
          onWheel={handleProjectToolWheel}
          onTouchStart={handleProjectToolTouchStart}
          onTouchMove={handleProjectToolTouchMove}
          onTouchEnd={resetProjectToolPull}
          onTouchCancel={resetProjectToolPull}
        >
          <header><span>{wbcT("rail.projectTools", "Tools")}</span></header>
          {fileToolsExpanded ? (
            <div className="wbc-project-tool-inline-header is-file">
              {filePath === "." ? (
                <span className="wbc-project-tool-icon" aria-hidden="true">{WBC_ICONS.folder}</span>
              ) : (
                <button
                  type="button"
                  className="wbc-project-tool-directory-control"
                  aria-label={wbcT("common.back", "Back")}
                  onClick={function () {
                    fileDirectionRef.current = "back";
                    setFilePath(filePath.indexOf("/") === -1 ? "." : filePath.split("/").slice(0, -1).join("/"));
                  }}
                >{WBC_ICONS.chevronLeft}</button>
              )}
              <span className="wbc-project-tool-copy"><b>{wbcT("rail.files", "Files")}</b><small>{filePath === "." ? (projectName || ".") : filePath}</small></span>
              <button
                type="button"
                className="wbc-project-tool-inline-collapse"
                onClick={function () { setFileToolsExpanded(false); }}
                aria-label={wbcT("common.collapse", "Collapse")}
                aria-expanded="true"
                aria-controls="wbc-project-file-list"
              >{WBC_ICONS.chevronRight}</button>
            </div>
          ) : (
            <button
              type="button"
              data-project-tool="file"
              onClick={function () {
                setMenuId("");
                setTerminalToolsExpanded(false);
                setFileToolsExpanded(true);
              }}
              aria-expanded="false"
              aria-controls="wbc-project-file-list"
            >
              <span className="wbc-project-tool-icon" aria-hidden="true">{WBC_ICONS.folder}</span>
              <span className="wbc-project-tool-copy"><b>{wbcT("rail.files", "Files")}</b><small>{projectName || "."}</small></span>
              <span className="wbc-project-tool-chevron" aria-hidden="true">{WBC_ICONS.chevronRight}</span>
            </button>
          )}
          <div
            id="wbc-project-file-list"
            className={"wbc-project-tool-expand wbc-project-file-expand" + (fileToolsExpanded ? " is-expanded" : "")}
            aria-hidden={!fileToolsExpanded}
          >
            <div className="wbc-project-tool-expand-inner">
              <div className="wbc-project-file-list">
                {filesLoading && !hasLoadedFiles && <div className="workbench-muted wbc-rail-loading">{wbcT("rail.loadingFiles", "Loading files...")}</div>}
                {!filesLoading && filesError && <div className="workbench-error wbc-rail-empty">{filesError}</div>}
                {visibleFiles.map(function (entry) {
                  var visual = wbcProjectFileVisual(entry);
                  var projectFile = wbcProjectFileResource(projectId, entry);
                  return <button
                    key={entry.path}
                    type="button"
                    className={"workbench-project-file-row is-" + visual.kind + " enter-" + fileDirection}
                    draggable={projectFile ? "true" : undefined}
                    onDragStart={projectFile ? function (event) { wbcStartFileDrag(event, projectFile); } : undefined}
                    onClick={function () {
                      if (entry.kind === "directory") {
                        fileDirectionRef.current = "forward";
                        setFilePath(entry.path);
                      } else if (onOpenFile) {
                        onOpenFile(entry);
                      }
                    }}>
                    <span className="workbench-project-file-icon" aria-hidden="true">{visual.icon}</span>
                    <b title={entry.path}>{entry.name}</b>
                    {entry.kind === "directory" && <span className="workbench-project-file-chevron" aria-hidden="true">{WBC_ICONS.chevronRight}</span>}
                  </button>;
                })}
                {!filesLoading && !filesError && visibleFiles.length === 0 && <div className="workbench-muted wbc-rail-empty">{query ? wbcT("rail.noMatchingFiles", "No matching files.") : wbcT("rail.noFiles", "This folder is empty.")}</div>}
              </div>
            </div>
          </div>
          {terminalToolsExpanded ? (
            <div className="wbc-project-tool-inline-header is-terminal">
              <span className="wbc-project-tool-icon" aria-hidden="true">{WBC_ICONS.slash}</span>
              <span className="wbc-project-tool-copy"><b>{wbcT("terminal.title", "Terminal")}</b><small>{terminals.filter(function (terminal) { return terminal && (terminal.status === "running" || terminal.status === "starting"); }).length} {wbcT("terminal.runningCountSuffix", "running")}</small></span>
              <button
                type="button"
                className="wbc-project-tool-inline-action"
                onClick={onCreateTerminal}
                title={wbcT("terminal.new", "New terminal")}
                aria-label={wbcT("terminal.new", "New terminal")}
              >{WBC_ICONS.plus}</button>
              <button
                type="button"
                className="wbc-project-tool-inline-collapse"
                onClick={function () { setMenuId(""); setTerminalToolsExpanded(false); }}
                aria-label={wbcT("common.collapse", "Collapse")}
                aria-expanded="true"
                aria-controls="wbc-project-terminal-list"
              >{WBC_ICONS.chevronRight}</button>
            </div>
          ) : (
            <button
              type="button"
              data-project-tool="terminal"
              onClick={function () {
                setMenuId("");
                setFileToolsExpanded(false);
                setTerminalToolsExpanded(true);
              }}
              aria-expanded="false"
              aria-controls="wbc-project-terminal-list"
            >
              <span className="wbc-project-tool-icon" aria-hidden="true">{WBC_ICONS.slash}</span>
              <span className="wbc-project-tool-copy"><b>{wbcT("terminal.title", "Terminal")}</b><small>{terminals.filter(function (terminal) { return terminal && (terminal.status === "running" || terminal.status === "starting"); }).length} {wbcT("terminal.runningCountSuffix", "running")}</small></span>
              <span className="wbc-project-tool-chevron" aria-hidden="true">{WBC_ICONS.chevronRight}</span>
            </button>
          )}
          <div
            id="wbc-project-terminal-list"
            className={"wbc-project-tool-expand wbc-project-terminal-expand" + (terminalToolsExpanded ? " is-expanded" : "")}
            aria-hidden={!terminalToolsExpanded}
          >
            <div className="wbc-project-tool-expand-inner wbc-project-terminal-expand-inner">
              <div className={"wbc-project-terminal-list" + (terminalsLoading ? " is-loading" : "") + (menuId ? " menu-active" : "")}>
                {terminalsLoading && !orderedTerminals.length ? <div className="workbench-muted wbc-rail-loading" role="status">{wbcT("terminal.loading", "Loading terminals...")}</div> : null}
                {!terminalsLoading && !orderedTerminals.length ? <div className="workbench-muted wbc-rail-empty">{query ? wbcT("terminal.noMatches", "No matching terminals.") : wbcT("terminal.empty", "No terminals yet.")}</div> : null}
                {renderTerminalSection("pinned", wbcT("workbenchChat.pinnedSection", "Pinned"), pinnedTerminals)}
                {renderTerminalSection("recent", wbcT("workbenchChat.recent", "Recent"), recentTerminals)}
              </div>
            </div>
          </div>
        </section>
      ) : null}
      {pluginTools.length && railMode === "chat" && !collapsed ? (
        <section
          className="wbc-project-tools wbc-plugin-project-tools"
          aria-label={wbcT("rail.pluginTools", "Plugin tools")}
          onDragOver={function (event) {
            if (!pluginDragId || !wbcHasPluginViewDrag(event)) return;
            // Mirror the conversation list's fallback drop surface. Marking
            // the source region as a valid no-op target prevents Chromium on
            // macOS from playing its native invalid-drop return animation
            // before dragend when the user simply releases the card here.
            event.preventDefault();
            if (event.dataTransfer) event.dataTransfer.dropEffect = "move";
          }}
          onDrop={function (event) {
            if (!pluginDragId || !wbcHasPluginViewDrag(event)) return;
            event.preventDefault();
            setPluginDragId("");
          }}
        >
          <header><span>{wbcT("rail.pluginTools", "Plugin tools")}</span></header>
          {pluginTools.map(function (tool) {
            var packId = String(tool && tool.pack_id || "");
            var viewId = String(tool && tool.view || "");
            var title = pluginLocalizedField(tool, "title") || tool.id || packId;
            var subtitle = pluginLocalizedField(tool, "subtitle") || packId;
            var glyph = String(tool && (tool.icon_text || tool.iconText || tool.icon) || "").trim().slice(0, 2) || "◇";
            var toolKey = packId + ":" + String(tool && tool.id || viewId);
            var disabled = !packId || !viewId || !onOpenPluginView;
            var payload = {
              packId: packId,
              viewId: viewId,
              instanceId: String(tool && (tool.instance_id || tool.instanceId) || "default"),
              projectId: String(projectId || ""),
              title: title,
              subtitle: subtitle,
              state: !tool || tool.state == null ? null : tool.state,
            };
            return <div
              key={toolKey}
              data-plugin-tool-id={toolKey}
              role="button"
              tabIndex={disabled ? -1 : 0}
              draggable={disabled ? undefined : "true"}
              aria-disabled={disabled || undefined}
              aria-label={title}
              title={wbcT("workbenchChat.dragPluginView", "Drag to open {title} in a split view.", { title: title })}
              className={"wbc-chat-card wbc-project-plugin-tool" + (pluginDragId === toolKey ? " dragging" : "")}
              onClick={function () {
                if (disabled || pluginSuppressClickRef.current === toolKey) return;
                onOpenPluginView(payload);
              }}
              onKeyDown={function (event) {
                if (disabled || (event.key !== "Enter" && event.key !== " ")) return;
                event.preventDefault();
                onOpenPluginView(payload);
              }}
              onDragStart={function (event) {
                if (disabled) { event.preventDefault(); return; }
                pluginSuppressClickRef.current = toolKey;
                setPluginDragId(toolKey);
                wbcSetPluginViewDrag(event, payload);
                if (event.dataTransfer) {
                  prepareRailDragImage(event.currentTarget, event.dataTransfer, event.clientX, event.clientY);
                }
              }}
              onDragEnd={function () {
                clearRailDragImage();
                setPluginDragId("");
                window.setTimeout(function () {
                  if (pluginSuppressClickRef.current === toolKey) pluginSuppressClickRef.current = "";
                }, 0);
              }}
            >
              <span className="wbc-chat-card-top">
                <span className="wbc-chat-row-icon wbc-project-plugin-tool-icon" aria-hidden="true">{glyph}</span>
                <span className="wbc-chat-card-title">
                  <b><WbcHoverMarquee text={title} /></b>
                  <small className="wbc-project-plugin-tool-subtitle">{subtitle}</small>
                </span>
                <span className="wbc-project-tool-chevron" aria-hidden="true">{WBC_ICONS.chevronRight}</span>
              </span>
            </div>;
          })}
        </section>
      ) : null}
      {moduleDock}
      <WbcRenameDialog
        chat={renameChat}
        onClose={function () { setRenameChat(null); }}
        onRename={onRename}
      />
      <WbcRenameDialog
        chat={renameGroup}
        entity="group"
        onClose={function () { setRenameGroup(null); }}
        onRename={renameChatGroup}
      />
      <WbcRenameDialog
        chat={renameTerminalItem}
        entity="terminal"
        onClose={function () { setRenameTerminalItem(null); }}
        onRename={onRenameTerminal}
      />
    </aside>
  );
}

/* Shared host for module pages that need the exact Work rail without mounting
   the whole conversation workspace. The rail remains the single renderer for
   chats, unified search, project files, terminals, drag ordering, and
   menus; this host only supplies the terminal collection normally owned by
   WorkbenchChatPage. */
function WbcProjectRail(props) {
  props = props || {};
  var projectId = String(props.projectId || "");
  var dataStore = workbenchServices.data();
  dataStore.useVersion();
  var pluginModules = Array.isArray(dataStore.state.pluginModules)
    ? dataStore.state.pluginModules : [];
  var codeAvailable = pluginModules.indexOf("code") >= 0;
  var terminalModule = workbenchServices.terminal();
  var terminalClient = terminalModule.Client;
  var [terminals, setTerminals] = useWbcState([]);
  var [terminalsLoading, setTerminalsLoading] = useWbcState(false);
  var [activeTerminalId, setActiveTerminalId] = useWbcState("");

  function mergeTerminal(terminal) {
    if (!terminal || !terminal.id) return;
    setTerminals(function (current) {
      var found = false;
      var next = current.map(function (item) {
        if (String(item.id) !== String(terminal.id)) return item;
        found = true;
        return Object.assign({}, item, terminal);
      });
      return found ? next : [terminal].concat(next);
    });
  }

  function refreshTerminals(options) {
    if (!codeAvailable || !projectId) {
      setTerminals([]);
      setActiveTerminalId("");
      return Promise.resolve([]);
    }
    if (!(options && options.background)) setTerminalsLoading(true);
    return terminalClient.list(projectId).then(function (payload) {
      var items = Array.isArray(payload && payload.terminals) ? payload.terminals : [];
      setTerminals(items);
      var restoredId = String(payload && payload.activeTerminalId || "");
      if (restoredId && items.some(function (item) { return String(item.id) === restoredId; })) {
        setActiveTerminalId(restoredId);
      }
      return items;
    }).catch(function () {
      return [];
    }).finally(function () {
      if (!(options && options.background)) setTerminalsLoading(false);
    });
  }

  useWbcEffect(function () {
    setActiveTerminalId("");
    refreshTerminals();
  }, [projectId, codeAvailable]);

  useWbcEffect(function () {
    if (!codeAvailable || !projectId || props.active === false) return undefined;
    var timer = window.setInterval(function () {
      refreshTerminals({ background: true });
    }, 1500);
    return function () { window.clearInterval(timer); };
  }, [projectId, props.active, codeAvailable]);

  function openTerminal(terminalId, side) {
    var id = String(terminalId || "");
    if (!id) return;
    setActiveTerminalId(id);
    if (props.onOpenTerminal) props.onOpenTerminal(id, side);
  }

  function createTerminal() {
    if (!projectId) return Promise.resolve(null);
    setTerminalsLoading(true);
    return terminalClient.create(projectId).then(function (terminal) {
      mergeTerminal(terminal);
      if (terminal && terminal.id) openTerminal(terminal.id);
      return terminal;
    }).catch(function (error) {
      workbenchServices.feedback().showToast(wbcErrorText(error), "error");
      return null;
    }).finally(function () { setTerminalsLoading(false); });
  }

  function renameTerminal(terminalId, title) {
    return terminalClient.rename(terminalId, title).then(function (terminal) {
      mergeTerminal(terminal);
      return terminal;
    });
  }

  function updateTerminalLayout(order, pinned) {
    return terminalClient.layout(projectId, order, pinned).then(function (payload) {
      if (Array.isArray(payload && payload.terminals)) setTerminals(payload.terminals);
      return payload;
    });
  }

  function deleteTerminal(terminalId) {
    var terminal = terminals.find(function (item) { return String(item.id) === String(terminalId); });
    var feedback = workbenchServices.feedback();
    var request = feedback.confirmModal ? feedback.confirmModal({
      title: wbcT("terminal.deleteTitle", "Delete terminal"),
      body: wbcT("terminal.deleteBody", "This will stop the running process, cancel any pending Agent wake, and remove {title}.", { title: terminal && terminal.title || "Terminal" }),
      confirmLabel: wbcT("terminal.delete", "Delete terminal"),
      danger: true,
    }) : Promise.resolve(window.confirm(wbcT("terminal.deleteBody", "This will stop the running process and remove this terminal.")));
    return request.then(function (confirmed) {
      if (!confirmed) return null;
      return terminalClient.remove(terminalId);
    }).then(function (result) {
      if (!result) return null;
      setTerminals(function (current) { return current.filter(function (item) { return String(item.id) !== String(terminalId); }); });
      if (String(activeTerminalId) === String(terminalId)) setActiveTerminalId("");
      return result;
    });
  }

  return <WbcRail
    {...props}
    codeAvailable={codeAvailable}
    terminals={terminals}
    terminalsLoading={terminalsLoading}
    activeTerminalId={activeTerminalId}
    onOpenTerminal={openTerminal}
    onCreateTerminal={createTerminal}
    onRenameTerminal={renameTerminal}
    onDeleteTerminal={deleteTerminal}
    onUpdateTerminalLayout={updateTerminalLayout}
  />;
}

// ---------------------------------------------------------------------------
// Conversation main (column 3)
// ---------------------------------------------------------------------------

export { WbcHoverMarquee, WbcProjectRail, WbcRail, WbcRenameDialog, wbcProjectFileResource }
