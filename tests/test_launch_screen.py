from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent


def test_launch_screen_is_static_minimal_and_theme_aware():
    index = (ROOT / "src/webui/static/app/index.html").read_text(encoding="utf-8")

    assert 'id="cyrene-launch-screen"' in index
    assert '<img src="logo-mark.png"' in index
    assert "<strong>Cyrene</strong>" in index
    assert 'html[data-theme="dark"] #cyrene-launch-screen' in index
    assert "width: 72px" in index
    assert "gap: 20px" in index
    assert "font-size: 34px" in index
    assert "window.markCyreneReady" in index
    assert "launchRequestsInFlight" in index
    assert "launchIdleTimer = window.setTimeout(finishLaunch, 300)" in index
    assert "launchDeadlineTimer = window.setTimeout(finishLaunch, 20000)" in index
    assert "window.fetch = nativeFetch" in index


def test_launch_screen_waits_for_initial_workbench_content():
    data = (ROOT / "src/webui/static/app/data.jsx").read_text(encoding="utf-8")
    app = (ROOT / "src/webui/static/app/app.jsx").read_text(encoding="utf-8")
    workbench = (ROOT / "src/workbench-webui/workbench.jsx").read_text(encoding="utf-8")

    assert "window.cyreneInitialDataReady = bootstrapData()" in data
    assert "if (workbench) return undefined" in app
    assert "if (loading || launchReadyRef.current) return undefined" in workbench
    assert "Promise.resolve(window.cyreneInitialDataReady)" in workbench
    assert 'typeof window.markCyreneReady === "function"' in workbench
