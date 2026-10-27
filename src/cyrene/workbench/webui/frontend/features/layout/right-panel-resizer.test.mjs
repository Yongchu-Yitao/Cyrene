import assert from "node:assert/strict"
import { readFileSync } from "node:fs"
import test from "node:test"
import vm from "node:vm"
import { transformSync } from "esbuild"
import React from "react"
import { resizeCollapseEdge } from "./resize-collapse.mjs"

function panelHarness(closable = true) {
  const listeners = new Map(), handleListeners = new Map(), saved = new Map()
  const classes = new Set(), handleClasses = new Set(), events = [], closes = []
  const style = { setProperty(key, value) { this[key] = value }, getPropertyValue(key) { return this[key] || "" } }
  const grid = { style }
  const pane = { classList: { contains: name => name === "wbc-pane-layout" }, getBoundingClientRect: () => ({ left: 64 }) }
  const page = {
    classList: { contains: name => name === "wbc-page" }, children: [pane],
    getBoundingClientRect: () => ({ left: 0, right: 1600, width: 1600 }),
  }
  const panel = {
    getBoundingClientRect: () => ({ right: 1600, width: 350 }),
    closest: selector => selector === ".wbc-page" ? page : null,
  }
  let captured = false
  const handle = {
    closest: selector => selector === ".workbench-grid" ? grid : panel,
    classList: { add: name => handleClasses.add(name), remove: name => handleClasses.delete(name) },
    setPointerCapture() { captured = true }, hasPointerCapture: () => captured,
    releasePointerCapture() { captured = false; handleListeners.get("lostpointercapture")?.({ pointerId: 1 }) },
    addEventListener: (name, fn) => handleListeners.set(name, fn),
    removeEventListener: name => handleListeners.delete(name),
  }
  let pointerResize
  const context = {
    module: { exports: {} },
    require: name => {
      if (name.includes("pointer-resize")) return pointerResize
      if (name.includes("resize-collapse")) return { resizeCollapseEdge }
      return { workbenchServices: { i18n: () => ({ t: (_key, _params, fallback) => fallback }) } }
    },
    React: { ...React, useRef: () => ({ current: handle }), useEffect() {} },
    document: { body: { classList: { add: name => classes.add(name), remove: name => classes.delete(name) } } },
    window: {
      innerWidth: 1600, getComputedStyle: () => ({ getPropertyValue: () => "380px" }),
      addEventListener: (name, fn) => listeners.set(name, fn), removeEventListener: name => listeners.delete(name),
      dispatchEvent: event => events.push(event),
    },
    localStorage: { setItem: (key, value) => saved.set(key, value) },
    CustomEvent: class { constructor(type, options) { this.type = type; this.detail = options.detail } },
  }
  for (const file of ["pointer-resize.mjs", "right-panel-resizer.jsx"]) {
    context.module = { exports: {} }
    vm.runInNewContext(transformSync(readFileSync(new URL(file, import.meta.url), "utf8"), { loader: "jsx", format: "cjs" }).code, context)
    if (file === "pointer-resize.mjs") pointerResize = context.module.exports
  }
  const element = context.module.exports.WbColResizer({ trackGutter: true, onClose: closable ? () => closes.push(true) : undefined })
  element.props.onPointerDown({ button: 0, pointerId: 1, clientX: 1250, currentTarget: handle, preventDefault() {} })
  return {
    style, saved, closes, events, listeners, handleListeners, classes, handleClasses, captured: () => captured,
    move(x, pointerId = 1, buttons = 1) { listeners.get("pointermove")?.({ clientX: x, pointerId, pointerType: "mouse", buttons }) },
    stop(type = "pointerup") { (listeners.get(type) || handleListeners.get(type))?.({ pointerId: 1 }) },
  }
}

function assertFinished(drag) {
  assert.equal(drag.listeners.size, 0)
  assert.equal(drag.handleListeners.size, 0)
  assert.equal(drag.classes.size, 0)
  assert.equal(drag.handleClasses.size, 0)
  assert.equal(drag.captured(), false)
  assert.equal(drag.events.filter(event => event.detail.phase === "end").length, 1)
}

test("conversation panel closes after shrinking 48px past its minimum and retains a valid reopen width", () => {
  const drag = panelHarness()
  drag.move(1320)
  drag.move(1367)
  assert.deepEqual(drag.closes, [])
  assert.equal(drag.style["--wb-right-w"], "280px")
  drag.move(1368, 2)
  assert.deepEqual(drag.closes, [])
  drag.move(1368)
  assert.deepEqual(drag.closes, [true])
  assert.equal(drag.saved.get("wb-right-w"), "280")
  assertFinished(drag)
  drag.stop()
  assert.deepEqual(drag.closes, [true])
})

test("conversation panel clamps ordinary resizing and cancellation never closes it", () => {
  for (const stop of ["pointerup", "pointercancel", "blur", "lostpointercapture", "released-mouse"]) {
    const drag = panelHarness()
    drag.move(0)
    assert.equal(drag.style["--wb-right-w"], "1156px")
    drag.move(1367)
    if (stop === "released-mouse") drag.move(1400, 1, 0)
    else drag.stop(stop)
    assert.deepEqual(drag.closes, [])
    assert.equal(drag.saved.get("wb-right-w"), "280")
    assertFinished(drag)
  }
})

test("shared width handles without a close action keep clamping at the minimum", () => {
  const drag = panelHarness(false)
  drag.move(2000)
  assert.equal(drag.style["--wb-right-w"], "280px")
  assert.equal(drag.listeners.has("pointermove"), true)
  drag.stop()
  assert.deepEqual(drag.closes, [])
  assertFinished(drag)
})
