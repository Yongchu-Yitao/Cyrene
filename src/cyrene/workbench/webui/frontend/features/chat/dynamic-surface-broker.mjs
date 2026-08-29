const SURFACE_SCHEMA_VERSION = 1
const SURFACE_OUTCOMES = Object.freeze({
  UPDATED: "updated",
  OPENED: "opened",
  REPLACED: "replaced",
  SUPPRESSED: "suppressed",
  DEFERRED: "deferred",
  UNAVAILABLE: "unavailable",
})

function surfaceCatalogValue(catalog, surfaceId) {
  if (typeof catalog === "function") return catalog(surfaceId)
  return (Array.isArray(catalog) ? catalog : []).find(function (item) {
    return String(item && item.id || "") === String(surfaceId || "")
  }) || null
}

function stableHash(value) {
  const source = String(value || "")
  let hash = 2166136261
  for (let index = 0; index < source.length; index += 1) {
    hash ^= source.charCodeAt(index)
    hash = Math.imul(hash, 16777619)
  }
  return (hash >>> 0).toString(36)
}

function safeRelativePath(value) {
  const normalized = String(value || "").replace(/\\/g, "/").replace(/^\.\//, "")
  if (!normalized || normalized.startsWith("/") || /^[A-Za-z]:\//.test(normalized)) return ""
  const parts = normalized.split("/")
  if (parts.some(function (part) { return !part || part === ".." })) return ""
  return parts.join("/")
}

function wbcSurfaceResourceKey(resource) {
  const value = resource && typeof resource === "object" ? resource : {}
  const kind = String(value.kind || "")
  const projectId = String(value.projectId || value.project_id || "")
  if (kind === "file" || kind === "directory") {
    const path = safeRelativePath(value.path)
    return projectId && path ? projectId + ":" + kind + ":" + path : ""
  }
  const identity = String(value.id || value.executionId || value.execution_id || value.url || "")
  return kind && identity ? projectId + ":" + kind + ":" + identity : ""
}

function normalizedResource(resource) {
  const value = resource && typeof resource === "object" ? resource : {}
  const kind = String(value.kind || "")
  if (!kind) return null
  const next = Object.assign({}, value, {
    kind: kind,
    projectId: String(value.projectId || value.project_id || ""),
  })
  delete next.project_id
  if (kind === "file" || kind === "directory") {
    next.path = safeRelativePath(value.path)
    if (!next.projectId || !next.path) return null
  }
  return next
}

function wbcNormalizeSurfaceIntent(rawIntent, catalog) {
  const raw = rawIntent && typeof rawIntent === "object" ? rawIntent : {}
  const surfaceId = String(raw.surfaceId || raw.surface_id || raw.surface || "")
  const surface = surfaceCatalogValue(catalog, surfaceId)
  if (!surface) return null
  const resource = normalizedResource(raw.resource)
  if (!resource) return null
  const resourceKey = String(raw.resourceKey || raw.resource_key || wbcSurfaceResourceKey(resource))
  if (!resourceKey) return null
  return {
    schemaVersion: SURFACE_SCHEMA_VERSION,
    surfaceId: surfaceId,
    packId: String(surface.pack_id || raw.packId || raw.pack_id || ""),
    resource: resource,
    resourceKey: resourceKey,
    activity: String(raw.activity || ""),
    priority: String(raw.priority || surface.priority || "normal"),
    lifetime: String(raw.lifetime || surface.lifetime || "while-active"),
    preferredSide: String(raw.preferredSide || raw.preferred_side || surface.preferred_side || "either"),
    chatId: String(raw.chatId || raw.chat_id || ""),
    runId: String(raw.runId || raw.run_id || ""),
    state: raw.state == null ? null : raw.state,
    focus: false,
  }
}

function wbcSurfaceIntentsFromActivity(rawEvent, catalog) {
  const event = rawEvent && typeof rawEvent === "object" ? rawEvent : {}
  const payload = event.payload && typeof event.payload === "object" ? event.payload : event
  if (String(event.type || payload.type || "") === "surface.intent") {
    const explicit = payload.intent && typeof payload.intent === "object" ? payload.intent : payload
    const normalized = wbcNormalizeSurfaceIntent(explicit, catalog)
    return normalized ? [normalized] : []
  }
  const presentation = payload.presentation && typeof payload.presentation === "object"
    ? payload.presentation : {}
  const locations = Array.isArray(presentation.locations) ? presentation.locations : []
  const surfaces = Array.isArray(catalog) ? catalog : []
  const activity = String(payload.activity || payload.access || "")
  const intents = []
  for (const location of locations) {
    if (!location || typeof location !== "object") continue
    const kind = String(location.kind || "")
    const access = String(location.access || activity || "")
    const surface = surfaces.find(function (candidate) {
      const kinds = Array.isArray(candidate && candidate.resource_kinds) ? candidate.resource_kinds : []
      const activities = Array.isArray(candidate && candidate.accepted_activities) ? candidate.accepted_activities : []
      return (!kinds.length || kinds.indexOf(kind) >= 0)
        && (!activities.length || activities.indexOf(access) >= 0)
    })
    if (!surface) continue
    const normalized = wbcNormalizeSurfaceIntent({
      surfaceId: surface.id,
      resource: location,
      activity: access,
      chatId: event.chatId || event.chat_id || payload.chatId || payload.chat_id,
      runId: event.runId || event.run_id || payload.runId || payload.run_id,
      priority: surface.priority,
      lifetime: surface.lifetime,
    }, surfaces)
    if (normalized) intents.push(normalized)
  }
  return intents
}

function copyLayout(layout) {
  const source = layout && typeof layout === "object" ? layout : {}
  return {
    left: Array.isArray(source.left) ? source.left.slice(0, 2) : [],
    right: Array.isArray(source.right) ? source.right.slice(0, 2) : [],
    leftRatio: Number(source.leftRatio) || 0.5,
    rightRatio: Number(source.rightRatio) || 0.5,
  }
}

function surfaceCard(intent, now) {
  return {
    id: "surface:" + stableHash(intent.surfaceId + "\n" + intent.resourceKey),
    kind: "surface",
    payload: {
      schemaVersion: SURFACE_SCHEMA_VERSION,
      surfaceId: intent.surfaceId,
      packId: intent.packId,
      resource: intent.resource,
      resourceKey: intent.resourceKey,
      activity: intent.activity,
      state: intent.state,
      runId: intent.runId,
    },
    ownerChatId: intent.chatId,
    meta: {
      origin: "agent",
      claimedByUser: false,
      pinned: false,
      autoClosePolicy: intent.lifetime === "run" ? "run-end" : "never",
      createdAt: now,
      lastIntentAt: now,
    },
  }
}

function allCards(layout) {
  return ["left", "right"].flatMap(function (side) {
    return layout[side].map(function (card, index) { return { side: side, index: index, card: card } })
  })
}

function preferredSides(intent, layout) {
  if (intent.preferredSide === "left") return ["left", "right"]
  if (intent.preferredSide === "right") return ["right", "left"]
  if (!layout.right.length) return ["right", "left"]
  return layout.left.length <= layout.right.length ? ["left", "right"] : ["right", "left"]
}

function replaceable(location, canReplace) {
  const card = location.card || {}
  const meta = card.meta && typeof card.meta === "object" ? card.meta : {}
  if (card.kind !== "surface" || meta.origin !== "agent") return false
  if (meta.claimedByUser === true || meta.pinned === true) return false
  try { return typeof canReplace !== "function" || canReplace(card) !== false } catch (error) { return false }
}

function wbcRevealSurface(layoutValue, rawIntent, options) {
  const opts = options || {}
  const intent = wbcNormalizeSurfaceIntent(rawIntent, opts.catalog)
  if (!intent) return { layout: layoutValue, outcome: SURFACE_OUTCOMES.UNAVAILABLE, cardId: "", reason: "surface-unavailable" }
  if (typeof opts.isSuppressed === "function" && opts.isSuppressed(intent.runId, intent.resourceKey)) {
    return { layout: layoutValue, outcome: SURFACE_OUTCOMES.SUPPRESSED, cardId: "", reason: "user-suppressed" }
  }
  const now = Number(opts.now) || Date.now()
  const layout = copyLayout(layoutValue)
  const locations = allCards(layout)
  const existing = locations.find(function (location) {
    const payload = location.card && location.card.payload || {}
    return location.card.kind === "surface"
      && String(payload.surfaceId || "") === intent.surfaceId
      && String(payload.resourceKey || "") === intent.resourceKey
  })
  if (existing) {
    const current = existing.card
    layout[existing.side][existing.index] = Object.assign({}, current, {
      payload: Object.assign({}, current.payload || {}, {
        activity: intent.activity,
        state: intent.state,
        runId: intent.runId,
      }),
      meta: Object.assign({}, current.meta || {}, { lastIntentAt: now }),
    })
    return { layout: layout, outcome: SURFACE_OUTCOMES.UPDATED, cardId: current.id, reason: "same-resource" }
  }
  const card = surfaceCard(intent, now)
  const sides = preferredSides(intent, layout)
  for (const side of sides) {
    if (layout[side].length < 2) {
      layout[side].push(card)
      return { layout: layout, outcome: SURFACE_OUTCOMES.OPENED, cardId: card.id, reason: "empty-slot" }
    }
  }
  const candidates = locations.filter(function (location) {
    return replaceable(location, opts.canReplace)
  }).sort(function (left, right) {
    return Number(left.card.meta && left.card.meta.lastIntentAt || 0)
      - Number(right.card.meta && right.card.meta.lastIntentAt || 0)
  })
  if (!candidates.length) {
    return { layout: layoutValue, outcome: SURFACE_OUTCOMES.DEFERRED, cardId: "", reason: "layout-protected" }
  }
  const target = candidates[0]
  layout[target.side][target.index] = card
  return { layout: layout, outcome: SURFACE_OUTCOMES.REPLACED, cardId: card.id, reason: "agent-lru" }
}

function wbcClaimSurfaceCard(card) {
  if (!card || card.kind !== "surface") return card
  return Object.assign({}, card, {
    meta: Object.assign({}, card.meta || {}, { claimedByUser: true }),
  })
}

function wbcPinSurfaceCard(card, pinned) {
  if (!card || card.kind !== "surface") return card
  return Object.assign({}, card, {
    meta: Object.assign({}, card.meta || {}, {
      pinned: pinned !== false,
      claimedByUser: pinned !== false || !!(card.meta && card.meta.claimedByUser),
    }),
  })
}

export {
  SURFACE_OUTCOMES,
  SURFACE_SCHEMA_VERSION,
  wbcClaimSurfaceCard,
  wbcNormalizeSurfaceIntent,
  wbcPinSurfaceCard,
  wbcRevealSurface,
  wbcSurfaceIntentsFromActivity,
  wbcSurfaceResourceKey,
}
