const assert = require('node:assert/strict');
const { EventEmitter } = require('node:events');
const test = require('node:test');

const {
  createSingleFlight,
  isAbortedNavigation,
  loadWindowUrl,
} = require('./main-window-lifecycle');

test('shares one in-flight main-window creation across concurrent reveal requests', async () => {
  const singleFlight = createSingleFlight();
  let creations = 0;
  let complete;
  const gate = new Promise((resolve) => { complete = resolve; });
  const create = () => singleFlight.run(async () => {
    creations += 1;
    await gate;
    return 'window';
  });

  const startup = create();
  const secondInstance = create();
  const trayReveal = create();
  complete();

  assert.deepEqual(
    await Promise.all([startup, secondInstance, trayReveal]),
    ['window', 'window', 'window'],
  );
  assert.equal(creations, 1);
});

test('invalidation permits a replacement without stale completion clearing it', async () => {
  const singleFlight = createSingleFlight();
  let finishStale;
  let finishReplacement;
  const stale = singleFlight.run(({ isCurrent }) => new Promise((resolve) => {
    finishStale = () => resolve(isCurrent());
  }));
  await Promise.resolve();

  singleFlight.invalidate();
  const replacement = singleFlight.run(({ isCurrent }) => new Promise((resolve) => {
    finishReplacement = () => resolve(isCurrent());
  }));
  await Promise.resolve();
  finishStale();
  assert.equal(await stale, false);

  const concurrentReplacement = singleFlight.run(() => 'unexpected second replacement');
  finishReplacement();
  assert.equal(await replacement, true);
  assert.equal(await concurrentReplacement, true);
});

test('recognizes Electron aborted-navigation errors without matching unrelated failures', () => {
  assert.equal(isAbortedNavigation(Object.assign(new Error('navigation'), { code: 'ERR_ABORTED' })), true);
  assert.equal(isAbortedNavigation(new Error('ERR_ABORTED (-3) loading local page')), true);
  assert.equal(isAbortedNavigation(Object.assign(new Error('connection refused'), { code: 'ERR_CONNECTION_REFUSED' })), false);
});

test('accepts ERR_ABORTED only after the replacement reaches the expected origin', async () => {
  class FakeContents extends EventEmitter {
    constructor() {
      super();
      this.url = '';
      this.loading = true;
    }

    isDestroyed() { return false; }
    isLoadingMainFrame() { return this.loading; }
    getURL() { return this.url; }
  }

  const contents = new FakeContents();
  const aborted = Object.assign(new Error('ERR_ABORTED (-3) loading local page'), { code: 'ERR_ABORTED' });
  const win = {
    webContents: contents,
    isDestroyed: () => false,
    loadURL: async () => { throw aborted; },
  };
  const loading = loadWindowUrl(win, 'http://127.0.0.1:4242/', { timeoutMs: 100 });

  contents.url = 'http://127.0.0.1:4242/conversations';
  contents.loading = false;
  contents.emit('did-finish-load');
  await loading;
});

test('does not hide an aborted navigation when no replacement page succeeds', async () => {
  const contents = new EventEmitter();
  contents.isDestroyed = () => false;
  contents.isLoadingMainFrame = () => true;
  contents.getURL = () => '';
  const aborted = Object.assign(new Error('ERR_ABORTED (-3) loading local page'), { code: 'ERR_ABORTED' });
  const win = {
    webContents: contents,
    isDestroyed: () => false,
    loadURL: async () => { throw aborted; },
  };

  await assert.rejects(
    loadWindowUrl(win, 'http://127.0.0.1:4242/', { timeoutMs: 5 }),
    (error) => error === aborted,
  );
});
