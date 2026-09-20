import assert from 'node:assert/strict'
import test from 'node:test'
import { wbcChatRailVisualState } from './rail-status.mjs'

const context = {
  runningChatIds: {},
  WBC_ICONS: { running: 'running', alert: 'alert', errorCircle: 'error', check: 'check', file: 'file' },
  wbcT: (_key, fallback) => fallback,
};
const state = chat => wbcChatRailVisualState(chat, Boolean(context.runningChatIds[chat.id]) || ['running', 'resumed', 'planning', 'initializing', 'finishing'].includes(chat.runStatus), context.WBC_ICONS, context.wbcT);

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
