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
