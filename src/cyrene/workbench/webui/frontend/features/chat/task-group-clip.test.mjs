import test from 'node:test';
import assert from 'node:assert/strict';
import { trackTaskGroupClip } from './task-group-clip.mjs';

test('round clip follows sticky header and restores on cleanup', () => {
  let groupTop = 20, headerTop = 24, scheduled, scroll, value, disconnected = false;
  const win = {
    requestAnimationFrame(fn) { scheduled = fn; return 1; }, cancelAnimationFrame() {},
    addEventListener() {}, removeEventListener() {},
    ResizeObserver: class { observe() {} disconnect() { disconnected = true; } },
  };
  const group = {
    ownerDocument: { defaultView: win, addEventListener(type, fn) { scroll = fn; }, removeEventListener() {} },
    querySelector: () => ({ getBoundingClientRect: () => ({top: headerTop}) }),
    getBoundingClientRect: () => ({top: groupTop}),
    style: {setProperty(key, next) { value = next; }, removeProperty() { value = undefined; }},
  };
  const cleanup = trackTaskGroupClip(group);
  assert.equal(value, '0px');
  groupTop = -80; headerTop = 14;
  scroll(); scheduled();
  assert.equal(value, '90px');
  groupTop = 20; headerTop = 24;
  scroll(); scheduled();
  assert.equal(value, '0px');
  cleanup();
  assert.equal(value, undefined);
  assert.equal(disconnected, true);
});
