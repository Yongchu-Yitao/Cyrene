const { clipboard, contextBridge, ipcRenderer } = require('electron');

let uiSurfaceHandler = null;
ipcRenderer.on('ui-surface:request', async (_event, payload) => {
  let result;
  try {
    result = uiSurfaceHandler
      ? await uiSurfaceHandler(String(payload.method || ''), payload.args || {})
      : { ok: false, error: 'surface_not_ready' };
  } catch (error) {
    result = { ok: false, error: String((error && error.message) || error) };
  }
  ipcRenderer.send('ui-surface:response', {
    requestId: String(payload.requestId || ''),
    result,
  });
});

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
  onDesktopLanguageChanged: (callback) => {
    if (typeof callback !== 'function') return () => {};
    const listener = (_event, language) => callback(String(language || ''));
    ipcRenderer.on('desktop-language:changed', listener);
    return () => ipcRenderer.removeListener('desktop-language:changed', listener);
  },
  agentCursor: {
    setRunning: (running) => ipcRenderer.invoke('agent-cursor:set-running', { running: running === true }),
    claim: (owner) => ipcRenderer.invoke('agent-cursor:claim-owner', { owner: String(owner || '') }),
    onOwnerChanged: (callback) => {
      if (typeof callback !== 'function') return () => {};
      const listener = (_event, info) => callback(String(info && info.owner || ''));
      ipcRenderer.on('agent-cursor:owner-changed', listener);
      return () => ipcRenderer.removeListener('agent-cursor:owner-changed', listener);
    },
  },
  uiSurface: {
    register: (uiInstanceId, handler) => {
      if (typeof handler !== 'function') throw new Error('UI surface handler is required');
      uiSurfaceHandler = handler;
      return ipcRenderer.invoke('ui-surface:register', { uiInstanceId: String(uiInstanceId || '') });
    },
    unregister: (uiInstanceId) => {
      uiSurfaceHandler = null;
      return ipcRenderer.invoke('ui-surface:unregister', { uiInstanceId: String(uiInstanceId || '') });
    },
  },
  showNotification: ({ title, body }) => ipcRenderer.invoke('notification:show', { title, body }),
  writeClipboardText: (text) => {
    clipboard.writeText(String(text == null ? '' : text));
    return true;
  },
  showItemInFolder: (filePath) => ipcRenderer.invoke('shell:show-item-in-folder', {
    path: String(filePath == null ? '' : filePath),
  }),
  pickDirectory: () => ipcRenderer.invoke('dialog:pick-directory'),
  pickExtensionPath: (options) => ipcRenderer.invoke('dialog:pick-extension-path', options || {}),
  pickBackupSavePath: (options) => ipcRenderer.invoke('dialog:pick-backup-save-path', options || {}),
  pickBackupFile: (options) => ipcRenderer.invoke('dialog:pick-backup-file', options || {}),
  browser: {
    getState: (sessionId) => ipcRenderer.invoke('browser:get-state', { sessionId: String(sessionId || '') }),
    getManagerState: () => ipcRenderer.invoke('browser:get-manager-state'),
    syncProxy: () => ipcRenderer.invoke('browser:sync-proxy'),
    controlDownload: (info) => ipcRenderer.invoke('browser:control-download', info || {}),
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
    onManagerState: (callback) => {
      if (typeof callback !== 'function') return () => {};
      const listener = (_event, state) => callback(state);
      ipcRenderer.on('browser:manager-state', listener);
      return () => ipcRenderer.removeListener('browser:manager-state', listener);
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
  detachedPane: {
    // Establish the native drag session before returning to the pointerdown
    // handler. This keeps the first move from overtaking begin and also gives
    // the renderer a real rejection result instead of silently retaining a
    // dead drag session.
    beginDrag: (info) => {
      const result = ipcRenderer.sendSync('detached-pane:begin-drag', info || {});
      return Promise.resolve(result || { ok: false, error: 'detached_drag_not_started' });
    },
    updateDrag: (point) => ipcRenderer.send('detached-pane:update-drag', point || {}),
    finishDrag: (info) => ipcRenderer.invoke('detached-pane:finish-drag', info || {}),
    getContext: () => ipcRenderer.invoke('detached-pane:get-context'),
    ready: () => ipcRenderer.invoke('detached-pane:ready'),
    updateContext: (updates) => ipcRenderer.invoke('detached-pane:update-context', updates || {}),
    close: () => ipcRenderer.invoke('detached-pane:close'),
    toggleMaximize: () => ipcRenderer.invoke('detached-pane:toggle-maximize'),
    minimize: () => ipcRenderer.invoke('detached-pane:minimize'),
    returnBegin: (info) => ipcRenderer.invoke('detached-pane:return-begin', info || {}),
    returnMove: (point) => ipcRenderer.send('detached-pane:return-move', point || {}),
    returnEnd: (point) => ipcRenderer.invoke('detached-pane:return-end', point || {}),
    closeByChat: (chatId) => ipcRenderer.invoke('detached-pane:close-by-chat', {
      chatId: String(chatId || ''),
    }),
    onClosed: (callback) => {
      if (typeof callback !== 'function') return () => {};
      const listener = (_event, info) => callback(info);
      ipcRenderer.on('detached-pane:closed', listener);
      return () => ipcRenderer.removeListener('detached-pane:closed', listener);
    },
    onCreated: (callback) => {
      if (typeof callback !== 'function') return () => {};
      const listener = (_event, info) => callback(info);
      ipcRenderer.on('detached-pane:created', listener);
      return () => ipcRenderer.removeListener('detached-pane:created', listener);
    },
    onReturned: (callback) => {
      if (typeof callback !== 'function') return () => {};
      const listener = (_event, info) => callback(info);
      ipcRenderer.on('detached-pane:returned', listener);
      return () => ipcRenderer.removeListener('detached-pane:returned', listener);
    },
    onReturnHover: (callback) => {
      if (typeof callback !== 'function') return () => {};
      const listener = (_event, info) => callback(!!(info && info.active));
      ipcRenderer.on('detached-pane:return-hover', listener);
      return () => ipcRenderer.removeListener('detached-pane:return-hover', listener);
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
