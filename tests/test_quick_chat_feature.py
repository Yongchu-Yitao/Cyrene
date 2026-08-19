import importlib
import json
import sys
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent


def _pillow_image_module():
    pil_module = sys.modules.get("PIL")
    if pil_module is not None and getattr(pil_module, "__spec__", None) is None:
        for module_name in list(sys.modules):
            if module_name == "PIL" or module_name.startswith("PIL."):
                sys.modules.pop(module_name, None)
    return importlib.import_module("PIL.Image")


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
        ROOT / "src" / "webui" / "frontend" / "settings-overlay.jsx"
    ).read_text(encoding="utf-8")

    assert "quickChatShortcut: 'CommandOrControl+Shift+Space'" in main
    assert "quickChatShortcut: normalizeQuickChatShortcut" in main
    assert "registerQuickChatShortcut(next.quickChatShortcut)" in main
    assert 'startCapture("system-quick-chat")' in settings
    assert "keysToAccelerator(keys)" in settings
    assert "updateDesktopSettings({ quickChatShortcut:" in settings


def test_quick_chat_surface_is_loaded_without_uploading_the_screenshot():
    quick_chat = (
        ROOT / "src" / "webui" / "frontend" / "workbench-quick-chat.jsx"
    ).read_text(encoding="utf-8")
    app = (ROOT / "src" / "webui" / "frontend" / "entry" / "bootstrap.jsx").read_text(
        encoding="utf-8"
    )
    index = (ROOT / "src" / "webui" / "frontend" / "index.html").read_text(
        encoding="utf-8"
    )

    assert 'get("surface")' in app
    assert 'var surface = readWorkbenchSurface();' in app
    assert 'return surface === "quick-chat"' in app
    assert 'var QuickChatApp = window.CyreneUI.require("quickChat").App;' in app
    assert "<QuickChatApp />" in app
    assert "compiled/workbench-quick-chat.js?v=0.7.11" in index
    # The picker pulls writable targets from the dedicated endpoint.
    assert "/api/workbench/quick-chat/targets" in quick_chat
    assert "getLaunchContext" in quick_chat
    # The screenshot is only uploaded if the user clicks "add" — the renderer
    # never calls /api/chat/upload directly; it hands the file to the shared
    # composer via the existing attachment event.
    assert "/api/chat/upload" not in quick_chat
    assert "cyrene:add-chat-attachments" in quick_chat
    assert 'window.CyreneUI.register("quickChat"' in quick_chat
    assert "App: QuickChatApp" in quick_chat


def test_quick_chat_reuses_the_shared_composer_not_a_fork():
    quick_chat = (
        ROOT / "src" / "webui" / "frontend" / "workbench-quick-chat.jsx"
    ).read_text(encoding="utf-8")
    chat = (
        ROOT / "src" / "webui" / "frontend" / "workbench-chat.jsx"
    ).read_text(encoding="utf-8")

    # The composer is shared, not duplicated.
    assert "Composer: WbcComposer" in chat
    assert "chatService.Composer" in quick_chat
    # Quick chat isolates its draft via a namespace. The window now stays open
    # after a send (continuous conversation), so the composer clears its input on
    # send using the default behavior rather than passing clearOnSend={false}.
    assert 'draftNamespace: "quick-chat:"' in quick_chat
    assert "clearOnSend={false}" not in quick_chat
    # The composer honors the new optional props (defaults preserve main chat).
    assert "draftNamespace" in chat
    assert "shouldClearOnSend" in chat
    assert "autoFocus" in chat


def test_quick_chat_send_close_and_sync_contract():
    quick_chat = (
        ROOT / "src" / "webui" / "frontend" / "workbench-quick-chat.jsx"
    ).read_text(encoding="utf-8")
    chat = (
        ROOT / "src" / "webui" / "frontend" / "workbench-chat.jsx"
    ).read_text(encoding="utf-8")
    workbench = (
        ROOT / "src" / "webui" / "frontend" / "workbench.jsx"
    ).read_text(encoding="utf-8")
    main = (ROOT / "electron" / "main.js").read_text(encoding="utf-8")
    preload = (ROOT / "electron" / "preload.js").read_text(encoding="utf-8")

    # New chat in the default project is created once; existing target sends
    # directly. The window stays open after a send so the conversation can
    # continue, and follow-ups reuse the pinned chat. The transcript is driven by
    # the shared singleton run-manager (WorkbenchChatRuntimes) and rendered with
    # the same message cards as the main chat, so the reply — with tool-call
    # traces and the live "thinking" card — streams in-place. We notify the main
    # window on send but do NOT close.
    assert "model.createChat" in quick_chat
    assert "createdChatIdRef" in quick_chat
    # Reuses the shared run-manager + transcript hooks instead of a forked
    # streaming loop, so the run is durable server-side and survives the window.
    assert "chatService.Runtimes" in quick_chat
    assert "runtimeEngine.start" in quick_chat
    assert "onUserMessage" in quick_chat
    assert "chat_run_in_progress" in quick_chat
    # Renders the shared message cards (not a simplified bubble) inside the
    # shared thread layout.
    assert "chatService.LiveMessage" in quick_chat
    assert 'className="wbc-thread wbq-thread"' in quick_chat
    # closeWindow is wired to ESC only, never to a successful send/ack.
    assert "resetAfterSend" not in quick_chat
    # Main-window sync: quick window notifies, main process forwards, the chat
    # module re-pulls.
    assert "notifySent" in quick_chat and "notifySent" in preload
    assert "quick-chat:notify-sent" in main
    assert "quick-chat:sent" in main and "quick-chat:sent" in preload
    assert "cyrene:wbc-refresh-chats" in workbench
    assert "cyrene:wbc-refresh-chats" in chat


def test_quick_chat_keeps_backend_alive_for_the_global_shortcut():
    main = (ROOT / "electron" / "main.js").read_text(encoding="utf-8")
    # Closing a window must not strand the global shortcut on a dead backend.
    assert "function appStaysResident()" in main
    assert "if (appStaysResident()) return;" in main
    # Screenshot memory is bounded and the bytes are never logged.
    assert "MAX_SCREENSHOT_BYTES" in main


def test_background_residency_exposes_a_tray_entrypoint():
    main = (ROOT / "electron" / "main.js").read_text(encoding="utf-8")
    package_json = json.loads((ROOT / "electron" / "package.json").read_text(encoding="utf-8"))

    assert "Tray," in main
    assert "Menu," in main
    assert "nativeImage," in main
    assert "DESKTOP_TRANSLATIONS" in main
    assert "language: ''" in main
    assert "normalizeDesktopLanguage" in main
    assert "app.getLocale()" in main
    assert "tray.png" in main
    assert "tray-mac.png" in main
    assert "tray-mac@2x.png" in main
    assert "image.addRepresentation" in main
    assert "scaleFactor: 2" in main
    assert "width: isMac ? 18 : 32" in main
    assert "setTemplateImage(true)" not in main
    assert "function ensureTray()" in main
    assert "function syncTrayWithSettings(settings)" in main
    assert "tray.on('click'" in main
    assert "tray.setContextMenu(buildTrayMenu())" in main
    assert "revealMainWindow()" in main
    assert "syncTrayWithSettings(next)" in main
    assert "syncTrayWithSettings(desktopSettings)" in main
    assert "destroyTray()" in main
    assert "打开 Cyrene" in main
    assert "退出 Cyrene" in main

    extra_resources = package_json["build"]["extraResources"]
    assert {"from": "../build/icon.png", "to": "build/icon.png"} in extra_resources
    assert {"from": "../build/icon.ico", "to": "build/icon.ico"} in extra_resources
    assert {"from": "../build/tray.png", "to": "build/tray.png"} in extra_resources
    assert {"from": "../build/tray-mac.png", "to": "build/tray-mac.png"} in extra_resources
    assert {"from": "../build/tray-mac@2x.png", "to": "build/tray-mac@2x.png"} in extra_resources


def test_tray_icon_is_a_small_transparent_colored_asset(real_pillow_modules):
    Image = real_pillow_modules
    mac1x = Image.open(ROOT / "build" / "tray-mac.png").convert("RGBA")
    mac2x = Image.open(ROOT / "build" / "tray-mac@2x.png").convert("RGBA")
    colored = Image.open(ROOT / "build" / "tray.png").convert("RGBA")

    assert mac1x.size == (18, 18)
    assert mac2x.size == (36, 36)
    assert colored.size == (32, 32)
    assert mac1x.getchannel("A").getextrema() == (0, 255)
    assert mac2x.getchannel("A").getextrema() == (0, 255)
    assert colored.getchannel("A").getextrema() == (0, 255)

    for image in (mac1x, mac2x, colored):
        assert image.getchannel("A").getbbox() is not None

    # The tray icon should remain the full-color app mark, not a template mask.
    for image in (mac1x, mac2x, colored):
        opaque_pixels = [
            image.getpixel((x, y))
            for y in range(image.height)
            for x in range(image.width)
            if image.getpixel((x, y))[3] > 0
        ]
        assert len({pixel[:3] for pixel in opaque_pixels}) > 16


def test_quick_chat_is_opt_in_behind_general_settings_toggles():
    main = (ROOT / "electron" / "main.js").read_text(encoding="utf-8")
    general = (
        ROOT / "src" / "webui" / "frontend" / "settings-overlay.jsx"
    ).read_text(encoding="utf-8")

    # Opt-in: the global shortcut is only claimed when quick chat is enabled,
    # which itself requires background residency.
    assert "quickChatEnabled: false" in main
    assert "if (desktopSettings.quickChatEnabled) {" in main
    assert "next.runInBackground === true && next.quickChatEnabled === true" in main
    assert "unregisterQuickChatShortcut()" in main
    # General settings exposes both toggles; the quick-chat toggle is disabled
    # until background residency is on.
    assert 'settings.runInBackground' in general
    assert 'settings.quickChatAssistant' in general
    assert "updateDesktopSettings({ language:" in general
    assert "applyDesktop({ runInBackground:" in general
    assert "applyDesktop({ quickChatEnabled:" in general
    assert "desktopBusy || !runInBackground" in general


def test_quick_chat_javascript_sources_parse():
    for relative_path in ("electron/main.js", "electron/preload.js"):
        subprocess.run(
            ["node", "--check", str(ROOT / relative_path)],
            check=True,
            capture_output=True,
            text=True,
        )
