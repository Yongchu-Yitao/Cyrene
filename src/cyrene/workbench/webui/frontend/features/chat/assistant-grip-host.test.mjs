import test from 'node:test';
import assert from 'node:assert/strict';
import { assistantMenuBounds, observeAssistantGrip, listenAssistantMenuDismiss } from '../shell/assistant-grip-host.mjs';
import { createAssistantGripPointer } from '../shell/assistant-grip-pointer.mjs';

test('menu top aligns with the ordinary pane grip and stays inside pane bounds', () => {
  for (const pane of [
    { left: 500, top: 70, right: 900, bottom: 750 },
    { left: 700, top: 320, right: 980, bottom: 550 },
    { left: -20, top: -10, right: 350, bottom: 900 },
  ]) {
    const anchor = { pane, x: (pane.left + pane.right) / 2, bottom: pane.top + 56 };
    const menu = assistantMenuBounds(anchor, { width: 1000, height: 800 });
    assert.equal(menu.top, Math.max(12, pane.top + 17));
    assert.ok(menu.left >= Math.max(0, pane.left) + 12);
    assert.ok(menu.left + menu.width <= Math.min(1000, pane.right) - 12);
    assert.ok(menu.top + menu.maxHeight <= Math.min(800, pane.bottom) - 12);
    assert.ok(menu.maxHeight > 0);
  }
});

test('portalled grip follows position, visibility and theme changes without idle polling', () => {
  const events = new EventTarget(), frames = new Map(), observations = [];
  let nextId = 0, mutation, left = 100, hidden = false, color = '#fff', disconnected = 0, themeReads = 0;
  const card = { isConnected: true, parentElement: null, closest: () => hidden ? {} : null,
    getBoundingClientRect: () => ({ left, top: 40, right: left + 400, bottom: 600 }) };
  const grip = { getBoundingClientRect: () => ({ left, top: 44, width: 400, height: 52, bottom: 96 }) };
  const win = { innerWidth: 1200, innerHeight: 800, performance: { now: () => 0 },
    addEventListener: (...args) => events.addEventListener(...args), removeEventListener: (...args) => events.removeEventListener(...args),
    requestAnimationFrame: fn => { frames.set(++nextId, fn); return nextId; }, cancelAnimationFrame: id => frames.delete(id),
    getComputedStyle: () => ({ 0: '--wb-card-bg', length: 1, getPropertyValue: () => { themeReads++; return color; }, fontFamily: 'Workbench font', visibility: 'visible' }),
    ResizeObserver: class { observe() {} disconnect() { disconnected++; } },
    MutationObserver: class { constructor(fn) { mutation = fn; } observe() {} disconnect() { disconnected++; } },
  };
  const flush = time => { const pending = [...frames.values()]; frames.clear(); pending.forEach(fn => fn(time)); };
  const stop = observeAssistantGrip(card, grip, value => observations.push(value), win);
  assert.equal(observations.at(-1).x, 300); assert.equal(frames.size, 0);
  left = 500; color = '#17181c'; mutation(); flush(0);
  assert.equal(observations.at(-1).x, 700); assert.equal(observations.at(-1).theme['--wb-card-bg'], color);
  const transition = new Event('transitionrun');
  Object.defineProperty(transition, 'target', {value: card});
  events.dispatchEvent(transition); left = 600; flush(100);
  assert.equal(observations.at(-1).x, 800); assert.equal(frames.size, 1);
  flush(700); assert.equal(frames.size, 0);
  assert.equal(themeReads, 2, 'geometry-only frames reuse the theme snapshot');
  events.dispatchEvent(new Event('transitionrun'));
  events.dispatchEvent(new Event('animationstart'));
  assert.equal(frames.size, 0, 'unrelated or decorative animations do not restart tracking');
  hidden = true; mutation(); flush(701); assert.equal(observations.at(-1).visible, false);
  stop(); assert.equal(disconnected, 2); assert.equal(frames.size, 0);
});

test('menu closes for window blur, native plugin interaction and hidden document', () => {
  const win = new EventTarget(), doc = new EventTarget(); let closes = 0;
  const stop = listenAssistantMenuDismiss(() => closes++, doc, win);
  win.dispatchEvent(new Event('blur')); win.dispatchEvent(new Event('cyrene:plugin-view-interaction'));
  doc.hidden = true; doc.dispatchEvent(new Event('visibilitychange')); assert.equal(closes, 3);
  stop(); win.dispatchEvent(new Event('blur')); assert.equal(closes, 3);
});

function pointerFixture({ singlePane = false, open = false } = {}) {
  const win = new EventTarget(), calls = [], pane = {}, panel = {};
  let captured = false, copied = 0;
  const button = { dataset: {}, setPointerCapture() { captured = true; }, hasPointerCapture: () => captured,
    releasePointerCapture() { captured = false; } };
  const grip = { dataset: {}, closest: () => pane, getBoundingClientRect: () => ({ left: 100, top: 40 }),
    querySelector: () => ({ appendChild() { copied++; } }) };
  const slot = { querySelector: () => ({ cloneNode: () => ({ remove() { copied--; } }) }) };
  const actions = { singlePane, onDragStart: event => calls.push(['pane', event.currentTarget, event.pointerCaptureTarget, copied]),
    onDragEnd: (_event, options) => calls.push(['end', options.cancel]) };
  const controller = createAssistantGripPointer(() => ({ open, actions, grip, slot, panel, onPaneDragging: value => calls.push(['dragging', value]) }), win,
    (_slot, _event, _win, bounds) => { calls.push(['spring', bounds]); return { move: () => calls.push(['move']), finish: () => calls.push(['finish']), destroy() {} }; });
  const event = { button: 0, pointerId: 1, currentTarget: button, clientX: 140, clientY: 60,
    preventDefault() {}, stopPropagation() {} };
  return { controller, calls, button, grip, pane, panel, win, event, copied: () => copied };
}

test('split logo starts the existing pane drag only after movement and suppresses click', () => {
  const f = pointerFixture(); f.controller.down(f.event);
  assert.equal(f.calls.some(c => c[0] === 'pane'), false);
  f.controller.move({ ...f.event, clientX: 160 }); f.controller.move({ ...f.event, clientX: 190 });
  assert.deepEqual(f.calls.filter(c => c[0] === 'pane'), [['pane', f.grip, f.button, 1]]);
  assert.equal(f.grip.dataset.wbcDragHandleX, '40'); assert.equal(f.copied(), 0);
  f.controller.release({ ...f.event, type: 'pointerup' });
  assert.equal(f.button.dataset.wbcPointerDragged, 'true');
  assert.equal(f.calls.some(c => c[0] === 'end'), false); // Existing document listener owns the drop.
  f.controller.destroy();
});

test('single pane and open menu retain elastic logo drag within their original surface', () => {
  for (const open of [false, true]) {
    const f = pointerFixture({ singlePane: !open, open }); f.controller.down(f.event);
    f.controller.move({ ...f.event, clientX: 160 });
    assert.deepEqual(f.calls.find(c => c[0] === 'spring'), ['spring', open ? f.panel : f.pane]);
    assert.equal(f.calls.some(c => c[0] === 'pane'), false);
    f.win.dispatchEvent(new Event('blur')); assert.ok(f.calls.some(c => c[0] === 'finish'));
    f.controller.destroy();
  }
});

test('lost capture cancels a pane drag and a later click starts cleanly', () => {
  const f = pointerFixture(); f.controller.down(f.event); f.controller.move({ ...f.event, clientX: 160 });
  f.controller.release({ ...f.event, type: 'lostpointercapture' });
  assert.deepEqual(f.calls.find(c => c[0] === 'end'), ['end', true]);
  f.controller.down(f.event); f.controller.release({ ...f.event, type: 'pointerup' });
  assert.equal(f.button.dataset.wbcPointerDragged, 'false'); f.controller.destroy();
});
