const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('cyreneCredentialDialog', {
  context: () => ipcRenderer.invoke('remote-desktop:credential-context'),
  submit: (values) => ipcRenderer.invoke('remote-desktop:credential-submit', values || {}),
  cancel: () => ipcRenderer.invoke('remote-desktop:credential-cancel'),
});
