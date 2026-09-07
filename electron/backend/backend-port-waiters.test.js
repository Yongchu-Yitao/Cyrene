const assert = require('node:assert/strict');
const test = require('node:test');

const { createBackendPortWaiters } = require('./backend-port-waiters');

test('resolves every concurrent waiter when the backend port is published', async () => {
  let currentPort = null;
  const waiters = createBackendPortWaiters(() => currentPort);
  const first = waiters.wait(100);
  const second = waiters.wait(100);

  currentPort = 4242;
  waiters.resolveAll(currentPort);

  assert.deepEqual(await Promise.all([first, second]), [4242, 4242]);
});

test('an expired waiter cannot cancel a newer backend startup waiter', async () => {
  let currentPort = null;
  const waiters = createBackendPortWaiters(() => currentPort);
  const expired = waiters.wait(5);
  const active = waiters.wait(200);

  await assert.rejects(expired, /Timed out waiting for Python backend to start/);
  currentPort = 4242;
  waiters.resolveAll(currentPort);

  assert.equal(await active, 4242);
});

test('returns an already published port without registering a waiter', async () => {
  const waiters = createBackendPortWaiters(() => 4242);
  assert.equal(await waiters.wait(5), 4242);
});
