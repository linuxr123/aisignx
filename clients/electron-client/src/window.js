'use strict';
// The player BrowserWindow: kiosk, navigation lock, crash and hang watchdogs.
const path = require('path');
const { app, BrowserWindow } = require('electron');
const kiosk = require('./kiosk');
const log = require('./log');

const OFFLINE_FILE = path.join(__dirname, 'ui', 'offline.html');
const CRASH_LIMIT = 5;
const CRASH_WINDOW_MS = 10 * 60 * 1000;
const UNRESPONSIVE_MS = 30000;
const HANG_CHECK_MS = 60000;
const HANG_TIMEOUT_MS = 10000;
const REFOCUS_MS = 300;

// opts: {smoke, getOrigin: () => 'http://127.0.0.1:<port>',
//   controls: () => bool (forward control keys to the page), lock: createLock() (lock.js),
//   isQuitting: () => bool}
function createPlayerWindow(opts) {
  const smoke = !!opts.smoke;
  const win = new BrowserWindow({
    show: false,
    backgroundColor: '#000',
    kiosk: !smoke,
    fullscreen: !smoke,
    frame: smoke,
    width: 1280,
    height: 720,
    autoHideMenuBar: false,  // no menu at all (main.js sets a null application menu)
    title: 'AiSignX Player',
    webPreferences: {
      contextIsolation: true,
      sandbox: true,
      nodeIntegration: false,
      backgroundThrottling: false,
      spellcheck: false,
      preload: path.join(__dirname, 'preload-player.js'),
    },
  });
  const wc = win.webContents;
  const crashes = [];
  let unresponsiveTimer = null;
  let hangTimer = null;
  let refocusTimer = null;
  let bootTop = false;          // the 1 s on-top after the first show
  const suspended = new Set();  // reasons the lock's on-top and refocus are paused

  const locked = () => !smoke && !!opts.lock && opts.lock.isLocked();
  const quitting = () => typeof opts.isQuitting === 'function' && opts.isQuitting();

  const playerUrl = () => opts.getOrigin() + '/player/';
  const inside = (url) => typeof url === 'string' && url.startsWith(opts.getOrigin() + '/');

  function loadPlayer() {
    wc.loadURL(playerUrl()).catch(() => { /* did-fail-load handles it */ });
  }
  function loadOffline(reason, retry) {
    const query = { reason };
    if (retry !== false) query.target = playerUrl();
    win.loadFile(OFFLINE_FILE, { query }).catch(() => {});
  }

  // Locked: on top of normal windows, but below the PIN prompt ('screen-saver' level).
  function applyLockLevel() {
    if (smoke || win.isDestroyed()) return;
    if (locked() && !suspended.size) win.setAlwaysOnTop(true, 'pop-up-menu');
    else if (!bootTop) win.setAlwaysOnTop(false);
  }

  // suspend(true, reason) while setup, the PIN prompt or DevTools are open, so they can
  // come to the front; suspend(false, reason) when they close.
  function suspend(on, reason) {
    const key = reason || 'default';
    if (on) suspended.add(key); else suspended.delete(key);
    if (on) clearTimeout(refocusTimer);
    applyLockLevel();
  }

  if (!smoke) {
    win.once('ready-to-show', () => {
      win.show();
      // The v2 fix: briefly on top so the kiosk window wins over the desktop at logon.
      bootTop = true;
      win.setAlwaysOnTop(true);
      win.moveTop();
      win.focus();
      setTimeout(() => {
        bootTop = false;
        if (!win.isDestroyed()) { win.setAlwaysOnTop(false); applyLockLevel(); }
      }, 1000);
    });
    kiosk.attachKeyBlock(wc, {
      controls: () => typeof opts.controls === 'function' && !!opts.controls(),
      onControl: (m) => { if (!win.isDestroyed()) wc.send('shell-control', m); },
    });
    // Lock player: no close (Alt+F4 from the OS), no minimize, back to the front on blur.
    // App quit (Ctrl+Alt+Q, logoff, shutdown) sets isQuitting first and passes.
    win.on('close', (event) => {
      if (locked() && !quitting()) { event.preventDefault(); log.warn('close blocked (locked)'); }
    });
    win.on('minimize', () => { if (locked()) win.restore(); });
    win.on('blur', () => {
      clearTimeout(refocusTimer);
      if (!locked() || suspended.size) return;
      refocusTimer = setTimeout(() => {
        if (win.isDestroyed() || !locked() || suspended.size || win.isFocused()) return;
        win.show();
        win.moveTop();
        win.focus();
      }, REFOCUS_MS);
    });
    wc.on('devtools-opened', () => suspend(true, 'devtools'));
    wc.on('devtools-closed', () => suspend(false, 'devtools'));
  }
  win.removeMenu();
  win.setMenuBarVisibility(false);

  wc.on('will-navigate', (event, url) => {
    if (!inside(url)) { event.preventDefault(); log.warn('blocked navigation', url.slice(0, 200)); }
  });
  wc.on('will-redirect', (event, url) => {
    if (!inside(url)) event.preventDefault();
  });
  wc.setWindowOpenHandler(() => ({ action: 'deny' }));

  wc.on('did-fail-load', (event, code, desc, url, isMainFrame) => {
    if (!isMainFrame || code === -3) return; // -3: aborted by a newer navigation
    log.warn('player load failed', code, desc);
    loadOffline('proxy-down');
  });

  wc.on('render-process-gone', (event, details) => {
    log.error('renderer gone', details.reason, details.exitCode);
    const now = Date.now();
    crashes.push(now);
    while (crashes.length && now - crashes[0] > CRASH_WINDOW_MS) crashes.shift();
    if (crashes.length > CRASH_LIMIT) {
      log.error('crash limit reached, relaunching');
      app.relaunch();
      app.exit(0);
      return;
    }
    setTimeout(() => { if (!win.isDestroyed()) loadPlayer(); }, 2000);
  });

  win.on('unresponsive', () => {
    clearTimeout(unresponsiveTimer);
    unresponsiveTimer = setTimeout(() => {
      log.error('renderer unresponsive for 30 s, crashing it');
      if (!win.isDestroyed()) wc.forcefullyCrashRenderer();
    }, UNRESPONSIVE_MS);
  });
  win.on('responsive', () => clearTimeout(unresponsiveTimer));

  // A renderer that no longer answers a trivial script is reloaded.
  hangTimer = setInterval(() => {
    if (win.isDestroyed() || wc.isLoading() || wc.isCrashed()) return;
    let timer;
    const timeout = new Promise((resolve) => { timer = setTimeout(() => resolve('timeout'), HANG_TIMEOUT_MS); });
    Promise.race([wc.executeJavaScript('document.body && document.body.dataset.state', true), timeout])
      .then((r) => {
        clearTimeout(timer);
        if (r === 'timeout' && !win.isDestroyed()) { log.error('hang check timed out, reloading'); loadPlayer(); }
      }, () => clearTimeout(timer));
  }, HANG_CHECK_MS);

  win.on('closed', () => {
    clearInterval(hangTimer);
    clearTimeout(unresponsiveTimer);
    clearTimeout(refocusTimer);
  });

  return { win, loadPlayer, loadOffline, applyLockLevel, suspend };
}

module.exports = { createPlayerWindow };
