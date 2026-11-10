import test from 'node:test';
import assert from 'node:assert/strict';
import {harness} from './hook-harness.test.mjs';

const settle = async () => { for (let i = 0; i < 12; i++) await Promise.resolve(); };
const question = {id: 'question', kind: 'clarification', text: 'Which option?', options: ['A', 'B']};
const user = {id: 'user', role: 'user', content: 'Help me choose', createdAt: new Date().toISOString()};
const message = {id: 'question-message', role: 'assistant', content: question.text,
  questionPrompt: true, questionId: question.id, createdAt: new Date(Date.now() + 60_000).toISOString(),
  timelineVersion: 1, timelineOrder: 0, status: 'completed'};
const idleChat = {id: 'a', status: 'idle', runStatus: 'awaiting_user', pendingQuestion: question, messages: [user, message]};

function find(element, type) {
  if (!element) return null;
  if (Array.isArray(element)) return element.map(child => find(child, type)).find(Boolean) || null;
  return element.type === type ? element : find(element.props?.children, type);
}

function mount() {
  const timeline = harness('./runtime-timeline.jsx').run(mod => mod);
  const pageHooks = harness('./runtime-page-hooks.jsx').run(mod => mod);
  const rh = harness('./file-resources.jsx', {
    ...timeline, workbenchServices: {events: () => ({subscribe() {}})},
  });
  rh.context.performance = performance;
  rh.context.setTimeout = rh.context.window.setTimeout;
  rh.context.clearTimeout = rh.context.window.clearTimeout;
  const engine = rh.run(mod => mod.WorkbenchChatRuntimes);
  const requests = [], reconnects = [], streams = new Map(), endings = [];
  const model = {
    getChat: () => new Promise((resolve, reject) => requests.push({resolve, reject})),
    sendMessage(id, _input, handlers) {
      streams.set(id, handlers);
      return new Promise(resolve => endings.push(resolve));
    },
    reconnectRun(id) { reconnects.push(id); return new Promise(() => {}); },
  };
  const React = {createElement: (type, props, ...children) => ({type, props: {...props, children}})};
  const h = harness('../shell/assistant-chat.jsx', {
    ...timeline, ...pageHooks, React, useWbcComposerReserveHeight() {},
    workbenchServices: {chat: () => ({Model: model, Runtimes: engine,
      Transcript: 'transcript', Composer: 'composer', QuestionPrompt: 'question'})},
  });
  h.context.setTimeout = h.context.window.setTimeout;
  h.context.clearTimeout = h.context.window.clearTimeout;
  const cache = {details: {a: {id: 'a', status: 'running', messages: []}}};
  h.run((mod, hooks) => {
    Object.assign(React, {useState: hooks.useWbcState, useRef: hooks.useWbcRef, useEffect: hooks.useWbcEffect});
    return mod.AssistantChat({sessionId: 'a', cache});
  });
  return {
    h, engine, model, cache, requests, reconnects, streams,
    card: () => find(h.flush(), 'question'),
    firePoll() { for (const [id, callback] of [...h.timers]) { h.timers.delete(id); callback(); } },
    close() { h.unmount(); endings.forEach(end => end()); },
  };
}

for (const pendingKey of ['pendingQuestion', 'pending_question']) {
  for (const earlyPoll of [false, true]) {
    test(`persisted ${pendingKey} survives runtime removal and stale polling (earlyPoll=${earlyPoll})`, async () => {
      const m = mount();
      const run = m.engine.start('a', {message: user.content}, m.model);
      const stream = m.streams.get('a');
      stream.onAck({runId: 'run', userMessage: user});
      if (earlyPoll) {
        m.requests[0].resolve({id: 'a', status: 'running', messages: [user]});
        await settle();
      }
      stream.onAwaitingUser({type: 'awaiting_user', payload: {pendingQuestion: question}});
      assert.equal(m.card().props.pending, question);
      assert.ok(m.engine.get('a'));
      // Exercise both persisted messages and the terminal timeline fallback.
      stream.onTimeline({version: 1, revision: 1, runId: 'run', status: 'awaiting_user', messages: [message]});
      stream.onAwaitingUser({type: 'awaiting_user', [pendingKey]: question,
        assistantMessages: earlyPoll ? [message] : []});
      assert.equal(m.engine.get('a'), null);
      assert.equal(m.card().props.pending, question);
      assert.equal(m.cache.details.a.pendingQuestion, question);
      assert.equal(m.cache.details.a.runStatus, 'awaiting_user');
      assert.equal(m.cache.details.a.status, 'idle');
      assert.deepEqual(Array.from(m.cache.details.a.messages, item => item.id), ['user', 'question-message']);
      assert.equal(find(m.h.value, 'composer').props.running, false);
      if (!earlyPoll) {
        m.requests[0].resolve({id: 'a', status: 'running', messages: [user]});
        await settle();
        assert.equal(m.card().props.pending, question);
        assert.deepEqual(m.reconnects, []);
        m.firePoll();
      }
      m.requests[1].reject(new Error('offline'));
      await settle();
      assert.equal(m.card().props.pending, question);
      m.firePoll();
      m.requests[2].resolve(idleChat);
      await settle();
      assert.equal(m.card().props.pending, question);
      m.close();
      await run;
    });
  }
}

test('other sessions and live ACP permission requests do not settle the assistant question', async () => {
  const m = mount();
  const otherRun = m.engine.start('b', {message: 'Other session'}, m.model);
  m.streams.get('b').onAwaitingUser({type: 'awaiting_user', pendingQuestion: question});
  assert.equal(m.card(), null);
  assert.equal(m.cache.details.a.pendingQuestion, undefined);
  const run = m.engine.start('a', {message: user.content}, m.model);
  const permission = {...question, kind: 'permission.requested'};
  m.streams.get('a').onAwaitingUser(permission);
  assert.equal(m.card().props.pending, permission);
  assert.ok(m.engine.get('a'));
  assert.equal(m.cache.details.a.pendingQuestion, undefined);
  m.close();
  await Promise.all([run, otherRun]);
});

test('answering the adopted question hides it and a completed reply clears the cached question', async () => {
  const m = mount();
  const run = m.engine.start('a', {message: user.content}, m.model);
  m.streams.get('a').onAwaitingUser({type: 'awaiting_user', pendingQuestion: question, assistantMessages: [message]});
  const answer = m.card().props.onAnswer(question.id, 'A');
  assert.equal(m.card(), null);
  const reply = {...message, id: 'answer', questionPrompt: false, content: 'Selected A'};
  m.streams.get('a').onSaved({type: 'saved', assistantMessages: [reply]});
  assert.equal(m.card(), null);
  assert.equal(m.cache.details.a.pendingQuestion, null);
  assert.equal(m.cache.details.a.runStatus, 'completed');
  m.requests[0].resolve(idleChat);
  await settle();
  assert.equal(m.card(), null);
  m.close();
  await Promise.all([run, answer]);
});
