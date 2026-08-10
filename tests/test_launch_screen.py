from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent


def test_launch_screen_is_static_minimal_and_theme_aware():
    index = (ROOT / "src/webui/frontend/index.html").read_text(encoding="utf-8")

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
    assert "window.fetch = nativeFetch" in index


def test_launch_screen_waits_for_initial_workbench_content():
    data = (ROOT / "src/webui/frontend/platform/data-store.jsx").read_text(encoding="utf-8")
    app = (ROOT / "src/webui/frontend/entry/bootstrap.jsx").read_text(encoding="utf-8")
    workbench = (ROOT / "src/webui/frontend/workbench.jsx").read_text(encoding="utf-8")

    assert "DATA_STORE.ready = bootstrapData()" in data
    assert 'window.CyreneUI.data = window.CyreneUI.register("data", DATA_STORE)' in data
    assert "window.cyreneInitialDataReady" not in data
    assert 'readWorkbenchSurface() === "quick-chat"' in app
    assert 'root.dispatchEvent(new CustomEvent("cyrene:ready"))' in (
        ROOT / "src/webui/frontend/platform/readiness.jsx"
    ).read_text(encoding="utf-8")
    assert "if (loading || launchReadyRef.current) return undefined" in workbench
    assert "Promise.resolve(dataStore.ready)" in workbench
    assert 'window.CyreneUI.require("readiness").markReady()' in workbench
