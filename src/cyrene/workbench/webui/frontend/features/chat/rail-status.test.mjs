import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'
import vm from 'node:vm'

const rail = readFileSync(new URL('./rail.jsx', import.meta.url), 'utf8');
const model = readFileSync(new URL('./rail-model.jsx', import.meta.url), 'utf8');
const context = {
  runningChatIds: {},
  WBC_ICONS: { running: 'running', alert: 'alert', errorCircle: 'error', check: 'check', file: 'file' },
  wbcT: (_key, fallback) => fallback,
};
vm.createContext(context);
vm.runInContext(
  model.slice(model.indexOf('function wbcConversationTrackRawStatus('), model.indexOf('function wbcConversationTrackIsCompleted('))
  + rail.slice(rail.indexOf('  function chatRailVisualState('), rail.indexOf('  function prepareRailDragImage(')), context);
const state = chat => context.chatRailVisualState(chat);

test('new work replaces the waiting icon and color even before the summary refreshes', () => {
  const chat = { id: 'chat', runStatus: 'awaiting_user', awaitingUser: true, pendingQuestion: { id: 'approval' } };
  assert.equal(state(chat).icon, 'alert');
  context.runningChatIds.chat = true;
  try {
    assert.equal(state(chat).icon, 'running');
    assert.equal(state(chat).tone, ' status-running');
    assert.equal(state(chat).label, '');
  } finally { delete context.runningChatIds.chat; }
  assert.equal(state(chat).icon, 'alert');
  assert.equal(state({ id: 'chat', runStatus: 'completed' }).icon, 'check');
});

test('resumed lifecycle outranks residual question and previous failure fields', () => {
  for (const runStatus of ['running', 'resumed', 'planning', 'initializing', 'finishing']) {
    assert.equal(state({ id: 'chat', runStatus, pendingQuestion: { id: 'question' }, failed: true }).icon, 'running');
  }
  assert.equal(state({ id: 'chat', runStatus: 'failed' }).icon, 'error');
  assert.equal(state({ id: 'chat', runStatus: 'waiting_for_approval' }).icon, 'alert');
});
