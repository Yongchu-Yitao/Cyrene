// Workbench unified fetch wrapper — configurable request timeout (AbortController)
// + normalized errors + non-blocking toast on failure.
//
// Independent from the legacy WebUI data layer (src/webui/static/app/data.jsx):
// the workbench shell owns its OWN API surface so the two front-ends stay fully
// decoupled (no cross-shell import / reuse). Loaded before the other workbench
// scripts so every module can reach window.WorkbenchAPI.{fetch,json}.
//
// Why this exists: the workbench had ~83 bare fetch() sites with NO request
// timeout, so a stalled backend (slow model / lock contention) left the UI
// spinning forever with no feedback, and many failures were swallowed by empty
// .catch() handlers. This centralizes a configurable timeout and routes failures
// to window.showToast (the project's non-blocking feedback channel).
var WorkbenchAPI = (function () {
  // Default budget for ordinary JSON calls. Streaming sends and open-ended agent
  // calls opt out with `timeout: 0`.
  var DEFAULT_TIMEOUT_MS = 30000;

  function apiT(key, fallback, params) {
    if (window.WorkbenchI18n && typeof window.WorkbenchI18n.t === "function") {
      var value = window.WorkbenchI18n.t(key, params, fallback);
      if (value && value !== key) return value;
    }
    if (params && fallback) {
      Object.keys(params).forEach(function (name) {
        fallback = fallback.split("{" + name + "}").join(String(params[name]));
      });
    }
    return fallback || key;
  }

  // Normalize any thrown value to a short, user-facing string.
  function errorText(err) {
    if (err && err.isTimeout) return err.message;
    var raw = String((err && err.message) || err || "").trim();
    if (!raw
      || raw === "Load failed"
      || raw === "Failed to fetch"
      || raw === "NetworkError when attempting to fetch resource.") {
      return apiT("workbenchApi.error.network", "Network error — please retry");
    }
    return raw;
  }

  // fetch() + a configurable AbortController timeout. `opts` extends the standard
  // fetch init with:
  //   timeout: ms budget (default 30s). 0 / null / false → no timeout. Use the
  //            opt-out for streaming responses and open-ended agent calls that
  //            legitimately run for minutes.
  //   signal:  an external AbortSignal (e.g. a user "cancel" controller). When
  //            present it is chained to the internal timeout controller so BOTH a
  //            manual cancel and the timeout abort the request. Cancel semantics
  //            are preserved: a user abort rejects with AbortError, a timeout
  //            rejects with a distinct TimeoutError (isTimeout: true).
  //   toast / toastPrefix: consumed by json() only; stripped before fetch.
  function wbFetch(url, opts) {
    opts = opts || {};
    var timeoutMs = opts.timeout === undefined ? DEFAULT_TIMEOUT_MS : opts.timeout;
    var external = opts.signal || null;

    // Hand fetch only standard init keys.
    var init = {};
    Object.keys(opts).forEach(function (k) {
      if (k !== "timeout" && k !== "toast" && k !== "toastPrefix") init[k] = opts[k];
    });

    if (!timeoutMs || typeof AbortController === "undefined") {
      // No budget → straight passthrough, preserving any external signal as-is.
      return fetch(url, init);
    }

    var ctrl = new AbortController();
    var timedOut = false;
    var timer = setTimeout(function () {
      timedOut = true;
      try { ctrl.abort(); } catch (e) {}
    }, timeoutMs);

    function onExternalAbort() { try { ctrl.abort(); } catch (e) {} }
    if (external) {
      if (external.aborted) onExternalAbort();
      else external.addEventListener("abort", onExternalAbort);
    }
    init.signal = ctrl.signal;

    function done() {
      clearTimeout(timer);
      if (external) external.removeEventListener("abort", onExternalAbort);
    }

    function normalizeAbort(err) {
      if (err && err.name === "AbortError" && timedOut) {
        var e = new Error(apiT("workbenchApi.error.timeout", "Request timed out ({s}s)", { s: Math.round(timeoutMs / 1000) }));
        e.name = "TimeoutError";
        e.isTimeout = true;
        return e;
      }
      return err;
    }

    return fetch(url, init).then(function (resp) {
      // fetch() resolves when headers arrive. Keep the deadline active until
      // the response body is actually consumed; otherwise response.json() can
      // hang forever while the UI remains in its loading state.
      try {
        resp.__workbenchRequestDone = done;
        resp.__workbenchNormalizeAbort = normalizeAbort;
      } catch (e) {}
      ["json", "text", "blob", "arrayBuffer", "formData"].forEach(function (name) {
        if (typeof resp[name] !== "function") return;
        var original = resp[name].bind(resp);
        try {
          resp[name] = function () {
            return original.apply(null, arguments).then(function (value) {
              done();
              return value;
            }, function (err) {
              done();
              throw normalizeAbort(err);
            });
          };
        } catch (e) {}
      });
      if (!resp.body) done();
      return resp;
    }, function (err) {
      done();
      var normalized = normalizeAbort(err);
      if (normalized !== err) throw normalized;
      // User-abort (AbortError) or a network error — propagate untouched so the
      // caller's cancel handling still sees a genuine AbortError.
      throw err;
    });
  }

  // wbFetch + JSON parse + normalized HTTP-error throw. On any failure (timeout,
  // HTTP !ok, network) shows a toast UNLESS `opts.toast === false`, then re-throws
  // so callers keep full control of their own .catch flows. A user-cancel
  // (AbortError) never toasts.
  function wbJson(url, opts) {
    opts = opts || {};
    var withToast = opts.toast !== false; // default: surface failures to the user
    var prefix = opts.toastPrefix || "";
    return wbFetch(url, opts).then(function (response) {
      return response.json().catch(function (err) {
        if (typeof response.__workbenchNormalizeAbort === "function") {
          err = response.__workbenchNormalizeAbort(err);
        }
        if (err && (err.name === "AbortError" || err.isTimeout)) throw err;
        return {};
      }).then(function (payload) {
        if (!response.ok) {
          var error = new Error((payload && (payload.error || payload.detail)) || ("HTTP " + response.status));
          error.status = response.status;
          error.code = (payload && payload.code) || "";
          error.payload = payload;
          throw error;
        }
        return payload;
      }).then(function (payload) {
        if (typeof response.__workbenchRequestDone === "function") response.__workbenchRequestDone();
        return payload;
      }, function (err) {
        if (typeof response.__workbenchRequestDone === "function") response.__workbenchRequestDone();
        throw err;
      });
    }).catch(function (err) {
      if (err && err.name === "AbortError") throw err; // silent user-cancel
      if (withToast) toastError(err, prefix);
      throw err;
    });
  }

  // Imperative toast for a thrown value (skips silent user-cancels).
  function toastError(err, prefix) {
    if (err && err.name === "AbortError") return;
    if (typeof window.showToast === "function") {
      var msg = errorText(err);
      window.showToast(prefix ? (prefix + msg) : msg, "error");
    }
  }

  return {
    DEFAULT_TIMEOUT_MS: DEFAULT_TIMEOUT_MS,
    fetch: wbFetch,
    json: wbJson,
    errorText: errorText,
    toastError: toastError,
  };
})();
window.WorkbenchAPI = WorkbenchAPI;
