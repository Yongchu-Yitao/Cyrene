// Browser capture owns viewport staging, CDP capture and native fallback.

function screenshotClip(viewport, { highResolution = false, targetWidth = 0, targetHeight = 0 }) {
  const width = Math.max(1, Number(viewport.clientWidth) || 1);
  const height = Math.max(1, Number(viewport.clientHeight) || 1);
  const desiredWidth = highResolution ? Math.max(0, Number(targetWidth) || 0) : 0;
  const desiredHeight = highResolution ? Math.max(0, Number(targetHeight) || 0) : 0;
  const scale = Math.max(1, Math.min(4, Math.max(
    desiredWidth > 0 ? desiredWidth / width : 1,
    desiredHeight > 0 ? desiredHeight / height : 1,
  )));
  return { x: Number(viewport.pageX) || 0, y: Number(viewport.pageY) || 0, width, height, scale };
}

async function captureWithDebugger(manager, tab, options, pageWidth) {
  let pngBase64 = '';
  // Hidden task views may have no native compositor bitmap. Use CDP without
  // selecting the task's browser pane in the user's workspace.
  let stagedBounds = null;
  try {
    const debug = await manager._ensureDebugger(tab);
    let metrics = await debug.sendCommand('Page.getLayoutMetrics');
    let viewport = metrics.cssVisualViewport || metrics.visualViewport || {};
    if (Number(viewport.clientWidth) <= 8 || Number(viewport.clientHeight) <= 8) {
      const win = manager.surfaceWindow();
      if (win && manager.attachedTabId === tab.id) {
        stagedBounds = tab.view.getBounds();
        const hostBounds = win.getContentBounds();
        const bounds = { x: Math.max(0, hostBounds.width - 1), y: Math.max(0, hostBounds.height - 1),
          width: pageWidth, height: 800 };
        // As in prepareBoundsTransition, a 1px intersection lets macOS
        // create the first compositor surface without displaying the page.
        tab.view.setBounds(bounds);
        tab.view.setVisible(true);
        await manager.settlePageViewport(tab.view, bounds);
        metrics = await debug.sendCommand('Page.getLayoutMetrics');
        viewport = metrics.cssVisualViewport || metrics.visualViewport || {};
      }
    }
    const result = await debug.sendCommand('Page.captureScreenshot', {
      format: 'png',
      fromSurface: true,
      captureBeyondViewport: false,
      clip: screenshotClip(viewport, options),
    });
    pngBase64 = String(result && result.data || '');
  } catch (_) {
  } finally {
    if (stagedBounds) {
      tab.view.setVisible(false);
      tab.view.setBounds(stagedBounds);
      manager.syncAttachedView();
    }
  }
  return pngBase64;
}

async function browserScreenshot(manager, options, { pageWidth, validatePngBuffer }) {
  const tabId = options.tabId || '';
  const tab = tabId ? manager.tabs.get(String(tabId)) : manager.tabs.get(manager.activeTabId);
  if (!tab) return { ok: false, error: 'No browser tab is open.' };
  let pngBase64 = await captureWithDebugger(manager, tab, options, pageWidth);
  if (!pngBase64) {
    const image = await tab.view.webContents.capturePage();
    pngBase64 = image.toPNG().toString('base64');
  }
  let validation;
  try {
    validation = validatePngBuffer(Buffer.from(pngBase64, 'base64'));
    if (validation.width <= 8 || validation.height <= 8) {
      throw new Error('browser viewport has no usable screenshot area');
    }
  } catch (err) {
    return {
      ok: false,
      error: `Browser screenshot validation failed: ${String((err && err.message) || err)}`,
      title: tab.view.webContents.getTitle(),
      url: tab.view.webContents.getURL(),
      tabId: tab.id,
    };
  }
  return {
    ok: true,
    pngBase64,
    ...validation,
    title: tab.view.webContents.getTitle(),
    url: tab.view.webContents.getURL(),
    tabId: tab.id,
  };
}

async function initializeHiddenPageViewport(manager, tab, targetWidth) {
  const win = manager.surfaceWindow();
  if (!win || manager.attachedTabId !== tab.id) return;
  const viewport = await tab.view.webContents.executeJavaScript(
    '({ width: window.innerWidth, height: window.innerHeight })', true);
  if (viewport.width > 8 && viewport.height > 8) return;
  const originalBounds = tab.view.getBounds();
  const host = win.getContentBounds();
  const stagingBounds = { x: Math.max(0, host.width - 1), y: Math.max(0, host.height - 1),
    width: targetWidth, height: 800 };
  // Initialize Chromium's layout before the first snapshot or interaction,
  // even when the task's browser pane has never been opened.
  try {
    tab.view.setBounds(stagingBounds);
    tab.view.setVisible(true);
    await manager.settlePageViewport(tab.view, stagingBounds);
  } finally {
    tab.view.setVisible(false);
    tab.view.setBounds(originalBounds);
    manager.syncAttachedView();
  }
}

module.exports = { browserScreenshot, initializeHiddenPageViewport };
