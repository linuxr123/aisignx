'use strict';
// The PIN prompt in front of the Ctrl+Alt shortcuts. Without an unlock PIN from the server the
// shortcut runs at once, as before. With one, a small window on top of the kiosk asks for it;
// the check and the wrong-PIN backoff run here in main (see unlock.js), never in the page.
const path = require('path');
const electron = require('electron');
const log = require('./log');

const ACTIONS = new Set(['setup', 'log', 'devtools', 'quit']);
const PIN_MAX = 12;

function alive(w) {
  return w && !(typeof w.isDestroyed === 'function' && w.isDestroyed()) ? w : null;
}

// The window a "current window" shortcut acts on: the focused one, never the PIN prompt.
// When the shortcut is pressed again while the prompt has focus, keep the window picked
// when the prompt opened; otherwise fall back (the player window).
function pickTarget(focused, prompt, previous, fallback) {
  if (alive(focused) && focused !== prompt) return focused;
  if (focused && focused === prompt && alive(previous)) return previous;
  return alive(fallback);
}

// opts: {getUnlock: () => cfg.unlock | null, gate: createGate(...)}
function createUnlockWindow(opts) {
  let win = null;
  let pending = null;   // the action to run after a correct PIN
  let shown = '';       // the action name the prompt shows
  let ipcReady = false;
  let lastTarget = null; // the window picked by the last guardWindow shortcut

  function fromUnlock(event) {
    return !!win && !win.isDestroyed() && event.sender === win.webContents;
  }

  // Registered on first use, so requirePin without a PIN never touches Electron (and tests run in Node).
  function registerIpc() {
    if (ipcReady) return;
    ipcReady = true;
    const { ipcMain } = electron;
    ipcMain.handle('unlock:status', (event) => {
      if (!fromUnlock(event)) throw new Error('not allowed');
      return opts.gate.status();
    });
    ipcMain.handle('unlock:try', async (event, pin) => {
      if (!fromUnlock(event)) throw new Error('not allowed');
      const u = opts.getUnlock();
      if (!u) { finish(); return { ok: true, waitMs: 0 }; } // the PIN was removed meanwhile
      if (typeof pin !== 'string' || !pin || pin.length > PIN_MAX) return { ok: false, waitMs: opts.gate.status().waitMs };
      const r = await opts.gate.attempt(pin, u);
      if (r.ok) {
        log.info('unlock PIN accepted');
        finish();
      } else {
        log.warn('wrong unlock PIN', r.waitMs ? 'locked for ' + Math.round(r.waitMs / 1000) + ' s' : '');
      }
      return { ok: r.ok, waitMs: r.waitMs };
    });
    ipcMain.handle('unlock:cancel', (event) => {
      if (!fromUnlock(event)) throw new Error('not allowed');
      close();
      return true;
    });
  }

  function finish() {
    const action = pending;
    pending = null;
    close();
    if (typeof action === 'function') setImmediate(action);
  }

  function close() {
    if (win && !win.isDestroyed()) win.close();
  }

  function load(name) {
    shown = name;
    win.loadFile(path.join(__dirname, 'ui', 'unlock.html'), { query: { action: name } })
      .catch((e) => log.error('unlock window load', e.message));
  }

  function open(name) {
    registerIpc();
    if (win && !win.isDestroyed()) {
      if (name !== shown) load(name);
      win.show();
      win.focus();
      return;
    }
    win = new electron.BrowserWindow({
      width: 380,
      height: 270,
      show: false,
      resizable: false,
      minimizable: false,
      maximizable: false,
      fullscreenable: false,
      alwaysOnTop: true,
      skipTaskbar: true,
      title: 'AiSignX Player',
      autoHideMenuBar: true,
      backgroundColor: '#f4f5f7',
      webPreferences: {
        contextIsolation: true,
        sandbox: true,
        nodeIntegration: false,
        spellcheck: false,
        preload: path.join(__dirname, 'preload-unlock.js'),
      },
    });
    win.setMenuBarVisibility(false);
    win.setAlwaysOnTop(true, 'screen-saver'); // above the kiosk window
    win.webContents.on('will-navigate', (event) => event.preventDefault());
    win.webContents.setWindowOpenHandler(() => ({ action: 'deny' }));
    win.once('ready-to-show', () => { win.show(); win.focus(); });
    const self = win;
    win.on('closed', () => { if (win === self) { win = null; pending = null; } });
    load(name);
  }

  // Runs action() now when no PIN is set, otherwise after the PIN was entered.
  // A second shortcut while the prompt is open replaces the pending action.
  function requirePin(name, action) {
    if (!opts.getUnlock()) { action(); return; }
    pending = action;
    open(ACTIONS.has(name) ? name : '');
  }

  // A shortcut handler that asks for the PIN first.
  function guard(name, action) {
    return () => requirePin(name, action);
  }

  // Like guard(), for an action on the current window (devtools). The window is picked
  // when the shortcut fires, before the prompt takes focus: by the time a correct PIN runs
  // the action, the prompt is closing and may still be the focused window.
  // windows: {focused: () => BrowserWindow | null, fallback: () => BrowserWindow | null}
  function guardWindow(name, windows, action) {
    return () => {
      const target = pickTarget(windows.focused(), win, lastTarget, windows.fallback());
      lastTarget = target;
      requirePin(name, () => { if (alive(target)) action(target); });
    };
  }

  return { requirePin, guard, guardWindow, close, get window() { return win; } };
}

module.exports = { createUnlockWindow, pickTarget };
