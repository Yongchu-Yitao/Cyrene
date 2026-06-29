// Cyrene UI — live browser viewport (M2)
// Renders the agent's browser screencast (via the /ws/browser WebSocket) plus a
// ribbon describing the latest action. A takeover card placeholder is included
// for M3 (native-window login handoff); it only shows once the backend starts
// emitting browser_takeover_request events and a pending question id is wired in.

function BrowserViewportPanel({ roundId, onClose, onTakeoverComplete }) {
  if (typeof window.useDataVersion === "function") window.useDataVersion(); // re-render on DATA.browser updates
  const browser = (window.DATA && window.DATA.browser) || {};
  const imgRef = React.useRef(null);
  const [connected, setConnected] = React.useState(false);
  const [error, setError] = React.useState("");
  const [frameUrl, setFrameUrl] = React.useState("");
  const [takeoverSubmitting, setTakeoverSubmitting] = React.useState(false);
  const [takeoverError, setTakeoverError] = React.useState("");
  const objectUrlRef = React.useRef("");

  React.useEffect(function () {
    let ws = null;
    let closed = false;
    let retry = null;

    function connect() {
      try {
        const proto = location.protocol === "https:" ? "wss:" : "ws:";
        ws = new WebSocket(proto + "//" + location.host + "/ws/browser");
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
      if (ws) { try { ws.close(); } catch (e) {} }
      if (objectUrlRef.current) {
        URL.revokeObjectURL(objectUrlRef.current);
        objectUrlRef.current = "";
      }
    };
  }, []);

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

  const url = frameUrl || browser.url || "";
  const title = browser.title || "";
  const action = browser.action || "";
  const target = browser.target || "";
  const actionLabel = !action ? "" :
    action === "navigate" ? ("导航到 " + (url || "")) :
    action === "click" ? ("点击了 " + (target || "")) :
    action === "type" ? ("输入到 " + (target || "")) : action;

  const barStyle = { display: "flex", alignItems: "center", gap: 8, padding: "6px 10px", borderBottom: "1px solid var(--line)", fontFamily: "var(--mono)", fontSize: 11, color: "var(--text-3)", flexShrink: 0 };
  const stageStyle = { flex: 1, position: "relative", overflow: "auto", display: "flex", alignItems: "flex-start", justifyContent: "center", background: "var(--bg-1)" };

  return (
    <div className="browser-view" style={{ flex: 1, display: "flex", flexDirection: "column", overflow: "hidden" }}>
      <div className="browser-view-bar" style={barStyle}>
        <span className={"sa-dot " + (connected ? "running" : "queued")} style={{ width: 6, height: 6 }}></span>
        <span style={{ flex: 1, whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }} title={url}>
          {title ? (title + " — ") : ""}{url || "浏览器"}
        </span>
        {onClose && <span style={{ cursor: "pointer" }} onClick={onClose} title="关闭">✕</span>}
      </div>

      <div style={stageStyle}>
        {error ? (
          <div style={{ margin: "auto", maxWidth: 360, padding: 24, textAlign: "center", color: "var(--text-3)", fontSize: 12 }}>
            浏览器实时视图不可用：{error}
            <div style={{ marginTop: 8, color: "var(--text-4)", fontSize: 11 }}>请查看后端日志或重启 Cyrene 后重试。</div>
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
          <img ref={imgRef} alt="browser" style={{ width: "100%", height: "auto", display: "block", background: "#fff" }} />
        )}
      </div>

      {actionLabel && !error && !takeover.pending && (
        <div className="browser-view-action" style={{ padding: "5px 10px", borderTop: "1px solid var(--line)", fontFamily: "var(--mono)", fontSize: 11, color: "var(--text-3)", whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis", flexShrink: 0 }}>
          ▸ {actionLabel}
        </div>
      )}
    </div>
  );
}

window.BrowserViewportPanel = BrowserViewportPanel;
