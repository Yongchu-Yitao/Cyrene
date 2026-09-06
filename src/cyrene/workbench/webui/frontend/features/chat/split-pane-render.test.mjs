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

function resizeHarness(kind) {
  const listeners = new Map()
  const frames = new Map()
  const commits = []
  const events = []
  let frameId = 0
  let reads = 0
  let paints = 0
  const style = { setProperty(key, value) { this[key] = value; paints++ } }
  const layout = { style, getBoundingClientRect() { reads++; return { width: 1236, height: 812, top: 0 } } }
  const handle = { style: {}, closest: () => layout, addEventListener() {}, removeEventListener() {} }
  const dependencies = { useWbcRef: () => ({ current: handle }), useWbcEffect() {}, wbcT: (_key, fallback) => fallback }
  const context = {
    module: { exports: {} }, require: () => dependencies, React,
    document: { body: { classList: { add() {}, remove() {} } } },
    window: {
      addEventListener: (name, fn) => listeners.set(name, fn),
      removeEventListener: name => listeners.delete(name),
      dispatchEvent: event => events.push(event),
    },
    CustomEvent: class { constructor(type, options) { this.type = type; this.detail = options.detail } },
    requestAnimationFrame(fn) { frames.set(++frameId, fn); return frameId },
    cancelAnimationFrame: id => frames.delete(id),
  }
  vm.runInNewContext(code, context)
  const component = context.module.exports[kind === "column" ? "WbcPaneColumnResizer" : "WbcPaneRowResizer"]
  const element = component({ width: 520, ratio: 0.5, side: "left", onResize: value => commits.push(value) })
  element.props.onPointerDown({ button: 0, clientX: 700, pointerId: 1, currentTarget: handle, preventDefault() {} })
  return {
    commits, events, frames, layout, handle,
    reads: () => reads, paints: () => paints,
    move(x, y, pointerId = 1) { listeners.get("pointermove")({ clientX: x, clientY: y, pointerId, pointerType: "mouse", buttons: 1 }) },
    stop(type = "pointerup") { listeners.get(type)({ pointerId: 1 }) },
    flush() { for (const [id, fn] of frames) { frames.delete(id); fn() } },
    listeners,
  }
}

test("column dragging coalesces moves without state commits or repeated layout reads", () => {
  const drag = resizeHarness("column")
  for (let x = 699; x >= 600; x--) drag.move(x, 0)
  assert.equal(drag.commits.length, 0)
  assert.equal(drag.reads(), 1)
  assert.equal(drag.frames.size, 1)
  drag.flush()
  assert.equal(drag.paints(), 1)
  assert.equal(drag.layout.style["--wbc-pane-right-width"], "620px")
  drag.move(-5000, 0)
  drag.stop()
  assert.deepEqual(drag.commits, [820])
  assert.equal(drag.layout.style["--wbc-pane-right-width"], "820px")
  assert.equal(drag.frames.size, 0)
  assert.equal(drag.listeners.size, 0)
  assert.equal(drag.events[0].type, "workbench:split-resize-end")
})

test("row dragging previews tracks and separator, then commits once on cancellation", () => {
  const drag = resizeHarness("row")
  drag.move(0, 606, 2)
  assert.equal(drag.frames.size, 0)
  drag.move(0, 406)
  drag.move(0, 606)
  assert.equal(drag.commits.length, 0)
  assert.equal(drag.frames.size, 1)
  drag.stop("pointercancel")
  assert.deepEqual(drag.commits, [0.75])
  assert.equal(drag.layout.style.gridTemplateRows, "0.75fr 0.25fr")
  assert.equal(drag.handle.style.top, "calc(75% + -3px)")
  assert.equal(drag.frames.size, 0)
  assert.equal(drag.listeners.size, 0)
})
