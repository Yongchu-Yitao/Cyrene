const assert = require('node:assert/strict');
const test = require('node:test');
const { dispatchBrowserCommand } = require('./browser-rpc');

test('argument-object browser commands preserve identity, defaults and manager receiver', () => {
  for (const name of ['setBounds', 'setChatOverlay', 'setTabPicker', 'inspect', 'visibleLinkMatches', 'navigationGuard',
    'click', 'clickRef', 'clickText', 'clickAt', 'type', 'typeRef', 'waitFor', 'networkLog', 'screenshot',
    'prepareUpload', 'setInputFiles', 'reload', 'setMuted', 'scroll']) {
    let received;
    const manager = { [name](args) { assert.equal(this, manager); received = args; return 'result'; } };
    const args = { tabId: 'tab' };
    assert.equal(dispatchBrowserCommand(manager, name, args), 'result');
    assert.equal(received, args);
    dispatchBrowserCommand(manager, name, null);
    assert.deepEqual(received, {});
  }
});

test('navigation overwrites claimed ownership and creation awaits before returning state', async () => {
  const calls = [];
  let finish;
  const manager = {
    createTab(args) { calls.push(args); return new Promise(resolve => { finish = resolve; }); },
    state() { calls.push('state'); return 'state'; },
    navigate(args) { return args; }, openLocalFile(args) { return args; },
  };
  for (const method of ['navigate', 'openLocalFile']) {
    assert.equal(dispatchBrowserCommand(manager, method, { agentOwnerRoundId: 'forged' }, 'real').agentOwnerRoundId, 'real');
    assert.equal(dispatchBrowserCommand(manager, method, { agentOwnerRoundId: 'forged' }, '').agentOwnerRoundId, '');
  }
  const result = dispatchBrowserCommand(manager, 'createTab', { url: 'about:blank' }, 'round');
  assert.deepEqual(calls, [{ url: 'about:blank', agentOwnerRoundId: 'round' }]);
  finish();
  assert.equal(await result, 'state');
  assert.equal(calls[1], 'state');
});

test('scalar, snapshot, no-argument and obscured commands retain protocol shapes', () => {
  const manager = { pageSnapshot: (...args) => args, activateTab: id => id, closeTab: id => id };
  assert.deepEqual(dispatchBrowserCommand(manager, 'snapshot', { tabId: 'a', maxChars: 42 }), ['a', 42]);
  assert.deepEqual(dispatchBrowserCommand(manager, 'snapshot', null), [null, null]);
  for (const name of ['activateTab', 'closeTab']) assert.equal(dispatchBrowserCommand(manager, name, { tabId: 'a' }), 'a');
  for (const name of ['state', 'goBack', 'goForward']) {
    manager[name] = (...args) => args;
    assert.deepEqual(dispatchBrowserCommand(manager, name, { ignored: true }), []);
  }
  assert.equal(dispatchBrowserCommand(manager, 'setObscured', { obscured: false }, '', value => value), false);
});

test('unknown methods including prototype properties cannot invoke manager capabilities', () => {
  const manager = new Proxy({}, { get() { throw Error('must not access manager'); } });
  for (const name of ['constructor', '__proto__', 'toString', 'closeAll', 123]) {
    assert.deepEqual(dispatchBrowserCommand(manager, name), { ok: false, error: `Unknown browser RPC method: ${name}` });
  }
});
