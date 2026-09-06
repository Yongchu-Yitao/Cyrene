"""Behavior and architecture contracts for the consolidated WebUI."""

from __future__ import annotations
from conftest import workbench_chat_source

from collections import Counter
import json
from pathlib import Path
import re
import subprocess


ROOT = Path(__file__).resolve().parents[1]
WEBUI_ROOT = ROOT / "src" / "cyrene" / "workbench" / "webui"
WORKBENCH_ROOT = WEBUI_ROOT / "frontend"
INDEX = WORKBENCH_ROOT / "index.html"

BROWSER_AND_VENDOR_GLOBALS = {
    "AudioContext",
    "CustomEvent",
    "CyreneCodeMirror",
    "CyreneIconAssets",
    "DOMPurify",
    "L",
    "addEventListener",
    "alert",
    "atob",
    "btoa",
    "close",
    "confirm",
    "cyrene",
    "dispatchEvent",
    "echarts",
    "getSelection",
    "getComputedStyle",
    "hljs",
    "katex",
    "innerHeight",
    "innerWidth",
    "localStorage",
    "location",
    "marked",
    "matchMedia",
    "navigator",
    "open",
    "outerHeight",
    "pdfjsLib",
    "pdfjsViewer",
    "pip",
    "prompt",
    "ReactDOM",
    "removeEventListener",
    "setInterval",
    "clearInterval",
    "setTimeout",
    "clearTimeout",
    "cancelAnimationFrame",
    "fetch",
    "requestAnimationFrame",
    "requestIdleCallback",
    "webkitAudioContext",
}


def _openapi_operations(schema: dict) -> list[tuple[str, str, dict]]:
    methods = {"get", "post", "put", "patch", "delete", "options", "head", "trace"}
    return [(method.upper(), path, operation) for path, path_item in schema["paths"].items() for method, operation in path_item.items() if method.lower() in methods]


def _unexpected_dependency_parameters(
    operations: list[tuple[str, str, dict]],
) -> list[str]:
    forbidden = {
        "app",
        "bot",
        "context",
        "db",
        "db_path",
        "dbpath",
        "dependencies",
        "dependency",
        "repository",
        "router",
        "runtime",
        "service",
    }
    unexpected = []
    for method, path, operation in operations:
        for parameter in operation.get("parameters") or []:
            name = str(parameter.get("name") or "")
            normalized = name.strip().lower().replace("-", "_")
            if normalized in forbidden or any(normalized.startswith(f"{prefix}_") for prefix in ("db", "dependency", "repository", "runtime", "service")):
                unexpected.append(f"{method} {path}: {parameter.get('in', 'unknown')} parameter {name!r}")
    return sorted(unexpected)


def test_openapi_contract_has_stable_public_identifiers_and_no_dependency_leaks():
    from cyrene.workbench.webui.server import create_app

    schema = create_app(None, ":memory:").openapi()
    operations = _openapi_operations(schema)
    operation_ids = [str(operation.get("operationId") or "") for _, _, operation in operations]
    duplicate_ids = sorted(operation_id for operation_id, count in Counter(operation_ids).items() if operation_id and count > 1)

    assert operations, "OpenAPI schema has no operations"
    assert all(operation_ids), "OpenAPI operations without operationId"
    assert duplicate_ids == [], f"Duplicate OpenAPI operationIds: {duplicate_ids}"
    unexpected_parameters = _unexpected_dependency_parameters(operations)
    assert unexpected_parameters == [], f"Application dependencies leaked into the HTTP contract: {unexpected_parameters}"


def test_workbench_cross_script_globals_are_registered():
    globals_used: set[str] = set()
    for source_path in WORKBENCH_ROOT.rglob("*.jsx"):
        source = source_path.read_text(encoding="utf-8")
        globals_used.update(re.findall(r"\bwindow\.([A-Za-z_$][A-Za-z0-9_$]*)", source))

    owned_globals = {
        "CyreneUI": WORKBENCH_ROOT / "platform" / "runtime.jsx",
        "CyrenePageLifecycle": WORKBENCH_ROOT / "index.html",
        "CyreneTerminalSurface": WORKBENCH_ROOT / "features" / "chat" / "page.jsx",
    }
    assert globals_used - BROWSER_AND_VENDOR_GLOBALS == set(owned_globals)
    owner_sources = {
        name: path.read_text(encoding="utf-8")
        for name, path in owned_globals.items()
    }
    assert "root.CyreneUI = {" in owner_sources["CyreneUI"]
    assert "window.CyrenePageLifecycle = Object.freeze({" in owner_sources["CyrenePageLifecycle"]
    assert "window.CyreneTerminalSurface = bridge" in owner_sources["CyreneTerminalSurface"]
    feedback_source = (WORKBENCH_ROOT / "shared" / "feedback" / "service.jsx").read_text(encoding="utf-8")
    model_source = (WORKBENCH_ROOT / "workbench-model.jsx").read_text(encoding="utf-8")
    shell_source = (WORKBENCH_ROOT / "workbench.jsx").read_text(encoding="utf-8")
    assert 'platform.feedback = platform.register("feedback", service)' in feedback_source
    assert 'window.CyreneUI.model = window.CyreneUI.register("model", service)' in model_source
    assert 'import { workbenchServices } from "./shared/runtime/services.jsx"' in shell_source
    assert "workbenchServices.model()" in shell_source
    assert "window.CyreneUI.require(" not in shell_source
    index = INDEX.read_text(encoding="utf-8")
    inline_assignments = set(re.findall(r"\bwindow\.([A-Za-z_$][A-Za-z0-9_$]*)\s*=", index))
    # The launch gate wraps fetch and publishes the one bootstrap lifecycle
    # object needed before the CyreneUI registry script has loaded.
    assert inline_assignments <= {"fetch", "CyrenePageLifecycle"}


def test_workbench_uses_one_module_entry_after_ordered_vendor_scripts():
    index = INDEX.read_text(encoding="utf-8")
    scripts = re.findall(r'<script[^>]+src="([^"]+)"', index)
    required_in_order = [
        "react.production.min.js",
        "react-dom.production.min.js",
        "marked.min.js",
        "katex/katex.min.js",
        "purify.min.js",
        "highlight.min.js",
        "echarts.min.js",
        "leaflet.js",
        "pdfjs/pdf.min.js?v=0.9.0-beta10",
        "pdfjs/pdf_viewer.js?v=0.9.0-beta10",
        "compiled/app.js?v=0.9.0-beta10",
    ]

    positions = [scripts.index(script) for script in required_in_order]
    assert positions == sorted(positions)
    compiled_scripts = [script for script in scripts if script.startswith("compiled/")]
    assert compiled_scripts == ["compiled/app.js?v=0.9.0-beta10"]
    assert '<script type="module" src="compiled/app.js?v=0.9.0-beta10"></script>' in index


def test_single_webui_source_build_and_entrypoint_shape():
    """Keep the current Workbench source/output roots and entry points."""

    assert (WEBUI_ROOT / "frontend" / "entry" / "app.jsx").is_file()
    assert (WEBUI_ROOT / "frontend" / "entry" / "bootstrap.jsx").is_file()
    assert (WEBUI_ROOT / "frontend" / "entry" / "pdf.jsx").is_file()
    assert (WEBUI_ROOT / "static" / "app").is_dir()

    package = json.loads((WEBUI_ROOT / "package.json").read_text(encoding="utf-8"))
    assert set(package["dependencies"]) == {
        "@codemirror/autocomplete",
        "@codemirror/commands",
        "@codemirror/lang-css",
        "@codemirror/lang-html",
        "@codemirror/lang-javascript",
        "@codemirror/lang-json",
        "@codemirror/lang-markdown",
        "@codemirror/lang-python",
        "@codemirror/language",
        "@codemirror/search",
        "@codemirror/state",
        "@codemirror/view",
        "@aiden0z/pptx-renderer",
        "@lezer/highlight",
        "@lobehub/icons-static-svg",
        "@tabler/icons",
        "@xterm/addon-fit",
        "@xterm/addon-search",
        "@xterm/addon-unicode11",
        "@xterm/addon-web-links",
        "@xterm/xterm",
        "docx-preview",
        "esbuild",
        "echarts",
        "katex",
        "pdfjs-dist",
        "react",
        "react-dom",
        "simple-icons",
        "turndown",
        "turndown-plugin-gfm",
    }

    server_source = (WEBUI_ROOT / "server.py").read_text(encoding="utf-8")
    index_source = INDEX.read_text(encoding="utf-8")
    assert "app.mount(\"/static\"" in server_source
    assert '<script type="module" src="compiled/app.js?v=' in index_source


def test_ui_background_and_pdf_resources_have_explicit_cleanup_paths():
    actions = (WORKBENCH_ROOT / "shared" / "markdown" / "actions.jsx").read_text(encoding="utf-8")
    feedback = (WORKBENCH_ROOT / "shared" / "feedback" / "service.jsx").read_text(encoding="utf-8")
    chat = workbench_chat_source()
    library = (WORKBENCH_ROOT / "workbench-library.jsx").read_text(encoding="utf-8")
    standalone_pdf = (
        ROOT / "src" / "cyrene" / "plugins" / "builtin" / "cyrene_content" / "pdf_routes.py"
    ).read_text(encoding="utf-8")

    assert "window.clearInterval(_pollTimer)" in actions
    assert 'window.addEventListener("beforeunload", dispose' in actions
    assert "observer.disconnect()" in actions
    assert 'root.addEventListener("beforeunload", dispose' in feedback
    assert "window.clearTimeout(toastTimers[id])" in feedback
    assert "var toastOverlayActive = snapshot.toasts.length > 0;" in feedback
    assert 'platform.require("browser-overlays")' in feedback
    assert "}, [toastOverlayActive]);" in feedback
    for source in (chat, library):
        assert "abortLoader.abort()" in source
        assert "loadedDocument.destroy()" in source
        assert "selectionSanitizer.abort()" in source
        assert "copyFix.abort()" in source
    assert "pdfBridge.loadPdf(pdfUrl, viewer, abortLoader.signal)" in standalone_pdf
    assert "window.addEventListener('beforeunload'" in standalone_pdf
    assert "currentDoc.destroy()" in standalone_pdf


def test_data_store_and_sse_bridge_characterization():
    """Exercise bootstrap, subscriber dispatch, ring buffer, and correlation."""

    runtime_source = WEBUI_ROOT / "frontend" / "platform" / "runtime.jsx"
    events_source = WEBUI_ROOT / "frontend" / "platform" / "events.jsx"
    data_source = WEBUI_ROOT / "frontend" / "platform" / "data-store.jsx"
    script = r"""
const fs = require("fs");
const vm = require("vm");
global.window = global;
console.debug = () => {};
global.React = {
  useState: (value) => [value, () => {}],
  useEffect: () => {},
};
global.localStorage = { getItem: () => null };
global.navigator = { userAgent: "node" };
global.document = {
  hidden: false,
  addEventListener: () => {},
  removeEventListener: () => {},
};
global.Notification = function () {};
global.addEventListener = () => {};
global.__fetchCalls = [];
global.fetch = async (url) => {
  const requestUrl = String(url);
  __fetchCalls.push(requestUrl);
  if (requestUrl === "/api/workbench/sessions") return { ok: true, json: async () => ({ sessions: [] }) };
  if (requestUrl === "/api/status") return { ok: true, json: async () => ({ services: [] }) };
  if (requestUrl.startsWith("/api/dashboard?tz=")) return { ok: true, json: async () => ({ usage: {} }) };
  if (requestUrl === "/api/settings/config") return { ok: false, json: async () => ({}) };
  if (!requestUrl.startsWith("/api/ui-data")) throw new Error("unexpected fetch " + url);
  return {
    ok: true,
    json: async () => ({
      user: { name: "Ada", handle: "ada" },
      assistantName: "Cyrene",
      appVersion: "test",
      sessions: [{ id: "chat-1", title: "Baseline" }],
      dashboard: { usage: {} },
      status: { services: [] },
      skills: [],
      settings: {},
      onboarding: { needsOnboarding: false },
    }),
  };
};
class FakeEventSource {
  constructor(url) {
    this.url = url;
    this.closed = false;
    global.__eventSource = this;
  }
  close() { this.closed = true; }
}
global.EventSource = FakeEventSource;
for (const sourcePath of process.argv.slice(1)) {
  const source = fs.readFileSync(sourcePath, "utf8")
    .replace(/^export\s+\{[^}]+\}\s*;?\s*$/gm, "");
  vm.runInThisContext(source, { filename: sourcePath });
}
(async () => {
  const dataStore = window.CyreneUI.require("data");
  const eventBridge = window.CyreneUI.require("events");
  await dataStore.ready;
  const requestsAfterBootstrap = __fetchCalls.length;
  const delivered = [];
  eventBridge.subscribe((event) => delivered.push(event.type));
  __eventSource.onmessage({ data: JSON.stringify({ type: "heartbeat" }) });
  __eventSource.onmessage({
    data: JSON.stringify({
      type: "browser_frame",
      chat_id: "chat-1",
      round_id: "round-1",
      url: "https://example.test/",
      title: "Example",
    }),
  });
  __eventSource.onmessage({
    data: JSON.stringify({
      type: "map_pin",
      pins: [{ id: "pin-1", lat: 31.2, lng: 121.5 }],
      routes: [],
    }),
  });
  __eventSource.onmessage({ data: JSON.stringify({ type: "unknown_future_event", value: 1 }) });
  for (let i = 0; i < 205; i += 1) {
    __eventSource.onmessage({ data: JSON.stringify({ type: "probe_" + i }) });
  }
  const snapshot = {
    eventUrl: __eventSource.url,
    user: dataStore.state.user,
    sessionId: dataStore.state.sessions[0].id,
    delivered: delivered.slice(0, 3),
    recentLength: eventBridge.recent.length,
    recentFirst: eventBridge.recent[0].type,
    recentLast: eventBridge.recent[eventBridge.recent.length - 1].type,
    browser: dataStore.state.browserByChat["chat-1"],
    map: dataStore.state.map,
    knownCritical: [
      "destructive_confirmation",
      "external_upload_confirmation",
      "plan",
      "plan_progress",
      "tool_call_started",
      "tool_call_finished",
    ].every((name) => eventBridge.knownEventNames.has(name)),
    requestsAfterBootstrap,
  };
  dataStore.dispose();
  snapshot.sourceClosed = __eventSource.closed;
  snapshot.listenerCountAfterDispose = eventBridge.listeners.size;
  console.log(JSON.stringify(snapshot));
})().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
"""
    result = subprocess.run(
        ["node", "-e", script, str(runtime_source), str(events_source), str(data_source)],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    payload = json.loads(result.stdout)

    assert payload["eventUrl"] == "/api/events"
    assert payload["user"] == {"name": "Ada", "handle": "ada"}
    assert payload["sessionId"] == "chat-1"
    assert payload["delivered"] == [
        "browser_frame",
        "map_pin",
        "unknown_future_event",
    ]
    assert payload["recentLength"] == 200
    assert payload["recentFirst"] == "probe_5"
    assert payload["recentLast"] == "probe_204"
    assert payload["browser"] == {
        "active": True,
        "url": "https://example.test/",
        "title": "Example",
        "action": "",
        "target": None,
        "box": None,
        "roundId": "round-1",
        "userWindow": False,
        "sessionId": "chat-1",
    }
    assert payload["map"] == {
        "pins": [{"id": "pin-1", "lat": 31.2, "lng": 121.5}],
        "routes": [],
    }
    assert payload["knownCritical"] is True
    assert payload["requestsAfterBootstrap"] == 2
    assert payload["sourceClosed"] is True
    assert payload["listenerCountAfterDispose"] == 0
    assert "__bump()" not in data_source.read_text(encoding="utf-8")
