'use strict';

const assert = require('node:assert/strict');
const test = require('node:test');

const {
  displayDipPoint,
  inputBounds,
  inputPoint,
} = require('./remote-desktop-coordinates');

test('converts normalized Electron DIP coordinates to Windows physical pixels', () => {
  const display = { bounds: { x: 0, y: 0, width: 2195, height: 1235 } };
  const event = { x_normalized: 0.5, y_normalized: 0.5 };
  const calls = [];
  const screenApi = {
    dipToScreenPoint(point) {
      calls.push(point);
      return { x: Math.round(point.x * 1.75), y: Math.round(point.y * 1.75) };
    },
  };

  assert.deepEqual(displayDipPoint(display, event), { x: 1097, y: 617 });
  assert.deepEqual(inputPoint(display, event, { platform: 'win32', screenApi }), { x: 1920, y: 1080 });
  assert.deepEqual(calls, [{ x: 1097, y: 617 }]);
});

test('converts negative multi-display bounds with Electron screen APIs', () => {
  const display = { bounds: { x: -1280, y: -120, width: 1280, height: 1024 } };
  const calls = [];
  const screenApi = {
    dipToScreenRect(window, bounds) {
      calls.push({ window, bounds });
      return { x: -1920, y: -180, width: 1920, height: 1536 };
    },
  };

  assert.deepEqual(inputBounds(display, { platform: 'win32', screenApi }), {
    x: -1920,
    y: -180,
    width: 1920,
    height: 1536,
  });
  assert.deepEqual(calls, [{ window: null, bounds: display.bounds }]);
});

test('keeps DIP coordinates unchanged outside Windows', () => {
  const display = { bounds: { x: 10, y: 20, width: 101, height: 51 } };
  const screenApi = {
    dipToScreenPoint() { throw new Error('must not be called'); },
    dipToScreenRect() { throw new Error('must not be called'); },
  };

  assert.deepEqual(
    inputPoint(display, { x_normalized: 1, y_normalized: 1 }, { platform: 'darwin', screenApi }),
    { x: 110, y: 70 },
  );
  assert.deepEqual(inputBounds(display, { platform: 'darwin', screenApi }), display.bounds);
});
