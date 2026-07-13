const crypto = require('crypto');
const fs = require('fs');
const path = require('path');
const { execFile } = require('child_process');

const MANIFEST_VERSION = 'app-use-semantic-v2';
const DEFAULT_SESSION_TTL_MS = 5 * 60 * 1000;

const CAPABILITIES = Object.freeze([
  { name: 'snapshot', description: 'Read a compact semantic accessibility snapshot of the target window.', arguments: { scope_ref: 'string?', max_nodes: 'integer?', max_depth: 'integer?' }, background: 'safe' },
  { name: 'inspect', description: 'Read detailed properties and nearby descendants for one element ref.', arguments: { ref: 'string' }, background: 'safe' },
  { name: 'find', description: 'Find elements in the semantic tree by role, name, value, or state.', arguments: { role: 'string?', name: 'string?', contains: 'string?', enabled: 'boolean?', max_results: 'integer?' }, background: 'safe' },
  { name: 'press', description: 'Invoke the native default action of a button, menu item, link, or similar control.', arguments: { ref: 'string' }, background: 'safe_when_supported' },
  { name: 'set_value', description: 'Set the value of an editable control through the accessibility API.', arguments: { ref: 'string', value: 'string' }, background: 'safe_when_supported' },
  { name: 'select', description: 'Select a list item, menu item, tab, or option through its native action.', arguments: { ref: 'string' }, background: 'safe_when_supported' },
  { name: 'toggle', description: 'Toggle a checkbox, switch, or expandable control.', arguments: { ref: 'string' }, background: 'safe_when_supported' },
  { name: 'scroll', description: 'Scroll an accessible container or the target window.', arguments: { ref: 'string?', direction: 'up|down|left|right', amount: 'integer?' }, background: 'safe_when_supported' },
  { name: 'type_text', description: 'Write text to a semantically editable element and verify its value. This works in the background only when the accessibility provider exposes a writable value.', arguments: { ref: 'string', text: 'string', replace: 'boolean?' }, background: 'safe_when_supported' },
  { name: 'select_text', description: 'Select an exact text occurrence inside an editable text control.', arguments: { ref: 'string', text: 'string', occurrence: 'integer?', case_sensitive: 'boolean?' }, background: 'requires_focus' },
  { name: 'set_selection_range', description: 'Set the selected character range in an editable text control using zero-based start and exclusive end offsets.', arguments: { ref: 'string', start: 'integer', end: 'integer' }, background: 'requires_focus' },
  { name: 'click_at', description: 'Click a point inside the connected window. Coordinates are window-relative by default.', arguments: { x: 'number', y: 'number', coordinate_space: 'window|screen?' }, background: 'requires_focus' },
  { name: 'double_click', description: 'Double-click a point inside the connected window.', arguments: { x: 'number', y: 'number', coordinate_space: 'window|screen?', interval_ms: 'integer?' }, background: 'requires_focus' },
  { name: 'right_click', description: 'Right-click a point inside the connected window.', arguments: { x: 'number', y: 'number', coordinate_space: 'window|screen?' }, background: 'requires_focus' },
  { name: 'hover_at', description: 'Move the pointer to a point inside the connected window.', arguments: { x: 'number', y: 'number', coordinate_space: 'window|screen?', duration_ms: 'integer?' }, background: 'requires_focus' },
  { name: 'drag', description: 'Drag from one point to another inside the connected window.', arguments: { from_x: 'number', from_y: 'number', to_x: 'number', to_y: 'number', coordinate_space: 'window|screen?', duration_ms: 'integer?' }, background: 'requires_focus' },
  { name: 'swipe', description: 'Drag from a starting point in one direction inside the connected window.', arguments: { x: 'number', y: 'number', direction: 'up|down|left|right', distance: 'number?', coordinate_space: 'window|screen?', duration_ms: 'integer?' }, background: 'requires_focus' },
  { name: 'scroll_at', description: 'Send vertical or horizontal wheel scrolling at a point inside the connected window.', arguments: { x: 'number', y: 'number', direction: 'up|down|left|right', amount: 'integer?', coordinate_space: 'window|screen?' }, background: 'requires_focus' },
  { name: 'key_chord', description: 'Send a key or keyboard shortcut to the target window.', arguments: { keys: 'string[]' }, background: 'requires_focus' },
  { name: 'key_sequence', description: 'Atomically execute shortcut, text, key, and pause steps during one temporary-focus interval.', arguments: { steps: '{type:shortcut|text|key|pause,keys?:string[],text?:string,key?:string,ms?:integer}[]' }, background: 'requires_focus' },
  { name: 'wait', description: 'Wait for an element or property condition, then return a fresh semantic snapshot.', arguments: { ref: 'string?', property: 'string?', equals: 'any?', contains: 'string?', exists: 'boolean?', timeout_ms: 'integer?' }, background: 'safe' },
  { name: 'visual_describe', description: 'Capture the connected window and return a text description through Cyrene\'s configured vision model.', arguments: { prompt: 'string?' }, background: 'safe' },
  { name: 'focus_window', description: 'Bring the connected target window to the foreground.', arguments: {}, background: 'changes_focus' },
  { name: 'restore_previous_focus', description: 'Restore the window that was foreground before this App Use session focused its target.', arguments: {}, background: 'changes_focus' },
]);

const SAFARI_CAPABILITIES = Object.freeze([
  { name: 'browser_state', description: 'Read the current Safari tab URL and title without focusing its window.', arguments: {}, background: 'safe' },
  { name: 'navigate', description: 'Navigate the current Safari tab to a URL and verify the requested URL without focusing Safari.', arguments: { url: 'string' }, background: 'safe' },
  { name: 'reload', description: 'Reload the current Safari tab without focusing Safari.', arguments: {}, background: 'safe' },
]);

function capabilitiesForTarget(target) {
  return String(target.applicationId || '') === 'com.apple.Safari'
    ? [...SAFARI_CAPABILITIES, ...CAPABILITIES]
    : [...CAPABILITIES];
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
    return this.request('snapshot', { target, options });
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
      capabilities: capabilitiesForTarget(target),
    };
    this.sessions.set(sessionId, session);
    if (focusPolicy === 'always') await this._focusSessionTarget(session);
    return {
      status: 'success',
      session_id: sessionId,
      target: publicTarget(target, target.targetId),
      focus_policy: focusPolicy,
      manifest_version: MANIFEST_VERSION,
      capabilities: session.capabilities,
      next_valid_actions: ['call:snapshot', 'call:find', 'status', 'disconnect'],
    };
  }

  async _getSession(sessionId) {
    this._expireSessions();
    const session = this.sessions.get(String(sessionId || ''));
    if (!session) throw new AppUseError('stale_session', 'The App Use session has expired or does not exist.');
    await this.refreshTargets();
    const current = this.targets.get(session.target.targetId);
    if (!current || current.identity !== session.targetIdentity) {
      this.sessions.delete(session.sessionId);
      throw new AppUseError('stale_session', 'The connected application window changed or closed. Reconnect before acting.');
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
    const grade = canvasCount > 0 && actionable.length === 0 ? 'insufficient' : ratio < 0.6 ? 'partial' : 'full';
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
      nodes,
      truncated: providerResult.truncated === true,
    };
  }

  async _snapshot(session, parameters = {}) {
    const options = {
      maxNodes: clampInteger(parameters.max_nodes, 80, 1, 200),
      maxDepth: clampInteger(parameters.max_depth, 8, 1, 16),
    };
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
    const name = String(parameters.name || '').toLowerCase();
    const contains = String(parameters.contains || '').toLowerCase();
    const maxResults = clampInteger(parameters.max_results, 20, 1, 100);
    const nodes = snapshot.nodes.filter((node) => {
      if (role && !String(node.role || '').toLowerCase().includes(role)) return false;
      if (name && String(node.name || '').toLowerCase() !== name) return false;
      if (parameters.enabled !== undefined && Boolean(node.enabled) !== Boolean(parameters.enabled)) return false;
      if (contains) {
        const haystack = `${node.name || ''} ${node.description || ''} ${node.value || ''}`.toLowerCase();
        if (!haystack.includes(contains)) return false;
      }
      return true;
    }).slice(0, maxResults);
    return { ...snapshot, nodes, matched: nodes.length, total_nodes: snapshot.nodes.length };
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
      throw new AppUseError('focus_failed', `Could not focus ${session.target.appName || 'the target application'}.`);
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
      'click_at', 'double_click', 'right_click', 'hover_at', 'drag', 'swipe', 'scroll_at', 'key_sequence',
    ]);
    const needsFocus = focusCapabilities.has(capability);
    let focusedTemporarily = false;
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
    if (name === 'snapshot') return this._snapshot(session, parameters);
    if (name === 'inspect') return this._inspect(session, parameters);
    if (name === 'find') return this._find(session, parameters);
    if (name === 'wait') return this._wait(session, parameters);
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
  SAFARI_CAPABILITIES,
  capabilitiesForTarget,
  CommandPlatformProvider,
  MANIFEST_VERSION,
  resolveProviderScriptPath,
  targetIdentity,
};
