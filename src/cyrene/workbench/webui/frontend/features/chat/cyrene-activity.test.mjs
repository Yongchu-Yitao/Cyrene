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

test('durable run status overrides stale persisted running status', async () => {
  for (const [runStatus, expected] of [['idle','idle'], ['completed','idle'], ['failed','error'], ['cancelled','stopped']]) {
    const chat = {status:'running', runStatus};
    assert.equal(state({chat}), expected);
    const f = fixture(async () => chat);
    await settle();
    assert.equal(f.states.at(-1), expected);
    assert(!f.states.includes('thinking'));
    f.stop();
  }
  assert.equal(state({chat:{status:'idle',runStatus:'running'}}), 'thinking');
  assert.equal(state({runtime:{},chat:{status:'idle',runStatus:'completed'}}), 'thinking');
});

test('a settled timeline cannot animate stale reply or tool activity during stream cleanup', () => {
  for (const [status, expected] of [['idle','idle'], ['completed','idle'], ['failed','error'], ['cancelled','stopped'], ['waiting','waiting']]) {
    assert.equal(state({runtime:{reconnecting:true,replying:true,
      timeline:{status,messages:[{role:'assistant',status:'running',trace:[{kind:'tool',status:'running'}]}]}},
      chat:{status:'running'}}), expected);
  }
});

test('idle polling never restarts work or celebrates a run that is already idle', async () => {
  let chat = {status:'idle',runStatus:'idle'};
  const f = fixture(async () => chat); await settle();
  chat = {status:'running',runStatus:'idle'};
  f.tick(15000); await settle();
  chat = {status:'idle',runStatus:'idle'};
  f.tick(15000); await settle();
  assert.deepEqual(f.states, ['idle']);
  f.stop();
});

test('terminal timeline stays settled when the runtime disappears before its saved event', async () => {
  const requests = [];
  const f = fixture(() => new Promise(resolve => requests.push(resolve)));
  requests[0]({status:'idle',runStatus:'idle'}); await settle();
  f.update({streamGeneration:'one',timeline:{status:'running',messages:[]}});
  f.tick(15000);
  f.update({streamGeneration:'one',timeline:{status:'completed',messages:[]}});
  const settledCount = f.states.length;
  assert.equal(f.states.at(-1), 'idle');
  f.update(null);
  requests[1]({status:'running',runStatus:'running'}); await settle();
  assert.equal(f.states.length, settledCount);
  f.stop();
});

test('background failure and cancellation do not play a completion animation', async () => {
  for (const [runStatus, expected] of [['failed','error'], ['cancelled','stopped'], ['idle','idle'], ['completed','done']]) {
    let chat = {status:'running',runStatus:'running'};
    const f = fixture(async () => chat); await settle();
    chat = {status:'idle',runStatus};
    f.tick(3000); await settle();
    assert.equal(f.states.at(-1), expected);
    if (runStatus !== 'completed') assert(!f.states.includes('done'));
    f.stop();
  }
});

test('fresh polling replaces the previous terminal outcome with a new background approval', async () => {
  let chat = {status:'idle',runStatus:'completed'};
  const f = fixture(async () => chat); await settle();
  f.update({streamGeneration:'one',runId:'run1'});
  f.event('completed'); f.update(null); f.tick(2400);
  chat = {status:'idle',runStatus:'awaiting_user',pendingQuestion:{id:'new-approval'}};
  f.tick(15000); await settle();
  assert.equal(f.states.at(-1), 'waiting');
  f.stop();
});

test('a new live run clears the previous durable outcome and completes only once', async () => {
  const f = fixture(async () => ({status:'idle',runStatus:'failed'})); await settle();
  f.update({streamGeneration:'new'});
  assert.equal(f.states.at(-1), 'thinking');
  f.event('completed');
  assert.equal(f.states.at(-1), 'done');
  f.update(null); f.tick(2400);
  assert.equal(f.states.at(-1), 'idle');
  assert.equal(f.states.filter(value => value === 'done').length, 1);
  f.stop();
});

test('stream cleanup refreshes immediately and rejects an older in-flight running response', async () => {
  const requests = [];
  const f = fixture(() => new Promise(resolve => requests.push(resolve)));
  requests[0]({status:'idle',runStatus:'idle'}); await settle();
  f.update({streamGeneration:'reconnect',reconnecting:true});
  f.tick(15000);
  f.update(null); f.event('refresh');
  requests[1]({status:'running',runStatus:'running'}); await settle();
  f.tick(0);
  assert.equal(requests.length, 3);
  requests[2]({status:'running',runStatus:'idle'}); await settle();
  assert.equal(f.states.at(-1), 'idle');
  assert(!f.states.includes('done'));
  f.stop(); assert.equal(f.timers.size, 0);
});

test('dock and grip share lightweight status reads and suspend hidden polling', async () => {
  const target = new EventTarget(), doc = new EventTarget(), timers = new Map();
  let id = 0, reads = 0, subscriptions = 0, runtime = null;
  doc.hidden = false; target.document = doc;
  target.setTimeout = (fn, delay) => { timers.set(++id, {fn,delay}); return id; };
  target.clearTimeout = id => timers.delete(id);
  const engine = {get:()=>runtime,subscribe:()=>{subscriptions++;return()=>subscriptions--;}};
  const model = {getChat(){throw Error('History must not be read');}, async getChatStatus(){reads++;return {runStatus:'idle'};}};
  const first = [], second = [];
  const options = {sessionId:'assistant', engine, model, target};
  const stopFirst = observeCyreneActivity({...options,onState:s=>first.push(s)});
  const stopSecond = observeCyreneActivity({...options,onState:s=>second.push(s)});
  await settle();
  assert.equal(reads,1); assert.equal(subscriptions,1); assert.equal(timers.size,1);
  assert.deepEqual(first,second);
  doc.hidden = true; doc.dispatchEvent(new Event('visibilitychange'));
  assert.equal(timers.size,0);
  stopFirst(); assert.equal(subscriptions,1);
  doc.hidden = false; doc.dispatchEvent(new Event('visibilitychange')); await settle();
  assert.equal(reads,2);
  stopSecond(); assert.equal(subscriptions,0); assert.equal(timers.size,0);
});
