// Only the trusted Workbench WebView has this interface. Browsed pages never do.
export function installAndroidBrowserHost(win) {
  const native = win.CyreneAndroidBrowser;
  if (!native || (win.cyrene && win.cyrene.browser)) return null;
  let sequence = 0;
  let capability, authorize;
  const authorized = new Promise(resolve => { authorize = resolve; });
  const documentId = Date.now().toString(36) + Math.random().toString(36).slice(2);
  const pending = new Map(), listeners = new Set(), managers = new Set();
  function call(method, args = {}) {
    return new Promise((resolve, reject) => {
      const id = documentId + ":" + String(++sequence);
      const timer = win.setTimeout(() => { pending.delete(id); reject(new Error("Android browser timed out")); }, 45000);
      pending.set(id, { resolve, reject, timer });
      authorized.then(() => {
        if (!pending.has(id)) return;
        try { native.request(id, JSON.stringify({ method, args, sessionId: args.sessionId || "" }), capability); }
        catch (error) { win.clearTimeout(timer); pending.delete(id); reject(error); }
      });
    });
  }
  win.__cyreneAndroidBrowserResult = (id, result) => {
    const item = pending.get(String(id));
    if (!item) return;
    pending.delete(String(id)); win.clearTimeout(item.timer);
    // Match Electron: state and tools may carry ok:false without rejecting RPC.
    item.resolve(result);
  };
  win.__cyreneAndroidBrowserState = (state, manager) => {
    listeners.forEach(fn => fn(state)); managers.forEach(fn => fn(manager));
  };
  const browser = { platform: "android", supportsPictureInPicture: false,
    getState: sessionId => call("state", { sessionId }),
    getManagerState: () => call("managerState"),
    onState: fn => { listeners.add(fn); return () => listeners.delete(fn); },
    onManagerState: fn => { managers.add(fn); return () => managers.delete(fn); } };
  for (const method of ["setBounds", "setContext", "setObscured", "createTab", "activateTab", "closeTab", "navigate", "screenshot", "setMuted"]) {
    browser[method] = args => call(method, typeof args === "boolean" ? { obscured: args } : args || {});
  }
  for (const method of ["goBack", "goForward", "reload"]) browser[method] = args => call(method, typeof args === "object" ? args : { sessionId: args || "" });
  win.cyrene = { ...(win.cyrene || {}), browser };
  let socket, stopped = false, retry;
  function connect() {
    if (stopped) return;
    socket = new win.WebSocket((win.location.protocol === "https:" ? "wss://" : "ws://") + win.location.host + "/ws/browser/native-host");
    socket.onmessage = async event => {
      let request;
      try { request = JSON.parse(event.data); } catch (_) { return; }
      if (!request.id || !request.method) return;
      const current = socket;
      let result;
      try { result = await call(request.method, { ...request.args, sessionId: request.sessionId || "", roundId: request.roundId || "" }); }
      catch (error) { result = { ok: false, error: error.message, code: "ANDROID_BROWSER_UNAVAILABLE" }; }
      if (current.readyState === 1) current.send(JSON.stringify({ id: request.id, result }));
    };
    socket.onclose = () => { if (!stopped) retry = win.setTimeout(connect, 1500); };
  }
  win.__cyreneInitAndroidBrowser = value => {
    if (capability || typeof value !== "string" || value.length < 64) return;
    capability = value; authorize(); connect();
  };
  const close = () => { stopped = true; win.clearTimeout(retry); socket?.close(); pending.forEach(p => { win.clearTimeout(p.timer); p.reject(new Error("Browser host closed")); }); pending.clear(); };
  win.addEventListener("pagehide", close, { once: true });
  return { browser, close };
}
if (typeof window !== "undefined") installAndroidBrowserHost(window);
