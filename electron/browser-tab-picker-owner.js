// Owns the picker view, parent, window subscription and hide timer.
// Browser actions and tab storage stay with BrowserTabManager.
class BrowserTabPicker {
  constructor({ sessionId, View, preloadPath, flatChromeCSS, pickerUrl,
    ownerWindow, activeTabId, tabSnapshots, tabCount, surfaceBounds, hostReady }) {
    this.sessionId = sessionId;
    this.View = View;
    this.preloadPath = preloadPath;
    this.flatChromeCSS = flatChromeCSS;
    this.pickerUrl = pickerUrl;
    this.ownerWindow = ownerWindow;
    this.activeTabId = activeTabId;
    this.tabSnapshots = tabSnapshots;
    this.tabCount = tabCount;
    this.surfaceBounds = surfaceBounds;
    this.hostReady = hostReady;
    this.tabPickerView = null;
    this.tabPickerParent = null;
    this.tabPickerState = {
      visible: false,
      closing: false,
      variant: 'maximized',
      colors: {},
      labels: {},
    };
    this.tabPickerReady = false;
    this.tabPickerWindow = null;
    this._tabPickerWindowBlurHandler = null;
    this._tabPickerHideTimer = null;
  }

  tabPickerSnapshot() {
    const state = this.tabPickerState || {};
    return {
      sessionId: this.sessionId,
      visible: state.visible === true,
      closing: state.closing === true,
      variant: state.variant === 'split' ? 'split' : 'maximized',
      activeTabId: this.activeTabId(),
      tabs: this.tabSnapshots(),
      labels: state.labels && typeof state.labels === 'object' ? state.labels : {},
      colors: state.colors && typeof state.colors === 'object' ? state.colors : {},
    };
  }

  notifyTabPickerRenderer(extra = {}) {
    const win = this.ownerWindow();
    if (!win) return;
    try {
      win.webContents.send('browser:tab-picker-action', {
        sessionId: this.sessionId,
        visible: this.tabPickerState.visible === true,
        variant: this.tabPickerState.variant === 'split' ? 'split' : 'maximized',
        ...extra,
      });
    } catch (_) {}
  }

  ensureTabPickerView() {
    if (this.tabPickerView && !this.tabPickerView.webContents.isDestroyed()) return this.tabPickerView;
    if (!this.View) throw new Error('Electron WebContentsView is unavailable.');
    const view = new this.View({
      webPreferences: {
        preload: this.preloadPath,
        contextIsolation: true,
        nodeIntegration: false,
        sandbox: true,
        backgroundThrottling: true,
      },
    });
    this.tabPickerReady = false;
    try { view.setBackgroundColor('#00000000'); } catch (_) {}
    view.webContents.on('did-finish-load', async () => {
      if (this.tabPickerView !== view) return;
      try {
        await view.webContents.insertCSS(this.flatChromeCSS);
      } catch (_) {}
      if (this.tabPickerView !== view || view.webContents.isDestroyed()) return;
      this.tabPickerReady = true;
      this.pushTabPickerState();
      if (this.tabPickerState.visible) {
        try { view.webContents.focus(); } catch (_) {}
      }
    });
    view.webContents.on('did-fail-load', (_event, code, description) => {
      if (Number(code) === -3) return;
      console.warn(`[electron] Browser tab picker failed to load (${code}): ${description}`);
    });
    this.tabPickerView = view;
    const pickerUrl = this.pickerUrl();
    view.webContents.loadURL(pickerUrl).catch((err) => {
      console.error('[electron] Failed to load browser tab picker:', err);
    });
    return view;
  }

  pushTabPickerState() {
    const view = this.tabPickerView;
    if (!view || view.webContents.isDestroyed()) return;
    try { view.webContents.send('browser-tab-picker:state', this.tabPickerSnapshot()); } catch (_) {}
  }

  tabPickerBounds() {
    const surface = this.surfaceBounds();
    const variant = this.tabPickerState.variant === 'split' ? 'split' : 'maximized';
    const horizontalInset = variant === 'maximized' ? 116 : 12;
    // The native page starts below the browser navigation row. Lift the
    // floating picker by that chrome height so it sits beneath the title bar
    // and overlays the navigation row, matching the renderer-hosted menu.
    const verticalLift = 60;
    const availableWidth = Math.max(0, surface.width - horizontalInset);
    const width = Math.min(560, availableWidth);
    const rows = Math.max(1, this.tabCount());
    const desiredHeight = 22 + (rows * 48);
    const height = Math.min(350, desiredHeight, Math.max(0, surface.height - 12));
    return {
      x: surface.x + Math.max(0, Math.round((surface.width - width) / 2)),
      y: Math.max(0, surface.y - verticalLift),
      width: Math.max(0, Math.round(width)),
      height: Math.max(0, Math.round(height)),
    };
  }

  trackTabPickerWindow(win) {
    if (this.tabPickerWindow === win) return;
    if (this.tabPickerWindow && this._tabPickerWindowBlurHandler) {
      try { this.tabPickerWindow.removeListener('blur', this._tabPickerWindowBlurHandler); } catch (_) {}
    }
    this.tabPickerWindow = win || null;
    this._tabPickerWindowBlurHandler = null;
    if (!win || win.isDestroyed()) return;
    this._tabPickerWindowBlurHandler = () => {
      if (this.tabPickerState.visible) this.dismissTabPicker(true);
    };
    win.on('blur', this._tabPickerWindowBlurHandler);
  }

  finishTabPickerHide() {
    if (this.tabPickerState.visible) return;
    if (this._tabPickerHideTimer) clearTimeout(this._tabPickerHideTimer);
    this._tabPickerHideTimer = null;
    this.tabPickerState = { ...this.tabPickerState, closing: false };
    const view = this.tabPickerView;
    const win = this.ownerWindow();
    let restoreRendererFocus = false;
    if (view && !view.webContents.isDestroyed()) {
      try {
        restoreRendererFocus = !!(win && win.isFocused() && view.webContents.isFocused());
      } catch (_) {}
      try { view.setVisible(false); } catch (_) {}
    }
    if (restoreRendererFocus && win) {
      try { win.webContents.focus(); } catch (_) {}
    }
  }

  dismissTabPicker(animate = true) {
    const wasVisible = this.tabPickerState.visible === true;
    const wasClosing = this.tabPickerState.closing === true;
    if (!wasVisible && !wasClosing) return { ok: true, visible: false };
    if (this._tabPickerHideTimer) clearTimeout(this._tabPickerHideTimer);
    this._tabPickerHideTimer = null;
    const shouldAnimate = animate === true && wasVisible && this.tabPickerReady
      && !!this.tabPickerView && !this.tabPickerView.webContents.isDestroyed();
    this.tabPickerState = {
      ...this.tabPickerState,
      visible: false,
      closing: shouldAnimate,
    };
    this.pushTabPickerState();
    this.notifyTabPickerRenderer({ type: 'visibility' });
    if (!shouldAnimate) {
      this.finishTabPickerHide();
    } else {
      this._tabPickerHideTimer = setTimeout(() => this.finishTabPickerHide(), 220);
    }
    return { ok: true, visible: false };
  }

  syncTabPicker(container, raise = false) {
    const parent = container && container.contentView ? container.contentView : container;
    const hostReady = !!(this.hostReady() && parent && this.activeTabId());
    if (!hostReady) {
      this.hideForUnavailableHost();
      return;
    }
    if (!this.tabPickerState.visible && !this.tabPickerState.closing) {
      this.finishTabPickerHide();
      return;
    }
    const bounds = this.tabPickerBounds();
    if (bounds.width < 120 || bounds.height < 48) {
      this.dismissTabPicker(false);
      return;
    }
    const view = this.ensureTabPickerView();
    if (!this.attachTabPickerView(view, parent, bounds, raise)) return;
    this.trackTabPickerWindow(this.ownerWindow());
    try { view.setVisible(true); } catch (_) {}
    if (this.tabPickerState.visible) this.pushTabPickerState();
  }

  hideForUnavailableHost() {
    if (this.tabPickerState.visible || this.tabPickerState.closing) this.dismissTabPicker(false);
    else this.finishTabPickerHide();
  }

  attachTabPickerView(view, parent, bounds, raise) {
    if (this.tabPickerParent && this.tabPickerParent !== parent) {
      try { this.tabPickerParent.removeChildView(view); } catch (_) {}
      this.tabPickerParent = null;
    }
    try { view.setBounds(bounds); } catch (_) {}
    if (raise && this.tabPickerParent === parent) {
      try { parent.removeChildView(view); } catch (_) {}
      this.tabPickerParent = null;
    }
    if (this.tabPickerParent !== parent) {
      try {
        parent.addChildView(view);
        this.tabPickerParent = parent;
      } catch (err) {
        console.error('[electron] Failed to attach browser tab picker:', err);
        this.dismissTabPicker(false);
        return false;
      }
    }
    return true;
  }

  setTabPicker(info = {}) {
    const requestedVariant = info.variant === 'split' ? 'split' : 'maximized';
    if (info.visible !== true || !this.tabCount()) {
      if ((this.tabPickerState.visible || this.tabPickerState.closing)
        && this.tabPickerState.variant !== requestedVariant) {
        return { ok: true, visible: this.tabPickerState.visible === true };
      }
      return this.dismissTabPicker(true);
    }
    if (this._tabPickerHideTimer) clearTimeout(this._tabPickerHideTimer);
    this._tabPickerHideTimer = null;
    const labels = info.labels && typeof info.labels === 'object' ? info.labels : {};
    const colors = info.colors && typeof info.colors === 'object' ? info.colors : {};
    this.tabPickerState = {
      visible: true,
      closing: false,
      variant: requestedVariant,
      labels: Object.fromEntries(Object.entries(labels).map(([key, value]) => [String(key), String(value || '').slice(0, 120)])),
      colors: Object.fromEntries(Object.entries(colors).map(([key, value]) => [String(key), String(value || '').slice(0, 120)])),
    };
    this.syncTabPicker(this.ownerWindow()?.contentView || null, true);
    this.notifyTabPickerRenderer({ type: 'visibility' });
    this.focusTabPickerSoon();
    return { ok: true, visible: this.tabPickerState.visible };
  }

  focusTabPickerSoon() {
    const view = this.tabPickerView;
    if (view && !view.webContents.isDestroyed()) {
      setTimeout(() => {
        if (!this.tabPickerState.visible || this.tabPickerView !== view) return;
        try { view.webContents.focus(); } catch (_) {}
      }, 0);
    }
  }

  dispose() {
    if (this._tabPickerHideTimer) clearTimeout(this._tabPickerHideTimer);
    this._tabPickerHideTimer = null;
    if (this.tabPickerWindow && this._tabPickerWindowBlurHandler) {
      try { this.tabPickerWindow.removeListener('blur', this._tabPickerWindowBlurHandler); } catch (_) {}
    }
    this.tabPickerWindow = null;
    this._tabPickerWindowBlurHandler = null;
    if (this.tabPickerView && this.tabPickerParent) {
      try { this.tabPickerParent.removeChildView(this.tabPickerView); } catch (_) {}
    }
    if (this.tabPickerView && !this.tabPickerView.webContents.isDestroyed()) {
      try { this.tabPickerView.webContents.close(); } catch (_) {}
    }
    this.tabPickerView = null;
    this.tabPickerParent = null;
    this.tabPickerReady = false;
    this.tabPickerState = {
      visible: false,
      closing: false,
      variant: 'maximized',
      colors: {},
      labels: {},
    };
  }
}

module.exports = { BrowserTabPicker };
