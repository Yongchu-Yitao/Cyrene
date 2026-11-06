import test from 'node:test';
import assert from 'node:assert/strict';
import { cyreneActivityState as state, observeCyreneActivity } from '../shell/cyrene-activity.mjs';

const settle = async () => { for (let i = 0; i < 8; i++) await Promise.resolve(); };
function fixture(getChat = async () => ({ status: 'idle' })) {
  let runtime = null, failure = null, timerId = 0;
  const subscribers = new Set(), listeners = new Map(), timers = new Map(), states = [];
  const target = {
    addEventListener: (name, fn) => listeners.set(name, fn),
    removeEventListener: name => listeners.delete(name),
    setTimeout(fn, delay) { timers.set(++timerId, {fn, delay}); return timerId; },
    clearTimeout: id => timers.delete(id),
  };
  const engine = { get: () => runtime, getFailure: () => failure,
    subscribe(fn) { subscribers.add(fn); return () => subscribers.delete(fn); } };
  const stop = observeCyreneActivity({ sessionId: 'assistant-a', engine, model: {getChat}, target, onState: value => states.push(value) });
  return { states, timers, subscribers, listeners, stop,
    update(value) { runtime = value; subscribers.forEach(fn => fn()); },
    fail(value) { failure = value; },
    event(status, chatId = 'assistant-a', runId = '') { listeners.get('cyrene:wbc-chat-lifecycle')?.({detail:{chatId,status,runId}}); },
    tick(delay) { for (const [id, timer] of [...timers]) if (timer.delay === delay) { timers.delete(id); timer.fn(); } },
  };
}

test('maps live phases and terminal outcomes without celebrating historical completion', () => {
  for (const [input, expected] of [
    [{chat:{status:'completed'}},'idle'], [{runtime:{}},'thinking'],
    [{runtime:{replying:true}},'replying'], [{runtime:{replying:true,streamDone:true}},'thinking'],
    [{runtime:{replying:true,progress:[{kind:'tool',status:'running'}]}},'tool'],
    [{runtime:{reconnecting:true,pendingQuestion:{id:'q'}}},'waiting'],
    [{runtime:{reconnecting:true}},'reconnecting'], [{chat:{status:'awaiting_user'}},'waiting'],
    [{chat:{status:'failed'}},'error'], [{chat:{status:'cancelled'}},'stopped'],
    [{chat:{status:'stopped'}},'stopped'], [{completion:true},'done'],
    [{runtime:{},chat:{status:'awaiting_user',pendingQuestion:{id:'old'}}},'thinking'],
  ]) assert.equal(state(input),expected);
});

test('authoritative timeline replies and tools override stale legacy summaries', () => {
  const stale = { progress:[{kind:'tool',status:'running'}], replying:false };
  const reply = {role:'assistant',status:'running',content:'hello'};
  assert.equal(state({runtime:{...stale,timeline:{messages:[reply]}}}),'replying');
  assert.equal(state({runtime:{...stale,timeline:{messages:[{activityCard:true,status:'running',trace:[{toolCallId:'t',status:'running'}]}]}}}),'tool');
  assert.equal(state({runtime:{...stale,timeline:{messages:[{...reply,status:'completed'}]}}}),'thinking');
});

test('completion before runtime removal plays once, then settles and ignores stale hydration', async () => {
  let resolve;
  const f=fixture(()=>new Promise(r=>{resolve=r;}));
  f.update({streamGeneration:'one'});
  f.event('completed');
  assert.equal(f.states.at(-1),'done');
  f.update(null);
  resolve({status:'running'});await settle();
  assert.equal(f.states.at(-1),'done');
  f.tick(2400);
  assert.equal(f.states.at(-1),'idle');
  f.update({streamGeneration:'two'});
  assert.equal(f.states.at(-1),'thinking');
  f.stop();assert.equal(f.timers.size,0);
});

test('awaiting approval, failures and interruption never masquerade as completion', async () => {
  for (const [status,expected] of [['awaiting_user','waiting'],['failed','error'],['cancelled','stopped']]) {
    const f=fixture();await settle();f.update({streamGeneration:'one'});
    if (status==='failed') f.fail(new Error('provider failed'));
    if (status==='awaiting_user') f.update({streamGeneration:'one',pendingQuestion:{id:'q'}});
    f.event(status);f.update(null);
    assert.equal(f.states.at(-1),expected);
    assert(!f.states.includes('done'));f.stop();
  }
});

test('token updates do not restart animation or emit extra state; other sessions cannot affect it', async () => {
  const f=fixture();await settle();
  f.update({streamGeneration:'one',replying:true});
  const count=f.states.length;
  for (let i=0;i<30;i++) f.update({streamGeneration:'one',replying:true,text:'x'.repeat(i)});
  f.event('failed','another-session');
  assert.equal(f.states.length,count);
  f.stop();assert.equal(f.subscribers.size,0);assert.equal(f.listeners.size,0);
});

test('new run clears waiting and rejects terminal events for an older run', async () => {
  const f=fixture();await settle();
  f.update({streamGeneration:'one',runId:'run1',pendingQuestion:{id:'q'}});f.event('awaiting_user');f.update(null);
  f.update({streamGeneration:'two',runId:'run2'});f.event('completed','assistant-a','run1');
  assert.equal(f.states.at(-1),'thinking');f.stop();
});

test('disposed selection cannot emit, resubscribe or schedule after an async response', async () => {
  let resolve;const f=fixture(()=>new Promise(r=>{resolve=r;}));
  f.stop();const count=f.states.length;resolve({status:'running'});await settle();
  assert.equal(f.states.length,count);assert.equal(f.timers.size,0);assert.equal(f.subscribers.size,0);
});

test('server reconciliation recovers from transport errors and completes background work', async () => {
  let value=new Error('offline');
  const f=fixture(async()=>{if(value instanceof Error)throw value;return value;});
  await settle();assert.equal(f.states.at(-1),'reconnecting');
  value={status:'running'};f.tick(3000);await settle();assert.equal(f.states.at(-1),'thinking');
  value={status:'idle'};f.tick(3000);await settle();assert.equal(f.states.at(-1),'done');
  f.tick(2400);assert.equal(f.states.at(-1),'idle');f.stop();
});
