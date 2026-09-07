const { spawn } = require('node:child_process');
const { createBackendPortWaiters } = require('./backend-port-waiters');

// Owns process state and termination; the desktop owns dialogs and windows.
class BackendProcess {
  constructor(callbacks, { spawnProcess = spawn, windows = process.platform === 'win32', schedule = setTimeout } = {}) {
    this.callbacks = callbacks;
    this.spawnProcess = spawnProcess;
    this.windows = windows;
    this.schedule = schedule;
    this.process = null;
    this.port = null;
    this.restarting = false;
    this.waiters = createBackendPortWaiters(() => this.port);
  }

  start() {
    if (this.process) return;
    this.callbacks.clearConnection();
    const { command, args, options } = this.callbacks.launch();
    this.process = this.spawnProcess(command, args, options);
    this.process.stdout.on('data', data => {
      const text = data.toString();
      const match = text.match(/^PORT=(\d+)$/m);
      if (match) {
        this.port = parseInt(match[1], 10);
        this.callbacks.publishConnection(this.port);
        this.waiters.resolveAll(this.port);
      }
      this.callbacks.stdout(text);
    });
    this.process.stderr.on('data', data => this.callbacks.stderr(data.toString()));
    this.process.on('error', error => {
      this.callbacks.error(error);
      this.waiters.resolveAll(null);
      this.port = null;
      this.callbacks.unavailable();
    });
    this.process.on('exit', code => {
      this.callbacks.logExit(code);
      this.process = null;
      this.port = null;
      this.callbacks.clearConnection();
      const restarting = this.restarting;
      if (restarting) this.restarting = false;
      this.callbacks.exit(code, restarting);
    });
  }

  restart() {
    if (!this.process) {
      this.start();
      this.callbacks.reveal();
      return;
    }
    this.restarting = true;
    const child = this.process;
    this.callbacks.invalidateWindows();
    try { this.terminate(child); }
    catch (_) { this.restarting = false; }
  }

  stop() {
    if (!this.process) return;
    this.callbacks.stopping();
    const child = this.process;
    this.process = null;
    this.callbacks.clearConnection();
    try { this.terminate(child); } catch (_) { /* preserve best-effort shutdown */ }
  }

  terminate(child) {
    if (this.windows) {
      // Do not kill the tree: the detached Terminal Daemon must keep its PTYs.
      const taskkill = this.spawnProcess('taskkill', ['/pid', String(child.pid), '/f'], {
        stdio: 'ignore', windowsHide: true,
      });
      taskkill.unref();
    } else {
      child.kill('SIGTERM');
      this.schedule(() => {
        try { if (child.exitCode === null) child.kill('SIGKILL'); } catch (_) {}
      }, 5000);
    }
  }

  waitForPort(timeoutMs = 30000) { return this.waiters.wait(timeoutMs); }
}

module.exports = { BackendProcess };
