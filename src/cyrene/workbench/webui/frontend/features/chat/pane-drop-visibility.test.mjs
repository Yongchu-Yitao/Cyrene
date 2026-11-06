import assert from "node:assert/strict"
import { readFileSync } from "node:fs"
import test from "node:test"
import vm from "node:vm"
import React from "react"
import { transformSync } from "esbuild"

function load(name, dependencies = {}) {
  const source = readFileSync(new URL(name, import.meta.url), "utf8")
  const context = {
    module: { exports: {} }, React, Image: function () {},
    window: { CyreneUI: { register: () => ({}) } },
    require: () => dependencies,
  }
  vm.runInNewContext(transformSync(source, { loader: "jsx", format: "cjs" }).code, context)
  return context.module.exports
}
const drag = load("./drag-layout.jsx")
const { wbcPaneWorkspacePresentation: presentation } = load("./pane-workspace.jsx", drag)
const { wbcHandlePaneDrop: drop } = load("./pane-drop-controller.jsx", drag)
const { WbcPaneCardFrame: frame } = load("./split-pane.jsx", {
  wbcT: (_key, fallback) => fallback,
})
const main = { id: "chat:main", kind: "chat", payload: "main", ownerChatId: "main" }
const layout = () => ({ left: [main], right: [] })

for (const docked of [true, false]) {
  test(`single conversation uses ${docked ? "context tracks" : "card targets"} when docked context is ${docked ? "visible" : "absent"}`, () => {
    const state = presentation(layout(), "", true, false, "main", docked)
    assert.equal(state.singlePaneDropUsesContextTracks, docked)
    assert.equal(state.singlePaneContextDropActive, docked)
    assert.equal(state.paneDropSessionActive, true)
  })
}

for (const edge of ["left", "right", "top", "bottom", "replace"]) {
  test(`hidden context: ${edge} card sensor commits its displayed action`, () => {
    let current = layout()
    const context = {
      activeChatIdRef: { current: "main" }, paneCardDragImageCleanupRef: { current: null },
      paneLayoutFor: () => current,
      updatePaneLayout: update => { current = update(current) },
      selectChat: id => { current = { selectedChat: id } },
      setPaneCardDragId() {}, setResourceDragSession() {}, setChatDragKind() {}, setPaneDropTarget() {},
    }
    const event = {
      preventDefault() {}, stopPropagation() {},
      dataTransfer: {
        types: [drag.WBC_CHAT_DRAG_MIME],
        getData: mime => mime === drag.WBC_CHAT_DRAG_MIME
          ? JSON.stringify({ kind: "chat", id: "incoming" }) : "",
      },
    }
    const tree = frame({
      card: main, dropKey: main.id, dropEnabled: true, axisEnabled: true,
      onDropOver: (_event, cardId, hoveredEdge) => { context.paneDropTarget = { cardId, edge: hoveredEdge } },
      onDrop: (event, cardId, edge) => drop(context, event, cardId, edge),
    })
    const layer = React.Children.toArray(tree.props.children).find(child => child.props.className?.includes("drop-layer"))
    const surface = layer.props.children.type(layer.props.children.props)
    const sensor = React.Children.toArray(surface.props.children).find(child => child.props.className === `wbc-pane-card-axis-sensor ${edge}`)
    assert.ok(sensor)
    sensor.props.onDragOver(event)
    sensor.props.onDrop(event)
    if (edge === "replace") assert.equal(current.selectedChat, "incoming")
    else if (edge === "left" || edge === "right") {
      assert.equal(current[edge][0].payload, "incoming")
      assert.equal(current[edge === "left" ? "right" : "left"][0].payload, "main")
    } else {
      assert.equal(current.left[edge === "top" ? 0 : 1].payload, "incoming")
      assert.equal(current.right.length, 0)
    }
  })
}
