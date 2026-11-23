import test from 'node:test';
import assert from 'node:assert/strict';
import { harness } from './hook-harness.test.mjs';

const settle = async () => { for (let i = 0; i < 12; i++) await Promise.resolve(); };
function fixture(hidden = false) {
  const h = harness('../shell/assistant-page.jsx');
  const requests = [], snapshots = [], errors = [], timers = new Map(), delays = [];
  let event, next = 0;
  const doc = h.context.document;
  doc.hidden = hidden;
  const target = {...h.context.window, document: doc,
    setTimeout(fn, delay) { const id = ++next; timers.set(id, fn); delays.push(delay); return id; },
    clearTimeout(id) { timers.delete(id); },
  };
  const stop = h.run(mod => mod.observeAssistantSidebar({target,
    request: options => new Promise((resolve, reject) => requests.push({options, resolve, reject})),
    subscribe: fn => { event = fn; return () => { event = null; }; },
    onSnapshot: value => snapshots.push(value), onError: value => errors.push(value),
  }));
  return {h, requests, snapshots, errors, delays, timers, stop,
    event: value => event(value),
    fire() { const entries = [...timers]; timers.clear(); entries.forEach(([,fn]) => fn()); },
    visibility(value) { doc.hidden = value; h.listeners.get('visibilitychange')(); },
    async respond(i, value) { requests[i].resolve(value); await settle(); },
  };
}

test('sidebar reconciles once a minute and coalesces lifecycle and server events', async () => {
  const f = fixture();
  await f.respond(0, {sessions: []});
  assert.equal(f.delays.at(-1), 60000);
  for (let i = 0; i < 20; i++) f.event({type: 'workbench_chat_changed'});
  assert.equal(f.timers.size, 1);
  assert.equal(f.delays.at(-1), 200);
  f.fire();
  assert.equal(f.requests.length, 2);
  f.event({type: 'user_question'});
  f.h.listeners.get('cyrene:wbc-chat-lifecycle')({});
  assert.equal(f.requests.length, 2, 'no overlapping requests');
  await f.respond(1, {sessions: ['updated']});
  assert.equal(f.delays.at(-1), 200);
  f.fire();
  await f.respond(2, {sessions: ['completed']});
  assert.equal(f.delays.at(-1), 60000);
  f.stop();
  assert.equal(f.timers.size, 0);
  assert.equal(f.h.listeners.size, 0);
});

test('hidden sidebar aborts reads, rejects stale responses and refreshes on reveal', async () => {
  const f = fixture(true);
  assert.equal(f.requests.length, 0);
  f.event({type: 'workbench_chat_changed'});
  assert.equal(f.timers.size, 0);
  f.visibility(false);
  f.visibility(true);
  assert.equal(f.requests[0].options.signal.aborted, true);
  f.visibility(false);
  await f.respond(0, {stale: true});
  assert.equal(f.snapshots.length, 0);
  f.fire();
  await f.respond(1, {fresh: true});
  assert.equal(f.snapshots[0].fresh, true);
  f.stop();
});

test('reconnect refreshes and unmount prevents in-flight completion from restarting timers', async () => {
  const f = fixture();
  f.requests[0].reject(new Error('offline'));
  await settle();
  assert.equal(f.errors.at(-1), 'offline');
  f.h.listeners.get('online')(); f.fire();
  assert.equal(f.requests.length, 2);
  f.stop();
  assert.equal(f.requests[1].options.signal.aborted, true);
  await f.respond(1, {late: true});
  assert.equal(f.snapshots.length, 0);
  assert.equal(f.timers.size, 0);
});
