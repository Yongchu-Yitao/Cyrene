import test from 'node:test';
import assert from 'node:assert/strict';
import { drawerSwipe } from './mobile-gesture.mjs';
test('edge direction opens the matching drawer and reverse motion closes it', () => {
  assert.equal(drawerSwipe('left', '', 80, 10), 'left');
  assert.equal(drawerSwipe('right', '', -80, 10), 'right');
  assert.equal(drawerSwipe('left', 'left', -80, 10), '');
  assert.equal(drawerSwipe('right', 'right', 80, 10), '');
  assert.equal(drawerSwipe('right', 'left', 80, 0), null);
});
test('taps, vertical scrolling, diagonals, and outward edge motion do not navigate', () => {
  for (const [edge, dx, dy] of [['left', 15, 0], ['left', 80, 90], ['right', -60, 50], ['left', -80, 0], ['right', 80, 0]]) {
    assert.equal(drawerSwipe(edge, '', dx, dy), null);
  }
});

test('center gestures open either side without treating taps or vertical motion as navigation', () => {
  assert.equal(drawerSwipe('center', '', 80, 5), 'left');
  assert.equal(drawerSwipe('center', '', -80, 5), 'right');
  assert.equal(drawerSwipe('center', '', 20, 0), null);
  assert.equal(drawerSwipe('center', '', 80, 90), null);
});
