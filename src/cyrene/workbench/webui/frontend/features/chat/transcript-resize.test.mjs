import assert from 'node:assert/strict';
import test from 'node:test';
import { protectTranscriptResize } from './transcript-resize.mjs';

function surface() {
  const listeners = new Map();
  return {
    addEventListener(type, fn) { if (!listeners.has(type)) listeners.set(type, new Set()); listeners.get(type).add(fn); },
    dispatchEvent(event) { this.emit(event.type); },
    removeEventListener(type, fn) { listeners.get(type)?.delete(fn); },
    emit(type, extra = {}) { for (const fn of listeners.get(type) || []) fn({ type, target: this, ...extra }); },
    get listeners() { return [...listeners.values()].reduce((n, set) => n + set.size, 0); },
  };
}
function fixture(count = 30) {
  let selected = false, pip = false, sticking = false, restored = 0;
  const timers = new Map(), frames = new Map();
  const doc = surface();
  doc.defaultView = Object.assign(surface(), {
    requestAnimationFrame(fn) { frames.set(1, fn); return 1; },
    cancelAnimationFrame(id) { frames.delete(id); },
    setTimeout(fn) { timers.set(1, fn); return 1; },
    clearTimeout(id) { timers.delete(id); },
    Event: class { constructor(type) { this.type = type; } },
    getSelection() { return { isCollapsed: !selected }; },
  });
  const rows = Array.from({ length: count }, (_, i) => {
    const attrs = new Map(), styles = new Map();
    return {
      isConnected: true,
      querySelectorAll() { return []; },
      getBoundingClientRect() { return { top: (i - 15) * 100, bottom: (i - 14) * 100, height: 100 }; },
      hasAttribute(name) { return attrs.has(name); },
      setAttribute(name, value) { attrs.set(name, value); },
      removeAttribute(name) { attrs.delete(name); },
      style: { setProperty(name, value) { styles.set(name, value); }, removeProperty(name) { styles.delete(name); } },
      contains(el) { return el === this; },
      classList: { contains() { return false; } },
      attrs, styles,
    };
  });
  const thread = Object.assign(surface(), {
    ownerDocument: doc, children: rows, scrollTop: 1500, scrollHeight: 3000,
    getBoundingClientRect: () => ({ top: 0, bottom: 400, height: 400 }),
    querySelectorAll: () => rows,
  });
  const page = Object.assign(surface(), { querySelector: () => pip });
  const cleanup = protectTranscriptResize(thread, page, () => sticking, () => { restored++; });
  return {
    rows, thread, page, doc, timers, cleanup, frames,
    flushFrames() { while(frames.size) {const fn=frames.get(1);frames.delete(1);fn();} },
    get restored() { return restored; },
    start() { page.emit('transitionrun', { propertyName: 'grid-template-columns' }); },
    end(type = 'transitionend') { page.emit(type, { propertyName: 'grid-template-columns' }); this.flushFrames(); },
    frozen() { return rows.filter(row => row.hasAttribute('data-wbc-resize-frozen')); },
    select() { selected = true; }, pip() { pip = true; }, stick() { sticking = true; },
  };
}

test('only distant history is frozen; final layout restores the live tail', () => {
  const f = fixture();
  f.stick(); f.start();
  assert.ok(f.frozen().length > 0);
  assert.ok(!f.rows[15].attrs.size);
  assert.equal(f.rows[0].styles.get('--wbc-resize-row-height'), '100px');
  f.end();
  assert.equal(f.frozen().length, 0);
  assert.equal(f.thread.scrollTop, 3000);
  assert.equal(f.timers.size, 0);
  assert.ok(f.rows.every(row => !row.styles.size));
  f.cleanup();
});

test('reading anchor is compensated after frozen rows regain their natural height', () => {
  const f = fixture();
  f.start();
  const anchor = f.rows[15];
  anchor.getBoundingClientRect = () => ({ top: f.frozen().length ? 0 : 125, bottom: 225, height: 100 });
  f.end();
  assert.equal(f.thread.scrollTop, 1625);
  f.cleanup();
});

test('short history, selected text and focused rows stay live', () => {
  for (const configure of [f => f.select()]) {
    const f = fixture(); configure(f); f.start(); assert.equal(f.frozen().length, 0); f.cleanup();
  }
  const short = fixture(10);
  for (const row of short.rows) row.getBoundingClientRect = () => ({top:0,bottom:100,height:100});
  short.start(); assert.equal(short.frozen().length, 0); short.cleanup();
  const f = fixture(); f.doc.activeElement = f.rows[0]; f.start();
  assert.equal(f.rows[0].attrs.size, 0); f.cleanup();
});

test('interruptions, cancellation, timeout and cleanup cannot strand hidden messages', () => {
  for (const finish of [
    f => f.end('transitioncancel'), f => f.thread.emit('wheel'),
    f => f.doc.emit('keydown'), f => f.doc.emit('pointerdown'),
    f => f.timers.get(1)(), f => f.cleanup(),
  ]) {
    const f = fixture(); f.start(); assert.ok(f.frozen().length); finish(f);
    assert.equal(f.frozen().length, 0); f.cleanup();
    assert.equal(f.doc.listeners + f.thread.listeners + f.page.listeners, 0);
    assert.equal(f.timers.size, 0);
  }
});

test('unrelated transitions are ignored and repeated starts restore before freezing', () => {
  const f = fixture();
  f.page.emit('transitionrun', { propertyName: 'opacity' });
  f.page.emit('transitionrun', { propertyName: 'grid-template-columns', target: f.thread });
  assert.equal(f.frozen().length, 0);
  f.start(); const count = f.frozen().length; f.start();
  assert.equal(f.frozen().length, count);
  f.end(); assert.equal(f.frozen().length, 0); f.cleanup();
});

test('PiP only keeps its avoided rows live and refreshes avoidance after thaw', () => {
  const f = fixture(); f.pip();
  f.rows[0].classList.contains = name => name === 'wbc-browser-avoid-right';
  f.rows[1].classList.contains = name => name === 'wbc-browser-avoid-left';
  f.start();
  assert.ok(f.frozen().length > 0);
  assert.equal(f.rows[0].attrs.size + f.rows[1].attrs.size, 0);
  assert.equal(f.restored, 0);
  f.end(); assert.equal(f.restored, 1);
  f.cleanup(); assert.equal(f.restored, 1);
});

test('few long replies protect offscreen Markdown blocks and retain an inner reading anchor', () => {
  const f = fixture(2);
  const distant = f.rows[1];
  const visible = {...distant, attrs: new Map()};
  visible.hasAttribute = () => false;
  visible.getBoundingClientRect = () => ({top: distant.attrs.size ? -20 : 80, bottom: 180, height:100});
  const reply = f.rows[0];
  reply.getBoundingClientRect = () => ({top:-3000,bottom:4000,height:7000});
  reply.querySelectorAll = () => [distant, visible];
  f.thread.querySelectorAll = () => [reply];
  f.thread.children = [reply];
  f.start();
  assert.equal(reply.attrs.size, 0);
  assert.ok(distant.attrs.size);
  assert.equal(f.thread.wbcResizeActive, true);
  f.end();
  assert.equal(f.thread.scrollTop, 1600);
  assert.equal(f.thread.wbcResizeActive, false);
  f.cleanup();
});

test('sidebar pointerdown prepares old geometry and transitionrun reuses it', () => {
  const f = fixture();
  const control = {}; f.page.contains = node => node === control;
  f.doc.emit('pointerdown', {target:{closest:()=>control}});
  const count = f.frozen().length;
  assert.ok(count > 0);
  assert.equal(f.restored, 0);
  f.start();
  assert.equal(f.frozen().length, count);
  assert.equal(f.restored, 0);
  f.end(); assert.equal(f.restored, 1); f.cleanup();
});

test('large transcripts thaw in bounded batches rather than a single transitionend task', () => {
  const f = fixture(200); f.start();
  const count = f.frozen().length;
  f.page.emit('transitionend', {propertyName:'grid-template-columns'});
  assert.equal(f.frozen().length, count - 32);
  assert.equal(f.restored, 0);
  assert.equal(f.thread.wbcResizeActive, true);
  f.flushFrames();
  assert.equal(f.frozen().length, 0);
  assert.equal(f.restored, 1);
  f.cleanup(); assert.equal(f.frames.size, 0);
});

test('restoring the right panel through its menu prepares before the state update', () => {
  const f = fixture(); f.doc.defaultView.emit('workbench:show-chat-side');
  assert.ok(f.frozen().length); f.start(); assert.equal(f.restored, 0);
  f.end(); f.cleanup(); assert.equal(f.doc.defaultView.listeners, 0);
});
