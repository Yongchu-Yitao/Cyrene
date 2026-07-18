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

const APP_NAME = 'Cyrene';
const TEMP_ARTIFACT_TTL_MS = 24 * 60 * 60 * 1000;
const BROWSER_UPLOAD_TARGET_TTL_MS = 15 * 60 * 1000;
const BROWSER_UPLOAD_MAX_FILES = 10;
const BROWSER_UPLOAD_MAX_FILE_BYTES = 100 * 1024 * 1024;
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
let tray = null;
const browserTabManagers = new Map();
let activeBrowserSessionId = '';
let browserSurfaceObscured = false;
let activeVideoFullscreenManager = null;
let appUseManager = null;
let appUsePointerWindow = null;
let appUsePointerHideTimer = null;
let electronRpcServer = null;
let electronRpcPort = null;
const BROWSER_USER_EVENT_CONSOLE_PREFIX = '__CYRENE_BROWSER_USER_EVENT__';

const DEFAULT_DESKTOP_SETTINGS = Object.freeze({
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

const DESKTOP_TRANSLATIONS = Object.freeze({
  en: {
    open: 'Open Cyrene',
    quit: 'Quit Cyrene',
  },
  zh: {
    open: '打开 Cyrene',
    quit: '退出 Cyrene',
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

const BROWSER_VISIBLE_ELEMENTS_SCRIPT = `
(function(maxArg, textArg) {
  const maxElements = Math.max(1, Math.min(200, Number(maxArg) || 80));
  const textLimit = Math.max(20, Math.min(500, Number(textArg) || 160));
  const viewportW = window.innerWidth || document.documentElement.clientWidth || 0;
  const viewportH = window.innerHeight || document.documentElement.clientHeight || 0;
  const candidates = Array.from(document.querySelectorAll('a,button,input,textarea,select,[role],[tabindex],summary,label,img,[contenteditable="true"],video,section,article,div,span'));
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
  for (const el of candidates) {
    if (!(el instanceof Element) || seen.has(el)) continue;
    seen.add(el);
    const style = window.getComputedStyle(el);
    if (!style || style.display === 'none' || style.visibility === 'hidden' || Number(style.opacity) === 0) continue;
    const rect = el.getBoundingClientRect();
    if (!rect || rect.width <= 0 || rect.height <= 0) continue;
    if (rect.bottom < 0 || rect.right < 0 || rect.top > viewportH || rect.left > viewportW) continue;
    const tag = String(el.tagName || '').toLowerCase();
    const role = roleOf(el, tag);
    const text = clean(el.innerText || el.textContent || el.getAttribute('value') || el.getAttribute('title') || el.getAttribute('alt'));
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
      inputType: tag === 'input' ? clean(el.getAttribute('type') || 'text', 40).toLowerCase() : '',
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
    text: clean(document.body ? document.body.innerText : '', 2000),
    viewport: { width: viewportW, height: viewportH, scrollX: window.scrollX || 0, scrollY: window.scrollY || 0 },
    elements: out,
  };
})
`;

const BROWSER_FIND_TARGET_SCRIPT = `
(function(modeArg, valueArg, exactArg, visibleOnlyArg) {
  const mode = String(modeArg || 'selector');
  const value = String(valueArg || '');
  const exact = exactArg === true;
  const visibleOnly = visibleOnlyArg !== false;
  const norm = (v) => String(v || '').replace(/\\s+/g, ' ').trim();
  const isVisible = (el) => {
    if (!(el instanceof Element)) return false;
    const style = window.getComputedStyle(el);
    if (!style || style.display === 'none' || style.visibility === 'hidden' || Number(style.opacity) === 0) return false;
    const r = el.getBoundingClientRect();
    return !!r && r.width > 0 && r.height > 0;
  };
  let el = null;
  if (mode === 'ref') {
    const n = value.replace(/^e/i, '');
    el = document.querySelector('[data-cyrene-ref="' + n.replace(/"/g, '\\\\"') + '"]');
  } else if (mode === 'text') {
    const needle = norm(value).toLowerCase();
    const nodes = Array.from(document.querySelectorAll('a,button,input,textarea,select,[role],[tabindex],label,summary,[contenteditable="true"],div,span,section,article'));
    el = nodes.find((node) => {
      if (visibleOnly && !isVisible(node)) return false;
      const hay = norm(node.innerText || node.textContent || node.getAttribute('aria-label') || node.getAttribute('title') || node.getAttribute('placeholder') || node.getAttribute('value')).toLowerCase();
      return exact ? hay === needle : hay.includes(needle);
    }) || null;
  } else {
    el = document.querySelector(value);
  }
  if (!el) return { ok: false, error: 'nf' };
  if (visibleOnly && !isVisible(el)) return { ok: false, error: 'not visible' };
  el.scrollIntoView({ block: 'center', inline: 'center' });
  const r = el.getBoundingClientRect();
  if (!r || r.width <= 0 || r.height <= 0) return { ok: false, error: 'not visible' };
  return {
    ok: true,
    x: Math.round(r.left + r.width / 2),
    y: Math.round(r.top + r.height / 2),
    box: { x: Math.round(r.left), y: Math.round(r.top), w: Math.round(r.width), h: Math.round(r.height) },
    tag: String(el.tagName || '').toLowerCase(),
    inputType: String(el.getAttribute && el.getAttribute('type') || '').toLowerCase(),
    accept: String(el.getAttribute && el.getAttribute('accept') || ''),
    multiple: !!(el.hasAttribute && el.hasAttribute('multiple')),
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
    this.visible = false;
    this.obscured = browserSurfaceObscured;
    this.attachedTabId = '';
    this.attachedWindow = null;
    this._syncTimer = null;
    this._repaintTimer = null;
    this._boundsTransitionToken = 0;
    this._boundsTransitioning = false;
    this.videoFullscreen = { active: false, external: false, tabId: '' };
    this.videoFullscreenWindow = null;
    this._videoFullscreenWindowClosing = false;
    this._mainWindowWasFullScreen = false;
    this._fullscreenResizeHandler = null;
    this._mainFullscreenLeaveHandler = null;
    this.browserContext = { sessionId: this.sessionId, roundId: '' };
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
        backgroundThrottling: false,
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
      this.createTab({ url, activate: true }).catch((err) => {
        console.error('[electron] Failed to open browser popup tab:', err);
      });
      return { action: 'deny' };
    });
    const update = () => this.emitState();
    wc.on('did-start-loading', update);
    wc.on('did-stop-loading', update);
    wc.on('did-navigate', update);
    wc.on('did-navigate-in-page', update);
    wc.on('page-title-updated', update);
    wc.on('media-started-playing', update);
    wc.on('media-paused', update);
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
    wc.on('did-finish-load', () => this.installUserEventCapture(view).catch(() => {}));
    wc.on('console-message', (_event, _level, message) => {
      this.handleCapturedUserEvent(view, message);
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

  handleCapturedUserEvent(view, message) {
    const raw = String(message || '');
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
      active: tab.id === this.activeTabId,
      loading: wc.isLoading(),
      canGoBack: wc.canGoBack(),
      canGoForward: wc.canGoForward(),
      muted: typeof wc.isAudioMuted === 'function' ? wc.isAudioMuted() : !!wc.audioMuted,
      audible: typeof wc.isCurrentlyAudible === 'function' ? wc.isCurrentlyAudible() : false,
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

  async createTab({ url = 'about:blank', activate = true } = {}) {
    if (!WebContentsView) throw new Error('Electron WebContentsView is unavailable.');
    const id = `tab_${this.nextTabId++}`;
    const view = this.createView();
    const tab = {
      id,
      view,
      url: normalizeBrowserUrl(url),
      title: '',
      debuggerReady: false,
      fileChoosers: new Map(),
      uploadTargets: new Map(),
      lastAgentFileChooser: null,
      agentFileChooserResolver: null,
    };
    this.tabs.set(id, tab);
    if (activate || !this.activeTabId) this.activeTabId = id;

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
    this.activeTabId = id;
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

  syncAttachedView() {
    const fullscreenTab = this.fullscreenTab();
    const active = fullscreenTab || this.tabs.get(this.activeTabId);
    const fullscreenActive = !!fullscreenTab;
    const win = this.surfaceWindow();
    if (!win) return;
    const ownsVisibleSurface = fullscreenActive || this.sessionId === activeBrowserSessionId;
    for (const tab of this.tabs.values()) {
      if (!active || tab.id !== active.id || !ownsVisibleSurface) this.detachView(tab);
    }
    if (!active || !ownsVisibleSurface) return;
    const shouldShow = fullscreenActive || (this.visible && !this.obscured && !this._boundsTransitioning);
    if (!shouldShow) {
      // Keep the active WebContentsView attached but hidden across PiP/fullscreen
      // transitions. Removing and re-adding it on macOS can strand Chromium's
      // compositor surface as a white rectangle when the size shrinks again.
      if (this.attachedTabId === active.id) {
        try { active.view.setVisible(false); } catch (_) {}
      }
      return;
    }
    const wasAttached = this.attachedTabId === active.id;
    const wasAttachedToTargetWindow = wasAttached && this.attachedWindow === win;
    let wasVisible = false;
    if (wasAttachedToTargetWindow && typeof active.view.getVisible === 'function') {
      try { wasVisible = active.view.getVisible(); } catch (_) {}
    }
    const targetBounds = fullscreenActive ? this.fullscreenBounds(win) : this.bounds;
    const targetRadius = fullscreenActive ? 0 : this.borderRadius;
    if (!wasAttachedToTargetWindow) {
      this.detachView(active);
      try { active.view.setBorderRadius(targetRadius); } catch (_) {}
      try { active.view.setBounds(targetBounds); } catch (_) {}
      try { win.contentView.addChildView(active.view); } catch (_) {}
      this.attachedTabId = active.id;
      this.attachedWindow = win;
    } else {
      try { active.view.setBorderRadius(targetRadius); } catch (_) {}
      try { active.view.setBounds(targetBounds); } catch (_) {}
    }
    try { active.view.setVisible(true); } catch (_) {}
    if (!wasAttached || !wasVisible) this.repaintView(active);
  }

  async settleBoundsTransition() {
    const token = ++this._boundsTransitionToken;
    this._boundsTransitioning = true;
    if (this._syncTimer) { clearTimeout(this._syncTimer); this._syncTimer = null; }
    this.syncAttachedView();
    const active = this.tabs.get(this.activeTabId);
    if (!active || active.view.webContents.isDestroyed()) {
      this._boundsTransitioning = false;
      return this.state();
    }
    try { active.view.setBorderRadius(this.borderRadius); } catch (_) {}
    try { active.view.setBounds(this.bounds); } catch (_) {}
    try { active.view.webContents.invalidate(); } catch (_) {}
    // capturePage waits for Chromium to produce a frame at the final size. Keep
    // the renderer's bitmap proxy visible until this promise resolves, so the
    // native compositor never exposes its temporary white surface.
    await Promise.race([
      active.view.webContents.capturePage().catch(() => null),
      new Promise((resolve) => setTimeout(resolve, 180)),
    ]);
    if (token !== this._boundsTransitionToken) return this.state();
    this._boundsTransitioning = false;
    this.syncAttachedView();
    await new Promise((resolve) => setTimeout(resolve, 34));
    if (token === this._boundsTransitionToken) this.repaintView(active);
    return this.state();
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
    this.visible = info.visible === true && width > 8 && height > 8;
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
    this.syncAttachedView();
    this.emitState();
    return this.state();
  }

  async navigate({ url, tabId = '', maxChars = 8000 } = {}) {
    const targetUrl = normalizeBrowserUrl(url);
    let tab = tabId ? this.tabs.get(String(tabId)) : this.tabs.get(this.activeTabId);
    if (!tab) tab = await this.createTab({ url: 'about:blank', activate: true });
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
      return { ...(result || {}), ok: true, tabId: tab.id };
    } catch (err) {
      return { ok: false, error: 'Inspect failed: ' + String((err && err.message) || err), url: wc.getURL(), title: wc.getTitle(), tabId: tab.id };
    }
  }

  async _findTarget(wc, { mode = 'selector', value = '', exact = false, visibleOnly = true } = {}) {
    try {
      return await wc.executeJavaScript(
        `${BROWSER_FIND_TARGET_SCRIPT}(${JSON.stringify(mode)}, ${JSON.stringify(value)}, ${exact ? 'true' : 'false'}, ${visibleOnly === false ? 'false' : 'true'})`,
        true
      );
    } catch (err) {
      return { ok: false, error: 'js execution failed: ' + String((err && err.message) || err) };
    }
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

  async _dispatchClick(tab, info) {
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
      this._markAgentInput(tab);
      wc.sendInputEvent({ type: 'mouseMove', x: info.x, y: info.y });
      wc.sendInputEvent({ type: 'mouseDown', x: info.x, y: info.y, button: 'left', clickCount: 1 });
      wc.sendInputEvent({ type: 'mouseUp', x: info.x, y: info.y, button: 'left', clickCount: 1 });
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
      return this._finishClick(tab, info);
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
    const snapshot = await this.pageSnapshot(tab.id, 4000);
    return { ...snapshot, tabId: tab.id, box: info && info.box ? info.box : null };
  }

  async click({ selector, tabId = '' } = {}) {
    const tab = tabId ? this.tabs.get(String(tabId)) : this.tabs.get(this.activeTabId);
    if (!tab) return { ok: false, error: 'No page open. Call browser_navigate first.' };
    const wc = tab.view.webContents;
    // Find element via JS — coordinates are sent as real OS-level input events
    // to bypass isTrusted=false restrictions (SPAs like Vue/React reject JS clicks).
    const info = await this._findTarget(wc, { mode: 'selector', value: String(selector || '') });
    if (!info || !info.ok) return { ok: false, error: 'Element ' + ((info && info.error) || 'not found'), url: wc.getURL(), title: wc.getTitle(), tabId: tab.id };
    // sendInputEvent dispatches trusted OS-level events.  Chromium's input
    // pipeline generates the full click chain (pointerdown → mousedown →
    // pointerup → mouseup → click) with isTrusted=true.
    return this._dispatchClick(tab, info);
  }

  async clickRef({ ref, tabId = '' } = {}) {
    const tab = tabId ? this.tabs.get(String(tabId)) : this.tabs.get(this.activeTabId);
    if (!tab) return { ok: false, error: 'No page open. Call browser_navigate first.' };
    const wc = tab.view.webContents;
    const info = await this._findTarget(wc, { mode: 'ref', value: String(ref || '') });
    if (!info || !info.ok) return { ok: false, error: 'Element ' + ((info && info.error) || 'not found'), url: wc.getURL(), title: wc.getTitle(), tabId: tab.id };
    return this._dispatchClick(tab, info);
  }

  async clickText({ text, exact = false, tabId = '' } = {}) {
    const tab = tabId ? this.tabs.get(String(tabId)) : this.tabs.get(this.activeTabId);
    if (!tab) return { ok: false, error: 'No page open. Call browser_navigate first.' };
    const wc = tab.view.webContents;
    const info = await this._findTarget(wc, { mode: 'text', value: String(text || ''), exact: exact === true });
    if (!info || !info.ok) return { ok: false, error: 'Element ' + ((info && info.error) || 'not found'), url: wc.getURL(), title: wc.getTitle(), tabId: tab.id };
    return this._dispatchClick(tab, info);
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
    const tab = tabId ? this.tabs.get(String(tabId)) : this.tabs.get(this.activeTabId);
    if (!tab) return { ok: false, error: 'No page open. Call browser_navigate first.' };
    const wc = tab.view.webContents;
    const script = `
      (function(mode, value, textValue, submitValue) {
        const find = ${BROWSER_FIND_TARGET_SCRIPT};
        const info = find(mode, value, false, true);
        if (!info || !info.ok) return { ok: false, error: 'Element ' + ((info && info.error) || 'not found') };
        let el = null;
        if (mode === 'ref') {
          el = document.querySelector('[data-cyrene-ref="' + String(value || '').replace(/^e/i, '').replace(/"/g, '\\\\"') + '"]');
        } else {
          el = document.querySelector(String(value || ''));
        }
        if (!el) return { ok: false, error: 'Element not found' };
        el.focus();
        const tag = String(el.tagName || '').toLowerCase();
        if ('value' in el) {
          el.value = String(textValue || '');
          el.dispatchEvent(new InputEvent('input', { bubbles: true, inputType: 'insertText', data: String(textValue || '') }));
          el.dispatchEvent(new Event('change', { bubbles: true }));
        } else if (el.isContentEditable) {
          el.textContent = String(textValue || '');
          el.dispatchEvent(new InputEvent('input', { bubbles: true, inputType: 'insertText', data: String(textValue || '') }));
        } else {
          return { ok: false, error: 'Element is not text-editable' };
        }
        if (submitValue) {
          const form = el.form || el.closest('form');
          if (form && typeof form.requestSubmit === 'function') {
            try { form.requestSubmit(); }
            catch (_) { el.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', code: 'Enter', bubbles: true })); }
          } else {
            el.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', code: 'Enter', bubbles: true }));
            el.dispatchEvent(new KeyboardEvent('keypress', { key: 'Enter', code: 'Enter', bubbles: true }));
            el.dispatchEvent(new KeyboardEvent('keyup', { key: 'Enter', code: 'Enter', bubbles: true }));
          }
        }
        return { ok: true, tag, box: info.box };
      })(${JSON.stringify(mode)}, ${JSON.stringify(value)}, ${JSON.stringify(String(text || ''))}, ${submit ? 'true' : 'false'})
    `;
    this._markAgentInput(tab);
    const result = await wc.executeJavaScript(script, true);
    if (!result || !result.ok) return { ok: false, error: (result && result.error) || 'Unable to type into element.', url: wc.getURL(), title: wc.getTitle(), tabId: tab.id };
    if (submit) await this._waitNav(wc);
    return { ok: true, url: wc.getURL(), title: wc.getTitle(), tabId: tab.id, box: result.box };
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

  async screenshot({ tabId = '' } = {}) {
    const tab = tabId ? this.tabs.get(String(tabId)) : this.tabs.get(this.activeTabId);
    if (!tab) return { ok: false, error: 'No browser tab is open.' };
    const image = await tab.view.webContents.capturePage();
    return {
      ok: true,
      pngBase64: image.toPNG().toString('base64'),
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

  reload() {
    const tab = this.tabs.get(this.activeTabId);
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
    if (!Number.isFinite(px)) px = Math.floor(Math.max(1, bounds.width) / 2);
    if (!Number.isFinite(py)) py = Math.floor(Math.max(1, bounds.height) / 2);
    px = Math.max(0, Math.min(Math.max(0, bounds.width - 1), Math.round(px)));
    py = Math.max(0, Math.min(Math.max(0, bounds.height - 1), Math.round(py)));

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
    wc.sendInputEvent({ type: 'mouseMove', x: px, y: py });
    wc.sendInputEvent({
      type: 'mouseWheel',
      x: px,
      y: py,
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
      isHostForeground: () => Boolean(mainWindow && !mainWindow.isDestroyed() && mainWindow.isFocused()),
      focusHost: async () => { await revealMainWindow(); },
    });
    appUseManager.start();
  }
  return appUseManager;
}

async function showAppUseVirtualPointer({ x = 0, y = 0, durationMs = 1200 } = {}) {
  if (!app.isReady()) return;
  if (!appUsePointerWindow || appUsePointerWindow.isDestroyed()) {
    appUsePointerWindow = new BrowserWindow({
      width: 24,
      height: 24,
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
    const pointerHtml = `<!doctype html><meta charset="utf-8"><style>
      html,body{margin:0;width:24px;height:24px;overflow:hidden;background:transparent}
      .p{position:absolute;left:4px;top:4px;box-sizing:border-box;width:16px;height:16px;
        border:2px solid rgba(255,255,255,.96);border-radius:50%;background:#2684ff;
        box-shadow:0 0 0 1px rgba(22,93,200,.85),0 3px 9px rgba(0,0,0,.38)}
    </style><div class="p"></div>`;
    await appUsePointerWindow.loadURL(`data:text/html;charset=utf-8,${encodeURIComponent(pointerHtml)}`);
  }
  const pointerX = Math.round(Number(x) || 0) - 12;
  const pointerY = Math.round(Number(y) || 0) - 12;
  appUsePointerWindow.setPosition(pointerX, pointerY, false);
  appUsePointerWindow.showInactive();
  if (appUsePointerHideTimer) clearTimeout(appUsePointerHideTimer);
  appUsePointerHideTimer = setTimeout(() => {
    if (appUsePointerWindow && !appUsePointerWindow.isDestroyed()) appUsePointerWindow.hide();
  }, Math.max(100, Math.min(10000, Number(durationMs) || 1200)));
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
  if (method === 'setContext') {
    return activateBrowserSession(args || {}).state();
  }
  if (method === 'closeSession') {
    return closeBrowserSession(browserRpcSessionId(args, context));
  }
  const manager = getBrowserTabManager(browserRpcSessionId(args, context));
  const roundId = String(context.roundId || context.round_id || args && (args.roundId || args.round_id) || '').trim();
  if (roundId) manager.setContext({ roundId });
  switch (method) {
    case 'state':
      return manager.state();
    case 'setBounds':
      return manager.setBounds(args || {});
    case 'setObscured':
      return setBrowserSurfaceObscured(args && args.obscured);
    case 'createTab':
      await manager.createTab(args || {});
      return manager.state();
    case 'activateTab':
      return manager.activateTab(args && args.tabId);
    case 'closeTab':
      return manager.closeTab(args && args.tabId);
    case 'navigate':
      return manager.navigate(args || {});
    case 'snapshot':
      return manager.pageSnapshot(args && args.tabId, args && args.maxChars);
    case 'inspect':
      return manager.inspect(args || {});
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
      return manager.reload();
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

function startElectronRpcServer() {
  if (electronRpcServer && electronRpcPort) return Promise.resolve(electronRpcPort);
  const MAX_RETRIES = 3;
  function attempt(retriesLeft) {
    return new Promise((resolve, reject) => {
      const server = http.createServer((req, res) => {
        const rpcPath = String(req.url || '');
        if (req.method !== 'POST' || !['/browser/rpc', '/app/rpc'].includes(rpcPath)) {
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
              : await handleBrowserRpc(
                  String(payload.method || ''),
                  payload.args || {},
                  {
                    sessionId: Object.prototype.hasOwnProperty.call(payload, 'sessionId') ? payload.sessionId : '',
                    roundId: payload.roundId || '',
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

function saveDesktopSettings(updates) {
  const current = readDesktopSettings();
  const next = {
    ...current,
    ...updates,
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
        + `If this keeps happening, check cyrene_error.log in ${getCyreneTempDir()}`
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

async function createMainWindow(shellOverride) {
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
    // Electron's macOS traffic-light image renders slightly below its nominal
    // 14px bounds. Place it 1px above geometric center in the 58px workbench
    // topbar so its visible center aligns with the brand mark and wordmark.
    windowOptions.trafficLightPosition = { x: 12, y: 21 };
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
    hideAllBrowserSessions();
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
    ipcMain.handle('browser:get-state', (_event, info) => handleBrowserRpc('state', {}, info || {}));
    ipcMain.handle('browser:set-bounds', (_event, info) => handleBrowserRpc('setBounds', info || {}, info || {}));
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
      const result = await handleBrowserRpc('reload', {}, info || {});
      getBrowserTabManager(browserRpcSessionId({}, info || {})).recordUserEvent('navigate', { payload: { action: 'reload' } });
      return result;
    });
    ipcMain.handle('browser:set-muted', (_event, info) => handleBrowserRpc('setMuted', info || {}, info || {}));
    ipcMain.handle('browser:screenshot', (_event, info) => handleBrowserRpc('screenshot', info || {}, info || {}));
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
    destroyTray();
    globalShortcut.unregisterAll();
    if (appUseManager) appUseManager.stop();
    if (appUsePointerHideTimer) clearTimeout(appUsePointerHideTimer);
    appUsePointerHideTimer = null;
    if (appUsePointerWindow && !appUsePointerWindow.isDestroyed()) appUsePointerWindow.destroy();
    appUsePointerWindow = null;
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
