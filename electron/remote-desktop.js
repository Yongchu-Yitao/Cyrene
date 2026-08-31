const {
  BrowserWindow,
  clipboard,
  desktopCapturer,
  ipcMain,
  nativeImage,
  powerMonitor,
  screen,
  session,
  systemPreferences,
} = require('electron');
const crypto = require('crypto');
const fs = require('fs');
const path = require('path');
const { pathToFileURL } = require('url');
const { spawn } = require('child_process');

const ANSWER_TIMEOUT_MS = 75_000;
const CREDENTIAL_TIMEOUT_MS = 180_000;

function deferred() {
  let resolve;
  let reject;
  const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
}

function commandExists(name) {
  const directories = String(process.env.PATH || '').split(path.delimiter).filter(Boolean);
  const names = process.platform === 'win32' ? [name, `${name}.exe`] : [name];
  return directories.some((directory) => names.some((candidate) => {
    try { return fs.statSync(path.join(directory, candidate)).isFile(); } catch (_) { return false; }
  }));
}

function runCommand(binary, args, timeoutMs = 5000) {
  return new Promise((resolve, reject) => {
    const child = spawn(binary, args.map(String), {
      stdio: 'ignore',
      windowsHide: true,
      env: process.env,
    });
    let settled = false;
    const finish = (error) => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      if (error) reject(error); else resolve({ ok: true });
    };
    const timer = setTimeout(() => {
      try { child.kill('SIGKILL'); } catch (_) {}
      finish(new Error('desktop_input_timeout'));
    }, timeoutMs);
    child.once('error', finish);
    child.once('exit', (code) => finish(code === 0 ? null : new Error('desktop_input_failed')));
  });
}

function publicDisplay(display, primaryId) {
  const bounds = display.bounds || {};
  return {
    id: String(display.id),
    name: String(display.label || (String(display.id) === String(primaryId) ? 'Primary display' : `Display ${display.id}`)),
    width: Math.max(1, Number(bounds.width || display.size && display.size.width || 1)),
    height: Math.max(1, Number(bounds.height || display.size && display.size.height || 1)),
    scale: Math.max(0.1, Number(display.scaleFactor || 1)),
    rotation: Number(display.rotation || 0),
    primary: String(display.id) === String(primaryId),
    kind: 'physical',
  };
}

class RemoteDesktopManager {
  constructor({ getMainWindow, getAppUseManager, getLanguage = () => 'en', clipboardRoot = '' }) {
    this.getMainWindow = getMainWindow;
    this.getAppUseManager = getAppUseManager;
    this.getLanguage = getLanguage;
    this.sessions = new Map();
    this.inputSessions = new Map();
    this.pendingCredentials = new Map();
    this.credentialWindows = new Map();
    this.indicatorWindows = new Map();
    this.terminatedSessions = new Map();
    this.screenLocked = false;
    this.securitySurfaceState = false;
    this.securityEpoch = 0;
    this.clipboardRoot = path.resolve(String(clipboardRoot || path.join(process.cwd(), '.cyrene-clipboard')));
    this.ipcInstalled = false;
  }

  installIpc() {
    if (this.ipcInstalled) return;
    this.ipcInstalled = true;
    ipcMain.on('remote-desktop:answer', (event, payload) => this._acceptAnswer(event.sender, payload));
    ipcMain.on('remote-desktop:input', (event, payload) => this._acceptInput(event.sender, payload));
    ipcMain.on('remote-desktop:control', (event, payload) => this._acceptControl(event.sender, payload));
    ipcMain.on('remote-desktop:state', (event, payload) => this._acceptState(event.sender, payload));
    ipcMain.handle('remote-desktop:credential-context', (event) => this._credentialContext(event.sender));
    ipcMain.handle('remote-desktop:credential-submit', (event, values) => this._credentialSubmit(event.sender, values));
    ipcMain.handle('remote-desktop:credential-cancel', (event) => this._credentialCancel(event.sender));
    ipcMain.handle('remote-desktop:indicator-context', (event) => this._indicatorContext(event.sender));
    ipcMain.handle('remote-desktop:indicator-disconnect', (event) => this._indicatorDisconnect(event.sender));
    powerMonitor.on('lock-screen', () => this._setSecureSurface(true));
    powerMonitor.on('unlock-screen', () => this._setSecureSurface(false));
  }

  _setSecureSurface(value) {
    this.screenLocked = value === true;
    const security = this._securitySnapshot();
    for (const record of this.sessions.values()) {
      if (!record.window || record.window.isDestroyed()) continue;
      record.window.webContents.send('remote-desktop:command', {
        operation: 'security_state',
        secure_surface: security.secure_surface,
        security_epoch: security.security_epoch,
      });
    }
  }

  _secureSurface() {
    try {
      return this.screenLocked || powerMonitor.getSystemIdleState(1) === 'locked';
    } catch (_) {
      return this.screenLocked;
    }
  }

  _securitySnapshot() {
    const secureSurface = this._secureSurface();
    if (secureSurface !== this.securitySurfaceState) {
      this.securitySurfaceState = secureSurface;
      this.securityEpoch += 1;
    }
    return {
      secure_surface: secureSurface,
      security_epoch: this.securityEpoch,
    };
  }

  _displays() {
    const primary = screen.getPrimaryDisplay();
    return screen.getAllDisplays().map((display) => publicDisplay(display, primary && primary.id));
  }

  _display(displayId) {
    const requested = String(displayId || '');
    const displays = screen.getAllDisplays();
    return displays.find((item) => String(item.id) === requested) || screen.getPrimaryDisplay() || displays[0] || null;
  }

  _systemAudioAvailable() {
    if (process.platform === 'win32' || process.platform === 'darwin') return true;
    if (process.platform !== 'linux') return false;
    return commandExists('pw-cli') || commandExists('pactl');
  }

  async probe() {
    let screenPermission = 'granted';
    let microphonePermission = 'granted';
    let accessibilityPermission = 'granted';
    const displayServer = process.platform === 'linux'
      ? String(process.env.XDG_SESSION_TYPE || 'x11').toLowerCase() : '';
    const microphoneSinkId = String(process.env.CYRENE_REMOTE_DESKTOP_MIC_SINK_ID || '').trim();
    if (process.platform === 'darwin') {
      try { screenPermission = systemPreferences.getMediaAccessStatus('screen'); } catch (_) { screenPermission = 'unknown'; }
      try { microphonePermission = systemPreferences.getMediaAccessStatus('microphone'); } catch (_) { microphonePermission = 'unknown'; }
      try { accessibilityPermission = systemPreferences.isTrustedAccessibilityClient(false) ? 'granted' : 'denied'; } catch (_) { accessibilityPermission = 'unknown'; }
    } else if (process.platform === 'linux') {
      // xdotool is an X11 tool. Reporting it as a Wayland input bridge made a
      // view-only Wayland session look controllable even though native clients
      // reject its synthetic events.
      accessibilityPermission = displayServer === 'wayland'
        ? 'denied' : commandExists('xdotool') ? 'granted' : 'denied';
    }
    const security = this._securitySnapshot();
    return {
      ok: true,
      version: '1',
      platform: process.platform,
      displays: this._displays(),
      permissions: {
        screen: screenPermission,
        microphone: microphonePermission,
        accessibility: accessibilityPermission,
      },
      system_audio: this._systemAudioAvailable(),
      // Playing the upstream track through ordinary speakers is not microphone
      // injection. A configured virtual-audio output is required for the
      // current-desktop Provider to advertise this capability.
      microphone: Boolean(microphoneSinkId),
      microphone_injection: Boolean(microphoneSinkId),
      display_server: displayServer,
      audio_backend: process.platform === 'linux'
        ? (commandExists('pw-cli') ? 'PipeWire' : commandExists('pactl') ? 'PulseAudio' : '')
        : (process.platform === 'win32' ? 'WASAPI' : 'CoreAudio'),
      secure_surface: security.secure_surface,
      security_epoch: security.security_epoch,
    };
  }

  async _sourceForDisplay(displayId) {
    const display = this._display(displayId);
    if (!display) throw new Error('desktop_display_not_found');
    const sources = await desktopCapturer.getSources({
      types: ['screen'],
      thumbnailSize: { width: 0, height: 0 },
      fetchWindowIcons: false,
    });
    const source = sources.find((item) => String(item.display_id || '') === String(display.id))
      || sources.find((item) => String(item.id || '').endsWith(`:${display.id}:0`))
      || sources[0];
    if (!source) throw new Error('desktop_capture_source_unavailable');
    return { display, source };
  }

  async _createHost(record) {
    const partition = `cyrene-remote-desktop-${crypto.randomUUID()}`;
    const captureSession = session.fromPartition(partition, { cache: false });
    captureSession.setPermissionRequestHandler((_contents, permission, callback, details = {}) => {
      const mediaTypes = Array.isArray(details.mediaTypes) ? details.mediaTypes : [];
      callback(permission === 'media' && mediaTypes.every((kind) => kind === 'audio' || kind === 'video'));
    });
    captureSession.setDisplayMediaRequestHandler(async (_request, callback) => {
      try {
        const selected = await this._sourceForDisplay(record.displayId);
        const response = { video: selected.source };
        if (record.permissions.system_audio === true && this._systemAudioAvailable()) {
          response.audio = 'loopback';
        }
        callback(response);
      } catch (_) {
        callback({});
      }
    }, { useSystemPicker: false });
    const window = new BrowserWindow({
      width: 640,
      height: 480,
      show: false,
      frame: false,
      skipTaskbar: true,
      webPreferences: {
        preload: path.join(__dirname, 'remote-desktop-preload.js'),
        partition,
        contextIsolation: true,
        nodeIntegration: false,
        sandbox: true,
        backgroundThrottling: false,
      },
    });
    record.window = window;
    record.partition = partition;
    window.on('closed', () => {
      if (this.sessions.get(record.sessionId) === record) {
        this.sessions.delete(record.sessionId);
        this.terminatedSessions.set(record.sessionId, 'host_closed');
        this.hideIndicator({ session_id: record.sessionId });
      }
      this._releasePointer(record).catch(() => {});
      record.window = null;
      if (record.clipboardTimer) clearInterval(record.clipboardTimer);
      if (record.pendingAnswer) record.pendingAnswer.reject(new Error('desktop_host_closed'));
    });
    await window.loadFile(path.join(__dirname, 'remote-desktop-host.html'));
  }

  async negotiate(args) {
    const sessionId = String(args.session_id || '');
    if (!/^rdh_[0-9a-f]{32}$/.test(sessionId)) return { ok: false, code: 'desktop_session_invalid', error: 'Invalid desktop session.' };
    await this.disconnect({ session_id: sessionId });
    const record = {
      sessionId,
      displayId: String(args.display_id || ''),
      qualityMode: String(args.quality_mode || 'auto'),
      permissions: args.permissions && typeof args.permissions === 'object' ? { ...args.permissions } : {},
      microphoneSinkId: String(process.env.CYRENE_REMOTE_DESKTOP_MIC_SINK_ID || '').trim(),
      window: null,
      pendingAnswer: deferred(),
      clipboardRevision: 0,
      lastClipboardText: clipboard.readText(),
      clipboardTimer: null,
      clipboardImages: new Map(),
      lastClipboardImageHash: this._clipboardImage().hash,
      clipboardFiles: new Map(),
      lastClipboardFilesHash: this._clipboardFiles().hash,
      connectionState: 'new',
      connectionLossTimer: null,
      inputTargetId: '',
      inputTargetBounds: null,
      dragStart: null,
      activePointerSession: '',
      focusedInputSession: '',
      pointerPressed: false,
      lastPointerPoint: null,
      inputQueue: Promise.resolve(),
      pendingMove: null,
      moveQueued: false,
    };
    const selected = this._display(record.displayId);
    if (selected) record.displayId = String(selected.id);
    this.sessions.set(sessionId, record);
    try {
      await this._createHost(record);
      const pendingAnswer = record.pendingAnswer;
      record.window.webContents.send('remote-desktop:start', {
        session_id: sessionId,
        offer: args.offer,
        display_id: record.displayId,
        quality_mode: record.qualityMode,
        ice_servers: Array.isArray(args.ice_servers) ? args.ice_servers : [],
        permissions: record.permissions,
        microphone_sink_id: record.microphoneSinkId,
        secure_surface: this._secureSurface(),
      });
      const timeout = setTimeout(() => pendingAnswer.reject(new Error('desktop_negotiation_timeout')), ANSWER_TIMEOUT_MS);
      let answer;
      try { answer = await pendingAnswer.promise; } finally { clearTimeout(timeout); }
      if (!answer || answer.ok === false) {
        await this.disconnect({ session_id: sessionId });
        return answer || { ok: false, code: 'desktop_capture_failed', error: 'Desktop capture failed.' };
      }
      this._startClipboardMonitor(record);
      const security = this._securitySnapshot();
      return { ...answer, transport_kind: 'webrtc', ...security };
    } catch (error) {
      await this.disconnect({ session_id: sessionId });
      return { ok: false, code: String(error && error.message || 'desktop_capture_failed'), error: String(error && error.message || error) };
    }
  }

  _recordForSender(sender, sessionId) {
    const record = this.sessions.get(String(sessionId || ''));
    return record && record.window && !record.window.isDestroyed() && record.window.webContents === sender ? record : null;
  }

  _acceptAnswer(sender, payload) {
    const record = this._recordForSender(sender, payload && payload.session_id);
    if (!record || !record.pendingAnswer) return;
    record.pendingAnswer.resolve(payload && typeof payload === 'object' ? payload : { ok: false });
    record.pendingAnswer = null;
  }

  _acceptState(sender, payload) {
    const record = this._recordForSender(sender, payload && payload.session_id);
    if (!record) return;
    record.connectionState = String(payload && payload.connection_state || record.connectionState);
    if (record.connectionState === 'connected') {
      if (record.connectionLossTimer) clearTimeout(record.connectionLossTimer);
      record.connectionLossTimer = null;
      return;
    }
    if (record.connectionState !== 'failed' && record.connectionState !== 'disconnected') return;
    if (record.connectionLossTimer) clearTimeout(record.connectionLossTimer);
    record.connectionLossTimer = setTimeout(() => {
      if (this.sessions.get(record.sessionId) !== record) return;
      if (record.connectionState !== 'failed' && record.connectionState !== 'disconnected') return;
      this.terminatedSessions.set(record.sessionId, 'transport_lost');
      this.hideIndicator({ session_id: record.sessionId });
      this.disconnect({ session_id: record.sessionId }).catch(() => {});
    }, record.connectionState === 'failed' ? 0 : 5000);
  }

  _acceptInput(sender, payload) {
    const record = this._recordForSender(sender, payload && payload.session_id);
    if (!record || record.permissions.input !== true) return;
    this._queueInput(record, payload.event || {});
  }

  _queueInput(record, event) {
    if (String(event && event.type || '') === 'pointer' && String(event && event.action || '') === 'move') {
      record.pendingMove = event;
      if (record.moveQueued) return;
      record.moveQueued = true;
      record.inputQueue = Promise.resolve(record.inputQueue).catch(() => {}).then(async () => {
        const latest = record.pendingMove;
        record.pendingMove = null;
        record.moveQueued = false;
        if (latest) await this._performInput(record, latest);
        if (record.pendingMove) this._queueInput(record, record.pendingMove);
      });
      record.inputQueue.catch(() => {});
      return;
    }
    record.inputQueue = Promise.resolve(record.inputQueue)
      .catch(() => {})
      .then(() => this._performInput(record, event || {}));
    record.inputQueue.catch(() => {});
  }

  _acceptControl(sender, payload) {
    const record = this._recordForSender(sender, payload && payload.session_id);
    if (!record) return;
    const message = payload && payload.message && typeof payload.message === 'object' ? payload.message : {};
    if (message.type === 'input' && record.permissions.input === true) {
      this._queueInput(record, message.event || {});
      return;
    }
    if (message.type === 'clipboard:text' && record.permissions.clipboard_text === true) {
      const text = String(message.text || '').slice(0, 1024 * 1024);
      record.lastClipboardText = text;
      record.clipboardRevision += 1;
      clipboard.writeText(text);
    }
  }

  _startClipboardMonitor(record) {
    if ((
      record.permissions.clipboard_text !== true
      && record.permissions.clipboard_image !== true
      && record.permissions.clipboard_file !== true
    ) || record.clipboardTimer) return;
    record.clipboardTimer = setInterval(() => {
      if (!record.window || record.window.isDestroyed()) return;
      try {
        // File clipboards commonly expose URI text, while image clipboards can
        // expose fallback text. Prefer the richest representation so one copy
        // operation produces only one remote clipboard update.
        const files = record.permissions.clipboard_file === true
          ? this._clipboardFiles() : { paths: [], hash: '' };
        const image = !files.paths.length && record.permissions.clipboard_image === true
          ? this._clipboardImage() : { data: Buffer.alloc(0), hash: '', width: 0, height: 0 };
        if (record.permissions.clipboard_text === true && !files.paths.length && !image.hash) {
          const text = clipboard.readText();
          if (text !== record.lastClipboardText) {
            record.lastClipboardText = text;
            record.clipboardRevision += 1;
            record.window.webContents.send('remote-desktop:clipboard', {
              text: text.slice(0, 1024 * 1024),
              revision: record.clipboardRevision,
            });
          }
        }
        if (record.permissions.clipboard_image === true && !files.paths.length) {
          if (image.hash && image.hash !== record.lastClipboardImageHash) {
            record.lastClipboardImageHash = image.hash;
            const offerId = `clipboard_image_${crypto.randomUUID().replace(/-/g, '')}`;
            record.clipboardImages.set(offerId, { ...image, createdAt: Date.now() });
            for (const [id, offer] of record.clipboardImages) {
              if (Date.now() - offer.createdAt > 5 * 60_000) record.clipboardImages.delete(id);
            }
            record.window.webContents.send('remote-desktop:clipboard-image-offer', {
              offer_id: offerId,
              sha256: image.hash,
              size: image.data.length,
              width: image.width,
              height: image.height,
            });
          }
        }
        if (record.permissions.clipboard_file === true) {
          if (files.hash && files.hash !== record.lastClipboardFilesHash) {
            record.lastClipboardFilesHash = files.hash;
            const offerId = `clipboard_files_${crypto.randomUUID().replace(/-/g, '')}`;
            record.clipboardFiles.set(offerId, { ...files, createdAt: Date.now() });
            for (const [id, offer] of record.clipboardFiles) {
              if (Date.now() - offer.createdAt > 5 * 60_000) record.clipboardFiles.delete(id);
            }
            record.window.webContents.send('remote-desktop:clipboard-file-offer', {
              offer_id: offerId,
              entries: files.paths.map((filePath) => {
                let info;
                try { info = fs.statSync(filePath); } catch (_) { return null; }
                return {
                  name: path.basename(filePath),
                  kind: info.isDirectory() ? 'directory' : 'file',
                  size: info.isFile() ? Number(info.size || 0) : 0,
                };
              }).filter(Boolean),
            });
          }
        }
      } catch (_) {
        // Clipboard contents can disappear while another app replaces them.
        // The next bounded interval performs a fresh scan.
      }
    }, 650);
    if (record.clipboardTimer.unref) record.clipboardTimer.unref();
  }

  _screenPoint(record, event) {
    const display = this._display(record.displayId);
    if (!display) throw new Error('desktop_display_not_found');
    const bounds = display.bounds;
    const nx = Math.max(0, Math.min(1, Number(event.x_normalized)));
    const ny = Math.max(0, Math.min(1, Number(event.y_normalized)));
    return {
      x: Math.round(Number(bounds.x || 0) + nx * Math.max(1, Number(bounds.width || 1) - 1)),
      y: Math.round(Number(bounds.y || 0) + ny * Math.max(1, Number(bounds.height || 1) - 1)),
    };
  }

  async _linuxInput(record, event) {
    if (String(process.env.XDG_SESSION_TYPE || 'x11').toLowerCase() === 'wayland') {
      throw new Error('desktop_wayland_input_bridge_unavailable');
    }
    if (!commandExists('xdotool')) throw new Error('desktop_linux_input_component_missing');
    const type = String(event.type || '');
    if (type === 'pointer') {
      const point = this._screenPoint(record, event);
      const action = String(event.action || 'move');
      if (action === 'move') return runCommand('xdotool', ['mousemove', '--sync', point.x, point.y]);
      if (action === 'button_down') {
        record.dragStart = point;
        return runCommand('xdotool', ['mousemove', '--sync', point.x, point.y, 'mousedown', '1']);
      }
      if (action === 'button_up') {
        record.dragStart = null;
        return runCommand('xdotool', ['mousemove', '--sync', point.x, point.y, 'mouseup', '1']);
      }
      if (action === 'scroll') {
        const button = Number(event.delta_y || 0) > 0 ? 5 : 4;
        return runCommand('xdotool', ['mousemove', point.x, point.y, 'click', button]);
      }
      const button = action === 'right_click' ? 3 : 1;
      const count = action === 'double_click' ? 2 : 1;
      return runCommand('xdotool', ['mousemove', point.x, point.y, 'click', '--repeat', count, button]);
    }
    if (type === 'text') return runCommand('xdotool', ['type', '--clearmodifiers', '--delay', '1', String(event.text || '').slice(0, 65536)], 15_000);
    if (type === 'key') {
      const rawKey = String(event.key || '').slice(0, 64);
      const key = ({
        ArrowUp: 'Up', ArrowDown: 'Down', ArrowLeft: 'Left', ArrowRight: 'Right',
        Enter: 'Return', PageUp: 'Prior', PageDown: 'Next', ' ': 'space',
      })[rawKey] || rawKey;
      const modifiers = Array.isArray(event.modifiers)
        ? event.modifiers.map((item) => String(item) === 'meta' ? 'super' : String(item)) : [];
      return runCommand('xdotool', ['key', '--clearmodifiers', modifiers.concat([key]).join('+')]);
    }
    return { ok: true };
  }

  async _appUseSessionForPoint(record, point, forceRefresh = false) {
    const manager = this.getAppUseManager();
    const cachedBounds = record.inputTargetBounds;
    const cachedSession = this.inputSessions.get(record.inputTargetId) || '';
    if (!forceRefresh && point && cachedSession && cachedBounds
        && point.x >= Number(cachedBounds.x || 0)
        && point.y >= Number(cachedBounds.y || 0)
        && point.x < Number(cachedBounds.x || 0) + Number(cachedBounds.width || 0)
        && point.y < Number(cachedBounds.y || 0) + Number(cachedBounds.height || 0)) {
      return cachedSession;
    }
    const listed = await manager.handle('list_targets', {});
    const targets = Array.isArray(listed && listed.targets) ? listed.targets : [];
    const target = (point ? targets.filter((item) => {
      const bounds = item && item.bounds || {};
      return point.x >= Number(bounds.x || 0) && point.y >= Number(bounds.y || 0)
        && point.x < Number(bounds.x || 0) + Number(bounds.width || 0)
        && point.y < Number(bounds.y || 0) + Number(bounds.height || 0);
    }).sort((left, right) => Number(Boolean(right.foreground)) - Number(Boolean(left.foreground)))[0] : null)
      || targets.find((item) => item.foreground);
    if (!target) throw new Error('desktop_input_target_not_found');
    const targetId = String(target.target_id || '');
    let appSession = this.inputSessions.get(targetId);
    if (!appSession) {
      const connected = await manager.handle('connect', {
        target_id: targetId,
        parameters: { mode: 'visual', focus_policy: 'never' },
      });
      if (!connected || connected.status === 'error') throw new Error(String(connected && connected.type || 'desktop_input_connect_failed'));
      appSession = String(connected.session_id || '');
      this.inputSessions.set(targetId, appSession);
    }
    record.inputTargetId = targetId;
    record.inputTargetBounds = target.bounds && typeof target.bounds === 'object'
      ? { ...target.bounds } : null;
    return appSession;
  }

  async _performInput(record, event) {
    if (process.platform === 'linux') return this._linuxInput(record, event);
    const manager = this.getAppUseManager();
    const type = String(event.type || '');
    let point = null;
    if (type === 'pointer') point = this._screenPoint(record, event);
    const pointerAction = type === 'pointer' ? String(event.action || 'move') : '';
    let appSession = '';
    if (point && record.activePointerSession && ['move', 'button_up'].includes(pointerAction)) {
      appSession = record.activePointerSession;
    } else if (point) {
      appSession = await this._appUseSessionForPoint(
        record,
        point,
        ['button_down', 'right_click'].includes(pointerAction),
      );
    }
    else if (record.focusedInputSession) appSession = record.focusedInputSession;
    if (!appSession) {
      appSession = await this._appUseSessionForPoint(record, null);
    }
    let capability = '';
    const display = this._display(record.displayId);
    const bounds = display && display.bounds || { x: 0, y: 0, width: 1, height: 1 };
    let parameters = { desktop_bounds: bounds };
    if (type === 'pointer') {
      const action = String(event.action || 'move');
      capability = ['move', 'button_down', 'button_up'].includes(action) ? 'pointer_event'
        : action === 'right_click' ? 'right_click'
        : action === 'scroll' ? 'scroll_at' : '';
      parameters = { ...parameters, x: point.x, y: point.y, coordinate_space: 'screen' };
      if (capability === 'pointer_event') {
        parameters.action = action;
        parameters.button = 'left';
        parameters.pressed = action === 'button_up' ? false : record.pointerPressed || action === 'button_down';
      }
      if (capability === 'scroll_at') {
        parameters.direction = Number(event.delta_y || 0) >= 0 ? 'down' : 'up';
        parameters.amount = Math.max(1, Math.min(20, Math.round(Math.abs(Number(event.delta_y || 100)) / 100)));
      }
    } else if (type === 'text') {
      capability = 'key_sequence';
      parameters.focus_target = true;
      parameters.steps = [{ type: 'text', text: String(event.text || '').slice(0, 65536) }];
    } else if (type === 'key') {
      capability = 'key_sequence';
      const keys = (Array.isArray(event.modifiers) ? event.modifiers : []).concat([String(event.key || '')]).filter(Boolean);
      parameters.steps = [{ type: keys.length > 1 ? 'shortcut' : 'key', keys, key: keys.length === 1 ? keys[0] : undefined }];
      parameters.focus_target = true;
    }
    if (!capability) return { ok: true };
    try {
      const result = await manager.remoteDesktopInput(appSession, capability, parameters);
      if (pointerAction === 'button_down') {
        record.pointerPressed = true;
        record.activePointerSession = appSession;
        record.focusedInputSession = appSession;
      } else if (pointerAction === 'right_click') {
        record.focusedInputSession = appSession;
      }
      if (point) record.lastPointerPoint = point;
      return result;
    } catch (error) {
      this.inputSessions.delete(record.inputTargetId);
      record.inputTargetBounds = null;
      throw error;
    } finally {
      if (pointerAction === 'button_up') {
        record.pointerPressed = false;
        record.activePointerSession = '';
      }
    }
  }

  async _releasePointer(record) {
    if (!record) return;
    try {
      if (process.platform === 'linux' && commandExists('xdotool')) {
        await runCommand('xdotool', ['mouseup', '1'], 1500);
      } else if (record.pointerPressed && record.activePointerSession && record.lastPointerPoint) {
        const display = this._display(record.displayId);
        const bounds = display && display.bounds || { x: 0, y: 0, width: 1, height: 1 };
        await this.getAppUseManager().remoteDesktopInput(record.activePointerSession, 'pointer_event', {
          x: record.lastPointerPoint.x,
          y: record.lastPointerPoint.y,
          coordinate_space: 'screen',
          action: 'button_up',
          button: 'left',
          pressed: false,
          desktop_bounds: bounds,
        });
      }
    } finally {
      record.dragStart = null;
      record.pointerPressed = false;
      record.activePointerSession = '';
    }
  }

  async disconnect(args) {
    const sessionId = String(args && args.session_id || '');
    const record = this.sessions.get(sessionId);
    if (!record) return { ok: true, disconnected: false };
    this.sessions.delete(sessionId);
    if (record.clipboardTimer) clearInterval(record.clipboardTimer);
    if (record.connectionLossTimer) clearTimeout(record.connectionLossTimer);
    await this._releasePointer(record).catch(() => {});
    if (record.window && !record.window.isDestroyed()) {
      record.window.webContents.send('remote-desktop:command', { operation: 'disconnect' });
      record.window.destroy();
    }
    return { ok: true, disconnected: true };
  }

  displays() { return { ok: true, displays: this._displays() }; }

  async selectDisplay(args) {
    const record = this.sessions.get(String(args.session_id || ''));
    const display = this._display(args.display_id);
    if (!record || !display) return { ok: false, code: 'desktop_display_not_found' };
    if (record.permissions.display_select !== true) return { ok: false, code: 'desktop_capability_denied' };
    record.displayId = String(display.id);
    record.window.webContents.send('remote-desktop:command', { operation: 'select_display', display_id: record.displayId });
    return { ok: true, display: publicDisplay(display, screen.getPrimaryDisplay().id) };
  }

  setQuality(args) {
    const record = this.sessions.get(String(args.session_id || ''));
    if (!record) return { ok: false, code: 'desktop_session_not_found' };
    record.qualityMode = String(args.quality_mode || 'auto');
    record.window.webContents.send('remote-desktop:command', { operation: 'set_quality', quality_mode: record.qualityMode });
    return { ok: true, quality_mode: record.qualityMode };
  }

  setMicrophone(args) {
    const record = this.sessions.get(String(args.session_id || ''));
    if (!record) return { ok: false, code: 'desktop_session_not_found' };
    if (record.permissions.microphone !== true) return { ok: false, code: 'desktop_capability_denied' };
    if (!record.microphoneSinkId) {
      return { ok: false, code: 'desktop_microphone_injection_unavailable' };
    }
    record.window.webContents.send('remote-desktop:command', { operation: 'set_microphone', enabled: args.enabled === true });
    return { ok: true, enabled: args.enabled === true };
  }

  _clipboardImage() {
    try {
      const image = clipboard.readImage();
      if (!image || image.isEmpty()) return { data: Buffer.alloc(0), hash: '', width: 0, height: 0 };
      const data = image.toPNG();
      if (!data.length || data.length > 64 * 1024 * 1024) return { data: Buffer.alloc(0), hash: '', width: 0, height: 0 };
      const size = image.getSize();
      return {
        data,
        hash: crypto.createHash('sha256').update(data).digest('hex'),
        width: Number(size.width || 0),
        height: Number(size.height || 0),
      };
    } catch (_) {
      return { data: Buffer.alloc(0), hash: '', width: 0, height: 0 };
    }
  }

  _clipboardFiles() {
    try {
      const formats = new Set(clipboard.availableFormats().map(String));
      let raw = '';
      if (process.platform === 'win32' && formats.has('CF_HDROP')) {
        const buffer = clipboard.readBuffer('CF_HDROP');
        const offset = buffer.length >= 20 ? buffer.readUInt32LE(0) : 0;
        const wide = buffer.length >= 20 && buffer.readUInt32LE(16) !== 0;
        raw = offset > 0 && offset < buffer.length
          ? buffer.subarray(offset).toString(wide ? 'utf16le' : 'utf8').replace(/\0+$/g, '').replace(/\0/g, '\n') : '';
      } else if (process.platform === 'win32' && formats.has('FileNameW')) {
        raw = clipboard.readBuffer('FileNameW').toString('utf16le').replace(/\0+$/g, '');
      } else if (formats.has('x-special/gnome-copied-files')) {
        raw = clipboard.readBuffer('x-special/gnome-copied-files').toString('utf8').replace(/^copy\s*/i, '');
      } else if (formats.has('text/uri-list')) {
        raw = clipboard.readBuffer('text/uri-list').toString('utf8');
      } else if (formats.has('NSFilenamesPboardType')) {
        raw = clipboard.readBuffer('NSFilenamesPboardType').toString('utf8')
          .match(/<string>([\s\S]*?)<\/string>/g)?.map((value) => value.replace(/^<string>|<\/string>$/g, ''))
          .join('\n') || '';
      } else if (formats.has('public.file-url')) {
        raw = clipboard.readBuffer('public.file-url').toString('utf8');
      }
      const paths = raw.split(/\r?\n/).map((value) => value.trim()).filter((value) => value && !value.startsWith('#')).map((value) => {
        if (/^file:/i.test(value)) {
          try { return decodeURIComponent(new URL(value).pathname); } catch (_) { return ''; }
        }
        const decoded = value.replace(/&amp;/g, '&').replace(/&lt;/g, '<').replace(/&gt;/g, '>');
        return path.isAbsolute(decoded) ? decoded : '';
      }).filter((value, index, all) => value && all.indexOf(value) === index && fs.existsSync(value)).slice(0, 512);
      if (!paths.length) return { paths: [], hash: '' };
      const identity = paths.map((filePath) => {
        const info = fs.statSync(filePath);
        return `${filePath}\0${info.size}\0${info.mtimeMs}`;
      }).join('\n');
      return { paths, hash: crypto.createHash('sha256').update(identity).digest('hex') };
    } catch (_) {
      return { paths: [], hash: '' };
    }
  }

  _safeClipboardPath(value) {
    const candidate = path.resolve(String(value || ''));
    if (candidate !== this.clipboardRoot && !candidate.startsWith(this.clipboardRoot + path.sep)) {
      throw new Error('desktop_clipboard_path_denied');
    }
    return candidate;
  }

  exportClipboardImage(args) {
    const record = this.sessions.get(String(args.session_id || ''));
    const offer = record && record.clipboardImages.get(String(args.offer_id || ''));
    if (!record || !offer) return { ok: false, code: 'desktop_clipboard_offer_not_found' };
    let target;
    try { target = this._safeClipboardPath(args.path); } catch (_) { return { ok: false, code: 'desktop_clipboard_path_denied' }; }
    fs.mkdirSync(path.dirname(target), { recursive: true });
    fs.writeFileSync(target, offer.data, { mode: 0o600 });
    return { ok: true, path: target, size: offer.data.length, sha256: offer.hash, width: offer.width, height: offer.height };
  }

  ackClipboardImage(args) {
    const record = this.sessions.get(String(args.session_id || ''));
    if (!record) return { ok: false, code: 'desktop_session_not_found' };
    return { ok: true, removed: record.clipboardImages.delete(String(args.offer_id || '')) };
  }

  exportClipboardFiles(args) {
    const record = this.sessions.get(String(args.session_id || ''));
    const offer = record && record.clipboardFiles.get(String(args.offer_id || ''));
    if (!record || !offer) return { ok: false, code: 'desktop_clipboard_offer_not_found' };
    let target;
    try { target = this._safeClipboardPath(args.path); } catch (_) { return { ok: false, code: 'desktop_clipboard_path_denied' }; }
    fs.mkdirSync(target, { recursive: true });
    let count = 0;
    let total = 0;
    const copyEntry = (source, destination) => {
      const info = fs.lstatSync(source);
      if (info.isSymbolicLink()) throw new Error('symlink');
      if (info.isDirectory()) {
        fs.mkdirSync(destination, { recursive: true });
        for (const name of fs.readdirSync(source)) copyEntry(path.join(source, name), path.join(destination, name));
        return;
      }
      if (!info.isFile()) return;
      count += 1;
      total += Number(info.size || 0);
      if (count > 512 || total > 64 * 1024 * 1024) throw new Error('limit');
      fs.mkdirSync(path.dirname(destination), { recursive: true });
      fs.copyFileSync(source, destination);
      fs.chmodSync(destination, 0o600);
    };
    try {
      for (const source of offer.paths) copyEntry(source, path.join(target, path.basename(source)));
    } catch (_) {
      fs.rmSync(target, { recursive: true, force: true });
      return { ok: false, code: 'desktop_clipboard_file_invalid' };
    }
    return { ok: true, path: target, count, size: total };
  }

  ackClipboardFiles(args) {
    const record = this.sessions.get(String(args.session_id || ''));
    if (!record) return { ok: false, code: 'desktop_session_not_found' };
    return { ok: true, removed: record.clipboardFiles.delete(String(args.offer_id || '')) };
  }

  writeClipboardImage(args) {
    const record = this.sessions.get(String(args.session_id || ''));
    if (!record) return { ok: false, code: 'desktop_session_not_found' };
    if (record.permissions.clipboard_image !== true) return { ok: false, code: 'desktop_capability_denied' };
    let filePath;
    try { filePath = this._safeClipboardPath(args.path); } catch (_) { return { ok: false, code: 'desktop_clipboard_path_denied' }; }
    let data;
    try {
      const stat = fs.statSync(filePath);
      if (!stat.isFile() || stat.size < 8 || stat.size > 64 * 1024 * 1024) throw new Error('invalid_size');
      data = fs.readFileSync(filePath);
    } catch (_) {
      return { ok: false, code: 'desktop_clipboard_image_invalid' };
    }
    if (!data.subarray(0, 8).equals(Buffer.from([137, 80, 78, 71, 13, 10, 26, 10]))) {
      return { ok: false, code: 'desktop_clipboard_image_invalid' };
    }
    const image = nativeImage.createFromBuffer(data);
    if (image.isEmpty()) return { ok: false, code: 'desktop_clipboard_image_invalid' };
    record.lastClipboardImageHash = crypto.createHash('sha256').update(data).digest('hex');
    clipboard.writeImage(image);
    return { ok: true, width: image.getSize().width, height: image.getSize().height };
  }

  writeLocalClipboardImage(args) {
    let filePath;
    try { filePath = this._safeClipboardPath(args.path); } catch (_) { return { ok: false, code: 'desktop_clipboard_path_denied' }; }
    try {
      const data = fs.readFileSync(filePath);
      if (data.length < 8 || data.length > 64 * 1024 * 1024) throw new Error('invalid_size');
      const image = nativeImage.createFromBuffer(data);
      if (image.isEmpty()) throw new Error('invalid_image');
      clipboard.writeImage(image);
      return { ok: true, width: image.getSize().width, height: image.getSize().height };
    } catch (_) {
      return { ok: false, code: 'desktop_clipboard_image_invalid' };
    }
  }

  writeClipboardFiles(args) {
    const record = this.sessions.get(String(args.session_id || ''));
    if (!record) return { ok: false, code: 'desktop_session_not_found' };
    if (record.permissions.clipboard_file !== true) return { ok: false, code: 'desktop_capability_denied' };
    const values = Array.isArray(args.paths) ? args.paths.slice(0, 512) : [];
    const paths = [];
    try {
      for (const value of values) {
        const candidate = this._safeClipboardPath(value);
        if (!fs.existsSync(candidate)) throw new Error('missing');
        paths.push(candidate);
      }
    } catch (_) {
      return { ok: false, code: 'desktop_clipboard_file_invalid' };
    }
    if (!paths.length) return { ok: false, code: 'desktop_clipboard_file_invalid' };
    const uris = paths.map((item) => pathToFileURL(item).href);
    try {
      clipboard.clear();
      if (process.platform === 'win32') {
        const names = Buffer.from(`${paths.join('\0')}\0\0`, 'utf16le');
        const drop = Buffer.alloc(20 + names.length);
        drop.writeUInt32LE(20, 0);
        drop.writeUInt32LE(1, 16);
        names.copy(drop, 20);
        clipboard.writeBuffer('CF_HDROP', drop);
        clipboard.writeBuffer('FileNameW', Buffer.from(`${paths[0]}\0`, 'utf16le'));
        clipboard.writeBuffer('Preferred DropEffect', Buffer.from([1, 0, 0, 0]));
      } else if (process.platform === 'darwin') {
        const escaped = paths.map((item) => `<string>${item.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')}</string>`).join('');
        const plist = `<?xml version="1.0" encoding="UTF-8"?><plist version="1.0"><array>${escaped}</array></plist>`;
        clipboard.writeBuffer('NSFilenamesPboardType', Buffer.from(plist, 'utf8'));
        clipboard.writeBuffer('public.file-url', Buffer.from(uris[0], 'utf8'));
        clipboard.writeBuffer('text/uri-list', Buffer.from(uris.join('\r\n'), 'utf8'));
      } else {
        clipboard.writeBuffer('x-special/gnome-copied-files', Buffer.from(`copy\n${uris.join('\n')}`, 'utf8'));
        clipboard.writeBuffer('text/uri-list', Buffer.from(uris.join('\r\n'), 'utf8'));
      }
    } catch (_) {
      return { ok: false, code: 'desktop_clipboard_file_unsupported' };
    }
    record.lastClipboardFilesHash = this._clipboardFiles().hash;
    return { ok: true, count: paths.length };
  }

  writeLocalClipboardFiles(args) {
    const pseudoRecord = {
      sessionId: '__local__',
      permissions: { clipboard_file: true },
      lastClipboardFilesHash: '',
    };
    this.sessions.set('__local__', pseudoRecord);
    try { return this.writeClipboardFiles({ session_id: '__local__', paths: args.paths }); }
    finally { this.sessions.delete('__local__'); }
  }

  async showIndicator(args) {
    const sessionId = String(args.session_id || '');
    if (!/^rdh_[0-9a-f]{32}$/.test(sessionId)) {
      return { ok: false, code: 'desktop_session_invalid' };
    }
    this.hideIndicator({ session_id: sessionId });
    const indicator = new BrowserWindow({
      width: 380,
      height: 104,
      show: false,
      frame: false,
      resizable: false,
      minimizable: false,
      maximizable: false,
      closable: false,
      alwaysOnTop: true,
      skipTaskbar: true,
      webPreferences: {
        preload: path.join(__dirname, 'remote-desktop-indicator-preload.js'),
        contextIsolation: true,
        nodeIntegration: false,
        sandbox: true,
      },
    });
    const record = {
      sessionId,
      controllerName: String(args.controller_name || '').trim().slice(0, 160),
      mode: String(args.mode || 'current_desktop'),
      canControl: args.can_control === true,
      language: String(this.getLanguage() || 'en'),
      window: indicator,
    };
    this.indicatorWindows.set(sessionId, record);
    indicator.on('closed', () => {
      if (this.indicatorWindows.get(sessionId) === record) this.indicatorWindows.delete(sessionId);
    });
    indicator.once('ready-to-show', () => {
      const display = screen.getDisplayNearestPoint(screen.getCursorScreenPoint());
      const area = display && display.workArea || { x: 0, y: 0, width: 1280 };
      indicator.setPosition(
        Math.round(Number(area.x || 0) + Number(area.width || 1280) - 396),
        Math.round(Number(area.y || 0) + 16),
        false,
      );
      indicator.showInactive();
    });
    try {
      await indicator.loadFile(path.join(__dirname, 'remote-desktop-indicator.html'));
      return { ok: true };
    } catch (_) {
      this.hideIndicator({ session_id: sessionId });
      return { ok: false, code: 'desktop_target_indicator_unavailable' };
    }
  }

  hideIndicator(args) {
    const sessionId = String(args.session_id || '');
    const record = this.indicatorWindows.get(sessionId);
    if (!record) return { ok: true, hidden: false };
    this.indicatorWindows.delete(sessionId);
    if (record.window && !record.window.isDestroyed()) record.window.destroy();
    return { ok: true, hidden: true };
  }

  _indicatorRecord(sender) {
    for (const record of this.indicatorWindows.values()) {
      if (record.window && !record.window.isDestroyed() && record.window.webContents === sender) return record;
    }
    return null;
  }

  _indicatorContext(sender) {
    const record = this._indicatorRecord(sender);
    return record ? {
      session_id: record.sessionId,
      controller_name: record.controllerName,
      mode: record.mode,
      can_control: record.canControl,
      language: record.language,
    } : {};
  }

  async _indicatorDisconnect(sender) {
    const record = this._indicatorRecord(sender);
    if (!record) return { ok: false, code: 'desktop_session_not_found' };
    this.terminatedSessions.set(record.sessionId, 'emergency');
    this.hideIndicator({ session_id: record.sessionId });
    await this.disconnect({ session_id: record.sessionId });
    return { ok: true };
  }

  consumeForcedDisconnects() {
    const sessions = [...this.terminatedSessions].map(([sessionId, reason]) => ({
      session_id: sessionId,
      reason,
    }));
    this.terminatedSessions.clear();
    return { ok: true, sessions, session_ids: sessions.map((item) => item.session_id) };
  }

  securityState() {
    return { ok: true, ...this._securitySnapshot() };
  }

  _credentialRecord(sender) {
    for (const record of this.pendingCredentials.values()) {
      if (record.window && !record.window.isDestroyed() && record.window.webContents === sender) return record;
    }
    return null;
  }

  _credentialContext(sender) {
    const record = this._credentialRecord(sender);
    return record ? {
      session_id: record.sessionId,
      device_name: record.deviceName,
      language: record.language,
    } : {};
  }

  _settleCredential(record, result) {
    if (!record) return { ok: false, code: 'credential_dialog_not_found' };
    this.pendingCredentials.delete(record.sessionId);
    this.credentialWindows.delete(record.sessionId);
    if (record.timer) clearTimeout(record.timer);
    if (record.window && !record.window.isDestroyed()) record.window.destroy();
    record.request.resolve(result);
    return { ok: true };
  }

  _credentialSubmit(sender, values) {
    const record = this._credentialRecord(sender);
    const input = values && typeof values === 'object' ? values : {};
    const username = String(input.username || '').slice(0, 256);
    const password = String(input.password || '').slice(0, 1024);
    if (!record || !username || !password) return { ok: false, code: 'credential_invalid' };
    return this._settleCredential(record, {
      ok: true,
      username,
      domain: String(input.domain || '').slice(0, 256),
      password,
    });
  }

  _credentialCancel(sender) {
    return this._settleCredential(this._credentialRecord(sender), { ok: false, code: 'credential_cancelled' });
  }

  async requestCredentials(args) {
    const sessionId = String(args.session_id || '');
    if (!sessionId) return { ok: false, code: 'desktop_session_invalid' };
    const existing = this.pendingCredentials.get(sessionId);
    if (existing) return existing.request.promise;
    const owner = this.getMainWindow();
    const request = deferred();
    const window = new BrowserWindow({
      width: 440,
      height: 390,
      parent: owner && !owner.isDestroyed() ? owner : undefined,
      modal: Boolean(owner && !owner.isDestroyed()),
      show: false,
      resizable: false,
      minimizable: false,
      maximizable: false,
      title: 'Remote Desktop sign in',
      webPreferences: {
        preload: path.join(__dirname, 'remote-desktop-credential-preload.js'),
        contextIsolation: true,
        nodeIntegration: false,
        sandbox: true,
      },
    });
    const record = {
      sessionId,
      deviceName: String(args.device_name || '').slice(0, 160),
      request,
      window,
      timer: null,
      language: String(this.getLanguage() || 'en').toLowerCase().startsWith('zh') ? 'zh' : 'en',
    };
    this.pendingCredentials.set(sessionId, record);
    this.credentialWindows.set(sessionId, window);
    window.once('ready-to-show', () => window.show());
    window.on('closed', () => {
      if (this.pendingCredentials.get(sessionId) === record) this._settleCredential(record, { ok: false, code: 'credential_cancelled' });
    });
    record.timer = setTimeout(() => this._settleCredential(record, { ok: false, code: 'credential_timeout' }), CREDENTIAL_TIMEOUT_MS);
    await window.loadFile(path.join(__dirname, 'remote-desktop-credential.html'));
    return request.promise;
  }

  async handle(method, args = {}) {
    switch (String(method || '')) {
      case 'probe': return this.probe();
      case 'negotiate': return this.negotiate(args);
      case 'disconnect': return this.disconnect(args);
      case 'displays': return this.displays(args);
      case 'select_display': return this.selectDisplay(args);
      case 'set_quality': return this.setQuality(args);
      case 'set_microphone': return this.setMicrophone(args);
      case 'write_clipboard_image': return this.writeClipboardImage(args);
      case 'export_clipboard_image': return this.exportClipboardImage(args);
      case 'ack_clipboard_image': return this.ackClipboardImage(args);
      case 'export_clipboard_files': return this.exportClipboardFiles(args);
      case 'ack_clipboard_files': return this.ackClipboardFiles(args);
      case 'write_local_clipboard_image': return this.writeLocalClipboardImage(args);
      case 'write_clipboard_files': return this.writeClipboardFiles(args);
      case 'write_local_clipboard_files': return this.writeLocalClipboardFiles(args);
      case 'request_credentials': return this.requestCredentials(args);
      case 'show_indicator': return this.showIndicator(args);
      case 'hide_indicator': return this.hideIndicator(args);
      case 'consume_forced_disconnects': return this.consumeForcedDisconnects();
      case 'security_state': return this.securityState();
      default: return { ok: false, code: 'desktop_host_method_unknown', error: `Unknown desktop RPC method: ${method}` };
    }
  }

  async close() {
    for (const sessionId of [...this.sessions.keys()]) await this.disconnect({ session_id: sessionId });
    for (const sessionId of [...this.indicatorWindows.keys()]) this.hideIndicator({ session_id: sessionId });
    for (const record of [...this.pendingCredentials.values()]) this._settleCredential(record, { ok: false, code: 'credential_cancelled' });
    const manager = this.getAppUseManager();
    for (const sessionId of this.inputSessions.values()) await manager.handle('disconnect', { session_id: sessionId }).catch(() => {});
    this.inputSessions.clear();
  }
}

module.exports = { RemoteDesktopManager };
