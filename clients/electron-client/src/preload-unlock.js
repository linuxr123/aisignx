'use strict';
// Unlock window preload (sandboxed): a small, fixed API for ui/unlock.js.
const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('unlockApi', {
  status: () => ipcRenderer.invoke('unlock:status'),
  tryPin: (pin) => ipcRenderer.invoke('unlock:try', String(pin || '').slice(0, 12)),
  cancel: () => ipcRenderer.invoke('unlock:cancel'),
});
