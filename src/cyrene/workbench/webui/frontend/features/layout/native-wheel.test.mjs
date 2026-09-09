import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';

test('wheel listener cancels natively, uses latest render and is removed on ref cleanup', () => {
  let ref, memo, options;
  const React = {
    useRef(value) { return ref ||= { current: value }; },
    useMemo(create) { return memo ||= create(); },
  };
  const source = fs.readFileSync(new URL('../../shared/native-wheel.jsx', import.meta.url), 'utf8');
  const hook = vm.runInNewContext(source.replace('export function', 'function') + '\nuseNativeWheel;', { React });
  const target = new EventTarget();
  const originalAdd = target.addEventListener.bind(target);
  target.addEventListener = (type, listener, settings) => { options = settings; originalAdd(type, listener, settings); };
  const forwarded = { current: null };
  let calls = 0;
  const callback = hook(event => { event.preventDefault(); calls++; }, forwarded);
  callback(target);
  assert.equal(options.passive, false);
  assert.equal(forwarded.current, target);
  assert.equal(target.dispatchEvent(new Event('wheel', { cancelable: true })), false);
  assert.equal(calls, 1);
  assert.equal(hook(event => { event.preventDefault(); calls += 10; }, forwarded), callback);
  target.dispatchEvent(new Event('wheel', { cancelable: true }));
  assert.equal(calls, 11);
  callback(null);
  assert.equal(forwarded.current, null);
  assert.equal(target.dispatchEvent(new Event('wheel', { cancelable: true })), true);
  assert.equal(calls, 11);
});
