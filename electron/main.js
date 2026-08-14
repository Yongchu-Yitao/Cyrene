const {
  app,
  BrowserWindow,
  WebContentsView,
  desktopCapturer,
  dialog,
  globalShortcut,
  ipcMain,
  Menu,
  nativeImage,
  Notification,
  screen,
  session,
  shell,
  systemPreferences,
  Tray,
} = require('electron');
const http = require('http');
const path = require('path');
const fs = require('fs');
const os = require('os');
const crypto = require('crypto');
const { spawn } = require('child_process');
const { AppUseManager } = require('./app-use');
const {
  AGENT_CURSOR_FADE_IN_MS,
  AGENT_CURSOR_MOVE_MS,
  AGENT_CURSOR_PRESS_MS,
  agentCursorCommand,
  agentCursorHideCommand,
  agentCursorOverlayHtml,
  agentCursorRunningCommand,
  agentCursorVisualScaleForZoom,
} = require('./agent-cursor');
const { buildBrowserTypeTargetScript } = require('./browser-input');
const { BROWSER_FIND_TARGET_SCRIPT } = require('./browser-target');
const { HostControl } = require('./host-control');

const APP_NAME = 'Cyrene';
const TEMP_ARTIFACT_TTL_MS = 24 * 60 * 60 * 1000;
const BROWSER_UPLOAD_TARGET_TTL_MS = 15 * 60 * 1000;
const BROWSER_UPLOAD_MAX_FILES = 10;
const BROWSER_UPLOAD_MAX_FILE_BYTES = 100 * 1024 * 1024;
const CLI_CONNECTION_FILENAME = 'cli-connection.json';
let _errorLogStream = null;

function getCyreneUserDataDir() {
  if (process.env.CYRENE_USER_DATA_DIR) return process.env.CYRENE_USER_DATA_DIR;
  if (isMac) return path.join(os.homedir(), 'Library', 'Application Support', APP_NAME);
  if (isWindows) return path.join(process.env.APPDATA || path.join(os.homedir(), 'AppData', 'Roaming'), APP_NAME);
  return path.join(process.env.XDG_DATA_HOME || path.join(os.homedir(), '.local', 'share'), APP_NAME);
}

function getCyreneCacheDir() {
  if (process.env.CYRENE_CACHE_DIR) return process.env.CYRENE_CACHE_DIR;
  if (isMac) return path.join(os.homedir(), 'Library', 'Caches', APP_NAME);
  if (isWindows) {
    return path.join(process.env.LOCALAPPDATA || process.env.APPDATA || path.join(os.homedir(), 'AppData', 'Local'), APP_NAME, 'Cache');
  }
  return path.join(process.env.XDG_CACHE_HOME || path.join(os.homedir(), '.cache'), APP_NAME);
}

function getCyreneTempDir() {
  return process.env.CYRENE_TEMP_DIR || path.join(getCyreneCacheDir(), 'tmp');
}

function getErrorLogPath() {
  return path.join(getCyreneTempDir(), 'cyrene_error.log');
}

function getCliConnectionPath() {
  return path.join(getCyreneTempDir(), CLI_CONNECTION_FILENAME);
}

function clearCliConnection() {
  try {
    const target = getCliConnectionPath();
    if (!fs.existsSync(target)) return;
    const payload = JSON.parse(fs.readFileSync(target, 'utf8'));
    if (Number(payload && payload.electronPid) === process.pid) {
      fs.rmSync(target, { force: true });
    }
  } catch (_) {}
}

function publishCliConnection(port) {
  const tempDir = getCyreneTempDir();
  const target = getCliConnectionPath();
  const temporary = `${target}.${process.pid}.tmp`;
  try {
    fs.mkdirSync(tempDir, { recursive: true, mode: 0o700 });
    fs.writeFileSync(temporary, JSON.stringify({
      version: 1,
      url: `http://127.0.0.1:${Number(port)}`,
      token: AUTH_TOKEN,
      electronPid: process.pid,
      backendPid: pythonProcess && pythonProcess.pid ? pythonProcess.pid : null,
    }), { encoding: 'utf8', mode: 0o600 });
    try { fs.chmodSync(temporary, 0o600); } catch (_) {}
    try { fs.rmSync(target, { force: true }); } catch (_) {}
    fs.renameSync(temporary, target);
  } catch (err) {
    try { fs.rmSync(temporary, { force: true }); } catch (_) {}
    console.error('[electron] Failed to publish CLI connection:', err.message);
  }
}

function getErrorLogStream() {
  if (!_errorLogStream) {
    try {
      fs.mkdirSync(getCyreneTempDir(), { recursive: true });
      _errorLogStream = fs.createWriteStream(getErrorLogPath(), { flags: 'a' });
    } catch (_) {}
  }
  return _errorLogStream;
}

function appendErrorLog(text) {
  const s = getErrorLogStream();
  if (s) s.write(text);
}

function installWindowDiagnostics(window, label) {
  if (!window || !window.webContents) return;
  const prefix = `[electron:${label}]`;
  window.webContents.on('console-message', (details, legacyLevel, legacyMessage, legacyLine, legacySourceId) => {
    const level = String(details && details.level || legacyLevel || 'info');
    const reportable = level === 'error' || level === 'warning' || Number(legacyLevel) >= 2;
    if (!reportable) return;
    const message = String(details && details.message || legacyMessage || 'renderer console message');
    const line = Number(details && details.lineNumber || legacyLine || 0);
    const sourceId = String(details && details.sourceId || legacySourceId || '');
    const text = `${prefix} renderer-${level} ${sourceId}${line ? `:${line}` : ''} ${message}\n`;
    appendErrorLog(text);
    if (isDesktopSmokeTest) process.stderr.write(text);
  });
  window.webContents.on('preload-error', (_event, preloadPath, error) => {
    const detail = error && error.stack ? error.stack : String(error || 'unknown preload error');
    const text = `${prefix} preload-error ${preloadPath}: ${detail}\n`;
    appendErrorLog(text);
    if (isDesktopSmokeTest) process.stderr.write(text);
  });
  window.webContents.on('did-fail-load', (_event, code, description, targetUrl, isMainFrame) => {
    // ERR_ABORTED is expected when a navigation is replaced or the app quits.
    if (isMainFrame === false || Number(code) === -3) return;
    appendErrorLog(`${prefix} did-fail-load code=${code} description=${description} url=${targetUrl}\n`);
  });
  window.webContents.on('render-process-gone', (_event, details) => {
    appendErrorLog(`${prefix} render-process-gone ${JSON.stringify(details || {})}\n`);
  });
  window.on('unresponsive', () => {
    appendErrorLog(`${prefix} window became unresponsive\n`);
  });
}

function sha256File(filePath) {
  return new Promise((resolve, reject) => {
    const hash = crypto.createHash('sha256');
    const stream = fs.createReadStream(filePath);
    stream.on('error', reject);
    stream.on('data', (chunk) => hash.update(chunk));
    stream.on('end', () => resolve(hash.digest('hex')));
  });
}

function urlOrigin(value) {
  try {
    return new URL(String(value || '')).origin;
  } catch (_) {
    return '';
  }
}

function cdpAttributes(node) {
  const raw = node && Array.isArray(node.attributes) ? node.attributes : [];
  const attrs = {};
  for (let index = 0; index + 1 < raw.length; index += 2) {
    attrs[String(raw[index] || '').toLowerCase()] = String(raw[index + 1] || '');
  }
  return attrs;
}

function cleanupTemporaryArtifacts(ttlMs = TEMP_ARTIFACT_TTL_MS) {
  const tempDir = getCyreneTempDir();
  try {
    fs.mkdirSync(tempDir, { recursive: true });
    for (const name of fs.readdirSync(tempDir)) {
      if (name === CLI_CONNECTION_FILENAME) continue;
      const target = path.join(tempDir, name);
      try {
        const stat = fs.lstatSync(target);
        const targetTtlMs = name.startsWith('cyrene-browser-upload-')
          ? BROWSER_UPLOAD_TARGET_TTL_MS
          : Math.max(0, Number(ttlMs) || 0);
        const cutoff = Date.now() - targetTtlMs;
        if (stat.mtimeMs > cutoff) continue;
        fs.rmSync(target, { recursive: true, force: true });
      } catch (_) {}
    }
  } catch (_) {}
}

// Desktop-local auth token. Generated once at module load and shared with the
// Python backend via env (CYRENE_AUTH_TOKEN). Injected as the X-Cyrene-Token
// header on every request to the local backend (see installAuthHeaderInjector).
// The renderer never sees this token.
const AUTH_TOKEN = crypto.randomBytes(32).toString('hex');

const isDev = process.env.ELECTRON_DEV === '1';
const isMac = process.platform === 'darwin';
const isWindows = process.platform === 'win32';
const isLinux = process.platform === 'linux';
const supportsLoginItem = process.platform === 'darwin' || process.platform === 'win32';

// Spoken replies are generated asynchronously, after Chromium's transient
// click activation has expired.  Cyrene owns this local audio surface and the
// user explicitly enables or starts speech, so allow the delayed playback and
// automatic-read setting to produce sound reliably.
app.commandLine.appendSwitch('autoplay-policy', 'no-user-gesture-required');

// Chromium aborts with SIGTRAP when its Linux SUID sandbox helper exists but
// is not root-owned with mode 4755. deb/rpm installers repair that permission;
// portable/AppImage or manually copied builds cannot rely on a privileged
// install step, so fall back to Chromium's no-sandbox mode instead of crashing.
if (isLinux) {
  const sandboxPath = path.join(path.dirname(process.execPath), 'chrome-sandbox');
  let hasUsableSuidSandbox = false;
  try {
    const sandboxStat = fs.statSync(sandboxPath);
    hasUsableSuidSandbox = !process.env.APPIMAGE
      && sandboxStat.uid === 0
      && (sandboxStat.mode & 0o4000) !== 0;
  } catch (_) {}
  if (!hasUsableSuidSandbox) {
    app.commandLine.appendSwitch('no-sandbox');
  }
}

// Cyrene's glass and mask-heavy workbench relies on Chromium's GPU compositor
// for responsive scrolling, typing, and window resizing. Keep acceleration on
// by default on Linux, as it is on macOS and Windows. A small number of older
// Mesa, virtual-GPU, or Wayland setups can still produce a blank surface; those
// machines can explicitly opt into the compatibility renderer.
if (isLinux && process.env.CYRENE_DISABLE_HARDWARE_ACCELERATION === '1') {
  app.disableHardwareAcceleration();
}

let mainWindow = null;
let quickChatWindow = null;
let quickChatWindowReady = null;
let quickChatOpenPromise = null;
let pendingQuickChatScreenshot = null;
let registeredQuickChatShortcut = '';
let quickChatShortcutError = '';
let pythonProcess = null;
let isBackendRestarting = false;
let pendingPortResolve = null;
let backendPort = null;
let isShuttingDown = false;
let isQuitting = false;
let quitExtensionCheckInFlight = false;
let quitExtensionDecisionMade = false;
let launchHidden = process.argv.includes('--hidden');
let tray = null;
const browserTabManagers = new Map();
let activeBrowserSessionId = '';
let browserSurfaceObscured = false;
let activeVideoFullscreenManager = null;
let appUseManager = null;
let appUsePointerWindow = null;
let appUsePointerOwnerTargetId = '';
let agentCursorRunning = false;
const agentCursorRunningSources = new Map();
let electronRpcServer = null;
let electronRpcPort = null;
let hostControl = null;

function getHostControl() {
  if (!hostControl) {
    hostControl = new HostControl({
      app,
      screen,
      getMainWindow: () => mainWindow,
      getQuickChatWindow: () => quickChatWindow,
      revealMainWindow,
      openQuickChat,
      getDesktopSettings,
      updateDesktopSettings: saveDesktopSettings,
      lifecycleExecutor: executeApprovedLifecycle,
    });
  }
  return hostControl;
}

function executeApprovedLifecycle(actionId, action, receipt) {
  const id = String(actionId || '');
  const normalized = String(action || '');
  if (!/^host_action_[0-9a-f]{32}$/.test(id)) {
    return { ok: false, error: 'invalid_action_receipt' };
  }
  if (!['restart_backend', 'restart_app', 'quit', 'update_install'].includes(normalized)) {
    return { ok: false, error: 'unsupported_lifecycle_action' };
  }
  if (!receipt || String(receipt.expectedAppVersion || '') !== String(app.getVersion())
      || !/^[0-9a-f]{64}$/.test(String(receipt.parameterHash || ''))) {
    return { ok: false, error: 'invalid_action_receipt' };
  }
  setTimeout(() => {
    if (normalized === 'restart_backend') {
      restartPythonBackend();
      return;
    }
    isQuitting = true;
    if (normalized === 'restart_app') {
      app.relaunch();
    }
    app.quit();
  }, 1000);
  return { ok: true, summary: `accepted ${normalized}` };
}
const isDesktopSmokeTest = process.argv.includes('--desktop-smoke-test');
if (isDesktopSmokeTest) {
  // Keep release smoke tests independent from any resident desktop instance
  // and avoid reading or changing the runner/user's normal Electron profile.
  app.setPath('userData', path.join(getCyreneTempDir(), 'electron-smoke-profile'));
}
const BROWSER_USER_EVENT_CONSOLE_PREFIX = '__CYRENE_BROWSER_USER_EVENT__';
const BROWSER_RESIZE_EDGE_PREFIX = '__CYRENE_RESIZE_EDGE__';

const DEFAULT_DESKTOP_SETTINGS = Object.freeze({
  settingsRevision: 0,
  launchAtLogin: false,
  runInBackground: false,
  language: '',
  // Quick chat (global-shortcut assistant) is opt-in and requires background
  // residency — the global shortcut is only registered when it's enabled.
  quickChatEnabled: false,
  quickChatShortcut: 'CommandOrControl+Shift+Space',
});

function postBackendJson(pathname, payload) {
  if (!backendPort) return;
  const body = JSON.stringify(payload || {});
  const req = http.request({
    hostname: '127.0.0.1',
    port: backendPort,
    path: pathname,
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      'Content-Length': Buffer.byteLength(body),
      'X-Cyrene-Token': AUTH_TOKEN,
    },
    timeout: 3000,
  }, (res) => {
    res.resume();
  });
  req.on('error', () => {});
  req.on('timeout', () => {
    try { req.destroy(); } catch (_) {}
  });
  req.write(body);
  req.end();
}

function requestBackendJson(method, pathname, payload) {
  return new Promise((resolve, reject) => {
    if (!backendPort) {
      reject(new Error('backend unavailable'));
      return;
    }
    const body = payload == null ? '' : JSON.stringify(payload);
    const req = http.request({
      hostname: '127.0.0.1',
      port: backendPort,
      path: pathname,
      method,
      headers: {
        'X-Cyrene-Token': AUTH_TOKEN,
        ...(body ? { 'Content-Type': 'application/json', 'Content-Length': Buffer.byteLength(body) } : {}),
      },
      timeout: 3000,
    }, (res) => {
      const chunks = [];
      res.on('data', (chunk) => chunks.push(chunk));
      res.on('end', () => {
        try {
          const parsed = JSON.parse(Buffer.concat(chunks).toString('utf8') || '{}');
          if ((res.statusCode || 500) >= 400) reject(new Error(parsed.error || `HTTP ${res.statusCode}`));
          else resolve(parsed);
        } catch (error) {
          reject(error);
        }
      });
    });
    req.on('error', reject);
    req.on('timeout', () => req.destroy(new Error('backend request timed out')));
    if (body) req.write(body);
    req.end();
  });
}

async function cancelExtensionTasksAndWait(tasks) {
  await Promise.allSettled(tasks.map((task) => (
    requestBackendJson('POST', `/api/extensions/tasks/${encodeURIComponent(task.id)}/cancel`, {})
  )));
  const deadline = Date.now() + 3000;
  while (Date.now() < deadline) {
    try {
      const payload = await requestBackendJson('GET', '/api/extensions/tasks');
      const pending = (payload.tasks || []).some((task) => (
        ['queued', 'running', 'cancelling'].includes(task.status)
      ));
      if (!pending) return;
    } catch (_) {
      return;
    }
    await new Promise((resolve) => setTimeout(resolve, 150));
  }
}

const DESKTOP_TRANSLATIONS = Object.freeze({
  en: {
    open: 'Open Cyrene',
    quit: 'Quit Cyrene',
    installQuitTitle: 'Extensions are still installing',
    installQuitMessage: 'One or more extension installations are still running.',
    installQuitDetail: 'Wait to keep the installations running, or cancel them before quitting. Interrupted tasks remain visible for a safe retry on the next launch.',
    installQuitWait: 'Wait',
    installQuitCancel: 'Cancel installs and quit',
  },
  zh: {
    open: '打开 Cyrene',
    quit: '退出 Cyrene',
    installQuitTitle: '扩展仍在安装',
    installQuitMessage: '一个或多个扩展安装任务仍在运行。',
    installQuitDetail: '可以等待安装完成，或取消安装后退出。异常中断的任务会保留记录，下次启动时可安全重试。',
    installQuitWait: '等待',
    installQuitCancel: '取消安装并退出',
  },
});

const MENU_TRANSLATIONS = Object.freeze({
  en: {
    about: 'About Cyrene',
    settings: 'Settings…',
    services: 'Services',
    hide: 'Hide Cyrene',
    hideOthers: 'Hide Others',
    showAll: 'Show All',
    quit: 'Quit Cyrene',
    file: 'File',
    newChat: 'New Chat',
    newProject: 'New Project',
    newTask: 'New Task',
    closeWindow: 'Close Window',
    edit: 'Edit',
    undo: 'Undo',
    redo: 'Redo',
    cut: 'Cut',
    copy: 'Copy',
    paste: 'Paste',
    selectAll: 'Select All',
    view: 'View',
    reload: 'Reload',
    forceReload: 'Force Reload',
    toggleDevTools: 'Toggle Developer Tools',
    zoomIn: 'Zoom In',
    zoomOut: 'Zoom Out',
    resetZoom: 'Actual Size',
    toggleTheme: 'Toggle Theme',
    toggleSidebar: 'Toggle Sidebar',
    toggleFullScreen: 'Toggle Full Screen',
    windowMenu: 'Window',
    minimize: 'Minimize',
    zoom: 'Zoom',
    bringAllToFront: 'Bring All to Front',
    help: 'Help',
    documentation: 'Documentation',
    feedback: 'Submit Feedback…',
    workspaceAbout: 'About This Workspace',
  },
  zh: {
    about: '关于 Cyrene',
    settings: '设置…',
    services: '服务',
    hide: '隐藏 Cyrene',
    hideOthers: '隐藏其他',
    showAll: '显示全部',
    quit: '退出 Cyrene',
    file: '文件',
    newChat: '新建对话',
    newProject: '新建项目',
    newTask: '新建任务',
    closeWindow: '关闭窗口',
    edit: '编辑',
    undo: '撤销',
    redo: '重做',
    cut: '剪切',
    copy: '复制',
    paste: '粘贴',
    selectAll: '全选',
    view: '视图',
    reload: '重新加载',
    forceReload: '强制重新加载',
    toggleDevTools: '开发者工具',
    zoomIn: '放大',
    zoomOut: '缩小',
    resetZoom: '实际大小',
    toggleTheme: '切换主题',
    toggleSidebar: '切换侧栏',
    toggleFullScreen: '切换全屏',
    windowMenu: '窗口',
    minimize: '最小化',
    zoom: '缩放',
    bringAllToFront: '全部置于顶层',
    help: '帮助',
    documentation: '使用文档',
    feedback: '提交反馈…',
    workspaceAbout: '关于此工作区',
  },
});

const BROWSER_PARTITION = 'persist:cyrene-browser';
const DEFAULT_BROWSER_VERSION = '147.0.0.0';
const guardedBrowserPartitions = new Set();
const BROWSER_CHAT_OVERLAY_HTML = `<!doctype html>
<html><head><meta charset="utf-8"><style>
  :root { color-scheme: light dark; }
  * { box-sizing: border-box; }
  html, body { width: 100%; height: 100%; margin: 0; overflow: hidden; background: transparent; }
  body { display: flex; flex-direction: column; justify-content: flex-end; align-items: center; gap: 6px; padding: 8px 12px 10px; font-family: Manrope, "Noto Sans SC", system-ui, "Segoe UI", sans-serif; font-synthesis-weight: none; }
  #status { display: none; max-width: min(420px, 100%); min-height: 28px; align-items: center; gap: 8px; padding: 5px 11px; border: 1px solid var(--line, #d8dce4); border-radius: 999px; background: var(--panel, rgba(255,255,255,.96)); color: var(--muted, #6f737b); box-shadow: 0 5px 16px rgba(10,18,32,.12); font-size: 11.5px; font-weight: 650; line-height: 1.2; }
  body.has-status #status { display: flex; }
  #status-dot { width: 7px; height: 7px; flex: 0 0 auto; border-radius: 50%; background: var(--green, #1f9d57); animation: pulse 1.45s ease-out infinite; }
  #status-text { min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  form { width: 100%; height: 44px; display: flex; align-items: center; gap: 8px; padding: 5px 6px 5px 14px; border: 1px solid var(--line, #d8dce4); border-radius: 14px; background: var(--panel, #fff); box-shadow: 0 3px 12px rgba(9,17,30,.04); }
  form:focus-within { border-color: color-mix(in srgb, var(--accent, #6d5dfc) 36%, var(--line, #d8dce4)); box-shadow: 0 0 0 2px color-mix(in srgb, var(--accent, #6d5dfc) 7%, transparent); }
  input { flex: 1 1 auto; min-width: 0; height: 30px; padding: 0; border: 0; outline: 0; background: transparent; color: var(--text, #17191d); font: inherit; font-size: 13px; }
  input::placeholder { color: var(--faint, #9297a1); }
  button { width: 32px; height: 32px; flex: 0 0 auto; display: grid; place-items: center; padding: 0; border: 1px solid color-mix(in srgb, var(--accent, #6d5dfc) 70%, transparent); border-radius: 10px; background: var(--accent, #6d5dfc); color: var(--accent-text, #fff); box-shadow: 0 2px 7px color-mix(in srgb, var(--accent, #6d5dfc) 20%, transparent); cursor: pointer; }
  button:hover:not(:disabled) { background: color-mix(in srgb, var(--accent, #6d5dfc) 88%, white); }
  button.stop { border-color: color-mix(in srgb, var(--red, #d84848) 60%, transparent); background: color-mix(in srgb, var(--red, #d84848) 14%, var(--panel, #fff)); color: var(--red, #d84848); box-shadow: 0 2px 7px color-mix(in srgb, var(--red, #d84848) 16%, transparent); }
  button:disabled { opacity: .42; cursor: default; }
  button svg { width: 15px; height: 15px; fill: none; stroke: currentColor; stroke-width: 2; stroke-linecap: round; stroke-linejoin: round; }
  @media (max-resolution: 1.5dppx) {
    html[data-platform="win32"] body { font-family: "Segoe UI Variable", "Microsoft YaHei UI", "Segoe UI", sans-serif; font-weight: 400; }
    html[data-platform="win32"] #status, html[data-platform="win32"] button { font-weight: 600; }
  }
  @keyframes pulse { 0% { box-shadow: 0 0 0 0 color-mix(in srgb, var(--green, #1f9d57) 34%, transparent); } 70%, 100% { box-shadow: 0 0 0 5px transparent; } }
  body.status-complete #status-dot { animation: none; box-shadow: none; }
  @media (prefers-reduced-motion: reduce) { #status-dot { animation: none; } }
</style></head><body>
  <div id="status" role="status" aria-live="polite"><span id="status-dot"></span><span id="status-text"></span></div>
  <form><input type="text"><button type="submit" aria-label="Send"></button></form>
  <script>
    document.documentElement.dataset.platform = new URLSearchParams(location.search).get('platform') || '';
    const input = document.querySelector('input');
    const button = document.querySelector('button');
    const statusText = document.getElementById('status-text');
    const sendIcon = '<svg viewBox="0 0 24 24"><path d="M12 19V5M5 12l7-7 7 7"/></svg>';
    const stopIcon = '<svg viewBox="0 0 24 24"><rect x="7" y="7" width="10" height="10" rx="1"/></svg>';
    let state = { running: false, sessionId: '' };
    function syncButton() {
      const stopping = state.running && !input.value.trim();
      button.classList.toggle('stop', stopping);
      button.disabled = !state.running && !input.value.trim();
      button.innerHTML = stopping ? stopIcon : sendIcon;
      button.title = stopping ? (state.stopLabel || 'Stop') : (state.running ? (state.guideLabel || 'Send guidance') : (state.sendLabel || 'Send'));
      button.setAttribute('aria-label', button.title);
    }
    input.addEventListener('input', syncButton);
    document.querySelector('form').addEventListener('submit', (event) => {
      event.preventDefault();
      const text = input.value.trim();
      if (!text) {
        if (state.running) window.browserChatOverlay.stop();
        return;
      }
      window.browserChatOverlay.submit(text);
      input.value = '';
      syncButton();
    });
    window.browserChatOverlay.onState((next) => {
      state = next || {};
      document.body.classList.toggle('has-status', !!state.showStatus);
      document.body.classList.toggle('status-complete', !!state.statusComplete);
      statusText.textContent = state.statusText || '';
      input.placeholder = state.running ? (state.placeholderRunning || '') : (state.placeholder || '');
      const colors = state.colors || {};
      Object.keys(colors).forEach((key) => document.documentElement.style.setProperty('--' + key, String(colors[key] || '')));
      syncButton();
    });
  </script>
</body></html>`;

const BROWSER_TAB_PICKER_HTML = `<!doctype html>
<html><head><meta charset="utf-8"><style>
  :root { color-scheme: light dark; }
  * { box-sizing: border-box; }
  html, body { width: 100%; height: 100%; margin: 0; overflow: hidden; background: transparent; }
  body { padding: 6px; font-family: Manrope, "Noto Sans SC", system-ui, "Segoe UI", sans-serif; font-synthesis-weight: none; }
  #menu { width: 100%; height: 100%; display: flex; flex-direction: column; gap: 4px; padding: 7px; overflow: auto; border: 1px solid var(--line, #d8dce4); border-radius: 14px; background: var(--panel, #fff); color: var(--text, #17191d); box-shadow: 0 14px 34px rgba(9,17,30,.18); opacity: 0; transform: translate3d(0,-10px,0) scale(.985); transform-origin: top center; }
  body.open #menu { animation: picker-in 220ms cubic-bezier(.22,1,.36,1) both; }
  body.closing #menu { pointer-events: none; animation: picker-out 150ms cubic-bezier(.4,0,.2,1) both; }
  .row { min-height: 44px; display: flex; flex: 0 0 auto; align-items: center; gap: 4px; padding: 3px; border-radius: 10px; color: var(--muted, #6f737b); }
  .row:hover { background: var(--hover, rgba(23,25,29,.06)); color: var(--text, #17191d); }
  .row.active { background: var(--selected, rgba(23,25,29,.045)); color: var(--text, #17191d); box-shadow: inset 0 0 0 1px var(--line, #d8dce4); }
  button { border: 0; background: transparent; color: inherit; font: inherit; cursor: pointer; }
  button:focus-visible { outline: none; }
  .row:focus-within { background: var(--hover, rgba(23,25,29,.06)); color: var(--text, #17191d); box-shadow: inset 0 0 0 1px var(--line, #d8dce4); }
  .select { min-width: 0; min-height: 38px; display: flex; flex: 1 1 auto; align-items: center; gap: 10px; padding: 5px 8px; text-align: left; }
  .select b { min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; font-size: 12px; font-weight: 650; }
  .favicon { position: relative; width: 22px; height: 22px; flex: 0 0 22px; display: grid; place-items: center; border-radius: 6px; background: var(--hover, rgba(23,25,29,.06)); color: var(--faint, #9297a1); overflow: hidden; }
  .favicon img { position: absolute; inset: 3px; width: 16px; height: 16px; object-fit: contain; }
  .actions { display: flex; flex: 0 0 auto; align-items: center; gap: 2px; }
  .actions button { width: 30px; height: 30px; display: grid; place-items: center; padding: 0; border-radius: 8px; color: var(--faint, #9297a1); }
  .actions button:hover, .actions button.active { background: var(--hover, rgba(23,25,29,.07)); color: var(--text, #17191d); }
  svg { width: 15px; height: 15px; fill: none; stroke: currentColor; stroke-width: 1.9; stroke-linecap: round; stroke-linejoin: round; }
  @media (max-resolution: 1.5dppx) {
    html[data-platform="win32"] body { font-family: "Segoe UI Variable", "Microsoft YaHei UI", "Segoe UI", sans-serif; font-weight: 400; }
    html[data-platform="win32"] .select b, html[data-platform="win32"] .row.active { font-weight: 600; }
  }
  @keyframes picker-in { from { opacity: 0; transform: translate3d(0,-10px,0) scale(.985); } to { opacity: 1; transform: translate3d(0,0,0) scale(1); } }
  @keyframes picker-out { from { opacity: 1; transform: translate3d(0,0,0) scale(1); } to { opacity: 0; transform: translate3d(0,-8px,0) scale(.985); } }
  @media (prefers-reduced-motion: reduce) { body.open #menu { animation-duration: 1ms; } body.closing #menu { animation-duration: 1ms; } }
</style></head><body><div id="menu" role="menu"></div><script>
  document.documentElement.dataset.platform = new URLSearchParams(location.search).get('platform') || '';
  const menu = document.getElementById('menu');
  const icons = {
    tab: '<svg viewBox="0 0 24 24"><rect x="4" y="6" width="16" height="12" rx="2"/><path d="M4 9h16"/></svg>',
    reload: '<svg viewBox="0 0 24 24"><path d="M20 11a8 8 0 1 0-2.3 5.7"/><path d="M20 5v6h-6"/></svg>',
    volume: '<svg viewBox="0 0 24 24"><path d="M11 5 6.5 9H3v6h3.5L11 19Z"/><path d="M15 9a4 4 0 0 1 0 6"/></svg>',
    muted: '<svg viewBox="0 0 24 24"><path d="M11 5 6.5 9H3v6h3.5L11 19Z"/><path d="m16 10 5 5m0-5-5 5"/></svg>',
    close: '<svg viewBox="0 0 24 24"><path d="m7 7 10 10M17 7 7 17"/></svg>'
  };
  let state = { visible: false, tabs: [], activeTabId: '', labels: {}, colors: {} };
  let wasVisible = false;
  function action(type, tabId) { window.browserTabPicker.action(type, tabId || ''); }
  function iconButton(type, tab, label, active) {
    const button = document.createElement('button');
    button.type = 'button';
    button.className = active ? 'active' : '';
    button.dataset.key = type + ':' + String(tab.id || '');
    button.setAttribute('role', 'menuitem');
    button.title = label;
    button.setAttribute('aria-label', label);
    button.innerHTML = icons[type] || '';
    button.addEventListener('click', () => action(type === 'volume' || type === 'muted' ? 'mute' : type, tab.id));
    return button;
  }
  function render() {
    const focusKey = document.activeElement && document.activeElement.dataset
      ? String(document.activeElement.dataset.key || '')
      : '';
    const colors = state.colors || {};
    Object.keys(colors).forEach((key) => document.documentElement.style.setProperty('--' + key, String(colors[key] || '')));
    menu.setAttribute('aria-label', String(state.labels && state.labels.tabs || 'Browser tabs'));
    menu.replaceChildren();
    (Array.isArray(state.tabs) ? state.tabs : []).forEach((tab) => {
      const selected = String(tab.id || '') === String(state.activeTabId || '');
      const row = document.createElement('div');
      row.className = 'row' + (selected ? ' active' : '');
      row.setAttribute('role', 'none');
      const select = document.createElement('button');
      select.type = 'button';
      select.className = 'select';
      select.dataset.key = 'select:' + String(tab.id || '');
      select.setAttribute('role', 'menuitemradio');
      select.setAttribute('aria-checked', selected ? 'true' : 'false');
      const favicon = document.createElement('span');
      favicon.className = 'favicon';
      favicon.innerHTML = icons.tab;
      if (tab.favicon) {
        const image = document.createElement('img');
        image.src = String(tab.favicon);
        image.alt = '';
        image.addEventListener('error', () => image.remove());
        favicon.appendChild(image);
      }
      const title = document.createElement('b');
      title.textContent = String(tab.title || tab.url || state.labels.browser || 'Browser');
      select.append(favicon, title);
      select.addEventListener('click', () => action('select', tab.id));
      const actions = document.createElement('span');
      actions.className = 'actions';
      actions.append(
        iconButton('reload', tab, state.labels.reload || 'Reload', false),
        iconButton(tab.muted ? 'muted' : 'volume', tab, tab.muted ? (state.labels.unmute || 'Unmute') : (state.labels.mute || 'Mute'), !!tab.muted),
        iconButton('close', tab, state.labels.close || 'Close tab', false)
      );
      row.append(select, actions);
      menu.appendChild(row);
    });
    document.body.classList.toggle('closing', !state.visible);
    if (state.visible && !wasVisible) {
      document.body.classList.remove('open', 'closing');
      void menu.offsetWidth;
      requestAnimationFrame(() => {
        document.body.classList.add('open');
        const target = menu.querySelector('.row.active .select') || menu.querySelector('button');
        if (target) target.focus({ preventScroll: true });
      });
    } else if (state.visible) {
      document.body.classList.add('open');
      document.body.classList.remove('closing');
      if (focusKey) {
        const target = Array.from(menu.querySelectorAll('button')).find((button) => button.dataset.key === focusKey);
        if (target) target.focus({ preventScroll: true });
      }
    } else {
      document.body.classList.remove('open');
    }
    wasVisible = !!state.visible;
  }
  window.browserTabPicker.onState((next) => { state = next || state; render(); });
  window.addEventListener('keydown', (event) => {
    if (event.key === 'Escape') { event.preventDefault(); action('dismiss', ''); return; }
    if (!['ArrowDown', 'ArrowUp', 'Home', 'End'].includes(event.key)) return;
    const buttons = Array.from(menu.querySelectorAll('button'));
    if (!buttons.length) return;
    event.preventDefault();
    const current = Math.max(0, buttons.indexOf(document.activeElement));
    const next = event.key === 'Home' ? 0
      : event.key === 'End' ? buttons.length - 1
      : event.key === 'ArrowDown' ? (current + 1) % buttons.length
      : (current - 1 + buttons.length) % buttons.length;
    buttons[next].focus({ preventScroll: true });
  });
  menu.addEventListener('animationend', (event) => { if (!state.visible && event.animationName === 'picker-out') window.browserTabPicker.hiddenReady(); });
  window.browserTabPicker.ready();
</script></body></html>`;

function normalizeBrowserSessionId(value) {
  return String(value || '').trim();
}

function browserUserAgent() {
  const override = String(process.env.CYRENE_BROWSER_USER_AGENT || '').trim();
  if (override) return override;
  const version = String(process.env.CYRENE_BROWSER_VERSION || DEFAULT_BROWSER_VERSION).trim() || DEFAULT_BROWSER_VERSION;
  return `Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/${version} Safari/537.36`;
}

function normalizeBrowserUrl(value) {
  const raw = String(value || '').trim();
  if (!raw) return 'about:blank';
  if (/^(https?:|about:)/i.test(raw)) return raw;
  if (/^[a-z][a-z0-9+.-]*:/i.test(raw)) return raw;
  return `https://${raw}`;
}

function trimBrowserText(text, maxChars = 8000) {
  const value = String(text || '').replace(/\s+/g, ' ').trim();
  const limit = Math.max(0, Number(maxChars) || 0);
  return limit && value.length > limit ? value.slice(0, limit) : value;
}

function browserPageSignal(url, title, text) {
  const compact = `${title || ''} ${text || ''}`.replace(/\s+/g, '').toLowerCase();
  const unavailable = [
    '暂时无法浏览', '暂时无法访问', '内容暂不可用',
    'temporarilyunavailable', 'contentisnotavailable',
  ].some((marker) => compact.includes(marker));
  const alternateAction = [
    '请打开app', '扫码查看', '登录后查看',
    'openintheapp', 'scan', 'signin', 'loginto',
  ].some((marker) => compact.includes(marker));
  if (unavailable && alternateAction) {
    return {
      kind: 'access_gate',
      requiresUserTakeover: false,
      retryAllowed: true,
      maxRetries: 1,
      cooldownMs: 10000,
      message: '页面内容暂不可用；允许一次有冷却时间的恢复尝试，仍失败时请求用户接管。',
    };
  }
  return { kind: 'normal', requiresUserTakeover: false, retryAllowed: true, message: '' };
}

// Embedded browser panes can be as narrow as ~270px; sites then serve
// mobile layouts whose header covers the first feed items, so click
// coordinates land on the header nav instead of the target. Scale the page
// down so the CSS viewport always spans a desktop width: the page renders
// the full desktop layout at full size and the pane displays it shrunk to
// fit. (enableDeviceEmulation is avoided — it SIGSEGVs on Electron 35 +
// macOS 26 with WebContentsView viewport changes, see createView.)
// Click coordinates are CSS pixels while sendInputEvent uses DIP pixels, so
// every input event converts them with the current zoom factor.
const PAGE_CSS_TARGET_WIDTH = 1100;
const PAGE_CSS_MAX_FIT_WIDTH = 1920;
const PAGE_MIN_ZOOM = 0.1;

function validatePngBuffer(buffer) {
  if (!Buffer.isBuffer(buffer) || buffer.length <= 0) {
    throw new Error('screenshot data is empty');
  }
  const signature = Buffer.from('89504e470d0a1a0a', 'hex');
  if (buffer.length < 24 || !buffer.subarray(0, signature.length).equals(signature)) {
    throw new Error('screenshot data is not PNG format');
  }
  if (buffer.subarray(12, 16).toString('ascii') !== 'IHDR') {
    throw new Error('screenshot PNG has no IHDR header');
  }
  const headerWidth = buffer.readUInt32BE(16);
  const headerHeight = buffer.readUInt32BE(20);
  const decoded = nativeImage.createFromBuffer(buffer);
  const size = decoded && !decoded.isEmpty() ? decoded.getSize() : { width: 0, height: 0 };
  if (headerWidth <= 0 || headerHeight <= 0 || size.width <= 0 || size.height <= 0) {
    throw new Error('screenshot PNG cannot be decoded');
  }
  return { byteLength: buffer.length, width: size.width, height: size.height };
}

const BROWSER_VISIBLE_ELEMENTS_SCRIPT = `
(function(maxArg, textArg) {
  const maxElements = Math.max(1, Math.min(200, Number(maxArg) || 80));
  const textLimit = Math.max(20, Math.min(500, Number(textArg) || 160));
  const viewportW = window.innerWidth || document.documentElement.clientWidth || 0;
  const viewportH = window.innerHeight || document.documentElement.clientHeight || 0;
  // data-cyrene-ref is shared with text-links and visible_link_matches, which
  // number independently. Clear every previous stamp so each snapshot's refs
  // are unique; stale refs on off-viewport elements made click_ref resolve
  // the wrong (document-order-first) element.
  for (const el of document.querySelectorAll('[data-cyrene-ref]')) {
    el.removeAttribute('data-cyrene-ref');
  }
  const candidates = [
    ...Array.from(document.querySelectorAll('input,textarea,select,button,a[href],[contenteditable="true"],[role="textbox"],[role="searchbox"],[role="combobox"],[role="button"],[role="link"],[tabindex]')),
    ...Array.from(document.querySelectorAll('summary,label,[role],img,video,section,article,div,span')),
  ];
  const seen = new Set();
  const out = [];
  const cssEscape = (value) => {
    if (window.CSS && typeof window.CSS.escape === 'function') return window.CSS.escape(value);
    return String(value).replace(/[^a-zA-Z0-9_-]/g, '\\\\$&');
  };
  const clean = (value, limit = textLimit) => String(value || '').replace(/\\s+/g, ' ').trim().slice(0, limit);
  const roleOf = (el, tag) => {
    const explicit = clean(el.getAttribute('role'), 60);
    if (explicit) return explicit;
    if (tag === 'a' && el.href) return 'link';
    if (tag === 'button') return 'button';
    if (tag === 'input') {
      const type = String(el.getAttribute('type') || 'text').toLowerCase();
      if (type === 'button' || type === 'submit' || type === 'reset') return 'button';
      if (type === 'file') return 'file-upload';
      return 'textbox';
    }
    if (tag === 'textarea') return 'textbox';
    if (tag === 'select') return 'combobox';
    if (tag === 'img') return 'img';
    if (el.isContentEditable) return 'textbox';
    return '';
  };
  const selectorFor = (el, tag, index) => {
    const id = clean(el.id, 120);
    if (id) return '#' + cssEscape(id);
    const testId = clean(el.getAttribute('data-testid') || el.getAttribute('data-test') || el.getAttribute('data-cy'), 120);
    if (testId) return tag + '[data-testid="' + testId.replace(/"/g, '\\\\"') + '"]';
    const href = clean(el.getAttribute('href'), 180);
    if (tag === 'a' && href) return 'a[href="' + href.replace(/"/g, '\\\\"') + '"]';
    return '[data-cyrene-ref="' + index + '"]';
  };
  const interactiveRect = (el) => {
    if (el.hidden || el.closest('[hidden],[inert],[aria-hidden="true"]')) return null;
    if (typeof el.checkVisibility === 'function' && !el.checkVisibility({
      checkOpacity: true,
      checkVisibilityCSS: true,
      contentVisibilityAuto: true,
    })) return null;
    for (let node = el; node instanceof Element; node = node.parentElement) {
      if (node.hidden || node.hasAttribute('inert')
          || String(node.getAttribute('aria-hidden') || '').toLowerCase() === 'true') return null;
      const style = window.getComputedStyle(node);
      if (!style || style.display === 'none' || style.visibility === 'hidden' || style.visibility === 'collapse'
          || style.contentVisibility === 'hidden' || Number(style.opacity) <= 0.001) return null;
    }
    const rect = el.getBoundingClientRect();
    if (!rect || rect.width <= 0 || rect.height <= 0) return null;
    const left = Math.max(0, rect.left);
    const top = Math.max(0, rect.top);
    const right = Math.min(viewportW, rect.right);
    const bottom = Math.min(viewportH, rect.bottom);
    if (right <= left || bottom <= top) return null;
    const insetX = Math.min(1, (right - left) / 4);
    const insetY = Math.min(1, (bottom - top) / 4);
    const points = [
      [(left + right) / 2, (top + bottom) / 2],
      [left + insetX, top + insetY],
      [right - insetX, top + insetY],
      [left + insetX, bottom - insetY],
      [right - insetX, bottom - insetY],
    ];
    const hittable = points.some(([x, y]) => {
      const hits = typeof document.elementsFromPoint === 'function'
        ? document.elementsFromPoint(x, y)
        : [document.elementFromPoint(x, y)].filter(Boolean);
      return hits.some((hit) => hit === el || el.contains(hit));
    });
    return hittable ? rect : null;
  };
  for (const el of candidates) {
    if (!(el instanceof Element) || seen.has(el)) continue;
    seen.add(el);
    const rect = interactiveRect(el);
    if (!rect) continue;
    const tag = String(el.tagName || '').toLowerCase();
    const role = roleOf(el, tag);
    const disabled = el.matches(':disabled') || String(el.getAttribute('aria-disabled') || '').toLowerCase() === 'true';
    const style = window.getComputedStyle(el);
    const interactive = !disabled && (
      ['a', 'button', 'input', 'textarea', 'select', 'summary'].includes(tag)
      || el.isContentEditable || el.tabIndex >= 0 || typeof el.onclick === 'function'
      || ['button', 'link', 'textbox', 'searchbox', 'combobox', 'checkbox', 'radio', 'switch', 'menuitem', 'tab'].includes(role)
      || (style && style.cursor === 'pointer')
    );
    const inputType = tag === 'input' ? clean(el.getAttribute('type') || 'text', 40).toLowerCase() : '';
    const text = tag === 'input' || tag === 'textarea'
      ? (inputType === 'password' ? '' : clean(el.value))
      : clean(el.innerText || el.textContent || el.getAttribute('value') || el.getAttribute('title') || el.getAttribute('alt'));
    const ariaLabel = clean(el.getAttribute('aria-label') || el.getAttribute('title') || el.getAttribute('alt'));
    const placeholder = clean(el.getAttribute('placeholder'));
    const href = el.href ? String(el.href) : clean(el.getAttribute('href'), 300);
    const src = el.currentSrc || el.src || clean(el.getAttribute('src'), 300);
    const interesting = role || href || placeholder || ariaLabel || tag === 'img' || tag === 'input' || tag === 'textarea' || tag === 'select' || text.length >= 2;
    if (!interesting) continue;
    const ref = 'e' + (out.length + 1);
    el.setAttribute('data-cyrene-ref', String(out.length + 1));
    out.push({
      ref,
      tag,
      role,
      visible: true,
      interactive,
      disabled,
      inputType,
      accept: tag === 'input' ? clean(el.getAttribute('accept'), 240) : '',
      multiple: tag === 'input' && el.hasAttribute('multiple'),
      text,
      ariaLabel,
      placeholder,
      href,
      src: tag === 'img' ? src : '',
      alt: tag === 'img' ? clean(el.getAttribute('alt')) : '',
      selector: selectorFor(el, tag, out.length + 1),
      rect: { x: Math.round(rect.left), y: Math.round(rect.top), w: Math.round(rect.width), h: Math.round(rect.height) },
    });
    if (out.length >= maxElements) break;
  }
  return {
    ok: true,
    url: location.href,
    title: document.title || '',
    text: clean(Array.from(new Set(out.map((item) => item.text).filter(Boolean))).join(' '), 2000),
    viewport: { width: viewportW, height: viewportH, scrollX: window.scrollX || 0, scrollY: window.scrollY || 0 },
    elements: out,
  };
})
`;

function installBrowserSessionGuards(partition = BROWSER_PARTITION) {
  const partitionName = String(partition || BROWSER_PARTITION);
  if (guardedBrowserPartitions.has(partitionName)) return;
  let browserSession = null;
  try {
    browserSession = session.fromPartition(partitionName);
  } catch (_) {
    return;
  }
  guardedBrowserPartitions.add(partitionName);
  // Fullscreen is the only permission granted to arbitrary browser content.
  // Electron routes document.requestFullscreen() through the session
  // permission manager before emitting enter-html-full-screen. Denying it here
  // makes player controls (for example Bilibili's fullscreen button) silently
  // do nothing, so Cyrene never gets a chance to present its own platform-
  // specific fullscreen surface. Camera, microphone, location, capture, etc.
  // remain denied.
  const browserPermissionAllowed = (permission) => permission === 'fullscreen';
  browserSession.setPermissionCheckHandler((_webContents, permission) => (
    browserPermissionAllowed(permission)
  ));
  browserSession.setPermissionRequestHandler((_webContents, permission, callback) => {
    callback(browserPermissionAllowed(permission));
  });
  browserSession.webRequest.onBeforeSendHeaders((details, callback) => {
    details.requestHeaders = {
      ...details.requestHeaders,
      'User-Agent': browserUserAgent(),
      'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
    };
    callback({ requestHeaders: details.requestHeaders });
  });
}

class BrowserTabManager {
  constructor(sessionId = '') {
    this.sessionId = normalizeBrowserSessionId(sessionId);
    // Tabs and navigation state are session-scoped, while browser identity is
    // intentionally shared: every manager uses the historical persistent
    // partition so a login completed in one conversation is available in all.
    this.partition = BROWSER_PARTITION;
    installBrowserSessionGuards(this.partition);
    this.tabs = new Map();
    this.activeTabId = '';
    this.nextTabId = 1;
    this.bounds = { x: 0, y: 0, width: 0, height: 0 };
    this.borderRadius = 0;
    this.pageCornerRadius = 0;
    this.chatOverlayView = null;
    this.chatOverlayParent = null;
    this.chatOverlayState = { visible: false, running: false, showStatus: false };
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
    this.visible = false;
    this.obscured = browserSurfaceObscured;
    this.zoomEnabled = true;
    this.resizeEdgeHintEnabled = false;
    this.resizeEdgeHintActive = false;
    this.attachedTabId = '';
    this.attachedWindow = null;
    this._syncTimer = null;
    this._repaintTimer = null;
    this._boundsTransitionToken = 0;
    this._boundsTransitioning = false;
    this._pageZoomTokenByContents = new Map();
    this.videoFullscreen = { active: false, external: false, tabId: '' };
    this.videoFullscreenWindow = null;
    this._videoFullscreenWindowClosing = false;
    this._mainWindowWasFullScreen = false;
    this._fullscreenResizeHandler = null;
    this._mainFullscreenLeaveHandler = null;
    this.browserContext = { sessionId: this.sessionId, roundId: '' };
    this.activeAgentRoundId = '';
    this.agentOwnedTabIdsByRound = new Map();
    this.latestSnapshot = null;
    this.agentCursorRunning = agentCursorRunning;
  }

  invalidateSnapshot() {
    this.latestSnapshot = null;
  }

  async showAgentCursor(tab, x, y, { press = false, moveDurationMs = AGENT_CURSOR_MOVE_MS } = {}) {
    if (
      !tab || tab.id !== this.activeTabId || !this.visible || this.obscured
      || !tab.view || !tab.view.webContents || tab.view.webContents.isDestroyed()
    ) return null;
    let dipWidth = 0;
    try { dipWidth = Math.max(0, Math.round(Number(tab.view.getBounds().width) || 0)); } catch (_) {}
    const zoom = await this.pageZoomOf(tab.view.webContents, dipWidth);
    if (
      tab.id !== this.activeTabId || !this.visible || this.obscured
      || tab.view.webContents.isDestroyed()
    ) return null;
    // The cursor lives in the page DOM, so Chromium's page zoom would also
    // shrink it in a narrow browser pane. Counter-scale only the cursor art;
    // its CSS target coordinates and the trusted click coordinates stay intact.
    const visualScale = agentCursorVisualScaleForZoom(zoom);
    return tab.view.webContents.executeJavaScript(agentCursorCommand({
      x, y, press, moveDurationMs, running: this.agentCursorRunning, visualScale,
    }), true).catch(() => null);
  }

  setAgentCursorRunning(running) {
    this.agentCursorRunning = running === true;
    const command = agentCursorRunningCommand(this.agentCursorRunning);
    return Promise.all(Array.from(this.tabs.values()).map((tab) => {
      if (!tab || !tab.view || !tab.view.webContents || tab.view.webContents.isDestroyed()) return false;
      return tab.view.webContents.executeJavaScript(command, true).catch(() => false);
    }));
  }

  async hideAgentCursor(tab, sequence = null) {
    if (!tab || !tab.view || !tab.view.webContents || tab.view.webContents.isDestroyed()) return false;
    return tab.view.webContents.executeJavaScript(agentCursorHideCommand(sequence), true).catch(() => false);
  }

  hideAllAgentCursors() {
    for (const tab of this.tabs.values()) this.hideAgentCursor(tab).catch(() => {});
  }

  ownerWindow() {
    return mainWindow && !mainWindow.isDestroyed() ? mainWindow : null;
  }

  surfaceWindow() {
    if (
      this.videoFullscreen.active
      && this.videoFullscreen.external
      && this.videoFullscreenWindow
      && !this.videoFullscreenWindow.isDestroyed()
    ) {
      return this.videoFullscreenWindow;
    }
    return this.ownerWindow();
  }

  fullscreenTab() {
    if (!this.videoFullscreen.active) return null;
    return this.tabs.get(this.videoFullscreen.tabId) || null;
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
    this.syncAttachedView();
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
    const tab = this._tabForView(view);
    if (!tab || !view || view.webContents.isDestroyed()) return;
    if (activeVideoFullscreenManager && activeVideoFullscreenManager !== this) {
      activeVideoFullscreenManager.requestVideoFullscreenExit();
    }
    activeVideoFullscreenManager = this;
    this.activeTabId = tab.id;
    this._mainWindowWasFullScreen = !!(
      mainWindow && !mainWindow.isDestroyed() && mainWindow.isFullScreen()
    );
    this.videoFullscreen = {
      active: true,
      external: isMac,
      tabId: tab.id,
    };

    if (isMac) {
      const display = mainWindow && !mainWindow.isDestroyed()
        ? screen.getDisplayMatching(mainWindow.getBounds())
        : screen.getPrimaryDisplay();
      const displayBounds = display && display.bounds ? display.bounds : {};
      const videoWindow = new BrowserWindow({
        x: Number(displayBounds.x) || 0,
        y: Number(displayBounds.y) || 0,
        width: Math.max(640, Number(displayBounds.width) || 1280),
        height: Math.max(360, Number(displayBounds.height) || 720),
        title: 'Cyrene Video',
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
        if (this._videoFullscreenWindowClosing || isQuitting) return;
        event.preventDefault();
        this.requestVideoFullscreenExit();
      });
      videoWindow.on('closed', () => {
        if (this.videoFullscreenWindow === videoWindow) this.videoFullscreenWindow = null;
      });
      videoWindow.setMenuBarVisibility(false);
      videoWindow.show();
      videoWindow.setFullScreen(true);
      if (mainWindow && !mainWindow.isDestroyed() && !this._mainWindowWasFullScreen && mainWindow.isFullScreen()) {
        mainWindow.setFullScreen(false);
      }
    } else if ((isWindows || isLinux) && mainWindow && !mainWindow.isDestroyed()) {
      this._fullscreenResizeHandler = () => this.syncVideoFullscreenBounds();
      this._mainFullscreenLeaveHandler = () => {
        this.syncVideoFullscreenBounds();
        if (this.videoFullscreen.active && !this.videoFullscreen.external) {
          this.requestVideoFullscreenExit();
        }
      };
      mainWindow.on('resize', this._fullscreenResizeHandler);
      mainWindow.on('enter-full-screen', this._fullscreenResizeHandler);
      mainWindow.on('leave-full-screen', this._mainFullscreenLeaveHandler);
      if (!mainWindow.isFullScreen()) mainWindow.setFullScreen(true);
    }

    this.syncAttachedView();
    this.emitState();
    setTimeout(() => this.syncVideoFullscreenBounds(), 80);
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

    if (this._fullscreenResizeHandler) {
      if (externalWindow && !externalWindow.isDestroyed()) {
        externalWindow.removeListener('resize', this._fullscreenResizeHandler);
        externalWindow.removeListener('enter-full-screen', this._fullscreenResizeHandler);
      }
      if (mainWindow && !mainWindow.isDestroyed()) {
        mainWindow.removeListener('resize', this._fullscreenResizeHandler);
        mainWindow.removeListener('enter-full-screen', this._fullscreenResizeHandler);
      }
      this._fullscreenResizeHandler = null;
    }
    if (this._mainFullscreenLeaveHandler) {
      if (mainWindow && !mainWindow.isDestroyed()) {
        mainWindow.removeListener('leave-full-screen', this._mainFullscreenLeaveHandler);
      }
      this._mainFullscreenLeaveHandler = null;
    }

    if (wasExternal && externalWindow && !externalWindow.isDestroyed()) {
      this._videoFullscreenWindowClosing = true;
      try { externalWindow.contentView.removeChildView(tab && tab.view); } catch (_) {}
      externalWindow.destroy();
      this.videoFullscreenWindow = null;
      this._videoFullscreenWindowClosing = false;
    } else if (!wasExternal && mainWindow && !mainWindow.isDestroyed() && !this._mainWindowWasFullScreen) {
      mainWindow.setFullScreen(false);
    }

    this.attachedTabId = '';
    this.attachedWindow = null;
    this.syncAttachedView();
    this.emitState();
  }

  createView() {
    const view = new WebContentsView({
      webPreferences: {
        partition: this.partition,
        contextIsolation: true,
        nodeIntegration: false,
        sandbox: true,
        backgroundThrottling: true,
        // Cyrene owns the platform-specific fullscreen presentation. Prevent
        // Chromium's HTML fullscreen request from resizing whichever window
        // currently hosts this view before we can move/expand it ourselves.
        disableHtmlFullscreenWindowResize: true,
      },
    });
    const wc = view.webContents;
    wc.setUserAgent(browserUserAgent());
    // enableDeviceEmulation is intentionally omitted — it triggers SIGSEGV on
    // Electron 35 + macOS 26 when combined with WebContentsView viewport changes.
    // The UA override alone serves mobile content to most sites.
    wc.setWindowOpenHandler(({ url }) => {
      const opener = this._tabForView(view);
      const agentOwnerRoundId = opener && opener.agentClickInFlight
        ? String(opener.agentOwnerRoundId || this.browserContext.roundId || '')
        : '';
      this.createTab({ url, activate: true, agentOwnerRoundId }).catch((err) => {
        console.error('[electron] Failed to open browser popup tab:', err);
      });
      return { action: 'deny' };
    });
    const update = () => this.emitState();
    wc.on('did-start-loading', () => {
      update();
      const tab = this._tabForView(view);
      if (tab) this.hideAgentCursor(tab).catch(() => {});
    });
    wc.on('did-stop-loading', update);
    wc.on('did-navigate', update);
    wc.on('did-navigate-in-page', update);
    wc.on('did-navigate', () => this.invalidateSnapshot());
    wc.on('did-navigate-in-page', () => this.invalidateSnapshot());
    wc.on('page-title-updated', update);
    wc.on('page-favicon-updated', (_event, favicons) => {
      const tab = this._tabForView(view);
      if (!tab) return;
      tab.favicon = String(Array.isArray(favicons) && favicons[0] || '');
      update();
    });
    wc.on('media-started-playing', update);
    wc.on('media-paused', update);
    wc.on('focus', () => {
      if (this.tabPickerState.visible) this.dismissTabPicker(true);
    });
    wc.on('before-input-event', (_event, input) => {
      if (this.tabPickerState.visible && String(input && input.key || '') === 'Escape') {
        this.dismissTabPicker(true);
      }
    });
    wc.on('enter-html-full-screen', () => {
      this.enterVideoFullscreen(view).catch((err) => {
        console.error('[electron] Failed to enter browser video fullscreen:', err);
      });
    });
    wc.on('leave-html-full-screen', () => {
      this.finishVideoFullscreen(view);
    });
    wc.on('did-fail-load', (_event, code, desc, url) => {
      if (code === -3) return; // aborted by a new navigation
      console.warn(`[electron] Browser tab load failed (${code}) ${url}: ${desc}`);
      update();
    });
    wc.on('did-finish-load', () => {
      this.applyPageFrameStyle(view, undefined, true);
      this.installUserEventCapture(view).catch(() => {});
      // Navigation may reset the page zoom; re-converge on the desktop-width
      // CSS viewport so snapshots and clicks stay in a stable coordinate space.
      const tab = this._tabForView(view);
      if (tab) {
        let bounds = this.bounds;
        try { bounds = tab.view.getBounds(); } catch (_) {}
        this.applyPageZoom(view, bounds, this.zoomEnabled !== false).catch(() => {});
      }
    });
    wc.on('console-message', (details) => {
      this.handleCapturedUserEvent(view, String(details && details.message || ''));
    });
    wc.debugger.on('message', (_event, method, params) => {
      if (method !== 'Page.fileChooserOpened') return;
      const tab = this._tabForView(view);
      if (!tab) return;
      // Page.enable asks Chromium to report chooser events even while native
      // interception is off. Ignore those user-driven events completely.
      if (typeof tab.agentFileChooserResolver !== 'function') return;
      this._captureFileChooser(tab, params || {}).catch((err) => {
        console.warn('[electron] Failed to capture browser file chooser:', err);
        if (typeof tab.agentFileChooserResolver === 'function') {
          tab.agentFileChooserResolver({
            error: 'The file chooser was blocked because its input target could not be verified: ' + String((err && err.message) || err),
            code: 'FILE_CHOOSER_TARGET_UNVERIFIED',
          });
        }
      });
    });
    wc.debugger.on('detach', () => {
      const tab = this._tabForView(view);
      if (tab) tab.debuggerReady = false;
    });
    wc.on('destroyed', () => {
      const destroyedTab = this._tabForView(view);
      const destroyedFullscreenTab = !!(
        destroyedTab
        && this.videoFullscreen.active
        && this.videoFullscreen.tabId === destroyedTab.id
      );
      for (const [id, tab] of this.tabs.entries()) {
        if (tab.view === view) this.tabs.delete(id);
      }
      if (destroyedFullscreenTab) this.finishVideoFullscreen(view);
      if (this.activeTabId && !this.tabs.has(this.activeTabId)) {
        this.activeTabId = this.tabs.keys().next().value || '';
      }
      this.attachedTabId = this.attachedTabId === this.activeTabId ? this.attachedTabId : '';
      this.emitState();
    });
    return view;
  }

  setContext(info = {}) {
    const roundId = String(info.roundId || info.round_id || '').trim();
    this.browserContext.roundId = roundId;
    return this.state();
  }

  _agentTabs() {
    return Array.from(this.tabs.values()).filter((tab) => tab.agentCreated === true);
  }

  _recordAgentTab(tab, roundId) {
    const normalized = String(roundId || '').trim();
    if (!tab || !normalized) return;
    if (!this.agentOwnedTabIdsByRound.has(normalized)) {
      this.agentOwnedTabIdsByRound.set(normalized, new Set());
    }
    this.agentOwnedTabIdsByRound.get(normalized).add(tab.id);
  }

  _reduceAgentTabs(candidates, { activateKept = false } = {}) {
    const tabs = Array.from(candidates || []).filter((tab) => tab && this.tabs.has(tab.id));
    if (!tabs.length) return { keptTab: null, closedTabIds: [] };
    const active = tabs.find((tab) => tab.id === this.activeTabId);
    const keptTab = active || tabs[tabs.length - 1];
    const closedTabIds = [];
    for (const tab of tabs) {
      if (tab.id === keptTab.id) continue;
      closedTabIds.push(tab.id);
      this.closeTab(tab.id);
    }
    if (activateKept && keptTab.id !== this.activeTabId && this.tabs.has(keptTab.id)) {
      this.activateTab(keptTab.id);
    }
    return { keptTab, closedTabIds };
  }

  beginAgentRound(roundId) {
    const normalized = String(roundId || '').trim();
    if (!normalized) return this.state();
    this.browserContext.roundId = normalized;
    if (this.activeAgentRoundId === normalized) return this.state();
    // A process interruption may skip finishAgentRound. Collapse stale
    // agent-created tabs before assigning the reusable survivor to this run.
    const { keptTab } = this._reduceAgentTabs(this._agentTabs(), { activateKept: true });
    if (keptTab) {
      keptTab.agentOwnerRoundId = normalized;
      keptTab.lastAgentRoundId = normalized;
      this._recordAgentTab(keptTab, normalized);
    }
    for (const ownedRoundId of this.agentOwnedTabIdsByRound.keys()) {
      if (ownedRoundId !== normalized) this.agentOwnedTabIdsByRound.delete(ownedRoundId);
    }
    this.activeAgentRoundId = normalized;
    this.emitState();
    return this.state();
  }

  finishAgentRound(roundId) {
    const normalized = String(roundId || '').trim();
    if (!normalized) return { ok: false, error: 'roundId is required.' };
    const recordedIds = this.agentOwnedTabIdsByRound.get(normalized) || new Set();
    const ownedTabs = this._agentTabs().filter((tab) => (
      tab.agentOwnerRoundId === normalized || recordedIds.has(tab.id)
    ));
    const { keptTab, closedTabIds } = this._reduceAgentTabs(ownedTabs, { activateKept: false });
    if (keptTab) {
      keptTab.agentOwnerRoundId = '';
      keptTab.lastAgentRoundId = normalized;
    }
    if (this.activeAgentRoundId === normalized) {
      this.activeAgentRoundId = '';
      if (this.browserContext.roundId === normalized) this.browserContext.roundId = '';
    }
    this.agentOwnedTabIdsByRound.delete(normalized);
    this.emitState();
    return {
      ok: true,
      sessionId: this.sessionId,
      roundId: normalized,
      keptTabId: keptTab ? keptTab.id : '',
      closedTabIds,
      state: this.state(),
    };
  }

  _tabForView(view) {
    for (const tab of this.tabs.values()) {
      if (tab.view === view) return tab;
    }
    return null;
  }

  async _ensureDebugger(tab) {
    const wc = tab && tab.view && tab.view.webContents;
    if (!wc || wc.isDestroyed()) throw new Error('Browser tab is unavailable.');
    if (!wc.debugger.isAttached()) wc.debugger.attach('1.3');
    if (!tab.debuggerReady) {
      await wc.debugger.sendCommand('Page.enable', { enableFileChooserOpenedEvent: true });
      await wc.debugger.sendCommand('DOM.enable');
      tab.debuggerReady = true;
    }
    return wc.debugger;
  }

  async _setFileChooserInterception(tab, enabled) {
    const debug = await this._ensureDebugger(tab);
    await debug.sendCommand('Page.setInterceptFileChooserDialog', {
      enabled: enabled === true,
      cancel: false,
    });
  }

  async _frameState(tab, frameId = '') {
    const debug = await this._ensureDebugger(tab);
    const result = await debug.sendCommand('Page.getFrameTree');
    const visit = (tree) => {
      if (!tree || !tree.frame) return null;
      if (!frameId || String(tree.frame.id || '') === String(frameId)) {
        return {
          id: String(tree.frame.id || ''),
          url: String(tree.frame.url || ''),
          loaderId: String(tree.frame.loaderId || ''),
        };
      }
      for (const child of tree.childFrames || []) {
        const found = visit(child);
        if (found) return found;
      }
      return null;
    };
    return visit(result && result.frameTree);
  }

  async _frameUrl(tab, frameId = '') {
    const frame = await this._frameState(tab, frameId);
    return frame ? frame.url : '';
  }

  async _describeUploadTarget(tab, backendNodeId, { chooserId = '', frameId = '', mode = '' } = {}) {
    const debug = await this._ensureDebugger(tab);
    const described = await debug.sendCommand('DOM.describeNode', {
      backendNodeId: Number(backendNodeId),
      depth: 0,
      pierce: true,
    });
    const node = described && described.node;
    const attrs = cdpAttributes(node);
    if (!node || String(node.nodeName || '').toLowerCase() !== 'input' || String(attrs.type || '').toLowerCase() !== 'file') {
      throw new Error('The intercepted target is not a file input.');
    }
    const topUrl = tab.view.webContents.getURL();
    const frameState = await this._frameState(tab, frameId);
    const frameUrl = (frameState && frameState.url) || topUrl;
    const frameLoaderId = String(frameState && frameState.loaderId || '');
    const stableKey = [tab.id, topUrl, frameUrl, frameLoaderId, String(node.backendNodeId || backendNodeId)].join('\n');
    const targetId = 'upload_' + crypto.createHash('sha256').update(stableKey, 'utf8').digest('hex').slice(0, 24);
    const target = {
      id: targetId,
      tabId: tab.id,
      chooserId: String(chooserId || ''),
      backendNodeId: Number(node.backendNodeId || backendNodeId),
      frameId: String(frameId || ''),
      mode: String(mode || (Object.prototype.hasOwnProperty.call(attrs, 'multiple') ? 'selectMultiple' : 'selectSingle')),
      multiple: mode === 'selectMultiple' || Object.prototype.hasOwnProperty.call(attrs, 'multiple'),
      accept: String(attrs.accept || ''),
      name: String(attrs.name || ''),
      ariaLabel: String(attrs['aria-label'] || ''),
      topUrl,
      frameUrl,
      frameLoaderId,
      origin: urlOrigin(frameUrl) || urlOrigin(topUrl),
      createdAt: Date.now(),
    };
    tab.uploadTargets.set(targetId, target);
    return target;
  }

  _publicUploadTarget(target) {
    return {
      id: target.id,
      tabId: target.tabId,
      chooserId: target.chooserId || '',
      mode: target.mode,
      multiple: !!target.multiple,
      accept: target.accept || '',
      name: target.name || '',
      ariaLabel: target.ariaLabel || '',
      topUrl: target.topUrl,
      frameUrl: target.frameUrl,
      frameLoaderId: target.frameLoaderId || '',
      origin: target.origin,
    };
  }

  _pruneUploadTargets(tab) {
    const cutoff = Date.now() - BROWSER_UPLOAD_TARGET_TTL_MS;
    for (const [id, target] of tab.uploadTargets || []) {
      if (Number(target.createdAt || 0) < cutoff) tab.uploadTargets.delete(id);
    }
    for (const [id, chooser] of tab.fileChoosers || []) {
      if (Number(chooser.createdAt || 0) < cutoff) tab.fileChoosers.delete(id);
    }
  }

  async _captureFileChooser(tab, params) {
    const backendNodeId = Number(params && params.backendNodeId);
    if (!Number.isFinite(backendNodeId) || backendNodeId <= 0) {
      throw new Error('File chooser did not expose an input node.');
    }
    const chooserId = 'chooser_' + crypto.randomBytes(12).toString('hex');
    const target = await this._describeUploadTarget(tab, backendNodeId, {
      chooserId,
      frameId: String(params.frameId || ''),
      mode: String(params.mode || ''),
    });
    const chooser = {
      id: chooserId,
      targetId: target.id,
      createdAt: Date.now(),
    };
    tab.fileChoosers.set(chooserId, chooser);
    tab.lastAgentFileChooser = chooser;
    if (typeof tab.agentFileChooserResolver === 'function') {
      tab.agentFileChooserResolver(this._publicUploadTarget(target));
    }
  }

  async _targetFromRef(tab, ref) {
    const normalized = String(ref || '').trim().replace(/^e/i, '');
    if (!/^\d+$/.test(normalized)) throw new Error('Invalid browser element ref.');
    const debug = await this._ensureDebugger(tab);
    const expression = `document.querySelector('[data-cyrene-ref="${normalized}"]')`;
    const evaluated = await debug.sendCommand('Runtime.evaluate', {
      expression,
      returnByValue: false,
      silent: true,
    });
    const remote = evaluated && evaluated.result;
    if (!remote || !remote.objectId || remote.subtype === 'null') {
      throw new Error('Browser file input ref was not found. Take a new browser_snapshot and retry.');
    }
    try {
      const described = await debug.sendCommand('DOM.describeNode', { objectId: remote.objectId, depth: 0 });
      const node = described && described.node;
      if (!node || !node.backendNodeId) throw new Error('Unable to resolve browser file input.');
      return await this._describeUploadTarget(tab, node.backendNodeId, {});
    } finally {
      debug.sendCommand('Runtime.releaseObject', { objectId: remote.objectId }).catch(() => {});
    }
  }

  _markAgentInput(tab, ms = 1500) {
    if (!tab) return;
    tab.suppressUserEventsUntil = Date.now() + Math.max(0, Number(ms) || 0);
  }

  _shouldSuppressCapturedEvent(tab) {
    return !!(tab && Number(tab.suppressUserEventsUntil || 0) > Date.now());
  }

  async installUserEventCapture(view) {
    if (!view || !view.webContents || view.webContents.isDestroyed()) return;
    const wc = view.webContents;
    const script = `
      (() => {
        if (window.__cyreneBrowserUserCaptureInstalled) return true;
        window.__cyreneBrowserUserCaptureInstalled = true;
        const prefix = ${JSON.stringify(BROWSER_USER_EVENT_CONSOLE_PREFIX)};
        const resizeEdgeCursorStyle = document.createElement("style");
        resizeEdgeCursorStyle.setAttribute("data-cyrene-resize-edge-cursor", "");
        resizeEdgeCursorStyle.textContent =
          'html[data-cyrene-resize-edge-active], ' +
          'html[data-cyrene-resize-edge-active] * { cursor: col-resize !important; }';
        document.documentElement.appendChild(resizeEdgeCursorStyle);
        const resizeEdgePrefix = ${JSON.stringify(BROWSER_RESIZE_EDGE_PREFIX)};
        let resizeEdgeHintEnabled = ${JSON.stringify(this.resizeEdgeHintEnabled === true)};
        let resizeEdgeHintLocal = false;
        let resizeEdgeHintExternal = false;
        const renderResizeEdgeHint = () => {
          const active = resizeEdgeHintEnabled && (resizeEdgeHintLocal || resizeEdgeHintExternal);
          document.documentElement.toggleAttribute("data-cyrene-resize-edge-active", active);
        };
        const showResizeEdgeHint = (show) => {
          const next = resizeEdgeHintEnabled && show === true;
          if (next === resizeEdgeHintLocal) return;
          resizeEdgeHintLocal = next;
          console.info(resizeEdgePrefix + (next ? "in" : "out"));
          renderResizeEdgeHint();
        };
        window.__cyreneSetResizeEdgeHint = (enabled, externalActive) => {
          resizeEdgeHintEnabled = enabled === true;
          resizeEdgeHintExternal = externalActive === true;
          if (!resizeEdgeHintEnabled) showResizeEdgeHint(false);
          else renderResizeEdgeHint();
        };
        document.addEventListener("mousemove", (event) => {
          showResizeEdgeHint(event.clientX < 14);
        }, { passive: true, capture: true });
        document.addEventListener("mouseout", (event) => {
          if (!event.relatedTarget) showResizeEdgeHint(false);
        }, true);
        window.addEventListener("blur", () => showResizeEdgeHint(false));
        const clean = (value, limit = 240) => String(value == null ? "" : value).replace(/\\s+/g, " ").trim().slice(0, limit);
        const stableSelector = (el) => {
          if (!el || !(el instanceof Element)) return "";
          const escape = (value) => window.CSS && CSS.escape ? CSS.escape(String(value)) : String(value).replace(/[^a-zA-Z0-9_-]/g, "\\\\$&");
          if (el.id) return "#" + escape(el.id);
          for (const attr of ["data-testid", "data-test", "name"]) {
            const value = el.getAttribute && el.getAttribute(attr);
            if (value) return el.tagName.toLowerCase() + "[" + attr + "=\\\"" + escape(value) + "\\\"]";
          }
          const role = el.getAttribute && el.getAttribute("role");
          const aria = el.getAttribute && el.getAttribute("aria-label");
          if (role && aria) return "[role=\\\"" + escape(role) + "\\\"][aria-label=\\\"" + escape(aria) + "\\\"]";
          return "";
        };
        const describe = (el) => {
          if (!el || !(el instanceof Element)) return {};
          const rect = el.getBoundingClientRect ? el.getBoundingClientRect() : null;
          const tag = clean(el.tagName || "").toLowerCase();
          const inputType = clean(el.getAttribute && el.getAttribute("type") || "").toLowerCase();
          return {
            tag,
            type: inputType,
            id: clean(el.id || ""),
            name: clean(el.getAttribute && el.getAttribute("name") || ""),
            role: clean(el.getAttribute && el.getAttribute("role") || ""),
            ariaLabel: clean(el.getAttribute && el.getAttribute("aria-label") || ""),
            placeholder: clean(el.getAttribute && el.getAttribute("placeholder") || ""),
            text: clean(el.innerText || el.textContent || el.getAttribute && (el.getAttribute("aria-label") || el.getAttribute("title") || el.getAttribute("placeholder")) || "", 120),
            selector: clean(stableSelector(el), 240),
            x: rect ? Math.round(rect.left) : null,
            y: rect ? Math.round(rect.top) : null,
            w: rect ? Math.round(rect.width) : null,
            h: rect ? Math.round(rect.height) : null,
          };
        };
        const emit = (kind, payload, target) => {
          try {
            console.info(prefix + JSON.stringify({
              kind,
              payload: payload || {},
              target: target || {},
              url: location.href,
              title: document.title || "",
              ts: Date.now(),
            }));
          } catch (_) {}
        };
        document.addEventListener("click", (event) => {
          emit("click", {
            x: Math.round(event.clientX || 0),
            y: Math.round(event.clientY || 0),
            button: event.button || 0,
          }, describe(event.target));
        }, true);
        document.addEventListener("input", (event) => {
          const el = event.target;
          const target = describe(el);
          const isPassword = target.type === "password";
          let value = "";
          if (!isPassword && el && "value" in el) value = clean(el.value, 200);
          else if (!isPassword && el && el.isContentEditable) value = clean(el.textContent, 200);
          emit("input", {
            inputType: clean(event.inputType || ""),
            value: isPassword ? "[redacted-password]" : value,
          }, target);
        }, true);
        document.addEventListener("submit", (event) => {
          emit("submit", {}, describe(event.target));
        }, true);
        let lastScroll = 0;
        document.addEventListener("scroll", (event) => {
          const now = Date.now();
          if (now - lastScroll < 500) return;
          lastScroll = now;
          const scrollTarget = event.target === document
            ? document.scrollingElement
            : event.target;
          emit("scroll", {
            scrollLeft: Math.round(scrollTarget && scrollTarget.scrollLeft || 0),
            scrollTop: Math.round(scrollTarget && scrollTarget.scrollTop || 0),
            scrollWidth: Math.round(scrollTarget && scrollTarget.scrollWidth || 0),
            scrollHeight: Math.round(scrollTarget && scrollTarget.scrollHeight || 0),
            clientWidth: Math.round(scrollTarget && scrollTarget.clientWidth || 0),
            clientHeight: Math.round(scrollTarget && scrollTarget.clientHeight || 0),
            rootScrollX: Math.round(window.scrollX || 0),
            rootScrollY: Math.round(window.scrollY || 0),
          }, describe(scrollTarget));
        }, true);
        return true;
      })()
    `;
    await wc.executeJavaScript(script, true).catch(() => {});
  }

  applyResizeEdgeHint(view) {
    if (!view || !view.webContents || view.webContents.isDestroyed()) return;
    const enabled = this.resizeEdgeHintEnabled === true;
    const active = this.resizeEdgeHintActive === true;
    view.webContents.executeJavaScript(
      `window.__cyreneSetResizeEdgeHint && window.__cyreneSetResizeEdgeHint(${JSON.stringify(enabled)}, ${JSON.stringify(active)})`,
      true
    ).catch(() => {});
  }

  handleCapturedUserEvent(view, message) {
    const raw = String(message || '');
    if (raw.startsWith(BROWSER_RESIZE_EDGE_PREFIX)) {
      const active = String(raw.slice(BROWSER_RESIZE_EDGE_PREFIX.length)).trim() === 'in';
      const win = this.ownerWindow();
      if (win) {
        win.webContents.executeJavaScript(
          `document.body && document.body.classList.toggle("wb-col-resize-hover", ${JSON.stringify(active)})`,
          true
        ).catch(() => {});
      }
      return;
    }
    if (!raw.startsWith(BROWSER_USER_EVENT_CONSOLE_PREFIX)) return;
    const tab = this._tabForView(view);
    if (this._shouldSuppressCapturedEvent(tab)) return;
    let event = null;
    try {
      event = JSON.parse(raw.slice(BROWSER_USER_EVENT_CONSOLE_PREFIX.length));
    } catch (_) {
      return;
    }
    if (!event || typeof event !== 'object') return;
    this.recordUserEvent(String(event.kind || 'event'), {
      payload: event.payload && typeof event.payload === 'object' ? event.payload : {},
      target: event.target && typeof event.target === 'object' ? event.target : {},
      url: String(event.url || ''),
      title: String(event.title || ''),
      tab,
    });
  }

  recordUserEvent(kind, { payload = {}, target = {}, url = '', title = '', tab = null } = {}) {
    const active = tab || this.tabs.get(this.activeTabId);
    const wc = active && active.view && !active.view.webContents.isDestroyed() ? active.view.webContents : null;
    const finalUrl = url || (wc ? wc.getURL() : '');
    const finalTitle = title || (wc ? wc.getTitle() : '');
    postBackendJson('/api/browser/user-event', {
      sessionId: this.browserContext.sessionId || '',
      roundId: this.browserContext.roundId || '',
      eventKind: kind || 'event',
      browserUrl: finalUrl || '',
      browserTitle: finalTitle || '',
      target: target || {},
      payload: {
        ...(payload || {}),
        tabId: active ? active.id : '',
      },
    });
  }

  tabState(tab) {
    if (!tab || !tab.view || tab.view.webContents.isDestroyed()) return null;
    const wc = tab.view.webContents;
    return {
      id: tab.id,
      title: wc.getTitle() || tab.title || '',
      url: wc.getURL() || tab.url || 'about:blank',
      favicon: String(tab.favicon || ''),
      active: tab.id === this.activeTabId,
      loading: wc.isLoading(),
      canGoBack: wc.canGoBack(),
      canGoForward: wc.canGoForward(),
      muted: typeof wc.isAudioMuted === 'function' ? wc.isAudioMuted() : !!wc.audioMuted,
      audible: typeof wc.isCurrentlyAudible === 'function' ? wc.isCurrentlyAudible() : false,
      agentCreated: tab.agentCreated === true,
      agentOwnerRoundId: String(tab.agentOwnerRoundId || ''),
      lastAgentRoundId: String(tab.lastAgentRoundId || ''),
    };
  }

  state() {
    const tabs = Array.from(this.tabs.values()).map((tab) => this.tabState(tab)).filter(Boolean);
    return {
      ok: true,
      available: !!WebContentsView,
      sessionId: this.sessionId,
      activeTabId: this.activeTabId,
      visible: this.visible,
      tabs,
      activeTab: tabs.find((tab) => tab.id === this.activeTabId) || null,
      obscured: this.obscured,
      videoFullscreen: {
        active: this.videoFullscreen.active === true,
        external: this.videoFullscreen.external === true,
        tabId: this.videoFullscreen.tabId || '',
        platform: process.platform,
      },
    };
  }

  emitState() {
    if (this.tabPickerState.visible || this.tabPickerState.closing) this.pushTabPickerState();
    if (this.sessionId !== activeBrowserSessionId) return;
    // Fullscreen video may live in a separate macOS window, but state updates
    // always belong to the Cyrene renderer so each in-app browser surface can
    // show the same playback placeholder.
    const win = this.ownerWindow();
    if (win) {
      try { win.webContents.send('browser:state', this.state()); } catch (_) {}
    }
  }

  async ensureTab(url = 'about:blank') {
    if (this.activeTabId && this.tabs.has(this.activeTabId)) return this.tabs.get(this.activeTabId);
    return this.createTab({ url, activate: true });
  }

  async createTab({ url = 'about:blank', activate = true, agentOwnerRoundId = '' } = {}) {
    if (!WebContentsView) throw new Error('Electron WebContentsView is unavailable.');
    // Warm the tiny picker renderer alongside the first page so opening the
    // menu never waits for a new preload/data-document navigation.
    try { this.ensureTabPickerView(); } catch (_) {}
    const id = `tab_${this.nextTabId++}`;
    const view = this.createView();
    const tab = {
      id,
      view,
      url: normalizeBrowserUrl(url),
      title: '',
      favicon: '',
      debuggerReady: false,
      fileChoosers: new Map(),
      uploadTargets: new Map(),
      lastAgentFileChooser: null,
      agentFileChooserResolver: null,
      agentCreated: Boolean(String(agentOwnerRoundId || '').trim()),
      agentOwnerRoundId: String(agentOwnerRoundId || '').trim(),
      lastAgentRoundId: String(agentOwnerRoundId || '').trim(),
    };
    this.tabs.set(id, tab);
    this._recordAgentTab(tab, tab.agentOwnerRoundId);
    if (activate || !this.activeTabId) {
      const previous = this.tabs.get(this.activeTabId);
      if (previous && previous.id !== id) this.hideAgentCursor(previous).catch(() => {});
      this.activeTabId = id;
      this.invalidateSnapshot();
    }

    // Attach the WebContentsView before waiting for navigation. Chromium can
    // reject loadURL() for media documents with ERR_FAILED even though it has
    // already committed a usable native media page. Navigation failures must
    // not leave a live tab detached from Cyrene or reject browser:create-tab.
    this.syncAttachedView();
    this.emitState();
    if (tab.url && tab.url !== 'about:blank') {
      try {
        await view.webContents.loadURL(tab.url);
      } catch (err) {
        tab.lastLoadError = String((err && err.message) || err);
        console.warn(`[electron] Browser tab navigation reported an error for ${tab.url}: ${tab.lastLoadError}`);
      }
    } else {
      view.webContents.loadURL('about:blank').catch(() => {});
    }
    this.emitState();
    return tab;
  }

  activateTab(tabId) {
    const id = String(tabId || '').trim();
    if (!this.tabs.has(id)) throw new Error('Browser tab not found.');
    const previous = this.tabs.get(this.activeTabId);
    if (previous && previous.id !== id) this.hideAgentCursor(previous).catch(() => {});
    this.activeTabId = id;
    this.invalidateSnapshot();
    this.syncAttachedView();
    this.emitState();
    return this.state();
  }

  closeTab(tabId) {
    const id = String(tabId || this.activeTabId || '').trim();
    const tab = this.tabs.get(id);
    if (!tab) return this.state();
    if (this.videoFullscreen.active && this.videoFullscreen.tabId === id) {
      this.finishVideoFullscreen(tab.view);
    }
    this.detachView(tab);
    this.tabs.delete(id);
    try { tab.view.webContents.close(); } catch (_) {}
    if (this.activeTabId === id) {
      this.activeTabId = this.tabs.keys().next().value || '';
    }
    this.syncAttachedView();
    this.emitState();
    return this.state();
  }

  detachView(tab) {
    if (!tab) return;
    try { tab.view.setVisible(false); } catch (_) {}
    const windows = [this.attachedWindow, this.videoFullscreenWindow, this.ownerWindow()];
    for (const win of windows) {
      if (!win || win.isDestroyed()) continue;
      try { win.contentView.removeChildView(tab.view); } catch (_) {}
    }
    if (this.attachedTabId === tab.id) {
      this.attachedTabId = '';
      this.attachedWindow = null;
    }
  }

  repaintView(tab) {
    if (!tab || !tab.view || tab.view.webContents.isDestroyed()) return;
    const wc = tab.view.webContents;
    try { wc.invalidate(); } catch (_) {}
    if (this._repaintTimer) clearTimeout(this._repaintTimer);
    this._repaintTimer = setTimeout(() => {
      this._repaintTimer = null;
      if (tab.view.webContents.isDestroyed()) return;
      const ownsFullscreenSurface = this.videoFullscreen.active && this.videoFullscreen.tabId === tab.id;
      if (this.attachedTabId !== tab.id || (!ownsFullscreenSurface && (!this.visible || this.obscured))) return;
      try { tab.view.webContents.invalidate(); } catch (_) {}
    }, 80);
  }

  pageViewBounds(bounds = this.bounds) {
    const source = bounds || {};
    return {
      x: Math.round(Number(source.x) || 0),
      y: Math.round(Number(source.y) || 0),
      width: Math.max(0, Math.round(Number(source.width) || 0)),
      height: Math.max(0, Math.round(Number(source.height) || 0)),
    };
  }

  pageZoomForBounds(bounds = this.bounds) {
    const width = Math.max(0, Math.round(Number(bounds && bounds.width) || 0));
    if (width <= 0 || width >= PAGE_CSS_TARGET_WIDTH) return 1;
    return Math.max(PAGE_MIN_ZOOM, width / PAGE_CSS_TARGET_WIDTH);
  }

  async applyPageZoom(view, bounds = this.bounds, zoomEnabled = true, options = {}) {
    if (!view || !view.webContents || view.webContents.isDestroyed()) return 1;
    const wc = view.webContents;
    const contentsId = Number(wc.id) || 0;
    const zoomToken = (this._pageZoomTokenByContents.get(contentsId) || 0) + 1;
    this._pageZoomTokenByContents.set(contentsId, zoomToken);
    const isCurrent = () => (
      !wc.isDestroyed()
      && this._pageZoomTokenByContents.get(contentsId) === zoomToken
    );
    const dipWidth = Math.max(0, Math.round(Number(bounds && bounds.width) || 0));
    if (!zoomEnabled || dipWidth <= 0 || dipWidth >= PAGE_CSS_TARGET_WIDTH) {
      if (isCurrent()) {
        try { wc.setZoomFactor(1); } catch (_) {}
        wc.executeJavaScript(`(() => {
          document.documentElement.removeAttribute('data-cyrene-pip-fit-width');
          return true;
        })()`, true).catch(() => {});
      }
      return 1;
    }
    // Chromium quantizes zoom requests to discrete levels. Bias the single
    // request one zoom step below the mathematical fit so quantization can
    // never choose a larger factor that clips the right edge. Do not perform a
    // visible second-pass correction: changing zoom twice is perceived as a
    // flash in PiP, especially immediately after a split/maximized handoff.
    const fast = options && options.fast === true;
    const waitMs = fast ? 40 : 140;
    let pageLayoutWidth = PAGE_CSS_TARGET_WIDTH;
    try {
      const measuredLayoutWidth = Number(await wc.executeJavaScript(`(() => {
        const root = document.documentElement;
        const body = document.body;
        return Math.max(
          window.innerWidth || 0,
          root ? root.scrollWidth : 0,
          body ? body.scrollWidth : 0
        );
      })()`, true)) || 0;
      pageLayoutWidth = Math.min(
        PAGE_CSS_MAX_FIT_WIDTH,
        Math.max(PAGE_CSS_TARGET_WIDTH, measuredLayoutWidth)
      );
    } catch (_) {}
    if (!isCurrent()) return 1;
    const request = Math.max(PAGE_MIN_ZOOM, (dipWidth / pageLayoutWidth) / 1.2);
    let actual = request;
    try {
      if (!isCurrent()) return actual;
      wc.setZoomFactor(request);
      wc.executeJavaScript(`(() => {
        let style = document.querySelector('style[data-cyrene-pip-fit-width-style]');
        if (!style) {
          style = document.createElement('style');
          style.setAttribute('data-cyrene-pip-fit-width-style', '');
          style.textContent =
            'html[data-cyrene-pip-fit-width], ' +
            'html[data-cyrene-pip-fit-width] body {' +
            'overflow-x: hidden !important;' +
            'overscroll-behavior-x: none !important;' +
            'overscroll-behavior-y: auto !important;' +
            '}';
          (document.head || document.documentElement).appendChild(style);
        }
        document.documentElement.setAttribute('data-cyrene-pip-fit-width', '');
        const scrolling = document.scrollingElement || document.documentElement;
        if (scrolling && scrolling.scrollLeft) scrolling.scrollLeft = 0;
        return true;
      })()`, true).catch(() => {});
      await new Promise((resolve) => setTimeout(resolve, waitMs));
      if (!isCurrent()) return actual;
      const innerW = Number(await wc.executeJavaScript('window.innerWidth')) || 0;
      if (!isCurrent()) return actual;
      if (innerW > 0) {
        actual = dipWidth / innerW;
      }
    } catch (_) {}
    return actual;
  }

  async pageZoomOf(wc, dipWidth = 0) {
    try {
      const width = dipWidth > 0
        ? dipWidth
        : Math.max(0, Math.round(Number(this.bounds.width) || 0));
      const innerW = Number(await wc.executeJavaScript('window.innerWidth')) || 0;
      if (innerW <= 0 || width <= 0) return 1;
      return Math.max(0.001, Math.min(1, width / innerW));
    } catch (_) {
      return 1;
    }
  }

  async pageViewportMatches(view, bounds) {
    if (!view || !view.webContents || view.webContents.isDestroyed()) return false;
    const target = this.pageViewBounds(bounds);
    // The page zoom scales the CSS viewport. For zoomed panes the width is
    // pinned to the target desktop width; the height expectation follows
    // from the applied zoom read back from innerWidth.
    const zoom = this.pageZoomForBounds(target);
    let expectedWidth = target.width;
    let expectedHeight = target.height;
    let widthTolerance = 2;
    let fitWidth = false;
    if (zoom < 1) {
      expectedWidth = PAGE_CSS_TARGET_WIDTH;
      // Chromium applies zoom in discrete steps; depending on the exact PiP
      // width the next safe zoom step can expose up to ~20% extra CSS width.
      // That is intentional: a fit-width surface may be wider than the target,
      // but it must never be narrower and clip the page's right edge.
      fitWidth = true;
      widthTolerance = Math.ceil((PAGE_CSS_MAX_FIT_WIDTH * 1.2) - PAGE_CSS_TARGET_WIDTH);
      try {
        const innerW = Number(await view.webContents.executeJavaScript('window.innerWidth')) || 0;
        const applied = innerW > 0 ? target.width / innerW : zoom;
        expectedHeight = Math.round(target.height / applied);
      } catch (_) {
        expectedHeight = Math.round(target.height / zoom);
      }
    }
    try {
      const viewport = await Promise.race([
        view.webContents.executeJavaScript(
          '({ width: window.innerWidth, height: window.innerHeight })',
          true
        ),
        new Promise((resolve) => setTimeout(() => resolve(null), 80)),
      ]);
      const viewportWidth = Number(viewport && viewport.width) || 0;
      const widthMatches = fitWidth
        ? viewportWidth >= expectedWidth - 2 && viewportWidth <= expectedWidth + widthTolerance
        : Math.abs(viewportWidth - expectedWidth) <= widthTolerance;
      return !!viewport
        && widthMatches
        && Math.abs((Number(viewport.height) || 0) - expectedHeight) <= 2;
    } catch (_) {
      return false;
    }
  }

  async waitForPageViewport(view, bounds, attempts = 4) {
    const count = Math.max(1, Math.round(Number(attempts) || 1));
    for (let attempt = 0; attempt < count; attempt += 1) {
      if (await this.pageViewportMatches(view, bounds)) return true;
      if (attempt + 1 < count) {
        await new Promise((resolve) => setTimeout(resolve, 24));
      }
    }
    return false;
  }

  async settlePageViewport(view, bounds, forcePulse = false) {
    if (!view || !view.webContents || view.webContents.isDestroyed()) return false;
    const target = this.pageViewBounds(bounds);
    try { view.setBounds(target); } catch (_) {}
    try { view.webContents.invalidate(); } catch (_) {}
    if (!forcePulse && await this.waitForPageViewport(view, target, 3)) return true;

    // Electron 35 on macOS can acknowledge the first hidden PiP -> maximized
    // setBounds call without delivering a resize to Chromium's layout viewport.
    // A one-pixel geometry pulse forces a second native resize notification;
    // verify window.innerWidth/innerHeight before treating the transition as
    // settled so the renderer cannot cache a fullscreen shell around a PiP page.
    const pulse = {
      ...target,
      width: target.width > 9 ? target.width - 1 : target.width,
      height: target.height > 9 ? target.height - 1 : target.height,
    };
    try { view.setBounds(pulse); } catch (_) {}
    await new Promise((resolve) => setTimeout(resolve, 24));
    try { view.setBounds(target); } catch (_) {}
    try { view.webContents.invalidate(); } catch (_) {}
    return this.waitForPageViewport(view, target, 6);
  }

  applyPageFrameStyle(view, radius = this.pageCornerRadius, force = false) {
    if (!view || !view.webContents || view.webContents.isDestroyed()) return;
    const wc = view.webContents;
    const cornerRadius = Math.max(0, Math.min(24, Math.round(Number(radius) || 0)));
    const tab = this._tabForView(view);
    const signature = String(cornerRadius);
    if (!force && tab && tab.pageFrameSignature === signature) return;
    const script = `(() => {
      const scrollbarAttr = 'data-cyrene-pip-root-scrollbars';
      let scrollbarStyle = document.querySelector('style[' + scrollbarAttr + ']');
      const radius = ${JSON.stringify(cornerRadius)};
      if (radius <= 0) {
        if (scrollbarStyle) scrollbarStyle.remove();
        return true;
      }
      if (!document.documentElement) return false;
      if (!scrollbarStyle) {
        scrollbarStyle = document.createElement('style');
        scrollbarStyle.setAttribute(scrollbarAttr, '');
        document.documentElement.appendChild(scrollbarStyle);
      }
      scrollbarStyle.textContent =
        'html, body { scrollbar-width: none !important; }' +
        'html::-webkit-scrollbar, body::-webkit-scrollbar {' +
        ' display: none !important; width: 0 !important; height: 0 !important; }';
      return true;
    })()`;
    wc.executeJavaScript(script, true).then((applied) => {
      if (applied && tab) tab.pageFrameSignature = signature;
    }).catch(() => {});
  }

  ensureChatOverlayView() {
    if (this.chatOverlayView && !this.chatOverlayView.webContents.isDestroyed()) return this.chatOverlayView;
    const view = new WebContentsView({
      webPreferences: {
        preload: path.join(__dirname, 'browser-chat-overlay-preload.js'),
        contextIsolation: true,
        nodeIntegration: false,
        sandbox: true,
        backgroundThrottling: true,
      },
    });
    try { view.setBackgroundColor('#00000000'); } catch (_) {}
    view.webContents.on('did-finish-load', () => this.pushChatOverlayState());
    const overlayUrl = backendPort
      ? `http://127.0.0.1:${backendPort}/static/app/electron/browser-chat-overlay.html?platform=${encodeURIComponent(process.platform)}`
      : `data:text/html;charset=utf-8,${encodeURIComponent(BROWSER_CHAT_OVERLAY_HTML)}`;
    view.webContents.loadURL(overlayUrl).catch(() => {});
    this.chatOverlayView = view;
    return view;
  }

  pushChatOverlayState() {
    const view = this.chatOverlayView;
    if (!view || view.webContents.isDestroyed()) return;
    try { view.webContents.send('browser-chat-overlay:state', this.chatOverlayState); } catch (_) {}
  }

  hideChatOverlay() {
    if (!this.chatOverlayView) return;
    try { this.chatOverlayView.setVisible(false); } catch (_) {}
  }

  syncChatOverlay(container, raise = false) {
    // BrowserWindow owns native child views through its root contentView. Accept
    // either shape here so callers cannot accidentally attach to BrowserWindow.
    const parent = container && container.contentView ? container.contentView : container;
    const state = this.chatOverlayState || {};
    const shouldShow = !!(
      state.visible
      && this.visible
      && !this.obscured
      && !this.videoFullscreen.active
      && parent
      && this.bounds.width > 24
      && this.bounds.height > 24
    );
    if (!shouldShow) {
      this.hideChatOverlay();
      return;
    }
    const view = this.ensureChatOverlayView();
    if (this.chatOverlayParent && this.chatOverlayParent !== parent) {
      try {
        this.chatOverlayParent.removeChildView(view);
      } catch (err) {
        console.warn('[electron] Failed to detach browser chat overlay:', err);
      }
      this.chatOverlayParent = null;
    }
    const width = Math.max(244, Math.min(544, this.bounds.width));
    const height = state.showStatus ? 92 : 58;
    const bottomOffset = 56;
    try {
      view.setBounds({
        x: this.bounds.x + Math.round((this.bounds.width - width) / 2),
        y: this.bounds.y + Math.max(0, this.bounds.height - height - bottomOffset),
        width,
        height,
      });
    } catch (_) {}
    if (raise && this.chatOverlayParent === parent) {
      try {
        parent.removeChildView(view);
      } catch (err) {
        console.warn('[electron] Failed to raise browser chat overlay:', err);
      }
      this.chatOverlayParent = null;
    }
    if (this.chatOverlayParent !== parent) {
      try {
        parent.addChildView(view);
        this.chatOverlayParent = parent;
      } catch (err) {
        console.error('[electron] Failed to attach browser chat overlay:', err);
        this.hideChatOverlay();
        return;
      }
    }
    try { view.setVisible(true); } catch (_) {}
    this.pushChatOverlayState();
  }

  setChatOverlay(info = {}) {
    const colors = info.colors && typeof info.colors === 'object' ? info.colors : {};
    this.chatOverlayState = {
      visible: info.visible === true,
      running: info.running === true,
      showStatus: info.showStatus === true,
      statusComplete: info.statusComplete === true,
      statusText: String(info.statusText || '').slice(0, 160),
      placeholder: String(info.placeholder || '').slice(0, 120),
      placeholderRunning: String(info.placeholderRunning || '').slice(0, 120),
      sendLabel: String(info.sendLabel || '').slice(0, 80),
      guideLabel: String(info.guideLabel || '').slice(0, 80),
      stopLabel: String(info.stopLabel || '').slice(0, 80),
      sessionId: this.sessionId,
      colors,
    };
    // The page WebContentsView can be reattached or promoted while entering
    // maximized mode. Re-add the Agent overlay after it so the composer stays
    // above the live page instead of silently ending up behind it.
    const parent = this.ownerWindow()?.contentView || null;
    this.syncChatOverlay(parent, true);
    if (this.tabPickerState.visible || this.tabPickerState.closing) {
      this.syncTabPicker(parent, true);
    }
    return { ok: true, visible: this.chatOverlayState.visible };
  }

  tabPickerSnapshot() {
    const state = this.tabPickerState || {};
    return {
      sessionId: this.sessionId,
      visible: state.visible === true,
      closing: state.closing === true,
      variant: state.variant === 'split' ? 'split' : 'maximized',
      activeTabId: this.activeTabId,
      tabs: Array.from(this.tabs.values()).map((tab) => this.tabState(tab)).filter(Boolean),
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
    if (!WebContentsView) throw new Error('Electron WebContentsView is unavailable.');
    const view = new WebContentsView({
      webPreferences: {
        preload: path.join(__dirname, 'browser-tab-picker-preload.js'),
        contextIsolation: true,
        nodeIntegration: false,
        sandbox: true,
        backgroundThrottling: true,
      },
    });
    this.tabPickerReady = false;
    try { view.setBackgroundColor('#00000000'); } catch (_) {}
    view.webContents.on('did-finish-load', () => {
      if (this.tabPickerView !== view) return;
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
    const pickerUrl = backendPort
      ? `http://127.0.0.1:${backendPort}/static/app/electron/browser-tab-picker.html?platform=${encodeURIComponent(process.platform)}`
      : `data:text/html;charset=utf-8,${encodeURIComponent(BROWSER_TAB_PICKER_HTML)}`;
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
    const surface = this.pageViewBounds(this.bounds);
    const variant = this.tabPickerState.variant === 'split' ? 'split' : 'maximized';
    const horizontalInset = variant === 'maximized' ? 116 : 12;
    // The native page starts below the browser navigation row. Lift the
    // floating picker by that chrome height so it sits beneath the title bar
    // and overlays the navigation row, matching the renderer-hosted menu.
    const verticalLift = 60;
    const availableWidth = Math.max(0, surface.width - horizontalInset);
    const width = Math.min(560, availableWidth);
    const rows = Math.max(1, this.tabs.size);
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
    const hostReady = !!(
      this.visible
      && !this.obscured
      && !this._boundsTransitioning
      && !this.videoFullscreen.active
      && parent
      && this.activeTabId
    );
    if (!hostReady) {
      if (this.tabPickerState.visible || this.tabPickerState.closing) this.dismissTabPicker(false);
      else this.finishTabPickerHide();
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
        return;
      }
    }
    this.trackTabPickerWindow(this.ownerWindow());
    try { view.setVisible(true); } catch (_) {}
    if (this.tabPickerState.visible) this.pushTabPickerState();
  }

  setTabPicker(info = {}) {
    const requestedVariant = info.variant === 'split' ? 'split' : 'maximized';
    if (info.visible !== true || !this.tabs.size) {
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
    const view = this.tabPickerView;
    if (view && !view.webContents.isDestroyed()) {
      setTimeout(() => {
        if (!this.tabPickerState.visible || this.tabPickerView !== view) return;
        try { view.webContents.focus(); } catch (_) {}
      }, 0);
    }
    return { ok: true, visible: this.tabPickerState.visible };
  }

  handleTabPickerAction(action = {}) {
    const type = String(action.type || '');
    const tabId = String(action.tabId || '');
    if (type === 'dismiss') return this.dismissTabPicker(true);
    if (!this.tabPickerState.visible || !tabId || !this.tabs.has(tabId)) return this.state();
    if (type === 'select') {
      this.dismissTabPicker(true);
      const result = this.activateTab(tabId);
      this.recordUserEvent('select_tab', { payload: { tabId } });
      this.notifyTabPickerRenderer({ type, tabId, activeTabId: result.activeTabId, tabCount: result.tabs.length });
      return result;
    }
    if (type === 'reload') {
      const result = this.reload({ tabId });
      this.recordUserEvent('navigate', { payload: { action: 'reload', tabId } });
      this.notifyTabPickerRenderer({ type, tabId, activeTabId: result.activeTabId, tabCount: result.tabs.length });
      return result;
    }
    if (type === 'mute') {
      const tab = this.tabs.get(tabId);
      const muted = tab && typeof tab.view.webContents.isAudioMuted === 'function'
        ? tab.view.webContents.isAudioMuted()
        : false;
      const result = this.setMuted({ tabId, muted: !muted });
      this.notifyTabPickerRenderer({ type, tabId, activeTabId: result.activeTabId, tabCount: result.tabs.length });
      return result;
    }
    if (type === 'close') {
      this.recordUserEvent('close_tab', { payload: { tabId } });
      const result = this.closeTab(tabId);
      if (!result.tabs.length) this.dismissTabPicker(false);
      this.notifyTabPickerRenderer({ type, tabId, activeTabId: result.activeTabId, tabCount: result.tabs.length });
      return result;
    }
    return this.state();
  }

  syncAttachedView() {
    const fullscreenTab = this.fullscreenTab();
    const active = fullscreenTab || this.tabs.get(this.activeTabId);
    const fullscreenActive = !!fullscreenTab;
    const win = this.surfaceWindow();
    if (!win) {
      this.hideChatOverlay();
      this.dismissTabPicker(false);
      return;
    }
    const ownsVisibleSurface = fullscreenActive || this.sessionId === activeBrowserSessionId;
    for (const tab of this.tabs.values()) {
      if (!active || tab.id !== active.id || !ownsVisibleSurface) this.detachView(tab);
    }
    if (!active || !ownsVisibleSurface) {
      this.hideChatOverlay();
      this.dismissTabPicker(false);
      return;
    }
    const shouldShow = fullscreenActive || (this.visible && !this.obscured && !this._boundsTransitioning);
    if (!shouldShow) {
      // Keep the active WebContentsView attached but hidden across PiP/fullscreen
      // transitions. Removing and re-adding it on macOS can strand Chromium's
      // compositor surface as a white rectangle when the size shrinks again.
      if (this.attachedTabId === active.id) {
        try { active.view.setVisible(false); } catch (_) {}
      }
      this.hideChatOverlay();
      this.dismissTabPicker(false);
      return;
    }
    const wasAttached = this.attachedTabId === active.id;
    const wasAttachedToTargetWindow = wasAttached && this.attachedWindow === win;
    let wasVisible = false;
    if (wasAttachedToTargetWindow && typeof active.view.getVisible === 'function') {
      try { wasVisible = active.view.getVisible(); } catch (_) {}
    }
    const targetCornerRadius = fullscreenActive ? 0 : this.pageCornerRadius;
    const targetBounds = fullscreenActive ? this.fullscreenBounds(win) : this.pageViewBounds(this.bounds);
    if (!wasAttachedToTargetWindow) {
      this.detachView(active);
      try { active.view.setBorderRadius(targetCornerRadius); } catch (_) {}
      try { active.view.setBounds(targetBounds); } catch (_) {}
      try { win.contentView.addChildView(active.view); } catch (_) {}
      this.attachedTabId = active.id;
      this.attachedWindow = win;
    } else {
      try { active.view.setBorderRadius(targetCornerRadius); } catch (_) {}
      try { active.view.setBounds(targetBounds); } catch (_) {}
    }
    this.applyPageZoom(active.view, targetBounds, this.zoomEnabled !== false).catch(() => {});
    this.applyPageFrameStyle(active.view, targetCornerRadius);
    try { active.view.setVisible(true); } catch (_) {}
    // Always keep the native Agent composer as the last child view. Electron
    // may change the page view's native stacking order during a resize even
    // when the JS-side attachment did not change.
    this.syncChatOverlay(win.contentView, true);
    // The picker is another native child view. Raise it after the live page so
    // it can float over that page without hiding, snapshotting, or reattaching
    // the page's compositor surface.
    this.syncTabPicker(win.contentView, true);
    if (!wasAttached || !wasVisible) this.repaintView(active);
  }

  async prepareBoundsTransition() {
    const token = ++this._boundsTransitionToken;
    this._boundsTransitioning = true;
    if (this._syncTimer) { clearTimeout(this._syncTimer); this._syncTimer = null; }
    this.syncAttachedView();
    const active = this.tabs.get(this.activeTabId);
    if (!active || active.view.webContents.isDestroyed()) {
      this._boundsTransitioning = false;
      return { ...this.state(), ok: false, error: 'No active browser view.' };
    }
    const targetCornerRadius = this.pageCornerRadius;
    const targetBounds = this.pageViewBounds(this.bounds);
    // A hidden WebContentsView keeps its last compositor surface on macOS even
    // after setBounds updates window.innerWidth/innerHeight.  capturePage() then
    // returns a bitmap at the *source* size, which makes the renderer stretch a
    // PiP frame across the maximized shell (and vice versa) for one frame.
    // Stage the target-sized view at the owner's bottom-right clipping edge
    // while it is visible. macOS does not composite a completely offscreen
    // child view, but a 1x1 visible intersection is enough to produce the full
    // target surface without exposing resize/white-frame churn over the page.
    const ownerBounds = this.ownerWindow()?.getContentBounds?.() || {};
    const stagingBounds = {
      ...targetBounds,
      x: Math.max(0, Math.round(Number(ownerBounds.width) || 0) - 1),
      y: Math.max(0, Math.round(Number(ownerBounds.height) || 0) - 1),
    };
    try { active.view.setBorderRadius(targetCornerRadius); } catch (_) {}
    try { active.view.setBounds(stagingBounds); } catch (_) {}
    try { active.view.setVisible(true); } catch (_) {}
    await this.applyPageZoom(active.view, targetBounds, this.zoomEnabled !== false, { fast: true });
    this.applyPageFrameStyle(active.view, targetCornerRadius);
    const viewportReady = await this.settlePageViewport(active.view, stagingBounds);
    // The 1x1 edge intersection lets capturePage read the already-rasterized
    // target surface quickly. Validate its physical pixel size because macOS
    // can occasionally hand back the previous surface; use CDP only as the
    // slower fallback in that case.
    let targetPngBase64 = '';
    const displayScale = (() => {
      try {
        return Math.max(1, Number(screen.getDisplayMatching(this.ownerWindow().getBounds()).scaleFactor) || 1);
      } catch (_) {
        return 1;
      }
    })();
    const expectedPixelWidth = Math.round(targetBounds.width * displayScale);
    const expectedPixelHeight = Math.round(targetBounds.height * displayScale);
    const targetImage = await Promise.race([
      active.view.webContents.capturePage().catch(() => null),
      new Promise((resolve) => setTimeout(resolve, 120)),
    ]);
    if (targetImage) {
      const imageSize = targetImage.getSize();
      if (Math.abs(imageSize.width - expectedPixelWidth) <= 4
        && Math.abs(imageSize.height - expectedPixelHeight) <= 4) {
        targetPngBase64 = targetImage.toPNG().toString('base64');
      }
    }
    try {
      if (!targetPngBase64) {
        const debug = await this._ensureDebugger(active);
        const metrics = await debug.sendCommand('Page.getLayoutMetrics');
        const viewport = metrics.cssVisualViewport || metrics.visualViewport || {};
        const viewportWidth = Math.max(1, Number(viewport.clientWidth) || 1);
        const viewportHeight = Math.max(1, Number(viewport.clientHeight) || 1);
        const capture = await Promise.race([
          debug.sendCommand('Page.captureScreenshot', {
            format: 'png',
            fromSurface: true,
            captureBeyondViewport: false,
            clip: {
              x: Number(viewport.pageX) || 0,
              y: Number(viewport.pageY) || 0,
              width: viewportWidth,
              height: viewportHeight,
              scale: 1,
            },
          }),
          new Promise((resolve) => setTimeout(() => resolve(null), 500)),
        ]);
        targetPngBase64 = String(capture && capture.data || '');
      }
    } catch (_) {}
    // Leave the native view hidden at its final on-screen geometry. The target
    // bitmap remains visible in the renderer until commitBoundsTransition()
    // reveals this already-sized compositor surface.
    try { active.view.setVisible(false); } catch (_) {}
    try { active.view.setBounds(targetBounds); } catch (_) {}
    if (token !== this._boundsTransitionToken) return { ...this.state(), ok: false, error: 'Browser transition was superseded.' };
    // A preview is optional polish, not permission to keep the live surface
    // hidden. If capture fails, immediately recover the native view at the
    // target bounds so fallback mode changes cannot strand a white panel.
    if (!targetPngBase64) {
      this._boundsTransitioning = false;
      this.syncAttachedView();
      this.repaintView(active);
    }
    return {
      ...this.state(),
      ok: !!targetPngBase64,
      transitionPrepared: !!targetPngBase64,
      viewportReady,
      pngBase64: targetPngBase64,
    };
  }

  async commitBoundsTransition() {
    const token = this._boundsTransitionToken;
    const active = this.tabs.get(this.activeTabId);
    if (!active || active.view.webContents.isDestroyed()) {
      this._boundsTransitioning = false;
      return this.state();
    }
    const targetBounds = this.pageViewBounds(this.bounds);
    let viewportReady = false;
    this._boundsTransitioning = false;
    this.syncAttachedView();
    await new Promise((resolve) => setTimeout(resolve, 34));
    // A hidden WebContentsView can report the right JS viewport while its
    // visible compositor surface still holds the previous size. Always pulse
    // once after reattachment, then wait for a visible frame before allowing
    // the renderer to remove its bitmap proxy.
    if (token === this._boundsTransitionToken) {
      viewportReady = await this.settlePageViewport(active.view, targetBounds, true);
      await Promise.race([
        active.view.webContents.capturePage().catch(() => null),
        new Promise((resolve) => setTimeout(resolve, 180)),
      ]);
    }
    if (token === this._boundsTransitionToken) this.repaintView(active);
    if (!viewportReady && token === this._boundsTransitionToken) {
      console.warn(
        `[electron] Browser viewport did not settle at ${targetBounds.width}x${targetBounds.height}.`
      );
    }
    return this.state();
  }

  async settleBoundsTransition() {
    let prepared;
    try {
      prepared = await this.prepareBoundsTransition();
    } catch (error) {
      this._boundsTransitioning = false;
      this.syncAttachedView();
      throw error;
    }
    if (!prepared || prepared.ok === false) {
      this._boundsTransitioning = false;
      this.syncAttachedView();
      return prepared || this.state();
    }
    return this.commitBoundsTransition();
  }

  setBounds(info = {}) {
    const width = Math.max(0, Math.round(Number(info.width) || 0));
    const height = Math.max(0, Math.round(Number(info.height) || 0));
    this.bounds = {
      x: Math.round(Number(info.x) || 0),
      y: Math.round(Number(info.y) || 0),
      width,
      height,
    };
    this.borderRadius = Math.max(0, Math.min(24, Math.round(Number(info.borderRadius) || 0)));
    this.pageCornerRadius = Math.max(0, Math.min(24, Math.round(Number(info.pageCornerRadius) || 0)));
    // Floating and split browser surfaces can independently select fit-width
    // zoom and the split-divider cursor bridge. Only update either preference
    // when the payload carries it: hide-only bounds must preserve both.
    if (Object.prototype.hasOwnProperty.call(info, 'zoomEnabled')) {
      this.zoomEnabled = info.zoomEnabled !== false;
    }
    if (Object.prototype.hasOwnProperty.call(info, 'resizeEdgeHintEnabled')) {
      this.resizeEdgeHintEnabled = info.resizeEdgeHintEnabled === true;
    }
    if (Object.prototype.hasOwnProperty.call(info, 'resizeEdgeHintActive')) {
      this.resizeEdgeHintActive = info.resizeEdgeHintActive === true;
    }
    const active = this.tabs.get(this.activeTabId);
    if (active) this.applyResizeEdgeHint(active.view);
    // A newly mounted split surface is authoritative. It may arrive after a
    // cancelled/failed PiP transition whose renderer never reached commit.
    if (info.forceVisible === true && this._boundsTransitioning) {
      this._boundsTransitionToken += 1;
      this._boundsTransitioning = false;
    }
    this.visible = info.visible === true && width > 8 && height > 8;
    if (!this.visible) this.hideAllAgentCursors();
    // Preserve the in-app host geometry while a video owns the fullscreen
    // surface, but never let renderer layout churn resize the fullscreen View.
    if (this.videoFullscreen.active) return this.state();
    // Coalesce native view updates to a stable ~30fps cadence. Electron 35 can
    // leave a WebContentsView white (or crash on some macOS builds) when
    // setBounds is hammered by concurrent renderer IPC calls. A 32ms trailing
    // update stays visually attached without the old 50ms drag lag.
    if (!this.visible) {
      this._boundsTransitionToken += 1;
      this._boundsTransitioning = false;
      if (this._syncTimer) { clearTimeout(this._syncTimer); this._syncTimer = null; }
      this.syncAttachedView();
    } else if (info.transition === 'prepare') {
      return this.prepareBoundsTransition().catch((error) => {
        this._boundsTransitioning = false;
        this.syncAttachedView();
        throw error;
      });
    } else if (info.transition === 'commit') {
      return this.commitBoundsTransition();
    } else if (info.transition === true) {
      return this.settleBoundsTransition();
    } else if (!this._syncTimer) {
      this._syncTimer = setTimeout(() => {
        this._syncTimer = null;
        this.syncAttachedView();
      }, 32);
    }
    return this.state();
  }

  setObscured(obscured = false) {
    this.obscured = obscured === true;
    if (this.obscured) this.hideAllAgentCursors();
    this.syncAttachedView();
    this.emitState();
    return this.state();
  }

  async navigate({ url, tabId = '', maxChars = 8000, agentOwnerRoundId = '' } = {}) {
    this.invalidateSnapshot();
    const targetUrl = normalizeBrowserUrl(url);
    const ownerRoundId = String(agentOwnerRoundId || '').trim();
    let tab = tabId ? this.tabs.get(String(tabId)) : this.tabs.get(this.activeTabId);
    if (!tabId && ownerRoundId && (!tab || tab.agentCreated !== true)) {
      const reusable = this._agentTabs().slice(-1)[0];
      tab = reusable || await this.createTab({
        url: 'about:blank',
        activate: true,
        agentOwnerRoundId: ownerRoundId,
      });
    }
    if (!tab) tab = await this.createTab({
      url: 'about:blank',
      activate: true,
      agentOwnerRoundId: ownerRoundId,
    });
    if (ownerRoundId && tab.agentCreated === true) {
      tab.agentOwnerRoundId = ownerRoundId;
      tab.lastAgentRoundId = ownerRoundId;
      this._recordAgentTab(tab, ownerRoundId);
    }
    const previous = this.tabs.get(this.activeTabId);
    if (previous && previous.id !== tab.id) await this.hideAgentCursor(previous);
    await this.hideAgentCursor(tab);
    this.activeTabId = tab.id;
    this.syncAttachedView();
    try {
      await tab.view.webContents.loadURL(targetUrl);
    } catch (err) {
      return { ok: false, error: String((err && err.message) || err), url: targetUrl };
    }
    return this.pageSnapshot(tab.id, maxChars);
  }

  async pageSnapshot(tabId = '', maxChars = 8000) {
    const tab = tabId ? this.tabs.get(String(tabId)) : this.tabs.get(this.activeTabId);
    if (!tab) return { ok: false, error: 'No browser tab is open.' };
    const wc = tab.view.webContents;
    let text = '';
    let links = [];
    try {
      const pageData = await wc.executeJavaScript(
        `(() => {
          const clean = (value, limit = 200) => String(value || '').replace(/\\s+/g, ' ').trim().slice(0, limit);
          for (const el of document.querySelectorAll('[data-cyrene-ref]')) {
            el.removeAttribute('data-cyrene-ref');
          }
          const seen = new Set();
          const links = [];
          for (const el of Array.from(document.querySelectorAll('a[href]'))) {
            const style = window.getComputedStyle(el);
            const rect = el.getBoundingClientRect();
            if (!style || style.display === 'none' || style.visibility === 'hidden' || Number(style.opacity) === 0) continue;
            if (!rect || rect.width <= 0 || rect.height <= 0) continue;
            const imageAlt = Array.from(el.querySelectorAll('img[alt]')).map((img) => img.getAttribute('alt') || '').join(' ');
            const text = clean(el.innerText || el.getAttribute('aria-label') || el.getAttribute('title') || imageAlt);
            if (!text) continue;
            let url = '';
            try { url = new URL(el.getAttribute('href') || '', location.href).href; } catch (_) { continue; }
            if (!/^https?:/i.test(url)) continue;
            const key = text + '\\n' + url;
            if (seen.has(key)) continue;
            seen.add(key);
            const ref = 'e' + (links.length + 1);
            el.setAttribute('data-cyrene-ref', String(links.length + 1));
            links.push({ ref, text, url });
            if (links.length >= 120) break;
          }
          return { text: document.body ? document.body.innerText : '', links };
        })()`,
        true
      );
      text = pageData && pageData.text ? pageData.text : '';
      links = pageData && Array.isArray(pageData.links) ? pageData.links : [];
    } catch (_) {
      text = '';
      links = [];
    }
    const url = wc.getURL();
    const title = wc.getTitle();
    const trimmedText = trimBrowserText(text, maxChars);
    return {
      ok: true,
      url,
      title,
      status: 0,
      text: trimmedText,
      links,
      pageSignal: browserPageSignal(url, title, trimmedText),
      tabId: tab.id,
    };
  }

  async inspect({ tabId = '', maxElements = 80, textLimit = 160 } = {}) {
    const tab = tabId ? this.tabs.get(String(tabId)) : this.tabs.get(this.activeTabId);
    if (!tab) return { ok: false, error: 'No browser tab is open.' };
    const wc = tab.view.webContents;
    try {
      const result = await wc.executeJavaScript(
        `${BROWSER_VISIBLE_ELEMENTS_SCRIPT}(${JSON.stringify(maxElements)}, ${JSON.stringify(textLimit)})`,
        true
      );
      const snapshotToken = crypto.randomBytes(24).toString('base64url');
      const snapshotUrl = String((result && result.url) || wc.getURL());
      this.latestSnapshot = { token: snapshotToken, tabId: tab.id, url: snapshotUrl, issuedAt: Date.now() };
      return { ...(result || {}), ok: true, tabId: tab.id, snapshotToken };
    } catch (err) {
      return { ok: false, error: 'Inspect failed: ' + String((err && err.message) || err), url: wc.getURL(), title: wc.getTitle(), tabId: tab.id };
    }
  }

  async visibleLinkMatches({ tabId = '', url = '' } = {}) {
    const tab = tabId ? this.tabs.get(String(tabId)) : this.tabs.get(this.activeTabId);
    if (!tab) return { ok: false, error: 'No browser tab is open.', matches: [] };
    const wc = tab.view.webContents;
    const targetUrl = normalizeBrowserUrl(url);
    try {
      const result = await wc.executeJavaScript(
        `(() => {
          const target = ${JSON.stringify(targetUrl)};
          const clean = (value, limit = 200) => String(value || '').replace(/\\s+/g, ' ').trim().slice(0, limit);
          let normalizedTarget = '';
          try { normalizedTarget = new URL(target, location.href).href; } catch (_) { return { ok: false, error: 'Invalid target URL.', matches: [] }; }
          // data-cyrene-ref is a shared namespace with browser_snapshot's
          // inspect script, which numbers from 1 independently. Allocate past
          // the current max so the two schemes never collide.
          let nextRef = 1;
          for (const el of document.querySelectorAll('[data-cyrene-ref]')) {
            const n = Number(el.getAttribute('data-cyrene-ref') || 0);
            if (Number.isInteger(n) && n >= nextRef) nextRef = n + 1;
          }
          const matches = [];
          for (const el of Array.from(document.querySelectorAll('a[href]'))) {
            const style = window.getComputedStyle(el);
            const rect = el.getBoundingClientRect();
            if (!style || style.display === 'none' || style.visibility === 'hidden' || Number(style.opacity) === 0) continue;
            if (!rect || rect.width <= 0 || rect.height <= 0) continue;
            let href = '';
            try { href = new URL(el.getAttribute('href') || '', location.href).href; } catch (_) { continue; }
            if (href !== normalizedTarget) continue;
            const imageAlt = Array.from(el.querySelectorAll('img[alt]')).map((img) => img.getAttribute('alt') || '').join(' ');
            const text = clean(el.innerText || el.getAttribute('aria-label') || el.getAttribute('title') || imageAlt);
            let refNumber = Number(el.getAttribute('data-cyrene-ref') || 0);
            if (!Number.isInteger(refNumber) || refNumber < 1) {
              refNumber = nextRef;
              nextRef += 1;
              el.setAttribute('data-cyrene-ref', String(refNumber));
            }
            matches.push({ ref: 'e' + refNumber, text, url: href });
          }
          return { ok: true, url: location.href, targetUrl: normalizedTarget, matches };
        })()`,
        true
      );
      return result && typeof result === 'object' ? result : { ok: false, error: 'Visible-link scan failed.', matches: [] };
    } catch (err) {
      return { ok: false, error: String((err && err.message) || err), matches: [] };
    }
  }

  async navigationGuard({ url = '', reason = '', snapshotToken = '' } = {}) {
    const tab = this.tabs.get(this.activeTabId);
    const targetUrl = normalizeBrowserUrl(url);
    if (!tab) {
      if (String(reason || '') === 'ui_unreachable') {
        return { ok: false, allowed: false, code: 'SNAPSHOT_CREDENTIAL_REQUIRED', error: 'ui_unreachable requires a fresh browser_snapshot credential.' };
      }
      return { ok: true, allowed: true, targetUrl };
    }
    const currentUrl = tab.view.webContents.getURL();
    let normalizedTarget = targetUrl;
    let normalizedCurrent = currentUrl;
    try { normalizedTarget = new URL(targetUrl, currentUrl).href; } catch (_) {}
    try { normalizedCurrent = new URL(currentUrl).href; } catch (_) {}
    if (normalizedCurrent === normalizedTarget) {
      return {
        ok: false,
        allowed: false,
        code: 'ALREADY_AT_TARGET',
        error: 'The active browser tab is already at the requested URL; browser_navigate was not executed.',
        url: normalizedCurrent,
        tabId: tab.id,
      };
    }
    if (String(reason || '') === 'user_exact_url') return { ok: true, allowed: true, targetUrl: normalizedTarget };
    if (String(reason || '') === 'ui_unreachable') {
      const credential = this.latestSnapshot;
      const token = String(snapshotToken || '');
      const providedToken = Buffer.from(token);
      const expectedToken = Buffer.from(String(credential && credential.token || ''));
      const valid = credential
        && token
        && providedToken.length === expectedToken.length
        && crypto.timingSafeEqual(providedToken, expectedToken)
        && credential.tabId === tab.id
        && credential.url === currentUrl
        && Date.now() - credential.issuedAt <= 120000;
      if (!valid) {
        return {
          ok: false,
          allowed: false,
          code: 'SNAPSHOT_CREDENTIAL_INVALID',
          error: 'ui_unreachable requires the unexpired token from the latest browser_snapshot of the active page.',
        };
      }
      this.invalidateSnapshot();
      const scan = await this.visibleLinkMatches({ tabId: tab.id, url: normalizedTarget });
      const matches = Array.isArray(scan.matches) ? scan.matches : [];
      if (matches.length) {
        return {
          ok: false,
          allowed: false,
          code: 'VISIBLE_LINK_AVAILABLE',
          error: 'Target URL is available through visible page UI. Use browser_click_ref from a fresh browser_snapshot.',
          targetUrl: normalizedTarget,
          matches,
        };
      }
    }
    return { ok: true, allowed: true, targetUrl: normalizedTarget };
  }

  async _findTarget(wc, { mode = 'selector', value = '', exact = false, visibleOnly = true } = {}) {
    const script = `${BROWSER_FIND_TARGET_SCRIPT}(${JSON.stringify(mode)}, ${JSON.stringify(value)}, ${exact ? 'true' : 'false'}, ${visibleOnly === false ? 'false' : 'true'})`;
    try {
      const first = await wc.executeJavaScript(script, true);
      if (!first || !first.ok) return first;
      // scrollIntoView may trigger sticky headers, virtualized lists, or a
      // framework render. Wait two frames and resolve the target again so the
      // returned center belongs to the settled layout.
      await wc.executeJavaScript(`new Promise((resolve) => {
        const frame = window.requestAnimationFrame || ((callback) => setTimeout(callback, 16));
        frame(() => frame(resolve));
      })`, true);
      return await wc.executeJavaScript(script, true);
    } catch (err) {
      return { ok: false, error: 'js execution failed: ' + String((err && err.message) || err) };
    }
  }

  async _pointTarget(wc, x, y) {
    try {
      return await wc.executeJavaScript(`(() => {
        const x = ${JSON.stringify(Math.round(Number(x) || 0))};
        const y = ${JSON.stringify(Math.round(Number(y) || 0))};
        const el = document.elementFromPoint ? document.elementFromPoint(x, y) : null;
        if (!el) return { ok: false, code: 'TARGET_NOT_VISIBLE', error: 'No element is present at the click point.' };
        const norm = (value) => String(value || '').replace(/\\s+/g, ' ').trim();
        const tag = String(el.tagName || '').toLowerCase();
        const role = norm(el.getAttribute && el.getAttribute('role')).slice(0, 60);
        const href = String((el.href || (el.getAttribute && el.getAttribute('href'))) || '').slice(0, 300);
        const id = norm(el.id).slice(0, 120);
        const ref = norm(el.getAttribute && el.getAttribute('data-cyrene-ref')).slice(0, 40);
        const text = norm(el.innerText || el.textContent || el.getAttribute('aria-label') || el.getAttribute('title')).slice(0, 160);
        return { ok: true, x, y, signature: JSON.stringify({ tag, role, href, id, ref, text }) };
      })()`, true);
    } catch (err) {
      return { ok: false, code: 'TARGET_CHECK_FAILED', error: String((err && err.message) || err) };
    }
  }

  _targetFailure(tab, info, fallback = 'Target is unavailable.') {
    const wc = tab.view.webContents;
    return {
      ok: false,
      code: String(info && info.code || 'TARGET_UNAVAILABLE'),
      error: String(info && info.error || fallback),
      matches: Array.isArray(info && info.matches) ? info.matches : undefined,
      url: wc.getURL(),
      title: wc.getTitle(),
      tabId: tab.id,
    };
  }

  async _waitForAgentCursor(tab, x, y) {
    const result = await this.showAgentCursor(tab, x, y, { press: false });
    const fallback = result && result.first ? AGENT_CURSOR_FADE_IN_MS + 34 : AGENT_CURSOR_MOVE_MS;
    const waitMs = Math.max(0, Number(result && result.waitMs) || (result && result.moved ? fallback : 0));
    if (waitMs > 0) await new Promise((resolve) => setTimeout(resolve, waitMs));
    return result;
  }

  async _pressAgentCursor(tab, x, y) {
    const result = await this.showAgentCursor(tab, x, y, { press: true, moveDurationMs: 0 });
    if (result) await new Promise((resolve) => setTimeout(resolve, AGENT_CURSOR_PRESS_MS));
    return result;
  }

  // Wait for navigation after a click or form submit.  Listens for both
  // did-navigate (page load) and did-navigate-in-page (SPA route change).
  async _waitNav(wc) {
    const beforeUrl = wc.getURL();
    const startedAt = Date.now();
    const settleMs = 400;
    let navigated = false;
    let idleSince = 0;
    const onNav = () => { navigated = true; };
    const onSpaNav = (_e, url) => { if (url !== beforeUrl) navigated = true; };
    wc.on('did-navigate', onNav);
    wc.on('did-navigate-in-page', onSpaNav);
    await new Promise((r) => {
      const i = setInterval(() => {
        try {
          if (wc.isDestroyed()) { clearInterval(i); r(); return; }
          if (wc.getURL() !== beforeUrl) navigated = true;
          if (!wc.isLoading()) {
            if (!idleSince) idleSince = Date.now();
          } else {
            idleSince = 0;
          }
          // Give both full navigations and SPA route handlers a short, bounded
          // settle window. This prevents the agent from reading the page while
          // its first async note request is still being scheduled.
          if (idleSince && Date.now() - idleSince >= settleMs && Date.now() - startedAt >= settleMs) {
            clearInterval(i); r();
          }
        } catch (_) { clearInterval(i); r(); }
      }, 100);
      setTimeout(() => { clearInterval(i); wc.removeListener('did-navigate', onNav); wc.removeListener('did-navigate-in-page', onSpaNav); r(); }, 3000);
    });
    wc.removeListener('did-navigate', onNav);
    wc.removeListener('did-navigate-in-page', onSpaNav);
    return navigated;
  }

  async _contentState(wc) {
    try {
      return await wc.executeJavaScript(`(() => {
        const clean = (value) => String(value || '').replace(/\\s+/g, ' ').trim();
        const semantic = Array.from(document.querySelectorAll('h1,h2,[role="heading"],main,article,[role="dialog"]'))
          .filter((el) => {
            const r = el.getBoundingClientRect();
            const s = getComputedStyle(el);
            return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none';
          })
          .map((el) => clean(el.innerText || el.textContent).slice(0, 500))
          .filter(Boolean)
          .slice(0, 12);
        return { url: location.href, title: document.title, semantic: semantic.join('\\n').slice(0, 3000) };
      })()`, true);
    } catch (_) {
      return { url: wc.getURL(), title: wc.getTitle(), semantic: '' };
    }
  }

  async _waitForClickOutcome(wc, before, timeoutMs = 3000) {
    const startedAt = Date.now();
    while (Date.now() - startedAt < timeoutMs) {
      if (wc.isDestroyed()) return;
      const current = await this._contentState(wc);
      if (current.url !== before.url || current.title !== before.title || current.semantic !== before.semantic) {
        await new Promise((resolve) => setTimeout(resolve, 400));
        return;
      }
      await new Promise((resolve) => setTimeout(resolve, 100));
    }
    // A click may legitimately update no semantic region (for example, a
    // toggle). The bounded wait is sufficient; do not add another timeout.
  }

  _beginClick(tab, debounceMs = 800) {
    const now = Date.now();
    if (tab.agentClickInFlight || (tab.lastAgentClickAt && now - tab.lastAgentClickAt < debounceMs)) {
      return { ok: false, error: 'Click suppressed: this tab received another agent click too recently.', code: 'CLICK_DEBOUNCED', tabId: tab.id };
    }
    tab.agentClickInFlight = true;
    return null;
  }

  async _dispatchClick(tab, info, targetRequest = null) {
    this.invalidateSnapshot();
    const blocked = this._beginClick(tab);
    if (blocked) return blocked;
    const wc = tab.view.webContents;
    try {
      try {
        await this._setFileChooserInterception(tab, true);
      } catch (err) {
        return {
          ok: false,
          code: 'FILE_CHOOSER_GUARD_UNAVAILABLE',
          error: 'Secure file chooser interception is unavailable: ' + String((err && err.message) || err),
          url: wc.getURL(),
          title: wc.getTitle(),
          tabId: tab.id,
        };
      }
      const before = await this._contentState(wc);
      tab.lastAgentFileChooser = null;
      const chooserPromise = new Promise((resolve) => { tab.agentFileChooserResolver = resolve; });
      let candidate = info;
      let pointSignature = '';
      if (!targetRequest) {
        const initialPoint = await this._pointTarget(wc, candidate.x, candidate.y);
        if (!initialPoint || !initialPoint.ok) return this._targetFailure(tab, initialPoint);
        pointSignature = String(initialPoint.signature || '');
      }
      let ready = false;
      for (let attempt = 0; attempt < 3; attempt += 1) {
        await this._waitForAgentCursor(tab, Number(candidate.x) || 0, Number(candidate.y) || 0);
        const settled = targetRequest
          ? await this._findTarget(wc, targetRequest)
          : await this._pointTarget(wc, candidate.x, candidate.y);
        if (!settled || !settled.ok) return this._targetFailure(tab, settled);
        if (targetRequest) {
          if (settled.hitMatches !== true) {
            return this._targetFailure(tab, {
              code: 'TARGET_OBSCURED',
              error: 'The target is covered at its click point; no click was sent.',
            });
          }
          if (Number(settled.x) !== Number(candidate.x) || Number(settled.y) !== Number(candidate.y)) {
            candidate = settled;
            continue;
          }
        } else if (String(settled.signature || '') !== pointSignature) {
          return this._targetFailure(tab, {
            code: 'TARGET_CHANGED',
            error: 'The element at the coordinate changed while the pointer was moving; no click was sent.',
          });
        }

        await this._pressAgentCursor(tab, Number(candidate.x) || 0, Number(candidate.y) || 0);
        const finalTarget = targetRequest
          ? await this._findTarget(wc, targetRequest)
          : await this._pointTarget(wc, candidate.x, candidate.y);
        if (!finalTarget || !finalTarget.ok) return this._targetFailure(tab, finalTarget);
        if (targetRequest) {
          if (finalTarget.hitMatches !== true) {
            return this._targetFailure(tab, {
              code: 'TARGET_OBSCURED',
              error: 'The target became covered before the click; no click was sent.',
            });
          }
          if (Number(finalTarget.x) !== Number(candidate.x) || Number(finalTarget.y) !== Number(candidate.y)) {
            candidate = finalTarget;
            continue;
          }
          candidate = finalTarget;
        } else if (String(finalTarget.signature || '') !== pointSignature) {
          return this._targetFailure(tab, {
            code: 'TARGET_CHANGED',
            error: 'The element at the coordinate changed before the click; no click was sent.',
          });
        }
        ready = true;
        break;
      }
      if (!ready) {
        return this._targetFailure(tab, {
          code: 'TARGET_UNSTABLE',
          error: 'The target kept moving; no click was sent.',
        });
      }
      this._markAgentInput(tab);
      // Target coordinates are CSS pixels (getBoundingClientRect); input
      // events are delivered in DIP pixels, scaled by the page zoom.
      let dipWidth = 0;
      try { dipWidth = Math.max(0, Math.round(Number(tab.view.getBounds().width) || 0)); } catch (_) {}
      const zoom = await this.pageZoomOf(wc, dipWidth);
      const x = Math.round((Number(candidate.x) || 0) * zoom);
      const y = Math.round((Number(candidate.y) || 0) * zoom);
      wc.sendInputEvent({ type: 'mouseMove', x, y });
      wc.sendInputEvent({ type: 'mouseDown', x, y, button: 'left', clickCount: 1 });
      wc.sendInputEvent({ type: 'mouseUp', x, y, button: 'left', clickCount: 1 });
      const outcome = await Promise.race([
        this._waitForClickOutcome(wc, before).then(() => ({ kind: 'page' })),
        chooserPromise.then((target) => ({ kind: 'file-chooser', target })),
      ]);
      if (outcome && outcome.kind === 'file-chooser') {
        if (!outcome.target || outcome.target.error) {
          return {
            ok: false,
            code: String(outcome.target && outcome.target.code || 'FILE_CHOOSER_TARGET_UNVERIFIED'),
            error: String(outcome.target && outcome.target.error || 'The intercepted file input could not be verified.'),
            url: wc.getURL(),
            title: wc.getTitle(),
            tabId: tab.id,
          };
        }
        return {
          ok: false,
          code: 'FILE_CHOOSER_INTERCEPTED',
          error: 'A file chooser was intercepted. Use browser_upload_files with the returned chooser_id.',
          chooserId: outcome.target.chooserId,
          uploadTarget: outcome.target,
          url: wc.getURL(),
          title: wc.getTitle(),
          tabId: tab.id,
          box: info && info.box ? info.box : null,
        };
      }
      return this._finishClick(tab, candidate);
    } finally {
      tab.agentFileChooserResolver = null;
      await this._setFileChooserInterception(tab, false).catch(() => {});
      tab.agentClickInFlight = false;
      tab.lastAgentClickAt = Date.now();
    }
  }

  async prepareUpload({ chooserId = '', ref = '', tabId = '' } = {}) {
    const tab = tabId ? this.tabs.get(String(tabId)) : this.tabs.get(this.activeTabId);
    if (!tab) return { ok: false, error: 'No browser tab is open.' };
    this._pruneUploadTargets(tab);
    let target = null;
    const chooserKey = String(chooserId || '').trim();
    if (chooserKey) {
      const chooser = tab.fileChoosers.get(chooserKey);
      if (!chooser) return { ok: false, error: 'The intercepted file chooser expired or is no longer available.', code: 'FILE_CHOOSER_EXPIRED' };
      target = tab.uploadTargets.get(chooser.targetId) || null;
    } else if (ref) {
      try {
        target = await this._targetFromRef(tab, ref);
      } catch (err) {
        return { ok: false, error: String((err && err.message) || err), code: 'FILE_INPUT_NOT_FOUND' };
      }
    } else {
      return { ok: false, error: 'chooserId or ref is required.' };
    }
    if (!target) return { ok: false, error: 'The browser upload target is no longer available.', code: 'FILE_INPUT_EXPIRED' };
    if (tab.view.webContents.getURL() !== target.topUrl) {
      return { ok: false, error: 'The page changed after the file chooser was captured. Click the upload control again.', code: 'UPLOAD_PAGE_CHANGED' };
    }
    return { ok: true, target: this._publicUploadTarget(target) };
  }

  async setInputFiles({ targetId = '', files = [], tabId = '' } = {}) {
    const tab = tabId ? this.tabs.get(String(tabId)) : this.tabs.get(this.activeTabId);
    if (!tab) return { ok: false, error: 'No browser tab is open.' };
    this._pruneUploadTargets(tab);
    const target = tab.uploadTargets.get(String(targetId || ''));
    if (!target) return { ok: false, error: 'The approved browser upload target expired.', code: 'FILE_INPUT_EXPIRED' };
    const entries = Array.isArray(files) ? files : [];
    if (!entries.length || entries.length > BROWSER_UPLOAD_MAX_FILES) {
      return { ok: false, error: `Choose between 1 and ${BROWSER_UPLOAD_MAX_FILES} files.` };
    }
    if (!target.multiple && entries.length !== 1) {
      return { ok: false, error: 'This file input accepts only one file.' };
    }
    if (tab.view.webContents.getURL() !== target.topUrl) {
      return { ok: false, error: 'The page changed after approval. Upload was cancelled.', code: 'UPLOAD_PAGE_CHANGED' };
    }
    const currentFrame = await this._frameState(tab, target.frameId);
    const currentFrameUrl = (currentFrame && currentFrame.url) || target.topUrl;
    if (
      currentFrameUrl !== target.frameUrl
      || String(currentFrame && currentFrame.loaderId || '') !== String(target.frameLoaderId || '')
      || (urlOrigin(currentFrameUrl) || urlOrigin(target.topUrl)) !== target.origin
    ) {
      return { ok: false, error: 'The receiving frame changed after approval. Upload was cancelled.', code: 'UPLOAD_ORIGIN_CHANGED' };
    }
    const validatedPaths = [];
    const publicFiles = [];
    for (const entry of entries) {
      const rawFilePath = String(entry && entry.path || '').trim();
      const expectedSize = Number(entry && entry.size);
      const expectedSha256 = String(entry && entry.sha256 || '').toLowerCase();
      if (!rawFilePath || !expectedSha256) return { ok: false, error: 'File validation metadata is incomplete.' };
      const filePath = path.resolve(rawFilePath);
      let lst = null;
      try { lst = fs.lstatSync(filePath); } catch (_) { return { ok: false, error: `File is no longer available: ${path.basename(filePath)}` }; }
      if (lst.isSymbolicLink() || !lst.isFile()) return { ok: false, error: `Only regular, non-symlink files may be uploaded: ${path.basename(filePath)}` };
      if (lst.size > BROWSER_UPLOAD_MAX_FILE_BYTES || lst.size !== expectedSize) {
        return { ok: false, error: `File size changed or exceeds the upload limit: ${path.basename(filePath)}` };
      }
      const actualSha256 = await sha256File(filePath);
      if (actualSha256 !== expectedSha256) {
        return { ok: false, error: `File content changed after approval: ${path.basename(filePath)}`, code: 'UPLOAD_FILE_CHANGED' };
      }
      validatedPaths.push(filePath);
      publicFiles.push({ name: path.basename(filePath), size: lst.size, sha256: actualSha256 });
    }
    try {
      const debug = await this._ensureDebugger(tab);
      await debug.sendCommand('DOM.setFileInputFiles', {
        files: validatedPaths,
        backendNodeId: target.backendNodeId,
      });
    } catch (err) {
      return { ok: false, error: 'Failed to set browser file input: ' + String((err && err.message) || err), code: 'SET_INPUT_FILES_FAILED' };
    }
    tab.uploadTargets.delete(target.id);
    if (target.chooserId) tab.fileChoosers.delete(target.chooserId);
    return {
      ok: true,
      target: this._publicUploadTarget(target),
      files: publicFiles,
      url: tab.view.webContents.getURL(),
      title: tab.view.webContents.getTitle(),
      tabId: tab.id,
    };
  }

  async _finishClick(tab, info) {
    const activeTab = this.tabs.get(this.activeTabId) || tab;
    const openedNewTab = activeTab.id !== tab.id;
    const snapshot = await this.pageSnapshot(activeTab.id, 4000);
    return {
      ...snapshot,
      tabId: activeTab.id,
      activeTabId: activeTab.id,
      openedNewTab,
      sourceTabId: tab.id,
      sourceUrl: tab.view.webContents.getURL(),
      box: info && info.box ? info.box : null,
    };
  }

  async click({ selector, tabId = '' } = {}) {
    const tab = tabId ? this.tabs.get(String(tabId)) : this.tabs.get(this.activeTabId);
    if (!tab) return { ok: false, error: 'No page open. Call browser_navigate first.' };
    const wc = tab.view.webContents;
    // Find element via JS — coordinates are sent as real OS-level input events
    // to bypass isTrusted=false restrictions (SPAs like Vue/React reject JS clicks).
    const info = await this._findTarget(wc, { mode: 'selector', value: String(selector || '') });
    if (!info || !info.ok) return this._targetFailure(tab, info, 'Element not found.');
    // sendInputEvent dispatches trusted OS-level events.  Chromium's input
    // pipeline generates the full click chain (pointerdown → mousedown →
    // pointerup → mouseup → click) with isTrusted=true.
    return this._dispatchClick(tab, info, { mode: 'selector', value: String(selector || '') });
  }

  async clickRef({ ref, tabId = '' } = {}) {
    const tab = tabId ? this.tabs.get(String(tabId)) : this.tabs.get(this.activeTabId);
    if (!tab) return { ok: false, error: 'No page open. Call browser_navigate first.' };
    const wc = tab.view.webContents;
    const info = await this._findTarget(wc, { mode: 'ref', value: String(ref || '') });
    if (!info || !info.ok) return this._targetFailure(tab, info, 'Element not found.');
    return this._dispatchClick(tab, info, { mode: 'ref', value: String(ref || '') });
  }

  async clickText({ text, exact = false, tabId = '' } = {}) {
    const tab = tabId ? this.tabs.get(String(tabId)) : this.tabs.get(this.activeTabId);
    if (!tab) return { ok: false, error: 'No page open. Call browser_navigate first.' };
    const wc = tab.view.webContents;
    const info = await this._findTarget(wc, { mode: 'text', value: String(text || ''), exact: exact === true });
    if (!info || !info.ok) return this._targetFailure(tab, info, 'Element not found.');
    return this._dispatchClick(tab, info, { mode: 'text', value: String(text || ''), exact: exact === true });
  }

  async clickAt({ x, y, tabId = '' } = {}) {
    const tab = tabId ? this.tabs.get(String(tabId)) : this.tabs.get(this.activeTabId);
    if (!tab) return { ok: false, error: 'No page open. Call browser_navigate first.' };
    const wc = tab.view.webContents;
    const px = Math.round(Number(x));
    const py = Math.round(Number(y));
    if (!Number.isFinite(px) || !Number.isFinite(py)) return { ok: false, error: 'Invalid coordinates.', url: wc.getURL(), title: wc.getTitle(), tabId: tab.id };
    return this._dispatchClick(tab, { x: px, y: py, box: { x: px, y: py, w: 1, h: 1 } });
  }

  async _typeIntoTarget({ mode = 'selector', value = '', text = '', submit = false, tabId = '' } = {}) {
    this.invalidateSnapshot();
    const tab = tabId ? this.tabs.get(String(tabId)) : this.tabs.get(this.activeTabId);
    if (!tab) return { ok: false, error: 'No page open. Call browser_navigate first.' };
    const wc = tab.view.webContents;
    const desiredText = String(text ?? '');
    const pointerInfo = await this._findTarget(wc, { mode, value });
    if (!pointerInfo || !pointerInfo.ok) {
      return { ok: false, error: 'Element ' + ((pointerInfo && pointerInfo.error) || 'not found'), url: wc.getURL(), title: wc.getTitle(), tabId: tab.id };
    }
    await this.showAgentCursor(tab, pointerInfo.x, pointerInfo.y);
    const runPageOperation = (operation) => wc.executeJavaScript(
      buildBrowserTypeTargetScript(BROWSER_FIND_TARGET_SCRIPT, {
        mode,
        value,
        text: desiredText,
        operation,
      }),
      true,
    );
    this._markAgentInput(tab);
    let result = await runPageOperation('set-native');
    if (!result || !result.ok) return { ok: false, error: (result && result.error) || 'Unable to type into element.', url: wc.getURL(), title: wc.getTitle(), tabId: tab.id };

    let strategy = 'native-setter';
    if (!result.persisted || result.needsTrustedInput) {
      const prepared = await runPageOperation('prepare-trusted');
      if (!prepared || !prepared.ok) {
        return {
          ok: false,
          error: (prepared && prepared.error) || 'Unable to prepare the element for trusted text input.',
          url: wc.getURL(),
          title: wc.getTitle(),
          tabId: tab.id,
        };
      }
      try {
        wc.focus();
        if (desiredText) {
          await wc.insertText(desiredText);
        } else {
          wc.delete();
        }
      } catch (err) {
        return {
          ok: false,
          error: 'Trusted text input failed: ' + String((err && err.message) || err),
          url: wc.getURL(),
          title: wc.getTitle(),
          tabId: tab.id,
        };
      }
      result = await runPageOperation('verify');
      strategy = 'trusted-editor';
      if (!result || !result.ok || !result.persisted) {
        return {
          ok: false,
          error: (result && result.error) || 'The page rejected or reverted the requested text.',
          url: wc.getURL(),
          title: wc.getTitle(),
          tabId: tab.id,
        };
      }
    }

    if (submit) {
      const submitResult = await runPageOperation('submit');
      if (!submitResult || !submitResult.ok) {
        return {
          ok: false,
          error: (submitResult && submitResult.error) || 'Unable to submit the text input.',
          url: wc.getURL(),
          title: wc.getTitle(),
          tabId: tab.id,
        };
      }
      if (submitResult.needsTrustedEnter) {
        wc.focus();
        wc.sendInputEvent({ type: 'keyDown', keyCode: 'Enter' });
        wc.sendInputEvent({ type: 'keyUp', keyCode: 'Enter' });
      }
      await this._waitNav(wc);
    }
    return {
      ok: true,
      url: wc.getURL(),
      title: wc.getTitle(),
      tabId: tab.id,
      box: result.box,
      strategy,
    };
  }

  async type({ selector, text = '', submit = false, tabId = '' } = {}) {
    return this._typeIntoTarget({ mode: 'selector', value: String(selector || ''), text, submit, tabId });
  }

  async typeRef({ ref, text = '', submit = false, tabId = '' } = {}) {
    return this._typeIntoTarget({ mode: 'ref', value: String(ref || ''), text, submit, tabId });
  }

  async waitFor({ selector = '', text = '', urlContains = '', timeoutMs = 5000, tabId = '' } = {}) {
    const tab = tabId ? this.tabs.get(String(tabId)) : this.tabs.get(this.activeTabId);
    if (!tab) return { ok: false, error: 'No page open. Call browser_navigate first.' };
    const wc = tab.view.webContents;
    const deadline = Date.now() + Math.max(100, Math.min(30000, Number(timeoutMs) || 5000));
    while (Date.now() < deadline) {
      const result = await wc.executeJavaScript(`
        (() => {
          const selector = ${JSON.stringify(String(selector || ''))};
          const text = ${JSON.stringify(String(text || ''))};
          const urlContains = ${JSON.stringify(String(urlContains || ''))};
          const urlOk = !urlContains || location.href.includes(urlContains);
          const elOk = !selector || !!document.querySelector(selector);
          const textOk = !text || ((document.body && document.body.innerText) || '').includes(text);
          return { ok: urlOk && elOk && textOk, url: location.href, title: document.title || '' };
        })()
      `, true).catch((err) => ({ ok: false, error: String((err && err.message) || err), url: wc.getURL(), title: wc.getTitle() }));
      if (result && result.ok) return { ok: true, url: result.url || wc.getURL(), title: result.title || wc.getTitle(), tabId: tab.id };
      await new Promise((resolve) => setTimeout(resolve, 150));
    }
    return { ok: false, error: 'Timed out waiting for page condition.', url: wc.getURL(), title: wc.getTitle(), tabId: tab.id };
  }

  async networkLog({ tabId = '', maxEntries = 40 } = {}) {
    const tab = tabId ? this.tabs.get(String(tabId)) : this.tabs.get(this.activeTabId);
    if (!tab) return { ok: false, error: 'No browser tab is open.' };
    const wc = tab.view.webContents;
    const result = await wc.executeJavaScript(`
      (() => {
        const max = Math.max(1, Math.min(200, Number(${JSON.stringify(maxEntries)}) || 40));
        const entries = performance.getEntriesByType('resource').slice(-max).map((e) => ({
          name: String(e.name || ''),
          type: String(e.initiatorType || ''),
          durationMs: Math.round(Number(e.duration || 0)),
          transferSize: Number(e.transferSize || 0),
        }));
        return { ok: true, url: location.href, title: document.title || '', entries };
      })()
    `, true).catch((err) => ({ ok: false, error: String((err && err.message) || err), entries: [] }));
    return { ...(result || {}), tabId: tab.id };
  }

  async screenshot({ tabId = '', highResolution = false, targetWidth = 0, targetHeight = 0 } = {}) {
    const tab = tabId ? this.tabs.get(String(tabId)) : this.tabs.get(this.activeTabId);
    if (!tab) return { ok: false, error: 'No browser tab is open.' };
    let pngBase64 = '';
    if (highResolution === true) {
      try {
        const debug = await this._ensureDebugger(tab);
        const metrics = await debug.sendCommand('Page.getLayoutMetrics');
        const viewport = metrics.cssVisualViewport || metrics.visualViewport || {};
        const width = Math.max(1, Number(viewport.clientWidth) || 1);
        const height = Math.max(1, Number(viewport.clientHeight) || 1);
        const desiredWidth = Math.max(0, Number(targetWidth) || 0);
        const desiredHeight = Math.max(0, Number(targetHeight) || 0);
        const scale = Math.max(1, Math.min(4, Math.max(
          desiredWidth > 0 ? desiredWidth / width : 1,
          desiredHeight > 0 ? desiredHeight / height : 1,
        )));
        const result = await debug.sendCommand('Page.captureScreenshot', {
          format: 'png',
          fromSurface: true,
          captureBeyondViewport: false,
          clip: {
            x: Number(viewport.pageX) || 0,
            y: Number(viewport.pageY) || 0,
            width,
            height,
            scale,
          },
        });
        pngBase64 = String(result && result.data || '');
      } catch (_) {}
    }
    if (!pngBase64) {
      const image = await tab.view.webContents.capturePage();
      pngBase64 = image.toPNG().toString('base64');
    }
    let validation;
    try {
      validation = validatePngBuffer(Buffer.from(pngBase64, 'base64'));
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

  goBack() {
    const tab = this.tabs.get(this.activeTabId);
    if (tab && tab.view.webContents.canGoBack()) tab.view.webContents.goBack();
    this.emitState();
    return this.state();
  }

  goForward() {
    const tab = this.tabs.get(this.activeTabId);
    if (tab && tab.view.webContents.canGoForward()) tab.view.webContents.goForward();
    this.emitState();
    return this.state();
  }

  reload({ tabId = '' } = {}) {
    const tab = tabId ? this.tabs.get(String(tabId)) : this.tabs.get(this.activeTabId);
    if (tab) tab.view.webContents.reload();
    this.emitState();
    return this.state();
  }

  setMuted({ tabId = '', muted = false } = {}) {
    const tab = tabId ? this.tabs.get(String(tabId)) : this.tabs.get(this.activeTabId);
    if (!tab) return this.state();
    tab.view.webContents.setAudioMuted(!!muted);
    this.emitState();
    return this.state();
  }

  async scroll({ deltaX = 0, deltaY = 0, x = null, y = null, ref = '', tabId = '' } = {}) {
    this.invalidateSnapshot();
    const tab = tabId ? this.tabs.get(String(tabId)) : this.tabs.get(this.activeTabId);
    if (!tab) return { ok: false, error: 'No browser tab is open.' };
    const wc = tab.view.webContents;
    const dx = Number(deltaX);
    const dy = Number(deltaY);
    if (!Number.isFinite(dx) || !Number.isFinite(dy)) return { ok: false, error: 'Invalid scroll delta.' };

    let px = x === null || x === undefined ? NaN : Number(x);
    let py = y === null || y === undefined ? NaN : Number(y);
    if (String(ref || '').trim()) {
      const info = await this._findTarget(wc, { mode: 'ref', value: String(ref).trim() });
      if (!info || !info.ok) {
        return { ok: false, error: 'Scroll target ' + ((info && info.error) || 'not found'), url: wc.getURL(), title: wc.getTitle(), tabId: tab.id };
      }
      px = info.x;
      py = info.y;
    }
    const bounds = tab.view.getBounds();
    // Default/clamp bounds are DIP sizes; CSS viewport coordinates are
    // DIP / zoom, so probe and click coordinates must use the CSS space.
    const zoom = await this.pageZoomOf(wc, bounds.width);
    const cssWidth = Math.max(1, bounds.width / zoom);
    const cssHeight = Math.max(1, bounds.height / zoom);
    if (!Number.isFinite(px)) px = Math.floor(cssWidth / 2);
    if (!Number.isFinite(py)) py = Math.floor(cssHeight / 2);
    px = Math.max(0, Math.min(Math.max(0, cssWidth - 1), Math.round(px)));
    py = Math.max(0, Math.min(Math.max(0, cssHeight - 1), Math.round(py)));
    await this.showAgentCursor(tab, px, py);

    // Mark the nearest scrollable ancestor under the pointer so the result can
    // report whether Chromium actually moved it. The wheel event itself is sent
    // through Chromium's trusted input pipeline, matching a user's mouse/trackpad
    // and allowing nested overflow containers to scroll.
    const probeId = 'scroll_' + Date.now().toString(36) + '_' + Math.random().toString(36).slice(2);
    const before = await wc.executeJavaScript(`(() => {
      const x = ${JSON.stringify(px)};
      const y = ${JSON.stringify(py)};
      const dx = ${JSON.stringify(dx)};
      const dy = ${JSON.stringify(dy)};
      const probeId = ${JSON.stringify(probeId)};
      const root = document.scrollingElement || document.documentElement;
      const canMove = (el) => {
        if (!(el instanceof Element)) return false;
        const style = getComputedStyle(el);
        const overflowX = style.overflowX || style.overflow;
        const overflowY = style.overflowY || style.overflow;
        const scrollableX = el === root || (/^(auto|scroll|overlay)$/).test(overflowX);
        const scrollableY = el === root || (/^(auto|scroll|overlay)$/).test(overflowY);
        const canX = dx > 0
          ? scrollableX && el.scrollLeft + el.clientWidth < el.scrollWidth - 1
          : dx < 0 && scrollableX && el.scrollLeft > 1;
        const canY = dy > 0
          ? scrollableY && el.scrollTop + el.clientHeight < el.scrollHeight - 1
          : dy < 0 && scrollableY && el.scrollTop > 1;
        return canX || canY;
      };
      const parentOf = (el) => el.parentElement || (el.getRootNode && el.getRootNode().host) || null;
      let target = document.elementFromPoint(x, y);
      while (target && !canMove(target)) target = parentOf(target);
      if (!target && canMove(root)) target = root;
      if (!target) return { found: false, x, y };
      target.setAttribute('data-cyrene-scroll-probe', probeId);
      return {
        found: true,
        x,
        y,
        tag: String(target.tagName || '').toLowerCase(),
        id: String(target.id || ''),
        ref: String(target.getAttribute('data-cyrene-ref') || ''),
        scrollLeft: Number(target.scrollLeft || 0),
        scrollTop: Number(target.scrollTop || 0),
      };
    })()`, true).catch(() => ({ found: false, x: px, y: py }));

    this._markAgentInput(tab);
    // Probe coordinates above are CSS pixels; input events are delivered in
    // DIP pixels, scaled by the page zoom.
    const dipX = Math.round(px * zoom);
    const dipY = Math.round(py * zoom);
    wc.sendInputEvent({ type: 'mouseMove', x: dipX, y: dipY });
    wc.sendInputEvent({
      type: 'mouseWheel',
      x: dipX,
      y: dipY,
      // Electron follows native wheel direction (positive is left/up), while
      // browser_scroll and Playwright use positive deltas for right/down.
      deltaX: -dx,
      deltaY: -dy,
      hasPreciseScrollingDeltas: true,
      canScroll: true,
    });
    await new Promise((resolve) => setTimeout(resolve, 100));

    const after = await wc.executeJavaScript(`(() => {
      const target = document.querySelector('[data-cyrene-scroll-probe=${JSON.stringify(probeId)}]');
      if (!target) return { found: false };
      const result = {
        found: true,
        scrollLeft: Number(target.scrollLeft || 0),
        scrollTop: Number(target.scrollTop || 0),
      };
      target.removeAttribute('data-cyrene-scroll-probe');
      return result;
    })()`, true).catch(() => ({ found: false }));
    const actualDeltaX = before.found && after.found ? after.scrollLeft - before.scrollLeft : 0;
    const actualDeltaY = before.found && after.found ? after.scrollTop - before.scrollTop : 0;
    return {
      ok: true,
      moved: actualDeltaX !== 0 || actualDeltaY !== 0,
      actualDeltaX,
      actualDeltaY,
      target: before.found ? { tag: before.tag, id: before.id, ref: before.ref } : null,
      x: px,
      y: py,
      tabId: tab.id,
    };
  }

  closeAll() {
    this.hideAllAgentCursors();
    if (this.videoFullscreen.active) this.finishVideoFullscreen();
    if (this._syncTimer) clearTimeout(this._syncTimer);
    this._syncTimer = null;
    if (this._repaintTimer) clearTimeout(this._repaintTimer);
    this._repaintTimer = null;
    this._boundsTransitionToken += 1;
    this._boundsTransitioning = false;
    for (const tab of Array.from(this.tabs.values())) {
      this.detachView(tab);
      try { tab.view.webContents.close(); } catch (_) {}
    }
    this.tabs.clear();
    this.activeTabId = '';
    this.attachedTabId = '';
    this.attachedWindow = null;
    if (this.chatOverlayView && this.chatOverlayParent) {
      try { this.chatOverlayParent.removeChildView(this.chatOverlayView); } catch (_) {}
    }
    if (this.chatOverlayView && !this.chatOverlayView.webContents.isDestroyed()) {
      try { this.chatOverlayView.webContents.close(); } catch (_) {}
    }
    this.chatOverlayView = null;
    this.chatOverlayParent = null;
    this.chatOverlayState = { visible: false, running: false, showStatus: false };
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
    this.visible = false;
  }
}

function getBrowserTabManager(sessionId = activeBrowserSessionId) {
  const normalized = normalizeBrowserSessionId(sessionId);
  if (!browserTabManagers.has(normalized)) {
    browserTabManagers.set(normalized, new BrowserTabManager(normalized));
  }
  return browserTabManagers.get(normalized);
}

function activateBrowserSession(info = {}) {
  const sessionId = normalizeBrowserSessionId(info.sessionId || info.session_id);
  if (sessionId !== activeBrowserSessionId) {
    const previous = browserTabManagers.get(activeBrowserSessionId);
    if (previous) {
      previous.hideAllAgentCursors();
      previous.visible = false;
      previous.syncAttachedView();
    }
    activeBrowserSessionId = sessionId;
  }
  const manager = getBrowserTabManager(sessionId);
  manager.setContext(info);
  manager.syncAttachedView();
  manager.emitState();
  return manager;
}

function hideAllBrowserSessions() {
  for (const manager of browserTabManagers.values()) {
    manager.setBounds({ visible: false });
  }
}

function setBrowserSurfaceObscured(obscured = false) {
  browserSurfaceObscured = obscured === true;
  for (const manager of browserTabManagers.values()) {
    manager.setObscured(browserSurfaceObscured);
  }
  return getBrowserTabManager(activeBrowserSessionId).state();
}

async function setAgentCursorRunning(running) {
  agentCursorRunning = running === true;
  const updates = Array.from(browserTabManagers.values()).map((manager) => (
    manager.setAgentCursorRunning(agentCursorRunning)
  ));
  if (appUsePointerWindow && !appUsePointerWindow.isDestroyed()) {
    updates.push(appUsePointerWindow.webContents.executeJavaScript(
      agentCursorRunningCommand(agentCursorRunning),
      true,
    ).catch(() => false));
  }
  await Promise.all(updates);
  return { ok: true, running: agentCursorRunning };
}

function updateAgentCursorRunningSource(webContents, running) {
  const sourceId = Number(webContents && webContents.id);
  if (!Number.isFinite(sourceId)) return setAgentCursorRunning(running);
  if (!agentCursorRunningSources.has(sourceId) && webContents && typeof webContents.once === 'function') {
    webContents.once('destroyed', () => {
      agentCursorRunningSources.delete(sourceId);
      setAgentCursorRunning(Array.from(agentCursorRunningSources.values()).some(Boolean)).catch(() => {});
    });
  }
  agentCursorRunningSources.set(sourceId, running === true);
  return setAgentCursorRunning(Array.from(agentCursorRunningSources.values()).some(Boolean));
}

function closeAllBrowserSessions() {
  for (const manager of browserTabManagers.values()) manager.closeAll();
  browserTabManagers.clear();
  activeBrowserSessionId = '';
  browserSurfaceObscured = false;
}

function closeBrowserSession(sessionId) {
  const normalized = normalizeBrowserSessionId(sessionId);
  const manager = browserTabManagers.get(normalized);
  if (!manager) return { ok: true, sessionId: normalized, closed: false };
  manager.closeAll();
  browserTabManagers.delete(normalized);
  if (activeBrowserSessionId === normalized) activeBrowserSessionId = '';
  return { ok: true, sessionId: normalized, closed: true };
}

function getAppUseManager() {
  if (!appUseManager) {
    appUseManager = new AppUseManager({
      ownPid: process.pid,
      ownApplicationIds: ['com.cyrene.app'],
      ownAppNames: [APP_NAME],
      captureTarget: captureAppUseTarget,
      showVirtualPointer: showAppUseVirtualPointer,
      hideVirtualPointer: hideAppUseVirtualPointer,
      isHostForeground: () => Boolean(mainWindow && !mainWindow.isDestroyed() && mainWindow.isFocused()),
      focusHost: async () => { await revealMainWindow(); },
    });
    appUseManager.start();
  }
  return appUseManager;
}

function appUsePointerDesktopBounds() {
  const displays = screen.getAllDisplays();
  if (!displays.length) return { x: 0, y: 0, width: 1, height: 1 };
  const left = Math.min(...displays.map((display) => Number(display.bounds.x) || 0));
  const top = Math.min(...displays.map((display) => Number(display.bounds.y) || 0));
  const right = Math.max(...displays.map((display) => (Number(display.bounds.x) || 0) + (Number(display.bounds.width) || 0)));
  const bottom = Math.max(...displays.map((display) => (Number(display.bounds.y) || 0) + (Number(display.bounds.height) || 0)));
  return { x: left, y: top, width: Math.max(1, right - left), height: Math.max(1, bottom - top) };
}

async function showAppUseVirtualPointer({
  x = 0,
  y = 0,
  press = false,
  moveDurationMs = AGENT_CURSOR_MOVE_MS,
  target = null,
} = {}) {
  if (!app.isReady()) return;
  const desktopBounds = appUsePointerDesktopBounds();
  if (!appUsePointerWindow || appUsePointerWindow.isDestroyed()) {
    appUsePointerWindow = new BrowserWindow({
      ...desktopBounds,
      frame: false,
      transparent: true,
      resizable: false,
      movable: false,
      minimizable: false,
      maximizable: false,
      closable: false,
      focusable: false,
      skipTaskbar: true,
      alwaysOnTop: true,
      hasShadow: false,
      show: false,
      webPreferences: {
        sandbox: true,
        contextIsolation: true,
        nodeIntegration: false,
      },
    });
    appUsePointerWindow.setIgnoreMouseEvents(true, { forward: true });
    appUsePointerWindow.setAlwaysOnTop(true, 'floating');
    if (isMac) appUsePointerWindow.setVisibleOnAllWorkspaces(true, { visibleOnFullScreen: true });
    await appUsePointerWindow.loadURL(`data:text/html;charset=utf-8,${encodeURIComponent(agentCursorOverlayHtml())}`);
  } else {
    appUsePointerWindow.setBounds(desktopBounds, false);
  }
  appUsePointerOwnerTargetId = String(target && (target.target_id || target.targetId) || '');
  appUsePointerWindow.showInactive();
  return appUsePointerWindow.webContents.executeJavaScript(agentCursorCommand({
    x: (Number(x) || 0) - desktopBounds.x,
    y: (Number(y) || 0) - desktopBounds.y,
    press: press === true,
    moveDurationMs,
    running: agentCursorRunning,
  }), true);
}

async function hideAppUseVirtualPointer({ target = null } = {}) {
  if (!appUsePointerWindow || appUsePointerWindow.isDestroyed()) return false;
  const targetId = String(target && (target.target_id || target.targetId) || '');
  if (targetId && appUsePointerOwnerTargetId && targetId !== appUsePointerOwnerTargetId) return false;
  appUsePointerOwnerTargetId = '';
  return appUsePointerWindow.webContents.executeJavaScript(agentCursorHideCommand(), true).catch(() => false);
}

async function captureAppUseTarget(target) {
  const sources = await desktopCapturer.getSources({
    types: ['window'],
    thumbnailSize: { width: 1920, height: 1200 },
    fetchWindowIcons: false,
  });
  const nativeId = String((target && target.windowId) || '');
  const title = String((target && target.windowTitle) || '');
  const source = sources.find((candidate) => {
    const parts = String(candidate.id || '').split(':');
    return parts[0] === 'window' && parts[1] === nativeId;
  }) || sources.find((candidate) => title && String(candidate.name || '') === title);
  if (!source || source.thumbnail.isEmpty()) {
    throw new Error('The connected application window could not be captured.');
  }
  const size = source.thumbnail.getSize();
  return {
    imageBase64: source.thumbnail.toPNG().toString('base64'),
    pixelHash: require('crypto').createHash('sha256').update(source.thumbnail.toBitmap()).digest('hex'),
    mimeType: 'image/png',
    width: size.width,
    height: size.height,
  };
}

function browserRpcSessionId(args = {}, context = {}) {
  if (Object.prototype.hasOwnProperty.call(context || {}, 'sessionId')) {
    return normalizeBrowserSessionId(context.sessionId);
  }
  if (Object.prototype.hasOwnProperty.call(context || {}, 'session_id')) {
    return normalizeBrowserSessionId(context.session_id);
  }
  if (Object.prototype.hasOwnProperty.call(args || {}, 'sessionId')) {
    return normalizeBrowserSessionId(args.sessionId);
  }
  if (Object.prototype.hasOwnProperty.call(args || {}, 'session_id')) {
    return normalizeBrowserSessionId(args.session_id);
  }
  return activeBrowserSessionId;
}

async function handleBrowserRpc(method, args, context = {}) {
  if (method === 'clearStorage') {
    closeAllBrowserSessions();
    const browserSessions = new Set([
      session.fromPartition(BROWSER_PARTITION),
      session.defaultSession,
    ]);
    for (const browserSession of browserSessions) {
      await browserSession.clearStorageData();
      if (typeof browserSession.clearCache === 'function') await browserSession.clearCache();
      if (typeof browserSession.clearAuthCache === 'function') await browserSession.clearAuthCache();
    }
    resetDesktopSettings();
    return { ok: true, cleared: true, desktopSettingsReset: true };
  }
  if (method === 'setContext') {
    return activateBrowserSession(args || {}).state();
  }
  if (method === 'closeSession') {
    return closeBrowserSession(browserRpcSessionId(args, context));
  }
  const manager = getBrowserTabManager(browserRpcSessionId(args, context));
  const roundId = String(context.roundId || context.round_id || args && (args.roundId || args.round_id) || '').trim();
  const agentRequest = context.agentRequest === true;
  if (method === 'finishRound') return manager.finishAgentRound(roundId);
  if (roundId) {
    if (agentRequest) manager.beginAgentRound(roundId);
    else manager.setContext({ roundId });
  }
  switch (method) {
    case 'state':
      return manager.state();
    case 'setBounds':
      return manager.setBounds(args || {});
    case 'setChatOverlay':
      return manager.setChatOverlay(args || {});
    case 'setTabPicker':
      return manager.setTabPicker(args || {});
    case 'setObscured':
      return setBrowserSurfaceObscured(args && args.obscured);
    case 'createTab':
      await manager.createTab({
        ...(args || {}),
        agentOwnerRoundId: agentRequest ? roundId : '',
      });
      return manager.state();
    case 'activateTab':
      return manager.activateTab(args && args.tabId);
    case 'closeTab':
      return manager.closeTab(args && args.tabId);
    case 'navigate':
      return manager.navigate({
        ...(args || {}),
        agentOwnerRoundId: agentRequest ? roundId : '',
      });
    case 'snapshot':
      return manager.pageSnapshot(args && args.tabId, args && args.maxChars);
    case 'inspect':
      return manager.inspect(args || {});
    case 'visibleLinkMatches':
      return manager.visibleLinkMatches(args || {});
    case 'navigationGuard':
      return manager.navigationGuard(args || {});
    case 'click':
      return manager.click(args || {});
    case 'clickRef':
      return manager.clickRef(args || {});
    case 'clickText':
      return manager.clickText(args || {});
    case 'clickAt':
      return manager.clickAt(args || {});
    case 'type':
      return manager.type(args || {});
    case 'typeRef':
      return manager.typeRef(args || {});
    case 'waitFor':
      return manager.waitFor(args || {});
    case 'networkLog':
      return manager.networkLog(args || {});
    case 'screenshot':
      return manager.screenshot(args || {});
    case 'prepareUpload':
      return manager.prepareUpload(args || {});
    case 'setInputFiles':
      return manager.setInputFiles(args || {});
    case 'goBack':
      return manager.goBack();
    case 'goForward':
      return manager.goForward();
    case 'reload':
      return manager.reload(args || {});
    case 'setMuted':
      return manager.setMuted(args || {});
    case 'scroll':
      return manager.scroll(args || {});
    default:
      return { ok: false, error: `Unknown browser RPC method: ${method}` };
  }
}

async function handleAppUseRpc(method, args) {
  return getAppUseManager().handle(method, args || {});
}

async function handleHostRpc(method, args) {
  return getHostControl().handle(method, args || {});
}

function startElectronRpcServer() {
  if (electronRpcServer && electronRpcPort) return Promise.resolve(electronRpcPort);
  const MAX_RETRIES = 3;
  function attempt(retriesLeft) {
    return new Promise((resolve, reject) => {
      const server = http.createServer((req, res) => {
        const rpcPath = String(req.url || '');
        if (req.method !== 'POST' || !['/browser/rpc', '/app/rpc', '/host/rpc'].includes(rpcPath)) {
          res.writeHead(404, { 'Content-Type': 'application/json' });
          res.end(JSON.stringify({ ok: false, error: 'not_found' }));
          return;
        }
        if (req.headers['x-cyrene-token'] !== AUTH_TOKEN) {
          res.writeHead(403, { 'Content-Type': 'application/json' });
          res.end(JSON.stringify({ ok: false, error: 'forbidden' }));
          return;
        }
        let body = '';
        req.on('data', (chunk) => {
          body += chunk;
          if (body.length > 1024 * 1024) req.destroy();
        });
        req.on('end', async () => {
          try {
            const payload = JSON.parse(body || '{}');
            const result = rpcPath === '/app/rpc'
              ? await handleAppUseRpc(String(payload.method || ''), payload.args || {})
              : rpcPath === '/host/rpc'
                ? await handleHostRpc(String(payload.method || ''), payload.args || {})
                : await handleBrowserRpc(
                  String(payload.method || ''),
                  payload.args || {},
                  {
                    sessionId: Object.prototype.hasOwnProperty.call(payload, 'sessionId') ? payload.sessionId : '',
                    roundId: payload.roundId || '',
                    agentRequest: true,
                  }
                );
            res.writeHead(200, { 'Content-Type': 'application/json' });
            res.end(JSON.stringify(result || { ok: true }));
          } catch (err) {
            res.writeHead(500, { 'Content-Type': 'application/json' });
            res.end(JSON.stringify({ ok: false, error: String((err && err.message) || err) }));
          }
        });
      });
      server.on('error', (err) => {
        if (retriesLeft > 0) {
          console.warn(`[electron] RPC server failed to start (${err.message}), retrying (${retriesLeft} left)...`);
          setTimeout(() => attempt(retriesLeft - 1).then(resolve, reject), 500);
        } else {
          reject(err);
        }
      });
      server.listen(0, '127.0.0.1', () => {
        electronRpcServer = server;
        electronRpcPort = server.address().port;
        resolve(electronRpcPort);
      });
    });
  }
  return attempt(MAX_RETRIES);
}

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

function getTrayIconImage() {
  if (isMac) {
    const mac1x = findExistingPath([
      path.join(__dirname, '..', 'build', 'tray-mac.png'),
      path.join(process.resourcesPath || '', 'build', 'tray-mac.png'),
    ]);
    const mac2x = findExistingPath([
      path.join(__dirname, '..', 'build', 'tray-mac@2x.png'),
      path.join(process.resourcesPath || '', 'build', 'tray-mac@2x.png'),
    ]);
    if (mac1x) {
      try {
        const image = nativeImage.createEmpty();
        image.addRepresentation({
          scaleFactor: 1,
          dataURL: `data:image/png;base64,${fs.readFileSync(mac1x).toString('base64')}`,
        });
        if (mac2x) {
          image.addRepresentation({
            scaleFactor: 2,
            dataURL: `data:image/png;base64,${fs.readFileSync(mac2x).toString('base64')}`,
          });
        }
        if (!image.isEmpty()) return image;
      } catch (_) {}
    }
  }

  const candidates = [
    path.join(__dirname, '..', 'build', 'tray.png'),
    isWindows ? path.join(__dirname, '..', 'build', 'icon.ico') : '',
    path.join(__dirname, '..', 'build', 'icon.png'),
    path.join(process.resourcesPath || '', 'build', 'tray.png'),
    isWindows ? path.join(process.resourcesPath || '', 'build', 'icon.ico') : '',
    path.join(process.resourcesPath || '', 'build', 'icon.png'),
  ];
  const iconPath = findExistingPath(candidates);
  if (!iconPath) return null;
  try {
    const image = nativeImage.createFromPath(iconPath);
    if (image.isEmpty()) return null;
    return image.resize({ width: isMac ? 18 : 32, height: isMac ? 18 : 32 });
  } catch (_) {
    return null;
  }
}

function findExistingPath(candidates) {
  return candidates.find((candidate) => {
    try {
      return candidate && fs.existsSync(candidate);
    } catch (_) {
      return false;
    }
  });
}

function normalizeDesktopLanguage(value) {
  const lang = String(value || '').trim().toLowerCase();
  if (lang === 'en' || lang.startsWith('en-')) return 'en';
  if (lang === 'zh' || lang.startsWith('zh-')) return 'zh';
  return '';
}

function getFallbackDesktopLanguage() {
  try {
    return normalizeDesktopLanguage(app.getLocale()) || 'en';
  } catch (_) {
    return 'en';
  }
}

function getDesktopLanguage(settings) {
  return normalizeDesktopLanguage(settings && settings.language) || getFallbackDesktopLanguage();
}

function desktopT(key, settings) {
  const lang = getDesktopLanguage(settings);
  const dict = DESKTOP_TRANSLATIONS[lang] || DESKTOP_TRANSLATIONS.en;
  return dict[key] || DESKTOP_TRANSLATIONS.en[key] || key;
}

// ---------------------------------------------------------------------------
// macOS 应用菜单（i18n 原生菜单栏）
// ---------------------------------------------------------------------------

function sendToMainWindow(channel, ...args) {
  try {
    if (mainWindow && !mainWindow.isDestroyed()) {
      mainWindow.webContents.send(channel, ...args);
    }
  } catch (err) {
    console.warn('[electron] sendToMainWindow failed:', err);
  }
}

function rebuildApplicationMenu(maybeSettings) {
  // macOS 才需要自定义菜单；Win/Linux 已设为 null
  if (!isMac) return;
  const settings = maybeSettings || readDesktopSettings();
  const lang = getDesktopLanguage(settings);
  const t = (key) => {
    const dict = MENU_TRANSLATIONS[lang] || MENU_TRANSLATIONS.en;
    return dict[key] || MENU_TRANSLATIONS.en[key] || key;
  };

  const template = [
    {
      label: APP_NAME,
      submenu: [
        { role: 'about', label: t('about') },
        { type: 'separator' },
        { label: t('settings'), accelerator: 'Cmd+,', click: () => sendToMainWindow('menu:action', 'open-settings') },
        { type: 'separator' },
        { role: 'services', label: t('services') },
        { type: 'separator' },
        { role: 'hide', label: t('hide') },
        { role: 'hideOthers', label: t('hideOthers') },
        { role: 'unhide', label: t('showAll') },
        { type: 'separator' },
        { role: 'quit', label: t('quit') },
      ],
    },
    {
      label: t('file'),
      submenu: [
        { label: t('newChat'), accelerator: 'CmdOrCtrl+N', click: () => sendToMainWindow('menu:action', 'new-chat') },
        { label: t('newProject'), accelerator: 'CmdOrCtrl+Shift+N', click: () => sendToMainWindow('menu:action', 'new-project') },
        { label: t('newTask'), accelerator: 'CmdOrCtrl+Alt+N', click: () => sendToMainWindow('menu:action', 'new-task') },
        { type: 'separator' },
        { role: 'close', label: t('closeWindow') },
      ],
    },
    {
      label: t('edit'),
      submenu: [
        { role: 'undo', label: t('undo') },
        { role: 'redo', label: t('redo') },
        { type: 'separator' },
        { role: 'cut', label: t('cut') },
        { role: 'copy', label: t('copy') },
        { role: 'paste', label: t('paste') },
        { role: 'selectAll', label: t('selectAll') },
      ],
    },
    {
      label: t('view'),
      submenu: [
        { role: 'reload', label: t('reload') },
        ...(isDev ? [
          { role: 'forceReload', label: t('forceReload') },
          { role: 'toggleDevTools', label: t('toggleDevTools') },
          { type: 'separator' },
        ] : []),
        { role: 'zoomIn', label: t('zoomIn') },
        { role: 'zoomOut', label: t('zoomOut') },
        { role: 'resetZoom', label: t('resetZoom') },
        { type: 'separator' },
        { label: t('toggleTheme'), accelerator: 'CmdOrCtrl+Shift+T', click: () => sendToMainWindow('menu:action', 'toggle-theme') },
        { label: t('toggleSidebar'), accelerator: 'CmdOrCtrl+B', click: () => sendToMainWindow('menu:action', 'toggle-sidebar') },
        { type: 'separator' },
        { role: 'togglefullscreen', label: t('toggleFullScreen') },
      ],
    },
    {
      label: t('windowMenu'),
      submenu: [
        { role: 'minimize', label: t('minimize') },
        { role: 'zoom', label: t('zoom') },
        { type: 'separator' },
        { role: 'front', label: t('bringAllToFront') },
      ],
    },
    {
      label: t('help'),
      submenu: [
        { label: t('workspaceAbout'), click: () => sendToMainWindow('menu:action', 'open-about') },
        { type: 'separator' },
        { label: t('documentation'), click: () => shell.openExternal('https://github.com/ikerrrrrrrrrrr/Cyrene#readme').catch(function () {}) },
        { label: t('feedback'), click: () => shell.openExternal('https://github.com/ikerrrrrrrrrrr/Cyrene/issues/new').catch(function () {}) },
      ],
    },
  ];

  Menu.setApplicationMenu(Menu.buildFromTemplate(template));
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
      settingsRevision: Number.isInteger(parsed.settingsRevision) && parsed.settingsRevision >= 0 ? parsed.settingsRevision : 0,
      launchAtLogin: parsed.launchAtLogin === true,
      runInBackground,
      language: normalizeDesktopLanguage(parsed.language),
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
    settingsRevision: Number.isInteger(settings.settingsRevision) && settings.settingsRevision >= 0 ? settings.settingsRevision : 0,
    launchAtLogin: settings.launchAtLogin === true,
    runInBackground,
    language: normalizeDesktopLanguage(settings.language),
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
    language: normalizeDesktopLanguage(stored.language),
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

function saveDesktopSettings(updates, expectedRevision) {
  const current = readDesktopSettings();
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
  next.quickChatShortcut = normalizeQuickChatShortcut(next.quickChatShortcut);
  next.language = normalizeDesktopLanguage(next.language);
  // Quick chat depends on background residency — turning residency off also
  // disables it (the UI gates the toggle, but enforce it here too).
  next.quickChatEnabled = next.runInBackground === true && next.quickChatEnabled === true;

  // Persist settings before attempting the shortcut side-effect, so a
  // registration failure doesn't discard a language or other setting change.
  writeDesktopSettings(next);
  applyLaunchAtLogin(next.launchAtLogin);
  syncTrayWithSettings(next);
  if (getDesktopLanguage(current) !== getDesktopLanguage(next)) {
    rebuildApplicationMenu(next);
  }

  let shortcutUpdateOk = true;
  if (next.quickChatEnabled) {
    // Register (or re-register) the global shortcut. Only attempt it when the
    // binding is missing or changed so an unrelated toggle doesn't churn it.
    if (
      next.quickChatShortcut !== registeredQuickChatShortcut
      || !globalShortcut.isRegistered(next.quickChatShortcut)
    ) {
      shortcutUpdateOk = registerQuickChatShortcut(next.quickChatShortcut);
    }
  } else {
    // Disabled (or residency off) — release the shortcut and tear down the
    // transient window so nothing keeps the app resident for it.
    unregisterQuickChatShortcut();
    destroyQuickChatWindow();
  }

  return {
    ...getDesktopSettings(),
    shortcutUpdateOk,
  };
}

function resetDesktopSettings() {
  const current = readDesktopSettings();
  const next = {
    ...DEFAULT_DESKTOP_SETTINGS,
    settingsRevision: current.settingsRevision + 1,
  };
  writeDesktopSettings(next);
  applyLaunchAtLogin(false);
  unregisterQuickChatShortcut();
  destroyQuickChatWindow();
  syncTrayWithSettings(next);
  rebuildApplicationMenu(next);
  return next;
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
    return [
      path.join(__dirname, '..', 'src', 'cyrene', 'local_cli.py'),
      '--workbench',
      '--electron-mode',
    ];
  }
  // Frozen mode: trampoline with --launch-web + --electron
  return ['--launch-web', '--electron'];
}

function getCurrentAppExecutablePath() {
  // Electron's executable lives inside AppImage's temporary SquashFS mount
  // (for example /tmp/.mount_Cyrene.../cyrene).  That path is read-only and
  // disappears when the app exits, so it cannot be used as the updater's
  // replacement target.  AppImage exports the original image path explicitly.
  if (isLinux && process.env.APPIMAGE) {
    return path.resolve(process.env.APPIMAGE);
  }
  // electron-builder's Windows portable target expands into a temporary
  // directory before launching. Pass the original single-file executable to
  // the backend/updater instead of that disposable extracted copy.
  if (isWindows && process.env.PORTABLE_EXECUTABLE_FILE) {
    return path.resolve(process.env.PORTABLE_EXECUTABLE_FILE);
  }
  return app.getPath('exe');
}

function spawnPython() {
  if (pythonProcess) return;
  clearCliConnection();
  const binaryPath = getPythonBinaryPath();
  const args = getPythonArgs();
  const cwd = isDev ? path.join(__dirname, '..') : undefined;
  const childEnv = {
    ...process.env,
    CYRENE_APP_EXECUTABLE: getCurrentAppExecutablePath(),
    CYRENE_AUTH_TOKEN: AUTH_TOKEN,
    CYRENE_ELECTRON_RPC_PORT: electronRpcPort ? String(electronRpcPort) : '',
    CYRENE_ELECTRON_RPC_TOKEN: AUTH_TOKEN,
  };
  if (!isDev) {
    // Respect explicit path overrides for portable installs, diagnostics and
    // isolated packaged-app smoke tests. Normal launches have no overrides and
    // continue to use Electron's platform-specific application directories.
    childEnv.CYRENE_USER_DATA_DIR = process.env.CYRENE_USER_DATA_DIR || getCyreneUserDataDir();
    childEnv.CYRENE_CACHE_DIR = process.env.CYRENE_CACHE_DIR || getCyreneCacheDir();
    childEnv.CYRENE_TEMP_DIR = process.env.CYRENE_TEMP_DIR || getCyreneTempDir();
    childEnv.CYRENE_INSTALL_RESOURCES_DIR = process.resourcesPath;
  }

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
    // Scan each line for PORT=<number>
    const match = text.match(/^PORT=(\d+)$/m);
    if (match) {
      port = parseInt(match[1], 10);
      // Store globally so a later waitForPort() can resolve even if the
      // PORT event arrived before any window registered a pending resolver
      // (e.g. launch-at-login hidden startup).
      backendPort = port;
      publishCliConnection(port);
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
    clearCliConnection();
    if (isBackendRestarting) {
      isBackendRestarting = false;
      isShuttingDown = false;
      spawnPython();
      createMainWindow().catch((err) => {
        appendErrorLog(`[electron] Failed to recreate window after backend restart: ${String(err && err.stack || err)}\n`);
      });
    } else if (code === 42) {
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
        + `If this keeps happening, check cyrene_error.log in ${getCyreneTempDir()}`
      );
      app.quit();
    }
  });
}

function restartPythonBackend() {
  if (!pythonProcess) {
    spawnPython();
    createMainWindow().catch(() => {});
    return;
  }
  isBackendRestarting = true;
  const proc = pythonProcess;
  // Recreate renderer surfaces after the backend reports its new port. This
  // also invalidates every old UI tree and ui_instance_id.
  if (mainWindow && !mainWindow.isDestroyed()) mainWindow.destroy();
  mainWindow = null;
  if (quickChatWindow && !quickChatWindow.isDestroyed()) quickChatWindow.destroy();
  quickChatWindow = null;
  quickChatWindowReady = null;
  try {
    if (isWindows) {
      spawn('taskkill', ['/pid', String(proc.pid), '/f', '/t'], {
        stdio: ['ignore', 'pipe', 'pipe'], windowsHide: true,
      });
    } else {
      proc.kill('SIGTERM');
      setTimeout(() => {
        try { if (proc.exitCode === null) proc.kill('SIGKILL'); } catch (_) {}
      }, 5000);
    }
  } catch (_) {
    isBackendRestarting = false;
  }
}

function killPython() {
  if (!pythonProcess) return;
  isShuttingDown = true;
  const proc = pythonProcess;
  pythonProcess = null;
  clearCliConnection();

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

// Inject the shared X-Cyrene-Token header only on requests to the exact local
// backend port. The wildcard filter is required because the backend port is
// discovered after this listener is installed; the callback must not leak the
// renderer-hidden token to an unrelated process listening on another loopback
// port. Document loads, fetch, SSE, and WebSocket upgrades all pass through
// onBeforeSendHeaders. Must be registered BEFORE the window loads the URL.
function installAuthHeaderInjector() {
  session.defaultSession.webRequest.onBeforeSendHeaders(
    { urls: ['http://127.0.0.1:*/*', 'ws://127.0.0.1:*/*'] },
    (details, callback) => {
      let isLocalBackend = false;
      try {
        const target = new URL(String(details.url || ''));
        isLocalBackend = target.hostname === '127.0.0.1'
          && target.port === String(backendPort || '');
      } catch (_) {}
      if (!isLocalBackend) {
        callback({ requestHeaders: details.requestHeaders });
        return;
      }
      const requestHeaders = { ...details.requestHeaders, 'X-Cyrene-Token': AUTH_TOKEN };
      callback({ requestHeaders });
    }
  );

  // Keep the renderer permission boundary closed except for audio-only capture
  // requested by Cyrene's own loopback origin. Browser tabs use a separate
  // partition and remain unable to request microphone or camera access.
  session.defaultSession.setPermissionRequestHandler((webContents, permission, callback, details = {}) => {
    let isLocalBackend = false;
    try {
      const target = new URL(String((webContents && webContents.getURL()) || details.requestingUrl || ''));
      isLocalBackend = target.hostname === '127.0.0.1' && target.port === String(backendPort || '');
    } catch (_) {}
    const mediaTypes = Array.isArray(details.mediaTypes) ? details.mediaTypes : [];
    const audioOnly = mediaTypes.length > 0 && mediaTypes.every((mediaType) => mediaType === 'audio');
    callback(permission === 'media' && isLocalBackend && audioOnly);
  });
  installBrowserSessionGuards();
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
  // 页面导航/刷新时隐藏原生浏览器视图（但保留 tabs）。
  // 只在导航允许（即目标为本地后端）时隐藏，防止导航被阻止后浏览器不恢复。
  window.webContents.on('will-navigate', (event, navigationUrl) => {
    try {
      const target = new URL(navigationUrl);
      if (target.hostname !== '127.0.0.1' || target.port !== String(port)) {
        event.preventDefault();
        return;
      }
    } catch (_) {
      event.preventDefault();
      return;
    }
    // Navigation allowed — hide the browser view
    hideAllBrowserSessions();
  });
  // did-start-navigation 补充 will-navigate 不触发的场景（Cmd+R 等），
  // 但排除 SPA 同文档导航（hash 变更 / pushState）。
  window.webContents.on('did-start-navigation', (event, url, isInPlace, isMainFrame) => {
    if (isInPlace) return;
    if (isMainFrame) hideAllBrowserSessions();
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
    // Compact-but-roomy idle height: tall enough that the composer's upward
    // permission / slash menus always fit above it. The renderer grows the
    // window once on the first send (see 'quick-chat:resize') so the
    // conversation has space, and the user can freely resize from there — the
    // flex layout reflows the transcript to whatever height they pick.
    height: 460,
    minWidth: 560,
    minHeight: 400,
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
      backgroundThrottling: true,
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
    await getAppUseManager().captureQuickChatOrigin().catch(() => {});
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

function findDesktopSmokeNode(node, predicate) {
  if (!node || typeof node !== 'object') return null;
  if (predicate(node)) return node;
  for (const child of Array.isArray(node.children) ? node.children : []) {
    const found = findDesktopSmokeNode(child, predicate);
    if (found) return found;
  }
  return null;
}

async function waitForDesktopSmokeTree(uiInstanceId, predicate, label, timeoutMs = 12000) {
  const deadline = Date.now() + timeoutMs;
  let last = null;
  while (Date.now() < deadline) {
    last = await getHostControl().requestSurface(
      uiInstanceId,
      'snapshot',
      { max_depth: 12 },
      2500,
    );
    if (last && last.ok !== false && predicate(last)) return last;
    await new Promise((resolve) => setTimeout(resolve, 100));
  }
  throw new Error(`Timed out waiting for semantic UI state ${label}: ${JSON.stringify(last)}`);
}

async function runDesktopSmokeAction(uiInstanceId, tree, node, actionId, input = {}) {
  const nodeId = String(node && node.node_id || '');
  let currentTree = tree;
  let currentNode = node;
  for (let attempt = 0; attempt < 20; attempt += 1) {
    const action = Array.isArray(currentNode && currentNode.actions)
      ? currentNode.actions.find((item) => item && item.action_id === actionId)
      : null;
    if (!currentNode || !action) {
      throw new Error(`Semantic smoke action is unavailable: ${String(actionId || '')}`);
    }
    const result = await getHostControl().handle('ui.gesture.execute_current', {
      uiInstanceId,
      snapshot_id: currentTree.snapshot_id,
      revision: currentTree.revision,
      node_id: nodeId,
      action_id: actionId,
      input,
    });
    if (result && result.ok !== false) return result;
    if (!result || result.error !== 'stale_snapshot') {
      // A lost acknowledgement may follow an already executed action. Only a
      // conclusive stale-snapshot rejection is safe for this smoke driver to retry.
      throw new Error(`Semantic smoke action failed: ${JSON.stringify(result)}`);
    }
    currentTree = await waitForDesktopSmokeTree(
      uiInstanceId,
      (candidate) => !!findDesktopSmokeNode(candidate.root, (item) => (
        item.node_id === nodeId
        && Array.isArray(item.actions)
        && item.actions.some((candidateAction) => candidateAction.action_id === actionId)
      )),
      `fresh ${nodeId}.${actionId}`,
    );
    currentNode = findDesktopSmokeNode(
      currentTree.root,
      (item) => item.node_id === nodeId,
    );
  }
  throw new Error(`Semantic smoke action stayed stale: ${nodeId}.${actionId}`);
}

async function runDesktopSettingsSmokeTest() {
  const before = await getHostControl().handle('desktop.settings.get', {});
  if (!before || before.ok === false || !before.settings) {
    throw new Error(`Desktop settings smoke read failed: ${JSON.stringify(before)}`);
  }
  const initialRevision = Number(before.settings.settingsRevision);
  if (!Number.isInteger(initialRevision) || initialRevision < 0) {
    throw new Error(`Desktop settings smoke received invalid revision: ${JSON.stringify(before)}`);
  }
  const initialLanguage = String(before.settings.language || '');
  const smokeLanguage = initialLanguage === 'en' ? 'zh' : 'en';
  const changed = await getHostControl().handle('desktop.settings.update', {
    changes: { language: smokeLanguage },
    expectedRevision: initialRevision,
  });
  if (
    !changed
    || changed.ok === false
    || !changed.settings
    || changed.settings.language !== smokeLanguage
    || changed.settings.settingsRevision !== initialRevision + 1
  ) {
    throw new Error(`Desktop settings smoke update failed: ${JSON.stringify(changed)}`);
  }
  const stale = await getHostControl().handle('desktop.settings.update', {
    changes: { language: initialLanguage },
    expectedRevision: initialRevision,
  });
  if (!stale || stale.ok !== false || stale.error !== 'revision_conflict') {
    throw new Error(`Desktop settings smoke accepted a stale revision: ${JSON.stringify(stale)}`);
  }
  const restored = await getHostControl().handle('desktop.settings.update', {
    changes: { language: initialLanguage },
    expectedRevision: changed.settings.settingsRevision,
  });
  if (
    !restored
    || restored.ok === false
    || !restored.settings
    || restored.settings.language !== initialLanguage
    || restored.settings.settingsRevision !== initialRevision + 2
  ) {
    throw new Error(`Desktop settings smoke restore failed: ${JSON.stringify(restored)}`);
  }
  return 'desktop_settings_cas';
}

async function runShortcutSettingsSmokeTest(window) {
  const result = await window.webContents.executeJavaScript(`(async () => {
    const read = async () => {
      const response = await fetch('/api/settings/namespaces/shortcuts');
      if (!response.ok) throw new Error('shortcut read failed');
      return response.json();
    };
    const write = async (bindings, expectedRevision) => {
      const response = await fetch('/api/settings/namespaces/shortcuts', {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          changes: { shortcut_bindings: bindings },
          expected_revision: expectedRevision,
        }),
      });
      const payload = await response.json();
      if (!response.ok) throw new Error('shortcut write failed: ' + JSON.stringify(payload));
      return payload;
    };
    const waitForBinding = async (action, keys) => {
      const deadline = Date.now() + 4000;
      while (Date.now() < deadline) {
        const service = window.CyreneUI.require('shortcuts');
        if (JSON.stringify(service.get(action)) === JSON.stringify(keys)) return true;
        await new Promise((resolve) => setTimeout(resolve, 50));
      }
      return false;
    };
    const before = await read();
    const original = { ...((before.values && before.values.shortcut_bindings) || {}) };
    const userKeys = ['mod', 'ctrl', 'alt', 'shift', 'F11'];
    const smokeKeys = ['mod', 'ctrl', 'alt', 'shift', 'F12'];
    const userChanged = await write({ 'new-chat': userKeys }, before.revision);
    if (!(await waitForBinding('new-chat', userKeys))) throw new Error('user shortcut renderer sync failed');
    const staleResponse = await fetch('/api/settings/namespaces/shortcuts', {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        changes: { shortcut_bindings: { search: smokeKeys } },
        expected_revision: before.revision,
      }),
    });
    if (staleResponse.status !== 409) {
      throw new Error('stale shortcut update was not rejected: ' + await staleResponse.text());
    }
    const afterStale = await read();
    if (JSON.stringify(afterStale.values.shortcut_bindings['new-chat']) !== JSON.stringify(userKeys)) {
      throw new Error('stale Agent update changed the user shortcut');
    }
    const changed = await write({ search: smokeKeys }, userChanged.revision);
    if (!(await waitForBinding('search', smokeKeys))) throw new Error('shortcut renderer sync failed');
    if (!(await waitForBinding('new-chat', userKeys))) throw new Error('Agent update erased user shortcut');
    const restored = await write({
      search: original.search || null,
      'new-chat': original['new-chat'] || null,
    }, changed.revision);
    const expected = original.search || ['mod', 'K'];
    if (!(await waitForBinding('search', expected))) throw new Error('shortcut renderer restore failed');
    const expectedNewChat = original['new-chat'] || ['mod', 'N'];
    if (!(await waitForBinding('new-chat', expectedNewChat))) throw new Error('user shortcut restore failed');
    return {
      ok: restored.ok === true,
      changedRevision: changed.revision,
      restoredRevision: restored.revision,
    };
  })()`, true);
  if (
    !result
    || result.ok !== true
    || !Number.isInteger(result.changedRevision)
    || result.restoredRevision !== result.changedRevision + 1
  ) {
    throw new Error(`Shortcut settings smoke failed: ${JSON.stringify(result)}`);
  }
  return 'shortcut_settings_sync';
}

function isDesktopOnboardingTree(candidate) {
  return !!(
    candidate
    && candidate.surface
    && candidate.surface.kind === 'main'
    && findDesktopSmokeNode(candidate.root, (node) => node.node_id === 'onboarding')
    && findDesktopSmokeNode(candidate.root, (node) => node.node_id === 'onboarding_custom_model_source')
    && findDesktopSmokeNode(candidate.root, (node) => node.node_id === 'onboarding_oauth_source')
  );
}

async function runDesktopOnboardingSmokeTest(window, uiInstanceId, initialTree) {
  let tree = initialTree;
  const oauthSource = findDesktopSmokeNode(
    tree.root,
    (node) => node.node_id === 'onboarding_oauth_source',
  );
  await runDesktopSmokeAction(uiInstanceId, tree, oauthSource, 'invoke');
  tree = await waitForDesktopSmokeTree(
    uiInstanceId,
    (candidate) => {
      const source = findDesktopSmokeNode(
        candidate.root,
        (node) => node.node_id === 'onboarding_oauth_source',
      );
      return isDesktopOnboardingTree(candidate)
        && !!source
        && !!(source.state && source.state.pressed);
    },
    'onboarding OAuth source',
  );

  const customSource = findDesktopSmokeNode(
    tree.root,
    (node) => node.node_id === 'onboarding_custom_model_source',
  );
  await runDesktopSmokeAction(uiInstanceId, tree, customSource, 'invoke');
  tree = await waitForDesktopSmokeTree(
    uiInstanceId,
    (candidate) => {
      const source = findDesktopSmokeNode(
        candidate.root,
        (node) => node.node_id === 'onboarding_custom_model_source',
      );
      return isDesktopOnboardingTree(candidate)
        && !!source
        && !!(source.state && source.state.pressed);
    },
    'onboarding custom model source',
  );

  let onboarding = findDesktopSmokeNode(
    tree.root,
    (node) => node.node_id === 'onboarding',
  );
  await runDesktopSmokeAction(
    uiInstanceId,
    tree,
    onboarding,
    'scroll_page',
    { delta: 320 },
  );
  tree = await waitForDesktopSmokeTree(
    uiInstanceId,
    (candidate) => isDesktopOnboardingTree(candidate)
      && !!findDesktopSmokeNode(candidate.root, (node) => node.node_id === 'onboarding_base_url'),
    'onboarding custom model endpoint after scroll',
  );

  let endpoint = findDesktopSmokeNode(
    tree.root,
    (node) => node.node_id === 'onboarding_base_url',
  );
  const originalEndpoint = String(endpoint.value_summary || '');
  const marker = 'https://cyrene-smoke.invalid/v1';
  await runDesktopSmokeAction(uiInstanceId, tree, endpoint, 'set_value', { value: marker });
  tree = await waitForDesktopSmokeTree(
    uiInstanceId,
    (candidate) => {
      const node = findDesktopSmokeNode(
        candidate.root,
        (item) => item.node_id === 'onboarding_base_url',
      );
      return !!node && node.value_summary === marker;
    },
    'onboarding endpoint value',
  );
  endpoint = findDesktopSmokeNode(
    tree.root,
    (node) => node.node_id === 'onboarding_base_url',
  );
  await runDesktopSmokeAction(
    uiInstanceId,
    tree,
    endpoint,
    'set_value',
    { value: originalEndpoint },
  );
  await waitForDesktopSmokeTree(
    uiInstanceId,
    (candidate) => {
      const node = findDesktopSmokeNode(
        candidate.root,
        (item) => item.node_id === 'onboarding_base_url',
      );
      return !!node && node.value_summary === originalEndpoint;
    },
    'restored onboarding endpoint',
  );

  tree = await waitForDesktopSmokeTree(
    uiInstanceId,
    (candidate) => {
      const onboarding = findDesktopSmokeNode(
        candidate.root,
        (node) => node.node_id === 'onboarding',
      );
      return !!onboarding && Array.isArray(onboarding.actions)
        && onboarding.actions.some((action) => action.action_id === 'scroll_page');
    },
    'scrollable onboarding surface',
  );
  onboarding = findDesktopSmokeNode(tree.root, (node) => node.node_id === 'onboarding');
  await runDesktopSmokeAction(
    uiInstanceId,
    tree,
    onboarding,
    'scroll_page',
    { delta: 1200 },
  );
  await waitForDesktopSmokeTree(
    uiInstanceId,
    (candidate) => !!findDesktopSmokeNode(
      candidate.root,
      (node) => node.node_id === 'onboarding_model',
    ),
    'onboarding model field after scroll',
  );

  const settingsCheck = await runDesktopSettingsSmokeTest();
  const shortcutCheck = await runShortcutSettingsSmokeTest(window);
  return {
    uiInstanceId,
    checks: [
      'tree', 'onboarding_sources', 'onboarding_model_fields',
      'onboarding_endpoint', settingsCheck, shortcutCheck,
    ],
  };
}

async function runDesktopSemanticSmokeTest(window) {
  window.show();
  window.focus();
  const uiInstanceId = await window.webContents.executeJavaScript(`(() => {
    const service = window.CyreneUI && window.CyreneUI.has('uiSurface')
      ? window.CyreneUI.require('uiSurface')
      : null;
    return service ? service.getInstanceId() : '';
  })()`, true);
  if (!uiInstanceId) throw new Error('Semantic UI surface did not register');

  let tree = await waitForDesktopSmokeTree(
    uiInstanceId,
    (candidate) => candidate.surface && candidate.surface.kind === 'main'
      && !!findDesktopSmokeNode(candidate.root, (node) => node.node_id === 'navigation_chat')
      && !!findDesktopSmokeNode(candidate.root, (node) => node.node_id === 'workspace_sidebar')
      && (
        !!findDesktopSmokeNode(candidate.root, (node) => node.node_id === 'project_switcher')
        || isDesktopOnboardingTree(candidate)
      ),
    'main project/navigation or onboarding tree',
  );
  if (isDesktopOnboardingTree(tree)) {
    return runDesktopOnboardingSmokeTest(window, uiInstanceId, tree);
  }
  let sidebar = findDesktopSmokeNode(tree.root, (node) => node.node_id === 'workspace_sidebar');
  if (sidebar && sidebar.state && sidebar.state.collapsed === true) {
    await runDesktopSmokeAction(uiInstanceId, tree, sidebar, 'toggle');
    tree = await waitForDesktopSmokeTree(
      uiInstanceId,
      (candidate) => {
        const node = findDesktopSmokeNode(candidate.root, (item) => item.node_id === 'workspace_sidebar');
        return !!node && (!node.state || node.state.collapsed !== true);
      },
      'expanded workspace sidebar',
    );
  }
  const projectSwitcher = findDesktopSmokeNode(tree.root, (node) => node.node_id === 'project_switcher');
  await runDesktopSmokeAction(uiInstanceId, tree, projectSwitcher, 'open_menu');

  tree = await waitForDesktopSmokeTree(
    uiInstanceId,
    (candidate) => candidate.surface && candidate.surface.scope === 'project_menu'
      && !!findDesktopSmokeNode(candidate.root, (node) => (
        node.role === 'menuitemradio'
        && Array.isArray(node.actions)
        && node.actions.some((action) => action.action_id === 'select')
      )),
    'project menu',
  );
  const projectItem = findDesktopSmokeNode(tree.root, (node) => (
    node.role === 'menuitemradio'
    && Array.isArray(node.actions)
    && node.actions.some((action) => action.action_id === 'select')
  ));
  await runDesktopSmokeAction(uiInstanceId, tree, projectItem, 'select');

  tree = await waitForDesktopSmokeTree(
    uiInstanceId,
    (candidate) => candidate.surface && candidate.surface.scope === 'main'
      && !!findDesktopSmokeNode(candidate.root, (node) => node.node_id === 'navigation_chat'),
    'main tree after project selection',
  );
  const chatNavigation = findDesktopSmokeNode(tree.root, (node) => node.node_id === 'navigation_chat');
  await runDesktopSmokeAction(uiInstanceId, tree, chatNavigation, 'open');

  tree = await waitForDesktopSmokeTree(
    uiInstanceId,
    (candidate) => !!findDesktopSmokeNode(candidate.root, (node) => node.node_id === 'chat_search_input'),
    'chat project search',
  );
  let search = findDesktopSmokeNode(tree.root, (node) => node.node_id === 'chat_search_input');
  const marker = '__cyrene_semantic_smoke__';
  await runDesktopSmokeAction(uiInstanceId, tree, search, 'set_value', { value: marker });

  tree = await waitForDesktopSmokeTree(
    uiInstanceId,
    (candidate) => {
      const node = findDesktopSmokeNode(candidate.root, (item) => item.node_id === 'chat_search_input');
      return !!node && node.value_summary === marker
        && Number(node.state && node.state.query_length) === marker.length;
    },
    'chat search value',
  );
  search = findDesktopSmokeNode(tree.root, (node) => node.node_id === 'chat_search_input');
  await runDesktopSmokeAction(uiInstanceId, tree, search, 'clear_value');
  await waitForDesktopSmokeTree(
    uiInstanceId,
    (candidate) => {
      const node = findDesktopSmokeNode(candidate.root, (item) => item.node_id === 'chat_search_input');
      return !!node && node.value_summary === ''
        && Number(node.state && node.state.query_length) === 0;
    },
    'cleared chat search',
  );
  tree = await waitForDesktopSmokeTree(
    uiInstanceId,
    (candidate) => !!findDesktopSmokeNode(candidate.root, (node) => node.node_id === 'open_settings'),
    'settings launcher',
  );
  const settingsLauncher = findDesktopSmokeNode(tree.root, (node) => node.node_id === 'open_settings');
  await runDesktopSmokeAction(uiInstanceId, tree, settingsLauncher, 'open');
  tree = await waitForDesktopSmokeTree(
    uiInstanceId,
    (candidate) => candidate.surface && candidate.surface.scope === 'settings'
      && !!findDesktopSmokeNode(candidate.root, (node) => node.node_id === 'settings_tab_shortcuts')
      && !!findDesktopSmokeNode(candidate.root, (node) => (
        String(node.node_id || '').startsWith('dom_')
        && Array.isArray(node.actions)
        && node.actions.some((action) => action.action_id === 'invoke')
      )),
    'projected settings controls',
  );
  const shortcutTab = findDesktopSmokeNode(tree.root, (node) => node.node_id === 'settings_tab_shortcuts');
  await runDesktopSmokeAction(uiInstanceId, tree, shortcutTab, 'open');
  tree = await waitForDesktopSmokeTree(
    uiInstanceId,
    (candidate) => {
      const selected = findDesktopSmokeNode(candidate.root, (node) => node.node_id === 'settings_tab_shortcuts');
      return !!selected && !!(selected.state && selected.state.selected)
        && !!findDesktopSmokeNode(candidate.root, (node) => (
          Array.isArray(node.actions)
          && node.actions.some((action) => action.action_id === 'scroll_page')
        ));
    },
    'shortcut settings and scrollable list',
  );
  const scrollable = findDesktopSmokeNode(tree.root, (node) => (
    Array.isArray(node.actions)
    && node.actions.some((action) => action.action_id === 'scroll_page')
  ));
  await runDesktopSmokeAction(uiInstanceId, tree, scrollable, 'scroll_page', { delta: 240 });
  const settingsCheck = await runDesktopSettingsSmokeTest();
  const shortcutCheck = await runShortcutSettingsSmokeTest(window);
  return {
    uiInstanceId,
    checks: [
      'tree', 'project_switch', 'chat_navigation', 'chat_search',
      'settings_controls', 'list_scroll', settingsCheck, shortcutCheck,
    ],
  };
}

async function runDesktopSmokeTest(window) {
  const renderTimeoutMs = 90000;
  const state = await window.webContents.executeJavaScript(`new Promise((resolve) => {
    const startedAt = Date.now();
    const inspect = () => {
      const root = document.querySelector('#root');
      const launchScreen = document.querySelector('#cyrene-launch-screen');
      const result = {
        readyState: document.readyState,
        hasRoot: Boolean(root),
        rootChildren: root ? root.childElementCount : 0,
        launchScreenPresent: Boolean(launchScreen),
        bodyText: String(document.body && document.body.innerText || '').trim().slice(0, 200)
      };
      if (
        result.readyState === 'complete'
        && result.rootChildren > 0
        && !result.launchScreenPresent
      ) {
        resolve(result);
        return;
      }
      if (Date.now() - startedAt >= ${renderTimeoutMs}) {
        resolve(result);
        return;
      }
      window.setTimeout(inspect, 100);
    };
    inspect();
  })`, true);
  const image = await window.webContents.capturePage();
  const bitmap = image.toBitmap();
  let nonWhitePixels = 0;
  for (let offset = 0; offset + 3 < bitmap.length; offset += 4) {
    // BGRA on all Electron desktop platforms. Count pixels visibly darker than
    // a blank white window; the Cyrene mark and text easily exceed this floor.
    if (bitmap[offset] < 238 || bitmap[offset + 1] < 238 || bitmap[offset + 2] < 238) {
      nonWhitePixels += 1;
    }
  }
  if (
    !state
    || state.readyState !== 'complete'
    || !state.hasRoot
    || state.rootChildren < 1
    || state.launchScreenPresent
    || image.isEmpty()
    || nonWhitePixels < 100
  ) {
    throw new Error(`Desktop smoke test rendered an invalid surface: ${JSON.stringify({
      ...state,
      imageEmpty: image.isEmpty(),
      nonWhitePixels,
    })}`);
  }
  const semantic = await runDesktopSemanticSmokeTest(window);
  const successMessage = `DESKTOP_SMOKE_TEST=ok nonWhitePixels=${nonWhitePixels} semantic=${semantic.checks.join(',')}`;
  const resultPath = String(process.env.CYRENE_DESKTOP_SMOKE_RESULT || '').trim();
  if (resultPath) {
    fs.mkdirSync(path.dirname(resultPath), { recursive: true });
    fs.writeFileSync(resultPath, `${successMessage}\n`, 'utf8');
  }
  console.log(successMessage);
  isQuitting = true;
  app.quit();
}

async function createMainWindow() {
  if (mainWindow && !mainWindow.isDestroyed()) {
    mainWindow.show();
    if (mainWindow.isMinimized()) mainWindow.restore();
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
      + `Check cyrene_error.log in ${getCyreneTempDir()} for details.`
    );
    killPython();
    app.quit();
    return;
  }

  if (!port) {
    // Error already handled in spawnPython (port resolve returned null)
    return;
  }

  // Workbench draws its own top bar and reserves room for the traffic lights.
  // The inset title bar and traffic-light positioning remain macOS-specific.
  // Windows and Linux keep their native frame so close/minimize/maximize
  // controls remain available.
  const useInsetTitleBar = isMac;
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
  if (isLinux) {
    const iconPath = getNotificationIconPath();
    if (iconPath) windowOptions.icon = iconPath;
  }
  if (useInsetTitleBar) {
    windowOptions.titleBarStyle = 'hidden';
    // Electron's macOS traffic-light image renders slightly below its nominal
    // 14px bounds. Place it 1px above geometric center in the 58px workbench
    // topbar so its visible center aligns with the brand mark and wordmark.
    windowOptions.trafficLightPosition = { x: 12, y: 21 };
  }
  mainWindow = new BrowserWindow(windowOptions);
  installWindowDiagnostics(mainWindow, 'main');

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
    hideAllBrowserSessions();
    mainWindow = null;
  });

  // Navigate to the sole Workbench surface.
  const url = `http://127.0.0.1:${port}`;
  // Force clear cache so the app always loads fresh assets
  installLocalNavigationGuards(mainWindow, port, { allowLocalPopups: true });
  await mainWindow.webContents.session.clearCache();
  try {
    await mainWindow.loadURL(url);
    if (isDesktopSmokeTest) {
      await runDesktopSmokeTest(mainWindow);
    }
  } catch (err) {
    // Some headless Linux desktop-portal combinations report ERR_FAILED after
    // Chromium has already received and rendered the full local page. In smoke
    // mode, trust the stronger DOM, screenshot, semantic-tree, and interaction
    // checks before treating loadURL's transport status as a package failure.
    if (isDesktopSmokeTest) {
      try {
        await runDesktopSmokeTest(mainWindow);
        return;
      } catch (smokeErr) {
        const loadDetail = err && err.stack ? err.stack : String(err);
        const smokeDetail = smokeErr && smokeErr.stack ? smokeErr.stack : String(smokeErr);
        const detail = `${loadDetail}\nPost-load smoke validation failed: ${smokeDetail}`;
        appendErrorLog(`[electron:main] load failed: ${detail}\n`);
        console.error(`DESKTOP_SMOKE_TEST=failed ${detail}`);
        isQuitting = true;
        app.exit(1);
        return;
      }
    }
    const detail = err && err.stack ? err.stack : String(err);
    appendErrorLog(`[electron:main] load failed: ${detail}\n`);
    dialog.showErrorBox(
      'Cyrene - Window Error',
      'The application window could not be rendered.\n\n'
      + `Check cyrene_error.log in ${getCyreneTempDir()} for details.`
    );
  }
}

async function revealMainWindow() {
  launchHidden = false;
  if (mainWindow && !mainWindow.isDestroyed()) {
    if (!mainWindow.isVisible()) mainWindow.show();
    if (mainWindow.isMinimized()) mainWindow.restore();
    mainWindow.focus();
    return;
  }
  if (!electronRpcPort) {
    try { await startElectronRpcServer(); } catch (err) {
      console.error('[electron] Failed to start RPC server on reveal:', err);
    }
  }
  spawnPython();
  await createMainWindow();
}

function buildTrayMenu() {
  const settings = readDesktopSettings();
  return Menu.buildFromTemplate([
    {
      label: desktopT('open', settings),
      click: () => {
        revealMainWindow().catch((err) => {
          console.error('[electron] Failed to open Cyrene from tray:', err);
          appendErrorLog(`[electron] Failed to open Cyrene from tray: ${err && err.stack ? err.stack : err}\n`);
        });
      },
    },
    { type: 'separator' },
    {
      label: desktopT('quit', settings),
      click: () => {
        isQuitting = true;
        app.quit();
      },
    },
  ]);
}

function ensureTray() {
  if (tray) return;
  const image = getTrayIconImage();
  if (!image) {
    console.warn('[electron] Tray icon unavailable; background mode will still keep running.');
    return;
  }
  tray = new Tray(image);
  tray.setToolTip(APP_NAME);
  tray.setContextMenu(buildTrayMenu());
  tray.on('click', () => {
    revealMainWindow().catch((err) => {
      console.error('[electron] Failed to open Cyrene from tray:', err);
      appendErrorLog(`[electron] Failed to open Cyrene from tray: ${err && err.stack ? err.stack : err}\n`);
    });
  });
  tray.on('double-click', () => {
    revealMainWindow().catch(() => {});
  });
  tray.on('right-click', () => {
    if (tray) tray.popUpContextMenu(buildTrayMenu());
  });
}

function destroyTray() {
  if (!tray) return;
  try {
    tray.destroy();
  } catch (_) {}
  tray = null;
}

function syncTrayWithSettings(settings) {
  const shouldShowTray = !!(
    settings
    && (settings.runInBackground === true || settings.quickChatEnabled === true)
  );
  if (shouldShowTray) {
    ensureTray();
    if (tray) tray.setContextMenu(buildTrayMenu());
  }
  else destroyTray();
}

// ---------------------------------------------------------------------------
// App lifecycle
// ---------------------------------------------------------------------------

const gotSingleInstanceLock = app.requestSingleInstanceLock();
if (!gotSingleInstanceLock) {
  app.quit();
} else {
  app.on('second-instance', () => {
    revealMainWindow().catch((err) => {
      console.error('[electron] Failed to handle second instance:', err);
      appendErrorLog(`[electron] Failed to handle second instance: ${err && err.stack ? err.stack : err}\n`);
    });
  });

  app.whenReady().then(async () => {
    cleanupTemporaryArtifacts();
    installAuthHeaderInjector();
    try {
      await startElectronRpcServer();
    } catch (err) {
      console.error('[electron] Failed to start Electron RPC server:', err);
      appendErrorLog(`[electron] Failed to start Electron RPC server: ${err && err.stack ? err.stack : err}\n`);
    }
    getAppUseManager();
    const desktopSettings = readDesktopSettings();
    applyLaunchAtLogin(desktopSettings.launchAtLogin);
    // Only claim the global shortcut when the user has enabled quick chat.
    if (desktopSettings.quickChatEnabled) {
      registerQuickChatShortcut(desktopSettings.quickChatShortcut);
    }
    syncTrayWithSettings(desktopSettings);
    rebuildApplicationMenu(desktopSettings);
    // Windows/Linux：移除默认菜单（File/Edit/View），macOS 用上面的自定义 i18n 菜单
    if (!isMac) {
      Menu.setApplicationMenu(null);
    }
    ipcMain.handle('desktop-settings:get', () => getDesktopSettings());
    ipcMain.handle('desktop-settings:update', (_event, updates) => saveDesktopSettings(updates || {}));
    ipcMain.handle('agent-cursor:set-running', (event, info) => (
      updateAgentCursorRunningSource(event.sender, info && info.running === true)
    ));
    ipcMain.handle('ui-surface:register', (event, payload) => (
      getHostControl().registerSurface(payload && payload.uiInstanceId, event.sender)
    ));
    ipcMain.handle('ui-surface:unregister', (event, payload) => (
      getHostControl().unregisterSurface(payload && payload.uiInstanceId, event.sender)
    ));
    ipcMain.on('ui-surface:response', (event, payload) => {
      getHostControl().receiveSurfaceResponse(payload || {}, event.sender);
    });
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
    // The quick-chat renderer grows the window once on the first send so the
    // conversation has room (the user can resize freely afterwards). Anchored at
    // the top: the y stays put and the window grows downward, clamped to the work
    // area so it never spills past the bottom of the screen.
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
    ipcMain.handle('shell:show-item-in-folder', (_event, info) => {
      const target = String(info && info.path || '').trim();
      if (!target) return { ok: false, error: 'File path is required.' };
      const resolved = path.resolve(target);
      if (!fs.existsSync(resolved)) {
        return { ok: false, error: 'File no longer exists.' };
      }
      shell.showItemInFolder(resolved);
      return { ok: true };
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
    ipcMain.handle('dialog:pick-extension-path', async (event, info) => {
      const owner = BrowserWindow.fromWebContents(event.sender);
      const directory = !!(info && info.directory);
      const result = await dialog.showOpenDialog(owner || mainWindow, {
        title: String(info && info.title || (directory ? 'Select extension folder' : 'Select executable')),
        properties: [directory ? 'openDirectory' : 'openFile'],
      });
      if (result.canceled || !result.filePaths.length) return { path: '', cancelled: true };
      return { path: result.filePaths[0] };
    });
    ipcMain.handle('dialog:pick-backup-save-path', async (event, info) => {
      const owner = BrowserWindow.fromWebContents(event.sender);
      const requestedName = path.basename(String(info && info.defaultName || '').trim()) || 'cyrene_backup.zip';
      const result = await dialog.showSaveDialog(owner || mainWindow, {
        title: String(info && info.title || 'Save Cyrene backup'),
        defaultPath: path.join(app.getPath('documents'), requestedName),
        filters: [{ name: 'Cyrene backup', extensions: ['zip'] }],
        properties: ['createDirectory', 'showOverwriteConfirmation'],
      });
      if (result.canceled || !result.filePath) return { path: '', cancelled: true };
      const selectedPath = result.filePath.toLowerCase().endsWith('.zip')
        ? result.filePath
        : result.filePath + '.zip';
      return { path: selectedPath };
    });
    ipcMain.handle('dialog:pick-backup-file', async (event, info) => {
      const owner = BrowserWindow.fromWebContents(event.sender);
      const result = await dialog.showOpenDialog(owner || mainWindow, {
        title: String(info && info.title || 'Choose a Cyrene backup'),
        filters: [{ name: 'Cyrene backup', extensions: ['zip'] }],
        properties: ['openFile'],
      });
      if (result.canceled || !result.filePaths.length) return { path: '', cancelled: true };
      return { path: result.filePaths[0] };
    });
    ipcMain.handle('browser:get-state', (_event, info) => handleBrowserRpc('state', {}, info || {}));
    ipcMain.handle('browser:set-bounds', (_event, info) => handleBrowserRpc('setBounds', info || {}, info || {}));
    ipcMain.handle('browser:set-chat-overlay', (_event, info) => handleBrowserRpc('setChatOverlay', info || {}, info || {}));
    ipcMain.handle('browser:set-tab-picker', (_event, info) => handleBrowserRpc('setTabPicker', info || {}, info || {}));
    ipcMain.handle('browser:set-context', (_event, info) => handleBrowserRpc('setContext', info || {}));
    ipcMain.handle('browser:set-obscured', (_event, info) => handleBrowserRpc('setObscured', info || {}, info || {}));
    ipcMain.handle('browser:create-tab', async (_event, info) => {
      const result = await handleBrowserRpc('createTab', info || {}, info || {});
      getBrowserTabManager(browserRpcSessionId(info || {}, info || {})).recordUserEvent('navigate', { payload: { action: 'create_tab', url: info && info.url || '' } });
      return result;
    });
    ipcMain.handle('browser:activate-tab', async (_event, info) => {
      const result = await handleBrowserRpc('activateTab', info || {}, info || {});
      getBrowserTabManager(browserRpcSessionId(info || {}, info || {})).recordUserEvent('select_tab', { payload: { tabId: String(info && info.tabId || '') } });
      return result;
    });
    ipcMain.handle('browser:close-tab', async (_event, info) => {
      getBrowserTabManager(browserRpcSessionId(info || {}, info || {})).recordUserEvent('close_tab', { payload: { tabId: String(info && info.tabId || '') } });
      return handleBrowserRpc('closeTab', info || {}, info || {});
    });
    ipcMain.handle('browser:navigate', async (_event, info) => {
      const result = await handleBrowserRpc('navigate', info || {}, info || {});
      const manager = getBrowserTabManager(browserRpcSessionId(info || {}, info || {}));
      manager.recordUserEvent('navigate', { payload: { url: info && info.url || '' } });
      return result && result.ok === false ? result : manager.state();
    });
    ipcMain.handle('browser:go-back', async (_event, info) => {
      const result = await handleBrowserRpc('goBack', {}, info || {});
      getBrowserTabManager(browserRpcSessionId({}, info || {})).recordUserEvent('navigate', { payload: { action: 'go_back' } });
      return result;
    });
    ipcMain.handle('browser:go-forward', async (_event, info) => {
      const result = await handleBrowserRpc('goForward', {}, info || {});
      getBrowserTabManager(browserRpcSessionId({}, info || {})).recordUserEvent('navigate', { payload: { action: 'go_forward' } });
      return result;
    });
    ipcMain.handle('browser:reload', async (_event, info) => {
      const result = await handleBrowserRpc('reload', info || {}, info || {});
      getBrowserTabManager(browserRpcSessionId(info || {}, info || {})).recordUserEvent('navigate', { payload: { action: 'reload', tabId: String(info && info.tabId || '') } });
      return result;
    });
    ipcMain.handle('browser:set-muted', (_event, info) => handleBrowserRpc('setMuted', info || {}, info || {}));
    ipcMain.handle('browser:screenshot', (_event, info) => handleBrowserRpc('screenshot', info || {}, info || {}));
    ipcMain.on('browser-chat-overlay:action', (event, action) => {
      const sessionId = normalizeBrowserSessionId(action && action.sessionId);
      const manager = browserTabManagers.get(sessionId);
      if (!manager || !manager.chatOverlayView || manager.chatOverlayView.webContents !== event.sender) return;
      if (mainWindow && !mainWindow.isDestroyed()) {
        mainWindow.webContents.send('browser:chat-overlay-action', {
          sessionId,
          type: action && action.type === 'stop' ? 'stop' : 'submit',
          text: String(action && action.text || '').slice(0, 20000),
        });
      }
    });
    ipcMain.on('browser-tab-picker:ready', (event, info) => {
      const sessionId = normalizeBrowserSessionId(info && info.sessionId);
      const manager = browserTabManagers.get(sessionId) || Array.from(browserTabManagers.values()).find((candidate) => (
        candidate.tabPickerView && candidate.tabPickerView.webContents === event.sender
      ));
      if (!manager || !manager.tabPickerView || manager.tabPickerView.webContents !== event.sender) return;
      manager.tabPickerReady = true;
      manager.pushTabPickerState();
    });
    ipcMain.on('browser-tab-picker:action', (event, action) => {
      const sessionId = normalizeBrowserSessionId(action && action.sessionId);
      const manager = browserTabManagers.get(sessionId);
      if (!manager || !manager.tabPickerView || manager.tabPickerView.webContents !== event.sender) return;
      manager.handleTabPickerAction(action || {});
    });
    ipcMain.on('browser-tab-picker:hidden-ready', (event, info) => {
      const sessionId = normalizeBrowserSessionId(info && info.sessionId);
      const manager = browserTabManagers.get(sessionId);
      if (!manager || !manager.tabPickerView || manager.tabPickerView.webContents !== event.sender) return;
      manager.finishTabPickerHide();
    });
    spawnPython();
    if (!launchHidden) {
      createMainWindow().catch((err) => {
        const detail = err && err.stack ? err.stack : String(err);
        appendErrorLog(`[electron:main] create window failed: ${detail}\n`);
        console.error('[electron] Failed to create main window:', err);
        if (isDesktopSmokeTest) {
          console.error(`DESKTOP_SMOKE_TEST=failed ${detail}`);
          isQuitting = true;
          app.exit(1);
        }
      });
    }
  });

  app.on('window-all-closed', () => {
    // restartPythonBackend deliberately tears down every renderer so stale
    // ui_instance_id/tree revisions cannot survive the backend boundary. Do
    // not interpret that temporary zero-window state as an application quit.
    if (isBackendRestarting) return;
    // Keep the backend alive while the app stays resident for the global
    // shortcut / background mode; otherwise tear it down and quit on non-mac.
    if (appStaysResident()) return;
    killPython();
    if (process.platform !== 'darwin') {
      app.quit();
    }
  });

  app.on('before-quit', (event) => {
    if (!quitExtensionDecisionMade && backendPort && !quitExtensionCheckInFlight) {
      event.preventDefault();
      quitExtensionCheckInFlight = true;
      requestBackendJson('GET', '/api/extensions/tasks')
        .then(async (payload) => {
          const active = (payload.tasks || []).filter((task) => ['queued', 'running', 'cancelling'].includes(task.status));
          if (!active.length) {
            quitExtensionDecisionMade = true;
            app.quit();
            return;
          }
          const settings = readDesktopSettings();
          const choice = await dialog.showMessageBox(mainWindow && !mainWindow.isDestroyed() ? mainWindow : undefined, {
            type: 'warning',
            title: desktopT('installQuitTitle', settings),
            message: desktopT('installQuitMessage', settings),
            detail: desktopT('installQuitDetail', settings),
            buttons: [desktopT('installQuitWait', settings), desktopT('installQuitCancel', settings)],
            defaultId: 0,
            cancelId: 0,
            noLink: true,
          });
          if (choice.response === 0) {
            isQuitting = false;
            return;
          }
          await cancelExtensionTasksAndWait(active);
          quitExtensionDecisionMade = true;
          app.quit();
        })
        .catch(() => {
          // If the backend cannot answer, the durable task store reconciles
          // interrupted jobs on the next launch. Do not trap the user in-app.
          quitExtensionDecisionMade = true;
          app.quit();
        })
        .finally(() => { quitExtensionCheckInFlight = false; });
      return;
    }
    if (quitExtensionCheckInFlight && !quitExtensionDecisionMade) {
      event.preventDefault();
      return;
    }
    isQuitting = true;
    destroyTray();
    globalShortcut.unregisterAll();
    if (appUseManager) appUseManager.stop();
    if (appUsePointerWindow && !appUsePointerWindow.isDestroyed()) appUsePointerWindow.destroy();
    appUsePointerWindow = null;
    appUsePointerOwnerTargetId = '';
    closeAllBrowserSessions();
    killPython();
  });

  app.on('activate', () => {
    // macOS: re-create window when dock icon is clicked and no windows exist
    revealMainWindow().catch((err) => {
      console.error('[electron] Failed to activate Cyrene:', err);
      appendErrorLog(`[electron] Failed to activate Cyrene: ${err && err.stack ? err.stack : err}\n`);
    });
  });
}
