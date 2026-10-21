'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { test } = require('node:test');
const { buildRelease } = require('./build-release');
const { readPackageJson } = require('app-builder-lib/out/util/packageMetadata');
const { AppInfo } = require('app-builder-lib/out/appInfo');

test('four-part release preserves public and Windows versions through the real builder metadata reader', async () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'cyrene-version-'));
  const manifestPath = path.join(dir, 'package.json');
  const original = JSON.stringify({ name: 'cyrene', version: '0.9.0.1', description: 'Cyrene' });
  fs.writeFileSync(manifestPath, original);
  let metadata;
  try {
    assert.equal(buildRelease(['--win'], { directory: dir, run: (_command, args) => {
      const staged = JSON.parse(fs.readFileSync(manifestPath, 'utf8'));
      assert.equal(staged.version, '0.9.0');
      assert.ok(args.includes('--config.extraMetadata.version=0.9.0.1'));
      assert.ok(args.includes('--config.buildVersion=0.9.0.1'));
      assert.ok(args.includes('--config.buildNumber=1'));
      // Copy the staged input so the asynchronous reader sees it after restoration.
      const stagedPath = path.join(dir, 'staged.json');
      fs.writeFileSync(stagedPath, JSON.stringify(staged));
      metadata = readPackageJson(stagedPath);
      return { status: 0 };
    } }), 0);
    const info = new AppInfo({ metadata: { ...await metadata, version: '0.9.0.1' }, config: { buildVersion: '0.9.0.1', buildNumber: '1' } });
    assert.equal(info.version, '0.9.0.1');
    assert.equal(info.buildVersion, '0.9.0.1');
    assert.equal(info.getVersionInWeirdWindowsForm(), '0.9.0.1');
    assert.equal(info.channel, null);
    assert.equal(fs.readFileSync(manifestPath, 'utf8'), original);
  } finally { fs.rmSync(dir, { recursive: true, force: true }); }
});

for (const fails of [false, true]) test(`packaging restores the manifest after ${fails ? 'an exception' : 'a failed build'}`, () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'cyrene-version-'));
  const manifestPath = path.join(dir, 'package.json');
  const original = '{"name":"cyrene","version":"0.9.0.1"}\n';
  fs.writeFileSync(manifestPath, original);
  const run = () => { if (fails) throw new Error('build failed'); return { status: 7 }; };
  try {
    if (fails) assert.throws(() => buildRelease([], { directory: dir, run }), /build failed/);
    else assert.equal(buildRelease([], { directory: dir, run }), 7);
    assert.equal(fs.readFileSync(manifestPath, 'utf8'), original);
  } finally { fs.rmSync(dir, { recursive: true, force: true }); }
});
