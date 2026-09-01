const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('cyreneRemoteDesktopIndicator', Object.freeze({
  context: () => ipcRenderer.invoke('remote-desktop:indicator-context'),
  disconnect: () => ipcRenderer.invoke('remote-desktop:indicator-disconnect'),
  onThemeChanged: (callback) => {
    if (typeof callback !== 'function') return () => {};
    const listener = (_event, theme) => callback(String(theme || ''));
    ipcRenderer.on('remote-desktop:indicator-theme', listener);
    return () => ipcRenderer.removeListener('remote-desktop:indicator-theme', listener);
  },
}));
