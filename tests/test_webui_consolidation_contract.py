"""Behavior and compatibility contracts for the WebUI consolidation.

These tests intentionally freeze external/runtime behavior before source files
move.  Path-only assertions may be updated during the mechanical move, but the
OpenAPI, tool wire, event, and dependency assertions must remain equivalent.
"""

from __future__ import annotations

import hashlib
from importlib.metadata import version
import json
from pathlib import Path
import re
import subprocess


ROOT = Path(__file__).resolve().parents[1]
WEBUI_ROOT = ROOT / "src" / "webui"
WORKBENCH_ROOT = WEBUI_ROOT / "frontend"
INDEX = WORKBENCH_ROOT / "index.html"

OPENAPI_OPERATION_COUNT = 295
OPENAPI_BASELINE_FASTAPI = "0.136.1"
OPENAPI_BASELINE_PYDANTIC = "2.13.4"
OPENAPI_SHA256 = "f1762d75b0dc465fb980bb4f2d890b67115338b7e413845c0a4ad142babc5287"
TOOL_REGISTRY_SHA256 = "3b44e3cd4554cf4f722c4dc03d18955307b5d21941ea8e5e8df0db9d61f3f8f8"
MAIN_WIRE_SHA256 = "56f247691752283c226eb34ea1a8a902df14c8cd5c83ab94f92dbdd89f0e76f3"
SUBAGENT_WIRE_SHA256 = "2d29000a405e62b49ae6374bbbf43b1edb34fbf0fdee32e5816f73b026981c75"

# CyreneUI is the sole application-owned browser global. Every cross-script
# capability is registered under its explicit service name.
REGISTERED_WORKBENCH_GLOBALS = {"CyreneUI"}

BROWSER_AND_VENDOR_GLOBALS = {
    "DOMPurify",
    "L",
    "addEventListener",
    "alert",
    "close",
    "confirm",
    "cyrene",
    "dispatchEvent",
    "getSelection",
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
    "removeEventListener",
    "setInterval",
    "clearInterval",
    "setTimeout",
    "clearTimeout",
}


def _sha256_json(value: object) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    return hashlib.sha256(encoded).hexdigest()


def test_openapi_contract_matches_locked_generator_baseline():
    from webui.server import create_app

    # The OpenAPI renderer is dependency-sensitive.  Keep the exact generator
    # versions beside the strict hash so a dependency update requires an
    # intentional schema review instead of producing an unexplained mismatch.
    assert version("fastapi") == OPENAPI_BASELINE_FASTAPI
    assert version("pydantic") == OPENAPI_BASELINE_PYDANTIC

    schema = create_app(None, ":memory:").openapi()
    operation_count = sum(
        1
        for path_item in schema["paths"].values()
        for method in path_item
        if method.upper()
        in {"GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD", "TRACE"}
    )

    assert operation_count == OPENAPI_OPERATION_COUNT
    assert _sha256_json(schema) == OPENAPI_SHA256


def test_tool_registry_wire_and_actor_policy_contracts_are_unchanged():
    from cyrene.tooling import catalog, wire

    assert len(catalog.TOOL_DEFS) == 98
    assert len(catalog.TOOL_HANDLERS) == 98
    assert len(catalog._MAIN_ONLY_TOOLS) == 37
    assert _sha256_json(catalog.TOOL_DEFS) == TOOL_REGISTRY_SHA256

    assert len(wire.get_main_wire_tool_defs()) == 29
    assert wire.get_wire_bundle_hash("main") == MAIN_WIRE_SHA256
    assert len(wire.get_subagent_wire_tool_defs()) == 23
    assert wire.get_wire_bundle_hash("subagent") == SUBAGENT_WIRE_SHA256


def test_workbench_cross_script_globals_are_registered():
    globals_used: set[str] = set()
    for source_path in WORKBENCH_ROOT.rglob("*.jsx"):
        source = source_path.read_text(encoding="utf-8")
        globals_used.update(re.findall(r"\bwindow\.([A-Za-z_$][A-Za-z0-9_$]*)", source))

    assert globals_used - BROWSER_AND_VENDOR_GLOBALS == REGISTERED_WORKBENCH_GLOBALS
    feedback_source = (
        WORKBENCH_ROOT / "shared" / "feedback" / "service.jsx"
    ).read_text(encoding="utf-8")
    model_source = (WORKBENCH_ROOT / "workbench-model.jsx").read_text(
        encoding="utf-8"
    )
    shell_source = (WORKBENCH_ROOT / "workbench.jsx").read_text(encoding="utf-8")
    assert "root.workbenchT" not in feedback_source
    assert "var WorkbenchModel = (function" not in model_source
    assert 'var WorkbenchModel = window.CyreneUI.require("model")' in shell_source
    index = INDEX.read_text(encoding="utf-8")
    inline_assignments = set(
        re.findall(r"\bwindow\.([A-Za-z_$][A-Za-z0-9_$]*)\s*=", index)
    )
    # The launch gate temporarily wraps the native fetch function and restores
    # it after the initial request set settles; it does not expose an app API.
    assert inline_assignments <= {"fetch"}


def test_workbench_runtime_dependencies_keep_their_relative_script_order():
    index = INDEX.read_text(encoding="utf-8")
    scripts = re.findall(r'<script[^>]+src="([^"]+)"', index)
    required_in_order = [
        "react.development.js",
        "react-dom.development.js",
        "marked.min.js",
        "katex/katex.min.js",
        "purify.min.js",
        "highlight.min.js",
        "compiled/platform/runtime.js?v=0.7.0b4",
        "compiled/shared/markdown/math.js?v=0.7.0b4",
        "compiled/shared/markdown/highlight.js?v=0.7.0b4",
        "leaflet.js",
        "pdfjs/pdf.min.js?v=0.7.4",
        "pdfjs/pdf_viewer.js?v=0.7.4",
        "compiled/platform/readiness.js?v=0.7.0b4",
        "compiled/platform/events.js?v=0.7.0b4",
        "compiled/platform/navigation.js?v=0.7.0b4",
        "compiled/workbench-i18n.js?v=0.7.0b4",
        "compiled/shared/i18n/format.js?v=0.7.0b4",
        "compiled/shared/i18n/translations.js?v=0.7.0b4",
        "compiled/shared/pdf/bridge.js?v=0.7.4",
        "compiled/shared/feedback/service.js?v=0.7.0b4",
        "compiled/shared/markdown/renderer.js?v=0.7.0b4",
        "compiled/platform/data-store.js?v=0.7.0b4",
        "compiled/shared/browser/viewport.js?v=0.7.0b4",
        "compiled/shared/search/overlay.js?v=0.7.0b4",
        "compiled/shared/markdown/actions.js?v=0.7.0b4",
        "compiled/shared/diff/viewer.js?v=0.7.0b4",
        "compiled/platform/api.js?v=0.7.0b4",
        "compiled/workbench-chat.js?v=0.7.0b4",
        "compiled/workbench-quick-chat.js?v=0.7.0b4",
        "compiled/workbench.js?v=0.7.0b4",
        "compiled/settings-overlay.js?v=0.7.0b4",
        "compiled/entry/bootstrap.js?v=0.7.0b4",
    ]

    positions = [scripts.index(script) for script in required_in_order]
    assert positions == sorted(positions)
    assert not {
        "compiled/app.js?v=0.7.0b4",
        "compiled/chat.js?v=0.7.0b4",
        "compiled/dashboard.js?v=0.7.0b4",
        "compiled/knowledge.js?v=0.7.0b4",
        "compiled/memory.js?v=0.7.0b4",
        "compiled/tasks.js?v=0.7.0b4",
        "compiled/settings.js?v=0.7.0b4",
    }.intersection(scripts)


def test_single_webui_source_build_and_entrypoint_shape():
    """Keep one Workbench source/output root and no classic selector."""

    assert not (ROOT / "src" / "workbench-webui").exists()
    assert (WEBUI_ROOT / "frontend" / "entry" / "bootstrap.jsx").is_file()
    assert (WEBUI_ROOT / "static" / "app").is_dir()

    package = json.loads((WEBUI_ROOT / "package.json").read_text(encoding="utf-8"))
    assert set(package["dependencies"]) == {"esbuild", "katex", "pdfjs-dist"}

    server_source = (WEBUI_ROOT / "server.py").read_text(encoding="utf-8")
    index_source = INDEX.read_text(encoding="utf-8")
    shell_source = (ROOT / "src" / "route" / "system" / "shell.py").read_text(
        encoding="utf-8"
    )
    module_entry = (ROOT / "src" / "cyrene" / "__main__.py").read_text(
        encoding="utf-8"
    )
    electron_source = (ROOT / "electron" / "main.js").read_text(encoding="utf-8")
    assert "/static/workbench-ui" not in server_source
    assert "shell=legacy" not in shell_source
    assert "--agent" not in module_entry
    assert "window.markCyreneReady" not in index_source
    assert "shell=legacy" not in electron_source
    assert "CYRENE_UI_MODE" not in electron_source


def test_ui_background_and_pdf_resources_have_explicit_cleanup_paths():
    actions = (
        WORKBENCH_ROOT / "shared" / "markdown" / "actions.jsx"
    ).read_text(encoding="utf-8")
    feedback = (
        WORKBENCH_ROOT / "shared" / "feedback" / "service.jsx"
    ).read_text(encoding="utf-8")
    chat = (WORKBENCH_ROOT / "workbench-chat.jsx").read_text(encoding="utf-8")
    library = (WORKBENCH_ROOT / "workbench-library.jsx").read_text(encoding="utf-8")
    standalone_pdf = (ROOT / "src" / "route" / "pdf.py").read_text(encoding="utf-8")

    assert "window.clearInterval(_pollTimer)" in actions
    assert 'window.addEventListener("beforeunload", dispose' in actions
    assert "observer.disconnect()" in actions
    assert 'root.addEventListener("beforeunload", dispose' in feedback
    assert "window.clearTimeout(toastTimers[id])" in feedback
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
global.Notification = function () {};
global.setInterval = () => 0;
global.clearInterval = () => {};
global.addEventListener = () => {};
global.fetch = async (url) => {
  if (!String(url).startsWith("/api/ui-data")) throw new Error("unexpected fetch " + url);
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
  vm.runInThisContext(fs.readFileSync(sourcePath, "utf8"), { filename: sourcePath });
}
(async () => {
  const dataStore = window.CyreneUI.require("data");
  const eventBridge = window.CyreneUI.require("events");
  await dataStore.ready;
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
    assert payload["sourceClosed"] is True
    assert payload["listenerCountAfterDispose"] == 0
    assert "__bump()" not in data_source.read_text(encoding="utf-8")
