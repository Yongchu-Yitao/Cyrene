import assert from "node:assert/strict"
import { readFileSync } from "node:fs"
import test from "node:test"
import vm from "node:vm"
import { transformSync } from "esbuild"
import React from "react"
import { paneColumnBounds, paneRowGeometry } from "./pane-resize-geometry.mjs"
import { resizeCollapseEdge } from "../layout/resize-collapse.mjs"

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
  const hookSource = readFileSync(new URL("./transcript-resize-hooks.jsx", import.meta.url), "utf8")
  const hookContext = { module: { exports: {} }, require: () => dependencies }
  vm.runInNewContext(transformSync(hookSource, { loader: "jsx", format: "cjs" }).code, hookContext)
  const context = { module: { exports: {} }, require: name => name.includes("transcript-resize-hooks") ? hookContext.module.exports : dependencies, React }
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

function resizeHarness(kind, { closable = false, layoutWidth = 1236, width = 520 } = {}) {
  const listeners = new Map()
  const handleListeners = new Map()
  const classes = new Set()
  const frames = new Map()
  const commits = []
  const closes = []
  const events = []
  let frameId = 0
  let reads = 0
  let paints = 0
  const style = { setProperty(key, value) { this[key] = value; paints++ } }
  let captured = false
  const layout = { style, getBoundingClientRect() { reads++; return { width: layoutWidth, height: 812, top: 0 } } }
  const handle = {
    style: {}, closest: () => layout,
    addEventListener: (name, fn) => handleListeners.set(name, fn),
    removeEventListener: name => handleListeners.delete(name),
    setPointerCapture() { captured = true }, hasPointerCapture: () => captured,
    releasePointerCapture() { captured = false; handleListeners.get("lostpointercapture")?.({ pointerId: 1 }) },
  }
  const dependencies = { useWbcRef: () => ({ current: handle }), useWbcEffect() {}, wbcT: (_key, fallback) => fallback }
  let pointerResize
  const context = {
    module: { exports: {} }, require: name => {
      if (name.includes("pane-resize-geometry")) return { paneColumnBounds, paneRowGeometry }
      if (name.includes("resize-collapse")) return { resizeCollapseEdge }
      if (name.includes("pointer-resize")) return pointerResize
      return dependencies
    }, React,
    document: { body: { classList: { add: name => classes.add(name), remove: name => classes.delete(name) } } },
    window: {
      addEventListener: (name, fn) => listeners.set(name, fn),
      removeEventListener: name => listeners.delete(name),
      dispatchEvent: event => events.push(event),
    },
    CustomEvent: class { constructor(type, options = {}) { this.type = type; this.detail = options.detail } },
    requestAnimationFrame(fn) { frames.set(++frameId, fn); return frameId },
    cancelAnimationFrame: id => frames.delete(id),
  }
  const pointerSource = readFileSync(new URL("../layout/pointer-resize.mjs", import.meta.url), "utf8")
  vm.runInNewContext(transformSync(pointerSource, { format: "cjs" }).code, context)
  pointerResize = context.module.exports
  context.module = { exports: {} }
  vm.runInNewContext(code, context)
  const component = context.module.exports[kind === "column" ? "WbcPaneColumnResizer" : "WbcPaneRowResizer"]
  const element = component({ width, ratio: 0.5, side: "left", onResize: value => commits.push(value), onClose: closable ? side => closes.push(side) : undefined })
  element.props.onPointerDown({ button: 0, clientX: 700, pointerId: 1, currentTarget: handle, preventDefault() {} })
  return {
    commits, closes, events, frames, layout, handle, classes, handleListeners,
    captured: () => captured,
    reads: () => reads, paints: () => paints,
    move(x, y, pointerId = 1, buttons = 1) { listeners.get("pointermove")?.({ clientX: x, clientY: y, pointerId, pointerType: "mouse", buttons }) },
    stop(type = "pointerup", pointerId = 1) { (listeners.get(type) || handleListeners.get(type))?.({ pointerId }) },
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
  assert.deepEqual(drag.events.map(event => event.type), ["workbench:split-resize-start", "workbench:split-resize-end"])
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

function assertDragFinished(drag) {
  assert.equal(drag.frames.size, 0)
  assert.equal(drag.listeners.size, 0)
  assert.equal(drag.handleListeners.size, 0)
  assert.equal(drag.classes.size, 0)
  assert.equal(drag.captured(), false)
  assert.deepEqual(drag.events.map(event => event.type), ["workbench:split-resize-start", "workbench:split-resize-end"])
}

test("either column closes only after 48px of travel beyond its minimum width", () => {
  for (const [side, limitX, closeX, committedWidth] of [["right", 840, 888, 380], ["left", 400, 352, 820]]) {
    const drag = resizeHarness("column", { closable: true })
    drag.move(limitX, 0)
    drag.move(closeX + (side === "right" ? -1 : 1), 0)
    assert.deepEqual(drag.closes, [])
    drag.move(closeX, 0)
    assert.deepEqual(drag.closes, [side])
    assert.deepEqual(drag.commits, [committedWidth])
    assert.equal(drag.layout.style["--wbc-pane-right-width"], committedWidth + "px")
    assertDragFinished(drag)
    drag.move(closeX, 0)
    drag.stop()
    assert.deepEqual(drag.closes, [side])
  }
})

test("compact columns can close even when both tracks are already at their minimum", () => {
  for (const [x, side] of [[748, "right"], [652, "left"]]) {
    const drag = resizeHarness("column", { closable: true, layoutWidth: 636, width: 520 })
    drag.move(x, 0)
    assert.deepEqual(drag.closes, [side])
    assert.deepEqual(drag.commits, [300])
    assertDragFinished(drag)
  }
})

test("vertical resizing closes the compressed top or bottom pane", () => {
  for (const [y, index, ratio] of [[118, 0, 0.2], [694, 1, 0.8]]) {
    const drag = resizeHarness("row", { closable: true })
    drag.move(0, y + (index === 0 ? 1 : -1))
    assert.deepEqual(drag.closes, [])
    drag.move(0, y)
    assert.deepEqual(drag.closes, [index])
    assert.deepEqual(drag.commits, [ratio])
    assertDragFinished(drag)
  }
})

test("cancel, blur, lost capture and a released mouse end resizing without closing", () => {
  for (const type of ["pointercancel", "blur", "lostpointercapture", "released-mouse"]) {
    const drag = resizeHarness("column", { closable: true })
    drag.move(887, 0)
    drag.stop("pointerup", 2)
    assert.equal(drag.commits.length, 0)
    if (type === "released-mouse") drag.move(900, 0, 1, 0)
    else drag.stop(type)
    assert.deepEqual(drag.closes, [])
    assert.deepEqual(drag.commits, [380])
    assertDragFinished(drag)
  }
})
