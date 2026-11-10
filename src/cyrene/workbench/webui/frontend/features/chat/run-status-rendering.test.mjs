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
import { presentConversationMessages } from "./message-presentation.mjs"
import { scheduleContinuationFeedback } from "./continuation-feedback.mjs"

function indicator() {
  const h = harness("./messages.jsx", {
    React, createDisclosureSubscriptions, scheduleContinuationFeedback, useWorkbenchI18n() {}, WbcThreadItem: "thread-item",
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

test("initial processing renders immediately without scheduling a handoff timer", () => {
  const h = indicator()
  const row = h.run(module => module.WbcContinuationIndicator({ immediate: true }))
  assert.equal(row.type, "thread-item")
  assert.equal(row.props.children.props.label, "Processing")
  assert.equal(h.timers.size, 0)
  h.unmount()
})

test("an already visible initial indicator stays visible when the server starts an empty reply", () => {
  const h = indicator()
  let immediate = true
  assert.ok(h.run(module => module.WbcContinuationIndicator({ immediate })))
  immediate = false
  assert.ok(h.flush())
  assert.equal(h.timers.size, 0)
  h.unmount()
})

for (const questionId of [undefined, "question"]) test(`send feedback precedes any server response (answer=${!!questionId})`, async () => {
  const timeline = harness("./runtime-timeline.jsx").run(module => module)
  const engineHarness = harness("./file-resources.jsx", {
    ...timeline, workbenchServices: { events: () => ({ subscribe() {} }) },
  })
  engineHarness.context.performance = performance
  const engine = engineHarness.run(module => module.WorkbenchChatRuntimes)
  let handlers, finish
  const run = engine.start("chat", { message: "Hello", questionId }, {
    sendMessage(_id, _input, callbacks) {
      handlers = callbacks
      return new Promise(resolve => { finish = resolve })
    },
  })
  const h = harness("./messages.jsx", {
    ...timeline, React, presentConversationMessages, createDisclosureSubscriptions, scheduleContinuationFeedback,
    useWorkbenchI18n() {}, WbcThreadItem: "thread-item",
  })
  const module = h.context.module.exports
  function status() {
    const tree = module.WbcTranscript({ messages: [], runtime: engine.get("chat"), chatId: "chat" })
    return React.Children.toArray(tree.props.children).find(row => row.type === module.WbcContinuationIndicator)
  }
  assert.equal(timeline.wbcProjectTranscript([], engine.get("chat"))[0].content, "Hello")
  assert.equal(status().props.immediate, true)
  assert.ok(h.run(() => module.WbcContinuationIndicator(status().props)))
  assert.equal(h.timers.size, 0)
  handlers.onTimeline({ version: 2, runId: "run", revision: 1, status: "running", updates: [] })
  handlers.onAck({ runId: "run" })
  assert.equal(status().props.immediate, true)
  assert.ok(h.flush())
  assert.equal(h.timers.size, 0)
  handlers.onTimeline({ version: 2, runId: "run", revision: 2, status: "running", messages: [{
    id: "reply", runId: "run", role: "assistant", status: "running", content: "",
  }] })
  assert.equal(status().props.immediate, false)
  assert.ok(h.flush())
  assert.equal(h.timers.size, 0)
  handlers.onTimeline({ version: 2, runId: "run", revision: 3, status: "running", messages: [{
    id: "activity", runId: "run", role: "assistant", activityCard: true, status: "running", trace: [], reasoningActive: true,
  }] })
  assert.equal(status(), undefined)
  handlers.onSaved({ type: "saved", assistantMessages: [] })
  assert.equal(status(), undefined)
  h.unmount()
  finish()
  await run
})
