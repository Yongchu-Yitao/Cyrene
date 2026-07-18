const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');

const {
  AppUseManager,
  CAPABILITIES,
  DARWIN_MENU_CAPABILITY,
  DARWIN_PID_TYPE_CAPABILITY,
  capabilitiesForTarget,
  resolveDarwinHitTestHelperPath,
  resolveProviderScriptPath,
  SAFARI_CAPABILITIES,
} = require('./app-use');

test('packaged provider resolves from external resources instead of app.asar', () => {
  const resourcesPath = path.join('/Applications', 'Cyrene.app', 'Contents', 'Resources');
  const expected = path.join(resourcesPath, 'app-use', 'app-use-macos.jxa');
  const resolved = resolveProviderScriptPath({
    platform: 'darwin',
    baseDir: path.join(resourcesPath, 'app.asar'),
    resourcesPath,
    existsSync: (candidate) => candidate === expected || candidate.endsWith('app.asar/app-use-macos.jxa'),
  });
  assert.equal(resolved, expected);
  assert.equal(resolved.includes('app.asar'), false);
});

test('Windows packaged provider uses the same external resource layout', () => {
  const resourcesPath = path.join('C:', 'Cyrene', 'resources');
  const expected = path.join(resourcesPath, 'app-use', 'app-use-windows.ps1');
  assert.equal(resolveProviderScriptPath({
    platform: 'win32',
    baseDir: path.join(resourcesPath, 'app.asar'),
    resourcesPath,
    existsSync: (candidate) => candidate === expected,
  }), expected);
});

test('Windows provider source includes Win32 bounds and robust focus fallbacks', () => {
  const source = fs.readFileSync(path.join(__dirname, 'app-use-windows.ps1'), 'utf8');
  assert.match(source, /GetWindowRect/);
  assert.match(source, /WindowRect\(\$handleValue\)/);
  assert.match(source, /AttachThreadInput/);
  assert.match(source, /sameIntegrityLevelRequired/);
});

test('coordinate scroll providers split large amounts into safe wheel events', () => {
  const macSource = fs.readFileSync(path.join(__dirname, 'app-use-macos.jxa'), 'utf8');
  const windowsSource = fs.readFileSync(path.join(__dirname, 'app-use-windows.ps1'), 'utf8');
  assert.match(macSource, /const MAX_SCROLL_AT_AMOUNT = 50000;/);
  assert.match(macSource, /const MAX_SCROLL_EVENT_AMOUNT = 10;/);
  assert.match(macSource, /const DEFAULT_SCROLL_AT_PIXEL_AMOUNT = 30;/);
  assert.match(macSource, /while \(remaining > 0\)/);
  assert.match(macSource, /Math\.min\(MAX_SCROLL_EVENT_AMOUNT, remaining\)/);
  assert.match(macSource, /kCGScrollEventUnitPixel/);
  assert.doesNotMatch(macSource, /Math\.min\(20, Math\.trunc\(Number\(parameters\.amount/);
  assert.match(windowsSource, /\$MaxScrollAtAmount = 50000/);
  assert.match(windowsSource, /\$MaxScrollEventAmount = 10/);
  assert.match(windowsSource, /while \(\$remaining -gt 0\)/);
  assert.match(windowsSource, /\[Math\]::Min\(\$MaxScrollEventAmount, \$remaining\)/);
  assert.doesNotMatch(windowsSource, /\[Math\]::Min\(20, .*\$Parameters\.amount/);
});

test('Windows runtime capabilities exclude macOS PID typing and menu commands', () => {
  const names = capabilitiesForTarget({ platform: 'win32', applicationId: 'C:\\Demo\\demo.exe' })
    .map((item) => item.name);
  assert.equal(names.includes('virtual_type_at'), false);
  assert.equal(names.includes('menu_command'), false);
  assert.equal(names.includes('visual_describe'), true);
  assert.equal(names.includes('virtual_click_at'), true);
});

test('packaged macOS coordinate hit-test helper resolves outside app.asar', () => {
  const resourcesPath = path.join('/Applications', 'Cyrene.app', 'Contents', 'Resources');
  const expected = path.join(resourcesPath, 'app-use', 'app-use-macos-hit-test');
  const resolved = resolveDarwinHitTestHelperPath({
    platform: 'darwin',
    baseDir: path.join(resourcesPath, 'app.asar'),
    resourcesPath,
    existsSync: (candidate) => candidate === expected,
  });
  assert.equal(resolved, expected);
  assert.equal(resolved.includes('app.asar'), false);
});

test('missing provider returns a non-retryable actionable error before launching a command', () => {
  assert.throws(
    () => resolveProviderScriptPath({
      platform: 'darwin',
      baseDir: '/Applications/Cyrene.app/Contents/Resources/app.asar',
      resourcesPath: '/Applications/Cyrene.app/Contents/Resources',
      existsSync: () => false,
    }),
    (error) => {
      assert.equal(error.code, 'provider_unavailable');
      assert.equal(error.extra.retryable, false);
      assert.match(error.extra.remediation, /rebuild or reinstall/i);
      assert.match(error.extra.remediation, /do not substitute shell automation/i);
      return true;
    },
  );
});

class FakeProvider {
  constructor() {
    this.targets = [
      {
        platform: 'darwin', pid: 10, processStartTime: '1', appName: 'Notes', applicationId: 'notes',
        windowId: '100', windowIndex: 0, windowTitle: 'Foreground note', foreground: true, minimized: false,
        bounds: { x: 0, y: 0, width: 800, height: 600 },
      },
      {
        platform: 'darwin', pid: 20, processStartTime: '2', appName: 'TextEdit', applicationId: 'textedit',
        windowId: '200', windowIndex: 0, windowTitle: 'Background document', foreground: false, minimized: false,
        bounds: { x: 100, y: 100, width: 800, height: 600 },
      },
    ];
    this.focused = [];
    this.performed = [];
    this.value = 'draft';
    this.snapshotCount = 0;
  }

  async listTargets(exclusions = {}) {
    this.lastExclusions = exclusions;
    return this.targets.map((target) => ({ ...target }));
  }

  async snapshot(target, options = {}) {
    this.snapshotCount += 1;
    const prefix = options.nativeRef || 'w0';
    return {
      ok: true,
      nodes: [
        { nativeRef: prefix, role: 'Window', name: target.windowTitle, enabled: true, actions: [], bounds: target.bounds },
        { nativeRef: `${prefix}/e0`, role: 'TextField', name: 'Body', value: this.value, enabled: true, actions: ['set_value'], bounds: { x: target.bounds.x + 20, y: target.bounds.y + 80, width: 300, height: 40 } },
        { nativeRef: `${prefix}/e1`, role: 'Button', subrole: 'CloseButton', name: 'Save', enabled: true, actions: ['press'], nativeActions: ['AXPress'], bounds: { x: target.bounds.x + 40, y: target.bounds.y + 30, width: 80, height: 30 } },
      ],
    };
  }

  async inspect(target, nativeRef) {
    return { ok: true, nodes: [{ nativeRef, role: 'Button', name: 'Save', enabled: true, actions: ['press'] }] };
  }

  async hitTest(target, point, preferredActions, perform) {
    this.hitTests = this.hitTests || [];
    this.hitTests.push({ target, point, preferredActions, perform });
    const bounds = {
      x: target.bounds.x + 40, y: target.bounds.y + 30, width: 80, height: 30,
    };
    const contains = point.x >= bounds.x && point.y >= bounds.y
      && point.x < bounds.x + bounds.width && point.y < bounds.y + bounds.height;
    if (!contains || !preferredActions.includes('press')) {
      return {
        ok: true, found: false,
        diagnostics: { method: 'fake direct hit-test', treeScanUsed: false },
      };
    }
    if (perform) this.performed.push({ target, capability: 'press', nativeRef: null, parameters: { point } });
    return {
      ok: true, found: true, performed: perform, verified: perform,
      action: 'press', nativeAction: 'AXPress', role: 'Button', name: 'Save', bounds,
      diagnostics: { method: perform ? 'fake AXPress' : 'fake direct hit-test', treeScanUsed: false },
    };
  }

  async menuCommand(target, parameters, perform) {
    this.menuCommands = this.menuCommands || [];
    this.menuCommands.push({ target, parameters, perform });
    return {
      ok: true, found: true, performed: perform, verified: perform,
      action: 'press', nativeAction: 'AXPress', role: 'MenuItem', name: parameters.name || 'New Tab',
      foregroundAffected: false, diagnostics: { method: 'fake AX menu item press', backgroundSafe: true },
    };
  }

  async perform(target, capability, nativeRef, parameters) {
    this.performed.push({ target, capability, nativeRef, parameters });
    if (capability === 'set_value') this.value = String(parameters.value ?? '');
    if (capability === 'type_text') {
      const text = String(parameters.text ?? '');
      this.value = parameters.replace === true ? text : `${this.value}${text}`;
    }
    return { ok: true, verified: true, summary: `${capability} ok`, diagnostics: { fake: true } };
  }

  async focusTarget(target) {
    this.focused.push(target.windowId);
    return { ok: true };
  }
}

async function connectedManager({ focusPolicy = 'when_required' } = {}) {
  const provider = new FakeProvider();
  const manager = new AppUseManager({ provider, ownPid: 999, sessionTtlMs: 60_000 });
  const listed = await manager.handle('list_targets', {});
  const background = listed.targets.find((target) => target.app_name === 'TextEdit');
  const connected = await manager.handle('connect', {
    target_id: background.target_id,
    parameters: { focus_policy: focusPolicy },
  });
  return { provider, manager, listed, connected };
}

async function connectedManagerWithCapture() {
  const provider = new FakeProvider();
  const manager = new AppUseManager({
    provider,
    ownPid: 999,
    captureTarget: async () => ({ imageBase64: 'aW1hZ2U=', mimeType: 'image/png', width: 800, height: 600 }),
  });
  const listed = await manager.handle('list_targets', {});
  const connected = await manager.handle('connect', {
    target_id: listed.targets[0].target_id,
    parameters: {},
  });
  return { manager, connected };
}

test('lists foreground and background targets with stable opaque ids', async () => {
  const provider = new FakeProvider();
  const manager = new AppUseManager({ provider, ownPid: 999 });
  const first = await manager.handle('list_targets', {});
  const second = await manager.handle('list_targets', {});
  assert.equal(first.status, 'success');
  assert.equal(first.targets.length, 2);
  assert.equal(first.targets[0].target_id, second.targets[0].target_id);
  assert.equal(first.targets.find((item) => item.foreground).app_name, 'Notes');
  assert.equal(first.targets.find((item) => !item.foreground).app_name, 'TextEdit');
});

test('connect discloses runtime capabilities without changing the gateway', async () => {
  const { connected } = await connectedManager();
  assert.equal(connected.status, 'success');
  assert.match(connected.session_id, /^app_session_/);
  assert.equal(connected.target.app_name, 'TextEdit');
  assert.deepEqual(connected.capabilities, [DARWIN_MENU_CAPABILITY, DARWIN_PID_TYPE_CAPABILITY, ...CAPABILITIES]);
  assert.ok(connected.capabilities.some((item) => item.name === 'menu_command'));
  assert.ok(connected.capabilities.some((item) => item.name === 'snapshot'));
  assert.ok(connected.capabilities.some((item) => item.name === 'key_chord'));
  assert.ok(connected.capabilities.some((item) => item.name === 'click_at'));
  assert.ok(connected.capabilities.some((item) => item.name === 'drag'));
  assert.ok(connected.capabilities.some((item) => item.name === 'select_text'));
  assert.ok(connected.capabilities.some((item) => item.name === 'key_sequence'));
  assert.ok(connected.capabilities.some((item) => item.name === 'visual_describe'));
});

test('connect removes semantic-tree fallbacks for container-only windows', async () => {
  const provider = new FakeProvider();
  provider.snapshot = async (target) => ({
    ok: true,
    nodes: [
      { nativeRef: 'w0', role: 'Window', name: target.windowTitle, actions: [], nativeActions: ['AXRaise'], bounds: target.bounds },
      { nativeRef: 'w0/e0', role: 'Group', subrole: 'HostingView', actions: [], bounds: target.bounds },
    ],
  });
  const manager = new AppUseManager({ provider, ownPid: 999 });
  const listed = await manager.handle('list_targets', {});
  const connected = await manager.handle('connect', {
    target_id: listed.targets.find((target) => target.app_name === 'TextEdit').target_id,
    parameters: { focus_policy: 'never' },
  });
  const names = connected.capabilities.map((capability) => capability.name);
  assert.deepEqual(connected.semantic_profile, {
    status: 'unavailable', reason: 'container_only_tree',
  });
  assert.equal(names.includes('snapshot'), false);
  assert.equal(names.includes('find'), false);
  assert.equal(names.includes('press'), false);
  assert.ok(names.includes('virtual_click_at'));
  assert.ok(names.includes('visual_describe'));
  const blocked = await manager.handle('call', {
    session_id: connected.session_id,
    capability: 'snapshot',
    parameters: {},
  });
  assert.equal(blocked.status, 'error');
  assert.equal(blocked.type, 'unsupported_capability');
});

test('connect treats a bounded semantic probe timeout as unavailable', async () => {
  const provider = new FakeProvider();
  provider.snapshot = async () => {
    const error = new Error('tree probe timed out');
    error.code = 'accessibility_tree_timeout';
    throw error;
  };
  const manager = new AppUseManager({ provider, ownPid: 999 });
  const listed = await manager.handle('list_targets', {});
  const connected = await manager.handle('connect', {
    target_id: listed.targets.find((target) => target.app_name === 'TextEdit').target_id,
    parameters: { focus_policy: 'never' },
  });
  const names = connected.capabilities.map((capability) => capability.name);
  assert.deepEqual(connected.semantic_profile, {
    status: 'unavailable', reason: 'semantic_probe_timeout', probe_timeout_ms: 2000,
  });
  assert.equal(names.includes('snapshot'), false);
  assert.equal(names.includes('find'), false);
  assert.ok(names.includes('virtual_click_at'));
});

test('Safari connections disclose native background browser capabilities only at runtime', async () => {
  const provider = new FakeProvider();
  provider.targets.push({
    platform: 'darwin', pid: 40, processStartTime: '4', appName: 'Safari', applicationId: 'com.apple.Safari',
    windowId: '400', windowIndex: 0, windowTitle: 'Start Page', foreground: false, minimized: false,
  });
  provider.perform = async (target, capability, nativeRef, parameters) => ({
    ok: true,
    verified: true,
    skipSnapshot: true,
    summary: `${capability} ok`,
    verification: { url: parameters.url || 'https://example.com', foreground: false },
  });
  const manager = new AppUseManager({ provider, ownPid: 999 });
  const listed = await manager.handle('list_targets', {});
  const connected = await manager.handle('connect', {
    target_id: listed.targets.find((target) => target.app_name === 'Safari').target_id,
    parameters: {},
  });
  assert.deepEqual(connected.capabilities.slice(0, SAFARI_CAPABILITIES.length), SAFARI_CAPABILITIES);
  const result = await manager.handle('call', {
    session_id: connected.session_id,
    capability: 'navigate',
    parameters: { url: 'https://github.com' },
  });
  assert.equal(result.status, 'success');
  assert.equal(result.focused_temporarily, false);
  assert.equal(result.verification.url, 'https://github.com');
  assert.deepEqual(provider.focused, []);
});

test('visual describe captures the connected window for the Python vision adapter', async () => {
  const { manager, connected } = await connectedManagerWithCapture();
  assert.equal(connected.focus_policy, 'when_required');
  assert.ok(connected.capabilities.some((item) => item.name === 'click_at'));
  const result = await manager.handle('call', {
    session_id: connected.session_id,
    capability: 'visual_describe',
    parameters: { prompt: 'Describe the chart.' },
  });
  assert.equal(result.status, 'success');
  assert.equal(result.image_base64, 'aW1hZ2U=');
  assert.equal(result.width, 800);
  assert.equal(result.prompt, 'Describe the chart.');
});

test('snapshot creates refs and semantic coverage for a background window', async () => {
  const { manager, connected } = await connectedManager();
  const snapshot = await manager.handle('call', {
    session_id: connected.session_id,
    capability: 'snapshot',
    parameters: {},
  });
  assert.equal(snapshot.status, 'success');
  assert.equal(snapshot.target.foreground, false);
  assert.deepEqual(snapshot.nodes.map((node) => node.ref), ['e1', 'e2', 'e3']);
  assert.equal(snapshot.semantic_coverage.grade, 'full');
  assert.equal(snapshot.nodes[1].nativeRef, undefined);
});

test('semantic action resolves ref and returns a verification snapshot', async () => {
  const { manager, provider, connected } = await connectedManager();
  const snapshot = await manager.handle('call', {
    session_id: connected.session_id, capability: 'snapshot', parameters: {},
  });
  const body = snapshot.nodes.find((node) => node.name === 'Body');
  const result = await manager.handle('call', {
    session_id: connected.session_id,
    capability: 'set_value',
    parameters: { ref: body.ref, value: 'finished' },
  });
  assert.equal(result.status, 'success');
  assert.equal(provider.performed[0].nativeRef, 'w0/e0');
  assert.equal(result.verification.nodes.find((node) => node.name === 'Body').value, 'finished');
});

test('inspect does not invalidate refs outside the inspected subtree', async () => {
  const { manager, connected } = await connectedManager();
  const snapshot = await manager.handle('call', {
    session_id: connected.session_id, capability: 'snapshot', parameters: {},
  });
  const save = snapshot.nodes.find((node) => node.name === 'Save');
  const body = snapshot.nodes.find((node) => node.name === 'Body');
  const inspected = await manager.handle('call', {
    session_id: connected.session_id, capability: 'inspect', parameters: { ref: save.ref },
  });
  assert.equal(inspected.status, 'success');
  const result = await manager.handle('call', {
    session_id: connected.session_id, capability: 'set_value', parameters: { ref: body.ref, value: 'still valid' },
  });
  assert.equal(result.status, 'success');
});

test('find filters semantic nodes', async () => {
  const { manager, connected } = await connectedManager();
  const result = await manager.handle('call', {
    session_id: connected.session_id,
    capability: 'find',
    parameters: { role: 'button', contains: 'sav' },
  });
  assert.equal(result.status, 'success');
  assert.equal(result.matched, 1);
  assert.equal(result.nodes[0].name, 'Save');
});

test('find filters subroles and actions', async () => {
  const { manager, connected } = await connectedManager();
  const result = await manager.handle('call', {
    session_id: connected.session_id,
    capability: 'find',
    parameters: { subrole: 'closebutton', action: 'press' },
  });
  assert.equal(result.status, 'success');
  assert.equal(result.matched, 1);
  assert.equal(result.nodes[0].name, 'Save');
});

test('virtual coordinate click activates a background accessible control without focusing or moving the real pointer', async () => {
  const provider = new FakeProvider();
  const displayed = [];
  const manager = new AppUseManager({
    provider,
    ownPid: 999,
    showVirtualPointer: async (point) => { displayed.push(point); },
  });
  const listed = await manager.handle('list_targets', {});
  const connected = await manager.handle('connect', {
    target_id: listed.targets.find((target) => target.app_name === 'TextEdit').target_id,
    parameters: { focus_policy: 'when_required' },
  });
  const snapshotsAfterConnectProbe = provider.snapshotCount;
  const result = await manager.handle('call', {
    session_id: connected.session_id,
    capability: 'virtual_click_at',
    parameters: { x: 80, y: 45, coordinate_space: 'window' },
  });
  assert.equal(result.status, 'success');
  assert.equal(result.foreground_affected, undefined);
  assert.equal(result.virtual_target.name, 'Save');
  assert.equal(result.virtual_target.action, 'press');
  assert.equal(result.input_mode, 'background_accessibility');
  assert.equal(result.real_cursor_moved, false);
  assert.equal(result.focus_requested, false);
  assert.deepEqual(provider.focused, []);
  assert.equal(provider.performed.at(-1).capability, 'press');
  assert.equal(provider.snapshotCount, snapshotsAfterConnectProbe);
  assert.equal(provider.hitTests.length, 2);
  assert.deepEqual(displayed[0].x, 180);
  assert.deepEqual(displayed[0].y, 145);
});

test('virtual coordinate click sends no input when the point has no accessible action', async () => {
  const provider = new FakeProvider();
  const displayed = [];
  const manager = new AppUseManager({
    provider,
    ownPid: 999,
    showVirtualPointer: async (point) => { displayed.push(point); },
  });
  const listed = await manager.handle('list_targets', {});
  const connected = await manager.handle('connect', {
    target_id: listed.targets.find((target) => target.app_name === 'TextEdit').target_id,
    parameters: { focus_policy: 'when_required' },
  });
  const snapshotsAfterConnectProbe = provider.snapshotCount;
  const result = await manager.handle('call', {
    session_id: connected.session_id,
    capability: 'virtual_click_at',
    parameters: { x: 700, y: 500, coordinate_space: 'window' },
  });
  assert.equal(result.status, 'error');
  assert.equal(result.type, 'unsupported_background_interaction');
  assert.equal(result.real_cursor_moved, false);
  assert.equal(result.focus_requested, false);
  assert.deepEqual(provider.focused, []);
  assert.deepEqual(provider.performed, []);
  assert.deepEqual(displayed, []);
  assert.equal(provider.snapshotCount, snapshotsAfterConnectProbe);
  assert.equal(provider.hitTests.length, 1);
});

test('Windows virtual click never enters the macOS PID event fallback', async () => {
  const provider = new FakeProvider();
  provider.targets = provider.targets.map((target) => ({
    ...target,
    platform: 'win32',
    applicationId: `C:\\Apps\\${target.appName}.exe`,
  }));
  let pidFallbackCalled = false;
  provider.pidEvent = async () => {
    pidFallbackCalled = true;
    throw new Error('macOS-only PID fallback must not run on Windows');
  };
  const manager = new AppUseManager({ provider, ownPid: 999 });
  const listed = await manager.handle('list_targets', {});
  const connected = await manager.handle('connect', {
    target_id: listed.targets.find((target) => target.app_name === 'TextEdit').target_id,
    parameters: {},
  });
  const result = await manager.handle('call', {
    session_id: connected.session_id,
    capability: 'virtual_click_at',
    parameters: { x: 700, y: 500, coordinate_space: 'window', pid_event_fallback: true },
  });
  assert.equal(result.status, 'error');
  assert.equal(result.type, 'unsupported_background_interaction');
  assert.equal(pidFallbackCalled, false);
});

test('virtual coordinate click remains independent of a timing-out full accessibility snapshot', async () => {
  const provider = new FakeProvider();
  provider.snapshot = async () => { throw new Error('full tree timed out'); };
  const manager = new AppUseManager({ provider, ownPid: 999 });
  const listed = await manager.handle('list_targets', {});
  const connected = await manager.handle('connect', {
    target_id: listed.targets.find((target) => target.app_name === 'TextEdit').target_id,
    parameters: {},
  });
  const result = await manager.handle('call', {
    session_id: connected.session_id,
    capability: 'virtual_click_at',
    parameters: { x: 80, y: 45, coordinate_space: 'window' },
  });
  assert.equal(result.status, 'success');
  assert.equal(result.diagnostics.treeScanUsed, false);
  assert.equal(provider.hitTests.length, 2);
});

test('virtual coordinate click preserves negative secondary-display coordinates', async () => {
  const provider = new FakeProvider();
  provider.targets[1] = {
    ...provider.targets[1], bounds: { x: -1756, y: -1014, width: 1512, height: 949 },
  };
  provider.hitTest = async (target, point, preferredActions, perform) => {
    provider.hitTests = provider.hitTests || [];
    provider.hitTests.push({ target, point, preferredActions, perform });
    return {
      ok: true, found: true, performed: perform, verified: perform,
      action: 'press', nativeAction: 'AXPress', diagnostics: { method: 'fake', treeScanUsed: false },
    };
  };
  const manager = new AppUseManager({ provider, ownPid: 999 });
  const listed = await manager.handle('list_targets', {});
  const connected = await manager.handle('connect', {
    target_id: listed.targets.find((target) => target.app_name === 'TextEdit').target_id,
    parameters: {},
  });
  const result = await manager.handle('call', {
    session_id: connected.session_id,
    capability: 'virtual_click_at',
    parameters: { x: 756, y: 475, coordinate_space: 'window' },
  });
  assert.equal(result.status, 'success');
  assert.deepEqual(provider.hitTests[0].point, { x: -1000, y: -539 });
});

test('rejects unknown capability parameters before any desktop action', async () => {
  const { manager, provider, connected } = await connectedManager();
  const result = await manager.handle('call', {
    session_id: connected.session_id,
    capability: 'virtual_click_at',
    parameters: { x: 80, y: 45, keyboard_shortcut: ['command', 't'] },
  });
  assert.equal(result.status, 'error');
  assert.equal(result.type, 'invalid_arguments');
  assert.match(result.message, /keyboard_shortcut/);
  assert.deepEqual(provider.performed, []);
});

test('coordinate scroll accepts large distances and rejects invalid amounts before input', async () => {
  const { manager, provider, connected } = await connectedManager();
  for (const amount of [10_000, 40_000, 50_000]) {
    const accepted = await manager.handle('call', {
      session_id: connected.session_id,
      capability: 'scroll_at',
      parameters: { x: 80, y: 45, direction: 'down', amount, allow_foreground_input: true },
    });
    assert.equal(accepted.status, 'success');
    assert.equal(provider.performed.at(-1).parameters.amount, amount);
  }

  const performedCount = provider.performed.length;
  for (const amount of [0, 1.5, 50_001, '100']) {
    const rejected = await manager.handle('call', {
      session_id: connected.session_id,
      capability: 'scroll_at',
      parameters: { x: 80, y: 45, direction: 'down', amount, allow_foreground_input: true },
    });
    assert.equal(rejected.status, 'error');
    assert.equal(rejected.type, 'invalid_arguments');
    assert.match(rejected.message, /integer from 1 to 50000/);
  }
  assert.equal(provider.performed.length, performedCount);
});

test('background menu command reports the exact AX action without foreground input', async () => {
  const { manager, provider, connected } = await connectedManager();
  const result = await manager.handle('call', {
    session_id: connected.session_id,
    capability: 'menu_command',
    parameters: { name: 'New Tab', shortcut: ['command', 't'], verify_effect: false },
  });
  assert.equal(result.status, 'success');
  assert.equal(result.executed_action.capability, 'menu_command');
  assert.equal(result.executed_action.native_action, 'AXPress');
  assert.equal(result.foreground_input_used, false);
  assert.equal(result.real_cursor_moved, false);
  assert.equal(result.focus_requested, false);
  assert.deepEqual(provider.focused, []);
  assert.equal(provider.menuCommands.length, 2);
});

test('virtual coordinate activation verifies its UI effect with a capture diff', async () => {
  const provider = new FakeProvider();
  const manager = new AppUseManager({
    provider,
    ownPid: 999,
    captureTarget: async () => ({
      imageBase64: Buffer.from(provider.performed.length ? 'after' : 'before').toString('base64'),
      mimeType: 'image/png', width: 800, height: 600,
    }),
  });
  const listed = await manager.handle('list_targets', {});
  const connected = await manager.handle('connect', {
    target_id: listed.targets.find((target) => target.app_name === 'TextEdit').target_id,
    parameters: {},
  });
  const result = await manager.handle('call', {
    session_id: connected.session_id,
    capability: 'virtual_click_at',
    parameters: { x: 80, y: 45 },
  });
  assert.equal(result.status, 'success');
  assert.equal(result.verification.action_accepted, true);
  assert.equal(result.verification.effect_verified, true);
  assert.equal(result.verification.effect_verification.method, 'window_capture_diff');
});

test('virtual type routes text only to the target pid and preserves cursor and foreground', async () => {
  const provider = new FakeProvider();
  provider.pidEvent = async (target, operation, point, parameters, perform) => {
    provider.pidEvents = provider.pidEvents || [];
    provider.pidEvents.push({ target, operation, point, parameters, perform });
    if (perform) provider.performed.push({ capability: operation });
    return {
      ok: true, found: true, performed: perform, verified: perform,
      realCursorMoved: false, foregroundAffected: false,
      diagnostics: { method: 'CGEventPostToPid', backgroundSafe: true },
    };
  };
  const manager = new AppUseManager({ provider, ownPid: 999 });
  const listed = await manager.handle('list_targets', {});
  const connected = await manager.handle('connect', {
    target_id: listed.targets.find((target) => target.app_name === 'TextEdit').target_id,
    parameters: {},
  });
  const result = await manager.handle('call', {
    session_id: connected.session_id,
    capability: 'virtual_type_at',
    parameters: { x: 400, y: 300, text: 'hello' },
  });
  assert.equal(result.status, 'uncertain');
  assert.equal(result.executed_action.native_action, 'CGEventPostToPid');
  assert.equal(result.executed_action.text_length, 5);
  assert.equal(result.real_cursor_moved, false);
  assert.equal(result.foreground_affected, false);
  assert.equal(result.verification.event_delivered, true);
  assert.deepEqual(provider.focused, []);
  assert.equal(provider.pidEvents.length, 1);
});

test('virtual type never treats an unrelated full-window repaint as verified text insertion', async () => {
  const provider = new FakeProvider();
  provider.pidEvent = async (_target, _operation, _point, _parameters, perform) => {
    if (perform) provider.performed.push({ capability: 'pid_type_at' });
    return { ok: true, found: true, performed: perform, verified: perform, realCursorMoved: false, foregroundAffected: false };
  };
  const manager = new AppUseManager({
    provider,
    ownPid: 999,
    captureTarget: async () => ({
      imageBase64: Buffer.from(provider.performed.length ? 'dynamic-after' : 'before').toString('base64'),
      mimeType: 'image/png', width: 800, height: 600,
    }),
  });
  const listed = await manager.handle('list_targets', {});
  const connected = await manager.handle('connect', {
    target_id: listed.targets.find((target) => target.app_name === 'TextEdit').target_id,
    parameters: {},
  });
  const result = await manager.handle('call', {
    session_id: connected.session_id,
    capability: 'virtual_type_at',
    parameters: { x: 400, y: 300, text: 'hello' },
  });
  assert.equal(result.status, 'uncertain');
  assert.equal(result.verification.effect_verified, null);
  assert.equal(result.verification.visual_changed, true);
});

test('whole-window AX group is rejected so coordinate click reaches the target pid fallback', async () => {
  const provider = new FakeProvider();
  provider.hitTest = async (target, point, preferredActions, perform) => {
    provider.hitTests = provider.hitTests || [];
    provider.hitTests.push({ target, point, preferredActions, perform });
    return {
      ok: true, found: true, performed: perform, verified: perform,
      action: 'press', nativeAction: 'AXPress', role: 'Group', name: '',
      bounds: { ...target.bounds }, diagnostics: { method: 'fake whole-window AXGroup' },
    };
  };
  provider.pidEvent = async (target, operation, point, parameters, perform) => {
    provider.pidEvents = provider.pidEvents || [];
    provider.pidEvents.push({ target, operation, point, parameters, perform });
    return {
      ok: true, found: true, performed: perform, verified: perform,
      realCursorMoved: false, foregroundAffected: false,
      diagnostics: { method: 'CGEventPostToPid' },
    };
  };
  const manager = new AppUseManager({ provider, ownPid: 999 });
  const listed = await manager.handle('list_targets', {});
  const connected = await manager.handle('connect', {
    target_id: listed.targets.find((target) => target.app_name === 'TextEdit').target_id,
    parameters: {},
  });
  const result = await manager.handle('call', {
    session_id: connected.session_id,
    capability: 'virtual_click_at',
    parameters: { x: 400, y: 300 },
  });
  assert.equal(result.status, 'uncertain');
  assert.equal(result.executed_action.input_mode, 'background_pid_event');
  assert.equal(result.diagnostics.ax_candidate_rejected.reason, 'degenerate_window_container');
  assert.equal(provider.hitTests.length, 1);
  assert.equal(provider.hitTests[0].perform, false);
  assert.equal(provider.pidEvents.length, 1);
  assert.equal(provider.pidEvents[0].operation, 'pid_click_at');
});

test('real pointer and focus-dependent input require explicit foreground authorization', async () => {
  const { manager, provider, connected } = await connectedManager();
  const result = await manager.handle('call', {
    session_id: connected.session_id,
    capability: 'hover_at',
    parameters: { x: 400, y: 300, coordinate_space: 'window' },
  });
  assert.equal(result.status, 'error');
  assert.equal(result.type, 'foreground_input_not_allowed');
  assert.deepEqual(provider.focused, []);
  assert.deepEqual(provider.performed, []);
});

test('focus failure preserves Windows diagnostics and remediation', async () => {
  const provider = new FakeProvider();
  provider.targets = provider.targets.map((target) => ({ ...target, platform: 'win32' }));
  provider.focusTarget = async () => ({
    ok: true,
    verified: false,
    diagnostics: { method: 'SetForegroundWindow+AttachThreadInput', sameIntegrityLevelRequired: true },
  });
  const manager = new AppUseManager({ provider, ownPid: 999 });
  const listed = await manager.handle('list_targets', {});
  const connected = await manager.handle('connect', {
    target_id: listed.targets.find((target) => target.app_name === 'TextEdit').target_id,
    parameters: {},
  });
  const result = await manager.handle('call', {
    session_id: connected.session_id,
    capability: 'focus_window',
    parameters: {},
  });
  assert.equal(result.status, 'error');
  assert.equal(result.type, 'focus_failed');
  assert.equal(result.retryable, true);
  assert.equal(result.diagnostics.sameIntegrityLevelRequired, true);
  assert.match(result.remediation, /same Windows integrity level/i);
});

test('focus policy never allows verified background text value writes', async () => {
  const { manager, connected } = await connectedManager({ focusPolicy: 'never' });
  const snapshot = await manager.handle('call', {
    session_id: connected.session_id, capability: 'snapshot', parameters: {},
  });
  const body = snapshot.nodes.find((node) => node.name === 'Body');
  const result = await manager.handle('call', {
    session_id: connected.session_id,
    capability: 'type_text',
    parameters: { ref: body.ref, text: 'hello' },
  });
  assert.equal(result.status, 'success');
  assert.equal(result.focused_temporarily, false);
  assert.equal(result.verification.nodes.find((node) => node.name === 'Body').value, 'drafthello');
});

test('when_required does not focus for semantic text writes', async () => {
  const { manager, provider, connected } = await connectedManager();
  const snapshot = await manager.handle('call', {
    session_id: connected.session_id, capability: 'snapshot', parameters: {},
  });
  const body = snapshot.nodes.find((node) => node.name === 'Body');
  const result = await manager.handle('call', {
    session_id: connected.session_id,
    capability: 'type_text',
    parameters: { ref: body.ref, text: 'hello' },
  });
  assert.equal(result.status, 'success');
  assert.equal(result.focused_temporarily, false);
  assert.deepEqual(provider.focused, []);
});

test('keyboard shortcut is uncertain and restores prior foreground', async () => {
  const { manager, provider, connected } = await connectedManager();
  provider.perform = async () => ({ ok: true, verified: false, uncertain: true, summary: 'sent' });
  const result = await manager.handle('call', {
    session_id: connected.session_id,
    capability: 'key_chord',
    parameters: { keys: ['escape'], allow_foreground_input: true },
  });
  assert.equal(result.status, 'uncertain');
  assert.equal(result.focused_temporarily, true);
  assert.deepEqual(provider.focused, ['200', '100']);
});

test('keyboard shortcut restores Cyrene when the host was foreground', async () => {
  const provider = new FakeProvider();
  provider.targets = provider.targets.map((target) => ({ ...target, foreground: false }));
  provider.perform = async () => ({ ok: true, verified: false, uncertain: true, summary: 'sent' });
  let hostFocusCount = 0;
  const manager = new AppUseManager({
    provider,
    ownPid: 999,
    isHostForeground: () => true,
    focusHost: async () => { hostFocusCount += 1; },
  });
  const listed = await manager.handle('list_targets', {});
  const connected = await manager.handle('connect', {
    target_id: listed.targets.find((target) => target.app_name === 'TextEdit').target_id,
    parameters: { focus_policy: 'when_required' },
  });
  const result = await manager.handle('call', {
    session_id: connected.session_id, capability: 'key_chord', parameters: { keys: ['escape'], allow_foreground_input: true },
  });
  assert.equal(result.status, 'uncertain');
  assert.equal(hostFocusCount, 1);
  assert.equal(result.focus_restore.summary, 'Restored focus to Cyrene.');
});

test('excludes every Cyrene instance by application identity and name', async () => {
  const provider = new FakeProvider();
  provider.targets.push(
    { ...provider.targets[0], pid: 30, windowId: '300', appName: 'Cyrene', applicationId: 'com.cyrene.app' },
    { ...provider.targets[0], pid: 31, windowId: '301', appName: 'Cyrene', applicationId: 'dev.cyrene.app' },
  );
  const manager = new AppUseManager({ provider, ownPid: 999 });
  const result = await manager.handle('list_targets', {});
  assert.deepEqual(result.targets.map((target) => target.app_name), ['Notes', 'TextEdit']);
  assert.ok(provider.lastExclusions.excludeApplicationIds.includes('com.cyrene.app'));
  assert.ok(provider.lastExclusions.excludeAppNames.includes('cyrene'));
});

test('provider unverifiable actions are never reported as success', async () => {
  const { manager, provider, connected } = await connectedManager();
  const snapshot = await manager.handle('call', {
    session_id: connected.session_id, capability: 'snapshot', parameters: {},
  });
  provider.perform = async () => ({ ok: true, verified: false, summary: 'attempted' });
  const save = snapshot.nodes.find((node) => node.name === 'Save');
  const result = await manager.handle('call', {
    session_id: connected.session_id, capability: 'press', parameters: { ref: save.ref },
  });
  assert.equal(result.status, 'uncertain');
});

test('coordinate actions focus once, restore, and report screenshot differences', async () => {
  const provider = new FakeProvider();
  provider.perform = async (target, capability, nativeRef, parameters) => ({
    ok: true, verified: true, skipSnapshot: true, visualChangeExpected: true,
    summary: `${capability} injected`, diagnostics: { parameters },
  });
  let captureCount = 0;
  const manager = new AppUseManager({
    provider,
    ownPid: 999,
    captureTarget: async () => ({
      imageBase64: Buffer.from(`frame-${captureCount += 1}`).toString('base64'), width: 800, height: 600,
    }),
  });
  const listed = await manager.handle('list_targets', {});
  const connected = await manager.handle('connect', {
    target_id: listed.targets.find((target) => target.app_name === 'TextEdit').target_id,
    parameters: { focus_policy: 'when_required' },
  });
  const result = await manager.handle('call', {
    session_id: connected.session_id,
    capability: 'click_at',
    parameters: { x: 20, y: 30, allow_foreground_input: true },
  });
  assert.equal(result.status, 'success');
  assert.equal(result.visual_verification.available, true);
  assert.equal(result.visual_verification.changed, true);
  assert.deepEqual(provider.focused, ['200', '100']);
});

test('coordinate action with no visual change is uncertain', async () => {
  const provider = new FakeProvider();
  provider.perform = async () => ({
    ok: true, verified: true, skipSnapshot: true, visualChangeExpected: true, summary: 'injected',
  });
  const manager = new AppUseManager({
    provider,
    ownPid: 999,
    captureTarget: async () => ({ imageBase64: Buffer.from('same-frame').toString('base64'), width: 800, height: 600 }),
  });
  const listed = await manager.handle('list_targets', {});
  const connected = await manager.handle('connect', {
    target_id: listed.targets.find((target) => target.app_name === 'TextEdit').target_id,
    parameters: { focus_policy: 'when_required' },
  });
  const result = await manager.handle('call', {
    session_id: connected.session_id, capability: 'right_click', parameters: { x: 20, y: 30, allow_foreground_input: true },
  });
  assert.equal(result.status, 'uncertain');
  assert.equal(result.visual_verification.changed, false);
});

test('selection and atomic key sequence each use one temporary focus interval', async () => {
  const { manager, provider, connected } = await connectedManager();
  const snapshot = await manager.handle('call', {
    session_id: connected.session_id, capability: 'snapshot', parameters: {},
  });
  const body = snapshot.nodes.find((node) => node.name === 'Body');
  const selection = await manager.handle('call', {
    session_id: connected.session_id,
    capability: 'set_selection_range',
    parameters: { ref: body.ref, start: 0, end: 2, allow_foreground_input: true },
  });
  assert.equal(selection.status, 'success');
  assert.deepEqual(provider.focused, ['200', '100']);
  provider.focused.length = 0;
  const sequence = await manager.handle('call', {
    session_id: connected.session_id,
    capability: 'key_sequence',
    parameters: { allow_foreground_input: true, steps: [{ type: 'shortcut', keys: ['command', 'l'] }, { type: 'text', text: 'example.com' }, { type: 'key', key: 'return' }] },
  });
  assert.equal(sequence.focused_temporarily, true);
  assert.deepEqual(provider.focused, ['200', '100']);
});

test('wait observes a later semantic value', async () => {
  const { manager, provider, connected } = await connectedManager();
  const snapshot = await manager.handle('call', {
    session_id: connected.session_id, capability: 'snapshot', parameters: {},
  });
  const body = snapshot.nodes.find((node) => node.name === 'Body');
  setTimeout(() => { provider.value = 'ready'; }, 40);
  const result = await manager.handle('call', {
    session_id: connected.session_id,
    capability: 'wait',
    parameters: { ref: body.ref, property: 'value', equals: 'ready', timeout_ms: 1000 },
  });
  assert.equal(result.status, 'success');
  assert.equal(result.wait_status, 'matched');
});

test('closed target invalidates the session', async () => {
  const { manager, provider, connected } = await connectedManager();
  provider.targets = provider.targets.filter((target) => target.pid !== 20);
  const result = await manager.handle('status', { session_id: connected.session_id });
  assert.equal(result.status, 'error');
  assert.equal(result.type, 'stale_session');
});

test('quick chat origin captures the external foreground target', async () => {
  const provider = new FakeProvider();
  const manager = new AppUseManager({ provider, ownPid: 999 });
  const targetId = await manager.captureQuickChatOrigin();
  const listed = await manager.listTargets();
  assert.equal(targetId, listed.selection_hints.quick_chat_origin);
  assert.equal(listed.targets.find((target) => target.target_id === targetId).app_name, 'Notes');
});
