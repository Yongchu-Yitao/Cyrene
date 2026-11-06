import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'
import vm from 'node:vm'

const source = readFileSync(new URL('./rail.jsx', import.meta.url), 'utf8');
const revealSource = 'function revealRailMenu(actions) {' + source.split('function revealRailMenu(actions) {')[1].split('\n  useWbcEffect')[0];

function reveal({ bottom = 140, top = 116, connected = true, popover = true } = {}) {
  let shown = 0;
  let scrolled = 0;
  const menu = {
    style: {},
    hasAttribute: () => popover,
    showPopover: () => { shown += 1; },
    getBoundingClientRect: () => ({ width: 160, height: 100 }),
    scrollIntoView: () => { scrolled += 1; },
  };
  const actions = {
    isConnected: connected,
    querySelector: () => menu,
    getBoundingClientRect: () => ({ top, bottom, right: 260 }),
  };
  const context = { requestAnimationFrame: callback => callback(), document: { documentElement: { clientWidth: 300, clientHeight: 400 } }, actions };
  vm.runInNewContext(revealSource + '\nrevealRailMenu(actions);', context);
  return { shown, scrolled, style: menu.style };
}

test('group menu opens below its anchor without scrolling the conversation list', () => {
  assert.deepEqual(reveal(), { shown: 1, scrolled: 0, style: { left: '100px', top: '144px' } });
});

test('group menu flips above an anchor near the window bottom without scrolling', () => {
  assert.deepEqual(reveal({ top: 350, bottom: 374 }), { shown: 1, scrolled: 0, style: { left: '100px', top: '246px' } });
});

test('closed or removed menu anchors are ignored', () => {
  assert.deepEqual(reveal({ connected: false }), { shown: 0, scrolled: 0, style: {} });
});

test('ordinary chat menus retain their existing reveal behavior', () => {
  assert.deepEqual(reveal({ popover: false }), { shown: 0, scrolled: 1, style: {} });
});
