const assert = require('node:assert/strict');
const path = require('node:path');
const test = require('node:test');

const {
  AppUseManager,
  CAPABILITIES,
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
      },
      {
        platform: 'darwin', pid: 20, processStartTime: '2', appName: 'TextEdit', applicationId: 'textedit',
        windowId: '200', windowIndex: 0, windowTitle: 'Background document', foreground: false, minimized: false,
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
        { nativeRef: prefix, role: 'Window', name: target.windowTitle, enabled: true, actions: [] },
        { nativeRef: `${prefix}/e0`, role: 'TextField', name: 'Body', value: this.value, enabled: true, actions: ['set_value'] },
        { nativeRef: `${prefix}/e1`, role: 'Button', name: 'Save', enabled: true, actions: ['press'] },
      ],
    };
  }

  async inspect(target, nativeRef) {
    return { ok: true, nodes: [{ nativeRef, role: 'Button', name: 'Save', enabled: true, actions: ['press'] }] };
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
  assert.deepEqual(connected.capabilities, CAPABILITIES);
  assert.ok(connected.capabilities.some((item) => item.name === 'snapshot'));
  assert.ok(connected.capabilities.some((item) => item.name === 'key_chord'));
  assert.ok(connected.capabilities.some((item) => item.name === 'click_at'));
  assert.ok(connected.capabilities.some((item) => item.name === 'drag'));
  assert.ok(connected.capabilities.some((item) => item.name === 'select_text'));
  assert.ok(connected.capabilities.some((item) => item.name === 'key_sequence'));
  assert.ok(connected.capabilities.some((item) => item.name === 'visual_describe'));
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
    parameters: { keys: ['escape'] },
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
    parameters: {},
  });
  const result = await manager.handle('call', {
    session_id: connected.session_id, capability: 'key_chord', parameters: { keys: ['escape'] },
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
    parameters: {},
  });
  const result = await manager.handle('call', {
    session_id: connected.session_id,
    capability: 'click_at',
    parameters: { x: 20, y: 30 },
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
    parameters: {},
  });
  const result = await manager.handle('call', {
    session_id: connected.session_id, capability: 'right_click', parameters: { x: 20, y: 30 },
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
    parameters: { ref: body.ref, start: 0, end: 2 },
  });
  assert.equal(selection.status, 'success');
  assert.deepEqual(provider.focused, ['200', '100']);
  provider.focused.length = 0;
  const sequence = await manager.handle('call', {
    session_id: connected.session_id,
    capability: 'key_sequence',
    parameters: { steps: [{ type: 'shortcut', keys: ['command', 'l'] }, { type: 'text', text: 'example.com' }, { type: 'key', key: 'return' }] },
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
