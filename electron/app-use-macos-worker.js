const { spawn } = require('child_process');
const { StringDecoder } = require('string_decoder');

// Persistent native window enumeration, with workspace notifications on the
// same ordered pipe. Control operations still use their existing providers.
class MacWindowWorker {
  constructor(helperPath, { spawnImpl = spawn } = {}) {
    this.helperPath = helperPath;
    this.spawnImpl = spawnImpl;
    this.child = null;
    this.pending = new Map();
    this.listeners = new Set();
    this.sequence = 0;
    this.restartTimer = null;
    this.closed = false;
  }

  start() {
    if (this.child || this.closed) return;
    const child = this.spawnImpl(this.helperPath, ['--window-worker'], { stdio: ['pipe', 'pipe', 'pipe'] });
    this.child = child;
    const decoder = new StringDecoder('utf8');
    let buffer = '';
    child.stdout.on('data', chunk => {
      buffer += decoder.write(chunk);
      let end;
      while ((end = buffer.indexOf('\n')) >= 0) {
        const line = buffer.slice(0, end); buffer = buffer.slice(end + 1);
        try {
          const message = JSON.parse(line);
          if (message.event === 'targets_changed') {
            for (const listener of this.listeners) listener(message);
          } else {
            const request = this.pending.get(message.requestId);
            if (request) {
              this.pending.delete(message.requestId);
              clearTimeout(request.timer);
              request.resolve(message);
            }
          }
        } catch (error) { this.fail(child, error); return; }
      }
    });
    child.stderr.on('data', () => {});
    child.once('error', error => this.fail(child, error));
    child.once('exit', code => this.fail(child, new Error(`Window worker exited (${code})`)));
  }

  fail(child, error) {
    if (this.child !== child) return;
    this.child = null;
    child.kill();
    for (const request of this.pending.values()) {
      clearTimeout(request.timer); request.reject(error);
    }
    this.pending.clear();
    if (!this.closed && this.listeners.size && !this.restartTimer) {
      this.restartTimer = setTimeout(() => {
        this.restartTimer = null;
        this.start();
        // Refresh after re-establishing observation: changes during a helper
        // restart must not leave stale target identities in the manager.
        for (const listener of this.listeners) listener({ event: 'targets_changed' });
      }, 250);
      this.restartTimer.unref?.();
    }
  }

  watch(listener) {
    this.listeners.add(listener);
    this.start();
    return () => this.listeners.delete(listener);
  }

  request(payload, timeout = 20000) {
    this.start();
    return new Promise((resolve, reject) => {
      if (!this.child) { reject(new Error('Window worker is closed')); return; }
      const child = this.child;
      const requestId = ++this.sequence;
      const timer = setTimeout(() => this.fail(child, new Error('Window enumeration timed out')), timeout);
      this.pending.set(requestId, { resolve, reject, timer });
      child.stdin.write(JSON.stringify({ ...payload, requestId }) + '\n', error => {
        if (error) this.fail(child, error);
      });
    });
  }

  close() {
    this.closed = true;
    clearTimeout(this.restartTimer);
    this.restartTimer = null;
    this.listeners.clear();
    if (this.child) this.fail(this.child, new Error('Window worker stopped'));
  }
}
module.exports = { MacWindowWorker };
