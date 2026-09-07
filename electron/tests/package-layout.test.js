const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const root = path.resolve(__dirname, '..');
const manifest = require('../package.json');
const files = new Set(manifest.build.files);

test('packaged runtime imports, preloads and HTML assets resolve inside the package', () => {
  for (const relative of files) {
    const filename = path.join(root, relative);
    assert.ok(fs.statSync(filename).isFile(), relative);
    const source = fs.readFileSync(filename, 'utf8');
    const dependencies = [];
    if (relative.endsWith('.js')) {
      for (const match of source.matchAll(/require\(['"](\.[^'"]+)['"]\)/g)) {
        dependencies.push(path.extname(match[1]) ? match[1] : match[1] + '.js');
      }
      for (const match of source.matchAll(/path.join\(__dirname, ['"]([^'"]+\.(?:js|html))['"]\)/g)) {
        dependencies.push(match[1]);
      }
    }
    if (relative.endsWith('.html')) {
      for (const match of source.matchAll(/(?:src|href)=["']([^"']+\.(?:js|css))["']/g)) dependencies.push(match[1]);
    }
    for (const dependency of dependencies) {
      const resolved = path.relative(root, path.resolve(path.dirname(filename), dependency));
      assert.ok(files.has(resolved), `${relative} requires unpackaged ${resolved}`);
    }
  }
});

test('build hooks and native source resources survive directory organization', () => {
  for (const relative of [manifest.build.beforePack, manifest.build.deb.afterInstall,
    manifest.build.rpm.afterInstall, 'automation/app-use-macos-hit-test.swift',
    'automation/app-use-macos.jxa', 'automation/app-use-windows.ps1']) {
    assert.ok(fs.statSync(path.join(root, relative)).isFile(), relative);
  }
  const native = manifest.build.extraResources.filter(entry => entry.to.startsWith('app-use/'));
  assert.deepEqual(native.map(entry => entry.to).sort(), [
    'app-use/app-use-macos-hit-test', 'app-use/app-use-macos.jxa', 'app-use/app-use-windows.ps1',
  ]);
});

test('release commands resolve native helper paths on Windows', () => {
  const release = fs.readFileSync(path.join(root, '../.github/workflows/release.yml'), 'utf8');
  const scripts = [...release.matchAll(/-File (electron\\[^\s]+\.ps1)/g)];
  assert.equal(scripts.length, 2);
  for (const match of scripts) {
    assert.ok(fs.statSync(path.resolve(root, '..', match[1].replaceAll('\\', '/'))).isFile());
  }
});
