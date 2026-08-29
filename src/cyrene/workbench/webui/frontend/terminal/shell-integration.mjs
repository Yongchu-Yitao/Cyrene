function markerLine(marker) {
  return marker && !marker.isDisposed ? Number(marker.line) : -1;
}

function commandSnapshot(command) {
  return {
    promptLine: markerLine(command.promptMarker),
    commandStartLine: markerLine(command.commandStartMarker),
    outputStartLine: markerLine(command.outputStartMarker),
    outputEndLine: markerLine(command.outputEndMarker),
    exitCode: command.exitCode,
    running: !command.outputEndMarker,
  };
}

function parseExitCode(payload) {
  var value = String(payload || "").split(";")[1] || "";
  return /^-?\d+$/.test(value) ? Number(value) : null;
}

function attachTerminalKeyHandler(terminal, options, jumpPrompt) {
  terminal.attachCustomKeyEventHandler(function (event) {
    if (event.type !== "keydown") return true;
    var key = String(event.key || "");
    if (!event.metaKey && event.ctrlKey && event.shiftKey && key.toLowerCase() === "f") {
      event.preventDefault();
      event.stopPropagation();
      if (typeof options.toggleFullscreen === "function") options.toggleFullscreen();
      return false;
    }
    var isMac = options.isMac == null
      ? typeof navigator !== "undefined" && /Mac|iPhone|iPad|iPod/.test(navigator.platform)
      : Boolean(options.isMac);
    var navigationModifier = isMac
      ? event.metaKey && !event.ctrlKey
      : event.ctrlKey && !event.metaKey;
    if (
      event.shiftKey && !event.altKey && navigationModifier
      && key.toLowerCase() === "o"
    ) {
      event.preventDefault();
      event.stopPropagation();
      if (typeof options.copyLastCommandOutput === "function") options.copyLastCommandOutput();
      return false;
    }
    if (event.shiftKey && navigationModifier && (key === "ArrowUp" || key === "ArrowDown")) {
      event.preventDefault();
      event.stopPropagation();
      jumpPrompt(key === "ArrowUp" ? -1 : 1);
      return false;
    }
    // Keep TUI control keys inside xterm while leaving macOS Command shortcuts
    // available to the Workbench and native window menu.
    if (
      !event.metaKey
      && (event.ctrlKey || event.altKey || event.key === "Escape"
        || event.key === "Tab" || event.key.startsWith("Arrow")
        || /^F\d{1,2}$/.test(event.key))
    ) event.stopPropagation();
    return true;
  });
}

export function installTerminalShellIntegration(terminal, options) {
  options = options || {};
  var promptMarkers = [];
  var commands = [];
  var allMarkers = [];
  var currentCommand = null;
  var lastPromptMarker = null;
  var navigationMarker = null;

  function createMarker() {
    if (terminal.buffer.active.type !== "normal") return null;
    var marker = terminal.registerMarker(0);
    if (marker) allMarkers.push(marker);
    return marker;
  }

  function handleOsc133(payload) {
    var parts = String(payload || "").split(";");
    var kind = parts[0];
    if (kind === "A") {
      lastPromptMarker = createMarker();
      if (lastPromptMarker) promptMarkers.push(lastPromptMarker);
      navigationMarker = null;
    } else if (kind === "B") {
      currentCommand = {
        promptMarker: lastPromptMarker,
        commandStartMarker: createMarker(),
        outputStartMarker: null,
        outputEndMarker: null,
        exitCode: null,
      };
    } else if (kind === "C" && currentCommand) {
      currentCommand.outputStartMarker = createMarker();
    } else if (kind === "D" && currentCommand) {
      currentCommand.outputEndMarker = createMarker();
      currentCommand.exitCode = parseExitCode(payload);
      commands.push(currentCommand);
      currentCommand = null;
    }
    return true;
  }

  function livePrompts() {
    promptMarkers = promptMarkers.filter(function (marker) { return markerLine(marker) >= 0; });
    return promptMarkers;
  }

  function jumpPrompt(direction) {
    var markers = livePrompts();
    var normal = terminal.buffer.normal;
    var anchor = markerLine(navigationMarker);
    if (anchor < 0) anchor = Number(normal.baseY || 0) + Number(normal.cursorY || 0);
    var target = direction < 0
      ? markers.slice().reverse().find(function (marker) { return markerLine(marker) < anchor; })
      : markers.find(function (marker) { return markerLine(marker) > anchor; });
    if (!target) return false;
    navigationMarker = target;
    if (typeof options.beforePromptNavigation === "function") options.beforePromptNavigation();
    terminal.scrollToLine(markerLine(target));
    return true;
  }

  var osc133 = terminal.parser.registerOscHandler(133, handleOsc133);
  attachTerminalKeyHandler(terminal, options, jumpPrompt);
  return {
    getCommands: function () {
      var records = currentCommand ? commands.concat([currentCommand]) : commands;
      return records.map(commandSnapshot);
    },
    getPromptLines: function () { return livePrompts().map(markerLine); },
    previousPrompt: function () { return jumpPrompt(-1); },
    nextPrompt: function () { return jumpPrompt(1); },
    dispose: function () {
      osc133.dispose();
      allMarkers.forEach(function (marker) { if (!marker.isDisposed) marker.dispose(); });
      allMarkers = [];
      promptMarkers = [];
      commands = [];
      currentCommand = null;
    },
  };
}
