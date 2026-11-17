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
const unlock = require('./unlock');
const { createUnlockWindow } = require('./unlock-window');
const { createLock, parseCommand } = require('./lock');
const { createAgent } = require('./agent');
const systemOps = require('./agent/system');

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
// Media keys arrive as ordinary key input (before-input-event, then player controls) instead
// of going to the OS media overlay.
app.commandLine.appendSwitch('disable-features', 'HardwareMediaKeyHandling');

const DATA = app.getPath('userData');
const CAPTURE_MAX_SIDE = 1920;
fs.mkdirSync(DATA, { recursive: true });
log.init(path.join(DATA, 'aisignx-player.log'));

const S = {
  cfg: config.defaults(),
  clientId: '',
  proxy: null, upstream: null, pageCache: null, mediaCache: null,
  player: null, setup: null, cookie: '', retryTimer: null, quitting: false,
  gate: null, unlockWin: null, lock: null, agent: null, sessionEnd: false,
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
    caps: ['capture', 'pin-exit', 'controls', 'kiosk-lock'].concat(S.agent ? ['agent'] : []),
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
    S.player = createPlayerWindow({
      smoke: SMOKE,
      getOrigin: origin,
      controls: () => !!S.cfg.allowInput,
      lock: S.lock,
      isQuitting: () => S.quitting,
    });
    S.player.win.on('closed', () => { S.player = null; });
    // Windows logoff or shutdown: a locked window must not hold it up.
    S.player.win.on('session-end', () => { S.quitting = true; S.sessionEnd = true; });
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
  if (S.agent) S.agent.forgetKeys(); // a new or forgotten server gets a fresh trust-on-first-use key
}

// ---- Setup callbacks.
async function onSaved(cfg, { serverChanged }) {
  if (serverChanged) {
    await wipeServerData();
    S.gate.reset();
  }
  S.cfg = cfg;
  S.lock.configure({ kioskLock: cfg.kioskLock });
  kiosk.applyAutostart(cfg.autostart);
  await startPlayer();
  if (S.setup) S.setup.close();
}

async function onForget() {
  if (S.player && !S.player.win.isDestroyed()) S.player.win.destroy();
  await wipeServerData();
  if (S.agent) S.agent.disableWatchdog().catch(() => {});
  S.cfg = config.saveConfig(DATA, Object.assign({}, S.cfg,
    { serverUrl: '', certPin: null, unlock: null, allowInput: true, kioskLock: false }));
  S.lock.configure({ kioskLock: false });
  S.gate.reset();
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
    if (!fromPlayer(event) || typeof json !== 'string') return;
    applySettings(json);
    if (S.agent) S.agent.onManifest(json);
    if (S.mediaCache) S.mediaCache.onManifest(json);
  });
  ipcMain.on('shell-alive', (event, json) => {
    if (fromPlayer(event) && S.agent) S.agent.onAlive(json);
  });
  ipcMain.handle('shell-capture', async (event) => (fromPlayer(event) ? capture() : null));
  ipcMain.on('shell-stats', (event) => {
    event.returnValue = fromPlayer(event) ? stats() : null;
  });
  // `unlock` / `lock` commands from the admin, forwarded by the page.
  ipcMain.on('shell-command', (event, json) => {
    if (!fromPlayer(event) || SMOKE) return;
    const c = parseCommand(json);
    if (!c) return;
    if (c.action === 'unlock') {
      if (S.lock.unlockFor(c.minutes, c.rid)) log.info('unlocked remotely for', c.minutes, 'min');
    } else if (S.lock.relock(c.rid)) {
      log.info('locked again remotely');
    }
  });
}

// Keeps the unlock PIN hash, "Player controls" and "Lock player" from the manifest's
// `settings` in config.json, so they apply when the app starts offline. An old server sends
// no `settings`: no change.
function applySettings(json) {
  if (SMOKE || !S.cfg.serverUrl) return;
  let settings;
  try { settings = JSON.parse(json).settings; } catch (e) { return; }
  const patch = config.settingsPatch(S.cfg, settings);
  if (!Object.keys(patch).length) return;
  if ('unlock' in patch) log.info(patch.unlock ? 'unlock PIN set by the server' : 'unlock PIN removed by the server');
  if ('allowInput' in patch) log.info('player controls', patch.allowInput ? 'on' : 'off');
  if ('kioskLock' in patch) log.info('lock player', patch.kioskLock ? 'on' : 'off');
  const next = Object.assign({}, S.cfg, patch);
  try {
    S.cfg = config.saveConfig(DATA, next);
  } catch (e) {
    S.cfg = next; // still applied until the next start
    log.warn('could not save the display settings', e.message);
  }
  S.lock.configure({ kioskLock: S.cfg.kioskLock });
}

// A JPEG of the player window (what the screen shows), longest side at most 1920, or null.
async function capture() {
  try {
    if (!S.player || S.player.win.isDestroyed()) return null;
    let img = await S.player.win.webContents.capturePage();
    if (!img || img.isEmpty()) return null;
    const { width, height } = img.getSize();
    const scale = Math.min(1, CAPTURE_MAX_SIDE / Math.max(width, height));
    if (scale < 1) img = img.resize({ width: Math.round(width * scale), height: Math.round(height * scale), quality: 'good' });
    return img.toJPEG(80);
  } catch (e) {
    log.warn('screen capture failed', e.message);
    return null;
  }
}

function stats() {
  const out = { uptime_s: Math.floor(process.uptime()), cache: S.mediaCache ? S.mediaCache.stats() : null,
    lock: S.lock ? S.lock.state() : 'off' };
  try {
    const st = fs.statfsSync(DATA);
    out.storage_free_bytes = st.bavail * st.bsize;
  } catch (e) { /* not reported */ }
  if (S.agent) out.agent = S.agent.stats();
  return out;
}

function lockDownSession() {
  const ses = session.defaultSession;
  // Players and web items get no camera, microphone, location, notifications and so on.
  ses.setPermissionRequestHandler((wc, permission, callback) => callback(permission === 'fullscreen'));
  ses.setPermissionCheckHandler((wc, permission) => permission === 'fullscreen');
  ses.cookies.on('changed', (event, cookie) => { if (cookie.name === COOKIE_NAME) refreshCookie(); });
}

function startAgent() {
  S.agent = createAgent({
    dataDir: DATA,
    version: app.getVersion(),
    log,
    isPackaged: app.isPackaged,
    exe: process.execPath,
    exeArgs: process.argv.slice(1).filter((a) => a.startsWith('--user-data-dir=')),
    scriptSource: path.join(app.isPackaged ? process.resourcesPath : path.join(__dirname, '..', 'resources'), 'watchdog.ps1'),
    getUpstream: () => S.upstream,
    getToken: () => S.cookie,
    getConfig: () => S.cfg,
    readLogs: () => log.readAll(),
    offlinePage: () => !!S.player && !S.player.win.isDestroyed() && S.player.win.webContents.getURL().startsWith('file:'),
    relaunch: () => { S.sessionEnd = true; app.relaunch(); app.exit(0); },
    // Tier B (signed commands, docs/signed-commands.md).
    launcherSource: path.join(app.isPackaged ? process.resourcesPath : path.join(__dirname, '..', 'resources'), 'shell-launcher.ps1'),
    startup: {
      get: () => app.getLoginItemSettings().openAtLogin,
      set: (on) => { S.cfg = config.saveConfig(DATA, Object.assign({}, S.cfg, { autostart: on })); kiosk.applyAutostart(on); },
    },
    // The update quits WITHOUT the 'stopped' marker (the agent wrote 'updating' instead), so neither the
    // watchdog nor the shell launcher takes it for a deliberate exit and Explorer does not take over.
    quit: () => { S.sessionEnd = true; app.quit(); },
    spawnInstaller: (file, args) => systemOps.spawnInstaller(file, args, 2000),
  });
  S.agent.start();
}

// ---- Startup.
async function main() {
  lockDownSession();
  registerIpc();
  S.clientId = config.clientId(DATA);
  S.gate = unlock.createGate({ dataDir: DATA });
  if (!SMOKE) startAgent();
  S.lock = createLock();
  S.lock.onChange((state) => {
    log.info('lock state', state);
    if (S.player) S.player.applyLockLevel();
  });
  const suspendPlayer = (on, reason) => { if (S.player && !S.player.win.isDestroyed()) S.player.suspend(on, reason); };
  S.unlockWin = createUnlockWindow({
    getUnlock: () => S.cfg.unlock,
    gate: S.gate,
    // A remote unlock skips the PIN only while Lock player is on; otherwise the PIN guards as before.
    isRemoteUnlocked: () => !!S.cfg.kioskLock && S.lock.state() === 'unlocked',
    onOpen: () => suspendPlayer(true, 'unlock'),
    onClose: () => suspendPlayer(false, 'unlock'),
  });

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
    onOpen: () => suspendPlayer(true, 'setup'),
    onClosed: () => {
      suspendPlayer(false, 'setup');
      if (!S.player && !S.quitting) app.quit();
    },
    quit: () => app.quit(),
  });
  // Every Ctrl+Alt shortcut asks for the unlock PIN when the server set one.
  const guard = S.unlockWin.guard;
  kiosk.registerShortcuts({
    setup: guard('setup', openSetup),
    log: guard('log', () => shell.openPath(log.file)),
    devtools: S.unlockWin.guardWindow('devtools', {
      focused: () => BrowserWindow.getFocusedWindow(),
      fallback: () => S.player && S.player.win,
    }, (w) => w.webContents.openDevTools({ mode: 'detach' })),
    quit: guard('quit', () => app.quit()),
  });
  kiosk.keepAwake();

  const loaded = config.loadConfig(DATA);
  S.cfg = loaded.config;
  S.lock.configure({ kioskLock: S.cfg.kioskLock });
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
  app.on('before-quit', () => {
    S.quitting = true;
    // A deliberate quit (PIN exit, setup Quit, window closed): the watchdog must not restart it.
    if (S.agent && !S.sessionEnd) S.agent.markStopped();
  });
  app.on('will-quit', () => {
    kiosk.unregisterShortcuts();
    kiosk.releaseAwake();
    stopProxy().catch(() => {});
  });
}
