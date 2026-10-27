import assert from "node:assert/strict"
import { readFileSync } from "node:fs"
import test from "node:test"
import vm from "node:vm"
import { transformSync } from "esbuild"

function loadModule(file, require, globals = {}) {
  const context = { module: { exports: {} }, require, ...globals }
  vm.runInNewContext(transformSync(readFileSync(new URL(file, import.meta.url), "utf8"), { loader: "jsx", format: "cjs" }).code, context)
  return context.module.exports
}
const plain = value => JSON.parse(JSON.stringify(value))
const card = (id, kind = "chat") => ({ id, kind, payload: kind === "chat" ? id : { path: id } })

function closeHarness(layout, { drafts = {}, answers = [], pluginClose = () => Promise.resolve() } = {}) {
  const events = [], selected = [], closes = [], discarded = [], calls = [], prompts = []
  const dragLayout = loadModule("drag-layout.jsx", () => ({}), {
    Image: class {}, window: { CyreneUI: { register: (_name, value) => value } },
  })
  const transforms = loadModule("pane-layout-transforms.jsx", () => dragLayout)
  const controller = loadModule("pane-layout-controller.jsx", name => name.includes("pane-layout-transforms") ? transforms : dragLayout, {
    window: { dispatchEvent: event => events.push(event.type) }, CustomEvent: class { constructor(type) { this.type = type } },
  })
  const owner = "main"
  const context = {
    projectId: "project", activeChatIdRef: { current: owner }, paneLayoutsByChat: { [owner]: layout },
    paneLayoutRestoreRef: { current: {} }, surfaceSuppressionRef: { current: new Map() },
    setPaneLayoutsByChat(update) { this.paneLayoutsByChat = update(this.paneLayoutsByChat) },
    selectChat: id => selected.push(id),
  }
  const dependencies = {
    wbcT: (_key, fallback) => fallback,
    workbenchServices: { feedback: () => ({ confirmModal: options => { prompts.push(options); return Promise.resolve(answers.shift()) } }) },
    WBC_PROJECT_FILE_DRAFTS: drafts,
    wbcProjectFileDraftKey: payload => payload.path,
    wbcDiscardProjectFileDraft: payload => { discarded.push(payload.path); delete drafts[payload.path] },
    PluginFrontendService: { call: (...args) => { calls.push(args); return pluginClose(...args) } },
  }
  const confirmation = loadModule("pane-close-controller.jsx", () => dependencies)
  const onClose = (ids, requestedOwner) => { closes.push(plain(ids)); controller.wbcClosePaneCard(context, ids, requestedOwner) }
  return {
    context, closes, selected, discarded, calls, prompts, events, drafts,
    close: cards => confirmation.wbcClosePaneCardsWithConfirmation({ ...context, closePaneCard: onClose }, cards, owner),
    layout: () => plain(context.paneLayoutsByChat[selected.at(-1) || owner]),
  }
}

test("collapsing a stacked column removes it atomically and promotes a surviving conversation", async () => {
  const main = card("main"), sibling = card("sibling"), survivor = card("survivor")
  const drag = closeHarness({ left: [main, sibling], right: [survivor], leftRatio: 0.3, rightRatio: 0.7 })
  drag.context.paneLayoutRestoreRef.current.sibling = { left: [sibling], right: [] }
  assert.equal((await drag.close([main, sibling])).closed, true)
  assert.deepEqual(drag.closes, [["main", "sibling"]])
  assert.deepEqual(drag.selected, ["survivor"])
  assert.deepEqual(drag.layout().left.map(item => item.id), ["survivor"])
  assert.deepEqual(drag.layout().right, [])
  assert.deepEqual(drag.context.paneLayoutRestoreRef.current, {})
  assert.deepEqual(drag.events, ["workbench:pane-layout-change"])
  assert.equal(drag.context.paneLayoutsByChat.main, undefined)
})

test("closing the right column preserves the left column and both height ratios", async () => {
  const left = [card("main"), card("left-bottom")], right = [card("right-top"), card("right-bottom")]
  const drag = closeHarness({ left, right, leftRatio: 0.3, rightRatio: 0.7 })
  await drag.close(right)
  assert.deepEqual(drag.layout().left.map(item => item.id), ["main", "left-bottom"])
  assert.deepEqual(drag.layout().right, [])
  assert.equal(drag.layout().leftRatio, 0.3)
  assert.equal(drag.layout().rightRatio, 0.7)
  assert.deepEqual(drag.selected, [])
})

test("cancelling any unsaved-file prompt keeps the entire column and every draft", async () => {
  const files = [card("one", "file"), card("two", "viewer")]
  const drafts = { one: "first draft", two: "second draft" }
  const drag = closeHarness({ left: [card("main")], right: files }, { drafts, answers: [true, false] })
  assert.equal((await drag.close(files)).cancelled, true)
  assert.equal(drag.prompts.length, 2)
  assert.deepEqual(drag.discarded, [])
  assert.deepEqual(drag.drafts, { one: "first draft", two: "second draft" })
  assert.deepEqual(drag.closes, [])
  assert.deepEqual(drag.layout().right.map(item => item.id), ["one", "two"])
})

test("confirmed file and automatic-surface drafts are discarded before the column closes", async () => {
  const file = card("file", "file")
  const surface = { id: "surface", kind: "surface", payload: { runId: "run", resourceKey: "key", resource: { kind: "file", path: "surface" } }, meta: { origin: "agent" } }
  const drag = closeHarness({ left: [card("main")], right: [file, surface] }, { drafts: { file: "draft", surface: "draft" }, answers: [true, true] })
  const result = await drag.close([file, surface])
  assert.equal(result.closed, true)
  assert.equal(result.discarded_draft, true)
  assert.deepEqual(drag.discarded, ["file"])
  assert.deepEqual(drag.drafts, {})
  assert.equal(drag.context.surfaceSuppressionRef.current.get("run\nkey"), true)
  assert.deepEqual(drag.closes, [["file", "surface"]])
})

test("single-pane closes retain the existing restore behavior", async () => {
  const pane = card("preview", "file")
  const drag = closeHarness({ left: [card("main")], right: [pane] })
  drag.context.paneLayoutRestoreRef.current.preview = { left: [card("main")], right: [card("previous")], leftRatio: 0.4, rightRatio: 0.6 }
  await drag.close([pane])
  assert.deepEqual(drag.closes, ["preview"])
  assert.deepEqual(drag.layout().right.map(item => item.id), ["previous"])
  assert.equal(drag.layout().leftRatio, 0.4)
})

test("plugin session cleanup is attempted before closing and a cleanup failure still permits closing", async () => {
  const plugin = { id: "plugin", kind: "plugin-view", payload: { packId: "pack", state: { closeMethod: "close", session_id: "session" } } }
  let finish
  const drag = closeHarness({ left: [card("main")], right: [plugin] }, { pluginClose: () => new Promise((_resolve, reject) => { finish = reject }) })
  const closing = drag.close([plugin])
  assert.deepEqual(drag.closes, [])
  assert.deepEqual(plain(drag.calls), [["pack", "close", { session_id: "session" }, "project"]])
  finish(new Error("already closed"))
  assert.equal((await closing).closed, true)
  assert.deepEqual(drag.closes, ["plugin"])
})
