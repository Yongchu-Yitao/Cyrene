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
