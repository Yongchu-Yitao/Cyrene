const { contextBridge, ipcRenderer } = require('electron');
contextBridge.exposeInMainWorld('cyreneDoctor', {
  inspect: () => ipcRenderer.invoke('doctor-recovery-inspect'),
  retry: () => ipcRenderer.invoke('doctor-recovery-retry'),
});
