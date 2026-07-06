const { clipboard, contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('cyrene', {
  platform: process.platform,
  version: process.env.npm_package_version || '0.0.0',
  onMenuAction: function (callback) {
    if (typeof callback !== 'function') return function () {};
    var listener = function (_event, action) { callback(action); };
    ipcRenderer.on('menu:action', listener);
    return function () { ipcRenderer.removeListener('menu:action', listener); };
  },
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
  browser: {
    getState: () => ipcRenderer.invoke('browser:get-state'),
    setBounds: (info) => ipcRenderer.invoke('browser:set-bounds', info || {}),
    setContext: (info) => ipcRenderer.invoke('browser:set-context', info || {}),
    setObscured: (obscured) => ipcRenderer.invoke('browser:set-obscured', obscured === true),
    createTab: (info) => ipcRenderer.invoke('browser:create-tab', info || {}),
    activateTab: (tabId) => ipcRenderer.invoke('browser:activate-tab', tabId),
    closeTab: (tabId) => ipcRenderer.invoke('browser:close-tab', tabId),
    navigate: (info) => ipcRenderer.invoke('browser:navigate', info || {}),
    goBack: () => ipcRenderer.invoke('browser:go-back'),
    goForward: () => ipcRenderer.invoke('browser:go-forward'),
    reload: () => ipcRenderer.invoke('browser:reload'),
    setMuted: (info) => ipcRenderer.invoke('browser:set-muted', info || {}),
    onState: (callback) => {
      if (typeof callback !== 'function') return () => {};
      const listener = (_event, state) => callback(state);
      ipcRenderer.on('browser:state', listener);
      return () => ipcRenderer.removeListener('browser:state', listener);
    },
  },
  quickChat: {
    getLaunchContext: () => ipcRenderer.invoke('quick-chat:get-launch-context'),
    getScreenshot: () => ipcRenderer.invoke('quick-chat:get-screenshot'),
    clearScreenshot: () => ipcRenderer.invoke('quick-chat:clear-screenshot'),
    close: () => ipcRenderer.invoke('quick-chat:close'),
    // Renderer measures its content height and asks the main process to size the
    // window to match, so the surface never shows dead space.
    resize: (size) => ipcRenderer.invoke('quick-chat:resize', size || {}),
    openScreenPermissionSettings: () => ipcRenderer.invoke('quick-chat:open-screen-settings'),
    // Quick-chat window → main process → main window: a message was sent so the
    // main workbench can refresh its chat list / navigate to the conversation.
    notifySent: (info) => ipcRenderer.invoke('quick-chat:notify-sent', info || {}),
    onContextUpdated: (callback) => {
      if (typeof callback !== 'function') return () => {};
      const listener = (_event, context) => callback(context);
      ipcRenderer.on('quick-chat:context-updated', listener);
      return () => ipcRenderer.removeListener('quick-chat:context-updated', listener);
    },
    // Subscribed by the main window to learn about quick-chat sends.
    onSent: (callback) => {
      if (typeof callback !== 'function') return () => {};
      const listener = (_event, info) => callback(info);
      ipcRenderer.on('quick-chat:sent', listener);
      return () => ipcRenderer.removeListener('quick-chat:sent', listener);
    },
  },
});
