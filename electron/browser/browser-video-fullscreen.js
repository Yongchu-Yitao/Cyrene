// Own native video fullscreen windows, listeners and exit timing.
// Dependencies expose host actions, never the complete BrowserTabManager.
let activeVideoFullscreenManager = null;

class BrowserVideoFullscreen {
  constructor(dependencies) {
    this.dependencies = dependencies;
    this.videoFullscreen = { active: false, external: false, tabId: '' };
    this.videoFullscreenWindow = null;
    this._videoFullscreenWindowClosing = false;
    this._mainWindowWasFullScreen = false;
    this._fullscreenResizeHandler = null;
    this._mainFullscreenLeaveHandler = null;
  }

  fullscreenTab() {
    if (!this.videoFullscreen.active) return null;
    return this.dependencies.tab(this.videoFullscreen.tabId) || null;
  }

  fullscreenBounds(win) {
    if (!win || win.isDestroyed()) return { x: 0, y: 0, width: 0, height: 0 };
    const size = win.getContentSize();
    return {
      x: 0,
      y: 0,
      width: Math.max(0, Math.round(Number(size && size[0]) || 0)),
      height: Math.max(0, Math.round(Number(size && size[1]) || 0)),
    };
  }

  syncVideoFullscreenBounds() {
    if (!this.videoFullscreen.active) return;
    this.dependencies.syncAttachedView();
  }

  requestVideoFullscreenExit() {
    const tab = this.fullscreenTab();
    const wc = tab && tab.view && tab.view.webContents;
    if (!wc || wc.isDestroyed()) {
      this.finishVideoFullscreen(tab && tab.view);
      return;
    }
    wc.executeJavaScript(`(() => {
      if (document.fullscreenElement && document.exitFullscreen) {
        return document.exitFullscreen().then(() => true).catch(() => false);
      }
      return false;
    })()`, true).catch(() => false).finally(() => {
      if (this._videoFullscreenExitTimer) clearTimeout(this._videoFullscreenExitTimer);
      this._videoFullscreenExitTimer = setTimeout(() => {
        this._videoFullscreenExitTimer = null;
        if (this.videoFullscreen.active) this.finishVideoFullscreen(tab.view);
      }, 260);
    });
  }

  async enterVideoFullscreen(view) {
    const tab = this.dependencies.tabForView(view);
    if (!tab || !view || view.webContents.isDestroyed()) return;
    if (activeVideoFullscreenManager && activeVideoFullscreenManager !== this) {
      activeVideoFullscreenManager.requestVideoFullscreenExit();
    }
    activeVideoFullscreenManager = this;
    this.dependencies.activate(tab.id);
    this._mainWindowWasFullScreen = !!(
      this.dependencies.mainWindow() && !this.dependencies.mainWindow().isDestroyed() && this.dependencies.mainWindow().isFullScreen()
    );
    this.videoFullscreen = {
      active: true,
      external: this.dependencies.isMac,
      tabId: tab.id,
    };

    if (this.dependencies.isMac) {
      this.enterExternalWindow();
    } else if ((this.dependencies.isWindows || this.dependencies.isLinux) && this.dependencies.mainWindow() && !this.dependencies.mainWindow().isDestroyed()) {
      this.enterHostWindow();
    }

    this.dependencies.syncAttachedView();
    this.dependencies.emitState();
    setTimeout(() => this.syncVideoFullscreenBounds(), 80);
  }

  enterExternalWindow() {
    const display = this.dependencies.mainWindow() && !this.dependencies.mainWindow().isDestroyed()
      ? this.dependencies.screen.getDisplayMatching(this.dependencies.mainWindow().getBounds())
      : this.dependencies.screen.getPrimaryDisplay();
    const displayBounds = display && display.bounds ? display.bounds : {};
    const videoWindow = new this.dependencies.BrowserWindow({
      x: Number(displayBounds.x) || 0,
      y: Number(displayBounds.y) || 0,
      width: Math.max(640, Number(displayBounds.width) || 1280),
      height: Math.max(360, Number(displayBounds.height) || 720),
      title: this.dependencies.windowTitle(),
      show: false,
      frame: false,
      fullscreenable: true,
      backgroundColor: '#000000',
      autoHideMenuBar: true,
      webPreferences: {
        contextIsolation: true,
        nodeIntegration: false,
        sandbox: true,
      },
    });
    this.videoFullscreenWindow = videoWindow;
    this._videoFullscreenWindowClosing = false;
    this._fullscreenResizeHandler = () => this.syncVideoFullscreenBounds();
    videoWindow.on('resize', this._fullscreenResizeHandler);
    videoWindow.on('enter-full-screen', this._fullscreenResizeHandler);
    videoWindow.on('leave-full-screen', () => {
      if (!this._videoFullscreenWindowClosing && this.videoFullscreen.active) {
        this.requestVideoFullscreenExit();
      }
    });
    videoWindow.on('close', (event) => {
      if (this._videoFullscreenWindowClosing || this.dependencies.isQuitting()) return;
      event.preventDefault();
      this.requestVideoFullscreenExit();
    });
    videoWindow.on('closed', () => {
      if (this.videoFullscreenWindow === videoWindow) this.videoFullscreenWindow = null;
    });
    videoWindow.setMenuBarVisibility(false);
    videoWindow.show();
    videoWindow.setFullScreen(true);
    if (this.dependencies.mainWindow() && !this.dependencies.mainWindow().isDestroyed() && !this._mainWindowWasFullScreen && this.dependencies.mainWindow().isFullScreen()) {
      this.dependencies.mainWindow().setFullScreen(false);
    }
  }

  enterHostWindow() {
    this._fullscreenResizeHandler = () => this.syncVideoFullscreenBounds();
    this._mainFullscreenLeaveHandler = () => {
      this.syncVideoFullscreenBounds();
      if (this.videoFullscreen.active && !this.videoFullscreen.external) {
        this.requestVideoFullscreenExit();
      }
    };
    this.dependencies.mainWindow().on('resize', this._fullscreenResizeHandler);
    this.dependencies.mainWindow().on('enter-full-screen', this._fullscreenResizeHandler);
    this.dependencies.mainWindow().on('leave-full-screen', this._mainFullscreenLeaveHandler);
    if (!this.dependencies.mainWindow().isFullScreen()) this.dependencies.mainWindow().setFullScreen(true);
  }

  finishVideoFullscreen(view) {
    if (!this.videoFullscreen.active) return;
    const tab = this.fullscreenTab();
    if (view && tab && tab.view !== view) return;
    if (this._videoFullscreenExitTimer) clearTimeout(this._videoFullscreenExitTimer);
    this._videoFullscreenExitTimer = null;
    const externalWindow = this.videoFullscreenWindow;
    const wasExternal = this.videoFullscreen.external;
    this.videoFullscreen = { active: false, external: false, tabId: '' };
    if (activeVideoFullscreenManager === this) activeVideoFullscreenManager = null;

    this.removeWindowListeners(externalWindow);

    if (wasExternal && externalWindow && !externalWindow.isDestroyed()) {
      this._videoFullscreenWindowClosing = true;
      try { externalWindow.contentView.removeChildView(tab && tab.view); } catch (_) {}
      externalWindow.destroy();
      this.videoFullscreenWindow = null;
      this._videoFullscreenWindowClosing = false;
    } else if (!wasExternal && this.dependencies.mainWindow() && !this.dependencies.mainWindow().isDestroyed() && !this._mainWindowWasFullScreen) {
      this.dependencies.mainWindow().setFullScreen(false);
    }

    this.dependencies.resetAttachment();
    this.dependencies.syncAttachedView();
    this.dependencies.emitState();
  }

  removeWindowListeners(externalWindow) {
    if (this._fullscreenResizeHandler) {
      if (externalWindow && !externalWindow.isDestroyed()) {
        externalWindow.removeListener('resize', this._fullscreenResizeHandler);
        externalWindow.removeListener('enter-full-screen', this._fullscreenResizeHandler);
      }
      if (this.dependencies.mainWindow() && !this.dependencies.mainWindow().isDestroyed()) {
        this.dependencies.mainWindow().removeListener('resize', this._fullscreenResizeHandler);
        this.dependencies.mainWindow().removeListener('enter-full-screen', this._fullscreenResizeHandler);
      }
      this._fullscreenResizeHandler = null;
    }
    if (this._mainFullscreenLeaveHandler) {
      if (this.dependencies.mainWindow() && !this.dependencies.mainWindow().isDestroyed()) {
        this.dependencies.mainWindow().removeListener('leave-full-screen', this._mainFullscreenLeaveHandler);
      }
      this._mainFullscreenLeaveHandler = null;
    }

  }

}

module.exports = { BrowserVideoFullscreen };
