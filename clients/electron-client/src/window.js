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

// opts: {smoke, getOrigin: () => 'http://127.0.0.1:<port>'}
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
    autoHideMenuBar: true,
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

  if (!smoke) {
    win.once('ready-to-show', () => {
      win.show();
      // The v2 fix: briefly on top so the kiosk window wins over the desktop at logon.
      win.setAlwaysOnTop(true);
      win.moveTop();
      win.focus();
      setTimeout(() => { if (!win.isDestroyed()) win.setAlwaysOnTop(false); }, 1000);
    });
    kiosk.attachKeyBlock(wc);
  }
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
  });

  return { win, loadPlayer, loadOffline };
}

module.exports = { createPlayerWindow };
