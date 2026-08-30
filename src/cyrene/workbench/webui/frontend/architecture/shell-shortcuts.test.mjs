import assert from "node:assert/strict"
import test from "node:test"

import { dispatchWorkbenchGlobalShortcut } from "../features/shell/global-shortcuts.mjs"

function eventFor(action, key) {
  return {
    action: action,
    key: key || "",
    target: { tagName: "DIV", isContentEditable: false },
    metaKey: true,
    ctrlKey: false,
    altKey: false,
    prevented: false,
    preventDefault: function () { this.prevented = true },
  }
}

function harness(state) {
  var calls = []
  var shortcuts = { matches: function (event, action) { return event.action === action } }
  var actions = {
    openSearch: function () { calls.push("search") },
    createChat: function () { calls.push("chat") },
    startVoice: function () { calls.push("voice") },
    openShortcutSettings: function () { calls.push("settings") },
    toggleSidebar: function () { calls.push("sidebar") },
    selectProject: function (id) { calls.push("project:" + id) },
  }
  return { shortcuts: shortcuts, state: Object.assign({ projects: [] }, state), actions: actions, calls: calls }
}

test("shell shortcuts dispatch semantic actions without duplicating navigation", () => {
  var target = harness({ hasActiveProject: true, projects: [{ id: "p1" }, { id: "p2" }] })
  var chatEvent = eventFor("new-chat")
  assert.equal(dispatchWorkbenchGlobalShortcut(chatEvent, target.shortcuts, target.state, target.actions), true)
  assert.equal(chatEvent.prevented, true)
  assert.deepEqual(target.calls, ["chat"])

  dispatchWorkbenchGlobalShortcut(eventFor("settings"), target.shortcuts, target.state, target.actions)
  dispatchWorkbenchGlobalShortcut(eventFor("switch-project", "2"), target.shortcuts, target.state, target.actions)
  assert.deepEqual(target.calls, ["chat", "settings", "project:p2"])
})

test("shell shortcuts respect overlays and editable fields", () => {
  var blocked = harness({ searchOpen: true, hasActiveProject: true })
  assert.equal(dispatchWorkbenchGlobalShortcut(eventFor("new-chat"), blocked.shortcuts, blocked.state, blocked.actions), false)
  assert.deepEqual(blocked.calls, [])

  var target = harness({ hasActiveProject: false })
  var editableEvent = eventFor("search")
  editableEvent.target = { tagName: "INPUT", isContentEditable: false }
  editableEvent.metaKey = false
  assert.equal(dispatchWorkbenchGlobalShortcut(editableEvent, target.shortcuts, target.state, target.actions), false)
})
