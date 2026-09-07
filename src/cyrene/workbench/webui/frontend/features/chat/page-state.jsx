import { useWbcEffect, useWbcRef, useWbcState, wbcLoadDraftAgentBinding, wbcSaveDraftAgentBinding } from "../../workbench-chat.jsx"

// Request ownership remains with the page sequencer. Only matching project
// projections may populate the shared cache. Keep notification dependencies
// tied to data changes, so a project switch cannot publish the old list again.
export function useWbcChatProjections(projectId, chatCache, onChatsChange) {
  var [chats, setChats] = useWbcState([]);
  var chatsRef = useWbcRef([]);
  var chatsProjectIdRef = useWbcRef("");
  var [activeChat, setActiveChat] = useWbcState(null);
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
  return { chats, setChats, chatsRef, chatsProjectIdRef, activeChat, setActiveChat };
}

export function useWbcDraftAgentBinding(projectId, agentsAvailable) {
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
  return { draftAgentBinding, setDraftAgentBinding, draftAgentBindingRef, handleDraftAgentChange };
}

export function useWbcSplitSide() {
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
  return { splitSide, toggleSplitSide, setSplitSideDirect };
}
