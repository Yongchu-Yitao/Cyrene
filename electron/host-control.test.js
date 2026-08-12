'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const { HostControl } = require('./host-control');

class FakeWebContents {
  constructor() { this.destroyed = false; this.listeners = {}; this.sent = []; }
  isDestroyed() { return this.destroyed; }
  once(name, callback) { this.listeners[name] = callback; }
  send(channel, payload) { this.sent.push({ channel, payload }); }
}

class FakeWindow {
  constructor(kind) {
    this.kind = kind;
    this.webContents = new FakeWebContents();
    this.focused = true;
    this.visible = true;
    this.maximized = false;
    this.minimized = false;
    this.fullscreen = false;
    this.bounds = { x: 0, y: 0, width: 1000, height: 800 };
  }
  isDestroyed() { return false; }
  isFocused() { return this.focused; }
  isVisible() { return this.visible; }
  isMaximized() { return this.maximized; }
  isMinimized() { return this.minimized; }
  isFullScreen() { return this.fullscreen; }
  getBounds() { return this.bounds; }
  getMinimumSize() { return [300, 240]; }
  show() { this.visible = true; }
  focus() { this.focused = true; }
  hide() { this.visible = false; }
  minimize() { this.minimized = true; }
  maximize() { this.maximized = true; }
  unmaximize() { this.maximized = false; }
  restore() { this.minimized = false; }
  setFullScreen(value) { this.fullscreen = !!value; }
  setBounds(value) { this.bounds = value; }
}

function fixture(lifecycleExecutor) {
  const main = new FakeWindow('main');
  const quick = new FakeWindow('quick_chat');
  const control = new HostControl({
    app: { getVersion: () => '0.7.4' },
    screen: { getDisplayMatching: () => ({ workArea: { x: 0, y: 0, width: 1200, height: 900 } }) },
    getMainWindow: () => main,
    getQuickChatWindow: () => quick,
    revealMainWindow: async () => { main.show(); main.focus(); },
    openQuickChat: async () => { quick.show(); quick.focus(); },
    getDesktopSettings: () => ({ settingsRevision: 0 }),
    updateDesktopSettings: () => ({ settingsRevision: 1 }),
    lifecycleExecutor,
  });
  return { control, main, quick };
}

test('surface registration and window control stay bound to the current main or quick-chat renderer', async () => {
  const { control, main, quick } = fixture();
  assert.deepEqual(control.registerSurface('main-id', main.webContents), {
    ok: true, uiInstanceId: 'main-id', surfaceKind: 'main',
  });
  assert.deepEqual(control.registerSurface('quick-id', quick.webContents), {
    ok: true, uiInstanceId: 'quick-id', surfaceKind: 'quick_chat',
  });
  assert.equal(control.registerSurface('foreign', new FakeWebContents()).ok, false);

  const maximized = await control.handle('window.control', {
    uiInstanceId: 'quick-id', action: 'maximize',
  });
  assert.equal(maximized.ok, true);
  assert.equal(quick.maximized, true);
  assert.equal(main.maximized, false);

  quick.focused = false;
  const snapshotPromise = control.handle('ui.snapshot.current', {
    uiInstanceId: 'quick-id',
  });
  const requestId = quick.webContents.sent[0].payload.requestId;
  control.receiveSurfaceResponse({
    requestId, result: { ok: true, snapshot_id: 'quick-tree' },
  }, quick.webContents);
  assert.deepEqual(await snapshotPromise, { ok: true, snapshot_id: 'quick-tree' });

  const status = await control.handle('host.status', { uiInstanceId: 'quick-id' });
  assert.equal(status.surfaceAvailable, true);
  assert.equal(status.surfaceKind, 'quick_chat');
  assert.equal(status.window.focused, false);

  quick.visible = false;
  const restored = await control.handle('window.control', {
    uiInstanceId: 'quick-id', action: 'reveal',
  });
  assert.equal(restored.ok, true);
  assert.equal(quick.visible, true);
  assert.equal(quick.focused, true);
});

test('stale renderer destruction cannot unregister a replacement surface', () => {
  const { control, main } = fixture();
  const oldContents = main.webContents;
  control.registerSurface('main-id', oldContents);

  const replacement = new FakeWebContents();
  main.webContents = replacement;
  control.registerSurface('main-id', replacement);
  oldContents.listeners.destroyed();

  const surface = control.resolveSurface('main-id');
  assert.equal(surface.webContents, replacement);
});

test('surface unregister is owner-bound and resolves outstanding requests', async () => {
  const { control, main } = fixture();
  const foreign = new FakeWebContents();
  control.registerSurface('main-id', main.webContents);
  const pending = control.requestSurface('main-id', 'snapshot', {});

  assert.equal(control.unregisterSurface('main-id', foreign).ok, false);
  assert.deepEqual(control.unregisterSurface('main-id', main.webContents), {
    ok: true, uiInstanceId: 'main-id',
  });
  assert.deepEqual(await pending, { ok: false, error: 'surface_disposed' });
  assert.equal(control.resolveSurface('main-id'), null);
});

test('lifecycle execution requires exact version and canonical parameter hash', async () => {
  const accepted = [];
  const { control } = fixture((actionId, action, receipt) => {
    accepted.push({ actionId, action, receipt });
    return { ok: true };
  });
  const base = {
    actionId: `host_action_${'a'.repeat(32)}`,
    action: 'restart_app',
    parameterHash: 'b'.repeat(64),
    expectedAppVersion: '0.7.4',
  };
  assert.equal((await control.handle('lifecycle.execute_approved', base)).ok, true);
  assert.equal(accepted.length, 1);
  assert.equal((await control.handle('lifecycle.execute_approved', {
    ...base, expectedAppVersion: '9.9.9',
  })).error, 'app_version_drift');
  assert.equal((await control.handle('lifecycle.execute_approved', {
    ...base, parameterHash: 'not-a-hash',
  })).error, 'invalid_parameter_hash');

  const update = { ...base, action: 'update_install' };
  assert.equal((await control.handle('lifecycle.execute_approved', {
    ...update, phase: 'commit',
  })).error, 'update_install_not_prepared');
  assert.equal((await control.handle('lifecycle.execute_approved', {
    ...update, phase: 'prepare',
  })).ok, true);
  assert.equal(accepted.length, 1);
  assert.equal((await control.handle('lifecycle.execute_approved', {
    ...update, phase: 'commit',
  })).ok, true);
  assert.equal(accepted.length, 2);
});

test('surface acknowledgements are accepted only from the renderer that received the request', async () => {
  const { control, main } = fixture();
  const foreign = new FakeWebContents();
  control.registerSurface('main-id', main.webContents);
  const resultPromise = control.requestSurface('main-id', 'snapshot', {});
  const requestId = main.webContents.sent[0].payload.requestId;

  control.receiveSurfaceResponse({ requestId, result: { ok: true, source: 'foreign' } }, foreign);
  control.receiveSurfaceResponse({ requestId, result: { ok: true, source: 'main' } }, main.webContents);

  assert.deepEqual(await resultPromise, { ok: true, source: 'main' });
});
