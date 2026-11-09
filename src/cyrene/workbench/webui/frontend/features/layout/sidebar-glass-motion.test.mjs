import test from 'node:test';
import assert from 'node:assert/strict';
import { startSidebarGlassMotion } from './sidebar-glass-motion.mjs';

function fixture(reduced = false) {
  const frames = new Map(), timers = new Map(), attributes = new Map();
  let id = 0;
  const win = {
    matchMedia: () => ({ matches: reduced }),
    requestAnimationFrame: fn => (frames.set(++id, fn), id),
    cancelAnimationFrame: id => frames.delete(id),
    setTimeout: fn => (timers.set(++id, fn), id),
    clearTimeout: id => timers.delete(id),
  };
  const grid = {
    ownerDocument: { defaultView: win },
    setAttribute: (key, value) => attributes.set(key, value),
    removeAttribute: key => attributes.delete(key),
    getAnimations: () => [],
  };
  return { grid, frames, timers, moving: () => attributes.has('data-sidebar-motion'),
    frame: () => { for (const fn of frames.values()) fn(); frames.clear(); } };
}
const flush = () => new Promise(resolve => setImmediate(resolve));
function transition(property) {
  let resolve, reject;
  const finished = new Promise((yes, no) => { resolve = yes; reject = no; });
  return { transitionProperty: property, finished, resolve, reject };
}

test('restores glass only after all geometry transitions finish or cancel', async () => {
  const f = fixture(), width = transition('width'), height = transition('height');
  f.grid.getAnimations = () => [width, height, { finished: new Promise(() => {}) }];
  startSidebarGlassMotion(f.grid);
  assert.equal(f.moving(), true);
  f.frame();
  width.resolve(); await flush();
  assert.equal(f.moving(), true);
  height.reject(new Error('cancelled')); await flush();
  assert.equal(f.moving(), false);
  assert.equal(f.timers.size, 0);
});

test('rapid reversal cannot let stale completion restore glass mid-transition', async () => {
  const f = fixture(), first = transition('width'), second = transition('width');
  f.grid.getAnimations = () => [first];
  const cancel = startSidebarGlassMotion(f.grid); f.frame(); cancel();
  f.grid.getAnimations = () => [second];
  startSidebarGlassMotion(f.grid); f.frame(); first.resolve(); await flush();
  assert.equal(f.moving(), true);
  second.resolve(); await flush(); assert.equal(f.moving(), false);
});

test('no animation, reduced motion, unmount and hidden document all clean up', async () => {
  const f = fixture(); startSidebarGlassMotion(f.grid); f.frame(); await flush();
  assert.equal(f.moving(), false);
  const reduced = fixture(true); startSidebarGlassMotion(reduced.grid);
  assert.equal(reduced.moving(), false); assert.equal(reduced.frames.size, 0);
  const cancel = startSidebarGlassMotion(f.grid); cancel();
  assert.equal(f.frames.size, 0); assert.equal(f.timers.size, 0);
  startSidebarGlassMotion(f.grid);
  for (const fn of f.timers.values()) fn();
  assert.equal(f.moving(), false); assert.equal(f.frames.size, 0);
});
