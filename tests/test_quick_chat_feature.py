import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent


def test_electron_quick_chat_main_process_contract():
    source = (ROOT / "electron" / "main.js").read_text(encoding="utf-8")

    assert "globalShortcut.register(requested" in source
    assert "globalShortcut.unregisterAll()" in source
    assert "async function captureQuickChatScreenshot()" in source
    assert "desktopCapturer.getSources" in source
    assert "screen.getCursorScreenPoint()" in source
    assert "screen.getDisplayNearestPoint(cursorPoint)" in source
    assert "pendingQuickChatScreenshot" in source
    assert "async function createQuickChatWindow()" in source
    assert "/?surface=quick-chat" in source
    assert "quickChatWindow.hide()" in source
    assert "setVisibleOnAllWorkspaces(true, { visibleOnFullScreen: true })" in source


def test_quick_chat_preload_exposes_narrow_bridge():
    source = (ROOT / "electron" / "preload.js").read_text(encoding="utf-8")

    assert "quickChat: {" in source
    assert "getLaunchContext:" in source
    assert "getScreenshot:" in source
    assert "clearScreenshot:" in source
    assert "openScreenPermissionSettings:" in source
    assert "onContextUpdated:" in source


def test_quick_chat_shortcut_is_persisted_by_the_main_process():
    main = (ROOT / "electron" / "main.js").read_text(encoding="utf-8")
    settings = (
        ROOT / "src" / "workbench-webui" / "settings-overlay.jsx"
    ).read_text(encoding="utf-8")

    assert "quickChatShortcut: 'CommandOrControl+Shift+Space'" in main
    assert "quickChatShortcut: normalizeQuickChatShortcut" in main
    assert "registerQuickChatShortcut(next.quickChatShortcut)" in main
    assert 'startCapture("system-quick-chat")' in settings
    assert "keysToAccelerator(keys)" in settings
    assert "updateDesktopSettings({ quickChatShortcut:" in settings


def test_quick_chat_surface_is_loaded_without_uploading_the_screenshot():
    quick_chat = (
        ROOT / "src" / "workbench-webui" / "workbench-quick-chat.jsx"
    ).read_text(encoding="utf-8")
    app = (ROOT / "src" / "webui" / "static" / "app" / "app.jsx").read_text(
        encoding="utf-8"
    )
    index = (ROOT / "src" / "webui" / "static" / "app" / "index.html").read_text(
        encoding="utf-8"
    )

    assert 'params.get("surface")' in app
    assert 'readUiSurfaceMode() === "quick-chat"' in app
    assert "<window.QuickChatApp />" in app
    assert "compiled/workbench-quick-chat.js?v=beta10" in index
    assert 'quickChatJson("/api/projects")' in quick_chat
    assert 'quickChatJson("/api/workbench/chats")' in quick_chat
    assert "getLaunchContext" in quick_chat
    assert "/api/chat/upload" not in quick_chat
    assert "window.QuickChatApp = QuickChatApp;" in quick_chat


def test_quick_chat_javascript_sources_parse():
    for relative_path in ("electron/main.js", "electron/preload.js"):
        subprocess.run(
            ["node", "--check", str(ROOT / relative_path)],
            check=True,
            capture_output=True,
            text=True,
        )
