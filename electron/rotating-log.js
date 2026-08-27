'use strict';

const fs = require('node:fs');
const path = require('node:path');

const DEFAULT_MAX_BYTES = 8 * 1024 * 1024;
const DEFAULT_BACKUP_COUNT = 3;

class RotatingFileLog {
  constructor(filePath, options = {}) {
    this.filePath = path.resolve(String(filePath));
    this.maxBytes = Math.max(1024, Number(options.maxBytes) || DEFAULT_MAX_BYTES);
    this.backupCount = Math.max(1, Number(options.backupCount) || DEFAULT_BACKUP_COUNT);
    this.onError = typeof options.onError === 'function' ? options.onError : () => {};
    this.stream = null;
    this.bytes = 0;
    this.rotating = false;
    this.pending = [];
  }

  append(value) {
    const text = String(value || '');
    if (!text) return;
    if (this.rotating) {
      this.pending.push(text);
      return;
    }
    const incomingBytes = Buffer.byteLength(text);
    this._open();
    if (!this.stream) return;
    if (this.bytes > 0 && this.bytes + incomingBytes > this.maxBytes) {
      this.pending.push(text);
      this._beginRotation();
      return;
    }
    this.bytes += incomingBytes;
    this.stream.write(text);
  }

  close() {
    const stream = this.stream;
    this.stream = null;
    if (stream) stream.end();
  }

  _open() {
    if (this.stream || this.rotating) return;
    try {
      fs.mkdirSync(path.dirname(this.filePath), { recursive: true });
      const size = this._fileSize(this.filePath);
      if (size >= this.maxBytes) this._rotateFiles();
      this.bytes = this._fileSize(this.filePath);
      const stream = fs.createWriteStream(this.filePath, { flags: 'a' });
      this.stream = stream;
      stream.on('error', (error) => {
        if (this.stream === stream) this.stream = null;
        this.onError(error);
      });
    } catch (error) {
      this.stream = null;
      this.onError(error);
    }
  }

  _beginRotation() {
    if (this.rotating) return;
    this.rotating = true;
    const stream = this.stream;
    this.stream = null;
    this.bytes = 0;
    const finish = () => {
      try {
        this._rotateFiles();
      } catch (error) {
        this.onError(error);
      }
      this.rotating = false;
      const pending = this.pending;
      this.pending = [];
      for (const text of pending) this.append(text);
    };
    if (stream) stream.end(finish);
    else finish();
  }

  _rotateFiles() {
    const oldest = `${this.filePath}.${this.backupCount}`;
    fs.rmSync(oldest, { force: true });
    for (let index = this.backupCount - 1; index >= 1; index -= 1) {
      const source = `${this.filePath}.${index}`;
      if (fs.existsSync(source)) fs.renameSync(source, `${this.filePath}.${index + 1}`);
    }
    if (fs.existsSync(this.filePath)) {
      const backup = `${this.filePath}.1`;
      fs.renameSync(this.filePath, backup);
      this._trimToLimit(backup);
    }
  }

  _trimToLimit(filePath) {
    const size = this._fileSize(filePath);
    if (size <= this.maxBytes) return;
    const buffer = Buffer.allocUnsafe(this.maxBytes);
    const descriptor = fs.openSync(filePath, 'r');
    try {
      fs.readSync(descriptor, buffer, 0, this.maxBytes, size - this.maxBytes);
    } finally {
      fs.closeSync(descriptor);
    }
    fs.writeFileSync(filePath, buffer);
  }

  _fileSize(filePath) {
    try {
      return fs.statSync(filePath).size;
    } catch (error) {
      if (error && error.code === 'ENOENT') return 0;
      throw error;
    }
  }
}

module.exports = {
  DEFAULT_BACKUP_COUNT,
  DEFAULT_MAX_BYTES,
  RotatingFileLog,
};
