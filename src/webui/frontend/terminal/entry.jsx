import { Terminal } from "@xterm/xterm";
import { FitAddon } from "@xterm/addon-fit";
import { SearchAddon } from "@xterm/addon-search";
import { Unicode11Addon } from "@xterm/addon-unicode11";
import { WebLinksAddon } from "@xterm/addon-web-links";
import "@xterm/xterm/css/xterm.css";

var RESIZE_SETTLE_MS = 160;
var TERMINAL_FONT_SIZE = 13;
var TERMINAL_SCROLLBACK_LINES = 100000;
var TERMINAL_TAIL_TOP_RESERVE_LINES = 2;
var DARK_TERMINAL_THEME = {
  background: "#171819",
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
    throw new Error(String(payload.detail || payload.error || response.statusText || "Terminal request failed"));
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
};

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
  if (reason === "signal") return "PTY 被系统信号终止" + (code == null ? "。" : "（" + code + "）。");
  if (reason === "pty_lost") return "PTY 连接意外丢失，滚动记录仍然保留。";
  if (reason === "recovery_failed" || reason === "restart_failed") return "终端恢复失败，滚动记录仍然保留。";
  if (reason.indexOf("_interrupted") >= 0) return "应用或 Daemon 更新时终端进程被中断。";
  return "终端进程已退出" + (code == null ? "。" : "（退出代码 " + code + "）。");
}

function terminalRecoveryMessage(terminal) {
  var reason = String(terminal && terminal.recoveryReason || "");
  if (reason === "app_upgrade") return "应用升级后已重新连接；名称、目录和滚动记录已保留，交互 Shell 已重新启动。";
  if (reason === "daemon_restart") return "Terminal Daemon 中断后已恢复；名称、目录和滚动记录已保留，交互 Shell 已重新启动。";
  if (reason === "pty_restart") return "终端已重新启动；原名称、工作目录和滚动记录均已保留。";
  return "";
}

var shownTerminalRecoveryNotices = new Set();
var shownTerminalExitNotices = new Set();

function showTerminalToast(message, type, options) {
  if (!message) return false;
  try {
    var feedback = window.CyreneUI.require("feedback");
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
  if (showTerminalToast("终端已恢复：" + message, "success", { duration: 5200 })) {
    shownTerminalRecoveryNotices.add(key);
  }
}

function showTerminalExitToast(terminal, onRestart) {
  if (!terminal || String(terminal.status || "") !== "exited") return;
  var key = [
    String(terminal.id || ""),
    String(terminal.updatedAt || ""),
    String(terminal.exitReason || ""),
    String(terminal.exitCode == null ? "" : terminal.exitCode),
  ].join(":");
  if (shownTerminalExitNotices.has(key)) return;
  var recoverable = Boolean(terminal.recoverable) && typeof onRestart === "function";
  if (showTerminalToast(
    "终端已退出：" + terminalExitMessage(terminal),
    "warning",
    {
      duration: recoverable ? 0 : 6000,
      actionLabel: recoverable ? "重新启动" : "",
      onAction: recoverable ? onRestart : null,
    },
  )) shownTerminalExitNotices.add(key);
}

function TerminalPane({ terminalId, onState }) {
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

  React.useEffect(function () {
    var host = hostRef.current;
    if (!host || !terminalId) return undefined;
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
    var appearance = terminalAppearance(host);
    var appearanceSignature = "";
    var terminal = new Terminal({
      cursorBlink: true,
      cursorStyle: "bar",
      cursorWidth: 2,
      cursorInactiveStyle: "outline",
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
      fontFamily: appearance.fontFamily,
      fontSize: appearance.fontSize,
      fontWeight: "400",
      fontWeightBold: "600",
      lineHeight: 1.22,
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
    var tailSpacer = document.createElement("div");
    tailSpacer.className = "wbc-terminal-tail-spacer";
    tailSpacer.setAttribute("aria-hidden", "true");
    host.appendChild(tailSpacer);

    function updateTailSpacer() {
      var lineHeight = Math.max(1, Number(terminal.options.fontSize || 13)
        * Number(terminal.options.lineHeight || 1));
      // Keep enough scroll range for the newest terminal row to rest near the
      // top of the viewport. Reserving two rows preserves the small top inset
      // shown by native terminals while the remaining viewport stays blank.
      var viewportTail = host.clientHeight - (lineHeight * TERMINAL_TAIL_TOP_RESERVE_LINES);
      tailSpacer.style.height = Math.max(lineHeight, viewportTail) + "px";
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
    terminal.attachCustomKeyEventHandler(function (event) {
      if (event.type !== "keydown") return true;
      var key = String(event.key || "");
      if (!event.metaKey && event.ctrlKey && event.shiftKey && key.toLowerCase() === "f") {
        event.preventDefault();
        event.stopPropagation();
        setFullscreen(function (value) { return !value; });
        return false;
      }
      // The Workbench has global Escape/arrow/shortcut handlers. Once the
      // terminal owns focus, keep TUI control keys inside xterm while leaving
      // platform clipboard/window shortcuts (Command on macOS) untouched.
      if (
        !event.metaKey
        && (event.ctrlKey || event.altKey || event.key === "Escape"
          || event.key === "Tab" || event.key.startsWith("Arrow")
          || /^F\d{1,2}$/.test(event.key))
      ) {
        event.stopPropagation();
      }
      return true;
    });
    terminalRef.current = terminal;
    fitRef.current = fit;

    function commitFit() {
      if (disposed || !host.isConnected) return;
      var dimensions;
      try { dimensions = fit.proposeDimensions(); } catch (error) { return; }
      if (!dimensions || dimensions.cols < 20 || dimensions.rows < 5) return;
      updateTailSpacer();
      if (terminal.cols !== dimensions.cols || terminal.rows !== dimensions.rows) {
        terminal.resize(dimensions.cols, dimensions.rows);
      }
      var socket = socketRef.current;
      if (
        socket && socket.readyState === WebSocket.OPEN
        && (lastSentCols !== terminal.cols || lastSentRows !== terminal.rows)
      ) {
        lastSentCols = terminal.cols;
        lastSentRows = terminal.rows;
        socket.send(JSON.stringify({ type: "resize", cols: terminal.cols, rows: terminal.rows }));
      }
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
        host.scrollTop = 0;
        // A full-screen TUI can leave DECTCEM disabled in durable scrollback.
        // Re-enable it after replay so a live interactive shell always gets
        // an input cursor; later live TUI output can still hide it explicitly.
        terminal.write("\u001b[?25h");
        terminal.refresh(0, terminal.rows - 1);
        var interactive = statusRef.current === "running";
        if (interactive && reconnectRef.current > 0) {
          showTerminalToast(
            "终端连接已恢复，断线期间的输出已经补齐。",
            "success",
            { duration: 4200 },
          );
        }
        reconnectRef.current = 0;
        setReconnectAttempt(0);
        inputReadyRef.current = interactive;
        setConnection(interactive ? "connected" : "exited");
        if (interactive) terminal.focus();
      });
    }

    function scheduleReplaySettle(delay) {
      window.clearTimeout(replayRefreshTimer);
      replayRefreshTimer = window.setTimeout(settleReplay, delay);
    }

    function refreshInteractiveScreen() {
      if (disposed || replayRefreshStarted) return;
      replayRefreshStarted = true;
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
      // Full-screen TUIs use the normal buffer and leave
      // prior paint frames in scrollback. Pulse SIGWINCH after replay so the
      // live process emits one authoritative screen at the current geometry.
      // This also works with an older daemon because it uses ordinary resize
      // commands instead of a new protocol action.
      var actualCols = terminal.cols;
      var actualRows = terminal.rows;
      var pulseCols = actualCols > 20 ? actualCols - 1 : actualCols + 1;
      socket.send(JSON.stringify({ type: "resize", cols: pulseCols, rows: actualRows }));
      replayResizeTimer = window.setTimeout(function () {
        var current = socketRef.current;
        if (!current || current.readyState !== WebSocket.OPEN) return;
        current.send(JSON.stringify({ type: "resize", cols: actualCols, rows: actualRows }));
      }, 32);
      scheduleReplaySettle(180);
    }

    function connect() {
      if (disposed) return;
      inputReadyRef.current = false;
      setConnection(reconnectRef.current > 0 ? "disconnected" : "connecting");
      commitFit();
      var socket = new WebSocket(terminalWebSocketUrl(terminalId, cursorRef.current));
      socketRef.current = socket;
      socket.onopen = function () {
        if (disposed) return;
        replayTarget = null;
        replaySettled = false;
        replayRefreshStarted = false;
        window.clearTimeout(replayRefreshTimer);
        window.clearTimeout(replayRefreshDeadlineTimer);
        window.clearTimeout(replayResizeTimer);
        setConnection("replaying");
        lastSentCols = 0;
        lastSentRows = 0;
        scheduleFit();
      };
      socket.onmessage = function (event) {
        var message;
        try { message = JSON.parse(event.data); } catch (error) { return; }
        if (message.type === "snapshot" || message.type === "state") {
          if (message.terminal) {
            statusRef.current = String(message.terminal.status || "exited");
            showTerminalRecoveryToast(message.terminal);
            showTerminalExitToast(message.terminal, restartTerminal);
            if (statusRef.current !== "running") inputReadyRef.current = false;
            var oldest = Number(message.terminal.oldestSeq || 0);
            if (message.type === "snapshot" && cursorRef.current < oldest) {
              terminal.reset();
              cursorRef.current = oldest;
            }
            if (message.type === "snapshot") {
              replayTarget = Number(message.terminal.nextSeq || 0);
              if (cursorRef.current >= replayTarget) refreshInteractiveScreen();
            }
            if (onState) onState(message.terminal);
            if (
              message.type === "state" && statusRef.current === "running"
              && replaySettled
            ) {
              inputReadyRef.current = true;
              setConnection("connected");
              terminal.scrollToBottom();
              terminal.write("\u001b[?25h");
              terminal.focus();
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
          if (replaySettled || replayTarget === null || end < replayTarget) return;
          if (!replayRefreshStarted) refreshInteractiveScreen();
          else scheduleReplaySettle(90);
        });
      };
      socket.onclose = function (event) {
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
      };
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
      sendInput(data, false);
    });
    var binaryInput = terminal.onBinary(function (data) {
      sendInput(data, true);
    });
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
      observer.disconnect();
      appearanceObserver.disconnect();
      host.removeEventListener("wheel", handleTailWheel, { capture: true });
      input.dispose();
      binaryInput.dispose();
      if (socketRef.current) socketRef.current.close();
      terminal.dispose();
      tailSpacer.remove();
      terminalRef.current = null;
      fitRef.current = null;
    };
  }, [terminalId]);

  function restartTerminal() {
    if (restartBusy) return;
    setRestartBusy(true);
    setRestartError("");
    TerminalClient.restart(terminalId).then(function (terminal) {
      statusRef.current = String(terminal.status || "running");
      if (onState) onState(terminal);
      inputReadyRef.current = statusRef.current === "running";
      setConnection(statusRef.current === "running" ? "connected" : "exited");
      if (terminalRef.current) {
        terminalRef.current.scrollToBottom();
        terminalRef.current.focus();
      }
    }).catch(function (error) {
      setRestartError(String(error && error.message || "终端重启失败"));
      setConnection("exited");
    }).finally(function () {
      setRestartBusy(false);
    });
  }

  var notice = null;
  if (restartError) {
    notice = { kind: "error", title: "终端恢复失败", detail: restartError, retry: true };
  } else if (connection === "connecting") {
    notice = { kind: "loading", title: "正在连接终端…" };
  } else if (connection === "replaying") {
    notice = { kind: "loading", title: "正在恢复终端现场…", detail: "输入将在恢复完成后启用。" };
  } else if (connection === "disconnected") {
    notice = { kind: "warning", title: "终端连接已中断", detail: "正在第 " + reconnectAttempt + " 次自动重连；当前画面和滚动位置已保留。", reconnect: true };
  } else if (connection === "offline") {
    notice = { kind: "warning", title: "网络不可用", detail: "网络恢复后会自动重连，也可以立即重试。", reconnect: true };
  } else if (connection === "missing") {
    notice = { kind: "error", title: "终端不存在", detail: "该终端可能已在其他窗口中被删除。" };
  }

  React.useEffect(function () {
    var feedback;
    try { feedback = window.CyreneUI.require("feedback"); } catch (error) {}
    if (!feedback || typeof feedback.showToast !== "function") return;
    if (!notice) {
      if (statusToastRef.current && typeof feedback.dismissToast === "function") {
        feedback.dismissToast(statusToastRef.current);
      }
      statusToastRef.current = 0;
      return;
    }
    var actionLabel = notice.reconnect
      ? "立即重试"
      : (notice.retry ? (restartBusy ? "正在重启…" : "重新启动") : "");
    var onAction = notice.reconnect
      ? function () { if (retryNowRef.current) retryNowRef.current(); }
      : (notice.retry && !restartBusy ? restartTerminal : null);
    statusToastRef.current = feedback.showToast(
      notice.title + (notice.detail ? "：" + notice.detail : ""),
      notice.kind === "loading" ? "info" : notice.kind,
      {
        key: "terminal-status:" + String(terminalId || ""),
        duration: notice.kind === "error" && !onAction ? 6000 : 0,
        actionLabel: actionLabel,
        onAction: onAction,
      },
    );
  }, [terminalId, connection, reconnectAttempt, restartError, restartBusy]);

  React.useEffect(function () {
    return function () {
      if (!statusToastRef.current) return;
      try {
        var feedback = window.CyreneUI.require("feedback");
        if (feedback && typeof feedback.dismissToast === "function") {
          feedback.dismissToast(statusToastRef.current);
        }
      } catch (error) {}
      statusToastRef.current = 0;
    };
  }, [terminalId]);

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
