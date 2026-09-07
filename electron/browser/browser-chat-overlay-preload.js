const { contextBridge, ipcRenderer } = require('electron');

let overlayState = { sessionId: '' };

contextBridge.exposeInMainWorld('browserChatOverlay', {
  onState(callback) {
    if (typeof callback !== 'function') return;
    ipcRenderer.on('browser-chat-overlay:state', (_event, state) => {
      overlayState = state && typeof state === 'object' ? state : { sessionId: '' };
      callback(overlayState);
    });
  },
  submit(text) {
    ipcRenderer.send('browser-chat-overlay:action', {
      sessionId: String(overlayState.sessionId || ''),
      type: 'submit',
      text: String(text || ''),
    });
  },
  stop() {
    ipcRenderer.send('browser-chat-overlay:action', {
      sessionId: String(overlayState.sessionId || ''),
      type: 'stop',
    });
  },
});
