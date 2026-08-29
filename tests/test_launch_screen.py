import json
import subprocess
from pathlib import Path

from conftest import workbench_shell_source


ROOT = Path(__file__).resolve().parent.parent


def test_launch_screen_is_static_minimal_and_theme_aware():
    index = (ROOT / "src/cyrene/workbench/webui/frontend/index.html").read_text(encoding="utf-8")

    assert 'id="cyrene-launch-screen"' in index
    assert '<img src="logo-mark.png"' in index
    assert "<strong>Cyrene</strong>" in index
    assert 'html[data-theme="dark"] #cyrene-launch-screen' in index
    assert "width: 72px" in index
    assert "gap: 20px" in index
    assert "font-size: 34px" in index
    assert 'window.addEventListener("cyrene:ready", requestLaunchReady' in index
    assert "window.markCyreneReady" not in index
    assert "launchRequestsInFlight" in index
    assert "launchIdleTimer = window.setTimeout(finishLaunch, 300)" in index
    assert "launchDeadlineTimer = window.setTimeout(() => finishLaunch(true), 20000)" in index
    assert "launchFontsPending" in index
    assert "window.fetch = guardedFetch" in index
    assert "response.status === 401" in index
    assert 'new CustomEvent("cyrene:page-invalidated"' in index
    assert 'error.code = "page_invalidated"' in index


def test_launch_screen_waits_for_initial_workbench_content():
    data = (ROOT / "src/cyrene/workbench/webui/frontend/platform/data-store.jsx").read_text(encoding="utf-8")
    app = (ROOT / "src/cyrene/workbench/webui/frontend/entry/bootstrap.jsx").read_text(encoding="utf-8")
    workbench = workbench_shell_source()
    ui_surface = (ROOT / "src/cyrene/workbench/webui/frontend/platform/ui-surface.jsx").read_text(encoding="utf-8")
    markdown_actions = (ROOT / "src/cyrene/workbench/webui/frontend/shared/markdown/actions.jsx").read_text(encoding="utf-8")

    assert "DATA_STORE.ready = bootstrapData()" in data
    assert 'window.CyreneUI.data = window.CyreneUI.register("data", DATA_STORE)' in data
    assert "window.cyreneInitialDataReady" not in data
    assert 'surface === "quick-chat"' in app
    assert 'window.CyreneUI.has(name)' in app
    assert 'workbenchServicesReady(surface)' in app
    assert 'Date.now() - workbenchMountStartedAt < 2500' in app
    assert 'window.location.reload()' in app
    assert 'retries < 2' in app
    assert 'root.dispatchEvent(new CustomEvent("cyrene:ready"))' in (
        ROOT / "src/cyrene/workbench/webui/frontend/platform/readiness.jsx"
    ).read_text(encoding="utf-8")
    assert "if (loading || launchReadyRef.current) return undefined" in workbench
    assert "Promise.resolve(dataStore.ready)" in workbench
    assert "workbenchServices.readiness().markReady()" in workbench
    assert "workbenchReactRoot.unmount()" in app
    assert '"cyrene:page-invalidated"' in app
    assert 'window.addEventListener("pagehide", disposePageData' in data
    assert 'window.addEventListener("unload", disposePageData' in data
    assert 'fetch("/api/status", { cache: "no-store" })' in data
    assert 'root.addEventListener("cyrene:page-invalidated", disposeSurface' in ui_surface
    assert 'root.cyrene.uiSurface.unregister(instanceId)' in ui_surface
    assert 'surfaceSocket.close()' in ui_surface
    assert '} else if (typeof root.WebSocket === "function") {' not in ui_surface
    assert 'if (typeof root.WebSocket === "function") {' in ui_surface
    assert 'window.addEventListener("cyrene:page-invalidated", dispose' in markdown_actions


def test_terminal_frontend_preserves_tui_controls_and_large_scrollback():
    terminal = (ROOT / "src/cyrene/workbench/webui/frontend/terminal/entry.jsx").read_text(
        encoding="utf-8"
    )
    shell_integration = (
        ROOT / "src/cyrene/workbench/webui/frontend/terminal/shell-integration.mjs"
    ).read_text(encoding="utf-8")
    styles = (ROOT / "src/cyrene/workbench/webui/frontend/terminal/terminal.css").read_text(
        encoding="utf-8"
    )

    assert "TERMINAL_SCROLLBACK_LINES = 100000" in terminal
    assert "terminal.onData" in terminal
    assert "terminal.onBinary" in terminal
    assert "terminal.attachCustomKeyEventHandler" in shell_integration
    assert 'event.key === "Escape"' in shell_integration
    assert 'event.key.startsWith("Arrow")' in shell_integration
    assert 'socket.send(JSON.stringify({ type: "resize"' in terminal
    assert 'message.type === "resync_required"' in terminal
    assert 'socket.close(4001, "terminal output backlog")' in terminal
    assert 'setFullscreen(function (value) { return !value; })' in terminal
    assert ".wbc-terminal-pane.is-fullscreen" in styles


def test_stale_page_stops_same_origin_api_requests_after_first_401():
    script = r'''
const fs = require("fs");
const vm = require("vm");
const html = fs.readFileSync(process.argv[1], "utf8");
const match = html.match(/<script>\s*(\(\(\) => \{[\s\S]*?\}\)\(\);)\s*<\/script>/);
if (!match) throw new Error("launch lifecycle script not found");
global.window = global;
global.location = { href: "http://127.0.0.1:4242/", origin: "http://127.0.0.1:4242" };
global.localStorage = { getItem: () => null };
global.document = {
  documentElement: {
    dataset: {},
    style: { setProperty: () => {} },
  },
  getElementById: () => null,
  fonts: { load: () => Promise.resolve() },
};
global.requestAnimationFrame = (fn) => fn();
global.CustomEvent = function (type, init) { this.type = type; this.detail = init.detail; };
const listeners = {};
global.addEventListener = (name, fn) => { listeners[name] = fn; };
global.dispatchEvent = (event) => { if (listeners[event.type]) listeners[event.type](event); };
let nativeCalls = 0;
global.fetch = async () => { nativeCalls += 1; return { status: 401, ok: false }; };
let disposed = 0;
global.CyreneUI = {
  has: (name) => name === "data",
  require: () => ({ dispose: () => { disposed += 1; } }),
};
vm.runInThisContext(match[1]);
(async () => {
  await global.fetch("/api/status");
  let blocked = false;
  try { await global.fetch("/api/sessions"); } catch (error) {
    blocked = error && error.code === "page_invalidated";
  }
  process.stdout.write(JSON.stringify({
    nativeCalls,
    disposed,
    blocked,
    invalidated: global.CyrenePageLifecycle.isInvalidated(),
  }));
})().catch((error) => { console.error(error); process.exitCode = 1; });
'''
    result = subprocess.run(
        ["node", "-e", script, str(ROOT / "src/cyrene/workbench/webui/frontend/index.html")],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    assert json.loads(result.stdout) == {
        "nativeCalls": 1,
        "disposed": 1,
        "blocked": True,
        "invalidated": True,
    }
