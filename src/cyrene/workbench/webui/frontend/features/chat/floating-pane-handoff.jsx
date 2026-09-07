import { promoteSplitConversation, restoreSplitConversation } from "./pane-promotion-transition.jsx"
import { wbcClampSideSplitWidthForPage } from "../../workbench-chat.jsx"

// Own the reversible conversation handoff: snapshot, commit, restore and abandonment.
export function beginFloatingPanelSplit(context, openSplit, sourceChatId, sourceChatSnapshot) {
  var { activeChatIdRef, pageRef, chatCache, floatingSplitRestoreRef, activeChat, splitSide, sideAgentSplitWidth, splitStateSnapshot, setFloatingConversationPanelOpen, setActiveChat, setChatLoading, selectChat, setSideAgentSplitWidth, setSplitSideDirect, restoreSplitState } = context;
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
  promoteSplitConversation({ sourceId, activeId, pageRef, commitPromotionNow });
}

export function restoreFloatingPanelSplit(context) {
  var { activeChatIdRef, pageRef, chatCache, floatingSplitRestoreRef, activeChat, splitSide, sideAgentSplitWidth, splitStateSnapshot, setFloatingConversationPanelOpen, setActiveChat, setChatLoading, selectChat, setSideAgentSplitWidth, setSplitSideDirect, restoreSplitState } = context;
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
  return restoreSplitConversation({ snapshot, pageRef, commitRestoreNow });
}


export function abandonFloatingPaneHandoff(context, activeChatId) {
    var { floatingSplitRestoreRef, setSideAgentSplitWidth, pageRef, restoreSplitState } = context;
    var snapshot = floatingSplitRestoreRef.current;
    if (!snapshot || snapshot.chatId === String(activeChatId || "")) return;
    floatingSplitRestoreRef.current = null;
    setSideAgentSplitWidth(wbcClampSideSplitWidthForPage(snapshot.splitWidth, pageRef.current));
    restoreSplitState(snapshot.activeChatId, snapshot.activeSplit);
    if (snapshot.chatId !== snapshot.activeChatId) {
      restoreSplitState(snapshot.chatId, snapshot.sourceSplit);
    }
}
