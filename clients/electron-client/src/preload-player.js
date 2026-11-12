'use strict';
// Player preload (sandboxed, main frame only, so web-item iframes never see it).
// Exposes window.AiSignXShell to the browser player served by the loopback proxy.
// No credential passes through here: the display cookie stays HttpOnly in Chromium.
const { contextBridge, ipcRenderer } = require('electron');

let shell = null;
try { shell = ipcRenderer.sendSync('shell-info'); } catch (e) { shell = null; }

if (shell && shell.port && location.origin === 'http://127.0.0.1:' + shell.port) {
  const info = Object.freeze(Object.assign({}, shell.info));
  contextBridge.exposeInMainWorld('AiSignXShell', {
    info: () => Object.assign({}, info),
    onState: (s) => {
      if (typeof s !== 'string') return;
      ipcRenderer.send('shell-state', s.slice(0, 32));
      if (s === 'PAIRED') ipcRenderer.send('flush-cookies');
    },
    onManifest: (json) => {
      if (typeof json === 'string' && json.length < 4 * 1024 * 1024) ipcRenderer.send('shell-manifest', json);
    },
  });
}
