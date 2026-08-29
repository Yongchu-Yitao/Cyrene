function includesCursorVisibilityMode(params) {
  return (Array.isArray(params) ? params : []).some(function (param) {
    return Array.isArray(param) ? param.indexOf(25) >= 0 : param === 25;
  });
}

export function installTerminalCursorVisibilitySync(terminal, scheduleUpdate) {
  function handleCursorVisibilityMode(params) {
    if (includesCursorVisibilityMode(params)) scheduleUpdate();
    // Let xterm's built-in DECSET/DECRST handlers update isCursorHidden.
    return false;
  }

  var showCursor = terminal.parser.registerCsiHandler(
    { prefix: "?", final: "h" },
    handleCursorVisibilityMode,
  );
  var hideCursor = terminal.parser.registerCsiHandler(
    { prefix: "?", final: "l" },
    handleCursorVisibilityMode,
  );

  return {
    dispose: function () {
      showCursor.dispose();
      hideCursor.dispose();
    },
  };
}
