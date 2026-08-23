import { WbcVoice, wbcErrorText } from "../../workbench-chat.jsx"
import { wbcArtifactFileKey } from "./split-pane.jsx"

function wbcDeleteSplitEntry(setter, chatId) {
  setter(function (current) {
    if (!current[chatId]) return current;
    var updated = Object.assign({}, current);
    delete updated[chatId];
    return updated;
  });
}

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
      context.setSideAgentSplitByChat(function (openByChat) {
        if (openByChat[parentChatId] !== id) return openByChat;
        var updated = Object.assign({}, openByChat);
        delete updated[parentChatId];
        return updated;
      });
      if (!next.length) context.setSideTab("");
      return next;
    });
  }).catch(function (error) {
    context.setErrorKind("message");
    context.setError(wbcErrorText(error));
  });
}

function wbcClearOtherSplits(context, chatId, activeSetter) {
  [
    ["side-agent", context.setSideAgentSplitByChat],
    ["artifact", context.setArtifactSplitByChat],
    ["change", context.setChangeSplitByChat],
    ["resource", context.setResourceSplitByChat],
  ].forEach(function (entry) {
    if (entry[0] !== activeSetter) wbcDeleteSplitEntry(entry[1], chatId);
  });
}

function wbcSelectSideAgent(context, agentId) {
  var chatId = String(context.activeChatIdRef.current || "");
  var id = String(agentId || "");
  if (!chatId || !id) return;
  context.setActiveSideAgentByChat(function (current) { return Object.assign({}, current, { [chatId]: id }); });
  context.setSideAgentSplitByChat(function (current) { return Object.assign({}, current, { [chatId]: id }); });
  wbcClearOtherSplits(context, chatId, "side-agent");
  context.openPaneContent("side-agent", id, { side: "right" });
}

function wbcSelectArtifact(context, file) {
  var chatId = String(context.activeChatIdRef.current || "");
  var key = wbcArtifactFileKey(file);
  if (!chatId || !key) return;
  context.setArtifactSplitByChat(function (current) { return Object.assign({}, current, { [chatId]: key }); });
  wbcClearOtherSplits(context, chatId, "artifact");
  context.openPaneContent("file", file, { side: "right" });
}

function wbcSelectChange(context, change) {
  var chatId = String(context.activeChatIdRef.current || "");
  if (!chatId || !change || !change.setId || !change.path) return;
  context.setChangeSplitByChat(function (current) { return Object.assign({}, current, { [chatId]: change }); });
  wbcClearOtherSplits(context, chatId, "change");
  context.openPaneContent("change", change, { side: "right" });
}

function wbcSelectResourceSplit(context, type, payload, skipPane) {
  var chatId = String(context.activeChatIdRef.current || "");
  if (!chatId || !type) return;
  context.setResourceSplitByChat(function (current) {
    return Object.assign({}, current, { [chatId]: { type: type, payload: payload } });
  });
  wbcClearOtherSplits(context, chatId, "resource");
  if (!skipPane) context.openPaneContent(type, payload, { side: "right" });
}

function wbcSplitStateSnapshot(context, chatId) {
  return {
    sideAgentId: context.sideAgentSplitByChat[chatId] || "",
    artifactKey: context.artifactSplitByChat[chatId] || "",
    change: context.changeSplitByChat[chatId] || null,
    resource: context.resourceSplitByChat[chatId] || null,
  };
}

function wbcRestoreSplitState(context, chatId, snapshot) {
  if (!chatId || !snapshot) return;
  function restoreEntry(setter, value) {
    setter(function (current) {
      var updated = Object.assign({}, current);
      if (value) updated[chatId] = value;
      else delete updated[chatId];
      return updated;
    });
  }
  restoreEntry(context.setSideAgentSplitByChat, snapshot.sideAgentId);
  restoreEntry(context.setArtifactSplitByChat, snapshot.artifactKey);
  restoreEntry(context.setChangeSplitByChat, snapshot.change);
  restoreEntry(context.setResourceSplitByChat, snapshot.resource);
}

function wbcCloseNamedSplit(context, setter) {
  context.setFloatingConversationPanelOpen(false);
  if (context.restoreFloatingPanelSplit()) return;
  var chatId = String(context.activeChatIdRef.current || "");
  if (chatId) wbcDeleteSplitEntry(setter, chatId);
}

function wbcCloseResourceSplit(context) {
  context.setFloatingConversationPanelOpen(false);
  if (context.restoreFloatingPanelSplit()) return;
  var chatId = String(context.activeChatIdRef.current || "");
  if (!chatId) return;
  var closingViewer = !!(context.resourceSplitByChat[chatId]
    && context.resourceSplitByChat[chatId].type === "viewer");
  wbcDeleteSplitEntry(context.setResourceSplitByChat, chatId);
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
  wbcDeleteSplitEntry(context.setResourceSplitByChat, sourceChatId);
  context.selectChat(targetChatId);
}

function wbcCloseActiveSplit(context) {
  context.setFloatingConversationPanelOpen(false);
  if (context.restoreFloatingPanelSplit()) return;
  wbcCloseNamedSplit(context, context.setSideAgentSplitByChat);
  wbcCloseNamedSplit(context, context.setArtifactSplitByChat);
  wbcCloseNamedSplit(context, context.setChangeSplitByChat);
  wbcCloseResourceSplit(context);
}

export {
  wbcCloseActiveSplit, wbcCloseMainConversationSplit, wbcCloseNamedSplit,
  wbcCloseResourceSplit, wbcDeleteSideAgent, wbcRestoreSplitState,
  wbcSelectArtifact, wbcSelectChange, wbcSelectResourceSplit, wbcSelectSideAgent,
  wbcSplitStateSnapshot, wbcUpdateSideAgent,
}
