'use strict';
// Setup window preload (sandboxed): a small, fixed API for ui/setup.js.
const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('setupApi', {
  getConfig: () => ipcRenderer.invoke('setup:get-config'),
  testServer: (url) => ipcRenderer.invoke('setup:test', String(url || '')),
  save: (cfg) => ipcRenderer.invoke('setup:save', {
    serverUrl: String((cfg && cfg.serverUrl) || ''),
    trust: !!(cfg && cfg.trust),
    autostart: !!(cfg && cfg.autostart),
  }),
  forget: () => ipcRenderer.invoke('setup:forget'),
  close: () => ipcRenderer.invoke('setup:close'),
  quit: () => ipcRenderer.invoke('setup:quit'),
});
