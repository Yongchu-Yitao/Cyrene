import assert from "node:assert/strict"
import test from "node:test"

import { installTerminalCursorVisibilitySync } from "../terminal/cursor-visibility.mjs"


function fakeTerminal() {
  var handlers = new Map()
  return {
    handlers: handlers,
    parser: {
      registerCsiHandler: function (identifier, handler) {
        var key = String(identifier.prefix || "") + identifier.final
        handlers.set(key, handler)
        return { dispose: function () { handlers.delete(key) } }
      },
    },
  }
}


test("DECTCEM changes schedule custom cursor synchronization", () => {
  var terminal = fakeTerminal()
  var updates = 0
  var sync = installTerminalCursorVisibilitySync(terminal, function () { updates += 1 })
  var showCursor = terminal.handlers.get("?h")
  var hideCursor = terminal.handlers.get("?l")

  assert.equal(showCursor([25]), false)
  assert.equal(hideCursor([1, 25]), false)
  assert.equal(showCursor([[25]]), false)
  assert.equal(showCursor([1049]), false)
  assert.equal(updates, 3)

  sync.dispose()
  assert.equal(terminal.handlers.size, 0)
})
