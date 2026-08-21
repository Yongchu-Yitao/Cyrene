from conftest import workbench_chat_source
from pathlib import Path


def test_remote_pairing_refreshes_devices_without_reentering_loading_state():
    root = Path(__file__).resolve().parent.parent
    source = (
        root / "src" / "webui" / "frontend" / "settings-overlay.jsx"
    ).read_text(encoding="utf-8")
    remote_panel = source.split("function RemotePanel(p) {", 1)[1].split(
        "function RemotePeerCard", 1
    )[0]

    assert "function loadRemote(options)" in remote_panel
    assert "if (!background) setLoading(true);" in remote_panel
    assert "loadRemote({ background: true })" in remote_panel
    assert "function upsertRemotePeer(peer)" in remote_panel
    assert "upsertRemotePeer(payload.peer);" in remote_panel
    assert "var timer = setInterval(refresh, 1000);" in remote_panel
    assert "previousIds.indexOf(peer.device_id) < 0" in remote_panel


def test_remote_context_catalog_refreshes_live_without_restart():
    root = Path(__file__).resolve().parent.parent
    workbench = workbench_chat_source()
    settings = (
        root / "src" / "webui" / "frontend" / "settings-overlay.jsx"
    ).read_text(encoding="utf-8")
    events = (
        root / "src" / "webui" / "frontend" / "platform" / "events.jsx"
    ).read_text(encoding="utf-8")

    assert 'fetch("/api/remote/context-devices", { cache: "no-store" })' in workbench
    assert 'event.type === "remote_devices_changed"' in workbench
    assert 'new BroadcastChannel("cyrene-remote-devices")' in workbench
    assert 'window.addEventListener("focus"' in workbench
    assert 'document.visibilityState === "visible"' in workbench
    assert 'window.dispatchEvent(new CustomEvent("cyrene:remote-devices-changed"' in settings
    assert '"remote_devices_changed"' in events
