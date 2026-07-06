// Cyrene UI — live browser viewport (M2 screencast + S3 user control)
// Renders the agent's browser screencast (via the /ws/browser WebSocket) plus a
// ribbon describing the latest action. The user can TAKE CONTROL of the live
// view: mouse/keyboard events are forwarded over the same socket and injected
// into the headless page via CDP, so the user drives the very session the agent
// uses — no native window needed. For sites that fingerprint headless (e.g.
// CAPTCHA) a native-window escape hatch (/api/browser/takeover) is offered.
// The agent-initiated login takeover card (M3, browser_request_takeover) still
// appears when the backend emits browser_takeover_request.

function BrowserViewportPanel(props) {
  if (window.cyrene && window.cyrene.browser) {
    return React.createElement(ElectronBrowserViewportPanel, props);
  }
  return React.createElement(ScreencastBrowserViewportPanel, props);
}

function BrowserIcon({ name, size }) {
  size = size || 16;
  var common = {
    viewBox: "0 0 24 24",
    width: size,
    height: size,
    fill: "none",
    stroke: "currentColor",
    strokeWidth: "2",
    strokeLinecap: "round",
    strokeLinejoin: "round",
    "aria-hidden": "true",
  };
  if (name === "back") return <svg {...common}><path d="m15 18-6-6 6-6" /></svg>;
  if (name === "forward") return <svg {...common}><path d="m9 18 6-6-6-6" /></svg>;
  if (name === "reload") return <svg {...common}><path d="M21 12a9 9 0 1 1-2.64-6.36" /><path d="M21 3v7h-7" /></svg>;
  if (name === "go") return <svg {...common}><path d="M5 12h14" /><path d="m13 6 6 6-6 6" /></svg>;
  if (name === "plus") return <svg {...common}><path d="M12 5v14" /><path d="M5 12h14" /></svg>;
  if (name === "close") return <svg {...common}><path d="M18 6 6 18" /><path d="m6 6 12 12" /></svg>;
  if (name === "volume") return <svg {...common}><path d="M11 5 6 9H3v6h3l5 4V5Z" /><path d="M15.5 8.5a5 5 0 0 1 0 7" /></svg>;
  if (name === "muted") return <svg {...common}><path d="M11 5 6 9H3v6h3l5 4V5Z" /><path d="m16 9 5 5" /><path d="m21 9-5 5" /></svg>;
  return null;
}

function ElectronBrowserViewportPanel({ onClose, browserState }) {
  browserState = browserState || (window.DATA && window.DATA.browser);
  const bridge = window.cyrene && window.cyrene.browser;
  const hostRef = React.useRef(null);
  const addressRef = React.useRef(null);
  const boundsRafRef = React.useRef(0);
  const [state, setState] = React.useState({ tabs: [], activeTabId: "", activeTab: null });
  const [address, setAddress] = React.useState("");
  const [busy, setBusy] = React.useState(false);
  const [error, setError] = React.useState("");

  const active = state.activeTab || null;
  const tabs = Array.isArray(state.tabs) ? state.tabs : [];

  function refreshState() {
    if (!bridge || typeof bridge.getState !== "function") return;
    bridge.getState().then(function (next) {
      if (next && next.ok !== false) setState(next);
    }).catch(function () {});
  }

  React.useEffect(function () {
    refreshState();
    if (!bridge || typeof bridge.onState !== "function") return undefined;
    return bridge.onState(function (next) {
      if (next && next.ok !== false) setState(next);
    });
  }, []);

  React.useEffect(function () {
    const nextUrl = (active && active.url) || (browserState && browserState.url) || "";
    setAddress(nextUrl === "about:blank" ? "" : nextUrl);
  }, [active && active.id, active && active.url, browserState && browserState.url]);

  function sendBounds(visible) {
    if (!bridge || typeof bridge.setBounds !== "function") return;
    const node = hostRef.current;
    if (!visible || !node) {
      bridge.setBounds({ visible: false }).catch(function () {});
      return;
    }
    const rect = node.getBoundingClientRect();
    bridge.setBounds({
      visible: true,
      x: rect.left,
      y: rect.top,
      width: rect.width,
      height: rect.height,
    }).catch(function () {});
  }

  function scheduleBounds() {
    if (boundsRafRef.current) cancelAnimationFrame(boundsRafRef.current);
    boundsRafRef.current = requestAnimationFrame(function () {
      boundsRafRef.current = 0;
      sendBounds(true);
    });
  }

  React.useEffect(function () {
    scheduleBounds();
    const node = hostRef.current;
    const ro = typeof ResizeObserver !== "undefined" && node ? new ResizeObserver(scheduleBounds) : null;
    if (ro && node) ro.observe(node);
    window.addEventListener("resize", scheduleBounds);
    return function () {
      if (boundsRafRef.current) cancelAnimationFrame(boundsRafRef.current);
      if (ro) ro.disconnect();
      window.removeEventListener("resize", scheduleBounds);
      sendBounds(false);
    };
  }, []);

  React.useEffect(function () {
    function onWorkbenchRightResize(ev) {
      var phase = ev && ev.detail && ev.detail.phase;
      if (phase === "start") sendBounds(false);
      else scheduleBounds();
    }
    window.addEventListener("workbench:right-resize", onWorkbenchRightResize);
    return function () {
      window.removeEventListener("workbench:right-resize", onWorkbenchRightResize);
    };
  }, []);

  React.useEffect(function () {
    scheduleBounds();
  }, [state.activeTabId, tabs.length]);

  function run(action) {
    setBusy(true);
    setError("");
    return Promise.resolve()
      .then(action)
      .then(function (next) { if (next && next.ok !== false) setState(next); else if (next && next.error) setError(next.error); })
      .catch(function (e) { setError((e && e.message) || String(e || "browser action failed")); })
      .finally(function () { setBusy(false); scheduleBounds(); });
  }

  function createTab(url) {
    return run(function () { return bridge.createTab({ url: url || "about:blank", activate: true }); });
  }

  React.useEffect(function () {
    if (!tabs.length && ((browserState && browserState.active) || (browserState && browserState.url))) {
      createTab(browserState.url || "about:blank");
    }
  }, [browserState && browserState.active, browserState && browserState.url, tabs.length]);

  function navigate() {
    const url = (addressRef.current ? addressRef.current.value : address).trim();
    if (!url) return createTab("about:blank");
    run(function () { return bridge.navigate({ url: url }); });
  }

  function onAddressKeyDown(e) {
    if (e.key === "Enter") {
      e.preventDefault();
      navigate();
    }
  }

  return (
    <div className="browser-view native">
      <div className="browser-tabs-strip">
        {tabs.map(function (tab) {
          return (
            <button key={tab.id} type="button" className={"browser-tab" + (tab.id === state.activeTabId ? " active" : "")} onClick={function () { run(function () { return bridge.activateTab(tab.id); }); }} title={(tab.title || tab.url || "Browser") + (tab.audible ? " · audible" : "")}>
              <span className="browser-tab-title">{tab.title || tab.url || "New tab"}</span>
              {tab.audible && <span className="browser-tab-audio" aria-hidden="true"><BrowserIcon name="volume" size={13} /></span>}
              <span
                className="browser-tab-close"
                role="button"
                tabIndex={-1}
                onClick={function (e) { e.stopPropagation(); run(function () { return bridge.closeTab(tab.id); }); }}
                title="Close tab"
              ><BrowserIcon name="close" size={12} /></span>
            </button>
          );
        })}
        <button type="button" className="browser-icon-btn" onClick={function () { createTab("about:blank"); }} title="New tab"><BrowserIcon name="plus" /></button>
        {onClose && <button type="button" className="browser-icon-btn" onClick={onClose} title="Close panel"><BrowserIcon name="close" /></button>}
      </div>
      <div className="browser-nav-bar">
        <button type="button" className="browser-icon-btn" disabled={!active || !active.canGoBack || busy} onClick={function () { run(function () { return bridge.goBack(); }); }} title="Back"><BrowserIcon name="back" /></button>
        <button type="button" className="browser-icon-btn" disabled={!active || !active.canGoForward || busy} onClick={function () { run(function () { return bridge.goForward(); }); }} title="Forward"><BrowserIcon name="forward" /></button>
        <button type="button" className="browser-icon-btn" disabled={!active || busy} onClick={function () { run(function () { return bridge.reload(); }); }} title="Reload"><BrowserIcon name="reload" /></button>
        <input ref={addressRef} className="browser-address" value={address} onChange={function (e) { setAddress(e.target.value); }} onKeyDown={onAddressKeyDown} placeholder="https://example.com" />
        <button type="button" className="browser-icon-btn browser-go-btn" disabled={busy} onClick={navigate} title="Go"><BrowserIcon name="go" /></button>
        <button type="button" className={"browser-icon-btn" + (active && active.muted ? " muted" : "")} disabled={!active} onClick={function () { run(function () { return bridge.setMuted({ muted: !(active && active.muted) }); }); }} title={active && active.muted ? "Unmute" : "Mute"}>
          <BrowserIcon name={active && active.muted ? "muted" : "volume"} />
        </button>
      </div>
      {error && <div className="browser-error">{error}</div>}
      <div ref={hostRef} className="browser-native-host">
        {!tabs.length && (
          <div className="browser-empty">
            <button type="button" className="btn primary" onClick={function () { createTab("about:blank"); }}>打开浏览器</button>
          </div>
        )}
      </div>
    </div>
  );
}

function ScreencastBrowserViewportPanel({ roundId, onClose, onTakeoverComplete, browserState, browserSessionId }) {
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
        ws.onopen = function () {
          if (!closed) {
            setConnected(true); setError("");
            try {
              ws.send(JSON.stringify({
                type: "context",
                sessionId: String(browserSessionId || browser.sessionId || ""),
                roundId: String(roundId || browser.roundId || ""),
              }));
            } catch (e) {}
          }
        };
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
  }, [browserSessionId, roundId]);

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
  return (
    <div className="browser-view screencast">
      <div className="browser-view-bar">
        <span className={"browser-status-dot " + (connected ? "running" : "queued")}></span>
        <span className="browser-view-title" title={url}>
          {title ? (title + " — ") : ""}{url || "浏览器"}
        </span>
        {showControls && (controlling ? (
          <React.Fragment>
            <button type="button" className="browser-text-btn active" onClick={stopControl} title="把控制权交还给 Agent">退出控制</button>
            <button type="button" className="browser-text-btn" onClick={openNativeWindow} disabled={nativeBusy} title="遇到验证码/反爬时，改用独立浏览器窗口">独立窗口</button>
          </React.Fragment>
        ) : (
          <button type="button" className="browser-text-btn active" onClick={startControl} disabled={!connected} title="在此面板内直接操作浏览器">接管</button>
        ))}
        {onClose && <button type="button" className="browser-icon-btn compact" onClick={onClose} title="关闭"><BrowserIcon name="close" size={14} /></button>}
      </div>

      <div
        ref={stageRef}
        className={"browser-stage" + (controlling ? " controlling" : "")}
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
          <div className="browser-state-card">
            浏览器实时视图不可用：{error}
            <div className="browser-state-note">请查看后端日志或重启 Cyrene 后重试。</div>
          </div>
        ) : nativeWindow ? (
          <div className="browser-state-card wide">
            <div className="browser-state-title">已在独立浏览器窗口打开</div>
            <div className="browser-state-copy">请在弹出的浏览器窗口里完成登录 / 验证码，完成后点下面切回内嵌视图继续。</div>
            <button type="button" className="btn primary" onClick={closeNativeWindow} disabled={nativeBusy} style={{ minWidth: 132 }}>
              {nativeBusy ? "正在切回…" : "切回内嵌视图"}
            </button>
            {takeoverError && <div className="browser-error-text">{takeoverError}</div>}
          </div>
        ) : takeover.pending ? (
          <div className="browser-state-card wide browser-takeover">
            <div className="browser-state-title">等待你在浏览器窗口登录…</div>
            {takeover.reason && <div className="browser-state-copy">{takeover.reason}</div>}
            <div className="browser-state-url">{takeover.url || url}</div>
            <div className="browser-state-copy">请在弹出的浏览器窗口完成登录，然后回到这里继续。</div>
            <button
              type="button"
              className="btn primary"
              onClick={completeTakeover}
              disabled={takeoverSubmitting}
              style={{ minWidth: 132 }}
            >
              {takeoverSubmitting ? "正在继续…" : completeLabel}
            </button>
            {takeoverError && <div className="browser-error-text">{takeoverError}</div>}
          </div>
        ) : (
          <img ref={imgRef} alt="browser" draggable={false} className="browser-frame-img" />
        )}
      </div>

      {controlling && !error && !takeover.pending && !nativeWindow ? (
        <div className="browser-view-action active">
          ● 你正在直接控制浏览器（agent 浏览器操作已暂停）— 点击 / 滚动 / 输入（含中文）都会作用到页面
        </div>
      ) : actionLabel && !error && !takeover.pending && !nativeWindow && (
        <div className="browser-view-action">
          ▸ {actionLabel}
        </div>
      )}
    </div>
  );
}

window.BrowserViewportPanel = BrowserViewportPanel;
