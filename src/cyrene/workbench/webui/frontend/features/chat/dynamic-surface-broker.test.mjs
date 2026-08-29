import assert from "node:assert/strict"
import test from "node:test"

import {
  wbcClaimSurfaceCard,
  wbcPinSurfaceCard,
  wbcRevealSurface,
  wbcSurfaceIntentsFromActivity,
  wbcSurfaceResourceKey,
} from "./dynamic-surface-broker.mjs"

const catalog = [{
  id: "sample/editor",
  pack_id: "sample",
  priority: "normal",
  lifetime: "run",
  preferred_side: "right",
}]

function intent(path, extra) {
  return Object.assign({
    surfaceId: "sample/editor",
    chatId: "chat-1",
    runId: "run-1",
    activity: "write",
    resource: { kind: "file", projectId: "project-1", path: path },
  }, extra || {})
}

function userCard(id) {
  return { id: id, kind: "chat", payload: id, ownerChatId: "chat-1" }
}

test("surface broker opens and updates the same resource without moving it", () => {
  const base = { left: [userCard("chat-1")], right: [], leftRatio: 0.5, rightRatio: 0.5 }
  const opened = wbcRevealSurface(base, intent("src/app.py"), { catalog, now: 10 })
  assert.equal(opened.outcome, "opened")
  assert.equal(opened.layout.left[0].id, "chat-1")
  assert.equal(opened.layout.right.length, 1)
  assert.equal(opened.layout.right[0].meta.origin, "agent")
  assert.equal(opened.layout.right[0].meta.autoClosePolicy, "run-end")

  const updated = wbcRevealSurface(opened.layout, intent("src/app.py", { activity: "read" }), {
    catalog,
    now: 20,
  })
  assert.equal(updated.outcome, "updated")
  assert.equal(updated.cardId, opened.cardId)
  assert.equal(updated.layout.right[0].payload.activity, "read")
  assert.equal(updated.layout.right[0].meta.lastIntentAt, 20)
})

test("surface broker never replaces user, pinned, claimed, or dirty cards", () => {
  const generated = wbcRevealSurface(
    { left: [], right: [], leftRatio: 0.5, rightRatio: 0.5 },
    intent("old.py"),
    { catalog, now: 1 },
  ).layout.right[0]
  const full = {
    left: [userCard("user-left"), wbcPinSurfaceCard(generated, true)],
    right: [userCard("user-right"), Object.assign({}, generated, {
      id: "dirty-agent",
      payload: Object.assign({}, generated.payload, { resourceKey: "dirty" }),
      meta: Object.assign({}, generated.meta, { pinned: false, claimedByUser: false }),
    })],
    leftRatio: 0.5,
    rightRatio: 0.5,
  }
  const deferred = wbcRevealSurface(full, intent("new.py"), {
    catalog,
    now: 30,
    canReplace: function (card) { return card.id !== "dirty-agent" },
  })
  assert.equal(deferred.outcome, "deferred")
  assert.equal(deferred.layout, full)

  const replaceable = Object.assign({}, full, {
    right: [full.right[0], Object.assign({}, full.right[1], {
      id: "replaceable-agent",
      meta: Object.assign({}, full.right[1].meta, { lastIntentAt: 2 }),
    })],
  })
  const replaced = wbcRevealSurface(replaceable, intent("new.py"), {
    catalog,
    now: 31,
    canReplace: function () { return true },
  })
  assert.equal(replaced.outcome, "replaced")
  assert.equal(replaced.layout.left[0].id, "user-left")
  assert.equal(replaced.layout.right[0].id, "user-right")
  assert.equal(replaced.layout.right[1].payload.resource.path, "new.py")
})

test("surface suppression and ownership helpers are deterministic", () => {
  const base = { left: [userCard("chat-1")], right: [], leftRatio: 0.5, rightRatio: 0.5 }
  const suppressed = wbcRevealSurface(base, intent("src/app.py"), {
    catalog,
    isSuppressed: function (runId, key) {
      return runId === "run-1" && key === "project-1:file:src/app.py"
    },
  })
  assert.equal(suppressed.outcome, "suppressed")
  assert.equal(suppressed.layout, base)

  const opened = wbcRevealSurface(base, intent("src/app.py"), { catalog }).layout.right[0]
  assert.equal(wbcClaimSurfaceCard(opened).meta.claimedByUser, true)
  assert.equal(wbcPinSurfaceCard(opened, true).meta.pinned, true)
  assert.equal(wbcPinSurfaceCard(opened, true).meta.claimedByUser, true)
  assert.equal(wbcSurfaceResourceKey({
    kind: "directory", projectId: "project-1", path: "src/components",
  }), "project-1:directory:src/components")
})

test("surface broker rejects absolute and escaping file locations", () => {
  const base = { left: [], right: [], leftRatio: 0.5, rightRatio: 0.5 }
  assert.equal(wbcRevealSurface(base, intent("/etc/passwd"), { catalog }).outcome, "unavailable")
  assert.equal(wbcRevealSurface(base, intent("../secret"), { catalog }).outcome, "unavailable")
})

test("tool activity stays silent without an exact semantic reveal grant", () => {
  const event = {
    type: "tool.started",
    runId: "run-2",
    payload: {
      chatId: "chat-1",
      presentation: {
        locations: [{
          kind: "file",
          access: "write",
          projectId: "project-1",
          path: "src/activity.py",
        }],
      },
    },
  }
  const intents = wbcSurfaceIntentsFromActivity(event, catalog)
  assert.equal(intents.length, 1)
  assert.equal(intents[0].surfaceId, "sample/editor")
  assert.equal(intents[0].resourceKey, "project-1:file:src/activity.py")
  assert.equal(intents[0].runId, "run-2")
  assert.equal(intents[0].attention, "observe")
  const observed = wbcRevealSurface(
    { left: [], right: [], leftRatio: 0.5, rightRatio: 0.5 },
    intents[0],
    { catalog },
  )
  assert.equal(observed.outcome, "observed")
  assert.equal(observed.layout.left.length + observed.layout.right.length, 0)

  event.payload.presentation.attention = {
    mode: "reveal",
    reason: "explicit-user-resource-request",
    operation: "edit",
    resource_keys: ["project-1:file:src/other.py"],
  }
  assert.equal(wbcSurfaceIntentsFromActivity(event, catalog)[0].attention, "observe")

  event.payload.presentation.attention.resource_keys = ["project-1:file:src/activity.py"]
  const granted = wbcSurfaceIntentsFromActivity(event, catalog)[0]
  assert.equal(granted.attention, "reveal")
  assert.equal(wbcRevealSurface(
    { left: [], right: [], leftRatio: 0.5, rightRatio: 0.5 },
    granted,
    { catalog },
  ).outcome, "opened")
})

test("update attention refreshes an existing surface but never opens one", () => {
  const base = { left: [], right: [], leftRatio: 0.5, rightRatio: 0.5 }
  const unopened = wbcRevealSurface(base, intent("src/app.py", { attention: "update" }), { catalog })
  assert.equal(unopened.outcome, "observed")
  assert.equal(unopened.reason, "update-only")
  assert.equal(unopened.layout, base)

  const opened = wbcRevealSurface(base, intent("src/app.py"), { catalog, now: 10 })
  const updated = wbcRevealSurface(opened.layout, intent("src/app.py", {
    attention: "update",
    activity: "read",
  }), { catalog, now: 20 })
  assert.equal(updated.outcome, "updated")
  assert.equal(updated.layout.right[0].payload.activity, "read")
})

test("automatic explicit surface events still require semantic authorization", () => {
  const explicit = {
    type: "surface.intent",
    payload: { intent: intent("src/app.py") },
  }
  assert.equal(wbcSurfaceIntentsFromActivity(explicit, catalog)[0].attention, "observe")
  explicit.payload.presentation = {
    attention: {
      mode: "reveal",
      reason: "explicit-user-resource-request",
      operation: "edit",
      resource_keys: ["project-1:file:src/app.py"],
    },
  }
  assert.equal(wbcSurfaceIntentsFromActivity(explicit, catalog)[0].attention, "reveal")
})
