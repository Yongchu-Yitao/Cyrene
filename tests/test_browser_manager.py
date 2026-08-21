from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_electron_publishes_cross_session_browser_manager_state():
    main = read("electron/main.js")
    preload = read("electron/preload.js")

    assert "function browserManagerState()" in main
    assert "for (const manager of browserTabManagers.values())" in main
    assert "pageCount: pages.length" in main
    assert "browser:manager-state" in main
    assert "browser:get-manager-state" in main
    assert "getManagerState: () => ipcRenderer.invoke('browser:get-manager-state')" in preload
    assert "browser:control-download" in main
    assert "controlDownload: (info) => ipcRenderer.invoke('browser:control-download', info || {})" in preload
    assert "onManagerState: (callback)" in preload


def test_downloads_are_associated_with_the_originating_browser_page():
    main = read("electron/main.js")

    assert "browserSession.on('will-download'" in main
    assert "browserContentOwners.set(view.webContents, { sessionId: this.sessionId, tabId: id })" in main
    assert "downloads: byPage.get(key) || []" in main
    assert "item.on('updated'" in main
    assert "item.once('done'" in main
    assert "downloads," in main


def test_global_download_center_supports_pause_resume_cancel_and_progress():
    main = read("electron/main.js")
    source = read("src/webui/frontend/workbench.jsx")
    styles = read("src/webui/frontend/workbench.css")

    assert "function controlBrowserDownload(downloadId, action)" in main
    assert "record.item.pause()" in main
    assert "record.item.resume()" in main
    assert "record.item.cancel()" in main
    assert 'className="workbench-browser-manager-download-center"' in source
    assert 'className="workbench-browser-manager-download-list"' in source
    assert 'controlManagedBrowserDownload(download, download.paused ? "resume" : "pause", event)' in source
    assert 'controlManagedBrowserDownload(download, "cancel", event)' in source
    assert 'role="progressbar"' in source
    assert source.index('className="workbench-browser-manager-list"') < source.index('className="workbench-browser-manager-download-center"')
    assert ".workbench-browser-manager-download-center {" in styles
    assert "border-top:" in styles.split(".workbench-browser-manager-download-center {", 1)[1].split("}", 1)[0]
    assert "scrollbar-width: none" in styles.split(".workbench-browser-manager-list {", 1)[1].split("}", 1)[0]
    assert "scrollbar-width: none" in styles.split(".workbench-browser-manager-download-list {", 1)[1].split("}", 1)[0]
    assert ".workbench-browser-manager-download-list::-webkit-scrollbar" in styles


def test_topbar_manager_keeps_the_existing_pinned_resource_hint():
    source = read("src/webui/frontend/workbench.jsx")
    styles = read("src/webui/frontend/workbench.css")

    assert 'workbench-browser-manager-button' in source
    assert 'className="workbench-browser-manager-download-center"' in source
    assert 'className="workbench-resource-shelf-empty"' in source
    assert "!resources.length ? (" in source
    assert ".workbench-browser-manager-menu {" in styles
    assert ".workbench-resource-shelf-empty {" in styles
    assert source.index('className={"workbench-resource-shelf"') < source.index('className="workbench-browser-manager-anchor"')
    assert source.index('className="workbench-browser-manager-anchor"') < source.index('className="workbench-top-actions"')


def test_browser_manager_entry_hides_when_there_are_no_pages_or_downloads():
    source = read("src/webui/frontend/workbench.jsx")

    assert '&& (browserManagerPages.length || browserManagerDownloads.length) ? (' in source
    assert "if (browserManagerMenu && !browserManagerState.pageCount && !browserManagerState.downloadCount)" in source


def test_browser_manager_topbar_uses_page_favicons_and_opaque_flyout():
    source = read("src/webui/frontend/workbench.jsx")
    styles = read("src/webui/frontend/workbench.css")

    assert "browserManagerPreviewPages" in source
    assert 'className="workbench-browser-manager-preview"' in source
    assert "page.favicon ? <img" in source
    assert 'className="workbench-browser-manager-count"' not in source
    assert '"--wb-flyout-bg", "--wb-flyout-border", "--wb-flyout-shadow"' in source
    assert "background: var(--wb-flyout-bg, var(--wb-card-bg-strong, var(--wb-card-bg, #ffffff)));" in styles
    assert ".workbench-browser-manager-preview-icon img {" in styles
    assert "border-color: color-mix(in srgb, var(--wb-blue) 34%, var(--wb-line));" in styles
    manager_button = styles.split(".workbench-browser-manager-button {", 1)[1].split("}", 1)[0]
    preview_icon = styles.split(".workbench-browser-manager-preview-icon {", 1)[1].split("}", 1)[0]
    assert "height: 34px" in manager_button
    assert "width: 22px" in preview_icon


def test_browser_manager_can_pin_pages_and_pinned_browser_keeps_its_favicon():
    source = read("src/webui/frontend/workbench.jsx")
    styles = read("src/webui/frontend/workbench.css")

    assert "function toggleManagedBrowserPin(page, event)" in source
    assert "var pinnedResource = pinnedBrowserResource(page);" in source
    assert 'stableRef: String(page.sessionId || "")' in source
    assert 'favicon: String(page.favicon || "")' in source
    assert 'name={pinnedResource ? "pinned-off" : "pin"}' in source
    assert '<><WorkbenchAssetIcon name="browser" />{resource.favicon ? <img' in source
    assert "var owner = sessionTabCandidates.find" in source
    assert "browserBridge.activateTab({" in source
    assert "tabId: resource.tabId" in source
    assert "justify-content: flex-start" in styles.split(".workbench-resource-shelf {", 1)[1].split("}", 1)[0]
    assert "grid-template-columns: 174px minmax(0, max-content) minmax(34px, 1fr) auto auto;" in styles


def test_browser_manager_uses_packaged_tabler_icon_assets_and_i18n():
    build = read("src/webui/build-jsx.mjs")
    source = read("src/webui/frontend/workbench.jsx")
    translations = read("src/webui/frontend/workbench-i18n.jsx")

    for icon in ("browser.svg", "chevron-down.svg", "devices.svg", "download.svg", "reload.svg", "volume.svg", "volume-off.svg", "pin.svg", "pinned-off.svg", "player-pause.svg", "player-play.svg", "x.svg"):
        assert f"'{icon}'" in build
    assert 'name="devices"' in source
    assert '"workbench.browserManager.title": "Browser pages"' in translations
    assert '"workbench.browserManager.title": "浏览器页面"' in translations
