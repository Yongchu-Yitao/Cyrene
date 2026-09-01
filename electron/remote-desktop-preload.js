const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('cyreneRemoteDesktopHost', {
  onStart(callback) {
    if (typeof callback !== 'function') return () => {};
    const listener = (_event, payload) => callback(payload || {});
    ipcRenderer.on('remote-desktop:start', listener);
    return () => ipcRenderer.removeListener('remote-desktop:start', listener);
  },
  onCommand(callback) {
    if (typeof callback !== 'function') return () => {};
    const listener = (_event, payload) => callback(payload || {});
    ipcRenderer.on('remote-desktop:command', listener);
    return () => ipcRenderer.removeListener('remote-desktop:command', listener);
  },
  onClipboard(callback) {
    if (typeof callback !== 'function') return () => {};
    const listener = (_event, payload) => callback(payload || {});
    ipcRenderer.on('remote-desktop:clipboard', listener);
    return () => ipcRenderer.removeListener('remote-desktop:clipboard', listener);
  },
  onClipboardImageOffer(callback) {
    if (typeof callback !== 'function') return () => {};
    const listener = (_event, payload) => callback(payload || {});
    ipcRenderer.on('remote-desktop:clipboard-image-offer', listener);
    return () => ipcRenderer.removeListener('remote-desktop:clipboard-image-offer', listener);
  },
  onClipboardFileOffer(callback) {
    if (typeof callback !== 'function') return () => {};
    const listener = (_event, payload) => callback(payload || {});
    ipcRenderer.on('remote-desktop:clipboard-file-offer', listener);
    return () => ipcRenderer.removeListener('remote-desktop:clipboard-file-offer', listener);
  },
  answer(payload) {
    ipcRenderer.send('remote-desktop:answer', payload || {});
  },
  input(payload) {
    ipcRenderer.send('remote-desktop:input', payload || {});
  },
  control(payload) {
    ipcRenderer.send('remote-desktop:control', payload || {});
  },
  state(payload) {
    ipcRenderer.send('remote-desktop:state', payload || {});
  },
});
