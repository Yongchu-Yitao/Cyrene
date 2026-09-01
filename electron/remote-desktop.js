const {
  BrowserWindow,
  clipboard,
  desktopCapturer,
  ipcMain,
  nativeImage,
  nativeTheme,
  powerMonitor,
  screen,
  session,
  systemPreferences,
} = require('electron');
const crypto = require('crypto');
const fs = require('fs');
const http = require('http');
const path = require('path');
const { pathToFileURL } = require('url');
const { spawn } = require('child_process');
const {
  inputBounds: remoteDesktopInputBounds,
  inputPoint: remoteDesktopInputPoint,
} = require('./remote-desktop-coordinates');

let dbus = null;
if (process.platform === 'linux') {
  try { dbus = require('dbus-next'); } catch (_) {}
}

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

const MUTTER_REMOTE_DESKTOP_BUS = 'org.gnome.Mutter.RemoteDesktop';
const MUTTER_REMOTE_DESKTOP_PATH = '/org/gnome/Mutter/RemoteDesktop';
const MUTTER_REMOTE_DESKTOP_MANAGER = 'org.gnome.Mutter.RemoteDesktop';
const MUTTER_REMOTE_DESKTOP_SESSION = 'org.gnome.Mutter.RemoteDesktop.Session';
const MUTTER_SCREEN_CAST_BUS = 'org.gnome.Mutter.ScreenCast';
const MUTTER_SCREEN_CAST_PATH = '/org/gnome/Mutter/ScreenCast';
const MUTTER_SCREEN_CAST_MANAGER = 'org.gnome.Mutter.ScreenCast';
const MUTTER_SCREEN_CAST_SESSION = 'org.gnome.Mutter.ScreenCast.Session';
const DBUS_PROPERTIES = 'org.freedesktop.DBus.Properties';

const KEY_SYMS = {
  ArrowUp: 0xff52,
  ArrowDown: 0xff54,
  ArrowLeft: 0xff51,
  ArrowRight: 0xff53,
  Backspace: 0xff08,
  Delete: 0xffff,
  Enter: 0xff0d,
  Escape: 0xff1b,
  Home: 0xff50,
  End: 0xff57,
  Insert: 0xff63,
  PageUp: 0xff55,
  PageDown: 0xff56,
  Tab: 0xff09,
  ' ': 0x20,
  F1: 0xffbe,
  F2: 0xffbf,
  F3: 0xffc0,
  F4: 0xffc1,
  F5: 0xffc2,
  F6: 0xffc3,
  F7: 0xffc4,
  F8: 0xffc5,
  F9: 0xffc6,
  F10: 0xffc7,
  F11: 0xffc8,
  F12: 0xffc9,
};

const MODIFIER_KEY_SYMS = {
  shift: 0xffe1,
  control: 0xffe3,
  ctrl: 0xffe3,
  alt: 0xffe9,
  meta: 0xffeb,
  super: 0xffeb,
};

function keySym(value) {
  const key = String(value || '');
  if (Object.prototype.hasOwnProperty.call(KEY_SYMS, key)) return KEY_SYMS[key];
  const codePoint = key.codePointAt(0);
  if (!Number.isInteger(codePoint)) return 0;
  return codePoint <= 0xff ? codePoint : 0x01000000 | codePoint;
}

class MutterRemoteDesktopInput {
  constructor() {
    this.bus = null;
    this.manager = null;
    this.session = null;
    this.sessionPath = '';
    this.screenCastManager = null;
    this.screenCastSession = null;
    this.screenCastSessionPath = '';
    this.streamPath = '';
    this.streamBounds = null;
    this.streamIsVirtual = false;
    this.pipeWireNode = null;
    this.pipeWireNodeReady = null;
    this.captureServer = null;
    this.captureProcess = null;
    this.captureClients = new Set();
    this.captureBuffer = Buffer.alloc(0);
    this.latestCaptureFrame = null;
    this.starting = null;
  }

  async available() {
    if (!dbus || !process.env.DBUS_SESSION_BUS_ADDRESS) return false;
    try {
      if (!this.bus) this.bus = dbus.sessionBus();
      if (!this.manager) {
        const root = await this.bus.getProxyObject(
          MUTTER_REMOTE_DESKTOP_BUS,
          MUTTER_REMOTE_DESKTOP_PATH,
        );
        this.manager = root.getInterface(MUTTER_REMOTE_DESKTOP_MANAGER);
      }
      return Boolean(this.manager && typeof this.manager.CreateSession === 'function');
    } catch (_) {
      await this.stop();
      return false;
    }
  }

  async _createAbsoluteStream(sessionProxy, display, captureSize = null) {
    if (!dbus || !dbus.Variant) return;
    const properties = sessionProxy.getInterface(DBUS_PROPERTIES);
    const remoteSessionId = await properties.Get(MUTTER_REMOTE_DESKTOP_SESSION, 'SessionId');
    const root = await this.bus.getProxyObject(MUTTER_SCREEN_CAST_BUS, MUTTER_SCREEN_CAST_PATH);
    this.screenCastManager = root.getInterface(MUTTER_SCREEN_CAST_MANAGER);
    this.screenCastSessionPath = String(await this.screenCastManager.CreateSession({
      'remote-desktop-session-id': new dbus.Variant(
        's',
        String(remoteSessionId && remoteSessionId.value || remoteSessionId || ''),
      ),
    }));
    const screenCastProxy = await this.bus.getProxyObject(
      MUTTER_SCREEN_CAST_BUS,
      this.screenCastSessionPath,
    );
    this.screenCastSession = screenCastProxy.getInterface(MUTTER_SCREEN_CAST_SESSION);
    const bounds = display && display.bounds;
    const streamOptions = {
      // The controller renders its local system cursor. Keeping the compositor
      // cursor out of the stream avoids the double-cursor artifact entirely.
      'cursor-mode': new dbus.Variant('u', 0),
      'is-recording': new dbus.Variant('b', false),
    };
    if (bounds) {
      try {
        this.streamPath = String(await this.screenCastSession.RecordArea(
          Math.round(Number(bounds.x || 0)),
          Math.round(Number(bounds.y || 0)),
          Math.max(1, Math.round(Number(bounds.width || 1))),
          Math.max(1, Math.round(Number(bounds.height || 1))),
          streamOptions,
        ));
        this.streamBounds = {
          width: Math.max(1, Number(bounds.width || 1)),
          height: Math.max(1, Number(bounds.height || 1)),
        };
      } catch (_) {
        // Headless GNOME sessions expose an XWayland root window but no Mutter
        // monitor. RecordArea is therefore off-screen and Chromium captures a
        // valid-looking black frame. RecordVirtual asks Mutter to create the
        // actual compositor-backed desktop instead.
      }
    }
    if (!this.streamPath) {
      const width = Math.max(320, Math.round(Number(captureSize && captureSize.width || 1920)));
      const height = Math.max(240, Math.round(Number(captureSize && captureSize.height || 1080)));
      this.streamPath = String(await this.screenCastSession.RecordVirtual({
        'cursor-mode': new dbus.Variant('u', 0),
        'is-platform': new dbus.Variant('b', false),
      }));
      this.streamBounds = { width, height };
      this.streamIsVirtual = true;
    }
    const streamProxy = await this.bus.getProxyObject(MUTTER_SCREEN_CAST_BUS, this.streamPath);
    const stream = streamProxy.getInterface('org.gnome.Mutter.ScreenCast.Stream');
    this.pipeWireNodeReady = new Promise((resolve) => {
      stream.once('PipeWireStreamAdded', (nodeId) => {
        this.pipeWireNode = Number(nodeId);
        resolve(this.pipeWireNode);
      });
    });
  }

  _clearAbsoluteStream() {
    this.screenCastManager = null;
    this.screenCastSession = null;
    this.screenCastSessionPath = '';
    this.streamPath = '';
    this.streamBounds = null;
    this.streamIsVirtual = false;
    this.pipeWireNode = null;
    this.pipeWireNodeReady = null;
  }

  async start(display = null, captureSize = null) {
    if (this.session && display && !this.streamPath) await this.stop();
    if (this.session) return this.session;
    if (this.starting) return this.starting;
    this.starting = (async () => {
      if (!await this.available()) throw new Error('desktop_wayland_input_bridge_unavailable');
      const sessionPath = await this.manager.CreateSession();
      const proxy = await this.bus.getProxyObject(MUTTER_REMOTE_DESKTOP_BUS, sessionPath);
      const session = proxy.getInterface(MUTTER_REMOTE_DESKTOP_SESSION);
      try {
        await this._createAbsoluteStream(proxy, display, captureSize);
      } catch (_) {
        // GNOME versions without a linked ScreenCast stream still support
        // relative input. The fallback never re-anchors on button-down, which
        // was the source of the visible click-time cursor jump.
        this._clearAbsoluteStream();
      }
      await session.Start();
      this.sessionPath = String(sessionPath);
      this.session = session;
      session.once('Closed', () => {
        this.session = null;
        this.sessionPath = '';
        this._clearAbsoluteStream();
      });
      return session;
    })();
    try { return await this.starting; } finally { this.starting = null; }
  }

  async stop() {
    await this.stopNativeCapture();
    const session = this.session;
    this.session = null;
    this.sessionPath = '';
    this._clearAbsoluteStream();
    if (session) await session.Stop().catch(() => {});
    if (this.bus) {
      try { this.bus.disconnect(); } catch (_) {}
    }
    this.bus = null;
    this.manager = null;
  }

  _captureDimensions(qualityMode, viewport = null) {
    const profiles = {
      smooth: { maxWidth: 1600, maxHeight: 1200, maxPixels: 1_450_000, frameRate: 45, jpegQuality: 88 },
      balanced: { maxWidth: 2560, maxHeight: 1600, maxPixels: 3_300_000, frameRate: 30, jpegQuality: 94 },
      clear: { maxWidth: 3840, maxHeight: 2160, maxPixels: 8_300_000, frameRate: 30, jpegQuality: 96 },
      auto: { maxWidth: 2560, maxHeight: 1600, maxPixels: 3_300_000, frameRate: 30, jpegQuality: 94 },
    };
    const profile = profiles[String(qualityMode || 'auto')] || profiles.auto;
    const ratio = Math.max(0.5, Math.min(2, Number(viewport && viewport.device_pixel_ratio || 1)));
    const requestedWidth = Math.max(320, Math.min(3840, Math.round(
      Number(viewport && viewport.width || 1920) * ratio,
    )));
    const requestedHeight = Math.max(240, Math.min(2160, Math.round(
      Number(viewport && viewport.height || 1080) * ratio,
    )));
    const scale = Math.min(
      1,
      profile.maxWidth / requestedWidth,
      profile.maxHeight / requestedHeight,
      Math.sqrt(profile.maxPixels / (requestedWidth * requestedHeight)),
    );
    return {
      width: Math.max(320, Math.round(requestedWidth * scale / 2) * 2),
      height: Math.max(240, Math.round(requestedHeight * scale / 2) * 2),
      frameRate: profile.frameRate,
      jpegQuality: profile.jpegQuality,
    };
  }

  _publishCaptureFrame(frame) {
    this.latestCaptureFrame = frame;
    const header = Buffer.from(
      `--cyrene-frame\r\nContent-Type: image/jpeg\r\nContent-Length: ${frame.length}\r\n\r\n`,
    );
    const ending = Buffer.from('\r\n');
    for (const response of [...this.captureClients]) {
      if (response.destroyed || response.writableEnded) {
        this.captureClients.delete(response);
        continue;
      }
      try {
        response.write(header);
        response.write(frame);
        response.write(ending);
      } catch (_) {
        this.captureClients.delete(response);
      }
    }
  }

  _consumeCaptureBytes(chunk) {
    this.captureBuffer = Buffer.concat([this.captureBuffer, chunk]);
    while (this.captureBuffer.length > 3) {
      const start = this.captureBuffer.indexOf(Buffer.from([0xff, 0xd8]));
      if (start < 0) {
        this.captureBuffer = this.captureBuffer.subarray(Math.max(0, this.captureBuffer.length - 1));
        return;
      }
      const end = this.captureBuffer.indexOf(Buffer.from([0xff, 0xd9]), start + 2);
      if (end < 0) {
        if (start > 0) this.captureBuffer = this.captureBuffer.subarray(start);
        if (this.captureBuffer.length > 32 * 1024 * 1024) this.captureBuffer = Buffer.alloc(0);
        return;
      }
      const frame = this.captureBuffer.subarray(start, end + 2);
      this.captureBuffer = this.captureBuffer.subarray(end + 2);
      this._publishCaptureFrame(frame);
    }
  }

  async startNativeCapture(display, qualityMode = 'auto', viewport = null) {
    if (!commandExists('gst-launch-1.0')) throw new Error('desktop_pipewire_capture_component_missing');
    const dimensions = this._captureDimensions(qualityMode, viewport);
    await this.start(display, dimensions);
    const nodeId = this.pipeWireNode || await Promise.race([
      this.pipeWireNodeReady,
      new Promise((_, reject) => setTimeout(
        () => reject(new Error('desktop_pipewire_node_timeout')),
        8000,
      )),
    ]);
    if (!Number.isInteger(nodeId)) throw new Error('desktop_pipewire_node_unavailable');

    this.captureServer = http.createServer((request, response) => {
      if (request.url !== '/stream') {
        response.writeHead(404, { 'Content-Type': 'text/plain' });
        response.end('Not found');
        return;
      }
      response.writeHead(200, {
        'Access-Control-Allow-Origin': '*',
        'Cache-Control': 'no-store, no-cache, must-revalidate',
        Connection: 'close',
        'Content-Type': 'multipart/x-mixed-replace; boundary=cyrene-frame',
      });
      this.captureClients.add(response);
      if (this.latestCaptureFrame) this._publishCaptureFrame(this.latestCaptureFrame);
      request.on('close', () => this.captureClients.delete(response));
    });
    await new Promise((resolve, reject) => {
      this.captureServer.once('error', reject);
      this.captureServer.listen(0, '127.0.0.1', resolve);
    });

    const captureWidth = this.streamIsVirtual
      ? dimensions.width : Math.max(1, Math.round(Number(this.streamBounds && this.streamBounds.width || dimensions.width)));
    const captureHeight = this.streamIsVirtual
      ? dimensions.height : Math.max(1, Math.round(Number(this.streamBounds && this.streamBounds.height || dimensions.height)));
    const caps = [
      'video/x-raw',
      'format=BGRx',
      `width=${captureWidth}`,
      `height=${captureHeight}`,
      // Mutter advertises a fixed 0/1 framerate plus max-framerate. Requesting
      // ordinary framerate here makes PipeWire reject every input format.
      `max-framerate=${dimensions.frameRate}/1`,
    ].join(',');
    this.captureProcess = spawn('gst-launch-1.0', [
      '-q',
      'pipewiresrc', `path=${nodeId}`, 'do-timestamp=true',
      '!', caps,
      '!', 'videoconvert',
      '!', 'jpegenc', `quality=${dimensions.jpegQuality}`, 'snapshot=false',
      '!', 'fdsink', 'fd=1', 'sync=false',
    ], {
      stdio: ['ignore', 'pipe', 'pipe'],
      env: process.env,
    });
    let captureError = '';
    this.captureProcess.stderr.on('data', (chunk) => {
      captureError = `${captureError}${String(chunk)}`.slice(-4096);
    });
    this.captureProcess.stdout.on('data', (chunk) => this._consumeCaptureBytes(chunk));
    const firstFrame = new Promise((resolve, reject) => {
      const startedAt = Date.now();
      const timer = setInterval(() => {
        if (this.latestCaptureFrame) {
          clearInterval(timer);
          resolve();
        } else if (Date.now() - startedAt >= 8000) {
          clearInterval(timer);
          reject(new Error(captureError || 'desktop_pipewire_frame_timeout'));
        }
      }, 25);
      this.captureProcess.once('exit', (code) => {
        if (!this.latestCaptureFrame) {
          clearInterval(timer);
          reject(new Error(captureError || `desktop_pipewire_capture_exited_${code}`));
        }
      });
      this.captureProcess.once('error', (error) => {
        clearInterval(timer);
        reject(error);
      });
    });
    try {
      await firstFrame;
    } catch (error) {
      await this.stopNativeCapture();
      throw error;
    }
    const address = this.captureServer.address();
    return {
      url: `http://127.0.0.1:${Number(address && address.port)}/stream`,
      width: captureWidth,
      height: captureHeight,
      frame_rate: dimensions.frameRate,
      virtual: this.streamIsVirtual,
    };
  }

  async stopNativeCapture() {
    const processToStop = this.captureProcess;
    this.captureProcess = null;
    if (processToStop && processToStop.exitCode == null) {
      try { processToStop.kill('SIGTERM'); } catch (_) {}
    }
    for (const response of this.captureClients) {
      try { response.end(); } catch (_) {}
    }
    this.captureClients.clear();
    const server = this.captureServer;
    this.captureServer = null;
    if (server) await new Promise((resolve) => server.close(resolve));
    this.captureBuffer = Buffer.alloc(0);
    this.latestCaptureFrame = null;
  }

  async moveRelative(dx, dy, display = null) {
    const session = await this.start(display);
    await session.NotifyPointerMotionRelative(Number(dx || 0), Number(dy || 0));
  }

  async moveAbsolute(xNormalized, yNormalized, display) {
    const session = await this.start(display);
    if (!this.streamPath || !this.streamBounds) return false;
    const x = Math.max(0, Math.min(1, Number(xNormalized || 0)))
      * Math.max(1, this.streamBounds.width - 1);
    const y = Math.max(0, Math.min(1, Number(yNormalized || 0)))
      * Math.max(1, this.streamBounds.height - 1);
    await session.NotifyPointerMotionAbsolute(this.streamPath, x, y);
    return true;
  }

  async button(button, pressed) {
    const session = await this.start();
    await session.NotifyPointerButton(Number(button), pressed === true);
  }

  async scroll(deltaX, deltaY) {
    const session = await this.start();
    await session.NotifyPointerAxis(Number(deltaX || 0), Number(deltaY || 0), 0);
  }

  async typeKey(sym, modifiers = []) {
    const session = await this.start();
    const pressed = [];
    try {
      for (const modifier of modifiers) {
        const modifierSym = MODIFIER_KEY_SYMS[String(modifier || '').toLowerCase()];
        if (!modifierSym || pressed.includes(modifierSym)) continue;
        await session.NotifyKeyboardKeysym(modifierSym, true);
        pressed.push(modifierSym);
      }
      await session.NotifyKeyboardKeysym(Number(sym), true);
      await session.NotifyKeyboardKeysym(Number(sym), false);
    } finally {
      for (const modifierSym of pressed.reverse()) {
        await session.NotifyKeyboardKeysym(modifierSym, false).catch(() => {});
      }
    }
  }
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
    this.indicatorThemeSyncTimer = null;
    this.indicatorThemeSyncRunning = false;
    this.terminatedSessions = new Map();
    this.screenLocked = false;
    this.securitySurfaceState = false;
    this.securityEpoch = 0;
    this.mutterInput = process.platform === 'linux'
      ? new MutterRemoteDesktopInput() : null;
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
        ? await this.mutterInput.available() ? 'granted' : 'denied'
        : commandExists('xdotool') ? 'granted' : 'denied';
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
      this._releasePointer(record).catch(() => {}).finally(() => {
        if (record.mutterInput) record.mutterInput.stop().catch(() => {});
      });
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
      mutterInput: process.platform === 'linux' ? new MutterRemoteDesktopInput() : null,
      nativeCapture: null,
      nativeCaptureTimer: null,
      nativeCaptureUpdate: null,
      viewport: args.viewport && typeof args.viewport === 'object' ? { ...args.viewport } : {},
    };
    const selected = this._display(record.displayId);
    if (selected) record.displayId = String(selected.id);
    this.sessions.set(sessionId, record);
    try {
      if (
        process.platform === 'linux'
        && String(process.env.XDG_SESSION_TYPE || 'x11').toLowerCase() === 'wayland'
        && record.mutterInput
      ) {
        // Use the linked compositor stream for both unattended video capture
        // and absolute input. Chromium's X11 desktopCapturer only sees the
        // empty XWayland root in headless Wayland sessions, producing a black
        // but otherwise healthy WebRTC video track.
        record.nativeCapture = await record.mutterInput.startNativeCapture(
          selected,
          record.qualityMode,
          record.viewport,
        );
      }
      await this._createHost(record);
      if (process.platform === 'win32') {
        // Warm the persistent Windows input worker while WebRTC negotiates so
        // the first pointer event never pays PowerShell/.NET startup cost.
        this.getAppUseManager().handle('list_targets', {}).catch(() => {});
      }
      const pendingAnswer = record.pendingAnswer;
      record.window.webContents.send('remote-desktop:start', {
        session_id: sessionId,
        offer: args.offer,
        display_id: record.displayId,
        quality_mode: record.qualityMode,
        ice_servers: Array.isArray(args.ice_servers) ? args.ice_servers : [],
        permissions: record.permissions,
        microphone_sink_id: record.microphoneSinkId,
        native_capture: record.nativeCapture,
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
      record.inputQueue.catch((error) => this._reportInputFailure(record, event, error));
      return;
    }
    record.inputQueue = Promise.resolve(record.inputQueue)
      .catch(() => {})
      .then(() => this._performInput(record, event || {}));
    record.inputQueue.catch((error) => this._reportInputFailure(record, event, error));
  }

  _reportInputFailure(record, event, error) {
    const reason = String(error && (error.code || error.message) || error || 'desktop_input_failed');
    const now = Date.now();
    if (record.lastInputError === reason && now - Number(record.lastInputErrorAt || 0) < 2000) return;
    record.lastInputError = reason;
    record.lastInputErrorAt = now;
    const type = String(event && event.type || 'unknown');
    const action = String(event && event.action || '');
    console.warn(`[remote-desktop] ${type}${action ? `/${action}` : ''} input failed: ${reason}`);
  }

  _acceptControl(sender, payload) {
    const record = this._recordForSender(sender, payload && payload.session_id);
    if (!record) return;
    const message = payload && payload.message && typeof payload.message === 'object' ? payload.message : {};
    if (message.type === 'input' && record.permissions.input === true) {
      this._queueInput(record, message.event || {});
      return;
    }
    if (message.type === 'viewport') {
      record.viewport = {
        width: Math.max(1, Number(message.width || 1)),
        height: Math.max(1, Number(message.height || 1)),
        device_pixel_ratio: Math.max(0.5, Math.min(2, Number(message.device_pixel_ratio || 1))),
      };
      record.window.webContents.send('remote-desktop:command', {
        operation: 'set_viewport',
        width: record.viewport.width,
        height: record.viewport.height,
        device_pixel_ratio: record.viewport.device_pixel_ratio,
      });
      this._scheduleNativeViewport(record);
      return;
    }
    if (message.type === 'clipboard:text' && record.permissions.clipboard_text === true) {
      const text = String(message.text || '').slice(0, 1024 * 1024);
      record.lastClipboardText = text;
      record.clipboardRevision += 1;
      clipboard.writeText(text);
    }
  }

  _scheduleNativeViewport(record) {
    if (
      !record
      || !record.nativeCapture
      || !record.mutterInput
      || !record.mutterInput.streamIsVirtual
    ) return;
    const target = record.mutterInput._captureDimensions(record.qualityMode, record.viewport);
    const current = record.nativeCapture;
    const materiallyChanged = Math.abs(Number(current.width || 0) - target.width) > Math.max(32, target.width * 0.06)
      || Math.abs(Number(current.height || 0) - target.height) > Math.max(32, target.height * 0.06);
    if (!materiallyChanged) return;
    if (record.nativeCaptureTimer) clearTimeout(record.nativeCaptureTimer);
    record.nativeCaptureTimer = setTimeout(() => {
      record.nativeCaptureTimer = null;
      const update = Promise.resolve(record.nativeCaptureUpdate).catch(() => {}).then(async () => {
        if (this.sessions.get(record.sessionId) !== record) return;
        await this._releasePointer(record).catch(() => {});
        await record.mutterInput.stop();
        const next = await record.mutterInput.startNativeCapture(
          this._display(record.displayId),
          record.qualityMode,
          record.viewport,
        );
        if (this.sessions.get(record.sessionId) !== record) {
          await record.mutterInput.stop().catch(() => {});
          return;
        }
        record.nativeCapture = next;
        record.lastPointerPoint = null;
        record.window.webContents.send('remote-desktop:command', {
          operation: 'set_viewport',
          width: record.viewport.width,
          height: record.viewport.height,
          device_pixel_ratio: record.viewport.device_pixel_ratio,
          native_capture: next,
        });
      });
      record.nativeCaptureUpdate = update;
      update.catch((error) => {
        console.warn('[remote-desktop] Failed to resize native Wayland capture:', error);
      }).finally(() => {
        if (record.nativeCaptureUpdate === update) record.nativeCaptureUpdate = null;
      });
    }, 350);
    if (record.nativeCaptureTimer.unref) record.nativeCaptureTimer.unref();
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
    return remoteDesktopInputPoint(display, event, { platform: process.platform, screenApi: screen });
  }

  _inputBounds(record) {
    const display = this._display(record.displayId);
    return remoteDesktopInputBounds(display, { platform: process.platform, screenApi: screen });
  }

  async _linuxInput(record, event) {
    if (String(process.env.XDG_SESSION_TYPE || 'x11').toLowerCase() === 'wayland') {
      if (record.nativeCaptureUpdate) await record.nativeCaptureUpdate.catch(() => {});
      const mutterInput = record.mutterInput;
      if (!mutterInput) throw new Error('desktop_wayland_input_bridge_unavailable');
      const type = String(event.type || '');
      if (type === 'pointer') {
        const point = this._screenPoint(record, event);
        const action = String(event.action || 'move');
        const display = this._display(record.displayId);
        const displays = screen.getAllDisplays();
        const minX = Math.min(...displays.map((item) => Number(item.bounds && item.bounds.x || 0)));
        const minY = Math.min(...displays.map((item) => Number(item.bounds && item.bounds.y || 0)));
        const movedAbsolute = await mutterInput.moveAbsolute(
          event.x_normalized,
          event.y_normalized,
          display,
        );
        if (!movedAbsolute && !record.lastPointerPoint) {
          await mutterInput.moveRelative(-100000, -100000, display);
          await mutterInput.moveRelative(point.x - minX, point.y - minY, display);
        } else if (!movedAbsolute) {
          await mutterInput.moveRelative(
            point.x - Number(record.lastPointerPoint.x || 0),
            point.y - Number(record.lastPointerPoint.y || 0),
            display,
          );
        }
        record.lastPointerPoint = point;
        if (action === 'button_down') {
          record.pointerPressed = true;
          return mutterInput.button(0x110, true);
        }
        if (action === 'button_up') {
          record.pointerPressed = false;
          return mutterInput.button(0x110, false);
        }
        if (action === 'right_click') {
          await mutterInput.button(0x111, true);
          await mutterInput.button(0x111, false);
        } else if (action === 'double_click') {
          for (let index = 0; index < 2; index += 1) {
            await mutterInput.button(0x110, true);
            await mutterInput.button(0x110, false);
          }
        } else if (action === 'scroll') {
          await mutterInput.scroll(
            Number(event.delta_x || 0),
            Number(event.delta_y || 0),
          );
        } else if (action === 'click') {
          await mutterInput.button(0x110, true);
          await mutterInput.button(0x110, false);
        }
        return { ok: true };
      }
      if (type === 'text') {
        for (const character of Array.from(String(event.text || '').slice(0, 65536))) {
          const sym = keySym(character);
          if (sym) await mutterInput.typeKey(sym);
        }
        return { ok: true };
      }
      if (type === 'key') {
        const sym = keySym(event.key);
        if (sym) await mutterInput.typeKey(sym, Array.isArray(event.modifiers) ? event.modifiers : []);
        return { ok: true };
      }
      return { ok: true };
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
    const bounds = this._inputBounds(record);
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
      if (
        process.platform === 'linux'
        && String(process.env.XDG_SESSION_TYPE || 'x11').toLowerCase() === 'wayland'
        && record.mutterInput
        && record.pointerPressed
      ) {
        await record.mutterInput.button(0x110, false);
      } else if (process.platform === 'linux' && commandExists('xdotool')) {
        await runCommand('xdotool', ['mouseup', '1'], 1500);
      } else if (record.pointerPressed && record.activePointerSession && record.lastPointerPoint) {
        const bounds = this._inputBounds(record);
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
    if (record.nativeCaptureTimer) clearTimeout(record.nativeCaptureTimer);
    await this._releasePointer(record).catch(() => {});
    if (record.window && !record.window.isDestroyed()) {
      record.window.webContents.send('remote-desktop:command', { operation: 'disconnect' });
      record.window.destroy();
    }
    if (record.mutterInput) await record.mutterInput.stop();
    return { ok: true, disconnected: true };
  }

  displays() { return { ok: true, displays: this._displays() }; }

  async selectDisplay(args) {
    const record = this.sessions.get(String(args.session_id || ''));
    const display = this._display(args.display_id);
    if (!record || !display) return { ok: false, code: 'desktop_display_not_found' };
    if (record.permissions.display_select !== true) return { ok: false, code: 'desktop_capability_denied' };
    record.displayId = String(display.id);
    record.lastPointerPoint = null;
    if (record.mutterInput) {
      await record.mutterInput.stop().catch(() => {});
      if (String(process.env.XDG_SESSION_TYPE || 'x11').toLowerCase() === 'wayland') {
        record.nativeCapture = await record.mutterInput.startNativeCapture(
          display,
          record.qualityMode,
          record.viewport,
        );
      }
    }
    record.window.webContents.send('remote-desktop:command', {
      operation: 'select_display',
      display_id: record.displayId,
      native_capture: record.nativeCapture,
    });
    return { ok: true, display: publicDisplay(display, screen.getPrimaryDisplay().id) };
  }

  async setQuality(args) {
    const record = this.sessions.get(String(args.session_id || ''));
    if (!record) return { ok: false, code: 'desktop_session_not_found' };
    record.qualityMode = String(args.quality_mode || 'auto');
    if (
      record.mutterInput
      && String(process.env.XDG_SESSION_TYPE || 'x11').toLowerCase() === 'wayland'
    ) {
      await record.mutterInput.stop().catch(() => {});
      record.nativeCapture = await record.mutterInput.startNativeCapture(
        this._display(record.displayId),
        record.qualityMode,
        record.viewport,
      );
    }
    record.window.webContents.send('remote-desktop:command', {
      operation: 'set_quality',
      quality_mode: record.qualityMode,
      native_capture: record.nativeCapture,
    });
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
    const theme = await this._readIndicatorTheme();
    const retryDelays = [0, 100, 250, 500];
    let lastError = null;
    for (const retryDelay of retryDelays) {
      if (retryDelay) await new Promise((resolve) => setTimeout(resolve, retryDelay));
      const indicator = new BrowserWindow({
        width: 400,
        height: 84,
        show: false,
        frame: false,
        transparent: true,
        backgroundColor: '#00000000',
        hasShadow: false,
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
        theme,
        window: indicator,
      };
      this.indicatorWindows.set(sessionId, record);
      indicator.on('closed', () => {
        if (this.indicatorWindows.get(sessionId) !== record) return;
        this.indicatorWindows.delete(sessionId);
        if (!this.indicatorWindows.size) this._stopIndicatorThemeSync();
      });
      indicator.once('ready-to-show', () => {
        if (indicator.isDestroyed() || this.indicatorWindows.get(sessionId) !== record) return;
        const display = screen.getDisplayNearestPoint(screen.getCursorScreenPoint());
        const area = display && display.workArea || { x: 0, y: 0, width: 1280 };
        indicator.setPosition(
          Math.round(Number(area.x || 0) + Number(area.width || 1280) - 418),
          Math.round(Number(area.y || 0) + 16),
          false,
        );
        indicator.showInactive();
      });
      try {
        await indicator.loadFile(path.join(__dirname, 'remote-desktop-indicator.html'));
        this._startIndicatorThemeSync();
        return { ok: true };
      } catch (error) {
        lastError = error;
        this.hideIndicator({ session_id: sessionId });
      }
    }
    console.warn('[remote-desktop] Failed to load the active-session indicator after retries:', lastError);
    return { ok: false, code: 'desktop_target_indicator_unavailable' };
  }

  hideIndicator(args) {
    const sessionId = String(args.session_id || '');
    const record = this.indicatorWindows.get(sessionId);
    if (!record) return { ok: true, hidden: false };
    this.indicatorWindows.delete(sessionId);
    if (record.window && !record.window.isDestroyed()) record.window.destroy();
    if (!this.indicatorWindows.size) this._stopIndicatorThemeSync();
    return { ok: true, hidden: true };
  }

  async _readIndicatorTheme() {
    let mainWindow = null;
    try { mainWindow = this.getMainWindow(); } catch (_) {}
    if (mainWindow && !mainWindow.isDestroyed() && !mainWindow.webContents.isDestroyed()) {
      try {
        const theme = await mainWindow.webContents.executeJavaScript(
          "document.documentElement.dataset.theme || ''",
          true,
        );
        if (theme === 'light' || theme === 'dark') return theme;
      } catch (_) {}
    }
    return nativeTheme && nativeTheme.shouldUseDarkColors ? 'dark' : 'light';
  }

  _startIndicatorThemeSync() {
    if (this.indicatorThemeSyncTimer) return;
    const sync = async () => {
      if (this.indicatorThemeSyncRunning || !this.indicatorWindows.size) return;
      this.indicatorThemeSyncRunning = true;
      try {
        const theme = await this._readIndicatorTheme();
        for (const record of this.indicatorWindows.values()) {
          if (record.theme === theme || !record.window || record.window.isDestroyed()) continue;
          record.theme = theme;
          record.window.webContents.send('remote-desktop:indicator-theme', theme);
        }
      } finally {
        this.indicatorThemeSyncRunning = false;
      }
    };
    this.indicatorThemeSyncTimer = setInterval(sync, 1000);
    if (typeof this.indicatorThemeSyncTimer.unref === 'function') this.indicatorThemeSyncTimer.unref();
  }

  _stopIndicatorThemeSync() {
    if (!this.indicatorThemeSyncTimer) return;
    clearInterval(this.indicatorThemeSyncTimer);
    this.indicatorThemeSyncTimer = null;
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
      theme: record.theme,
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
    record.request.resolve(result);
    // Acknowledge ipcRenderer.invoke before destroying its sender. Destroying
    // synchronously can strand the renderer promise in a modal flow.
    setImmediate(() => {
      if (record.window && !record.window.isDestroyed()) record.window.destroy();
    });
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
    if (this.mutterInput) await this.mutterInput.stop();
  }
}

module.exports = { RemoteDesktopManager };
