'use strict';

const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const test = require('node:test');
const { once } = require('node:events');
const { RotatingFileLog } = require('./rotating-log');

async function waitForFile(filePath) {
  for (let attempt = 0; attempt < 100; attempt += 1) {
    if (fs.existsSync(filePath)) return;
    await new Promise((resolve) => setTimeout(resolve, 5));
  }
  throw new Error(`timed out waiting for ${filePath}`);
}

test('rotating log bounds oversized history and preserves new writes', async (t) => {
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'cyrene-rotating-log-'));
  t.after(() => fs.rmSync(directory, { recursive: true, force: true }));
  const filePath = path.join(directory, 'cyrene_error.log');
  fs.writeFileSync(filePath, `old-start\n${'x'.repeat(4096)}\nold-end\n`);

  const log = new RotatingFileLog(filePath, { maxBytes: 1024, backupCount: 2 });
  log.append('new-entry\n');
  await waitForFile(filePath);
  const stream = log.stream;
  log.close();
  if (stream && !stream.writableFinished) await once(stream, 'finish');

  assert.equal(fs.readFileSync(filePath, 'utf8'), 'new-entry\n');
  assert.ok(fs.statSync(`${filePath}.1`).size <= 1024);
  assert.match(fs.readFileSync(`${filePath}.1`, 'utf8'), /old-end/);
});

test('rotating log keeps only the configured backup count', async (t) => {
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'cyrene-rotating-log-'));
  t.after(() => fs.rmSync(directory, { recursive: true, force: true }));
  const filePath = path.join(directory, 'cyrene_error.log');
  const log = new RotatingFileLog(filePath, { maxBytes: 1024, backupCount: 2 });

  for (let round = 0; round < 4; round += 1) {
    log.append(`${round}:${'x'.repeat(900)}\n`);
    log.append(`${round}:rotate\n`);
    while (log.rotating) await new Promise((resolve) => setTimeout(resolve, 5));
  }
  const stream = log.stream;
  log.close();
  if (stream && !stream.writableFinished) await once(stream, 'finish');

  assert.equal(fs.existsSync(`${filePath}.3`), false);
  assert.ok(fs.statSync(`${filePath}.1`).size <= 1024);
  assert.ok(fs.statSync(`${filePath}.2`).size <= 1024);
});
