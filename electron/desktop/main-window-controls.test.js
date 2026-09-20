const { test } = require('node:test');
const assert = require('node:assert/strict');
const { EventEmitter } = require('node:events');
const { mainWindowChromeOptions, installMainWindowControls, observeMainWindowControls } = require('./main-window-controls');

function fixture(platform = 'linux') {
  let handler;
  const calls = [];
  const win = new EventEmitter();
  let maximized = false;
  Object.assign(win, {
    isDestroyed: () => false,
    isMaximized: () => maximized,
    isFullScreen: () => false,
    minimize: () => calls.push('minimize'),
    maximize: () => { maximized = true; win.emit('maximize'); },
    unmaximize: () => { maximized = false; win.emit('unmaximize'); },
    close: () => calls.push('close'),
    setTitleBarOverlay: options => calls.push(options),
    webContents: { mainFrame: {}, isDestroyed: () => false, send: (...args) => calls.push(args) },
  });
  installMainWindowControls({ platform, getMainWindow: () => win,
    ipcMain: { handle: (_channel, callback) => { handler = callback; } } });
  const event = { sender: win.webContents, senderFrame: win.webContents.mainFrame };
  return { win, calls, event, handler, invoke: info => handler(event, info) };
}

test('only Windows and Linux change window chrome; macOS options remain untouched', () => {
  assert.deepEqual(mainWindowChromeOptions('darwin'), {});
  assert.deepEqual(mainWindowChromeOptions('linux'), { frame: false });
  const windows = mainWindowChromeOptions('win32');
  assert.equal(windows.titleBarStyle, 'hidden');
  assert.equal(windows.titleBarOverlay.height, 58);
  assert.equal(windows.frame, undefined); // Keep native resize/Snap behavior.
});

test('minimize, maximize/restore and close use the existing window lifecycle', () => {
  const f = fixture();
  assert.equal(f.invoke({ action: 'toggle-maximize' }).maximized, true);
  assert.equal(f.invoke({ action: 'toggle-maximize' }).maximized, false);
  f.invoke({ action: 'minimize' });
  f.invoke({ action: 'close' });
  assert.deepEqual(f.calls, ['minimize', 'close']);
  f.win.isFullScreen = () => true;
  f.win.setFullScreen = value => f.calls.push(['fullscreen', value]);
  f.invoke({ action: 'toggle-maximize' });
  assert.deepEqual(f.calls.at(-1), ['fullscreen', false]);
});

test('other windows, subframes and macOS cannot control the main window', () => {
  const f = fixture();
  assert.equal(f.invoke(null).ok, false);
  assert.equal(f.handler({ ...f.event, sender: {} }, { action: 'close' }).ok, false);
  assert.equal(f.handler({ ...f.event, senderFrame: {} }, { action: 'close' }).ok, false);
  assert.equal(fixture('darwin').invoke({ action: 'close' }).ok, false);
  f.win.isDestroyed = () => true;
  assert.equal(f.invoke({ action: 'close' }).ok, false);
  assert.deepEqual(f.calls, []);
});

test('Windows overlay accepts validated theme updates only', () => {
  const f = fixture('win32');
  const update = { action: 'overlay', color: '#101112', symbolColor: '#eeeeee', height: 58 };
  assert.equal(f.invoke(update).ok, true);
  assert.deepEqual(f.calls, [{ color: '#101112', symbolColor: '#eeeeee', height: 58 }]);
  assert.equal(f.invoke({ ...update, height: -1 }).ok, false);
  assert.equal(f.invoke({ ...update, color: 'invalid' }).ok, false);
  assert.equal(fixture().invoke(update).ok, false);
});

test('OS-driven window state changes update controls; macOS has no listeners', () => {
  const f = fixture();
  observeMainWindowControls(f.win, 'linux');
  f.win.maximize();
  f.win.unmaximize();
  assert.equal(f.calls[0][1].maximized, true);
  assert.equal(f.calls[1][1].maximized, false);
  const mac = fixture('darwin');
  observeMainWindowControls(mac.win, 'darwin');
  assert.deepEqual(mac.win.eventNames(), []);
});
