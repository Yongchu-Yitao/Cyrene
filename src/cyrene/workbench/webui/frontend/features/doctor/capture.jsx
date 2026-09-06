import { openDoctor } from "./doctor.jsx"
import { wbSetBrowserOverlayObscured } from "../../shared/browser/overlays.jsx"

// Capture metadata only: never URLs, request bodies, exception messages or stacks.
export function installDoctorCapture(root, open = openDoctor) {
  const originalFetch = root.fetch;
  const seen = new Map();
  let notice;
  let latest = {};
  function show(scope) {
    latest = scope;
    if (notice) return;
    notice = root.document.createElement("aside");
    notice.className = "wb-doctor-notice";
    notice.setAttribute("role", "status");
    const label = root.document.createElement("span");
    const t = (key, fallback) => root.CyreneUI?.i18n?.t(key, null, fallback) || fallback;
    label.textContent = t("doctor.notice", "Something went wrong. View diagnostic guidance.");
    const button = root.document.createElement("button");
    button.type = "button"; button.className = "wb-btn"; button.textContent = t("doctor.title", "Cyrene Doctor");
    button.onclick = () => { const scope = latest; dismiss(); open(scope); };
    const close = root.document.createElement("button");
    close.type = "button"; close.className = "wb-btn"; close.textContent = "×";
    close.setAttribute("aria-label", t("doctor.close", "Dismiss")); close.onclick = dismiss;
    notice.append(label, button, close);
    (root.document.querySelector(".workbench-shell") || root.document.body).appendChild(notice);
    wbSetBrowserOverlayObscured(1);
  }
  function dismiss() { if (notice) { notice.remove(); wbSetBrowserOverlayObscured(-1); } notice = null; }
  function capture(code, identifier = "") {
    const key = identifier || code;
    const now = Date.now();
    if (now - (seen.get(key) || 0) < 30000) return;
    seen.set(key, now);
    if (seen.size > 50) seen.delete(seen.keys().next().value);
    try { show({ client_code: code, ...( /^incident_[a-f0-9]{32}$/.test(identifier) ? { incident_id: identifier } : {}) }); } catch (_) { /* Reporting cannot replace the original failure. */ }
  }
  function relevant(input) {
    try {
      const url = new URL(typeof input === "string" || input instanceof URL ? input : input.url, root.location.href);
      return url.origin === root.location.origin && url.pathname.startsWith("/api/") && !url.pathname.startsWith("/api/doctor/");
    } catch (_) { return false; }
  }
  async function observedFetch(...args) {
    const observe = relevant(args[0]);
    try {
      const response = await originalFetch.apply(this, args);
      if (observe && response.status >= 400) capture("http_" + response.status, response.headers.get("x-cyrene-incident-id") || "");
      return response;
    } catch (error) {
      if (observe && (!error || error.name !== "AbortError")) capture("network_error");
      throw error;
    }
  }
  const onError = () => capture("frontend_error");
  const onRejection = event => { if (!event.reason || event.reason.name !== "AbortError") capture("unhandled_rejection"); };
  const onOpen = () => open({ client_code: "ui_error" });
  root.fetch = observedFetch;
  root.addEventListener("error", onError);
  root.addEventListener("unhandledrejection", onRejection);
  root.addEventListener("cyrene:diagnose-error", onOpen);
  return () => {
    if (root.fetch === observedFetch) root.fetch = originalFetch;
    root.removeEventListener("error", onError);
    root.removeEventListener("unhandledrejection", onRejection);
    root.removeEventListener("cyrene:diagnose-error", onOpen);
    dismiss();
  };
}

installDoctorCapture(window);
