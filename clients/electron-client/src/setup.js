'use strict';
// The setup window: a normal (not kiosk) window where the server address is entered and
// tested, and an untrusted certificate can be pinned (trust on first use).
const path = require('path');
const { BrowserWindow, ipcMain } = require('electron');
const config = require('./config');
const { checkServer } = require('./health');
const { portFree } = require('./proxy/server');
const log = require('./log');

async function firstFreePort() {
  for (let p = config.PORT_MIN; p <= config.PORT_MAX; p++) {
    if (await portFree(p)) return p;
  }
  throw new Error('No free local port in ' + config.PORT_MIN + '-' + config.PORT_MAX);
}

// opts: {dataDir, getConfig(), clientId, version, onSaved(cfg, {serverChanged}), onForget(), onClosed(), quit()}
function createSetup(opts) {
  let win = null;
  let lastCapture = null; // {serverUrl, card} from the last untrusted test; the PEM stays in main

  function fromSetup(event) {
    return !!win && !win.isDestroyed() && event.sender === win.webContents;
  }
  function handle(channel, fn) {
    ipcMain.handle(channel, async (event, arg) => {
      if (!fromSetup(event)) throw new Error('not allowed');
      return fn(arg);
    });
  }

  handle('setup:get-config', () => {
    const c = opts.getConfig();
    return { serverUrl: c.serverUrl, trusted: !!c.certPin, autostart: c.autostart !== false,
      port: c.port, clientId: opts.clientId, version: opts.version };
  });

  handle('setup:test', async (input) => {
    let serverUrl;
    try { serverUrl = config.normalizeServerUrl(input); } catch (e) { return { result: 'invalid', message: e.message }; }
    const saved = opts.getConfig();
    const certPin = saved.serverUrl === serverUrl ? saved.certPin : null;
    const r = await checkServer({ serverUrl, certPin });
    const u = new URL(serverUrl);
    const out = { result: r.result, message: r.message, serverUrl,
      httpWarning: u.protocol === 'http:' && !config.isPrivateHost(u.hostname) };
    if (r.card) {
      lastCapture = { serverUrl, card: r.card };
      out.card = { fingerprint: r.card.fingerprint, subject: r.card.subject, issuer: r.card.issuer,
        validTo: r.card.validTo, pinnable: r.card.pinnable, pinMismatch: !!r.pinMismatch };
    }
    log.info('setup test', serverUrl, r.result);
    return out;
  });

  handle('setup:save', async (input) => {
    let serverUrl;
    try { serverUrl = config.normalizeServerUrl(input.serverUrl); } catch (e) { return { ok: false, message: e.message }; }
    const prev = opts.getConfig();
    const serverChanged = !!prev.serverUrl && prev.serverUrl !== serverUrl;
    let certPin = serverChanged ? null : prev.certPin;
    if (input.trust && lastCapture && lastCapture.serverUrl === serverUrl && lastCapture.card.pinnable) {
      certPin = { sha256: lastCapture.card.fingerprint, caPem: lastCapture.card.caPem };
    }
    if (!serverUrl.startsWith('https:')) certPin = null;
    let port = prev.port;
    try {
      // Chosen once: the display cookie and localStorage belong to this loopback origin.
      if (!port) port = await firstFreePort();
    } catch (e) {
      return { ok: false, message: e.message };
    }
    const next = { serverUrl, port, certPin, autostart: !!input.autostart };
    try {
      const saved = config.saveConfig(opts.dataDir, next);
      log.info('setup saved', serverUrl, 'pinned:', !!certPin, 'server changed:', serverChanged);
      await opts.onSaved(saved, { serverChanged });
      return { ok: true };
    } catch (e) {
      log.error('setup save failed', e.message);
      return { ok: false, message: 'Could not save the settings: ' + e.message };
    }
  });

  handle('setup:forget', async () => {
    lastCapture = null;
    await opts.onForget();
    return { ok: true };
  });
  handle('setup:close', () => { if (win) win.close(); return true; });
  handle('setup:quit', () => { opts.quit(); return true; });

  function open(aboveKiosk) {
    if (win && !win.isDestroyed()) {
      win.show();
      win.focus();
      return win;
    }
    win = new BrowserWindow({
      width: 720,
      height: 800,
      show: false,
      title: 'AiSignX Player setup',
      autoHideMenuBar: true,
      alwaysOnTop: !!aboveKiosk,
      backgroundColor: '#f4f5f7',
      webPreferences: {
        contextIsolation: true,
        sandbox: true,
        nodeIntegration: false,
        spellcheck: false,
        preload: path.join(__dirname, 'preload-setup.js'),
      },
    });
    win.setMenuBarVisibility(false);
    win.webContents.on('will-navigate', (event) => event.preventDefault());
    win.webContents.setWindowOpenHandler(() => ({ action: 'deny' }));
    win.once('ready-to-show', () => { win.show(); win.focus(); });
    win.on('closed', () => { win = null; lastCapture = null; opts.onClosed(); });
    win.loadFile(path.join(__dirname, 'ui', 'setup.html')).catch((e) => log.error('setup load', e.message));
    return win;
  }

  function close() {
    if (win && !win.isDestroyed()) win.close();
  }

  return { open, close, get window() { return win; } };
}

module.exports = { createSetup, firstFreePort };
