import assert from "node:assert/strict"
import { readFileSync } from "node:fs"
import test from "node:test"
import vm from "node:vm"
import { transformSync } from "esbuild"
import React from "react"

const source = readFileSync(new URL("./runtime-timeline.jsx", import.meta.url), "utf8")
const { code } = transformSync(source, { loader: "jsx", format: "cjs" })
const context = {
  module: { exports: {} },
  exports: {},
  require: () => ({}),
  React,
}
context.exports = context.module.exports
vm.runInNewContext(code, context)

const {
  wbcProjectRuntimeTranscript,
  wbcProjectTranscript,
  wbcRuntimeTimelineMessages,
} = context.module.exports

function plain(value) {
  return JSON.parse(JSON.stringify(value))
}

test("runtime activity keeps its server timeline identity", () => {
  const messages = wbcRuntimeTimelineMessages({
    chatId: "chat_1",
    startedAt: Date.parse("2026-09-06T08:17:00Z"),
    activities: [{ id: "run_1:activity:4", progress: [] }],
  })
  const activity = messages.find(message => message.runtimeActivity)
  assert.equal(activity.id, "run_1:activity:4")
})

test("legacy projection replaces a checkpointed activity instead of rendering both", () => {
  const durable = [{
    id: "run_1:activity:4",
    role: "assistant",
    activityCard: true,
    createdAt: "2026-09-06T08:17:01Z",
    trace: [{ kind: "tool", toolCallId: "call_search_1", status: "running" }],
  }]
  const runtime = wbcRuntimeTimelineMessages({
    chatId: "chat_1",
    startedAt: Date.parse("2026-09-06T08:17:00Z"),
    activities: [{
      id: "run_1:activity:4",
      createdAt: Date.parse("2026-09-06T08:17:01Z"),
      progress: [{ kind: "tool", toolCallId: "call_search_1", status: "completed" }],
    }],
  })

  const projected = wbcProjectRuntimeTranscript(durable, runtime)
  const activities = projected.filter(message => message.activityCard || message.runtimeActivity)
  assert.equal(activities.length, 1)
  assert.ok(activities[0].runtimeActivity)
  assert.equal(activities[0].runtimeActivity.progress[0].status, "completed")
})

test("legacy projection deduplicates old prefixed ids by tool call identity", () => {
  const durable = [{
    id: "checkpoint_activity_7",
    role: "assistant",
    activityCard: true,
    createdAt: "2026-09-06T09:17:20Z",
    trace: [{ kind: "tool", toolCallId: "call_write_1", status: "completed" }],
  }]
  const runtime = [{
    id: "runtime_run_2:activity:18",
    role: "assistant",
    createdAt: "2026-09-06T09:17:20Z",
    runtimeActivity: {
      id: "run_2:activity:18",
      progress: [{ kind: "tool", toolCallId: "call_write_1", status: "completed" }],
    },
  }]

  const projected = wbcProjectRuntimeTranscript(durable, runtime)
  assert.equal(projected.filter(message => message.activityCard || message.runtimeActivity).length, 1)
  assert.equal(projected[0].id, "runtime_run_2:activity:18")
})

test("unified timeline suppresses a legacy checkpoint with a different activity id", () => {
  const durable = [{
    id: "legacy_activity_9",
    role: "assistant",
    activityCard: true,
    createdAt: "2026-09-06T09:18:00Z",
    trace: [{ kind: "tool", toolCallId: "call_bash_1", status: "completed" }],
  }]
  const runtime = {
    userMessages: [],
    timeline: {
      runId: "run_3",
      revision: 4,
      status: "running",
      messages: [{
        id: "run_3:activity:20",
        role: "assistant",
        activityCard: true,
        createdAt: "2026-09-06T09:18:00Z",
        status: "completed",
        trace: [{ kind: "tool", toolCallId: "call_bash_1", status: "completed" }],
      }],
    },
  }

  const projected = plain(wbcProjectTranscript(durable, runtime))
  assert.equal(projected.filter(message => message.activityCard).length, 1)
  assert.equal(projected[0].id, "run_3:activity:20")
})

const { wbcApplyTimeline } = context.module.exports

test("run fallback only fills quiet gaps without changing reasoning, tools or prose", () => {
  let runtime = {}
  const records = new Map()
  const frames = [
    [],
    [{ id: "a", activityCard: true, status: "running", reasoningActive: true, trace: [] }],
    [{ id: "a", activityCard: true, status: "completed", reasoningActive: false, trace: [] }],
    [{ id: "a", activityCard: true, status: "running", trace: [{ toolCallId: "tool", status: "running" }] }],
    [{ id: "a", activityCard: true, status: "completed", trace: [{ toolCallId: "tool", status: "failed", failed: true }] }],
    [{ id: "reply", status: "running", content: "" }],
    [{ id: "reply", status: "running", content: "Working on it" }],
    [{ id: "reply", status: "completed", intermediate: true, content: "Working on it" }],
    [{ id: "b", activityCard: true, status: "running", trace: [{ toolCallId: "next", status: "running" }] }],
    [{ id: "b", activityCard: true, status: "completed", trace: [{ toolCallId: "next", status: "completed" }] }],
    [{ id: "final", status: "running", content: "The answer" }],
    [{ id: "final", status: "completed", content: "The answer", attachments: [{ id: "file", url: "/file" }] }],
  ]
  for (const [index, updates] of frames.entries()) {
    const messages = updates.map(message => ({
      ...message, role: "assistant", timelineVersion: 1,
      timelineOrder: ["a", "reply", "b", "final"].indexOf(message.id),
      timelineRevision: index + 1, createdAt: "2026-09-12T00:00:00Z",
    }))
    for (const message of messages) records.set(message.id, message)
    runtime = wbcApplyTimeline(runtime, { version: 1, runId: "run", revision: index + 1, status: "running", messages })
    const before = JSON.stringify(runtime)
    // Also exercise checkpoint hydration while the same run is still active.
    const projected = plain(wbcProjectTranscript(Array.from(records.values()), runtime))
    const expectedFallback = [true, false, true, false, true, true, false, true, false, true, false, true][index]
    assert.equal(projected.filter(message => message.runtimeContinuation).length, Number(expectedFallback), `frame ${index}`)
    if (expectedFallback) assert.equal(projected.at(-1).id, "run:continuation")
    assert.deepEqual(projected.filter(message => !message.runtimeContinuation), Array.from(records.values()))
    assert.equal(JSON.stringify(runtime), before)
  }
  for (const status of ["completed", "failed", "cancelled"]) {
    const terminal = { ...runtime, timeline: { ...runtime.timeline, status } }
    assert.deepEqual(plain(wbcProjectTranscript([], terminal)), Array.from(records.values()))
  }
  assert.deepEqual(plain(wbcProjectTranscript(Array.from(records.values()), null)), Array.from(records.values()))
})

test("waiting and reconnection keep the existing suppression and resume the same run indicator", () => {
  const base = { timeline: { runId: "run", status: "running", messages: [] } }
  for (const runtime of [
    { ...base, pendingQuestion: { id: "question" } },
    { ...base, reconnecting: true },
    { timeline: { ...base.timeline, status: "waiting" } },
  ]) {
    assert.deepEqual(plain(wbcProjectTranscript([], runtime)), [])
  }
  assert.equal(wbcProjectTranscript([], base).at(-1).id, "run:continuation")
})

test("legacy status stays after checkpointed segments without altering activity state", () => {
  const base = { chatId: "legacy", startedAt: 1000 }
  const saved = [{ id: "later", role: "assistant", content: "checkpoint", createdAt: "2026-09-12T00:00:00Z" }]
  for (const state of [
    {},
    { activities: [{ id: "tool", progress: [{ status: "running" }] }] },
    { activities: [{ id: "tool", progress: [{ status: "completed" }] }] },
    { text: "reply" },
    { artifacts: [{ id: "file" }] },
  ]) {
    const runtime = { ...base, ...state }
    const before = JSON.stringify(runtime)
    const projected = wbcProjectRuntimeTranscript(saved, wbcRuntimeTimelineMessages(runtime))
    assert.equal(projected.filter(message => message.runtimeContinuation).length, 0)
    assert.equal(projected.find(message => message.id === "later"), saved[0])
    assert.equal(JSON.stringify(runtime), before)
  }
  const quiet = wbcProjectRuntimeTranscript(saved, wbcRuntimeTimelineMessages(base, { showReasoningPlaceholder: false }))
  assert.equal(quiet.at(-1).id, "runtime_continuation_legacy")
  assert.equal(quiet.filter(message => message.runtimeContinuation).length, 1)
  for (const state of [{ finalizing: true }, { pendingQuestion: { id: "q" } }, { reconnecting: true }]) {
    assert.equal(wbcRuntimeTimelineMessages({ ...base, ...state }).some(message => message.runtimeContinuation), false)
  }
})

test("running tools in earlier or folded activities suppress fallback, even after a newer activity completes", () => {
  const messages = [0, 1, 2].map(index => ({
    id: `activity-${index}`, role: "assistant", timelineVersion: 1, timelineRevision: 1,
    activityCard: true, status: index === 0 ? "running" : "completed",
    trace: [{ toolCallId: `call-${index}`, status: index === 0 ? "running" : "completed" }],
  }))
  const runtime = { timeline: { runId: "run", status: "running", messages } }
  assert.equal(wbcProjectTranscript([], runtime).some(message => message.runtimeContinuation), false)
  // The actual rendered checkpoint is newer than the live patch. Its liveness
  // must decide whether a second progress indicator would be redundant.
  const completed = { ...messages[0], timelineRevision: 2, status: "completed", trace: [{ toolCallId: "call-0", status: "completed" }] }
  assert.equal(wbcProjectTranscript([completed], runtime).at(-1).id, "run:continuation")
  runtime.timeline.messages[0] = completed
  const restarted = { ...completed, timelineRevision: 3, status: "running" }
  assert.equal(wbcProjectTranscript([restarted], runtime).some(message => message.runtimeContinuation), false)
})
