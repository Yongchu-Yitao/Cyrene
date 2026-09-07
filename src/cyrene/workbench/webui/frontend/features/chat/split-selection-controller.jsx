import { WbcVoice, wbcErrorText } from "../../workbench-chat.jsx"
import { wbcArtifactFileKey } from "./split-pane.jsx"

function wbcUpdateSideAgent(context, nextAgent) {
  if (!nextAgent || !nextAgent.id) return;
  context.setSideAgents(function (current) {
    return current.map(function (item) { return item.id === nextAgent.id ? nextAgent : item; });
  });
}

function wbcDeleteSideAgent(context, agentId) {
  var id = String(agentId || "");
  if (!id) return Promise.resolve();
  WbcVoice.stop();
  return context.model.deleteChat(id).then(function () {
    context.setSideAgents(function (current) {
      var next = current.filter(function (item) { return item.id !== id; });
      var parentChatId = String(context.activeChatIdRef.current || "");
      context.setActiveSideAgentByChat(function (selection) {
        if (selection[parentChatId] !== id) return selection;
        var updated = Object.assign({}, selection);
        if (next.length) updated[parentChatId] = next[next.length - 1].id;
        else delete updated[parentChatId];
        return updated;
      });
      context.splitSelection.close(parentChatId, "side-agent", id);
      if (!next.length) context.setSideTab("");
      return next;
    });
  }).catch(function (error) {
    context.setErrorKind("message");
    context.setError(wbcErrorText(error));
  });
}

function wbcSelectSideAgent(context, agentId) {
  var chatId = String(context.activeChatIdRef.current || "");
  var id = String(agentId || "");
  if (!chatId || !id) return;
  context.setActiveSideAgentByChat(function (current) { return Object.assign({}, current, { [chatId]: id }); });
  context.splitSelection.select(chatId, "side-agent", id);
  context.openPaneContent("side-agent", id, { side: "right" });
}

function wbcSelectArtifact(context, file) {
  var chatId = String(context.activeChatIdRef.current || "");
  var key = wbcArtifactFileKey(file);
  if (!chatId || !key) return;
  context.splitSelection.select(chatId, "artifact", key);
  context.openPaneContent("file", file, { side: "right" });
}

function wbcSelectChange(context, change) {
  var chatId = String(context.activeChatIdRef.current || "");
  if (!chatId || !change || !change.setId || !change.path) return;
  context.splitSelection.select(chatId, "change", change);
  context.openPaneContent("change", change, { side: "right" });
}

function wbcSelectResourceSplit(context, type, payload, skipPane) {
  var chatId = String(context.activeChatIdRef.current || "");
  if (!chatId || !type) return;
  context.splitSelection.select(chatId, "resource", { type: type, payload: payload });
  if (!skipPane) context.openPaneContent(type, payload, { side: "right" });
}

function wbcSplitStateSnapshot(context, chatId) {
  return context.splitSelection.snapshot(chatId);
}

function wbcRestoreSplitState(context, chatId, snapshot) {
  context.splitSelection.restore(chatId, snapshot);
}

function wbcCloseNamedSplit(context, kind) {
  context.setFloatingConversationPanelOpen(false);
  if (context.restoreFloatingPanelSplit()) return;
  var chatId = String(context.activeChatIdRef.current || "");
  if (chatId) context.splitSelection.close(chatId, kind);
}

function wbcCloseResourceSplit(context) {
  context.setFloatingConversationPanelOpen(false);
  if (context.restoreFloatingPanelSplit()) return;
  var chatId = String(context.activeChatIdRef.current || "");
  if (!chatId) return;
  var closingViewer = !!(context.resourceSplitByChat[chatId]
    && context.resourceSplitByChat[chatId].type === "viewer");
  context.splitSelection.close(chatId, "resource");
  if (closingViewer) {
    context.setViewerFile(null);
    context.setSideTab(function (current) { return current === "viewer" ? "" : current; });
  }
}

function wbcCloseMainConversationSplit(context) {
  context.setFloatingConversationPanelOpen(false);
  var sourceChatId = String(context.activeChatIdRef.current || "");
  var targetChatId = String(context.splitChatId || "");
  if (!sourceChatId || !targetChatId || sourceChatId === targetChatId) {
    context.closeActiveSplit();
    return;
  }
  context.splitSelection.close(sourceChatId, "resource");
  context.selectChat(targetChatId);
}

function wbcCloseActiveSplit(context) {
  context.setFloatingConversationPanelOpen(false);
  if (context.restoreFloatingPanelSplit()) return;
  wbcCloseNamedSplit(context, "side-agent");
  wbcCloseNamedSplit(context, "artifact");
  wbcCloseNamedSplit(context, "change");
  wbcCloseResourceSplit(context);
}

export {
  wbcCloseActiveSplit, wbcCloseMainConversationSplit, wbcCloseNamedSplit,
  wbcCloseResourceSplit, wbcDeleteSideAgent, wbcRestoreSplitState,
  wbcSelectArtifact, wbcSelectChange, wbcSelectResourceSplit, wbcSelectSideAgent,
  wbcSplitStateSnapshot, wbcUpdateSideAgent,
}
