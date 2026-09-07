const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const os = require('node:os');
const path = require('node:path');
const { createLocalPreview } = require('./browser-local-preview');

test('serves HTML and relative assets, denies escapes, and closes', async () => {
  const root = await fs.mkdtemp(path.join(os.tmpdir(), 'cyrene-preview-'));
  let preview;
  try {
    const dir = path.join(root, 'site');
    await fs.mkdir(dir);
    await fs.writeFile(path.join(dir, '你好 demo.html'), '<script src="app.js"></script>');
    await fs.writeFile(path.join(dir, 'app.js'), 'window.loaded = true;');
    await fs.writeFile(path.join(dir, '.hidden.json'), '{}');
    await fs.writeFile(path.join(root, 'outside.json'), '{"secret":true}');
    await fs.symlink(path.join(root, 'outside.json'), path.join(dir, 'escape.json'));
    await fs.symlink(path.join(dir, '.hidden.json'), path.join(dir, 'hidden-link.json'));
    preview = await createLocalPreview(path.join(dir, '你好 demo.html'), root);
    assert.match(await (await fetch(preview.url)).text(), /app.js/);
    const asset = new URL('app.js', preview.url);
    assert.equal((await fetch(asset)).headers.get('content-type'), 'text/javascript');
    for (const name of ['escape.json', 'hidden-link.json', '.hidden.json', '../outside.json', '%2e%2e%2foutside.json']) {
      assert.notEqual((await fetch(new URL(name, preview.url))).status, 200);
    }
    assert.equal((await fetch(asset, { method: 'POST' })).status, 403);
    assert.equal(preview.allows('https://example.com'), false);
    assert.equal(preview.allows('http://127.0.0.1:1/admin'), false);
    assert.equal(preview.allows(asset.href), true);
    await assert.rejects(createLocalPreview(path.join(root, 'outside.json'), dir));
    preview.close();
    await assert.rejects(fetch(preview.url));
  } finally {
    if (preview) preview.close();
    await fs.rm(root, { recursive: true, force: true });
  }
});
