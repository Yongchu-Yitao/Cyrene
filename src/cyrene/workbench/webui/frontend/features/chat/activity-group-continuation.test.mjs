import assert from "node:assert/strict"
import { readFileSync } from "node:fs"
import test from "node:test"
import vm from "node:vm"
import { transformSync } from "esbuild"
import React from "react"
import { harness } from "./hook-harness.test.mjs"
import { createDisclosureSubscriptions, subscribeDisclosureUpdates } from "./disclosure-subscriptions.mjs"

const context = { module: { exports: {} }, require: () => ({}) }
vm.runInNewContext(transformSync(readFileSync(new URL("./runtime-timeline.jsx", import.meta.url), "utf8"), {
  loader: "jsx", format: "cjs",
}).code, context)
const { wbcProjectTranscript } = context.module.exports

function setup() {
  const storage = new Map()
  return harness("./messages.jsx", {
    React, createDisclosureSubscriptions, subscribeDisclosureUpdates,
    WBC_ICONS: {}, useWorkbenchI18n() {},
    wbcFormatProcessingDuration: ms => `${ms / 1000}s`,
    wbcT: (_key, fallback, params = {}) => fallback.replace(/\{(\w+)\}/g, (_, key) => params[key]),
    localStorage: { getItem: key => storage.get(key) ?? null, setItem: (key, value) => storage.set(key, value) },
  })
}

function activities(runId = "run", count = 3) {
  return Array.from({ length: count }, (_, index) => ({
    id: `${runId}:activity:${index}`, runId, role: "assistant", timelineVersion: 1,
    activityCard: true, status: "completed", reasoning: `Thought ${index}`,
    trace: [{ kind: "tool", toolCallId: `call-${index}`, status: "completed", text: "read" }],
    createdAt: `2026-11-06T00:00:0${index * 2}Z`, endedAt: `2026-11-06T00:00:0${index * 2 + 1}Z`,
  }))
}

function project(h, messages, options = {}, saved = []) {
  const runtime = { ...options, timeline: { runId: "run", status: "running", messages, ...options.timeline } }
  const rows = wbcProjectTranscript(saved, runtime)
  return h.context.module.exports.wbcGroupConsecutiveActivityMessages(rows, runtime)
}

function summary(tree) { return tree.props.children[0] }
function label(tree) { return summary(tree).props.children[1].props.children }
function details(tree) { return tree.props.children[1].props.children.props.children.props.children }
const click = { currentTarget: { closest: () => null } }

test("handoffs reuse the group and keep its disclosure open through activity and run completion", () => {
  const h = setup()
  const messages = activities()
  const before = JSON.stringify(messages)
  let rows = h.context.module.exports.wbcGroupConsecutiveActivityMessages(messages, null)
  let group = rows[0]
  const id = group.id
  let tree = h.run(module => module.WbcActivityGroup({ group }))
  assert.equal(label(tree), "Processed 5s")
  assert.equal(summary(tree).props["aria-expanded"], false)

  rows = project(h, messages)
  assert.equal(rows.length, 1)
  group = rows[0]
  assert.equal(group.id, id)
  tree = h.flush()
  assert.equal(label(tree), "Processing")
  assert.equal(tree.props["aria-busy"], "true")
  assert.equal(summary(tree).props["aria-expanded"], false)
  summary(tree).props.onClick(click)
  tree = h.flush()
  assert.equal(summary(tree).props["aria-expanded"], true)
  assert.deepEqual(Array.from(details(tree), card => card.props.activity.reasoning), messages.map(message => message.reasoning))
  assert.ok(details(tree).every(card => !card.props.active && !card.props.live))
  assert.equal(h.timers.size, 0)

  const next = { ...activities("run", 4)[3], status: "running", reasoningActive: true, trace: [] }
  rows = project(h, [...messages, next])
  assert.equal(rows.length, 1)
  group = rows[0]
  assert.equal(group.id, id)
  tree = h.flush()
  assert.equal(label(tree), "Thinking")
  assert.equal(summary(tree).props["aria-expanded"], true)
  assert.equal(details(tree).length, 4)

  const completed = [...messages, { ...next, status: "completed", reasoningActive: false }]
  rows = project(h, completed)
  assert.equal(rows.length, 1)
  group = rows[0]
  tree = h.flush()
  assert.equal(label(tree), "Processing")
  assert.equal(summary(tree).props["aria-expanded"], true)

  for (const status of ["completed", "failed", "cancelled"]) {
    rows = project(h, completed, { timeline: { status } })
    assert.equal(rows.length, 1)
    group = rows[0]
    assert.equal(group.id, id)
    tree = h.flush()
    assert.equal(label(tree), "Processed 7s")
    assert.equal(tree.props["aria-busy"], undefined)
    assert.equal(summary(tree).props["aria-expanded"], true)
  }
  summary(tree).props.onClick(click)
  assert.equal(summary(h.flush()).props["aria-expanded"], false)
  assert.equal(JSON.stringify(messages), before)
  h.unmount()
})

test("fallback stays separate when no current-run group can own it", () => {
  const h = setup()
  const cases = [
    { messages: [], saved: [] },
    { messages: [], saved: activities("older-run") },
    { messages: activities("run", 2), saved: [] },
    { messages: [...activities(), { id: "reply", runId: "run", role: "assistant", content: "Progress update", status: "completed", createdAt: "2026-11-06T00:00:08Z" }], saved: [] },
  ]
  for (const { messages, saved } of cases) {
    const rows = project(h, messages, {}, saved)
    assert.equal(rows.at(-1).runtimeContinuation, true)
    assert.ok(rows.filter(row => row.activityGroup).every(group => !group.active && !group.continuing))
  }
  h.unmount()
})

test("waiting and reconnection do not turn a completed group into Processing", () => {
  const h = setup()
  const messages = activities()
  for (const options of [
    { pendingQuestion: { id: "question" } },
    { reconnecting: true },
    { timeline: { status: "waiting" } },
  ]) {
    const rows = project(h, messages, options)
    assert.equal(rows.length, 1)
    assert.equal(rows[0].active, false)
    assert.equal(rows[0].continuing, undefined)
  }
  h.unmount()
})
