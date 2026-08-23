"""Behavior and compatibility contracts for the WebUI consolidation.

These tests intentionally freeze external/runtime behavior before source files
move.  Path-only assertions may be updated during the mechanical move, but the
OpenAPI, tool wire, event, and dependency assertions must remain equivalent.
"""

from __future__ import annotations
from conftest import workbench_chat_source

import hashlib
import importlib
from collections import Counter
from importlib.metadata import version
import json
from pathlib import Path
import re
import subprocess


ROOT = Path(__file__).resolve().parents[1]
WEBUI_ROOT = ROOT / "src" / "webui"
WORKBENCH_ROOT = WEBUI_ROOT / "frontend"
INDEX = WORKBENCH_ROOT / "index.html"

OPENAPI_OPERATION_COUNT = 369
OPENAPI_BASELINE_FASTAPI = "0.136.1"
OPENAPI_BASELINE_PYDANTIC = "2.13.4"
OPENAPI_SHA256 = "2b853d76034d24d27cd276b3e97e44924681ca3a28faff47abc4a766638aabc5"
TOOL_REGISTRY_SHA256 = "c0b57a692939d438ea6b0efb09217929b3e01c6dff2e34ced5f7073dabc2b300"
MAIN_WIRE_SHA256 = "6a50a404102eb9093a8c8e6591af4862a78da8171f979b291698d560b95d5a0c"
SUBAGENT_WIRE_SHA256 = "78c1a2a7d7afcf057d453abb314c551e6a92952335ec3e7b497ae3e2404fa24d"

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


def _sha256_json(value: object) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    return hashlib.sha256(encoded).hexdigest()


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


def test_openapi_contract_matches_locked_generator_baseline():
    from webui.server import create_app

    # The OpenAPI renderer is dependency-sensitive.  Keep the exact generator
    # versions beside the strict hash so a dependency update requires an
    # intentional schema review instead of producing an unexplained mismatch.
    assert version("fastapi") == OPENAPI_BASELINE_FASTAPI
    assert version("pydantic") == OPENAPI_BASELINE_PYDANTIC

    schema = create_app(None, ":memory:").openapi()
    operations = _openapi_operations(schema)
    operation_count = len(operations)
    schema_hash = _sha256_json(schema)
    operation_ids = [str(operation.get("operationId") or "") for _, _, operation in operations]
    duplicate_ids = sorted(operation_id for operation_id, count in Counter(operation_ids).items() if operation_id and count > 1)

    assert operation_count == OPENAPI_OPERATION_COUNT, f"OpenAPI operation count changed: expected={OPENAPI_OPERATION_COUNT}, actual={operation_count}, actual_sha256={schema_hash}"
    assert all(operation_ids), "OpenAPI operations without operationId"
    assert duplicate_ids == [], f"Duplicate OpenAPI operationIds: {duplicate_ids}"
    unexpected_parameters = _unexpected_dependency_parameters(operations)
    assert unexpected_parameters == [], f"Application dependencies leaked into the HTTP contract: {unexpected_parameters}"
    assert schema_hash == OPENAPI_SHA256, (
        f"OpenAPI schema hash changed after operation/parameter audit: expected={OPENAPI_SHA256}, actual={schema_hash}, operation_count={operation_count}"
    )


def test_tool_registry_wire_and_actor_policy_contracts_are_unchanged(monkeypatch):
    from cyrene.runtime import settings_store
    from cyrene.tool_impl import NATIVE_TOOL_MODULES
    from cyrene.tool_impl.office import kit as office_kit
    from cyrene.tooling import catalog, wire

    monkeypatch.setattr(
        settings_store,
        "get_models",
        lambda: [{"provider": "openai_compatible", "model": "custom-model"}],
    )
    tool_names = [str((tool_def.get("function") or {}).get("name") or "") for tool_def in catalog.TOOL_DEFS]
    assert all(tool_names)
    assert len(tool_names) == len(set(tool_names))
    assert set(tool_names) == set(catalog.TOOL_HANDLERS)
    assert all(callable(catalog.TOOL_HANDLERS[name]) for name in tool_names)

    # Office is an additive native tool family. Keep the pre-Office registry
    # locked while allowing that family to grow, and require every registered
    # Office definition to retain its handler and canonical object schema.
    legacy_defs = [tool_def for tool_def in catalog.TOOL_DEFS if not str(tool_def["function"]["name"]).startswith(("Office", "PowerPoint"))]
    office_defs = [tool_def for tool_def in catalog.TOOL_DEFS if tool_def not in legacy_defs]
    assert len(legacy_defs) == 138
    assert _sha256_json(legacy_defs) == TOOL_REGISTRY_SHA256
    expected_office_names = {importlib.import_module(module_name).TOOL_DEF["function"]["name"] for module_name in NATIVE_TOOL_MODULES if ".office." in module_name}
    for family in (
        office_kit.READ_DEFS,
        office_kit.EDIT_OPS,
        office_kit.COMPOSE,
        office_kit.REVIEW,
        office_kit.ADVANCED,
        office_kit.ESCAPE,
    ):
        expected_office_names.update(item[0] for item in family)
    assert {tool_def["function"]["name"] for tool_def in office_defs} == expected_office_names
    for tool_def in office_defs:
        function = tool_def["function"]
        assert function["description"]
        assert function["parameters"]["type"] == "object"
        assert function["name"] in catalog.TOOL_HANDLERS
    assert len(catalog._MAIN_ONLY_TOOLS) == 72

    assert len(wire.get_main_wire_tool_defs()) == 18
    assert wire.get_wire_bundle_hash("main") == MAIN_WIRE_SHA256
    assert len(wire.get_subagent_wire_tool_defs()) == 12
    assert wire.get_wire_bundle_hash("subagent") == SUBAGENT_WIRE_SHA256


def test_workbench_cross_script_globals_are_registered():
    globals_used: set[str] = set()
    for source_path in WORKBENCH_ROOT.rglob("*.jsx"):
        source = source_path.read_text(encoding="utf-8")
        globals_used.update(re.findall(r"\bwindow\.([A-Za-z_$][A-Za-z0-9_$]*)", source))

    owned_globals = {
        "CyreneUI": WORKBENCH_ROOT / "platform" / "runtime.jsx",
        "CyrenePageLifecycle": WORKBENCH_ROOT / "index.html",
        "CyreneTaskPane": WORKBENCH_ROOT / "features" / "task" / "index.jsx",
        "CyreneTerminalSurface": WORKBENCH_ROOT / "features" / "chat" / "page.jsx",
    }
    assert globals_used - BROWSER_AND_VENDOR_GLOBALS == set(owned_globals)
    owner_sources = {
        name: path.read_text(encoding="utf-8")
        for name, path in owned_globals.items()
    }
    assert "root.CyreneUI = {" in owner_sources["CyreneUI"]
    assert "window.CyrenePageLifecycle = Object.freeze({" in owner_sources["CyrenePageLifecycle"]
    assert "window.CyreneTaskPane = WorkbenchTaskPane" in owner_sources["CyreneTaskPane"]
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
        "pdfjs/pdf.min.js?v=0.8.0-beta1",
        "pdfjs/pdf_viewer.js?v=0.8.0-beta1",
        "compiled/app.js?v=0.8.0-beta1",
    ]

    positions = [scripts.index(script) for script in required_in_order]
    assert positions == sorted(positions)
    compiled_scripts = [script for script in scripts if script.startswith("compiled/")]
    assert compiled_scripts == ["compiled/app.js?v=0.8.0-beta1"]
    assert '<script type="module" src="compiled/app.js?v=0.8.0-beta1"></script>' in index


def test_single_webui_source_build_and_entrypoint_shape():
    """Keep one Workbench source/output root and no classic selector."""

    assert not (ROOT / "src" / "workbench-webui").exists()
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
    shell_source = (ROOT / "src" / "route" / "system" / "shell.py").read_text(encoding="utf-8")
    module_entry = (ROOT / "src" / "cyrene" / "__main__.py").read_text(encoding="utf-8")
    electron_source = (ROOT / "electron" / "main.js").read_text(encoding="utf-8")
    assert "/static/workbench-ui" not in server_source
    assert "shell=legacy" not in shell_source
    assert "--agent" not in module_entry
    assert "window.markCyreneReady" not in index_source
    assert "shell=legacy" not in electron_source
    assert "CYRENE_UI_MODE" not in electron_source


def test_ui_background_and_pdf_resources_have_explicit_cleanup_paths():
    actions = (WORKBENCH_ROOT / "shared" / "markdown" / "actions.jsx").read_text(encoding="utf-8")
    feedback = (WORKBENCH_ROOT / "shared" / "feedback" / "service.jsx").read_text(encoding="utf-8")
    chat = workbench_chat_source()
    library = (WORKBENCH_ROOT / "workbench-library.jsx").read_text(encoding="utf-8")
    standalone_pdf = (ROOT / "src" / "route" / "pdf.py").read_text(encoding="utf-8")

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
  if (requestUrl === "/api/sessions") return { ok: true, json: async () => ({ sessions: [], model_stats: [] }) };
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
