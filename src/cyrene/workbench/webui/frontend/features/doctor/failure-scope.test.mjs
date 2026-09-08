import test from 'node:test';
import assert from 'node:assert/strict';
import { failureScope } from './failure-scope.mjs';
import { loadReport } from './reports.mjs';
import React from 'react';
import vm from 'node:vm';
import { readFileSync } from 'node:fs';
import { transformSync } from 'esbuild';

test('clicked failure owns the run, including installed legacy terminal errors', () => {
  const chat = { id: 'chat_one', lastRun: { id: 'run_latest', status: 'done', outcome: 'error' } };
  assert.equal(failureScope({}, chat, 'failed').run_id, 'run_latest');
  assert.equal(failureScope({}, chat, { runId: 'run_old' }).run_id, 'run_old');
  assert.equal(failureScope({}, chat, { incidentId: 'incident_old' }).run_id, '');
  assert.equal(failureScope({}, chat, {}).auto_repair, true);
});

test('reopening auto repair reattaches; explicit recheck starts again', async () => {
  const calls = [];
  const scope = { chat_id: 'chat_failure_cache', auto_repair: true };
  const request = async (...args) => { calls.push(args); return { id: 'report_cached', analysis: { status: 'completed' } }; };
  await loadReport(scope, 'zh', request);
  await loadReport(scope, 'zh', request);
  await loadReport(scope, 'zh', request, true);
  assert.deepEqual(calls.map(c => c[0]), ['reports', 'reports/report_cached', 'reports']);
});

test('initial Doctor render accepts an absent report', () => {
  const code = transformSync(readFileSync(new URL('./doctor.jsx', import.meta.url), 'utf8'), { loader: 'jsx', format: 'cjs' }).code;
  const runtime = { ...React, useState: initial => [typeof initial === 'function' ? initial() : initial, () => {}],
    useRef: value => ({ current: value }), useEffect: () => {}, useId: () => 'description' };
  const context = { module: { exports: {} }, React: runtime, require: () => ({
    cachedReport: () => null, useWorkbenchI18n: () => ({ lang: 'en', t: key => key }) }) };
  vm.runInNewContext(code, context);
  assert.ok(context.module.exports.DoctorPanel({ scope: {} }));
});

test('structured failure preserves incident and run metadata', () => {
  const code = transformSync(readFileSync(new URL('../chat/agent-events.jsx', import.meta.url), 'utf8'), { loader: 'jsx', format: 'cjs' }).code;
  const context = { module: { exports: {} }, wbcT: (_, fallback) => fallback, require: () => ({}) };
  vm.runInNewContext(code, context);
  const error = context.module.exports.wbcAgentRunFailedError({ runId: 'run_old', payload: { message: 'failure', incident_id: 'incident_old' } });
  assert.equal(error.runId, 'run_old');
  assert.equal(error.incidentId, 'incident_old');
});

test('failure progress and diagnosis share one card', () => {
  const code = transformSync(readFileSync(new URL('./doctor.jsx', import.meta.url), 'utf8'), { loader: 'jsx', format: 'cjs' }).code;
  const report = { id: 'one', scope: { run_id: 'run_one' }, findings: [], analysis: { status: 'completed', summary: 'The failed stage', user_summary: 'The reply stopped unexpectedly'  },
    failure: { status: 'needs_attention', phase: 'diagnosed', reason: 'host_transition_failed' } };
  const runtime = { ...React, useState: initial => [typeof initial === 'function' ? initial() : initial, () => {}],
    useRef: value => ({ current: value }), useEffect: () => {}, useId: () => 'description' };
  const context = { module: { exports: {} }, React: runtime, require: () => ({ cachedReport: () => report,
    useWorkbenchI18n: () => ({ lang: 'en', t: key => key }) }) };
  vm.runInNewContext(code, context);
  function nodes(node) { return React.isValidElement(node) ? [node, ...React.Children.toArray(node.props.children).flatMap(nodes)] : []; }
  const rendered = nodes(context.module.exports.DoctorPanel({ scope: {} }));
  const cards = rendered.filter(n => n.props.className === 'wb-doctor-analysis');
  assert.equal(cards.length, 1);
  assert.ok(nodes(cards[0]).some(n => n.props.children === 'The reply stopped unexpectedly'));
  assert.ok(!nodes(cards[0]).some(n => n.props.children === 'The failed stage'));
  const technical = rendered.find(n => n.props.className === 'wb-doctor-technical wb-doctor-finding');
  assert.equal(technical.type, 'details');
  assert.ok(!technical.props.open);
  assert.ok(nodes(technical).some(n => n.props.children === 'The failed stage'));
  assert.ok(nodes(technical).some(n => n.type === 'pre' && n.props.children.includes('run_one')));
  assert.ok(nodes(cards[0]).some(n => n.props.className === 'wb-doctor-failure-progress'));
});
