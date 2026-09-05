const crypto = require('crypto');
const fs = require('fs');
const path = require('path');
const { execFile, spawn } = require('child_process');
const { StringDecoder } = require('string_decoder');

const MANIFEST_VERSION = 'app-use-schemes-v3';
const DEFAULT_SESSION_TTL_MS = 5 * 60 * 1000;
const MAX_SCROLL_AT_AMOUNT = 50_000;
const AGENT_CURSOR_FADE_IN_MS = 150;
const AGENT_CURSOR_MOVE_MS = 180;
const AGENT_CURSOR_PRESS_MS = 100;
const SEMANTIC_MODE_CAPABILITIES = new Set([
  'snapshot', 'inspect', 'find', 'press', 'set_value', 'select', 'toggle', 'scroll', 'type_text',
  'semantic_double_click', 'semantic_drag', 'wait',
]);
const VISUAL_MODE_CAPABILITIES = new Set([
  'click_at', 'double_click', 'right_click', 'hover_at', 'drag', 'swipe', 'scroll_at',
  'key_chord', 'key_sequence', 'visual_describe', 'focus_window', 'restore_previous_focus',
]);
const WINDOWS_LOW_LATENCY_CAPABILITIES = new Set([
  'pointer_event', 'right_click', 'scroll_at', 'key_sequence',
]);

const CAPABILITIES = Object.freeze([
  { name: 'snapshot', description: 'Read a compact semantic accessibility snapshot of the target window.', arguments: { scope_ref: 'string?', max_nodes: 'integer?', max_depth: 'integer?' }, background: 'safe' },
  { name: 'inspect', description: 'Read the next semantic layer below one element ref, traversing transparent structural wrappers. Deep Electron/Chromium trees may require max_depth 12 or more.', arguments: { ref: 'string', max_nodes: 'integer?', max_depth: 'integer?' }, background: 'safe' },
  { name: 'find', description: 'Find elements in the semantic tree by role, subrole, name, value, action, native action, automation id, class name, or state.', arguments: { role: 'string?', subrole: 'string?', name: 'string?', contains: 'string?', action: 'string?', native_action: 'string?', automation_id: 'string?', class_name: 'string?', enabled: 'boolean?', max_results: 'integer?' }, background: 'safe' },
  { name: 'press', description: 'Invoke the native default action of a button, menu item, link, or similar control.', arguments: { ref: 'string' }, background: 'safe_when_supported' },
  { name: 'set_value', description: 'Set the value of an editable control through the accessibility API.', arguments: { ref: 'string', value: 'string' }, background: 'safe_when_supported' },
  { name: 'select', description: 'Select a list item, menu item, tab, or option through its native action.', arguments: { ref: 'string' }, background: 'safe_when_supported' },
  { name: 'toggle', description: 'Toggle a checkbox, switch, or expandable control.', arguments: { ref: 'string' }, background: 'safe_when_supported' },
  { name: 'scroll', description: 'Scroll an accessible container or the target window.', arguments: { ref: 'string?', direction: 'up|down|left|right', amount: 'integer?' }, background: 'safe_when_supported' },
  { name: 'type_text', description: 'Write text to a semantically editable element and verify its value. This works in the background only when the accessibility provider exposes a writable value.', arguments: { ref: 'string', text: 'string', replace: 'boolean?' }, background: 'safe_when_supported' },
  { name: 'semantic_double_click', description: 'Invoke a provider-declared semantic double-click action without coordinates or focus.', arguments: { ref: 'string' }, background: 'safe_when_supported' },
  { name: 'semantic_drag', description: 'Invoke a provider-declared semantic move, reorder, resize, or drag action without coordinates or focus.', arguments: { ref: 'string' }, background: 'safe_when_supported' },
  { name: 'click_at', description: 'Primary App Use click tool. Click the latest calibrated point with the real OS pointer. Coordinates are window-relative by default and allow_foreground_input=true is required.', arguments: { x: 'number', y: 'number', coordinate_space: 'window|screen?', allow_foreground_input: 'boolean' }, background: 'requires_focus' },
  { name: 'double_click', description: 'Double-click with the real OS pointer; allow_foreground_input=true is required.', arguments: { x: 'number', y: 'number', coordinate_space: 'window|screen?', interval_ms: 'integer?', allow_foreground_input: 'boolean' }, background: 'requires_focus' },
  { name: 'right_click', description: 'Right-click with the real OS pointer; allow_foreground_input=true is required.', arguments: { x: 'number', y: 'number', coordinate_space: 'window|screen?', allow_foreground_input: 'boolean' }, background: 'requires_focus' },
  { name: 'hover_at', description: 'Move the real OS pointer; allow_foreground_input=true is required. This is unavailable in a background-only session.', arguments: { x: 'number', y: 'number', coordinate_space: 'window|screen?', duration_ms: 'integer?', allow_foreground_input: 'boolean' }, background: 'requires_focus' },
  { name: 'drag', description: 'Drag with the real OS pointer; allow_foreground_input=true is required.', arguments: { from_x: 'number', from_y: 'number', to_x: 'number', to_y: 'number', coordinate_space: 'window|screen?', duration_ms: 'integer?', allow_foreground_input: 'boolean' }, background: 'requires_focus' },
  { name: 'swipe', description: 'Swipe with the real OS pointer; allow_foreground_input=true is required.', arguments: { x: 'number', y: 'number', direction: 'up|down|left|right', distance: 'number?', coordinate_space: 'window|screen?', duration_ms: 'integer?', allow_foreground_input: 'boolean' }, background: 'requires_focus' },
  { name: 'scroll_at', description: `Send real OS wheel events at a point. On macOS, amount is pixels (default 30); on Windows, amount is wheel steps (default 3). The maximum is ${MAX_SCROLL_AT_AMOUNT}, injected in safe increments; allow_foreground_input=true is required.`, arguments: { x: 'number', y: 'number', direction: 'up|down|left|right', amount: 'integer?', coordinate_space: 'window|screen?', allow_foreground_input: 'boolean' }, background: 'requires_focus' },
  { name: 'key_chord', description: 'Send a focus-dependent key or shortcut; allow_foreground_input=true is required.', arguments: { keys: 'string[]', allow_foreground_input: 'boolean' }, background: 'requires_focus' },
  { name: 'key_sequence', description: 'Execute focus-dependent keyboard steps; allow_foreground_input=true is required.', arguments: { steps: '{type:shortcut|text|key|pause,keys?:string[],text?:string,key?:string,ms?:integer}[]', allow_foreground_input: 'boolean' }, background: 'requires_focus' },
  { name: 'wait', description: 'Wait for an element or property condition, then return a fresh semantic snapshot.', arguments: { ref: 'string?', property: 'string?', equals: 'any?', contains: 'string?', exists: 'boolean?', timeout_ms: 'integer?' }, background: 'safe' },
  { name: 'visual_describe', description: 'Capture the connected window for agent inspection. The Python gateway returns a persisted screenshot artifact plus a text description through Cyrene\'s configured vision model.', arguments: { prompt: 'string?' }, background: 'safe' },
  { name: 'focus_window', description: 'Bring the connected target window to the foreground.', arguments: {}, background: 'changes_focus' },
  { name: 'restore_previous_focus', description: 'Restore the window that was foreground before this App Use session focused its target.', arguments: {}, background: 'changes_focus' },
]);

const DARWIN_PID_TYPE_CAPABILITY = Object.freeze({
  name: 'virtual_type_at',
  description: 'Best-effort delivery of a coordinate click and Unicode text directly to a target macOS process with CGEventPostToPid. It does not move the real cursor, use the foreground keyboard, or focus the application. Event delivery never proves text insertion; use visual_type for exact verification.',
  arguments: { x: 'number', y: 'number', text: 'string', coordinate_space: 'window|screen?', pointer_duration_ms: 'integer?', verify_effect: 'boolean?' },
  background: 'best_effort_without_foreground',
});

function capabilitiesForTarget(target, { mode = 'visual' } = {}) {
  const platform = String(target.platform || process.platform);
  if (mode === 'semantic') {
    return CAPABILITIES.filter((item) => SEMANTIC_MODE_CAPABILITIES.has(item.name));
  }
  if (mode !== 'visual' || platform === 'linux') return [];
  const visual = CAPABILITIES.filter((item) => VISUAL_MODE_CAPABILITIES.has(item.name));
  return platform === 'darwin' ? [DARWIN_PID_TYPE_CAPABILITY, ...visual] : visual;
}

class AppUseError extends Error {
  constructor(code, message, extra = {}) {
    super(message);
    this.name = 'AppUseError';
    this.code = code;
    this.extra = extra;
  }
}

function delay(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function captureFingerprint(capture) {
  const encoded = String((capture && capture.imageBase64) || '');
  const suppliedPixelHash = String((capture && capture.pixelHash) || '');
  if (!encoded && !suppliedPixelHash) return null;
  return {
    sha256: suppliedPixelHash || crypto.createHash('sha256').update(Buffer.from(encoded, 'base64')).digest('hex'),
    width: Number(capture.width || 0),
    height: Number(capture.height || 0),
  };
}

function clampInteger(value, fallback, min, max) {
  const parsed = Number.parseInt(value, 10);
  if (!Number.isFinite(parsed)) return fallback;
  return Math.max(min, Math.min(max, parsed));
}

const GENERIC_SEMANTIC_LABELS = new Set([
  'application', 'app', 'window', 'group', 'pane', 'panel', 'container', 'unknown',
  'button', 'text', 'image', 'list', 'row', 'cell', 'menu', 'toolbar', 'web area',
  '应用', '窗口', '组', '窗格', '面板', '容器', '未知', '按钮', '文本', '图像', '列表', '行', '单元格', '菜单', '工具栏',
]);

function normalizedSemanticText(value) {
  return String(value || '').trim().toLocaleLowerCase().replace(/[\s_\-:]+/g, ' ');
}

function hasMeaningfulSemanticLabel(node) {
  const role = normalizedSemanticText(node.role).replace(/^ax/, '');
  const candidates = [node.name, node.description, node.help]
    .map(normalizedSemanticText)
    .filter(Boolean);
  return candidates.some((label) => (
    label !== role
    && label !== `ax${role}`
    && !GENERIC_SEMANTIC_LABELS.has(label)
  ));
}

function validateCapabilityParameters(capability, parameters) {
  if (!parameters || typeof parameters !== 'object' || Array.isArray(parameters)) {
    throw new AppUseError('invalid_arguments', `${capability} parameters must be an object.`);
  }
  const descriptor = CAPABILITIES.concat([DARWIN_PID_TYPE_CAPABILITY]).find((item) => item.name === capability);
  if (!descriptor) return;
  const accepted = new Set(Object.keys(descriptor.arguments || {}));
  if (descriptor.background === 'requires_focus') accepted.add('restore_focus');
  const unknown = Object.keys(parameters).filter((key) => !accepted.has(key)).sort();
  if (unknown.length) {
    throw new AppUseError(
      'invalid_arguments',
      `${capability} does not accept: ${unknown.join(', ')}.`,
      { accepted_arguments: [...accepted].sort() },
    );
  }
  if (capability === 'scroll_at' && Object.prototype.hasOwnProperty.call(parameters, 'amount')) {
    const amount = parameters.amount;
    if (!Number.isInteger(amount) || amount < 1 || amount > MAX_SCROLL_AT_AMOUNT) {
      throw new AppUseError(
        'invalid_arguments',
        `scroll_at amount must be an integer from 1 to ${MAX_SCROLL_AT_AMOUNT}.`,
        { accepted_range: { amount: { min: 1, max: MAX_SCROLL_AT_AMOUNT } } },
      );
    }
  }
}

function targetIdentity(target) {
  return [
    String(target.platform || ''),
    String(target.pid || ''),
    String(target.processStartTime || ''),
    String(target.windowId || ''),
  ].join(':');
}

function publicTarget(target, targetId) {
  return {
    target_id: targetId,
    app_name: String(target.appName || ''),
    application_id: String(target.applicationId || ''),
    pid: Number(target.pid || 0),
    window_title: String(target.windowTitle || ''),
    foreground: target.foreground === true,
    minimized: target.minimized === true,
    bounds: target.bounds || null,
    coordinate_space: 'global_multi_monitor',
    platform: String(target.platform || process.platform),
  };
}

function normalizeResultError(error) {
  if (error instanceof AppUseError) {
    return { status: 'error', type: error.code, message: error.message, ...error.extra };
  }
  return {
    status: 'error',
    type: 'internal_error',
    message: String((error && error.message) || error || 'Unknown App Use error.'),
  };
}

function runCommand(command, args, { timeout = 15000, maxBuffer = 12 * 1024 * 1024 } = {}) {
  return new Promise((resolve, reject) => {
    execFile(command, args, { timeout, maxBuffer, windowsHide: true }, (error, stdout, stderr) => {
      if (error) {
        if (error.killed || error.code === 'ETIMEDOUT') {
          reject(new AppUseError('timeout', `Desktop accessibility provider timed out after ${timeout} ms.`));
          return;
        }
        const detail = String(stderr || stdout || error.message || error).trim();
        reject(new AppUseError('provider_error', detail || 'Desktop accessibility provider failed.'));
        return;
      }
      const output = String(stdout || '').trim();
      if (!output) {
        reject(new AppUseError('provider_error', 'Desktop accessibility provider returned no data.'));
        return;
      }
      try {
        const lines = output.split(/\r?\n/).filter(Boolean);
        resolve(JSON.parse(lines[lines.length - 1]));
      } catch (parseError) {
        reject(new AppUseError('provider_error', `Invalid provider response: ${output.slice(0, 500)}`));
      }
    });
  });
}

class WindowsPowerShellWorker {
  constructor(scriptPath, { spawnImpl = spawn } = {}) {
    this.scriptPath = scriptPath;
    this.spawnImpl = spawnImpl;
    this.child = null;
    this.pending = [];
    this.stdoutBuffer = '';
    this.stderrBuffer = '';
    this.decoder = new StringDecoder('utf8');
  }

  _rejectAll(error) {
    const pending = this.pending.splice(0);
    for (const request of pending) {
      clearTimeout(request.timer);
      request.reject(error);
    }
  }

  _stop(error, child = this.child) {
    if (child && this.child !== child) return;
    this.child = null;
    if (child) {
      try { child.kill(); } catch (_) {}
    }
    this._rejectAll(error || new AppUseError('provider_error', 'Windows input worker stopped.'));
  }

  _acceptLine(line) {
    const request = this.pending.shift();
    if (!request) return;
    clearTimeout(request.timer);
    try {
      request.resolve(JSON.parse(line));
    } catch (_) {
      request.reject(new AppUseError('provider_error', `Invalid Windows input response: ${line.slice(0, 500)}`));
    }
  }

  _start() {
    if (this.child) return this.child;
    const child = this.spawnImpl('powershell.exe', [
      '-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass',
      '-File', this.scriptPath, '-Worker',
    ], {
      stdio: ['pipe', 'pipe', 'pipe'],
      windowsHide: true,
    });
    this.child = child;
    this.stdoutBuffer = '';
    this.stderrBuffer = '';
    this.decoder = new StringDecoder('utf8');
    child.stdout.on('data', (chunk) => {
      this.stdoutBuffer += this.decoder.write(chunk);
      const lines = this.stdoutBuffer.split(/\r?\n/);
      this.stdoutBuffer = lines.pop() || '';
      for (const line of lines) {
        const value = line.trim();
        if (value) this._acceptLine(value);
      }
    });
    child.stderr.on('data', (chunk) => {
      this.stderrBuffer = (this.stderrBuffer + String(chunk || '')).slice(-4000);
    });
    child.once('error', (error) => {
      this._stop(new AppUseError('provider_error', String(error && error.message || error)), child);
    });
    child.once('exit', (code) => {
      const detail = this.stderrBuffer.trim();
      this._stop(new AppUseError(
        'provider_error',
        detail || `Windows input worker exited with code ${Number(code || 0)}.`,
      ), child);
    });
    return child;
  }

  request(payload, timeout = 15000) {
    return new Promise((resolve, reject) => {
      let child;
      try { child = this._start(); } catch (error) { reject(error); return; }
      const request = { resolve, reject, timer: null };
      request.timer = setTimeout(() => {
        this._stop(new AppUseError('timeout', `Windows input worker timed out after ${timeout} ms.`), child);
      }, timeout);
      this.pending.push(request);
      const encoded = Buffer.from(JSON.stringify(payload), 'utf8').toString('base64');
      child.stdin.write(`${encoded}\n`, (error) => {
        if (error) this._stop(new AppUseError('provider_error', String(error.message || error)), child);
      });
    });
  }

  close() {
    this._stop(new AppUseError('provider_stopped', 'Windows input worker stopped.'));
  }
}

const PROVIDER_SCRIPT_NAMES = Object.freeze({
  darwin: 'app-use-macos.jxa',
  win32: 'app-use-windows.ps1',
});
const DARWIN_HIT_TEST_HELPER_NAME = 'app-use-macos-hit-test';

function isAsarPath(candidate) {
  return String(candidate || '').split(/[\\/]+/).some((part) => part.toLowerCase().endsWith('.asar'));
}

function resolveProviderScriptPath({
  platform = process.platform,
  baseDir = __dirname,
  resourcesPath = process.resourcesPath || '',
  existsSync = fs.existsSync,
} = {}) {
  const scriptName = PROVIDER_SCRIPT_NAMES[platform];
  if (!scriptName) {
    throw new AppUseError('unsupported_platform', `App Use is not implemented for ${platform}.`);
  }

  // External executables such as osascript and PowerShell cannot read a file
  // through Electron's app.asar virtual filesystem. Packaged providers must
  // therefore live in extraResources; source/dev runs keep using baseDir.
  const candidates = [
    resourcesPath ? path.join(resourcesPath, 'app-use', scriptName) : '',
    baseDir ? path.join(baseDir, scriptName) : '',
  ].filter((candidate, index, all) => candidate && all.indexOf(candidate) === index);
  const scriptPath = candidates.find((candidate) => !isAsarPath(candidate) && existsSync(candidate));
  if (scriptPath) return scriptPath;

  throw new AppUseError(
    'provider_unavailable',
    `The ${platform} App Use provider is missing from this Cyrene installation. Update or reinstall Cyrene before retrying App Use.`,
    {
      retryable: false,
      remediation: 'Rebuild or reinstall Cyrene with the App Use provider resources. Do not substitute shell automation for the requested App Use action.',
      expected_paths: candidates,
    },
  );
}

function resolveDarwinHitTestHelperPath({
  platform = process.platform,
  baseDir = __dirname,
  resourcesPath = process.resourcesPath || '',
  existsSync = fs.existsSync,
} = {}) {
  if (platform !== 'darwin') return '';
  const candidates = [
    resourcesPath ? path.join(resourcesPath, 'app-use', DARWIN_HIT_TEST_HELPER_NAME) : '',
    baseDir ? path.join(baseDir, DARWIN_HIT_TEST_HELPER_NAME) : '',
  ].filter((candidate, index, all) => candidate && all.indexOf(candidate) === index);
  const helperPath = candidates.find((candidate) => !isAsarPath(candidate) && existsSync(candidate));
  if (helperPath) return helperPath;
  throw new AppUseError(
    'provider_unavailable',
    'The native macOS AX App Use helper is missing from this Cyrene installation.',
    {
      retryable: false,
      remediation: 'Rebuild or reinstall Cyrene with the native AX App Use helper. Do not substitute shell automation or a real OS pointer event.',
      expected_paths: candidates,
    },
  );
}

class CommandPlatformProvider {
  constructor({
    platform = process.platform,
    baseDir = __dirname,
    resourcesPath = process.resourcesPath || '',
    existsSync = fs.existsSync,
    spawnImpl = spawn,
  } = {}) {
    this.platform = platform;
    this.baseDir = baseDir;
    this.resourcesPath = resourcesPath;
    this.existsSync = existsSync;
    this.spawnImpl = spawnImpl;
    this.windowsInputWorker = null;
    this.macWindowWorker = null;
    if (platform === 'linux') {
      // Load the optional D-Bus dependency only on Linux. Unit tests and
      // non-Linux packages must not require Electron production dependencies.
      const { LinuxAtspiProvider } = require('./app-use-linux');
      this.linuxProvider = new LinuxAtspiProvider();
    } else {
      this.linuxProvider = null;
    }
  }

  async request(operation, payload = {}, timeout = 15000) {
    const request = { operation, ...payload };
    let result;
    try {
      if (this.platform === 'darwin') {
        const nativeSemanticOperation = ['snapshot', 'inspect'].includes(operation)
          || (operation === 'perform' && SEMANTIC_MODE_CAPABILITIES.has(String(payload.capability || '')));
        if (nativeSemanticOperation) {
          const helperPath = resolveDarwinHitTestHelperPath(this);
          result = await runCommand(helperPath, [JSON.stringify(request)], { timeout });
        } else {
          const scriptPath = resolveProviderScriptPath(this);
          result = await runCommand('osascript', [
            '-l', 'JavaScript', scriptPath, JSON.stringify(request),
          ], { timeout });
        }
      } else if (this.platform === 'win32') {
        const scriptPath = resolveProviderScriptPath(this);
        const lowLatency = ['list_targets', 'focus'].includes(operation)
          || (operation === 'perform'
            && WINDOWS_LOW_LATENCY_CAPABILITIES.has(String(payload.capability || '')));
        if (lowLatency) {
          if (!this.windowsInputWorker || this.windowsInputWorker.scriptPath !== scriptPath) {
            this.windowsInputWorker = new WindowsPowerShellWorker(scriptPath, { spawnImpl: this.spawnImpl });
          }
          result = await this.windowsInputWorker.request(request, timeout);
        } else {
          const encoded = Buffer.from(JSON.stringify(request), 'utf8').toString('base64');
          result = await runCommand('powershell.exe', [
            '-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass',
            '-File', scriptPath,
            '-PayloadBase64', encoded,
          ], { timeout });
        }
      } else {
        throw new AppUseError('unsupported_platform', `App Use is not implemented for ${this.platform}.`);
      }
    } catch (error) {
      if (error instanceof AppUseError && error.code === 'timeout' && ['snapshot', 'inspect'].includes(operation)) {
        throw new AppUseError(
          'accessibility_tree_timeout',
          `The target application's accessibility tree exceeded the ${timeout} ms snapshot budget. This does not imply that the window must be foreground or that all accessibility actions are unavailable.`,
          {
            retryable: true,
            remediation: 'Retry once with a smaller max_nodes/max_depth scope, then switch explicitly to the visual scheme if the semantic provider remains unavailable.',
          },
        );
      }
      throw error;
    }
    if (!result || result.ok === false) {
      throw new AppUseError(
        String((result && result.errorType) || 'provider_error'),
        String((result && result.error) || 'Desktop accessibility provider failed.'),
      );
    }
    return result;
  }

  stop() {
    if (this.windowsInputWorker) this.windowsInputWorker.close();
    this.windowsInputWorker = null;
    if (this.macWindowWorker) this.macWindowWorker.close();
    this.macWindowWorker = null;
  }

  _windowWorker() {
    if (!this.macWindowWorker) {
      const { MacWindowWorker } = require('./app-use-macos-worker');
      this.macWindowWorker = new MacWindowWorker(resolveDarwinHitTestHelperPath(this), { spawnImpl: this.spawnImpl });
    }
    return this.macWindowWorker;
  }

  watchTargets(listener) {
    if (this.platform !== 'darwin') return null;
    return this._windowWorker().watch(listener);
  }

  async listTargets(exclusions = {}) {
    if (this.linuxProvider) {
      try { return await this.linuxProvider.listTargets(exclusions); }
      catch (error) { throw new AppUseError('provider_error', String(error.message || error), { retryable: true }); }
    }
    const normalized = typeof exclusions === 'number' ? { excludePid: exclusions } : exclusions;
    const result = this.platform === 'darwin'
      ? await this._windowWorker().request({ operation: 'list_targets', ...normalized })
      : await this.request('list_targets', normalized, 20000);
    return Array.isArray(result.targets) ? result.targets : [];
  }

  async snapshot(target, options = {}) {
    if (this.linuxProvider) {
      try { return await this.linuxProvider.snapshot(target, options); }
      catch (error) { throw new AppUseError('provider_error', String(error.message || error), { retryable: true }); }
    }
    const timeout = clampInteger(options.timeoutMs, 15000, 250, 15000);
    const providerOptions = { ...options };
    delete providerOptions.timeoutMs;
    return this.request('snapshot', { target, options: providerOptions }, timeout);
  }

  async enableAccessibility(target) {
    if (this.linuxProvider) {
      try { return await this.linuxProvider.enableAccessibility(target); }
      catch (error) { throw new AppUseError('provider_error', String(error.message || error), { retryable: true }); }
    }
    if (this.platform !== 'darwin') return { ok: true, enabled: false, supported: false };
    const helperPath = resolveDarwinHitTestHelperPath(this);
    return runCommand(helperPath, [JSON.stringify({ operation: 'enable_accessibility', target })], { timeout: 5000 });
  }

  async pidEvent(target, operation, point, parameters = {}, perform = false) {
    if (this.platform !== 'darwin') throw new AppUseError('unsupported_platform', 'Targeted PID events are currently available only on macOS.');
    const helperPath = resolveDarwinHitTestHelperPath(this);
    return runCommand(helperPath, [JSON.stringify({
      operation, target, point, text: String(parameters.text || ''), perform: perform === true,
    })], { timeout: 5000 });
  }

  async inspect(target, nativeRef, options = {}) {
    if (this.linuxProvider) {
      try { return await this.linuxProvider.inspect(target, nativeRef, options); }
      catch (error) { throw new AppUseError('provider_error', String(error.message || error), { retryable: true }); }
    }
    return this.request('inspect', { target, nativeRef, options });
  }

  async perform(target, capability, nativeRef, parameters = {}) {
    if (this.linuxProvider) {
      try { return await this.linuxProvider.perform(target, capability, nativeRef, parameters); }
      catch (error) { throw new AppUseError('provider_error', String(error.message || error), { retryable: false }); }
    }
    return this.request('perform', { target, capability, nativeRef, parameters });
  }

  async focusTarget(target) {
    if (this.linuxProvider) throw new AppUseError('unsupported_capability', 'Linux App Use is semantic-only and never changes focus.');
    return this.request('focus', { target }, 20000);
  }
}

class AppUseManager {
  constructor({
    provider = new CommandPlatformProvider(),
    ownPid = process.pid,
    ownApplicationIds = ['com.cyrene.app'],
    ownAppNames = ['Cyrene'],
    captureTarget = null,
    showVirtualPointer = null,
    hideVirtualPointer = null,
    isHostForeground = null,
    focusHost = null,
    sessionTtlMs = DEFAULT_SESSION_TTL_MS,
    pollIntervalMs = 1500,
    targetRetryDelaysMs = [75, 150],
  } = {}) {
    this.provider = provider;
    this.ownPid = ownPid;
    this.ownApplicationIds = new Set(ownApplicationIds.map((value) => String(value).toLowerCase()));
    this.ownAppNames = new Set(ownAppNames.map((value) => String(value).toLowerCase()));
    this.captureTarget = captureTarget;
    this.showVirtualPointer = showVirtualPointer;
    this.hideVirtualPointer = hideVirtualPointer;
    this.isHostForeground = isHostForeground;
    this.focusHost = focusHost;
    this.sessionTtlMs = sessionTtlMs;
    this.pollIntervalMs = pollIntervalMs;
    this.targetRetryDelaysMs = Array.isArray(targetRetryDelaysMs)
      ? targetRetryDelaysMs.map((value) => Math.max(0, Number(value) || 0))
      : [75, 150];
    this.targets = new Map();
    this.sessions = new Map();
    this.lastExternalTargetId = '';
    this.quickChatOriginTargetId = '';
    this.trackerTimer = null;
    this.stopTracking = null;
    this.tracking = false;
    this.refreshPromise = null;
  }

  start() {
    if (this.tracking) return;
    this.tracking = true;
    const tick = () => {
      this.targetRefreshRequested = true;
      if (this.targetRefreshRunning) return;
      this.targetRefreshRunning = true;
      (async () => {
        try {
          do {
            this.targetRefreshRequested = false;
            await this.refreshTargets().catch(() => {});
          } while (this.targetRefreshRequested && this.tracking);
        } finally { this.targetRefreshRunning = false; }
      })();
    };
    this.stopTracking = this.provider.watchTargets?.(tick) || null;
    tick();
    if (!this.stopTracking) {
      this.trackerTimer = setInterval(tick, this.pollIntervalMs);
      this.trackerTimer.unref?.();
    }
  }

  _scheduleActiveTracking() {
    if (!this.tracking || !this.stopTracking || this.trackerTimer || !this.sessions.size) return;
    // Window movement/minimization within the same app does not necessarily
    // emit an NSWorkspace notification. Preserve checks during control sessions.
    this.trackerTimer = setTimeout(() => {
      this.trackerTimer = null;
      this._expireSessions();
      this._scheduleActiveTracking();
      if (this.sessions.size) this.refreshTargets().catch(() => {});
    }, this.pollIntervalMs);
    this.trackerTimer.unref?.();
  }

  stop() {
    this.tracking = false;
    if (this.stopTracking) this.stopTracking();
    this.stopTracking = null;
    if (this.trackerTimer) clearInterval(this.trackerTimer);
    this.trackerTimer = null;
    if (typeof this.hideVirtualPointer === 'function') {
      Promise.resolve(this.hideVirtualPointer({})).catch(() => {});
    }
    this.sessions.clear();
    this.targets.clear();
    if (this.provider && typeof this.provider.stop === 'function') this.provider.stop();
  }

  async refreshTargets() {
    if (this.refreshPromise) return this.refreshPromise;
    this.refreshPromise = (async () => {
      const rawTargets = await this.provider.listTargets({
        excludePid: this.ownPid,
        excludeApplicationIds: [...this.ownApplicationIds],
        excludeAppNames: [...this.ownAppNames],
      });
      const next = new Map();
      for (const raw of rawTargets) {
        if (!raw || Number(raw.pid || 0) === Number(this.ownPid)) continue;
        if (this.ownApplicationIds.has(String(raw.applicationId || '').toLowerCase())) continue;
        if (this.ownAppNames.has(String(raw.appName || '').toLowerCase())) continue;
        const identity = targetIdentity(raw);
        if (!identity.replace(/:/g, '')) continue;
        const targetId = `target_${crypto.createHash('sha256').update(identity).digest('hex').slice(0, 16)}`;
        const target = { ...raw, targetId, identity };
        next.set(targetId, target);
        if (target.foreground) this.lastExternalTargetId = targetId;
      }
      this.targets = next;
      if (typeof this.hideVirtualPointer === 'function') {
        for (const activeSession of this.sessions.values()) {
          const current = next.get(activeSession.target.targetId);
          if (!current || current.minimized === true) {
            Promise.resolve(this.hideVirtualPointer({
              target: publicTarget(activeSession.target, activeSession.target.targetId),
            })).catch(() => {});
          }
        }
      }
      this._expireSessions();
      return [...next.values()];
    })();
    try {
      return await this.refreshPromise;
    } finally {
      this.refreshPromise = null;
    }
  }

  async captureQuickChatOrigin() {
    await this.refreshTargets();
    const foreground = [...this.targets.values()].find((target) => target.foreground);
    this.quickChatOriginTargetId = foreground ? foreground.targetId : this.lastExternalTargetId;
    return this.quickChatOriginTargetId;
  }

  _expireSessions() {
    const now = Date.now();
    for (const [sessionId, session] of this.sessions.entries()) {
      if (now - session.lastUsedAt > this.sessionTtlMs) this.sessions.delete(sessionId);
    }
  }

  async listTargets() {
    const targets = await this.refreshTargets();
    return {
      status: 'success',
      selection_hints: {
        foreground: targets.find((target) => target.foreground)?.targetId || '',
        recent_external: this.lastExternalTargetId,
        quick_chat_origin: this.quickChatOriginTargetId,
      },
      targets: targets.map((target) => publicTarget(target, target.targetId)),
    };
  }

  _selectTarget(targetId, parameters = {}) {
    let selectedId = String(targetId || '').trim();
    const selection = String(parameters.selection || '').trim();
    if (!selectedId && selection === 'foreground') {
      selectedId = [...this.targets.values()].find((target) => target.foreground)?.targetId || '';
    } else if (!selectedId && selection === 'quick_chat_origin') {
      selectedId = this.quickChatOriginTargetId;
    } else if (!selectedId) {
      selectedId = this.lastExternalTargetId;
    }
    const target = this.targets.get(selectedId);
    if (!target) {
      throw new AppUseError('target_not_found', 'The requested application window is no longer available.', {
        next_valid_actions: ['list_targets'],
      });
    }
    return target;
  }

  async _selectTargetWithRetry(targetId, parameters = {}) {
    let lastError = null;
    for (let attempt = 0; attempt <= this.targetRetryDelaysMs.length; attempt += 1) {
      await this.refreshTargets();
      try {
        return this._selectTarget(targetId, parameters);
      } catch (error) {
        if (!(error instanceof AppUseError) || error.code !== 'target_not_found') throw error;
        lastError = error;
      }
      if (attempt < this.targetRetryDelaysMs.length) {
        await new Promise((resolve) => setTimeout(resolve, this.targetRetryDelaysMs[attempt]));
      }
    }
    throw lastError;
  }

  async connect(targetId, parameters = {}) {
    const target = await this._selectTargetWithRetry(targetId, parameters);
    const requestedMode = String(parameters.mode || 'visual').toLowerCase();
    if (!['visual', 'semantic'].includes(requestedMode)) {
      throw new AppUseError('invalid_arguments', 'mode must be visual or semantic.');
    }
    const platform = String(target.platform || process.platform);
    if (platform === 'linux' && requestedMode === 'visual') {
      throw new AppUseError('unsupported_mode', 'Linux App Use supports semantic mode only.');
    }
    const mode = platform === 'linux' ? 'semantic' : requestedMode;
    const requestedFocusPolicy = ['never', 'when_required', 'always'].includes(parameters.focus_policy)
      ? parameters.focus_policy
      : 'when_required';
    const focusPolicy = mode === 'semantic' ? 'never' : requestedFocusPolicy;
    let accessibilityActivation = null;
    if (mode !== 'visual' && typeof this.provider.enableAccessibility === 'function') {
      accessibilityActivation = await this.provider.enableAccessibility(target).catch((error) => ({
        ok: false, errorType: String((error && error.code) || 'provider_error'),
        error: String(error && error.message ? error.message : error),
      }));
      if (accessibilityActivation && accessibilityActivation.foregroundAffected === true) {
        throw new AppUseError('foreground_interference_detected', 'Enabling the target accessibility tree changed the foreground application.');
      }
    }
    const runtimeCapabilities = capabilitiesForTarget(target, { mode }).filter(
      (capability) => focusPolicy !== 'never' || !['requires_focus', 'changes_focus'].includes(capability.background),
    );
    const sessionId = `app_session_${crypto.randomUUID()}`;
    const session = {
      sessionId,
      target: { ...target },
      targetIdentity: target.identity,
      mode,
      focusPolicy,
      createdAt: Date.now(),
      lastUsedAt: Date.now(),
      revision: 0,
      refs: new Map(),
      pathToRef: new Map(),
      previousFocusTarget: null,
      previousFocusWasHost: false,
      capabilities: runtimeCapabilities,
      semanticProbeAttempts: 0,
      semanticProbeStartedAt: Date.now(),
    };
    this.sessions.set(sessionId, session);
    this._scheduleActiveTracking();
    let semanticProfile = null;
    if (mode === 'semantic') {
      semanticProfile = {
        status: 'initializing', reason: 'semantic_probe_not_completed', probe_timeout_ms: 5000,
      };
      try {
        if (accessibilityActivation && accessibilityActivation.ok === false && accessibilityActivation.errorType === 'permission_required') {
          throw new AppUseError('permission_required', accessibilityActivation.error || 'Accessibility permission is required.');
        }
        const probe = await this._snapshot(session, {
          max_nodes: 80,
          max_depth: 10,
          _probe_timeout_ms: 5000,
        });
        semanticProfile = probe.semantic_profile;
      } catch (error) {
        const reason = error && error.code ? String(error.code) : 'semantic_probe_failed';
        semanticProfile = reason === 'accessibility_tree_timeout' || reason === 'timeout'
          ? { status: 'initializing', reason: 'semantic_probe_timeout', probe_timeout_ms: 5000, retryable: true }
          : reason === 'permission_required'
              ? { status: 'permission_required', reason, probe_timeout_ms: 5000, retryable: true }
              : { status: 'provider_error', reason, probe_timeout_ms: 5000, retryable: true };
      }
    }
    session.semanticProfile = semanticProfile;
    if (focusPolicy === 'always') await this._focusSessionTarget(session);
    return {
      status: 'success',
      session_id: sessionId,
      target: publicTarget(target, target.targetId),
      mode,
      focus_policy: focusPolicy,
      manifest_version: MANIFEST_VERSION,
      capabilities: session.capabilities,
      ...(accessibilityActivation ? { accessibility_activation: accessibilityActivation } : {}),
      ...(semanticProfile ? { semantic_profile: semanticProfile } : {}),
      next_valid_actions: mode === 'visual'
        ? ['call:visual_describe', 'status', 'disconnect']
        : ['call:snapshot', 'call:find', 'status', 'disconnect'],
    };
  }

  async _getSession(sessionId) {
    this._expireSessions();
    const session = this.sessions.get(String(sessionId || ''));
    if (!session) throw new AppUseError('stale_session', 'The App Use session has expired or does not exist.');
    let current = null;
    for (let attempt = 0; attempt <= this.targetRetryDelaysMs.length; attempt += 1) {
      await this.refreshTargets();
      const candidate = this.targets.get(session.target.targetId);
      if (candidate && candidate.identity === session.targetIdentity) {
        current = candidate;
        break;
      }
      if (attempt < this.targetRetryDelaysMs.length) {
        await new Promise((resolve) => setTimeout(resolve, this.targetRetryDelaysMs[attempt]));
      }
    }
    if (!current || current.identity !== session.targetIdentity) {
      if (typeof this.hideVirtualPointer === 'function') {
        await this.hideVirtualPointer({
          target: publicTarget(session.target, session.target.targetId),
        }).catch(() => {});
      }
      this.sessions.delete(session.sessionId);
      throw new AppUseError('stale_session', 'The connected application window changed or closed. Reconnect before acting.');
    }
    if (current.minimized === true && typeof this.hideVirtualPointer === 'function') {
      await this.hideVirtualPointer({
        target: publicTarget(current, current.targetId),
      }).catch(() => {});
    }
    session.target = { ...current };
    session.lastUsedAt = Date.now();
    return session;
  }

  _nativeRef(session, ref, required = true) {
    const key = String(ref || '').trim();
    if (!key && !required) return '';
    const nativeRef = session.refs.get(key);
    if (!nativeRef) throw new AppUseError('stale_element', `Element ref ${key || '(empty)'} is unavailable. Never invent refs; take a successful snapshot or use a ref returned by find.`, {
      next_valid_actions: ['call:snapshot', 'call:find'],
    });
    return nativeRef;
  }

  _mapNodes(session, providerResult, { prune = true } = {}) {
    const rawNodes = Array.isArray(providerResult.nodes) ? providerResult.nodes : [];
    const nodes = [];
    const livePaths = new Set();
    for (const raw of rawNodes) {
      const nativeRef = String(raw.nativeRef || raw.path || '');
      if (!nativeRef) continue;
      livePaths.add(nativeRef);
      let ref = session.pathToRef.get(nativeRef);
      if (!ref) {
        ref = `e${session.pathToRef.size + 1}`;
        session.pathToRef.set(nativeRef, ref);
      }
      session.refs.set(ref, nativeRef);
      const node = { ref };
      for (const [key, value] of Object.entries(raw)) {
        if (key === 'nativeRef' || key === 'path') continue;
        if (value === undefined || value === null || value === '') continue;
        node[key] = value;
      }
      nodes.push(node);
    }
    for (const node of nodes) {
      const raw = rawNodes.find((item) => String(item.nativeRef || item.path || '') === session.refs.get(node.ref));
      const parentNativeRef = String((raw && raw.parentNativeRef) || '');
      if (parentNativeRef && session.pathToRef.has(parentNativeRef)) node.parent_ref = session.pathToRef.get(parentNativeRef);
    }
    if (prune) {
      for (const [ref, nativeRef] of session.refs.entries()) {
        if (!livePaths.has(nativeRef)) session.refs.delete(ref);
      }
    }
    session.revision += 1;
    const actionable = nodes.filter((node) => Array.isArray(node.actions) && node.actions.length > 0);
    const labeled = actionable.filter(hasMeaningfulSemanticLabel);
    const genericActionable = actionable.filter((node) => !hasMeaningfulSemanticLabel(node));
    const meaningfulTaskActionable = labeled.filter((node) => !/closebutton|minimizebutton|fullscreenbutton|zoombutton/i.test(String(node.subrole || '')));
    const ratio = actionable.length ? labeled.length / actionable.length : 1;
    const lowUsableCoverage = ratio < 0.8 && meaningfulTaskActionable.length < 3;
    const canvasCount = nodes.filter((node) => /canvas|image|unknown/i.test(String(node.role || '')) && !(node.name || node.description)).length;
    const windowBounds = session.target.bounds || {};
    const windowArea = Number(windowBounds.width) * Number(windowBounds.height);
    const containerOnly = nodes.length === 0 || nodes.every((node) => {
      const role = String(node.role || '').toLowerCase();
      if (!/^(application|window|group|pane|hostingview|unknown)$/.test(role)) return false;
      if ((node.actions || []).length > 0) return false;
      if ((node.nativeActions || []).some((action) => String(action).toLowerCase() !== 'axraise')) return false;
      if (String(node.value || '').trim()) return false;
      const bounds = node.bounds || {};
      const nodeArea = Number(bounds.width) * Number(bounds.height);
      return !(windowArea > 0 && Number.isFinite(nodeArea)) || nodeArea / windowArea >= 0.9;
    });
    session.semanticProbeAttempts = Number(session.semanticProbeAttempts || 0) + 1;
    const semanticProfile = containerOnly
      ? {
        status: session.semanticProbeAttempts < 3 ? 'initializing' : 'unavailable',
        reason: session.semanticProbeAttempts < 3 ? 'tree_not_ready' : 'container_only_tree',
        retryable: true,
        probe_attempts: session.semanticProbeAttempts,
      }
      : {
        status: lowUsableCoverage || providerResult.truncated === true ? 'partial' : 'available',
        reason: providerResult.truncated === true
          ? 'snapshot_truncated'
          : lowUsableCoverage ? 'generic_or_unlabeled_actions' : 'meaningful_nodes_exposed',
        retryable: true,
        probe_attempts: session.semanticProbeAttempts,
      };
    const grade = containerOnly || (canvasCount > 0 && actionable.length === 0)
      ? 'insufficient' : lowUsableCoverage ? 'partial' : 'full';
    const visualRecommended = providerResult.truncated !== true
      && !String(session.target.platform || this.provider.platform || process.platform).startsWith('linux')
      && (grade === 'insufficient' || (meaningfulTaskActionable.length === 0 && genericActionable.length > 0));
    return {
      status: 'success',
      session_id: session.sessionId,
      snapshot_revision: session.revision,
      target: publicTarget(session.target, session.target.targetId),
      semantic_coverage: {
        grade,
        total_nodes: nodes.length,
        actionable_nodes: actionable.length,
        meaningful_actionable_nodes: labeled.length,
        meaningful_task_actionable_nodes: meaningfulTaskActionable.length,
        generic_or_unlabeled_actionable_nodes: genericActionable.length,
        labeled_actionable_ratio: Number(ratio.toFixed(2)),
        unlabeled_visual_regions: canvasCount,
        visual_recommended: visualRecommended,
      },
      semantic_profile: semanticProfile,
      nodes,
      truncated: providerResult.truncated === true,
    };
  }

  async _snapshot(session, parameters = {}) {
    const options = {
      maxNodes: clampInteger(parameters.max_nodes, 80, 1, 500),
      maxDepth: clampInteger(parameters.max_depth, 8, 1, 24),
    };
    if (parameters._probe_timeout_ms) {
      options.timeoutMs = clampInteger(parameters._probe_timeout_ms, 2000, 250, 5000);
    }
    if (parameters.scope_ref) options.nativeRef = this._nativeRef(session, parameters.scope_ref);
    const result = await this.provider.snapshot(session.target, options);
    const mapped = this._mapNodes(session, result, { prune: !options.nativeRef });
    session.semanticProfile = mapped.semantic_profile;
    return mapped;
  }

  async _inspect(session, parameters = {}) {
    const nativeRef = this._nativeRef(session, parameters.ref);
    const result = await this.provider.inspect(session.target, nativeRef, {
      maxNodes: clampInteger(parameters.max_nodes, 200, 1, 500),
      maxDepth: clampInteger(parameters.max_depth, 12, 1, 24),
    });
    return this._mapNodes(session, result, { prune: false });
  }

  async _find(session, parameters = {}) {
    const snapshot = await this._snapshot(session, {
      max_nodes: parameters.max_nodes || 80,
      max_depth: parameters.max_depth || 8,
    });
    const role = String(parameters.role || '').toLowerCase();
    const subrole = String(parameters.subrole || '').toLowerCase();
    const name = String(parameters.name || '').toLowerCase();
    const contains = String(parameters.contains || '').toLowerCase();
    const action = String(parameters.action || '').toLowerCase();
    const nativeAction = String(parameters.native_action || '').toLowerCase();
    const automationId = String(parameters.automation_id || '').toLowerCase();
    const className = String(parameters.class_name || '').toLowerCase();
    const maxResults = clampInteger(parameters.max_results, 20, 1, 100);
    const nodes = snapshot.nodes.filter((node) => {
      if (role && !String(node.role || '').toLowerCase().includes(role)) return false;
      if (subrole && !String(node.subrole || '').toLowerCase().includes(subrole)) return false;
      if (name && String(node.name || '').toLowerCase() !== name) return false;
      if (action && !(node.actions || []).some((value) => String(value).toLowerCase() === action)) return false;
      if (nativeAction && !(node.nativeActions || []).some((value) => String(value).toLowerCase() === nativeAction)) return false;
      if (automationId && String(node.automationId || '').toLowerCase() !== automationId) return false;
      if (className && String(node.className || '').toLowerCase() !== className) return false;
      if (parameters.enabled !== undefined && Boolean(node.enabled) !== Boolean(parameters.enabled)) return false;
      if (contains) {
        const haystack = [
          node.role, node.subrole, node.name, node.description, node.help, node.value,
          node.automationId, node.className, ...(node.actions || []), ...(node.nativeActions || []),
        ].map((value) => String(value || '')).join(' ').toLowerCase();
        if (!haystack.includes(contains)) return false;
      }
      return true;
    }).slice(0, maxResults);
    return { ...snapshot, nodes, matched: nodes.length, total_nodes: snapshot.nodes.length };
  }

  _coordinatePoint(session, parameters = {}, xKey = 'x', yKey = 'y') {
    const bounds = session.target.bounds || {};
    const left = Number(bounds.x);
    const top = Number(bounds.y);
    const width = Number(bounds.width);
    const height = Number(bounds.height);
    let x = Number(parameters[xKey]);
    let y = Number(parameters[yKey]);
    if (![left, top, width, height, x, y].every(Number.isFinite) || width <= 0 || height <= 0) {
      throw new AppUseError('invalid_arguments', 'The coordinate action requires finite coordinates and valid target window bounds.');
    }
    const coordinateSpace = String(parameters.coordinate_space || 'window').toLowerCase();
    if (coordinateSpace === 'window') {
      x += left;
      y += top;
    } else if (coordinateSpace !== 'screen') {
      throw new AppUseError('invalid_arguments', 'coordinate_space must be window or screen.');
    }
    if (x < left || y < top || x >= left + width || y >= top + height) {
      throw new AppUseError('invalid_arguments', 'The virtual pointer coordinate is outside the connected window.');
    }
    return { screen: { x, y }, window: { x: x - left, y: y - top } };
  }

  async _showCoordinatePointer(session, capability, parameters = {}) {
    if (typeof this.showVirtualPointer !== 'function') return;
    const target = publicTarget(session.target, session.target.targetId);
    const requestedDuration = Number.parseInt(parameters.duration_ms, 10);
    const durationMs = Number.isFinite(requestedDuration) && requestedDuration > 0
      ? Math.min(5000, requestedDuration)
      : 350;
    if (capability === 'drag') {
      const from = this._coordinatePoint(session, parameters, 'from_x', 'from_y');
      const to = this._coordinatePoint(session, parameters, 'to_x', 'to_y');
      const first = await this.showVirtualPointer({ x: from.screen.x, y: from.screen.y, target });
      await delay(first && first.first ? 150 : 180);
      await this.showVirtualPointer({
        x: to.screen.x, y: to.screen.y, moveDurationMs: durationMs, target,
      });
      return;
    }
    if (capability === 'swipe') {
      const from = this._coordinatePoint(session, parameters);
      const direction = String(parameters.direction || '').toLowerCase();
      const distance = Math.max(1, Math.min(2000, Number(parameters.distance || 240)));
      const delta = { up: [0, -distance], down: [0, distance], left: [-distance, 0], right: [distance, 0] }[direction];
      if (!delta) return;
      const to = this._coordinatePoint(session, {
        coordinate_space: 'screen',
        x: from.screen.x + delta[0],
        y: from.screen.y + delta[1],
      });
      const first = await this.showVirtualPointer({ x: from.screen.x, y: from.screen.y, target });
      await delay(first && first.first ? 150 : 180);
      await this.showVirtualPointer({
        x: to.screen.x, y: to.screen.y, moveDurationMs: durationMs, target,
      });
      return;
    }
    const point = this._coordinatePoint(session, parameters);
    if (['click_at', 'double_click', 'right_click'].includes(capability)) {
      await this._showPointerClickFeedback(
        point,
        publicTarget(session.target, session.target.targetId),
      );
      return;
    }
    await this.showVirtualPointer({
      x: point.screen.x,
      y: point.screen.y,
      moveDurationMs: capability === 'hover_at' ? durationMs : undefined,
      target,
    });
  }

  async _showPointerClickFeedback(point, target) {
    if (typeof this.showVirtualPointer !== 'function') return;
    const moved = await this.showVirtualPointer({
      x: point.screen.x,
      y: point.screen.y,
      press: false,
      target,
    });
    const fallbackWait = moved && moved.first
      ? AGENT_CURSOR_FADE_IN_MS + 34
      : AGENT_CURSOR_MOVE_MS;
    const waitMs = Math.max(0, Number(moved && moved.waitMs) || (moved && moved.moved !== false ? fallbackWait : 0));
    if (waitMs > 0) await delay(waitMs);
    await this.showVirtualPointer({
      x: point.screen.x,
      y: point.screen.y,
      press: true,
      moveDurationMs: 0,
      target,
    });
    await delay(AGENT_CURSOR_PRESS_MS);
  }

  async _pidEventAt(session, operation, pointOrParameters = {}, parameters = {}) {
    const point = pointOrParameters.screen ? pointOrParameters : this._coordinatePoint(session, pointOrParameters);
    const effectiveParameters = pointOrParameters.screen ? parameters : pointOrParameters;
    if (operation === 'pid_type_at' && typeof effectiveParameters.text !== 'string') {
      throw new AppUseError('invalid_arguments', 'virtual_type_at requires text as a string.');
    }
    const beforeVisual = effectiveParameters.verify_effect !== false && typeof this.captureTarget === 'function'
      ? captureFingerprint(await this.captureTarget(session.target).catch(() => null)) : null;
    if (typeof this.showVirtualPointer === 'function') {
      await this._showPointerClickFeedback(
        point,
        publicTarget(session.target, session.target.targetId),
      ).catch(() => {});
    }
    const result = await this.provider.pidEvent(session.target, operation, point.screen, effectiveParameters, true);
    if (!result || result.performed !== true) {
      throw new AppUseError('background_activation_failed', 'The target process rejected the directed background event.');
    }
    if (result.realCursorMoved === true || result.foregroundAffected === true) {
      throw new AppUseError('foreground_interference_detected', 'The directed event violated the background-only cursor/focus invariant.', {
        action_may_have_run: true, diagnostics: result.diagnostics || null,
      });
    }
    let afterVisual = null;
    if (beforeVisual && typeof this.captureTarget === 'function') {
      for (let attempt = 0; attempt < 3; attempt += 1) {
        await delay(120);
        afterVisual = captureFingerprint(await this.captureTarget(session.target).catch(() => null));
        if (afterVisual && beforeVisual.sha256 !== afterVisual.sha256) break;
      }
    }
    const capability = 'virtual_type_at';
    const visualChanged = beforeVisual && afterVisual ? beforeVisual.sha256 !== afterVisual.sha256 : null;
    // A full-window diff is not proof that a PID-directed event affected the
    // intended control: Electron apps often repaint timers, banners, and
    // cursors independently. High-level workflows must verify the requested
    // target state (for typing, the exact text) before reporting success.
    const effectVerified = null;
    return {
      status: 'uncertain',
      summary: 'Delivered a targeted background click and Unicode text event to the application process.',
      session_id: session.sessionId,
      virtual_pointer: { ...point, displayed: typeof this.showVirtualPointer === 'function' },
      input_mode: 'background_pid_event', real_cursor_moved: false, focus_requested: false,
      foreground_input_used: false, foreground_affected: false,
      requested_action: { capability, point, text: String(effectiveParameters.text || '') },
      executed_action: {
        capability, input_mode: 'background_pid_event', native_action: 'CGEventPostToPid', point,
        text_length: String(effectiveParameters.text || '').length,
      },
      verification: {
        status: 'uncertain', event_delivered: result.verified === true,
        real_cursor_moved: false, foreground_affected: false, effect_verified: effectVerified,
        visual_changed: visualChanged,
        method: 'CGEventPostToPid invariants',
      },
      diagnostics: result.diagnostics || null,
      next_valid_actions: [
        'call:measure_coordinates',
        'call:visual_describe',
        'disconnect',
      ],
    };
  }

  async _focusSessionTarget(session) {
    await this.refreshTargets();
    const previous = [...this.targets.values()].find((target) => target.foreground);
    if (previous && previous.identity !== session.targetIdentity) {
      session.previousFocusTarget = { ...previous };
      session.previousFocusWasHost = false;
    } else if (!previous && typeof this.isHostForeground === 'function' && this.isHostForeground()) {
      session.previousFocusTarget = null;
      session.previousFocusWasHost = true;
    }
    const result = await this.provider.focusTarget(session.target);
    if (result && result.verified === false) {
      throw new AppUseError(
        'focus_failed',
        `Could not focus ${session.target.appName || 'the target application'}.`,
        {
          diagnostics: result.diagnostics || null,
          retryable: true,
          remediation: 'Ensure Cyrene and the target run at the same Windows integrity level, restore the target window, and retry.',
        },
      );
    }
    return result || { ok: true };
  }

  async remoteDesktopInput(sessionId, capability, parameters = {}) {
    const requestedSessionId = String(sessionId || '');
    this._expireSessions();
    let session = this.sessions.get(requestedSessionId);
    if (!session) throw new AppUseError('stale_session', 'The Remote Desktop input session expired.');
    if (session.mode !== 'visual' || !['darwin', 'win32'].includes(String(session.target.platform || process.platform))) {
      throw new AppUseError('unsupported_capability', 'Native remote desktop input is unavailable for this target.');
    }
    const name = String(capability || '');
    if (!['pointer_event', 'right_click', 'scroll_at', 'key_sequence'].includes(name)) {
      throw new AppUseError('unsupported_capability', `Unsupported remote desktop input capability: ${name || '(empty)'}.`);
    }
    const effective = parameters && typeof parameters === 'object' && !Array.isArray(parameters)
      ? { ...parameters } : {};
    const focusTarget = effective.focus_target === true;
    const desktopBounds = effective.desktop_bounds;
    delete effective.focus_target;
    delete effective.desktop_bounds;
    if (focusTarget) {
      session = await this._getSession(requestedSessionId);
      await this._focusSessionTarget(session);
    } else {
      session.lastUsedAt = Date.now();
    }
    let target = session.target;
    if (desktopBounds && typeof desktopBounds === 'object') {
      const bounds = {
        x: Number(desktopBounds.x),
        y: Number(desktopBounds.y),
        width: Number(desktopBounds.width),
        height: Number(desktopBounds.height),
      };
      if (![bounds.x, bounds.y, bounds.width, bounds.height].every(Number.isFinite)
          || bounds.width <= 0 || bounds.height <= 0) {
        throw new AppUseError('invalid_arguments', 'Remote desktop input requires valid display bounds.');
      }
      target = { ...session.target, bounds };
    }
    if (name === 'pointer_event') {
      const action = String(effective.action || 'move');
      const button = String(effective.button || 'left');
      if (!['move', 'button_down', 'button_up'].includes(action) || !['left', 'right'].includes(button)) {
        throw new AppUseError('invalid_arguments', 'pointer_event requires a valid action and button.');
      }
      this._coordinatePoint({ target }, effective);
    }
    return this.provider.perform(target, name, '', effective);
  }

  async remoteDesktopGlobalInput(capability, parameters = {}) {
    const providerPlatform = String(this.provider && this.provider.platform || process.platform);
    if (providerPlatform !== 'win32') {
      throw new AppUseError('unsupported_capability', 'Global Remote Desktop input is available only on Windows.');
    }
    const name = String(capability || '');
    if (!['pointer_event', 'right_click', 'scroll_at', 'key_sequence'].includes(name)) {
      throw new AppUseError('unsupported_capability', `Unsupported global Remote Desktop input capability: ${name || '(empty)'}.`);
    }
    const effective = parameters && typeof parameters === 'object' && !Array.isArray(parameters)
      ? { ...parameters } : {};
    const desktopBounds = effective.desktop_bounds;
    delete effective.focus_target;
    delete effective.desktop_bounds;
    const bounds = {
      x: Number(desktopBounds && desktopBounds.x),
      y: Number(desktopBounds && desktopBounds.y),
      width: Number(desktopBounds && desktopBounds.width),
      height: Number(desktopBounds && desktopBounds.height),
    };
    if (![bounds.x, bounds.y, bounds.width, bounds.height].every(Number.isFinite)
        || bounds.width <= 0 || bounds.height <= 0) {
      throw new AppUseError('invalid_arguments', 'Global Remote Desktop input requires valid display bounds.');
    }
    const target = { platform: 'win32', bounds };
    if (name === 'pointer_event') {
      const action = String(effective.action || 'move');
      const button = String(effective.button || 'left');
      if (!['move', 'button_down', 'button_up'].includes(action) || !['left', 'right'].includes(button)) {
        throw new AppUseError('invalid_arguments', 'pointer_event requires a valid action and button.');
      }
    }
    if (name !== 'key_sequence') this._coordinatePoint({ target }, effective);
    return this.provider.perform(target, name, '', effective);
  }

  async _restoreFocus(session) {
    if (session.previousFocusWasHost && typeof this.focusHost === 'function') {
      await this.focusHost();
      return { status: 'success', summary: 'Restored focus to Cyrene.' };
    }
    if (!session.previousFocusTarget) {
      return { status: 'success', summary: 'No previous external window was recorded.' };
    }
    await this.provider.focusTarget(session.previousFocusTarget);
    return { status: 'success', summary: `Restored focus to ${session.previousFocusTarget.appName || 'the previous application'}.` };
  }

  async _perform(session, capability, parameters = {}) {
    if (capability === 'focus_window') {
      await this._focusSessionTarget(session);
      return { status: 'success', summary: `Focused ${session.target.appName || 'target application'}.` };
    }
    if (capability === 'restore_previous_focus') return this._restoreFocus(session);

    const focusCapabilities = new Set([
      'click_at', 'double_click', 'right_click',
      'hover_at', 'drag', 'swipe', 'scroll_at', 'key_chord', 'key_sequence',
    ]);
    const visualCapabilities = new Set([
      'click_at', 'double_click', 'right_click', 'hover_at', 'drag', 'swipe', 'scroll_at',
    ]);
    const needsFocus = focusCapabilities.has(capability);
    let focusedTemporarily = false;
    if (needsFocus && parameters.allow_foreground_input !== true) {
      throw new AppUseError(
        'foreground_input_not_allowed',
        `${capability} uses the real OS pointer or focus-dependent input. Pass allow_foreground_input=true only when the user explicitly permits foreground interference.`,
        { next_valid_actions: ['disconnect'] },
      );
    }
    if (needsFocus && session.focusPolicy === 'never') {
      throw new AppUseError('focus_required', `${capability} requires the target window to be focused.`);
    }
    if (needsFocus && session.focusPolicy === 'when_required') {
      await this._focusSessionTarget(session);
      focusedTemporarily = true;
      await delay(100);
    } else if (needsFocus && session.focusPolicy === 'always') {
      await this._focusSessionTarget(session);
      await delay(100);
    }
    const refFree = [
      'click_at', 'double_click', 'right_click', 'hover_at', 'drag', 'swipe', 'scroll_at',
      'key_chord', 'key_sequence',
    ];
    const needsRef = !refFree.includes(capability) && capability !== 'scroll';
    const nativeRef = this._nativeRef(session, parameters.ref, needsRef);
    let result;
    let restoreResult = null;
    let beforeVisual = null;
    let afterVisual = null;
    if (visualCapabilities.has(capability) && typeof this.captureTarget === 'function') {
      beforeVisual = captureFingerprint(await this.captureTarget(session.target).catch(() => null));
    }
    try {
      if (visualCapabilities.has(capability)) {
        await this._showCoordinatePointer(session, capability, parameters).catch(() => {});
      }
      result = await this.provider.perform(session.target, capability, nativeRef, parameters);
      if (visualCapabilities.has(capability) && typeof this.captureTarget === 'function') {
        await delay(120);
        afterVisual = captureFingerprint(await this.captureTarget(session.target).catch(() => null));
      }
    } finally {
      if (focusedTemporarily && parameters.restore_focus !== false) {
        restoreResult = await this._restoreFocus(session).catch((error) => ({
          status: 'error', message: String(error && error.message ? error.message : error),
        }));
      }
    }
    const visualVerification = beforeVisual && afterVisual ? {
      available: true,
      changed: beforeVisual.sha256 !== afterVisual.sha256,
      before_sha256: beforeVisual.sha256,
      after_sha256: afterVisual.sha256,
      width: afterVisual.width,
      height: afterVisual.height,
    } : { available: false, changed: null };
    if (result.visualChangeExpected === true) {
      if (visualVerification.available && visualVerification.changed) {
        result.verified = true;
        result.uncertain = false;
      } else {
        result.uncertain = true;
      }
    }
    let verification = result.verification || null;
    if (session.mode === 'semantic' && !verification && result.skipSnapshot !== true) {
      try {
        verification = await this._snapshot(session, { max_nodes: 80, max_depth: 8 });
      } catch (_) {}
    }
    return {
      status: result.verified === false || result.uncertain ? 'uncertain' : 'success',
      summary: String(result.summary || `${capability} completed.`),
      session_id: session.sessionId,
      focused_temporarily: focusedTemporarily,
      diagnostics: result.diagnostics || null,
      visual_verification: visualCapabilities.has(capability) ? visualVerification : null,
      focus_restore: restoreResult,
      verification,
      next_valid_actions: result.nextValidActions || (
        session.mode === 'semantic' ? ['call:wait', 'call:snapshot', 'disconnect'] : ['call:visual_describe', 'disconnect']
      ),
    };
  }

  _conditionMatches(snapshot, session, parameters) {
    let nodes = snapshot.nodes || [];
    if (parameters.ref) nodes = nodes.filter((node) => node.ref === parameters.ref);
    const expectedExists = parameters.exists !== false;
    if (!expectedExists) return nodes.length === 0;
    if (!nodes.length) return false;
    const property = String(parameters.property || '').trim();
    if (!property && parameters.contains === undefined && parameters.equals === undefined) return true;
    return nodes.some((node) => {
      const value = property ? node[property] : `${node.name || ''} ${node.description || ''} ${node.value || ''}`;
      if (parameters.equals !== undefined && value !== parameters.equals) return false;
      if (parameters.contains !== undefined && !String(value ?? '').includes(String(parameters.contains))) return false;
      return true;
    });
  }

  async _wait(session, parameters = {}) {
    const timeoutMs = clampInteger(parameters.timeout_ms, 5000, 100, 30000);
    const deadline = Date.now() + timeoutMs;
    let latest;
    while (Date.now() <= deadline) {
      latest = await this._snapshot(session, {
        max_nodes: parameters.max_nodes || 80,
        max_depth: parameters.max_depth || 8,
      });
      if (this._conditionMatches(latest, session, parameters)) {
        return { ...latest, wait_status: 'matched' };
      }
      await delay(150);
    }
    throw new AppUseError('timeout', 'Timed out waiting for the requested application condition.', { last_snapshot: latest });
  }

  async call(sessionId, capability, parameters = {}) {
    const session = await this._getSession(sessionId);
    const name = String(capability || '').trim();
    if (!session.capabilities.some((item) => item.name === name)) {
      throw new AppUseError('unsupported_capability', `Unknown App Use capability: ${name || '(empty)'}.`);
    }
    validateCapabilityParameters(name, parameters);
    if (name === 'snapshot') return this._snapshot(session, parameters);
    if (name === 'inspect') return this._inspect(session, parameters);
    if (name === 'find') return this._find(session, parameters);
    if (name === 'wait') return this._wait(session, parameters);
    if (name === 'virtual_type_at') return this._pidEventAt(session, 'pid_type_at', parameters);
    if (name === 'visual_describe') {
      if (typeof this.captureTarget !== 'function') {
        throw new AppUseError('unsupported_capability', 'Window capture is unavailable in this desktop host.');
      }
      const capture = await this.captureTarget(session.target);
      return {
        status: 'success',
        session_id: session.sessionId,
        target: publicTarget(session.target, session.target.targetId),
        image_base64: String(capture.imageBase64 || ''),
        mime_type: String(capture.mimeType || 'image/png'),
        width: Number(capture.width || 0),
        height: Number(capture.height || 0),
        coordinate_mapping: {
          input_space: 'window',
          logical_width: Number((session.target.bounds && session.target.bounds.width) || 0),
          logical_height: Number((session.target.bounds && session.target.bounds.height) || 0),
          captured_width: Number(capture.width || 0),
          captured_height: Number(capture.height || 0),
          note: 'Scale captured-image coordinates into window-relative logical coordinates before pointer actions.',
        },
        prompt: String(parameters.prompt || ''),
      };
    }
    return this._perform(session, name, parameters);
  }

  async status(sessionId) {
    const session = await this._getSession(sessionId);
    return {
      status: 'success',
      session_id: session.sessionId,
      target: publicTarget(session.target, session.target.targetId),
      focus_policy: session.focusPolicy,
      mode: session.mode,
      snapshot_revision: session.revision,
      manifest_version: MANIFEST_VERSION,
      ...(session.mode === 'semantic' ? { semantic_profile: session.semanticProfile || null } : {}),
    };
  }

  async disconnect(sessionId) {
    const session = this.sessions.get(String(sessionId || ''));
    if (!session) return { status: 'success', summary: 'App Use session was already disconnected.' };
    if (typeof this.hideVirtualPointer === 'function') {
      await this.hideVirtualPointer({
        target: publicTarget(session.target, session.target.targetId),
      }).catch(() => {});
    }
    this.sessions.delete(session.sessionId);
    return { status: 'success', summary: `Disconnected from ${session.target.appName || 'application window'}.` };
  }

  async handle(operation, args = {}) {
    try {
      switch (String(operation || '')) {
        case 'list_targets': return await this.listTargets();
        case 'connect': return await this.connect(args.target_id, args.parameters || {});
        case 'call': return await this.call(args.session_id, args.capability, args.parameters || {});
        case 'status': return await this.status(args.session_id);
        case 'disconnect': return await this.disconnect(args.session_id);
        default: throw new AppUseError('invalid_arguments', `Unknown App Use operation: ${operation || '(empty)'}.`);
      }
    } catch (error) {
      return normalizeResultError(error);
    }
  }
}

module.exports = {
  AppUseError,
  AppUseManager,
  CAPABILITIES,
  DARWIN_PID_TYPE_CAPABILITY,
  capabilitiesForTarget,
  CommandPlatformProvider,
  WindowsPowerShellWorker,
  MANIFEST_VERSION,
  resolveProviderScriptPath,
  resolveDarwinHitTestHelperPath,
  targetIdentity,
};
