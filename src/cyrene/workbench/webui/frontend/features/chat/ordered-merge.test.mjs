import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import { transformSync } from 'esbuild';

const source = readFileSync(new URL('./runtime-timeline.jsx', import.meta.url), 'utf8');
function load(legacy = false) {
  const entry = 'var merged = wbcMergeProjectedMessages(durable.filter(function (message) {';
  assert.equal(source.split(entry).length, 2);
  const input = legacy ? source.replace(entry, 'var merged = wbcMergeChronologicalMessages(durable.filter(function (message) {') : source;
  const { code } = transformSync(input, { loader: 'jsx', format: 'cjs' });
  const module = { exports: {} };
  // Same realm as fixture objects: a foreign VM would only test guard fallback.
  return new Function('require', 'module', 'exports', code + `
    return { ...module.exports, fast: wbcTryMergeOrderedMessages,
      merge: wbcMergeProjectedMessages, legacy: wbcMergeChronologicalMessages };`
  )(() => ({}), module, module.exports);
}
const current = load(), previous = load(true);
const at = n => new Date(1700000000000 + n * 1000).toISOString();
const messages = (n, prefix, offset = 0) => Array.from({ length: n }, (_, i) => ({
  id: prefix + i, role: 'assistant', createdAt: at(offset + i), content: prefix + i,
}));
function compare(a, b) {
  const expected = current.legacy(a, b), actual = current.merge(a, b);
  assert.deepEqual(actual, expected);
  for (let i = 0; i < expected.length; i++) {
    assert.equal(i in actual, i in expected);
    if (a.includes(expected[i]) || b.includes(expected[i])) assert.equal(actual[i], expected[i]);
  }
  return actual;
}

test('ordered merge preserves equal-time precedence, empty ids, duplicates and object identity', () => {
  const a = messages(100, 'a'), b = messages(40, 'b', 30);
  a[31].id = a[30].id;
  b[1].id = 'a30'; b[2].id = ''; b[3].id = ''; b[5].id = b[4].id;
  for (const item of [...a, ...b]) Object.freeze(item);
  Object.freeze(a); Object.freeze(b);
  const fast = current.fast(a, b);
  assert.ok(fast !== null, 'exercise the fast path, not just the legacy oracle');
  assert.deepEqual(fast, compare(a, b));
  assert.equal(fast.findIndex(x => x === a[30]) < fast.findIndex(x => x === b[0]), true);
});

test('small inputs avoid the fast path and legacy timestamps keep their priority', () => {
  assert.equal(current.fast(messages(1000, 'a'), messages(1, 'b')), null);
  assert.equal(current.fast(messages(10, 'a'), messages(8, 'b')), null);
  const a = messages(100, 'a'), b = messages(40, 'b', 100);
  for (const x of b) { x.created_at = x.createdAt; x.createdAt = ''; }
  assert.ok(current.fast(a, b) !== null);
  compare(a, b);
});

test('special data falls back without changing the legacy result', () => {
  const edits = [
    (a, b) => { a.reverse(); }, (a, b) => { b.reverse(); },
    (a, b) => { a[99].createdAt = 'invalid'; },
    (a, b) => { b[0].id = 42; },
    (a, b) => { b[0].role = 'user'; b[0].answerToQuestionId = 'question'; },
    (a, b) => { b[0].clientRequestId = 'request'; },
    (a, b) => { b[0].optimistic = true; },
    (a, b) => { delete a[40]; }, (a, b) => { delete b[5]; },
    (a, b) => { a[40] = null; }, (a, b) => { b[5] = null; },
    (a, b) => { b[0] = Object.create(b[0]); },
  ];
  for (const edit of edits) {
    const a = messages(100, 'a'), b = messages(40, 'b', 100);
    edit(a, b);
    assert.equal(current.fast(a, b), null);
    compare(a, b);
  }
});

test('fallback preserves getter and custom array-method calls', () => {
  for (const kind of ['record', 'slot', 'method']) {
    function run(merge) {
      let calls = 0;
      const a = messages(100, 'a'), b = messages(40, 'b', 100);
      if (kind === 'record') Object.defineProperty(b[0], 'id', { get() { calls++; return 'b0'; } });
      if (kind === 'slot') { const first = b[0]; Object.defineProperty(b, '0', { get() { calls++; return first; } }); }
      if (kind === 'method') b.forEach = function (fn) { calls++; Array.prototype.forEach.call(this, fn); };
      const result = merge(a, b).map(x => x.content);
      return { result, calls };
    }
    assert.deepEqual(run(current.merge), run(current.legacy));
  }
});

test('seeded differential cases cover fast and fallback paths without mutating input', () => {
  let seed = 20260920, hits = 0, fallbacks = 0;
  const rand = n => { seed = (Math.imul(seed, 1664525) + 1013904223) >>> 0; return seed % n; };
  for (let iteration = 0; iteration < 10000; iteration++) {
    const a = messages(70 + rand(40), 'a'), b = messages(40 + rand(30), 'b', rand(80));
    for (const x of b) { if (rand(4) === 0) x.id = 'a' + rand(110); if (rand(8) === 0) x.id = ''; }
    if (iteration % 5 === 0) b.reverse();
    if (iteration % 7 === 0) b[0].clientRequestId = 'request';
    if (iteration % 11 === 0) a[0].createdAt = 'invalid';
    for (const x of [...a, ...b]) Object.freeze(x);
    Object.freeze(a); Object.freeze(b);
    if (current.fast(a, b) === null) fallbacks++; else hits++;
    compare(a, b);
  }
  assert.ok(hits > 4000 && fallbacks > 1000);
});

test('production projection preserves every patch, replay, checkpoint, removal and terminal state', () => {
  const history = messages(100, 'history');
  let oldRuntime = {}, newRuntime = {};
  for (let revision = 1; revision <= 120; revision++) {
    let patch;
    if (revision === 1 || revision === 61) {
      patch = { version: 2, snapshot: true, runId: 'run', revision, status: 'running',
        messages: messages(40, 'live', 100).map((x, i) => ({ ...x, timelineOrder: i, timelineRevision: revision, status: 'running' })) };
    } else if (revision % 11 === 0) {
      patch = { version: 2, runId: 'run', revision, status: 'running', removedMessageIds: ['live0'], messages: [] };
    } else {
      const last = oldRuntime.timeline.messages.find(x => x.id === 'live39');
      patch = { version: 2, runId: 'run', revision, status: revision === 120 ? 'completed' : 'running',
        updates: [{ id: last.id, baseRevision: last.timelineRevision, append: { content: ' delta' }, set: { timelineRevision: revision } }] };
    }
    for (let replay = 0; replay < 2; replay++) {
      oldRuntime = previous.wbcApplyTimeline(oldRuntime, patch);
      newRuntime = current.wbcApplyTimeline(newRuntime, patch);
      assert.deepEqual(current.wbcProjectTranscript(history, newRuntime), previous.wbcProjectTranscript(history, oldRuntime));
    }
  }
  for (const state of ['running', 'completed', 'cancelled', 'failed']) {
    newRuntime.timeline.status = state;
    const last = newRuntime.timeline.messages.at(-1);
    const checkpoint = { ...last, timelineRevision: last.timelineRevision + 1, content: 'newer checkpoint' };
    const saved = history.concat(checkpoint);
    for (const extra of [{}, { reconnecting: true }, { pendingQuestion: { id: 'q' } }]) {
      const runtime = { ...newRuntime, ...extra };
      assert.deepEqual(current.wbcProjectTranscript(saved, runtime), previous.wbcProjectTranscript(saved, runtime));
    }
  }
});
