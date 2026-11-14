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

test('rail menu opens below its anchor without scrolling the conversation list', () => {
  assert.deepEqual(reveal(), { shown: 1, scrolled: 0, style: { left: '100px', top: '144px' } });
});

test('rail menu flips above an anchor near the window bottom without scrolling', () => {
  assert.deepEqual(reveal({ top: 350, bottom: 374 }), { shown: 1, scrolled: 0, style: { left: '100px', top: '246px' } });
});

test('closed or removed menu anchors are ignored', () => {
  assert.deepEqual(reveal({ connected: false }), { shown: 0, scrolled: 0, style: {} });
});

test('non-popover menus retain their existing reveal behavior', () => {
  assert.deepEqual(reveal({ popover: false }), { shown: 0, scrolled: 1, style: {} });
});

// Mount-based opening also covers right-click and UI-surface open_menu actions.
test('chat menus mount in the top layer regardless of the opening action', () => {
  assert.match(source, /popover="manual" data-cyrene-node-id="chat_context_menu"\s+ref=\{function \(menu\) \{ if \(menu\) revealRailMenu\(menu.parentElement\); \}\}/);
  const css = readFileSync(new URL('./chat.css', import.meta.url), 'utf8');
  assert.match(css, /\.wbc-chat-card-actions \.wb-card-menu\[popover\],\s*\.wbc-chat-group-actions \.wb-card-menu\[popover\] \{\s*position: fixed;/);
});
