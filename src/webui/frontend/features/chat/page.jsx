import { WBC_ICONS, WbcVoice, WorkbenchChatModel, useWbcEffect, useWbcLayoutEffect, useWbcRef, useWbcState, wbcAgentEventPayload, wbcBuildRailCardDragPreview, wbcCanOpenPageContextMenu, wbcCaptureConversationViewport, wbcChatCache, wbcChatDropReplacesActiveConversation, wbcChatSideDropZone, wbcChatSideZoneRect, wbcClampSideSplitWidth, wbcClampSideSplitWidthForPage, wbcClearModelOutputForRetry, wbcClonePaneWithLiveState, wbcConfirmOptimisticMessage, wbcDefaultPaneLayout, wbcErrorText, wbcEscapeHtml, wbcFileViewKind, wbcHasChatDrag, wbcHasResourceDrag, wbcHasSplitDrag, wbcHasTaskDrag, wbcHideNativeDragImage, wbcLastChatByProject, wbcLoadDraftAgentBinding, wbcMergeChronologicalMessages, wbcMergeSavedAssistantMessages, wbcNormalizePaneLayout, wbcNormalizePermissionMode, wbcNotifyBrowserWindowInteraction, wbcOpenAgentDetail, wbcPageContextMenuPlacement, wbcPaneCard, wbcPaneCardLocation, wbcPinPageSplitLayout, wbcPinSplitMotionOpen, wbcPlacePaneCard, wbcPreserveLiveTimelineAnchors, wbcReadChatDrag, wbcReadResourceDrag, wbcReadSplitDrag, wbcReadTaskDrag, wbcReleasePinnedPageSplitLayout, wbcReleasePinnedSplitMotion, wbcRestoreConversationViewport, wbcRetryTurnSelection, wbcSaveDraftAgentBinding, wbcSetSplitDrag, wbcSplitSideForDraggedConversation, wbcT } from "../../workbench-chat.jsx"
import { WBC_PROJECT_FILE_DRAFTS, WbcArtifactSplit, WbcArtifactSplitHost, WbcBrowserSplit, WbcBrowserSplitHost, WbcChangeSplit, WbcChangeSplitHost, WbcChatSplit, WbcChatSplitHost, WbcMapPaneContent, WbcMapSplitHost, WbcPaneCardFrame, WbcPaneColumnResizer, WbcPaneContextTrackDropSurface, WbcPaneRowResizer, WbcSide, WbcSideAgentSplit, WbcSideAgentSplitHost, WbcSplitGripBar, WbcSubagentsSplitHost, WbcSubagentsTab, wbcArtifactFileKey, wbcChatArtifactFiles, wbcDiscardProjectFileDraft, wbcEditableChatFileResource, wbcMapItemLabel, wbcProjectFileDraftKey } from "./split-pane.jsx"
import { WorkbenchChatRuntimes, wbcRuntimePresenceSnapshot, wbcSameRuntimePresence, wbcTaskSessionFromStore } from "./file-resources.jsx"
import { resolveRefreshedChatSelection as wbcResolveRefreshedChatSelection, settleChatListItem as wbcSettleChatListItem } from "./behavior.mjs"
import { WorkbenchFileDropOverlay, useWorkbenchFileDrop } from "../../workbench.jsx"
import { WbcRail, WbcRenameDialog, wbcProjectFileResource } from "./rail.jsx"
import { WbcMain, wbcIsLiveAgentRequest, wbcVoiceQuestionText } from "./conversation.jsx"
import { wbcBrowserStateForChat } from "./composer.jsx"
import { WbcQuickActionItems } from "./context-panel.jsx"

// Workbench chat feature module with explicit ESM dependencies.
function WorkbenchChatPage({ active, project, newChatRequestId, taskOpenRequest, tasks, activeTaskId, onSelectTask, onCreateTask, onDeleteTask, onRenameTask, onTaskStoreChange, TaskPaneComponent, TaskContextPanelComponent, onOpenTask, onActiveChatChange, onActiveChatIdChange, onChatsChange, pinnedChatIds, pinnedTaskIds, onTogglePinnedChat, onTogglePinnedTask, navCollapsed, onToggleNavCollapsed, collapseControl, moduleDock }) {
  window.CyreneUI.require("i18n").use();
  window.CyreneUI.require("data").useVersion();
  var isActive = active !== false;
  var model = WorkbenchChatModel;
  var projectId = project ? project.id : "";
  var terminalModule = window.CyreneUI.require("terminal");
  var terminalClient = terminalModule.Client;
  var [terminals, setTerminals] = useWbcState([]);
  var [terminalsLoading, setTerminalsLoading] = useWbcState(false);
  var [activeTerminalId, setActiveTerminalId] = useWbcState("");
  var [railMode, setRailMode] = useWbcState("chat");
  // Switching the rail is browsing, not navigation. Hide selection in the
  // newly opened list until the user explicitly chooses one of its cards.
  var [railSelectionSuppressed, setRailSelectionSuppressed] = useWbcState(false);
  var lastWorkRailModeRef = useWbcRef("chat");
  var chatCache = wbcChatCache();
  var [chats, setChats] = useWbcState([]);
  var chatsRef = useWbcRef([]);
  var chatsProjectIdRef = useWbcRef("");
  var chatListRequestSequenceRef = useWbcRef({});
  var [activeChatId, setActiveChatId] = useWbcState("");
  var activeChatIdRef = useWbcRef("");
  // The active conversation card is keyed by chat id and remounts after a
  // switch. Keep the trackpad gesture lock at page scope so inertial wheel
  // events from that same swipe cannot switch a second conversation.
  var horizontalSessionWheelRef = useWbcRef({
    delta: 0,
    direction: 0,
    lockedUntil: 0,
    lastEventAt: 0,
    waitingForIdle: false,
  });
  // A full-page task belongs to the project workspace, not to whichever chat
  // happened to be selected before it opened. Split tasks deliberately leave
  // this empty so they keep sharing the active conversation's pane layout.
  var activeTaskWorkspaceRef = useWbcRef("");
  var [taskPaneSessions, setTaskPaneSessions] = useWbcState({});
  var [taskRightTabs, setTaskRightTabs] = useWbcState({});
  // Draft Agent binding for a not-yet-created chat (handoff §8.3): the first
  // message's lazy createChat() submits this binding instead of creating a
  // default-Agent chat and immediately rebinding it.
  var [draftAgentBinding, setDraftAgentBinding] = useWbcState(function () {
    return wbcLoadDraftAgentBinding(projectId);
  });
  var draftAgentBindingRef = useWbcRef(draftAgentBinding);
  useWbcEffect(function () { draftAgentBindingRef.current = draftAgentBinding; }, [draftAgentBinding]);
  function handleDraftAgentChange(binding) {
    setDraftAgentBinding(binding || null);
    wbcSaveDraftAgentBinding(projectId, binding || null);
  }
  function handleSwitchAgent(binding) {
    if (!binding || !activeChat || !activeChat.id) return Promise.resolve(null);
    setError("");
    var hasMessages = (Array.isArray(activeChat.messages) && activeChat.messages.length > 0)
      || Number(activeChat.messageCount || 0) > 0;
    if (!hasMessages && model.updateChatAgent) {
      return model.updateChatAgent(activeChat.id, binding).then(function (chat) {
        setChats(function (prev) { return prev.map(function (item) { return item.id === chat.id ? chat : item; }); });
        setActiveChat(chat);
        return chat;
      }).catch(function (err) {
        setError(wbcErrorText(err));
        return null;
      });
    }
    var confirmModal = window.CyreneUI.require("feedback").confirmModal;
    var confirmation = confirmModal ? confirmModal({
      title: wbcT("workbenchChat.agentNewChatTitle", "Use in a new chat"),
      body: wbcT("workbenchChat.agentNewChatBody", "This conversation already has messages. The selected Agent will be used in a new chat."),
      confirmLabel: wbcT("workbenchChat.agentNewChatConfirm", "Use in new chat"),
    }) : Promise.resolve(window.confirm(wbcT("workbenchChat.agentNewChatBody", "This conversation already has messages. The selected Agent will be used in a new chat.")));
    return confirmation.then(function (confirmed) {
      if (!confirmed) return null;
      return model.createChatWithBinding(projectId, "", binding);
    }).then(function (chat) {
      if (!chat) return null;
      setChats(function (prev) { return [chat].concat(prev); });
      skipNextHydrationChatIdRef.current = chat.id;
      selectChat(chat.id);
      setActiveChat(chat);
      return chat;
    }).catch(function (err) {
      setError(wbcErrorText(err));
      return null;
    });
  }
  function handleOpenAgentDetail(agent) {
    wbcOpenAgentDetail(agent);
  }
  function selectChat(chatId) {
    var nextId = String(chatId || "");
    var previousId = String(activeChatIdRef.current || "");
    setRailSelectionSuppressed(false);
    activeTaskWorkspaceRef.current = "";
    lastWorkRailModeRef.current = "chat";
    setRailMode("chat");
    restoreTerminalReplacement(previousId);
    if (nextId !== previousId) restoreTerminalReplacement(nextId);
    if (nextId) {
      var nextLayout = paneLayoutFor(nextId);
      var nextCards = nextLayout.left.concat(nextLayout.right);
      // Repair layouts produced by older builds, which stored a full task as
      // the current chat's only card. Selecting the chat must restore its own
      // conversation rather than reopening that stale task.
      if (nextCards.length === 1 && nextCards[0].kind === "task") {
        updatePaneLayout(wbcDefaultPaneLayout(nextId), nextId);
      }
    }
    // Publish selection intent immediately. Passive effects run too late to
    // protect a newly-created chat from an already in-flight list refresh.
    activeChatIdRef.current = nextId;
    setActiveChatId(nextId);
  }
  var [activeChat, setActiveChat] = useWbcState(null);
  var [loading, setLoading] = useWbcState(true);
  var [chatLoading, setChatLoading] = useWbcState(false);
  var [loadRevision, setLoadRevision] = useWbcState(0);
  var projectIdRef = useWbcRef(projectId);
  // This page stays mounted while the user switches projects. Event and
  // navigation listeners are intentionally registered once, so publish the
  // latest project synchronously on every render instead of leaving those
  // long-lived callbacks with the project captured on their first render.
  projectIdRef.current = projectId;
  // POST /chats and /fork already return the complete conversation. Mark the
  // adopted id so the selection effect does not clear it and fetch it again.
  var skipNextHydrationChatIdRef = useWbcRef("");
  var chatHydrationSequenceRef = useWbcRef({});
  var handledNewChatRequestIdRef = useWbcRef(0);
  var pendingTerminalRestoreRef = useWbcRef({ projectId: "", terminalId: "" });
  var [terminalRestoreRevision, setTerminalRestoreRevision] = useWbcState(0);

  function refreshTerminals(options) {
    if (!projectId) {
      setTerminals([]);
      return Promise.resolve([]);
    }
    if (!(options && options.background)) setTerminalsLoading(true);
    return terminalClient.list(projectId).then(function (payload) {
      var items = Array.isArray(payload && payload.terminals) ? payload.terminals : [];
      setTerminals(items);
      var restoredId = String(payload && payload.activeTerminalId || "");
      if (!(options && options.skipRestore) && restoredId && items.some(function (item) { return String(item.id) === restoredId; })) {
        pendingTerminalRestoreRef.current = {
          projectId: String(projectId),
          terminalId: restoredId,
        };
        setTerminalRestoreRevision(function (revision) { return revision + 1; });
      }
      return items;
    }).catch(function (err) {
      if (!(options && options.background)) {
        window.CyreneUI.require("feedback").showToast(wbcErrorText(err), "error");
      }
      return [];
    }).finally(function () {
      if (!(options && options.background)) setTerminalsLoading(false);
    });
  }

  useWbcEffect(function () {
    setActiveTerminalId("");
    setRailSelectionSuppressed(false);
    activeTaskWorkspaceRef.current = "";
    lastWorkRailModeRef.current = "chat";
    setRailMode("chat");
    pendingTerminalRestoreRef.current = { projectId: "", terminalId: "" };
    refreshTerminals();
  }, [projectId]);

  useWbcEffect(function () {
    if (!projectId || !isActive) return undefined;
    var timer = window.setInterval(function () {
      refreshTerminals({ background: true, skipRestore: true });
    }, 1500);
    return function () { window.clearInterval(timer); };
  }, [projectId, isActive]);

  useWbcEffect(function () {
    var pending = pendingTerminalRestoreRef.current;
    if (
      loading
      || !pending.terminalId
      || String(pending.projectId) !== String(projectId)
    ) return;
    pendingTerminalRestoreRef.current = { projectId: "", terminalId: "" };
    setRailMode(lastWorkRailModeRef.current === "task" ? "task" : "chat");
    replaceWithTerminal(pending.terminalId, {
      skipPersist: true,
      ownerChatId: String(activeChatId || ""),
    });
  }, [projectId, activeChatId, loading, terminalRestoreRevision]);

  function updateTerminalSummary(terminal) {
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

  function openTerminal(terminalId, side) {
    var id = String(terminalId || "");
    if (!id) return;
    if (side !== "left" && side !== "right") {
      replaceWithTerminal(id);
      return;
    }
    setActiveTerminalId(id);
    terminalClient.activate(projectId, id).catch(function () {});
    openPaneContent("terminal", id, { side: side });
  }

  function showAgentTerminal(terminalId, preferredSide) {
    var id = String(terminalId || "");
    if (!id) return null;
    var ownerChatId = String(activeChatIdRef.current || "");
    var layout = paneLayoutFor(ownerChatId);
    var existing = wbcPaneCardLocation(layout, "terminal:" + id);
    setActiveTerminalId(id);
    terminalClient.activate(projectId, id).catch(function () {});
    if (existing) return existing.card;
    var card = paneContentCard("terminal", id, ownerChatId);
    var requestedSide = preferredSide === "left" ? "left" : "right";
    updatePaneLayout(function (current) {
      var next = {
        left: current.left.slice(),
        right: current.right.slice(),
        leftRatio: current.leftRatio,
        rightRatio: current.rightRatio,
      };
      var count = next.left.length + next.right.length;
      if (count <= 1) {
        // Agent display is always a split operation. If the only current card
        // already occupies the preferred side, put the terminal opposite it.
        var occupiedSide = next.left.length ? "left" : (next.right.length ? "right" : "");
        var targetSide = occupiedSide === requestedSide
          ? (requestedSide === "left" ? "right" : "left")
          : requestedSide;
        next[targetSide] = [card];
        return next;
      }
      // Once the workspace is already split, replace exactly one card on the
      // preferred side. This keeps the pane count and the other user's view
      // stable instead of creating a third pane or replacing the workspace.
      var replaceSide = next[requestedSide].length
        ? requestedSide
        : (requestedSide === "left" ? "right" : "left");
      var replaceIndex = Math.max(0, next[replaceSide].length - 1);
      next[replaceSide][replaceIndex] = card;
      return next;
    }, ownerChatId);
    return card;
  }

  function replaceWithTerminal(terminalId, options) {
    var id = String(terminalId || "");
    if (!id) return;
    var ownerChatId = String(
      options && options.ownerChatId != null
        ? options.ownerChatId
        : (activeChatIdRef.current || "")
    );
    var currentLayout = paneLayoutFor(ownerChatId);
    var currentCards = currentLayout.left.concat(currentLayout.right);
    var replacedTerminal = currentCards.length === 1 && currentCards[0].kind === "terminal"
      ? currentCards[0]
      : null;
    setActiveTerminalId(id);
    if (!(options && options.skipPersist)) terminalClient.activate(projectId, id).catch(function () {});
    if (replacedTerminal && String(replacedTerminal.payload || "") === id) return;
    var restoreLayout = replacedTerminal
      && Object.prototype.hasOwnProperty.call(paneLayoutRestoreRef.current, replacedTerminal.id)
      ? paneLayoutRestoreRef.current[replacedTerminal.id]
      : currentLayout;
    if (replacedTerminal) delete paneLayoutRestoreRef.current[replacedTerminal.id];
    openPaneContent("terminal", id, {
      replaceWorkspace: true,
      restore: true,
      restoreLayout: restoreLayout,
      ownerChatId: ownerChatId,
    });
  }

  function createTerminal() {
    setTerminalsLoading(true);
    return terminalClient.create(projectId).then(function (terminal) {
      updateTerminalSummary(terminal);
      replaceWithTerminal(terminal.id);
      return terminal;
    }).catch(function (err) {
      window.CyreneUI.require("feedback").showToast(wbcErrorText(err), "error");
      return null;
    }).finally(function () { setTerminalsLoading(false); });
  }

  function renameTerminal(terminalId, title) {
    return terminalClient.rename(terminalId, title).then(function (terminal) {
      updateTerminalSummary(terminal);
      return terminal;
    });
  }

  function updateTerminalLayout(order, pinned) {
    return terminalClient.layout(projectId, order, pinned).then(function (payload) {
      if (Array.isArray(payload && payload.terminals)) setTerminals(payload.terminals);
      return payload;
    }).catch(function (err) {
      window.CyreneUI.require("feedback").showToast(wbcErrorText(err), "error");
      return null;
    });
  }

  function deleteTerminal(terminalId) {
    var terminal = terminals.find(function (item) { return String(item.id) === String(terminalId); });
    var feedback = window.CyreneUI.require("feedback");
    var confirmation = feedback.confirmModal ? feedback.confirmModal({
      title: wbcT("terminal.deleteTitle", "Delete terminal"),
      body: wbcT("terminal.deleteBody", "This will stop the running process, cancel any pending Agent wake, and remove {title}.", { title: terminal && terminal.title || "Terminal" }),
      confirmLabel: wbcT("terminal.delete", "Delete terminal"),
      danger: true,
    }) : Promise.resolve(window.confirm(wbcT("terminal.deleteBody", "This will stop the running process and remove this terminal.")));
    return confirmation.then(function (confirmed) {
      if (!confirmed) return null;
      return terminalClient.remove(terminalId);
    }).then(function (result) {
      if (!result) return null;
      setTerminals(function (current) { return current.filter(function (item) { return String(item.id) !== String(terminalId); }); });
      setActiveTerminalId(function (current) { return String(current) === String(terminalId) ? "" : current; });
      setPaneLayoutsByChat(function (current) {
        var next = {};
        Object.keys(current).forEach(function (ownerId) {
          var layout = wbcNormalizePaneLayout(current[ownerId], ownerId.indexOf("project:") === 0 ? "" : ownerId);
          next[ownerId] = Object.assign({}, layout, {
            left: layout.left.filter(function (card) { return !(card.kind === "terminal" && String(card.payload) === String(terminalId)); }),
            right: layout.right.filter(function (card) { return !(card.kind === "terminal" && String(card.payload) === String(terminalId)); }),
          });
        });
        return next;
      });
      return result;
    });
  }

  function beginChatHydration(chatId) {
    var id = String(chatId || "");
    var next = Number(chatHydrationSequenceRef.current[id] || 0) + 1;
    chatHydrationSequenceRef.current[id] = next;
    return next;
  }

  function isCurrentChatHydration(chatId, sequence) {
    return Number(chatHydrationSequenceRef.current[String(chatId || "")] || 0) === sequence;
  }

  function beginChatListRequest(requestedProjectId) {
    var id = String(requestedProjectId || "");
    var next = Number(chatListRequestSequenceRef.current[id] || 0) + 1;
    chatListRequestSequenceRef.current[id] = next;
    return next;
  }

  function isCurrentChatListRequest(requestedProjectId, sequence) {
    return Number(chatListRequestSequenceRef.current[String(requestedProjectId || "")] || 0) === sequence;
  }

  useWbcEffect(function () {
    chatsRef.current = chats;
    if (
      projectId
      && chatsProjectIdRef.current === projectId
      && Array.isArray(chats)
      && chats.every(function (chat) { return String((chat && chat.projectId) || "") === String(projectId); })
    ) {
      chatCache.lists[projectId] = chats;
    }
    if (onChatsChange && projectId) onChatsChange(projectId, chats);
  }, [chats]);
  useWbcEffect(function () {
    if (
      activeChat
      && activeChat.id
      && String(activeChat.projectId || "") === String(projectId)
    ) {
      chatCache.details[activeChat.id] = activeChat;
    }
  }, [activeChat]);
  useWbcEffect(function () {
    activeChatIdRef.current = activeChatId;
    // Remember the open conversation per project so switching to another module
    // and back restores it (rather than snapping to the most-recent chat) — key
    // for not "losing" a conversation whose run is streaming in the background.
    if (activeChatId) {
      wbcLastChatByProject[projectId] = activeChatId;
    }
  }, [activeChatId]);
  var [error, setError] = useWbcState("");
  var [errorKind, setErrorKind] = useWbcState("load");
  var [retryClearingMessageIds, setRetryClearingMessageIds] = useWbcState([]);
  var [retrySuppressedTurn, setRetrySuppressedTurn] = useWbcState({ chatId: "", messageIds: [] });
  var retrySuppressedTurnRef = useWbcRef({ chatId: "", messageIds: [] });
  var retryClearCommitRef = useWbcRef(null);
  var retryPendingChatIdRef = useWbcRef("");
  useWbcEffect(function () {
    return function () {
      retryClearCommitRef.current = null;
    };
  }, []);
  // Which side of the conversation the detail split anchors to. Global across
  // chats (like the split width) so the choice survives conversation switches.
  var [splitSide, setSplitSide] = useWbcState(function () {
    try {
      return localStorage.getItem("wbc-split-side") === "left" ? "left" : "right";
    } catch (e) {
      return "right";
    }
  });
  function toggleSplitSide() {
    setSplitSide(function (current) {
      var next = current === "left" ? "right" : "left";
      try { localStorage.setItem("wbc-split-side", next); } catch (e) {}
      return next;
    });
  }
  // Idempotent setter used by the grip drag so moving the pointer across the
  // window midline follows the split live without toggling on every move.
  function setSplitSideDirect(next) {
    setSplitSide(function (current) {
      if (current === next) return current;
      try { localStorage.setItem("wbc-split-side", next); } catch (e) {}
      return next;
    });
  }
  var [paneLayoutsByChat, setPaneLayoutsByChat] = useWbcState({});

  useWbcEffect(function () {
    var bridge = {
      current: function () {
        var visible = [];
        var panes = document.querySelectorAll(".wbc-terminal-pane[data-terminal-id]");
        Array.prototype.forEach.call(panes, function (pane) {
          var rect = pane.getBoundingClientRect();
          var style = window.getComputedStyle(pane);
          if (rect.width <= 0 || rect.height <= 0 || style.display === "none" || style.visibility === "hidden") return;
          var id = String(pane.getAttribute("data-terminal-id") || "");
          if (!id || visible.some(function (item) { return item.terminalId === id; })) return;
          var terminal = terminals.find(function (item) { return String(item.id || "") === id; });
          visible.push({
            terminalId: id,
            title: String(terminal && terminal.title || "Terminal"),
            side: rect.left + rect.width / 2 <= window.innerWidth / 2 ? "left" : "right",
            left: rect.left,
          });
        });
        visible.sort(function (a, b) { return Number(a.left || 0) - Number(b.left || 0); });
        if (visible.length > 1) {
          return { ok: false, error: "multiple_terminals_visible", terminals: visible };
        }
        return {
          ok: true,
          terminalId: visible[0] ? visible[0].terminalId : "",
          terminals: visible,
        };
      },
      show: function (terminalId, side) {
        var id = String(terminalId || "");
        if (!id || !terminals.some(function (item) { return String(item.id) === id; })) {
          return { ok: false, error: "terminal_not_found" };
        }
        var requestedSide = side === "left" ? "left" : "right";
        showAgentTerminal(id, requestedSide);
        return { ok: true, terminalId: id, mode: "split", preferredSide: requestedSide };
      },
    };
    window.CyreneTerminalSurface = bridge;
    return function () {
      if (window.CyreneTerminalSurface === bridge) delete window.CyreneTerminalSurface;
    };
  }, [activeChatId, paneLayoutsByChat, terminals]);
  var paneLayoutRestoreRef = useWbcRef({});
  var paneCardDragImageCleanupRef = useWbcRef(null);
  var paneCardDetachRef = useWbcRef(null);
  var [paneDropTarget, setPaneDropTarget] = useWbcState(null);
  var [paneCardDragId, setPaneCardDragId] = useWbcState("");
  var [resourceDragSession, setResourceDragSession] = useWbcState(false);
  var [detachedPaneReturnHover, setDetachedPaneReturnHover] = useWbcState(false);

  function projectPaneOwnerKey() {
    return projectId ? "project:" + String(projectId) : "";
  }

  function paneOwnerKey(chatId) {
    return String(chatId || activeChatIdRef.current || projectPaneOwnerKey());
  }

  function paneLayoutFor(chatId) {
    var ownerChatId = String(chatId || activeChatIdRef.current || "");
    var ownerId = paneOwnerKey(ownerChatId);
    return wbcNormalizePaneLayout(paneLayoutsByChat[ownerId], ownerChatId);
  }

  function restoreTerminalReplacement(ownerChatId) {
    var normalizedChatId = String(ownerChatId || "");
    var layout = paneLayoutFor(normalizedChatId);
    var cards = layout.left.concat(layout.right);
    if (cards.length !== 1 || cards[0].kind !== "terminal") return false;
    var terminalCard = cards[0];
    var restore = paneLayoutRestoreRef.current[terminalCard.id];
    if (!restore) return false;
    delete paneLayoutRestoreRef.current[terminalCard.id];
    updatePaneLayout(restore, normalizedChatId);
    setActiveTerminalId("");
    terminalClient.activate(projectId, null).catch(function () {});
    return true;
  }

  function updatePaneLayout(updater, ownerChatId) {
    var normalizedChatId = String(ownerChatId || activeChatIdRef.current || "");
    var ownerId = paneOwnerKey(normalizedChatId);
    if (!ownerId) return;
    setPaneLayoutsByChat(function (current) {
      var previous = wbcNormalizePaneLayout(current[ownerId], normalizedChatId);
      var updated = typeof updater === "function" ? updater(previous) : updater;
      return Object.assign({}, current, { [ownerId]: wbcNormalizePaneLayout(updated, normalizedChatId) });
    });
  }

  function paneContentCard(type, payload, ownerChatId) {
    var normalizedType = type === "artifact" ? "file" : String(type || "");
    return wbcPaneCard(normalizedType, payload, {
      ownerChatId: ownerChatId || activeChatIdRef.current || projectPaneOwnerKey(),
    });
  }

  function openPaneContent(type, payload, options) {
    var opts = options || {};
    var ownerChatId = String(opts.ownerChatId || activeChatIdRef.current || "");
    var ownerId = paneOwnerKey(ownerChatId);
    if (!ownerId || !type) return null;
    var normalizedType = type === "artifact" ? "file" : String(type || "");
    if (normalizedType === "file" || normalizedType === "viewer") {
      payload = wbcEditableChatFileResource({ projectId: projectId }, payload);
    }
    var canonicalCardId = (normalizedType === "chat" || normalizedType === "terminal" || normalizedType === "task") && payload
      ? normalizedType + ":" + String(payload)
      : "";
    var existingChatCard = canonicalCardId
      ? wbcPaneCardLocation(paneLayoutFor(ownerChatId), canonicalCardId)
      : null;
    if (normalizedType === "terminal" && existingChatCard && !opts.replaceWorkspace) {
      return existingChatCard.card;
    }
    // Opening the conversation that is already visible creates a second view,
    // not a second card with the same DOM/state identity. Drop highlighting is
    // keyed by card id, so duplicate canonical ids made both panes display the
    // same target prompt at once.
    var card = existingChatCard
      ? (normalizedType === "chat"
        ? wbcPaneCard("chat", payload, { ownerChatId: ownerId, freshInstance: true })
        : wbcPaneCard(normalizedType, payload, { ownerChatId: ownerId, freshInstance: true }))
      : paneContentCard(normalizedType, payload, ownerId);
    updatePaneLayout(function (layout) {
      var source = opts.sourceCardId ? wbcPaneCardLocation(layout, opts.sourceCardId) : null;
      var targetSide = opts.side === "left" || opts.side === "right"
        ? opts.side
        : (source && source.side === "right" ? "left" : "right");
      if (opts.restore) paneLayoutRestoreRef.current[card.id] = layout;
      if (opts.restore && Object.prototype.hasOwnProperty.call(opts, "restoreLayout")) {
        paneLayoutRestoreRef.current[card.id] = opts.restoreLayout;
      }
      var next = {
        left: layout.left.slice(),
        right: layout.right.slice(),
        leftRatio: layout.leftRatio,
        rightRatio: layout.rightRatio,
      };
      if (opts.replaceWorkspace) {
        next.left = [card];
        next.right = [];
        return next;
      }
      if (opts.promoteSourceLeft && source) {
        next.left = [source.card];
        next.right = [card];
        return next;
      }
      // Opening from the conversation panel follows the established detail
      // split contract: the selected content replaces the opposite column.
      next[targetSide] = [card];
      return next;
    }, ownerChatId);
    return card;
  }

  function openTaskWorkspace(taskId) {
    var id = String(taskId || "");
    if (!id) return null;
    setRailSelectionSuppressed(false);
    activeTaskWorkspaceRef.current = id;
    lastWorkRailModeRef.current = "task";
    setRailMode("task");
    // Clear the conversation selection synchronously so subsequent pane work
    // cannot inherit the previous chat as its owner during React's batched
    // state update. The task card is stored under the project owner instead.
    activeChatIdRef.current = "";
    setActiveChatId("");
    setActiveChat(null);
    if (onSelectTask) onSelectTask(id);
    return openPaneContent("task", id, {
      replaceWorkspace: true,
      ownerChatId: projectPaneOwnerKey(),
    });
  }

  var handledTaskOpenSequenceRef = useWbcRef(0);
  useWbcEffect(function () {
    var request = taskOpenRequest && typeof taskOpenRequest === "object" ? taskOpenRequest : {};
    var sequence = Number(request.sequence || 0);
    var taskId = String(request.id || "");
    if (!sequence || !taskId || handledTaskOpenSequenceRef.current === sequence) return;
    handledTaskOpenSequenceRef.current = sequence;
    openTaskWorkspace(taskId);
  }, [taskOpenRequest && taskOpenRequest.sequence, projectId]);

  function updatePaneCard(cardId, updater) {
    updatePaneLayout(function (layout) {
      var next = {
        left: layout.left.slice(),
        right: layout.right.slice(),
        leftRatio: layout.leftRatio,
        rightRatio: layout.rightRatio,
      };
      var location = wbcPaneCardLocation(next, cardId);
      if (!location) return next;
      next[location.side][location.index] = typeof updater === "function"
        ? updater(location.card)
        : updater;
      return next;
    });
  }

  function closePaneCard(cardId, requestedOwnerChatId) {
    var ownerChatId = String(requestedOwnerChatId != null ? requestedOwnerChatId : (activeChatIdRef.current || ""));
    var ownerId = paneOwnerKey(ownerChatId);
    var restore = paneLayoutRestoreRef.current[cardId];
    if (restore) {
      delete paneLayoutRestoreRef.current[cardId];
      updatePaneLayout(restore, ownerChatId);
      return;
    }
    var layout = paneLayoutFor(ownerChatId);
    var location = wbcPaneCardLocation(layout, cardId);
    if (!location) return;
    var closingCard = location.card;
    var remaining = layout.left.concat(layout.right).filter(function (card) {
      return String(card.id) !== String(cardId);
    });
    var nextChat = closingCard.kind === "chat" && String(closingCard.payload || "") === ownerChatId
      ? remaining.find(function (card) { return card.kind === "chat"; })
      : null;
    var next = {
      left: layout.left.filter(function (card) { return String(card.id) !== String(cardId); }),
      right: layout.right.filter(function (card) { return String(card.id) !== String(cardId); }),
      leftRatio: layout.leftRatio,
      rightRatio: layout.rightRatio,
    };
    if (!next.left.length && next.right.length) {
      next.left = next.right;
      next.right = [];
    }
    if (!next.left.length && !next.right.length) next = wbcDefaultPaneLayout(ownerChatId);
    if (nextChat && nextChat.payload) {
      setPaneLayoutsByChat(function (current) {
        var updated = Object.assign({}, current);
        updated[String(nextChat.payload)] = next;
        delete updated[ownerId];
        return updated;
      });
      if (String(activeChatIdRef.current || "") === ownerChatId) selectChat(String(nextChat.payload));
      return;
    }
    updatePaneLayout(next, ownerChatId);
  }

  function closeDeletedChatSplits(chatId) {
    var deletedChatId = String(chatId || "");
    if (!deletedChatId) return;
    var detachedBridge = window.cyrene && window.cyrene.detachedPane;
    if (detachedBridge && typeof detachedBridge.closeByChat === "function") {
      detachedBridge.closeByChat(deletedChatId).catch(function () {});
    }

    setPaneLayoutsByChat(function (current) {
      var updated = Object.assign({}, current);
      var changed = false;
      Object.keys(current).forEach(function (ownerId) {
        if (String(ownerId) === deletedChatId) {
          delete updated[ownerId];
          changed = true;
          return;
        }
        var ownerChatId = String(ownerId).indexOf("project:") === 0 ? "" : String(ownerId);
        var layout = wbcNormalizePaneLayout(current[ownerId], ownerChatId);
        var left = layout.left.filter(function (card) {
          return !(card && card.kind === "chat" && String(card.payload || "") === deletedChatId);
        });
        var right = layout.right.filter(function (card) {
          return !(card && card.kind === "chat" && String(card.payload || "") === deletedChatId);
        });
        if (left.length === layout.left.length && right.length === layout.right.length) return;
        if (!left.length && right.length) {
          left = right;
          right = [];
        }
        updated[ownerId] = wbcNormalizePaneLayout({
          left: left,
          right: right,
          leftRatio: layout.leftRatio,
          rightRatio: layout.rightRatio,
        }, ownerChatId);
        changed = true;
      });
      return changed ? updated : current;
    });

    setResourceSplitByChat(function (current) {
      var updated = Object.assign({}, current);
      var changed = false;
      Object.keys(current).forEach(function (ownerId) {
        var resource = current[ownerId];
        if (
          String(ownerId) === deletedChatId
          || (resource && resource.type === "chat" && String(resource.payload || "") === deletedChatId)
        ) {
          delete updated[ownerId];
          changed = true;
        }
      });
      return changed ? updated : current;
    });

    Object.keys(paneLayoutRestoreRef.current).forEach(function (cardId) {
      var restore = paneLayoutRestoreRef.current[cardId];
      var cards = restore && (restore.left || []).concat(restore.right || []);
      if (cards && cards.some(function (card) {
        return card && card.kind === "chat" && String(card.payload || "") === deletedChatId;
      })) {
        delete paneLayoutRestoreRef.current[cardId];
      }
    });
    var floatingRestore = floatingSplitRestoreRef.current;
    if (floatingRestore && (
      String(floatingRestore.chatId || "") === deletedChatId
      || String(floatingRestore.activeChatId || "") === deletedChatId
    )) {
      floatingSplitRestoreRef.current = null;
    }
  }

  function clearPaneCardDetachSubscription(pendingDetach) {
    if (!pendingDetach || typeof pendingDetach.unsubscribeCreated !== "function") return;
    pendingDetach.unsubscribeCreated();
    pendingDetach.unsubscribeCreated = null;
  }

  function cancelPaneCardDetachment(pendingDetach) {
    clearPaneCardDetachSubscription(pendingDetach);
    if (paneCardDetachRef.current === pendingDetach) paneCardDetachRef.current = null;
    if (paneCardDragImageCleanupRef.current) paneCardDragImageCleanupRef.current();
    setPaneCardDragId("");
    setPaneDropTarget(null);
  }

  function paneCardDetachIpcPayload(pendingDetach, extra) {
    if (!pendingDetach) return Object.assign({}, extra || {});
    // pendingDetach also owns renderer-only lifecycle fields such as the
    // unsubscribe callback. Functions cannot cross Electron's structured
    // clone boundary, so never send the mutable frontend object itself.
    return Object.assign({
      cardId: String(pendingDetach.cardId || ""),
      layoutOwnerChatId: String(pendingDetach.layoutOwnerChatId || ""),
      descriptor: pendingDetach.descriptor,
      sourceSide: pendingDetach.sourceSide,
      sourceIndex: pendingDetach.sourceIndex,
      sourceBounds: pendingDetach.sourceBounds,
      grabOffset: pendingDetach.grabOffset,
    }, extra || {});
  }

  function selectAlternateChatAfterDetachment(pendingDetach) {
    var ownerChatId = String(pendingDetach && pendingDetach.layoutOwnerChatId || "");
    var descriptor = pendingDetach && pendingDetach.descriptor;
    var detachedChatId = String(descriptor && descriptor.kind === "chat" ? descriptor.payload || "" : "");
    var canonicalCardId = detachedChatId ? "chat:" + detachedChatId : "";
    if (
      !detachedChatId
      || detachedChatId !== ownerChatId
      || String(pendingDetach && pendingDetach.cardId || "") !== canonicalCardId
      || String(activeChatIdRef.current || "") !== ownerChatId
    ) return;

    // A remaining conversation in the same pane layout is selected by
    // closePaneCard. Only navigate to the recent list when the card being
    // detached is the sole main conversation; otherwise detaching a secondary
    // split must not unexpectedly change the user's active conversation.
    var layout = paneLayoutFor(ownerChatId);
    var remainingPaneChat = layout.left.concat(layout.right).find(function (card) {
      return card
        && card.kind === "chat"
        && String(card.id || "") !== String(pendingDetach.cardId || "");
    });
    if (remainingPaneChat) return;
    var alternate = (chatsRef.current || []).find(function (chat) {
      var candidateId = String(chat && chat.id || "");
      return candidateId && candidateId !== detachedChatId;
    });
    if (alternate && alternate.id) selectChat(String(alternate.id));
  }

  function removeDetachedPaneCard(pendingDetach) {
    selectAlternateChatAfterDetachment(pendingDetach);
    closePaneCard(pendingDetach.cardId, pendingDetach.layoutOwnerChatId);
  }

  function completePaneCardDetachment(pendingDetach) {
    if (!pendingDetach || pendingDetach.completed) return;
    pendingDetach.completed = true;
    clearPaneCardDetachSubscription(pendingDetach);
    if (paneCardDetachRef.current === pendingDetach) paneCardDetachRef.current = null;
    if (paneCardDragImageCleanupRef.current) paneCardDragImageCleanupRef.current();
    setPaneCardDragId("");
    setPaneDropTarget(null);
    // Removal is deliberately delayed until the native window has loaded.
    // Closing the source card collapses any former horizontal/vertical split
    // back to the remaining single card in the same atomic layout update.
    var sourceCard = Array.prototype.slice.call(document.querySelectorAll(".wbc-pane-card")).find(function (candidate) {
      return String(candidate.dataset.paneCardId || "") === String(pendingDetach.cardId || "");
    });
    var reducedMotion = !!(window.matchMedia
      && window.matchMedia("(prefers-reduced-motion: reduce)").matches);
    if (!sourceCard || reducedMotion || typeof sourceCard.animate !== "function") {
      removeDetachedPaneCard(pendingDetach);
      return;
    }
    var animation = sourceCard.animate([
      { opacity: 1, transform: "scale(1) translate3d(0, 0, 0)" },
      { opacity: 0.82, transform: "scale(.985) translate3d(0, 2px, 0)", offset: 0.66 },
      { opacity: 0, transform: "scale(.965) translate3d(0, -6px, 0)" },
    ], {
      duration: 190,
      easing: "cubic-bezier(.22, 1.12, .36, 1)",
      fill: "forwards",
    });
    Promise.resolve(animation.finished).catch(function () {}).then(function () {
      removeDetachedPaneCard(pendingDetach);
    });
  }

  function restoreReturnedDetachedPane(info) {
    var returned = info && typeof info === "object" ? info : {};
    var descriptor = returned.descriptor && typeof returned.descriptor === "object"
      ? returned.descriptor
      : null;
    var cardId = String(returned.cardId || "");
    var ownerChatId = String(returned.layoutOwnerChatId || descriptor && descriptor.ownerChatId || "");
    if (!descriptor || !descriptor.kind || !cardId) return;
    var restoredCard = wbcPaneCard(descriptor.kind, descriptor.payload, {
      id: cardId,
      ownerChatId: descriptor.ownerChatId || ownerChatId,
    });
    var side = returned.sourceSide === "right" ? "right" : "left";
    var index = Math.max(0, Math.min(1, Number(returned.sourceIndex) || 0));
    updatePaneLayout(function (current) {
      if (wbcPaneCardLocation(current, cardId)) return current;
      var next = {
        left: current.left.slice(),
        right: current.right.slice(),
        leftRatio: current.leftRatio,
        rightRatio: current.rightRatio,
      };
      if (next[side].length < 2) next[side].splice(Math.min(index, next[side].length), 0, restoredCard);
      else next[side][index] = restoredCard;
      return next;
    }, ownerChatId);
    if (descriptor.kind === "chat" && descriptor.payload) {
      selectChat(String(descriptor.payload));
    }
  }

  useWbcEffect(function () {
    var detachedBridge = window.cyrene && window.cyrene.detachedPane;
    if (!detachedBridge || typeof detachedBridge.onReturned !== "function") return undefined;
    var unsubscribeReturned = detachedBridge.onReturned(restoreReturnedDetachedPane);
    var unsubscribeHover = typeof detachedBridge.onReturnHover === "function"
      ? detachedBridge.onReturnHover(setDetachedPaneReturnHover)
      : null;
    return function () {
      if (typeof unsubscribeReturned === "function") unsubscribeReturned();
      if (typeof unsubscribeHover === "function") unsubscribeHover();
    };
  }, [projectId]);

  function movePaneCardOtherSide(cardId) {
    updatePaneLayout(function (layout) {
      var location = wbcPaneCardLocation(layout, cardId);
      if (!location) return layout;
      var next = {
        left: layout.left.slice(),
        right: layout.right.slice(),
        leftRatio: layout.leftRatio,
        rightRatio: layout.rightRatio,
      };
      if (next[location.side].length === 2) {
        next[location.side].reverse();
      } else {
        var left = next.left;
        next.left = next.right;
        next.right = left;
      }
      return next;
    });
  }

  function createPaneConversation(cardId) {
    var layout = paneLayoutFor();
    var location = wbcPaneCardLocation(layout, cardId);
    if (!location || layout[location.side].length > 1 || !projectId) return Promise.resolve(null);
    return model.createChat(projectId).then(function (chat) {
      chatCache.details[chat.id] = chat;
      setChats(function (previous) { return [chat].concat(previous); });
      updatePaneLayout(function (current) {
        var liveLocation = wbcPaneCardLocation(current, cardId);
        if (!liveLocation || current[liveLocation.side].length > 1) return current;
        var next = {
          left: current.left.slice(),
          right: current.right.slice(),
          leftRatio: current.leftRatio,
          rightRatio: current.rightRatio,
        };
        next[liveLocation.side] = [
          liveLocation.card,
          wbcPaneCard("chat", chat.id, { id: "chat:" + chat.id, ownerChatId: chat.id }),
        ];
        return next;
      });
      return chat;
    }).catch(function (err) {
      setErrorKind("message");
      setError(wbcErrorText(err));
      return null;
    });
  }

  function resizePaneRow(side, ratio) {
    var normalizedSide = side === "left" ? "left" : "right";
    var nextRatio = Math.max(0.2, Math.min(0.8, Number(ratio) || 0.5));
    try { localStorage.setItem("wbc-pane-" + normalizedSide + "-height", String(nextRatio)); } catch (e) {}
    updatePaneLayout(function (layout) {
      return Object.assign({}, layout, {
        [normalizedSide + "Ratio"]: nextRatio,
      });
    });
  }
  // The split panel is lifted with a native drag, same as images/documents:
  // the panel stays in place, a drag ghost follows the pointer (shrinking to
  // a chat card over the rail), and the drop zones (rail = close, main
  // left/right half = anchored side) light up under the cursor. The ghost and
  // zones are created as raw DOM during the drag session — never through
  // React — so the drag source's DOM stays untouched while Chromium is
  // tracking the gesture (any React re-render here cancels the drag).
  var splitOverlayCleanupRef = useWbcRef(null);

  function handleSplitDragStart(event, dragSource) {
    var transfer = event && event.dataTransfer;
    if (!transfer) return;
    wbcSetSplitDrag(event);
    // Hide the native ghost with a preloaded transparent image; the custom overlay
    // (panel-shaped, shrinking to a chat card over the rail) takes over.
    wbcHideNativeDragImage(transfer);
    var page = pageRef.current;
    if (!page) return;
    if (splitOverlayCleanupRef.current) splitOverlayCleanupRef.current();
    // The ghost is the real dialog being lifted: a clone of the live DOM
    // (same transcript, header, composer) in a fixed overlay. The main
    // conversation's grip lifts the main column; the split panel's grip
    // lifts the split panel. It starts at the panel's own position and
    // follows the pointer from the grab point. A clone of the matching rail
    // card rides along; over the conversation rail the ghost switches to
    // that card, as if the conversation itself were being picked up from
    // the list.
    var ghost = document.createElement("div");
    ghost.className = "wbc-split-drag-ghost";
    var grabOffset = null;
    var panelW = 0;
    var panelH = 0;
    var cardW = 0;
    var cardH = 0;
    var restoreGhostViewport = null;
    // Each conversation grip declares which pane it owns. Do not infer the
    // source from a page-wide query: several split hosts can briefly coexist
    // during their closing animation, which made both grips clone the same
    // conversation. For the split grip, walk up from that exact handle so its
    // own live pane is always the ghost source.
    var fromMainGrip = dragSource === "main";
    // Remember the side occupied by the conversation under this exact grip.
    // The midpoint dead band below keeps that side stable until the pointer
    // has genuinely crossed into the opposite half.
    var previewConversationSide = fromMainGrip
      ? (splitSide === "left" ? "right" : "left")
      : (splitSide === "left" ? "left" : "right");
    var dragHandle = event.currentTarget;
    var panel = fromMainGrip
      ? page.querySelector(":scope > .wbc-main")
      : (dragHandle && dragHandle.closest
        ? dragHandle.closest(".wbc-side-agent-split")
        : null);
    if (panel) {
      var panelRect = panel.getBoundingClientRect();
      // The clone keeps the live content and scroll state. Ghost-only CSS
      // removes the app-bar/grip clearance because the detached preview no
      // longer sits underneath the real window chrome.
      var clonedPane = wbcClonePaneWithLiveState(panel);
      var clone = clonedPane.clone;
      restoreGhostViewport = clonedPane.restoreViewport;
      clone.style.border = "0";
      clone.style.boxShadow = "none";
      ghost.appendChild(clone);
      panelW = Math.max(120, Math.min(
        Math.round(panelRect.width),
        Math.round(window.innerWidth - 16)
      ));
      panelH = Math.max(120, Math.min(
        Math.round(panelRect.height),
        Math.round(window.innerHeight * 0.72)
      ));
      ghost.style.width = panelW + "px";
      ghost.style.height = panelH + "px";
      ghost.style.left = panelRect.left + "px";
      var dragHandleRect = dragHandle && dragHandle.getBoundingClientRect
        ? dragHandle.getBoundingClientRect()
        : null;
      // The live pane begins underneath the fixed app bar, but the detached
      // ghost does not. Anchor its vertical grab point to the handle itself so
      // the pointer stays on the ghost's top edge instead of floating inside
      // the transcript at the live pane's old app-bar offset.
      var ghostTopGrabOffset = dragHandleRect
        ? Math.max(0, Math.min(dragHandleRect.height, event.clientY - dragHandleRect.top))
        : 0;
      ghost.style.top = (event.clientY - ghostTopGrabOffset) + "px";
      grabOffset = {
        x: Math.max(0, Math.min(panelRect.width, event.clientX - panelRect.left)),
        y: ghostTopGrabOffset,
      };
    } else {
      ghost.style.left = event.clientX + "px";
      ghost.style.top = event.clientY + "px";
    }
    // A chat split carries its own id; a side-question split lifts the side
    // agent's chat (if it has no rail card the active card is used instead).
    var liftChatId = fromMainGrip
      ? String(activeChatIdRef.current || "")
      : (splitChatId || splitSideAgentId);
    var railCard = liftChatId
      ? page.querySelector('.wbc-chat-card[data-chat-id="' + liftChatId + '"]')
      : null;
    // Never substitute another rail conversation for a known chat id. If the
    // matching card is outside the rendered recent list, keep the pane-shaped
    // ghost instead of showing the active/first session under the wrong grip.
    if (!railCard && !splitChatId && !fromMainGrip) railCard = page.querySelector(".wbc-chat-card.active");
    if (railCard) {
      var cardRect = railCard.getBoundingClientRect();
      var cardClone = railCard.cloneNode(true);
      cardClone.classList.remove("active", "dragging", "menu-open", "group-drop-target", "wbc-chat-group-child");
      ghost.appendChild(cardClone);
      cardW = Math.round(cardRect.width);
      cardH = Math.round(cardRect.height);
    }
    // Over the rail the matching real card lifts off the list, echoing the
    // ghost: the conversation itself looks picked up from the rail.
    var sourceCardEl = railCard;
    // The theme palette lives on .workbench-shell; the ghost sits outside it,
    // so copy the custom properties or the cloned panel renders unstyleed.
    var shell = document.querySelector(".workbench-shell");
    if (shell) {
      var shellStyle = window.getComputedStyle(shell);
      for (var i = 0; i < shellStyle.length; i++) {
        var name = shellStyle[i];
        if (name.indexOf("--") === 0) {
          ghost.style.setProperty(name, shellStyle.getPropertyValue(name));
        }
      }
    }
    document.body.appendChild(ghost);
    if (restoreGhostViewport) {
      restoreGhostViewport();
      // Ghost-only CSS replaces the live app-bar clearance with compact,
      // even content padding. Compensate the raw scrollTop by that difference
      // so adding breathing room does not change the visible reading anchor.
      var sourceThread = panel && panel.querySelector ? panel.querySelector(".wbc-thread") : null;
      var ghostThread = clone && clone.querySelector ? clone.querySelector(".wbc-thread") : null;
      if (sourceThread && ghostThread) {
        var sourceThreadPaddingTop = parseFloat(window.getComputedStyle(sourceThread).paddingTop) || 0;
        var ghostThreadPaddingTop = parseFloat(window.getComputedStyle(ghostThread).paddingTop) || 0;
        var removedThreadPadding = Math.max(0, sourceThreadPaddingTop - ghostThreadPaddingTop);
        if (removedThreadPadding) {
          ghostThread.scrollTop = Math.max(0, ghostThread.scrollTop - removedThreadPadding);
        }
      }
    }
    // Native dragover may arrive a frame late (or not at all until the pointer
    // moves). Clamp the first painted frame as soon as the ghost is measurable.
    positionGhostAt(event.clientX, event.clientY);
    // The zones are pure visual (never hit-testable): an interactive overlay
    // covering the drag source would make Chromium cancel the drag. Zone
    // detection happens on document-level dragover/drop via pointer position.
    var zones = document.createElement("div");
    zones.className = "wbc-split-drop-zones";
    zones.setAttribute("role", "presentation");
    // A conversation keeps the width of the pane under its own grip while
    // moving between sides. The main pane and split pane are often different
    // widths, so the landing highlight must not always borrow the split
    // track's width.
    if (panelW) zones.style.setProperty("--wbc-split-drop-width", panelW + "px");
    zones.setAttribute("data-conversation-side", previewConversationSide);
    zones.innerHTML = ""
      + '<div class="wbc-split-drop-zone wbc-split-drop-left" data-zone="left">'
      + '<span class="wbc-chat-side-drop-hint" role="status">' + wbcEscapeHtml(wbcT("workbenchChat.splitDropLeft", "Release to move the split to the left side")) + '</span>'
      + '</div>'
      + '<div class="wbc-split-drop-zone wbc-split-drop-right" data-zone="right">'
      + '<span class="wbc-chat-side-drop-hint" role="status">' + wbcEscapeHtml(wbcT("workbenchChat.splitDropRight", "Release to move the split to the right side")) + '</span>'
      + '</div>'
      + '<div class="wbc-split-drop-zone wbc-split-drop-rail" data-zone="rail">'
      + '<span class="wbc-chat-side-drop-hint" role="status">' + wbcEscapeHtml(wbcT("workbenchChat.splitDropClose", "Release to close the split panel")) + '</span>'
      + '</div>';
    page.appendChild(zones);

    var clearTimer = null;
    // Sides are judged against the PAGE midline, not the main column's: the
    // layout moves while the drag preview swaps anchors, so a column-relative
    // test would lock the preview onto the side it already previewed.
    function zoneAt(clientX, clientY) {
      var rail = document.querySelector(".wbc-rail");
      var r = rail ? rail.getBoundingClientRect() : null;
      if (r && clientX >= r.left && clientX <= r.right && clientY >= r.top && clientY <= r.bottom) return "rail";
      var page = pageRef.current;
      if (!page) return "";
      var pr = page.getBoundingClientRect();
      if (!pr.width) return "";
      var midpoint = pr.left + pr.width / 2;
      var swapThreshold = 24;
      if (clientX < midpoint - swapThreshold) return "left";
      if (clientX > midpoint + swapThreshold) return "right";
      return previewConversationSide;
    }
    function setActive(zone) {
      ghost.classList.toggle("card", zone === "rail");
      if (zone === "rail" && cardW) {
        ghost.style.width = cardW + "px";
        ghost.style.height = cardH + "px";
      } else if (panelW) {
        ghost.style.width = panelW + "px";
        ghost.style.height = panelH + "px";
      }
      if (sourceCardEl) {
        sourceCardEl.classList.toggle("wbc-split-card-lifted", zone === "rail");
      }
      // querySelectorAll: the half zones nest inside .wbc-split-drop-main.
      var zoneEls = zones.querySelectorAll(".wbc-split-drop-zone");
      for (var i = 0; i < zoneEls.length; i++) {
        var el = zoneEls[i];
        el.classList.toggle("active", el.getAttribute("data-zone") === zone);
      }
    }
    function positionGhostAt(clientX, clientY) {
      var rect = ghost.getBoundingClientRect();
      var viewportInset = 8;
      // Width/height animate between the rail card and full pane. Clamp using
      // the destination size so the growing ghost cannot cross the viewport
      // after a correct position was calculated from its smaller start size.
      var targetWidth = ghost.classList.contains("card") && cardW ? cardW : (panelW || rect.width);
      var targetHeight = ghost.classList.contains("card") && cardH ? cardH : (panelH || rect.height);
      var rawLeft = clientX - (grabOffset ? grabOffset.x : 0);
      var rawTop = clientY - (grabOffset ? grabOffset.y : 0);
      var maxLeft = Math.max(viewportInset, window.innerWidth - targetWidth - viewportInset);
      var maxTop = Math.max(viewportInset, window.innerHeight - targetHeight - viewportInset);
      ghost.style.left = Math.max(viewportInset, Math.min(maxLeft, rawLeft)) + "px";
      ghost.style.top = Math.max(viewportInset, Math.min(maxTop, rawTop)) + "px";
    }
    function onDocumentDragOver(ev) {
      if (clearTimer) { clearTimeout(clearTimer); clearTimer = null; }
      var zone = zoneAt(ev.clientX, ev.clientY);
      if (!zone) {
        setActive("");
        positionGhostAt(ev.clientX, ev.clientY);
        return;
      }
      ev.preventDefault();
      if (ev.dataTransfer) ev.dataTransfer.dropEffect = "move";
      setActive(zone);
      // Position after the panel/card size switch so the current ghost box,
      // including its rounded border, always stays inside the viewport.
      positionGhostAt(ev.clientX, ev.clientY);
      if (zone !== "rail") {
        // Live preview follows the conversation under this handle. For the
        // main grip the split anchor is the opposite side, so merely starting
        // a drag over the main pane can no longer exchange the two panes.
        previewConversationSide = zone;
        zones.setAttribute("data-conversation-side", previewConversationSide);
        setSplitSideDirect(wbcSplitSideForDraggedConversation(zone, fromMainGrip));
      }
    }
    function onDocumentDrop(ev) {
      if (!wbcHasSplitDrag(ev)) return;
      var zone = zoneAt(ev.clientX, ev.clientY);
      if (!zone) return;
      ev.preventDefault();
      ev.stopImmediatePropagation();
      handleSplitDragEnd();
      if (zone === "rail") {
        // Each conversation owns its own grip. Dropping the main
        // conversation's grip closes that conversation and promotes the
        // split conversation; dropping the split conversation's grip closes
        // only the split and leaves the main conversation in place.
        if (splitChatId) {
          if (fromMainGrip) closeMainConversationSplit();
          else closeResourceSplit();
        } else {
          closeActiveSplit();
        }
        return;
      }
      previewConversationSide = zone;
      setSplitSideDirect(wbcSplitSideForDraggedConversation(zone, fromMainGrip));
    }
    function cleanup() {
      if (splitOverlayCleanupRef.current !== cleanup) return;
      splitOverlayCleanupRef.current = null;
      if (clearTimer) clearTimeout(clearTimer);
      document.removeEventListener("dragover", onDocumentDragOver, true);
      document.removeEventListener("drop", onDocumentDrop, true);
      if (sourceCardEl) sourceCardEl.classList.remove("wbc-split-card-lifted");
      if (ghost.parentNode) ghost.parentNode.removeChild(ghost);
      if (zones.parentNode) zones.parentNode.removeChild(zones);
    }
    document.addEventListener("dragover", onDocumentDragOver, true);
    document.addEventListener("drop", onDocumentDrop, true);
    splitOverlayCleanupRef.current = cleanup;
  }

  function handleSplitDragEnd() {
    if (splitOverlayCleanupRef.current) splitOverlayCleanupRef.current();
  }
  var [sideTab, setSideTab] = useWbcState("");
  var [sideVisible, setSideVisible] = useWbcState(true);
  var [sideAgents, setSideAgents] = useWbcState([]);
  var [sideAgentsLoading, setSideAgentsLoading] = useWbcState(false);
  var [sideAgentCreating, setSideAgentCreating] = useWbcState(false);
  var [activeSideAgentByChat, setActiveSideAgentByChat] = useWbcState({});
  // Selecting a side question in the right-hand list opens its conversation
  // beside the main thread. Keep this separate from the remembered list
  // selection so creating/loading an agent never opens the split by itself.
  var [sideAgentSplitByChat, setSideAgentSplitByChat] = useWbcState({});
  // Artifacts use the same resizable detail track as side conversations. Store
  // only the stable file key so refreshed chat payloads can supply fresh URLs.
  var [artifactSplitByChat, setArtifactSplitByChat] = useWbcState({});
  var [changeSplitByChat, setChangeSplitByChat] = useWbcState({});
  // Map, browser, viewer and subagent details share the same right-side split
  // shell. The side panel only exposes their lightweight index/list surface.
  var [resourceSplitByChat, setResourceSplitByChat] = useWbcState({});
  // While a detail split is open, the conversation panel can float beneath
  // the main conversation grip without disturbing that split. If a resource
  // is opened from the floating panel, remember the displaced split so its
  // close action can restore the exact previous layout.
  var [floatingConversationPanelOpen, setFloatingConversationPanelOpen] = useWbcState(false);
  var floatingSplitRestoreRef = useWbcRef(null);
  var [sideAgentSplitWidth, setSideAgentSplitWidth] = useWbcState(function () {
    var initial = 520;
    try {
      var saved = Number(localStorage.getItem("wbc-side-agent-split-width"));
      if (Number.isFinite(saved) && saved >= 300) initial = saved;
    } catch (e) {}
    return wbcClampSideSplitWidth(initial, window.innerWidth);
  });
  // Card-workspace columns resize independently from the docked conversation
  // panel. Sharing the old side-panel width made the hidden panel visibly
  // breathe underneath the cards and gave the two card columns different
  // constraints.
  var [paneColumnWidth, setPaneColumnWidth] = useWbcState(function () {
    var initial = 520;
    try {
      var saved = Number(localStorage.getItem("wbc-pane-column-width"));
      if (Number.isFinite(saved) && saved > 0) initial = saved;
    } catch (e) {}
    return initial;
  });
  var [browserActiveByChat, setBrowserActiveByChat] = useWbcState({});
  // The side-panel Browser tab and the floating browser are two presentations
  // of the same session. Keep only the floating presentation state here so the
  // WebContentsView is never mounted in two places at once.
  var [browserWindowModeByChat, setBrowserWindowModeByChat] = useWbcState({});
  var [viewerFile, setViewerFile] = useWbcState(null);
  var [subagentData, setSubagentData] = useWbcState({ rounds: [], activeRoundId: "", agents: [], messages: [] });
  var [subagentLoading, setSubagentLoading] = useWbcState(false);
  var subagentRefreshTimerRef = useWbcRef(null);

  useWbcEffect(function () {
    function keepSplitWithinViewport() {
      setSideAgentSplitWidth(function (current) {
        var next = wbcClampSideSplitWidthForPage(current, pageRef.current);
        if (next === current) return current;
        try { localStorage.setItem("wbc-side-agent-split-width", String(next)); } catch (e) {}
        return next;
      });
    }
    keepSplitWithinViewport();
    var observer = typeof ResizeObserver !== "undefined" && pageRef.current
      ? new ResizeObserver(keepSplitWithinViewport)
      : null;
    if (observer) observer.observe(pageRef.current);
    window.addEventListener("resize", keepSplitWithinViewport);
    return function () {
      if (observer) observer.disconnect();
      window.removeEventListener("resize", keepSplitWithinViewport);
    };
  }, [splitSide]);

  useWbcEffect(function () {
    function handleShowChatSide() {
      if (isActive) setSideVisible(true);
    }
    window.addEventListener("workbench:show-chat-side", handleShowChatSide);
    return function () {
      window.removeEventListener("workbench:show-chat-side", handleShowChatSide);
    };
  }, [isActive]);

  useWbcEffect(function () {
    window.dispatchEvent(new CustomEvent("workbench:chat-side-visibility", {
      detail: { active: isActive, hidden: isActive && !sideVisible },
    }));
  }, [isActive, sideVisible]);
  var remoteChatRefreshTimerRef = useWbcRef(null);
  var remoteChangedChatIdsRef = useWbcRef(new Set());
  // True while the backend reads the whole conversation and synthesizes a task.
  var [toTaskBusy, setToTaskBusy] = useWbcState(false);
  var [compactBusy, setCompactBusy] = useWbcState(false);
  var [memoryLearningBusy, setMemoryLearningBusy] = useWbcState(false);
  var [pageContextMenu, setPageContextMenu] = useWbcState(null);
  var pageContextMenuRef = useWbcRef(null);
  var pendingPageContextMenuRef = useWbcRef(null);
  var pageContextPreviewTimerRef = useWbcRef(null);
  var [quickRenameChat, setQuickRenameChat] = useWbcState(null);
  // Streaming runtimes live in the module-level engine so a run survives this
  // page unmounting when the user switches modules mid-reply. Only the active
  // runtime receives token-level updates; the rail receives a stable presence
  // map. Thus an old/background run cannot repaint a newly-created conversation.
  var runtimeEngine = WorkbenchChatRuntimes;
  var [activeRuntime, setActiveRuntime] = useWbcState(function () {
    return runtimeEngine.get(activeChatId);
  });
  var [runningChatIds, setRunningChatIds] = useWbcState(function () {
    return wbcRuntimePresenceSnapshot(runtimeEngine.snapshot());
  });
  useWbcEffect(function () {
    function applyRuntimeSnapshot(snapshot) {
      var nextActive = snapshot[activeChatIdRef.current] || null;
      setActiveRuntime(function (current) { return current === nextActive ? current : nextActive; });
      var nextPresence = wbcRuntimePresenceSnapshot(snapshot);
      setRunningChatIds(function (current) {
        return wbcSameRuntimePresence(current, nextPresence) ? current : nextPresence;
      });
    }
    applyRuntimeSnapshot(runtimeEngine.snapshot());
    return runtimeEngine.subscribe(applyRuntimeSnapshot);
  }, []);
  useWbcEffect(function () {
    setActiveRuntime(runtimeEngine.get(activeChatId));
  }, [activeChatId]);

  useWbcEffect(function () {
    var chatId = String(activeChatId || "");
    var cancelled = false;
    if (!chatId || chatId.indexOf("legacy:") === 0) {
      setSideAgents([]);
      setSideAgentsLoading(false);
      return undefined;
    }
    // Never let the previous conversation's side questions leak into the next
    // conversation while its request is in flight.
    setSideAgents([]);
    setSideAgentsLoading(true);
    model.listSideAgents(chatId).then(function (agents) {
      if (!cancelled && activeChatIdRef.current === chatId) {
        setSideAgents(agents);
        if (agents.length) {
          setActiveSideAgentByChat(function (current) {
            if (agents.some(function (agent) { return agent.id === current[chatId]; })) {
              return current;
            }
            return Object.assign({}, current, { [chatId]: agents[agents.length - 1].id });
          });
        }
      }
    }).catch(function () {
      if (!cancelled && activeChatIdRef.current === chatId) setSideAgents([]);
    }).finally(function () {
      if (!cancelled && activeChatIdRef.current === chatId) setSideAgentsLoading(false);
    });
    return function () { cancelled = true; };
  }, [activeChatId]);
  // Holds a chat id requested by global search until the chat list is loaded.
  var pendingChatIdRef = useWbcRef("");
  // A topbar context-menu action can navigate to another conversation and
  // reveal one of its resources in the same operation.
  var pendingTopbarResourceRef = useWbcRef(null);
  var [resourceSplitDropSide, setResourceSplitDropSide] = useWbcState("");
  var chatFileDropActive = useWorkbenchFileDrop(function (files) {
    try {
      window.dispatchEvent(new CustomEvent("cyrene:add-chat-attachments", { detail: { files: files } }));
    } catch (e) {}
  }, !!(isActive && project));

  function openViewer(file, preferredSide) {
    if (!file) return;
    setViewerFile(file);
    // Message file actions are direct navigation: open the shared preview
    // split immediately instead of expanding the Viewer index in the
    // conversation panel first.
    setSideTab("");
    setSideVisible(true);
    selectResourceSplit("viewer", wbcArtifactFileKey(file), true);
    openPaneContent("file", file, { side: preferredSide === "left" ? "left" : "right" });
  }

  function openProjectFile(entry) {
    var file = wbcProjectFileResource(projectId, entry);
    if (file) openViewer(file);
  }

  function resourceSplitDropGeometry() {
    var page = pageRef.current;
    if (!page) return null;
    var pageRect = page.getBoundingClientRect();
    var rail = page.querySelector(".wbc-rail");
    var railRect = rail && rail.getBoundingClientRect ? rail.getBoundingClientRect() : null;
    var contentLeft = railRect && railRect.right > pageRect.left ? railRect.right : pageRect.left;
    var contentRight = pageRect.right;
    var chatSideRect = wbcChatSideZoneRect();
    // Keep file drops aligned with the conversation drop preview. Previously
    // the file target split all available content at its midpoint, so its
    // right-side outline covered part of the main conversation even though a
    // conversation drop only highlighted the actual side-panel footprint.
    var rightLeft = chatSideRect
      ? Math.max(contentLeft, Math.min(contentRight, chatSideRect.left))
      : contentLeft + ((contentRight - contentLeft) / 2);
    var rightRight = chatSideRect
      ? Math.max(rightLeft, Math.min(contentRight, chatSideRect.right))
      : contentRight;
    return {
      pageRect: pageRect,
      contentLeft: contentLeft,
      rightLeft: rightLeft,
      rightRight: rightRight,
    };
  }

  function resourceSplitSideAt(event) {
    var geometry = resourceSplitDropGeometry();
    if (!geometry) return "";
    var pageRect = geometry.pageRect;
    if (
      event.clientX < geometry.contentLeft || event.clientX > geometry.rightRight
      || event.clientY < pageRect.top || event.clientY > pageRect.bottom
    ) return "";
    return event.clientX < geometry.rightLeft ? "left" : "right";
  }

  function handleResourceSplitDragOver(event) {
    if (!wbcHasResourceDrag(event)) return;
    if (event.target && event.target.closest && event.target.closest(".wbc-rail")) {
      if (resourceSplitDropSide) setResourceSplitDropSide("");
      return;
    }
    // A card owns its top / replace / bottom targets. Keep the page-level
    // left/right preview out of that stacking context so its hint cannot sit
    // behind (or visually compete with) the card drop feedback.
    if (event.target && event.target.closest && event.target.closest(".wbc-pane-card")) {
      if (resourceSplitDropSide) setResourceSplitDropSide("");
      return;
    }
    var side = resourceSplitSideAt(event);
    if (!side) {
      if (resourceSplitDropSide) setResourceSplitDropSide("");
      return;
    }
    event.preventDefault();
    if (event.dataTransfer) event.dataTransfer.dropEffect = "copy";
    if (resourceSplitDropSide !== side) setResourceSplitDropSide(side);
  }

  function handleResourceSplitDrop(event) {
    if (!wbcHasResourceDrag(event)) return;
    var side = resourceSplitSideAt(event);
    var resource = wbcReadResourceDrag(event);
    setResourceSplitDropSide("");
    if (!side || !resource) return;
    if (resource.kind !== "file" && resource.kind !== "terminal") return;
    event.preventDefault();
    event.stopPropagation();
    if (resource.kind === "terminal") {
      openTerminal(resource.terminalId, side);
      return;
    }
    setSplitSideDirect(side);
    openViewer(resource.file && Object.keys(resource.file).length ? resource.file : resource, side);
  }

  function revealTopbarResource(chatId, resource) {
    if (!chatId || !resource) return;
    if (resource.type === "browser") {
      setBrowserActiveByChat(function (prev) {
        return Object.assign({}, prev, { [chatId]: true });
      });
      setBrowserWindowModeByChat(function (prev) {
        return Object.assign({}, prev, { [chatId]: "pip" });
      });
      return;
    }
    if (resource.type === "file" && resource.file) openViewer(resource.file);
  }

  function markViewerFileRead(file) {
    if (!file || file.source === "project" || !projectId || !file.url) return;
    fetch("/api/workbench/library/read?workspace=" + encodeURIComponent(projectId), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        attachment_url: String(file.url || ""),
        file_name: String(file.name || ""),
      }),
    }).catch(function () { /* Reading history must never interrupt the viewer. */ });
  }

  function loadSubagents(chatId, roundId) {
    if (!chatId) {
      setSubagentData({ rounds: [], activeRoundId: "", agents: [], messages: [] });
      return Promise.resolve(null);
    }
    setSubagentLoading(true);
    return model.getSubagents(chatId, roundId)
      .then(function (payload) {
        if (activeChatIdRef.current === chatId) setSubagentData(payload);
        return payload;
      })
      .catch(function (err) {
        if (activeChatIdRef.current === chatId) setError(wbcErrorText(err));
        return null;
      })
      .finally(function () {
        if (activeChatIdRef.current === chatId) setSubagentLoading(false);
      });
  }

  function refreshChats(selectId) {
    // Callers include one-time SSE and navigation subscriptions. Resolve the
    // target at invocation time so a mobile update after a project switch does
    // not refresh the project that happened to be active at mount time.
    var requestedProjectId = String(projectIdRef.current || "");
    if (!requestedProjectId) return Promise.resolve([]);
    var selectionAtRequest = String(activeChatIdRef.current || "");
    var requestSequence = beginChatListRequest(requestedProjectId);
    return model.listChats(requestedProjectId).then(function (list) {
      // A background run may finish after the user has switched projects.
      if (projectIdRef.current !== requestedProjectId) return list;
      // Multiple lifecycle/navigation refreshes can overlap. An older list
      // response must never restore a pre-terminal `running` summary after a
      // newer response (or the synchronous terminal patch) has settled it.
      if (!isCurrentChatListRequest(requestedProjectId, requestSequence)) return list;
      chatCache.lists[requestedProjectId] = list;
      chatsProjectIdRef.current = requestedProjectId;
      setChats(list);
      // Background chat-list refreshes must not pull a full-page task back
      // into conversation mode. An explicit chat target still wins.
      if (!selectId && activeTaskWorkspaceRef.current) return list;
      var targetId = wbcResolveRefreshedChatSelection(
        list,
        selectId,
        selectionAtRequest,
        activeChatIdRef.current
      );
      if (targetId !== null) selectChat(targetId);
      return list;
    }).finally(function () {
      if (
        projectIdRef.current === requestedProjectId
        && isCurrentChatListRequest(requestedProjectId, requestSequence)
      ) setLoading(false);
    });
  }

  // Initial load + project switch.
  useWbcEffect(function () {
    var requestedProjectId = projectId;
    var cachedList = Array.isArray(chatCache.lists[requestedProjectId])
      ? chatCache.lists[requestedProjectId]
      : null;
    setLoading(!cachedList);
    setError("");
    setErrorKind("load");
    setDraftAgentBinding(wbcLoadDraftAgentBinding(requestedProjectId));
    if (!projectId) { setChats([]); setLoading(false); return; }
    var navigation = window.CyreneUI.require("navigation");
    var pending = navigation.getPending();
    var pendingChatId = pending && pending.type === "chat" ? (pending.chatId || pending.id) : "";
    if (pendingChatId && pending.topbarResource) {
      pendingTopbarResourceRef.current = { chatId: pendingChatId, resource: pending.topbarResource };
    }
    var remembered = wbcLastChatByProject[projectId];
    function selectFrom(list) {
      if (activeTaskWorkspaceRef.current) {
        setActiveChat(null);
        return "";
      }
      var targetId = pendingChatId && list.some(function (c) { return c.id === pendingChatId; })
        ? pendingChatId
        : (remembered && list.some(function (c) { return c.id === remembered; })
          ? remembered
          : (list[0] ? list[0].id : ""));
      if (targetId === pendingChatId) pendingChatIdRef.current = pendingChatId;
      selectChat(targetId);
      setActiveChat(targetId && chatCache.details[targetId] ? chatCache.details[targetId] : null);
      return targetId;
    }
    if (cachedList) {
      chatsProjectIdRef.current = requestedProjectId;
      setChats(cachedList);
      selectFrom(cachedList);
    } else {
      chatsProjectIdRef.current = "";
      setChats([]);
      setActiveChat(null);
      selectChat("");
    }
    var requestSequence = beginChatListRequest(requestedProjectId);
    model.listChats(requestedProjectId)
      .then(function (list) {
        if (projectIdRef.current !== requestedProjectId) return;
        if (!isCurrentChatListRequest(requestedProjectId, requestSequence)) return;
        chatCache.lists[requestedProjectId] = list;
        chatsProjectIdRef.current = requestedProjectId;
        setChats(list);
        selectFrom(list);
      })
      .catch(function (err) {
        if (projectIdRef.current === requestedProjectId && !cachedList) setError(wbcErrorText(err));
      })
      .finally(function () {
        if (
          projectIdRef.current === requestedProjectId
          && isCurrentChatListRequest(requestedProjectId, requestSequence)
        ) setLoading(false);
      });
  }, [projectId]);

  // Apply a chat id requested by global search once the chat list is available.
  useWbcEffect(function () {
    var targetId = pendingChatIdRef.current;
    if (!targetId) return;
    if (Array.isArray(chats) && chats.some(function (c) { return c.id === targetId; })) {
      selectChat(targetId);
      pendingChatIdRef.current = "";
      window.CyreneUI.require("navigation").clearPending();
    }
  }, [chats]);

  // Load the full transcript when the selection changes. The transcript and
  // subagent history are deliberately independent: auxiliary history must not
  // prevent an otherwise healthy conversation from rendering.
  useWbcEffect(function () {
    if (activeChatId && skipNextHydrationChatIdRef.current === activeChatId) {
      skipNextHydrationChatIdRef.current = "";
      setChatLoading(false);
      setSubagentLoading(false);
      setSubagentData({ rounds: [], activeRoundId: "", agents: [], messages: [] });
      return;
    }
    var cachedChat = activeChatId ? (chatCache.details[activeChatId] || null) : null;
    // Never show a transcript from a different conversation. A cache hit is
    // safe because it is keyed by the exact target id; a miss clears first.
    if (!cachedChat) setActiveChat(null);
    if (!activeChatId) {
      setChatLoading(false);
      setSubagentData({ rounds: [], activeRoundId: "", agents: [], messages: [] });
      return;
    }
    var controller = (typeof AbortController !== "undefined") ? new AbortController() : null;
    var requestOptions = controller ? { signal: controller.signal } : {};
    var cachedSubagents = chatCache.subagents[activeChatId] || null;
    // Switching back to a recently viewed project paints its cached transcript
    // immediately. The requests below still refresh it in the background.
    setActiveChat(cachedChat);
    if (cachedSubagents) setSubagentData(cachedSubagents);
    else setSubagentData({ rounds: [], activeRoundId: "", agents: [], messages: [] });
    var retainedRunError = runtimeEngine.getFailure(activeChatId);
    setError(retainedRunError || "");
    setErrorKind(retainedRunError ? "message" : "load");
    setChatLoading(!cachedChat);
    setSubagentLoading(!cachedSubagents);
    var hydrationSequence = beginChatHydration(activeChatId);
    model.getChat(activeChatId, requestOptions)
      .then(function (chat) {
        if (
          activeChatIdRef.current !== activeChatId
          || !isCurrentChatHydration(activeChatId, hydrationSequence)
        ) return;
        setActiveChat(function (previous) {
          var reconciled = wbcPreserveLiveTimelineAnchors(
            previous,
            chat,
            runtimeEngine.get(activeChatId)
          );
          chatCache.details[activeChatId] = reconciled;
          return reconciled;
        });
      })
      .catch(function (err) {
        if (err && err.name === "AbortError") return;
        if (
          activeChatIdRef.current === activeChatId
          && isCurrentChatHydration(activeChatId, hydrationSequence)
        ) {
          setError(wbcT("workbenchChat.error.transcriptPrefix", "Conversation details: {error}", { error: wbcErrorText(err) }));
        }
      })
      .finally(function () {
        if (
          activeChatIdRef.current === activeChatId
          && isCurrentChatHydration(activeChatId, hydrationSequence)
        ) setChatLoading(false);
      });
    model.getSubagents(activeChatId, "", requestOptions)
      .then(function (payload) {
        chatCache.subagents[activeChatId] = payload;
        if (activeChatIdRef.current === activeChatId) setSubagentData(payload);
      })
      .catch(function (err) {
        if (err && err.name === "AbortError") return;
        // Subagent history is auxiliary. Keep the transcript usable and retain
        // a precise diagnostic without turning this into a chat-load failure.
        console.warn("Workbench subagent history load failed", activeChatId, err);
      })
      .finally(function () {
        if (activeChatIdRef.current === activeChatId) setSubagentLoading(false);
      });
    return function () {
      if (controller) controller.abort();
    };
  }, [activeChatId, loadRevision]);

  // Viewer / content tabs belong to one conversation — reset on switch.
  useWbcEffect(function () {
    setViewerFile(null);
    setSideTab("");
  }, [activeChatId]);

  // Run after the conversation-switch reset above so a requested file remains
  // open and a requested browser session is restored directly as a PiP window.
  useWbcEffect(function () {
    var pendingResource = pendingTopbarResourceRef.current;
    if (!pendingResource || String(pendingResource.chatId) !== String(activeChatId || "")) return;
    pendingTopbarResourceRef.current = null;
    revealTopbarResource(activeChatId, pendingResource.resource);
  }, [activeChatId]);

  // New PDFs reveal the Viewer row but keep it collapsed until the user opens it.
  var lastAutoPdfUrlRef = useWbcRef("");
  useWbcEffect(function () {
    if (!activeChat || !Array.isArray(activeChat.messages)) return;
    var msgs = activeChat.messages;
    for (var mi = msgs.length - 1; mi >= 0; mi--) {
      var files = Array.isArray(msgs[mi].attachments) ? msgs[mi].attachments : [];
      for (var fi = 0; fi < files.length; fi++) {
        var f = files[fi];
        var isPdf = wbcFileViewKind(f) === "pdf";
        if (isPdf) {
          var fUrl = f.url || f.id || "";
          if (fUrl && fUrl !== lastAutoPdfUrlRef.current) {
            lastAutoPdfUrlRef.current = fUrl;
            setViewerFile(f);
            setSideVisible(true);
            return;
          }
        }
      }
    }
  }, [activeChat && activeChat.messages && activeChat.messages.map(function (m) {
    return (m.attachments || []).map(function (a) { return a.url || a.id || ''; }).join(',');
  }).join('|')]);

  // Surface the active conversation title in the topbar crumbs.
  useWbcEffect(function () {
    if (onActiveChatChange) onActiveChatChange(activeChat ? activeChat.title : "");
    return function () { if (onActiveChatChange) onActiveChatChange(""); };
  }, [activeChat && activeChat.title]);

  // Report the open conversation id up to the shell so the notification center
  // can treat replies in *this* chat as already-seen (no redundant "new" badge).
  useWbcEffect(function () {
    if (onActiveChatIdChange) onActiveChatIdChange(activeChatId || "");
    return function () { if (onActiveChatIdChange) onActiveChatIdChange(""); };
  }, [activeChatId]);

  // Global search navigation: select the requested chat when the user clicks a
  // search result. If the chat list is already loaded we apply immediately,
  // otherwise we stash the id in a ref so the effect above can apply it once
  // the list loads.
  function applyPendingChatSelection() {
    var navigation = window.CyreneUI.require("navigation");
    var pending = navigation.getPending();
    var targetId = pending && pending.type === "chat" ? (pending.chatId || pending.id) : "";
    if (!targetId) return;
    var topbarResource = pending.topbarResource || null;
    if (Array.isArray(chatsRef.current) && chatsRef.current.some(function (c) { return c.id === targetId; })) {
      if (topbarResource && String(activeChatIdRef.current || "") === String(targetId)) {
        revealTopbarResource(targetId, topbarResource);
      } else {
        if (topbarResource) pendingTopbarResourceRef.current = { chatId: targetId, resource: topbarResource };
        selectChat(targetId);
      }
      navigation.clearPending(pending);
    } else {
      pendingChatIdRef.current = targetId;
      if (topbarResource) pendingTopbarResourceRef.current = { chatId: targetId, resource: topbarResource };
      // A notification may target a chat created after this long-lived page
      // last loaded its list. Refresh the current project now; otherwise the
      // pending id has no state change that would ever cause it to be applied.
      // A cross-project target is handled by the project-change loading effect.
      var targetProjectId = String(pending.projectId || "");
      if (!targetProjectId || targetProjectId === String(projectIdRef.current || "")) {
        refreshChats(targetId);
      }
    }
  }

  useWbcEffect(function () {
    function onNavigate(event) {
      var detail = event && event.detail;
      if (!detail) return;
      if (detail.type === "chat") {
        applyPendingChatSelection();
        return;
      }
      if (detail.type === "file" && detail.entry) {
        openProjectFile(detail.entry);
        return;
      }
      if (detail.type === "terminal" && detail.terminalId) {
        openTerminal(detail.terminalId);
      }
    }
    window.addEventListener("cyrene:workbench-navigate", onNavigate);
    applyPendingChatSelection();
    return function () { window.removeEventListener("cyrene:workbench-navigate", onNavigate); };
  }, []);

  // Re-pull the chat list when another surface (the quick-chat window) sent a
  // message into this project, so the new conversation / reply shows up without
  // a manual refresh. Re-registered per project so refreshChats stays current.
  useWbcEffect(function () {
    function onRefresh(event) {
      var detail = (event && event.detail) || {};
      if (detail.projectId && String(detail.projectId) !== String(projectId)) return;
      refreshChats(detail.selectId || "");
    }
    window.addEventListener("cyrene:wbc-refresh-chats", onRefresh);
    return function () { window.removeEventListener("cyrene:wbc-refresh-chats", onRefresh); };
  }, [projectId]);

  // Live tool progress: reuse the platform SSE feed and keep only
  // events tagged with a running conversation's session id.
  useWbcEffect(function () {
    function onEvent(event) {
      if (!event) return;
      if (event.type === "remote_job_update") {
        try {
          window.dispatchEvent(new CustomEvent("cyrene:remote-job-update", { detail: event }));
          var feedback = window.CyreneUI.require("feedback");
          if (feedback && typeof feedback.showToast === "function") {
            feedback.showToast(
              wbcT(
                "workbenchChat.remoteJobFinished",
                "Remote job {jobId}: {status}",
                { jobId: event.job_id || "", status: event.status || "completed" }
              ),
              event.status === "completed" ? "success" : "info"
            );
          }
        } catch (e) {}
        return;
      }
      if (event.type === "workbench_chat_changed") {
        if (
          event.project_id
          && String(event.project_id) !== String(projectIdRef.current || "")
        ) return;
        var changedChatId = String(event.chat_id || event.session_id || event.chatId || "");
        remoteChangedChatIdsRef.current.add(changedChatId || "*");
        if (remoteChatRefreshTimerRef.current) {
          clearTimeout(remoteChatRefreshTimerRef.current);
        }
        remoteChatRefreshTimerRef.current = setTimeout(function () {
          remoteChatRefreshTimerRef.current = null;
          var changedChatIds = remoteChangedChatIdsRef.current;
          remoteChangedChatIdsRef.current = new Set();
          refreshChats("");
          var openChatId = String(activeChatIdRef.current || "");
          if (openChatId && (changedChatIds.has("*") || changedChatIds.has(openChatId))) {
            // Remote-control surfaces persist the user/reply messages without
            // going through this page's optimistic streaming hooks. Re-run the
            // detail hydration as well as the rail refresh so the conversation
            // already on screen reflects the durable transcript immediately.
            setLoadRevision(function (value) { return value + 1; });
          }
        }, 80);
        return;
      }
      if (event.type === "workspace_changes") {
        try { window.dispatchEvent(new CustomEvent("workbench:workspace-changes", { detail: event })); } catch (e) {}
      }
      if (event.type === "workbench_proactive_message") {
        if (String(event.project_id || "") !== String(projectIdRef.current || "")) return;
        var proactiveChatId = String(event.chat_id || event.session_id || "");
        var proactiveMessage = event.message;
        var updatedAt = String(event.updated_at || (proactiveMessage && proactiveMessage.createdAt) || "");
        setChats(function (prev) {
          var found = false;
          var next = prev.map(function (chat) {
            if (chat.id !== proactiveChatId) return chat;
            found = true;
            return {
              ...chat,
              updatedAt: updatedAt || chat.updatedAt,
              preview: proactiveMessage ? proactiveMessage.content : chat.preview,
              messageCount: (chat.messageCount || 0) + 1,
            };
          });
          if (!found) return prev;
          return next.slice().sort(function (a, b) {
            return String(b.updatedAt || "").localeCompare(String(a.updatedAt || ""));
          });
        });
        if (activeChatIdRef.current === proactiveChatId && proactiveMessage) {
          setActiveChat(function (prev) {
            if (!prev || prev.id !== proactiveChatId) return prev;
            var messages = prev.messages || [];
            if (messages.some(function (item) { return item.id === proactiveMessage.id; })) return prev;
            return {
              ...prev,
              updatedAt: updatedAt || prev.updatedAt,
              messages: messages.concat([proactiveMessage]),
            };
          });
        }
        return;
      }
      var chatId = String(event.session_id || event.chat_id || event.chatId || "");
      if (
        chatId
        && activeChatIdRef.current === chatId
        && (event.type === "plan_progress" || event.type === "plan")
        && event.plan
      ) {
        setActiveChat(function (prev) {
          if (!prev || prev.id !== chatId) return prev;
          return { ...prev, activePlan: event.plan };
        });
      }
      if (
        chatId
        && activeChatIdRef.current === chatId
        && (event.type === "subagent_update" || event.type === "agent_comm" || event.type === "agent_chat_user_message")
      ) {
        if (subagentRefreshTimerRef.current) clearTimeout(subagentRefreshTimerRef.current);
        subagentRefreshTimerRef.current = setTimeout(function () {
          loadSubagents(chatId);
        }, 120);
      }
      var browserEventChatId = String(event.session_id || event.chat_id || event.chatId || "");
      if (
        (event.type === "browser_frame" || event.type === "browser_takeover_request")
        && activeChatIdRef.current
        && (!browserEventChatId || browserEventChatId === String(activeChatIdRef.current))
      ) {
        setBrowserActiveByChat(function (prev) {
          var sid = String(browserEventChatId || activeChatIdRef.current || "");
          if (!sid || prev[sid]) return prev;
          return { ...prev, [sid]: true };
        });
        setBrowserWindowModeByChat(function (prev) {
          var sid = String(browserEventChatId || activeChatIdRef.current || "");
          if (!sid || prev[sid]) return prev;
          return { ...prev, [sid]: "pip" };
        });
      }
      // Live tool/phase/subagent progress is folded into the runtime by the
      // module-level engine (WorkbenchChatRuntimes) so it keeps accumulating even
      // when this page is unmounted; nothing to do here.
    }
    var unsubscribe = window.CyreneUI.require("events").subscribe(onEvent);
    return function () {
      unsubscribe();
      if (remoteChatRefreshTimerRef.current) {
        clearTimeout(remoteChatRefreshTimerRef.current);
        remoteChatRefreshTimerRef.current = null;
      }
      remoteChangedChatIdsRef.current = new Set();
    };
  }, []);

  // 按对话查询 Electron 中对应的 BrowserTabManager。每个 manager 的 tabs
  // 和 persistent partition 都由 chatId 隔离，刷新 UI 不会把别的对话误认
  // 为当前对话的浏览器。
  var browserRestoredRef = useWbcRef({});
  useWbcEffect(function () {
    function handleCopiedBrowser(event) {
      var targetChatId = String(event && event.detail && event.detail.targetChatId || "");
      if (!targetChatId) return;
      browserRestoredRef.current[targetChatId] = true;
      setBrowserActiveByChat(function (prev) {
        return Object.assign({}, prev, { [targetChatId]: true });
      });
      setBrowserWindowModeByChat(function (prev) {
        return Object.assign({}, prev, { [targetChatId]: "pip" });
      });
    }
    window.addEventListener("cyrene:browser-copied-to-chat", handleCopiedBrowser);
    return function () {
      window.removeEventListener("cyrene:browser-copied-to-chat", handleCopiedBrowser);
    };
  }, []);

  useWbcEffect(function () {
    var bridge = window.cyrene && window.cyrene.browser;
    if (!bridge || typeof bridge.getState !== "function") return;
    var chatId = activeChatId || "";
    if (!chatId) return;
    if (browserRestoredRef.current[chatId]) return;
    browserRestoredRef.current[chatId] = true;
    bridge.getState(chatId).then(function (state) {
      if (String(state && state.sessionId || "") !== String(chatId)) return;
      if (!state || !state.tabs || !Array.isArray(state.tabs) || !state.tabs.length) return;
      setBrowserActiveByChat(function (prev) {
        if (prev[chatId]) return prev;
        return Object.assign({}, prev, { [chatId]: true });
      });
      setBrowserWindowModeByChat(function (prev) {
        if (prev[chatId]) return prev;
        return Object.assign({}, prev, { [chatId]: "pip" });
      });
    }).catch(function (err) { console.error("getState failed", err); });
  }, [activeChatId]);

  function ensureChat() {
    if (activeChatId) return Promise.resolve(activeChatId);
    var binding = draftAgentBindingRef.current;
    var bindingChat = model.createChatWithBinding
      ? model.createChatWithBinding(projectId, "", binding)
      : model.createChat(projectId);
    return bindingChat.then(function (chat) {
      if (binding) {
        setDraftAgentBinding(null);
        wbcSaveDraftAgentBinding(projectId, null);
      }
      try {
        window.dispatchEvent(new CustomEvent("cyrene:wbc-chat-created", {
          detail: { projectId: projectId, chatId: chat.id },
        }));
      } catch (e) {}
      setChats(function (prev) { return [chat].concat(prev); });
      skipNextHydrationChatIdRef.current = chat.id;
      selectChat(chat.id);
      setActiveChat(chat);
      return chat.id;
    });
  }

  function retryLoad() {
    if (!projectId) return;
    setError("");
    setErrorKind("load");
    setChatLoading(true);
    refreshChats(activeChatId)
      .then(function (list) {
        var chatId = activeChatId || (list[0] && list[0].id) || "";
        if (!chatId) {
          setActiveChat(null);
          setChatLoading(false);
          return null;
        }
        selectChat(chatId);
        setLoadRevision(function (value) { return value + 1; });
        return null;
      })
      .catch(function (err) {
        setChatLoading(false);
        setError(wbcT("workbenchChat.error.listPrefix", "Chat list: {error}", { error: wbcErrorText(err) }));
      });
  }

  // Register transcript hooks with the streaming engine so a run patches THIS
  // page's local transcript / chat list while it is mounted. Re-registered every
  // render so the closures (refreshChats, model …) never go stale; on unmount the
  // cleanup clears them and the run streams on, with the transcript re-pulled
  // from the server on remount. Each hook guards by chatId so a background run
  // only touches the conversation it belongs to.
  useWbcEffect(function () {
    runtimeEngine.setHooks({
      onUserMessage: function (chatId, userMessage) {
        setActiveChat(function (prev) {
          if (!prev || prev.id !== chatId) return prev;
          return { ...prev, messages: wbcMergeChronologicalMessages(prev.messages || [], [userMessage]) };
        });
      },
      onUserMessageConfirmed: function (chatId, confirmation) {
        setActiveChat(function (prev) {
          if (!prev || prev.id !== chatId) return prev;
          var userMessage = confirmation && confirmation.userMessage;
          if (!userMessage) return prev;
          var optimisticId = String(confirmation.optimisticId || "");
          var messages = prev.messages || [];
          if (optimisticId) {
            for (var i = 0; i < messages.length; i++) {
              if (String(messages[i] && messages[i].id || "") !== optimisticId) continue;
              var confirmed = messages.slice();
              confirmed[i] = wbcConfirmOptimisticMessage(messages[i], userMessage);
              return { ...prev, messages: confirmed };
            }
          }
          return { ...prev, messages: wbcMergeChronologicalMessages(messages, [userMessage]) };
        });
      },
      onRetryTruncate: function (chatId, truncateInfo) {
        // Regenerating: drop everything after the replayed user message.
        var suppressedTurn = retrySuppressedTurnRef.current || {};
        var locallySuppressedIds = String(suppressedTurn.chatId || "") === String(chatId || "")
          ? (Array.isArray(suppressedTurn.messageIds) ? suppressedTurn.messageIds.map(String) : [])
          : [];
        setActiveChat(function (prev) {
          if (!prev || prev.id !== chatId) return prev;
          var list = prev.messages || [];
          var afterId = typeof truncateInfo === "string" ? truncateInfo : String(truncateInfo && truncateInfo.afterId || "");
          var hasExplicitReplacedIds = !!(truncateInfo && Array.isArray(truncateInfo.replacedIds));
          var replacedIds = new Set((hasExplicitReplacedIds ? truncateInfo.replacedIds.map(String) : []).concat(locallySuppressedIds));
          if (hasExplicitReplacedIds || locallySuppressedIds.length) {
            return {
              ...prev,
              messages: list.filter(function (item) { return !replacedIds.has(String(item && item.id || "")); }),
            };
          }
          var cut = -1;
          for (var i = 0; i < list.length; i++) {
            if (String(list[i].id) === afterId) { cut = i; break; }
          }
          if (cut < 0) return prev;
          return { ...prev, messages: list.slice(0, cut + 1) };
        });
        if (String(retrySuppressedTurnRef.current && retrySuppressedTurnRef.current.chatId || "") === String(chatId || "")) {
          retrySuppressedTurnRef.current = { chatId: "", messageIds: [] };
        }
        setRetrySuppressedTurn(function (current) {
          return String(current && current.chatId || "") === String(chatId || "")
            ? { chatId: "", messageIds: [] }
            : current;
        });
      },
      onReplyStream: function (chatId, event) {
        if (String(activeChatIdRef.current || "") !== String(chatId || "")) return;
        var payload = event && typeof event === "object" ? event : {};
        WbcVoice.autoStream(
          String(payload.text || ""),
          "auto-chat:" + String(chatId || ""),
          payload.done === true,
          payload.start === true
        );
      },
      onIntermediateMessage: function (chatId, message) {
        if (String(activeChatIdRef.current || "") !== String(chatId || "")) return;
        var item = message && typeof message === "object" ? message : {};
        WbcVoice.autoSpeak(
          String(item.content || ""),
          "auto-chat:" + String(chatId || ""),
          "intermediate:" + String(item.id || item.content || "")
        );
      },
      onAssistantSaved: function (chatId, assistantMessages, terminalEvent) {
        if (String(activeChatIdRef.current || "") === String(chatId || "")) {
          var terminalMessages = Array.isArray(assistantMessages) ? assistantMessages : [];
          var terminalMessage = null;
          for (var ti = terminalMessages.length - 1; ti >= 0; ti -= 1) {
            var candidate = terminalMessages[ti];
            if (candidate && candidate.role === "assistant" && String(candidate.content || "").trim()) {
              terminalMessage = candidate;
              break;
            }
          }
          if (terminalMessage) {
            WbcVoice.autoSpeakFinal(
              String(terminalMessage.content || ""),
              "auto-chat:" + String(chatId || ""),
              "final:" + String(terminalMessage.id || terminalMessage.content || "")
            );
          }
        }
        // A background conversation has no active React transcript to patch.
        // Persist the terminal messages into its detail cache before the
        // runtime is cleared so switching to it never paints a stale snapshot.
        var cachedChat = chatCache.details[chatId] || null;
        if (cachedChat) {
          chatCache.details[chatId] = wbcMergeSavedAssistantMessages(
            cachedChat,
            assistantMessages
          );
        }
        setActiveChat(function (prev) {
          if (!prev || prev.id !== chatId) return prev;
          return wbcMergeSavedAssistantMessages(prev, assistantMessages);
        });
        // Do not leave the rail waiting for an asynchronous list refresh to
        // discover the terminal state. This also invalidates any older list
        // request that could otherwise resurrect its `running` snapshot.
        var currentProjectId = String(projectIdRef.current || "");
        beginChatListRequest(currentProjectId);
        setChats(function (previous) {
          return previous.map(function (chat) {
            return String(chat && chat.id || "") === String(chatId || "")
              ? wbcSettleChatListItem(chat, "completed", terminalEvent)
              : chat;
          });
        });
      },
      onAgentArtifact: function (chatId, artifactEvent) {
        var attachment = artifactEvent && artifactEvent.attachment;
        if (!attachment) return;
        setActiveChat(function (prev) {
          if (!prev || prev.id !== chatId) return prev;
          var live = Array.isArray(prev.liveAgentArtifacts) ? prev.liveAgentArtifacts.slice() : [];
          var key = String(artifactEvent.artifactId || attachment.id || attachment.url || "");
          var index = live.findIndex(function (item) {
            return String(item && (item.artifactId || item.id || item.url) || "") === key;
          });
          var next = { ...attachment, artifactId: key };
          if (index >= 0) live[index] = { ...live[index], ...next };
          else live.push(next);
          return { ...prev, liveAgentArtifacts: live };
        });
      },
      onAgentUsageUpdated: function (chatId, payload) {
        setActiveChat(function (prev) {
          if (!prev || prev.id !== chatId) return prev;
          var usage = { ...(prev.usage || {}) };
          [["inputTokens", "prompt_tokens"], ["outputTokens", "completion_tokens"], ["totalTokens", "total_tokens"], ["used", "total_tokens"]].forEach(function (pair) {
            var value = Number(payload && payload[pair[0]] || 0);
            if (value > 0) usage[pair[1]] = value;
          });
          return { ...prev, usage: usage, liveAgentContextUsage: payload || {} };
        });
      },
      onAgentSessionUpdated: function (chatId, session) {
        setActiveChat(function (prev) {
          if (!prev || prev.id !== chatId) return prev;
          var next = { ...prev };
          if (session.sessionId) next.agent = { ...(prev.agent || {}), externalSessionId: session.sessionId, runtimeState: "ready" };
          if (session.commands.length) next.agentCommands = session.commands;
          if (session.mode != null) next.agentMode = session.mode;
          if (session.plan) next.activePlan = session.plan;
          if (session.configOption || session.configOptions.length) {
            var options = Array.isArray(prev.agentConfigOptions) ? prev.agentConfigOptions.slice() : [];
            var incomingOptions = session.configOptions.concat(session.configOption ? [session.configOption] : []);
            incomingOptions.forEach(function (incoming) {
              var id = String(incoming && incoming.id || "");
              var index = options.findIndex(function (item) { return String(item && item.id || "") === id; });
              if (id && index >= 0) options[index] = { ...options[index], ...incoming };
              else if (id) options.push(incoming);
            });
            next.agentConfigOptions = options;
          }
          return next;
        });
      },
      onAgentRequestResolved: function (chatId, event) {
        var payload = wbcAgentEventPayload(event);
        var requestId = String(payload.requestId || payload.request_id || "");
        setActiveChat(function (prev) {
          if (!prev || prev.id !== chatId || !prev.pendingQuestion) return prev;
          if (requestId && String(prev.pendingQuestion.id || "") !== requestId) return prev;
          return { ...prev, pendingQuestion: null, status: "running" };
        });
      },
      onAwaitingUser: function (chatId, pendingQuestion) {
        // The run paused for a permission / clarification answer — stash the
        // question so the composer shows an answer prompt instead of a reply.
        if (String(activeChatIdRef.current || "") === String(chatId || "") && pendingQuestion) {
          WbcVoice.autoSpeak(
            wbcVoiceQuestionText(pendingQuestion),
            "auto-chat:" + String(chatId || ""),
            "question:" + String(pendingQuestion.id || pendingQuestion.text || "")
          );
        }
        setActiveChat(function (prev) {
          if (!prev || prev.id !== chatId) return prev;
          return { ...prev, status: "idle", pendingQuestion: pendingQuestion || null };
        });
      },
      onInterrupted: function (chatId) {
        // The server emits this only after accepting the interruption. Clear
        // the stale persisted-looking state immediately; the interrupt request
        // also settles storage before its response completes.
        setActiveChat(function (prev) {
          if (!prev || prev.id !== chatId) return prev;
          return { ...prev, status: "idle" };
        });
        if (String(activeChatIdRef.current || "") === String(chatId || "")) WbcVoice.stop();
        if (String(retrySuppressedTurnRef.current && retrySuppressedTurnRef.current.chatId || "") === String(chatId || "")) {
          retrySuppressedTurnRef.current = { chatId: "", messageIds: [] };
        }
        setRetrySuppressedTurn(function (current) {
          return String(current && current.chatId || "") === String(chatId || "")
            ? { chatId: "", messageIds: [] }
            : current;
        });
        refreshChats();
      },
      onError: function (chatId, err, failureState) {
        var terminal = !!(failureState && failureState.terminal);
        var errorCode = String(err && err.code || "");
        var budgetError = errorCode.startsWith("budget_");
        if (budgetError && String(activeChatIdRef.current || "") === String(chatId || "")) {
          setErrorKind("message");
          setError(err);
        }
        if (terminal) {
          var cachedChat = chatCache.details[chatId];
          if (cachedChat) {
            chatCache.details[chatId] = { ...cachedChat, status: "idle", runStatus: "failed" };
          }
          setActiveChat(function (prev) {
            if (!prev || String(prev.id || "") !== String(chatId || "")) return prev;
            return { ...prev, status: "idle", runStatus: "failed" };
          });
          var currentProjectId = String(projectIdRef.current || "");
          beginChatListRequest(currentProjectId);
          setChats(function (previous) {
            return previous.map(function (chat) {
              return String(chat && chat.id || "") === String(chatId || "")
                ? wbcSettleChatListItem(chat, "failed", err)
                : chat;
            });
          });
        }
        if (terminal && String(activeChatIdRef.current || "") === String(chatId || "")) {
          setErrorKind("message");
          setError(err || wbcT("workbenchChat.agentError.failed", "Agent run failed"));
          WbcVoice.stop();
          if (String(retrySuppressedTurnRef.current && retrySuppressedTurnRef.current.chatId || "") === String(chatId || "")) {
            retrySuppressedTurnRef.current = { chatId: "", messageIds: [] };
          }
          setRetrySuppressedTurn(function (current) {
            return String(current && current.chatId || "") === String(chatId || "")
              ? { chatId: "", messageIds: [] }
              : current;
          });
        }
      },
      onSettled: function (chatId) {
        var hydrationSequence = beginChatHydration(chatId);
        model.getChat(chatId).then(function (chat) {
          if (!isCurrentChatHydration(chatId, hydrationSequence)) return;
          chatCache.details[chatId] = chat;
          if (activeChatIdRef.current === chatId) setActiveChat(chat);
        }).catch(function () {});
        refreshChats();
      },
      onResync: function (chatId) {
        // Stream ended without a `saved` event (e.g. interrupted) — re-pull.
        var hydrationSequence = beginChatHydration(chatId);
        model.getChat(chatId).then(function (chat) {
          if (!isCurrentChatHydration(chatId, hydrationSequence)) return;
          chatCache.details[chatId] = chat;
          if (activeChatIdRef.current === chatId) setActiveChat(chat);
        }).catch(function () {});
        refreshChats();
      },
    });
    return function () { runtimeEngine.setHooks(null); };
  });

  useWbcEffect(function () {
    if (
      activeChat
      && activeChat.id
      && activeChat.status === "running"
      && !runtimeEngine.isRunning(activeChat.id)
    ) {
      runtimeEngine.reconnect(activeChat.id, model);
    }
  }, [activeChat && activeChat.id, activeChat && activeChat.status]);

  function handleSend(input) {
    WbcVoice.stop();
    setError("");
    setErrorKind("load");
    var preparedInput = Object.assign({}, input || {});
    preparedInput.mode = wbcNormalizePermissionMode(
      preparedInput.mode,
      activeChat && activeChat.permissionMode
        ? activeChat.permissionMode
        : "auto"
    );
    return ensureChat().then(function (chatId) {
      setActiveChat(function (prev) {
        if (!prev || prev.id !== chatId) return prev;
        return { ...prev, permissionMode: preparedInput.mode };
      });
      // The engine owns the stream (so it outlives this page) and enforces a
      // single in-flight run per conversation.
      return runtimeEngine.start(chatId, preparedInput, model);
    }).catch(function (err) {
      setError(wbcErrorText(err));
    });
  }

  function handleAskSelection(text) {
    var quote = String(text || "").trim().slice(0, 12000);
    var parentChatId = String(activeChatIdRef.current || "");
    if (!quote || !parentChatId || parentChatId.indexOf("legacy:") === 0 || sideAgentCreating) {
      return Promise.resolve(null);
    }
    setSideAgentCreating(true);
    setSideVisible(true);
    return model.createSideAgent(parentChatId, quote).then(function (agent) {
      if (activeChatIdRef.current !== parentChatId) return agent;
      setSideAgents(function (current) {
        return current.some(function (item) { return item.id === agent.id; })
          ? current
          : current.concat([agent]);
      });
      setActiveSideAgentByChat(function (current) {
        return Object.assign({}, current, { [parentChatId]: agent.id });
      });
      setSideTab("side-agents");
      return agent;
    }).catch(function (err) {
      setErrorKind("message");
      setError(wbcErrorText(err));
      throw err;
    }).finally(function () {
      setSideAgentCreating(false);
    });
  }

  function updateSideAgent(nextAgent) {
    if (!nextAgent || !nextAgent.id) return;
    setSideAgents(function (current) {
      return current.map(function (item) {
        return item.id === nextAgent.id ? nextAgent : item;
      });
    });
  }

  function deleteSideAgent(agentId) {
    var id = String(agentId || "");
    if (!id) return Promise.resolve();
    WbcVoice.stop();
    return model.deleteChat(id).then(function () {
      setSideAgents(function (current) {
        var next = current.filter(function (item) { return item.id !== id; });
        var parentChatId = String(activeChatIdRef.current || "");
        setActiveSideAgentByChat(function (selection) {
          if (selection[parentChatId] !== id) return selection;
          var updated = Object.assign({}, selection);
          if (next.length) updated[parentChatId] = next[next.length - 1].id;
          else delete updated[parentChatId];
          return updated;
        });
        setSideAgentSplitByChat(function (openByChat) {
          if (openByChat[parentChatId] !== id) return openByChat;
          var updated = Object.assign({}, openByChat);
          delete updated[parentChatId];
          return updated;
        });
        if (!next.length) setSideTab("");
        return next;
      });
    }).catch(function (err) {
      setErrorKind("message");
      setError(wbcErrorText(err));
    });
  }

  function selectSideAgent(agentId) {
    var chatId = String(activeChatIdRef.current || "");
    var id = String(agentId || "");
    if (!chatId || !id) return;
    setActiveSideAgentByChat(function (current) {
      return Object.assign({}, current, { [chatId]: id });
    });
    setSideAgentSplitByChat(function (current) {
      return Object.assign({}, current, { [chatId]: id });
    });
    setArtifactSplitByChat(function (current) {
      if (!current[chatId]) return current;
      var updated = Object.assign({}, current);
      delete updated[chatId];
      return updated;
    });
    setChangeSplitByChat(function (current) {
      if (!current[chatId]) return current;
      var updated = Object.assign({}, current);
      delete updated[chatId];
      return updated;
    });
    setResourceSplitByChat(function (current) {
      if (!current[chatId]) return current;
      var updated = Object.assign({}, current);
      delete updated[chatId];
      return updated;
    });
    openPaneContent("side-agent", id, { side: "right" });
  }

  function selectArtifact(file) {
    var chatId = String(activeChatIdRef.current || "");
    var key = wbcArtifactFileKey(file);
    if (!chatId || !key) return;
    setArtifactSplitByChat(function (current) {
      return Object.assign({}, current, { [chatId]: key });
    });
    setSideAgentSplitByChat(function (current) {
      if (!current[chatId]) return current;
      var updated = Object.assign({}, current);
      delete updated[chatId];
      return updated;
    });
    setChangeSplitByChat(function (current) {
      if (!current[chatId]) return current;
      var updated = Object.assign({}, current);
      delete updated[chatId];
      return updated;
    });
    setResourceSplitByChat(function (current) {
      if (!current[chatId]) return current;
      var updated = Object.assign({}, current);
      delete updated[chatId];
      return updated;
    });
    openPaneContent("file", file, { side: "right" });
  }

  function selectChange(change) {
    var chatId = String(activeChatIdRef.current || "");
    if (!chatId || !change || !change.setId || !change.path) return;
    setChangeSplitByChat(function (current) {
      return Object.assign({}, current, { [chatId]: change });
    });
    setSideAgentSplitByChat(function (current) {
      if (!current[chatId]) return current;
      var updated = Object.assign({}, current);
      delete updated[chatId];
      return updated;
    });
    setArtifactSplitByChat(function (current) {
      if (!current[chatId]) return current;
      var updated = Object.assign({}, current);
      delete updated[chatId];
      return updated;
    });
    setResourceSplitByChat(function (current) {
      if (!current[chatId]) return current;
      var updated = Object.assign({}, current);
      delete updated[chatId];
      return updated;
    });
    openPaneContent("change", change, { side: "right" });
  }

  function selectResourceSplit(type, payload, skipPane) {
    var chatId = String(activeChatIdRef.current || "");
    if (!chatId || !type) return;
    setResourceSplitByChat(function (current) {
      return Object.assign({}, current, { [chatId]: { type: type, payload: payload } });
    });
    setSideAgentSplitByChat(function (current) {
      if (!current[chatId]) return current;
      var updated = Object.assign({}, current);
      delete updated[chatId];
      return updated;
    });
    setArtifactSplitByChat(function (current) {
      if (!current[chatId]) return current;
      var updated = Object.assign({}, current);
      delete updated[chatId];
      return updated;
    });
    setChangeSplitByChat(function (current) {
      if (!current[chatId]) return current;
      var updated = Object.assign({}, current);
      delete updated[chatId];
      return updated;
    });
    if (!skipPane) openPaneContent(type, payload, { side: "right" });
  }

  function splitStateSnapshot(chatId) {
    return {
      sideAgentId: sideAgentSplitByChat[chatId] || "",
      artifactKey: artifactSplitByChat[chatId] || "",
      change: changeSplitByChat[chatId] || null,
      resource: resourceSplitByChat[chatId] || null,
    };
  }

  function restoreSplitState(chatId, snapshot) {
    if (!chatId || !snapshot) return;
    function restoreEntry(setter, value) {
      setter(function (current) {
        var updated = Object.assign({}, current);
        if (value) updated[chatId] = value;
        else delete updated[chatId];
        return updated;
      });
    }
    restoreEntry(setSideAgentSplitByChat, snapshot.sideAgentId);
    restoreEntry(setArtifactSplitByChat, snapshot.artifactKey);
    restoreEntry(setChangeSplitByChat, snapshot.change);
    restoreEntry(setResourceSplitByChat, snapshot.resource);
  }

  function beginFloatingPanelSplit(openSplit, sourceChatId, sourceChatSnapshot) {
    var activeId = String(activeChatIdRef.current || "");
    var sourceId = String(sourceChatId || activeId);
    if (!activeId || !sourceId || typeof openSplit !== "function") return;
    var page = pageRef.current;
    var currentMainPane = page && page.querySelector(":scope > .wbc-main");
    var currentMainRect = currentMainPane && currentMainPane.getBoundingClientRect();
    var sourceChat = sourceChatSnapshot && String(sourceChatSnapshot.id || "") === sourceId
      ? sourceChatSnapshot
      : (chatCache.details[sourceId] || null);
    if (!floatingSplitRestoreRef.current) {
      floatingSplitRestoreRef.current = {
        // `chatId` is the temporary content owner and therefore the only
        // selection that should keep this restore transaction alive.
        chatId: sourceId,
        activeChatId: activeId,
        activeChat: activeChat && String(activeChat.id || "") === activeId
          ? activeChat
          : (chatCache.details[activeId] || null),
        splitSide: splitSide,
        // Opening a resource swaps the two track widths as well as their
        // contents. The promoted conversation therefore keeps the exact same
        // rectangle and can travel as one rigid pane instead of being scaled
        // or reflowed during the handoff. Closing restores this width.
        splitWidth: sideAgentSplitWidth,
        promotedResourceWidth: Math.round((currentMainRect && currentMainRect.width) || sideAgentSplitWidth),
        activeSplit: splitStateSnapshot(activeId),
        sourceSplit: sourceId === activeId ? null : splitStateSnapshot(sourceId),
      };
    }
    setFloatingConversationPanelOpen(false);
    // The conversation that opened the resource becomes the left pane while
    // the resource owns the right track. Swap the track widths in the same
    // atomic commit: the source and destination conversation rectangles then
    // have identical dimensions, so the shared layer performs only a rigid
    // horizontal translation of the complete conversation UI.
    function commitPromotion() {
      if (sourceId !== activeId) {
        // The split conversation already owns a complete, live transcript.
        // Hand that exact snapshot to the main pane in the same commit as the
        // selection change; otherwise the main pane paints an empty loading
        // state before its hydration effect can reuse the cache.
        if (sourceChat) {
          chatCache.details[sourceId] = sourceChat;
          setActiveChat(sourceChat);
          setChatLoading(false);
        }
        selectChat(sourceId);
        setSideAgentSplitWidth(wbcClampSideSplitWidthForPage(
          floatingSplitRestoreRef.current.promotedResourceWidth,
          pageRef.current
        ));
      }
      setSplitSideDirect("right");
      openSplit();
    }
    function commitPromotionNow() {
      if (window.ReactDOM && typeof window.ReactDOM.flushSync === "function") {
        window.ReactDOM.flushSync(commitPromotion);
      } else {
        commitPromotion();
      }
    }
    function promoteSourceAndOpenContent() {
      var reducedMotion = !!(window.matchMedia
        && window.matchMedia("(prefers-reduced-motion: reduce)").matches);
      var page = pageRef.current;
      var sourcePane = page && page.querySelector(".wbc-side-agent-split-motion.open .wbc-chat-split");
      var canTransitionHandoff = !!(
        sourceId !== activeId
        && !reducedMotion
        && sourcePane
        && document.startViewTransition
        && window.ReactDOM
        && typeof window.ReactDOM.flushSync === "function"
      );
      if (!canTransitionHandoff) {
        commitPromotionNow();
        return;
      }

      // The conversation changes React owners here (split pane -> main pane).
      // Give both complete panes one shared transition identity. Their widths
      // are swapped with the resource track in commitPromotion, so Chromium
      // translates this snapshot without resizing or reflowing it.
      var transitionName = "wbc-promoted-conversation";
      var displacedName = "wbc-displaced-conversation";
      var resourceName = "wbc-promoted-resource";
      var displacedPane = page && page.querySelector(":scope > .wbc-main");
      var targetPane = null;
      var targetResourcePane = null;
      var promotedViewport = wbcCaptureConversationViewport(sourcePane);
      sourcePane.style.viewTransitionName = transitionName;
      if (displacedPane) displacedPane.style.viewTransitionName = displacedName;
      document.documentElement.classList.add("wbc-split-view-transition");
      document.documentElement.classList.add("wbc-split-view-transition-opening");
      wbcPinPageSplitLayout(page);
      function clearTransitionIdentity() {
        sourcePane.style.viewTransitionName = "";
        if (displacedPane) displacedPane.style.viewTransitionName = "";
        if (targetPane) targetPane.style.viewTransitionName = "";
        if (targetResourcePane) {
          targetResourcePane.style.viewTransitionName = "";
          wbcReleasePinnedSplitMotion(targetResourcePane);
        }
        document.documentElement.classList.remove("wbc-split-view-transition");
        document.documentElement.classList.remove("wbc-split-view-transition-opening");
        wbcReleasePinnedPageSplitLayout(page);
      }
      try {
        var transition = document.startViewTransition(function () {
          // The old snapshot has already captured the split pane. Remove its
          // name before the final DOM is captured, then assign it to the main
          // pane created by the atomic promotion commit.
          sourcePane.style.viewTransitionName = "";
          if (displacedPane) displacedPane.style.viewTransitionName = "";
          commitPromotionNow();
          targetPane = pageRef.current && pageRef.current.querySelector(":scope > .wbc-main");
          wbcRestoreConversationViewport(targetPane, promotedViewport);
          if (targetPane) targetPane.style.viewTransitionName = transitionName;
          var resourceContent = pageRef.current && pageRef.current.querySelector(
            '.wbc-side-agent-split-motion[data-split-open="true"] .wbc-side-agent-split:not(.wbc-chat-split)'
          );
          targetResourcePane = resourceContent && resourceContent.closest(".wbc-side-agent-split-motion");
          if (targetResourcePane) {
            // Resource hosts normally enter on the next animation frame. Make
            // the final snapshot measurable now and suppress that independent
            // entrance, otherwise the resource is captured one track-width
            // offscreen and visibly slides again after the handoff.
            wbcPinSplitMotionOpen(targetResourcePane);
            targetResourcePane.style.viewTransitionName = resourceName;
          }
        });
        // Passive mount effects and transcript measurement can try to restore
        // the live tail after the atomic owner handoff. Reapply the visual
        // anchor once the new snapshot is ready and again before its overlay
        // is removed, so the final live pane cannot reveal another position.
        Promise.resolve(transition.ready).then(function () {
          wbcRestoreConversationViewport(targetPane, promotedViewport);
        }).catch(function () {});
        Promise.resolve(transition.finished).catch(function () {}).then(function () {
          wbcRestoreConversationViewport(targetPane, promotedViewport);
          clearTransitionIdentity();
        });
      } catch (error) {
        clearTransitionIdentity();
        commitPromotionNow();
      }
    }
    promoteSourceAndOpenContent();
  }

  function restoreFloatingPanelSplit() {
    var snapshot = floatingSplitRestoreRef.current;
    var chatId = String(activeChatIdRef.current || "");
    if (!snapshot || !chatId) return false;
    if (snapshot.chatId !== chatId) return false;
    var restoredChat = snapshot.activeChat || chatCache.details[snapshot.activeChatId] || null;
    function commitRestore() {
      floatingSplitRestoreRef.current = null;
      restoreSplitState(snapshot.activeChatId, snapshot.activeSplit);
      setSideAgentSplitWidth(wbcClampSideSplitWidthForPage(snapshot.splitWidth, pageRef.current));
      if (snapshot.chatId !== snapshot.activeChatId) {
        restoreSplitState(snapshot.chatId, snapshot.sourceSplit);
        if (restoredChat) {
          setActiveChat(restoredChat);
          setChatLoading(false);
        }
        selectChat(snapshot.activeChatId);
      }
      setSplitSideDirect(snapshot.splitSide === "left" ? "left" : "right");
    }
    function commitRestoreNow() {
      if (window.ReactDOM && typeof window.ReactDOM.flushSync === "function") {
        window.ReactDOM.flushSync(commitRestore);
      } else {
        commitRestore();
      }
    }
    var reducedMotion = !!(window.matchMedia
      && window.matchMedia("(prefers-reduced-motion: reduce)").matches);
    var page = pageRef.current;
    var sourcePane = page && page.querySelector(":scope > .wbc-main");
    var sourceResourcePane = page && page.querySelector(".wbc-side-agent-split-motion.open");
    var canTransitionRestore = !!(
      snapshot.chatId !== snapshot.activeChatId
      && !reducedMotion
      && sourcePane
      && document.startViewTransition
      && window.ReactDOM
      && typeof window.ReactDOM.flushSync === "function"
    );
    if (!canTransitionRestore) {
      commitRestoreNow();
      return true;
    }

    // Exact inverse of promotion: the current main conversation returns to
    // its original split rectangle, the resource fades out, and the displaced
    // main conversation fades back into the left track.
    var transitionName = "wbc-promoted-conversation";
    var displacedName = "wbc-displaced-conversation";
    var resourceName = "wbc-promoted-resource";
    var targetPane = null;
    var targetMainPane = null;
    var restoredViewport = wbcCaptureConversationViewport(sourcePane);
    sourcePane.style.viewTransitionName = transitionName;
    if (sourceResourcePane) sourceResourcePane.style.viewTransitionName = resourceName;
    document.documentElement.classList.add("wbc-split-view-transition");
    document.documentElement.classList.add("wbc-split-view-transition-closing");
    wbcPinPageSplitLayout(page);
    function clearRestoreTransitionIdentity() {
      sourcePane.style.viewTransitionName = "";
      if (sourceResourcePane) sourceResourcePane.style.viewTransitionName = "";
      if (targetPane) {
        targetPane.style.viewTransitionName = "";
        wbcReleasePinnedSplitMotion(targetPane.closest(".wbc-side-agent-split-motion"));
      }
      if (targetMainPane) targetMainPane.style.viewTransitionName = "";
      document.documentElement.classList.remove("wbc-split-view-transition");
      document.documentElement.classList.remove("wbc-split-view-transition-closing");
      wbcReleasePinnedPageSplitLayout(page);
    }
    try {
      var transition = document.startViewTransition(function () {
        sourcePane.style.viewTransitionName = "";
        if (sourceResourcePane) sourceResourcePane.style.viewTransitionName = "";
        commitRestoreNow();
        targetPane = pageRef.current && pageRef.current.querySelector(
          '.wbc-side-agent-split-motion[data-split-open="true"] .wbc-chat-split'
        );
        var targetMotion = targetPane && targetPane.closest(".wbc-side-agent-split-motion");
        // Capture the restored conversation at its settled right-hand
        // rectangle. Without pinning, the host's own enter transition makes
        // the shared layer target x=offscreen, so closing is not the inverse
        // of opening and the pane snaps back after the View Transition.
        wbcPinSplitMotionOpen(targetMotion);
        wbcRestoreConversationViewport(targetPane, restoredViewport);
        if (targetPane) targetPane.style.viewTransitionName = transitionName;
        targetMainPane = pageRef.current && pageRef.current.querySelector(":scope > .wbc-main");
        if (targetMainPane) targetMainPane.style.viewTransitionName = displacedName;
      });
      Promise.resolve(transition.ready).then(function () {
        wbcRestoreConversationViewport(targetPane, restoredViewport);
      }).catch(function () {});
      Promise.resolve(transition.finished).catch(function () {}).then(function () {
        wbcRestoreConversationViewport(targetPane, restoredViewport);
        clearRestoreTransitionIdentity();
      });
    } catch (error) {
      clearRestoreTransitionIdentity();
      commitRestoreNow();
    }
    return true;
  }

  function closeSideAgentSplit() {
    setFloatingConversationPanelOpen(false);
    if (restoreFloatingPanelSplit()) return;
    var chatId = String(activeChatIdRef.current || "");
    if (!chatId) return;
    setSideAgentSplitByChat(function (current) {
      if (!current[chatId]) return current;
      var updated = Object.assign({}, current);
      delete updated[chatId];
      return updated;
    });
  }

  function closeArtifactSplit() {
    setFloatingConversationPanelOpen(false);
    if (restoreFloatingPanelSplit()) return;
    var chatId = String(activeChatIdRef.current || "");
    if (!chatId) return;
    setArtifactSplitByChat(function (current) {
      if (!current[chatId]) return current;
      var updated = Object.assign({}, current);
      delete updated[chatId];
      return updated;
    });
  }

  function closeChangeSplit() {
    setFloatingConversationPanelOpen(false);
    if (restoreFloatingPanelSplit()) return;
    var chatId = String(activeChatIdRef.current || "");
    if (!chatId) return;
    setChangeSplitByChat(function (current) {
      if (!current[chatId]) return current;
      var updated = Object.assign({}, current);
      delete updated[chatId];
      return updated;
    });
  }

  function closeResourceSplit() {
    setFloatingConversationPanelOpen(false);
    if (restoreFloatingPanelSplit()) return;
    var chatId = String(activeChatIdRef.current || "");
    if (!chatId) return;
    var closingViewer = !!(
      resourceSplitByChat[chatId]
      && resourceSplitByChat[chatId].type === "viewer"
    );
    setResourceSplitByChat(function (current) {
      if (!current[chatId]) return current;
      var updated = Object.assign({}, current);
      delete updated[chatId];
      return updated;
    });
    if (closingViewer) {
      setViewerFile(null);
      setSideTab(function (current) { return current === "viewer" ? "" : current; });
    }
  }

  function closeMainConversationSplit() {
    setFloatingConversationPanelOpen(false);
    var sourceChatId = String(activeChatIdRef.current || "");
    var targetChatId = String(splitChatId || "");
    if (!sourceChatId || !targetChatId || sourceChatId === targetChatId) {
      closeActiveSplit();
      return;
    }
    // The split relationship is stored on the main conversation (A). Remove
    // that exact relationship before selecting B; closeResourceSplit cannot
    // be reused after selectChat because the active-id ref updates eagerly.
    setResourceSplitByChat(function (current) {
      if (!current[sourceChatId]) return current;
      var updated = Object.assign({}, current);
      delete updated[sourceChatId];
      return updated;
    });
    selectChat(targetChatId);
  }

  function closeActiveSplit() {
    setFloatingConversationPanelOpen(false);
    if (restoreFloatingPanelSplit()) return;
    closeSideAgentSplit();
    closeArtifactSplit();
    closeChangeSplit();
    closeResourceSplit();
  }

  // A rail chat dragged onto the right side (or the open split) is opened
  // beside the main conversation instead of replacing it. A dedicated drop
  // layer covers the right zone while a chat drag is in progress; the main
  // conversation column keeps its original drop-to-open behaviour untouched.
  var pageRef = useWbcRef(null);
  // Conversations and tasks share the exact same side-split target. Keep the
  // dragged kind only so that shared target can describe the card accurately.
  var [chatDragKind, setChatDragKind] = useWbcState("");
  var chatDragSession = !!chatDragKind;
  var [chatSideDropActive, setChatSideDropActive] = useWbcState(false);

  useWbcEffect(function () {
    function onDocumentDragStart(event) {
      // Bubbles after the rail's React onDragStart has populated the
      // dataTransfer, so the chat MIME is already visible here.
      if (wbcHasTaskDrag(event)) setChatDragKind("task");
      else if (wbcHasChatDrag(event)) setChatDragKind("chat");
      if (wbcHasResourceDrag(event)) setResourceDragSession(true);
    }
    // Derive the preview from the pointer's page-level position. The right
    // target and main grid both move when the preview reserves its split
    // track; relying on the target element's dragleave would interpret that
    // layout motion as the pointer leaving and expand the composer again.
    function onDocumentChatDragOver(event) {
      if (!wbcHasChatDrag(event) && !wbcHasTaskDrag(event)) return;
      var inside = wbcChatSideDropZone(event);
      setChatSideDropActive(function (current) {
        return current === inside ? current : inside;
      });
    }
    function onDocumentDragEnd() {
      setChatDragKind("");
      setResourceDragSession(false);
      setPaneCardDragId("");
      setPaneDropTarget(null);
      setChatSideDropActive(false);
    }
    document.addEventListener("dragstart", onDocumentDragStart);
    document.addEventListener("dragover", onDocumentChatDragOver, true);
    document.addEventListener("dragend", onDocumentDragEnd);
    document.addEventListener("drop", onDocumentDragEnd);
    return function () {
      document.removeEventListener("dragstart", onDocumentDragStart);
      document.removeEventListener("dragover", onDocumentChatDragOver, true);
      document.removeEventListener("dragend", onDocumentDragEnd);
      document.removeEventListener("drop", onDocumentDragEnd);
    };
  }, []);

  function paneCardDetachDescriptor(cardId, paneOverride) {
    var location = wbcPaneCardLocation(paneLayoutFor(), cardId);
    // The rendered card is authoritative. During a chat switch React can
    // commit the new card before the layout lookup closure observes the new
    // owner key; looking it up again then returns null and starts a visual
    // drag with no native session behind it.
    var pane = paneOverride || (location && location.card);
    if (!pane) return null;
    var descriptor = {
      kind: pane.kind,
      payload: pane.payload,
      ownerChatId: pane.ownerChatId || activeChatIdRef.current || "",
      project: project || null,
      title: "",
      items: [],
      agent: null,
      agents: [],
      draft: null,
    };
    if (pane.kind === "chat") {
      var detachedChatId = String(pane.payload || "");
      if (!detachedChatId) return null;
      var detachedChat = chatCache.details[detachedChatId]
        || (activeChat && String(activeChat.id || "") === detachedChatId ? activeChat : null);
      descriptor.title = detachedChat && detachedChat.title || wbcT("workbenchChat.chatSplitLabel", "Chat");
    } else if (pane.kind === "task") {
      var detachedTask = (Array.isArray(tasks) ? tasks : []).find(function (task) {
        return String(task && task.id || "") === String(pane.payload || "");
      });
      descriptor.title = detachedTask && detachedTask.title || wbcT("workbench.page.task", "Task");
    } else if (pane.kind === "file" || pane.kind === "viewer") {
      var detachedFile = pane.payload;
      descriptor.title = detachedFile && detachedFile.name || wbcT("workbenchChat.viewer", "Viewer");
      descriptor.items = artifactItems;
      var detachedDraftKey = wbcProjectFileDraftKey(detachedFile);
      descriptor.draft = detachedDraftKey && WBC_PROJECT_FILE_DRAFTS[detachedDraftKey]
        ? WBC_PROJECT_FILE_DRAFTS[detachedDraftKey]
        : null;
    } else if (pane.kind === "change") {
      descriptor.title = pane.payload && pane.payload.path || wbcT("workbenchChat.changes", "Changes");
    } else if (pane.kind === "map") {
      descriptor.title = wbcMapItemLabel(pane.payload) || wbcT("chat.side.map", "Map");
    } else if (pane.kind === "browser") {
      var browserState = wbcBrowserStateForChat(descriptor.ownerChatId);
      var browserTab = browserState && Array.isArray(browserState.tabs)
        ? browserState.tabs.find(function (tab) { return String(tab.id || "") === String(pane.payload || ""); })
        : null;
      descriptor.title = browserTab && (browserTab.title || browserTab.url) || wbcT("chat.side.browser", "Browser");
    } else if (pane.kind === "subagents") {
      descriptor.title = wbcT("workbenchChat.subagents", "Subagents");
    } else if (pane.kind === "terminal") {
      var detachedTerminal = terminals.find(function (terminal) {
        return String(terminal && terminal.id || "") === String(pane.payload || "");
      });
      descriptor.title = detachedTerminal && detachedTerminal.title || wbcT("terminal.title", "Terminal");
    } else if (pane.kind === "side-agent") {
      descriptor.agent = sideAgents.find(function (agent) {
        return String(agent && agent.id || "") === String(pane.payload || "");
      }) || null;
      descriptor.agents = sideAgents;
      descriptor.title = descriptor.agent && (descriptor.agent.title || descriptor.agent.sourceQuote)
        || wbcT("workbenchChat.sideAgent.tab", "Side questions");
    }
    return descriptor;
  }

  function preparePaneCardDetach(event, cardId, paneOverride) {
    if (!cardId) return null;
    var existing = paneCardDetachRef.current;
    var layoutOwnerChatId = String(activeChatIdRef.current || "");
    // A pointer release can be swallowed when it crosses a native window
    // boundary. Never reuse that gesture on the next pointerdown: doing so
    // skips beginDrag and makes the grip work only on alternating attempts.
    // The main process also replaces any previous session for this sender, so
    // beginning afresh is both idempotent and the only reliable ownership
    // model for pointer capture.
    clearPaneCardDetachSubscription(existing);
    paneCardDetachRef.current = null;
    var dragHandle = event && event.currentTarget;
    var card = dragHandle && dragHandle.closest ? dragHandle.closest(".wbc-pane-card") : null;
    var descriptor = paneCardDetachDescriptor(cardId, paneOverride);
    if (!card || !dragHandle || !descriptor) return null;
    var cardRect = card.getBoundingClientRect();
    var handleRect = dragHandle.getBoundingClientRect();
    var clientX = Number(event && event.clientX);
    var clientY = Number(event && event.clientY);
    var capturedHandleX = Number(dragHandle.dataset.wbcDragHandleX);
    var capturedHandleY = Number(dragHandle.dataset.wbcDragHandleY);
    var handleGrabX = Number.isFinite(capturedHandleX)
      ? Math.max(0, Math.min(handleRect.width, capturedHandleX))
      : Math.max(0, Math.min(handleRect.width, clientX - handleRect.left));
    var handleGrabY = Number.isFinite(capturedHandleY)
      ? Math.max(0, Math.min(handleRect.height, capturedHandleY))
      : Math.max(0, Math.min(handleRect.height, clientY - handleRect.top));
    var sourceLocation = wbcPaneCardLocation(paneLayoutFor(layoutOwnerChatId), cardId);
    var pendingDetach = {
      cardId: String(cardId),
      layoutOwnerChatId: layoutOwnerChatId,
      descriptor: descriptor,
      sourceSide: sourceLocation && sourceLocation.side || "left",
      sourceIndex: sourceLocation ? sourceLocation.index : 0,
      sourceBounds: {
        width: Math.round(cardRect.width),
        height: Math.round(cardRect.height),
      },
      grabOffset: {
        x: Math.round((handleRect.left - cardRect.left) + handleGrabX),
        y: Math.round((handleRect.top - cardRect.top) + handleGrabY),
      },
    };
    paneCardDetachRef.current = pendingDetach;
    var detachedBridge = window.cyrene && window.cyrene.detachedPane;
    if (detachedBridge && typeof detachedBridge.onCreated === "function") {
      pendingDetach.unsubscribeCreated = detachedBridge.onCreated(function (result) {
        if (!result
          || String(result.cardId || "") !== pendingDetach.cardId
          || String(result.layoutOwnerChatId || "") !== pendingDetach.layoutOwnerChatId) return;
        if (result.ok === false || result.detached !== true) {
          cancelPaneCardDetachment(pendingDetach);
          return;
        }
        completePaneCardDetachment(pendingDetach);
      });
    }
    if (detachedBridge && typeof detachedBridge.beginDrag === "function") {
      detachedBridge.beginDrag(paneCardDetachIpcPayload(pendingDetach)).then(function (result) {
        if (!result || result.ok !== false) return;
        cancelPaneCardDetachment(pendingDetach);
      }).catch(function () {
        cancelPaneCardDetachment(pendingDetach);
      });
    }
    return pendingDetach;
  }

  function handlePaneCardPointerDown(event, cardId, paneOverride) {
    if (event && event.button != null && event.button !== 0) return;
    handlePaneCardDragStart(event, cardId, paneOverride);
  }

  function handlePaneCardDragStart(event, cardId, paneOverride) {
    if (!cardId) return;
    var card = event.currentTarget && event.currentTarget.closest
      ? event.currentTarget.closest(".wbc-pane-card")
      : null;
    var dragHandle = event.currentTarget;
    if (!card || !dragHandle) return;
    var pendingDetach = preparePaneCardDetach(event, cardId, paneOverride);
    // Do not dim the workspace or mount a ghost unless this exact gesture has
    // a valid card descriptor and a matching native drag session request.
    if (!pendingDetach) return;
    setPaneCardDragId(String(cardId));
    if (paneCardDragImageCleanupRef.current) paneCardDragImageCleanupRef.current();

    // Never ask Chromium to snapshot the live scroll container. Its native
    // drag image can clip or blank the transcript near the bottom. Capture the
    // visible message anchor first, then restore it inside a detached clone so
    // the ghost always shows this exact conversation at its current viewport.
    var cardRect = card.getBoundingClientRect();
    var handleRect = dragHandle.getBoundingClientRect();
    var capturedClientX = Number(dragHandle.dataset.wbcDragClientX);
    var capturedClientY = Number(dragHandle.dataset.wbcDragClientY);
    var capturedHandleX = Number(dragHandle.dataset.wbcDragHandleX);
    var capturedHandleY = Number(dragHandle.dataset.wbcDragHandleY);
    var conversationViewport = wbcCaptureConversationViewport(card);
    var clonedCard = wbcClonePaneWithLiveState(card);
    var panePreview = clonedCard.clone;
    panePreview.classList.add("wbc-pane-card-drag-surface");
    panePreview.classList.remove("dragging");
    panePreview.removeAttribute("draggable");
    var ghost = document.createElement("div");
    ghost.className = "wbc-pane-card-drag-ghost";
    ghost.setAttribute("aria-hidden", "true");
    ghost.style.left = "0px";
    ghost.style.top = "0px";
    ghost.style.width = cardRect.width + "px";
    ghost.style.height = cardRect.height + "px";
    ghost.appendChild(panePreview);
    var sourceStyle = window.getComputedStyle(card);
    for (var propertyIndex = 0; propertyIndex < sourceStyle.length; propertyIndex += 1) {
      var propertyName = sourceStyle[propertyIndex];
      if (propertyName.indexOf("--") === 0) {
        ghost.style.setProperty(propertyName, sourceStyle.getPropertyValue(propertyName));
      }
    }
    var draggedPaneLocation = wbcPaneCardLocation(paneLayoutFor(), cardId);
    var draggedPaneDescriptor = draggedPaneLocation && draggedPaneLocation.card;
    var draggedChatId = draggedPaneDescriptor && draggedPaneDescriptor.kind === "chat"
      ? String(draggedPaneDescriptor.payload || "")
      : "";
    var railCard = draggedChatId
      ? Array.prototype.slice.call(document.querySelectorAll(".wbc-rail .wbc-chat-card")).find(function (candidate) {
          return String(candidate.dataset.chatId || "") === draggedChatId;
        })
      : null;
    var railElement = railCard && railCard.closest
      ? railCard.closest(".wbc-rail")
      : null;
    var railPreview = null;
    var railPreviewWidth = 0;
    var railPreviewHeight = 0;
    if (railCard) {
      var builtRailPreview = wbcBuildRailCardDragPreview(railCard, "wbc-pane-card-rail-drag-card");
      if (builtRailPreview) {
        railPreview = builtRailPreview.host;
        railPreviewWidth = Math.round(builtRailPreview.rect.width);
        railPreviewHeight = Math.round(builtRailPreview.rect.height);
        ghost.appendChild(railPreview);
      }
    }
    document.body.appendChild(ghost);
    clonedCard.restoreViewport();
    wbcRestoreConversationViewport(panePreview, conversationViewport);

    // Preserve the exact point pressed inside the grip. Moving the pointer
    // horizontally across the handle therefore moves the ghost with that same
    // handle point under the cursor instead of snapping to its centre.
    var eventClientX = Number(event.clientX);
    var eventClientY = Number(event.clientY);
    var initialClientX = Number.isFinite(eventClientX) && eventClientX !== 0
      ? eventClientX
      : capturedClientX;
    var initialClientY = Number.isFinite(eventClientY) && eventClientY !== 0
      ? eventClientY
      : capturedClientY;
    var handleGrabX = Number.isFinite(capturedHandleX)
      ? Math.max(0, Math.min(handleRect.width, capturedHandleX))
      : Math.max(0, Math.min(handleRect.width, initialClientX - handleRect.left));
    var handleGrabY = Number.isFinite(capturedHandleY)
      ? Math.max(0, Math.min(handleRect.height, capturedHandleY))
      : Math.max(0, Math.min(handleRect.height, initialClientY - handleRect.top));
    var grabX = (handleRect.left - cardRect.left) + handleGrabX;
    var grabY = (handleRect.top - cardRect.top) + handleGrabY;
    var detachedBridge = window.cyrene && window.cyrene.detachedPane;
    var railGrabX = railPreviewWidth && handleRect.width
      ? railPreviewWidth * (handleGrabX / handleRect.width)
      : 0;
    var railGrabY = railPreviewHeight && handleRect.height
      ? railPreviewHeight * (handleGrabY / handleRect.height)
      : 0;
    if (railPreview) {
      // Keep the outer ghost at the pane's fixed dimensions. The compact card
      // is offset within it so both preview layers place their mapped grab
      // point under the same pointer without animating any layout dimension.
      railPreview.style.left = (grabX - railGrabX) + "px";
      railPreview.style.top = (grabY - railGrabY) + "px";
    }
    var ghostOverRail = false;
    var pointerPaneTarget = null;
    var lastPointerPosition = {
      clientX: initialClientX,
      clientY: initialClientY,
      screenX: Number(event && event.screenX),
      screenY: Number(event && event.screenY),
    };
    function pointerIsOverRail(clientX, clientY) {
      if (!railPreview || !railElement) return false;
      var rect = railElement.getBoundingClientRect();
      return !!(rect
        && clientX >= rect.left && clientX <= rect.right
        && clientY >= rect.top && clientY <= rect.bottom);
    }
    function pointerIsOverMatchingRailCard(clientX, clientY) {
      var rect = railCard && railCard.getBoundingClientRect();
      return !!(rect
        && clientX >= rect.left && clientX <= rect.right
        && clientY >= rect.top && clientY <= rect.bottom);
    }
    function setPaneCardGhostRailMode(overRail, overMatchingCard) {
      if (ghostOverRail !== overRail) {
        ghostOverRail = overRail;
        ghost.classList.toggle("rail-card", overRail);
        if (railCard) railCard.classList.toggle("dragging", overRail);
      }
      if (railCard) railCard.classList.toggle("wbc-split-return-target", !!overMatchingCard);
    }
    function paneTargetAt(clientX, clientY) {
      var layout = paneLayoutFor();
      var sourceLocation = wbcPaneCardLocation(layout, cardId);
      var sourceSide = sourceLocation && sourceLocation.side;
      var oppositeSide = sourceSide === "left" ? "right" : "left";
      var sourceStack = sourceSide ? (layout[sourceSide] || []) : [];
      var layoutElement = card && card.closest ? card.closest(".wbc-pane-layout") : null;
      var layoutRect = layoutElement && layoutElement.getBoundingClientRect();
      if (
        sourceLocation
        && sourceStack.length === 2
        && !(layout[oppositeSide] || []).length
        && layoutRect && layoutRect.width > 0
        && clientX >= layoutRect.left && clientX <= layoutRect.right
        && clientY >= layoutRect.top && clientY <= layoutRect.bottom
      ) {
        var relativeX = (clientX - layoutRect.left) / layoutRect.width;
        var axisEdge = relativeX < 0.34 ? "left" : (relativeX > 0.66 ? "right" : "");
        if (axisEdge) {
          var companionCard = sourceStack[sourceLocation.index === 0 ? 1 : 0];
          return {
            cardId: String(companionCard && companionCard.id || ""),
            dropKey: "axis:" + axisEdge,
            edge: axisEdge,
          };
        }
      }
      var elements = typeof document.elementsFromPoint === "function"
        ? document.elementsFromPoint(clientX, clientY)
        : [document.elementFromPoint(clientX, clientY)];
      var targetCard = null;
      for (var elementIndex = 0; elementIndex < elements.length; elementIndex += 1) {
        var candidate = elements[elementIndex] && elements[elementIndex].closest
          ? elements[elementIndex].closest(".wbc-pane-card")
          : null;
        if (candidate && String(candidate.dataset.paneCardId || "") !== String(cardId || "")) {
          targetCard = candidate;
          break;
        }
      }
      if (!targetCard) return null;
      var targetId = String(targetCard.dataset.paneCardId || "");
      var location = wbcPaneCardLocation(layout, targetId);
      if (!location) return null;
      var rect = targetCard.getBoundingClientRect();
      var relativeY = rect.height > 0 ? (clientY - rect.top) / rect.height : 0.5;
      var edge = (layout[location.side] || []).length >= 2
        ? "replace"
        : (relativeY < 0.34 ? "top" : (relativeY > 0.66 ? "bottom" : "replace"));
      return {
        cardId: targetId,
        dropKey: String(targetCard.dataset.paneDropKey || targetId),
        edge: edge,
      };
    }
    function movePaneCardGhost(moveEvent) {
      var clientX = Number(moveEvent && moveEvent.clientX);
      var clientY = Number(moveEvent && moveEvent.clientY);
      if (!Number.isFinite(clientX) || !Number.isFinite(clientY) || (clientX === 0 && clientY === 0)) return;
      lastPointerPosition = {
        clientX: clientX,
        clientY: clientY,
        screenX: Number(moveEvent && moveEvent.screenX),
        screenY: Number(moveEvent && moveEvent.screenY),
      };
      if (detachedBridge && typeof detachedBridge.updateDrag === "function") {
        detachedBridge.updateDrag({
          clientX: clientX,
          clientY: clientY,
          screenX: Number(moveEvent && moveEvent.screenX),
          screenY: Number(moveEvent && moveEvent.screenY),
          viewportWidth: window.innerWidth,
          viewportHeight: window.innerHeight,
        });
      }
      var overRail = pointerIsOverRail(clientX, clientY);
      var overMatchingCard = pointerIsOverMatchingRailCard(clientX, clientY);
      setPaneCardGhostRailMode(overRail, overMatchingCard);
      pointerPaneTarget = overRail ? null : paneTargetAt(clientX, clientY);
      setPaneDropTarget(pointerPaneTarget);
      // Accept the drag everywhere while this renderer-owned preview is
      // active. A release outside a real target remains a no-op, but Chromium
      // no longer runs its cancelled-drag snap-back before emitting drop/end.
      if (moveEvent && typeof moveEvent.preventDefault === "function") {
        moveEvent.preventDefault();
        if (moveEvent.dataTransfer) moveEvent.dataTransfer.dropEffect = "move";
      }
      ghost.style.transform = "translate3d(" + (clientX - grabX) + "px, " + (clientY - grabY) + "px, 0)";
    }
    var ghostCleared = false;
    var ghostRetired = false;
    function retirePaneCardGhost() {
      if (ghostRetired) return;
      ghostRetired = true;
      ghost.style.visibility = "hidden";
      function detachGhost() {
        if (ghost.parentNode) ghost.parentNode.removeChild(ghost);
      }
      if (typeof window.requestIdleCallback === "function") {
        window.requestIdleCallback(detachGhost, { timeout: 300 });
      } else {
        setTimeout(detachGhost, 80);
      }
    }
    function clearPaneCardGhost() {
      if (ghostCleared) return;
      ghostCleared = true;
      document.removeEventListener("pointermove", movePaneCardGhost, true);
      document.removeEventListener("pointerup", finishPaneCardGhost, true);
      document.removeEventListener("pointercancel", finishPaneCardGhost, true);
      document.removeEventListener("lostpointercapture", finishLostPaneCardCapture, true);
      if (railCard) railCard.classList.remove("dragging", "wbc-split-return-target");
      // Start a compositor-owned fade before React clears drag state. Unlike a
      // CSS-only class change, Web Animations is submitted immediately and
      // keeps progressing while the pane/drop overlays reconcile.
      var reducedMotion = !!(window.matchMedia
        && window.matchMedia("(prefers-reduced-motion: reduce)").matches);
      var fadeAnimation = typeof ghost.animate === "function"
        ? ghost.animate([
            { opacity: 0.92 },
            { opacity: 0 },
          ], {
            duration: reducedMotion ? 0 : 72,
            easing: "cubic-bezier(.4, 0, 1, 1)",
            fill: "forwards",
          })
        : null;
      if (fadeAnimation) {
        Promise.resolve(fadeAnimation.finished).then(retirePaneCardGhost).catch(retirePaneCardGhost);
      } else {
        ghost.addEventListener("transitionend", function (fadeEvent) {
          if (fadeEvent.propertyName === "opacity") retirePaneCardGhost();
        }, { once: true });
      }
      ghost.classList.add("releasing");
      setTimeout(retirePaneCardGhost, reducedMotion ? 0 : 120);
      if (paneCardDragImageCleanupRef.current === clearPaneCardGhost) {
        paneCardDragImageCleanupRef.current = null;
      }
    }
    function finishPaneCardGhost(dropEvent) {
      var dropClientX = Number(dropEvent && dropEvent.clientX);
      var dropClientY = Number(dropEvent && dropEvent.clientY);
      if (!Number.isFinite(dropClientX)) dropClientX = lastPointerPosition.clientX;
      if (!Number.isFinite(dropClientY)) dropClientY = lastPointerPosition.clientY;
      var droppedOnRail = pointerIsOverRail(dropClientX, dropClientY);
      var droppedOnMatchingCard = pointerIsOverMatchingRailCard(
        dropClientX,
        dropClientY
      );
      if (droppedOnRail) {
        if (dropEvent && typeof dropEvent.preventDefault === "function") dropEvent.preventDefault();
        if (dropEvent && typeof dropEvent.stopImmediatePropagation === "function") dropEvent.stopImmediatePropagation();
      }
      clearPaneCardGhost();
      // Returning a split conversation to its list is the inverse of opening
      // that split. Files and other content have no conversation-row preview,
      // so they keep the normal cancelled-drop behavior over the rail.
      if (droppedOnMatchingCard && draggedChatId) {
        closePaneCard(cardId);
      } else if (pointerPaneTarget) {
        placeExistingPaneCard(cardId, pointerPaneTarget.cardId, pointerPaneTarget.edge);
      }
      handlePaneCardDragEnd(dropEvent, {
        cancel: false,
        screenX: Number.isFinite(Number(dropEvent && dropEvent.screenX))
          ? Number(dropEvent.screenX)
          : lastPointerPosition.screenX,
        screenY: Number.isFinite(Number(dropEvent && dropEvent.screenY))
          ? Number(dropEvent.screenY)
          : lastPointerPosition.screenY,
      });
    }
    function finishLostPaneCardCapture(captureEvent) {
      if (captureEvent && captureEvent.target !== dragHandle) return;
      finishPaneCardGhost(captureEvent || lastPointerPosition);
    }
    // Keep cleanup on the stable document root. React may reconcile the card
    // while the pointer is captured; listeners attached only to the old grip
    // then miss pointerup and leave every following gesture cleaning up the
    // previous one first.
    document.addEventListener("pointermove", movePaneCardGhost, true);
    document.addEventListener("pointerup", finishPaneCardGhost, true);
    document.addEventListener("pointercancel", finishPaneCardGhost, true);
    document.addEventListener("lostpointercapture", finishLostPaneCardCapture, true);
    paneCardDragImageCleanupRef.current = clearPaneCardGhost;
    movePaneCardGhost(event);
  }

  function handlePaneCardDragEnd(_event, options) {
    if (paneCardDragImageCleanupRef.current) paneCardDragImageCleanupRef.current();
    setPaneCardDragId("");
    setPaneDropTarget(null);
    var pendingDetach = paneCardDetachRef.current;
    var detachedBridge = window.cyrene && window.cyrene.detachedPane;
    if (!pendingDetach || !detachedBridge || typeof detachedBridge.finishDrag !== "function") {
      clearPaneCardDetachSubscription(pendingDetach);
      if (paneCardDetachRef.current === pendingDetach) paneCardDetachRef.current = null;
      return;
    }
    detachedBridge.finishDrag(paneCardDetachIpcPayload(pendingDetach, {
      cancel: !!(options && options.cancel),
      screenX: Number(options && options.screenX),
      screenY: Number(options && options.screenY),
    })).then(function (result) {
      if (result && result.pending === true) return;
      if (result && result.ok !== false && result.detached === true) {
        completePaneCardDetachment(pendingDetach);
        return;
      }
      cancelPaneCardDetachment(pendingDetach);
    }).catch(function () {
      cancelPaneCardDetachment(pendingDetach);
    });
  }

  function placeExistingPaneCard(sourceCardId, targetCardId, edge) {
    var layout = paneLayoutFor();
    var source = wbcPaneCardLocation(layout, sourceCardId);
    var target = wbcPaneCardLocation(layout, targetCardId);
    if (!source || !target || String(sourceCardId || "") === String(targetCardId || "")) return;
    var axisEdge = edge === "left" || edge === "right";
    var effectiveEdge = axisEdge
      ? edge
      : ((layout[target.side] || []).length >= 2 ? "replace" : edge);
    updatePaneLayout(function (current) {
      var currentSource = wbcPaneCardLocation(current, sourceCardId);
      var currentTarget = wbcPaneCardLocation(current, targetCardId);
      if (!currentSource || !currentTarget) return current;
      // Moving one member of an existing vertical pair over the other is an
      // order change, not destructive replacement.
      if (!axisEdge && currentSource.side === currentTarget.side && current[currentTarget.side].length === 2) {
        var reordered = {
          left: current.left.slice(),
          right: current.right.slice(),
          leftRatio: current.leftRatio,
          rightRatio: current.rightRatio,
        };
        if (currentSource.index !== currentTarget.index) reordered[currentTarget.side].reverse();
        return reordered;
      }
      return wbcPlacePaneCard(
        current,
        currentSource.card,
        currentTarget.side,
        effectiveEdge,
        sourceCardId,
        targetCardId
      );
    });
    setPaneCardDragId("");
    setPaneDropTarget(null);
  }

  function handlePaneDropOver(event, cardId, edge, dropKey) {
    if (!wbcHasSplitDrag(event) && !wbcHasChatDrag(event) && !wbcHasTaskDrag(event) && !wbcHasResourceDrag(event)) return;
    event.preventDefault();
    event.stopPropagation();
    // The card-local five-way target supersedes the legacy page-level side
    // preview. Clear that preview as soon as the pointer enters a pane card so
    // only one geometry and one visual language can be shown at a time.
    if (chatSideDropActive) setChatSideDropActive(false);
    if (resourceSplitDropSide) setResourceSplitDropSide("");
    if (event.dataTransfer) {
      event.dataTransfer.dropEffect = wbcHasSplitDrag(event) ? "move" : "copy";
    }
    var nextEdge = edge === "top" || edge === "left" || edge === "right"
      ? edge
      : (edge === "replace" ? "replace" : "bottom");
    var next = { cardId: String(cardId), dropKey: String(dropKey || cardId), edge: nextEdge };
    if (!paneDropTarget || paneDropTarget.dropKey !== next.dropKey || paneDropTarget.edge !== next.edge) {
      setPaneDropTarget(next);
    }
  }

  function handlePaneDrop(event, targetCardId, edge) {
    if (!wbcHasSplitDrag(event) && !wbcHasChatDrag(event) && !wbcHasTaskDrag(event) && !wbcHasResourceDrag(event)) return;
    event.preventDefault();
    event.stopPropagation();
    var layout = paneLayoutFor();
    var target = wbcPaneCardLocation(layout, targetCardId);
    if (!target) return;
    // A vertical pair has reached the two-level limit. Even if a stale drag
    // event still carries a top/bottom edge, the only valid operation is to
    // replace the card currently under the pointer.
    var effectiveEdge = (layout[target.side] || []).length >= 2 ? "replace" : edge;
    var sourceCardId = "";
    var card = null;
    var droppedChatSelection = "";
    var splitPayload = wbcReadSplitDrag(event);
    if (splitPayload && splitPayload.kind === "pane-card") {
      sourceCardId = String(splitPayload.cardId || "");
      var source = wbcPaneCardLocation(layout, sourceCardId);
      card = source && source.card;
    } else if (wbcHasChatDrag(event)) {
      var chatPayload = wbcReadChatDrag(event);
      if (chatPayload && chatPayload.id) {
        var draggedChatId = String(chatPayload.id);
        if (wbcChatDropReplacesActiveConversation(
          target,
          effectiveEdge,
          draggedChatId,
          activeChatIdRef.current
        )) droppedChatSelection = draggedChatId;
        var canonicalChatCardId = "chat:" + draggedChatId;
        var existingChat = wbcPaneCardLocation(layout, canonicalChatCardId);
        var replacingSameChatCard = existingChat
          && String(existingChat.card && existingChat.card.id || "") === String(targetCardId || "")
          && effectiveEdge === "replace";
        // A rail drag opens another view; only a pane-grip drag moves an
        // existing card. Keep replacing the exact same card as a no-op.
        sourceCardId = replacingSameChatCard ? canonicalChatCardId : "";
        card = replacingSameChatCard
          ? existingChat.card
          : wbcPaneCard("chat", draggedChatId, {
              id: existingChat ? undefined : canonicalChatCardId,
              ownerChatId: draggedChatId,
              freshInstance: !!existingChat,
            });
      }
    } else if (wbcHasTaskDrag(event)) {
      var taskPayload = wbcReadTaskDrag(event);
      if (taskPayload && taskPayload.id) {
        var draggedTaskId = String(taskPayload.id);
        var canonicalTaskCardId = "task:" + draggedTaskId;
        var existingTask = wbcPaneCardLocation(layout, canonicalTaskCardId);
        var replacingSameTaskCard = existingTask
          && String(existingTask.card && existingTask.card.id || "") === String(targetCardId || "")
          && effectiveEdge === "replace";
        sourceCardId = replacingSameTaskCard ? canonicalTaskCardId : "";
        card = replacingSameTaskCard
          ? existingTask.card
          : wbcPaneCard("task", draggedTaskId, {
              id: existingTask ? undefined : canonicalTaskCardId,
              ownerChatId: activeChatIdRef.current || projectPaneOwnerKey(),
              freshInstance: !!existingTask,
            });
        if (onSelectTask) onSelectTask(draggedTaskId);
      }
    } else if (wbcHasResourceDrag(event)) {
      var resource = wbcReadResourceDrag(event);
      if (resource && resource.kind === "file") {
        var file = resource.file && Object.keys(resource.file).length ? resource.file : resource;
        card = paneContentCard("file", file, activeChatIdRef.current);
      } else if (resource && resource.kind === "terminal" && resource.terminalId) {
        setActiveTerminalId(String(resource.terminalId));
        terminalClient.activate(projectId, String(resource.terminalId)).catch(function () {});
        card = paneContentCard("terminal", String(resource.terminalId), activeChatIdRef.current);
      }
    }
    if (!card) return;
    // Stop compositor tracking before React recalculates the pane grid. The
    // hidden clone is detached asynchronously by its cleanup routine.
    if (paneCardDragImageCleanupRef.current) paneCardDragImageCleanupRef.current();
    if (droppedChatSelection) {
      // Replacing the canonical main-conversation card (whether it is an
      // empty draft or an existing conversation) is navigation, not a nested
      // chat-card mutation. Keeping the old owner id would make the dropped
      // transcript appear inside the current chat's workspace and would hide
      // its docked conversation panel as if a split were open.
      setPaneCardDragId("");
      setResourceDragSession(false);
      setChatDragKind("");
      setPaneDropTarget(null);
      selectChat(droppedChatSelection);
      return;
    }
    updatePaneLayout(function (current) {
      return wbcPlacePaneCard(current, card, target.side, effectiveEdge, sourceCardId, targetCardId);
    });
    setPaneCardDragId("");
    setResourceDragSession(false);
    setChatDragKind("");
    setPaneDropTarget(null);
  }

  function handleSideLayerDragOver(event) {
    if (!wbcHasChatDrag(event) && !wbcHasTaskDrag(event)) return;
    event.preventDefault();
    event.stopPropagation();
    if (event.dataTransfer) event.dataTransfer.dropEffect = "move";
    setChatSideDropActive(true);
  }

  function handleSideLayerDrop(event) {
    if (!wbcHasChatDrag(event) && !wbcHasTaskDrag(event)) return;
    event.preventDefault();
    event.stopPropagation();
    setChatSideDropActive(false);
    setChatDragKind("");
    if (wbcHasTaskDrag(event)) {
      var taskPayload = wbcReadTaskDrag(event);
      if (taskPayload && taskPayload.id) {
        if (onSelectTask) onSelectTask(String(taskPayload.id));
        openPaneContent("task", String(taskPayload.id), { side: "right" });
      }
      return;
    }
    var payload = wbcReadChatDrag(event);
    if (payload && payload.id) {
      var droppedChatId = String(payload.id);
      if (activeChatIdRef.current) openChatSplit(droppedChatId);
      else openPaneContent("chat", droppedChatId, { side: "right" });
    }
  }


  function openChatSplit(chatId) {
    var parentId = String(activeChatIdRef.current || "");
    if (!parentId || !chatId) return;
    selectResourceSplit("chat", String(chatId));
    setSideVisible(true);
    // The drop zone lives on the right side, so a drag-opened split should
    // appear there regardless of the remembered side preference.
    setSplitSideDirect("right");
  }

  function resizeSideAgentSplit(width) {
    var next = wbcClampSideSplitWidthForPage(width, pageRef.current);
    if (!next) return;
    setSideAgentSplitWidth(next);
    try { localStorage.setItem("wbc-side-agent-split-width", String(next)); } catch (e) {}
  }

  function resizePaneColumn(width) {
    var next = Math.round(Number(width) || 520);
    if (!next) return;
    setPaneColumnWidth(next);
    try { localStorage.setItem("wbc-pane-column-width", String(next)); } catch (e) {}
  }

  function handleInterrupt() {
    runtimeEngine.interrupt(activeChatIdRef.current, model);
  }

  function handleGuidance(message) {
    var chatId = activeChatIdRef.current;
    var text = String(message || "").trim();
    if (!chatId || !text || !runtimeEngine.isRunning(chatId)) return Promise.resolve(null);
    var clientRequestId = "guide_" + Date.now();
    var optimisticMessage = {
      id: "guidance_pending_" + clientRequestId,
      role: "user",
      content: text,
      createdAt: new Date().toISOString(),
      guidance: true,
      optimistic: true,
      clientRequestId: clientRequestId,
    };
    setError("");
    runtimeEngine.closeTimeline(chatId);
    runtimeEngine.recordUserMessage(chatId, optimisticMessage);
    setActiveChat(function (prev) {
      if (!prev || prev.id !== chatId) return prev;
      return { ...prev, messages: wbcMergeChronologicalMessages(prev.messages || [], [optimisticMessage]) };
    });
    return model.sendGuidance(chatId, text, clientRequestId).then(function (response) {
      if (response && response.userMessage) {
        runtimeEngine.recordUserMessage(chatId, response.userMessage, optimisticMessage.id);
        setActiveChat(function (prev) {
          if (!prev || prev.id !== chatId) return prev;
          return { ...prev, messages: wbcMergeChronologicalMessages(prev.messages || [], [response.userMessage]) };
        });
      }
      return response;
    }).catch(function (err) {
      setActiveChat(function (prev) {
        if (!prev || prev.id !== chatId) return prev;
        return {
          ...prev,
          messages: (prev.messages || []).filter(function (item) {
            return String(item && item.clientRequestId || "") !== clientRequestId;
          }),
        };
      });
      if (err && err.code === "chat_not_running") {
        runtimeEngine.deferSend(chatId, { message: text }, model);
        return { deferred: true };
      }
      setErrorKind("message");
      setError(wbcErrorText(err));
      throw err;
    });
  }

  // Answer the pending permission / clarification question → resume the round.
  // The server returns the continued reply (append it) or a follow-up question
  // (swap the prompt). Optimistically clears the prompt while resuming.
  function answerQuestionForChat(chatId, questionId, optionText, resumeMode) {
    chatId = String(chatId || "");
    var formAnswer = optionText && typeof optionText === "object" && optionText.__agentForm === true;
    if (!chatId || !questionId || (!formAnswer && !optionText)) return Promise.resolve(null);
    WbcVoice.stop();
    var targetSummary = chatsRef.current.find(function (chat) { return String(chat && chat.id || "") === chatId; }) || {};
    var targetDetail = activeChatIdRef.current === chatId ? (activeChat || {}) : (chatCache.details[chatId] || {});
    var liveAgentRequest = targetDetail.pendingQuestion || targetSummary.pendingQuestion || null;
    if (activeChatIdRef.current === chatId) setError("");
    if (wbcIsLiveAgentRequest(liveAgentRequest)) {
      var response = String(liveAgentRequest.kind || "") === "permission.requested"
        ? { type: "option", optionId: String(optionText || "") }
        : (formAnswer
          ? { type: "form", form: optionText.values && typeof optionText.values === "object" ? optionText.values : {} }
          : { type: "text", text: String(optionText || "") });
      setChats(function (previous) {
        return previous.map(function (chat) {
          return String(chat && chat.id || "") === chatId
            ? { ...chat, pendingQuestion: null, status: "running", runStatus: "running" }
            : chat;
        });
      });
      setActiveChat(function (prev) {
        return prev && String(prev.id || "") === chatId
          ? { ...prev, pendingQuestion: null, status: "running" }
          : prev;
      });
      return model.answerAgentRequest(chatId, questionId, response).catch(function (err) {
        setActiveChat(function (prev) {
          return prev && String(prev.id || "") === chatId
            ? { ...prev, pendingQuestion: liveAgentRequest, status: "idle" }
            : prev;
        });
        if (activeChatIdRef.current === chatId) setError(wbcErrorText(err));
        throw err;
      });
    }
    var optimisticAnswer = {
      id: "answer_pending_" + Date.now(),
      role: "user",
      content: optionText,
      createdAt: new Date().toISOString(),
      answerToQuestionId: questionId,
      optimistic: true,
    };
    setChats(function (previous) {
      return previous.map(function (chat) {
        return String(chat && chat.id || "") === chatId
          ? { ...chat, pendingQuestion: null, status: "running", runStatus: "running" }
          : chat;
      });
    });
    var cachedTarget = chatCache.details[chatId];
    if (cachedTarget) {
      chatCache.details[chatId] = {
        ...cachedTarget,
        pendingQuestion: null,
        status: "running",
        messages: wbcMergeChronologicalMessages(cachedTarget.messages || [], [optimisticAnswer]),
      };
    }
    setActiveChat(function (prev) {
      if (!prev || String(prev.id || "") !== chatId) return prev;
      return {
        ...prev,
        pendingQuestion: null,
        status: "running",
        messages: wbcMergeChronologicalMessages(prev.messages || [], [optimisticAnswer]),
      };
    });
    // Drive a live runtime for the resume so the thread streams the same feedback
    // as a normal send: the "Thinking..." card renders immediately and SSE tool
    // progress folds into it (onSseEvent only fills a runtime that already exists).
    // Without it the resume ran invisibly — an empty thread while the side panel
    // showed a frozen "Replying" — and the composer offered no way to stop it.
    var answerStartedAt = Date.parse(String(optimisticAnswer.createdAt || "")) || Date.now();
    runtimeEngine.update(chatId, { chatId: chatId, text: "", progress: [], activities: [], activitySeq: 0, segments: [], notifications: [], userMessages: [optimisticAnswer], startedAt: answerStartedAt, lastEventAt: answerStartedAt, replying: true });
    var targetPermissionMode = activeChatIdRef.current === chatId && activeChat && activeChat.permissionMode
      ? activeChat.permissionMode
      : targetSummary.permissionMode;
    var answerMode = wbcNormalizePermissionMode(
      resumeMode,
      targetPermissionMode
        ? targetPermissionMode
        : "default"
    );
    var answerSettled = false;
    return model.answerChat(chatId, questionId, optionText, { mode: answerMode }).then(function (result) {
      answerSettled = true;
      var terminalStatus = result && result.interrupted
        ? "cancelled"
        : (result && result.awaitingUser ? "awaiting_user" : "completed");
      runtimeEngine.publishLifecycle(chatId, terminalStatus, result || {});
      runtimeEngine.update(chatId, null);
      beginChatListRequest(String(projectIdRef.current || ""));
      setChats(function (previous) {
        return previous.map(function (chat) {
          return String(chat && chat.id || "") === chatId
            ? wbcSettleChatListItem(chat, terminalStatus, result)
            : chat;
        });
      });
      // Pull the durable transcript: it now contains the question, this answer,
      // every pre-question tool/intermediate block, and the continued reply.
      var hydrationSequence = beginChatHydration(chatId);
      return model.getChat(chatId).then(function (chat) {
        if (!isCurrentChatHydration(chatId, hydrationSequence)) return;
        chatCache.details[chatId] = chat;
        if (activeChatIdRef.current === chatId) setActiveChat(chat);
      });
    }).then(function () {
      return refreshChats();
    }).catch(function (err) {
      // A transcript/list revalidation can fail after the answer itself has
      // already completed. Do not overwrite that terminal lifecycle with a
      // false execution failure just because the follow-up GET failed.
      if (!answerSettled) runtimeEngine.publishLifecycle(chatId, "failed", {});
      runtimeEngine.update(chatId, null);
      if (activeChatIdRef.current === chatId) setError(wbcErrorText(err));
      // Restore the prompt so the user can retry.
      var hydrationSequence = beginChatHydration(chatId);
      return model.getChat(chatId).then(function (chat) {
        if (!isCurrentChatHydration(chatId, hydrationSequence)) return;
        chatCache.details[chatId] = chat;
        if (activeChatIdRef.current === chatId) setActiveChat(chat);
      }).catch(function () {}).then(function () {
        return refreshChats().catch(function () {});
      }).then(function () { throw err; });
    });
  }

  function handleAnswer(questionId, optionText, resumeMode) {
    return answerQuestionForChat(activeChatId, questionId, optionText, resumeMode).catch(function () {
      // The inline prompt surfaces its error in the conversation itself.
      return null;
    });
  }

  function handleRailAnswer(chatId, questionId, optionText, resumeMode) {
    return answerQuestionForChat(chatId, questionId, optionText, resumeMode);
  }

  // Regenerate the last assistant reply (replays the last user message).
  function handleRetryMessage(messageId) {
    if (!activeChat || activeChat.legacy || runtimeEngine.isRunning(activeChat.id) || retryPendingChatIdRef.current) return;
    var retryChatId = String(activeChat.id || "");
    var retryMessageId = typeof messageId === "string" ? messageId : "";
    var selection = wbcRetryTurnSelection(activeChat, retryMessageId);
    var retryMode = wbcNormalizePermissionMode(activeChat.permissionMode, "auto");
    retryPendingChatIdRef.current = retryChatId;
    setError("");
    setErrorKind("load");
    setRetryClearingMessageIds(selection.outputIds);
    function startRetryAfterClear() {
      var cachedChat = chatCache.details[retryChatId];
      if (cachedChat) chatCache.details[retryChatId] = wbcClearModelOutputForRetry(cachedChat, retryMessageId);
      setActiveChat(function (prev) {
        if (!prev || String(prev.id || "") !== retryChatId) return prev;
        return wbcClearModelOutputForRetry(prev, retryMessageId);
      });
      var suppressedTurn = { chatId: retryChatId, messageIds: selection.outputIds };
      retrySuppressedTurnRef.current = suppressedTurn;
      setRetrySuppressedTurn(suppressedTurn);
      setRetryClearingMessageIds([]);
      retryPendingChatIdRef.current = "";
      runtimeEngine.start(retryChatId, { retry: true, mode: retryMode }, model);
    }
    var reduceMotion = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    if (!selection.outputIds.length || reduceMotion) {
      startRetryAfterClear();
      return;
    }
    // Commit the transcript change only after CSS has completed the visual
    // collapse. A same-duration timer can run before the animation's final
    // frame and produce a visible second jump.
    retryClearCommitRef.current = startRetryAfterClear;
  }

  function handleRetryClearAnimationEnd() {
    var commit = retryClearCommitRef.current;
    if (!commit) return;
    retryClearCommitRef.current = null;
    commit();
  }

  // Edit a user message → fork the conversation at that point, switch to the
  // forked chat, and replay the edited turn through the streaming engine. The
  // original conversation is preserved untouched.
  function handleEditMessage(messageId, newContent) {
    if (!activeChat || activeChat.legacy || runtimeEngine.isRunning(activeChat.id)) return;
    if (!messageId || !newContent) return;
    setError("");
    var replayMode = wbcNormalizePermissionMode(
      activeChat && activeChat.permissionMode,
      "auto"
    );
    model.forkChat(activeChat.id, messageId, newContent).then(function (newChat) {
      newChat = { ...newChat, permissionMode: replayMode };
      setChats(function (prev) { return [newChat].concat(prev); });
      skipNextHydrationChatIdRef.current = newChat.id;
      selectChat(newChat.id);
      setActiveChat(newChat);
      // Replay the edited user message (already the last entry in the forked
      // transcript) through the agent. forkReplay tells the server the state
      // was already truncated by the fork — no re-truncation needed.
      return runtimeEngine.start(newChat.id, { retry: true, forkReplay: true, mode: replayMode }, model);
    }).catch(function (err) { setError(wbcErrorText(err)); });
  }

  function handleCreateChat() {
    return model.createChat(projectId).then(function (chat) {
      setChats(function (prev) { return [chat].concat(prev); });
      skipNextHydrationChatIdRef.current = chat.id;
      selectChat(chat.id);
      setActiveChat(chat);
      return chat;
    }).catch(function (err) { setError(wbcErrorText(err)); });
  }

  // The shell-level menu/shortcut owns navigation, while this page owns chat
  // persistence. A monotonically increasing request id bridges those layers and
  // still works when the chat page is mounted by the same render as Cmd/Ctrl+N.
  useWbcEffect(function () {
    var requestId = Number(newChatRequestId || 0);
    if (
      !requestId
      || requestId === handledNewChatRequestIdRef.current
      || !isActive
      || !projectId
    ) return;
    handledNewChatRequestIdRef.current = requestId;
    handleCreateChat();
  }, [newChatRequestId, isActive, projectId]);

  function handleRenameChat(chatId, title) {
    if (!chatId) return Promise.resolve();
    return model.renameChat(chatId, title).then(function (chat) {
      setActiveChat(function (prev) {
        return prev && prev.id === chat.id ? { ...prev, title: chat.title } : prev;
      });
      setChats(function (prev) {
        return prev.map(function (item) { return item.id === chat.id ? { ...item, title: chat.title } : item; });
      });
      return chat;
    });
  }

  function handleRename(title) {
    if (!activeChat) return Promise.resolve();
    return handleRenameChat(activeChat.id, title);
  }

  function openQuickRename() {
    if (!activeChat || activeChat.legacy) return;
    closePageContextMenu();
    setQuickRenameChat(activeChat);
  }

  function setOpenPageContextMenu(menu) {
    pageContextMenuRef.current = menu;
    setPageContextMenu(menu);
  }

  function clearPendingPageContextMenu() {
    pendingPageContextMenuRef.current = null;
    if (pageContextPreviewTimerRef.current) {
      clearTimeout(pageContextPreviewTimerRef.current);
      pageContextPreviewTimerRef.current = null;
    }
  }

  function closePageContextMenu() {
    var current = pageContextMenuRef.current;
    var pending = pendingPageContextMenuRef.current;
    clearPendingPageContextMenu();
    setOpenPageContextMenu(null);
    if ((current && current.browserPreview) || pending) {
      wbcNotifyBrowserWindowInteraction(false, "context-menu", (current && current.browserSessionId) || (pending && pending.browserSessionId) || activeChatIdRef.current);
    }
  }

  function openPageContextMenu(event) {
    if (!activeChat || activeChat.legacy || !wbcCanOpenPageContextMenu(event)) return;
    event.preventDefault();
    event.stopPropagation();
    closePageContextMenu();
    var nativeHost = document.querySelector(".wbc-browser-window .browser-native-host");
    var nativeRect = nativeHost && nativeHost.getBoundingClientRect();
    var placement = wbcPageContextMenuPlacement(event.clientX, event.clientY, nativeRect);
    var menu = {
      left: placement.left,
      top: placement.top,
      browserPreview: false,
      browserSessionId: String(activeChat.id || ""),
    };
    if (!placement.overlapsBrowser) {
      setOpenPageContextMenu(menu);
      return;
    }
    pendingPageContextMenuRef.current = menu;
    wbcNotifyBrowserWindowInteraction(true, "context-menu", menu.browserSessionId);
    pageContextPreviewTimerRef.current = setTimeout(function () {
      if (pendingPageContextMenuRef.current !== menu) return;
      clearPendingPageContextMenu();
      wbcNotifyBrowserWindowInteraction(false, "context-menu", menu.browserSessionId);
      window.CyreneUI.require("feedback").showToast(
        wbcT("workbenchChat.contextMenuUnavailable", "Could not open the chat menu over the browser window."),
        "warning"
      );
    }, 900);
  }

  function handleDelete() {
    if (!activeChat) return;
    handleDeleteChat(activeChat.id);
  }

  function handleDeleteChat(chatId) {
    if (!chatId) return;
    var deletingActiveChat = activeChatId === chatId;
    function detachDeletedForkSource(item) {
      if (!item || String(item.forkedFromChatId || "") !== String(chatId)) return item;
      var cleaned = { ...item };
      delete cleaned.forkedFromChatId;
      delete cleaned.forkedAtMessageId;
      delete cleaned.forkMessage;
      return cleaned;
    }
    window.CyreneUI.require("feedback").confirmModal({
      body: wbcT("workbenchChat.confirmDelete", "Delete this chat? Its messages cannot be recovered."),
      confirmLabel: wbcT("common.delete", "Delete"),
      danger: true,
    }).then(function (ok) {
      if (!ok) return;
      WbcVoice.stop();
      var deletedIndex = chats.findIndex(function (item) { return item.id === chatId; });
      var deletedItem = deletedIndex >= 0 ? chats[deletedIndex] : null;
      var deletedActiveChat = deletingActiveChat ? activeChat : null;
      var detachedForks = chats.filter(function (item) {
        return String(item.forkedFromChatId || "") === String(chatId);
      });
      var previousActiveChat = activeChat;
      setChats(function (prev) {
        var next = prev
          .filter(function (item) { return item.id !== chatId; })
          .map(detachDeletedForkSource);
        if (deletingActiveChat) selectChat(next[0] ? next[0].id : "");
        return next;
      });
      if (deletingActiveChat) setActiveChat(null);
      else setActiveChat(function (prev) { return detachDeletedForkSource(prev); });
      model.deleteChat(chatId).then(function () {
        runtimeEngine.abort(chatId);
        runtimeEngine.clear(chatId);
        closeDeletedChatSplits(chatId);
      }).catch(function (err) {
        if (deletedItem) {
          setChats(function (prev) {
            var next = prev.map(function (item) {
              var original = detachedForks.find(function (fork) { return fork.id === item.id; });
              return original ? {
                ...item,
                forkedFromChatId: original.forkedFromChatId,
                forkedAtMessageId: original.forkedAtMessageId,
                forkMessage: original.forkMessage,
              } : item;
            });
            if (!next.some(function (item) { return item.id === chatId; })) {
              next.splice(Math.min(Math.max(deletedIndex, 0), next.length), 0, deletedItem);
            }
            return next;
          });
        }
        if (deletingActiveChat) {
          selectChat(chatId);
          setActiveChat(deletedActiveChat);
        } else if (
          previousActiveChat
          && String(previousActiveChat.forkedFromChatId || "") === String(chatId)
        ) {
          setActiveChat(previousActiveChat);
        }
        setError(wbcErrorText(err));
      });
    });
  }

  function handleToTask(chatId) {
    var targetChatId = typeof chatId === "string"
      ? chatId
      : String(activeChat && activeChat.id || "");
    if (!targetChatId || toTaskBusy) return;
    setToTaskBusy(true);
    setError("");
    model.toTask(targetChatId).then(function (payload) {
      if (onOpenTask) onOpenTask(payload);
    }).catch(function (err) {
      setError(wbcErrorText(err));
    }).then(function () {
      setToTaskBusy(false);
    });
  }

  function handleCompact() {
    if (!activeChat || activeChat.legacy || compactBusy) return;
    setCompactBusy(true);
    setError("");
    model.compactChat(activeChat.id).then(function (payload) {
      var before = Number(payload.beforeTokens || 0);
      var after = Number(payload.afterTokens || before);
      var limit = Number(payload.ctxLimit || 0);
      if (payload.compacted) {
        setActiveChat(function (prev) {
          return prev ? { ...prev, contextRevision: Date.now() } : prev;
        });
        window.CyreneUI.require("feedback").showToast(wbcT(
          "workbenchChat.compactSuccess",
          "Chat compressed: {before}% → {after}%",
          {
            before: limit > 0 ? Math.round(before / limit * 100) : "—",
            after: limit > 0 ? Math.round(after / limit * 100) : "—",
          }
        ), "success");
        return;
      }
      if (payload.reason === "empty") {
        window.CyreneUI.require("feedback").showToast(wbcT("workbenchChat.compactEmpty", "There is no agent context to compress."), "warning");
      } else if (payload.reason === "running") {
        window.CyreneUI.require("feedback").showToast(wbcT("workbenchChat.compactRunning", "The agent is currently working. Try again after it finishes."), "warning");
      } else if (payload.reason === "awaiting_user") {
        window.CyreneUI.require("feedback").showToast(wbcT("workbenchChat.compactAwaitingUser", "Answer the agent's question before compressing this chat."), "warning");
      } else if (payload.reason === "no_tool_activity") {
        window.CyreneUI.require("feedback").showToast(wbcT("workbenchChat.compactNoTools", "This chat has no tool activity to compress."), "warning");
      } else if (payload.reason === "distilling") {
        window.CyreneUI.require("feedback").showToast(wbcT("workbenchChat.compactDistilling", "Background context compression is still running. Try again shortly."), "warning");
      } else {
        window.CyreneUI.require("feedback").showToast(wbcT("workbenchChat.compactNoChange", "No earlier context is available to compress."), "warning");
      }
    }).catch(function (err) {
      setError(wbcErrorText(err));
    }).then(function () {
      setCompactBusy(false);
    });
  }

  function handleGenerateMemory() {
    if (!activeChat || activeChat.legacy || memoryLearningBusy) return;
    setMemoryLearningBusy(true);
    setErrorKind("memory");
    setError("");
    var memoryLanguage = window.CyreneUI.require("i18n").getLang();
    model.generateMemory(activeChat.id, memoryLanguage).then(function (payload) {
      setErrorKind("load");
      var duplicate = payload && payload.status === "deduplicated";
      window.CyreneUI.require("feedback").showToast(
        duplicate
          ? wbcT("workbenchChat.memoryLearningDeduplicated", "This conversation context has already been submitted for learning.")
          : wbcT("workbenchChat.memoryLearningQueued", "Project memory learning started."),
        duplicate ? "warning" : "success"
      );
    }).catch(function (err) {
      setErrorKind("memory");
      setError(wbcErrorText(err));
      window.CyreneUI.require("feedback").showToast(wbcErrorText(err), "error");
    }).then(function () {
      setMemoryLearningBusy(false);
    });
  }

  function onToggleSide() { setSideVisible(function (v) { return !v; }); }

  function rememberTaskPaneSession(taskId, session) {
    var id = String(taskId || "");
    if (!id || !session) return;
    setTaskPaneSessions(function (current) {
      if (current[id] === session) return current;
      return Object.assign({}, current, { [id]: session });
    });
  }

  function openTaskRightPanel(taskId, tab) {
    var id = String(taskId || "");
    if (!id) return;
    setTaskRightTabs(function (current) {
      return Object.assign({}, current, { [id]: String(tab || "context") });
    });
    setSideVisible(true);
  }

  function refreshTaskRightPanel(taskId, nextStore) {
    var id = String(taskId || "");
    var nextSession = wbcTaskSessionFromStore(nextStore, id);
    if (nextSession) rememberTaskPaneSession(id, nextSession);
    if (onTaskStoreChange) onTaskStoreChange(nextStore, id);
  }

  useWbcEffect(function () {
    function onBrowserPreviewReady(event) {
      var pending = pendingPageContextMenuRef.current;
      if (!pending) return;
      var detail = event && event.detail || {};
      if (String(detail.sessionId || "") !== String(pending.browserSessionId || "")) return;
      clearPendingPageContextMenu();
      if (detail.fallback) {
        wbcNotifyBrowserWindowInteraction(false, "context-menu", pending.browserSessionId);
        window.CyreneUI.require("feedback").showToast(
          wbcT("workbenchChat.contextMenuUnavailable", "Could not open the chat menu over the browser window."),
          "warning"
        );
        return;
      }
      setOpenPageContextMenu({ ...pending, browserPreview: true });
    }
    window.addEventListener("workbench:browser-window-preview-ready", onBrowserPreviewReady);
    return function () {
      window.removeEventListener("workbench:browser-window-preview-ready", onBrowserPreviewReady);
      closePageContextMenu();
    };
  }, []);

  useWbcEffect(function () {
    closePageContextMenu();
    setQuickRenameChat(null);
  }, [activeChatId]);

  // The open conversation only renders and controls its own runtime. Other
  // conversations continue streaming in the background.
  var activeRunning = !!activeRuntime;
  // Effects run after paint, so also guard the render itself against a stale
  // activeChat during the ID -> transcript fetch gap.
  var visibleChat = activeChat && String(activeChat.id || "") === String(activeChatId || "")
    ? activeChat
    : null;
  var selectedChatSummary = chats.find(function (item) {
    return String(item.id || "") === String(activeChatId || "");
  }) || null;
  var activeBrowserState = wbcBrowserStateForChat(activeChatId);
  var browserMarkedActive = !!(browserActiveByChat && browserActiveByChat[activeChatId]);
  var hasActiveBrowser = !!((activeBrowserState && activeBrowserState.active) || browserMarkedActive);
  var browserWindowMode = browserWindowModeByChat[activeChatId] || "pip";
  var splitResource = resourceSplitByChat[activeChatId] || null;
  var browserTabOpen = !!(
    hasActiveBrowser
    && splitResource
    && splitResource.type === "browser"
    && browserWindowMode !== "maximized"
  );
  var conversationLoading = loading || chatLoading;
  var splitSideAgentId = sideAgentSplitByChat[activeChatId] || "";
  var splitSideAgent = sideAgents.find(function (agent) {
    return String(agent && agent.id || "") === String(splitSideAgentId);
  }) || null;
  var artifactItems = wbcChatArtifactFiles(visibleChat || selectedChatSummary);
  var splitArtifactKey = artifactSplitByChat[activeChatId] || "";
  var splitArtifactItem = artifactItems.find(function (item) {
    return wbcArtifactFileKey(item && item.file) === splitArtifactKey;
  }) || null;
  var splitArtifact = splitArtifactItem && splitArtifactItem.file;
  var splitChange = changeSplitByChat[activeChatId] || null;
  var viewerItems = viewerFile && viewerFile.source === "project"
    ? [{ file: viewerFile, role: "project" }].concat(artifactItems.filter(function (item) {
      return wbcArtifactFileKey(item && item.file) !== wbcArtifactFileKey(viewerFile);
    }))
    : artifactItems;
  var splitViewer = splitResource && splitResource.type === "viewer"
    ? (
      viewerFile && wbcArtifactFileKey(viewerFile) === String(splitResource.payload || "")
        ? viewerFile
        : (viewerItems.find(function (item) { return wbcArtifactFileKey(item && item.file) === String(splitResource.payload || ""); }) || {}).file
    )
    : null;
  var splitMap = splitResource && splitResource.type === "map" ? splitResource.payload : null;
  var splitBrowserTabId = splitResource && splitResource.type === "browser" ? String(splitResource.payload || "") : "";
  var splitSubagents = !!(splitResource && splitResource.type === "subagents");
  // Dragging a rail chat onto the right panel opens that conversation here.
  var splitChatId = splitResource && splitResource.type === "chat" ? String(splitResource.payload || "") : "";
  var paneLayout = paneLayoutFor(activeChatId);
  var paneCardCount = paneLayout.left.length + paneLayout.right.length;
  var paneHasTwoColumns = !!(paneLayout.left.length && paneLayout.right.length);
  var paneDraggedLocation = paneCardDragId ? wbcPaneCardLocation(paneLayout, paneCardDragId) : null;
  var paneAxisDropAvailable = !!(
    paneDraggedLocation
    && (paneLayout[paneDraggedLocation.side] || []).length === 2
    && !(paneLayout[paneDraggedLocation.side === "left" ? "right" : "left"] || []).length
  );
  var paneOnlyCard = paneLayout.left[0] || paneLayout.right[0] || null;
  // A single conversation/task is visually composed of its main surface and
  // its docked context panel. Its drop target therefore belongs to the page
  // grid spanning both tracks, rather than to the main card alone.
  var singlePaneDropUsesContextTracks = !!(
    paneCardCount === 1
    && paneOnlyCard
    && (paneOnlyCard.kind === "chat" || paneOnlyCard.kind === "task")
  );
  var paneDropSessionActive = !!(paneCardDragId || chatDragSession || resourceDragSession);
  var singlePaneContextDropActive = !!(
    singlePaneDropUsesContextTracks
    && paneDropSessionActive
    && String(paneCardDragId || "") !== String(paneOnlyCard.id || "")
  );
  var paneUsesWorkspace = paneCardCount > 1 || !!(
    paneOnlyCard
    && (paneOnlyCard.kind !== "chat" || String(paneOnlyCard.payload || "") !== String(activeChatId || ""))
  );
  var splitDetailOpen = paneUsesWorkspace;
  // A project file can be opened before any conversation exists. In that
  // state the file workspace owns every track to the right of the rail; there
  // is no conversation context panel to reserve space for.
  var projectPaneOnly = !activeChatId && paneCardCount > 0;
  var projectTaskPanelCard = paneCardCount === 1
    && paneOnlyCard && paneOnlyCard.kind === "task"
    ? paneOnlyCard
    : null;
  var projectTaskPanelId = projectTaskPanelCard ? String(projectTaskPanelCard.payload || "") : "";
  var projectTaskPanelSession = projectTaskPanelId
    ? (taskPaneSessions[projectTaskPanelId] || (Array.isArray(tasks) ? tasks.find(function (task) {
        return String(task && task.id || "") === projectTaskPanelId;
      }) : null))
    : null;
  var showNewConversationWorkspace = !activeChatId && paneCardCount === 0;
  var singleColumnWorkspaceOpen = splitDetailOpen && !projectPaneOnly && !paneHasTwoColumns && !projectTaskPanelCard;

  useWbcEffect(function () {
    if (!splitDetailOpen) setFloatingConversationPanelOpen(false);
  }, [splitDetailOpen]);

  useWbcEffect(function () {
    setFloatingConversationPanelOpen(false);
    var snapshot = floatingSplitRestoreRef.current;
    if (!snapshot || snapshot.chatId === String(activeChatId || "")) return;
    floatingSplitRestoreRef.current = null;
    setSideAgentSplitWidth(wbcClampSideSplitWidthForPage(snapshot.splitWidth, pageRef.current));
    restoreSplitState(snapshot.activeChatId, snapshot.activeSplit);
    if (snapshot.chatId !== snapshot.activeChatId) {
      restoreSplitState(snapshot.chatId, snapshot.sourceSplit);
    }
  }, [activeChatId]);

  // The browser page is an Electron WebContentsView, so it does not
  // participate in the renderer's grid layout. ResizeObserver normally keeps
  // it aligned, but the split track can change both the surface's left edge
  // and width in one committed grid update without producing a reliable
  // observation on every macOS/Electron frame. Publish one authoritative
  // layout pass after React has committed each split-width change. The browser
  // viewport coalesces these notifications to one bounds IPC per animation
  // frame, keeping drag resizing live without reintroducing resize flashing.
  useWbcLayoutEffect(function () {
    if (!splitDetailOpen) return undefined;
    var frame = requestAnimationFrame(function () {
      window.dispatchEvent(new CustomEvent("workbench:browser-layout", {
        detail: { source: "pane-column-resize", width: paneColumnWidth },
      }));
    });
    return function () { cancelAnimationFrame(frame); };
  }, [splitDetailOpen, paneColumnWidth, activeChatId]);

  function setBrowserWindowModeForChat(chatId, mode) {
    chatId = String(chatId || "");
    if (!chatId) return;
    setBrowserWindowModeByChat(function (prev) {
      if (prev[chatId] === mode) return prev;
      return Object.assign({}, prev, { [chatId]: mode });
    });
  }

  function setActiveBrowserWindowMode(mode) {
    setBrowserWindowModeForChat(activeChatId, mode);
  }

  function openSplitChatContent(type, payload, sourceChatSnapshot) {
    var sourceChatId = String(splitChatId || "");
    if (!sourceChatId || !type) return;
    beginFloatingPanelSplit(function () {
      if (type === "artifact") selectArtifact(payload);
      else if (type === "change") selectChange(payload);
      else if (type === "viewer") openViewer(payload);
      else if (type === "map") selectResourceSplit("map", payload);
      else if (type === "browser") {
        setBrowserWindowModeForChat(sourceChatId, "pip");
        selectResourceSplit("browser", payload);
      } else if (type === "subagents") {
        selectResourceSplit("subagents", true);
      } else if (type === "side-agent") {
        selectSideAgent(payload);
      }
    }, sourceChatId, sourceChatSnapshot);
  }

  function renderConversationPanel(floating) {
    function openPanelContent(type, payload) {
      if (type === "viewer" && payload) setViewerFile(payload);
      if (type === "browser") setActiveBrowserWindowMode("pip");
      openPaneContent(type === "artifact" ? "file" : type, payload, {
        sourceCardId: "chat:" + String(activeChatId || ""),
        restore: !!floating,
        promoteSourceLeft: true,
      });
      if (floating) setFloatingConversationPanelOpen(false);
    }
    return (
      <WbcSide
        project={project}
        chat={visibleChat || selectedChatSummary}
        chatLoading={chatLoading}
        chatDetailed={!!visibleChat}
        chats={chats}
        activeChatId={activeChatId}
        onSelectChat={selectChat}
        runtime={activeRuntime}
        subagentData={subagentData}
        subagentLoading={subagentLoading}
        onSelectSubagentRound={function (roundId) { loadSubagents(activeChatId, roundId); }}
        tab={sideTab}
        onTabChange={setSideTab}
        viewerFile={viewerFile}
        onOpenFile={function (file) { openPanelContent("file", file); }}
        onSelectArtifact={function (file) { openPanelContent("file", file); }}
        onSelectChange={function (change) { openPanelContent("change", change); }}
        onSelectViewer={function (file) { openPanelContent("viewer", file); }}
        onSelectMap={function (item) { openPanelContent("map", item); }}
        onSelectBrowser={function (tabId) {
          openPanelContent("browser", tabId);
        }}
        onOpenSubagents={function () { openPanelContent("subagents", true); }}
        onViewerViewed={markViewerFileRead}
        onRename={openQuickRename}
        onDelete={handleDelete}
        onToTask={handleToTask}
        toTaskBusy={toTaskBusy}
        onCompact={handleCompact}
        compactBusy={compactBusy}
        sideAgents={sideAgents}
        sideAgentsLoading={sideAgentsLoading}
        activeSideAgentId={splitSideAgentId}
        onSelectSideAgent={function (agentId) { openPanelContent("side-agent", agentId); }}
        onUpdateSideAgent={updateSideAgent}
        onDeleteSideAgent={deleteSideAgent}
        terminals={terminals}
        terminalsLoading={terminalsLoading}
        onSelectTerminal={function (terminalId) { openTerminal(terminalId, "right"); }}
        onToggleSide={onToggleSide}
        onBrowserTakeoverComplete={function (payload) {
          var pending = activeChat && activeChat.pendingQuestion;
          if (!pending || !pending.id) return Promise.reject(new Error("登录确认已不在等待中。"));
          var takeoverQuestionId = String(payload && payload.questionId || "");
          if (takeoverQuestionId && String(pending.id || "") !== takeoverQuestionId) {
            return Promise.reject(new Error("登录确认已更新，请使用对话中的最新确认。"));
          }
          handleAnswer(pending.id, (payload && payload.text) || "我已完成登录");
          return Promise.resolve();
        }}
        browserActiveByChat={browserActiveByChat}
        browserSuppressed={browserWindowMode === "maximized"}
        floating={floating}
        widthResizable={!floating && paneCardCount === 1}
        onCloseFloating={function () { setFloatingConversationPanelOpen(false); }}
      />
    );
  }

  function openContentFromPaneCard(card, type, payload) {
    if (type === "viewer" && payload) setViewerFile(payload);
    if (type === "browser") setBrowserWindowModeForChat(card.ownerChatId || activeChatId, "pip");
    openPaneContent(type === "artifact" ? "file" : type, payload, {
      sourceCardId: card.id,
      restore: true,
      promoteSourceLeft: true,
    });
  }

  function renderActiveConversationCard(card) {
    return (
      <WbcMain
        project={project}
        chat={visibleChat}
        chatSummary={selectedChatSummary}
        loading={conversationLoading}
        runtime={activeRuntime}
        error={error}
        errorKind={errorKind}
        onRetry={errorKind === "message" ? handleRetryMessage : (errorKind === "memory" ? handleGenerateMemory : retryLoad)}
        running={activeRunning}
        onSend={handleSend}
        onGuidance={handleGuidance}
        onInterrupt={handleInterrupt}
        onAnswer={handleAnswer}
        onRetryMessage={handleRetryMessage}
        onRetryClearAnimationEnd={handleRetryClearAnimationEnd}
        retryClearingMessageIds={retryClearingMessageIds}
        retrySuppressedMessageIds={String(retrySuppressedTurn.chatId || "") === String(activeChatId || "") ? retrySuppressedTurn.messageIds : []}
        onEditMessage={handleEditMessage}
        onAskSelection={handleAskSelection}
        sideAgentCreating={sideAgentCreating}
        onConversationContextMenu={openPageContextMenu}
        onRename={handleRename}
        onDelete={handleDelete}
        onToTask={handleToTask}
        toTaskBusy={toTaskBusy}
        onOpenFile={function (file) { openPaneContent("file", file, { sourceCardId: card.id }); }}
        onOpenDroppedChat={function (chatId) {
          if ((chatsRef.current || []).some(function (item) {
            return String(item && item.id || "") === String(chatId || "");
          })) selectChat(chatId);
        }}
        sideVisible={sideVisible}
        sidePanelTabExpanded={sideVisible && !!sideTab}
        onToggleSide={onToggleSide}
        splitOpen={paneCardCount > 1}
        browserState={activeBrowserState}
        browserSessionId={activeChatId || ""}
        browserVisible={hasActiveBrowser && !browserTabOpen && paneCardCount === 1}
        browserWindowMode={browserWindowMode}
        onBrowserMaximize={function () { setActiveBrowserWindowMode("maximized"); }}
        onBrowserRestore={function () { setActiveBrowserWindowMode("pip"); }}
        draftAgent={draftAgentBinding}
        onDraftAgentChange={handleDraftAgentChange}
        onSwitchAgent={handleSwitchAgent}
        onOpenAgentDetail={handleOpenAgentDetail}
        horizontalSessionWheelGesture={horizontalSessionWheelRef.current}
        onBrowserTakeoverComplete={function (payload) {
          var pending = activeChat && activeChat.pendingQuestion;
          if (!pending || !pending.id) return Promise.reject(new Error("登录确认已不在等待中。"));
          var takeoverQuestionId = String(payload && payload.questionId || "");
          if (takeoverQuestionId && String(pending.id || "") !== takeoverQuestionId) {
            return Promise.reject(new Error("登录确认已更新，请使用对话中的最新确认。"));
          }
          handleAnswer(pending.id, (payload && payload.text) || "我已完成登录");
          return Promise.resolve();
        }}
      />
    );
  }

  function openConversationPanelFromMainGrip() {
    // In the single-card workspace this action means restoring the docked
    // conversation panel that the user collapsed. A floating copy is only
    // appropriate while multiple content cards already occupy the workspace.
    if (paneCardCount === 1) {
      setFloatingConversationPanelOpen(false);
      window.dispatchEvent(new CustomEvent("workbench:show-chat-side"));
      return;
    }
    setFloatingConversationPanelOpen(true);
  }

  function renderPaneCard(card, side, columnLength, dropKey) {
    // Before the first durable chat exists, the workspace uses a synthetic
    // card. It is still the main conversation surface, not a detached chat
    // split (which requires a real chat id to hydrate).
    var isNewConversation = card.kind === "chat"
      && !activeChatId
      && String(card.id || "") === "new-conversation";
    var isActiveConversation = isNewConversation || (
      card.kind === "chat"
      && String(card.payload || "") === String(activeChatId || "")
      && String(card.id || "") === "chat:" + String(activeChatId || "")
    );
    var singlePane = paneCardCount === 1;
    var content = null;
    var grip = null;
    var close = function () {
      var draftKey = (card.kind === "file" || card.kind === "viewer")
        ? wbcProjectFileDraftKey(card.payload)
        : "";
      if (!draftKey || !WBC_PROJECT_FILE_DRAFTS[draftKey]) {
        closePaneCard(card.id);
        return;
      }
      var feedback = window.CyreneUI.require("feedback");
      var request = feedback.confirmModal ? feedback.confirmModal({
        title: wbcT("workbenchChat.editorUnsavedTitle", "Unsaved changes"),
        body: wbcT("workbenchChat.editorUnsavedBody", "Discard the changes made to this file?"),
        confirmLabel: wbcT("workbenchChat.editorDiscard", "Discard changes"),
        danger: true,
      }) : Promise.resolve(window.confirm(wbcT("workbenchChat.editorUnsavedBody", "Discard the changes made to this file?")));
      Promise.resolve(request).then(function (confirmed) {
        if (!confirmed) return;
        wbcDiscardProjectFileDraft(card.payload);
        closePaneCard(card.id);
      });
    };
    var move = function () { movePaneCardOtherSide(card.id); };
    var dragStart = function (event) { handlePaneCardDragStart(event, card.id, card); };
    var pointerDown = function (event) { handlePaneCardPointerDown(event, card.id, card); };
    if (isActiveConversation) {
      content = renderActiveConversationCard(card);
      grip = <WbcSplitGripBar
        dragSource={card.id}
        menuDisabled={singlePane}
        onToggleSide={move}
        onClose={close}
        onOpenConversationPanel={openConversationPanelFromMainGrip}
        onSplitPointerDown={pointerDown}
        onSplitDragStart={dragStart}
        onSplitDragEnd={handlePaneCardDragEnd}
      />;
    } else if (card.kind === "chat") {
      content = <WbcChatSplit
        chatId={String(card.payload || "")}
        project={project}
        onOpenContent={function (type, payload) { openContentFromPaneCard(card, type, payload); }}
        browserActiveByChat={browserActiveByChat}
        onClose={close}
        onOpenInMain={selectChat}
        splitSide={side}
        onToggleSide={move}
        onSplitPointerDown={pointerDown}
        onSplitDragStart={dragStart}
        onSplitDragEnd={handlePaneCardDragEnd}
        menuDisabled={singlePane}
      />;
    } else if (card.kind === "task") {
      var TaskPane = TaskPaneComponent || window.CyreneTaskPane;
      var taskId = String(card.payload || "");
      grip = <WbcSplitGripBar
        dragSource={card.id}
        menuDisabled={singlePane}
        onToggleSide={move}
        onClose={close}
        onOpenConversationPanel={function () {
          window.dispatchEvent(new CustomEvent("cyrene:open-task-context-panel", {
            detail: { taskId: taskId },
          }));
        }}
        openPanelLabel={wbcT("task.side.openDetailPanel", "Open task details")}
        onSplitPointerDown={pointerDown}
        onSplitDragStart={dragStart}
        onSplitDragEnd={handlePaneCardDragEnd}
      />;
      content = TaskPane ? <aside className="wbc-side-agent-split wbc-task-split" aria-label={wbcT("workbench.page.task", "Task")}>
        <TaskPane
          taskId={taskId}
          project={project}
          split={!singlePane}
          onTaskStoreChange={onTaskStoreChange}
          onSessionChange={function (session) { rememberTaskPaneSession(taskId, session); }}
          onRightTab={function (tab) { openTaskRightPanel(taskId, tab); }}
          onOpenTask={function (taskId) {
            openTaskWorkspace(taskId);
          }}
        />
      </aside> : null;
    } else if (card.kind === "side-agent") {
      var paneAgent = sideAgents.find(function (agent) {
        return String(agent && agent.id || "") === String(card.payload || "");
      }) || null;
      grip = <WbcSplitGripBar
        dragSource={card.id}
        menuDisabled={singlePane}
        onToggleSide={move}
        onClose={close}
        onOpenConversationPanel={function () {
          setSideTab("side-agents");
          setFloatingConversationPanelOpen(true);
        }}
        onSplitPointerDown={pointerDown}
        onSplitDragStart={dragStart}
        onSplitDragEnd={handlePaneCardDragEnd}
      />;
      content = paneAgent ? <WbcSideAgentSplit
        agent={paneAgent}
        agents={sideAgents}
        project={project}
        onOpenFile={function (file) { openContentFromPaneCard(card, "file", file); }}
        onUpdate={updateSideAgent}
        onSelect={function (agentId) {
          updatePaneCard(card.id, function (current) {
            return Object.assign({}, current, { payload: agentId });
          });
        }}
        onClose={close}
      /> : null;
    } else {
      grip = <WbcSplitGripBar
        dragSource={card.id}
        menuDisabled={singlePane}
        menuType="content"
        onToggleSide={move}
        onClose={close}
        onNewConversation={columnLength === 1 ? function () { createPaneConversation(card.id); } : null}
        onSplitPointerDown={pointerDown}
        onSplitDragStart={dragStart}
        onSplitDragEnd={handlePaneCardDragEnd}
      />;
      if (card.kind === "file" || card.kind === "viewer") {
        var file = card.payload;
        var fileItems = file && file.source === "project"
          ? [{ file: file, role: "project" }].concat(artifactItems.filter(function (item) {
              return wbcArtifactFileKey(item && item.file) !== wbcArtifactFileKey(file);
            }))
          : artifactItems;
        content = <WbcArtifactSplit
          file={file}
          items={fileItems}
          label={wbcT("workbenchChat.viewer", "Viewer")}
          onSelect={function (nextFile) { updatePaneCard(card.id, function (current) { return Object.assign({}, current, { payload: nextFile }); }); }}
          onClose={close}
          onViewed={markViewerFileRead}
        />;
      } else if (card.kind === "change") {
        content = <WbcChangeSplit
          change={card.payload}
          onSelect={function (nextChange) { updatePaneCard(card.id, function (current) { return Object.assign({}, current, { payload: nextChange }); }); }}
          onClose={close}
        />;
      } else if (card.kind === "map") {
        content = <WbcMapPaneContent
          chatId={card.ownerChatId || activeChatId}
          item={card.payload}
          onSelect={function (nextItem) { updatePaneCard(card.id, function (current) { return Object.assign({}, current, { payload: nextItem }); }); }}
          onClose={close}
        />;
      } else if (card.kind === "browser") {
        var cardBrowserState = wbcBrowserStateForChat(card.ownerChatId || activeChatId);
        content = <WbcBrowserSplit
          active={true}
          tabId={String(card.payload || "")}
          tabs={cardBrowserState && cardBrowserState.tabs || []}
          browserState={cardBrowserState}
          browserSessionId={card.ownerChatId || activeChatId}
          onSelect={function (tabId) { updatePaneCard(card.id, function (current) { return Object.assign({}, current, { payload: tabId }); }); }}
          onClose={close}
          onTakeoverComplete={function () { return Promise.resolve(); }}
        />;
      } else if (card.kind === "subagents") {
        content = <aside className="wbc-side-agent-split wbc-subagents-split" aria-label={wbcT("workbenchChat.subagents", "Subagents")}>
          <header className="wbc-side-agent-split-head wbc-static-split-head"><span className="wbc-side-agent-split-title"><span>{wbcT("workbenchChat.subagents", "Subagents")}</span></span><button type="button" className="wbc-side-agent-split-close" onClick={close} aria-label={wbcT("workbenchChat.closeSubagents", "Close subagents")}>{WBC_ICONS.x}</button></header>
          <div className="wbc-resource-split-body wbc-subagents-split-body"><WbcSubagentsTab data={subagentData} loading={subagentLoading} onSelectRound={function (roundId) { loadSubagents(card.ownerChatId || activeChatId, roundId); }} /></div>
        </aside>;
      } else if (card.kind === "terminal") {
        var TerminalPane = terminalModule.Pane;
        content = <TerminalPane terminalId={String(card.payload || "")} onState={updateTerminalSummary} />;
      }
    }
    if (!content) return null;
    return <WbcPaneCardFrame
      key={card.id}
      card={card}
      dropKey={dropKey || card.id}
      replaceConversation={paneCardCount === 1 && card.kind === "chat"}
      grip={grip}
      dropEnabled={!singlePaneDropUsesContextTracks && paneDropSessionActive
        && String(paneCardDragId || "") !== String(card.id || "")}
      replaceOnly={columnLength === 2}
      axisEnabled={paneCardCount === 1}
      dropTarget={paneDropTarget}
      onDropOver={handlePaneDropOver}
      onDrop={handlePaneDrop}
      onDropLeave={function (event) {
        if (!event.currentTarget.contains(event.relatedTarget)) setPaneDropTarget(null);
      }}
    >
      {content}
      {isActiveConversation && floatingConversationPanelOpen ? (
        <div className="wbc-pane-floating-conversation-panel">
          {renderConversationPanel(true)}
        </div>
      ) : null}
    </WbcPaneCardFrame>;
  }

  function renderPaneColumn(side, cards) {
    if (!cards.length) return null;
    var ratio = side === "left" ? paneLayout.leftRatio : paneLayout.rightRatio;
    return (
      <section
        key={side}
        className={"wbc-pane-column " + side + (cards.length === 2 ? " vertical" : "")}
        style={cards.length === 2 ? { gridTemplateRows: ratio + "fr " + (1 - ratio) + "fr" } : undefined}
      >
        {cards.map(function (card, index) { return renderPaneCard(card, side, cards.length, side + ":" + index); })}
        {cards.length === 2 ? <WbcPaneRowResizer ratio={ratio} onResize={function (next) { resizePaneRow(side, next); }} /> : null}
      </section>
    );
  }

  return (
    <div
      ref={pageRef}
      className={"wbc-page"
        + (sideVisible ? "" : " wbc-side-hidden")
        + (navCollapsed ? " wbc-nav-collapsed" : "")
        + (projectPaneOnly ? " wbc-project-pane-only" : "")
        + (projectTaskPanelCard ? " wbc-project-task-pane" : "")
        + (splitDetailOpen && !projectPaneOnly && !projectTaskPanelCard ? " side-agent-split-open" : "")
        + (singleColumnWorkspaceOpen ? " wbc-pane-single-column-open" : "")
        + (splitDetailOpen && !projectPaneOnly && splitSide === "left" ? " wbc-split-left" : "")
        + (chatSideDropActive && paneCardCount !== 1 ? " wbc-chat-side-drop-active" : "")}
      style={{
        "--wbc-chat-side-preview-width": sideAgentSplitWidth + "px",
        ...(projectTaskPanelCard && !sideVisible
          ? { "--wbc-side-track-width": "0px" }
          : splitDetailOpen && !projectPaneOnly && !singleColumnWorkspaceOpen && !projectTaskPanelCard
            ? { "--wbc-side-track-width": sideAgentSplitWidth + "px" }
            : {}),
      }}
      data-active-chat-id={activeChatId || ""}
      data-project-id={projectId || ""}
      onDragOver={handleResourceSplitDragOver}
      onDragLeave={function (event) {
        var rect = event.currentTarget.getBoundingClientRect();
        if (
          event.clientX <= rect.left || event.clientX >= rect.right
          || event.clientY <= rect.top || event.clientY >= rect.bottom
        ) setResourceSplitDropSide("");
      }}
      onDragEnd={function () { setResourceSplitDropSide(""); }}
      onDrop={handleResourceSplitDrop}
    >
      {detachedPaneReturnHover ? (
        <div className="wbc-detached-pane-return-layer" role="status">
          <span>{wbcT("workbenchChat.detachedReturn", "Release to merge back into the main window")}</span>
        </div>
      ) : null}
      {chatFileDropActive && <WorkbenchFileDropOverlay key="file-drop-overlay" label={wbcT("workbenchChat.dropToAttach", "Release to add files to the message input")} />}
      {resourceSplitDropSide && paneCardCount !== 1 && (function () {
        var geometry = resourceSplitDropGeometry();
        if (!geometry) return null;
        var pageLeft = geometry.pageRect.left;
        return (
          <div className="wbc-resource-file-drop-zones">
            <div
              className={"wbc-resource-file-drop-zone left" + (resourceSplitDropSide === "left" ? " active" : "")}
              style={{
                left: (geometry.contentLeft - pageLeft) + "px",
                width: Math.max(0, geometry.rightLeft - geometry.contentLeft) + "px",
              }}
            >
              <span className="wbc-chat-side-drop-hint" role="status">{wbcT("workbenchChat.dropFileSplitLeft", "Release to open the file on the left")}</span>
            </div>
            <div
              className={"wbc-resource-file-drop-zone right" + (resourceSplitDropSide === "right" ? " active" : "")}
              style={{
                left: (geometry.rightLeft - pageLeft) + "px",
                width: Math.max(0, geometry.rightRight - geometry.rightLeft) + "px",
              }}
            >
              <span className="wbc-chat-side-drop-hint" role="status">{wbcT("workbenchChat.dropFileSplitRight", "Release to open the file on the right")}</span>
            </div>
          </div>
        );
      })()}
      {chatDragSession && paneCardCount !== 1 && !paneHasTwoColumns && (function () {
        var zone = wbcChatSideZoneRect();
        if (!zone) return null;
        var pageRect = pageRef.current ? pageRef.current.getBoundingClientRect() : null;
        var left = pageRect ? zone.left - pageRect.left : 0;
        return (
          <div
            key="chat-side-drop-layer"
            className={"wbc-chat-side-drop-layer" + (chatSideDropActive ? " active" : "")}
            style={{ left: left + "px", width: (zone.right - zone.left) + "px" }}
            onDragOver={handleSideLayerDragOver}
            onDrop={handleSideLayerDrop}
          >
            <span className="wbc-chat-side-drop-hint" role="status">
              {chatDragKind === "task"
                ? wbcT("workbenchChat.dropTaskToOpenSide", "Release to open this task in the side panel")
                : wbcT("workbenchChat.dropToOpenSide", "Release to open this conversation in the side panel")}
            </span>
          </div>
        );
      })()}
      {singlePaneContextDropActive ? (
        <div className="wbc-pane-context-drop-host" role="presentation">
          <div
            className="wbc-pane-card-drop-layer context-tracks"
            onDragLeave={function (event) {
              if (!event.currentTarget.contains(event.relatedTarget)) setPaneDropTarget(null);
            }}
          >
            <WbcPaneContextTrackDropSurface
              card={paneOnlyCard}
              dropKey="workspace:single"
              dropTarget={paneDropTarget}
              onDropOver={handlePaneDropOver}
              onDrop={handlePaneDrop}
            />
          </div>
        </div>
      ) : null}
      <WbcRail
        projectId={projectId}
        projectName={project && project.name || ""}
        chats={chats}
        tasks={tasks}
        terminals={terminals}
        terminalsLoading={terminalsLoading}
        activeTerminalId={activeTerminalId}
        railMode={railMode}
        workRailMode={lastWorkRailModeRef.current}
        pinnedChatIds={pinnedChatIds}
        pinnedTaskIds={pinnedTaskIds}
        activeChatId={railSelectionSuppressed ? "" : activeChatId}
        activeTaskId={railSelectionSuppressed ? "" : activeTaskId}
        loading={loading}
        runningChatIds={runningChatIds}
        runtimeEngine={runtimeEngine}
        onSelect={selectChat}
        onSelectTask={function (taskId) {
          openTaskWorkspace(taskId);
        }}
        onAnswer={handleRailAnswer}
        onCreate={handleCreateChat}
        onCreateTask={onCreateTask}
        onRename={handleRenameChat}
        onRenameTask={onRenameTask}
        onDelete={handleDeleteChat}
        onDeleteTask={onDeleteTask}
        onToTask={handleToTask}
        toTaskBusy={toTaskBusy}
        onTogglePinned={onTogglePinnedChat}
        onTogglePinnedTask={onTogglePinnedTask}
        onOpenFile={openProjectFile}
        onOpenTerminal={openTerminal}
        onCreateTerminal={createTerminal}
        onRenameTerminal={renameTerminal}
        onDeleteTerminal={deleteTerminal}
        onUpdateTerminalLayout={updateTerminalLayout}
        onRailModeChange={function (mode) {
          if (mode === "chat" || mode === "task") {
            lastWorkRailModeRef.current = mode;
            setRailSelectionSuppressed(true);
          }
          setRailMode(mode);
        }}
        collapsed={navCollapsed}
        onToggleCollapsed={onToggleNavCollapsed}
        collapseControl={collapseControl}
        moduleDock={moduleDock}
      />
      <div
        className={"wbc-pane-layout" + (paneHasTwoColumns ? " split" : " single")}
        style={paneHasTwoColumns ? { "--wbc-pane-right-width": paneColumnWidth + "px" } : undefined}
      >
        {paneAxisDropAvailable ? (
          <div className="wbc-pane-axis-drop-layer" role="presentation">
            <div className={"wbc-pane-axis-drop-zone left" + (paneDropTarget && paneDropTarget.edge === "left" ? " active" : "")}>
              <span>{wbcT("workbenchChat.dropPaneLeft", "Release to open on the left")}</span>
            </div>
            <div className={"wbc-pane-axis-drop-zone right" + (paneDropTarget && paneDropTarget.edge === "right" ? " active" : "")}>
              <span>{wbcT("workbenchChat.dropPaneRight", "Release to open on the right")}</span>
            </div>
          </div>
        ) : null}
        {showNewConversationWorkspace
          ? <section className="wbc-pane-column left wbc-new-conversation-column">
              {renderPaneCard({ id: "new-conversation", kind: "chat", payload: "", ownerChatId: "" }, "left", 1)}
            </section>
          : <React.Fragment>
              {renderPaneColumn("left", paneLayout.left)}
              {renderPaneColumn("right", paneLayout.right)}
              {paneHasTwoColumns ? <WbcPaneColumnResizer width={paneColumnWidth} onResize={resizePaneColumn} /> : null}
            </React.Fragment>}
      </div>
      {projectTaskPanelCard && TaskContextPanelComponent ? (
        <TaskContextPanelComponent
          className="wbc-task-context-panel"
          project={project}
          session={projectTaskPanelSession}
          expandedStepId=""
          tab={Object.prototype.hasOwnProperty.call(taskRightTabs, projectTaskPanelId)
            ? taskRightTabs[projectTaskPanelId]
            : "context"}
          onTabChange={function (tab) {
            setTaskRightTabs(function (current) {
              return Object.assign({}, current, { [projectTaskPanelId]: String(tab || "") });
            });
          }}
          onRefresh={function (nextStore) { refreshTaskRightPanel(projectTaskPanelId, nextStore); }}
          onToggleSide={onToggleSide}
        />
      ) : null}
      {pageContextMenu && visibleChat && (
        <div className="wb-item-context-layer wbc-page-context-layer">
          <div className="wb-item-context-scrim" onPointerDown={closePageContextMenu} />
          <div
            className="wb-item-context-menu wbc-page-context-menu"
            role="menu"
            aria-label={wbcT("workbenchChat.quickActions", "Quick actions")}
            style={{ left: pageContextMenu.left + "px", top: pageContextMenu.top + "px" }}
            onContextMenu={function (event) { event.preventDefault(); }}
          >
            <WbcQuickActionItems
              chat={visibleChat}
              menu={true}
              onBeforeAction={closePageContextMenu}
              onRename={openQuickRename}
              onDelete={handleDelete}
              onToTask={handleToTask}
              toTaskBusy={toTaskBusy}
              onCompact={handleCompact}
              compactBusy={compactBusy}
              onGenerateMemory={handleGenerateMemory}
              memoryLearningBusy={memoryLearningBusy}
            />
          </div>
        </div>
      )}
      <WbcRenameDialog
        chat={quickRenameChat}
        onClose={function () { setQuickRenameChat(null); }}
        onRename={handleRenameChat}
      />
      {!projectPaneOnly && !floatingConversationPanelOpen && !splitDetailOpen ? renderConversationPanel(false) : null}
      {false ? <><WbcSideAgentSplitHost
        agent={splitSideAgent}
        agents={sideAgents}
        width={sideAgentSplitWidth}
        project={project}
        onOpenFile={openViewer}
        onUpdate={updateSideAgent}
        onSelect={selectSideAgent}
        onResize={resizeSideAgentSplit}
        onClose={closeSideAgentSplit}
        splitSide={splitSide}
        onToggleSide={toggleSplitSide}
        onSplitDragStart={handleSplitDragStart}
        onSplitDragEnd={handleSplitDragEnd}
      />
      <WbcArtifactSplitHost
        file={splitArtifact}
        items={artifactItems}
        width={sideAgentSplitWidth}
        onSelect={selectArtifact}
        onResize={resizeSideAgentSplit}
        onClose={closeArtifactSplit}
        onViewed={markViewerFileRead}
        splitSide={splitSide}
        onToggleSide={toggleSplitSide}
        onSplitDragStart={handleSplitDragStart}
        onSplitDragEnd={handleSplitDragEnd}
      />
      <WbcChangeSplitHost
        change={splitChange}
        width={sideAgentSplitWidth}
        onSelect={selectChange}
        onResize={resizeSideAgentSplit}
        onClose={closeChangeSplit}
        splitSide={splitSide}
        onToggleSide={toggleSplitSide}
        onSplitDragStart={handleSplitDragStart}
        onSplitDragEnd={handleSplitDragEnd}
      />
      <WbcArtifactSplitHost
        file={splitViewer}
        items={viewerItems}
        width={sideAgentSplitWidth}
        label={wbcT("workbenchChat.viewer", "Viewer")}
        onSelect={function (file) { selectResourceSplit("viewer", wbcArtifactFileKey(file)); }}
        onResize={resizeSideAgentSplit}
        onClose={closeResourceSplit}
        onViewed={markViewerFileRead}
        splitSide={splitSide}
        onToggleSide={toggleSplitSide}
        onSplitDragStart={handleSplitDragStart}
        onSplitDragEnd={handleSplitDragEnd}
      />
      <WbcMapSplitHost
        chatId={activeChatId}
        item={splitMap}
        width={sideAgentSplitWidth}
        onSelect={function (next) { selectResourceSplit("map", next); }}
        onResize={resizeSideAgentSplit}
        onClose={closeResourceSplit}
        splitSide={splitSide}
        onToggleSide={toggleSplitSide}
        onSplitDragStart={handleSplitDragStart}
        onSplitDragEnd={handleSplitDragEnd}
      />
      <WbcBrowserSplitHost
        tabId={splitBrowserTabId}
        browserState={activeBrowserState}
        browserSessionId={activeChatId || ""}
        width={sideAgentSplitWidth}
        onSelect={function (tabId) { selectResourceSplit("browser", tabId); }}
        onResize={resizeSideAgentSplit}
        onClose={closeResourceSplit}
        onTakeoverComplete={function (payload) {
          var pending = activeChat && activeChat.pendingQuestion;
          if (!pending || !pending.id) return Promise.reject(new Error("登录确认已不在等待中。"));
          handleAnswer(pending.id, (payload && payload.text) || "我已完成登录");
          return Promise.resolve();
        }}
        splitSide={splitSide}
        onToggleSide={toggleSplitSide}
        onSplitDragStart={handleSplitDragStart}
        onSplitDragEnd={handleSplitDragEnd}
      />
      <WbcSubagentsSplitHost
        open={splitSubagents}
        data={subagentData}
        loading={subagentLoading}
        width={sideAgentSplitWidth}
        onSelectRound={function (roundId) { loadSubagents(activeChatId, roundId); }}
        onResize={resizeSideAgentSplit}
        onClose={closeResourceSplit}
        splitSide={splitSide}
        onToggleSide={toggleSplitSide}
        onSplitDragStart={handleSplitDragStart}
        onSplitDragEnd={handleSplitDragEnd}
      />
      <WbcChatSplitHost
        chatId={splitChatId}
        project={project}
        width={sideAgentSplitWidth}
        onOpenContent={openSplitChatContent}
        browserActiveByChat={browserActiveByChat}
        onResize={resizeSideAgentSplit}
        onClose={closeResourceSplit}
        onOpenInMain={selectChat}
        splitSide={splitSide}
        onToggleSide={toggleSplitSide}
        onSplitDragStart={handleSplitDragStart}
        onSplitDragEnd={handleSplitDragEnd}
      /></> : null}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Conversation rail (column 2)
// ---------------------------------------------------------------------------

export { WorkbenchChatPage }
