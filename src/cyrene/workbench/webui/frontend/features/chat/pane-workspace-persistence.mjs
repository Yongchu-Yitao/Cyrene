const WBC_PANE_WORKSPACE_STORAGE_PREFIX = "wbc-pane-workspace:";
const WBC_PANE_WORKSPACE_SCHEMA_VERSION = 1;
const WBC_PANE_WORKSPACE_MAX_PAYLOAD_LENGTH = 64 * 1024;

function wbcPaneWorkspaceStorageKey(projectId) {
  var normalizedProjectId = String(projectId || "");
  return normalizedProjectId
    ? WBC_PANE_WORKSPACE_STORAGE_PREFIX + normalizedProjectId
    : "";
}

function wbcPaneWorkspacePlainPayload(value) {
  if (value == null || typeof value !== "object") return null;
  try {
    var encoded = JSON.stringify(value);
    if (!encoded || encoded.length > WBC_PANE_WORKSPACE_MAX_PAYLOAD_LENGTH) return null;
    return JSON.parse(encoded);
  } catch (error) {
    return null;
  }
}

function wbcPaneWorkspaceCard(value, activeChatId) {
  var card = value && typeof value === "object" ? value : null;
  var kind = String(card && card.kind || "");
  if (["chat", "terminal", "file", "viewer"].indexOf(kind) < 0) return null;
  var payload = null;
  if (kind === "chat" || kind === "terminal") {
    payload = String(card.payload || "");
    if (!payload) return null;
  } else {
    payload = wbcPaneWorkspacePlainPayload(card.payload);
    if (!payload) return null;
  }
  var fallbackId = kind === "chat" || kind === "terminal"
    ? kind + ":" + payload : "";
  var id = String(card.id || fallbackId);
  if (!id) return null;
  return {
    id: id,
    kind: kind,
    payload: payload,
    ownerChatId: String(activeChatId || ""),
  };
}

function wbcPaneWorkspaceLayout(value, activeChatId) {
  var layout = value && typeof value === "object" ? value : {};
  var left = (Array.isArray(layout.left) ? layout.left : []).slice(0, 2).map(function (card) {
    return wbcPaneWorkspaceCard(card, activeChatId);
  }).filter(Boolean);
  var right = (Array.isArray(layout.right) ? layout.right : []).slice(0, 2).map(function (card) {
    return wbcPaneWorkspaceCard(card, activeChatId);
  }).filter(Boolean);
  if (!left.concat(right).some(function (card) { return card.kind === "terminal"; })) return null;
  if (!left.length && right.length) { left = right; right = []; }
  return {
    left: left,
    right: right,
    leftRatio: Math.max(0.2, Math.min(0.8, Number(layout.leftRatio) || 0.5)),
    rightRatio: Math.max(0.2, Math.min(0.8, Number(layout.rightRatio) || 0.5)),
  };
}

function wbcReadPaneWorkspace(projectId) {
  var key = wbcPaneWorkspaceStorageKey(projectId);
  if (!key) return null;
  try {
    var stored = JSON.parse(localStorage.getItem(key) || "null");
    if (!stored || Number(stored.schemaVersion) !== WBC_PANE_WORKSPACE_SCHEMA_VERSION) return null;
    var activeChatId = String(stored.activeChatId || "");
    var layout = wbcPaneWorkspaceLayout(stored.layout, activeChatId);
    return layout ? { activeChatId: activeChatId, layout: layout } : null;
  } catch (error) {
    return null;
  }
}

function wbcWritePaneWorkspace(projectId, activeChatId, layout) {
  var key = wbcPaneWorkspaceStorageKey(projectId);
  if (!key) return false;
  var normalizedChatId = String(activeChatId || "");
  var normalizedLayout = wbcPaneWorkspaceLayout(layout, normalizedChatId);
  try {
    if (!normalizedLayout) {
      localStorage.removeItem(key);
      return false;
    }
    localStorage.setItem(key, JSON.stringify({
      schemaVersion: WBC_PANE_WORKSPACE_SCHEMA_VERSION,
      activeChatId: normalizedChatId,
      layout: normalizedLayout,
    }));
    return true;
  } catch (error) {
    return false;
  }
}

function wbcClearPaneWorkspace(projectId) {
  var key = wbcPaneWorkspaceStorageKey(projectId);
  if (!key) return;
  try { localStorage.removeItem(key); } catch (error) {}
}

function wbcValidatePaneWorkspace(snapshot, chats, terminals) {
  if (!snapshot || !snapshot.layout) return null;
  var chatIds = new Set((Array.isArray(chats) ? chats : []).map(function (chat) {
    return String(chat && chat.id || "");
  }).filter(Boolean));
  var terminalIds = new Set((Array.isArray(terminals) ? terminals : []).map(function (terminal) {
    return String(terminal && terminal.id || "");
  }).filter(Boolean));
  var activeChatId = String(snapshot.activeChatId || "");
  if (activeChatId && !chatIds.has(activeChatId)) return null;
  function validCard(card) {
    if (card.kind === "terminal") return terminalIds.has(String(card.payload || ""));
    if (card.kind === "chat") return chatIds.has(String(card.payload || ""));
    return card.kind === "file" || card.kind === "viewer";
  }
  var layout = {
    left: (snapshot.layout.left || []).filter(validCard),
    right: (snapshot.layout.right || []).filter(validCard),
    leftRatio: snapshot.layout.leftRatio,
    rightRatio: snapshot.layout.rightRatio,
  };
  var normalizedLayout = wbcPaneWorkspaceLayout(layout, activeChatId);
  return normalizedLayout
    ? { activeChatId: activeChatId, layout: normalizedLayout }
    : null;
}

export {
  WBC_PANE_WORKSPACE_STORAGE_PREFIX,
  wbcClearPaneWorkspace,
  wbcPaneWorkspaceStorageKey,
  wbcReadPaneWorkspace,
  wbcValidatePaneWorkspace,
  wbcWritePaneWorkspace,
}
