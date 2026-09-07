const test = require('node:test');
const assert = require('node:assert/strict');
const { EventEmitter } = require('node:events');
const { PassThrough } = require('node:stream');
const { MacWindowWorker } = require('./app-use-macos-worker');
const { AppUseManager } = require('./app-use');

test('native worker reuses process and demultiplexes notifications and replies', async () => {
  let spawns = 0;
  let child;
  const worker = new MacWindowWorker('/helper', { spawnImpl: (_path, args) => {
    spawns++;
    assert.deepEqual(args, ['--window-worker']);
    child = new EventEmitter();
    child.stdout = new PassThrough(); child.stderr = new PassThrough();
    child.stdin = new PassThrough(); child.kill = () => {};
    child.stdin.on('data', data => {
      const request = JSON.parse(String(data));
      child.stdout.write(JSON.stringify({ event:'targets_changed' }) + '\n');
      child.stdout.write(JSON.stringify({ requestId:request.requestId, targets:[{windowTitle:'中文🙂'}] }) + '\n');
    });
    return child;
  }});
  let notifications = 0;
  worker.watch(() => notifications++);
  try {
    const first = await worker.request({operation:'list_targets'});
    const second = await worker.request({operation:'list_targets'});
    assert.deepEqual(first.targets, second.targets);
    assert.equal(first.targets[0].windowTitle, '中文🙂');
    assert.equal(spawns, 1);
    assert.equal(notifications, 2);
  } finally { worker.close(); }
  assert.equal(worker.pending.size, 0);
});

test('notification tracking does not schedule idle enumeration and keeps quick chat origin', async () => {
  let notify;
  let scans = 0;
  let stopped = false;
  const manager = new AppUseManager({ ownPid:1, pollIntervalMs:10, provider:{
    watchTargets:callback => { notify=callback; return () => { stopped=true; }; },
    listTargets:async () => { scans++; return [{pid:2,windowId:'3',appName:'Editor',foreground:true}]; },
    stop() {},
  }});
  manager.start();
  try {
    await new Promise(resolve=>setImmediate(resolve));
    assert.equal(manager.trackerTimer, null);
    const initial = scans;
    await new Promise(resolve=>setTimeout(resolve,40));
    assert.equal(scans, initial);
    notify();
    await new Promise(resolve=>setImmediate(resolve));
    assert.equal(scans, initial+1);
    await manager.captureQuickChatOrigin();
    assert.ok(manager.quickChatOriginTargetId);
    assert.equal(manager.quickChatOriginTargetId, manager.lastExternalTargetId);
  } finally { manager.stop(); }
  assert.equal(stopped,true);
});
