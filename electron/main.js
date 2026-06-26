const {
  app,
  BrowserWindow,
  desktopCapturer,
  dialog,
  globalShortcut,
  ipcMain,
  Notification,
  screen,
  session,
  shell,
  systemPreferences,
} = require('electron');
const path = require('path');
const fs = require('fs');
const os = require('os');
const { spawn } = require('child_process');

// Log file for Python backend output — written to os.tmpdir() so it survives
// app crashes and is easy to find even without a terminal window.
// On Windows with console=False the process has no console; writing from the
// Node side ensures the log is populated on every platform.
const ERROR_LOG_PATH = path.join(os.tmpdir(), 'cyrene_error.log');
let _errorLogStream = null;

function getErrorLogStream() {
  if (!_errorLogStream) {
    try {
      _errorLogStream = fs.createWriteStream(ERROR_LOG_PATH, { flags: 'a' });
    } catch (_) {}
  }
  return _errorLogStream;
}

function appendErrorLog(text) {
  const s = getErrorLogStream();
  if (s) s.write(text);
}

// Desktop-local auth token. Generated once at module load and shared with the
// Python backend via env (CYRENE_AUTH_TOKEN). Injected as the X-Cyrene-Token
// header on every request to the local backend (see installAuthHeaderInjector).
// The renderer never sees this token.
const AUTH_TOKEN = require('crypto').randomBytes(32).toString('hex');

const isDev = process.env.ELECTRON_DEV === '1';
const isMac = process.platform === 'darwin';
const isWindows = process.platform === 'win32';
const isLinux = process.platform === 'linux';
const supportsLoginItem = process.platform === 'darwin' || process.platform === 'win32';

let mainWindow = null;
let quickChatWindow = null;
let quickChatWindowReady = null;
let quickChatOpenPromise = null;
let pendingQuickChatScreenshot = null;
let registeredQuickChatShortcut = '';
let quickChatShortcutError = '';
let pythonProcess = null;
let pendingPortResolve = null;
let backendPort = null;
let backendUiMode = null;
let isShuttingDown = false;
let isQuitting = false;
let launchHidden = process.argv.includes('--hidden');

const DEFAULT_DESKTOP_SETTINGS = Object.freeze({
  launchAtLogin: false,
  runInBackground: false,
  // Quick chat (global-shortcut assistant) is opt-in and requires background
  // residency — the global shortcut is only registered when it's enabled.
  quickChatEnabled: false,
  quickChatShortcut: 'CommandOrControl+Shift+Space',
});

function getNotificationIconPath() {
  const candidates = [
    path.join(__dirname, '..', 'build', 'icon.png'),
    path.join(process.resourcesPath || '', 'build', 'icon.png'),
    path.join(process.resourcesPath || '', 'cyrene.png'),
  ];
  return candidates.find((candidate) => {
    try {
      return candidate && fs.existsSync(candidate);
    } catch (_) {
      return false;
    }
  });
}

function getDesktopSettingsPath() {
  return path.join(app.getPath('userData'), 'desktop_settings.json');
}

function readDesktopSettings() {
  try {
    const raw = fs.readFileSync(getDesktopSettingsPath(), 'utf8');
    const parsed = JSON.parse(raw);
    const runInBackground = parsed.runInBackground === true;
    return {
      launchAtLogin: parsed.launchAtLogin === true,
      runInBackground,
      // Quick chat can't be on without background residency.
      quickChatEnabled: runInBackground && parsed.quickChatEnabled === true,
      quickChatShortcut: normalizeQuickChatShortcut(parsed.quickChatShortcut),
    };
  } catch (_) {
    return { ...DEFAULT_DESKTOP_SETTINGS };
  }
}

function writeDesktopSettings(settings) {
  const runInBackground = settings.runInBackground === true;
  const payload = {
    launchAtLogin: settings.launchAtLogin === true,
    runInBackground,
    quickChatEnabled: runInBackground && settings.quickChatEnabled === true,
    quickChatShortcut: normalizeQuickChatShortcut(settings.quickChatShortcut),
  };
  fs.mkdirSync(path.dirname(getDesktopSettingsPath()), { recursive: true });
  fs.writeFileSync(getDesktopSettingsPath(), JSON.stringify(payload, null, 2), 'utf8');
}

function applyLaunchAtLogin(enabled) {
  if (!supportsLoginItem) return false;
  app.setLoginItemSettings({
    openAtLogin: enabled === true,
    openAsHidden: enabled === true,
    args: enabled === true ? ['--hidden'] : [],
  });
  return true;
}

function getDesktopSettings() {
  const stored = readDesktopSettings();
  return {
    ...stored,
    supportsLaunchAtLogin: supportsLoginItem,
    platform: process.platform,
    quickChatShortcutRegistered: (
      registeredQuickChatShortcut === stored.quickChatShortcut
      && globalShortcut.isRegistered(stored.quickChatShortcut)
    ),
    quickChatShortcutError,
  };
}

// The app must keep running — and the Python backend must stay alive — after the
// last window is closed whenever a global quick-chat shortcut is registered
// (otherwise pressing it would open a window pointing at a dead backend) or the
// user opted into background mode. Quitting still tears Python down in
// before-quit; a hidden main window is restored via 'activate' (macOS) or by
// relaunching the app (single-instance → second-instance).
function appStaysResident() {
  if (registeredQuickChatShortcut) return true;
  try {
    return readDesktopSettings().runInBackground === true;
  } catch (_) {
    return false;
  }
}

function saveDesktopSettings(updates) {
  const current = readDesktopSettings();
  const next = {
    ...current,
    ...updates,
  };
  next.quickChatShortcut = normalizeQuickChatShortcut(next.quickChatShortcut);
  // Quick chat depends on background residency — turning residency off also
  // disables it (the UI gates the toggle, but enforce it here too).
  next.quickChatEnabled = next.runInBackground === true && next.quickChatEnabled === true;

  let shortcutUpdateOk = true;
  if (next.quickChatEnabled) {
    // Register (or re-register) the global shortcut. Only attempt it when the
    // binding is missing or changed so an unrelated toggle doesn't churn it.
    if (
      next.quickChatShortcut !== registeredQuickChatShortcut
      || !globalShortcut.isRegistered(next.quickChatShortcut)
    ) {
      shortcutUpdateOk = registerQuickChatShortcut(next.quickChatShortcut);
      if (!shortcutUpdateOk) {
        return {
          ...getDesktopSettings(),
          shortcutUpdateOk: false,
        };
      }
    }
  } else {
    // Disabled (or residency off) — release the shortcut and tear down the
    // transient window so nothing keeps the app resident for it.
    unregisterQuickChatShortcut();
    destroyQuickChatWindow();
  }

  writeDesktopSettings(next);
  applyLaunchAtLogin(next.launchAtLogin);
  return {
    ...getDesktopSettings(),
    shortcutUpdateOk,
  };
}

function unregisterQuickChatShortcut() {
  if (registeredQuickChatShortcut) {
    try { globalShortcut.unregister(registeredQuickChatShortcut); } catch (_) {}
  }
  registeredQuickChatShortcut = '';
  quickChatShortcutError = '';
}

function destroyQuickChatWindow() {
  pendingQuickChatScreenshot = null;
  if (quickChatWindow && !quickChatWindow.isDestroyed()) {
    quickChatWindow.destroy();
  }
  quickChatWindow = null;
  quickChatWindowReady = null;
}

function normalizeQuickChatShortcut(value) {
  const shortcut = String(value || '').trim();
  return shortcut || DEFAULT_DESKTOP_SETTINGS.quickChatShortcut;
}

function registerQuickChatShortcut(accelerator) {
  const requested = normalizeQuickChatShortcut(accelerator);
  const previous = registeredQuickChatShortcut;

  if (previous === requested && globalShortcut.isRegistered(requested)) {
    quickChatShortcutError = '';
    return true;
  }

  if (previous) {
    try { globalShortcut.unregister(previous); } catch (_) {}
    registeredQuickChatShortcut = '';
  }

  let registered = false;
  try {
    registered = globalShortcut.register(requested, () => {
      openQuickChat().catch((err) => {
        console.error('[electron] Failed to open quick chat:', err);
        appendErrorLog(`[electron] Failed to open quick chat: ${err && err.stack ? err.stack : err}\n`);
      });
    });
  } catch (err) {
    quickChatShortcutError = String((err && err.message) || err || 'shortcut_registration_failed');
  }

  if (registered) {
    registeredQuickChatShortcut = requested;
    quickChatShortcutError = '';
    return true;
  }

  quickChatShortcutError = quickChatShortcutError || 'shortcut_in_use';
  if (previous) {
    try {
      if (globalShortcut.register(previous, () => {
        openQuickChat().catch((err) => {
          console.error('[electron] Failed to open quick chat:', err);
        });
      })) {
        registeredQuickChatShortcut = previous;
      }
    } catch (_) {}
  }
  return false;
}

// ---------------------------------------------------------------------------
// Python child process management
// ---------------------------------------------------------------------------

function getPythonBinaryPath() {
  if (isDev) {
    return null; // use system python
  }
  // In a packaged Electron app, extraResources are in process.resourcesPath
  const base = process.resourcesPath;
  const name = isWindows ? 'Cyrene.exe' : 'Cyrene';
  return path.join(base, 'python-bundle', name);
}

function getPythonArgs() {
  if (isDev) {
    // Dev mode: use system python. CYRENE_UI_MODE=agent launches the legacy UI
    // (for testing the native title bar); anything else uses the workbench.
    const uiFlag = process.env.CYRENE_UI_MODE === 'agent' ? '--agent' : '--workbench';
    return [
      path.join(__dirname, '..', 'src', 'cyrene', 'local_cli.py'),
      uiFlag,
      '--electron-mode',
    ];
  }
  // Frozen mode: trampoline with --launch-web + --electron
  return ['--launch-web', '--electron'];
}

function spawnPython() {
  if (pythonProcess) return;
  const binaryPath = getPythonBinaryPath();
  const args = getPythonArgs();
  const cwd = isDev ? path.join(__dirname, '..') : undefined;
  const childEnv = {
    ...process.env,
    CYRENE_APP_EXECUTABLE: app.getPath('exe'),
    CYRENE_AUTH_TOKEN: AUTH_TOKEN,
  };

  if (binaryPath) {
    pythonProcess = spawn(binaryPath, args, {
      stdio: ['ignore', 'pipe', 'pipe'],
      windowsHide: true,
      env: childEnv,
    });
  } else {
    pythonProcess = spawn('python3', args, {
      stdio: ['ignore', 'pipe', 'pipe'],
      cwd: cwd,
      env: childEnv,
    });
  }

  let port = null;

  pythonProcess.stdout.on('data', (data) => {
    const text = data.toString();
    // Capture the UI mode (printed just before PORT) so the window is created
    // with the matching title bar style.
    const modeMatch = text.match(/^UIMODE=(\w+)$/m);
    if (modeMatch) {
      backendUiMode = modeMatch[1];
    }
    // Scan each line for PORT=<number>
    const match = text.match(/^PORT=(\d+)$/m);
    if (match) {
      port = parseInt(match[1], 10);
      // Store globally so a later waitForPort() can resolve even if the
      // PORT event arrived before any window registered a pending resolver
      // (e.g. launch-at-login hidden startup).
      backendPort = port;
      if (pendingPortResolve) {
        pendingPortResolve(port);
        pendingPortResolve = null;
      }
    }
    // Log any other stdout for debugging
    process.stdout.write(`[cyrene] ${text}`);
    appendErrorLog(text);
  });

  pythonProcess.stderr.on('data', (data) => {
    const text = data.toString();
    process.stderr.write(`[cyrene] ${text}`);
    appendErrorLog(text);
  });

  pythonProcess.on('error', (err) => {
    console.error('[electron] Failed to start Python backend:', err.message);
    dialog.showErrorBox(
      'Cyrene - Startup Error',
      `Failed to start the Python backend.\n\n${err.message}\n\n`
        + (isDev
          ? 'Make sure Python 3 is installed and accessible as "python3".'
          : 'The application may be corrupted. Please reinstall.')
    );
    if (pendingPortResolve) {
      pendingPortResolve(null);
      pendingPortResolve = null;
    }
    backendPort = null;
    app.quit();
  });

  pythonProcess.on('exit', (code) => {
    console.log(`[electron] Python backend exited (code=${code})`);
    pythonProcess = null;
    backendPort = null;
    if (code === 42) {
      // Exit code 42 = intentional restart after update.
      // Exit immediately to release the single-instance lock so the
      // detached updater script can launch the new version.
      app.exit(0);
    } else if (isShuttingDown) {
      // Normal shutdown — Python handled SIGTERM gracefully and exited with
      // code 0.  Don't scare the user with a crash dialog.
      app.quit();
    } else {
      // Show error regardless of window state — if Python crashed before
      // printing PORT= the window doesn't exist yet and the user would see
      // a silent flash-quit without this unconditional dialog.
      dialog.showErrorBox(
        'Cyrene - Backend Error',
        `The Python backend stopped unexpectedly (exit code ${code}).\n`
        + 'The application will now close.\n\n'
        + `If this keeps happening, check cyrene_error.log in ${os.tmpdir()}`
      );
      app.quit();
    }
  });
}

function killPython() {
  if (!pythonProcess) return;
  isShuttingDown = true;
  const proc = pythonProcess;
  pythonProcess = null;

  try {
    if (isWindows) {
      // On Windows, SIGTERM doesn't exist — use taskkill for the process tree.
      spawn('taskkill', ['/pid', String(proc.pid), '/f', '/t'], {
        stdio: ['ignore', 'pipe', 'pipe'],
        windowsHide: true,
      });
    } else {
      proc.kill('SIGTERM');
      // Graceful shutdown: wait up to 5s, then force-kill
      setTimeout(() => {
        try {
          if (proc.exitCode === null) proc.kill('SIGKILL');
        } catch (_) { /* ignore */ }
      }, 5000);
    }
  } catch (_) { /* ignore */ }
}

// ---------------------------------------------------------------------------
// Wait for Python to report its port
// ---------------------------------------------------------------------------

function waitForPort(timeoutMs = 30000) {
  // Port already reported (event may have arrived before this call) — resolve now.
  if (backendPort !== null) {
    return Promise.resolve(backendPort);
  }
  return new Promise((resolve, reject) => {
    pendingPortResolve = resolve;
    setTimeout(() => {
      if (pendingPortResolve) {
        pendingPortResolve = null;
        reject(new Error('Timed out waiting for Python backend to start'));
      }
    }, timeoutMs);
  });
}

// ---------------------------------------------------------------------------
// Auth header injection
// ---------------------------------------------------------------------------

// Inject the shared X-Cyrene-Token header on every request to the local
// backend — document loads, fetch, SSE, and WebSocket upgrades all go through
// onBeforeSendHeaders. Must be registered BEFORE the window loads the URL.
function installAuthHeaderInjector() {
  session.defaultSession.webRequest.onBeforeSendHeaders(
    { urls: ['http://127.0.0.1:*/*', 'ws://127.0.0.1:*/*'] },
    (details, callback) => {
      const requestHeaders = { ...details.requestHeaders, 'X-Cyrene-Token': AUTH_TOKEN };
      callback({ requestHeaders });
    }
  );

  // Deny all permission requests (camera, microphone, geolocation, etc.)
  session.defaultSession.setPermissionRequestHandler((_webContents, _permission, callback) => {
    callback(false);
  });
}

// ---------------------------------------------------------------------------
// Window management
// ---------------------------------------------------------------------------

function getScreenPermissionStatus() {
  if (!isMac) return 'granted';
  try {
    return systemPreferences.getMediaAccessStatus('screen');
  } catch (_) {
    return 'unknown';
  }
}

function quickChatScreenshotMetadata() {
  if (!pendingQuickChatScreenshot) return null;
  const screenshot = pendingQuickChatScreenshot;
  return {
    capturedAt: screenshot.capturedAt,
    displayId: screenshot.displayId,
    width: screenshot.width,
    height: screenshot.height,
    mimeType: screenshot.mimeType,
    size: screenshot.buffer.length,
  };
}

function getQuickChatLaunchContext() {
  return {
    screenshot: quickChatScreenshotMetadata(),
    screenPermissionStatus: getScreenPermissionStatus(),
    desktopSettings: getDesktopSettings(),
  };
}

function notifyQuickChatContextUpdated() {
  if (!quickChatWindow || quickChatWindow.isDestroyed()) return;
  quickChatWindow.webContents.send('quick-chat:context-updated', getQuickChatLaunchContext());
}

function waitForCompositor(ms = 120) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

async function captureQuickChatScreenshot() {
  const permissionStatus = getScreenPermissionStatus();
  if (permissionStatus === 'denied' || permissionStatus === 'restricted') {
    pendingQuickChatScreenshot = null;
    return getQuickChatLaunchContext();
  }

  if (quickChatWindow && !quickChatWindow.isDestroyed() && quickChatWindow.isVisible()) {
    quickChatWindow.hide();
    await waitForCompositor();
  }

  const cursorPoint = screen.getCursorScreenPoint();
  const display = screen.getDisplayNearestPoint(cursorPoint);
  const scaleFactor = Math.max(1, Number(display.scaleFactor) || 1);
  const rawWidth = Math.max(1, Math.round(display.size.width * scaleFactor));
  const rawHeight = Math.max(1, Math.round(display.size.height * scaleFactor));
  const maxDimension = 4096;
  const resizeScale = Math.min(1, maxDimension / Math.max(rawWidth, rawHeight));
  const thumbnailSize = {
    width: Math.max(1, Math.round(rawWidth * resizeScale)),
    height: Math.max(1, Math.round(rawHeight * resizeScale)),
  };

  try {
    const sources = await desktopCapturer.getSources({
      types: ['screen'],
      thumbnailSize,
      fetchWindowIcons: false,
    });
    const displayId = String(display.id);
    const source = sources.find((item) => String(item.display_id || '') === displayId)
      || sources.find((item) => String(item.id || '').startsWith(`screen:${displayId}:`))
      || sources[0];

    if (!source || source.thumbnail.isEmpty()) {
      pendingQuickChatScreenshot = null;
      return {
        ...getQuickChatLaunchContext(),
        screenshotError: 'screen_capture_unavailable',
      };
    }

    const buffer = source.thumbnail.toPNG();
    // Bound the in-memory screenshot. Dimensions are already capped, but a very
    // large display can still produce a big PNG — drop it rather than hold tens
    // of MB resident (and never log the bytes themselves).
    const MAX_SCREENSHOT_BYTES = 32 * 1024 * 1024;
    if (!buffer || buffer.length === 0 || buffer.length > MAX_SCREENSHOT_BYTES) {
      pendingQuickChatScreenshot = null;
      return {
        ...getQuickChatLaunchContext(),
        screenshotError: buffer && buffer.length ? 'screenshot_too_large' : 'screen_capture_unavailable',
      };
    }
    const imageSize = source.thumbnail.getSize();
    pendingQuickChatScreenshot = {
      buffer,
      capturedAt: new Date().toISOString(),
      displayId,
      width: imageSize.width,
      height: imageSize.height,
      mimeType: 'image/png',
    };
    return getQuickChatLaunchContext();
  } catch (err) {
    pendingQuickChatScreenshot = null;
    const message = String((err && err.message) || err || 'screen_capture_failed');
    console.error('[electron] Quick chat screenshot failed:', message);
    appendErrorLog(`[electron] Quick chat screenshot failed: ${message}\n`);
    return {
      ...getQuickChatLaunchContext(),
      screenshotError: message,
    };
  }
}

function positionQuickChatWindow() {
  if (!quickChatWindow || quickChatWindow.isDestroyed()) return;
  const cursorPoint = screen.getCursorScreenPoint();
  const display = screen.getDisplayNearestPoint(cursorPoint);
  const workArea = display.workArea;
  const bounds = quickChatWindow.getBounds();
  const x = Math.round(workArea.x + Math.max(0, (workArea.width - bounds.width) / 2));
  const y = Math.round(workArea.y + Math.max(24, Math.min(96, workArea.height * 0.1)));
  quickChatWindow.setPosition(x, y, false);
}

function installLocalNavigationGuards(window, port, { allowLocalPopups = false } = {}) {
  window.webContents.on('will-navigate', (event, navigationUrl) => {
    try {
      const target = new URL(navigationUrl);
      if (target.hostname !== '127.0.0.1' || target.port !== String(port)) {
        event.preventDefault();
      }
    } catch (_) {
      event.preventDefault();
    }
  });

  window.webContents.setWindowOpenHandler(({ url }) => {
    try {
      const target = new URL(url);
      if (
        allowLocalPopups
        && target.hostname === '127.0.0.1'
        && target.port === String(port)
        && !/\.html?$/i.test(target.pathname)
      ) {
        return { action: 'allow' };
      }
      const isLocalBackend = target.hostname === '127.0.0.1' && target.port === String(port);
      if (!isLocalBackend && (target.protocol === 'https:' || target.protocol === 'http:')) {
        shell.openExternal(url);
      }
    } catch (_) {}
    return { action: 'deny' };
  });
}

async function createQuickChatWindow() {
  if (quickChatWindow && !quickChatWindow.isDestroyed()) {
    if (quickChatWindowReady) await quickChatWindowReady;
    return quickChatWindow;
  }

  const port = await waitForPort();
  if (!port) return null;

  quickChatWindow = new BrowserWindow({
    width: 680,
    // Initial height is a hint only — the renderer measures its content and
    // resizes the window to fit (see the 'quick-chat:resize' handler). minHeight
    // is low so the empty state can shrink to hug its content.
    height: 300,
    minWidth: 560,
    minHeight: 160,
    title: 'Cyrene Quick Chat',
    show: false,
    frame: false,
    resizable: true,
    maximizable: false,
    fullscreenable: false,
    alwaysOnTop: true,
    skipTaskbar: true,
    backgroundColor: '#111418',
    webPreferences: {
      preload: path.join(__dirname, 'preload.js'),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: false,
      backgroundThrottling: false,
    },
  });

  if (isMac) {
    quickChatWindow.setVisibleOnAllWorkspaces(true, { visibleOnFullScreen: true });
  }

  quickChatWindow.on('close', (event) => {
    if (isQuitting) return;
    event.preventDefault();
    quickChatWindow.hide();
    pendingQuickChatScreenshot = null;
  });
  quickChatWindow.on('closed', () => {
    quickChatWindow = null;
    quickChatWindowReady = null;
  });

  installLocalNavigationGuards(quickChatWindow, port);
  quickChatWindowReady = quickChatWindow.loadURL(`http://127.0.0.1:${port}/?surface=quick-chat`);
  await quickChatWindowReady;
  return quickChatWindow;
}

async function openQuickChat() {
  if (quickChatOpenPromise) return quickChatOpenPromise;
  quickChatOpenPromise = (async () => {
    const context = await captureQuickChatScreenshot();
    const window = await createQuickChatWindow();
    if (!window || window.isDestroyed()) return context;
    positionQuickChatWindow();
    window.show();
    window.moveTop();
    window.focus();
    notifyQuickChatContextUpdated();
    return context;
  })();
  try {
    return await quickChatOpenPromise;
  } finally {
    quickChatOpenPromise = null;
  }
}

async function createMainWindow(shellOverride) {
  if (mainWindow && !mainWindow.isDestroyed()) {
    mainWindow.show();
    mainWindow.focus();
    return;
  }

  let port;
  try {
    port = await waitForPort();
  } catch (err) {
    dialog.showErrorBox(
      'Cyrene - Startup Timeout',
      'The Python backend did not start within 30 seconds.\n\n'
      + 'Check cyrene_error.log in your temp directory for details.'
    );
    killPython();
    app.quit();
    return;
  }

  if (!port) {
    // Error already handled in spawnPython (port resolve returned null)
    return;
  }

  // The workbench draws its own top bar and reserves room for the traffic
  // lights, so it uses the frameless inset title bar. The legacy/agent UI has a
  // normal top bar that the inset controls would overlap — keep the native
  // (default) title bar there. Unknown mode falls back to the workbench style.
  const uiShell = shellOverride || backendUiMode || 'workbench';
  const isLegacyShell = uiShell === 'legacy' || uiShell === 'agent';
  // The inset title bar and traffic-light positioning are macOS-specific.
  // Windows and Linux keep their native frame so close/minimize/maximize
  // controls remain available.
  const useInsetTitleBar = !isLegacyShell && isMac;
  const windowOptions = {
    width: 1200,
    height: 800,
    minWidth: 800,
    minHeight: 600,
    title: 'Cyrene',
    show: false,
    webPreferences: {
      preload: path.join(__dirname, 'preload.js'),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: false,
    },
  };
  if (useInsetTitleBar) {
    windowOptions.titleBarStyle = 'hidden';
    windowOptions.trafficLightPosition = { x: 12, y: 19 };
  }
  mainWindow = new BrowserWindow(windowOptions);

  mainWindow.once('ready-to-show', () => {
    if (!launchHidden) {
      mainWindow.show();
    }
    if (isDev) {
      mainWindow.webContents.openDevTools();
    }
  });

  mainWindow.on('close', (event) => {
    if (!isQuitting && appStaysResident()) {
      // Stay resident (hide) so the global quick-chat shortcut keeps working and
      // the backend keeps running. Do NOT kill Python here — a lingering hidden
      // quick-chat window would otherwise be left pointing at a dead backend.
      event.preventDefault();
      mainWindow.hide();
      return;
    }
    // Nothing keeps us resident — let the window close; teardown happens in
    // window-all-closed (non-mac quit) or before-quit (explicit quit).
  });

  mainWindow.on('closed', () => {
    mainWindow = null;
  });

  // Navigate to the local Python server. The legacy/agent UI is selected via
  // the ?shell=legacy param so it renders in this (natively-framed) window even
  // when the backend was launched in workbench mode.
  const url = isLegacyShell
    ? `http://127.0.0.1:${port}/?shell=legacy`
    : `http://127.0.0.1:${port}`;
  // Force clear cache so the app always loads fresh assets
  mainWindow.webContents.session.clearCache();
  mainWindow.loadURL(url);

  installLocalNavigationGuards(mainWindow, port, { allowLocalPopups: true });
}

// Swap the window to a different UI shell at runtime (e.g. the workbench's
// "旧界面" button). titleBarStyle is fixed at creation, so we build a fresh
// window with the right chrome and discard the old one. The new window is
// created BEFORE the old is destroyed, so the window count never hits zero
// (which would fire window-all-closed → killPython). Returning to the new UI
// is a normal app restart.
let isSwitchingShell = false;
async function reopenWindowForShell(uiShell) {
  if (isSwitchingShell) return;
  isSwitchingShell = true;
  try {
    const old = mainWindow;
    const bounds = old && !old.isDestroyed() ? old.getBounds() : null;
    if (old && !old.isDestroyed()) {
      // Drop lifecycle listeners so destroying the old window doesn't
      // hide-to-background or kill the (still-needed) Python backend.
      old.removeAllListeners('close');
      old.removeAllListeners('closed');
    }
    mainWindow = null;
    await createMainWindow(uiShell);
    if (bounds && mainWindow && !mainWindow.isDestroyed()) {
      mainWindow.setBounds(bounds);  // keep the same size/position across the swap
    }
    if (old && !old.isDestroyed()) {
      old.destroy();
    }
  } finally {
    isSwitchingShell = false;
  }
}

// ---------------------------------------------------------------------------
// App lifecycle
// ---------------------------------------------------------------------------

const gotSingleInstanceLock = app.requestSingleInstanceLock();
if (!gotSingleInstanceLock) {
  app.quit();
} else {
  app.on('second-instance', () => {
    launchHidden = false;
    if (mainWindow) {
      if (!mainWindow.isVisible()) mainWindow.show();
      if (mainWindow.isMinimized()) mainWindow.restore();
      mainWindow.focus();
    } else {
      spawnPython();
      createMainWindow();
    }
  });

  app.whenReady().then(() => {
    installAuthHeaderInjector();
    const desktopSettings = readDesktopSettings();
    applyLaunchAtLogin(desktopSettings.launchAtLogin);
    // Only claim the global shortcut when the user has enabled quick chat.
    if (desktopSettings.quickChatEnabled) {
      registerQuickChatShortcut(desktopSettings.quickChatShortcut);
    }
    ipcMain.handle('desktop-settings:get', () => getDesktopSettings());
    ipcMain.handle('desktop-settings:update', (_event, updates) => saveDesktopSettings(updates || {}));
    ipcMain.handle('quick-chat:get-launch-context', () => getQuickChatLaunchContext());
    ipcMain.handle('quick-chat:get-screenshot', () => {
      if (!pendingQuickChatScreenshot) return null;
      return {
        ...quickChatScreenshotMetadata(),
        bytes: pendingQuickChatScreenshot.buffer,
      };
    });
    ipcMain.handle('quick-chat:clear-screenshot', () => {
      pendingQuickChatScreenshot = null;
      notifyQuickChatContextUpdated();
      return { ok: true };
    });
    ipcMain.handle('quick-chat:close', () => {
      pendingQuickChatScreenshot = null;
      if (quickChatWindow && !quickChatWindow.isDestroyed()) quickChatWindow.hide();
      return { ok: true };
    });
    // The quick-chat renderer measures its content and asks for a matching window
    // height, so the surface auto-sizes (no dead space, room for the upward menu).
    // Anchored at the top: the y stays put and the window grows/shrinks downward.
    ipcMain.handle('quick-chat:resize', (_event, info) => {
      if (!quickChatWindow || quickChatWindow.isDestroyed()) return { ok: false };
      const requested = Math.round(Number(info && info.height) || 0);
      if (!requested) return { ok: false };
      const bounds = quickChatWindow.getBounds();
      const workArea = screen.getDisplayNearestPoint({ x: bounds.x, y: bounds.y }).workArea;
      const minH = 200;
      // Never grow past the bottom of the work area.
      const maxH = Math.max(minH, workArea.y + workArea.height - bounds.y - 16);
      const height = Math.max(minH, Math.min(requested, maxH));
      if (Math.abs(height - bounds.height) < 2) return { ok: true };
      quickChatWindow.setBounds({ x: bounds.x, y: bounds.y, width: bounds.width, height }, false);
      return { ok: true };
    });
    ipcMain.handle('quick-chat:notify-sent', (_event, info) => {
      const payload = {
        projectId: String((info && info.projectId) || ''),
        chatId: String((info && info.chatId) || ''),
      };
      if (mainWindow && !mainWindow.isDestroyed()) {
        mainWindow.webContents.send('quick-chat:sent', payload);
      }
      return { ok: true };
    });
    ipcMain.handle('quick-chat:open-screen-settings', async () => {
      if (!isMac) return { ok: false, error: 'unsupported_platform' };
      await shell.openExternal('x-apple.systempreferences:com.apple.preference.security?Privacy_ScreenCapture');
      return { ok: true };
    });
    ipcMain.handle('notification:show', (_event, { title, body }) => {
      const icon = getNotificationIconPath();
      new Notification({ title, body, ...(icon ? { icon } : {}) }).show();
    });
    ipcMain.handle('dialog:pick-directory', async (event) => {
      if (!isLinux) {
        return { path: '', error: 'Native directory picker is only enabled on Linux' };
      }
      const owner = BrowserWindow.fromWebContents(event.sender);
      const result = await dialog.showOpenDialog(owner || mainWindow, {
        title: 'Select workspace directory',
        properties: ['openDirectory', 'createDirectory'],
      });
      if (result.canceled || !result.filePaths.length) {
        return { path: '', cancelled: true };
      }
      return { path: result.filePaths[0] };
    });
    ipcMain.handle('window:switch-shell', (_event, mode) => {
      const target = (mode === 'legacy' || mode === 'agent') ? 'legacy' : 'workbench';
      return reopenWindowForShell(target);
    });
    spawnPython();
    if (!launchHidden) {
      createMainWindow();
    }
  });

  app.on('window-all-closed', () => {
    // Keep the backend alive while the app stays resident for the global
    // shortcut / background mode; otherwise tear it down and quit on non-mac.
    if (appStaysResident()) return;
    killPython();
    if (process.platform !== 'darwin') {
      app.quit();
    }
  });

  app.on('before-quit', () => {
    isQuitting = true;
    globalShortcut.unregisterAll();
    killPython();
  });

  app.on('activate', () => {
    // macOS: re-create window when dock icon is clicked and no windows exist
    launchHidden = false;
    if (mainWindow === null) {
      spawnPython();
      createMainWindow();
    } else {
      mainWindow.show();
      mainWindow.focus();
    }
  });
}
