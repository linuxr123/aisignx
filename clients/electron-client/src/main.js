'use strict';
// AiSignX Player (Electron): a kiosk shell around the v3 browser player.
// Chromium loads http://127.0.0.1:<port>/player/ from a loopback caching proxy that forwards
// to the configured AiSignX server. See README.md.
const fs = require('fs');
const os = require('os');
const path = require('path');
const { app, ipcMain, session, shell, BrowserWindow, Menu } = require('electron');
const config = require('./config');
const log = require('./log');
const kiosk = require('./kiosk');
const { createUpstream } = require('./proxy/upstream');
const { createPageCache } = require('./proxy/page-cache');
const { createMediaCache, COOKIE_NAME } = require('./proxy/media-cache');
const { createProxy, sendOffline } = require('./proxy/server');
const { createPlayerWindow } = require('./window');
const { createSetup } = require('./setup');
const { runSmoke, tempProfile } = require('./smoke');

function argValue(name) {
  const hit = process.argv.find((a) => a.startsWith('--' + name + '='));
  return hit ? hit.slice(name.length + 3) : '';
}
const SMOKE = process.argv.includes('--smoke');
const userDataArg = argValue('user-data-dir');

// ---- Before ready.
if (userDataArg) app.setPath('userData', path.resolve(userDataArg));
else if (SMOKE) app.setPath('userData', tempProfile());
else app.setPath('userData', path.join(app.getPath('appData'), 'AiSignX Player', 'v3'));
// No application menu: Electron's default one (Reload, Toggle DevTools, Close) could be
// reached with Alt on a kiosk that has a keyboard.
Menu.setApplicationMenu(null);
app.commandLine.appendSwitch('autoplay-policy', 'no-user-gesture-required');
app.commandLine.appendSwitch('disk-cache-size', '67108864');

const DATA = app.getPath('userData');
fs.mkdirSync(DATA, { recursive: true });
log.init(path.join(DATA, 'aisignx-player.log'));

const S = {
  cfg: config.defaults(),
  clientId: '',
  proxy: null, upstream: null, pageCache: null, mediaCache: null,
  player: null, setup: null, cookie: '', retryTimer: null, quitting: false,
};

function origin() {
  return S.proxy ? S.proxy.origin : 'http://127.0.0.1:' + S.cfg.port;
}

function shellInfo() {
  return {
    name: 'electron',
    version: app.getVersion(),
    device: (os.hostname() + ' / ' + os.version()).slice(0, 120),
    client_id: S.clientId,
    admin_url: S.cfg.serverUrl,
  };
}

// ---- Display cookie, kept in memory for the media cache's hit check.
async function refreshCookie() {
  if (!S.proxy) { S.cookie = ''; return; }
  try {
    const list = await session.defaultSession.cookies.get({ url: origin() + '/api/player', name: COOKIE_NAME });
    S.cookie = list.length ? list[0].value : '';
  } catch (e) {
    S.cookie = '';
  }
}

// ---- Proxy lifecycle.
async function stopProxy() {
  clearTimeout(S.retryTimer);
  if (S.mediaCache) S.mediaCache.stop();
  if (S.proxy) await S.proxy.stop();
  if (S.upstream) S.upstream.close();
  S.proxy = S.upstream = S.pageCache = S.mediaCache = null;
}

async function startProxy() {
  await stopProxy();
  const wiring = { port: S.cfg.port };
  const proxy = createProxy(wiring);
  await proxy.start(); // EADDRINUSE rejects
  const upstream = createUpstream({ serverUrl: S.cfg.serverUrl, config: S.cfg, loopbackOrigin: proxy.origin });
  const pageCache = createPageCache({ dir: path.join(DATA, 'page-cache'), upstream, offlinePage: sendOffline });
  const mediaCache = createMediaCache({ dir: path.join(DATA, 'media'), upstream, getCookie: () => S.cookie });
  Object.assign(wiring, { upstream, pageCache, mediaCache });
  Object.assign(S, { proxy, upstream, pageCache, mediaCache });
  await refreshCookie();
  log.info('proxy listening on', proxy.origin, 'for', S.cfg.serverUrl);
}

async function startPlayer() {
  if (!S.player || S.player.win.isDestroyed()) {
    S.player = createPlayerWindow({ smoke: SMOKE, getOrigin: origin });
    S.player.win.on('closed', () => { S.player = null; });
  }
  try {
    await startProxy();
  } catch (e) {
    log.error('proxy start failed', e.code || e.message);
    S.player.loadOffline(e.code === 'EADDRINUSE' ? 'port-in-use' : 'proxy-down', false);
    S.retryTimer = setTimeout(startPlayer, 10000);
    return;
  }
  S.player.loadPlayer();
}

async function wipeServerData() {
  const loopback = 'http://127.0.0.1:' + S.cfg.port;
  await stopProxy();
  try {
    await session.defaultSession.clearStorageData({ origin: loopback });
    await session.defaultSession.clearCache();
  } catch (e) {
    log.warn('clearStorageData failed', e.message);
  }
  for (const d of ['page-cache', 'media']) fs.rmSync(path.join(DATA, d), { recursive: true, force: true });
  S.cookie = '';
}

// ---- Setup callbacks.
async function onSaved(cfg, { serverChanged }) {
  if (serverChanged) await wipeServerData();
  S.cfg = cfg;
  kiosk.applyAutostart(cfg.autostart);
  await startPlayer();
  if (S.setup) S.setup.close();
}

async function onForget() {
  if (S.player && !S.player.win.isDestroyed()) S.player.win.destroy();
  await wipeServerData();
  S.cfg = config.saveConfig(DATA, Object.assign({}, S.cfg, { serverUrl: '', certPin: null }));
  log.info('server forgotten');
}

function openSetup() {
  S.setup.open(!!S.player);
}

// ---- IPC from the player preload. Only the player window's main frame on the loopback origin.
function fromPlayer(event) {
  if (!S.player || S.player.win.isDestroyed() || event.sender !== S.player.win.webContents) return false;
  const frame = event.senderFrame;
  return !frame || !frame.url || frame.url.startsWith(origin() + '/');
}

function registerIpc() {
  ipcMain.on('shell-info', (event) => {
    event.returnValue = fromPlayer(event) && S.proxy ? { port: S.proxy.port, info: shellInfo() } : null;
  });
  ipcMain.on('shell-state', (event, s) => {
    if (fromPlayer(event) && typeof s === 'string') log.info('player state', s.replace(/[^A-Z_]/gi, '').slice(0, 32));
  });
  ipcMain.on('flush-cookies', (event) => {
    if (!fromPlayer(event)) return;
    refreshCookie();
    session.defaultSession.cookies.flushStore().catch((e) => log.warn('flushStore', e.message));
  });
  ipcMain.on('shell-manifest', (event, json) => {
    if (fromPlayer(event) && S.mediaCache && typeof json === 'string') S.mediaCache.onManifest(json);
  });
}

function lockDownSession() {
  const ses = session.defaultSession;
  // Players and web items get no camera, microphone, location, notifications and so on.
  ses.setPermissionRequestHandler((wc, permission, callback) => callback(permission === 'fullscreen'));
  ses.setPermissionCheckHandler((wc, permission) => permission === 'fullscreen');
  ses.cookies.on('changed', (event, cookie) => { if (cookie.name === COOKIE_NAME) refreshCookie(); });
}

// ---- Startup.
async function main() {
  lockDownSession();
  registerIpc();
  S.clientId = config.clientId(DATA);

  if (SMOKE) {
    // --port=N keeps the loopback origin (and so localStorage) across smoke runs.
    const port = Number(argValue('port'));
    S.cfg = Object.assign(config.defaults(), {
      serverUrl: config.normalizeServerUrl(argValue('server') || 'http://127.0.0.1:5000'),
      port: port >= 1024 && port <= 65535 ? port : 0,
    });
    await startPlayer();
    runSmoke({ getWindow: () => S.player && S.player.win, getOrigin: origin, log })
      .then((code) => app.exit(code));
    return;
  }

  S.setup = createSetup({
    dataDir: DATA,
    getConfig: () => S.cfg,
    clientId: S.clientId,
    version: app.getVersion(),
    onSaved,
    onForget,
    onClosed: () => { if (!S.player && !S.quitting) app.quit(); },
    quit: () => app.quit(),
  });
  kiosk.registerShortcuts({
    setup: openSetup,
    log: () => shell.openPath(log.file),
    devtools: () => {
      const w = BrowserWindow.getFocusedWindow() || (S.player && S.player.win);
      if (w) w.webContents.openDevTools({ mode: 'detach' });
    },
    quit: () => app.quit(),
  });
  kiosk.keepAwake();

  const loaded = config.loadConfig(DATA);
  S.cfg = loaded.config;
  log.info('AiSignX Player', app.getVersion(), 'starting; config', loaded.status);
  if (!S.cfg.serverUrl || !S.cfg.port) {
    openSetup();
    return;
  }
  kiosk.applyAutostart(S.cfg.autostart);
  await startPlayer();
}

if (!SMOKE && !app.requestSingleInstanceLock()) {
  app.quit();
} else {
  app.on('second-instance', () => {
    const w = (S.setup && S.setup.window) || (S.player && S.player.win);
    if (w && !w.isDestroyed()) { if (w.isMinimized()) w.restore(); w.show(); w.focus(); }
  });
  app.whenReady().then(main).catch((e) => {
    log.error('startup failed', e.stack || e.message);
    app.exit(1);
  });
  app.on('window-all-closed', () => {
    if (!S.setup || !S.setup.window) app.quit();
  });
  app.on('before-quit', () => { S.quitting = true; });
  app.on('will-quit', () => {
    kiosk.unregisterShortcuts();
    kiosk.releaseAwake();
    stopProxy().catch(() => {});
  });
}
