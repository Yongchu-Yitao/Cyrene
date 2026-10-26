import test from 'node:test';
import assert from 'node:assert/strict';
import { createGraphReader, pollVisibleGraph } from './context-graph-reader.mjs';

const response = (data, etag) => new Response(JSON.stringify(data), { headers: { ETag: etag } });
const tick = () => new Promise(resolve => setImmediate(resolve));

test('conditional graph reads retain the existing object on 304', async () => {
  const calls = [];
  const reader = createGraphReader(async (url, options) => {
    calls.push([url, options]);
    return calls.length === 1 ? response({ graphRevision: 'a' }, '"a"') : new Response(null, { status: 304 });
  });
  const first = await reader.read('/first');
  assert.equal(await reader.read('/first'), first);
  assert.equal(calls[1][1].headers['If-None-Match'], '"a"');
});

test('switching chats clears the conditional validator', async () => {
  const reader = createGraphReader(async (url, options) => {
    assert.deepEqual(options.headers, {});
    return response({ url }, '"a"');
  });
  assert.equal((await reader.read('/first')).url, '/first');
  assert.equal((await reader.read('/second')).url, '/second');
});

test('superseded responses cannot replace newer graph data even if fetch ignores abort', async () => {
  let complete;
  const signals = [];
  const reader = createGraphReader(async (_url, options) => {
    signals.push(options.signal);
    if (signals.length === 1) return await new Promise(resolve => { complete = resolve; });
    return response({ graphRevision: 'new' }, '"new"');
  });
  const first = reader.read('/same');
  const rejected = assert.rejects(first, { name: 'AbortError' });
  const latest = await reader.read('/same');
  assert.equal(signals[0].aborted, true);
  complete(response({ graphRevision: 'old' }, '"old"'));
  await rejected;
  assert.equal(latest.graphRevision, 'new');
});

test('abort on unmount prevents applying a late response', async () => {
  let complete;
  const reader = createGraphReader(() => new Promise(resolve => { complete = resolve; }));
  const pending = reader.read('/same');
  const rejected = assert.rejects(pending, { name: 'AbortError' });
  reader.abort();
  complete(response({ graphRevision: 'late' }, '"late"'));
  await rejected;
});

test('HTTP failures do not overwrite the last successful validator', async () => {
  let calls = 0;
  const reader = createGraphReader(async (_url, options) => {
    calls++;
    if (calls === 1) return response({ graphRevision: 'a' }, '"a"');
    assert.equal(options.headers['If-None-Match'], '"a"');
    if (calls === 2) return new Response(JSON.stringify({ detail: 'failed' }), { status: 500 });
    return new Response(null, { status: 304 });
  });
  const first = await reader.read('/same');
  await assert.rejects(reader.read('/same'), /failed/);
  assert.equal(await reader.read('/same'), first);
});

test('poll waits for completion, pauses while hidden and stops on cleanup', async () => {
  const listeners = new Map(), timers = new Map();
  let nextTimer = 0, calls = 0, resolveLoad;
  const document = {
    hidden: false,
    addEventListener: (key, fn) => listeners.set(key, fn),
    removeEventListener: key => listeners.delete(key),
  };
  const stop = pollVisibleGraph(async () => {
    calls++;
    await new Promise(resolve => { resolveLoad = resolve; });
  }, { document, schedule: fn => { timers.set(++nextTimer, fn); return nextTimer; }, cancel: id => timers.delete(id) });
  assert.equal(calls, 1);
  assert.equal(timers.size, 0);
  listeners.get('visibilitychange')();
  assert.equal(calls, 1);
  document.hidden = true;
  listeners.get('visibilitychange')();
  resolveLoad();
  await tick();
  assert.equal(timers.size, 0);
  document.hidden = false;
  listeners.get('visibilitychange')();
  assert.equal(calls, 2);
  resolveLoad();
  await tick();
  assert.equal(timers.size, 1);
  stop();
  assert.equal(timers.size, 0);
  assert.equal(listeners.size, 0);
});
