/** Pure Workbench-chat behaviors shared by the UI and native Node tests. */

const CONTEXT_BLOCK_COLOR_BY_ID = Object.freeze({
  "system.identity": 0,
  "context.memory": 1,
  "system.behavior": 2,
  "system.tools": 3,
  "context.learned_skills": 4,
  "context.persona": 5,
  "system.workspace": 6,
  "system.message_overhead": 7,
})

const CONTEXT_BLOCK_COLOR_BY_TYPE = Object.freeze({
  identity: 0,
  memory: 1,
  instructions: 2,
  tools: 3,
  workspace: 6,
  runtime: 5,
  system: 6,
  overhead: 7,
})

export function contextBlockColorIndex(block) {
  const id = String(block?.id || "")
  if (Object.prototype.hasOwnProperty.call(CONTEXT_BLOCK_COLOR_BY_ID, id)) {
    return CONTEXT_BLOCK_COLOR_BY_ID[id]
  }
  const type = String(block?.type || "")
  return Object.prototype.hasOwnProperty.call(CONTEXT_BLOCK_COLOR_BY_TYPE, type)
    ? CONTEXT_BLOCK_COLOR_BY_TYPE[type]
    : 7
}

export function toolPresentationKind(entry) {
  const raw = String(entry?.presentation?.kind || "").trim().toLowerCase()
  return ["terminal", "file", "diff", "browser", "error", "event"].includes(raw)
    ? raw
    : "generic"
}

export function normalizePermissionMode(value, fallback, validModeIds) {
  const allowed = new Set((validModeIds || []).map((item) => String(item)))
  const normalized = String(value || "").trim().toLowerCase()
  if (allowed.has(normalized)) return normalized
  const safeFallback = String(fallback || "default").trim().toLowerCase()
  return allowed.has(safeFallback) ? safeFallback : "default"
}

export function permissionOptionLabel(option, index, total, translate) {
  const t = typeof translate === "function" ? translate : (_key, fallback) => fallback
  const semanticValues = option && typeof option === "object"
    ? [option.optionId, option.id, option.kind, option.value, option.label, option.title]
    : [option]
  const semantics = semanticValues.map((value) => (
    String(value || "").trim().toLowerCase().replace(/[\s-]+/g, "_")
  )).filter(Boolean)
  if (semantics.some((value) => [
    "allow_once", "once", "同意一次", "仅这次允许", "允许这次", "允许一次",
  ].includes(value))) {
    return t("workbenchChat.permissionOnce", "Allow once")
  }
  if (semantics.some((value) => [
    "allow_always", "always_allow", "always", "always_allow_globally", "始终允许", "总是允许",
  ].includes(value))) {
    return t("workbenchChat.permissionAlways", "Always allow")
  }
  if (semantics.some((value) => [
    "allow_session", "allow_for_this_session", "always_allow_this_session", "session",
    "在本次会话同意", "本次会话内总是允许", "本次会话允许", "本轮总是允许",
  ].includes(value))) {
    return t("workbenchChat.permissionSession", "Allow for this session")
  }
  if (semantics.some((value) => ["reject", "deny", "denied", "拒绝"].includes(value))) {
    return t("workbenchChat.reject", "Reject")
  }
  if (option && typeof option === "object" && String(option.label || option.title || "").trim()) {
    return String(option.label || option.title).trim()
  }
  if (total <= 2) {
    return index === 0
      ? t("workbenchChat.permissionOnce", "Allow once")
      : t("workbenchChat.reject", "Reject")
  }
  if (index === 0) return t("workbenchChat.permissionSession", "Allow for this session")
  if (index === 1) return t("workbenchChat.permissionOnce", "Allow once")
  return t("workbenchChat.reject", "Reject")
}

export function moveChatOrderBlock(order, movingIds, targetIds, edge) {
  const current = (Array.isArray(order) ? order : []).map(String)
  const movingSet = new Set((Array.isArray(movingIds) ? movingIds : []).map(String))
  const movingBlock = current.filter((id) => movingSet.has(id))
  if (!movingBlock.length) return current

  const withoutMoving = current.filter((id) => !movingSet.has(id))
  const targetSet = new Set((Array.isArray(targetIds) ? targetIds : [])
    .map(String)
    .filter((id) => !movingSet.has(id)))
  const targetBlock = withoutMoving.filter((id) => targetSet.has(id))
  if (!targetBlock.length) {
    return edge === "before"
      ? movingBlock.concat(withoutMoving)
      : withoutMoving.concat(movingBlock)
  }

  const firstTargetIndex = withoutMoving.findIndex((id) => targetSet.has(id))
  const targetAnchor = withoutMoving.slice(0, firstTargetIndex)
    .filter((id) => !targetSet.has(id)).length
  const withoutEither = withoutMoving.filter((id) => !targetSet.has(id))
  withoutEither.splice(targetAnchor, 0, ...targetBlock)
  const insertionIndex = targetAnchor + (edge === "after" ? targetBlock.length : 0)
  withoutEither.splice(insertionIndex, 0, ...movingBlock)
  return withoutEither
}

export function resolveRefreshedChatSelection(
  list,
  selectId,
  selectionAtRequest,
  liveSelectionId,
) {
  const chats = Array.isArray(list) ? list : []
  const requestedId = String(selectId || "")
  const liveId = String(liveSelectionId || "")
  if (requestedId) {
    return chats.some((chat) => String(chat.id || "") === requestedId)
      ? requestedId
      : (chats[0] ? String(chats[0].id || "") : "")
  }
  if (liveId !== String(selectionAtRequest || "")) return null
  if (liveId && chats.some((chat) => String(chat.id || "") === liveId)) return null
  return chats[0] ? String(chats[0].id || "") : ""
}

export function settleChatListItem(chat, status, event, now = () => new Date().toISOString()) {
  if (!chat) return chat
  const terminalStatus = String(status || "completed")
  const payload = event && typeof event === "object" ? event : {}
  const runId = String(payload.runId || payload.run_id || "")
  const completedAt = String(payload.timestamp || now())
  const lastRun = chat.lastRun && typeof chat.lastRun === "object"
    ? { ...chat.lastRun }
    : {}
  if (runId) lastRun.id = runId
  lastRun.status = terminalStatus === "completed" ? "done" : terminalStatus
  lastRun.completedAt = completedAt
  return {
    ...chat,
    status: "idle",
    runStatus: terminalStatus,
    agentBusy: null,
    lastRun,
  }
}
