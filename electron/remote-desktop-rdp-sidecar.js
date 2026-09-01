'use strict';

// Linux development implementation of the FreeRDP sidecar contract. It keeps
// the RDP client on an isolated Xvfb display, captures only that window, and
// reuses Cyrene's existing WebRTC media/input host.

const fs = require('fs');
const os = require('os');
const path = require('path');
const crypto = require('crypto');
const readline = require('readline');
const { spawn, spawnSync } = require('child_process');
const {
  app,
  BrowserWindow,
  clipboard,
  desktopCapturer,
  ipcMain,
  nativeImage,
  session,
} = require('electron');

const sidecarUserData = path.join(os.tmpdir(), `cyrene-rdp-electron-${process.pid}`);
app.setPath('userData', sidecarUserData);
app.commandLine.appendSwitch('ozone-platform', 'x11');
app.commandLine.appendSwitch('disable-gpu');
app.commandLine.appendSwitch('disable-features', 'WaylandWindowDecorations');

const state = {
  sessionId: '',
  rdp: null,
  rdpWindowId: '',
  window: null,
  partition: null,
  permissions: {},
  viewport: { width: 1920, height: 1080, device_pixel_ratio: 1 },
  qualityMode: 'auto',
  viewportTimer: null,
  pendingViewport: null,
  answerResolve: null,
  answerReject: null,
  inputQueue: Promise.resolve(),
  pendingMove: null,
  moveQueued: false,
  clipboardTimer: null,
  clipboardText: '',
  clipboardImageHash: '',
  clipboardFileHash: '',
  imageOffers: new Map(),
  fileOffers: new Map(),
  lastRdpError: '',
  stopping: false,
};

function terminateXvfb() {
  const xvfbPid = Number(process.env.CYRENE_RDP_XVFB_PID || 0);
  if (xvfbPid > 1) {
    try { process.kill(xvfbPid, 'SIGTERM'); } catch (_) {}
  }
}

function diagnostic(...values) {
  process.stderr.write(`[cyrene-rdp] ${values.map(String).join(' ')}\n`);
}

function rdpFailure() {
  const detail = String(state.lastRdpError || '');
  if (/ERRCONNECT_LOGON_FAILURE|STATUS_LOGON_FAILURE|authentication fail/i.test(detail)) {
    const error = new Error('RDP authentication failed. Use the credentials configured for Remote Login on the controlled device.');
    error.code = 'rdp_authentication_failed';
    return error;
  }
  if (/HOST IDENTIFICATION HAS CHANGED|certificate verification failed|host key verification failed/i.test(detail)) {
    const error = new Error('The local RDP certificate could not be verified.');
    error.code = 'rdp_certificate_verification_failed';
    return error;
  }
  const error = new Error('The local RDP client exited before opening a desktop window.');
  error.code = 'freerdp_connection_failed';
  return error;
}

function executable(name) {
  const result = spawnSync('sh', ['-lc', `command -v ${name}`], {
    encoding: 'utf8',
    stdio: ['ignore', 'pipe', 'ignore'],
  });
  return result.status === 0 ? String(result.stdout || '').trim() : '';
}

function run(command, args, timeout = 10000) {
  return new Promise((resolve, reject) => {
    const child = spawn(command, args.map(String), { stdio: ['ignore', 'pipe', 'pipe'] });
    let stdout = '';
    let stderr = '';
    const timer = setTimeout(() => {
      child.kill('SIGKILL');
      reject(new Error(`${path.basename(command)}_timeout`));
    }, timeout);
    child.stdout.on('data', (chunk) => { stdout += chunk.toString(); });
    child.stderr.on('data', (chunk) => { stderr += chunk.toString(); });
    child.once('error', (error) => {
      clearTimeout(timer);
      reject(error);
    });
    child.once('exit', (code) => {
      clearTimeout(timer);
      if (code === 0) resolve(stdout.trim());
      else reject(new Error(stderr.trim() || `${path.basename(command)}_failed_${code}`));
    });
  });
}

function normalizedViewport(raw) {
  const value = raw && typeof raw === 'object' ? raw : {};
  const ratio = Math.max(0.5, Math.min(2, Number(value.device_pixel_ratio || 1)));
  return {
    width: Math.max(320, Math.min(3840, Math.round(Number(value.width || 1920) * ratio))),
    height: Math.max(240, Math.min(2160, Math.round(Number(value.height || 1080) * ratio))),
    device_pixel_ratio: ratio,
  };
}

function keyName(raw) {
  const value = String(raw || '').slice(0, 64);
  return ({
    ArrowUp: 'Up', ArrowDown: 'Down', ArrowLeft: 'Left', ArrowRight: 'Right',
    Enter: 'Return', PageUp: 'Prior', PageDown: 'Next', ' ': 'space',
    Escape: 'Escape', Backspace: 'BackSpace', Delete: 'Delete', Tab: 'Tab',
  })[value] || value;
}

async function resizeRdpWindow(rawViewport) {
  state.viewport = normalizedViewport(rawViewport);
  if (!state.rdpWindowId) return;
  await run('xdotool', [
    'windowmove', state.rdpWindowId, 0, 0,
    'windowsize', '--sync', state.rdpWindowId, state.viewport.width, state.viewport.height,
  ]).catch((error) => diagnostic('resize failed:', error.message));
  if (state.window && !state.window.isDestroyed()) {
    state.window.webContents.send('remote-desktop:command', {
      operation: 'set_viewport',
      width: state.viewport.width / state.viewport.device_pixel_ratio,
      height: state.viewport.height / state.viewport.device_pixel_ratio,
      device_pixel_ratio: state.viewport.device_pixel_ratio,
    });
  }
}

function scheduleViewport(rawViewport) {
  state.pendingViewport = rawViewport && typeof rawViewport === 'object' ? { ...rawViewport } : {};
  if (state.viewportTimer) clearTimeout(state.viewportTimer);
  state.viewportTimer = setTimeout(() => {
    state.viewportTimer = null;
    const latest = state.pendingViewport;
    state.pendingViewport = null;
    resizeRdpWindow(latest).catch((error) => diagnostic('viewport update failed:', error.message));
  }, 120);
}

async function inputEvent(event) {
  if (!state.rdpWindowId) return;
  const type = String(event && event.type || '');
  if (type === 'pointer') {
    const x = Math.round(Math.max(0, Math.min(1, Number(event.x_normalized || 0))) * Math.max(1, state.viewport.width - 1));
    const y = Math.round(Math.max(0, Math.min(1, Number(event.y_normalized || 0))) * Math.max(1, state.viewport.height - 1));
    const action = String(event.action || 'move');
    const prefix = ['mousemove', '--window', state.rdpWindowId, x, y];
    if (action === 'move') return run('xdotool', prefix);
    if (action === 'button_down') return run('xdotool', prefix.concat(['mousedown', '1']));
    if (action === 'button_up') return run('xdotool', prefix.concat(['mouseup', '1']));
    if (action === 'scroll') {
      const button = Number(event.delta_y || 0) > 0 ? 5 : 4;
      return run('xdotool', prefix.concat(['click', button]));
    }
    const button = action === 'right_click' ? 3 : 1;
    const repeat = action === 'double_click' ? 2 : 1;
    return run('xdotool', prefix.concat(['click', '--repeat', repeat, button]));
  }
  if (type === 'text') {
    return run('xdotool', ['type', '--window', state.rdpWindowId, '--clearmodifiers', '--delay', 1, String(event.text || '').slice(0, 65536)], 15000);
  }
  if (type === 'key') {
    const modifiers = Array.isArray(event.modifiers)
      ? event.modifiers.map((item) => String(item) === 'meta' ? 'super' : String(item)) : [];
    return run('xdotool', ['key', '--window', state.rdpWindowId, '--clearmodifiers', modifiers.concat([keyName(event.key)]).join('+')]);
  }
}

function queueInput(event) {
  if (String(event && event.type || '') === 'pointer' && String(event.action || '') === 'move') {
    state.pendingMove = event;
    if (state.moveQueued) return;
    state.moveQueued = true;
    state.inputQueue = Promise.resolve(state.inputQueue).catch(() => {}).then(async () => {
      const latest = state.pendingMove;
      state.pendingMove = null;
      state.moveQueued = false;
      if (latest) await inputEvent(latest);
      if (state.pendingMove) queueInput(state.pendingMove);
    });
    state.inputQueue.catch(() => {});
    return;
  }
  state.inputQueue = Promise.resolve(state.inputQueue).catch(() => {}).then(() => inputEvent(event || {}));
  state.inputQueue.catch(() => {});
}

function writeClipboardFiles(paths) {
  const resolved = (Array.isArray(paths) ? paths : []).map((item) => path.resolve(String(item))).filter(fs.existsSync);
  const uriList = resolved.map((item) => `file://${encodeURI(item)}`).join('\r\n');
  clipboard.write({ text: uriList, bookmark: '' });
  if (uriList) clipboard.writeBuffer('text/uri-list', Buffer.from(uriList, 'utf8'));
}

function startClipboardMonitor() {
  if (state.clipboardTimer) return;
  state.clipboardTimer = setInterval(() => {
    if (!state.window || state.window.isDestroyed()) return;
    if (state.permissions.clipboard_text === true) {
      const text = clipboard.readText().slice(0, 1024 * 1024);
      if (text && text !== state.clipboardText && !text.startsWith('file://')) {
        state.clipboardText = text;
        state.window.webContents.send('remote-desktop:clipboard', { text, revision: Date.now() });
      }
    }
    if (state.permissions.clipboard_image === true) {
      const image = clipboard.readImage();
      if (!image.isEmpty()) {
        const data = image.toPNG();
        const hash = crypto.createHash('sha256').update(data).digest('hex');
        if (hash !== state.clipboardImageHash) {
          state.clipboardImageHash = hash;
          const offerId = `rdp_image_${crypto.randomUUID().replace(/-/g, '')}`;
          state.imageOffers.set(offerId, data);
          const size = image.getSize();
          state.window.webContents.send('remote-desktop:clipboard-image-offer', {
            offer_id: offerId, sha256: hash, size: data.length, width: size.width, height: size.height,
          });
        }
      }
    }
    if (state.permissions.clipboard_file === true) {
      const uriList = clipboard.readBuffer('text/uri-list').toString('utf8').trim();
      const paths = uriList.split(/\r?\n/).filter((item) => item.startsWith('file://')).map((item) => decodeURI(item.slice(7))).filter(fs.existsSync);
      const hash = crypto.createHash('sha256').update(paths.join('\n')).digest('hex');
      if (paths.length && hash !== state.clipboardFileHash) {
        state.clipboardFileHash = hash;
        const offerId = `rdp_files_${crypto.randomUUID().replace(/-/g, '')}`;
        state.fileOffers.set(offerId, paths);
        state.window.webContents.send('remote-desktop:clipboard-file-offer', {
          offer_id: offerId,
          entries: paths.map((item) => {
            const info = fs.statSync(item);
            return { name: path.basename(item), kind: info.isDirectory() ? 'directory' : 'file', size: info.isFile() ? info.size : 0 };
          }),
        });
      }
    }
  }, 650);
  if (state.clipboardTimer.unref) state.clipboardTimer.unref();
}

function bindIpc() {
  ipcMain.on('remote-desktop:answer', (_event, payload) => {
    if (!state.answerResolve || String(payload && payload.session_id || '') !== state.sessionId) return;
    const resolve = state.answerResolve;
    state.answerResolve = null;
    state.answerReject = null;
    resolve(payload || {});
  });
  ipcMain.on('remote-desktop:input', (_event, payload) => {
    if (state.permissions.input === true && String(payload && payload.session_id || '') === state.sessionId) queueInput(payload.event || {});
  });
  ipcMain.on('remote-desktop:control', (_event, payload) => {
    if (String(payload && payload.session_id || '') !== state.sessionId) return;
    const message = payload && payload.message && typeof payload.message === 'object' ? payload.message : {};
    if (message.type === 'input' && state.permissions.input === true) queueInput(message.event || {});
    if (message.type === 'viewport') scheduleViewport(message);
    if (message.type === 'clipboard:text' && state.permissions.clipboard_text === true) {
      state.clipboardText = String(message.text || '').slice(0, 1024 * 1024);
      clipboard.writeText(state.clipboardText);
    }
  });
  ipcMain.on('remote-desktop:state', (_event, payload) => {
    if (payload && payload.connection_state === 'failed' && state.answerReject) {
      state.answerReject(new Error('webrtc_connection_failed'));
    }
  });
}

function startFreeRdp(args) {
  const binary = executable('xfreerdp3') || executable('xfreerdp');
  if (!binary) throw new Error('freerdp_client_missing');
  const target = args.target && typeof args.target === 'object' ? args.target : {};
  const credentials = args.credentials && typeof args.credentials === 'object' ? args.credentials : {};
  const rdpArguments = [
    `/v:${String(target.host || '127.0.0.1')}:${Math.max(1, Number(target.port || 3389))}`,
    `/u:${String(credentials.username || '')}`,
    `/p:${String(credentials.password || '')}`,
    `/size:${state.viewport.width}x${state.viewport.height}`,
    '+dynamic-resolution',
    '/cert:tofu',
    '/sec:nla',
    '+clipboard',
    '/audio-mode:redirect',
    '/network:auto',
    '/bpp:32',
  ];
  if (String(credentials.domain || '')) rdpArguments.push(`/d:${String(credentials.domain)}`);
  if (state.permissions.microphone === true) rdpArguments.push('/microphone:sys:pulse');
  state.lastRdpError = '';
  const child = spawn(binary, ['/args-from:stdin'], { stdio: ['pipe', 'pipe', 'pipe'] });
  const consumeOutput = (chunk) => {
    const text = chunk.toString().replace(/\/p:[^\s]+/g, '/p:[redacted]').trim();
    if (!text) return;
    state.lastRdpError = `${state.lastRdpError}\n${text}`.slice(-16000);
    diagnostic(text.slice(0, 2000));
  };
  child.stdout.on('data', consumeOutput);
  child.stderr.on('data', consumeOutput);
  child.once('exit', (code) => {
    if (!state.stopping && state.answerReject) state.answerReject(new Error(`freerdp_exited_${code}`));
  });
  child.stdin.end(`${rdpArguments.join('\n')}\n`);
  credentials.username = '';
  credentials.password = '';
  credentials.domain = '';
  state.rdp = child;
}

async function waitForRdpWindow() {
  const deadline = Date.now() + 25000;
  while (Date.now() < deadline) {
    if (!state.rdp || state.rdp.exitCode !== null) throw rdpFailure();
    const found = await run('xdotool', ['search', '--onlyvisible', '--class', 'xfreerdp'], 2000).catch(() => '');
    const ids = String(found || '').split(/\s+/).filter(Boolean);
    if (ids.length) {
      state.rdpWindowId = ids[ids.length - 1];
      await run('xdotool', [
        'windowmove', state.rdpWindowId, 0, 0,
        'windowsize', '--sync', state.rdpWindowId, state.viewport.width, state.viewport.height,
      ]);
      return;
    }
    await new Promise((resolve) => setTimeout(resolve, 100));
  }
  throw new Error('freerdp_window_timeout');
}

async function captureSource() {
  const sources = await desktopCapturer.getSources({
    types: ['window'],
    thumbnailSize: { width: 0, height: 0 },
    fetchWindowIcons: false,
  });
  const id = String(state.rdpWindowId || '');
  const source = sources.find((item) => {
    const sourceId = String(item.id || '');
    return sourceId.includes(`:${id}:`) || sourceId.includes(`:${Number(id).toString(16)}:`);
  }) || sources.find((item) => /freerdp|remote desktop/i.test(String(item.name || '')));
  if (!source) throw new Error('freerdp_capture_source_missing');
  return source;
}

async function createMediaHost(args) {
  state.partition = session.fromPartition(`cyrene-rdp-${state.sessionId}-${Date.now()}`, { cache: false });
  state.partition.setPermissionCheckHandler((_contents, permission) => ['media', 'display-capture'].includes(permission));
  state.partition.setPermissionRequestHandler((_contents, permission, callback) => callback(['media', 'display-capture'].includes(permission)));
  state.partition.setDisplayMediaRequestHandler(async (_request, callback) => {
    try {
      const source = await captureSource();
      callback({ video: source, audio: state.permissions.system_audio === true ? 'loopback' : undefined });
    } catch (error) {
      diagnostic('capture source failed:', error.message);
      callback({});
    }
  }, { useSystemPicker: false });
  state.window = new BrowserWindow({
    show: false,
    width: 800,
    height: 600,
    frame: false,
    skipTaskbar: true,
    webPreferences: {
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
      preload: path.join(__dirname, 'remote-desktop-preload.js'),
      partition: state.partition.getPartition(),
      backgroundThrottling: false,
    },
  });
  await state.window.loadFile(path.join(__dirname, 'remote-desktop-host.html'));
  const answer = new Promise((resolve, reject) => {
    state.answerResolve = resolve;
    state.answerReject = reject;
    setTimeout(() => {
      if (state.answerReject === reject) {
        state.answerResolve = null;
        state.answerReject = null;
        reject(new Error('freerdp_media_timeout'));
      }
    }, 30000);
  });
  state.window.webContents.send('remote-desktop:start', {
    session_id: state.sessionId,
    offer: args.offer,
    quality_mode: state.qualityMode,
    ice_servers: Array.isArray(args.ice_servers) ? args.ice_servers : [],
    permissions: state.permissions,
  });
  return answer;
}

async function connect(args) {
  if (state.rdp) return { ok: false, code: 'freerdp_session_exists', error: 'An RDP session is already active.' };
  state.sessionId = String(args.session_id || '');
  state.permissions = args.permissions && typeof args.permissions === 'object' ? { ...args.permissions } : {};
  state.qualityMode = String(args.quality_mode || 'auto');
  state.viewport = normalizedViewport(args.viewport);
  try {
    startFreeRdp(args);
    await waitForRdpWindow();
    const answer = await createMediaHost(args);
    startClipboardMonitor();
    return { ...answer, ok: answer.ok !== false, display_id: 'rdp-display-1' };
  } catch (error) {
    // Keep Electron's X display alive until the JSON failure has been written
    // to stdout. Destroying Xvfb here can terminate Electron before consume()
    // emits the real FreeRDP error, leaving the parent with only
    // `freerdp_sidecar_stopped`.
    await stop({ terminateRuntime: false });
    return {
      ok: false,
      code: String(error && error.code || 'freerdp_connect_failed'),
      error: String(error && error.message || error),
    };
  }
}

async function stop(options = {}) {
  const terminateRuntime = options.terminateRuntime !== false;
  state.stopping = true;
  if (state.clipboardTimer) clearInterval(state.clipboardTimer);
  state.clipboardTimer = null;
  if (state.viewportTimer) clearTimeout(state.viewportTimer);
  state.viewportTimer = null;
  state.pendingViewport = null;
  if (state.window && !state.window.isDestroyed()) {
    state.window.webContents.send('remote-desktop:command', { operation: 'disconnect' });
    state.window.destroy();
  }
  state.window = null;
  if (state.rdp && state.rdp.exitCode === null) {
    state.rdp.kill('SIGTERM');
    await new Promise((resolve) => setTimeout(resolve, 250));
    if (state.rdp && state.rdp.exitCode === null) state.rdp.kill('SIGKILL');
  }
  state.rdp = null;
  if (terminateRuntime) {
    const xvfbPid = Number(process.env.CYRENE_RDP_XVFB_PID || 0);
    if (xvfbPid > 1) {
      try { process.kill(xvfbPid, 'SIGTERM'); } catch (_) {}
    }
    try { fs.rmSync(sidecarUserData, { recursive: true, force: true }); } catch (_) {}
    const configDir = String(process.env.CYRENE_RDP_CONFIG_DIR || '');
    if (configDir && path.basename(configDir).startsWith('cyrene-rdp-config-')) {
      try { fs.rmSync(configDir, { recursive: true, force: true }); } catch (_) {}
    }
  }
  state.stopping = false;
}

async function copyOfferFiles(offerId, destination) {
  const paths = state.fileOffers.get(String(offerId)) || [];
  if (!paths.length) throw new Error('desktop_clipboard_offer_not_found');
  await fs.promises.mkdir(destination, { recursive: true });
  const entries = [];
  for (const source of paths) {
    const target = path.join(destination, path.basename(source));
    await fs.promises.cp(source, target, { recursive: true, force: true });
    entries.push({ name: path.basename(source), path: target });
  }
  return entries;
}

async function handle(request) {
  const method = String(request && request.method || '');
  const args = request && request.args && typeof request.args === 'object' ? request.args : {};
  if (method === 'connect') return connect(args);
  if (method === 'disconnect') {
    await stop();
    setImmediate(() => app.quit());
    return { ok: true };
  }
  if (method === 'displays') return { ok: true, displays: [{ id: 'rdp-display-1', name: 'RDP display', width: state.viewport.width, height: state.viewport.height, scale: 1, primary: true }] };
  if (method === 'select_display') return String(args.display_id || '') === 'rdp-display-1' ? { ok: true } : { ok: false, code: 'desktop_display_not_found' };
  if (method === 'set_quality') {
    state.qualityMode = String(args.quality_mode || 'auto');
    if (state.window && !state.window.isDestroyed()) state.window.webContents.send('remote-desktop:command', { operation: 'set_quality', quality_mode: state.qualityMode });
    return { ok: true };
  }
  if (method === 'set_microphone') {
    if (state.window && !state.window.isDestroyed()) state.window.webContents.send('remote-desktop:command', { operation: 'set_microphone', enabled: args.enabled === true });
    return { ok: true };
  }
  if (method === 'security_state') return { ok: true, secure_surface: true, security_epoch: 1 };
  if (method === 'write_clipboard_image') {
    const image = nativeImage.createFromPath(String(args.path || ''));
    if (image.isEmpty()) return { ok: false, code: 'desktop_clipboard_image_invalid' };
    state.clipboardImageHash = crypto.createHash('sha256').update(image.toPNG()).digest('hex');
    clipboard.writeImage(image);
    return { ok: true };
  }
  if (method === 'export_clipboard_image') {
    const data = state.imageOffers.get(String(args.offer_id || ''));
    if (!data) return { ok: false, code: 'desktop_clipboard_offer_not_found' };
    await fs.promises.writeFile(String(args.path || ''), data);
    return { ok: true, path: String(args.path || ''), size: data.length };
  }
  if (method === 'ack_clipboard_image') {
    state.imageOffers.delete(String(args.offer_id || ''));
    return { ok: true };
  }
  if (method === 'write_clipboard_files') {
    const paths = Array.isArray(args.paths) ? args.paths.map(String).filter(fs.existsSync) : [];
    state.clipboardFileHash = crypto.createHash('sha256').update(paths.map((item) => path.resolve(item)).join('\n')).digest('hex');
    writeClipboardFiles(paths);
    return { ok: true };
  }
  if (method === 'export_clipboard_files') {
    try {
      const entries = await copyOfferFiles(String(args.offer_id || ''), String(args.path || ''));
      return { ok: true, path: String(args.path || ''), entries };
    } catch (error) {
      return { ok: false, code: String(error.message || 'desktop_clipboard_offer_not_found') };
    }
  }
  if (method === 'ack_clipboard_files') {
    state.fileOffers.delete(String(args.offer_id || ''));
    return { ok: true };
  }
  return { ok: false, code: 'freerdp_sidecar_unknown_method', error: `Unknown sidecar method: ${method}` };
}

function output(value) {
  process.stdout.write(`${JSON.stringify(value)}\n`);
}

async function consume(request) {
  try {
    output(await handle(request));
  } catch (error) {
    output({ ok: false, code: 'freerdp_sidecar_failed', error: String(error && error.message || error) });
  }
}

async function start() {
  bindIpc();
  const bootstrapFd = Number(process.env.CYRENE_RDP_BOOTSTRAP_FD || -1);
  if (bootstrapFd < 0) throw new Error('freerdp_bootstrap_missing');
  const bootstrap = fs.readFileSync(bootstrapFd, 'utf8');
  try { fs.closeSync(bootstrapFd); } catch (_) {}
  const first = JSON.parse(bootstrap.trim());
  await consume(first);
  const lines = readline.createInterface({ input: process.stdin, crlfDelay: Infinity });
  let chain = Promise.resolve();
  lines.on('line', (line) => {
    if (!line.trim()) return;
    chain = chain.then(() => consume(JSON.parse(line))).catch((error) => output({ ok: false, code: 'freerdp_sidecar_invalid_request', error: String(error.message || error) }));
  });
  lines.on('close', () => stop().finally(() => app.quit()));
}

process.on('SIGTERM', () => stop().finally(() => app.quit()));
process.on('SIGINT', () => stop().finally(() => app.quit()));
process.on('exit', terminateXvfb);
app.whenReady().then(start).catch((error) => {
  output({ ok: false, code: 'freerdp_sidecar_start_failed', error: String(error && error.message || error) });
  stop().finally(() => app.exit(1));
});
