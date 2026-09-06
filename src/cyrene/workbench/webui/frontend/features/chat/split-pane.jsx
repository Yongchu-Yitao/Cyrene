import { WbcTranscript } from "./messages.jsx"
import { workbenchServices } from "../../shared/runtime/services.jsx"
import { WBC_ICONS, WBC_SIDE_TAB_ICONS, WbcSplitPickerMenu, WorkbenchChatModel, useWbcEffect, useWbcLayoutEffect, useWbcMemo, useWbcRef, useWbcState, wbcAgentColor, wbcAgentInitials, wbcAttachmentTypeLabel, wbcBrowserTabPickerPayload, wbcBrowserTabPickerToggleIsDebounced, wbcClampSideSplitWidthForPage, wbcCreateDetachedRuntime, wbcErrorText, wbcFileViewKind, wbcFormatTime, wbcHighlightMentions, wbcMergeChronologicalMessages, wbcNormalizePermissionMode, wbcNotifyBrowserLayoutChanged, wbcPreserveLiveTimelineAnchors, wbcReconcileLiveUserMessages, wbcReduceDetachedRuntime, wbcRenderMapMarkdown, wbcRenderMarkdown, wbcSubagentStatusClass, wbcSubagentStatusText, wbcT } from "../../workbench-chat.jsx"
import { WbcComposer, wbcBranchConnectors, wbcBranchKindLabel, wbcBranchLineage, wbcBranchRows, wbcBrowserStateForChat } from "./composer.jsx"
import { wbcProjectFileResource } from "./rail.jsx"
import { WorkbenchChatRuntimes, wbcCanOpenExternally, wbcChatUsedMap, wbcDownloadLink, wbcStartFileDrag } from "./file-resources.jsx"
import { WbcMapTab, WbcViewerTab } from "./viewer.jsx"
import { WbcThreadItem, wbcIsLiveAgentRequest } from "./conversation.jsx"
import { WbcResourceListRow } from "./resource-list.jsx"
import { useWbcConversationChanges } from "./conversation-changes.jsx"
import { WbcActivityGroup, WbcAgentNotification, WbcAssistantMessage, WbcModelStatusMessage, WbcQuestionPrompt, WbcRuntimeTranscript, WbcUserMessage, wbcGroupConsecutiveActivityMessages } from "./messages.jsx"
import { WbcArtifactsTab, WbcContextTab, WbcOverviewTab, useWbcLiveChatMetrics, useWbcLiveContextBlocks, useWbcLiveInbox } from "./context-panel.jsx"
import { ConversationPlanTimeline } from "../plan/conversation-plan-timeline.jsx"
import { WbcGoalTab } from "../goal/goal-ui.jsx"
import { WBC_PROJECT_FILE_DRAFTS, WbcArtifactSplit, WbcArtifactSplitHost, WbcBrowserList, WbcBrowserSplit, WbcBrowserSplitHost, WbcChangeSplit, WbcChangeSplitHost, WbcMapList, WbcMapPaneContent, WbcMapSplitHost, WbcResourceSplitHost, WbcViewerList, useWbcMapData, wbcArtifactFileKey, wbcCanEditProjectTextFile, wbcChatArtifactFiles, wbcChatDeliveredArtifacts, wbcDiscardProjectFileDraft, wbcEditableChatFileResource, wbcMapItemKey, wbcMapItemLabel, wbcProjectFileDraftKey, wbcProjectFileEditUrl, wbcViewerFileFromItems } from "./resource-splits.jsx"

// Workbench chat feature module with explicit ESM dependencies.
function WbcBranchTab({ chats, activeChatId, onSelectChat }) {
  var rows = useWbcMemo(function () {
    return wbcBranchRows(wbcBranchLineage(chats, activeChatId));
  }, [chats, activeChatId]);
  if (!rows.length) {
    return <p className="workbench-muted wbc-branch-empty">{wbcT("workbenchChat.branchEmpty", "This conversation has no branches.")}</p>;
  }
  var maxDepth = rows.reduce(function (depth, row) {
    return Math.max(depth, Number(row.depth) || 0);
  }, 0);
  return (
    <div className="wbc-branch" style={{ "--wbc-branch-rail": (maxDepth * 14 + 30) + "px" }}>
      <ul className="wbc-branch-tree">
        {rows.map(function (row, index) {
          var isActive = row.chatId === activeChatId;
          var isCurrent = isActive && row.isHead;
          var lane = row.depth > 0 ? " lane-fork" : " lane-main";
          var cls = "wbc-branch-row depth-" + row.depth + " kind-" + row.kind + lane + (isActive ? " on-current-branch" : "") + (isCurrent ? " current" : "");
          return (
            <li
              key={row.chatId + ":" + row.kind + ":" + index}
              className={cls}
            >
              <button
                type="button"
                className="wbc-branch-button"
                title={row.text || row.title || ""}
                aria-current={isCurrent ? "true" : undefined}
                onClick={function () { onSelectChat(row.chatId); }}
              >
                {wbcBranchConnectors(row).map(function (seg, segIndex) {
                  return <span key={"seg" + segIndex} className={"wbc-branch-line " + seg.cls} style={seg.style} aria-hidden="true" />;
                })}
                <span className="wbc-branch-node" style={{ left: (row.depth * 14 + 14) + "px" }} aria-hidden="true">
                  <span className="wbc-branch-node-core" />
                </span>
                <span className="wbc-branch-card">
                  <span className="wbc-branch-kind">{wbcBranchKindLabel(row.kind)}</span>
                  <span className="wbc-branch-text">{row.text || wbcT("workbenchChat.branchNoText", "(empty message)")}</span>
                  {isCurrent && <span className="wbc-branch-here">{wbcT("workbenchChat.branchHere", "Current")}</span>}
                </span>
              </button>
            </li>
          );
        })}
      </ul>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Right context panel (column 4)
// ---------------------------------------------------------------------------

function WbcSubagentsSplitHost({ open, data, loading, width, onSelectRound, onResize, onClose, splitSide, onToggleSide, onSplitDragStart, onSplitDragEnd }) {
  return (
       <WbcResourceSplitHost openKey={open ? "subagents" : ""} width={width} onResize={onResize} splitSide={splitSide} onToggleSide={onToggleSide} onClose={onClose} onSplitDragStart={onSplitDragStart} onSplitDragEnd={onSplitDragEnd}>
      {open ? <aside className="wbc-side-agent-split wbc-subagents-split" aria-label={wbcT("workbenchChat.subagents", "Subagents")}><div className="wbc-resource-split-body wbc-subagents-split-body"><WbcSubagentsTab data={data} loading={loading} onSelectRound={onSelectRound} onClose={onClose} /></div></aside> : null}
    </WbcResourceSplitHost>
  );
}

function WbcSideAgentSplitHost({ agent, agents, width, project, onOpenFile, onUpdate, onSelect, onResize, onClose, splitSide, onToggleSide, onSplitDragStart, onSplitDragEnd }) {
  return (
       <WbcResourceSplitHost openKey={agent && agent.id || ""} width={width} onResize={onResize} splitSide={splitSide} onToggleSide={onToggleSide} onClose={onClose} onSplitDragStart={onSplitDragStart} onSplitDragEnd={onSplitDragEnd}>
      {agent ? (
        <WbcSideAgentSplit
          agent={agent}
          agents={agents}
          project={project}
          onOpenFile={onOpenFile}
          onUpdate={onUpdate}
          onSelect={onSelect}
          onClose={onClose}
        />
      ) : null}
    </WbcResourceSplitHost>
  );
}

// A rail chat dragged onto the side panel opens as a read-only conversation
// beside the main thread. It polls while the source chat is running so a
// background run keeps the split in sync.
function WbcChatSplitHost({ chatId, width, onOpenContent, browserActiveByChat, onResize, onClose, onOpenInMain, splitSide, onToggleSide, project, onSplitDragStart, onSplitDragEnd }) {
  // The openKey must be empty when no chat is split, otherwise the host's
  // close branch (exit animation + lastChildren cleanup) never runs.
  var key = chatId ? "chat:" + chatId : "";
  return (
     <WbcResourceSplitHost openKey={key} width={width} onResize={onResize} splitSide={splitSide} onToggleSide={onToggleSide} onClose={onClose} onSplitDragStart={onSplitDragStart} onSplitDragEnd={onSplitDragEnd}>
      {chatId ? <WbcChatSplit chatId={chatId} project={project} onOpenContent={onOpenContent} browserActiveByChat={browserActiveByChat} onClose={onClose} onOpenInMain={onOpenInMain} splitSide={splitSide} onToggleSide={onToggleSide} onSplitDragStart={onSplitDragStart} onSplitDragEnd={onSplitDragEnd} /> : null}
    </WbcResourceSplitHost>
  );
}

function wbcRefreshSplitChat(options) {
  var requestedId = options.chatIdRef.current;
  if (!requestedId) {
    options.setLoading(false);
    return Promise.resolve(null);
  }
  var requestSequence = ++options.refreshSequenceRef.current;
  if (!options.background) options.setLoading(true);
  function stale() {
    return options.disposedRef.current
      || String(options.chatIdRef.current || "") !== requestedId
      || options.refreshSequenceRef.current !== requestSequence;
  }
  return WorkbenchChatModel.getChat(requestedId, { toast: false })
    .then(function (fresh) {
      if (stale()) return null;
      var reconciled = wbcPreserveLiveTimelineAnchors(
        null,
        fresh,
        options.runtimeEngine.get(requestedId)
      );
      options.setChat(reconciled);
      options.setLoading(false);
      return reconciled;
    })
    .catch(function (err) {
      if (stale()) return null;
      if (Number(err && err.status) === 404 && options.onDeleted) {
        options.onDeleted(requestedId);
        return null;
      }
      options.setError(wbcErrorText(err));
      options.setLoading(false);
      return null;
    });
}

function WbcChatSplit({ chatId, project, runtimeEngine, onOpenContent, browserActiveByChat, onClose, onDeleted, onOpenInMain, splitSide, onToggleSide, onSplitPointerDown, onSplitDragStart, onSplitDragEnd, menuDisabled }) {
  runtimeEngine = runtimeEngine || WorkbenchChatRuntimes;
  var [chat, setChat] = useWbcState(null); var [loading, setLoading] = useWbcState(true);
  var [error, setError] = useWbcState(""); var [streamText, setStreamText] = useWbcState("");
  var [streamNotifications, setStreamNotifications] = useWbcState([]); var [streamRuntime, setStreamRuntime] = useWbcState(null);
  var [running, setRunning] = useWbcState(false);
  // The grip menu's "open conversation panel" action floats this split chat's
  // own conversation panel here — never the main conversation's panel. Like
  // the main panel it starts collapsed (no accordion tab open), and a
  // pointer-down anywhere outside the panel dismisses it.
  var [splitPanelOpen, setSplitPanelOpen] = useWbcState(false);
  var [splitPanelTab, setSplitPanelTab] = useWbcState("");
  var splitPanelRef = useWbcRef(null);
  var splitRef = useWbcRef(null);
  var scrollRef = useWbcRef(null);
  var chatIdRef = useWbcRef(chatId);
  var disposedRef = useWbcRef(false);
  var refreshSequenceRef = useWbcRef(0);
  var runStartedAtRef = useWbcRef(Date.now());
  var previousRuntimeRef = useWbcRef(null);
  chatIdRef.current = chatId;

  function refresh(background) {
    return wbcRefreshSplitChat({
      background: background, chatIdRef: chatIdRef, disposedRef: disposedRef,
      onDeleted: onDeleted, refreshSequenceRef: refreshSequenceRef,
      runtimeEngine: runtimeEngine, setChat: setChat,
      setError: setError, setLoading: setLoading,
    });
  }

  useWbcEffect(function () {
    var requestedId = String(chatId || "");
    disposedRef.current = false;
    refreshSequenceRef.current += 1;
    previousRuntimeRef.current = null;
    setChat(null);
    setError("");
    setStreamNotifications([]);
    setStreamRuntime(null);
    setLoading(true);
    setSplitPanelOpen(false);
    setSplitPanelTab("");
    refresh(true).then(function (fresh) {
      if (
        disposedRef.current
        || !fresh
        || fresh.status !== "running"
        || runtimeEngine.isRunning(requestedId)
      ) return;
      runtimeEngine.reconnect(requestedId, WorkbenchChatModel);
    });
    return function () {
      disposedRef.current = true;
    };
  }, [chatId, runtimeEngine]);

  useWbcEffect(function () {
    var requestedId = String(chatId || "");
    if (!requestedId || !runtimeEngine || !runtimeEngine.subscribe) return undefined;
    function applyRuntime(snapshot) {
      var next = snapshot[requestedId] || null;
      var previous = previousRuntimeRef.current;
      previousRuntimeRef.current = next;
      // Retry is a transcript state transition, not a render-time filter.
      // Apply it to every owner of chat state as soon as the shared runtime
      // publishes the boundary (including detached-window renderers).
      if (next && next.retryTruncateAfterMessageId) {
        setChat(function (current) {
          return wbcPreserveLiveTimelineAnchors(current, current, next);
        });
      }
      setStreamRuntime(next);
      setStreamText(String(next && next.text || ""));
      setStreamNotifications(next && Array.isArray(next.notifications) ? next.notifications : []);
      setRunning(!!next);
      if (previous && !next) {
        var failure = runtimeEngine.getFailure ? runtimeEngine.getFailure(requestedId) : null;
        if (failure) setError(wbcErrorText(failure));
        refresh(true);
      }
    }
    applyRuntime(runtimeEngine.snapshot());
    return runtimeEngine.subscribe(applyRuntime);
  }, [chatId, runtimeEngine]);

  useWbcLayoutEffect(function () {
    var el = scrollRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [chat && chat.messages && chat.messages.length, loading, running, streamText]);

  // Split conversations own an absolutely positioned composer. Measure it on
  // the split itself instead of relying on the main page's reserve variable:
  // detached windows do not have a `.wbc-page` ancestor, and an invalid/missing
  // reserve would leave the end of the transcript underneath the composer.
  useWbcLayoutEffect(function () {
    var split = splitRef.current;
    var composer = split && split.querySelector(":scope > .wbc-composer");
    if (!split || !composer) return undefined;
    var resizeRaf = 0;
    var lastHeight = 0;
    function commitComposerReserveHeight() {
      resizeRaf = 0;
      var height = Math.ceil(composer.getBoundingClientRect().height);
      if (height <= 0 || height === lastHeight) return;
      lastHeight = height;
      split.style.setProperty("--wbc-composer-reserve-height", height + "px");
    }
    function scheduleComposerReserveHeight() {
      if (resizeRaf) return;
      resizeRaf = requestAnimationFrame(commitComposerReserveHeight);
    }
    commitComposerReserveHeight();
    var observer = typeof ResizeObserver === "function"
      ? new ResizeObserver(scheduleComposerReserveHeight)
      : null;
    if (observer) observer.observe(composer);
    window.addEventListener("resize", scheduleComposerReserveHeight);
    return function () {
      if (observer) observer.disconnect();
      window.removeEventListener("resize", scheduleComposerReserveHeight);
      if (resizeRaf) cancelAnimationFrame(resizeRaf);
      split.style.removeProperty("--wbc-composer-reserve-height");
    };
  }, [chatId]);

  // Dismiss the panel when the user interacts with the split conversation
  // around it (transcript, composer, grip); clicks inside the panel keep it.
  useWbcEffect(function () {
    if (!splitPanelOpen) return undefined;
    function closeOutside(event) {
      if (splitPanelRef.current && !splitPanelRef.current.contains(event.target)) {
        setSplitPanelOpen(false);
      }
    }
    document.addEventListener("pointerdown", closeOutside);
    return function () { document.removeEventListener("pointerdown", closeOutside); };
  }, [splitPanelOpen]);

  function submit(payload) {
    var question = String(payload && payload.message || "").trim();
    var attachments = payload && Array.isArray(payload.attachments) ? payload.attachments : [];
    var current = chatIdRef.current;
    if ((!question && !attachments.length) || running || !current) return;
    setError("");
    runStartedAtRef.current = Date.now();
    runtimeEngine.start(current, {
      message: question,
      attachments: attachments,
      mode: chat && chat.permissionMode || payload.mode || "default",
      model: chat && chat.modelSelectionId || payload.model || "",
      reasoningEffort: chat && chat.reasoningEffort || payload.reasoningEffort || "",
    }, WorkbenchChatModel);
  }

  function stop() {
    if (!running) return;
    setError("");
    runtimeEngine.interrupt(chatIdRef.current, WorkbenchChatModel).catch(function (err) {
      if (!disposedRef.current) setError(wbcErrorText(err));
    });
  }

  function guide(message) {
    var current = chatIdRef.current;
    var text = String(message || "").trim();
    if (!running || !current || !text) return Promise.resolve(null);
    setError("");
    return WorkbenchChatModel.sendGuidance(
      current,
      text,
      "guide_" + Date.now() + "_" + Math.random().toString(36).slice(2, 8)
    ).then(function (response) {
      if (response && response.userMessage && !disposedRef.current) {
        setChat(function (prev) {
          if (!prev || String(prev.id || "") !== String(current)) return prev;
          return {
            ...prev,
            messages: wbcMergeChronologicalMessages(prev.messages || [], [response.userMessage]),
          };
        });
      }
      return response;
    }).catch(function (err) {
      if (!disposedRef.current) setError(wbcErrorText(err));
      throw err;
    });
  }

  // Resume a clarification / permission request in the split conversation
  // itself. The main thread's answer handler is bound to activeChatId, which
  // may be a different chat while this pane is open.
  function answerPendingQuestion(questionId, optionText, resumeMode) {
    var current = chatIdRef.current;
    var pending = streamRuntime && streamRuntime.pendingQuestion
      || chat && chat.pendingQuestion
      || null;
    var formAnswer = optionText && typeof optionText === "object" && optionText.__agentForm === true;
    var answer = formAnswer ? optionText : String(optionText || "").trim();
    var liveRequest = wbcIsLiveAgentRequest(pending);
    if (!current || !questionId || (!formAnswer && !answer) || (running && !liveRequest)) return;
    if (liveRequest) {
      var response = String(pending.kind || "") === "permission.requested"
        ? { type: "option", optionId: String(answer || "") }
        : (formAnswer
          ? { type: "form", form: answer.values && typeof answer.values === "object" ? answer.values : {} }
          : { type: "text", text: String(answer || "") });
      setError("");
      setChat(function (prev) { return prev ? { ...prev, pendingQuestion: null, status: "running" } : prev; });
      runtimeEngine.update(current, function (runtime) {
        return runtime ? { ...runtime, pendingQuestion: null, lastEventAt: Date.now() } : runtime;
      });
      return WorkbenchChatModel.answerAgentRequest(current, questionId, response).catch(function (err) {
        if (disposedRef.current) return;
        setError(wbcErrorText(err));
        setChat(function (prev) { return prev ? { ...prev, pendingQuestion: pending } : prev; });
        runtimeEngine.update(current, function (runtime) {
          return runtime ? { ...runtime, pendingQuestion: pending, lastEventAt: Date.now() } : runtime;
        });
      });
    }
    var optimisticAnswer = {
      id: "chat_split_answer_pending_" + Date.now(),
      role: "user",
      content: String(answer),
      createdAt: new Date().toISOString(),
      answerToQuestionId: questionId,
      optimistic: true,
    };
    setError("");
    runStartedAtRef.current = Date.now();
    runtimeEngine.update(current, {
      chatId: current,
      text: "",
      progress: [],
      activities: [],
      activitySeq: 0,
      segments: [],
      notifications: [],
      userMessages: [optimisticAnswer],
      startedAt: runStartedAtRef.current,
      lastEventAt: runStartedAtRef.current,
      replying: true,
    });
    setChat(function (prev) {
      if (!prev || String(prev.id || "") !== String(current)) return prev;
      return {
        ...prev,
        pendingQuestion: null,
        status: "running",
        messages: wbcMergeChronologicalMessages(prev.messages || [], [optimisticAnswer]),
      };
    });
    var answerMode = wbcNormalizePermissionMode(
      resumeMode,
      chat && chat.permissionMode ? chat.permissionMode : "default"
    );
    WorkbenchChatModel.answerChat(current, questionId, answer, { mode: answerMode })
      .then(function () { return refresh(true); })
      .catch(function (err) {
        if (disposedRef.current || String(chatIdRef.current || "") !== String(current)) return;
        setError(wbcErrorText(err));
        // Restore the durable pending question so the user can retry.
        refresh(true).catch(function () {});
      })
      .finally(function () {
        if (disposedRef.current || String(chatIdRef.current || "") !== String(current)) return;
        runtimeEngine.update(current, null);
      });
  }

  function openContent(type, payload) {
    setSplitPanelOpen(false);
    if (onOpenContent) onOpenContent(type, payload, chat);
  }

  function openFile(file) {
    openContent("viewer", file);
  }

  var livePendingQuestion = streamRuntime && streamRuntime.pendingQuestion || null;
  var displayChat = livePendingQuestion && chat
    ? { ...chat, pendingQuestion: livePendingQuestion, status: "running" }
    : chat;
  var messages = wbcReconcileLiveUserMessages(
    chat && Array.isArray(chat.messages) ? chat.messages : [],
    streamRuntime && streamRuntime.userMessages
  );
  var displayMessages = wbcGroupConsecutiveActivityMessages(messages, streamRuntime);
  var errorText = error;
  return (
    <aside ref={splitRef} className="wbc-side-agent-split wbc-chat-split wbc-conversation-split" data-tour="chat_split_pane" aria-label={wbcT("workbenchChat.chatSplitLabel", "Chat")}>
      <div className="wbc-split-panel-grip">
        <WbcSplitGripBar
          dragSource="split"
          side={splitSide}
          onToggleSide={onToggleSide}
          onClose={onClose}
          onOpenConversationPanel={function () { setSplitPanelOpen(true); }}
        onSplitPointerDown={onSplitPointerDown}
        onSplitDragStart={onSplitDragStart}
        onSplitDragEnd={onSplitDragEnd}
        menuDisabled={menuDisabled}
      />
      </div>
      {splitPanelOpen && (
        <div className="wbc-split-chat-panel" ref={splitPanelRef} role="dialog" aria-label={wbcT("workbenchChat.sidePanelTitle", "Conversation panel")}>
          <WbcSide
            project={project}
            chat={displayChat}
            chatLoading={loading}
            chatDetailed={!!chat}
            chats={chat ? [chat] : []}
            activeChatId={chatId}
            onSelectChat={function () {}}
            runtime={streamRuntime}
            subagentData={null}
            subagentLoading={false}
            onSelectSubagentRound={function () {}}
            tab={splitPanelTab}
            onTabChange={setSplitPanelTab}
            viewerFile={null}
            onOpenFile={openFile}
            onSelectArtifact={function (file) { openContent("artifact", file); }}
            onSelectChange={function (change) { openContent("change", change); }}
            onSelectViewer={function (file) { openContent("viewer", file); }}
            onSelectMap={function (item) { openContent("map", item); }}
            onSelectBrowser={function (tabId) { openContent("browser", tabId); }}
            onOpenSubagents={function () { openContent("subagents", true); }}
            onViewerViewed={function () {}}
            onRename={function () {}}
            onDelete={function () {}}
            onCompact={function () {}}
            compactBusy={false}
            sideAgents={[]}
            sideAgentsLoading={false}
            activeSideAgentId=""
            onSelectSideAgent={function () {}}
            onUpdateSideAgent={function () {}}
            onDeleteSideAgent={function () {}}
            onBrowserTakeoverComplete={function () { return Promise.resolve(); }}
            browserActiveByChat={browserActiveByChat}
            browserSuppressed={false}
            onToggleSide={function () {}}
            floating={true}
            onCloseFloating={function () { setSplitPanelOpen(false); }}
          />
        </div>
      )}
      <div className="wbc-thread-stage wbc-chat-split-stage">
        <div className="wbc-thread" data-cyrene-revision-volatile="true" ref={scrollRef}>
        {loading && !messages.length && (
          <div className="wbc-chat-split-state" role="status">
            <span className="wbc-spinner" aria-hidden="true" />
            <span>{wbcT("workbenchChat.loading", "Loading chats...")}</span>
          </div>
        )}
        {!loading && !messages.length && !errorText && (
          <div className="wbc-chat-split-state">{wbcT("workbenchChat.noMessages", "No messages yet")}</div>
        )}
        {errorText && <div className="wbc-side-agent-error" role="alert">{errorText}</div>}
        <WbcTranscript messages={messages} runtime={streamRuntime} onOpenFile={openFile} chatId={chatId}
          pendingQuestion={displayChat && displayChat.pendingQuestion} onAnswer={answerPendingQuestion} />
        {displayChat && displayChat.pendingQuestion && displayChat.pendingQuestion.id && (!running || wbcIsLiveAgentRequest(displayChat.pendingQuestion)) && !messages.some(function (message) {
          return message.questionPrompt && String(message.questionId || "") === String(displayChat.pendingQuestion.id || "");
        }) && (
          <WbcThreadItem><WbcQuestionPrompt pending={displayChat.pendingQuestion} onAnswer={answerPendingQuestion} busy={running && !wbcIsLiveAgentRequest(displayChat.pendingQuestion)} /></WbcThreadItem>
        )}
        </div>
      </div>
      <WbcComposer
        key={"split-composer:" + String(chat && chat.id || chatId || "")}
        chat={displayChat}
        project={project}
        runtime={streamRuntime}
        running={running}
        onSend={submit}
        onGuidance={guide}
        onInterrupt={stop}
        draftNamespace={"chat-split:"}
        autoFocus={false}
        clearOnSend={true}
        error={error}
        errorKind="message"
        compact={false}
        placeholder={wbcT("workbenchChat.placeholder", "Message Cyrene...")}
        runningPlaceholder={wbcT("workbenchChat.placeholderRunning", "Send guidance to the running agent...")}
      />
    </aside>
  );
}

function WbcPaneContextTrackDropSurface({ card, dropKey, dropTarget, onDropOver, onDrop }) {
  var activeEdge = dropTarget && String(dropTarget.dropKey || "") === String(dropKey || "")
    ? dropTarget.edge
    : "";
  var activeLabel = activeEdge === "replace"
    ? wbcT("workbenchChat.dropConversationReplace", "Release to replace the current conversation")
    : activeEdge === "right"
    ? wbcT("workbenchChat.dropPaneRight", "Release to open on the right")
    : activeEdge === "top"
    ? wbcT("workbenchChat.dropPaneTop", "Release to open above")
    : activeEdge === "bottom"
    ? wbcT("workbenchChat.dropPaneBottom", "Release to open below")
    : wbcT("workbenchChat.dropPaneLeft", "Release to open on the left");
  return (
    <React.Fragment>
      <div
        className="wbc-pane-context-drop-sensor top"
        onDragEnter={function (event) { onDropOver(event, card.id, "top", dropKey); }}
        onDragOver={function (event) { onDropOver(event, card.id, "top", dropKey); }}
        onDrop={function (event) { onDrop(event, card.id, "top"); }}
      />
      <div
        className="wbc-pane-context-drop-sensor replace"
        onDragEnter={function (event) { onDropOver(event, card.id, "replace", dropKey); }}
        onDragOver={function (event) { onDropOver(event, card.id, "replace", dropKey); }}
        onDrop={function (event) { onDrop(event, card.id, "replace"); }}
      />
      <div
        className="wbc-pane-context-drop-sensor right"
        onDragEnter={function (event) { onDropOver(event, card.id, "right", dropKey); }}
        onDragOver={function (event) { onDropOver(event, card.id, "right", dropKey); }}
        onDrop={function (event) { onDrop(event, card.id, "right"); }}
      />
      <div
        className="wbc-pane-context-drop-sensor bottom"
        onDragEnter={function (event) { onDropOver(event, card.id, "bottom", dropKey); }}
        onDragOver={function (event) { onDropOver(event, card.id, "bottom", dropKey); }}
        onDrop={function (event) { onDrop(event, card.id, "bottom"); }}
      />
      {activeEdge === "top" || activeEdge === "replace" || activeEdge === "right" || activeEdge === "bottom" ? (
        <div className={"wbc-pane-card-drop-zone context-preview " + activeEdge + " active"}>
          <span>{activeLabel}</span>
        </div>
      ) : null}
    </React.Fragment>
  );
}

function WbcPaneFiveWayDropSurface({ card, dropKey, replaceConversation, dropTarget, onDropOver, onDrop }) {
  var activeEdge = dropTarget && String(dropTarget.dropKey || "") === String(dropKey || "")
    ? dropTarget.edge
    : "";
  var replaceLabel = replaceConversation
    ? wbcT("workbenchChat.dropConversationReplace", "Release to replace the current conversation")
    : wbcT("workbenchChat.dropPaneReplace", "Release to replace this split");
  var axisLabel = activeEdge === "left"
    ? wbcT("workbenchChat.dropPaneLeft", "Release to open on the left")
    : activeEdge === "right"
    ? wbcT("workbenchChat.dropPaneRight", "Release to open on the right")
    : activeEdge === "top"
    ? wbcT("workbenchChat.dropPaneTop", "Release to open above")
    : activeEdge === "bottom"
    ? wbcT("workbenchChat.dropPaneBottom", "Release to open below")
    : replaceLabel;
  return (
    <React.Fragment>
      <div
        className="wbc-pane-card-axis-sensor top"
        onDragEnter={function (event) { onDropOver(event, card.id, "top", dropKey); }}
        onDragOver={function (event) { onDropOver(event, card.id, "top", dropKey); }}
        onDrop={function (event) { onDrop(event, card.id, "top"); }}
      />
      <div
        className="wbc-pane-card-axis-sensor left"
        onDragEnter={function (event) { onDropOver(event, card.id, "left", dropKey); }}
        onDragOver={function (event) { onDropOver(event, card.id, "left", dropKey); }}
        onDrop={function (event) { onDrop(event, card.id, "left"); }}
      />
      <div
        className="wbc-pane-card-axis-sensor replace"
        onDragEnter={function (event) { onDropOver(event, card.id, "replace", dropKey); }}
        onDragOver={function (event) { onDropOver(event, card.id, "replace", dropKey); }}
        onDrop={function (event) { onDrop(event, card.id, "replace"); }}
      />
      <div
        className="wbc-pane-card-axis-sensor right"
        onDragEnter={function (event) { onDropOver(event, card.id, "right", dropKey); }}
        onDragOver={function (event) { onDropOver(event, card.id, "right", dropKey); }}
        onDrop={function (event) { onDrop(event, card.id, "right"); }}
      />
      <div
        className="wbc-pane-card-axis-sensor bottom"
        onDragEnter={function (event) { onDropOver(event, card.id, "bottom", dropKey); }}
        onDragOver={function (event) { onDropOver(event, card.id, "bottom", dropKey); }}
        onDrop={function (event) { onDrop(event, card.id, "bottom"); }}
      />
      {activeEdge ? (
        <div className={"wbc-pane-card-drop-zone axis-preview " + activeEdge + " active"}>
          <span>{axisLabel}</span>
        </div>
      ) : null}
    </React.Fragment>
  );
}

function WbcPaneCardFrame({ card, semanticNodeId, dropKey, children, grip, dropEnabled, replaceOnly, axisEnabled, replaceConversation, dropTarget, onDropOver, onDrop, onDropLeave, observationState }) {
  var activeEdge = dropTarget && String(dropTarget.dropKey || "") === String(dropKey || "")
    ? dropTarget.edge
    : "";
  var replaceLabel = replaceConversation
    ? wbcT("workbenchChat.dropConversationReplace", "Release to replace the current conversation")
    : wbcT("workbenchChat.dropPaneReplace", "Release to replace this split");
  return (
    <article
      className={"wbc-pane-card wbc-pane-card-" + String(card && card.kind || "content")
        + (observationState ? " is-resource-observed" : "")
        + (observationState && observationState.finishing ? " is-resource-observation-finishing" : "")}
      data-pane-card-id={card && card.id || ""}
      data-pane-semantic-node-id={semanticNodeId || ""}
      data-pane-drop-key={dropKey || card.id}
    >
      {grip ? <div className="wbc-pane-card-grip">{grip}</div> : null}
      {observationState ? <div className="wbc-resource-observation-indicator" role="status">
        <span className="wbc-running-dot wbc-resource-observation-dot" aria-hidden="true" />
        <span>{wbcT("workbenchChat.agentViewing", "Agent is viewing")}</span>
        {Number(observationState.count || 0) > 1 ? <b>{Number(observationState.count)}</b> : null}
      </div> : null}
      {children}
      {dropEnabled ? (
        <div className={"wbc-pane-card-drop-layer" + (replaceOnly ? " replace-only" : "") + (axisEnabled ? " axis-enabled" : "")} onDragLeave={onDropLeave}>
          {replaceOnly ? (
            <div
              className={"wbc-pane-card-drop-zone replace" + (activeEdge === "replace" ? " active" : "")}
              onDragEnter={function (event) { onDropOver(event, card.id, "replace", dropKey); }}
              onDragOver={function (event) { onDropOver(event, card.id, "replace", dropKey); }}
              onDrop={function (event) { onDrop(event, card.id, "replace"); }}
            ><span>{replaceLabel}</span></div>
          ) : axisEnabled ? (
            <WbcPaneFiveWayDropSurface
              card={card}
              dropKey={dropKey}
              replaceConversation={replaceConversation}
              dropTarget={dropTarget}
              onDropOver={onDropOver}
              onDrop={onDrop}
            />
          ) : (
            <React.Fragment>
              <div
                className={"wbc-pane-card-drop-zone top" + (activeEdge === "top" ? " active" : "")}
                onDragEnter={function (event) { onDropOver(event, card.id, "top", dropKey); }}
                onDragOver={function (event) { onDropOver(event, card.id, "top", dropKey); }}
                onDrop={function (event) { onDrop(event, card.id, "top"); }}
              ><span>{wbcT("workbenchChat.dropPaneTop", "Release to open above")}</span></div>
              <div
                className={"wbc-pane-card-drop-zone replace" + (activeEdge === "replace" ? " active" : "")}
                onDragEnter={function (event) { onDropOver(event, card.id, "replace", dropKey); }}
                onDragOver={function (event) { onDropOver(event, card.id, "replace", dropKey); }}
                onDrop={function (event) { onDrop(event, card.id, "replace"); }}
              ><span>{replaceLabel}</span></div>
              <div
                className={"wbc-pane-card-drop-zone bottom" + (activeEdge === "bottom" ? " active" : "")}
                onDragEnter={function (event) { onDropOver(event, card.id, "bottom", dropKey); }}
                onDragOver={function (event) { onDropOver(event, card.id, "bottom", dropKey); }}
                onDrop={function (event) { onDrop(event, card.id, "bottom"); }}
              ><span>{wbcT("workbenchChat.dropPaneBottom", "Release to open below")}</span></div>
            </React.Fragment>
          )}
        </div>
      ) : null}
    </article>
  );
}

function WbcPaneRowResizer({ active, side, ratio, onResize }) {
  var handleRef = useWbcRef(null);
  var safeRatio = Math.max(0.2, Math.min(0.8, Number(ratio) || 0.5));
  // Grid gaps do not participate in fr sizing. Position the separator at the
  // exact centre of the 12px gap instead of at a percentage of the full
  // column, which drifts into one of the cards as the ratio changes.
  var seamOffset = 6 - (safeRatio * 12);
  var seamTop = "calc(" + (safeRatio * 100) + "% "
    + (seamOffset < 0 ? "- " : "+ ") + Math.abs(seamOffset) + "px)";
  function startResize(event) {
    if (event.button !== 0 || !onResize) return;
    event.preventDefault();
    var handle = event.currentTarget;
    var pointerId = event.pointerId;
    var column = event.currentTarget && event.currentTarget.closest
      ? event.currentTarget.closest(".wbc-pane-column")
      : null;
    if (!column) return;
    var rect = column.getBoundingClientRect();
    var finished = false;
    var frame = 0;
    var nextRatio = safeRatio;
    function paint() {
      frame = 0;
      column.style.gridTemplateRows = nextRatio + "fr " + (1 - nextRatio) + "fr";
      handle.style.top = "calc(" + (nextRatio * 100) + "% + " + (6 - nextRatio * 12) + "px)";
    }
    function move(moveEvent) {
      if (moveEvent.pointerId !== pointerId) return;
      // Plugin panes are sandboxed iframes. If a platform drops pointerup while
      // crossing that boundary, the next renderer move still exposes that the
      // primary button is no longer held; finish instead of resizing on hover.
      if (moveEvent.pointerType === "mouse" && !(moveEvent.buttons & 1)) {
        stop(moveEvent);
        return;
      }
      var trackHeight = Math.max(1, rect.height - 12);
      nextRatio = Math.max(0.2, Math.min(0.8, (moveEvent.clientY - rect.top - 6) / trackHeight));
      if (!frame) frame = requestAnimationFrame(paint);
    }
    function stop(stopEvent) {
      if (finished) return;
      if (stopEvent && Number.isFinite(stopEvent.pointerId) && stopEvent.pointerId !== pointerId) return;
      finished = true;
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", stop);
      window.removeEventListener("pointercancel", stop);
      window.removeEventListener("blur", stop);
      if (handle) handle.removeEventListener("lostpointercapture", stop);
      if (handle && handle.releasePointerCapture && handle.hasPointerCapture && handle.hasPointerCapture(pointerId)) {
        try { handle.releasePointerCapture(pointerId); } catch (error) {}
      }
      if (frame) { cancelAnimationFrame(frame); paint(); }
      document.body.classList.remove("wbc-resizing-pane-row");
      onResize(nextRatio);
      window.dispatchEvent(new CustomEvent("workbench:split-resize-end", {
        detail: { ratio: nextRatio, side: side },
      }));
    }
    if (handle && handle.setPointerCapture) {
      try { handle.setPointerCapture(pointerId); } catch (error) {}
      handle.addEventListener("lostpointercapture", stop, { once: true });
    }
    document.body.classList.add("wbc-resizing-pane-row");
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", stop, { once: true });
    window.addEventListener("pointercancel", stop, { once: true });
    window.addEventListener("blur", stop, { once: true });
  }
  function keyboardResize(event) {
    if (event.key !== "ArrowUp" && event.key !== "ArrowDown") return;
    event.preventDefault();
    onResize((Number(ratio) || 0.5) + (event.key === "ArrowUp" ? -0.04 : 0.04));
  }
  function semanticResize(input) {
    var next;
    if (input && Number.isFinite(Number(input.value_ratio))) {
      next = 0.2 + (Math.max(0, Math.min(1, Number(input.value_ratio))) * 0.6);
    } else {
      var delta = Number(input && input.delta_ratio);
      if (!Number.isFinite(delta)) throw new Error("delta_ratio or value_ratio is required");
      next = safeRatio + (Math.max(-1, Math.min(1, delta)) * 0.6);
    }
    next = Math.max(0.2, Math.min(0.8, next));
    onResize(next);
    return { ratio: next, side: side === "left" ? "left" : "right" };
  }
  useWbcEffect(function () {
    if (active === false || !window.CyreneUI.has("uiSurface")) return undefined;
    var normalizedSide = side === "left" ? "left" : "right";
    return workbenchServices.uiSurface().register({
      node_id: "pane_row_separator_" + normalizedSide,
      parent_id: "pane_workspace",
      scope: "main",
      order: normalizedSide === "left" ? 330 : 340,
      get_element: function () { return handleRef.current; },
      get_node: function () { return handleRef.current && handleRef.current.isConnected ? {
        role: "separator",
        name: wbcT("workbenchChat.resizePaneHeight", "Resize split height"),
        value_summary: String(Math.round(safeRatio * 100)),
        state: { orientation: "horizontal", side: normalizedSide, ratio: safeRatio },
      } : null; },
      actions: [
        { action_id: "adjust", kind: "adjust", risk: "R1", gesture_aliases: ["pointer_resize", "arrow_key"], input_schema: { delta_ratio: "-1..1" } },
        { action_id: "set_value", kind: "adjust", risk: "R1", gesture_aliases: ["pointer_resize"], input_schema: { value_ratio: "0..1" } },
      ],
      handlers: { adjust: semanticResize, set_value: semanticResize },
    });
  }, [active, side, ratio, onResize]);
  return (
    <div
      ref={handleRef}
      className="wbc-pane-row-resizer"
      role="separator"
      aria-orientation="horizontal"
      aria-label={wbcT("workbenchChat.resizePaneHeight", "Resize split height")}
      aria-valuenow={Math.round(safeRatio * 100)}
      tabIndex={0}
      style={{ top: seamTop }}
      onPointerDown={startResize}
      onKeyDown={keyboardResize}
    />
  );
}

function WbcPaneColumnResizer({ active, width, onResize }) {
  var handleRef = useWbcRef(null);
  function boundsFor(layout) {
    var rect = layout.getBoundingClientRect();
    // 24px outer padding + 12px card gap. Both tracks receive the exact same
    // 380px floor; on compact windows that floor shrinks symmetrically.
    var trackWidth = Math.max(0, rect.width - 36);
    var minimum = Math.min(380, trackWidth / 2);
    return {
      minimum: minimum,
      maximum: Math.max(minimum, trackWidth - minimum),
    };
  }
  function clampFor(layout, value) {
    var bounds = boundsFor(layout);
    return Math.max(bounds.minimum, Math.min(bounds.maximum, Number(value) || 520));
  }
  function startResize(event) {
    if (event.button !== 0 || !onResize) return;
    event.preventDefault();
    var handle = event.currentTarget;
    var pointerId = event.pointerId;
    var layout = event.currentTarget && event.currentTarget.closest
      ? event.currentTarget.closest(".wbc-pane-layout")
      : null;
    if (!layout) return;
    var startX = event.clientX;
    // The outer layout stays fixed throughout the gesture. Read its bounds
    // once, and keep pointer moves out of React and persistent storage.
    var bounds = boundsFor(layout);
    var startWidth = Math.max(bounds.minimum, Math.min(bounds.maximum, Number(width) || 520));
    var nextWidth = startWidth;
    var frame = 0;
    var finished = false;
    function paint() {
      frame = 0;
      layout.style.setProperty("--wbc-pane-right-width", nextWidth + "px");
    }
    function move(moveEvent) {
      if (moveEvent.pointerId !== pointerId) return;
      if (moveEvent.pointerType === "mouse" && !(moveEvent.buttons & 1)) {
        stop(moveEvent);
        return;
      }
      var next = startWidth + (startX - moveEvent.clientX);
      nextWidth = Math.max(bounds.minimum, Math.min(bounds.maximum, next));
      if (!frame) frame = requestAnimationFrame(paint);
    }
    function stop(stopEvent) {
      if (finished) return;
      if (stopEvent && Number.isFinite(stopEvent.pointerId) && stopEvent.pointerId !== pointerId) return;
      finished = true;
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", stop);
      window.removeEventListener("pointercancel", stop);
      window.removeEventListener("blur", stop);
      if (handle) handle.removeEventListener("lostpointercapture", stop);
      if (handle && handle.releasePointerCapture && handle.hasPointerCapture && handle.hasPointerCapture(pointerId)) {
        try { handle.releasePointerCapture(pointerId); } catch (error) {}
      }
      if (frame) { cancelAnimationFrame(frame); paint(); }
      document.body.classList.remove("wbc-resizing-pane-column");
      onResize(nextWidth);
      window.dispatchEvent(new CustomEvent("workbench:split-resize-end", {
        detail: { width: nextWidth },
      }));
    }
    if (handle && handle.setPointerCapture) {
      try { handle.setPointerCapture(pointerId); } catch (error) {}
      handle.addEventListener("lostpointercapture", stop, { once: true });
    }
    document.body.classList.add("wbc-resizing-pane-column");
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", stop, { once: true });
    window.addEventListener("pointercancel", stop, { once: true });
    window.addEventListener("blur", stop, { once: true });
  }
  function keyboardResize(event) {
    if (event.key !== "ArrowLeft" && event.key !== "ArrowRight") return;
    event.preventDefault();
    var layout = event.currentTarget && event.currentTarget.closest
      ? event.currentTarget.closest(".wbc-pane-layout")
      : null;
    var next = (Number(width) || 520) + (event.key === "ArrowLeft" ? 16 : -16);
    onResize(layout ? clampFor(layout, next) : next);
  }
  function semanticResize(input) {
    var handle = handleRef.current;
    var layout = handle && handle.closest ? handle.closest(".wbc-pane-layout") : null;
    if (!layout) throw new Error("pane column separator is not available");
    var bounds = boundsFor(layout);
    var current = clampFor(layout, width);
    var next;
    if (input && Number.isFinite(Number(input.value_ratio))) {
      var ratio = Math.max(0, Math.min(1, Number(input.value_ratio)));
      next = bounds.minimum + ((bounds.maximum - bounds.minimum) * ratio);
    } else {
      var delta = Number(input && input.delta_ratio);
      if (!Number.isFinite(delta)) throw new Error("delta_ratio or value_ratio is required");
      next = current + ((bounds.maximum - bounds.minimum) * Math.max(-1, Math.min(1, delta)));
    }
    next = Math.round(clampFor(layout, next));
    onResize(next);
    return { width: next, minimum: bounds.minimum, maximum: bounds.maximum };
  }
  useWbcEffect(function () {
    if (active === false || !window.CyreneUI.has("uiSurface")) return undefined;
    return workbenchServices.uiSurface().register({
      node_id: "pane_column_separator",
      parent_id: "pane_workspace",
      scope: "main",
      order: 320,
      get_element: function () { return handleRef.current; },
      get_node: function () { return handleRef.current && handleRef.current.isConnected ? {
        role: "separator",
        name: wbcT("workbenchChat.detailPanel.resize", "Resize detail panel"),
        value_summary: String(Math.round(Number(width) || 520)),
        state: { orientation: "vertical", width: Math.round(Number(width) || 520) },
      } : null; },
      actions: [
        { action_id: "adjust", kind: "adjust", risk: "R1", gesture_aliases: ["pointer_resize", "arrow_key"], input_schema: { delta_ratio: "-1..1" } },
        { action_id: "set_value", kind: "adjust", risk: "R1", gesture_aliases: ["pointer_resize"], input_schema: { value_ratio: "0..1" } },
      ],
      handlers: { adjust: semanticResize, set_value: semanticResize },
    });
  }, [active, width, onResize]);
  return (
    <div
      ref={handleRef}
      className="wbc-pane-column-resizer"
      role="separator"
      aria-orientation="vertical"
      aria-label={wbcT("workbenchChat.detailPanel.resize", "Resize detail panel")}
      aria-valuenow={Math.round(Number(width) || 520)}
      tabIndex={0}
      onPointerDown={startResize}
      onKeyDown={keyboardResize}
    />
  );
}

// Horizontal grip for a detail split: pointer capture keeps the drag alive
// across the native window boundary. Dropping it on the rail closes it,
// dropping it on either side moves it there, and click opens a menu (open the floating
// conversation panel or a new conversation, swap the side/vertical order, or
// close). Every card exposes one grip; detached chat cards keep their existing
// internal grip while the shared card frame supplies it for all other kinds.
function WbcSplitSettingSlider({ contribution, selected, onInvokeContribution }) {
  var options = Array.isArray(contribution && contribution.options) ? contribution.options : [];
  var selectedValue = String(selected == null ? "" : selected);
  var selectedIndex = options.findIndex(function (option) {
    return String(option && option.value || "") === selectedValue;
  });
  if (selectedIndex < 0) selectedIndex = 0;
  var [draftIndex, setDraftIndex] = useWbcState(selectedIndex);
  var committedValueRef = useWbcRef(selectedValue);

  useWbcEffect(function () {
    setDraftIndex(selectedIndex);
    committedValueRef.current = selectedValue;
  }, [selectedIndex, selectedValue]);

  if (!options.length) return null;
  var safeDraftIndex = Math.max(0, Math.min(options.length - 1, Number(draftIndex) || 0));
  var draftOption = options[safeDraftIndex] || options[0];
  var progress = options.length > 1 ? (safeDraftIndex / (options.length - 1)) * 100 : 0;

  function commit(index) {
    var nextIndex = Math.max(0, Math.min(options.length - 1, Number(index) || 0));
    var option = options[nextIndex];
    var nextValue = String(option && option.value || "");
    if (contribution.disabled === true || committedValueRef.current === nextValue) return;
    committedValueRef.current = nextValue;
    if (onInvokeContribution) {
      Promise.resolve(onInvokeContribution(contribution, option.value)).catch(function () {
        committedValueRef.current = selectedValue;
        setDraftIndex(selectedIndex);
      });
    }
  }

  return <div className="wbc-side-split-grip-setting-slider">
    <div className="wbc-side-split-grip-setting-heading">
      <span className="wbc-side-split-grip-setting-label">{String(contribution && contribution.label || contribution && contribution.id || "")}</span>
      <output>{String(draftOption && draftOption.label || draftOption && draftOption.value || "")}</output>
    </div>
    <input
      type="range"
      min="0"
      max={String(Math.max(0, options.length - 1))}
      step="1"
      value={String(safeDraftIndex)}
      disabled={contribution.disabled === true}
      aria-label={String(contribution && contribution.label || contribution && contribution.id || "")}
      aria-valuetext={String(draftOption && draftOption.label || draftOption && draftOption.value || "")}
      style={{ "--wbc-setting-progress": progress + "%" }}
      onChange={function (event) { setDraftIndex(Number(event.currentTarget.value) || 0); }}
      onPointerUp={function (event) { commit(event.currentTarget.value); }}
      onKeyUp={function (event) { commit(event.currentTarget.value); }}
      onBlur={function (event) { commit(event.currentTarget.value); }}
    />
    <div className="wbc-side-split-grip-setting-ticks" style={{ gridTemplateColumns: "repeat(" + options.length + ", minmax(0, 1fr))" }} aria-hidden="true">
      {options.map(function (option, index) {
        return <span className={index === safeDraftIndex ? "is-selected" : ""} key={String(option && option.value || index)}>{String(option && option.label || option && option.value || "")}</span>;
      })}
    </div>
  </div>;
}

function WbcPanelAccordionSurface({ className, role, surfaceRef, style, children }) {
  return <div ref={surfaceRef} style={style} className={"wbc-panel-accordion-surface" + (className ? " " + className : "")} role={role}>{children}</div>;
}

function WbcPanelAccordionList({ className, dataTour, children }) {
  return <div className={"wbc-panel-accordion-list" + (className ? " " + className : "")} data-tour={dataTour}>{children}</div>;
}

function WbcSplitGripAccordionBody({ expanded, children }) {
  var [rendered, setRendered] = useWbcState(!!expanded);

  useWbcEffect(function () {
    if (expanded) {
      setRendered(true);
      return undefined;
    }
    if (!rendered) return undefined;
    var hideTimer = window.setTimeout(function () { setRendered(false); }, 190);
    return function () { window.clearTimeout(hideTimer); };
  }, [expanded, rendered]);

  if (!rendered) return null;
  return <div className={"wbc-side-split-grip-expanded-body" + (expanded ? " open" : "")} aria-hidden={expanded ? "false" : "true"}>
    <div className="wbc-side-split-grip-expanded-content">{children}</div>
  </div>;
}

function WbcPanelAccordionSection({ icon, label, meta, expanded, onClick, disabled, role, checked, selected, menuItem, menuBody, bodyFlush, bodyClass, children }) {
  var expandable = children !== undefined && children !== null;
  return <section className={"wbc-side-accordion-item" + (expanded ? " expanded" : "")} role="none">
    <button
      type="button"
      className={"wbc-side-accordion-trigger" + (selected ? " is-selected" : "")}
      role={role || (menuItem ? "menuitem" : undefined)}
      aria-checked={checked}
      aria-expanded={expandable ? (expanded ? "true" : "false") : undefined}
      disabled={disabled === true}
      onClick={onClick}
    >
      <span className="wbc-side-accordion-icon" aria-hidden="true">{icon}</span>
      <span className="wbc-side-accordion-label">{label}</span>
      {meta ? <span className="wbc-side-accordion-meta">{meta}</span> : null}
      <span className="wbc-side-accordion-chevron" aria-hidden="true">{WBC_ICONS.chevronRight}</span>
    </button>
    {expandable ? (menuBody
      ? <WbcSplitGripAccordionBody expanded={!!expanded}>{children}</WbcSplitGripAccordionBody>
      : <WbcSideAccordionBody expanded={!!expanded} flush={!!bodyFlush} bodyClass={bodyClass}>{children}</WbcSideAccordionBody>
    ) : null}
  </section>;
}

function WbcSplitGripBar({ dragSource, side, onToggleSide, onClose, onOpenConversationPanel, openPanelLabel, onNewConversation, menuType, onTogglePin, pinned, onSplitPointerDown, onSplitDragStart, onSplitDragEnd, menuDisabled, menuContributions, menuState, onInvokeContribution }) {
  var [menuOpen, setMenuOpen] = useWbcState(false);
  var [expandedTab, setExpandedTab] = useWbcState("");
  var settingsOpen = expandedTab === "settings";
  var informationOpen = expandedTab !== "settings" ? expandedTab : "";
  var rootRef = useWbcRef(null);
  var gripRef = useWbcRef(null);
  var menuRef = useWbcRef(null);
  var pointerDragRef = useWbcRef(null);
  var [menuPosition, setMenuPosition] = useWbcState({ top: 0, left: 0, portalTheme: {} });
  var rootMenuContributions = (Array.isArray(menuContributions) ? menuContributions : []).filter(function (contribution) {
    return String(contribution && contribution.placement || "settings") === "root";
  });
  var settingsMenuContributions = (Array.isArray(menuContributions) ? menuContributions : []).filter(function (contribution) {
    return String(contribution && contribution.placement || "settings") !== "root";
  });

  // Electron's native browser surface is composited above renderer DOM, so a
  // CSS z-index alone cannot keep this menu visible. Reuse the shared overlay
  // coordinator: it paints an equal-sized screenshot proxy before hiding the
  // native layer, preserving the browser body's exact geometry without the
  // stretched white placeholder caused by resizing its viewport.
  useWbcEffect(function () {
    if (menuDisabled && menuOpen) setMenuOpen(false);
  }, [menuDisabled, menuOpen]);

  useWbcEffect(function () {
    if (!menuOpen && expandedTab) setExpandedTab("");
  }, [menuOpen, expandedTab]);

  useWbcEffect(function () {
    if (!menuOpen) return undefined;
    var overlays;
    try { overlays = workbenchServices.browserOverlays(); } catch (e) {}
    if (!overlays || typeof overlays.adjust !== "function") return undefined;
    overlays.adjust(1);
    return function () { overlays.adjust(-1); };
  }, [menuOpen]);

  useWbcEffect(function () {
    if (!menuOpen) return undefined;
    function closeOutside(event) {
      var insideGrip = rootRef.current && rootRef.current.contains(event.target);
      var insideMenu = menuRef.current && menuRef.current.contains(event.target);
      if (!insideGrip && !insideMenu) setMenuOpen(false);
    }
    function closeFromPlugin() { setMenuOpen(false); }
    function closeOnWindowBlur() { setMenuOpen(false); }
    function closeOnEscape(event) {
      if (event.key === "Escape") setMenuOpen(false);
    }
    document.addEventListener("pointerdown", closeOutside);
    window.addEventListener("cyrene:plugin-view-interaction", closeFromPlugin);
    window.addEventListener("blur", closeOnWindowBlur);
    document.addEventListener("keydown", closeOnEscape);
    return function () {
      document.removeEventListener("pointerdown", closeOutside);
      window.removeEventListener("cyrene:plugin-view-interaction", closeFromPlugin);
      window.removeEventListener("blur", closeOnWindowBlur);
      document.removeEventListener("keydown", closeOnEscape);
    };
  }, [menuOpen]);

  useWbcLayoutEffect(function () {
    if (!menuOpen) return undefined;
    function positionMenu() {
      var grip = gripRef.current;
      if (!grip) return;
      var rect = grip.getBoundingClientRect();
      // The popup is portalled to document.body to stay out of the Workbench
      // grid. Bridge the scoped theme tokens across that boundary so the
      // panel surface and plugin controls keep the conversation card styling.
      var portalTheme = {};
      var page = rootRef.current && rootRef.current.closest(".wbc-page");
      var grid = rootRef.current && rootRef.current.closest(".workbench-grid");
      [grid, page].forEach(function (scope) {
        if (!scope || !window.getComputedStyle) return;
        var computed = window.getComputedStyle(scope);
        for (var index = 0; index < computed.length; index += 1) {
          var propertyName = computed[index];
          if (propertyName.indexOf("--wb-") !== 0 && propertyName.indexOf("--wbc-") !== 0) continue;
          portalTheme[propertyName] = computed.getPropertyValue(propertyName);
        }
      });
      var card = rootRef.current && rootRef.current.closest(".wbc-pane-card, .wbc-side-card");
      var cardStyle = card && window.getComputedStyle ? window.getComputedStyle(card) : null;
      if (cardStyle && cardStyle.backgroundColor && cardStyle.backgroundColor !== "rgba(0, 0, 0, 0)") {
        portalTheme["--wbc-split-grip-surface"] = cardStyle.backgroundColor;
      }
      if (cardStyle && cardStyle.borderRadius) portalTheme["--wbc-split-grip-radius"] = cardStyle.borderRadius;
      if (cardStyle && cardStyle.boxShadow && cardStyle.boxShadow !== "none") {
        portalTheme["--wbc-split-grip-shadow"] = cardStyle.boxShadow;
      }
      setMenuPosition({
        top: Math.round(rect.bottom + 4),
        left: Math.round(rect.left + rect.width / 2),
        portalTheme: portalTheme
      });
    }
    positionMenu();
    window.addEventListener("resize", positionMenu);
    window.addEventListener("scroll", positionMenu, true);
    return function () {
      window.removeEventListener("resize", positionMenu);
      window.removeEventListener("scroll", positionMenu, true);
    };
  }, [menuOpen]);

  function handleKey(event) {
    if (menuDisabled) return;
    if (event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      setMenuOpen(function (open) { return !open; });
    } else if (event.key === "ArrowLeft" || event.key === "ArrowRight") {
      event.preventDefault();
      if (onToggleSide) onToggleSide();
    }
  }

  function captureDragPointer(event) {
    if (event.button !== 0) return;
    var target = event.currentTarget;
    var rect = target.getBoundingClientRect();
    target.dataset.wbcDragClientX = String(event.clientX);
    target.dataset.wbcDragClientY = String(event.clientY);
    target.dataset.wbcDragHandleX = String(event.clientX - rect.left);
    target.dataset.wbcDragHandleY = String(event.clientY - rect.top);
    pointerDragRef.current = {
      pointerId: event.pointerId,
      x: event.clientX,
      y: event.clientY,
      moved: false,
    };
    if (typeof target.setPointerCapture === "function") {
      try { target.setPointerCapture(event.pointerId); } catch (e) {}
    }
    event.preventDefault();
  }

  function trackDragPointer(event) {
    var drag = pointerDragRef.current;
    if (!drag || drag.pointerId !== event.pointerId) return;
    if (!drag.moved && Math.abs(event.clientX - drag.x) + Math.abs(event.clientY - drag.y) > 4) {
      drag.moved = true;
      // A click should only open the grip menu. Mounting the full-card drag
      // clone on pointerdown made controls briefly reflow in the clone.
      // Start the shared drag pipeline only after an intentional movement.
      if (onSplitDragStart) onSplitDragStart(event, dragSource);
    }
  }

  function releaseDragPointer(event) {
    var drag = pointerDragRef.current;
    if (!drag || drag.pointerId !== event.pointerId) return;
    var target = event.currentTarget;
    if (typeof target.releasePointerCapture === "function") {
      try { target.releasePointerCapture(event.pointerId); } catch (e) {}
    }
    target.dataset.wbcPointerDragged = drag.moved ? "true" : "false";
    pointerDragRef.current = null;
  }

  var swapLabel = wbcT("workbenchChat.splitMoveOtherSide", "Move split to the other side");
  function openConversationPanel() {
    setMenuOpen(false);
    if (onOpenConversationPanel) onOpenConversationPanel();
  }
  // Keep the fixed popup outside the Workbench's four-column grid. A fixed
  // element portalled into that grid can still inherit its static grid area in
  // Electron and stretch to the full row when an accordion section opens.
  var menuPortalRoot = typeof document !== "undefined" ? document.body : null;
  var canPortalMenu = menuOpen && !menuDisabled && menuPortalRoot && window.ReactDOM && typeof window.ReactDOM.createPortal === "function";
  return (
    <div className="wbc-split-grip-bar-host" ref={rootRef}>
      <div
        ref={gripRef}
        className="wbc-side-split-grip-bar"
        role="button"
        tabIndex={0}
        aria-label={wbcT("workbenchChat.detailPanel.move", "Move split panel")}
        title={wbcT("workbenchChat.detailPanel.move", "Move split panel")}
        onPointerDown={captureDragPointer}
        onPointerMove={trackDragPointer}
        onPointerUp={releaseDragPointer}
        onPointerCancel={releaseDragPointer}
        onClick={function (event) {
          if (event.currentTarget.dataset.wbcPointerDragged === "true") {
            event.currentTarget.dataset.wbcPointerDragged = "false";
            return;
          }
          if (!menuDisabled) setMenuOpen(function (open) { return !open; });
        }}
        onKeyDown={handleKey}
      >
        <span className="wbc-side-split-grip-bar-visual" aria-hidden="true" />
      </div>
      {canPortalMenu ? window.ReactDOM.createPortal((
        <WbcPanelAccordionSurface
          className={"wbc-side-split-grip-menu" + (Array.isArray(menuContributions) && menuContributions.length ? " has-contributions" : "")}
          role="menu"
          surfaceRef={menuRef}
          style={Object.assign({}, menuPosition.portalTheme, {
            "--wbc-split-grip-menu-top": menuPosition.top + "px",
            "--wbc-split-grip-menu-left": menuPosition.left + "px"
          })}
        >
          <WbcPanelAccordionList className="wbc-side-split-grip-accordion">
          {menuType === "content" ? (
            onNewConversation ? <WbcPanelAccordionSection
              menuItem={true}
              icon={WBC_ICONS.plus}
              label={wbcT("workbenchChat.newConversation", "New conversation")}
              onClick={function () { setMenuOpen(false); onNewConversation(); }}
            /> : null
          ) : (
            <WbcPanelAccordionSection
              menuItem={true}
              icon={WBC_ICONS.sidebar}
              label={openPanelLabel || wbcT("workbenchChat.detailPanel.openConversationPanel", "Open conversation panel")}
              onClick={openConversationPanel}
            />
          )}
          <WbcPanelAccordionSection
            menuItem={true}
            icon={<>{WBC_ICONS.chevronLeft}{WBC_ICONS.chevronRight}</>}
            label={swapLabel}
            onClick={function () { setMenuOpen(false); if (onToggleSide) onToggleSide(); }}
          />
          {onTogglePin ? (
            <WbcPanelAccordionSection
              menuItem={true}
              icon={WBC_ICONS.pin}
              label={pinned
                ? wbcT("workbenchChat.surfaceUnpin", "Unpin automatic surface")
                : wbcT("workbenchChat.surfacePin", "Pin automatic surface")}
              selected={!!pinned}
              onClick={function () { setMenuOpen(false); onTogglePin(!pinned); }}
            />
          ) : null}
          {rootMenuContributions.map(function (contribution) {
            var contributionId = String(contribution && contribution.id || "");
            var contributionKind = String(contribution && contribution.kind || "action");
            var contributionIcon = WBC_ICONS[String(contribution && contribution.icon_name || "tool")] || WBC_ICONS.tool;
            var active = Boolean(menuState && contribution.state_key && menuState[contribution.state_key]);
            var informationFields = Array.isArray(contribution && contribution.fields) ? contribution.fields : [];
            var informationDetails = informationFields.filter(function (field) { return String(field && field.group || "") !== "facts"; });
            var informationFacts = informationFields.filter(function (field) { return String(field && field.group || "") === "facts"; });
            if (contributionKind === "information") return <WbcPanelAccordionSection
                menuItem={true}
                key={contributionId}
                icon={contributionIcon}
                label={String(contribution.label || contributionId)}
                expanded={informationOpen === contributionId}
                menuBody={true}
                onClick={function () {
                  setExpandedTab(function (open) { return open === contributionId ? "" : contributionId; });
                }}
              >
              <div className="wbc-side-split-grip-information">
                <dl className="wbc-side-split-grip-information-details">
                  {informationDetails.map(function (field) {
                    return <div key={String(field && field.state_key || field && field.label || "field")}>
                      <dt>{String(field && field.label || "")}</dt>
                      <dd className={field && field.tone ? "is-" + String(field.tone) : ""}>{String(field && field.value || "—")}</dd>
                    </div>;
                  })}
                </dl>
                {informationFacts.length ? <dl className="wbc-side-split-grip-information-facts">
                  {informationFacts.map(function (field) {
                    return <div key={String(field && field.state_key || field && field.label || "field")}>
                      <dt>{String(field && field.label || "")}</dt>
                      <dd>{String(field && field.value || "—")}</dd>
                    </div>;
                  })}
                </dl> : null}
              </div>
            </WbcPanelAccordionSection>;
            return <WbcPanelAccordionSection
              menuItem={true}
              icon={contributionIcon}
              label={String(contribution.label || contributionId)}
              role={contributionKind === "toggle" ? "menuitemcheckbox" : "menuitem"}
              checked={contributionKind === "toggle" ? (active ? "true" : "false") : undefined}
              disabled={contribution.disabled === true}
              selected={active}
              key={contributionId}
              onClick={function () {
                setMenuOpen(false);
                if (onInvokeContribution) Promise.resolve(onInvokeContribution(contribution, !active)).catch(function () {});
              }}
            />;
          })}
          {settingsMenuContributions.length ? (
            <WbcPanelAccordionSection
                menuItem={true}
                icon={WBC_ICONS.settings}
                label={wbcT("workbenchChat.detailPanel.settings", "Settings")}
                expanded={settingsOpen}
                menuBody={true}
                onClick={function () {
                  setExpandedTab(function (open) { return open === "settings" ? "" : "settings"; });
                }}
              >
              <div className="wbc-side-split-grip-settings" role="group" aria-label={wbcT("workbenchChat.detailPanel.settings", "Settings")}>
                {settingsMenuContributions.map(function (contribution) {
                  var stateKey = String(contribution && contribution.state_key || "");
                  var selected = menuState && menuState[stateKey];
                  return <div className="wbc-side-split-grip-setting" key={String(contribution && contribution.id || stateKey)}>
                    {String(contribution && contribution.presentation || "") === "slider" ? <WbcSplitSettingSlider contribution={contribution} selected={selected} onInvokeContribution={onInvokeContribution} /> : <React.Fragment>
                    <span className="wbc-side-split-grip-setting-label">{String(contribution && contribution.label || contribution && contribution.id || "")}</span>
                    {String(contribution && contribution.kind || "") === "radio-group" ? <div role="group">
                      {(Array.isArray(contribution.options) ? contribution.options : []).map(function (option) {
                        var active = String(selected == null ? "" : selected) === String(option && option.value || "");
                        return <button
                          type="button"
                          role="menuitemradio"
                          aria-checked={active ? "true" : "false"}
                          disabled={contribution.disabled === true}
                          className={active ? "is-selected" : ""}
                          key={String(option && option.value || "")}
                          onClick={function () {
                            if (active) return;
                            if (onInvokeContribution) Promise.resolve(onInvokeContribution(contribution, option.value)).catch(function () {});
                          }}
                        >
                          <span aria-hidden="true">{active ? WBC_ICONS.check : null}</span>
                          <span>{String(option && option.label || option && option.value || "")}</span>
                        </button>;
                      })}
                    </div> : null}
                    </React.Fragment>}
                  </div>;
                })}
              </div>
            </WbcPanelAccordionSection>
          ) : null}
          {onClose ? (
            <WbcPanelAccordionSection
              menuItem={true}
              icon={WBC_ICONS.x}
              label={wbcT("workbenchChat.detailPanel.close", "Close split panel")}
              onClick={function () { setMenuOpen(false); onClose(); }}
            />
          ) : null}
          </WbcPanelAccordionList>
        </WbcPanelAccordionSurface>
      ), menuPortalRoot) : null}
    </div>
  );
}

function WbcSideAgentSplit({ agent, agents, project, onOpenFile, onUpdate, onSelect, onClose }) {
  var [pickerOpen, setPickerOpen] = useWbcState(false);
  var headerRef = useWbcRef(null);
  var items = Array.isArray(agents) ? agents : [];
  var title = String((agent && (agent.sourceQuote || agent.title)) || "")
    .replace(/\s+/g, " ")
    .trim();

  useWbcEffect(function () {
    if (!pickerOpen) return undefined;
    function closePicker(event) {
      if (headerRef.current && !headerRef.current.contains(event.target)) setPickerOpen(false);
    }
    document.addEventListener("pointerdown", closePicker);
    return function () { document.removeEventListener("pointerdown", closePicker); };
  }, [pickerOpen]);

  return (
    <aside className="wbc-side-agent-split" aria-label={wbcT("workbenchChat.sideAgent.conversation", "Side conversation")}>
      <header className="wbc-side-agent-split-head" ref={headerRef}>
        <button
          type="button"
          className="wbc-side-agent-split-picker"
          onClick={function () { setPickerOpen(function (open) { return !open; }); }}
          aria-expanded={pickerOpen}
          aria-haspopup="listbox"
        >
          <span className="wbc-side-agent-split-title">
          <span>{wbcT("workbenchChat.sideAgent.tab", "Side questions")}</span>
          <b title={title}>{title || wbcT("workbenchChat.sideAgent.untitled", "Side question")}</b>
          </span>
          <span className="wbc-side-agent-split-picker-chevron" aria-hidden="true">{WBC_ICONS.chevronRight}</span>
        </button>
        <button
          type="button"
          className="wbc-side-agent-split-close"
          onClick={onClose}
          title={wbcT("workbenchChat.sideAgent.closeConversation", "Close side conversation")}
          aria-label={wbcT("workbenchChat.sideAgent.closeConversation", "Close side conversation")}
        >{WBC_ICONS.x}</button>
        <WbcSplitPickerMenu open={pickerOpen} role="listbox" aria-label={wbcT("workbenchChat.sideAgent.list", "Side questions")}>
            {items.map(function (item, index) {
              var itemTitle = String(item.sourceQuote || item.title || "").replace(/\s+/g, " ").trim();
              var selected = item.id === (agent && agent.id);
              return (
                <button
                  type="button"
                  key={item.id}
                  className={selected ? "active" : ""}
                  role="option"
                  aria-selected={selected}
                  onClick={function () {
                    setPickerOpen(false);
                    if (onSelect) onSelect(item.id);
                  }}
                >
                  <span>{String(index + 1).padStart(2, "0")}</span>
                  <b>{itemTitle || wbcT("workbenchChat.sideAgent.untitled", "Side question")}</b>
                </button>
              );
            })}
        </WbcSplitPickerMenu>
      </header>
      <WbcSideAgentTab agent={agent} project={project} onOpenFile={onOpenFile} onUpdate={onUpdate} />
    </aside>
  );
}

function wbcCreateSideAgentStreamHandlers(config) {
  var mountedRef = config.mountedRef;
  var streamAttachedRef = config.streamAttachedRef;
  var agentRef = config.agentRef;

  function finishRun() {
    config.refreshAgent().finally(function () {
      streamAttachedRef.current = false;
      if (mountedRef.current) {
        config.resetStreamRuntime();
        config.setRunning(false);
      }
    });
  }

  function clearPendingQuestion() {
    if (!mountedRef.current) return;
    var current = agentRef.current;
    if (!current || !current.id) return;
    var next = { ...current, pendingQuestion: null };
    agentRef.current = next;
    config.onUpdate(next);
  }

  return {
    onTimeline: function (patch) { if (mountedRef.current) config.queueStreamAction("timeline", patch); },
    onReplyStart: function () {
      if (mountedRef.current) config.queueStreamAction("reply_start");
    },
    onReplyDelta: function (delta) {
      if (mountedRef.current) config.queueStreamAction("reply_delta", delta);
    },
    onReplyDone: function (text) {
      if (mountedRef.current) config.queueStreamAction("reply_done", text);
    },
    onNotification: function (notice) {
      if (!mountedRef.current || !notice || !notice.message) return;
      config.queueStreamAction("notification", notice);
    },
    onReasoningStart: function () { if (mountedRef.current) config.queueStreamAction("reasoning_start"); },
    onReasoningDelta: function (delta) { if (mountedRef.current) config.queueStreamAction("reasoning_delta", delta); },
    onReasoningDone: function (text) { if (mountedRef.current) config.queueStreamAction("reasoning_done", text); },
    onFinalizing: function () { if (mountedRef.current) config.queueStreamAction("finalizing"); },
    onToolStarted: function (event) { if (mountedRef.current) config.queueStreamAction("tool", event); },
    onToolUpdated: function (event) { if (mountedRef.current) config.queueStreamAction("tool", event); },
    onToolCompleted: function (event) { if (mountedRef.current) config.queueStreamAction("tool", event); },
    onArtifactEvent: function (event) { if (mountedRef.current) config.queueStreamAction("artifact", null, event); },
    onSaved: finishRun,
    onAwaitingUser: function (pending) {
      if (!mountedRef.current) return;
      if (!wbcIsLiveAgentRequest(pending)) {
        finishRun();
        return;
      }
      var current = agentRef.current;
      if (!current || !current.id) return;
      var next = { ...current, pendingQuestion: pending, status: "running" };
      agentRef.current = next;
      config.onUpdate(next);
    },
    onPermissionResolved: clearPendingQuestion,
    onElicitationResolved: clearPendingQuestion,
    onGuidanceReceived: function (event) {
      if (!mountedRef.current || !event || !event.userMessage) return;
      var current = agentRef.current;
      if (!current || !current.id) return;
      var next = {
        ...current,
        messages: wbcMergeChronologicalMessages(current.messages || [], [event.userMessage]),
      };
      agentRef.current = next;
      config.onUpdate(next);
    },
    onError: function (err) {
      if (mountedRef.current) config.setError(wbcErrorText(err));
    },
  };
}

function WbcSideAgentTab({ agent, project, onOpenFile, onUpdate }) {
  var agentRef = useWbcRef(agent); var scrollRef = useWbcRef(null);
  var mountedRef = useWbcRef(true); var streamAttachedRef = useWbcRef(false);
  var streamFrameRef = useWbcRef(0); var streamActionsRef = useWbcRef([]);
  var runStartedAtRef = useWbcRef(Date.now());
  var [running, setRunning] = useWbcState(!!(agent && agent.status === "running"));
  var [streamRuntime, setStreamRuntime] = useWbcState(null);
  var [error, setError] = useWbcState("");

  function flushStreamActions() {
    streamFrameRef.current = 0;
    var actions = streamActionsRef.current.splice(0);
    if (!mountedRef.current || !actions.length) return;
    setStreamRuntime(function (current) {
      return actions.reduce(function (runtime, item) {
        return wbcReduceDetachedRuntime(runtime, item.action, item.value, item.sourceEvent);
      }, current || wbcCreateDetachedRuntime(runStartedAtRef.current));
    });
  }

  function queueStreamAction(action, value, sourceEvent) {
    streamActionsRef.current.push({ action: action, value: value, sourceEvent: sourceEvent });
    if (!streamFrameRef.current) {
      streamFrameRef.current = requestAnimationFrame(flushStreamActions);
    }
  }

  function resetStreamRuntime() {
    if (streamFrameRef.current) cancelAnimationFrame(streamFrameRef.current);
    streamFrameRef.current = 0;
    streamActionsRef.current = [];
    if (mountedRef.current) setStreamRuntime(null);
  }

  useWbcEffect(function () {
    agentRef.current = agent;
    setRunning(!!(agent && agent.status === "running"));
  }, [agent]);

  useWbcEffect(function () {
    mountedRef.current = true;
    return function () {
      mountedRef.current = false;
      if (streamFrameRef.current) cancelAnimationFrame(streamFrameRef.current);
      streamFrameRef.current = 0;
      streamActionsRef.current = [];
    };
  }, []);

  useWbcLayoutEffect(function () {
    var el = scrollRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [agent && agent.messages && agent.messages.length, streamRuntime && streamRuntime.lastEventAt, running]);

  function refreshAgent() {
    return WorkbenchChatModel.getChat(agentRef.current.id).then(function (fresh) {
      agentRef.current = fresh;
      onUpdate(fresh);
      if (mountedRef.current) setRunning(fresh.status === "running");
      return fresh;
    });
  }

  function streamHandlers() {
    return wbcCreateSideAgentStreamHandlers({
      mountedRef: mountedRef,
      streamAttachedRef: streamAttachedRef,
      agentRef: agentRef,
      queueStreamAction: queueStreamAction,
      refreshAgent: refreshAgent,
      resetStreamRuntime: resetStreamRuntime,
      setRunning: setRunning,
      onUpdate: onUpdate,
      setError: setError,
    });
  }

  function ownStream(promise) {
    streamAttachedRef.current = true;
    promise.catch(function (err) {
      if (mountedRef.current && !(err && err.name === "AbortError")) {
        setError(wbcErrorText(err));
      }
    }).finally(function () {
      if (!streamAttachedRef.current) return;
      streamAttachedRef.current = false;
      refreshAgent().catch(function () {}).finally(function () {
        if (mountedRef.current) {
          resetStreamRuntime();
          setRunning(false);
        }
      });
    });
  }

  useWbcEffect(function () {
    if (!agent || agent.status !== "running" || streamAttachedRef.current) return undefined;
    runStartedAtRef.current = Date.now();
    setRunning(true);
    ownStream(WorkbenchChatModel.reconnectRun(agent.id, streamHandlers()));
    return undefined;
  }, [agent && agent.id, agent && agent.status]);

  function submit(payload) {
    var question = String(payload && payload.message || "").trim();
    var attachments = payload && Array.isArray(payload.attachments) ? payload.attachments : [];
    var current = agentRef.current;
    if ((!question && !attachments.length) || running || !current || !current.id) return;
    var optimistic = {
      id: "side_user_pending_" + Date.now(),
      role: "user",
      content: question,
      attachments: attachments,
      createdAt: new Date().toISOString(),
      optimistic: true,
    };
    var next = {
      ...current,
      status: "running",
      messages: (current.messages || []).concat([optimistic]),
    };
    agentRef.current = next;
    onUpdate(next);
    setError("");
    resetStreamRuntime();
    runStartedAtRef.current = Date.now();
    setStreamRuntime(wbcCreateDetachedRuntime(runStartedAtRef.current));
    setRunning(true);
    ownStream(WorkbenchChatModel.sendMessage(
      current.id,
      {
        message: question,
        attachments: attachments,
        mode: current.permissionMode || payload.mode || "default",
        model: current.modelSelectionId || payload.model || "",
        reasoningEffort: current.reasoningEffort || payload.reasoningEffort || "",
      },
      streamHandlers()
    ));
  }

  function stop() {
    if (!running) return;
    WorkbenchChatModel.interrupt(agentRef.current.id).catch(function (err) {
      if (mountedRef.current) setError(wbcErrorText(err));
    });
  }

  function guide(message) {
    var current = agentRef.current;
    var text = String(message || "").trim();
    if (!running || !current || !current.id || !text) return Promise.resolve(null);
    setError("");
    return WorkbenchChatModel.sendGuidance(
      current.id,
      text,
      "guide_" + Date.now() + "_" + Math.random().toString(36).slice(2, 8)
    ).then(function (response) {
      if (response && response.userMessage && mountedRef.current) {
        var latest = agentRef.current;
        var next = {
          ...latest,
          messages: wbcMergeChronologicalMessages(latest.messages || [], [response.userMessage]),
        };
        agentRef.current = next;
        onUpdate(next);
      }
      return response;
    }).catch(function (err) {
      if (mountedRef.current) setError(wbcErrorText(err));
      throw err;
    });
  }

  function answerPendingQuestion(questionId, optionText) {
    var current = agentRef.current;
    var pending = current && current.pendingQuestion || null;
    var formAnswer = optionText && typeof optionText === "object" && optionText.__agentForm === true;
    var answer = formAnswer ? optionText : String(optionText || "").trim();
    if (!current || !current.id || !questionId || !wbcIsLiveAgentRequest(pending) || (!formAnswer && !answer)) return;
    var response = String(pending.kind || "") === "permission.requested"
      ? { type: "option", optionId: String(answer || "") }
      : (formAnswer
        ? { type: "form", form: answer.values && typeof answer.values === "object" ? answer.values : {} }
        : { type: "text", text: String(answer || "") });
    var next = { ...current, pendingQuestion: null, status: "running" };
    agentRef.current = next;
    onUpdate(next);
    setError("");
    return WorkbenchChatModel.answerAgentRequest(current.id, questionId, response).catch(function (err) {
      if (!mountedRef.current) return;
      setError(wbcErrorText(err));
      var restored = { ...agentRef.current, pendingQuestion: pending };
      agentRef.current = restored;
      onUpdate(restored);
    });
  }

  var messages = agent && Array.isArray(agent.messages) ? agent.messages : [];
  var hasAsked = messages.some(function (message) { return message.role === "user"; });
  return (
    <section className="wbc-side-agent">
      {!hasAsked && <blockquote className="wbc-side-agent-quote">
        <span>{wbcT("workbenchChat.sideAgent.quote", "Selected text")}</span>
        <p>{agent && agent.sourceQuote}</p>
      </blockquote>}
      <div className="wbc-side-agent-thread wbc-thread" data-cyrene-revision-volatile="true" ref={scrollRef}>
        {!messages.length && !running && (
          <div className="wbc-side-agent-empty">
            <b>{wbcT("workbenchChat.sideAgent.askTitle", "Ask about this text")}</b>
            <p>{wbcT("workbenchChat.sideAgent.askHint", "This agent has its own context and will not interrupt the main conversation.")}</p>
          </div>
        )}
        <WbcTranscript messages={messages} runtime={streamRuntime} onOpenFile={onOpenFile} chatId={chatId} />
        {agent && agent.pendingQuestion && wbcIsLiveAgentRequest(agent.pendingQuestion) && (
          <WbcThreadItem><WbcQuestionPrompt pending={agent.pendingQuestion} onAnswer={answerPendingQuestion} busy={false} /></WbcThreadItem>
        )}
      </div>
      {error && <div className="wbc-side-agent-error" role="alert">{error}</div>}
      <div className="wbc-side-agent-composer-host">
        <WbcComposer
          key={"side-agent-composer:" + String(agent && agent.id || "")}
          chat={agent}
          project={project}
          runtime={streamRuntime && streamRuntime.text ? { text: streamRuntime.text } : null}
          running={running}
          onSend={submit}
          onGuidance={guide}
          onInterrupt={stop}
          draftNamespace="side-agent:"
          autoFocus={false}
          clearOnSend={true}
          error={error}
          errorKind="message"
          compact={true}
          placeholder={wbcT("workbenchChat.sideAgent.placeholder", "Ask a question about the selected text…")}
          runningPlaceholder={wbcT("workbenchChat.sideAgent.placeholderRunning", "Agent is working…")}
        />
      </div>
    </section>
  );
}

function WbcSideAgentsPanel({
  agents,
  activeAgentId,
  loading,
  onSelect,
  onDelete,
}) {
  var items = Array.isArray(agents) ? agents : [];

  if (loading && !items.length) {
    return (
      <div className="wbc-side-agent-panel-state" role="status">
        <span className="wbc-spinner" aria-hidden="true" />
        <span>{wbcT("workbenchChat.sideAgent.loading", "Loading side agents…")}</span>
      </div>
    );
  }

  if (!items.length) {
    return (
      <div className="wbc-side-agent-panel-state">
        <b>{wbcT("workbenchChat.sideAgent.empty", "No side questions")}</b>
        <span>{wbcT("workbenchChat.sideAgent.emptyHint", "Select text in the conversation to start one.")}</span>
      </div>
    );
  }

  return (
    <div className="wbc-side-agents-panel">
      <div className="wbc-side-agent-list" role="list" aria-label={wbcT("workbenchChat.sideAgent.list", "Side questions")}>
        {items.map(function (agent, index) {
          var selected = agent.id === activeAgentId;
          var preview = String(agent.sourceQuote || agent.title || "")
            .replace(/\s+/g, " ")
            .trim();
          var running = agent.status === "running";
          return (
            <div key={agent.id} className={"wbc-side-agent-index-row" + (selected ? " active" : "")} role="listitem">
              <button
                type="button"
                className="wbc-side-agent-index-select"
                onClick={function () { onSelect(agent.id); }}
                aria-current={selected ? "true" : undefined}
                title={preview}
              >
                <span className="wbc-side-agent-index-number">{String(index + 1).padStart(2, "0")}</span>
                <span className="wbc-side-agent-index-copy">
                  <b>{preview || wbcT("workbenchChat.sideAgent.untitled", "Side question")}</b>
                  <small>{running
                    ? wbcT("workbenchChat.sideAgent.thinking", "Thinking…")
                    : wbcT("workbenchChat.sideAgent.ready", "Ready")}</small>
                </span>
              </button>
              <button
                type="button"
                className="wbc-side-agent-index-close"
                onClick={function () { onDelete(agent.id); }}
                title={wbcT("workbenchChat.sideAgent.close", "Close side agent")}
                aria-label={wbcT("workbenchChat.sideAgent.close", "Close side agent")}
              >×</button>
            </div>
          );
        })}
      </div>
    </div>
  );
}

function WbcSideAccordionBody({ expanded, flush, bodyClass, children }) {
  var [mounted, setMounted] = useWbcState(expanded);
  var contentRef = useWbcRef(children);

  if (expanded) contentRef.current = children;

  useWbcEffect(function () {
    if (expanded) {
      setMounted(true);
      return undefined;
    }
    if (!mounted) return undefined;
    var timer = window.setTimeout(function () { setMounted(false); }, 190);
    return function () { window.clearTimeout(timer); };
  }, [expanded, mounted]);

  if (!mounted && !expanded) return null;
  return (
    <div className={"wbc-side-collapse" + (expanded ? " open" : " closing")} aria-hidden={!expanded}>
      <div className="wbc-side-collapse-inner">
        <div className={"wbc-side-body" + (flush ? " flush" : "") + (bodyClass ? " " + bodyClass : "")}>{contentRef.current}</div>
      </div>
    </div>
  );
}

function WbcConversationTerminalList({ terminals, loading, onSelect }) {
  var items = Array.isArray(terminals) ? terminals : [];
  if (loading && !items.length) {
    return <div className="workbench-muted wbc-side-terminal-empty">{wbcT("terminal.loading", "Loading terminals...")}</div>;
  }
  return (
    <div className="wbc-artifact-list wbc-side-terminal-list">
      {items.map(function (terminal) {
        var running = terminal.status === "running" || terminal.status === "starting";
        var title = terminal.title || wbcT("terminal.title", "Terminal");
        var statusLabel = running ? wbcT("terminal.running", "Running") : wbcT("terminal.exited", "Process exited");
        return (
          <WbcResourceListRow
            key={terminal.id}
            className="wbc-side-terminal-row"
            aria-label={title + " — " + statusLabel}
            onClick={function () { if (onSelect) onSelect(terminal.id); }}
            icon={WBC_ICONS.slash}
            iconAdornment={<i className={running ? "running" : "exited"} />}
            label={title}
            detail={running ? String(terminal.cwd || "") : statusLabel}
            detailTitle={running ? String(terminal.cwd || "") : statusLabel}
          />
        );
      })}
    </div>
  );
}

function WbcSide({
  project,
  chat,
  chatLoading,
  chatDetailed,
  chats,
  activeChatId,
  onSelectChat,
  runtime,
  subagentData,
  subagentLoading,
  onSelectSubagentRound,
  tab,
  onTabChange,
  viewerFile,
  onOpenFile,
  onSelectArtifact,
  onSelectChange,
  onSelectViewer,
  onSelectMap,
  onSelectBrowser,
  onOpenSubagents,
  workspaceAvailable,
  onOpenWorkspace,
  onViewerViewed,
  onRename,
  onDelete,
  onCompact,
  compactBusy,
  sideAgents,
  sideAgentsLoading,
  activeSideAgentId,
  onSelectSideAgent,
  onUpdateSideAgent,
  onDeleteSideAgent,
  terminals,
  terminalsLoading,
  onSelectTerminal,
  onBrowserTakeoverComplete,
  browserActiveByChat,
  browserSuppressed,
  mapAvailable,
  browserAvailable,
  onGoalChanged,
  onToggleSide,
  floating,
  widthResizable,
  onCloseFloating,
}) {
  var dataStore = workbenchServices.data();
  dataStore.useVersion();
  var pluginModules = Array.isArray(dataStore.state.pluginModules) ? dataStore.state.pluginModules : [];
  if (mapAvailable === undefined) mapAvailable = pluginModules.indexOf("map") >= 0;
  if (browserAvailable === undefined) browserAvailable = pluginModules.indexOf("browser") >= 0;
  var conversationChanges = useWbcConversationChanges(activeChatId);
  var hasWorkspaceChanges = (
    String(conversationChanges.chatId || "") === String(activeChatId || "")
    && !!conversationChanges.hasChanges
  );

  var browserState = wbcBrowserStateForChat(activeChatId);
  var browserMarkedActive = !!(browserActiveByChat && browserActiveByChat[activeChatId]);
  var browserPanelState = browserState || {};
  var hasMap = mapAvailable !== false && wbcChatUsedMap(chat, runtime);
  var hasBrowser = browserAvailable !== false && !!((browserState && browserState.active) || browserMarkedActive);
  var fileItems = wbcChatArtifactFiles(chat);
  var artifactItems = wbcChatDeliveredArtifacts(chat);
  var hasFiles = fileItems.length > 0;
  var viewerItems = fileItems;
  // viewerFile also tracks project files opened from the Files rail. Only
  // expose the Viewer row when that selection belongs to this conversation;
  // otherwise a file split leaks a phantom Viewer into unrelated chats.
  var chatViewerFile = wbcViewerFileFromItems(viewerFile, viewerItems);
  var hasBranches = useWbcMemo(function () {
    return !!wbcBranchLineage(chats, activeChatId);
  }, [chats, activeChatId]);
  var pendingPlan = wbcActivePlan(chat);
  var activeGoal = chat && chat.activeGoal && typeof chat.activeGoal === "object"
    ? chat.activeGoal : null;
  var hasSubagents = !!(
    subagentData
    && (
      (Array.isArray(subagentData.rounds) && subagentData.rounds.length)
      || (Array.isArray(subagentData.agents) && subagentData.agents.length)
    )
  );
  var tabs = [
    { id: "overview", label: wbcT("chat.side.overview", "Overview") },
  ];
  if (activeGoal) tabs.push({ id: "goal", label: wbcT("goal.tab", "Goal") });
  if (pendingPlan) tabs.push({ id: "plan", label: wbcT("chat.side.plan", "Plan") });
  if (hasSubagents) tabs.push({ id: "subagents", label: wbcT("workbenchChat.subagents", "Subagents") });
  tabs.push({ id: "context", label: wbcT("workbenchChat.context", "Context") });
  if (workspaceAvailable && onOpenWorkspace) {
    tabs.push({ id: "workspace", label: wbcT("workbenchChat.workspace", "Workspace") });
  }
  if (hasFiles) tabs.push({ id: "files", label: wbcT("workbenchChat.files", "Files") });
  if (artifactItems.length) tabs.push({ id: "artifacts", label: wbcT("workbenchChat.artifacts", "Artifacts") });
  if (hasWorkspaceChanges) {
    tabs.push({ id: "changes", label: wbcT("workbenchChat.changes", "Changes") });
  }
  if (hasBranches) tabs.push({ id: "branches", label: wbcT("chat.side.branches", "Branches") });
  if (chatViewerFile) tabs.push({ id: "viewer", label: wbcT("workbenchChat.viewer", "Viewer") });
  if (hasMap) tabs.push({ id: "map", label: wbcT("chat.side.map", "Map") });
  if (hasBrowser) tabs.push({ id: "browser", label: wbcT("chat.side.browser", "Browser") });
  var conversationTerminals = (Array.isArray(terminals) ? terminals : []).filter(function (terminal) {
    return terminal
      && String(terminal.createdBy || "") === "agent"
      && String(terminal.ownerChatId || "") === String(activeChatId || "");
  });
  if (conversationTerminals.length) {
    tabs.push({ id: "terminal", label: wbcT("terminal.title", "Terminal") });
  }
  if (sideAgents && sideAgents.length) {
    tabs.push({
      id: "side-agents",
      label: wbcT("workbenchChat.sideAgent.tab", "Side questions"),
    });
  }
  var activeTab = tabs.some(function (item) { return item.id === tab; }) ? tab : "";
  useWbcLiveChatMetrics(chat, !!runtime);
  // Keep the Context tab's two async sources warm while the panel is visible
  // so expanding the tab renders the latest data with no loading flash.
  var contextBlocks = useWbcLiveContextBlocks(chat, !!runtime);
  var inboxView = useWbcLiveInbox(chat, !!runtime);
  var flush = false;
  var sideTabMeta = {
    goal: activeGoal && Number(activeGoal.attempt || 0) > 0
      ? String(Number(activeGoal.attempt || 0))
      : "",
    plan: pendingPlan && Array.isArray(pendingPlan.steps) && pendingPlan.steps.length
      ? pendingPlan.steps.filter(function (step) { return step.status === "completed"; }).length + "/" + pendingPlan.steps.length
      : "",
    subagents: subagentData && Array.isArray(subagentData.agents) && subagentData.agents.length
      ? String(subagentData.agents.length)
      : "",
    files: hasFiles ? String(fileItems.length) : "",
    artifacts: artifactItems.length ? String(artifactItems.length) : "",
    viewer: viewerItems.length ? String(viewerItems.length) : "",
    browser: browserPanelState && Array.isArray(browserPanelState.tabs) ? String(browserPanelState.tabs.length) : "",
    terminal: conversationTerminals.length ? String(conversationTerminals.length) : "",
    "side-agents": sideAgents && sideAgents.length ? String(sideAgents.length) : "",
  };
  var activeContent = (
    <>
      {activeTab === "overview" && <WbcOverviewTab chat={chat} loading={chatLoading} detailed={chatDetailed} runtime={runtime} onRename={onRename} onDelete={onDelete} onCompact={onCompact} compactBusy={compactBusy} />}
      {activeTab === "goal" && <WbcGoalTab chat={chat} onGoalChanged={onGoalChanged} />}
      {activeTab === "plan" && <WbcPlanTab chatId={chat && chat.id || activeChatId} projectId={chat && chat.projectId || ""} plan={pendingPlan} />}
      {activeTab === "context" && <WbcContextTab chat={chat} contextBlocks={contextBlocks} inboxView={inboxView} />}
      {activeTab === "files" && <WbcArtifactsTab chat={chat} onSelectArtifact={onSelectArtifact} />}
      {activeTab === "artifacts" && <WbcArtifactsTab chat={chat} files={artifactItems} emptyKey="workbenchChat.noArtifacts" emptyFallback="This chat has not delivered any artifacts yet." onSelectArtifact={onSelectArtifact} />}
      {activeTab === "changes" && <WbcChangesTab chatId={activeChatId} changesState={conversationChanges} onSelectChange={onSelectChange} />}
      {activeTab === "branches" && <WbcBranchTab chats={chats} activeChatId={activeChatId} onSelectChat={onSelectChat} />}
      {activeTab === "viewer" && <WbcViewerList files={viewerItems} selectedFile={chatViewerFile} onSelect={onSelectViewer} />}
      {activeTab === "map" && mapAvailable !== false && <WbcMapList chatId={chat ? chat.id : ""} onSelect={onSelectMap} available={mapAvailable} />}
      {activeTab === "browser" && !browserSuppressed && (
        <WbcBrowserList browserState={browserPanelState} onSelect={onSelectBrowser} />
      )}
      {activeTab === "terminal" && (
        <WbcConversationTerminalList
          terminals={conversationTerminals}
          loading={terminalsLoading}
          onSelect={onSelectTerminal}
        />
      )}
      {activeTab === "side-agents" && (
        <WbcSideAgentsPanel
          agents={sideAgents}
          project={project}
          onOpenFile={onOpenFile}
          activeAgentId={activeSideAgentId}
          loading={sideAgentsLoading}
          onSelect={onSelectSideAgent}
          onDelete={onDeleteSideAgent}
          onUpdate={onUpdateSideAgent}
        />
      )}
    </>
  );
  return (
    <aside className={"wbc-side" + (floating ? " wbc-side-floating" : "")}>
      <WbcPanelAccordionSurface className="wbc-side-card">
        {widthResizable && React.createElement(
          workbenchServices.shell().ColResizer,
          { trackGutter: true, surfaceId: "conversation" }
        )}
        <div className="wbc-side-card-head">
          <strong>{wbcT("workbenchChat.sidePanelTitle", "Conversation panel")}</strong>
          <button
            type="button"
            className={"wbc-side-hide-btn" + (floating ? " wbc-side-floating-close" : "")}
            onClick={floating ? onCloseFloating : onToggleSide}
            title={floating
              ? wbcT("workbenchChat.closeFloatingConversationPanel", "Close floating conversation panel")
              : wbcT("workbenchChat.hideSidebar", "Hide side panel")}
            aria-label={floating
              ? wbcT("workbenchChat.closeFloatingConversationPanel", "Close floating conversation panel")
              : wbcT("workbenchChat.hideSidebar", "Hide side panel")}
          >
            {floating ? WBC_ICONS.x : WBC_ICONS.chevronsRight}
          </button>
        </div>
        <WbcPanelAccordionList className="wbc-side-accordion" dataTour="chat_sidebar">
          {tabs.map(function (item) {
            var opensSplit = item.id === "subagents" || item.id === "browser" || item.id === "workspace";
            var expanded = !opensSplit && activeTab === item.id;
            var meta = sideTabMeta[item.id] || "";
            return <WbcPanelAccordionSection
              key={item.id}
              icon={WBC_SIDE_TAB_ICONS[item.id] || WBC_SIDE_TAB_ICONS.overview}
              label={item.label}
              meta={meta}
              expanded={expanded}
              bodyFlush={flush}
              onClick={function () {
                if (opensSplit) {
                  onTabChange("");
                  if (item.id === "subagents" && onOpenSubagents) onOpenSubagents();
                  if (item.id === "workspace" && onOpenWorkspace) onOpenWorkspace();
                  if (item.id === "browser" && onSelectBrowser) {
                    var currentBrowserTab = browserPanelState && (browserPanelState.activeTabId || browserPanelState.activeTab && browserPanelState.activeTab.id);
                    onSelectBrowser(currentBrowserTab || "__active__");
                  }
                  return;
                }
                onTabChange(expanded ? "" : item.id);
              }}
            >{activeContent}</WbcPanelAccordionSection>;
          })}
        </WbcPanelAccordionList>
      </WbcPanelAccordionSurface>
    </aside>
  );
}

function wbcChangeTypeLabel(changeType) {
  if (changeType === "created") return wbcT("workbenchChat.changes.created", "Created");
  if (changeType === "deleted") return wbcT("workbenchChat.changes.deleted", "Deleted");
  return wbcT("workbenchChat.changes.modified", "Modified");
}

function WbcChangesTab({ chatId, changesState, onSelectChange }) {
  var payload = changesState && changesState.payload
    ? changesState.payload
    : { changeSets: [], fileCount: 0, additions: 0, deletions: 0 };
  var loading = !!(changesState && changesState.loading);
  var error = String(changesState && changesState.error || "");
  var [selectedSetId, setSelectedSetId] = useWbcState("");

  var changeSets = Array.isArray(payload.changeSets) ? payload.changeSets : [];
  var changeSetIds = changeSets.map(function (item) { return item.id; }).join("|");
  useWbcEffect(function () {
    setSelectedSetId(function (current) {
      return changeSets.some(function (item) { return item.id === current; })
        ? current
        : (changeSets[0] ? changeSets[0].id : "");
    });
  }, [chatId, changeSetIds]);
  var selectedSet = changeSets.find(function (item) { return item.id === selectedSetId; }) || changeSets[0] || null;
  var files = selectedSet && Array.isArray(selectedSet.files) ? selectedSet.files : [];
  return (
    <div className="wbc-changes-tab">
      {changeSets.length > 1 && (
        <div className="wbc-changes-run-picker">
          <select
            aria-label={wbcT("workbenchChat.changes.latestRun", "Latest run")}
            value={selectedSet ? selectedSet.id : ""}
            onChange={function (event) { setSelectedSetId(event.target.value); }}
          >
            {changeSets.map(function (item, index) {
              return <option value={item.id} key={item.id}>{index === 0 ? wbcT("workbenchChat.changes.latestRun", "Latest run") : wbcFormatTime(item.completedAt)}</option>;
            })}
          </select>
          <span className="wbc-changes-run-picker-chevron" aria-hidden="true">{WBC_ICONS.chevronDown}</span>
        </div>
      )}
      {loading && !changeSets.length ? (
        <p className="workbench-muted wbc-changes-state">{wbcT("workbenchChat.changes.loading", "Loading changes...")}</p>
      ) : error ? (
        <div className="wbc-changes-state"><p className="workbench-muted">{error}</p><button type="button" className="wb-btn ghost" onClick={function () { changesState.refresh(false); }}>{wbcT("workbenchChat.error.retry", "Retry")}</button></div>
      ) : !changeSets.length ? (
        <div className="wbc-changes-empty">
          <b>{wbcT("workbenchChat.changes.emptyTitle", "No agent changes yet")}</b>
          <p>{wbcT("workbenchChat.changes.emptyBody", "Files created, edited, or deleted by future agent runs will appear here automatically.")}</p>
        </div>
      ) : (
        <React.Fragment>
          <div className="wbc-resource-list wbc-changes-files">
            {files.map(function (item) {
              return (
                <button
                  type="button"
                  key={item.id || item.path}
                  className={"wbc-resource-list-row wbc-change-file " + item.changeType}
                  onClick={function () {
                    if (onSelectChange) onSelectChange({ chatId: chatId, setId: selectedSet.id, path: item.path, file: item, files: files });
                  }}
                >
                  <span className="wbc-resource-list-icon" aria-hidden="true">{WBC_SIDE_TAB_ICONS.changes}</span>
                  <span className="wbc-resource-list-copy">
                    <b className="wbc-change-file-path" title={item.path}>{item.path}</b>
                    <small><span className="wbc-change-file-status">{wbcChangeTypeLabel(item.changeType)}</span><span className="wbc-change-file-lines"><i>+{item.additions || 0}</i><em>−{item.deletions || 0}</em></span></small>
                  </span>
                  <span className="wbc-resource-list-chevron" aria-hidden="true">{WBC_ICONS.chevronRight}</span>
                </button>
              );
            })}
          </div>
        </React.Fragment>
      )}
    </div>
  );
}

// Workbench subagent panel, styled as a read-only group chat room. The main
// agent's delegated subagents appear as roster members; their inter-agent
// messages and results stream as chat bubbles. Observational only — the
// Workbench has no user→subagent send endpoint, so there is no composer.
function WbcSubagentsTab({ data, loading, onSelectRound, onClose }) {
  var rounds = data && Array.isArray(data.rounds) ? data.rounds : [];
  var agents = data && Array.isArray(data.agents) ? data.agents : [];
  var messages = data && Array.isArray(data.messages) ? data.messages : [];
  var activeRoundId = String((data && data.activeRoundId) || "");
  var [selectedAgentId, setSelectedAgentId] = useWbcState("");

  var roster = agents.map(function (agent) { return agent.id; }).join("|");
  // Default to no focused agent; reset when the round / roster changes.
  useWbcEffect(function () { setSelectedAgentId(""); }, [activeRoundId, roster]);

  var selectedAgent = agents.find(function (agent) { return agent.id === selectedAgentId; }) || null;
  var activeCount = agents.filter(function (agent) {
    return ["running", "resumed", "waiting"].indexOf(String(agent.status || "")) >= 0;
  }).length;
  function focusAgent(id) {
    setSelectedAgentId(id === selectedAgentId ? "" : id);
  }

  if (loading && !rounds.length && !agents.length) {
    return (
      <div className="wbc-subagent-page">
        <WbcSubagentHeader agentCount={0} activeCount={0} loading={true} onClose={onClose} />
        <div className="wbc-subagent-empty">
          <span className="wbc-spinner" aria-hidden="true"></span>
          <p>{wbcT("workbenchChat.subagent.loading", "Loading subagents...")}</p>
        </div>
      </div>
    );
  }
  if (!rounds.length && !agents.length) {
    return (
      <div className="wbc-subagent-page">
        <WbcSubagentHeader agentCount={0} activeCount={0} onClose={onClose} />
        <div className="wbc-subagent-empty">
          <span className="wbc-subagent-empty-glyph" aria-hidden="true">⠿</span>
          <b>{wbcT("workbenchChat.subagent.emptyTitle", "No subagents in this chat")}</b>
          <p>{wbcT("workbenchChat.subagent.emptyBody", "When the main agent delegates work, subagents and their results will appear here.")}</p>
        </div>
      </div>
    );
  }

  return (
    <div className="wbc-subagent-page">
      <WbcSubagentHeader agentCount={agents.length} activeCount={activeCount} onClose={onClose} />

      {rounds.length > 1 ? (
        <label className="wbc-subagent-round">
          <span>{wbcT("workbenchChat.subagent.round", "Round")}</span>
          <select value={activeRoundId} onChange={function (event) { onSelectRound && onSelectRound(event.target.value); }}>
            {rounds.map(function (round, index) {
              return <option key={round.id} value={round.id}>{wbcT("workbenchChat.subagent.roundNumber", "Round {n}", { n: index + 1 })}</option>;
            })}
          </select>
        </label>
      ) : null}

      <WbcSubagentRoster agents={agents} selectedId={selectedAgentId} onSelect={focusAgent} />

      {selectedAgent ? (
        <WbcSubagentSpotlight agent={selectedAgent} onClose={function () { setSelectedAgentId(""); }} />
      ) : null}

      <WbcSubagentStream
        messages={messages}
        agents={agents}
        active={activeCount > 0}
        selectedId={selectedAgentId}
        onSelectAgent={focusAgent}
      />
    </div>
  );
}

function WbcSubagentHeader({ agentCount, activeCount, loading, onClose }) {
  return (
    <header className="wbc-subagent-bar">
      <div className="wbc-subagent-bar-main">
        <b>{wbcT("workbenchChat.subagents", "Subagents")}</b>
        <span className="wbc-subagent-summary">
          {wbcT("workbenchChat.subagent.count", "{n} agents", { n: agentCount })}
        </span>
      </div>
      <div className="wbc-subagent-bar-actions">
        {!loading ? (
          <span className={"wbc-subagent-livepill " + (activeCount ? "live" : "idle")} role="status">
            <i aria-hidden="true"></i>
            {activeCount
              ? wbcT("workbenchChat.subagent.liveCount", "{n} working", { n: activeCount })
              : wbcT("workbenchChat.subagent.complete", "Complete")}
          </span>
        ) : null}
        {onClose ? (
          <button type="button" className="wbc-side-agent-split-close" onClick={onClose} aria-label={wbcT("workbenchChat.closeSubagents", "Close subagents")}>{WBC_ICONS.x}</button>
        ) : null}
      </div>
    </header>
  );
}

// Horizontal avatar strip of the round's subagents — the chat room's member list.
function WbcSubagentRoster({ agents, selectedId, onSelect }) {
  return (
    <div className="wbc-subagent-roster">
      {agents.map(function (agent) {
        var color = wbcAgentColor(agent.id);
        var statusCls = wbcSubagentStatusClass(agent.status);
        var name = agent.name || agent.id;
        return (
          <button
            key={agent.id}
            type="button"
            className={"wbc-subagent-chip" + (agent.id === selectedId ? " active" : "")}
            style={{ "--wb-agent-color": color }}
            onClick={function () { onSelect(agent.id); }}
            aria-pressed={agent.id === selectedId}
            title={name + " · " + wbcSubagentStatusText(agent.status)}
          >
            <span className="wbc-subagent-avatar" style={{ background: color }}>
              {wbcAgentInitials(name)}
              <i className={"wbc-subagent-avatar-dot " + statusCls} aria-hidden="true"></i>
            </span>
            <span className="wbc-subagent-chip-name">{name}</span>
          </button>
        );
      })}
    </div>
  );
}

// Focused-agent card: its task brief and (when available) full result.
function WbcSubagentSpotlight({ agent, onClose }) {
  var color = wbcAgentColor(agent.id);
  var name = agent.name || agent.id;
  return (
    <section className="wbc-subagent-spotlight" style={{ "--wb-agent-color": color }}>
      <header>
        <span className="wbc-subagent-avatar lg" style={{ background: color }}>{wbcAgentInitials(name)}</span>
        <div className="wbc-subagent-spotlight-id">
          <b title={name}>{name}</b>
          <span className={"wbc-subagent-status " + wbcSubagentStatusClass(agent.status)}>
            {wbcSubagentStatusText(agent.status)}
          </span>
        </div>
        <button type="button" className="wbc-subagent-spotlight-close" onClick={onClose} aria-label={wbcT("workbenchChat.subagent.close", "Close")}>×</button>
      </header>
      <div className="wbc-subagent-spotlight-body">
        <label>{wbcT("workbenchChat.subagent.task", "Task")}</label>
        <p>{agent.task || "—"}</p>
        <label>{wbcT("workbenchChat.subagent.result", "Result")}</label>
        {agent.result ? (
          <div className="markdown wbc-subagent-result" dangerouslySetInnerHTML={{ __html: wbcRenderMarkdown(agent.result) }} />
        ) : (
          <p className="workbench-muted">{wbcT("workbenchChat.subagent.resultPending", "No result yet.")}</p>
        )}
      </div>
    </section>
  );
}

// Scrolling chat transcript of inter-agent messages and results.
function WbcSubagentStream({ messages, agents, active, selectedId, onSelectAgent }) {
  var scrollRef = useWbcRef(null);
  var atBottomRef = useWbcRef(true);
  var initedRef = useWbcRef(false);

  function handleScroll() {
    var el = scrollRef.current;
    if (!el) return;
    atBottomRef.current = el.scrollTop + el.clientHeight >= el.scrollHeight - 48;
  }

  // First render jumps to the latest message; later updates only follow when the
  // reader is already near the bottom.
  useWbcLayoutEffect(function () {
    var el = scrollRef.current;
    if (!el) return;
    if (!initedRef.current) {
      initedRef.current = true;
      el.scrollTop = el.scrollHeight;
      atBottomRef.current = true;
      return;
    }
    if (atBottomRef.current) el.scrollTop = el.scrollHeight;
  }, [messages.length]);

  var nameById = {};
  var agentIds = [];
  agents.forEach(function (agent) { nameById[agent.id] = agent.name || agent.id; agentIds.push(agent.id); });

  if (!messages.length) {
    return (
      <div className="wbc-subagent-stream is-empty">
        <div className="wbc-subagent-stream-empty">
          {active ? (
            <span className="wbc-subagent-typing">
              <i></i><i></i><i></i>
              {wbcT("workbenchChat.subagent.working", "Subagents are working…")}
            </span>
          ) : (
            wbcT("workbenchChat.subagent.noActivity", "No messages recorded for this round.")
          )}
        </div>
      </div>
    );
  }

  var rows = [];
  var prevFrom = null;
  var prevTs = null;
  for (var i = 0; i < messages.length; i++) {
    var msg = messages[i];
    if (prevTs && msg.timestamp) {
      try {
        if (new Date(msg.timestamp) - new Date(prevTs) > 300000) {
          rows.push(<div className="wbc-subagent-timesep" key={"ts_" + i}><span>{wbcFormatTime(msg.timestamp)}</span></div>);
          prevFrom = null;
        }
      } catch (e) { /* unparseable timestamp — skip separator */ }
    }
    rows.push(
      <WbcSubagentBubble
        key={msg.id || i}
        msg={msg}
        name={nameById[msg.from] || msg.from}
        agentIds={agentIds}
        grouped={prevFrom === msg.from && msg.from}
        dimmed={!!selectedId && msg.from !== selectedId}
        onSelectAgent={onSelectAgent}
      />
    );
    prevFrom = msg.from;
    prevTs = msg.timestamp;
  }

  return (
    <div className="wbc-subagent-stream" ref={scrollRef} onScroll={handleScroll}>
      {rows}
    </div>
  );
}

// A single transcript entry: agent message, broadcast, result card, or system note.
function WbcSubagentBubble({ msg, name, agentIds, grouped, dimmed, onSelectAgent }) {
  var kind = String(msg.type || "message");
  var from = String(msg.from || "");
  var color = wbcAgentColor(from);
  var html = wbcHighlightMentions(wbcRenderMarkdown(msg.content || ""), agentIds);

  if (!from) {
    return <div className="wbc-subagent-syssep"><span dangerouslySetInnerHTML={{ __html: html }} /></div>;
  }

  if (kind === "result") {
    return (
      <article className={"wbc-subagent-msg result" + (dimmed ? " dimmed" : "")} style={{ "--wb-agent-color": color }}>
        <div className="wbc-subagent-msg-head">
          <span className="wbc-subagent-avatar sm" style={{ background: color }}>{wbcAgentInitials(name)}</span>
          <b>{name}</b>
          <span className="wbc-subagent-tag result">{wbcT("workbenchChat.subagent.result", "Result")}</span>
          <time>{wbcFormatTime(msg.timestamp)}</time>
        </div>
        <div className="wbc-subagent-bubble result markdown" dangerouslySetInnerHTML={{ __html: html }} />
      </article>
    );
  }

  var isBroadcast = kind === "broadcast" || String(msg.to || "") === "all";
  var toUser = String(msg.to || "") === "user";

  return (
    <article className={"wbc-subagent-msg" + (grouped ? " grouped" : "") + (dimmed ? " dimmed" : "")} style={{ "--wb-agent-color": color }}>
      {grouped ? (
        <span className="wbc-subagent-avatar-spacer" aria-hidden="true"></span>
      ) : (
        <button type="button" className="wbc-subagent-avatar sm" style={{ background: color }}
          onClick={function () { onSelectAgent && onSelectAgent(from); }} title={name}>
          {wbcAgentInitials(name)}
        </button>
      )}
      <div className="wbc-subagent-msg-body">
        {grouped ? null : (
          <div className="wbc-subagent-msg-head">
            <b style={{ color: color }}>{name}</b>
            {isBroadcast ? <span className="wbc-subagent-tag broadcast">{wbcT("workbenchChat.subagent.broadcast", "Broadcast")}</span> : null}
            {toUser ? <span className="wbc-subagent-tag touser">{wbcT("workbenchChat.subagent.toUser", "To you")}</span> : null}
            <time>{wbcFormatTime(msg.timestamp)}</time>
          </div>
        )}
        <div className="wbc-subagent-bubble markdown" dangerouslySetInnerHTML={{ __html: html }} />
      </div>
    </article>
  );
}

// Prefer the durable chat plan. Fall back to the pending confirmation payload
// during the small window before the chat record is re-fetched.
function wbcActivePlan(chat) {
  var active = chat && chat.activePlan;
  if (active && typeof active === "object") return active;
  var pq = chat && chat.pendingQuestion;
  var plan = pq && pq.plan;
  if (!plan || typeof plan !== "object") return null;
  var hasSteps = (Array.isArray(plan.steps) && plan.steps.length > 0)
    || (Array.isArray(plan.entries) && plan.entries.length > 0);
  return (plan.title || plan.summary || hasSteps) ? plan : null;
}

// Right-panel 计划 tab — durable from proposal through execution completion.
function WbcPlanTab({ chatId, projectId, plan }) {
  return <ConversationPlanTimeline
    chatId={chatId}
    projectId={projectId}
    plan={plan}
    className="workbench-side-stack wbc-conversation-plan-timeline"
  />;
}

function wbcZoomAnchorRestorer(container, oldScale, clientX, clientY) {
  if (!container) return function () {};
  var rect = container.getBoundingClientRect();
  var anchorX = Number.isFinite(clientX) ? clientX - rect.left : rect.width / 2;
  var anchorY = Number.isFinite(clientY) ? clientY - rect.top : rect.height / 2;
  anchorX = Math.max(0, Math.min(rect.width, anchorX));
  anchorY = Math.max(0, Math.min(rect.height, anchorY));
  var safeOldScale = Math.max(0.0001, Number(oldScale) || 1);
  var contentX = (container.scrollLeft + anchorX) / safeOldScale;
  var contentY = (container.scrollTop + anchorY) / safeOldScale;
  return function (newScale) {
    var safeNewScale = Math.max(0.0001, Number(newScale) || 1);
    window.requestAnimationFrame(function () {
      container.scrollLeft = Math.max(0, contentX * safeNewScale - anchorX);
      container.scrollTop = Math.max(0, contentY * safeNewScale - anchorY);
    });
  };
}

// ---- PDF.js viewer (replaces <embed> for PDF files) -------------------------

export { WBC_PROJECT_FILE_DRAFTS, WbcArtifactSplit, WbcArtifactSplitHost, WbcBrowserSplit, WbcBrowserSplitHost, WbcChangeSplit, WbcChangeSplitHost, WbcChatSplit, WbcChatSplitHost, WbcMapPaneContent, WbcMapSplitHost, WbcPaneCardFrame, WbcPaneColumnResizer, WbcPaneContextTrackDropSurface, WbcPaneRowResizer, WbcSide, WbcSideAgentSplit, WbcSideAgentSplitHost, WbcSplitGripBar, WbcSubagentsSplitHost, WbcSubagentsTab, useWbcMapData, wbcArtifactFileKey, wbcCanEditProjectTextFile, wbcChatArtifactFiles, wbcDiscardProjectFileDraft, wbcEditableChatFileResource, wbcMapItemKey, wbcMapItemLabel, wbcProjectFileDraftKey, wbcProjectFileEditUrl, wbcZoomAnchorRestorer }
