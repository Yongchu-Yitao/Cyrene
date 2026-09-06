const test = require('node:test');
const assert = require('node:assert/strict');
const { EventEmitter } = require('node:events');
const { doctorArgs, runOfflineDoctor } = require('./doctor-recovery');

test('offline invocation works for development and packaged entry points', () => {
  assert.deepEqual(doctorArgs(['run', 'cyrene', '--workbench']), ['run', 'cyrene', 'doctor', '--offline', '--json']);
  assert.deepEqual(doctorArgs(['--launch-web', '--electron']), ['doctor', '--offline', '--json']);
});
function child() { const value = new EventEmitter(); value.stdout = new EventEmitter(); value.stderr = new EventEmitter(); value.kill = () => {}; return value; }
test('Python unavailable still returns a direction', async () => {
  const proc = child();
  const pending = runOfflineDoctor('missing', [], {}, () => proc);
  proc.emit('error', new Error('not found'));
  assert.equal((await pending).reason, 'python_unavailable');
});
test('diagnostic findings returned even when command exits with detected problems', async () => {
  const proc = child();
  const pending = runOfflineDoctor('python', [], {}, () => proc);
  proc.stdout.emit('data', JSON.stringify({ findings: [{ status: 'failed' }] })); proc.emit('exit', 1);
  assert.equal((await pending).report.findings[0].status, 'failed');
});
test('invalid output returns fallback without exposing stderr', async () => {
  const proc = child();
  const pending = runOfflineDoctor('python', [], {}, () => proc);
  proc.stderr.emit('data', 'Bearer secret'); proc.stdout.emit('data', 'invalid'); proc.emit('exit', 2);
  const result = await pending;
  assert.equal(result.reason, 'diagnostic_failed'); assert.ok(!JSON.stringify(result).includes('secret'));
});
