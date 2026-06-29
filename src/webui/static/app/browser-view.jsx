// Cyrene UI — live browser viewport (M2 screencast + S3 user control)
// Renders the agent's browser screencast (via the /ws/browser WebSocket) plus a
// ribbon describing the latest action. The user can TAKE CONTROL of the live
// view: mouse/keyboard events are forwarded over the same socket and injected
// into the headless page via CDP, so the user drives the very session the agent
// uses — no native window needed. For sites that fingerprint headless (e.g.
// CAPTCHA) a native-window escape hatch (/api/browser/takeover) is offered.
// The agent-initiated login takeover card (M3, browser_request_takeover) still
// appears when the backend emits browser_takeover_request.

function BrowserViewportPanel({ roundId, onClose, onTakeoverComplete, browserState, browserSessionId }) {
  if (typeof window.useDataVersion === "function") window.useDataVersion(); // re-render on DATA.browser updates
  const browser = browserState || ((window.DATA && window.DATA.browser) || {});
  const imgRef = React.useRef(null);
  const stageRef = React.useRef(null);
  const sinkRef = React.useRef(null);      // hidden textarea: keyboard + IME sink
  const composingRef = React.useRef(false); // mid IME composition
  const wsRef = React.useRef(null);
  const moveTsRef = React.useRef(0);
  const objectUrlRef = React.useRef("");
  const [connected, setConnected] = React.useState(false);
  const [error, setError] = React.useState("");
  const [frameUrl, setFrameUrl] = React.useState("");
  const [controlling, setControlling] = React.useState(false);
  const [nativeBusy, setNativeBusy] = React.useState(false);
  // Single source of truth in the shared DATA, so the native-window state stays
  // correct across panel remounts (tab switches) and SSE updates (browser_frame
  // / takeover_cancelled clear it). useDataVersion() re-renders on every bump.
  const nativeWindow = !!browser.userWindow;
  function setNativeWindow(open) {
    if (window.DATA) {
      window.DATA.browser = window.DATA.browser || {};
      window.DATA.browser.userWindow = open;
      const chatId = String(browserSessionId || browser.sessionId || "").trim();
      if (chatId) {
        window.DATA.browserByChat = window.DATA.browserByChat || {};
        window.DATA.browserByChat[chatId] = {
          ...(window.DATA.browserByChat[chatId] || {}),
          userWindow: open,
          sessionId: chatId,
        };
      }
    }
    if (typeof window.bumpData === "function") window.bumpData();
  }
  const [takeoverSubmitting, setTakeoverSubmitting] = React.useState(false);
  const [takeoverError, setTakeoverError] = React.useState("");

  // ---- screencast socket (also the user-control channel) -----------------
  React.useEffect(function () {
    let closed = false;
    let retry = null;

    function connect() {
      try {
        const proto = location.protocol === "https:" ? "wss:" : "ws:";
        const ws = new WebSocket(proto + "//" + location.host + "/ws/browser");
        wsRef.current = ws;
        ws.binaryType = "arraybuffer";
        ws.onopen = function () { if (!closed) { setConnected(true); setError(""); } };
        ws.onmessage = function (ev) {
          if (typeof ev.data !== "string") {
            const img = imgRef.current;
            if (!img) return;
            const blob = ev.data instanceof Blob ? ev.data : new Blob([ev.data], { type: "image/jpeg" });
            const nextUrl = URL.createObjectURL(blob);
            const prevUrl = objectUrlRef.current;
            objectUrlRef.current = nextUrl;
            img.src = nextUrl;
            if (prevUrl) URL.revokeObjectURL(prevUrl);
            return;
          }
          let msg;
          try { msg = JSON.parse(ev.data); } catch (e) { return; }
          if (msg.type === "error") {
            setError(msg.error || "browser unavailable");
            closed = true;
            try { ws.close(); } catch (e) {}
            return;
          }
          if (msg.type === "frame") {
            if (msg.url) setFrameUrl(msg.url);
          }
        };
        ws.onclose = function () {
          if (wsRef.current === ws) wsRef.current = null;
          if (closed) return;
          setConnected(false);
          retry = setTimeout(connect, 1500);
        };
        ws.onerror = function () { try { ws.close(); } catch (e) {} };
      } catch (e) { /* ignore */ }
    }
    connect();

    return function () {
      closed = true;
      if (retry) clearTimeout(retry);
      const ws = wsRef.current;
      if (ws) {
        try { ws.send(JSON.stringify({ type: "control", on: false })); } catch (e) {}
        try { ws.close(); } catch (e) {}
      }
      wsRef.current = null;
      if (objectUrlRef.current) {
        URL.revokeObjectURL(objectUrlRef.current);
        objectUrlRef.current = "";
      }
    };
  }, []);

  function sendWs(obj) {
    const ws = wsRef.current;
    if (ws && ws.readyState === 1) {
      try { ws.send(JSON.stringify(obj)); return true; } catch (e) {}
    }
    return false;
  }

  // ---- user live-control: forward mouse/keyboard to the headless page ----
  function modsOf(e) {
    return (e.altKey ? 1 : 0) | (e.ctrlKey ? 2 : 0) | (e.metaKey ? 4 : 0) | (e.shiftKey ? 8 : 0);
  }
  function buttonOf(e) {
    return e.button === 2 ? "right" : e.button === 1 ? "middle" : "left";
  }
  // Map a DOM event to viewport CSS pixels. The screencast frame is the viewport
  // at scale 1 (maxWidth/maxHeight == viewport), so the JPEG's natural size maps
  // 1:1 to CDP Input coordinates regardless of how the panel scales the <img>.
  function toViewport(e) {
    const img = imgRef.current;
    if (!img || !img.naturalWidth) return null;
    const rect = img.getBoundingClientRect();
    if (!rect.width || !rect.height) return null;
    const x = (e.clientX - rect.left) / rect.width * img.naturalWidth;
    const y = (e.clientY - rect.top) / rect.height * img.naturalHeight;
    if (x < 0 || y < 0 || x > img.naturalWidth || y > img.naturalHeight) return null;
    return { x: Math.round(x), y: Math.round(y) };
  }

  function focusSink() {
    const sink = sinkRef.current;
    if (!sink) return;
    try { sink.focus({ preventScroll: true }); } catch (e) { try { sink.focus(); } catch (e2) {} }
  }

  function startControl() {
    if (!connected || error) return;
    setControlling(true);
    sendWs({ type: "control", on: true });
    setTimeout(focusSink, 0);
  }
  function stopControl() {
    setControlling(false);
    sendWs({ type: "control", on: false });
  }

  // Non-passive wheel listener so we can preventDefault and forward scrolling.
  React.useEffect(function () {
    if (!controlling) return undefined;
    const el = stageRef.current;
    if (!el) return undefined;
    function onWheel(e) {
      e.preventDefault();
      const pt = toViewport(e);
      if (!pt) return;
      sendWs({ type: "mouse", event: "mouseWheel", x: pt.x, y: pt.y, deltaX: e.deltaX, deltaY: e.deltaY, modifiers: modsOf(e) });
    }
    el.addEventListener("wheel", onWheel, { passive: false });
    return function () { el.removeEventListener("wheel", onWheel); };
  }, [controlling]);

  function onMouseDown(e) {
    if (!controlling) return;
    e.preventDefault();
    if (!composingRef.current) focusSink(); // keep keyboard captured after clicking the page
    const pt = toViewport(e); if (!pt) return;
    sendWs({ type: "mouse", event: "mousePressed", x: pt.x, y: pt.y, button: buttonOf(e), clickCount: e.detail || 1, modifiers: modsOf(e) });
  }
  function onMouseUp(e) {
    if (!controlling) return;
    e.preventDefault();
    const pt = toViewport(e); if (!pt) return;
    sendWs({ type: "mouse", event: "mouseReleased", x: pt.x, y: pt.y, button: buttonOf(e), clickCount: e.detail || 1, modifiers: modsOf(e) });
  }
  function onMouseMove(e) {
    if (!controlling) return;
    const now = Date.now();
    if (now - moveTsRef.current < 33) return; // throttle ~30fps
    moveTsRef.current = now;
    const pt = toViewport(e); if (!pt) return;
    sendWs({ type: "mouse", event: "mouseMoved", x: pt.x, y: pt.y, button: "none", modifiers: modsOf(e) });
  }
  function onContextMenu(e) {
    if (controlling) e.preventDefault();
  }

  // Keyboard + IME flow through a hidden, focused <textarea> sink. Printable keys
  // and IME composition are delivered as committed text via Input.insertText (the
  // only way CJK/IME characters get through); "special" keys and shortcuts are
  // forwarded as raw key events so Enter/Backspace/arrows/Ctrl-combos keep their
  // semantics. isComposing-guarded so pinyin keystrokes never leak as Latin.
  function isSpecialKey(e) {
    if (e.ctrlKey || e.metaKey || e.altKey) return true;
    return !(e.key && e.key.length === 1);
  }
  function onSinkKeyDown(e) {
    if (!controlling) return;
    if (e.isComposing || e.keyCode === 229) return; // IME building a candidate
    if (!isSpecialKey(e)) return;                    // printable → onSinkInput sends it
    e.preventDefault();
    sendWs({ type: "key", event: "keyDown", key: e.key, code: e.code, keyCode: e.keyCode || e.which || 0, modifiers: modsOf(e) });
  }
  function onSinkKeyUp(e) {
    if (!controlling) return;
    if (e.isComposing) return;
    if (!isSpecialKey(e)) return;
    e.preventDefault();
    sendWs({ type: "key", event: "keyUp", key: e.key, code: e.code, keyCode: e.keyCode || e.which || 0, modifiers: modsOf(e) });
  }
  function onSinkInput(e) {
    if (!controlling) return;
    const ne = e.nativeEvent || {};
    if (ne.isComposing || composingRef.current) return; // mid-composition → wait for end
    const it = ne.inputType || "";
    if ((it === "insertText" || it === "insertFromPaste") && ne.data) {
      sendWs({ type: "text", text: ne.data });
    }
    if (sinkRef.current) sinkRef.current.value = "";
  }
  function onSinkCompositionStart() { composingRef.current = true; }
  function onSinkCompositionEnd(e) {
    composingRef.current = false;
    const data = (e && e.data) || (e.nativeEvent && e.nativeEvent.data) || "";
    if (data) sendWs({ type: "text", text: data });
    if (sinkRef.current) sinkRef.current.value = "";
  }

  // ---- native-window escape hatch (sites that block headless) -----------
  function openNativeWindow() {
    if (nativeBusy) return;
    setNativeBusy(true);
    setTakeoverError("");
    stopControl();
    fetch("/api/browser/takeover", { method: "POST" })
      .then(function (r) { return r.json().catch(function () { return {}; }); })
      .then(function (d) {
        if (d && d.ok) setNativeWindow(true);
        else setTakeoverError((d && d.error) || "无法打开浏览器窗口");
      })
      .catch(function (e) { setTakeoverError((e && e.message) || "无法打开浏览器窗口"); })
      .finally(function () { setNativeBusy(false); });
  }
  function closeNativeWindow() {
    if (nativeBusy) return;
    setNativeBusy(true);
    fetch("/api/browser/release", { method: "POST" })
      .then(function (r) { return r.json().catch(function () { return {}; }); })
      .then(function () { setNativeWindow(false); })
      .catch(function () {})
      .finally(function () { setNativeBusy(false); });
  }

  // ---- agent-initiated login takeover (M3) ------------------------------
  const takeover = browser.takeover || {};
  const completeLabel = "我已完成登录";

  function completeTakeover() {
    if (takeoverSubmitting) return;
    setTakeoverSubmitting(true);
    setTakeoverError("");

    if (typeof onTakeoverComplete === "function") {
      Promise.resolve(onTakeoverComplete({
        questionId: takeover.questionId || "",
        selectedOption: completeLabel,
        text: completeLabel,
      })).catch(function (e) {
        setTakeoverError((e && e.message) || "提交失败");
      }).finally(function () {
        setTakeoverSubmitting(false);
      });
      return;
    }

    if (!takeover.questionId) {
      setTakeoverError("缺少登录确认问题，请在聊天输入区确认。");
      setTakeoverSubmitting(false);
      return;
    }

    fetch("/api/chat/answer-question", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        question_id: takeover.questionId,
        answer: completeLabel,
        selected_option: completeLabel,
        stream: false,
      }),
    }).then(function (r) {
      if (!r.ok) throw new Error("HTTP " + r.status);
      return r.json().catch(function () { return {}; });
    }).then(function () {
      if (typeof window.refreshSessions === "function") window.refreshSessions();
    }).catch(function (e) {
      setTakeoverError((e && e.message) || "提交失败");
    }).finally(function () {
      setTakeoverSubmitting(false);
    });
  }

  // ---- render ------------------------------------------------------------
  const url = frameUrl || browser.url || "";
  const title = browser.title || "";
  const action = browser.action || "";
  const target = browser.target || "";
  const actionLabel = !action ? "" :
    action === "navigate" ? ("导航到 " + (url || "")) :
    action === "click" ? ("点击了 " + (target || "")) :
    action === "type" ? ("输入到 " + (target || "")) : action;

  const showControls = !error && !takeover.pending && !nativeWindow;
  const barStyle = { display: "flex", alignItems: "center", gap: 8, padding: "6px 10px", borderBottom: "1px solid var(--line)", fontFamily: "var(--mono)", fontSize: 11, color: "var(--text-3)", flexShrink: 0 };
  const stageStyle = { flex: 1, position: "relative", overflow: "auto", display: "flex", alignItems: "flex-start", justifyContent: "center", background: "var(--bg-1)", outline: "none", boxShadow: controlling ? "inset 0 0 0 2px var(--accent, #16a34a)" : "none", cursor: controlling ? "default" : "auto" };
  const ctrlBtnStyle = { fontSize: 11, padding: "2px 8px", borderRadius: 5, border: "1px solid var(--line)", background: "var(--bg-2)", color: "var(--text-2)", cursor: "pointer", whiteSpace: "nowrap" };

  return (
    <div className="browser-view" style={{ flex: 1, display: "flex", flexDirection: "column", overflow: "hidden" }}>
      <div className="browser-view-bar" style={barStyle}>
        <span className={"sa-dot " + (connected ? "running" : "queued")} style={{ width: 6, height: 6 }}></span>
        <span style={{ flex: 1, whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }} title={url}>
          {title ? (title + " — ") : ""}{url || "浏览器"}
        </span>
        {showControls && (controlling ? (
          <React.Fragment>
            <button type="button" style={{ ...ctrlBtnStyle, borderColor: "var(--accent, #16a34a)", color: "var(--accent, #16a34a)" }} onClick={stopControl} title="把控制权交还给 Agent">退出控制</button>
            <button type="button" style={ctrlBtnStyle} onClick={openNativeWindow} disabled={nativeBusy} title="遇到验证码/反爬时，改用独立浏览器窗口">独立窗口</button>
          </React.Fragment>
        ) : (
          <button type="button" style={{ ...ctrlBtnStyle, borderColor: "var(--accent, #16a34a)", color: "var(--accent, #16a34a)" }} onClick={startControl} disabled={!connected} title="在此面板内直接操作浏览器">接管</button>
        ))}
        {onClose && <span style={{ cursor: "pointer" }} onClick={onClose} title="关闭">✕</span>}
      </div>

      <div
        ref={stageRef}
        style={stageStyle}
        onMouseDown={onMouseDown}
        onMouseUp={onMouseUp}
        onMouseMove={onMouseMove}
        onContextMenu={onContextMenu}
      >
        {controlling && (
          <textarea
            ref={sinkRef}
            defaultValue=""
            onKeyDown={onSinkKeyDown}
            onKeyUp={onSinkKeyUp}
            onInput={onSinkInput}
            onCompositionStart={onSinkCompositionStart}
            onCompositionEnd={onSinkCompositionEnd}
            autoCapitalize="off"
            autoCorrect="off"
            autoComplete="off"
            spellCheck={false}
            tabIndex={-1}
            aria-hidden="true"
            style={{ position: "absolute", top: 0, left: 0, width: 1, height: 1, opacity: 0, padding: 0, margin: 0, border: 0, resize: "none", pointerEvents: "none", overflow: "hidden", whiteSpace: "nowrap", zIndex: 1 }}
          />
        )}
        {error ? (
          <div style={{ margin: "auto", maxWidth: 360, padding: 24, textAlign: "center", color: "var(--text-3)", fontSize: 12 }}>
            浏览器实时视图不可用：{error}
            <div style={{ marginTop: 8, color: "var(--text-4)", fontSize: 11 }}>请查看后端日志或重启 Cyrene 后重试。</div>
          </div>
        ) : nativeWindow ? (
          <div style={{ margin: "auto", maxWidth: 420, padding: 24, textAlign: "center", color: "var(--text-3)" }}>
            <div style={{ fontSize: 13, fontWeight: 600, marginBottom: 8, color: "var(--text-2)" }}>已在独立浏览器窗口打开</div>
            <div style={{ fontSize: 12, marginBottom: 14 }}>请在弹出的浏览器窗口里完成登录 / 验证码，完成后点下面切回内嵌视图继续。</div>
            <button type="button" className="btn primary" onClick={closeNativeWindow} disabled={nativeBusy} style={{ minWidth: 132 }}>
              {nativeBusy ? "正在切回…" : "切回内嵌视图"}
            </button>
            {takeoverError && <div style={{ marginTop: 10, fontSize: 11, color: "var(--danger)" }}>{takeoverError}</div>}
          </div>
        ) : takeover.pending ? (
          <div className="browser-takeover" style={{ margin: "auto", maxWidth: 420, padding: 24, textAlign: "center", color: "var(--text-3)" }}>
            <div style={{ fontSize: 13, fontWeight: 600, marginBottom: 8, color: "var(--text-2)" }}>等待你在浏览器窗口登录…</div>
            {takeover.reason && <div style={{ fontSize: 12, marginBottom: 6 }}>{takeover.reason}</div>}
            <div style={{ fontSize: 11, color: "var(--text-4)", marginBottom: 12, fontFamily: "var(--mono)", wordBreak: "break-all" }}>{takeover.url || url}</div>
            <div style={{ fontSize: 12, marginBottom: 14 }}>请在弹出的浏览器窗口完成登录，然后回到这里继续。</div>
            <button
              type="button"
              className="btn primary"
              onClick={completeTakeover}
              disabled={takeoverSubmitting}
              style={{ minWidth: 132 }}
            >
              {takeoverSubmitting ? "正在继续…" : completeLabel}
            </button>
            {takeoverError && <div style={{ marginTop: 10, fontSize: 11, color: "var(--danger)" }}>{takeoverError}</div>}
          </div>
        ) : (
          <img ref={imgRef} alt="browser" draggable={false} style={{ width: "100%", height: "auto", display: "block", background: "#fff", userSelect: "none" }} />
        )}
      </div>

      {controlling && !error && !takeover.pending && !nativeWindow ? (
        <div className="browser-view-action" style={{ padding: "5px 10px", borderTop: "1px solid var(--line)", fontFamily: "var(--mono)", fontSize: 11, color: "var(--accent, #16a34a)", whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis", flexShrink: 0 }}>
          ● 你正在直接控制浏览器（agent 浏览器操作已暂停）— 点击 / 滚动 / 输入（含中文）都会作用到页面
        </div>
      ) : actionLabel && !error && !takeover.pending && !nativeWindow && (
        <div className="browser-view-action" style={{ padding: "5px 10px", borderTop: "1px solid var(--line)", fontFamily: "var(--mono)", fontSize: 11, color: "var(--text-3)", whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis", flexShrink: 0 }}>
          ▸ {actionLabel}
        </div>
      )}
    </div>
  );
}

window.BrowserViewportPanel = BrowserViewportPanel;
