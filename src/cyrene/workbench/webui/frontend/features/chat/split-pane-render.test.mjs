import assert from "node:assert/strict"
import { readFileSync } from "node:fs"
import test from "node:test"
import vm from "node:vm"
import { transformSync } from "esbuild"
import React from "react"

const source = readFileSync(new URL("./split-pane.jsx", import.meta.url), "utf8")
const { code } = transformSync(source, { loader: "jsx", format: "cjs" })

// Evaluate the real pane render, keeping network effects and child components
// outside this test. In particular, JSX prop expressions must run while the
// selected conversation is still loading (chat === null).
function renderPane(chat, loading = true, error = "") {
  let stateIndex = 0
  const dependencies = new Proxy({
    useWbcState(initial) {
      const values = [chat, loading, error]
      return [stateIndex < values.length ? values[stateIndex++] : initial, () => {}]
    },
    useWbcRef: current => ({ current }),
    useWbcEffect: () => {},
    useWbcLayoutEffect: () => {},
    wbcT: (_key, fallback) => fallback,
    wbcReconcileLiveUserMessages: messages => messages,
    wbcGroupConsecutiveActivityMessages: messages => messages,
  }, { get: (target, key) => key in target ? target[key] : String(key) })
  const context = { module: { exports: {} }, require: () => dependencies, React }
  vm.runInNewContext(code, context)
  return context.module.exports.WbcChatSplit({ chatId: "restored-chat" })
}

function findElement(element, type) {
  if (!React.isValidElement(element)) return null
  if (element.type === type) return element
  for (const child of React.Children.toArray(element.props.children)) {
    const match = findElement(child, type)
    if (match) return match
  }
  return null
}

test("restored split renders before its conversation loads, including load failure", () => {
  for (const [loading, error] of [[true, ""], [false, "Load failed"]]) {
    const pane = renderPane(null, loading, error)
    const transcript = findElement(pane, "WbcTranscript")
    assert.ok(transcript)
    assert.equal(transcript.props.pendingQuestion, null)
    assert.equal(transcript.props.messages.length, 0)
  }
})

test("loaded split passes the pending question and messages to the transcript", () => {
  const pendingQuestion = { id: "question-1" }
  const messages = [{ id: "user-1", role: "user", text: "Hello" }]
  const pane = renderPane({ id: "restored-chat", messages, pendingQuestion }, false)
  const transcript = findElement(pane, "WbcTranscript")
  assert.equal(transcript.props.pendingQuestion, pendingQuestion)
  assert.equal(transcript.props.messages, messages)
})
