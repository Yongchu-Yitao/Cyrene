import assert from "node:assert/strict"
import { readFileSync } from "node:fs"
import test from "node:test"
import vm from "node:vm"
import { transformSync } from "esbuild"
import React from "react"

const source = readFileSync(new URL("./conversation.jsx", import.meta.url), "utf8")
const helper = source.slice(source.indexOf("function wbcRenderConversationTimeline("), source.indexOf("// Keep token-level state below"))
const { code } = transformSync(helper + "\nexport { wbcRenderConversationTimeline };", { loader: "jsx", format: "cjs" })
const context = { module: { exports: {} }, React, WbcThreadItem: "thread-item", WbcAssistantMessage: "assistant-message" }
vm.runInNewContext(code, context)
const render = context.module.exports.wbcRenderConversationTimeline

test("legacy streaming inserts prose before the same mounted status element", () => {
  const history = React.createElement("history", { key: "history" })
  const status = React.createElement("status", { key: "runtime_continuation_chat" })
  const rows = [history, status]
  const runtime = { chatId: "chat", replyRenderKey: "reply" }
  assert.equal(render(rows, runtime), rows)
  for (const state of [{ text: "first token" }, { text: "more tokens" }, { artifacts: [{ id: "file" }] }]) {
    const rendered = render(rows, { ...runtime, ...state })
    assert.deepEqual(Array.from(rendered, row => row.key), ["history", "reply", status.key])
    assert.equal(rendered[0], history)
    assert.equal(rendered.at(-1), status)
  }
  const final = render([history], { ...runtime, text: "final", finalizing: true })
  assert.deepEqual(Array.from(final, row => row.key), ["history", "reply"])
  assert.equal(render(rows, { ...runtime, text: "reply", timeline: {} }), rows)
  assert.deepEqual(rows, [history, status])
})

// The timer is a display concern. Exercise its cancellation without real sleeps
// or changing any timeline/event timestamps.
import { harness } from "./hook-harness.test.mjs"
import { createDisclosureSubscriptions } from "./disclosure-subscriptions.mjs"

function indicator() {
  const h = harness("./messages.jsx", {
    React, createDisclosureSubscriptions, useWorkbenchI18n() {}, WbcThreadItem: "thread-item",
  })
  const schedule = h.context.window.setTimeout
  h.context.window.setTimeout = (callback, delay) => {
    assert.equal(delay, 400)
    return schedule(callback)
  }
  return h
}

test("short handoffs and unmounts cancel the delayed indicator without rendering an empty row", () => {
  const h = indicator()
  assert.equal(h.run(module => module.WbcContinuationIndicator({})), null)
  assert.equal(h.timers.size, 1)
  assert.equal(h.flush(), null)
  assert.equal(h.timers.size, 1)
  h.unmount()
  assert.equal(h.timers.size, 0)
  const nextGap = indicator()
  assert.equal(nextGap.run(module => module.WbcContinuationIndicator({})), null)
  nextGap.unmount()
})

test("a sustained quiet gap shows the existing processing card after the delay", () => {
  const h = indicator()
  h.run(module => module.WbcContinuationIndicator({ className: "retry-clearing" }))
  const [timer, callback] = h.timers.entries().next().value
  h.timers.delete(timer)
  callback()
  const row = h.flush()
  assert.equal(row.type, "thread-item")
  assert.equal(row.props.className, "retry-clearing")
  assert.equal(row.props.children.props.label, "Processing")
  assert.equal(row.props.children.props.running, true)
  assert.equal(h.timers.size, 0)
  h.unmount()
})
