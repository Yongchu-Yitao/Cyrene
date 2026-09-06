import { workbenchServices } from "../../shared/runtime/services.jsx"
import { PluginFrontendService, PluginView, pluginLocalizedField } from "../../platform/plugins.jsx"
import { WBC_ICONS, WbcVoice, WorkbenchChatModel, useWbcEffect, useWbcLayoutEffect, useWbcRef, useWbcState, wbcCaptureConversationViewport, wbcChatCache, wbcChatSideDropZone, wbcChatSideZoneRect, wbcClampSideSplitWidth, wbcClampSideSplitWidthForPage, wbcDefaultPaneLayout, wbcErrorText, wbcFileViewKind, wbcHasChatDrag, wbcHasPluginViewDrag, wbcHasResourceDrag, wbcHasSplitDrag, wbcLastChatByProject, wbcLoadDraftAgentBinding, wbcMergeChronologicalMessages, wbcNormalizePaneLayout, wbcNormalizePermissionMode, wbcNotifyBrowserWindowInteraction, wbcOpenAgentDetail, wbcPinPageSplitLayout, wbcPinSplitMotionOpen, wbcPreserveLiveTimelineAnchors, wbcReadChatDrag, wbcReadPluginViewDrag, wbcReleasePinnedPageSplitLayout, wbcReleasePinnedSplitMotion, wbcRestoreConversationViewport, wbcSaveDraftAgentBinding, wbcT } from "../../workbench-chat.jsx"
import { WBC_PROJECT_FILE_DRAFTS, WbcArtifactSplit, WbcArtifactSplitHost, WbcBrowserSplit, WbcBrowserSplitHost, WbcChangeSplit, WbcChangeSplitHost, WbcChatSplit, WbcChatSplitHost, WbcMapPaneContent, WbcMapSplitHost, WbcPaneCardFrame, WbcPaneColumnResizer, WbcPaneContextTrackDropSurface, WbcPaneRowResizer, WbcSide, WbcSideAgentSplit, WbcSideAgentSplitHost, WbcSplitGripBar, WbcSubagentsSplitHost, WbcSubagentsTab, wbcArtifactFileKey, wbcChatArtifactFiles, wbcDiscardProjectFileDraft, wbcProjectFileDraftKey } from "./split-pane.jsx"
import { WorkbenchChatRuntimes, wbcRuntimePresenceSnapshot, wbcSameRuntimePresence } from "./file-resources.jsx"
import { resolveRefreshedChatSelection as wbcResolveRefreshedChatSelection } from "./behavior.mjs"
import { WorkbenchFileDropOverlay, useWorkbenchFileDrop } from "../../shared/file-drop.jsx"
import { WbcRail, WbcRenameDialog } from "./rail.jsx"
import { WbcMain } from "./conversation.jsx"
import { wbcBrowserStateForChat } from "./composer.jsx"
import { WbcChatPageContextMenu, wbcClearPendingPageContextMenu, wbcClosePageContextMenu, wbcOpenPageContextMenu, wbcSetOpenPageContextMenu } from "./page-context-menu.jsx"
import { useWbcTerminalCatalog, wbcCreateTerminal, wbcDeleteTerminal, wbcOpenTerminal, wbcReplaceWithTerminal, wbcShowAgentTerminal, wbcSubscribeTerminalRefresh } from "./terminal-controller.jsx"
import { useWbcChatRequestSequencer } from "./request-sequencer.jsx"
import { wbcPaneWorkspacePresentation } from "./pane-workspace.jsx"
import { wbcCancelPaneCardDetachment, wbcClearPaneCardDetachSubscription, wbcCompletePaneCardDetachment, wbcPaneCardDetachIpcPayload, wbcRestoreReturnedDetachedPane } from "./pane-detachment.jsx"
import { wbcStartSplitDrag } from "./split-drag-controller.jsx"
import { wbcFinishPaneCardDrag, wbcStartPaneCardDrag } from "./pane-card-drag-controller.jsx"
import { wbcHandleResourceSplitDragOver, wbcHandleResourceSplitDrop, wbcResourceSplitDropGeometry, wbcResourceSplitSideAt } from "./page-drop-controller.jsx"
import { wbcHandlePaneDrop, wbcPlaceExistingPaneCard } from "./pane-drop-controller.jsx"
import { WbcPaneSemanticController, wbcPaneSemanticNodeId } from "./pane-semantic-controller.jsx"
import { wbcLoadSubagents, wbcMarkViewerFileRead, wbcOpenProjectFile, wbcOpenViewer, wbcRevealTopbarResource } from "./page-resource-controller.jsx"
import { useWbcRuntimePageHooks, wbcMergeChatSummary } from "./runtime-page-hooks.jsx"
import { wbcCloseDeletedChatSplits, wbcClosePaneCard, wbcCreatePaneConversation, wbcMovePaneCard, wbcMovePaneCardOtherSide, wbcOpenPaneContent, wbcPaneContentCard, wbcPaneLayoutFor, wbcPaneOwnerKey, wbcProjectPaneOwnerKey, wbcResizePaneRow, wbcRestoreTerminalReplacement, wbcSwapPaneCards, wbcUpdatePaneCard, wbcUpdatePaneLayout } from "./pane-layout-controller.jsx"
import { wbcCloseActiveSplit, wbcCloseMainConversationSplit, wbcCloseNamedSplit, wbcCloseResourceSplit, wbcDeleteSideAgent, wbcRestoreSplitState, wbcSelectArtifact, wbcSelectChange, wbcSelectResourceSplit, wbcSelectSideAgent, wbcSplitStateSnapshot, wbcUpdateSideAgent } from "./split-selection-controller.jsx"
import { wbcAnswerQuestionForChat, wbcHandleCreateChat, wbcHandleEditMessage, wbcHandleGuidance, wbcHandleRename, wbcHandleRenameChat, wbcHandleRetryMessage, wbcOpenQuickRename } from "./chat-action-controller.jsx"
import { useWbcLiveEventController } from "./live-event-controller.jsx"
import { WBC_SURFACE_INTENT_EVENT, WbcSurfaceHost } from "./dynamic-surfaces.jsx"
import { WbcGoalConfirmationDialog } from "../goal/goal-ui.jsx"
import { wbcClaimSurfaceCard, wbcNormalizeSurfaceIntent, wbcPinSurfaceCard, wbcRevealSurface, wbcSurfaceResourceKey } from "./dynamic-surface-broker.mjs"
import { wbWorkspaceTabsFromPaneLayouts } from "./workspace-tabs.jsx"
import { wbcClearPaneWorkspace, wbcReadPaneWorkspace, wbcValidatePaneWorkspace, wbcWritePaneWorkspace } from "./pane-workspace-persistence.mjs"

// Workbench chat feature module with explicit ESM dependencies.
function wbcCompleteBrowserTakeover(activeChat, payload, handleAnswer) {
  var pending = activeChat && activeChat.pendingQuestion;
  if (!pending || !pending.id) {
    return Promise.reject(new Error(wbcT(
      "workbenchChat.takeover.noLongerPending",
      "The sign-in confirmation is no longer pending."
    )));
  }
  var takeoverQuestionId = String(payload && payload.questionId || "");
  if (takeoverQuestionId && String(pending.id || "") !== takeoverQuestionId) {
    return Promise.reject(new Error(wbcT(
      "workbenchChat.takeover.updated",
      "The sign-in confirmation changed. Use the latest confirmation in the conversation."
    )));
  }
  handleAnswer(
    pending.id,
    (payload && payload.text) || wbcT("browser.takeover.completeLogin", "I completed sign-in")
  );
  return Promise.resolve();
}

function wbcWorkspaceSurfaceDescriptor(value, catalog, chatId) {
  var normalized = wbcNormalizeSurfaceIntent(Object.assign({}, value || {}, {
    chatId: String(chatId || ""),
  }), catalog);
  if (!normalized) return null;
  var surface = (Array.isArray(catalog) ? catalog : []).find(function (item) {
    return String(item && item.id || "") === normalized.surfaceId
      && String(item && item.pack_id || "") === normalized.packId;
  });
  var renderer = surface && surface.renderer && typeof surface.renderer === "object"
    ? surface.renderer : {};
  return renderer.kind === "native" && renderer.id === "workspace-composite"
    ? normalized : null;
}

function wbcDurableWorkspaceSurfaceDescriptor(descriptor, chatId) {
  if (!descriptor) return null;
  return {
    schemaVersion: 1,
    surfaceId: descriptor.surfaceId,
    packId: descriptor.packId,
    resource: descriptor.resource,
    resourceKey: descriptor.resourceKey,
    activity: descriptor.activity,
    attention: "reveal",
    priority: descriptor.priority,
    lifetime: descriptor.lifetime,
    preferredSide: descriptor.preferredSide,
    chatId: String(chatId || ""),
  };
}

function useWbcWorkspaceSurfaceState(chats, model, setChats, setActiveChat) {
  var [descriptors, setDescriptors] = useWbcState({});
  var [catalog, setCatalog] = useWbcState(function () {
    var snapshot = PluginFrontendService.snapshot();
    return Array.isArray(snapshot.workbenchSurfaces) ? snapshot.workbenchSurfaces : [];
  });
  var persistedRef = useWbcRef(new Map());
  useWbcEffect(function () {
    return PluginFrontendService.subscribe(function (snapshot) {
      setCatalog(Array.isArray(snapshot.workbenchSurfaces) ? snapshot.workbenchSurfaces : []);
    });
  }, []);
  useWbcEffect(function () {
    chats.forEach(function (chat) {
      if (!chat || !chat.id || !chat.workspaceSurface) return;
      persistedRef.current.set(String(chat.id), JSON.stringify(chat.workspaceSurface));
    });
  }, [chats]);
  function persist(chatId, descriptor) {
    var durable = wbcDurableWorkspaceSurfaceDescriptor(descriptor, chatId);
    if (!chatId || !durable) return;
    var signature = JSON.stringify(durable);
    if (persistedRef.current.get(chatId) === signature) return;
    persistedRef.current.set(chatId, signature);
    model.updateChatPreferences(chatId, { workspaceSurface: durable })
      .then(function (updated) {
        var persisted = updated && updated.workspaceSurface ? updated.workspaceSurface : durable;
        persistedRef.current.set(chatId, JSON.stringify(persisted));
        setChats(function (current) {
          return current.map(function (chat) {
            return String(chat && chat.id || "") === chatId
              ? Object.assign({}, chat, { workspaceSurface: persisted }) : chat;
          });
        });
        setActiveChat(function (current) {
          return current && String(current.id || "") === chatId
            ? Object.assign({}, current, { workspaceSurface: persisted }) : current;
        });
      })
      .catch(function () {
        if (persistedRef.current.get(chatId) === signature) persistedRef.current.delete(chatId);
        workbenchServices.feedback().showToast(
          wbcT("workbenchChat.workspaceStateSaveFailed", "The workspace state could not be saved."),
          "error"
        );
      });
  }
  return { catalog: catalog, descriptors: descriptors, persist: persist, setDescriptors: setDescriptors };
}

function useWbcSurfaceIntentListener(options) {
  useWbcEffect(function () {
    function onSurfaceIntent(event) {
      var intent = event && event.detail && typeof event.detail === "object" ? event.detail : {};
      var ownerChatId = String(intent.chatId || intent.chat_id || options.activeChatIdRef.current || "");
      var ownerId = ownerChatId || (options.projectId ? "project:" + String(options.projectId) : "");
      if (!ownerId) return;
      var declaredSurfaces = PluginFrontendService.snapshot().workbenchSurfaces;
      var catalog = Array.isArray(declaredSurfaces) ? declaredSurfaces : [];
      var workspaceDescriptor = wbcWorkspaceSurfaceDescriptor(
        wbcNormalizeSurfaceIntent(intent, catalog), catalog, ownerChatId
      );
      var result = null;
      options.setPaneLayoutsByChat(function (current) {
        var previous = wbcNormalizePaneLayout(current[ownerId], ownerChatId);
        result = wbcRevealSurface(previous, Object.assign({}, intent, { chatId: ownerChatId }), {
          catalog: catalog,
          isSuppressed: function (runId, resourceKey) {
            return options.surfaceSuppressionRef.current.has(String(runId || "") + "\n" + String(resourceKey || ""));
          },
          canReplace: function (card) {
            var resource = card && card.payload && card.payload.resource || {};
            if (resource.kind !== "file") return true;
            var draftKey = wbcProjectFileDraftKey({
              source: "project",
              projectId: resource.projectId || resource.project_id || options.projectId,
              path: resource.path,
            });
            return !(draftKey && WBC_PROJECT_FILE_DRAFTS[draftKey]);
          },
          canOpen: function (normalizedIntent) {
            var resourceKind = normalizedIntent && normalizedIntent.resource && normalizedIntent.resource.kind;
            if (["file", "directory"].indexOf(resourceKind) < 0) return true;
            var runId = String(normalizedIntent.runId || "");
            return !runId || !options.surfaceRevealedRunRef.current.has(runId);
          },
        });
        if (result && ["opened", "replaced"].indexOf(result.outcome) >= 0) {
          var resourceKind = intent && intent.resource && intent.resource.kind;
          var openedRunId = String(intent.runId || intent.run_id || "");
          if (openedRunId && ["file", "directory"].indexOf(resourceKind) >= 0) {
            options.surfaceRevealedRunRef.current.add(openedRunId);
            if (options.surfaceRevealedRunRef.current.size > 500) {
              options.surfaceRevealedRunRef.current.delete(options.surfaceRevealedRunRef.current.values().next().value);
            }
          }
        }
        return !result || result.layout === previous
          ? current : Object.assign({}, current, { [ownerId]: result.layout });
      });
      window.setTimeout(function () {
        if (workspaceDescriptor && result && ["opened", "replaced", "updated"].indexOf(result.outcome) >= 0) {
          options.setWorkspaceSurfaceDescriptors(function (current) {
            return Object.assign({}, current, { [ownerId]: workspaceDescriptor });
          });
          options.persistWorkspaceSurface(ownerChatId, workspaceDescriptor);
        }
        window.dispatchEvent(new CustomEvent("cyrene:surface-result", {
          detail: Object.assign({}, result || { outcome: "unavailable" }, {
            surfaceId: String(intent.surfaceId || intent.surface_id || intent.surface || ""),
            resourceKey: String(intent.resourceKey || intent.resource_key || ""),
          }),
        }));
      }, 0);
    }
    window.addEventListener(WBC_SURFACE_INTENT_EVENT, onSurfaceIntent);
    return function () { window.removeEventListener(WBC_SURFACE_INTENT_EVENT, onSurfaceIntent); };
  }, [options.projectId]);
}

function wbcOpenStartedWorkspace(options) {
  if (!options.descriptor) return false;
  var result = null;
  options.setPaneLayoutsByChat(function (current) {
    var previous = wbcNormalizePaneLayout(current[options.ownerId], options.ownerChatId);
    result = wbcRevealSurface(previous, Object.assign({}, options.descriptor, {
      attention: "reveal", chatId: options.ownerChatId,
    }), {
      catalog: PluginFrontendService.snapshot().workbenchSurfaces,
      canReplace: function (card) {
        var resource = card && card.payload && card.payload.resource || {};
        if (resource.kind !== "file") return true;
        var draftKey = wbcProjectFileDraftKey({
          source: "project",
          projectId: resource.projectId || resource.project_id || options.projectId,
          path: resource.path,
        });
        return !(draftKey && WBC_PROJECT_FILE_DRAFTS[draftKey]);
      },
    });
    if (!result || result.layout === previous) return current;
    var claimedLayout = Object.assign({}, result.layout);
    ["left", "right"].forEach(function (side) {
      claimedLayout[side] = result.layout[side].map(function (card) {
        return String(card && card.id || "") === String(result.cardId || "")
          ? wbcClaimSurfaceCard(card) : card;
      });
    });
    return Object.assign({}, current, { [options.ownerId]: claimedLayout });
  });
  window.setTimeout(function () {
    if (result && result.outcome === "deferred") {
      workbenchServices.feedback().showToast(
        wbcT("workbenchChat.workspaceOpenDeferred", "No split slot is available. Close or unpin a split and try again."),
        "warning"
      );
    }
  }, 0);
  options.setSideTab("");
  return true;
}

function WorkbenchChatPage({ active, project, workspaceContent, onActivateWorkspace, newChatRequestId, pinnedSessions, onActiveChatChange, onActiveChatIdChange, onChatsChange, onWorkspaceTabsChange, workspaceTabRequest, onWorkspaceTabRequestHandled, navCollapsed, onToggleNavCollapsed, collapseControl, moduleDock }) {
  workbenchServices.i18n().use();
  var dataStore = workbenchServices.data();
  dataStore.useVersion();
  var pluginModules = Array.isArray(dataStore.state.pluginModules)
    ? dataStore.state.pluginModules : [];
  var codeAvailable = pluginModules.indexOf("code") >= 0;
  var agentsAvailable = pluginModules.indexOf("agents") >= 0;
  var mapAvailable = pluginModules.indexOf("map") >= 0;
  var browserAvailable = pluginModules.indexOf("browser") >= 0;
  var memoryAvailable = pluginModules.indexOf("memory") >= 0;
  var knowledgeAvailable = pluginModules.indexOf("knowledge") >= 0;
  pinnedSessions = pinnedSessions || {};
  var pinnedChatIds = pinnedSessions.chatIds;
  var onTogglePinnedChat = pinnedSessions.onToggleChat;
  var isActive = active !== false;
  var model = WorkbenchChatModel;
  var projectId = project ? project.id : "";
  var terminalModule = workbenchServices.terminal();
  var terminalCatalog = useWbcTerminalCatalog(projectId, codeAvailable);
  var terminalClient = terminalCatalog.client;
  var terminals = terminalCatalog.terminals;
  var setTerminals = terminalCatalog.setTerminals;
  var terminalsLoading = terminalCatalog.loading;
  var terminalsHydrated = terminalCatalog.hydrated;
  var setTerminalsLoading = terminalCatalog.setLoading;
  var activeTerminalId = terminalCatalog.activeId;
  var setActiveTerminalId = terminalCatalog.setActiveId;
  var [railMode, setRailMode] = useWbcState("chat");
  // Switching the rail is browsing, not navigation. Hide selection in the
  // newly opened list until the user explicitly chooses one of its cards.
  var [railSelectionSuppressed, setRailSelectionSuppressed] = useWbcState(false);
  var chatCache = wbcChatCache();
  var [chats, setChats] = useWbcState([]);
  var chatsRef = useWbcRef([]);
  var chatsProjectIdRef = useWbcRef("");
  var requestSequencer = useWbcChatRequestSequencer();
  var beginChatHydration = requestSequencer.beginHydration;
  var isCurrentChatHydration = requestSequencer.isCurrentHydration;
  var beginChatListRequest = requestSequencer.beginList;
  var isCurrentChatListRequest = requestSequencer.isCurrentList;
  var [activeChatId, setActiveChatId] = useWbcState("");
  var activeChatIdRef = useWbcRef("");
  // Project-owned panes (for example Remote Desktop) are a workspace mode,
  // not a synthetic conversation. Keep that intent across background chat
  // list refreshes while the active chat id is deliberately empty.
  var projectPaneActiveRef = useWbcRef(false);
  var [selectedWorkspaceTabKey, setSelectedWorkspaceTabKey] = useWbcState("");
  var workspaceTabRefreshRequestRef = useWbcRef(0);
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
  // Draft Agent binding for a not-yet-created chat (handoff §8.3): the first
  // message's lazy createChat() submits this binding instead of creating a
  // default-Agent chat and immediately rebinding it.
  var [draftAgentBinding, setDraftAgentBinding] = useWbcState(function () {
    return wbcLoadDraftAgentBinding(projectId);
  });
  var draftAgentBindingRef = useWbcRef(draftAgentBinding);
  if (!agentsAvailable) draftAgentBindingRef.current = null;
  useWbcEffect(function () { draftAgentBindingRef.current = draftAgentBinding; }, [draftAgentBinding]);
  useWbcEffect(function () {
    if (agentsAvailable) return;
    draftAgentBindingRef.current = null;
    setDraftAgentBinding(null);
    wbcSaveDraftAgentBinding(projectId, null);
  }, [agentsAvailable, projectId]);
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
    var confirmModal = workbenchServices.feedback().confirmModal;
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
    projectPaneActiveRef.current = false;
    setRailSelectionSuppressed(false);
    setRailMode("chat");
    restoreTerminalReplacement(previousId);
    if (nextId !== previousId) restoreTerminalReplacement(nextId);
    // Publish selection intent immediately. Passive effects run too late to
    // protect a newly-created chat from an already in-flight list refresh.
    activeChatIdRef.current = nextId;
    setSelectedWorkspaceTabKey("");
    setActiveChatId(nextId);
  }
  function activateProjectPaneWorkspace() {
    var previousId = String(activeChatIdRef.current || "");
    projectPaneActiveRef.current = true;
    activeChatIdRef.current = "";
    setRailSelectionSuppressed(true);
    setSelectedWorkspaceTabKey("");
    setActiveChatId("");
    if (previousId) restoreTerminalReplacement(previousId);
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
  var handledNewChatRequestIdRef = useWbcRef(0);
  function refreshTerminals(options) {
    return terminalCatalog.refresh(options);
  }

  useWbcEffect(function () {
    setActiveTerminalId("");
    setRailSelectionSuppressed(false);
    setRailMode("chat");
    refreshTerminals();
  }, [projectId, codeAvailable]);

  useWbcEffect(function () {
    if (!codeAvailable || !projectId || !isActive) return undefined;
    return wbcSubscribeTerminalRefresh(projectId, refreshTerminals);
  }, [projectId, isActive, codeAvailable]);

  function terminalActionContext() {
    return {
      projectId: projectId,
      terminals: terminals,
      terminalClient: terminalClient,
      activeChatIdRef: activeChatIdRef,
      paneLayoutRestoreRef: paneLayoutRestoreRef,
      setTerminals: setTerminals,
      setTerminalsLoading: setTerminalsLoading,
      setActiveTerminalId: setActiveTerminalId,
      setPaneLayoutsByChat: setPaneLayoutsByChat,
      paneLayoutFor: paneLayoutFor,
      paneContentCard: paneContentCard,
      updatePaneLayout: updatePaneLayout,
      openPaneContent: openPaneContent,
      updateTerminalSummary: updateTerminalSummary,
      replaceWithTerminal: replaceWithTerminal,
    };
  }

  function updateTerminalSummary(terminal) { terminalCatalog.updateSummary(terminal); }
  function openTerminal(terminalId, side) { return wbcOpenTerminal(terminalActionContext(), terminalId, side); }
  function showAgentTerminal(terminalId, preferredSide) { return wbcShowAgentTerminal(terminalActionContext(), terminalId, preferredSide); }
  function replaceWithTerminal(terminalId, options) { return wbcReplaceWithTerminal(terminalActionContext(), terminalId, options); }
  function createTerminal() { return wbcCreateTerminal(terminalActionContext()); }

  function renameTerminal(terminalId, title) {
    return terminalCatalog.rename(terminalId, title);
  }

  function updateTerminalLayout(order, pinned) {
    return terminalCatalog.updateLayout(order, pinned);
  }

  function deleteTerminal(terminalId) { return wbcDeleteTerminal(terminalActionContext(), terminalId); }

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
  var [paneLayoutsByChat, setPaneLayoutsByChat] = useWbcState(function () {
    var restored = wbcReadPaneWorkspace(projectId);
    if (!restored) return {};
    var ownerId = restored.activeChatId || (projectId ? "project:" + String(projectId) : "");
    return ownerId ? { [ownerId]: restored.layout } : {};
  });
  var paneWorkspaceLoadedProjectRef = useWbcRef(projectId);
  var paneWorkspaceValidatedProjectRef = useWbcRef("");
  var paneWorkspaceSkipPersistRef = useWbcRef(false);
  useWbcEffect(function () {
    if (paneWorkspaceLoadedProjectRef.current === projectId) return;
    paneWorkspaceLoadedProjectRef.current = projectId;
    paneWorkspaceValidatedProjectRef.current = "";
    var restored = wbcReadPaneWorkspace(projectId);
    if (!restored) return;
    var ownerId = restored.activeChatId || (projectId ? "project:" + String(projectId) : "");
    if (!ownerId) return;
    setPaneLayoutsByChat(function (current) {
      return Object.assign({}, current, { [ownerId]: restored.layout });
    });
  }, [projectId]);
  var layoutProjectionRevisionRef = useWbcRef({});
  var observationLifecycleRef = useWbcRef({ ids: {}, finishTimers: {} });
  var [resourceObservationStates, setResourceObservationStates] = useWbcState({});
  useWbcEffect(function () {
    var events = window.CyreneUI && window.CyreneUI.events;
    if (!events || typeof events.subscribe !== "function") return undefined;
    function eventKey(event) {
      var paneCardId = String(event && event.pane_card_id || "");
      if (paneCardId) return "pane:" + paneCardId;
      var kind = String(event && event.resource_kind || "");
      var id = String(event && event.resource_id || "");
      return kind && id ? "resource:" + kind + ":" + id : "";
    }
    var unsubscribe = events.subscribe(function (event) {
      var type = String(event && event.type || "");
      if (type !== "resource_observation.started" && type !== "resource_observation.ended") return;
      var lifecycle = observationLifecycleRef.current;
      var observationId = String(event && event.observation_id || "");
      var key = type === "resource_observation.ended" && observationId
        ? String(lifecycle.ids[observationId] || eventKey(event)) : eventKey(event);
      if (!key) return;
      if (type === "resource_observation.started") {
        if (observationId && lifecycle.ids[observationId]) return;
        if (observationId) lifecycle.ids[observationId] = key;
        if (lifecycle.finishTimers[key]) {
          window.clearTimeout(lifecycle.finishTimers[key]);
          delete lifecycle.finishTimers[key];
        }
        setResourceObservationStates(function (current) {
          var previous = current[key] || { count: 0, finishing: false };
          return Object.assign({}, current, { [key]: { count: previous.count + 1, finishing: false } });
        });
        return;
      }
      if (observationId) delete lifecycle.ids[observationId];
      setResourceObservationStates(function (current) {
        var previous = current[key] || { count: 1, finishing: false };
        var count = Math.max(0, previous.count - 1);
        return Object.assign({}, current, { [key]: { count: count, finishing: count === 0 } });
      });
      lifecycle.finishTimers[key] = window.setTimeout(function () {
        delete lifecycle.finishTimers[key];
        setResourceObservationStates(function (current) {
          var value = current[key];
          if (!value || value.count > 0) return current;
          var next = Object.assign({}, current);
          delete next[key];
          return next;
        });
      }, 520);
    });
    return function () {
      unsubscribe();
      Object.values(observationLifecycleRef.current.finishTimers).forEach(function (timer) {
        window.clearTimeout(timer);
      });
      observationLifecycleRef.current = { ids: {}, finishTimers: {} };
    };
  }, []);
  var workspaceSurfaces = useWbcWorkspaceSurfaceState(chats, model, setChats, setActiveChat);
  var workspaceSurfaceDescriptors = workspaceSurfaces.descriptors;
  var surfaceCatalog = workspaceSurfaces.catalog;
  var surfaceSuppressionRef = useWbcRef(new Map());
  var surfaceRevealedRunRef = useWbcRef(new Set());

  useWbcSurfaceIntentListener({
    activeChatIdRef: activeChatIdRef, persistWorkspaceSurface: workspaceSurfaces.persist,
    projectId: projectId, setPaneLayoutsByChat: setPaneLayoutsByChat,
    setWorkspaceSurfaceDescriptors: workspaceSurfaces.setDescriptors,
    surfaceRevealedRunRef: surfaceRevealedRunRef, surfaceSuppressionRef: surfaceSuppressionRef,
  });

  useWbcEffect(function () {
    function claimSurface(event) {
      var resourceKey = String(event && event.detail && event.detail.resourceKey || "");
      if (!resourceKey) return;
      setPaneLayoutsByChat(function (current) {
        var changed = false;
        var next = {};
        Object.keys(current).forEach(function (ownerId) {
          var layout = current[ownerId];
          var ownerChanged = false;
          var patched = Object.assign({}, layout);
          ["left", "right"].forEach(function (side) {
            patched[side] = (layout[side] || []).map(function (card) {
              if (card.kind !== "surface" || String(card.payload && card.payload.resourceKey || "") !== resourceKey) return card;
              ownerChanged = true;
              return wbcClaimSurfaceCard(card);
            });
          });
          changed = changed || ownerChanged;
          next[ownerId] = ownerChanged ? patched : layout;
        });
        return changed ? next : current;
      });
    }
    window.addEventListener("cyrene:surface-claim", claimSurface);
    return function () { window.removeEventListener("cyrene:surface-claim", claimSurface); };
  }, []);

  useWbcEffect(function () {
    function planUpdated(event) {
      var detail = event && event.detail || {};
      var chatId = String(detail.chatId || "");
      var plan = detail.plan;
      if (!chatId || !plan || typeof plan !== "object") return;
      setActiveChat(function (current) {
        if (!current || String(current.id || "") !== chatId) return current;
        var next = Object.assign({}, current, { activePlan: plan });
        chatCache.details[chatId] = next;
        return next;
      });
      setChats(function (current) { return current.map(function (chat) {
        return String(chat && chat.id || "") === chatId ? Object.assign({}, chat, { activePlan: plan }) : chat;
      }); });
    }
    window.addEventListener("cyrene:conversation-plan-updated", planUpdated);
    return function () { window.removeEventListener("cyrene:conversation-plan-updated", planUpdated); };
  }, []);

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
        if (!id) return { ok: false, error: "terminal_id_required" };
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

  function paneLayoutContext() {
    return {
      projectId: projectId, model: model, chatCache: chatCache,
      paneLayoutsByChat: paneLayoutsByChat, activeChatIdRef: activeChatIdRef,
      paneLayoutRestoreRef: paneLayoutRestoreRef, floatingSplitRestoreRef: floatingSplitRestoreRef,
      terminalClient: terminalClient,
      setPaneLayoutsByChat: setPaneLayoutsByChat, setResourceSplitByChat: setResourceSplitByChat,
      setActiveTerminalId: setActiveTerminalId, setRailSelectionSuppressed: setRailSelectionSuppressed,
      setRailMode: setRailMode, setActiveChatId: setActiveChatId, setActiveChat: setActiveChat,
      setChats: setChats, setError: setError, setErrorKind: setErrorKind,
      selectChat: selectChat,
    };
  }
  function projectPaneOwnerKey() { return wbcProjectPaneOwnerKey(paneLayoutContext()); }
  function paneOwnerKey(chatId) { return wbcPaneOwnerKey(paneLayoutContext(), chatId); }
  function paneLayoutFor(chatId) { return wbcPaneLayoutFor(paneLayoutContext(), chatId); }
  function restoreTerminalReplacement(ownerChatId) { return wbcRestoreTerminalReplacement(paneLayoutContext(), ownerChatId); }
  function updatePaneLayout(updater, ownerChatId) { return wbcUpdatePaneLayout(paneLayoutContext(), updater, ownerChatId); }
  function paneContentCard(type, payload, ownerChatId) { return wbcPaneContentCard(paneLayoutContext(), type, payload, ownerChatId); }
  function openPaneContent(type, payload, options) { return wbcOpenPaneContent(paneLayoutContext(), type, payload, options); }

  function updatePaneCard(cardId, updater) { return wbcUpdatePaneCard(paneLayoutContext(), cardId, updater); }
  function closePaneCard(cardId, ownerChatId) { return wbcClosePaneCard(paneLayoutContext(), cardId, ownerChatId); }
  function closeDeletedChatSplits(chatId) { return wbcCloseDeletedChatSplits(paneLayoutContext(), chatId); }
  function paneDetachmentContext() {
    return {
      activeChatIdRef: activeChatIdRef,
      chatsRef: chatsRef,
      paneCardDetachRef: paneCardDetachRef,
      paneCardDragImageCleanupRef: paneCardDragImageCleanupRef,
      setPaneCardDragId: setPaneCardDragId,
      setPaneDropTarget: setPaneDropTarget,
      paneLayoutFor: paneLayoutFor,
      updatePaneLayout: updatePaneLayout,
      selectChat: selectChat,
      closePaneCard: closePaneCard,
    };
  }
  function clearPaneCardDetachSubscription(pendingDetach) { return wbcClearPaneCardDetachSubscription(pendingDetach); }
  function cancelPaneCardDetachment(pendingDetach) { return wbcCancelPaneCardDetachment(paneDetachmentContext(), pendingDetach); }
  function paneCardDetachIpcPayload(pendingDetach, extra) { return wbcPaneCardDetachIpcPayload(pendingDetach, extra); }
  function completePaneCardDetachment(pendingDetach) { return wbcCompletePaneCardDetachment(paneDetachmentContext(), pendingDetach); }
  function restoreReturnedDetachedPane(info) { return wbcRestoreReturnedDetachedPane(paneDetachmentContext(), info); }

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

  function movePaneCardOtherSide(cardId) { return wbcMovePaneCardOtherSide(paneLayoutContext(), cardId); }
  function createPaneConversation(cardId) { return wbcCreatePaneConversation(paneLayoutContext(), cardId); }
  function resizePaneRow(side, ratio) { return wbcResizePaneRow(paneLayoutContext(), side, ratio); }
  function movePaneCard(cardId, options) { return wbcMovePaneCard(paneLayoutContext(), cardId, options); }
  function swapPaneCards(firstCardId, secondCardId) { return wbcSwapPaneCards(paneLayoutContext(), firstCardId, secondCardId); }
  // The split panel is lifted with a native drag, same as images/documents:
  // the panel stays in place, a drag ghost follows the pointer (shrinking to
  // a chat card over the rail), and the drop zones (rail = close, main
  // left/right half = anchored side) light up under the cursor. The ghost and
  // zones are created as raw DOM during the drag session — never through
  // React — so the drag source's DOM stays untouched while Chromium is
  // tracking the gesture (any React re-render here cancels the drag).
  var splitOverlayCleanupRef = useWbcRef(null);

  function handleSplitDragStart(event, dragSource) {
    return wbcStartSplitDrag({
      pageRef: pageRef,
      splitOverlayCleanupRef: splitOverlayCleanupRef,
      activeChatIdRef: activeChatIdRef,
      splitSide: splitSide,
      splitChatId: splitChatId,
      splitSideAgentId: splitSideAgentId,
      setSplitSideDirect: setSplitSideDirect,
      handleSplitDragEnd: handleSplitDragEnd,
      closeMainConversationSplit: closeMainConversationSplit,
      closeResourceSplit: closeResourceSplit,
      closeActiveSplit: closeActiveSplit,
    }, event, dragSource);
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

  useWbcEffect(function () {
    function openGoalTab(event) {
      var requestedChatId = String(event && event.detail && event.detail.chatId || "");
      if (requestedChatId && requestedChatId !== String(activeChatIdRef.current || "")) return;
      setSideVisible(true);
      setSideTab("goal");
    }
    window.addEventListener("workbench:open-goal-tab", openGoalTab);
    return function () { window.removeEventListener("workbench:open-goal-tab", openGoalTab); };
  }, []);
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
  var knowledgeReadControllersRef = useWbcRef(new Set());
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
  var [compactBusy, setCompactBusy] = useWbcState(false);
  var [memoryLearningBusy, setMemoryLearningBusy] = useWbcState(false);
  var [pageContextMenu, setPageContextMenu] = useWbcState(null);
  var pageContextMenuRef = useWbcRef(null);
  var pendingPageContextMenuRef = useWbcRef(null);
  var pageContextPreviewTimerRef = useWbcRef(null);
  var [quickRenameChat, setQuickRenameChat] = useWbcState(null);
  // Streaming runtimes live in the module-level engine so a run survives this
  // page unmounting when the user switches modules mid-reply. The page only
  // observes semantic runtime changes for the side panel and rail; the message list
  // subscribes to token-level updates locally so a delta cannot repaint the
  // page layout or its sibling panes.
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
    return runtimeEngine.subscribeSummary(applyRuntimeSnapshot);
  }, []);
  useWbcEffect(function () {
    setActiveRuntime(runtimeEngine.get(activeChatId));
  }, [activeChatId]);

  useWbcEffect(function () {
    var chatId = String(activeChatId || "");
    var cancelled = false;
    if (!chatId) {
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

  function pageResourceContext() {
    return {
      projectId: projectId, model: model, activeChatIdRef: activeChatIdRef,
      setViewerFile: setViewerFile, setSideTab: setSideTab, setSideVisible: setSideVisible,
      setBrowserActiveByChat: setBrowserActiveByChat,
      setBrowserWindowModeByChat: setBrowserWindowModeByChat,
      knowledgeAvailable: knowledgeAvailable,
      knowledgeReadControllersRef: knowledgeReadControllersRef,
      setSubagentData: setSubagentData, setSubagentLoading: setSubagentLoading,
      setError: setError, selectResourceSplit: selectResourceSplit,
      openPaneContent: openPaneContent,
    };
  }
  function openViewer(file, preferredSide) {
    return wbcOpenViewer(pageResourceContext(), file, preferredSide);
  }

  function openProjectFile(entry) {
    return wbcOpenProjectFile(pageResourceContext(), entry);
  }

  // The board deliberately reuses this page's live rail. Any rail action that
  // opens workspace content must reveal Work before opening that content, or
  // the result would be created behind the still-visible board canvas.
  function activateWorkspace() {
    if (onActivateWorkspace) onActivateWorkspace();
  }

  function resourceDropContext() {
    return {
      pageRef: pageRef, resourceSplitDropSide: resourceSplitDropSide,
      setResourceSplitDropSide: setResourceSplitDropSide, openTerminal: openTerminal,
      setSplitSideDirect: setSplitSideDirect, openViewer: openViewer,
    };
  }
  function resourceSplitDropGeometry() { return wbcResourceSplitDropGeometry(pageRef); }
  function resourceSplitSideAt(event) { return wbcResourceSplitSideAt(resourceDropContext(), event); }
  function handleResourceSplitDragOver(event) { return wbcHandleResourceSplitDragOver(resourceDropContext(), event); }
  function handleResourceSplitDrop(event) { return wbcHandleResourceSplitDrop(resourceDropContext(), event); }
  function revealTopbarResource(chatId, resource) {
    return wbcRevealTopbarResource(pageResourceContext(), chatId, resource);
  }

  function markViewerFileRead(file) {
    return wbcMarkViewerFileRead(pageResourceContext(), file);
  }

  function loadSubagents(chatId, roundId) {
    return wbcLoadSubagents(pageResourceContext(), chatId, roundId);
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
      if (!list.length) {
        return model.createChat(requestedProjectId).then(function (chat) {
          if (
            projectIdRef.current !== requestedProjectId
            || !isCurrentChatListRequest(requestedProjectId, requestSequence)
          ) return [chat];
          var initial = [chat];
          chatCache.lists[requestedProjectId] = initial;
          chatsProjectIdRef.current = requestedProjectId;
          setChats(initial);
          selectChat(chat.id);
          return initial;
        });
      }
      chatCache.lists[requestedProjectId] = list;
      chatsProjectIdRef.current = requestedProjectId;
      setChats(list);
      var targetId = wbcResolveRefreshedChatSelection(
        list,
        selectId,
        selectionAtRequest,
        activeChatIdRef.current
      );
      if (!selectId && projectPaneActiveRef.current) targetId = null;
      if (targetId !== null) selectChat(targetId);
      return list;
    }).finally(function () {
      if (
        projectIdRef.current === requestedProjectId
        && isCurrentChatListRequest(requestedProjectId, requestSequence)
      ) setLoading(false);
    });
  }

  function applyChatSummaryEvent(event) {
    var summary = event && event.chatSummary;
    if (!summary || typeof summary !== "object") return false;
    var chatId = String(summary.id || event.chat_id || event.session_id || "");
    var summaryProjectId = String(summary.projectId || event.project_id || "");
    if (!chatId || (summaryProjectId && summaryProjectId !== String(projectIdRef.current || ""))) {
      return false;
    }
    var runStatus = String(event.run_status || "");
    var projected = wbcMergeChatSummary({ id: chatId }, summary, runStatus);
    if (Object.prototype.hasOwnProperty.call(event, "activeGoal")) {
      projected.activeGoal = event.activeGoal || null;
    }
    var projectedMessages = [];
    if (event.userMessage && typeof event.userMessage === "object") {
      projectedMessages.push(event.userMessage);
    }
    if (Array.isArray(event.assistantMessages)) {
      projectedMessages = projectedMessages.concat(event.assistantMessages);
    }
    function mergeProjection(chat) {
      var merged = wbcMergeChatSummary(chat, projected, runStatus);
      if (!merged || !projectedMessages.length) return merged;
      return {
        ...merged,
        messages: wbcMergeChronologicalMessages(merged.messages || [], projectedMessages),
      };
    }
    var cached = chatCache.details[chatId];
    if (cached) chatCache.details[chatId] = mergeProjection(cached);
    setActiveChat(function (current) {
      if (!current || String(current.id || "") !== chatId) return current;
      return mergeProjection(current);
    });
    beginChatListRequest(String(projectIdRef.current || ""));
    setChats(function (current) {
      var found = false;
      var next = current.map(function (item) {
        if (String(item && item.id || "") !== chatId) return item;
        found = true;
        return wbcMergeChatSummary(item, projected, runStatus);
      });
      return found ? next : [projected].concat(next);
    });
    return true;
  }

  function applyGoalChanged(activeGoal) {
    var chatId = String(activeChatIdRef.current || "");
    if (!chatId) return;
    function patch(chat) {
      if (!chat || String(chat.id || "") !== chatId) return chat;
      var next = Object.assign({}, chat, { activeGoal: activeGoal || null });
      if (chatCache.details[chatId]) chatCache.details[chatId] = next;
      return next;
    }
    setActiveChat(patch);
    setChats(function (current) { return current.map(patch); });
    if (!activeGoal && sideTab === "goal") setSideTab("");
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
    var navigation = workbenchServices.navigation();
    var pending = navigation.getPending();
    var pendingChatId = pending && pending.type === "chat" ? (pending.chatId || pending.id) : "";
    if (pendingChatId && pending.topbarResource) {
      pendingTopbarResourceRef.current = { chatId: pendingChatId, resource: pending.topbarResource };
    }
    var persistedWorkspace = wbcReadPaneWorkspace(projectId);
    var remembered = wbcLastChatByProject[projectId]
      || (persistedWorkspace && persistedWorkspace.activeChatId || "");
    function selectFrom(list) {
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
      workbenchServices.navigation().clearPending();
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
    if (cachedChat) {
      // Cache adoption is another transcript hydration boundary. A retry keeps
      // its replaced output durable until the new run commits, so never paint
      // an older cached snapshot without reconciling the live runtime first.
      cachedChat = wbcPreserveLiveTimelineAnchors(
        cachedChat,
        cachedChat,
        runtimeEngine.get(activeChatId)
      );
      chatCache.details[activeChatId] = cachedChat;
    }
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
    var navigation = workbenchServices.navigation();
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

  // Live tool progress reuses the platform SSE feed and remains subscribed for
  // the page lifetime; the controller projects only chat-owned events.
  useWbcLiveEventController({
    projectIdRef: projectIdRef, activeChatIdRef: activeChatIdRef,
    remoteChangedChatIdsRef: remoteChangedChatIdsRef,
    remoteChatRefreshTimerRef: remoteChatRefreshTimerRef,
    subagentRefreshTimerRef: subagentRefreshTimerRef,
    applyChatSummaryEvent: applyChatSummaryEvent, refreshChats: refreshChats,
    loadSubagents: loadSubagents, setLoadRevision: setLoadRevision,
    setChats: setChats, setActiveChat: setActiveChat,
    setBrowserActiveByChat: setBrowserActiveByChat,
    setBrowserWindowModeByChat: setBrowserWindowModeByChat,
  });

  // 按对话查询 Electron 中对应的 BrowserTabManager。每个 manager 的 tabs
  // 和 persistent partition 都由 chatId 隔离，刷新 UI 不会把别的对话误认
  // 为当前对话的浏览器。
  var browserRestoredRef = useWbcRef({});
  useWbcEffect(function () {
    if (!browserAvailable) return undefined;
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
  }, [browserAvailable]);

  useWbcEffect(function () {
    if (!browserAvailable) return;
    var bridge = window.cyrene && window.cyrene.browser;
    if (!bridge || typeof bridge.getState !== "function") return;
    var cancelled = false;
    var chatId = activeChatId || "";
    if (!chatId) return;
    if (browserRestoredRef.current[chatId]) return;
    browserRestoredRef.current[chatId] = true;
    bridge.getState(chatId).then(function (state) {
      if (cancelled) return;
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
    return function () { cancelled = true; };
  }, [activeChatId, browserAvailable]);

  // Agent navigation creates its Electron tab before the page finishes
  // loading. Observe the manager-level state as well as browser_frame SSE
  // events so a slow or stalled navigation still mounts the visible browser
  // surface immediately. The viewport subscription is installed only after
  // this flag is set, so relying on browser:state here would create a cycle:
  // no mounted viewport -> no active session -> no browser:state delivery.
  useWbcEffect(function () {
    if (!browserAvailable) return undefined;
    var bridge = window.cyrene && window.cyrene.browser;
    if (!bridge || typeof bridge.onManagerState !== "function") return undefined;
    var chatId = String(activeChatId || "");
    if (!chatId) return undefined;
    function revealOwnedBrowser(state) {
      var pages = Array.isArray(state && state.pages) ? state.pages : [];
      if (!pages.some(function (page) { return String(page && page.sessionId || "") === chatId; })) return;
      setBrowserActiveByChat(function (prev) {
        return prev[chatId] ? prev : { ...prev, [chatId]: true };
      });
      setBrowserWindowModeByChat(function (prev) {
        return prev[chatId] ? prev : { ...prev, [chatId]: "pip" };
      });
    }
    if (typeof bridge.getManagerState === "function") {
      bridge.getManagerState().then(revealOwnedBrowser).catch(function () {});
    }
    return bridge.onManagerState(revealOwnedBrowser);
  }, [activeChatId, browserAvailable]);

  useWbcEffect(function () {
    if (!knowledgeAvailable) {
      knowledgeReadControllersRef.current.forEach(function (controller) { controller.abort(); });
      knowledgeReadControllersRef.current.clear();
    }
  }, [knowledgeAvailable]);

  useWbcEffect(function () {
    if (mapAvailable && browserAvailable) return;
    var blockedKinds = new Set([].concat(mapAvailable ? [] : ["map"], browserAvailable ? [] : ["browser"]));
    setResourceSplitByChat(function (current) {
      var next = {};
      var changed = false;
      Object.keys(current).forEach(function (key) {
        var value = current[key];
        if (value && blockedKinds.has(value.type)) changed = true;
        else next[key] = value;
      });
      return changed ? next : current;
    });
    setPaneLayoutsByChat(function (current) {
      var next = Object.assign({}, current);
      var changed = false;
      Object.keys(current).forEach(function (key) {
        var layout = current[key] || {};
        var left = (layout.left || []).filter(function (card) { return !blockedKinds.has(card && card.kind); });
        var right = (layout.right || []).filter(function (card) { return !blockedKinds.has(card && card.kind); });
        if (left.length === (layout.left || []).length && right.length === (layout.right || []).length) return;
        if (!left.length && right.length) { left = right; right = []; }
        next[key] = left.length || right.length
          ? Object.assign({}, layout, { left: left, right: right })
          : wbcDefaultPaneLayout(String(key).indexOf("project:") === 0 ? "" : key);
        changed = true;
      });
      return changed ? next : current;
    });
    if ((!mapAvailable && sideTab === "map") || (!browserAvailable && sideTab === "browser")) setSideTab("");
    if (!browserAvailable) {
      browserRestoredRef.current = {};
      setBrowserActiveByChat({});
      setBrowserWindowModeByChat({});
    }
  }, [mapAvailable, browserAvailable]);

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

  useWbcRuntimePageHooks({
    runtimeEngine: runtimeEngine, model: model, chatCache: chatCache,
    activeChatIdRef: activeChatIdRef, projectIdRef: projectIdRef,
    setActiveChat: setActiveChat, setChats: setChats,
    setError: setError, setErrorKind: setErrorKind,
    beginChatListRequest: beginChatListRequest,
    beginChatHydration: beginChatHydration,
    isCurrentChatHydration: isCurrentChatHydration,
    refreshChats: refreshChats,
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
    if (!quote || !parentChatId || sideAgentCreating) {
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

  function splitSelectionContext() {
    return {
      model: model, activeChatIdRef: activeChatIdRef, splitChatId: splitChatId,
      sideAgentSplitByChat: sideAgentSplitByChat, artifactSplitByChat: artifactSplitByChat,
      changeSplitByChat: changeSplitByChat, resourceSplitByChat: resourceSplitByChat,
      setSideAgents: setSideAgents, setActiveSideAgentByChat: setActiveSideAgentByChat,
      setSideAgentSplitByChat: setSideAgentSplitByChat, setArtifactSplitByChat: setArtifactSplitByChat,
      setChangeSplitByChat: setChangeSplitByChat, setResourceSplitByChat: setResourceSplitByChat,
      setSideTab: setSideTab, setViewerFile: setViewerFile,
      setFloatingConversationPanelOpen: setFloatingConversationPanelOpen,
      setError: setError, setErrorKind: setErrorKind, openPaneContent: openPaneContent,
      restoreFloatingPanelSplit: restoreFloatingPanelSplit, selectChat: selectChat,
      closeActiveSplit: closeActiveSplit,
    };
  }
  function updateSideAgent(value) { return wbcUpdateSideAgent(splitSelectionContext(), value); }
  function deleteSideAgent(id) { return wbcDeleteSideAgent(splitSelectionContext(), id); }
  function selectSideAgent(id) { return wbcSelectSideAgent(splitSelectionContext(), id); }
  function selectArtifact(file) { return wbcSelectArtifact(splitSelectionContext(), file); }
  function selectChange(change) { return wbcSelectChange(splitSelectionContext(), change); }
  function selectResourceSplit(type, payload, skipPane) { return wbcSelectResourceSplit(splitSelectionContext(), type, payload, skipPane); }
  function splitStateSnapshot(chatId) { return wbcSplitStateSnapshot(splitSelectionContext(), chatId); }
  function restoreSplitState(chatId, snapshot) { return wbcRestoreSplitState(splitSelectionContext(), chatId, snapshot); }
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

  function closeSideAgentSplit() { return wbcCloseNamedSplit(splitSelectionContext(), setSideAgentSplitByChat); }
  function closeArtifactSplit() { return wbcCloseNamedSplit(splitSelectionContext(), setArtifactSplitByChat); }
  function closeChangeSplit() { return wbcCloseNamedSplit(splitSelectionContext(), setChangeSplitByChat); }
  function closeResourceSplit() { return wbcCloseResourceSplit(splitSelectionContext()); }
  function closeMainConversationSplit() { return wbcCloseMainConversationSplit(splitSelectionContext()); }
  function closeActiveSplit() { return wbcCloseActiveSplit(splitSelectionContext()); }
  // A rail chat dragged onto the right side (or the open split) is opened
  // beside the main conversation instead of replacing it. A dedicated drop
  // layer covers the right zone while a chat drag is in progress; the main
  // conversation column keeps its original drop-to-open behaviour untouched.
  var pageRef = useWbcRef(null);
  var [chatDragKind, setChatDragKind] = useWbcState("");
  var chatDragSession = !!chatDragKind;
  var [chatSideDropActive, setChatSideDropActive] = useWbcState(false);

  useWbcEffect(function () {
    function onDocumentDragStart(event) {
      // Bubbles after the rail's React onDragStart has populated the
      // dataTransfer, so the chat MIME is already visible here.
      if (wbcHasChatDrag(event)) setChatDragKind("chat");
      else if (wbcHasPluginViewDrag(event)) setChatDragKind("plugin-view");
      if (wbcHasResourceDrag(event)) setResourceDragSession(true);
    }
    // Derive the preview from the pointer's page-level position. The right
    // target and main grid both move when the preview reserves its split
    // track; relying on the target element's dragleave would interpret that
    // layout motion as the pointer leaving and expand the composer again.
    function onDocumentChatDragOver(event) {
      if (!wbcHasChatDrag(event) && !wbcHasPluginViewDrag(event)) return;
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

  function paneCardDragContext() {
    return {
      project: project, chatCache: chatCache, activeChat: activeChat,
      artifactItems: artifactItems, terminals: terminals, sideAgents: sideAgents,
      activeChatIdRef: activeChatIdRef, paneCardDetachRef: paneCardDetachRef,
      paneCardDragImageCleanupRef: paneCardDragImageCleanupRef,
      paneLayoutFor: paneLayoutFor, setPaneCardDragId: setPaneCardDragId,
      setPaneDropTarget: setPaneDropTarget, closePaneCard: closePaneCard,
      placeExistingPaneCard: placeExistingPaneCard,
      clearPaneCardDetachSubscription: clearPaneCardDetachSubscription,
      cancelPaneCardDetachment: cancelPaneCardDetachment,
      completePaneCardDetachment: completePaneCardDetachment,
      paneCardDetachIpcPayload: paneCardDetachIpcPayload,
      handlePaneCardDragEnd: handlePaneCardDragEnd,
    };
  }

  function handlePaneCardPointerDown(event, cardId, paneOverride) {
    if (event && event.button != null && event.button !== 0) return;
    handlePaneCardDragStart(event, cardId, paneOverride);
  }

  function handlePaneCardDragStart(event, cardId, paneOverride) {
    var pane = paneOverride;
    if (pane && pane.kind === "surface") {
      pane = wbcClaimSurfaceCard(pane);
      updatePaneCard(cardId, pane);
    } else if (pane && pane.meta && pane.meta.origin === "agent"
      && pane.meta.claimedByUser !== true && event && event.isTrusted === true) {
      // A real grip gesture is the explicit user claim boundary for an
      // agent-created card. Programmatic/semantic opens retain origin=agent
      // and cannot create a sensitive-resource layout grant by themselves.
      pane = Object.assign({}, pane, {
        meta: Object.assign({}, pane.meta, { claimedByUser: true }),
      });
      updatePaneCard(cardId, pane);
    }
    return wbcStartPaneCardDrag(paneCardDragContext(), event, cardId, pane);
  }

  function handlePaneCardDragEnd(_event, options) {
    return wbcFinishPaneCardDrag(paneCardDragContext(), options);
  }
  function placeExistingPaneCard(sourceCardId, targetCardId, edge) {
    return wbcPlaceExistingPaneCard(paneDropContext(), sourceCardId, targetCardId, edge);
  }
  function handlePaneDropOver(event, cardId, edge, dropKey) {
    if (!wbcHasSplitDrag(event) && !wbcHasChatDrag(event)
      && !wbcHasPluginViewDrag(event) && !wbcHasResourceDrag(event)) return;
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

  function paneDropContext() {
    return {
      activeChatIdRef: activeChatIdRef, paneCardDragImageCleanupRef: paneCardDragImageCleanupRef,
      paneDropTarget: paneDropTarget,
      terminalClient: terminalClient, projectId: projectId,
      paneLayoutFor: paneLayoutFor, projectPaneOwnerKey: projectPaneOwnerKey,
      paneContentCard: paneContentCard, updatePaneLayout: updatePaneLayout,
      setActiveTerminalId: setActiveTerminalId, setPaneCardDragId: setPaneCardDragId,
      setResourceDragSession: setResourceDragSession, setChatDragKind: setChatDragKind,
      setPaneDropTarget: setPaneDropTarget, selectChat: selectChat,
      activateProjectPaneWorkspace: activateProjectPaneWorkspace,
    };
  }
  function handlePaneDrop(event, targetCardId, edge) {
    return wbcHandlePaneDrop(paneDropContext(), event, targetCardId, edge);
  }
  function handleSideLayerDragOver(event) {
    if (!wbcHasChatDrag(event) && !wbcHasPluginViewDrag(event)) return;
    event.preventDefault();
    event.stopPropagation();
    if (event.dataTransfer) event.dataTransfer.dropEffect = "move";
    setChatSideDropActive(true);
  }

  function handleSideLayerDrop(event) {
    if (!wbcHasChatDrag(event) && !wbcHasPluginViewDrag(event)) return;
    event.preventDefault();
    event.stopPropagation();
    setChatSideDropActive(false);
    setChatDragKind("");
    if (wbcHasPluginViewDrag(event)) {
      var pluginPayload = wbcReadPluginViewDrag(event);
      if (pluginPayload) {
        var projectOwned = String(pluginPayload.paneOwnerScope || "chat") === "project";
        var projectOwnerId = projectOwned ? projectPaneOwnerKey() : "";
        if (projectOwned && activeChatIdRef.current) {
          updatePaneLayout(paneLayoutFor(activeChatIdRef.current), projectOwnerId);
        }
        if (projectOwned) activateProjectPaneWorkspace();
        var openedCard = openPaneContent(
          "plugin-view",
          Object.assign({ projectId: projectId }, pluginPayload),
          { side: "right", ownerChatId: projectOwnerId || undefined }
        );
        if (projectOwned && openedCard) {
          setSelectedWorkspaceTabKey("plugin-view:" + String(openedCard.id || ""));
        }
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

  function chatActionContext() {
    return {
      model: model, runtimeEngine: runtimeEngine, projectId: projectId,
      activeChat: activeChat, activeChatIdRef: activeChatIdRef, chatsRef: chatsRef,
      chatCache: chatCache, projectIdRef: projectIdRef,
      retryPendingChatIdRef: retryPendingChatIdRef,
      retryClearCommitRef: retryClearCommitRef, skipNextHydrationChatIdRef: skipNextHydrationChatIdRef,
      setActiveChat: setActiveChat, setChats: setChats,
      setError: setError, setErrorKind: setErrorKind,
      setRetryClearingMessageIds: setRetryClearingMessageIds,
      beginChatListRequest: beginChatListRequest, beginChatHydration: beginChatHydration,
      isCurrentChatHydration: isCurrentChatHydration, refreshChats: refreshChats,
      selectChat: selectChat, closePageContextMenu: closePageContextMenu,
      setQuickRenameChat: setQuickRenameChat,
    };
  }
  function handleGuidance(message) { return wbcHandleGuidance(chatActionContext(), message); }
  function answerQuestionForChat(chatId, questionId, optionText, resumeMode) {
    return wbcAnswerQuestionForChat(chatActionContext(), chatId, questionId, optionText, resumeMode);
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
  function handleRetryMessage(messageId) { return wbcHandleRetryMessage(chatActionContext(), messageId); }
  function handleRetryClearAnimationEnd() {
    var commit = retryClearCommitRef.current;
    if (!commit) return;
    retryClearCommitRef.current = null;
    commit();
  }

  // Edit a user message → fork the conversation at that point, switch to the
  // forked chat, and replay the edited turn through the streaming engine. The
  // original conversation is preserved untouched.
  function handleEditMessage(messageId, content) { return wbcHandleEditMessage(chatActionContext(), messageId, content); }
  function handleCreateChat() { return wbcHandleCreateChat(chatActionContext()); }
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

  function handleRename(title) { return wbcHandleRename(chatActionContext(), title); }
  function handleRenameChat(chatId, title) {
    return wbcHandleRenameChat(chatActionContext(), chatId, title);
  }
  function openQuickRename() { return wbcOpenQuickRename(chatActionContext()); }

  function pageContextMenuContext() {
    return {
      activeChat: activeChat, activeChatIdRef: activeChatIdRef,
      pageContextMenuRef: pageContextMenuRef, pendingPageContextMenuRef: pendingPageContextMenuRef,
      pageContextPreviewTimerRef: pageContextPreviewTimerRef, setPageContextMenu: setPageContextMenu,
    };
  }
  function setOpenPageContextMenu(menu) { return wbcSetOpenPageContextMenu(pageContextMenuContext(), menu); }
  function clearPendingPageContextMenu() { return wbcClearPendingPageContextMenu(pageContextMenuContext()); }
  function closePageContextMenu() { return wbcClosePageContextMenu(pageContextMenuContext()); }
  function openPageContextMenu(event) { return wbcOpenPageContextMenu(pageContextMenuContext(), event); }
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
    workbenchServices.feedback().confirmModal({
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

  function handleCompact() {
    if (!activeChat || compactBusy) return;
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
        workbenchServices.feedback().showToast(wbcT(
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
        workbenchServices.feedback().showToast(wbcT("workbenchChat.compactEmpty", "There is no agent context to compress."), "warning");
      } else if (payload.reason === "running") {
        workbenchServices.feedback().showToast(wbcT("workbenchChat.compactRunning", "The agent is currently working. Try again after it finishes."), "warning");
      } else if (payload.reason === "awaiting_user") {
        workbenchServices.feedback().showToast(wbcT("workbenchChat.compactAwaitingUser", "Answer the agent's question before compressing this chat."), "warning");
      } else if (payload.reason === "no_tool_activity") {
        workbenchServices.feedback().showToast(wbcT("workbenchChat.compactNoTools", "This chat has no tool activity to compress."), "warning");
      } else if (payload.reason === "distilling") {
        workbenchServices.feedback().showToast(wbcT("workbenchChat.compactDistilling", "Background context compression is still running. Try again shortly."), "warning");
      } else {
        workbenchServices.feedback().showToast(wbcT("workbenchChat.compactNoChange", "No earlier context is available to compress."), "warning");
      }
    }).catch(function (err) {
      setError(wbcErrorText(err));
    }).then(function () {
      setCompactBusy(false);
    });
  }

  function handleGenerateMemory() {
    if (!memoryAvailable || !activeChat || memoryLearningBusy) return;
    setMemoryLearningBusy(true);
    setErrorKind("memory");
    setError("");
    var memoryLanguage = workbenchServices.i18n().getLang();
    model.generateMemory(activeChat.id, memoryLanguage).then(function (payload) {
      setErrorKind("load");
      var duplicate = payload && payload.status === "deduplicated";
      workbenchServices.feedback().showToast(
        duplicate
          ? wbcT("workbenchChat.memoryLearningDeduplicated", "This conversation context has already been submitted for learning.")
          : wbcT("workbenchChat.memoryLearningQueued", "Project memory learning started."),
        duplicate ? "warning" : "success"
      );
    }).catch(function (err) {
      setErrorKind("memory");
      setError(wbcErrorText(err));
      workbenchServices.feedback().showToast(wbcErrorText(err), "error");
    }).then(function () {
      setMemoryLearningBusy(false);
    });
  }

  function onToggleSide() { setSideVisible(function (v) { return !v; }); }

  useWbcEffect(function () {
    function onBrowserPreviewReady(event) {
      var pending = pendingPageContextMenuRef.current;
      if (!pending) return;
      var detail = event && event.detail || {};
      if (String(detail.sessionId || "") !== String(pending.browserSessionId || "")) return;
      clearPendingPageContextMenu();
      if (detail.fallback) {
        wbcNotifyBrowserWindowInteraction(false, "context-menu", pending.browserSessionId);
        workbenchServices.feedback().showToast(
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
  // Effects run after paint, so also guard the render itself against a stale
  // activeChat during the ID -> transcript fetch gap.
  var visibleChat = activeChat && String(activeChat.id || "") === String(activeChatId || "")
    ? activeChat
    : null;
  var selectedChatSummary = chats.find(function (item) {
    return String(item.id || "") === String(activeChatId || "");
  }) || null;
  var activeWorkspaceDescriptor = wbcWorkspaceSurfaceDescriptor(
    workspaceSurfaceDescriptors[paneOwnerKey(activeChatId)]
      || ((visibleChat || selectedChatSummary) && (visibleChat || selectedChatSummary).workspaceSurface),
    surfaceCatalog,
    activeChatId
  );
  var activeBrowserState = wbcBrowserStateForChat(activeChatId);
  var browserMarkedActive = !!(browserActiveByChat && browserActiveByChat[activeChatId]);
  var hasActiveBrowser = browserAvailable && !!((activeBrowserState && activeBrowserState.active) || browserMarkedActive);
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
  var splitMap = mapAvailable && splitResource && splitResource.type === "map" ? splitResource.payload : null;
  var splitBrowserTabId = browserAvailable && splitResource && splitResource.type === "browser" ? String(splitResource.payload || "") : "";
  var splitSubagents = !!(splitResource && splitResource.type === "subagents");
  // Dragging a rail chat onto the right panel opens that conversation here.
  var splitChatId = splitResource && splitResource.type === "chat" ? String(splitResource.payload || "") : "";
  var paneLayout = paneLayoutFor(activeChatId);
  var paneLayoutProjectionKey = JSON.stringify(paneLayout);
  var workspaceTabsProjectionKey = JSON.stringify(paneLayoutsByChat);
  var terminalTabsProjectionKey = terminals.map(function (terminal) {
    return String(terminal && terminal.id || "") + ":" + String(terminal && (terminal.displayTitle || terminal.title) || "");
  }).join("|");
  useWbcEffect(function () {
    if (
      !projectId
      || loading
      || !terminalsHydrated
      || paneWorkspaceValidatedProjectRef.current === projectId
    ) return;
    paneWorkspaceValidatedProjectRef.current = projectId;
    var stored = wbcReadPaneWorkspace(projectId);
    if (!stored) return;
    paneWorkspaceSkipPersistRef.current = true;
    var restored = wbcValidatePaneWorkspace(stored, chats, terminals);
    if (!restored) {
      wbcClearPaneWorkspace(projectId);
      var staleOwnerId = stored.activeChatId || "project:" + String(projectId);
      setPaneLayoutsByChat(function (current) {
        if (!Object.prototype.hasOwnProperty.call(current, staleOwnerId)) return current;
        var next = Object.assign({}, current);
        delete next[staleOwnerId];
        return next;
      });
      return;
    }
    var ownerId = restored.activeChatId || "project:" + String(projectId);
    setPaneLayoutsByChat(function (current) {
      return Object.assign({}, current, { [ownerId]: restored.layout });
    });
  }, [projectId, loading, terminalsHydrated, chats, terminals]);
  useWbcEffect(function () {
    if (!projectId || loading || !terminalsHydrated) return;
    if (paneWorkspaceSkipPersistRef.current) {
      paneWorkspaceSkipPersistRef.current = false;
      return;
    }
    wbcWritePaneWorkspace(projectId, activeChatId, paneLayout);
  }, [projectId, activeChatId, paneLayoutProjectionKey, loading, terminalsHydrated]);
  useWbcEffect(function () {
    if (!onWorkspaceTabsChange) return;
    onWorkspaceTabsChange(wbWorkspaceTabsFromPaneLayouts({
      activeOwner: paneOwnerKey(activeChatId),
      activeKey: selectedWorkspaceTabKey,
      currentLayout: paneLayout,
      layouts: paneLayoutsByChat,
      terminals: terminals,
      project: project,
    }));
  }, [projectId, activeChatId, selectedWorkspaceTabKey, paneLayoutProjectionKey, workspaceTabsProjectionKey, terminalTabsProjectionKey, onWorkspaceTabsChange]);
  useWbcEffect(function () {
    var projectionTools = (PluginFrontendService.snapshot().projectTools || []).filter(function (tool) {
      return String(tool && tool.layout_projection_method || "");
    });
    if (!projectionTools.length) return undefined;
    var layoutId = "pane:" + String(projectId || "project") + ":" + String(activeChatId || "workspace");
    var cards = paneLayout.left.concat(paneLayout.right).map(function (card) {
      var payload = card && card.payload;
      var state = payload && typeof payload === "object" && payload.state && typeof payload.state === "object"
        ? payload.state : {};
      return {
        card_id: String(card && card.id || ""),
        kind: card && card.kind === "plugin-view" ? "plugin-view"
          : card && card.kind === "chat" ? "chat"
          : card && card.kind === "terminal" ? "terminal"
          : card && (card.kind === "file" || card.kind === "viewer") ? "file" : "other",
        chat_id: card && card.kind === "chat" ? String(card.payload || "") : "",
        pack_id: card && card.kind === "plugin-view" ? String(payload && (payload.packId || payload.pack_id) || "") : "",
        view_id: card && card.kind === "plugin-view" ? String(payload && (payload.viewId || payload.view_id) || "") : "",
        instance_id: card && card.kind === "plugin-view" ? String(payload && (payload.instanceId || payload.instance_id) || "") : "",
        device_id: card && card.kind === "plugin-view" ? String(state.device_id || payload && (payload.instanceId || payload.instance_id) || "") : "",
        session_id: card && card.kind === "plugin-view" ? String(state.session_id || "") : "",
        meta: Object.assign({}, card && card.meta || {}),
      };
    });
    var timer = window.setTimeout(function () {
      projectionTools.forEach(function (tool) {
        var key = String(tool.pack_id || "") + ":" + String(tool.layout_projection_method || "") + ":" + layoutId;
        var revision = Math.max(Date.now(), Number(layoutProjectionRevisionRef.current[key] || 0) + 1);
        layoutProjectionRevisionRef.current[key] = revision;
        PluginFrontendService.call(
          String(tool.pack_id || ""),
          String(tool.layout_projection_method || ""),
          {
            pane_layout_id: layoutId,
            projection_scope_id: "pane-project:" + String(projectId || "project"),
            revision: revision,
            origin: "user_pointer",
            cards: cards,
          },
          projectId
        ).catch(function () {});
      });
    }, 120);
    return function () { window.clearTimeout(timer); };
  }, [projectId, activeChatId, paneLayoutProjectionKey]);
  var panePresentation = wbcPaneWorkspacePresentation(
    paneLayout, paneCardDragId, chatDragSession, resourceDragSession,
    activeChatId
  );
  var paneCardCount = panePresentation.paneCardCount;
  var paneHasTwoColumns = panePresentation.paneHasTwoColumns;
  var paneAxisDropAvailable = panePresentation.paneAxisDropAvailable;
  var paneOnlyCard = panePresentation.paneOnlyCard;
  var singlePaneDropUsesContextTracks = panePresentation.singlePaneDropUsesContextTracks;
  var paneDropSessionActive = panePresentation.paneDropSessionActive;
  var singlePaneContextDropActive = panePresentation.singlePaneContextDropActive;
  var splitDetailOpen = panePresentation.splitDetailOpen;
  var projectPaneOnly = panePresentation.projectPaneOnly;
  var showNewConversationWorkspace = panePresentation.showNewConversationWorkspace;
  var singleColumnWorkspaceOpen = panePresentation.singleColumnWorkspaceOpen;

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

  function openStartedWorkspace() {
    var ownerChatId = String(activeChatId || "");
    return wbcOpenStartedWorkspace({
      descriptor: activeWorkspaceDescriptor,
      ownerChatId: ownerChatId,
      ownerId: paneOwnerKey(ownerChatId),
      projectId: projectId,
      setPaneLayoutsByChat: setPaneLayoutsByChat,
      setSideTab: setSideTab,
    });
  }

  function renderConversationPanel(floating) {
    function openPanelContent(type, payload) {
      if ((type === "map" && !mapAvailable) || (type === "browser" && !browserAvailable)) return;
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
        workspaceAvailable={!!activeWorkspaceDescriptor}
        onOpenWorkspace={function () {
          if (openStartedWorkspace() && floating) setFloatingConversationPanelOpen(false);
        }}
        onViewerViewed={markViewerFileRead}
        onRename={openQuickRename}
        onDelete={handleDelete}
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
          return wbcCompleteBrowserTakeover(activeChat, payload, handleAnswer);
        }}
        browserActiveByChat={browserActiveByChat}
        browserSuppressed={browserWindowMode === "maximized"}
        mapAvailable={mapAvailable}
        browserAvailable={browserAvailable}
        onGoalChanged={applyGoalChanged}
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
        runtimeEngine={runtimeEngine}
        error={error}
        errorKind={errorKind}
        onRetry={errorKind === "message" ? handleRetryMessage : (errorKind === "memory" ? handleGenerateMemory : retryLoad)}
        onSend={handleSend}
        onGuidance={handleGuidance}
        onInterrupt={handleInterrupt}
        onAnswer={handleAnswer}
        onRetryMessage={handleRetryMessage}
        onRetryClearAnimationEnd={handleRetryClearAnimationEnd}
        retryClearingMessageIds={retryClearingMessageIds}
        onEditMessage={handleEditMessage}
        onAskSelection={handleAskSelection}
        sideAgentCreating={sideAgentCreating}
        onConversationContextMenu={openPageContextMenu}
        onRename={handleRename}
        onDelete={handleDelete}
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
        onDraftAgentChange={agentsAvailable ? handleDraftAgentChange : null}
        onSwitchAgent={agentsAvailable ? handleSwitchAgent : null}
        onOpenAgentDetail={agentsAvailable ? handleOpenAgentDetail : null}
        horizontalSessionWheelGesture={horizontalSessionWheelRef.current}
        onBrowserTakeoverComplete={function (payload) {
          return wbcCompleteBrowserTakeover(activeChat, payload, handleAnswer);
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

  function closePaneCardWithConfirmation(card, ownerChatId) {
    var surfaceResource = card.kind === "surface" && card.payload && card.payload.resource || null;
    var draftKey = card.kind === "file" || card.kind === "viewer"
      ? wbcProjectFileDraftKey(card.payload)
      : surfaceResource && surfaceResource.kind === "file"
      ? wbcProjectFileDraftKey({
          source: "project",
          projectId: surfaceResource.projectId || surfaceResource.project_id || projectId,
          path: surfaceResource.path,
        })
      : "";
    function suppressAutomaticSurface() {
      if (card.kind !== "surface" || !card.meta || card.meta.origin !== "agent") return;
      var payload = card.payload || {};
      surfaceSuppressionRef.current.set(
        String(payload.runId || "") + "\n" + String(payload.resourceKey || ""),
        true
      );
    }
    if (!draftKey || !WBC_PROJECT_FILE_DRAFTS[draftKey]) {
      suppressAutomaticSurface();
      var pluginState = card.kind === "plugin-view" && card.payload && card.payload.state || {};
      var closeMethod = String(pluginState && pluginState.closeMethod || "");
      var closeSessionId = String(pluginState && pluginState.session_id || "");
      if (closeMethod && closeSessionId) {
        return PluginFrontendService.call(
          String(card.payload && card.payload.packId || ""),
          closeMethod,
          { session_id: closeSessionId },
          projectId
        ).catch(function () {}).then(function () {
          closePaneCard(card.id, ownerChatId);
          return { closed: true, card_id: String(card.id || "") };
        });
      }
      closePaneCard(card.id, ownerChatId);
      return Promise.resolve({ closed: true, card_id: String(card.id || "") });
    }
    var feedback = workbenchServices.feedback();
    var request = feedback.confirmModal ? feedback.confirmModal({
      title: wbcT("workbenchChat.editorUnsavedTitle", "Unsaved changes"),
      body: wbcT("workbenchChat.editorUnsavedBody", "Discard the changes made to this file?"),
      confirmLabel: wbcT("workbenchChat.editorDiscard", "Discard changes"),
      danger: true,
    }) : Promise.resolve(window.confirm(wbcT("workbenchChat.editorUnsavedBody", "Discard the changes made to this file?")));
    return Promise.resolve(request).then(function (confirmed) {
      if (!confirmed) return { closed: false, cancelled: true, card_id: String(card.id || "") };
      if (surfaceResource) delete WBC_PROJECT_FILE_DRAFTS[draftKey];
      else wbcDiscardProjectFileDraft(card.payload);
      suppressAutomaticSurface();
      closePaneCard(card.id, ownerChatId);
      return { closed: true, discarded_draft: true, card_id: String(card.id || "") };
    });
  }

  useWbcEffect(function () {
    var request = workspaceTabRequest;
    var item = request && request.item;
    if (!request || !item || String(item.projectId || "") !== String(projectId || "")) return;
    var locations = Array.isArray(item.locations) ? item.locations : [];
    var location = locations.find(function (entry) {
      return String(entry && entry.ownerChatId || "") === String(activeChatId || "");
    }) || locations[0];
    function complete() {
      if (onWorkspaceTabRequestHandled) onWorkspaceTabRequestHandled(request.id);
    }
    if (request.action === "open" && item.kind === "chat") {
      var targetChatId = String(item.id || "");
      if (!targetChatId) {
        complete();
        return;
      }
      if (targetChatId === String(activeChatId || "")) {
        complete();
        return;
      }
      var chatAvailable = chatsProjectIdRef.current === String(projectId || "")
        && chats.some(function (chat) { return String(chat && chat.id || "") === targetChatId; });
      if (chatAvailable) {
        selectChat(targetChatId);
        return;
      }
      if (!loading && chatsProjectIdRef.current === String(projectId || "")
        && workspaceTabRefreshRequestRef.current !== request.id) {
        workspaceTabRefreshRequestRef.current = request.id;
        refreshChats(targetChatId).then(function (list) {
          if (!list.some(function (chat) { return String(chat && chat.id || "") === targetChatId; })) complete();
        }).catch(complete);
      }
      return;
    }
    if (!location) {
      complete();
      return;
    }
    if (request.action === "open") {
      var ownerChatId = String(location.ownerChatId || "");
      if (!ownerChatId) {
        if (!projectPaneActiveRef.current) activateProjectPaneWorkspace();
        setSelectedWorkspaceTabKey(String(item.kind || "") + ":" + String(item.id || ""));
        complete();
        return;
      }
      if (ownerChatId !== String(activeChatId || "")) {
        selectChat(ownerChatId);
        return;
      }
      setSelectedWorkspaceTabKey(String(item.kind || "") + ":" + String(item.id || ""));
      complete();
      return;
    }
    if (request.action === "close") {
      var ownerKey = paneOwnerKey(location.ownerChatId);
      var ownerLayout = paneLayoutsByChat[ownerKey] || (ownerKey === paneOwnerKey(activeChatId) ? paneLayout : null);
      var card = ownerLayout && ownerLayout.left.concat(ownerLayout.right).find(function (candidate) {
        return String(candidate && candidate.id || "") === String(location.paneCardId || "");
      });
      if (!card) {
        complete();
        return;
      }
      Promise.resolve(closePaneCardWithConfirmation(card, location.ownerChatId)).then(complete, complete);
      return;
    }
    complete();
  }, [workspaceTabRequest && workspaceTabRequest.id, projectId, activeChatId, chats, loading, workspaceTabsProjectionKey]);

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
    );
    var singlePane = isNewConversation || paneCardCount === 1;
    var content = null;
    var grip = null;
    var close = function () { return closePaneCardWithConfirmation(card); };
    var move = function () { movePaneCardOtherSide(card.id); };
    var dragStart = function (event) { handlePaneCardDragStart(event, card.id, card); };
    var pointerDown = function (event) { handlePaneCardPointerDown(event, card.id, card); };
    var cardPayloadState = card && card.payload && typeof card.payload === "object" && card.payload.state && typeof card.payload.state === "object"
      ? card.payload.state : {};
    var observationState = resourceObservationStates["pane:" + String(card.id || "")]
      || resourceObservationStates[
        "resource:" + String(cardPayloadState.resource_kind || "") + ":" + String(cardPayloadState.resource_id || cardPayloadState.session_id || "")
      ] || null;
    if (isActiveConversation && singlePane) {
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
        runtimeEngine={runtimeEngine}
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
    } else if (card.kind === "surface") {
      grip = <WbcSplitGripBar
        dragSource={card.id}
        menuDisabled={singlePane}
        menuType="content"
        pinned={!!(card.meta && card.meta.pinned)}
        onTogglePin={function (pinned) {
          updatePaneCard(card.id, function (current) { return wbcPinSurfaceCard(current, pinned); });
        }}
        onToggleSide={move}
        onClose={close}
        onNewConversation={columnLength === 1 ? function () { createPaneConversation(card.id); } : null}
        onSplitPointerDown={pointerDown}
        onSplitDragStart={dragStart}
        onSplitDragEnd={handlePaneCardDragEnd}
      />;
      content = <WbcSurfaceHost
        descriptor={card.payload}
        projectId={projectId} chatId={String(card.ownerChatId || activeChatId || "")}
        items={artifactItems}
        onSelect={function (resource) {
          var selectedResource = Object.assign({}, resource || {}, {
            kind: resource && resource.kind || card.payload && card.payload.resource && card.payload.resource.kind || "file",
          });
          updatePaneCard(card.id, function (current) {
            return Object.assign({}, current, {
              payload: Object.assign({}, current.payload || {}, {
                resource: selectedResource,
                resourceKey: wbcSurfaceResourceKey(selectedResource),
              }),
              meta: Object.assign({}, current.meta || {}, { claimedByUser: true }),
            });
          });
        }}
        onClose={close}
        onViewed={markViewerFileRead}
      />;
    } else if (card.kind === "plugin-view") {
      var pluginPaneState = card.payload && card.payload.state && typeof card.payload.state === "object"
        ? card.payload.state : {};
      var pluginPaneMenu = (Array.isArray(pluginPaneState.paneMenu) ? pluginPaneState.paneMenu : []).filter(Boolean).map(function (contribution) {
        var requiredState = String(contribution && contribution.requires_state || "");
        var sessionKey = String(contribution.session_key || "session_id");
        var disabled = Boolean(
          (requiredState && !pluginPaneState[requiredState])
          || (contribution.requires_session === true && !pluginPaneState[sessionKey])
        );
        var availableKey = String(contribution && contribution.available_values_state_key || "");
        var availableValues = availableKey && Array.isArray(pluginPaneState[availableKey])
          ? pluginPaneState[availableKey].map(String) : null;
        return Object.assign({}, contribution, {
          disabled: disabled,
          label: pluginLocalizedField(contribution, "label") || String(contribution && contribution.id || "Settings"),
          fields: (Array.isArray(contribution && contribution.fields) ? contribution.fields : []).map(function (field) {
            var rawValue = pluginPaneState[String(field && field.state_key || "")];
            var valueLabel = (Array.isArray(field && field.value_labels) ? field.value_labels : []).find(function (candidate) {
              return String(candidate && candidate.value || "") === String(rawValue == null ? "" : rawValue);
            });
            var valueSuffix = pluginLocalizedField(field, "suffix");
            return Object.assign({}, field, {
              label: pluginLocalizedField(field, "label") || String(field && field.state_key || ""),
              tone: String(valueLabel && valueLabel.tone || ""),
              value: valueLabel
                ? pluginLocalizedField(valueLabel, "label")
                : String(rawValue == null || rawValue === ""
                  ? pluginLocalizedField(field, "empty_label") || "—"
                  : rawValue) + (rawValue == null || rawValue === "" ? "" : valueSuffix),
            });
          }),
          options: (Array.isArray(contribution && contribution.options) ? contribution.options : []).filter(function (option) {
            return !availableValues || availableValues.indexOf(String(option && option.value || "")) >= 0;
          }).map(function (option) {
            return Object.assign({}, option, { label: pluginLocalizedField(option, "label") || String(option && option.value || "") });
          }),
        });
      });
      function updatePluginPaneState(nextState) {
        if (!nextState || typeof nextState !== "object") return;
        updatePaneCard(card.id, function (current) {
          return Object.assign({}, current, {
            payload: Object.assign({}, current.payload || {}, {
              state: Object.assign({}, current.payload && current.payload.state || {}, nextState),
            }),
          });
        });
      }
      function invokePluginPaneMenu(contribution, value) {
        var frontendAction = String(contribution && contribution.frontend_action || "");
        if (frontendAction) {
          updatePluginPaneState({
            pluginViewCommand: {
              id: String(Date.now()) + "-" + Math.random().toString(36).slice(2),
              action: frontendAction,
              value: value,
            },
          });
          return Promise.resolve({ ok: true });
        }
        var currentState = card.payload && card.payload.state || {};
        var sessionKey = String(contribution && contribution.session_key || "session_id");
        var argumentKey = String(contribution && contribution.argument_key || contribution && contribution.state_key || "value");
        var args = { [argumentKey]: value };
        if (currentState[sessionKey]) args[sessionKey] = currentState[sessionKey];
        var contextArguments = contribution && contribution.context_arguments;
        if (contextArguments && typeof contextArguments === "object") {
          Object.keys(contextArguments).forEach(function (targetKey) {
            var stateKey = String(contextArguments[targetKey] || "");
            var contextValue = stateKey ? currentState[stateKey] : null;
            if ((contextValue == null || contextValue === "") && card.payload) {
              contextValue = card.payload[stateKey];
            }
            if (stateKey && contextValue != null && contextValue !== "") {
              args[String(targetKey)] = contextValue;
            }
          });
        }
        return PluginFrontendService.call(
          String(card.payload && card.payload.packId || ""),
          String(contribution && contribution.method || ""),
          args,
          projectId
        ).then(function (result) {
          var stateKey = String(contribution.state_key || argumentKey);
          var nextState = Object.assign({}, result && result.session || {}, { [stateKey]: value });
          if (contribution && contribution.reload_view === true) {
            nextState.pluginViewRevision = Date.now();
          }
          updatePluginPaneState(nextState);
          return result;
        });
      }
      grip = <WbcSplitGripBar
        dragSource={card.id}
        menuDisabled={singlePane && !pluginPaneMenu.length}
        menuType="content"
        menuContributions={pluginPaneMenu}
        menuState={pluginPaneState}
        onInvokeContribution={invokePluginPaneMenu}
        onToggleSide={move}
        onClose={close}
        onNewConversation={columnLength === 1 ? function () { createPaneConversation(card.id); } : null}
        onSplitPointerDown={pointerDown}
        onSplitDragStart={dragStart}
        onSplitDragEnd={handlePaneCardDragEnd}
      />;
      content = <section className="wbc-side-agent-split wbc-plugin-view-pane" aria-label={String(card.payload && card.payload.title || "Plugin")}>
        <div className="wbc-plugin-view-host-strip" aria-hidden="true" />
        <div className="wbc-plugin-view-content">
          <PluginView
            key={String(pluginPaneState.pluginViewRevision || "initial")}
            projectId={projectId}
            payload={card.payload}
            onStateChange={updatePluginPaneState}
          />
        </div>
      </section>;
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
      } else if (card.kind === "map" && mapAvailable) {
        content = <WbcMapPaneContent
          chatId={card.ownerChatId || activeChatId}
          item={card.payload}
          available={mapAvailable}
          onSelect={function (nextItem) { updatePaneCard(card.id, function (current) { return Object.assign({}, current, { payload: nextItem }); }); }}
          onClose={close}
        />;
      } else if (card.kind === "browser" && browserAvailable) {
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
          <div className="wbc-resource-split-body wbc-subagents-split-body"><WbcSubagentsTab data={subagentData} loading={subagentLoading} onSelectRound={function (roundId) { loadSubagents(card.ownerChatId || activeChatId, roundId); }} onClose={close} /></div>
        </aside>;
      } else if (card.kind === "terminal") {
        var TerminalPane = terminalModule.Pane;
        content = <TerminalPane terminalId={String(card.payload || "")} onState={updateTerminalSummary} />;
      }
    }
    if (!content) return null;
    // A rail terminal switch replaces the single pane's descriptor. Keep the
    // frame identity stable so TerminalPane can retain the previous terminal's
    // xterm/WebSocket instance and merely hide it.
    var paneFrameKey = card.kind === "terminal" && singlePane
      ? "terminal-workspace"
      : card.id;
    return <WbcPaneCardFrame
      key={paneFrameKey}
      card={card}
      semanticNodeId={wbcPaneSemanticNodeId(card.id)}
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
      observationState={observationState}
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
        {cards.length === 2 ? <WbcPaneRowResizer active={isActive} side={side} ratio={ratio} onResize={function (next) { resizePaneRow(side, next); }} /> : null}
      </section>
    );
  }

  return (
    <div
      ref={pageRef}
      className={"wbc-page"
        + (workspaceContent ? " wbc-external-workspace" : "")
        + (sideVisible ? "" : " wbc-side-hidden")
        + (navCollapsed ? " wbc-nav-collapsed" : "")
        + (projectPaneOnly ? " wbc-project-pane-only" : "")
        + (splitDetailOpen && !projectPaneOnly ? " side-agent-split-open" : "")
        + (singleColumnWorkspaceOpen ? " wbc-pane-single-column-open" : "")
        + (splitDetailOpen && !projectPaneOnly && splitSide === "left" ? " wbc-split-left" : "")
        + (chatSideDropActive && paneCardCount !== 1 ? " wbc-chat-side-drop-active" : "")}
      style={{
        "--wbc-chat-side-preview-width": sideAgentSplitWidth + "px",
        ...(splitDetailOpen && !projectPaneOnly && !singleColumnWorkspaceOpen
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
              {chatDragKind === "plugin-view"
                  ? wbcT("workbenchChat.dropPluginViewToOpenSide", "Release to open this plugin in the side panel")
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
        codeAvailable={codeAvailable}
        projectId={projectId}
        projectName={project && project.name || ""}
        chats={chats}
        terminals={terminals}
        terminalsLoading={terminalsLoading}
        activeTerminalId={activeTerminalId}
        railMode={railMode}
        pinnedChatIds={pinnedChatIds}
        activeChatId={railSelectionSuppressed ? "" : activeChatId}
        loading={loading}
        runningChatIds={runningChatIds}
        runtimeEngine={runtimeEngine}
        onSelect={function (chatId) { activateWorkspace(); return selectChat(chatId); }}
        onAnswer={handleRailAnswer}
        onCreate={function () { activateWorkspace(); return handleCreateChat(); }}
        onRename={handleRenameChat}
        onDelete={handleDeleteChat}
        onTogglePinned={onTogglePinnedChat}
        onOpenFile={function (entry) { activateWorkspace(); return openProjectFile(entry); }}
        onOpenTerminal={function (terminalId, side) { activateWorkspace(); return openTerminal(terminalId, side); }}
        onCreateTerminal={function () { activateWorkspace(); return createTerminal(); }}
        onRenameTerminal={renameTerminal}
        onDeleteTerminal={deleteTerminal}
        onUpdateTerminalLayout={updateTerminalLayout}
        onOpenPluginView={function (payload, options) {
          var ownerScope = String(options && options.ownerScope || payload && payload.paneOwnerScope || "chat");
          var projectOwned = ownerScope === "project";
          var projectOwnerId = projectOwned ? projectPaneOwnerKey() : "";
          var requestedOptions = Object.assign({}, options || {}, projectOwned ? {
            ownerChatId: projectOwnerId,
          } : {});
          var payloadPackId = String(payload && (payload.packId || payload.pack_id) || "");
          var payloadViewId = String(payload && (payload.viewId || payload.view_id) || "");
          var payloadInstanceId = String(payload && (payload.instanceId || payload.instance_id) || "");
          function matchesRequestedPlugin(card) {
            var candidate = card && card.kind === "plugin-view" ? card.payload || {} : {};
            return card && card.kind === "plugin-view"
              && String(candidate.packId || candidate.pack_id || "") === payloadPackId
              && String(candidate.viewId || candidate.view_id || "") === payloadViewId
              && String(candidate.instanceId || candidate.instance_id || "") === payloadInstanceId;
          }
          if (options && options.replaceWorkspace) {
            var replacementLayout = paneLayoutFor(projectOwned ? projectOwnerId : activeChatId);
            var replacedCards = replacementLayout.left.concat(replacementLayout.right);
            if (projectOwned) {
              Object.keys(paneLayoutsByChat).forEach(function (ownerId) {
                if (ownerId === projectOwnerId) return;
                var layout = paneLayoutsByChat[ownerId] || {};
                replacedCards = replacedCards.concat(
                  (layout.left || []).concat(layout.right || []).filter(matchesRequestedPlugin)
                );
              });
            }
            var closedCardIds = {};
            replacedCards.forEach(function (existingCard) {
              var existingCardId = String(existingCard && existingCard.id || "");
              if (existingCardId && closedCardIds[existingCardId]) return;
              if (existingCardId) closedCardIds[existingCardId] = true;
              var existingState = existingCard && existingCard.kind === "plugin-view"
                && existingCard.payload && existingCard.payload.state || {};
              var closeMethod = String(existingState && existingState.closeMethod || "");
              var closeSessionId = String(existingState && existingState.session_id || "");
              if (!closeMethod || !closeSessionId) return;
              PluginFrontendService.call(
                String(existingCard.payload && existingCard.payload.packId || ""),
                closeMethod,
                { session_id: closeSessionId },
                projectId
              ).catch(function () {});
            });
            if (projectOwned) {
              setPaneLayoutsByChat(function (current) {
                var next = {};
                Object.keys(current).forEach(function (ownerId) {
                  var layout = current[ownerId] || {};
                  next[ownerId] = Object.assign({}, layout, {
                    left: (layout.left || []).filter(function (card) {
                      return ownerId === projectOwnerId || !matchesRequestedPlugin(card);
                    }),
                    right: (layout.right || []).filter(function (card) {
                      return ownerId === projectOwnerId || !matchesRequestedPlugin(card);
                    }),
                  });
                });
                return next;
              });
            }
          }
          activateWorkspace();
          if (projectOwned) activateProjectPaneWorkspace();
          var openedCard = openPaneContent("plugin-view",
            Object.assign({ projectId: projectId }, payload || {}),
            requestedOptions
          );
          if (projectOwned && openedCard) {
            setSelectedWorkspaceTabKey("plugin-view:" + String(openedCard.id || ""));
          }
          return openedCard;
        }}
        onOpenSplit={function (chatId, options) {
          activateWorkspace();
          return openPaneContent("chat", String(chatId || ""), options || { side: "right" });
        }}
        collapsed={navCollapsed}
        onToggleCollapsed={onToggleNavCollapsed}
        collapseControl={collapseControl}
        moduleDock={moduleDock}
      />
      {workspaceContent ? <div className="wbc-external-workspace-host">{workspaceContent}</div> : null}
      <div
        className={"wbc-pane-layout" + (paneHasTwoColumns ? " split" : " single")}
        style={paneHasTwoColumns ? { "--wbc-pane-right-width": paneColumnWidth + "px" } : undefined}
      >
        <WbcPaneSemanticController
          active={isActive}
          layout={paneLayout}
          rootRef={pageRef}
          chats={chats}
          terminals={terminals}
          onOpenPane={function (kind, id, options) {
            if (kind === "terminal") return openTerminal(id, options && options.side);
            return openPaneContent(kind, id, options);
          }}
          onMovePane={movePaneCard}
          onSwapPanes={swapPaneCards}
          onClosePane={closePaneCardWithConfirmation}
        />
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
              {paneHasTwoColumns ? <WbcPaneColumnResizer active={isActive} width={paneColumnWidth} onResize={resizePaneColumn} /> : null}
            </React.Fragment>}
      </div>
      <WbcChatPageContextMenu
        menu={pageContextMenu}
        chat={visibleChat}
        onClose={closePageContextMenu}
        onRename={openQuickRename}
        onDelete={handleDelete}
        onCompact={handleCompact}
        compactBusy={compactBusy}
        onGenerateMemory={memoryAvailable ? handleGenerateMemory : null}
        memoryLearningBusy={memoryLearningBusy}
      />
      <WbcRenameDialog
        chat={quickRenameChat}
        onClose={function () { setQuickRenameChat(null); }}
        onRename={handleRenameChat}
      />
      <WbcGoalConfirmationDialog
        chat={visibleChat || selectedChatSummary}
        onGoalChanged={applyGoalChanged}
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
      {mapAvailable ? <WbcMapSplitHost
        chatId={activeChatId}
        available={mapAvailable}
        item={splitMap}
        width={sideAgentSplitWidth}
        onSelect={function (next) { selectResourceSplit("map", next); }}
        onResize={resizeSideAgentSplit}
        onClose={closeResourceSplit}
        splitSide={splitSide}
        onToggleSide={toggleSplitSide}
        onSplitDragStart={handleSplitDragStart}
        onSplitDragEnd={handleSplitDragEnd}
      /> : null}
      {browserAvailable ? <WbcBrowserSplitHost
        tabId={splitBrowserTabId}
        browserState={activeBrowserState}
        browserSessionId={activeChatId || ""}
        width={sideAgentSplitWidth}
        onSelect={function (tabId) { selectResourceSplit("browser", tabId); }}
        onResize={resizeSideAgentSplit}
        onClose={closeResourceSplit}
        onTakeoverComplete={function (payload) {
          return wbcCompleteBrowserTakeover(activeChat, payload, handleAnswer);
        }}
        splitSide={splitSide}
        onToggleSide={toggleSplitSide}
        onSplitDragStart={handleSplitDragStart}
        onSplitDragEnd={handleSplitDragEnd}
      /> : null}
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
