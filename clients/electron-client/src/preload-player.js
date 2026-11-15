'use strict';
// Player preload (sandboxed, main frame only, so web-item iframes never see it).
// Exposes window.AiSignXShell to the browser player served by the loopback proxy.
// No credential passes through here: the display cookie stays HttpOnly in Chromium.
const { contextBridge, ipcRenderer } = require('electron');

// Player control actions the shell forwards (docs/player-api.md, "Player controls").
const ACTIONS = new Set(['toggle', 'play', 'pause', 'forward', 'back', 'next', 'prev']);

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
    // JPEG bytes (Uint8Array) of the real window, or null.
    capture: () => ipcRenderer.invoke('shell-capture').catch(() => null),
    // {uptime_s, cache: {files, bytes}, storage_free_bytes} or null.
    stats: () => { try { return ipcRenderer.sendSync('shell-stats'); } catch (e) { return null; } },
    // The page's liveness note (JSON, about every 10 s) for the watchdog's heartbeat file.
    alive: (json) => {
      if (typeof json === 'string' && json.length < 2048) ipcRenderer.send('shell-alive', json);
    },
    // `unlock` / `lock` SSE commands, as JSON; main validates them.
    onCommand: (json) => {
      if (typeof json === 'string' && json.length < 1024) ipcRenderer.send('shell-command', json);
    },
    // cb(action, repeat) for control keys the shell takes before the page. One callback:
    // a later call replaces it.
    onControl: (cb) => {
      if (typeof cb !== 'function') return;
      ipcRenderer.removeAllListeners('shell-control');
      ipcRenderer.on('shell-control', (e, m) => {
        if (m && ACTIONS.has(m.a)) { try { cb(m.a, !!m.r); } catch (_) { /* page error */ } }
      });
    },
  });
}
