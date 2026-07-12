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
const { spawn } = require('child_process');

const APP_NAME = 'Cyrene';
const TEMP_ARTIFACT_TTL_MS = 24 * 60 * 60 * 1000;
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

function cleanupTemporaryArtifacts(ttlMs = TEMP_ARTIFACT_TTL_MS) {
  const tempDir = getCyreneTempDir();
  const cutoff = Date.now() - Math.max(0, Number(ttlMs) || 0);
  try {
    fs.mkdirSync(tempDir, { recursive: true });
    for (const name of fs.readdirSync(tempDir)) {
      const target = path.join(tempDir, name);
      try {
        const stat = fs.lstatSync(target);
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
let tray = null;
let browserTabManager = null;
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
  };
})
`;

function installBrowserSessionGuards() {
  let browserSession = null;
  try {
    browserSession = session.fromPartition(BROWSER_PARTITION);
  } catch (_) {
    return;
  }
  browserSession.setPermissionRequestHandler((_webContents, _permission, callback) => {
    callback(false);
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
  constructor() {
    this.tabs = new Map();
    this.activeTabId = '';
    this.nextTabId = 1;
    this.bounds = { x: 0, y: 0, width: 0, height: 0 };
    this.visible = false;
    this.obscured = false;
    this.attachedTabId = '';
    this._syncTimer = null;
    this.browserContext = { sessionId: '', roundId: '' };
  }

  ownerWindow() {
    return mainWindow && !mainWindow.isDestroyed() ? mainWindow : null;
  }

  createView() {
    const view = new WebContentsView({
      webPreferences: {
        partition: BROWSER_PARTITION,
        contextIsolation: true,
        nodeIntegration: false,
        sandbox: true,
        backgroundThrottling: false,
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
    wc.on('did-fail-load', (_event, code, desc, url) => {
      if (code === -3) return; // aborted by a new navigation
      console.warn(`[electron] Browser tab load failed (${code}) ${url}: ${desc}`);
      update();
    });
    wc.on('did-finish-load', () => this.installUserEventCapture(view).catch(() => {}));
    wc.on('console-message', (_event, _level, message) => {
      this.handleCapturedUserEvent(view, message);
    });
    wc.on('destroyed', () => {
      for (const [id, tab] of this.tabs.entries()) {
        if (tab.view === view) this.tabs.delete(id);
      }
      if (this.activeTabId && !this.tabs.has(this.activeTabId)) {
        this.activeTabId = this.tabs.keys().next().value || '';
      }
      this.attachedTabId = this.attachedTabId === this.activeTabId ? this.attachedTabId : '';
      this.emitState();
    });
    return view;
  }

  setContext(info = {}) {
    const sessionId = String(info.sessionId || info.session_id || '').trim();
    const roundId = String(info.roundId || info.round_id || '').trim();
    if (sessionId) this.browserContext.sessionId = sessionId;
    this.browserContext.roundId = roundId;
    return this.state();
  }

  _tabForView(view) {
    for (const tab of this.tabs.values()) {
      if (tab.view === view) return tab;
    }
    return null;
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
            text: clean(el.innerText || el.textContent || el.getAttribute && (el.getAttribute("aria-label") || el.getAttribute("title") || el.getAttribute("placeholder")) || "", 120),
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
        document.addEventListener("scroll", () => {
          const now = Date.now();
          if (now - lastScroll < 500) return;
          lastScroll = now;
          emit("scroll", {
            scrollX: Math.round(window.scrollX || 0),
            scrollY: Math.round(window.scrollY || 0),
          }, {});
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
      activeTabId: this.activeTabId,
      visible: this.visible,
      tabs,
      activeTab: tabs.find((tab) => tab.id === this.activeTabId) || null,
      obscured: this.obscured,
    };
  }

  emitState() {
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
    const tab = { id, view, url: normalizeBrowserUrl(url), title: '' };
    this.tabs.set(id, tab);
    if (activate || !this.activeTabId) this.activeTabId = id;
    if (tab.url && tab.url !== 'about:blank') {
      await view.webContents.loadURL(tab.url);
    } else {
      view.webContents.loadURL('about:blank').catch(() => {});
    }
    this.syncAttachedView();
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
    const win = this.ownerWindow();
    if (!win || !tab) return;
    try { win.contentView.removeChildView(tab.view); } catch (_) {}
    if (this.attachedTabId === tab.id) this.attachedTabId = '';
  }

  syncAttachedView() {
    const win = this.ownerWindow();
    if (!win) return;
    const active = this.tabs.get(this.activeTabId);
    for (const tab of this.tabs.values()) {
      if (!active || tab.id !== active.id || !this.visible || this.obscured) this.detachView(tab);
    }
    if (!active || !this.visible || this.obscured) return;
    if (this.attachedTabId !== active.id) {
      try { win.contentView.addChildView(active.view); } catch (_) {}
      this.attachedTabId = active.id;
    }
    try { active.view.setBounds(this.bounds); } catch (_) {}
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
    this.visible = info.visible === true && width > 8 && height > 8;
    // Debounce sync when visible — rapid bounds changes (e.g. resize, requestAnimationFrame)
    // can trigger concurrent WebContentsView setBounds calls that SIGSEGV on Electron 35.
    if (!this.visible) {
      if (this._syncTimer) { clearTimeout(this._syncTimer); this._syncTimer = null; }
      this.syncAttachedView();
    } else if (!this._syncTimer) {
      this._syncTimer = setTimeout(() => { this._syncTimer = null; this.syncAttachedView(); }, 50);
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
    try {
      text = await wc.executeJavaScript(
        '(() => document.body ? document.body.innerText : "")()',
        true
      );
    } catch (_) {
      text = '';
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
      const before = await this._contentState(wc);
      this._markAgentInput(tab);
      wc.sendInputEvent({ type: 'mouseMove', x: info.x, y: info.y });
      wc.sendInputEvent({ type: 'mouseDown', x: info.x, y: info.y, button: 'left', clickCount: 1 });
      wc.sendInputEvent({ type: 'mouseUp', x: info.x, y: info.y, button: 'left', clickCount: 1 });
      await this._waitForClickOutcome(wc, before);
      return this._finishClick(tab, info);
    } finally {
      tab.agentClickInFlight = false;
      tab.lastAgentClickAt = Date.now();
    }
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

  async scroll({ deltaX = 0, deltaY = 0, tabId = '' } = {}) {
    const tab = tabId ? this.tabs.get(String(tabId)) : this.tabs.get(this.activeTabId);
    if (!tab) return { ok: false, error: 'No browser tab is open.' };
    this._markAgentInput(tab);
    await tab.view.webContents.executeJavaScript(`window.scrollBy(${JSON.stringify(deltaX)},${JSON.stringify(deltaY)})`, true).catch(() => {});
    return { ok: true };
  }
}

function getBrowserTabManager() {
  if (!browserTabManager) browserTabManager = new BrowserTabManager();
  return browserTabManager;
}

async function handleBrowserRpc(method, args) {
  const manager = getBrowserTabManager();
  switch (method) {
    case 'state':
      return manager.state();
    case 'setBounds':
      return manager.setBounds(args || {});
    case 'setContext':
      return manager.setContext(args || {});
    case 'setObscured':
      return manager.setObscured(args && args.obscured);
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

function startElectronRpcServer() {
  if (electronRpcServer && electronRpcPort) return Promise.resolve(electronRpcPort);
  const MAX_RETRIES = 3;
  function attempt(retriesLeft) {
    return new Promise((resolve, reject) => {
      const server = http.createServer((req, res) => {
        if (req.method !== 'POST' || req.url !== '/browser/rpc') {
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
            const result = await handleBrowserRpc(String(payload.method || ''), payload.args || {});
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
    if (browserTabManager) browserTabManager.setBounds({ visible: false });
  });
  // did-start-navigation 补充 will-navigate 不触发的场景（Cmd+R 等），
  // 但排除 SPA 同文档导航（hash 变更 / pushState）。
  window.webContents.on('did-start-navigation', (event, url, isInPlace, isMainFrame) => {
    if (isInPlace) return;
    if (isMainFrame && browserTabManager) browserTabManager.setBounds({ visible: false });
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
    if (browserTabManager) browserTabManager.setBounds({ visible: false });
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
    ipcMain.handle('browser:get-state', () => getBrowserTabManager().state());
    ipcMain.handle('browser:set-bounds', (_event, info) => handleBrowserRpc('setBounds', info || {}));
    ipcMain.handle('browser:set-context', (_event, info) => handleBrowserRpc('setContext', info || {}));
    ipcMain.handle('browser:set-obscured', (_event, obscured) => handleBrowserRpc('setObscured', { obscured: obscured === true }));
    ipcMain.handle('browser:create-tab', async (_event, info) => {
      const result = await handleBrowserRpc('createTab', info || {});
      getBrowserTabManager().recordUserEvent('navigate', { payload: { action: 'create_tab', url: info && info.url || '' } });
      return result;
    });
    ipcMain.handle('browser:activate-tab', async (_event, tabId) => {
      const result = await handleBrowserRpc('activateTab', { tabId });
      getBrowserTabManager().recordUserEvent('select_tab', { payload: { tabId: String(tabId || '') } });
      return result;
    });
    ipcMain.handle('browser:close-tab', async (_event, tabId) => {
      getBrowserTabManager().recordUserEvent('close_tab', { payload: { tabId: String(tabId || '') } });
      return handleBrowserRpc('closeTab', { tabId });
    });
    ipcMain.handle('browser:navigate', async (_event, info) => {
      const result = await handleBrowserRpc('navigate', info || {});
      getBrowserTabManager().recordUserEvent('navigate', { payload: { url: info && info.url || '' } });
      return result;
    });
    ipcMain.handle('browser:go-back', async () => {
      const result = await handleBrowserRpc('goBack', {});
      getBrowserTabManager().recordUserEvent('navigate', { payload: { action: 'go_back' } });
      return result;
    });
    ipcMain.handle('browser:go-forward', async () => {
      const result = await handleBrowserRpc('goForward', {});
      getBrowserTabManager().recordUserEvent('navigate', { payload: { action: 'go_forward' } });
      return result;
    });
    ipcMain.handle('browser:reload', async () => {
      const result = await handleBrowserRpc('reload', {});
      getBrowserTabManager().recordUserEvent('navigate', { payload: { action: 'reload' } });
      return result;
    });
    ipcMain.handle('browser:set-muted', (_event, info) => handleBrowserRpc('setMuted', info || {}));
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
