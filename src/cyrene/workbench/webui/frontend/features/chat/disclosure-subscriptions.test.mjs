import test from 'node:test';
import assert from 'node:assert/strict';
import { createDisclosureSubscriptions, subscribeDisclosureUpdates } from './disclosure-subscriptions.mjs';

test('notifies matching panes and inherited groups, leaving unrelated history untouched', () => {
  const store = createDisclosureSubscriptions();
  let calls = [];
  const remove = store.subscribe(['tool', 'tool'], () => calls.push('pane1'));
  store.subscribe(['tool'], () => calls.push('pane2'));
  store.subscribe(['group', 'tool'], () => calls.push('parent'));
  for (let i = 0; i < 1000; i++) store.subscribe(['other:' + i], () => calls.push('unrelated'));
  store.notify('tool');
  assert.deepEqual(calls, ['pane1', 'pane2', 'parent']);
  calls = [];
  remove(); store.notify('tool');
  assert.deepEqual(calls, ['pane2', 'parent']);
  calls = []; store.notify('group');
  assert.deepEqual(calls, ['parent']);
});

test('filters cross-window changes and releases both subscriptions', () => {
  const store = createDisclosureSubscriptions();
  const events = new Map();
  const target = {
    addEventListener: (name, fn) => events.set(name, fn),
    removeEventListener: (name, fn) => { assert.equal(events.get(name), fn); events.delete(name); },
  };
  let calls = 0;
  const release = subscribeDisclosureUpdates(target, store, ['tool'], () => calls++);
  events.get('storage')({ key: 'unrelated' });
  assert.equal(calls, 0);
  events.get('storage')({ key: 'cyrene:disclosure:tool' });
  events.get('storage')({ key: null });
  store.notify('tool');
  assert.equal(calls, 3);
  release();
  store.notify('tool');
  assert.equal(calls, 3);
  assert.equal(events.size, 0);
});
