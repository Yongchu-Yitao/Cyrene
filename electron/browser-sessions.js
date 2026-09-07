'use strict';

class BrowserSessions {
  constructor({ getMainWindow, createManager, normalizeBrowserSessionId }) {
    this.browserTabManagers = new Map();
    this.browserContentOwners = new WeakMap();
    this.activeBrowserDownloads = new Map();
    this.nextBrowserDownloadId = 1;
    this.browserManagerPublishTimer = null;
    this.activeBrowserSessionId = '';
    this.browserSurfaceObscured = false;
    this.getMainWindow = getMainWindow;
    this.createManager = createManager;
    this.normalizeBrowserSessionId = normalizeBrowserSessionId;
  }

  browserContentOwner(webContents) {
    const known = webContents && this.browserContentOwners.get(webContents);
    if (known) return known;
    for (const manager of this.browserTabManagers.values()) {
      for (const tab of manager.tabs.values()) {
        if (tab && tab.view && tab.view.webContents === webContents) {
          const owner = { sessionId: manager.sessionId, tabId: tab.id };
          this.browserContentOwners.set(webContents, owner);
          return owner;
        }
      }
    }
    return { sessionId: '', tabId: '' };
  }

  browserDownloadRecord(item, webContents) {
    const owner = this.browserContentOwner(webContents);
    let pageTitle = '';
    let pageUrl = '';
    try { pageTitle = String(webContents && webContents.getTitle() || ''); } catch (_) {}
    try { pageUrl = String(webContents && webContents.getURL() || ''); } catch (_) {}
    const id = `download_${this.nextBrowserDownloadId++}`;
    return {
      id,
      item,
      sessionId: String(owner.sessionId || ''),
      tabId: String(owner.tabId || ''),
      pageTitle,
      pageUrl,
      filename: String(item && item.getFilename && item.getFilename() || ''),
      url: String(item && item.getURL && item.getURL() || ''),
      receivedBytes: 0,
      totalBytes: 0,
      paused: false,
      state: 'progressing',
      startedAt: Date.now(),
    };
  }

  syncBrowserDownloadRecord(record, state) {
    const item = record && record.item;
    if (!record || !item) return;
    try { record.filename = String(item.getFilename() || record.filename || ''); } catch (_) {}
    try { record.receivedBytes = Math.max(0, Number(item.getReceivedBytes()) || 0); } catch (_) {}
    try { record.totalBytes = Math.max(0, Number(item.getTotalBytes()) || 0); } catch (_) {}
    try { record.paused = item.isPaused() === true; } catch (_) {}
    record.state = String(state || record.state || 'progressing');
  }

  trackBrowserDownload(item, webContents) {
    if (!item) return;
    const record = this.browserDownloadRecord(item, webContents);
    this.activeBrowserDownloads.set(record.id, record);
    this.syncBrowserDownloadRecord(record, 'progressing');
    this.publishBrowserManagerState();
    item.on('updated', (_event, state) => {
      if (!this.activeBrowserDownloads.has(record.id)) return;
      this.syncBrowserDownloadRecord(record, state);
      this.scheduleBrowserManagerStatePublish();
    });
    item.once('done', (_event, state) => {
      this.syncBrowserDownloadRecord(record, state);
      this.activeBrowserDownloads.delete(record.id);
      this.publishBrowserManagerState();
    });
  }

  controlBrowserDownload(downloadId, action) {
    const record = this.activeBrowserDownloads.get(String(downloadId || ''));
    const command = String(action || '').trim().toLowerCase();
    if (!record || !record.item) return { ok: false, error: 'download_not_found' };
    try {
      if (command === 'pause') {
        record.item.pause();
      } else if (command === 'resume') {
        record.item.resume();
      } else if (command === 'cancel') {
        record.item.cancel();
      } else {
        return { ok: false, error: 'unsupported_download_action' };
      }
      this.syncBrowserDownloadRecord(record, command === 'cancel' ? 'cancelled' : 'progressing');
      if (command === 'cancel') this.activeBrowserDownloads.delete(record.id);
      this.publishBrowserManagerState();
      return { ok: true, state: this.browserManagerState() };
    } catch (error) {
      return { ok: false, error: String(error && error.message || error || 'download_action_failed') };
    }
  }

  browserManagerState() {
    const downloads = Array.from(this.activeBrowserDownloads.values()).map((record) => ({
      id: record.id,
      sessionId: record.sessionId,
      tabId: record.tabId,
      pageTitle: record.pageTitle,
      pageUrl: record.pageUrl,
      filename: record.filename,
      url: record.url,
      receivedBytes: record.receivedBytes,
      totalBytes: record.totalBytes,
      paused: record.paused,
      state: record.state,
      startedAt: record.startedAt,
    }));
    const byPage = new Map();
    downloads.forEach((download) => {
      const key = `${download.sessionId}:${download.tabId}`;
      if (!byPage.has(key)) byPage.set(key, []);
      byPage.get(key).push(download);
    });

    const pages = [];
    for (const manager of this.browserTabManagers.values()) {
      const state = manager.state();
      for (const tab of state.tabs) {
        const key = `${state.sessionId}:${tab.id}`;
        pages.push({
          ...tab,
          key,
          sessionId: state.sessionId,
          tabId: tab.id,
          sessionActive: state.sessionId === this.activeBrowserSessionId,
          downloads: byPage.get(key) || [],
        });
        byPage.delete(key);
      }
    }

    return {
      ok: true,
      pageCount: pages.length,
      downloadCount: downloads.length,
      activeSessionId: this.activeBrowserSessionId,
      pages,
      downloads,
    };
  }

  publishBrowserManagerState() {
    if (!this.getMainWindow() || this.getMainWindow().isDestroyed() || !this.getMainWindow().webContents || this.getMainWindow().webContents.isDestroyed()) return;
    try { this.getMainWindow().webContents.send('browser:manager-state', this.browserManagerState()); } catch (_) {}
  }

  scheduleBrowserManagerStatePublish() {
    if (this.browserManagerPublishTimer) return;
    this.browserManagerPublishTimer = setTimeout(() => {
      this.browserManagerPublishTimer = null;
      this.publishBrowserManagerState();
    }, 100);
  }

  getBrowserTabManager(sessionId = this.activeBrowserSessionId) {
    const normalized = this.normalizeBrowserSessionId(sessionId);
    if (!this.browserTabManagers.has(normalized)) {
      this.browserTabManagers.set(normalized, this.createManager(normalized));
    }
    return this.browserTabManagers.get(normalized);
  }

  activateBrowserSession(info = {}) {
    const sessionId = this.normalizeBrowserSessionId(info.sessionId || info.session_id);
    if (sessionId !== this.activeBrowserSessionId) {
      const previous = this.browserTabManagers.get(this.activeBrowserSessionId);
      if (previous) {
        previous.hideAllAgentCursors();
        previous.visible = false;
        previous.syncAttachedView();
      }
      this.activeBrowserSessionId = sessionId;
    }
    const manager = this.getBrowserTabManager(sessionId);
    manager.setContext(info);
    manager.syncAttachedView();
    manager.emitState();
    return manager;
  }

  hideAllBrowserSessions() {
    for (const manager of this.browserTabManagers.values()) {
      manager.setBounds({ visible: false });
    }
  }

  setBrowserSurfaceObscured(obscured = false) {
    this.browserSurfaceObscured = obscured === true;
    for (const manager of this.browserTabManagers.values()) {
      manager.setObscured(this.browserSurfaceObscured);
    }
    return this.getBrowserTabManager(this.activeBrowserSessionId).state();
  }

  closeAllBrowserSessions() {
    for (const manager of this.browserTabManagers.values()) manager.closeAll();
    this.browserTabManagers.clear();
    this.activeBrowserDownloads.clear();
    if (this.browserManagerPublishTimer) clearTimeout(this.browserManagerPublishTimer);
    this.browserManagerPublishTimer = null;
    this.activeBrowserSessionId = '';
    this.browserSurfaceObscured = false;
    this.publishBrowserManagerState();
  }

  closeBrowserSession(sessionId) {
    const normalized = this.normalizeBrowserSessionId(sessionId);
    const manager = this.browserTabManagers.get(normalized);
    if (!manager) return { ok: true, sessionId: normalized, closed: false };
    manager.closeAll();
    this.browserTabManagers.delete(normalized);
    if (this.activeBrowserSessionId === normalized) this.activeBrowserSessionId = '';
    this.publishBrowserManagerState();
    return { ok: true, sessionId: normalized, closed: true };
  }
}

module.exports = { BrowserSessions };
