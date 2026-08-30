import { workbenchServices } from "../shared/runtime/services.jsx"
import { Terminal } from "@xterm/xterm";
import { FitAddon } from "@xterm/addon-fit";
import { SearchAddon } from "@xterm/addon-search";
import { Unicode11Addon } from "@xterm/addon-unicode11";
import { WebLinksAddon } from "@xterm/addon-web-links";
import { installTerminalShellIntegration } from "./shell-integration.mjs";
import { installTerminalCursorVisibilitySync } from "./cursor-visibility.mjs";
import "@xterm/xterm/css/xterm.css";

var RESIZE_SETTLE_MS = 160;
var TERMINAL_FONT_SIZE = 13;
var TERMINAL_LINE_HEIGHT = 1.14;
var TERMINAL_CURSOR_HEIGHT_RATIO = 0.74;
var TERMINAL_TAIL_COMPACT_LINES = 1;
var TERMINAL_SCROLLBACK_LINES = 100000;

function terminalT(key, fallback, params) {
  return workbenchServices.i18n().t(key, params || null, fallback);
}
var DARK_TERMINAL_THEME = {
  background: "#17181C",
  foreground: "#F1F1F4",
  cursor: "#F1F1F4",
  cursorAccent: "#171819",
  selectionBackground: "#40354F",
  selectionInactiveBackground: "#2A2B2D",
  black: "#171819",
  brightBlack: "#8B8D97",
  red: "#D77657",
  brightRed: "#D77657",
  green: "#A3BE8C",
  brightGreen: "#A3BE8C",
  yellow: "#FEC106",
  brightYellow: "#FEC106",
  blue: "#81A1C1",
  brightBlue: "#88C0D0",
  magenta: "#B48EAD",
  brightMagenta: "#B48EAD",
  cyan: "#88C0D0",
  brightCyan: "#8FBCBB",
  white: "#C2C3CA",
  brightWhite: "#F1F1F4",
};
var LIGHT_TERMINAL_THEME = {
  background: "#FCFDFD",
  foreground: "#171C22",
  cursor: "#171C22",
  cursorAccent: "#FCFDFD",
  selectionBackground: "rgba(77, 154, 120, 0.22)",
  selectionInactiveBackground: "#E7ECEF",
  black: "#171C22",
  brightBlack: "#717C88",
  red: "#B93636",
  brightRed: "#CF5757",
  green: "#2F7D59",
  brightGreen: "#4D9A78",
  yellow: "#946512",
  brightYellow: "#BF8731",
  blue: "#286FA8",
  brightBlue: "#4D87B8",
  magenta: "#6E5A9A",
  brightMagenta: "#7565A7",
  cyan: "#187A8C",
  brightCyan: "#2B91A7",
  white: "#4D5762",
  brightWhite: "#171C22",
};

function terminalAppearance(host) {
  var root = document.documentElement;
  var styleTarget = host && host.closest(".workbench-shell") || root;
  var rootStyles = window.getComputedStyle(root);
  var targetStyles = window.getComputedStyle(styleTarget);
  var fontFamily = String(targetStyles.getPropertyValue("--mono") || rootStyles.getPropertyValue("--mono") || "").trim();
  var fontScale = Number.parseFloat(targetStyles.getPropertyValue("--wb-ui-font-scale"));
  if (!fontFamily) fontFamily = "'IBM Plex Mono', ui-monospace, 'JetBrains Mono', Menlo, Consolas, monospace";
  if (!Number.isFinite(fontScale) || fontScale <= 0) fontScale = 1;
  return {
    fontFamily: fontFamily,
    fontSize: Math.round(TERMINAL_FONT_SIZE * fontScale * 10) / 10,
    theme: root.dataset.theme === "light" ? LIGHT_TERMINAL_THEME : DARK_TERMINAL_THEME,
  };
}

function terminalError(response) {
  return response.json().catch(function () { return {}; }).then(function (payload) {
    throw new Error(String(payload.detail || payload.error || response.statusText || terminalT("terminal.requestFailed", "Terminal request failed")));
  });
}

var TerminalClient = {
  list: function (projectId) {
    return fetch("/api/terminals?projectId=" + encodeURIComponent(projectId || ""), { cache: "no-store" })
      .then(function (response) { return response.ok ? response.json() : terminalError(response); })
      .then(function (payload) { return {
        terminals: Array.isArray(payload.terminals) ? payload.terminals : [],
        activeTerminalId: String(payload.activeTerminalId || ""),
      }; });
  },
  create: function (projectId, options) {
    return fetch("/api/terminals", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(Object.assign({ projectId: projectId }, options || {})),
    }).then(function (response) { return response.ok ? response.json() : terminalError(response); })
      .then(function (payload) { return payload.terminal; });
  },
  rename: function (terminalId, title) {
    return fetch("/api/terminals/" + encodeURIComponent(terminalId), {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ title: title }),
    }).then(function (response) { return response.ok ? response.json() : terminalError(response); })
      .then(function (payload) { return payload.terminal; });
  },
  remove: function (terminalId) {
    return fetch("/api/terminals/" + encodeURIComponent(terminalId), { method: "DELETE" })
      .then(function (response) { return response.ok ? response.json() : terminalError(response); });
  },
  restart: function (terminalId) {
    return fetch("/api/terminals/" + encodeURIComponent(terminalId) + "/restart", {
      method: "POST",
    }).then(function (response) { return response.ok ? response.json() : terminalError(response); })
      .then(function (payload) { return payload.terminal; });
  },
  layout: function (projectId, order, pinned) {
    return fetch("/api/terminals/layout", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ projectId: projectId, order: order || [], pinned: pinned || [] }),
    }).then(function (response) { return response.ok ? response.json() : terminalError(response); });
  },
  activate: function (projectId, terminalId) {
    return fetch("/api/terminals/active", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ projectId: projectId, terminalId: terminalId || null }),
    }).then(function (response) { return response.ok ? response.json() : terminalError(response); });
  },
  searchHistory: function (projectId, query, options) {
    var params = new URLSearchParams({ projectId: projectId || "", q: query || "" });
    if (options && options.terminalId) params.set("terminalId", options.terminalId);
    if (options && options.limit) params.set("limit", String(options.limit));
    return fetch("/api/terminals/history/search?" + params.toString(), { cache: "no-store" })
      .then(function (response) { return response.ok ? response.json() : terminalError(response); })
      .then(function (payload) { return Array.isArray(payload.matches) ? payload.matches : []; });
  },
  commands: function (terminalId) {
    return fetch("/api/terminals/" + encodeURIComponent(terminalId) + "/commands", { cache: "no-store" })
      .then(function (response) { return response.ok ? response.json() : terminalError(response); })
      .then(function (payload) { return Array.isArray(payload.commands) ? payload.commands : []; });
  },
  commandOutput: function (terminalId, commandId) {
    return fetch("/api/terminals/" + encodeURIComponent(terminalId) + "/commands/" + encodeURIComponent(commandId) + "/output", { cache: "no-store" })
      .then(function (response) { return response.ok ? response.json() : terminalError(response); });
  },
  historyExportUrl: function (terminalId) {
    return "/api/terminals/" + encodeURIComponent(terminalId) + "/history/export";
  },
};

function copyLastTerminalCommandOutput(terminalId) {
  return TerminalClient.commands(terminalId).then(function (commands) {
    var command = commands.slice().reverse().find(function (item) { return !item.running; });
    if (!command || !command.id) return null;
    return TerminalClient.commandOutput(terminalId, command.id);
  }).then(function (payload) {
    var output = String(payload && payload.text || "");
    if (!output || !navigator.clipboard || !navigator.clipboard.writeText) return null;
    return navigator.clipboard.writeText(output);
  }).catch(function () { return null; });
}

function decodeBase64(value) {
  var binary = window.atob(String(value || ""));
  var bytes = new Uint8Array(binary.length);
  for (var index = 0; index < binary.length; index += 1) bytes[index] = binary.charCodeAt(index);
  return bytes;
}

function encodeBase64(bytes) {
  var binary = "";
  for (var offset = 0; offset < bytes.length; offset += 32768) {
    binary += String.fromCharCode.apply(null, bytes.subarray(offset, offset + 32768));
  }
  return window.btoa(binary);
}

function encodeUtf8Base64(value) {
  return encodeBase64(new TextEncoder().encode(String(value || "")));
}

function encodeBinaryBase64(value) {
  var text = String(value || "");
  var bytes = new Uint8Array(text.length);
  for (var index = 0; index < text.length; index += 1) bytes[index] = text.charCodeAt(index) & 0xff;
  return encodeBase64(bytes);
}

function terminalWebSocketUrl(terminalId, cursor) {
  var scheme = window.location.protocol === "https:" ? "wss:" : "ws:";
  return scheme + "//" + window.location.host + "/ws/terminals/" + encodeURIComponent(terminalId)
    + "?cursor=" + encodeURIComponent(String(cursor || 0));
}

function terminalExitMessage(terminal) {
  var code = terminal && terminal.exitCode;
  var reason = String(terminal && terminal.exitReason || "");
  if (reason === "signal") return code == null
    ? terminalT("terminal.exit.signal", "The PTY was terminated by a system signal.")
    : terminalT("terminal.exit.signalWithCode", "The PTY was terminated by system signal {code}.", { code: code });
  if (reason === "pty_lost") return terminalT("terminal.exit.ptyLost", "The PTY connection was lost unexpectedly. Scrollback has been preserved.");
  if (reason === "recovery_failed" || reason === "restart_failed") return terminalT("terminal.exit.recoveryFailed", "Terminal recovery failed. Scrollback has been preserved.");
  if (reason.indexOf("_interrupted") >= 0) return terminalT("terminal.exit.updateInterrupted", "The terminal process was interrupted while the app or daemon was updating.");
  return code == null
    ? terminalT("terminal.exit.generic", "The terminal process exited.")
    : terminalT("terminal.exit.withCode", "The terminal process exited with code {code}.", { code: code });
}

function terminalRecoveryMessage(terminal) {
  var reason = String(terminal && terminal.recoveryReason || "");
  if (reason === "app_upgrade") return terminalT("terminal.recovery.appUpgrade", "Reconnected after the app upgrade. The name, directory, and scrollback were preserved, and the interactive shell was restarted.");
  if (reason === "daemon_restart") return terminalT("terminal.recovery.daemonRestart", "Recovered after the terminal daemon interruption. The name, directory, and scrollback were preserved, and the interactive shell was restarted.");
  if (reason === "pty_restart") return terminalT("terminal.recovery.ptyRestart", "The terminal was restarted. Its name, working directory, and scrollback were preserved.");
  return "";
}

var shownTerminalRecoveryNotices = new Set();
var shownTerminalExitNotices = new Set();

function showTerminalToast(message, type, options) {
  if (!message) return false;
  try {
    var feedback = workbenchServices.feedback();
    if (feedback && typeof feedback.showToast === "function") {
      feedback.showToast(String(message), type || "info", options || {});
      return true;
    }
  } catch (error) {}
  return false;
}

function showTerminalRecoveryToast(terminal) {
  var recoveredAt = String(terminal && terminal.recoveredAt || "");
  var message = terminalRecoveryMessage(terminal);
  if (!recoveredAt || !message) return;
  var key = String(terminal && terminal.id || "") + ":" + recoveredAt;
  if (shownTerminalRecoveryNotices.has(key)) return;
  if (showTerminalToast(terminalT("terminal.recovered", "Terminal recovered"), "success", { duration: 5200 })) {
    shownTerminalRecoveryNotices.add(key);
  }
}

function showTerminalExitToast(terminal, onRestart) {
  if (!terminal || String(terminal.status || "") !== "exited") return;
  // One-shot terminals are command output consoles: exiting is their expected
  // lifecycle, and the owning workspace action reports success or failure.
  if (String(terminal.launchMode || "") === "one_shot") return;
  var key = [
    String(terminal.id || ""),
    String(terminal.updatedAt || ""),
    String(terminal.exitReason || ""),
    String(terminal.exitCode == null ? "" : terminal.exitCode),
  ].join(":");
  if (shownTerminalExitNotices.has(key)) return;
  var recoverable = Boolean(terminal.recoverable) && typeof onRestart === "function";
  if (showTerminalToast(
    terminalT("terminal.exitedWithReason", "Terminal exited: {reason}", { reason: terminalExitMessage(terminal) }),
    "warning",
    {
      duration: recoverable ? 0 : 6000,
      actionLabel: recoverable ? terminalT("terminal.restart", "Restart") : "",
      onAction: recoverable ? onRestart : null,
    },
  )) shownTerminalExitNotices.add(key);
}

function TerminalPane({ terminalId, onState }) {
  var dataStore = workbenchServices.data();
  dataStore.useVersion();
  var pluginModules = Array.isArray(dataStore.state.pluginModules)
    ? dataStore.state.pluginModules : [];
  var codeAvailable = pluginModules.indexOf("code") >= 0;
  var paneRef = React.useRef(null);
  var hostRef = React.useRef(null);
  var terminalRef = React.useRef(null);
  var fitRef = React.useRef(null);
  var socketRef = React.useRef(null);
  var cursorRef = React.useRef(0);
  var statusRef = React.useRef("starting");
  var inputReadyRef = React.useRef(false);
  var reconnectRef = React.useRef(0);
  var retryNowRef = React.useRef(null);
  var statusToastRef = React.useRef(0);
  var [connection, setConnection] = React.useState("connecting");
  var [fullscreen, setFullscreen] = React.useState(false);
  var [reconnectAttempt, setReconnectAttempt] = React.useState(0);
  var [hasContent, setHasContent] = React.useState(false);
  var [restartBusy, setRestartBusy] = React.useState(false);
  var [restartError, setRestartError] = React.useState("");

  React.useLayoutEffect(function () {
    var host = hostRef.current;
    if (!codeAvailable || !host || !terminalId) return undefined;
    // The ref survives a prop change even though this effect replaces xterm.
    // A new terminal must never inherit another terminal's replay cursor.
    cursorRef.current = 0;
    setHasContent(false);
    var disposed = false;
    var reconnectTimer = 0;
    var resizeTimer = 0;
    var resizeFrame = 0;
    var lastHostWidth = -1;
    var lastHostHeight = -1;
    var lastSentCols = 0;
    var lastSentRows = 0;
    var replayTarget = null;
    var replaySettled = false;
    var replayRefreshStarted = false;
    var replayRefreshTimer = 0;
    var replayRefreshDeadlineTimer = 0;
    var replayResizeTimer = 0;
    var replayGeometryActive = false;
    var replayGeometryCols = 0;
    var replayGeometryRows = 0;
    var replayNeedsFullRefresh = false;
    var desiredCols = 0;
    var desiredRows = 0;
    var bufferRestoreFrame = 0;
    var lastInputEventCount = null;
    var pendingUserInputMarker = null;
    var lastInteractionMarker = null;
    var lastInteractionFits = false;
    var reduceMotionQuery = window.matchMedia
      ? window.matchMedia("(prefers-reduced-motion: reduce)")
      : null;
    var appearance = terminalAppearance(host);
    var appearanceSignature = "";
    var terminal = new Terminal({
      cursorBlink: true,
      cursorStyle: "bar",
      cursorWidth: 2,
      cursorInactiveStyle: "none",
      allowProposedApi: true,
      convertEol: false,
      customGlyphs: true,
      drawBoldTextInBrightColors: true,
      fastScrollModifier: "alt",
      fastScrollSensitivity: 5,
      macOptionClickForcesSelection: true,
      macOptionIsMeta: true,
      minimumContrastRatio: 1,
      rightClickSelectsWord: false,
      scrollOnUserInput: true,
      // Wheel/trackpad scrolling must stay direct. Automatic layout movement
      // is animated separately so repeated input never queues animations.
      smoothScrollDuration: 0,
      fontFamily: appearance.fontFamily,
      fontSize: appearance.fontSize,
      fontWeight: "400",
      fontWeightBold: "600",
      lineHeight: TERMINAL_LINE_HEIGHT,
      scrollback: TERMINAL_SCROLLBACK_LINES,
      theme: appearance.theme,
    });
    var fit = new FitAddon();
    var unicode11 = new Unicode11Addon();
    terminal.loadAddon(fit);
    terminal.loadAddon(new SearchAddon());
    terminal.loadAddon(unicode11);
    terminal.unicode.activeVersion = "11";
    terminal.loadAddon(new WebLinksAddon());
    terminal.open(host);
    var terminalScreen = host.querySelector(".xterm-screen");
    var customCursor = document.createElement("div");
    customCursor.className = "wbc-terminal-input-cursor";
    customCursor.setAttribute("aria-hidden", "true");
    if (terminalScreen) terminalScreen.appendChild(customCursor);
    var tailSpacer = document.createElement("div");
    tailSpacer.className = "wbc-terminal-tail-spacer";
    tailSpacer.setAttribute("aria-hidden", "true");
    host.appendChild(tailSpacer);
    var tailBaseOverflow = Math.max(0, host.scrollHeight - host.clientHeight);
    var shellIntegration = installTerminalShellIntegration(terminal, {
      beforePromptNavigation: function () { host.scrollTop = 0; },
      copyLastCommandOutput: function () { copyLastTerminalCommandOutput(terminalId); },
      toggleFullscreen: function () {
        setFullscreen(function (value) { return !value; });
      },
    });
    function isNativeCursorHidden() {
      var core = terminal._core;
      return Boolean(core && core.coreService && core.coreService.isCursorHidden);
    }

    function updateInputCursor() {
      if (!terminalScreen || !terminalScreen.isConnected) return;
      var focused = Boolean(terminal.element && terminal.element.classList.contains("focus"));
      var activeBuffer = terminal.buffer.active;
      var rect = terminalScreen.getBoundingClientRect();
      var cellWidth = terminal.cols > 0 ? rect.width / terminal.cols : 0;
      var cellHeight = terminal.rows > 0 ? rect.height / terminal.rows : 0;
      var cursorRow = activeBuffer
        ? activeBuffer.cursorY + activeBuffer.baseY - activeBuffer.viewportY
        : -1;
      var visible = focused && !isNativeCursorHidden()
        && cellWidth > 0 && cellHeight > 0
        && cursorRow >= 0 && cursorRow < terminal.rows;
      customCursor.classList.toggle("is-visible", visible);
      if (!visible) return;
      var cursorHeight = Math.max(8, Math.round(cellHeight * TERMINAL_CURSOR_HEIGHT_RATIO));
      var cursorColumn = Math.min(Math.max(0, activeBuffer.cursorX), terminal.cols - 1);
      customCursor.style.left = Math.round(cursorColumn * cellWidth) + "px";
      customCursor.style.top = Math.round(
        cursorRow * cellHeight + (cellHeight - cursorHeight) / 2
      ) + "px";
      customCursor.style.height = cursorHeight + "px";
    }

    function scheduleInputCursorUpdate() {
      window.requestAnimationFrame(updateInputCursor);
    }

    // xterm updates isCursorHidden for DECTCEM without necessarily emitting a
    // render or cursor-move event. Observe the mode sequence and synchronize
    // the custom cursor on the next frame, after xterm's built-in handler runs.
    var cursorVisibilitySync = installTerminalCursorVisibilitySync(
      terminal,
      scheduleInputCursorUpdate,
    );

    function handleBufferChange(buffer) {
      window.cancelAnimationFrame(bufferRestoreFrame);
      if (!buffer || buffer.type === "alternate") {
        // The alternate screen owns the whole viewport. Remove normal-buffer
        // overscroll immediately so TUI painting cannot hide behind it.
        tailSpacer.classList.add("is-alternate-buffer");
        tailSpacer.style.height = "0px";
        host.scrollTop = 0;
        lastInteractionFits = false;
        scheduleInputCursorUpdate();
        return;
      }
      // xterm preserves the normal buffer while a TUI uses the alternate
      // screen, but the outer spacer has its own scroll position. Restore both
      // layers together after parsing the leave-alternate-screen sequence.
      bufferRestoreFrame = window.requestAnimationFrame(function () {
        if (disposed || terminal.buffer.active.type !== "normal") return;
        terminal.scrollToBottom();
        // Keep the transition disabled while the spacer receives its final
        // normal-buffer height, then animate only the visible viewport to the
        // latest interaction. This restores `top`/`less` like a native
        // terminal without exposing an older full-screen command output.
        updateTailSpacer();
        var interactionEnd = Math.max(0, host.scrollHeight - host.clientHeight);
        tailSpacer.classList.remove("is-alternate-buffer");
        host.scrollTo({
          top: interactionEnd,
          behavior: reduceMotionQuery && reduceMotionQuery.matches ? "auto" : "smooth",
        });
        terminal.refresh(0, terminal.rows - 1);
        scheduleInputCursorUpdate();
      });
    }

    function handleTerminalFocusChange() {
      scheduleInputCursorUpdate();
    }

    if (terminal.element) {
      terminal.element.addEventListener("focusin", handleTerminalFocusChange);
      terminal.element.addEventListener("focusout", handleTerminalFocusChange);
    }
    var bufferChange = terminal.buffer.onBufferChange(handleBufferChange);

    function createInteractionMarker() {
      if (terminal.buffer.active.type !== "normal") return null;
      return terminal.registerMarker(0);
    }

    function replaceLastInteractionMarker(marker) {
      if (!marker) return;
      if (lastInteractionMarker && lastInteractionMarker !== marker) {
        lastInteractionMarker.dispose();
      }
      lastInteractionMarker = marker;
    }

    function trackUserInputBoundary(data) {
      var value = String(data || "");
      var submits = /[\r\n]/.test(value) || value === "\u0003" || value === "\u0004";
      var printable = value
        .replace(/\u001b\[[0-9;?]*[ -/]*[@-~]/g, "")
        .replace(/[\u0000-\u001f\u007f]/g, "");
      if (!pendingUserInputMarker && (printable || submits)) {
        pendingUserInputMarker = createInteractionMarker();
      }
      if (!submits) return;
      replaceLastInteractionMarker(pendingUserInputMarker || createInteractionMarker());
      pendingUserInputMarker = null;
      updateTailSpacer();
    }

    function trackAgentInputBoundary(terminalState, messageType) {
      var nextCount = Number(terminalState && terminalState.inputEventCount || 0);
      if (lastInputEventCount === null || messageType === "snapshot") {
        lastInputEventCount = nextCount;
        return;
      }
      if (
        nextCount > lastInputEventCount
        && String(terminalState.lastActor || "") === "agent"
      ) {
        replaceLastInteractionMarker(createInteractionMarker());
        updateTailSpacer();
      }
      lastInputEventCount = Math.max(lastInputEventCount, nextCount);
    }

    function updateTailSpacer() {
      var screen = host.querySelector(".xterm-screen");
      var screenHeight = screen ? Number(screen.getBoundingClientRect().height || 0) : 0;
      var measuredLineHeight = terminal.rows > 0 ? screenHeight / terminal.rows : 0;
      var lineHeight = Math.max(1, measuredLineHeight || (
        Number(terminal.options.fontSize || 13) * Number(terminal.options.lineHeight || 1)
      ));
      var activeBuffer = terminal.buffer.active;
      var markerLine = lastInteractionMarker && !lastInteractionMarker.isDisposed
        ? Number(lastInteractionMarker.line)
        : -1;
      var cursorLine = activeBuffer && activeBuffer.type === "normal"
        ? Number(activeBuffer.baseY || 0) + Number(activeBuffer.cursorY || 0)
        : -1;
      var interactionLines = cursorLine >= markerLine && markerLine >= 0
        ? cursorLine - markerLine + 1
        : 0;
      var markerViewportRow = markerLine - Number(activeBuffer && activeBuffer.viewportY || 0);
      // When the latest command fits, the manual scroll endpoint places its
      // input row at the top so its input, output and next prompt are all
      // visible. If it is taller than the viewport, xterm's own bottom-follow
      // behavior wins and keeps the final row exactly at the bottom. Keep the
      // endpoint one row more compact so a short command does not leave an
      // unnecessarily large empty area below its prompt.
      var interactionFits = interactionLines > 0 && interactionLines <= terminal.rows;
      var desiredScrollTop = interactionFits && markerViewportRow >= 0
        ? lineHeight * Math.max(
          0,
          markerViewportRow + 1 - TERMINAL_TAIL_COMPACT_LINES
        )
        : 0;
      // Padding and xterm's own layout can contribute a small amount of base
      // overflow even with no spacer. Subtract it so the interaction's first
      // row lands fully inside the viewport instead of being clipped above it.
      var viewportTail = Math.max(0, desiredScrollTop - tailBaseOverflow);
      tailSpacer.style.height = Math.max(0, viewportTail) + "px";
      if (lastInteractionFits && !interactionFits && host.scrollTop > 0) {
        host.scrollTo({
          top: 0,
          behavior: reduceMotionQuery && reduceMotionQuery.matches ? "auto" : "smooth",
        });
      }
      lastInteractionFits = interactionFits;
    }

    function handleTailWheel(event) {
      if (!event.deltaY || terminal.modes.mouseTrackingMode !== "none") return;
      var activeBuffer = terminal.buffer.active;
      var atBufferBottom = activeBuffer.viewportY >= activeBuffer.baseY;
      if (!(host.scrollTop > 0 || (event.deltaY > 0 && atBufferBottom))) return;
      var maximum = Math.max(0, host.scrollHeight - host.clientHeight);
      var next = Math.max(0, Math.min(maximum, host.scrollTop + event.deltaY));
      if (next === host.scrollTop) return;
      host.scrollTop = next;
      event.preventDefault();
      event.stopPropagation();
    }

    host.addEventListener("wheel", handleTailWheel, { passive: false, capture: true });
    terminalRef.current = terminal;
    fitRef.current = fit;

    function sendResize(cols, rows) {
      var socket = socketRef.current;
      if (
        !socket || socket.readyState !== WebSocket.OPEN
        || (lastSentCols === cols && lastSentRows === rows)
      ) return;
      lastSentCols = cols;
      lastSentRows = rows;
      socket.send(JSON.stringify({ type: "resize", cols: cols, rows: rows }));
    }

    function measureFit() {
      if (disposed || !host.isConnected) return null;
      var dimensions;
      try { dimensions = fit.proposeDimensions(); } catch (error) { return null; }
      if (!dimensions || dimensions.cols < 20 || dimensions.rows < 5) return null;
      desiredCols = dimensions.cols;
      desiredRows = dimensions.rows;
      return dimensions;
    }

    function applyDesiredGeometry() {
      var dimensions = measureFit();
      var cols = dimensions ? dimensions.cols : desiredCols;
      var rows = dimensions ? dimensions.rows : desiredRows;
      if (!cols || !rows) return;
      if (terminal.cols !== cols || terminal.rows !== rows) {
        terminal.resize(cols, rows);
      }
      updateTailSpacer();
      updateInputCursor();
      sendResize(cols, rows);
    }

    function commitFit() {
      var dimensions = measureFit();
      if (!dimensions) return;
      // Raw PTY history contains cursor-addressing operations written for the
      // daemon's saved geometry. Keep that geometry stable until replay ends;
      // otherwise a newly mounted xterm interprets old TUI paint frames using
      // the current pane width and permanently scrambles the reconstructed
      // screen. Resize both sides together only after replay is complete.
      if (replayGeometryActive) return;
      if (terminal.cols !== dimensions.cols || terminal.rows !== dimensions.rows) {
        terminal.resize(dimensions.cols, dimensions.rows);
      }
      updateTailSpacer();
      updateInputCursor();
      sendResize(terminal.cols, terminal.rows);
    }

    function scheduleFit(delay) {
      window.clearTimeout(resizeTimer);
      window.cancelAnimationFrame(resizeFrame);
      resizeTimer = window.setTimeout(function () {
        resizeFrame = window.requestAnimationFrame(commitFit);
      }, typeof delay === "number" ? delay : RESIZE_SETTLE_MS);
    }

    function applyAppearance() {
      if (disposed) return;
      var next = terminalAppearance(host);
      var nextSignature = [
        document.documentElement.dataset.theme || "dark",
        next.fontFamily,
        next.fontSize,
      ].join("|");
      if (nextSignature === appearanceSignature) return;
      appearanceSignature = nextSignature;
      terminal.options.theme = next.theme;
      terminal.options.fontFamily = next.fontFamily;
      terminal.options.fontSize = next.fontSize;
      terminal.refresh(0, terminal.rows - 1);
      scheduleInputCursorUpdate();
      scheduleFit();
    }

    function sendInput(data, binary) {
      if (!data) return;
      if (statusRef.current !== "running" || !inputReadyRef.current) return;
      var socket = socketRef.current;
      if (!socket || socket.readyState !== WebSocket.OPEN) return;
      socket.send(JSON.stringify({
        type: "input",
        encoding: "base64",
        binary: Boolean(binary),
        data: binary ? encodeBinaryBase64(data) : encodeUtf8Base64(data),
      }));
    }

    function settleReplay() {
      if (disposed || replaySettled) return;
      replaySettled = true;
      window.requestAnimationFrame(function () {
        if (disposed) return;
        terminal.scrollToBottom();
        updateTailSpacer();
        // A full-screen TUI can leave DECTCEM disabled in durable scrollback.
        // Re-enable it after replay so a live interactive shell always gets
        // an input cursor; later live TUI output can still hide it explicitly.
        terminal.write("\u001b[?25h");
        terminal.refresh(0, terminal.rows - 1);
        var interactive = statusRef.current === "running";
        if (interactive && reconnectRef.current > 0) {
          showTerminalToast(
            terminalT("terminal.connectionRecovered", "The terminal connection was restored and output from the interruption has been replayed."),
            "success",
            { duration: 4200 },
          );
        }
        reconnectRef.current = 0;
        setReconnectAttempt(0);
        inputReadyRef.current = interactive;
        setConnection(interactive ? "connected" : "exited");
      });
    }

    function scheduleReplaySettle(delay) {
      window.clearTimeout(replayRefreshTimer);
      replayRefreshTimer = window.setTimeout(settleReplay, delay);
    }

    function refreshInteractiveScreen() {
      if (disposed || replayRefreshStarted) return;
      replayRefreshStarted = true;
      replayGeometryActive = false;
      applyDesiredGeometry();
      if (statusRef.current !== "running") {
        settleReplay();
        return;
      }
      window.clearTimeout(replayRefreshDeadlineTimer);
      replayRefreshDeadlineTimer = window.setTimeout(settleReplay, 650);
      var socket = socketRef.current;
      if (!socket || socket.readyState !== WebSocket.OPEN) {
        settleReplay();
        return;
      }
      if (!replayNeedsFullRefresh) {
        scheduleReplaySettle(90);
        return;
      }
      // A retained log can begin midway through a TUI paint frame. In that
      // exceptional case, pulse SIGWINCH after replay so the live process emits
      // an authoritative screen. Mirror the temporary geometry in xterm so
      // output produced for the pulse is never parsed at a different width.
      var actualCols = terminal.cols;
      var actualRows = terminal.rows;
      var pulseCols = actualCols > 20 ? actualCols - 1 : actualCols + 1;
      terminal.resize(pulseCols, actualRows);
      socket.send(JSON.stringify({ type: "resize", cols: pulseCols, rows: actualRows }));
      replayResizeTimer = window.setTimeout(function () {
        var current = socketRef.current;
        if (!current || current.readyState !== WebSocket.OPEN) return;
        terminal.resize(actualCols, actualRows);
        current.send(JSON.stringify({ type: "resize", cols: actualCols, rows: actualRows }));
      }, 32);
      scheduleReplaySettle(180);
    }

    function handleSocketOpen() {
        if (disposed) return;
        replayTarget = null;
        replaySettled = false;
        replayRefreshStarted = false;
        replayGeometryActive = false;
        replayGeometryCols = 0;
        replayGeometryRows = 0;
        replayNeedsFullRefresh = false;
        window.clearTimeout(replayRefreshTimer);
        window.clearTimeout(replayRefreshDeadlineTimer);
        window.clearTimeout(replayResizeTimer);
        setConnection("replaying");
        lastSentCols = 0;
        lastSentRows = 0;
        scheduleFit();
      }

    function handleSocketMessage(socket, event) {
        var message;
        try { message = JSON.parse(event.data); } catch (error) { return; }
        if (message.type === "resync_required") {
          socket.close(4001, "terminal output backlog");
          return;
        }
        if (message.type === "snapshot" || message.type === "state") {
          if (message.terminal) {
            trackAgentInputBoundary(message.terminal, message.type);
            statusRef.current = String(message.terminal.status || "exited");
            showTerminalRecoveryToast(message.terminal);
            showTerminalExitToast(message.terminal, restartTerminal);
            if (statusRef.current !== "running") inputReadyRef.current = false;
            var oldest = Number(message.terminal.oldestSeq || 0);
            if (message.type === "snapshot" && cursorRef.current < oldest) {
              terminal.reset();
              cursorRef.current = oldest;
              replayNeedsFullRefresh = true;
            }
            if (message.type === "snapshot") {
              replayGeometryCols = Math.max(20, Number(message.terminal.cols || terminal.cols));
              replayGeometryRows = Math.max(5, Number(message.terminal.rows || terminal.rows));
              replayGeometryActive = true;
              if (
                terminal.cols !== replayGeometryCols
                || terminal.rows !== replayGeometryRows
              ) terminal.resize(replayGeometryCols, replayGeometryRows);
              replayTarget = Number(message.terminal.nextSeq || 0);
              if (cursorRef.current >= replayTarget) refreshInteractiveScreen();
            }
            if (onState) onState(message.terminal);
            if (message.reason === "metadata") return;
            if (
              message.type === "state" && statusRef.current === "running"
              && replaySettled
            ) {
              inputReadyRef.current = true;
              setConnection("connected");
              terminal.scrollToBottom();
              terminal.write("\u001b[?25h");
            } else if (message.type === "state" && statusRef.current === "exited") {
              setConnection("exited");
            }
          }
          return;
        }
        if (message.type === "error") {
          if (message.terminal) {
            statusRef.current = String(message.terminal.status || "exited");
            inputReadyRef.current = false;
            showTerminalExitToast(message.terminal, restartTerminal);
            if (onState) onState(message.terminal);
          }
          if (message.code === "not_found") setConnection("missing");
          else if (message.code === "terminal_not_running") setConnection("exited");
          return;
        }
        if (message.type === "replay_complete") {
          var replayEnd = Number(message.nextSeq || cursorRef.current);
          // Missing historical bytes must not trap the pane in restoring or
          // make the first live output look like a permanent sequence gap.
          if (cursorRef.current < replayEnd) cursorRef.current = replayEnd;
          replayTarget = replayEnd;
          refreshInteractiveScreen();
          return;
        }
        if (message.type !== "output") return;
        var start = Number(message.seq || 0);
        var end = Number(message.nextSeq || start);
        if (end <= cursorRef.current) return;
        if (start > cursorRef.current) {
          socket.close(4001, "terminal output gap");
          return;
        }
        var bytes = decodeBase64(message.data);
        setHasContent(true);
        if (cursorRef.current > start) bytes = bytes.slice(cursorRef.current - start);
        cursorRef.current = end;
        terminal.write(bytes, function () {
          updateTailSpacer();
          if (replaySettled || replayTarget === null || end < replayTarget) return;
          if (!replayRefreshStarted) refreshInteractiveScreen();
          else scheduleReplaySettle(90);
        });
      }

    function handleSocketClose(event) {
        if (disposed) return;
        inputReadyRef.current = false;
        if (event.code === 4404) {
          setConnection("missing");
          return;
        }
        if (statusRef.current === "exited" || statusRef.current === "closed") {
          setConnection("exited");
          return;
        }
        setConnection("disconnected");
        reconnectRef.current += 1;
        setReconnectAttempt(reconnectRef.current);
        if (window.navigator && window.navigator.onLine === false) {
          setConnection("offline");
        }
        var delay = Math.min(15000, 400 * Math.pow(2, Math.min(5, reconnectRef.current - 1)));
        reconnectTimer = window.setTimeout(connect, delay);
      }

    function connect() {
      if (disposed) return;
      inputReadyRef.current = false;
      setConnection(reconnectRef.current > 0 ? "disconnected" : "connecting");
      commitFit();
      var socket = new WebSocket(terminalWebSocketUrl(terminalId, cursorRef.current));
      socketRef.current = socket;
      socket.onopen = handleSocketOpen;
      socket.onmessage = function (event) { handleSocketMessage(socket, event); };
      socket.onclose = handleSocketClose;
    }

    function retryNow() {
      if (disposed) return;
      window.clearTimeout(reconnectTimer);
      var current = socketRef.current;
      if (current && current.readyState <= WebSocket.OPEN) {
        current.onclose = null;
        current.close(4000, "manual retry");
      }
      socketRef.current = null;
      reconnectRef.current = Math.max(1, reconnectRef.current);
      setReconnectAttempt(reconnectRef.current);
      connect();
    }

    function handleOnline() { retryNow(); }
    function handleOffline() {
      inputReadyRef.current = false;
      setConnection("offline");
    }

    retryNowRef.current = retryNow;
    window.addEventListener("online", handleOnline);
    window.addEventListener("offline", handleOffline);

    var input = terminal.onData(function (data) {
      trackUserInputBoundary(data);
      sendInput(data, false);
    });
    var binaryInput = terminal.onBinary(function (data) {
      sendInput(data, true);
    });
    var cursorMove = terminal.onCursorMove(scheduleInputCursorUpdate);
    var cursorRender = terminal.onRender(scheduleInputCursorUpdate);
    var cursorScroll = terminal.onScroll(scheduleInputCursorUpdate);
    var cursorResize = terminal.onResize(scheduleInputCursorUpdate);
    var observer = new ResizeObserver(function (entries) {
      var rect = entries[0] && entries[0].contentRect;
      if (!rect) return;
      var width = Math.round(rect.width);
      var height = Math.round(rect.height);
      if (width === lastHostWidth && height === lastHostHeight) return;
      lastHostWidth = width;
      lastHostHeight = height;
      scheduleFit();
    });
    observer.observe(host);
    var appearanceObserver = new MutationObserver(applyAppearance);
    appearanceObserver.observe(document.documentElement, {
      attributes: true,
      attributeFilter: ["data-theme", "data-text-size"],
    });
    applyAppearance();
    connect();
    scheduleFit();
    if (document.fonts && document.fonts.ready) {
      document.fonts.ready.then(function () {
        if (disposed) return;
        terminal.refresh(0, terminal.rows - 1);
        scheduleFit();
      });
    }
    return function () {
      disposed = true;
      retryNowRef.current = null;
      window.removeEventListener("online", handleOnline);
      window.removeEventListener("offline", handleOffline);
      window.clearTimeout(reconnectTimer);
      window.clearTimeout(resizeTimer);
      window.clearTimeout(replayRefreshTimer);
      window.clearTimeout(replayRefreshDeadlineTimer);
      window.clearTimeout(replayResizeTimer);
      window.cancelAnimationFrame(resizeFrame);
      window.cancelAnimationFrame(bufferRestoreFrame);
      observer.disconnect();
      appearanceObserver.disconnect();
      host.removeEventListener("wheel", handleTailWheel, { capture: true });
      if (terminal.element) {
        terminal.element.removeEventListener("focusin", handleTerminalFocusChange);
        terminal.element.removeEventListener("focusout", handleTerminalFocusChange);
      }
      input.dispose();
      binaryInput.dispose();
      cursorMove.dispose();
      cursorRender.dispose();
      cursorScroll.dispose();
      cursorResize.dispose();
      cursorVisibilitySync.dispose();
      bufferChange.dispose();
      shellIntegration.dispose();
      if (socketRef.current) socketRef.current.close();
      if (pendingUserInputMarker) pendingUserInputMarker.dispose();
      if (lastInteractionMarker) lastInteractionMarker.dispose();
      customCursor.remove();
      terminal.dispose();
      tailSpacer.remove();
      terminalRef.current = null;
      fitRef.current = null;
    };
  }, [terminalId, codeAvailable]);

  function restartTerminal() {
    if (!codeAvailable || restartBusy) return;
    setRestartBusy(true);
    setRestartError("");
    TerminalClient.restart(terminalId).then(function (terminal) {
      statusRef.current = String(terminal.status || "running");
      if (onState) onState(terminal);
      inputReadyRef.current = statusRef.current === "running";
      setConnection(statusRef.current === "running" ? "connected" : "exited");
      if (terminalRef.current) {
        terminalRef.current.scrollToBottom();
      }
    }).catch(function (error) {
      setRestartError(String(error && error.message || terminalT("terminal.restartFailed", "Terminal restart failed")));
      setConnection("exited");
    }).finally(function () {
      setRestartBusy(false);
    });
  }

  var notice = null;
  if (!codeAvailable) {
    notice = null;
  } else if (restartError) {
    notice = { kind: "error", title: terminalT("terminal.recoveryFailed", "Terminal recovery failed"), detail: restartError, retry: true };
  } else if (connection === "connecting") {
    notice = { kind: "loading", title: terminalT("terminal.connecting", "Connecting to the terminal…") };
  } else if (connection === "replaying") {
    notice = { kind: "loading", title: terminalT("terminal.restoring", "Restoring the terminal session…"), detail: terminalT("terminal.restoringInputHint", "Input will be enabled after restoration finishes.") };
  } else if (connection === "disconnected") {
    notice = { kind: "warning", title: terminalT("terminal.disconnected", "Terminal connection interrupted"), detail: terminalT("terminal.reconnectingAttempt", "Automatic reconnect attempt {attempt}; the current view and scroll position are preserved.", { attempt: reconnectAttempt }), reconnect: true };
  } else if (connection === "offline") {
    notice = { kind: "warning", title: terminalT("common.networkUnavailable", "Network unavailable"), detail: terminalT("terminal.offlineHint", "The terminal will reconnect when the network returns, or you can retry now."), reconnect: true };
  } else if (connection === "missing") {
    notice = { kind: "error", title: terminalT("terminal.missing", "Terminal not found"), detail: terminalT("terminal.missingHint", "This terminal may have been deleted in another window.") };
  }

  React.useEffect(function () {
    var feedback;
    try { feedback = workbenchServices.feedback(); } catch (error) {}
    if (!feedback || typeof feedback.showToast !== "function") return;
    if (!notice) {
      if (statusToastRef.current && typeof feedback.dismissToast === "function") {
        feedback.dismissToast(statusToastRef.current);
      }
      statusToastRef.current = 0;
      return;
    }
    var actionLabel = notice.reconnect
      ? terminalT("common.retryNow", "Retry now")
      : (notice.retry ? (restartBusy ? terminalT("terminal.restarting", "Restarting…") : terminalT("terminal.restart", "Restart")) : "");
    var onAction = notice.reconnect
      ? function () { if (retryNowRef.current) retryNowRef.current(); }
      : (notice.retry && !restartBusy ? restartTerminal : null);
    statusToastRef.current = feedback.showToast(
      notice.title + (notice.detail ? ": " + notice.detail : ""),
      notice.kind === "loading" ? "info" : notice.kind,
      {
        key: "terminal-status:" + String(terminalId || ""),
        duration: notice.kind === "error" && !onAction ? 6000 : 0,
        actionLabel: actionLabel,
        onAction: onAction,
      },
    );
  }, [terminalId, connection, reconnectAttempt, restartError, restartBusy, codeAvailable]);

  React.useEffect(function () {
    return function () {
      if (!statusToastRef.current) return;
      try {
        var feedback = workbenchServices.feedback();
        if (feedback && typeof feedback.dismissToast === "function") {
          feedback.dismissToast(statusToastRef.current);
        }
      } catch (error) {}
      statusToastRef.current = 0;
    };
  }, [terminalId]);

  if (!codeAvailable) return null;

  return <section
    ref={paneRef}
    className={"wbc-terminal-pane" + (fullscreen ? " is-fullscreen" : "")}
    data-terminal-id={String(terminalId || "")}
    data-connection={connection}
    data-has-content={hasContent ? "true" : "false"}
  >
    <div ref={hostRef} className="wbc-terminal-host" onMouseDown={function () {
      if (terminalRef.current) terminalRef.current.focus();
    }} />
  </section>;
}

window.CyreneUI.terminal = window.CyreneUI.register("terminal", {
  Client: TerminalClient,
  Pane: TerminalPane,
});
