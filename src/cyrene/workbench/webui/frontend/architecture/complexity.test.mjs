import assert from 'node:assert/strict'
import test from 'node:test'
import { analyzeSource, exceptions, violations } from '../../build/javascript-structure.mjs'

test('nested callbacks own their decisions and Hook counts', () => {
  const result = analyzeSource(`function View(x) {
    useState(0);
    const callback = () => { if (x && x.ready) { while (x) break; } };
    if (x) return callback;
  }`, 'view.jsx')
  assert.equal(result['view.jsx::View'].decisions, 1)
  assert.equal(result['view.jsx::View'].hooks, 1)
  assert.equal(result['view.jsx::View.callback'].decisions, 3)
  assert.equal(result['view.jsx::View.callback'].nesting, 2)
})

test('class methods and instance fields cannot hide behind small functions', () => {
  const result = analyzeSource('class A { set(){this.x=1;this.x=2;} get(){this.y=3;} }', 'a.js')
  assert.deepEqual(result['a.js::A'], { methods: 2, fields: 2 })
})

test('budget applies per metric and cannot be transferred by renaming', () => {
  const baseline = { 'a::f': { lines: 200 } }
  assert.deepEqual(violations({ 'a::f': { lines: 180, decisions: 20 } }, baseline), [])
  assert.equal(violations({ 'a::f': { decisions: 21 } }, baseline).length, 1)
  assert.equal(violations({ 'a::renamed': { lines: 180 } }, baseline).length, 1)
  assert.deepEqual(exceptions({ a: { lines: 90 } }), {})
})

test('parser recovery is visible and new unparsed syntax fails the budget', () => {
  const result = analyzeSource('function broken(', 'a.js')
  assert.ok(result['a.js'].parse_errors > 0)
  assert.ok(violations(result, {}).some(message => message.includes('parse_errors')))
})

test('setter targets count unique destinations and subscriptions belong to their callback scope', () => {
  const result = analyzeSource(`function owner() {
    setActive(null); context.setChats([]); context.setChats([]);
    window.addEventListener('event', () => {
      service.subscribe(listener);
    });
  }`, 'owner.js');
  assert.equal(result['owner.js::owner'].setter_targets, 2);
  assert.equal(result['owner.js::owner'].subscriptions, 1);
  assert.equal(result['owner.js::owner.<anonymous>'].subscriptions, 1);
});
