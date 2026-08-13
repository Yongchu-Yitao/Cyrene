const assert = require('node:assert/strict');
const test = require('node:test');

const { encodeRef, decodeRef, normalizeAction } = require('./app-use-linux');
const { capabilitiesForTarget } = require('./app-use');

test('AT-SPI refs remain opaque and reversible inside the provider only', () => {
  const ref = encodeRef(':1.42', '/org/a11y/atspi/accessible/7');
  assert.equal(ref.includes('/org/a11y'), false);
  assert.deepEqual(decodeRef(ref), { busName: ':1.42', objectPath: '/org/a11y/atspi/accessible/7' });
});

test('AT-SPI native action labels map to the semantic action vocabulary', () => {
  assert.equal(normalizeAction('click'), 'press');
  assert.equal(normalizeAction('toggle checked'), 'toggle');
  assert.equal(normalizeAction('double click'), 'double_click');
  assert.equal(normalizeAction('reorder item'), 'drag');
  assert.equal(normalizeAction('scroll down'), 'scroll');
});

test('Linux never advertises visual, coordinate, keyboard, or focus capabilities', () => {
  const capabilities = capabilitiesForTarget({ platform: 'linux', applicationId: 'org.demo.App' });
  const names = new Set(capabilities.map((item) => item.name));
  for (const semantic of ['snapshot', 'inspect', 'press', 'set_value', 'scroll', 'semantic_drag']) {
    assert.equal(names.has(semantic), true);
  }
  for (const forbidden of ['visual_describe', 'click_at', 'virtual_click_at', 'key_chord', 'focus_window', 'drag']) {
    assert.equal(names.has(forbidden), false);
  }
});
