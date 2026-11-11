import test from 'node:test';
import assert from 'node:assert/strict';
import {harness} from './hook-harness.test.mjs';

const settle = async () => { for (let i = 0; i < 15; i++) await Promise.resolve(); };
const idle = {id: 'a', status: 'idle', runStatus: 'completed', messages: [{id: 'm', role: 'assistant', content: 'hello'}]};
function find(element, predicate) {
  if (!element) return null;
  if (Array.isArray(element)) return element.map(child => find(child, predicate)).find(Boolean) || null;
  return predicate(element) ? element : find(element.props?.children, predicate);
}

function mount({hidden = false} = {}) {
  const React = {createElement: (type, props, ...children) => ({type, props: {...props, children}})};
  const requests = [], reconnects = [], delays = [];
  const cache = {details: {}};
  let saved;
  const engine = {
    get: () => null, getFailure: () => null, subscribe: () => () => {},
    subscribeSaved(_id, fn) { saved = fn; return () => { saved = null; }; },
    reconnect: id => reconnects.push(id), start: async () => {},
  };
  const model = {syncChat(id, revision) {
    return new Promise((resolve, reject) => requests.push({id, revision, resolve, reject}));
  }};
  const timeline = harness('./runtime-timeline.jsx').run(mod => mod);
  const page = harness('./runtime-page-hooks.jsx').run(mod => mod);
  const h = harness('../shell/assistant-chat.jsx', {
    ...timeline, ...page, React, useWbcComposerReserveHeight() {},
    workbenchServices: {chat: () => ({Model: model, Runtimes: engine,
      Composer: 'composer', Transcript: 'transcript', QuestionPrompt: 'question'})},
  });
  h.context.document.hidden = hidden;
  h.context.setTimeout = (fn, delay) => { delays.push(delay); return h.context.window.setTimeout(fn); };
  h.context.clearTimeout = h.context.window.clearTimeout;
  h.run((mod, hooks) => {
    Object.assign(React, {useState: hooks.useWbcState, useRef: hooks.useWbcRef, useEffect: hooks.useWbcEffect});
    return mod.AssistantChat({sessionId: 'a', cache});
  });
  return {
    h, requests, reconnects, delays, cache, engine,
    element: type => find(h.value, el => el.type === type),
    saved: (...args) => saved(...args),
    poll() { for (const [id, fn] of [...h.timers]) { h.timers.delete(id); fn(); } },
    visibility(hidden) { h.context.document.hidden = hidden; h.listeners.get('visibilitychange')(); },
    async respond(index, result) { requests[index].resolve(result); await settle(); h.flush(); },
  };
}

test('idle uses conditional reads at the same cadence without replacing history or scrolling', async () => {
  const f = mount();
  await f.respond(0, {chat: idle, revision: 'v1'});
  const history = f.element('transcript').props.messages;
  const ref = find(f.h.value, el => el.props?.className?.startsWith('wbc-thread ')).props.ref;
  let measurements = 0;
  ref.current = {get scrollHeight() { measurements++; return 100; }, scrollTop: 0};
  for (let i = 1; i <= 4; i++) {
    f.poll();
    assert.equal(f.requests[i].revision, 'v1');
    await f.respond(i, {unchanged: true, revision: 'v1'});
    assert.equal(f.element('transcript').props.messages, history);
  }
  assert.equal(measurements, 0);
  assert.ok(f.delays.every(delay => delay === 2000));
  // An explicit refresh with an equal payload also preserves the rendered object.
  f.h.listeners.get('cyrene:card-submitted')({detail: {session_id: 'a'}});
  assert.equal(f.requests[5].revision, '');
  await f.respond(5, {chat: structuredClone(idle), revision: 'v1'});
  assert.equal(f.element('transcript').props.messages, history);
  assert.equal(measurements, 0);
  f.h.unmount();
  assert.equal(f.h.timers.size, 0);
});

test('hidden windows stop polling and immediately hydrate on return', async () => {
  const f = mount({hidden: true});
  assert.equal(f.requests.length, 0);
  assert.equal(f.h.timers.size, 0);
  f.visibility(false);
  await f.respond(0, {chat: idle, revision: 'v1'});
  f.visibility(true);
  f.poll();
  assert.equal(f.requests.length, 1);
  f.visibility(false);
  assert.equal(f.requests[1].revision, '');
  await f.respond(1, {chat: {...idle, messages: [...idle.messages, {id: 'new', content: 'changed while hidden'}]}, revision: 'v2'});
  assert.equal(f.element('transcript').props.messages.at(-1).id, 'new');
  f.h.unmount();
  assert.equal(f.h.listeners.has('visibilitychange'), false);
  assert.equal(f.h.listeners.has('online'), false);
});

test('visibility during an in-flight read coalesces a forced refresh and disposal cannot restart it', async () => {
  const f = mount();
  f.visibility(true);
  f.visibility(false);
  assert.equal(f.requests.length, 1);
  await f.respond(0, {chat: idle, revision: 'v1'});
  assert.equal(f.delays.at(-1), 0);
  f.poll();
  assert.equal(f.requests[1].revision, '');
  f.h.unmount();
  f.requests[1].resolve({chat: idle, revision: 'v2'});
  await settle();
  assert.equal(f.h.timers.size, 0);
});

test('saved replies reject both stale payloads and their revision tokens', async () => {
  const f = mount();
  await f.respond(0, {chat: idle, revision: 'v1'});
  f.poll();
  f.saved([{id: 'reply', role: 'assistant', content: 'done'}], {type: 'done'}, null);
  f.h.flush();
  assert.equal(f.element('transcript').props.messages.at(-1).id, 'reply');
  await f.respond(1, {chat: {...idle, runStatus: 'running'}, revision: 'stale'});
  assert.equal(f.element('transcript').props.messages.at(-1).id, 'reply');
  assert.deepEqual(f.reconnects, []);
  f.poll();
  assert.equal(f.requests[2].revision, '');
  f.h.unmount();
  f.requests[2].resolve({chat: idle, revision: 'v1'});
  await settle();
});

test('recovery hydrates after errors and running state retries stream attachment even when unchanged', async () => {
  const f = mount();
  await f.respond(0, {chat: idle, revision: 'v1'});
  f.poll(); f.requests[1].reject(Error('offline')); await settle(); f.h.flush();
  assert.match(JSON.stringify(f.h.value), /连接中断/);
  f.poll();
  assert.equal(f.requests[2].revision, '');
  await f.respond(2, {chat: idle, revision: 'v1'});
  assert.doesNotMatch(JSON.stringify(f.h.value), /连接中断/);
  f.poll();
  await f.respond(3, {chat: {...idle, runStatus: 'running'}, revision: 'v2'});
  assert.deepEqual(f.reconnects, ['a']);
  assert.equal(f.element('composer').props.running, true);
  f.poll();
  await f.respond(4, {unchanged: true, revision: 'v2'});
  assert.deepEqual(f.reconnects, ['a', 'a']);
  f.h.unmount();
});

test('failed answers refresh unconditionally and retain the pending question for retry', async () => {
  const f = mount();
  const question = {...idle, pendingQuestion: {id: 'q', text: 'Choose'}};
  await f.respond(0, {chat: question, revision: 'q1'});
  f.engine.start = async () => { throw Error('offline'); };
  await f.element('question').props.onAnswer('q', 'yes', 'default');
  assert.equal(f.requests[1].revision, '');
  await f.respond(1, {chat: question, revision: 'q1'});
  assert.equal(f.element('question').props.pending.id, 'q');
  f.h.unmount();
});

test('sync API encodes the revision and bypasses browser response caching', async () => {
  const calls = [];
  const h = harness('./model-api.jsx', {workbenchServices: {api: () => ({json: async (...args) => {
    calls.push(args); return {unchanged: true, revision: 'v1'};
  }})}});
  const model = h.run(mod => mod.WorkbenchChatModel);
  assert.equal((await model.syncChat('a/b', 'v:1')).unchanged, true);
  assert.equal(calls[0][0], '/api/workbench/chats/a%2Fb?sync_revision=v%3A1');
  assert.equal(calls[0][1].cache, 'no-store');
});
