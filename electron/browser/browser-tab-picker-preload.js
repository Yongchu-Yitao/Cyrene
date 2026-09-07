const { contextBridge, ipcRenderer } = require('electron');

let pickerState = { sessionId: '', visible: false };

contextBridge.exposeInMainWorld('browserTabPicker', {
  ready() {
    ipcRenderer.send('browser-tab-picker:ready', {
      sessionId: String(pickerState.sessionId || ''),
    });
  },
  onState(callback) {
    if (typeof callback !== 'function') return;
    ipcRenderer.on('browser-tab-picker:state', (_event, state) => {
      pickerState = state && typeof state === 'object'
        ? state
        : { sessionId: '', visible: false };
      callback(pickerState);
    });
  },
  action(type, tabId) {
    ipcRenderer.send('browser-tab-picker:action', {
      sessionId: String(pickerState.sessionId || ''),
      type: String(type || ''),
      tabId: String(tabId || ''),
    });
  },
  hiddenReady() {
    ipcRenderer.send('browser-tab-picker:hidden-ready', {
      sessionId: String(pickerState.sessionId || ''),
    });
  },
});
