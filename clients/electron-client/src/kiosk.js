'use strict';
// Kiosk behaviour: blocked keys, Ctrl+Alt admin shortcuts, keep-awake and autostart.
const fs = require('fs');
const os = require('os');
const path = require('path');
const electron = require('electron');

// The v2 block list. Ctrl+Alt combinations always pass (admin shortcuts).
function isBlocked(input) {
  if (!input || input.type !== 'keyDown') return false;
  const k = String(input.key || '').toLowerCase();
  const ctrl = !!(input.control || input.meta);
  const shift = !!input.shift;
  const alt = !!input.alt;
  if (ctrl && alt) return false;
  return k === 'f5' || k === 'f11' || k === 'f12' ||
    (ctrl && shift && (k === 'i' || k === 'j' || k === 'c')) ||
    (ctrl && (k === 'r' || k === 'w' || k === 'q')) ||
    (alt && k === 'f4') ||
    (ctrl && (k === '-' || k === '+' || k === '=' || k === '0')) ||
    (alt && (k === 'arrowleft' || k === 'arrowright')) ||
    k === 'backspace' || k === 'browserback' || k === 'browserforward' || k === 'browserrefresh';
}

function attachKeyBlock(webContents) {
  webContents.on('before-input-event', (event, input) => {
    if (isBlocked(input)) event.preventDefault();
  });
  // Zoom by mouse wheel or pinch.
  webContents.setVisualZoomLevelLimits(1, 1).catch(() => {});
  webContents.on('zoom-changed', () => webContents.setZoomFactor(1));
}

// handlers: {setup, log, devtools, quit}
function registerShortcuts(handlers) {
  const { globalShortcut } = electron;
  const map = { 'Control+Alt+S': handlers.setup, 'Control+Alt+L': handlers.log,
    'Control+Alt+D': handlers.devtools, 'Control+Alt+Q': handlers.quit };
  for (const [accel, fn] of Object.entries(map)) {
    if (typeof fn === 'function') {
      try { globalShortcut.register(accel, fn); } catch (e) { /* taken by another app */ }
    }
  }
}

function unregisterShortcuts() {
  try { electron.globalShortcut.unregisterAll(); } catch (e) { /* app not ready */ }
}

let blockerId = null;
function keepAwake() {
  const { powerSaveBlocker } = electron;
  if (blockerId === null || !powerSaveBlocker.isStarted(blockerId)) {
    blockerId = powerSaveBlocker.start('prevent-display-sleep');
  }
}
function releaseAwake() {
  if (blockerId !== null) {
    try { electron.powerSaveBlocker.stop(blockerId); } catch (e) { /* ignore */ }
    blockerId = null;
  }
}

function desktopEntry(execPath) {
  if (/["\r\n]/.test(execPath)) throw new Error('unsupported AppImage path');
  return ['[Desktop Entry]', 'Type=Application', 'Name=AiSignX Player', 'Comment=AiSignX display client',
    'Exec="' + execPath + '"', 'X-GNOME-Autostart-enabled=true', 'Terminal=false', ''].join('\n');
}

// Only for an installed (packaged) app: a dev checkout must not register itself.
// env/home are injectable for tests.
function applyAutostart(enabled, opts) {
  const o = opts || {};
  const app = o.app || electron.app;
  const platform = o.platform || process.platform;
  const env = o.env || process.env;
  if (!app.isPackaged) return 'skipped';
  if (platform === 'win32') {
    app.setLoginItemSettings({ openAtLogin: !!enabled, path: process.execPath });
    return 'login-item';
  }
  if (platform === 'linux' && env.APPIMAGE) {
    const dir = path.join(o.home || os.homedir(), '.config', 'autostart');
    const file = path.join(dir, 'aisignx-player.desktop');
    if (enabled) {
      fs.mkdirSync(dir, { recursive: true });
      fs.writeFileSync(file, desktopEntry(env.APPIMAGE));
    } else {
      try { fs.unlinkSync(file); } catch (e) { /* not there */ }
    }
    return 'xdg';
  }
  return 'unsupported';
}

module.exports = { isBlocked, attachKeyBlock, registerShortcuts, unregisterShortcuts, keepAwake, releaseAwake,
  applyAutostart, desktopEntry };
