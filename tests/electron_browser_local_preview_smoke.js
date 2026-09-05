const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const path = require('node:path');
const os = require('node:os');
const { app, BrowserWindow, session } = require('electron');
const { createLocalPreview } = require('../electron/browser-local-preview');

(async () => {
  await app.whenReady();
  const root = await fs.mkdtemp(path.join(os.tmpdir(), 'cyrene-preview-smoke-'));
  let preview;
  let win;
  try {
    await fs.writeFile(path.join(root, 'index.html'), '<title>Local preview</title><link rel="stylesheet" href="style.css"><button id="counter">0</button><script src="app.js"></script>');
    await fs.writeFile(path.join(root, 'style.css'), 'button { color: rgb(255, 0, 0); }');
    await fs.writeFile(path.join(root, 'app.js'), 'localStorage.setItem("preview", "yes"); counter.onclick = () => counter.textContent = Number(counter.textContent) + 1;');
    preview = await createLocalPreview(path.join(root, 'index.html'), root);
    const partition = 'preview-smoke';
    session.fromPartition(partition).webRequest.onBeforeRequest((details, callback) => callback({ cancel: !preview.allows(details.url) }));
    win = new BrowserWindow({ show: false, webPreferences: { partition, sandbox: true, contextIsolation: true, nodeIntegration: false } });
    await win.loadURL(preview.url);
    assert.equal(win.webContents.getTitle(), 'Local preview');
    const result = await win.webContents.executeJavaScript(`(() => {
      counter.click();
      return [counter.textContent, getComputedStyle(counter).color, localStorage.getItem('preview'), typeof require];
    })()`);
    assert.deepEqual(result, ['1', 'rgb(255, 0, 0)', 'yes', 'undefined']);
    assert.equal((await win.webContents.capturePage()).isEmpty(), false);
    await assert.rejects(win.loadURL('http://127.0.0.1:1/private'));
    console.log('PASS: local HTML, relative JS/CSS, click, storage, screenshot and network isolation');
  } finally {
    if (win) win.destroy();
    if (preview) preview.close();
    await fs.rm(root, { recursive: true, force: true });
  }
})().then(() => app.exit(0), error => { console.error(error); app.exit(1); });
