const crypto = require('crypto');
const fs = require('fs');
const path = require('path');
const { execFile } = require('child_process');

const MANIFEST_VERSION = 'app-use-semantic-v2';
const DEFAULT_SESSION_TTL_MS = 5 * 60 * 1000;
const MAX_SCROLL_AT_AMOUNT = 50_000;
const AGENT_CURSOR_FADE_IN_MS = 150;
const AGENT_CURSOR_MOVE_MS = 180;
const AGENT_CURSOR_PRESS_MS = 100;
const SEMANTIC_TREE_CAPABILITIES = new Set([
  'snapshot', 'inspect', 'find', 'press', 'set_value', 'select', 'toggle', 'scroll',
  'type_text', 'select_text', 'set_selection_range', 'wait',
]);

const CAPABILITIES = Object.freeze([
  { name: 'snapshot', description: 'Read a compact semantic accessibility snapshot of the target window.', arguments: { scope_ref: 'string?', max_nodes: 'integer?', max_depth: 'integer?' }, background: 'safe' },
  { name: 'inspect', description: 'Read detailed properties and nearby descendants for one element ref.', arguments: { ref: 'string' }, background: 'safe' },
  { name: 'find', description: 'Find elements in the semantic tree by role, subrole, name, value, action, native action, automation id, class name, or state.', arguments: { role: 'string?', subrole: 'string?', name: 'string?', contains: 'string?', action: 'string?', native_action: 'string?', automation_id: 'string?', class_name: 'string?', enabled: 'boolean?', max_results: 'integer?' }, background: 'safe' },
  { name: 'press', description: 'Invoke the native default action of a button, menu item, link, or similar control.', arguments: { ref: 'string' }, background: 'safe_when_supported' },
  { name: 'set_value', description: 'Set the value of an editable control through the accessibility API.', arguments: { ref: 'string', value: 'string' }, background: 'safe_when_supported' },
  { name: 'select', description: 'Select a list item, menu item, tab, or option through its native action.', arguments: { ref: 'string' }, background: 'safe_when_supported' },
  { name: 'toggle', description: 'Toggle a checkbox, switch, or expandable control.', arguments: { ref: 'string' }, background: 'safe_when_supported' },
  { name: 'scroll', description: 'Scroll an accessible container or the target window.', arguments: { ref: 'string?', direction: 'up|down|left|right', amount: 'integer?' }, background: 'safe_when_supported' },
  { name: 'type_text', description: 'Write text to a semantically editable element and verify its value. This works in the background only when the accessibility provider exposes a writable value.', arguments: { ref: 'string', text: 'string', replace: 'boolean?' }, background: 'safe_when_supported' },
  { name: 'select_text', description: 'Select an exact text occurrence using focus-dependent input; allow_foreground_input=true is required.', arguments: { ref: 'string', text: 'string', occurrence: 'integer?', case_sensitive: 'boolean?', allow_foreground_input: 'boolean' }, background: 'requires_focus' },
  { name: 'set_selection_range', description: 'Set a selected character range using focus-dependent input; allow_foreground_input=true is required.', arguments: { ref: 'string', start: 'integer', end: 'integer', allow_foreground_input: 'boolean' }, background: 'requires_focus' },
  { name: 'click_at', description: 'Primary App Use click tool. Click the latest calibrated point with the real OS pointer. Coordinates are window-relative by default and allow_foreground_input=true is required.', arguments: { x: 'number', y: 'number', coordinate_space: 'window|screen?', allow_foreground_input: 'boolean' }, background: 'requires_focus' },
  { name: 'virtual_click_at', description: 'Use a coordinate to activate a background control while showing Cyrene\'s virtual pointer. It tries AX/UIA first; on macOS it can fall back to CGEventPostToPid, which routes the click only to the target process without moving the real cursor or changing foreground.', arguments: { x: 'number', y: 'number', coordinate_space: 'window|screen?', preferred_actions: 'string[]?', pointer_duration_ms: 'integer?', verify_effect: 'boolean?', pid_event_fallback: 'boolean?' }, background: 'safe_when_supported' },
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

const DARWIN_MENU_CAPABILITY = Object.freeze({
  name: 'menu_command',
  description: 'Invoke a macOS application menu item through AXPress without sending a keyboard event or moving the real cursor. Match by menu item name, shortcut, or both.',
  arguments: { name: 'string?', shortcut: 'string[]?', verify_effect: 'boolean?' },
  background: 'safe_when_supported',
});

const DARWIN_PID_TYPE_CAPABILITY = Object.freeze({
  name: 'virtual_type_at',
  description: 'Best-effort delivery of a coordinate click and Unicode text directly to a target macOS process with CGEventPostToPid. It does not move the real cursor, use the foreground keyboard, or focus the application. Event delivery never proves text insertion; use visual_type for exact verification.',
  arguments: { x: 'number', y: 'number', text: 'string', coordinate_space: 'window|screen?', pointer_duration_ms: 'integer?', verify_effect: 'boolean?' },
  background: 'best_effort_without_foreground',
});

const SAFARI_CAPABILITIES = Object.freeze([
  { name: 'browser_state', description: 'Read the current Safari tab URL and title without focusing its window.', arguments: {}, background: 'safe' },
  { name: 'navigate', description: 'Navigate the current Safari tab to a URL and verify the requested URL without focusing Safari.', arguments: { url: 'string' }, background: 'safe' },
  { name: 'reload', description: 'Reload the current Safari tab without focusing Safari.', arguments: {}, background: 'safe' },
]);

function capabilitiesForTarget(target) {
  const platformCapabilities = String(target.platform || process.platform) === 'darwin'
    ? [DARWIN_MENU_CAPABILITY, DARWIN_PID_TYPE_CAPABILITY] : [];
  return String(target.applicationId || '') === 'com.apple.Safari'
    ? [...SAFARI_CAPABILITIES, ...platformCapabilities, ...CAPABILITIES]
    : [...platformCapabilities, ...CAPABILITIES];
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

function validateCapabilityParameters(capability, parameters) {
  if (!parameters || typeof parameters !== 'object' || Array.isArray(parameters)) {
    throw new AppUseError('invalid_arguments', `${capability} parameters must be an object.`);
  }
  const descriptor = CAPABILITIES.concat(SAFARI_CAPABILITIES, [DARWIN_MENU_CAPABILITY, DARWIN_PID_TYPE_CAPABILITY]).find((item) => item.name === capability);
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
    'The macOS background coordinate hit-test helper is missing from this Cyrene installation.',
    {
      retryable: false,
      remediation: 'Rebuild or reinstall Cyrene with the native App Use hit-test helper. Do not substitute a real OS pointer event.',
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
  } = {}) {
    this.platform = platform;
    this.baseDir = baseDir;
    this.resourcesPath = resourcesPath;
    this.existsSync = existsSync;
  }

  async request(operation, payload = {}, timeout = 15000) {
    const request = { operation, ...payload };
    let result;
    try {
      if (this.platform === 'darwin') {
        const scriptPath = resolveProviderScriptPath(this);
        result = await runCommand('osascript', [
          '-l', 'JavaScript', scriptPath, JSON.stringify(request),
        ], { timeout });
      } else if (this.platform === 'win32') {
        const scriptPath = resolveProviderScriptPath(this);
        const encoded = Buffer.from(JSON.stringify(request), 'utf8').toString('base64');
        result = await runCommand('powershell.exe', [
          '-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass',
          '-File', scriptPath,
          '-PayloadBase64', encoded,
        ], { timeout });
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
            remediation: 'Use direct coordinate virtual_click_at or retry with a smaller max_nodes/max_depth scope. Do not focus the window or substitute a real OS pointer event.',
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

  async listTargets(exclusions = {}) {
    const normalized = typeof exclusions === 'number' ? { excludePid: exclusions } : exclusions;
    const result = await this.request('list_targets', normalized, 10000);
    return Array.isArray(result.targets) ? result.targets : [];
  }

  async snapshot(target, options = {}) {
    const timeout = clampInteger(options.timeoutMs, 15000, 250, 15000);
    const providerOptions = { ...options };
    delete providerOptions.timeoutMs;
    return this.request('snapshot', { target, options: providerOptions }, timeout);
  }

  async hitTest(target, point, preferredActions = [], perform = false) {
    try {
      const payload = { target, point, preferredActions, perform: perform === true };
      if (this.platform === 'darwin') {
        const helperPath = resolveDarwinHitTestHelperPath(this);
        const result = await runCommand(helperPath, [JSON.stringify(payload)], { timeout: 5000 });
        if (!result || result.ok === false) {
          throw new AppUseError(
            String((result && result.errorType) || 'provider_error'),
            String((result && result.error) || 'macOS coordinate hit-test failed.'),
          );
        }
        return result;
      }
      return await this.request('hit_test', payload, 5000);
    } catch (error) {
      if (error instanceof AppUseError && error.code === 'timeout') {
        throw new AppUseError(
          'accessibility_hit_test_timeout',
          'The target application did not answer the coordinate accessibility hit-test within 5000 ms.',
          {
            retryable: true,
            remediation: 'Retry once with a fresh target connection. Do not focus the window or substitute a real OS pointer event.',
          },
        );
      }
      throw error;
    }
  }

  async menuCommand(target, parameters = {}, perform = false) {
    if (this.platform !== 'darwin') {
      throw new AppUseError('unsupported_platform', 'Background menu commands are currently available only on macOS.');
    }
    const helperPath = resolveDarwinHitTestHelperPath(this);
    const result = await runCommand(helperPath, [JSON.stringify({
      operation: 'menu_command', target, name: parameters.name || '',
      shortcut: Array.isArray(parameters.shortcut) ? parameters.shortcut : [], perform: perform === true,
    })], { timeout: 5000 });
    if (!result || result.ok === false) {
      throw new AppUseError(
        String((result && result.errorType) || 'provider_error'),
        String((result && result.error) || 'macOS background menu command failed.'),
      );
    }
    return result;
  }

  async enableAccessibility(target) {
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
    return this.request('inspect', { target, nativeRef, options });
  }

  async perform(target, capability, nativeRef, parameters = {}) {
    return this.request('perform', { target, capability, nativeRef, parameters });
  }

  async focusTarget(target) {
    return this.request('focus', { target }, 10000);
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
    this.targets = new Map();
    this.sessions = new Map();
    this.lastExternalTargetId = '';
    this.quickChatOriginTargetId = '';
    this.trackerTimer = null;
    this.refreshPromise = null;
  }

  start() {
    if (this.trackerTimer) return;
    const tick = () => this.refreshTargets().catch(() => {});
    tick();
    this.trackerTimer = setInterval(tick, this.pollIntervalMs);
    if (typeof this.trackerTimer.unref === 'function') this.trackerTimer.unref();
  }

  stop() {
    if (this.trackerTimer) clearInterval(this.trackerTimer);
    this.trackerTimer = null;
    if (typeof this.hideVirtualPointer === 'function') {
      Promise.resolve(this.hideVirtualPointer({})).catch(() => {});
    }
    this.sessions.clear();
    this.targets.clear();
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

  async connect(targetId, parameters = {}) {
    await this.refreshTargets();
    const target = this._selectTarget(targetId, parameters);
    const focusPolicy = ['never', 'when_required', 'always'].includes(parameters.focus_policy)
      ? parameters.focus_policy
      : 'when_required';
    let accessibilityActivation = null;
    if (String(target.platform || process.platform) === 'darwin' && typeof this.provider.enableAccessibility === 'function') {
      accessibilityActivation = await this.provider.enableAccessibility(target).catch((error) => ({
        ok: false, error: String(error && error.message ? error.message : error),
      }));
      if (accessibilityActivation && accessibilityActivation.foregroundAffected === true) {
        throw new AppUseError('foreground_interference_detected', 'Enabling the target accessibility tree changed the foreground application.');
      }
    }
    const runtimeCapabilities = capabilitiesForTarget(target).filter(
      (capability) => focusPolicy !== 'never' || !['requires_focus', 'changes_focus'].includes(capability.background),
    );
    const sessionId = `app_session_${crypto.randomUUID()}`;
    const session = {
      sessionId,
      target: { ...target },
      targetIdentity: target.identity,
      focusPolicy,
      createdAt: Date.now(),
      lastUsedAt: Date.now(),
      revision: 0,
      refs: new Map(),
      pathToRef: new Map(),
      previousFocusTarget: null,
      previousFocusWasHost: false,
      capabilities: runtimeCapabilities,
    };
    this.sessions.set(sessionId, session);
    let semanticProfile = {
      status: 'unknown',
      reason: 'semantic_probe_not_completed',
      probe_timeout_ms: 2000,
    };
    try {
      const probe = await this._snapshot(session, {
        max_nodes: 12,
        max_depth: 3,
        _probe_timeout_ms: 2000,
      });
      semanticProfile = probe.semantic_profile;
    } catch (error) {
      const reason = error && error.code ? String(error.code) : 'semantic_probe_failed';
      semanticProfile = reason === 'accessibility_tree_timeout' || reason === 'timeout'
        ? { status: 'unavailable', reason: 'semantic_probe_timeout', probe_timeout_ms: 2000 }
        : { status: 'unknown', reason, probe_timeout_ms: 2000 };
    }
    if (semanticProfile.status === 'unavailable') {
      session.capabilities = session.capabilities.filter(
        (capability) => !SEMANTIC_TREE_CAPABILITIES.has(capability.name),
      );
      session.refs.clear();
      session.pathToRef.clear();
    }
    session.semanticProfile = semanticProfile;
    if (focusPolicy === 'always') await this._focusSessionTarget(session);
    return {
      status: 'success',
      session_id: sessionId,
      target: publicTarget(target, target.targetId),
      focus_policy: focusPolicy,
      manifest_version: MANIFEST_VERSION,
      capabilities: session.capabilities,
      accessibility_activation: accessibilityActivation,
      semantic_profile: semanticProfile,
      next_valid_actions: semanticProfile.status === 'unavailable'
        ? ['call:visual_describe', 'status', 'disconnect']
        : ['call:snapshot', 'call:find', 'status', 'disconnect'],
    };
  }

  async _getSession(sessionId) {
    this._expireSessions();
    const session = this.sessions.get(String(sessionId || ''));
    if (!session) throw new AppUseError('stale_session', 'The App Use session has expired or does not exist.');
    await this.refreshTargets();
    const current = this.targets.get(session.target.targetId);
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
    if (prune) {
      for (const [ref, nativeRef] of session.refs.entries()) {
        if (!livePaths.has(nativeRef)) session.refs.delete(ref);
      }
    }
    session.revision += 1;
    const actionable = nodes.filter((node) => Array.isArray(node.actions) && node.actions.length > 0);
    const labeled = actionable.filter((node) => String(node.name || node.description || '').trim());
    const ratio = actionable.length ? labeled.length / actionable.length : 1;
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
    const semanticProfile = containerOnly
      ? { status: 'unavailable', reason: 'container_only_tree' }
      : { status: 'available', reason: 'meaningful_nodes_exposed' };
    const grade = containerOnly || (canvasCount > 0 && actionable.length === 0)
      ? 'insufficient' : ratio < 0.6 ? 'partial' : 'full';
    return {
      status: 'success',
      session_id: session.sessionId,
      snapshot_revision: session.revision,
      target: publicTarget(session.target, session.target.targetId),
      semantic_coverage: {
        grade,
        total_nodes: nodes.length,
        actionable_nodes: actionable.length,
        labeled_actionable_ratio: Number(ratio.toFixed(2)),
        unlabeled_visual_regions: canvasCount,
      },
      semantic_profile: semanticProfile,
      nodes,
      truncated: providerResult.truncated === true,
    };
  }

  async _snapshot(session, parameters = {}) {
    const options = {
      maxNodes: clampInteger(parameters.max_nodes, 80, 1, 200),
      maxDepth: clampInteger(parameters.max_depth, 8, 1, 16),
    };
    if (parameters._probe_timeout_ms) {
      options.timeoutMs = clampInteger(parameters._probe_timeout_ms, 2000, 250, 5000);
    }
    if (parameters.scope_ref) options.nativeRef = this._nativeRef(session, parameters.scope_ref);
    const result = await this.provider.snapshot(session.target, options);
    return this._mapNodes(session, result, { prune: !options.nativeRef });
  }

  async _inspect(session, parameters = {}) {
    const nativeRef = this._nativeRef(session, parameters.ref);
    const result = await this.provider.inspect(session.target, nativeRef, {
      maxNodes: clampInteger(parameters.max_nodes, 40, 1, 100),
      maxDepth: clampInteger(parameters.max_depth, 3, 1, 8),
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

  async _virtualClickAt(session, parameters = {}) {
    const point = this._coordinatePoint(session, parameters);
    const requestedActions = Array.isArray(parameters.preferred_actions)
      ? parameters.preferred_actions.map((value) => String(value || '').toLowerCase())
      : ['press', 'select', 'toggle'];
    const allowedActions = requestedActions.filter((value) => ['press', 'select', 'toggle'].includes(value));
    if (!allowedActions.length) {
      throw new AppUseError('invalid_arguments', 'preferred_actions must contain press, select, or toggle.');
    }
    if (typeof this.provider.hitTest !== 'function') {
      throw new AppUseError(
        'provider_unavailable',
        'The desktop accessibility provider does not support direct coordinate hit-testing.',
        { retryable: false, next_valid_actions: ['call:find', 'disconnect'] },
      );
    }
    const verifyEffect = parameters.verify_effect !== false;
    const beforeVisual = verifyEffect && typeof this.captureTarget === 'function'
      ? captureFingerprint(await this.captureTarget(session.target).catch(() => null))
      : null;
    const probe = await this.provider.hitTest(session.target, point.screen, allowedActions, false);
    const bounds = (probe && probe.bounds) || {};
    const windowBounds = session.target.bounds || {};
    const targetArea = Number(bounds.width) * Number(bounds.height);
    const windowArea = Number(windowBounds.width) * Number(windowBounds.height);
    const coverage = windowArea > 0 && Number.isFinite(targetArea) ? targetArea / windowArea : 0;
    const broadContainer = /^(?:ax)?(?:group|webarea|window|scrollarea|application)$/i.test(String((probe && probe.role) || ''));
    const degenerateAxTarget = Boolean(
      probe && probe.found === true && broadContainer && coverage >= 0.65,
    );
    if (!probe || probe.found !== true || !probe.action || degenerateAxTarget) {
      const supportsPidEventFallback = String(session.target.platform || process.platform) === 'darwin'
        && parameters.pid_event_fallback !== false
        && typeof this.provider.pidEvent === 'function';
      if (supportsPidEventFallback) {
        const fallbackResult = await this._pidEventAt(session, 'pid_click_at', point, parameters);
        if (degenerateAxTarget) {
          fallbackResult.diagnostics = {
            ...(fallbackResult.diagnostics || {}),
            ax_candidate_rejected: {
              reason: 'degenerate_window_container',
              role: String(probe.role || ''),
              name: String(probe.name || ''),
              bounds: probe.bounds || null,
              window_coverage: coverage,
            },
          };
        }
        return fallbackResult;
      }
      throw new AppUseError(
        'unsupported_background_interaction',
        'No background-accessible press, select, or toggle action exists at this coordinate; no OS mouse event was sent.',
        {
          session_id: session.sessionId,
          virtual_pointer: { ...point, displayed: false },
          input_mode: 'background_accessibility',
          real_cursor_moved: false,
          focus_requested: false,
          diagnostics: (probe && probe.diagnostics) || null,
          next_valid_actions: ['call:find', 'disconnect'],
        },
      );
    }
    if (typeof this.showVirtualPointer === 'function') {
      await this._showPointerClickFeedback(
        point,
        publicTarget(session.target, session.target.targetId),
      ).catch(() => {});
    }
    const result = await this.provider.hitTest(session.target, point.screen, [probe.action], true);
    if (!result || result.found !== true || result.performed !== true) {
      throw new AppUseError(
        'background_activation_failed',
        'The accessible element changed or rejected its action before background activation completed; no OS mouse event was sent.',
        {
          session_id: session.sessionId,
          virtual_pointer: { ...point, displayed: typeof this.showVirtualPointer === 'function' },
          input_mode: 'background_accessibility',
          real_cursor_moved: false,
          focus_requested: false,
          diagnostics: (result && result.diagnostics) || null,
          next_valid_actions: ['call:virtual_click_at', 'call:find', 'disconnect'],
        },
      );
    }
    let afterVisual = null;
    if (beforeVisual && typeof this.captureTarget === 'function') {
      for (let attempt = 0; attempt < 3; attempt += 1) {
        await delay(120);
        afterVisual = captureFingerprint(await this.captureTarget(session.target).catch(() => null));
        if (afterVisual && beforeVisual.sha256 !== afterVisual.sha256) break;
      }
    }
    const effectVerification = beforeVisual && afterVisual ? {
      available: true,
      changed: beforeVisual.sha256 !== afterVisual.sha256,
      before_sha256: beforeVisual.sha256,
      after_sha256: afterVisual.sha256,
      method: 'window_capture_diff',
    } : { available: false, changed: null, method: null };
    const effectUnverified = effectVerification.available && effectVerification.changed !== true;
    const executedAction = {
      capability: 'virtual_click_at',
      input_mode: 'background_accessibility',
      semantic_action: String(result.action || probe.action || ''),
      native_action: String(result.nativeAction || probe.nativeAction || ''),
      point,
    };
    return {
      status: effectUnverified || result.verified === false ? 'uncertain' : 'success',
      summary: `Virtual pointer activated the background control with ${result.action}.`,
      session_id: session.sessionId,
      virtual_pointer: { ...point, displayed: typeof this.showVirtualPointer === 'function' },
      input_mode: 'background_accessibility',
      real_cursor_moved: false,
      focus_requested: false,
      diagnostics: result.diagnostics || null,
      verification: {
        status: effectUnverified || result.verified === false ? 'uncertain' : 'success',
        method: String((result.diagnostics && result.diagnostics.method) || 'direct_accessibility_hit_test'),
        native_action: String(result.nativeAction || ''),
        action_accepted: result.performed === true,
        effect_verified: effectVerification.changed,
        effect_verification: effectVerification,
      },
      requested_action: {
        capability: 'virtual_click_at',
        preferred_actions: allowedActions,
        point,
      },
      executed_action: executedAction,
      next_valid_actions: result.nextValidActions || ['call:wait', 'call:snapshot', 'disconnect'],
      virtual_target: {
        role: String(result.role || probe.role || ''),
        name: String(result.name || probe.name || ''),
        bounds: result.bounds || probe.bounds || null,
        action: String(result.action || probe.action || ''),
        native_action: String(result.nativeAction || probe.nativeAction || ''),
      },
    };
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
    const capability = operation === 'pid_type_at' ? 'virtual_type_at' : 'virtual_click_at';
    const visualChanged = beforeVisual && afterVisual ? beforeVisual.sha256 !== afterVisual.sha256 : null;
    // A full-window diff is not proof that a PID-directed event affected the
    // intended control: Electron apps often repaint timers, banners, and
    // cursors independently. High-level workflows must verify the requested
    // target state (for typing, the exact text) before reporting success.
    const effectVerified = null;
    return {
      status: 'uncertain',
      summary: operation === 'pid_type_at'
        ? 'Delivered a targeted background click and Unicode text event to the application process.'
        : 'Delivered a targeted background click event to the application process.',
      session_id: session.sessionId,
      virtual_pointer: { ...point, displayed: typeof this.showVirtualPointer === 'function' },
      input_mode: 'background_pid_event', real_cursor_moved: false, focus_requested: false,
      foreground_input_used: false, foreground_affected: false,
      requested_action: { capability, point, text: operation === 'pid_type_at' ? String(effectiveParameters.text || '') : undefined },
      executed_action: {
        capability, input_mode: 'background_pid_event', native_action: 'CGEventPostToPid', point,
        text_length: operation === 'pid_type_at' ? String(effectiveParameters.text || '').length : undefined,
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
        ...(session.semanticProfile && session.semanticProfile.status === 'unavailable' ? [] : ['call:snapshot']),
        'disconnect',
      ],
    };
  }

  async _menuCommand(session, parameters = {}) {
    const name = String(parameters.name || '').trim();
    const shortcut = Array.isArray(parameters.shortcut) ? parameters.shortcut.map((item) => String(item)) : [];
    if (!name && !shortcut.length) {
      throw new AppUseError('invalid_arguments', 'menu_command requires a menu item name or shortcut.');
    }
    if (typeof this.provider.menuCommand !== 'function') {
      throw new AppUseError('provider_unavailable', 'The desktop accessibility provider does not support background menu commands.');
    }
    const verifyEffect = parameters.verify_effect !== false;
    const beforeVisual = verifyEffect && typeof this.captureTarget === 'function'
      ? captureFingerprint(await this.captureTarget(session.target).catch(() => null)) : null;
    const probe = await this.provider.menuCommand(session.target, { name, shortcut }, false);
    if (!probe || probe.found !== true) {
      throw new AppUseError('unsupported_background_interaction', 'No matching background-accessible application menu item was found.', {
        diagnostics: (probe && probe.diagnostics) || null,
        real_cursor_moved: false, focus_requested: false,
        next_valid_actions: ['call:find', 'disconnect'],
      });
    }
    const result = await this.provider.menuCommand(session.target, { name, shortcut }, true);
    if (!result || result.performed !== true) {
      throw new AppUseError('background_activation_failed', 'The application menu item rejected its AXPress action.', {
        diagnostics: (result && result.diagnostics) || null,
      });
    }
    if (result.foregroundAffected === true) {
      throw new AppUseError('foreground_interference_detected', 'The menu action changed the foreground application and was not background-safe.', {
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
    const effectVerified = beforeVisual && afterVisual ? beforeVisual.sha256 !== afterVisual.sha256 : null;
    const executedAction = {
      capability: 'menu_command', input_mode: 'background_accessibility', semantic_action: 'press',
      native_action: String(result.nativeAction || 'AXPress'), target: { role: result.role || 'MenuItem', name: result.name || '' },
    };
    return {
      status: effectVerified === false ? 'uncertain' : 'success',
      summary: `Executed background menu AXPress on ${result.name || name || shortcut.join('+')}.`,
      session_id: session.sessionId,
      input_mode: 'background_accessibility', real_cursor_moved: false, focus_requested: false,
      foreground_input_used: false, foreground_affected: false,
      requested_action: { capability: 'menu_command', name, shortcut },
      executed_action: executedAction,
      verification: {
        status: effectVerified === false ? 'uncertain' : 'success', action_accepted: true,
        effect_verified: effectVerified, method: effectVerified === null ? 'AXPress acceptance' : 'window_capture_diff',
      },
      diagnostics: result.diagnostics || null,
      next_valid_actions: ['call:wait', 'call:snapshot', 'disconnect'],
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
      'select_text', 'set_selection_range', 'click_at', 'double_click', 'right_click',
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
        { next_valid_actions: ['call:virtual_click_at', 'call:find', 'call:snapshot', 'disconnect'] },
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
      'key_chord', 'key_sequence', 'browser_state', 'navigate', 'reload',
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
    if (!verification && result.skipSnapshot !== true) {
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
      next_valid_actions: result.nextValidActions || ['call:wait', 'call:snapshot', 'disconnect'],
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
    if (name === 'virtual_click_at') return this._virtualClickAt(session, parameters);
    if (name === 'virtual_type_at') return this._pidEventAt(session, 'pid_type_at', parameters);
    if (name === 'menu_command') return this._menuCommand(session, parameters);
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
      snapshot_revision: session.revision,
      manifest_version: MANIFEST_VERSION,
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
  DARWIN_MENU_CAPABILITY,
  DARWIN_PID_TYPE_CAPABILITY,
  SAFARI_CAPABILITIES,
  capabilitiesForTarget,
  CommandPlatformProvider,
  MANIFEST_VERSION,
  resolveProviderScriptPath,
  resolveDarwinHitTestHelperPath,
  targetIdentity,
};
