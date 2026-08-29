import assert from "node:assert/strict"
import test from "node:test"

import { installTerminalShellIntegration } from "../terminal/shell-integration.mjs"


function fakeTerminal() {
  var oscHandlers = new Map()
  var terminal = {
    currentLine: 0,
    keyHandler: null,
    scrolledTo: [],
    buffer: {
      active: { type: "normal" },
      normal: { baseY: 0, cursorY: 0 },
    },
    parser: {
      registerOscHandler: function (id, handler) {
        oscHandlers.set(id, handler)
        return { dispose: function () { oscHandlers.delete(id) } }
      },
    },
    registerMarker: function () {
      return {
        line: terminal.currentLine,
        isDisposed: false,
        dispose: function () { this.isDisposed = true; this.line = -1 },
      }
    },
    attachCustomKeyEventHandler: function (handler) { terminal.keyHandler = handler },
    scrollToLine: function (line) { terminal.scrolledTo.push(line) },
    emitOsc133: function (payload, line) {
      terminal.currentLine = line
      terminal.buffer.normal.baseY = Math.max(0, line - 5)
      terminal.buffer.normal.cursorY = line - terminal.buffer.normal.baseY
      return oscHandlers.get(133)(payload)
    },
  }
  return terminal
}

function keyEvent(key, modifiers) {
  return Object.assign({
    type: "keydown",
    key: key,
    metaKey: false,
    ctrlKey: false,
    altKey: false,
    shiftKey: false,
    prevented: false,
    stopped: false,
    preventDefault: function () { this.prevented = true },
    stopPropagation: function () { this.stopped = true },
  }, modifiers || {})
}


test("OSC 133 replay records prompt and command boundaries with exit status", () => {
  var terminal = fakeTerminal()
  var integration = installTerminalShellIntegration(terminal)

  assert.equal(terminal.emitOsc133("A", 4), true)
  terminal.emitOsc133("B", 4)
  terminal.emitOsc133("C", 5)
  terminal.emitOsc133("D;7", 8)
  terminal.emitOsc133("A", 9)

  assert.deepEqual(integration.getPromptLines(), [4, 9])
  assert.deepEqual(integration.getCommands(), [{
    promptLine: 4,
    commandStartLine: 4,
    outputStartLine: 5,
    outputEndLine: 8,
    exitCode: 7,
    running: false,
  }])
})


test("prompt navigation uses platform shortcuts without changing existing keys", () => {
  var terminal = fakeTerminal()
  var fullscreenToggles = 0
  var beforeNavigation = 0
  var outputCopies = 0
  var integration = installTerminalShellIntegration(terminal, {
    isMac: true,
    beforePromptNavigation: function () { beforeNavigation += 1 },
    copyLastCommandOutput: function () { outputCopies += 1 },
    toggleFullscreen: function () { fullscreenToggles += 1 },
  })
  terminal.emitOsc133("A", 3)
  terminal.emitOsc133("A", 8)
  terminal.emitOsc133("A", 12)
  terminal.buffer.normal.baseY = 10
  terminal.buffer.normal.cursorY = 2

  var previous = keyEvent("ArrowUp", { metaKey: true, shiftKey: true })
  assert.equal(terminal.keyHandler(previous), false)
  assert.equal(previous.prevented, true)
  assert.equal(previous.stopped, true)
  assert.deepEqual(terminal.scrolledTo, [8])
  assert.equal(integration.previousPrompt(), true)
  assert.equal(integration.nextPrompt(), true)
  assert.deepEqual(terminal.scrolledTo, [8, 3, 8])
  assert.equal(beforeNavigation, 3)

  var fullscreen = keyEvent("f", { ctrlKey: true, shiftKey: true })
  assert.equal(terminal.keyHandler(fullscreen), false)
  assert.equal(fullscreenToggles, 1)

  var copyOutput = keyEvent("O", { metaKey: true, shiftKey: true })
  assert.equal(terminal.keyHandler(copyOutput), false)
  assert.equal(copyOutput.prevented, true)
  assert.equal(copyOutput.stopped, true)
  assert.equal(outputCopies, 1)

  var tuiKey = keyEvent("c", { ctrlKey: true })
  assert.equal(terminal.keyHandler(tuiKey), true)
  assert.equal(tuiKey.prevented, false)
  assert.equal(tuiKey.stopped, true)
})


test("non-mac prompt navigation uses Ctrl and dispose releases parser state", () => {
  var terminal = fakeTerminal()
  var outputCopies = 0
  var integration = installTerminalShellIntegration(terminal, {
    isMac: false,
    copyLastCommandOutput: function () { outputCopies += 1 },
  })
  terminal.emitOsc133("A", 2)
  terminal.emitOsc133("A", 6)
  terminal.buffer.normal.baseY = 4
  terminal.buffer.normal.cursorY = 2

  var previous = keyEvent("ArrowUp", { ctrlKey: true, shiftKey: true })
  assert.equal(terminal.keyHandler(previous), false)
  assert.deepEqual(terminal.scrolledTo, [2])

  var copyOutput = keyEvent("o", { ctrlKey: true, shiftKey: true })
  assert.equal(terminal.keyHandler(copyOutput), false)
  assert.equal(outputCopies, 1)

  integration.dispose()
  assert.deepEqual(integration.getPromptLines(), [])
  assert.equal(integration.getCommands().length, 0)
  assert.throws(function () { terminal.emitOsc133("A", 9) }, TypeError)
})
