import test from 'node:test';
import assert from 'node:assert/strict';
import React from 'react';
import vm from 'node:vm';
import { readFileSync } from 'node:fs';
import { transformSync } from 'esbuild';

const code = transformSync(readFileSync(new URL('./repair.jsx', import.meta.url), 'utf8'), { loader: 'jsx', format: 'cjs' }).code;
function elements(node) {
  if (!React.isValidElement(node)) return [];
  return [node, ...React.Children.toArray(node.props.children).flatMap(elements)];
}
function render(plan, request) {
  const values = ['custom.py', plan, false, ''];
  const runtime = { ...React, useState: () => [values.shift(), () => {}], useRef: () => ({ current: true }), useEffect: () => {} };
  const context = { module: { exports: {} }, React: runtime, require: () => ({ useWorkbenchI18n: () => ({ t: key => key }) }) };
  vm.runInNewContext(code, context);
  return elements(context.module.exports.DoctorRepair({ report: { id: 'report', scope: {}, online: true,
    plugin_targets: ['custom.py'], repair_executor: { mode: 'static_only' }, analysis: { status: 'idle' } }, description: 'Wrong result', request }));
}

test('reviewed patch submission binds the exact hash and preserves user description', async () => {
  const calls = [];
  const plan = { id: 'repair_one', status: 'planned', action: { target: 'custom.py' }, plan_hash: 'a'.repeat(64), diff: '-wrong\n+correct' };
  const nodes = render(plan, async (...args) => { calls.push(args); return plan; });
  const apply = nodes.find(n => n.type === 'button' && n.props.children === 'doctor.applyRepair');
  await apply.props.onClick();
  assert.equal(calls[0][0], 'repairs/repair_one/apply');
  assert.equal(calls[0][2].expected_plan_hash, plan.plan_hash);
  const generate = nodes.find(n => n.type === 'button' && n.props.children === 'doctor.generateRepair');
  await generate.props.onClick();
  assert.equal(calls[1][2].description, 'Wrong result');
  assert.equal(calls[1][2].target, 'custom.py');
  assert.ok(nodes.some(n => n.props.children === 'doctor.staticOnly'));
});

test('unverified outcome is explicit and an interrupted commit exposes rollback', () => {
  const nodes = render({ id: 'repair_one', status: 'interrupted', action: { target: 'custom.py' }, can_rollback: true,
    outcome: { status: 'unverified', basis: 'static_only' } }, async () => {});
  assert.ok(nodes.some(n => n.type === 'button' && n.props.children === 'doctor.rollback'));
  assert.ok(nodes.some(n => n.props.role === 'status' && React.Children.toArray(n.props.children).includes('doctor.outcome.unverified')));
});
