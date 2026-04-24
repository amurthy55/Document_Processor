'use strict';

const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('electronAPI', {
  getApiBase:        ()       => ipcRenderer.invoke('get-api-base'),
  getExtensionDir:   ()       => ipcRenderer.invoke('get-extension-dir'),
  getOpsDir:         ()       => ipcRenderer.invoke('get-ops-dir'),
  openFileDialog:    (opts)   => ipcRenderer.invoke('open-file-dialog', opts),
  showItemInFolder:  (p)      => ipcRenderer.invoke('show-item-in-folder', p),
  openExtensionDir:  ()       => ipcRenderer.invoke('open-extension-dir'),
  openOperationsDir: ()       => ipcRenderer.invoke('open-operations-dir'),
});

// Exposed only to the activation screen (activate.html)
contextBridge.exposeInMainWorld('licenseAPI', {
  activate:           (key)    => ipcRenderer.invoke('license-activate', key),
  activationComplete: (center) => ipcRenderer.send('license-activation-complete', center),
});
