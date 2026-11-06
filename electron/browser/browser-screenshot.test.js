const assert = require('node:assert/strict');
const test = require('node:test');
const fs = require('node:fs');
const vm = require('node:vm');

// Exercise the actual manager method without booting Electron or navigating.
const source = fs.readFileSync(require.resolve('../main.js'), 'utf8');
const method = source.slice(source.indexOf('  async screenshot('), source.indexOf('\n  goBack()', source.indexOf('  async screenshot(')));
const screenshot = vm.runInNewContext(`({${method}}).screenshot`, {
  Buffer,
  browserScreenshot: require('./browser-screenshot').browserScreenshot,
  PAGE_CSS_TARGET_WIDTH: 1100,
  validatePngBuffer(buffer) {
    if (!buffer.length) throw Error('screenshot data is empty');
    if (buffer.toString() === 'tiny') return { byteLength: buffer.length, width: 2, height: 2 };
    return { byteLength: buffer.length, width: 800, height: 600 };
  },
});
function manager({ data = 'cG5n', cdpError = false, nativeData = '', firstViewportTiny = false } = {}) {
  const commands = [];
  let nativeCalls = 0;
  const tab = { id: 'a', view: { webContents: {
    getTitle: () => 'page', getURL: () => 'https://example.com',
    async capturePage() { nativeCalls++; return { toPNG: () => Buffer.from(nativeData) }; },
  } } };
  return {
    activeTabId: 'a', tabs: new Map([['a', tab]]), commands,
    get nativeCalls() { return nativeCalls; },
    async _ensureDebugger() { return { async sendCommand(name, args) {
      commands.push({ name, args });
      if (cdpError) throw Error('debugger unavailable');
      return name === 'Page.getLayoutMetrics'
        ? { cssVisualViewport: { clientWidth: firstViewportTiny && commands.length === 1 ? 1 : 800, clientHeight: 600, pageX: 0, pageY: 50 } }
        : { data };
    } }; },
  };
}
test('default screenshot captures a detached page without relying on its empty native bitmap', async () => {
  const m = manager();
  const result = await screenshot.call(m);
  assert.equal(result.ok, true);
  assert.equal(m.nativeCalls, 0);
  assert.equal(m.commands[1].args.clip.scale, 1);
  assert.equal(m.commands[1].args.clip.y, 50);
});
test('the first tiny viewport is staged and restored before returning the screenshot', async () => {
  const m = manager({ firstViewportTiny: true });
  const changes = [];
  const original = { x: 0, y: 0, width: 1100, height: 800 };
  m.tabs.get('a').view.getBounds = () => original;
  m.tabs.get('a').view.setBounds = bounds => changes.push(bounds);
  m.tabs.get('a').view.setVisible = visible => changes.push(visible);
  m.attachedTabId = 'a';
  m.surfaceWindow = () => ({ getContentBounds: () => ({ width: 1200, height: 900 }) });
  m.settlePageViewport = async () => true;
  m.syncAttachedView = () => changes.push('restored');
  assert.equal((await screenshot.call(m)).ok, true);
  assert.equal(m.commands.at(-1).args.clip.width, 800);
  assert.equal(changes[0].x, 1199);
  assert.deepEqual(changes.slice(-3), [false, original, 'restored']);
});
test('high resolution keeps bounded scaling and CDP failure falls back to native capture', async () => {
  const m = manager();
  assert.equal((await screenshot.call(m, { highResolution: true, targetWidth: 9999 })).ok, true);
  assert.equal(m.commands[1].args.clip.scale, 4);
  const fallback = manager({ cdpError: true, nativeData: 'png' });
  assert.equal((await screenshot.call(fallback)).ok, true);
  assert.equal(fallback.nativeCalls, 1);
});
test('empty captures and missing tabs remain failures', async () => {
  const m = manager({ data: '' });
  assert.equal((await screenshot.call(m)).ok, false);
  assert.equal((await screenshot.call(m, { tabId: 'missing' })).ok, false);
  assert.equal((await screenshot.call(manager({ data: Buffer.from('tiny').toString('base64') }))).ok, false);
});

const syncMethod = source.slice(source.indexOf('  syncAttachedView() {'), source.indexOf('\n  async prepareBoundsTransition()', source.indexOf('  syncAttachedView() {')));
const syncAttachedView = vm.runInNewContext(`({${syncMethod}}).syncAttachedView`, {
  PAGE_CSS_TARGET_WIDTH: 1100,
  browserSessions: { activeBrowserSessionId: 'another-visible-task' },
});
test('an inactive task page is hosted hidden once, without changing the visible task or losing its viewport', () => {
  const mutations = [];
  const tab = { id: 'a', view: {
    setVisible: visible => mutations.push(['visible', visible]),
    setBounds: bounds => mutations.push(['bounds', bounds.width, bounds.height]),
  } };
  const win = { contentView: { addChildView: view => mutations.push(['attach', view]) } };
  const m = {
    sessionId: 'task', activeTabId: 'a', tabs: new Map([['a', tab]]), attachedTabId: '', attachedWindow: null,
    fullscreenTab: () => null, surfaceWindow: () => win,
    detachView() {}, hideChatOverlay() {}, tabPicker: { dismissTabPicker() {} },
  };
  syncAttachedView.call(m);
  assert.equal(m.attachedTabId, 'a');
  assert.equal(m.attachedWindow, win);
  assert.deepEqual(mutations.filter(x => x[0] === 'bounds'), [['bounds', 1100, 800]]);
  assert.equal(mutations.some(x => x[0] === 'visible' && x[1]), false);
  syncAttachedView.call(m);
  assert.equal(mutations.filter(x => x[0] === 'attach').length, 1);
  assert.equal(mutations.filter(x => x[0] === 'bounds').length, 1);
});

const initializeHiddenPageViewport = function (tab) {
  return require('./browser-screenshot').initializeHiddenPageViewport(this, tab, 1100);
};
test('new hidden pages initialize their layout before navigation returns and restore hosting on error', async () => {
  for (const fail of [false, true]) {
    const changes = [];
    const original = { x: 0, y: 0, width: 1100, height: 800 };
    const tab = { id: 'a', view: {
      webContents: { executeJavaScript: async () => ({ width: 1, height: 1 }) },
      getBounds: () => original,
      setBounds: bounds => changes.push(bounds), setVisible: value => changes.push(value),
    } };
    const m = {
      attachedTabId: 'a', surfaceWindow: () => ({ getContentBounds: () => ({ width: 1200, height: 900 }) }),
      async settlePageViewport() { if (fail) throw Error('view closed'); },
      syncAttachedView: () => changes.push('restored'),
    };
    const run = initializeHiddenPageViewport.call(m, tab);
    if (fail) await assert.rejects(run, /view closed/); else await run;
    assert.equal(changes[0].x, 1199);
    assert.deepEqual(changes.slice(-3), [false, original, 'restored']);
    changes.length = 0;
    tab.view.webContents.executeJavaScript = async () => ({ width: 1100, height: 800 });
    await initializeHiddenPageViewport.call(m, tab);
    assert.equal(changes.length, 0);
  }
});
