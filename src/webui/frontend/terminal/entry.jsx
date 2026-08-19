import { Terminal } from "@xterm/xterm";
import { FitAddon } from "@xterm/addon-fit";
import { SearchAddon } from "@xterm/addon-search";
import { Unicode11Addon } from "@xterm/addon-unicode11";
import { WebLinksAddon } from "@xterm/addon-web-links";
import "@xterm/xterm/css/xterm.css";

var RESIZE_SETTLE_MS = 160;
var TERMINAL_FONT_SIZE = 13;
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

function TerminalPane({ terminalId, onState }) {
  var hostRef = React.useRef(null);
  var terminalRef = React.useRef(null);
  var fitRef = React.useRef(null);
  var socketRef = React.useRef(null);
  var cursorRef = React.useRef(0);
  var reconnectRef = React.useRef(0);
  var [connection, setConnection] = React.useState("connecting");

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
      allowProposedApi: true,
      convertEol: false,
      customGlyphs: true,
      drawBoldTextInBrightColors: true,
      fastScrollModifier: "alt",
      macOptionIsMeta: true,
      minimumContrastRatio: 1,
      rightClickSelectsWord: true,
      scrollOnUserInput: true,
      fontFamily: appearance.fontFamily,
      fontSize: appearance.fontSize,
      fontWeight: "400",
      fontWeightBold: "600",
      lineHeight: 1.22,
      scrollback: 10000,
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
    terminalRef.current = terminal;
    fitRef.current = fit;

    function commitFit() {
      if (disposed || !host.isConnected) return;
      var dimensions;
      try { dimensions = fit.proposeDimensions(); } catch (error) { return; }
      if (!dimensions || dimensions.cols < 20 || dimensions.rows < 5) return;
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
        terminal.refresh(0, terminal.rows - 1);
        setConnection("connected");
        terminal.focus();
      });
    }

    function scheduleReplaySettle(delay) {
      window.clearTimeout(replayRefreshTimer);
      replayRefreshTimer = window.setTimeout(settleReplay, delay);
    }

    function refreshInteractiveScreen() {
      if (disposed || replayRefreshStarted) return;
      replayRefreshStarted = true;
      window.clearTimeout(replayRefreshDeadlineTimer);
      replayRefreshDeadlineTimer = window.setTimeout(settleReplay, 650);
      var socket = socketRef.current;
      if (!socket || socket.readyState !== WebSocket.OPEN) {
        settleReplay();
        return;
      }
      // Full-screen TUIs such as Claude Code use the normal buffer and leave
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
      setConnection("connecting");
      commitFit();
      var socket = new WebSocket(terminalWebSocketUrl(terminalId, cursorRef.current));
      socketRef.current = socket;
      socket.onopen = function () {
        if (disposed) return;
        reconnectRef.current = 0;
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
          }
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
        if (cursorRef.current > start) bytes = bytes.slice(cursorRef.current - start);
        cursorRef.current = end;
        terminal.write(bytes, function () {
          if (replaySettled || replayTarget === null || end < replayTarget) return;
          if (!replayRefreshStarted) refreshInteractiveScreen();
          else scheduleReplaySettle(90);
        });
      };
      socket.onclose = function () {
        if (disposed) return;
        setConnection("disconnected");
        reconnectRef.current += 1;
        reconnectTimer = window.setTimeout(connect, Math.min(4000, 400 * reconnectRef.current));
      };
    }

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
      window.clearTimeout(reconnectTimer);
      window.clearTimeout(resizeTimer);
      window.clearTimeout(replayRefreshTimer);
      window.clearTimeout(replayRefreshDeadlineTimer);
      window.clearTimeout(replayResizeTimer);
      window.cancelAnimationFrame(resizeFrame);
      observer.disconnect();
      appearanceObserver.disconnect();
      input.dispose();
      binaryInput.dispose();
      if (socketRef.current) socketRef.current.close();
      terminal.dispose();
      terminalRef.current = null;
      fitRef.current = null;
    };
  }, [terminalId]);

  return <section className="wbc-terminal-pane" data-connection={connection}>
    <div className="wbc-terminal-connection" role="status">
      {connection === "connecting" ? "Connecting…" : connection === "replaying" ? "Restoring…" : connection === "disconnected" ? "Reconnecting…" : ""}
    </div>
    <div ref={hostRef} className="wbc-terminal-host" onMouseDown={function () {
      if (terminalRef.current) terminalRef.current.focus();
    }} />
  </section>;
}

window.CyreneUI.terminal = window.CyreneUI.register("terminal", {
  Client: TerminalClient,
  Pane: TerminalPane,
});
