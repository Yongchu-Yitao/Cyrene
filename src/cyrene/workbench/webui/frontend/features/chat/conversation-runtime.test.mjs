import assert from "node:assert/strict"
import { readFileSync } from "node:fs"
import test from "node:test"
import vm from "node:vm"
import { transformSync } from "esbuild"
import React from "react"

const source = readFileSync(new URL("./conversation.jsx", import.meta.url), "utf8")
const { code } = transformSync(source + "\nexport { useWbcConversationRuntime };", { loader: "jsx", format: "cjs" })

// Run the production hook with persistent state and effect cleanup. Counting
// changed state values detects updates that would schedule React renders.
function mount(engine, chatId, summaryOnly) {
  let state, effectDeps, cleanup, changed = 0
  const dependencies = {
    useWbcState(initial) {
      if (state === undefined) state = initial()
      return [state, update => {
        const next = update(state)
        if (next !== state) { state = next; changed++ }
      }]
    },
    useWbcEffect(effect, deps) {
      if (!effectDeps || deps.some((value, i) => value !== effectDeps[i])) {
        cleanup?.()
        effectDeps = deps
        cleanup = effect()
      }
    },
  }
  const context = { module: { exports: {} }, require: () => dependencies, React }
  vm.runInNewContext(code, context)
  const hook = context.module.exports.useWbcConversationRuntime
  const instance = {
    read(id = chatId) { chatId = id; return hook({ id }, engine, summaryOnly) },
    get changes() { return changed },
    unmount() { cleanup?.() },
  }
  instance.read()
  return instance
}

function runtimeEngine() {
  let snapshot = {}
  const live = new Set(), summary = new Set()
  return {
    live, summary,
    get: id => snapshot[id] || null,
    snapshot: () => snapshot,
    subscribe(fn) { live.add(fn); return () => live.delete(fn) },
    subscribeSummary(fn) { summary.add(fn); return () => summary.delete(fn) },
    publish(id, value, semantic = false) {
      snapshot = { ...snapshot, [id]: value }
      if (semantic) summary.forEach(fn => fn(snapshot))
      live.forEach(fn => fn(snapshot))
    },
  }
}

test("tokens update the message subscriber while shell state stays unchanged", () => {
  const engine = runtimeEngine()
  const shell = mount(engine, "a", true)
  const messages = mount(engine, "a", false)
  const started = { text: "", replying: true }
  engine.publish("a", started, true)
  assert.equal(shell.read(), started)
  for (let i = 1; i <= 100; i++) engine.publish("a", { ...started, text: "x".repeat(i) })
  assert.equal(shell.changes, 1)
  assert.equal(messages.changes, 101)
  assert.equal(messages.read().text.length, 100)
  assert.equal(shell.read(), started)
  const before = messages.changes
  engine.publish("b", { text: "another conversation" })
  assert.equal(messages.changes, before)
  const done = { text: "x".repeat(100), streamDone: true }
  engine.publish("a", done, true)
  assert.equal(shell.read(), done)
  assert.equal(messages.read(), done)
  engine.publish("a", null, true)
  assert.equal(shell.read(), null)
  assert.equal(messages.read(), null)
  shell.unmount(); messages.unmount()
  assert.equal(engine.live.size, 0)
  assert.equal(engine.summary.size, 0)
})

test("switching conversations selects current state and replaces subscriptions", () => {
  for (const summaryOnly of [false, true]) {
    const engine = runtimeEngine()
    const a = { text: "first" }, b = { text: "second" }
    engine.publish("a", a, true); engine.publish("b", b, true)
    const instance = mount(engine, "a", summaryOnly)
    assert.equal(instance.read(), a)
    assert.equal(instance.read("b"), b)
    assert.equal(engine.live.size + engine.summary.size, 1)
    const before = instance.changes
    engine.publish("a", { text: "old chat delta" }, true)
    assert.equal(instance.changes, before)
    assert.equal(instance.read(), b)
    instance.unmount()
    assert.equal(engine.live.size + engine.summary.size, 0)
  }
})
