const assert = require('node:assert/strict');
const { EventEmitter } = require('node:events');
const test = require('node:test');
const { BackendProcess } = require('./backend-process');

function harness(windows = false) {
  const calls = [], children = [], timers = [];
  const callbacks = Object.fromEntries(['clearConnection', 'publishConnection', 'stdout', 'stderr', 'error', 'unavailable', 'logExit', 'exit', 'reveal', 'invalidateWindows', 'stopping']
    .map(name => [name, (...args) => calls.push([name, ...args])]));
  callbacks.launch = () => ({ command: 'python', args: ['--electron'], options: { env: { CYRENE_AUTH_TOKEN: 'test' } } });
  const backend = new BackendProcess(callbacks, {
    windows,
    schedule(fn, delay) { timers.push({ fn, delay }); },
    spawnProcess(...args) {
      calls.push(['spawn', ...args]);
      const child = new EventEmitter();
      Object.assign(child, { stdout: new EventEmitter(), stderr: new EventEmitter(), pid: 41 + children.length, exitCode: null,
        kill(signal) { calls.push(['kill', this.pid, signal]); },
        unref() { calls.push(['unref', this.pid]); },
      });
      children.push(child);
      return child;
    },
  });
  return { backend, calls, callbacks, children, timers };
}

test('single process publishes port before resolving early and late waiters', async () => {
  const h = harness();
  h.backend.start(); h.backend.start();
  assert.equal(h.children.length, 1);
  const early = h.backend.waitForPort(1000);
  h.children[0].stdout.emit('data', Buffer.from('ready\nPORT=3210\n'));
  assert.equal(await early, 3210);
  assert.equal(await h.backend.waitForPort(), 3210);
  assert.equal(h.calls.find(call => call[0] === 'publishConnection')[1], 3210);
  assert.equal(h.backend.process, h.children[0]);
});

test('startup error reports first, releases waiters, and opens recovery without an exit', async () => {
  const h = harness(); h.backend.start();
  const ready = h.backend.waitForPort(1000);
  const error = new Error('spawn failed');
  h.children[0].emit('error', error);
  assert.equal(await ready, null);
  assert.deepEqual(h.calls.slice(-2), [['error', error], ['unavailable']]);
  assert.equal(h.backend.port, null);
});

test('restart invalidates windows before termination and reports restart on exit', () => {
  const h = harness(); h.backend.start(); h.backend.restart();
  assert.equal(h.backend.restarting, true);
  assert.deepEqual(h.calls.slice(-2), [['invalidateWindows'], ['kill', 41, 'SIGTERM']]);
  assert.equal(h.timers[0].delay, 5000);
  h.children[0].exitCode = 0;
  h.children[0].emit('exit', 0);
  h.timers[0].fn();
  assert.equal(h.backend.process, null);
  assert.equal(h.backend.restarting, false);
  assert.deepEqual(h.calls.slice(-3), [['logExit', 0], ['clearConnection'], ['exit', 0, true]]);
});

test('stop is idempotent and escalates only a still-running child', () => {
  const h = harness(); h.backend.start(); h.backend.stop(); h.backend.stop();
  assert.equal(h.backend.process, null);
  assert.equal(h.timers.length, 1);
  h.timers[0].fn();
  assert.deepEqual(h.calls.slice(-1), [['kill', 41, 'SIGKILL']]);
});

test('Windows stop and restart kill only the backend, never its detached PTY descendants', () => {
  for (const action of ['stop', 'restart']) {
    const h = harness(true); h.backend.start(); h.backend[action]();
    assert.deepEqual(h.calls.find(call => call[0] === 'spawn' && call[1] === 'taskkill'),
      ['spawn', 'taskkill', ['/pid', '41', '/f'], { stdio: 'ignore', windowsHide: true }]);
    assert.equal(h.timers.length, 0);
    assert.deepEqual(h.calls.slice(-1), [['unref', 42]]);
  }
});

test('failed restart termination clears the restart flag and preserves the child', () => {
  const h = harness(); h.backend.start();
  h.children[0].kill = () => { throw new Error('denied'); };
  h.backend.restart();
  assert.equal(h.backend.restarting, false);
  assert.equal(h.backend.process, h.children[0]);
});

test('restart with no process starts and reveals once; update exit code is forwarded unchanged', () => {
  const h = harness(); h.backend.restart();
  assert.deepEqual(h.calls.slice(-1), [['reveal']]);
  h.children[0].emit('exit', 42);
  assert.deepEqual(h.calls.slice(-1), [['exit', 42, false]]);
});
