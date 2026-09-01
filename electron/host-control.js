'use strict';

const crypto = require('crypto');

const WINDOW_ACTIONS = new Set([
  'reveal', 'focus', 'hide', 'minimize', 'maximize', 'restore',
  'enter_fullscreen', 'exit_fullscreen', 'set_frame', 'status',
  'quick_chat_open', 'quick_chat_close', 'quick_chat_status',
]);

class HostControl {
  constructor(options) {
    this.getMainWindow = options.getMainWindow;
    this.getQuickChatWindow = options.getQuickChatWindow || (() => null);
    this.screen = options.screen;
    this.app = options.app;
    this.revealMainWindow = options.revealMainWindow;
    this.openQuickChat = options.openQuickChat;
    this.getDesktopSettings = options.getDesktopSettings;
    this.updateDesktopSettings = options.updateDesktopSettings;
    this.showNotification = options.showNotification || null;
    this.lifecycleExecutor = options.lifecycleExecutor || null;
    this.surfaces = new Map();
    this.pending = new Map();
    this.preparedLifecycle = new Map();
  }

  _removeSurface(uiInstanceId, webContents, error = 'surface_disposed') {
    const id = String(uiInstanceId || '').trim();
    const current = this.surfaces.get(id);
    if (!id || !current || current.webContents !== webContents) return false;
    this.surfaces.delete(id);
    for (const [requestId, pending] of this.pending.entries()) {
      if (pending.surfaceId !== id || pending.webContents !== webContents) continue;
      this.pending.delete(requestId);
      clearTimeout(pending.timer);
      pending.resolve({ ok: false, error });
    }
    return true;
  }

  registerSurface(uiInstanceId, webContents) {
    const id = String(uiInstanceId || '').trim();
    const mainWindow = this.getMainWindow();
    const quickWindow = this.getQuickChatWindow();
    const owner = mainWindow && !mainWindow.isDestroyed() && mainWindow.webContents === webContents
      ? { window: mainWindow, kind: 'main' }
      : quickWindow && !quickWindow.isDestroyed() && quickWindow.webContents === webContents
        ? { window: quickWindow, kind: 'quick_chat' }
        : null;
    if (!id || id.length > 160 || !owner) {
      return { ok: false, error: 'invalid_surface' };
    }
    for (const [existingId, existing] of this.surfaces.entries()) {
      if (existingId !== id && existing.webContents === webContents) {
        this._removeSurface(existingId, webContents, 'surface_replaced');
      }
    }
    const replaced = this.surfaces.get(id);
    if (replaced && replaced.webContents !== webContents) {
      this._removeSurface(id, replaced.webContents, 'surface_replaced');
    }
    this.surfaces.set(id, { webContents, kind: owner.kind });
    webContents.once('destroyed', () => {
      this._removeSurface(id, webContents);
    });
    return { ok: true, uiInstanceId: id, surfaceKind: owner.kind };
  }

  unregisterSurface(uiInstanceId, webContents) {
    const id = String(uiInstanceId || '').trim();
    const current = this.surfaces.get(id);
    if (!id || !current || current.webContents !== webContents) {
      return { ok: false, error: 'invalid_surface' };
    }
    this._removeSurface(id, webContents);
    return { ok: true, uiInstanceId: id };
  }

  resolveSurface(uiInstanceId) {
    const id = String(uiInstanceId || '').trim();
    const registered = this.surfaces.get(id);
    if (!id || !registered || registered.webContents.isDestroyed()) {
      return null;
    }
    const win = registered.kind === 'quick_chat'
      ? this.getQuickChatWindow()
      : this.getMainWindow();
    if (!win || win.isDestroyed() || win.webContents !== registered.webContents) return null;
    return { id, webContents: registered.webContents, window: win, kind: registered.kind };
  }

  receiveSurfaceResponse(payload, sender) {
    const requestId = String(payload && payload.requestId || '');
    const pending = this.pending.get(requestId);
    if (!pending || !sender || pending.webContents !== sender) return;
    this.pending.delete(requestId);
    clearTimeout(pending.timer);
    pending.resolve(payload && payload.result ? payload.result : { ok: false, error: 'invalid_renderer_response' });
  }

  requestSurface(uiInstanceId, method, args, timeoutMs = 5000) {
    const surface = this.resolveSurface(uiInstanceId);
    if (!surface) return Promise.resolve({ ok: false, error: 'no_current_surface' });
    const requestId = crypto.randomUUID();
    return new Promise((resolve) => {
      const timer = setTimeout(() => {
        this.pending.delete(requestId);
        // The renderer may have executed the action before its acknowledgement
        // was lost, so callers must not blindly retry a side effect.
        resolve({ ok: false, error: 'delivery_uncertain' });
      }, Math.max(250, Math.min(Number(timeoutMs) || 5000, 15000)));
      this.pending.set(requestId, {
        resolve,
        timer,
        surfaceId: surface.id,
        webContents: surface.webContents,
      });
      surface.webContents.send('ui-surface:request', {
        requestId,
        method: String(method || ''),
        args: args && typeof args === 'object' ? args : {},
      });
    });
  }

  async windowControl(uiInstanceId, action, args) {
    const normalized = String(action || '');
    if (!WINDOW_ACTIONS.has(normalized)) return { ok: false, error: 'unsupported_window_action' };
    const surface = this.resolveSurface(uiInstanceId);
    if (!surface) return { ok: false, error: 'no_current_surface' };
    const win = surface.window;
    if (normalized === 'status') return this.windowStatus(win);
    if (normalized === 'quick_chat_status') {
      const quick = this.getQuickChatWindow();
      return { ok: true, quickChat: { available: !!quick, visible: !!(quick && !quick.isDestroyed() && quick.isVisible()) } };
    }
    if (normalized === 'quick_chat_open') {
      await this.openQuickChat();
      const quick = this.getQuickChatWindow();
      return { ok: true, quickChat: { available: !!quick, visible: !!(quick && !quick.isDestroyed() && quick.isVisible()) } };
    }
    if (normalized === 'quick_chat_close') {
      const quick = this.getQuickChatWindow();
      if (quick && !quick.isDestroyed()) quick.hide();
      return { ok: true, quickChat: { available: !!quick, visible: false } };
    }
    if (normalized === 'reveal') {
      if (surface.kind === 'main') await this.revealMainWindow();
      else { win.show(); win.focus(); }
    }
    else if (normalized === 'focus') win.focus();
    else if (normalized === 'hide') win.hide();
    else if (normalized === 'minimize') win.minimize();
    else if (normalized === 'maximize') win.maximize();
    else if (normalized === 'restore') {
      if (win.isFullScreen()) win.setFullScreen(false);
      if (win.isMaximized()) win.unmaximize();
      if (win.isMinimized()) win.restore();
    } else if (normalized === 'enter_fullscreen') win.setFullScreen(true);
    else if (normalized === 'exit_fullscreen') win.setFullScreen(false);
    else if (normalized === 'set_frame') {
      try {
        this.setNormalizedFrame(win, args || {});
      } catch (error) {
        return { ok: false, error: 'invalid_window_frame', detail: String(error && error.message || error) };
      }
    }
    return { ok: true, window: this.windowStatus(win).window };
  }

  setNormalizedFrame(win, args) {
    const names = ['x_ratio', 'y_ratio', 'width_ratio', 'height_ratio'];
    const values = Object.fromEntries(names.map((name) => [name, Number(args[name])]));
    if (names.some((name) => !Number.isFinite(values[name]) || values[name] < 0 || values[name] > 1)) {
      throw new Error('normalized frame values must be between 0 and 1');
    }
    if (values.width_ratio < 0.2 || values.height_ratio < 0.2) {
      throw new Error('normalized frame is smaller than the allowed minimum');
    }
    const display = this.screen.getDisplayMatching(win.getBounds());
    const area = display.workArea;
    const width = Math.max(win.getMinimumSize()[0], Math.round(area.width * values.width_ratio));
    const height = Math.max(win.getMinimumSize()[1], Math.round(area.height * values.height_ratio));
    const x = Math.round(area.x + (area.width - width) * values.x_ratio);
    const y = Math.round(area.y + (area.height - height) * values.y_ratio);
    win.setBounds({ x, y, width, height });
  }

  windowStatus(win) {
    if (!win || win.isDestroyed()) return { ok: false, error: 'no_current_surface' };
    return {
      ok: true,
      window: {
        visible: win.isVisible(),
        focused: win.isFocused(),
        minimized: win.isMinimized(),
        maximized: win.isMaximized(),
        fullscreen: win.isFullScreen(),
      },
    };
  }

  async handle(method, args) {
    const input = args && typeof args === 'object' ? args : {};
    const uiInstanceId = String(input.uiInstanceId || '');
    switch (String(method || '')) {
      case 'host.status': {
        const surface = this.resolveSurface(uiInstanceId);
        const currentWindow = surface && surface.window;
        return {
          ok: true,
          hostKind: 'electron',
          appVersion: this.app.getVersion(),
          surfaceAvailable: !!surface,
          surfaceKind: surface ? surface.kind : '',
          window: this.windowStatus(currentWindow).window || null,
        };
      }
      case 'window.control':
        return this.windowControl(uiInstanceId, input.action, input);
      case 'ui.snapshot.current':
        return this.requestSurface(uiInstanceId, 'snapshot', input);
      case 'ui.gesture.execute_current':
        return this.requestSurface(uiInstanceId, 'act', input);
      case 'desktop.settings.get':
        return { ok: true, settings: this.getDesktopSettings() };
      case 'desktop.settings.update':
        try {
          const before = this.getDesktopSettings();
          const settings = this.updateDesktopSettings(input.changes || {}, input.expectedRevision);
          const diff = {};
          for (const key of Object.keys(input.changes || {})) {
            if (before[key] !== settings[key]) diff[key] = { before: before[key], after: settings[key] };
          }
          return {
            ok: true,
            settings,
            diff,
          };
        } catch (error) {
          return {
            ok: false,
            error: String(error && error.code || 'desktop_settings_error'),
            revision: Number.isInteger(error && error.actualRevision) ? error.actualRevision : null,
            detail: String(error && error.message || error),
          };
        }
      case 'notification.show':
        if (!this.showNotification) return { ok: false, error: 'notifications_unavailable' };
        return this.showNotification({
          title: String(input.title || '').slice(0, 160),
          body: String(input.body || '').slice(0, 4096),
        });
      case 'lifecycle.execute_approved':
        if (!this.lifecycleExecutor) return { ok: false, error: 'lifecycle_unavailable' };
        if (String(input.expectedAppVersion || '') !== String(this.app.getVersion())) {
          return { ok: false, error: 'app_version_drift' };
        }
        if (!/^[0-9a-f]{64}$/.test(String(input.parameterHash || ''))) {
          return { ok: false, error: 'invalid_parameter_hash' };
        }
        if (!/^host_action_[0-9a-f]{32}$/.test(String(input.actionId || ''))) {
          return { ok: false, error: 'invalid_action_receipt' };
        }
        if (String(input.action || '') === 'update_install') {
          const phase = String(input.phase || '');
          const actionId = String(input.actionId || '');
          if (phase === 'prepare') {
            const previous = this.preparedLifecycle.get(actionId);
            if (previous && previous.timer) clearTimeout(previous.timer);
            const timer = setTimeout(() => this.preparedLifecycle.delete(actionId), 60000);
            if (timer && typeof timer.unref === 'function') timer.unref();
            this.preparedLifecycle.set(actionId, {
              expectedAppVersion: String(input.expectedAppVersion || ''),
              parameterHash: String(input.parameterHash || ''),
              timer,
            });
            return { ok: true, summary: 'prepared update_install' };
          }
          if (phase === 'commit') {
            const prepared = this.preparedLifecycle.get(actionId);
            if (!prepared
                || prepared.expectedAppVersion !== String(input.expectedAppVersion || '')
                || prepared.parameterHash !== String(input.parameterHash || '')) {
              return { ok: false, error: 'update_install_not_prepared' };
            }
            if (prepared.timer) clearTimeout(prepared.timer);
            this.preparedLifecycle.delete(actionId);
            return this.lifecycleExecutor(input.actionId, input.action, {
              expectedAppVersion: input.expectedAppVersion,
              parameterHash: input.parameterHash,
            });
          }
          return { ok: false, error: 'invalid_lifecycle_phase' };
        }
        if (input.phase && String(input.phase) !== 'execute') {
          return { ok: false, error: 'invalid_lifecycle_phase' };
        }
        return this.lifecycleExecutor(input.actionId, input.action, {
          expectedAppVersion: input.expectedAppVersion,
          parameterHash: input.parameterHash,
        });
      default:
        return { ok: false, error: 'unknown_host_method' };
    }
  }
}

module.exports = { HostControl, WINDOW_ACTIONS };
