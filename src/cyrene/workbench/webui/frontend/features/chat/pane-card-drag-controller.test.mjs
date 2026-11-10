import assert from "node:assert/strict"
import { readFileSync } from "node:fs"
import test from "node:test"
import vm from "node:vm"

function harness(protectedPane) {
  const pane = { id: "chat:assistant", kind: "chat", payload: "assistant" }
  const calls = []
  const listeners = new Map()
  const bounds = { left: 0, top: 0, width: 500, height: 700 }
  const card = { getBoundingClientRect: () => bounds }
  const handle = { closest: () => card, getBoundingClientRect: () => bounds, dataset: {} }
  const bridge = {
    beginDrag: async () => { calls.push("begin") },
    updateDrag: () => calls.push("update"),
    finishDrag: async () => { calls.push("finish"); return { detached: false } },
  }
  const context = {
    canClosePaneCard: () => !protectedPane,
    paneLayoutFor: () => ({ left: [pane], right: [] }),
    activeChatIdRef: { current: "assistant" },
    paneCardDetachRef: { current: null }, paneCardDragImageCleanupRef: { current: null },
    chatCache: { details: {} },
    clearPaneCardDetachSubscription() {},
    setPaneCardDragId: id => calls.push(["drag", id]), setPaneDropTarget() {},
    paneCardDetachIpcPayload: value => value,
    cancelPaneCardDetachment() {},
    placeExistingPaneCard: (...args) => calls.push(["place", ...args]),
  }
  const sandbox = {
    window: { cyrene: { detachedPane: bridge } },
    document: { addEventListener: (name, fn) => listeners.set(name, fn) },
    wbcPaneCardLocation: () => ({ card: pane, side: "left", index: 0 }),
    wbcT: (_key, fallback) => fallback,
  }
  const source = readFileSync(new URL("./pane-card-drag-controller.jsx", import.meta.url), "utf8")
    .replace(/^import .*$/gm, "").replace(/^export .*$/gm, "")
  vm.createContext(sandbox)
  vm.runInContext(source, sandbox)
  // DOM preview painting is independent of the local/native drag lifecycle.
  sandbox.wbcCreatePaneDragSession = () => ({ context, cardId: pane.id, dragHandle: handle })
  sandbox.wbcInitializePaneDragPointer = session => { session.lastPointer = { clientX: 20, clientY: 20 } }
  sandbox.wbcMovePaneCardGhost = session => { session.pointerPaneTarget = { cardId: "file:target", edge: "bottom" } }
  sandbox.wbcClearPaneCardGhost = () => {}
  context.handlePaneCardDragEnd = (_event, options) => sandbox.wbcFinishPaneCardDrag(context, options)
  return { sandbox, context, calls, listeners, event: { currentTarget: handle, clientX: 20, clientY: 20 }, pane }
}

test("protected assistant can rearrange inside the window without native detachment", () => {
  const h = harness(true)
  h.sandbox.wbcStartPaneCardDrag(h.context, h.event, h.pane.id)
  assert.equal(h.context.paneCardDetachRef.current, null)
  assert.ok(h.listeners.has("pointermove"))
  h.listeners.get("pointerup")({ clientX: 20, clientY: 20 })
  assert.deepEqual(h.calls, [
    ["drag", h.pane.id], ["place", h.pane.id, "file:target", "bottom"], ["drag", ""],
  ])
})

test("ordinary conversation still starts and finishes native detachment", () => {
  const h = harness(false)
  h.sandbox.wbcStartPaneCardDrag(h.context, h.event, h.pane.id)
  assert.equal(h.context.paneCardDetachRef.current.cardId, h.pane.id)
  h.listeners.get("pointerup")({ clientX: 1200, clientY: 20 })
  assert.ok(h.calls.includes("begin"))
  assert.ok(h.calls.includes("finish"))
})
