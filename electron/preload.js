const { clipboard, contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('cyrene', {
  platform: process.platform,
  version: process.env.npm_package_version || '0.0.0',
  getDesktopSettings: () => ipcRenderer.invoke('desktop-settings:get'),
  updateDesktopSettings: (updates) => ipcRenderer.invoke('desktop-settings:update', updates),
  showNotification: ({ title, body }) => ipcRenderer.invoke('notification:show', { title, body }),
  writeClipboardText: (text) => {
    clipboard.writeText(String(text == null ? '' : text));
    return true;
  },
  pickDirectory: () => {
    if (process.platform !== 'linux') return Promise.resolve(null);
    return ipcRenderer.invoke('dialog:pick-directory');
  },
  switchUiShell: (mode) => ipcRenderer.invoke('window:switch-shell', mode),
  quickChat: {
    getLaunchContext: () => ipcRenderer.invoke('quick-chat:get-launch-context'),
    getScreenshot: () => ipcRenderer.invoke('quick-chat:get-screenshot'),
    clearScreenshot: () => ipcRenderer.invoke('quick-chat:clear-screenshot'),
    close: () => ipcRenderer.invoke('quick-chat:close'),
    openScreenPermissionSettings: () => ipcRenderer.invoke('quick-chat:open-screen-settings'),
    onContextUpdated: (callback) => {
      if (typeof callback !== 'function') return () => {};
      const listener = (_event, context) => callback(context);
      ipcRenderer.on('quick-chat:context-updated', listener);
      return () => ipcRenderer.removeListener('quick-chat:context-updated', listener);
    },
  },
});
