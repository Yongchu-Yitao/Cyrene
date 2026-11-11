import test from 'node:test';
import assert from 'node:assert/strict';
import { createCyreneMotion, CYRENE_STATES } from '../shell/cyrene-motion-model.mjs';
import { attachCyreneMotion } from '../shell/cyrene-motion-dom.mjs';
import { CYRENE_SLEEP_DELAY_MS, observeCyreneWindowActivity } from '../shell/cyrene-window-activity.mjs';

function events() {
  const listeners = new Map();
  return {
    listeners,
    addEventListener(name, fn) { if (!listeners.has(name)) listeners.set(name, new Set()); listeners.get(name).add(fn); },
    removeEventListener(name, fn) { listeners.get(name)?.delete(fn); },
    emit(name, event = {}) { for (const fn of listeners.get(name) || []) fn({ isTrusted: true, ...event }); },
  };
}

function fixture() {
  let now = 0, next = 0, focused = true, writes = 0;
  const timers = new Map(), frames = new Map(), media = Object.assign(events(), { matches: false });
  const win = Object.assign(events(), {
    performance: { now: () => now },
    setTimeout(fn, delay) { const id = ++next; timers.set(id, { fn, at: now + delay }); return id; },
    clearTimeout(id) { timers.delete(id); },
    requestAnimationFrame(fn) { const id = ++next; frames.set(id, fn); return id; },
    cancelAnimationFrame(id) { frames.delete(id); },
    matchMedia: () => media,
    MutationObserver: class { observe() {} disconnect() {} },
    getComputedStyle: () => ({ display: 'block', visibility: 'visible' }),
    innerWidth: 1000, innerHeight: 800,
  });
  const doc = Object.assign(events(), {
    defaultView: win, hidden: false, hasFocus: () => focused,
    documentElement: { dataset: {} }, querySelectorAll: () => [],
  });
  function svg() {
    const button = Object.assign(events(), { tagName: 'BUTTON', dataset: {} });
    const anchor = { isConnected: true, closest: () => null, animate: () => ({ cancel() {} }),
      getBoundingClientRect: () => ({ x: 10, y: 10, width: 32, height: 32, top: 10, left: 10, bottom: 42, right: 42 }) };
    const parts = new Map();
    return { ownerDocument: doc, parentElement: anchor, dataset: {}, closest: () => button,
      querySelector(selector) {
        if (!parts.has(selector)) parts.set(selector, { style: new Proxy({}, {
          set(target, key, value) { writes++; target[key] = value; return true; },
        }) });
        return parts.get(selector);
      },
    };
  }
  return { doc, win, media, timers, frames, svg,
    get writes() { return writes; },
    advance(ms) {
      now += ms;
      for (const [id, timer] of [...timers]) if (timer.at <= now) { timers.delete(id); timer.fn(); }
    },
    frame(ms = 16) {
      now += ms;
      const callbacks = [...frames.values()]; frames.clear();
      callbacks.forEach(fn => fn(now));
    },
    focus(value) { focused = value; win.emit(value ? 'focus' : 'blur', { target: win }); },
  };
}

test('window activity shares one deadline, resets on input, and stops scheduling during sleep', () => {
  const f = fixture(), a = [], b = [];
  const offA = observeCyreneWindowActivity(f.doc, asleep => a.push(asleep));
  const offB = observeCyreneWindowActivity(f.doc, asleep => b.push(asleep));
  assert.equal(f.timers.size, 1);
  f.advance(90000); f.doc.emit('pointermove');
  f.advance(30000); assert.deepEqual(a, [false]);
  f.advance(89999); assert.deepEqual(a, [false]);
  f.advance(1); assert.deepEqual(a, [false, true]); assert.deepEqual(a, b);
  assert.equal(f.timers.size, 0);
  f.doc.emit('input', { isTrusted: false }); assert.deepEqual(a, [false, true]);
  f.doc.emit('keydown'); assert.deepEqual(a, [false, true, false]);
  assert.equal(f.timers.size, 1);
  offA(); assert.equal(f.timers.size, 1);
  offB(); assert.equal(f.timers.size, 0);
  for (const host of [f.win, f.doc]) for (const listeners of host.listeners.values()) assert.equal(listeners.size, 0);
});

test('blur sleeps immediately; iframe focus and synthetic events cannot keep an unfocused window awake', () => {
  const f = fixture(), states = [];
  const off = observeCyreneWindowActivity(f.doc, asleep => states.push(asleep));
  f.win.emit('blur'); assert.deepEqual(states, [false], 'the document still owns focus in an iframe');
  f.focus(false); assert.deepEqual(states, [false, true]); assert.equal(f.timers.size, 0);
  f.doc.emit('pointermove'); f.win.emit('focus', { isTrusted: false });
  assert.deepEqual(states, [false, true]);
  f.focus(true); assert.deepEqual(states, [false, true, false]);
  off();
});

test('sleep closes the eyes and settles once from every business state, retaining the latest state for wake', () => {
  for (const state of CYRENE_STATES) {
    const motion = createCyreneMotion(); motion.setState(state);
    for (let i = 0; i < 30; i++) motion.step(1 / 60);
    const start = { ...motion.pose };
    motion.setSleeping(true);
    assert.deepEqual(motion.pose, start, 'sleep begins at the current pose');
    motion.step(.25); assert.equal(motion.resting, false);
    assert(motion.pose.blink < start.blink || start.blink <= .08);
    motion.step(.75);
    assert.equal(motion.resting, true); assert.equal(motion.pose.sleep, 1);
    assert(Math.abs(motion.pose.blink - .08) < 1e-8);
    const asleep = { ...motion.pose };
    motion.setState('replying'); motion.setAttentionTargets({ pointer: { x: 1, y: 1 } });
    motion.noticeInput(); motion.press(); motion.release(); motion.step(500);
    assert.deepEqual(motion.pose, asleep); assert.equal(motion.state, 'replying');
    motion.setSleeping(false); assert.deepEqual(motion.pose, asleep);
    for (let i = 0; i < 60; i++) motion.step(1 / 60);
    assert.equal(motion.sleeping, false); assert(motion.pose.sleep < .01); assert(motion.pose.blink > .9);
  }
});

test('after the sleep entrance there are no animation frames, timers or style writes until user interaction', () => {
  const f = fixture(), svg = f.svg(), controller = attachCyreneMotion(svg);
  f.frame(); f.advance(CYRENE_SLEEP_DELAY_MS);
  assert.equal(svg.dataset.sleeping, 'true');
  for (let i = 0; i < 70; i++) f.frame();
  assert.equal(f.frames.size, 0); assert.equal(f.timers.size, 0);
  assert.equal(svg.querySelector('.cyrene-icon-sleep').style.opacity, 1);
  const writes = f.writes;
  controller.setState('tool'); controller.setState('waiting');
  f.advance(600000); f.frame();
  assert.equal(f.writes, writes); assert.equal(f.frames.size, 0);
  f.doc.emit('pointerdown'); assert.equal(svg.dataset.sleeping, 'false');
  assert.equal(f.frames.size, 1); assert.equal(f.timers.size, 1);
  controller.destroy(); assert.equal(f.frames.size, 0); assert.equal(f.timers.size, 0);
});

test('hiding mid-entrance settles without frames; visibility alone does not wake, focus does', () => {
  const f = fixture(), svg = f.svg(), controller = attachCyreneMotion(svg);
  f.focus(false); f.frame(); f.frame(100);
  f.doc.hidden = true; f.doc.emit('visibilitychange');
  assert.equal(f.frames.size, 0); assert.equal(f.timers.size, 0);
  assert.equal(svg.querySelector('.cyrene-icon-sleep').style.opacity, 1);
  f.doc.hidden = false; f.doc.emit('visibilitychange');
  assert.equal(f.frames.size, 0); assert.equal(svg.dataset.sleeping, 'true');
  f.focus(true); assert.equal(svg.dataset.sleeping, 'false'); assert.equal(f.frames.size, 1);
  controller.destroy();
});

test('icons mounted in the background and reduced-motion users get a static sleep pose', () => {
  const f = fixture(); f.focus(false);
  const svg = f.svg(), controller = attachCyreneMotion(svg);
  assert.equal(svg.dataset.sleeping, 'true'); assert.equal(f.frames.size, 0); assert.equal(f.timers.size, 0);
  f.media.matches = true; f.media.emit('change');
  f.focus(true); assert.equal(svg.dataset.sleeping, 'false'); assert.equal(f.frames.size, 0);
  f.advance(CYRENE_SLEEP_DELAY_MS);
  assert.equal(svg.dataset.sleeping, 'true'); assert.equal(f.frames.size, 0);
  assert.equal(f.timers.size, 0); assert.equal(svg.querySelector('.cyrene-icon-sleep').style.opacity, 1);
  controller.destroy();
});
