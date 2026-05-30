// SPDX-License-Identifier: AGPL-3.0-or-later
// Copyright (C) 2026 AISignX contributors

const {
  app, BrowserWindow, ipcMain, screen, powerSaveBlocker, dialog, shell,
  globalShortcut, powerMonitor
} = require('electron');
const path   = require('path');
const fs     = require('fs');
const os     = require('os');
const https  = require('https');
const crypto = require('crypto');
const http   = require('http');

const CONFIG_PATH    = path.join(app.getPath('userData'), 'config.json');
const DEVICE_ID_PATH = path.join(app.getPath('userData'), 'device_id.txt');
const LOG_PATH       = path.join(app.getPath('userData'), 'aisignx-player.log');
const POLL_INTERVAL_MS   = 5_000;
const UPDATE_INTERVAL_MS = 15 * 60 * 1000;  // 15 min

let mainWindow  = null;
let psBlockerId = null;
let updateTimer = null;
let displayServerAutoUpdate = false;

// ── File logger ───────────────────────────────────────────────────────────────
// Writes to %APPDATA%\AISignX Player\aisignx-player.log so we can diagnose
// problems on production builds where DevTools is not available.
function flog(...parts) {
  const line = '[' + new Date().toISOString() + '] ' + parts.join(' ') + '\n';
  try { fs.appendFileSync(LOG_PATH, line); } catch (_) {}
  try { console.log.apply(console, parts); } catch (_) {}
}
// Trim log if it gets too big (>2MB)
try {
  if (fs.existsSync(LOG_PATH) && fs.statSync(LOG_PATH).size > 2_000_000) {
    fs.writeFileSync(LOG_PATH, '');
  }
} catch (_) {}
flog('=== AISignX Player started, version', app.getVersion(), '===');

// ── Config helpers ────────────────────────────────────────────────────────────
function loadConfig() {
  try {
    if (fs.existsSync(CONFIG_PATH)) {
      return JSON.parse(fs.readFileSync(CONFIG_PATH, 'utf8'));
    }
  } catch (_) {}
  return {};
}

function saveConfig(data) {
  fs.writeFileSync(CONFIG_PATH, JSON.stringify(data, null, 2));
}

function getDeviceId() {
  if (fs.existsSync(DEVICE_ID_PATH))
    return fs.readFileSync(DEVICE_ID_PATH, 'utf8').trim();
  const id = crypto.randomUUID();
  fs.writeFileSync(DEVICE_ID_PATH, id);
  return id;
}

// ── Offline cache enablement ──────────────────────────────────────────────────
// The player relies on a Service Worker (/static/sw.js) to cache media so it
// keeps playing when the server is unreachable. Chromium only registers
// service workers in a "secure context" (https or localhost). Most signage
// deployments run the server over plain HTTP on a LAN IP/hostname, where the
// SW would silently never register — so nothing gets cached and videos fail
// the moment the network drops. We mark the configured server origin as a
// trusted secure origin so the SW (and the whole offline cache) works over
// HTTP too. This MUST run before app `ready`, hence at module load.
(function enableOfflineCacheForServer() {
  try {
    const cfg = loadConfig();
    if (!cfg.serverUrl) return;
    const origin = new URL(cfg.serverUrl).origin;
    if (!origin.startsWith('http://')) return;   // https is already secure
    app.commandLine.appendSwitch('unsafely-treat-insecure-origin-as-secure', origin);
    // Chromium ignores the flag above unless a --user-data-dir is present.
    // Electron always has one; pass it explicitly to satisfy the check.
    app.commandLine.appendSwitch('user-data-dir', app.getPath('userData'));
    flog('[offline] treating', origin, 'as secure origin (service worker cache)');
  } catch (e) {
    flog('[offline] secure-origin switch failed:', String(e));
  }
})();

// ── HTTP helper (works for http + https, no external deps) ───────────────────
function doRequest(urlStr, options = {}, body = null) {
  return new Promise((resolve, reject) => {
    const u = new URL(urlStr);
    const mod = u.protocol === 'https:' ? https : http;
    const req = mod.request({
      hostname: u.hostname,
      port: u.port || (u.protocol === 'https:' ? 443 : 80),
      path: u.pathname + u.search,
      method: options.method || 'GET',
      headers: options.headers || {},
      rejectUnauthorized: false   // allow self-signed certs on local servers
    }, res => {
      let data = '';
      res.on('data', c => { data += c; });
      res.on('end', () => {
        try { resolve({ status: res.statusCode, body: JSON.parse(data) }); }
        catch (_) { resolve({ status: res.statusCode, body: data }); }
      });
    });
    req.on('error', reject);
    if (body) req.write(typeof body === 'string' ? body : JSON.stringify(body));
    req.end();
  });
}

// ── Semver comparison ─────────────────────────────────────────────────────────
function isNewer(localVer, remoteVer) {
  const parse = v => String(v || '0').split('.').map(n => parseInt(n) || 0);
  const l = parse(localVer);
  const r = parse(remoteVer);
  for (let i = 0; i < 3; i++) {
    if (r[i] > l[i]) return true;
    if (r[i] < l[i]) return false;
  }
  return false;
}

function clientKey() {
  return process.platform === 'win32' ? 'windows' : 'linux_appimage';
}

function normalizeUpdateMode(mode) {
  const m = String(mode || 'prompt').toLowerCase();
  if (m === 'auto' || m === 'manual') return m;
  return 'prompt';
}

function effectiveUpdateMode() {
  const cfg = loadConfig();
  if (displayServerAutoUpdate) return 'auto';
  return normalizeUpdateMode(cfg.updateMode);
}

// ── Update checker ────────────────────────────────────────────────────────────
async function checkForUpdate(silent = true) {
  const cfg = loadConfig();
  if (!cfg.serverUrl) return;
  const updateMode = effectiveUpdateMode();
  if (updateMode === 'manual' && silent) return;
  try {
    const res = await doRequest(`${cfg.serverUrl}/api/version`);
    if (!res.body || !res.body.clients) return;
    const remote = res.body.clients[clientKey()];
    if (!remote || !remote.version) return;
    const localVer = app.getVersion();
    if (!isNewer(localVer, remote.version)) {
      if (!silent) {
        dialog.showMessageBox(mainWindow, {
          type: 'info', title: 'Up to date',
          message: `You are running the latest version (${localVer}).`
        });
      }
      return;
    }
    if (updateMode === 'auto') {
      flog('[update] auto mode: installing', remote.version);
      await runSilentUpdate();
      return;
    }
    const downloadUrl = cfg.serverUrl + remote.url;
    const choice = await dialog.showMessageBox(mainWindow, {
      type: 'info',
      title: 'Update Available',
      message: `New version available: ${remote.version}  (you have ${localVer})`,
      detail: updateMode === 'prompt'
        ? 'Install now to update this player.'
        : 'Click Download to open the installer in your browser.',
      buttons: updateMode === 'prompt' ? ['Install Now', 'Later'] : ['Download', 'Later'],
      defaultId: 0,
      cancelId: 1
    });
    if (choice.response !== 0) return;
    if (updateMode === 'prompt') {
      await runSilentUpdate();
    } else {
      shell.openExternal(downloadUrl);
    }
  } catch (err) {
    if (!silent) {
      dialog.showMessageBox(mainWindow, {
        type: 'error',
        title: 'Update check failed',
        message: String(err && err.message || err)
      });
    }
  }
}

function scheduleUpdateChecks() {
  if (effectiveUpdateMode() === 'manual') {
    if (updateTimer) clearInterval(updateTimer);
    updateTimer = null;
    return;
  }
  if (updateTimer) clearInterval(updateTimer);
  setTimeout(() => checkForUpdate(true), 10_000);
  updateTimer = setInterval(() => checkForUpdate(true), UPDATE_INTERVAL_MS);
}

// ── Silent update (admin-triggered "update" command) ──────────────────────────
// Downloads the latest installer for this platform, then quits and runs it.
// Used when an admin clicks "Update Client" on the display detail page.
function downloadFile(url, destPath) {
  return new Promise((resolve, reject) => {
    const lib = url.startsWith('https') ? https : http;
    const file = fs.createWriteStream(destPath);
    lib.get(url, (res) => {
      if (res.statusCode >= 300 && res.statusCode < 400 && res.headers.location) {
        // Follow redirect
        file.close();
        fs.unlink(destPath, () => {});
        return downloadFile(res.headers.location, destPath).then(resolve, reject);
      }
      if (res.statusCode !== 200) {
        file.close();
        fs.unlink(destPath, () => {});
        return reject(new Error('HTTP ' + res.statusCode));
      }
      res.pipe(file);
      file.on('finish', () => file.close(() => resolve(destPath)));
    }).on('error', (err) => {
      file.close();
      fs.unlink(destPath, () => {});
      reject(err);
    });
  });
}

async function runSilentUpdate() {
  const cfg = loadConfig();
  if (!cfg.serverUrl) throw new Error('No server URL configured');
  flog('[update] fetching manifest from', cfg.serverUrl + '/api/version');
  const res = await doRequest(`${cfg.serverUrl}/api/version`);
  if (!res.body || !res.body.clients) throw new Error('No client manifest in /api/version response');
  const remote = res.body.clients[clientKey()];
  if (!remote || !remote.url) throw new Error('No installer URL for platform "' + clientKey() + '" in manifest');
  if (!remote.version || !isNewer(app.getVersion(), remote.version)) {
    throw new Error('No newer client is published on the server');
  }

  const downloadUrl = cfg.serverUrl + remote.url;
  const ext = path.extname(remote.filename || '') || '.exe';
  const tmpFile = path.join(app.getPath('temp'),
    `aisignx-update-${Date.now()}${ext}`);

  flog('[update] downloading', downloadUrl, '->', tmpFile);
  await downloadFile(downloadUrl, tmpFile);
  let size = 0;
  try { size = fs.statSync(tmpFile).size; } catch (_) {}
  flog('[update] download complete:', size, 'bytes; launching installer');

  if (process.platform === 'win32') {
    // Per-user NSIS installs spawned by the running app don't reliably
    // honor `runAfterFinish`, so we orchestrate the relaunch ourselves
    // with a PowerShell wrapper that writes its own log. PowerShell is
    // used instead of .bat because batch's quoted-path IF/ELSE blocks
    // are fragile and timeout.exe shows a visible countdown window.
    const installedExe = path.join(
      process.env.LOCALAPPDATA || app.getPath('appData'),
      'Programs', 'aisignx-player', 'AISignX Player.exe'
    );
    const wrapperPs1 = path.join(app.getPath('temp'),
      `aisignx-update-wrapper-${Date.now()}.ps1`);
    const wrapperLog = path.join(app.getPath('userData'),
      'aisignx-update-wrapper.log');

    const psEscape = (s) => s.replace(/'/g, "''");
    const psContent = [
      '$ErrorActionPreference = "Continue"',
      `$log = '${psEscape(wrapperLog)}'`,
      'function Log($m) { Add-Content -Path $log -Value ("[{0}] {1}" -f (Get-Date -Format o), $m) }',
      'Log "=== wrapper started ==="',
      'Log "waiting 3s for app to exit"',
      'Start-Sleep -Seconds 3',
      `$installer = '${psEscape(tmpFile)}'`,
      'Log "running installer: $installer"',
      'try {',
      '  $proc = Start-Process -FilePath $installer -ArgumentList "/S" -Wait -PassThru -WindowStyle Hidden',
      '  Log "installer exit code: $($proc.ExitCode)"',
      '} catch {',
      '  Log "installer threw: $_"',
      '}',
      'Log "waiting 3s for install to settle"',
      'Start-Sleep -Seconds 3',
      `$exe = '${psEscape(installedExe)}'`,
      'Log "checking exe at: $exe"',
      'if (Test-Path -LiteralPath $exe) {',
      '  Log "exe exists, launching"',
      '  try {',
      '    Start-Process -FilePath $exe -WindowStyle Maximized',
      '    Log "Start-Process returned"',
      '  } catch {',
      '    Log "Start-Process threw: $_"',
      '  }',
      '} else {',
      '  Log "EXE NOT FOUND at $exe"',
      '}',
      'Log "=== wrapper done ==="',
      'Remove-Item -LiteralPath $MyInvocation.MyCommand.Path -Force -ErrorAction SilentlyContinue'
    ].join('\r\n');

    try {
      fs.writeFileSync(wrapperPs1, psContent);
      flog('[update] wrote wrapper:', wrapperPs1);
      flog('[update] wrapper log will be at:', wrapperLog);
      flog('[update] installer:', tmpFile);
      flog('[update] will relaunch:', installedExe);
    } catch (err) {
      flog('[update] failed to write wrapper ps1:', String(err));
      throw err;
    }

    const { spawn } = require('child_process');
    flog('[update] scheduling powershell wrapper via schtasks');
    try {
      // Schedule a one-time task 5 seconds in the future. Task Scheduler
      // runs as a Windows service, fully independent of our process tree,
      // so it survives our app.exit(0) without any inheritance issues.
      // After the task fires, we delete it from inside the wrapper itself
      // (last line: schtasks /delete /tn AISignXUpdate /f).
      const stderrLog = path.join(app.getPath('userData'),
        'aisignx-update-wrapper.stderr.log');
      const taskName = 'AISignXUpdate_' + Date.now();

      // Schedule the task ~75 seconds in the future. schtasks /st rounds
      // to whole minutes, so if we ask for "now + 5s" the rounding can
      // land in the past and the task never fires. 75s gives us at least
      // one full minute of headroom even after rounding.
      const runTime = new Date(Date.now() + 75_000);
      const hh = String(runTime.getHours()).padStart(2, '0');
      const mm = String(runTime.getMinutes()).padStart(2, '0');
      const sd = String(runTime.getMonth() + 1).padStart(2, '0') + '/' +
                 String(runTime.getDate()).padStart(2, '0') + '/' +
                 runTime.getFullYear();

      const taskCmd = 'powershell.exe -NoProfile -NonInteractive ' +
        '-WindowStyle Hidden -ExecutionPolicy Bypass ' +
        '-File "' + wrapperPs1 + '" 2> "' + stderrLog + '"';

      flog('[update] task name:', taskName, 'run at:', hh + ':' + mm, 'on', sd);

      const child = spawn('schtasks.exe', [
        '/create', '/f',
        '/tn', taskName,
        '/sc', 'once',
        '/sd', sd,
        '/st', hh + ':' + mm,
        '/tr', taskCmd
      ], {
        detached:    true,
        stdio:       'ignore',
        windowsHide: true
      });
      child.on('error', err => flog('[update] schtasks error:', String(err)));
      child.unref();

      // Append a self-delete to the wrapper so the task gets removed after running
      try {
        fs.appendFileSync(wrapperPs1,
          '\r\nStart-Process -WindowStyle Hidden -FilePath schtasks.exe ' +
          '-ArgumentList \'/delete\', \'/tn\', \'' + taskName + '\', \'/f\'\r\n');
      } catch (_) {}

      flog('[update] task scheduled; quitting in 2s');
      flog('[update] PS stderr will be at:', stderrLog);
      setTimeout(() => {
        flog('[update] win: app.exit(0)');
        app.exit(0);
      }, 2000);
    } catch (err) {
      flog('[update] schtasks spawn threw:', String(err));
      throw err;
    }
  } else {
    if (tmpFile.endsWith('.AppImage')) {
      try { fs.chmodSync(tmpFile, 0o755); } catch (_) {}
      const { spawn } = require('child_process');
      spawn(tmpFile, [], { detached: true, stdio: 'ignore' }).unref();
    } else {
      shell.openPath(tmpFile);
    }
    // For Linux, we DO need to exit so the installer (or user-launched
    // package manager) can replace files.
    setTimeout(() => {
      flog('[update] linux: app.exit(0)');
      app.exit(0);
    }, 1500);
  }
}

// ── Window factory ────────────────────────────────────────────────────────────
// We ALWAYS create the window in fullscreen kiosk mode so there is never a
// visible windowed phase on launch (kiosks should never show a window).
// Setup and waiting screens render fine fullscreen too.
function ensureWindow() {
  if (mainWindow && !mainWindow.isDestroyed()) return;
  const { width, height } = screen.getPrimaryDisplay().workAreaSize;
  mainWindow = new BrowserWindow({
    width, height,
    fullscreen: true,
    frame: false,
    kiosk: true,
    show: false,                   // hide until first paint to avoid flash
    autoHideMenuBar: true,
    backgroundColor: '#000000',
    webPreferences: {
      preload:          path.join(__dirname, 'preload.js'),
      contextIsolation: true,
      nodeIntegration:  false,
      webSecurity:      false
    }
  });
  mainWindow.once('ready-to-show', () => {
    mainWindow.show();
    // Force foreground + kiosk fullscreen. When launched by a wrapper
    // batch script after an update, Windows may put us behind other
    // windows or minimized; this re-asserts our kiosk state.
    try {
      mainWindow.setAlwaysOnTop(true);
      mainWindow.setKiosk(true);
      mainWindow.setFullScreen(true);
      mainWindow.focus();
      mainWindow.moveTop();
      // Drop always-on-top after we have focus so dialogs etc. can appear
      setTimeout(() => {
        try { mainWindow.setAlwaysOnTop(false); } catch (_) {}
      }, 1000);
    } catch (_) {}
  });

  // Block built-in browser shortcuts that would let a passer-by escape
  // the kiosk: Ctrl+R reload, Ctrl+Shift+I devtools, F11 fullscreen
  // toggle, F12 devtools, Ctrl+W close, Ctrl+Q quit, Alt+F4 close,
  // Ctrl+- / Ctrl+= zoom, Backspace navigate-back, etc. The admin
  // hotkeys (Ctrl+Alt+D, Ctrl+Alt+L, Ctrl+Alt+Q) are registered as
  // globalShortcut so they bypass this filter.
  mainWindow.webContents.on('before-input-event', (event, input) => {
    if (input.type !== 'keyDown') return;
    const k = (input.key || '').toLowerCase();
    const ctrl = !!input.control;
    const shift = !!input.shift;
    const alt = !!input.alt;
    // Allow admin combos (Ctrl+Alt+anything) to fall through.
    if (ctrl && alt) return;
    const blocked =
      // Function keys that toggle DevTools / fullscreen / reload
      (k === 'f5' || k === 'f11' || k === 'f12') ||
      // DevTools open
      (ctrl && shift && (k === 'i' || k === 'j' || k === 'c')) ||
      // Reload, hard reload
      (ctrl && (k === 'r' || k === 'shift+r')) ||
      // Close / quit
      (ctrl && (k === 'w' || k === 'q')) ||
      (alt  && k === 'f4') ||
      // Zoom (Ctrl + or - or 0)
      (ctrl && (k === '-' || k === '+' || k === '=' || k === '0')) ||
      // Navigation
      (alt  && (k === 'arrowleft' || k === 'arrowright')) ||
      (k === 'backspace');
    if (blocked) event.preventDefault();
  });
  if (psBlockerId === null)
    psBlockerId = powerSaveBlocker.start('prevent-display-sleep');
  mainWindow.on('closed', () => { mainWindow = null; });
  // If we were minimized for an unlock and the user brings the window back
  // (taskbar click, Alt-Tab), treat that as "exit unlock mode": re-kiosk and
  // re-lock immediately.
  mainWindow.on('restore', () => { if (_minimizedForUnlock) restoreKioskAndLock('user'); });
  mainWindow.on('focus',   () => { if (_minimizedForUnlock) restoreKioskAndLock('user'); });
  attachOfflineRetryHandlers();
}

function loadLocalFile(file) {
  ensureWindow();
  mainWindow.setMenuBarVisibility(false);
  mainWindow.loadFile(path.join(__dirname, file));
}

function loadPlayerUrl(serverUrl, token) {
  const deviceId = encodeURIComponent(getDeviceId());
  const url = `${serverUrl.replace(/\/$/, '')}/display/${token}?client_id=${deviceId}`;
  ensureWindow();
  mainWindow.setMenuBarVisibility(false);
  try { mainWindow.webContents.closeDevTools(); } catch (_) {}
  // Track the most-recently-requested player URL so the retry timer can
  // reload it without re-reading config every cycle.
  _lastPlayerUrl = url;
  mainWindow.loadURL(url);
}

// ── Player URL retry on connection failure ────────────────────────────────────
// When the server is unreachable on cold start (no cached service worker, no
// cached page), Chromium normally shows a blank/error page. We catch the
// did-fail-load event, swap in a friendly "Waiting for server" page, and
// keep retrying every 10s in the background. As soon as the server comes
// back online we reload the real player URL.
let _lastPlayerUrl     = null;
let _retryTimer        = null;
let _showingOffline    = false;
const RETRY_INTERVAL_MS = 10_000;

function attachOfflineRetryHandlers() {
  if (!mainWindow || mainWindow._offlineHandlersAttached) return;
  mainWindow._offlineHandlersAttached = true;

  mainWindow.webContents.on('did-fail-load',
    (_evt, errorCode, errorDescription, validatedURL, isMainFrame) => {
      if (!isMainFrame) return;
      // -3 = ABORTED (we triggered another navigation). Ignore.
      if (errorCode === -3) return;
      // Only retry when the failed URL is the player URL we just tried.
      if (!_lastPlayerUrl || validatedURL !== _lastPlayerUrl) return;
      flog('[offline] did-fail-load', errorCode, errorDescription, validatedURL);
      _showingOffline = true;
      // Show the friendly waiting screen; pass the URL via hash so it can be
      // displayed.
      const offlineHtml = path.join(__dirname, 'offline.html');
      mainWindow.loadFile(offlineHtml, { hash: encodeURIComponent(_lastPlayerUrl) });
      scheduleRetry();
    });

  mainWindow.webContents.on('did-finish-load', () => {
    const currentUrl = mainWindow.webContents.getURL();
    if (currentUrl.includes('offline.html')) {
      scheduleRetry();
      return;
    }
    if (_lastPlayerUrl && currentUrl.startsWith(_lastPlayerUrl.split('?')[0])) {
      _showingOffline = false;
      if (_retryTimer) {
        clearTimeout(_retryTimer);
        _retryTimer = null;
      }
    }
  });
}

function scheduleRetry() {
  if (_retryTimer) clearTimeout(_retryTimer);
  _retryTimer = setTimeout(() => {
    if (!_lastPlayerUrl || !mainWindow || mainWindow.isDestroyed()) return;
    if (!_showingOffline) return;
    flog('[offline] retrying', _lastPlayerUrl);
    mainWindow.loadURL(_lastPlayerUrl);
  }, RETRY_INTERVAL_MS);
}

// ── Unlock → minimize → idle restore ──────────────────────────────────────────
// When the on-screen PIN unlocks the kiosk, the player asks us (via the
// 'unlock-minimize' IPC) to drop out of kiosk mode and minimize so a technician
// can reach the desktop. The window then STAYS minimized until either:
//   * the OS has been idle for IDLE_RESTORE_SECONDS (5 min), or
//   * the user restores the window themselves (taskbar / Alt-Tab),
// at which point we re-assert kiosk fullscreen and tell the player to re-lock.
const IDLE_RESTORE_SECONDS = 5 * 60;
let _idlePollTimer    = null;
let _minimizedForUnlock = false;

function startUnlockMinimize() {
  if (!mainWindow || mainWindow.isDestroyed()) return;
  flog('[unlock] minimizing kiosk for desktop access');
  _minimizedForUnlock = true;
  try {
    mainWindow.setAlwaysOnTop(false);
    mainWindow.setKiosk(false);
    mainWindow.setFullScreen(false);
    mainWindow.minimize();
  } catch (e) { flog('[unlock] minimize failed:', String(e)); }

  if (_idlePollTimer) clearInterval(_idlePollTimer);
  _idlePollTimer = setInterval(() => {
    if (!_minimizedForUnlock) return;
    let idle = 0;
    try { idle = powerMonitor.getSystemIdleTime(); } catch (_) {}
    if (idle >= IDLE_RESTORE_SECONDS) {
      flog('[unlock] system idle', idle, 's -> restoring kiosk');
      restoreKioskAndLock('idle');
    }
  }, 15_000);
}

function restoreKioskAndLock(reason) {
  if (_idlePollTimer) { clearInterval(_idlePollTimer); _idlePollTimer = null; }
  if (!_minimizedForUnlock) return;
  // Flip the flag first so the restore()-triggered 'restore'/'focus' events
  // below short-circuit instead of re-entering this function.
  _minimizedForUnlock = false;
  flog('[unlock] restoring kiosk + re-locking, reason=', reason);
  if (!mainWindow || mainWindow.isDestroyed()) return;
  try {
    mainWindow.restore();
    mainWindow.setKiosk(true);
    mainWindow.setFullScreen(true);
    mainWindow.setAlwaysOnTop(true);
    mainWindow.show();
    mainWindow.focus();
    mainWindow.moveTop();
    setTimeout(() => { try { mainWindow.setAlwaysOnTop(false); } catch (_) {} }, 1000);
  } catch (e) { flog('[unlock] restore failed:', String(e)); }
  try { mainWindow.webContents.send('relock-kiosk', { reason }); } catch (_) {}
}

// ── Registration polling ──────────────────────────────────────────────────────
async function pollRegistration(serverUrl, deviceId) {
  try {
    const res = await doRequest(`${serverUrl}/api/register/status/${deviceId}`);
    if (res.body?.status === 'approved') {
      const cfg = loadConfig();
      cfg.token = res.body.token;
      saveConfig(cfg);
      if (mainWindow) mainWindow.webContents.send('registration-approved');
      setTimeout(() => {
        loadPlayerUrl(serverUrl, res.body.token);
        scheduleUpdateChecks();
      }, 1500);
      return;
    }
    if (res.body?.status === 'declined') {
      if (mainWindow) mainWindow.webContents.send('registration-declined');
      return;
    }
  } catch (_) {}
  setTimeout(() => pollRegistration(serverUrl, deviceId), POLL_INTERVAL_MS);
}

// ── IPC ───────────────────────────────────────────────────────────────────────
ipcMain.handle('get-config',       ()  => loadConfig());
ipcMain.handle('get-device-id',    ()  => getDeviceId());
ipcMain.handle('get-app-version',  ()  => app.getVersion());
ipcMain.handle('set-display-auto-update', (_, on) => {
  displayServerAutoUpdate = !!on;
  scheduleUpdateChecks();
  return true;
});

async function applyClientConfig(raw) {
  try {
    const data = typeof raw === 'string' ? JSON.parse(raw) : raw;
    if (!data || data.format !== 'aisignx-player-config') {
      return { ok: false, error: 'Invalid config format' };
    }
    const url = String(data.server_url || '').trim().replace(/\/$/, '');
    if (!url) return { ok: false, error: 'server_url required' };
    try {
      const ver = await doRequest(`${url}/api/version`);
      if (ver.status !== 200) {
        return {
          ok: false,
          error: `Server responded with status ${ver.status}. Check server_url and that the server is running.`
        };
      }
    } catch (e) {
      return { ok: false, error: 'Cannot reach server: ' + (e && e.message ? e.message : String(e)) };
    }
    const token = String(data.display_token || data.token || '').trim();
    const enroll = String(data.enrollment_code || '').trim();
    if (!token && !enroll) {
      return { ok: false, error: 'Missing display_token and enrollment_code' };
    }
    const cfg = loadConfig();
    cfg.serverUrl = url;
    if (data.update_mode) cfg.updateMode = normalizeUpdateMode(data.update_mode);
    if (token) {
      cfg.token = token;
      saveConfig(cfg);
      setTimeout(() => {
        loadPlayerUrl(url, token);
        scheduleUpdateChecks();
      }, 400);
      return { ok: true, mode: 'player' };
    }
    delete cfg.token;
    saveConfig(cfg);
    return { ok: true, mode: 'enrollment', enrollment: enroll, config: data };
  } catch (e) {
    return { ok: false, error: String(e && e.message ? e.message : e) };
  }
}

ipcMain.handle('apply-client-config', async (_, raw) => {
  return applyClientConfig(raw);
});

ipcMain.handle('browse-client-config', async () => {
  try {
    const result = await dialog.showOpenDialog(mainWindow, {
      title: 'Select AISignX setup file',
      properties: ['openFile'],
      filters: [
        { name: 'AISignX setup file', extensions: ['json'] },
        { name: 'All files', extensions: ['*'] }
      ]
    });
    if (result.canceled || !result.filePaths || !result.filePaths[0]) {
      return { ok: false, canceled: true };
    }
    const raw = fs.readFileSync(result.filePaths[0], 'utf8');
    const applied = await applyClientConfig(raw);
    return { ...applied, filePath: result.filePaths[0] };
  } catch (e) {
    return { ok: false, error: String(e && e.message ? e.message : e) };
  }
});
ipcMain.handle('get-system-info',  ()  => ({
  hostname: os.hostname(),
  platform: os.platform(),
  release:  os.release(),
  version:  app.getVersion()
}));
ipcMain.handle('check-for-update', ()  => checkForUpdate(false));

// Player asks the shell to minimize after a successful PIN unlock so a
// technician can reach the desktop; the window auto-restores + re-locks on
// idle or when the user brings it back. See startUnlockMinimize().
ipcMain.handle('unlock-minimize', () => {
  startUnlockMinimize();
  return { ok: true };
});

// Handle admin-pushed commands relayed from the player page via SSE.
// Supported actions: 'reboot', 'update', 'reload'.
ipcMain.handle('run-command', async (_, action, payload) => {
  try {
    flog('[command] received:', action, JSON.stringify(payload || {}));
    switch ((action || '').toLowerCase()) {
      case 'reload':
        flog('[command] reload: reloading window');
        if (mainWindow && !mainWindow.isDestroyed()) mainWindow.reload();
        return { ok: true };

      case 'reboot':
        flog('[command] reboot: relaunching app');
        app.relaunch();
        app.exit(0);
        return { ok: true };

      case 'update':
        flog('[command] update: starting silent update');
        runSilentUpdate().catch(err => {
          const msg = err && err.stack ? err.stack : String(err);
          flog('[update] FAILED:', msg);
          try {
            dialog.showErrorBox('Update failed',
              'The auto-update failed:\n\n' + String(err && err.message || err) +
              '\n\nLog file: ' + LOG_PATH);
          } catch (e2) {
            flog('[update] showErrorBox itself failed:', String(e2));
          }
        });
        return { ok: true };

      default:
        flog('[command] unknown action:', action);
        return { ok: false, error: 'Unknown action: ' + action };
    }
  } catch (e) {
    flog('[command] handler crashed:', e && e.stack ? e.stack : String(e));
    return { ok: false, error: String(e) };
  }
});

ipcMain.handle('save-server-url', async (_, serverUrl) => {
  const url = serverUrl.trim().replace(/\/$/, '');
  try {
    const res = await doRequest(`${url}/api/version`);
    if (res.status !== 200) {
      return { ok: false, error: `Server responded with status ${res.status}. Check the URL and make sure the server is running.` };
    }
  } catch (e) {
    return { ok: false, error: 'Cannot reach server: ' + e.message };
  }
  const cfg = loadConfig();
  cfg.serverUrl = url;
  delete cfg.token;
  saveConfig(cfg);
  return { ok: true };
});

ipcMain.handle('register-device', async (_, { serverUrl, friendlyName, enrollmentCode, updateMode }) => {
  const deviceId = getDeviceId();
  const body = {
    device_id:     deviceId,
    friendly_name: friendlyName || os.hostname(),
    hostname:      os.hostname(),
    os:            `${os.platform()} ${os.release()}`,
    app_version:   app.getVersion(),
    // Server requires a per-tenant enrollment code so a hostile/misconfigured
    // device cannot pick which tenant it lands in. The code resolves the
    // domain server-side; without one /api/register returns 400.
    enrollment_code: (enrollmentCode || '').replace(/[\s-]/g, '').toUpperCase(),
    resolution:    (() => {
      const { width, height } = screen.getPrimaryDisplay().bounds;
      return `${width}x${height}`;
    })()
  };
  try {
    const res = await doRequest(`${serverUrl}/api/register`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' }
    }, body);
    if (res.body?.status === 'approved') {
      const cfg = loadConfig();
      cfg.token = res.body.token;
      cfg.updateMode = normalizeUpdateMode(updateMode || cfg.updateMode);
      saveConfig(cfg);
      // Immediately launch player — no need to wait for poll
      setTimeout(() => {
        loadPlayerUrl(serverUrl, res.body.token);
        scheduleUpdateChecks();
      }, 800);
      return { status: 'approved', token: res.body.token };
    }
    if (res.body?.status === 'pending') {
      setTimeout(() => pollRegistration(serverUrl, deviceId), POLL_INTERVAL_MS);
      return { status: 'pending' };
    }
    return { status: 'error', error: res.body?.message || 'Unknown error' };
  } catch (e) {
    return { status: 'error', error: e.message };
  }
});

ipcMain.handle('clear-config', () => {
  saveConfig({});
  loadLocalFile('setup.html');
  return true;
});

// ── App startup ───────────────────────────────────────────────────────────────
app.whenReady().then(async () => {
  const cfg = loadConfig();
  if (cfg.serverUrl && cfg.token) {
    // Verify the token is still valid before loading the player
    let tokenValid = false;
    try {
      const res = await doRequest(`${cfg.serverUrl}/api/register/status/${getDeviceId()}`);
      // If server says approved and token matches, we're good
      if (res.body?.status === 'approved' && res.body?.token === cfg.token) {
        tokenValid = true;
      }
    } catch (_) {
      // Server unreachable — try loading anyway (may work from cache)
      tokenValid = true;
    }
    if (tokenValid) {
      loadPlayerUrl(cfg.serverUrl, cfg.token);
      scheduleUpdateChecks();
    } else {
      // Token is stale (display deleted/re-added) — clear and go to setup
      saveConfig({});
      loadLocalFile('setup.html');
    }
  } else if (cfg.serverUrl) {
    loadLocalFile('waiting.html');
    setTimeout(() => pollRegistration(cfg.serverUrl, getDeviceId()), POLL_INTERVAL_MS);
  } else {
    loadLocalFile('setup.html');
  }
  app.on('activate', () => {
    if (!mainWindow || mainWindow.isDestroyed()) loadLocalFile('setup.html');
  });

  // Diagnostic hotkeys (work even in production builds where DevTools menu
  // is hidden). These are global so they fire from anywhere on the desktop.
  // Ctrl+Alt+L  → open the player log file in Notepad / xdg-open
  // Ctrl+Alt+D  → open Chromium DevTools on the player window
  // Ctrl+Alt+Q  → quit the player (escape kiosk)
  try {
    globalShortcut.register('Control+Alt+L', () => {
      flog('[hotkey] opening log file');
      shell.openPath(LOG_PATH);
    });
    globalShortcut.register('Control+Alt+D', () => {
      flog('[hotkey] opening devtools');
      if (mainWindow && !mainWindow.isDestroyed()) {
        mainWindow.webContents.openDevTools({ mode: 'detach' });
      }
    });
    globalShortcut.register('Control+Alt+Q', () => {
      flog('[hotkey] quit requested');
      app.exit(0);
    });
  } catch (e) {
    flog('[hotkey] registration failed:', String(e));
  }
});

app.on('window-all-closed', () => {
  if (process.platform !== 'darwin') app.quit();
});
app.on('will-quit', () => {
  if (psBlockerId !== null) powerSaveBlocker.stop(psBlockerId);
  if (updateTimer)          clearInterval(updateTimer);
  if (_idlePollTimer)       clearInterval(_idlePollTimer);
  try { globalShortcut.unregisterAll(); } catch (_) {}
});
