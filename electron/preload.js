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
  showItemInFolder: (filePath) => ipcRenderer.invoke('shell:show-item-in-folder', {
    path: String(filePath == null ? '' : filePath),
  }),
  pickDirectory: () => {
    if (process.platform !== 'linux') return Promise.resolve(null);
    return ipcRenderer.invoke('dialog:pick-directory');
  },
  pickBackupSavePath: (options) => ipcRenderer.invoke('dialog:pick-backup-save-path', options || {}),
  pickBackupFile: (options) => ipcRenderer.invoke('dialog:pick-backup-file', options || {}),
  browser: {
    getState: (sessionId) => ipcRenderer.invoke('browser:get-state', { sessionId: String(sessionId || '') }),
    setBounds: (info) => ipcRenderer.invoke('browser:set-bounds', info || {}),
    setChatOverlay: (info) => ipcRenderer.invoke('browser:set-chat-overlay', info || {}),
    setTabPicker: (info) => ipcRenderer.invoke('browser:set-tab-picker', info || {}),
    setContext: (info) => ipcRenderer.invoke('browser:set-context', info || {}),
    setObscured: (info) => ipcRenderer.invoke(
      'browser:set-obscured',
      info && typeof info === 'object' ? info : { obscured: info === true }
    ),
    createTab: (info) => ipcRenderer.invoke('browser:create-tab', info || {}),
    activateTab: (info) => ipcRenderer.invoke('browser:activate-tab', info || {}),
    closeTab: (info) => ipcRenderer.invoke('browser:close-tab', info || {}),
    navigate: (info) => ipcRenderer.invoke('browser:navigate', info || {}),
    goBack: (sessionId) => ipcRenderer.invoke('browser:go-back', { sessionId: String(sessionId || '') }),
    goForward: (sessionId) => ipcRenderer.invoke('browser:go-forward', { sessionId: String(sessionId || '') }),
    reload: (sessionOrInfo) => ipcRenderer.invoke(
      'browser:reload',
      sessionOrInfo && typeof sessionOrInfo === 'object'
        ? sessionOrInfo
        : { sessionId: String(sessionOrInfo || '') }
    ),
    setMuted: (info) => ipcRenderer.invoke('browser:set-muted', info || {}),
    screenshot: (info) => ipcRenderer.invoke('browser:screenshot', info || {}),
    onState: (callback) => {
      if (typeof callback !== 'function') return () => {};
      const listener = (_event, state) => callback(state);
      ipcRenderer.on('browser:state', listener);
      return () => ipcRenderer.removeListener('browser:state', listener);
    },
    onChatOverlayAction: (callback) => {
      if (typeof callback !== 'function') return () => {};
      const listener = (_event, action) => callback(action);
      ipcRenderer.on('browser:chat-overlay-action', listener);
      return () => ipcRenderer.removeListener('browser:chat-overlay-action', listener);
    },
    onTabPickerAction: (callback) => {
      if (typeof callback !== 'function') return () => {};
      const listener = (_event, action) => callback(action);
      ipcRenderer.on('browser:tab-picker-action', listener);
      return () => ipcRenderer.removeListener('browser:tab-picker-action', listener);
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
