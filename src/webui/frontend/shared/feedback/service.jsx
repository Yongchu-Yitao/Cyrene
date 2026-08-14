// Shared non-blocking feedback service for Workbench and Quick Chat.
// Imperative callers and the React host use the same queue and lifecycle.
(function (root) {
  "use strict";

  var platform = root.CyreneUI;
  if (!platform) throw new Error("CyreneUI platform registry must load first");
  var useState = React.useState;
  var useEffect = React.useEffect;

  function text(key, fallback) {
    if (platform.i18n && typeof platform.i18n.t === "function") {
      var translated = platform.i18n.t(key, null, fallback);
      if (translated && translated !== key) return translated;
    }
    return fallback || key;
  }

  var toasts = [];
  var confirms = [];
  var listeners = [];
  var sequence = 0;
  var toastTimers = Object.create(null);

  function emit() {
    listeners.slice().forEach(function (listener) {
      try {
        listener();
      } catch (error) {
        console.error("Cyrene: feedback subscriber failed", error);
      }
    });
  }

  function subscribe(listener) {
    listeners.push(listener);
    return function unsubscribe() {
      listeners = listeners.filter(function (item) { return item !== listener; });
    };
  }

  function dismissToast(id) {
    if (toastTimers[id]) {
      window.clearTimeout(toastTimers[id]);
      delete toastTimers[id];
    }
    var next = toasts.filter(function (toast) { return toast.id !== id; });
    if (next.length !== toasts.length) {
      toasts = next;
      emit();
    }
  }

  function showToast(message, type, options) {
    var opts = options || {};
    var id = ++sequence;
    var kind = type || "info";
    var duration = opts.duration != null
      ? opts.duration
      : (kind === "error" ? 6000 : 3200);
    toasts = toasts.concat([{
      id: id,
      message: message == null ? "" : String(message),
      type: kind,
      duration: duration,
    }]);
    emit();
    if (duration > 0) {
      toastTimers[id] = window.setTimeout(function () {
        delete toastTimers[id];
        dismissToast(id);
      }, duration);
    }
    return id;
  }

  function confirmModal(options) {
    var opts = typeof options === "string" ? { body: options } : (options || {});
    return new Promise(function (resolve) {
      confirms = confirms.concat([{
        id: ++sequence,
        title: opts.title || "",
        body: opts.body || "",
        confirmLabel: opts.confirmLabel || "",
        cancelLabel: opts.cancelLabel || "",
        danger: !!opts.danger,
        resolve: resolve,
      }]);
      emit();
    });
  }

  function resolveConfirm(id, value) {
    var selected = null;
    confirms = confirms.filter(function (item) {
      if (item.id === id) {
        selected = item;
        return false;
      }
      return true;
    });
    if (selected) {
      emit();
      selected.resolve(value);
    }
  }

  function dispose() {
    Object.keys(toastTimers).forEach(function (id) {
      window.clearTimeout(toastTimers[id]);
      delete toastTimers[id];
    });
    var pending = confirms.slice();
    toasts = [];
    confirms = [];
    listeners = [];
    pending.forEach(function (item) {
      item.resolve(false);
    });
  }

  function toastIcon(type) {
    if (type === "error") {
      return <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><circle cx="12" cy="12" r="9" /><path d="M12 8v5M12 16.5v.01" /></svg>;
    }
    if (type === "success") {
      return <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><circle cx="12" cy="12" r="9" /><path d="m8.5 12 2.5 2.5 4.5-5" /></svg>;
    }
    if (type === "warning") {
      return <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M10.3 4.3 2.5 18a1.8 1.8 0 0 0 1.6 2.7h15.8A1.8 1.8 0 0 0 21.5 18L13.7 4.3a1.9 1.9 0 0 0-3.4 0Z" /><path d="M12 9.5v4M12 17v.01" /></svg>;
    }
    return <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><circle cx="12" cy="12" r="9" /><path d="M12 11v5M12 7.5v.01" /></svg>;
  }

  function FeedbackHost() {
    var state = useState(0);
    var setTick = state[1];
    useEffect(function () {
      return subscribe(function () {
        setTick(function (value) { return (value + 1) % 1000000; });
      });
    }, []);
    var snapshot = service.snapshot();
    var active = snapshot.confirms.length ? snapshot.confirms[0] : null;
    var toastOverlayActive = snapshot.toasts.length > 0;

    // Electron WebContentsView is always above renderer DOM. While a toast is
    // visible in the lower-right corner, reuse the shared browser-overlay
    // coordinator: it captures the current page into the renderer before
    // hiding the native layer, so the notification can sit over the browser
    // content without exposing a white placeholder.
    useEffect(function () {
      if (!toastOverlayActive) return undefined;
      var overlays;
      try { overlays = platform.require("browser-overlays"); } catch (error) {}
      if (!overlays || typeof overlays.adjust !== "function") return undefined;
      overlays.adjust(1);
      return function () { overlays.adjust(-1); };
    }, [toastOverlayActive]);

    useEffect(function () {
      if (!active) return undefined;
      var overlays;
      try { overlays = platform.require("browser-overlays"); } catch (error) {}
      if (!overlays || typeof overlays.adjust !== "function") return undefined;
      overlays.adjust(1);
      return function () { overlays.adjust(-1); };
    }, [active ? active.id : 0]);

    useEffect(function () {
      if (!active) return undefined;
      function onKey(event) {
        if (event.key === "Escape") {
          event.preventDefault();
          event.stopImmediatePropagation();
          resolveConfirm(active.id, false);
        } else if (event.key === "Enter") {
          event.preventDefault();
          event.stopImmediatePropagation();
          resolveConfirm(active.id, true);
        }
      }
      document.addEventListener("keydown", onKey, true);
      return function () {
        document.removeEventListener("keydown", onKey, true);
      };
    }, [active ? active.id : 0]);

    return (
      <>
        {snapshot.toasts.length ? (
          <div className="workbench-toast-host" aria-live="polite">
            {snapshot.toasts.map(function (toast) {
              return (
                <div key={toast.id} className={"workbench-toast is-" + toast.type} role="status">
                  <span className="workbench-toast-icon">{toastIcon(toast.type)}</span>
                  <span className="workbench-toast-msg">{toast.message}</span>
                  <button type="button" className="workbench-toast-close" onClick={function () { dismissToast(toast.id); }} aria-label={text("common.close", "Close")}>
                    <svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round"><path d="m6 6 12 12M18 6 6 18" /></svg>
                  </button>
                </div>
              );
            })}
          </div>
        ) : null}
        {active ? (
          <div className="workbench-confirm-scrim" onMouseDown={function (event) { if (event.target === event.currentTarget) resolveConfirm(active.id, false); }}>
            <div
              className="workbench-confirm-modal"
              role="alertdialog"
              aria-modal="true"
              aria-labelledby={active.title ? "workbench-confirm-title-" + active.id : undefined}
              aria-describedby={"workbench-confirm-body-" + active.id}
            >
              {active.title ? <div id={"workbench-confirm-title-" + active.id} className="workbench-confirm-title">{active.title}</div> : null}
              <div id={"workbench-confirm-body-" + active.id} className="workbench-confirm-body">{active.body}</div>
              <div className="workbench-confirm-foot">
                <button type="button" className="wb-btn ghost" onClick={function () { resolveConfirm(active.id, false); }}>
                  {active.cancelLabel || text("common.cancel", "Cancel")}
                </button>
                <button type="button" className={"wb-btn " + (active.danger ? "danger" : "primary")} autoFocus onClick={function () { resolveConfirm(active.id, true); }}>
                  {active.confirmLabel || text("common.confirm", "Confirm")}
                </button>
              </div>
            </div>
          </div>
        ) : null}
      </>
    );
  }

  var service = {
    Host: FeedbackHost,
    subscribe: subscribe,
    snapshot: function () {
      return { toasts: toasts, confirms: confirms };
    },
    showToast: showToast,
    dismissToast: dismissToast,
    confirmModal: confirmModal,
    resolveConfirm: resolveConfirm,
    dispose: dispose,
  };
  platform.feedback = platform.register("feedback", service);
  root.addEventListener("beforeunload", dispose, { once: true });
})(window);
