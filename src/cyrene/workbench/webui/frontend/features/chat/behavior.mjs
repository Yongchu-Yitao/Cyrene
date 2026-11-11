/** Pure Workbench-chat behaviors shared by the UI and native Node tests. */

const CONTEXT_BLOCK_COLOR_BY_ID = Object.freeze({
  "system.identity": 0,
  "context.memory": 1,
  "system.behavior": 2,
  "system.tools": 3,
  "system.task_context": 6,
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
  const kind = option?.decision === "deny"
    ? "deny"
    : option?.decision === "allow"
      ? `allow_${option.scope}`
      : String(option?.kind || option?.optionId || option?.id || "")
  const labels = {
    allow_once: ["workbenchChat.permissionOnce", "Allow once"],
    allow_session: ["workbenchChat.permissionSession", "Allow for this session"],
    allow_always: ["workbenchChat.permissionAlways", "Always allow"],
    reject_once: ["workbenchChat.reject", "Reject"],
    reject_always: ["workbenchChat.reject", "Reject"],
    reject: ["workbenchChat.reject", "Reject"],
    deny: ["workbenchChat.reject", "Reject"],
  }
  if (labels[kind]) return t(...labels[kind])
  return String(typeof option === "string" ? option : option?.label || option?.title || "")

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
