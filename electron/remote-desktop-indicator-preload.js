const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('cyreneRemoteDesktopIndicator', Object.freeze({
  context: () => ipcRenderer.invoke('remote-desktop:indicator-context'),
  disconnect: () => ipcRenderer.invoke('remote-desktop:indicator-disconnect'),
}));
