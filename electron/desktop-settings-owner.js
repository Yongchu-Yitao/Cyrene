'use strict';
const path = require('path');
const fs = require('fs');

class DesktopSettings {
  constructor({ app, globalShortcut, supportsLoginItem, DEFAULT_DESKTOP_SETTINGS, normalizeDesktopLanguage, getDesktopLanguage, syncTrayWithSettings, rebuildApplicationMenu, broadcastDesktopLanguage, destroyQuickChatWindow, openQuickChat, appendErrorLog }) {
    this.registeredQuickChatShortcut = '';
    this.quickChatShortcutError = '';
    this.app = app;
    this.globalShortcut = globalShortcut;
    this.supportsLoginItem = supportsLoginItem;
    this.DEFAULT_DESKTOP_SETTINGS = DEFAULT_DESKTOP_SETTINGS;
    this.normalizeDesktopLanguage = normalizeDesktopLanguage;
    this.getDesktopLanguage = getDesktopLanguage;
    this.syncTrayWithSettings = syncTrayWithSettings;
    this.rebuildApplicationMenu = rebuildApplicationMenu;
    this.broadcastDesktopLanguage = broadcastDesktopLanguage;
    this.destroyQuickChatWindow = destroyQuickChatWindow;
    this.openQuickChat = openQuickChat;
    this.appendErrorLog = appendErrorLog;
  }

  getDesktopSettingsPath() {
    return path.join(this.app.getPath('userData'), 'desktop_settings.json');
  }

  readDesktopSettings() {
    try {
      const raw = fs.readFileSync(this.getDesktopSettingsPath(), 'utf8');
      const parsed = JSON.parse(raw);
      const runInBackground = parsed.runInBackground === true;
      return {
        settingsRevision: Number.isInteger(parsed.settingsRevision) && parsed.settingsRevision >= 0 ? parsed.settingsRevision : 0,
        launchAtLogin: parsed.launchAtLogin === true,
        runInBackground,
        language: this.normalizeDesktopLanguage(parsed.language),
        // Quick chat can't be on without background residency.
        quickChatEnabled: runInBackground && parsed.quickChatEnabled === true,
        quickChatShortcut: this.normalizeQuickChatShortcut(parsed.quickChatShortcut),
      };
    } catch (_) {
      return { ...this.DEFAULT_DESKTOP_SETTINGS };
    }
  }

  writeDesktopSettings(settings) {
    const runInBackground = settings.runInBackground === true;
    const payload = {
      settingsRevision: Number.isInteger(settings.settingsRevision) && settings.settingsRevision >= 0 ? settings.settingsRevision : 0,
      launchAtLogin: settings.launchAtLogin === true,
      runInBackground,
      language: this.normalizeDesktopLanguage(settings.language),
      quickChatEnabled: runInBackground && settings.quickChatEnabled === true,
      quickChatShortcut: this.normalizeQuickChatShortcut(settings.quickChatShortcut),
    };
    fs.mkdirSync(path.dirname(this.getDesktopSettingsPath()), { recursive: true });
    fs.writeFileSync(this.getDesktopSettingsPath(), JSON.stringify(payload, null, 2), 'utf8');
  }

  applyLaunchAtLogin(enabled) {
    if (!this.supportsLoginItem) return false;
    this.app.setLoginItemSettings({
      openAtLogin: enabled === true,
      openAsHidden: enabled === true,
      args: enabled === true ? ['--hidden'] : [],
    });
    return true;
  }

  getDesktopSettings() {
    const stored = this.readDesktopSettings();
    return {
      ...stored,
      supportsLaunchAtLogin: this.supportsLoginItem,
      platform: process.platform,
      quickChatShortcutRegistered: (
        this.registeredQuickChatShortcut === stored.quickChatShortcut
        && this.globalShortcut.isRegistered(stored.quickChatShortcut)
      ),
      quickChatShortcutError: this.quickChatShortcutError,
      language: this.normalizeDesktopLanguage(stored.language),
    };
  }

  // The app must keep running — and the Python backend must stay alive — after the
  // last window is closed whenever a global quick-chat shortcut is registered
  // (otherwise pressing it would open a window pointing at a dead backend) or the
  // user opted into background mode. Quitting still tears Python down in
  // before-quit; a hidden main window is restored via 'activate' (macOS) or by
  // relaunching the app (single-instance → second-instance).
  appStaysResident() {
    if (this.registeredQuickChatShortcut) return true;
    try {
      return this.readDesktopSettings().runInBackground === true;
    } catch (_) {
      return false;
    }
  }

  saveDesktopSettings(updates, expectedRevision) {
    const current = this.readDesktopSettings();
    const allowed = new Set(['launchAtLogin', 'runInBackground', 'language', 'quickChatEnabled', 'quickChatShortcut']);
    const input = updates && typeof updates === 'object' && !Array.isArray(updates) ? updates : {};
    const unknown = Object.keys(input).filter((key) => !allowed.has(key));
    if (unknown.length) {
      const error = new Error(`unknown desktop setting(s): ${unknown.join(', ')}`);
      error.code = 'validation_error';
      throw error;
    }
    const expected = expectedRevision == null ? null : Number(expectedRevision);
    if (expected !== null && (!Number.isInteger(expected) || expected < 0)) {
      const error = new Error('expected desktop settings revision must be a non-negative integer');
      error.code = 'validation_error';
      throw error;
    }
    if (expected !== null && expected !== current.settingsRevision) {
      const error = new Error(`desktop settings revision conflict: expected ${expected}, actual ${current.settingsRevision}`);
      error.code = 'revision_conflict';
      error.actualRevision = current.settingsRevision;
      throw error;
    }
    for (const key of ['launchAtLogin', 'runInBackground', 'quickChatEnabled']) {
      if (Object.prototype.hasOwnProperty.call(input, key) && typeof input[key] !== 'boolean') {
        const error = new Error(`${key} must be a boolean`);
        error.code = 'validation_error';
        throw error;
      }
    }
    for (const key of ['language', 'quickChatShortcut']) {
      if (Object.prototype.hasOwnProperty.call(input, key) && typeof input[key] !== 'string') {
        const error = new Error(`${key} must be a string`);
        error.code = 'validation_error';
        throw error;
      }
    }
    const next = {
      ...current,
      ...input,
      settingsRevision: current.settingsRevision + 1,
    };
    next.quickChatShortcut = this.normalizeQuickChatShortcut(next.quickChatShortcut);
    next.language = this.normalizeDesktopLanguage(next.language);
    // Quick chat depends on background residency — turning residency off also
    // disables it (the UI gates the toggle, but enforce it here too).
    next.quickChatEnabled = next.runInBackground === true && next.quickChatEnabled === true;

    // Persist settings before attempting the shortcut side-effect, so a
    // registration failure doesn't discard a language or other setting change.
    this.writeDesktopSettings(next);
    this.applyLaunchAtLogin(next.launchAtLogin);
    this.syncTrayWithSettings(next);
    if (this.getDesktopLanguage(current) !== this.getDesktopLanguage(next)) {
      this.rebuildApplicationMenu(next);
      this.broadcastDesktopLanguage(next);
    }

    let shortcutUpdateOk = true;
    if (next.quickChatEnabled) {
      // Register (or re-register) the global shortcut. Only attempt it when the
      // binding is missing or changed so an unrelated toggle doesn't churn it.
      if (
        next.quickChatShortcut !== this.registeredQuickChatShortcut
        || !this.globalShortcut.isRegistered(next.quickChatShortcut)
      ) {
        shortcutUpdateOk = this.registerQuickChatShortcut(next.quickChatShortcut);
      }
    } else {
      // Disabled (or residency off) — release the shortcut and tear down the
      // transient window so nothing keeps the app resident for it.
      this.unregisterQuickChatShortcut();
      this.destroyQuickChatWindow();
    }

    return {
      ...this.getDesktopSettings(),
      shortcutUpdateOk,
    };
  }

  resetDesktopSettings() {
    const current = this.readDesktopSettings();
    const next = {
      ...this.DEFAULT_DESKTOP_SETTINGS,
      settingsRevision: current.settingsRevision + 1,
    };
    this.writeDesktopSettings(next);
    this.applyLaunchAtLogin(false);
    this.unregisterQuickChatShortcut();
    this.destroyQuickChatWindow();
    this.syncTrayWithSettings(next);
    this.rebuildApplicationMenu(next);
    if (this.getDesktopLanguage(current) !== this.getDesktopLanguage(next)) {
      this.broadcastDesktopLanguage(next);
    }
    return next;
  }

  unregisterQuickChatShortcut() {
    if (this.registeredQuickChatShortcut) {
      try { this.globalShortcut.unregister(this.registeredQuickChatShortcut); } catch (_) {}
    }
    this.registeredQuickChatShortcut = '';
    this.quickChatShortcutError = '';
  }

  normalizeQuickChatShortcut(value) {
    const shortcut = String(value || '').trim();
    return shortcut || this.DEFAULT_DESKTOP_SETTINGS.quickChatShortcut;
  }

  registerQuickChatShortcut(accelerator) {
    const requested = this.normalizeQuickChatShortcut(accelerator);
    const previous = this.registeredQuickChatShortcut;

    if (previous === requested && this.globalShortcut.isRegistered(requested)) {
      this.quickChatShortcutError = '';
      return true;
    }

    if (previous) {
      try { this.globalShortcut.unregister(previous); } catch (_) {}
      this.registeredQuickChatShortcut = '';
    }

    let registered = false;
    try {
      registered = this.globalShortcut.register(requested, () => {
        this.openQuickChat().catch((err) => {
          console.error('[electron] Failed to open quick chat:', err);
          this.appendErrorLog(`[electron] Failed to open quick chat: ${err && err.stack ? err.stack : err}\n`);
        });
      });
    } catch (err) {
      this.quickChatShortcutError = String((err && err.message) || err || 'shortcut_registration_failed');
    }

    if (registered) {
      this.registeredQuickChatShortcut = requested;
      this.quickChatShortcutError = '';
      return true;
    }

    this.quickChatShortcutError = this.quickChatShortcutError || 'shortcut_in_use';
    if (previous) {
      try {
        if (this.globalShortcut.register(previous, () => {
          this.openQuickChat().catch((err) => {
            console.error('[electron] Failed to open quick chat:', err);
          });
        })) {
          this.registeredQuickChatShortcut = previous;
        }
      } catch (_) {}
    }
    return false;
  }

  // ---------------------------------------------------------------------------
  // Python child process management
  // ---------------------------------------------------------------------------
}

module.exports = { DesktopSettings };
