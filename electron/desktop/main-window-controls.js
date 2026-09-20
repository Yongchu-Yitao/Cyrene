// macOS retains its existing traffic-light configuration in main.js.
function mainWindowChromeOptions(platform) {
  if (platform === 'win32') {
    return {
      titleBarStyle: 'hidden',
      titleBarOverlay: { height: 58, color: '#f5f6f8', symbolColor: '#24292f' },
    };
  }
  // Linux WCO follows the desktop's button placement preference. Draw our own
  // controls so they stay on the right even with left-sided GNOME/KDE layouts.
  if (platform === 'linux') return { frame: false };
  return {};
}

function installMainWindowControls({ ipcMain, getMainWindow, platform }) {
  ipcMain.handle('main-window:controls', (event, info = {}) => {
    if (!info || typeof info !== 'object') return { ok: false };
    const win = getMainWindow();
    if (!['win32', 'linux'].includes(platform) || !win || win.isDestroyed()
        || event.sender !== win.webContents || event.senderFrame !== win.webContents.mainFrame) {
      return { ok: false };
    }
    switch (info.action) {
      case 'state': break;
      case 'minimize': win.minimize(); break;
      case 'toggle-maximize':
        if (win.isFullScreen()) win.setFullScreen(false);
        else if (win.isMaximized()) win.unmaximize();
        else win.maximize();
        break;
      // Respect main.js's close-to-background handler.
      case 'close': win.close(); return { ok: true };
      case 'overlay':
        if (platform !== 'win32') return { ok: false };
        if (!/^#[0-9a-f]{6}$/i.test(info.color)
            || !/^#[0-9a-f]{6}$/i.test(info.symbolColor)
            || !Number.isInteger(info.height) || info.height < 28 || info.height > 120) {
          return { ok: false };
        }
        win.setTitleBarOverlay({ color: info.color, symbolColor: info.symbolColor, height: info.height });
        break;
      default: return { ok: false };
    }
    return { ok: true, maximized: win.isMaximized(), fullscreen: win.isFullScreen() };
  });
}

function observeMainWindowControls(win, platform) {
  if (!['win32', 'linux'].includes(platform)) return;
  const notify = () => {
    if (!win.isDestroyed() && !win.webContents.isDestroyed()) {
      win.webContents.send('main-window:state', {
        ok: true, maximized: win.isMaximized(), fullscreen: win.isFullScreen(),
      });
    }
  };
  for (const name of ['maximize', 'unmaximize', 'enter-full-screen', 'leave-full-screen']) {
    win.on(name, notify);
  }
}

module.exports = { mainWindowChromeOptions, installMainWindowControls, observeMainWindowControls };
