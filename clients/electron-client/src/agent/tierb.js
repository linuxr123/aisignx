'use strict';
// Tier B device commands (reboot_device, set_startup, update_app): the signed ones.
// Design: docs/signed-commands.md. Nothing here is reachable from the page. The agent hands in a
// command and gets back what to acknowledge ({state, result, after}); `after` runs once the
// acknowledgement went out (reboot and install start only then).
const fs = require('fs');
const path = require('path');
const V = require('./verify');
const store = require('./store');
const pkg = require('./package');
const sys = require('./system');

const API = '/api/player/v1';
const KEY_RECHECK_MS = 30 * 60 * 1000;
const TIME_SAVE_STEP_MS = 60 * 1000;          // the server-time mark is written when it moved this much (or at a command)
const DOWNLOAD_DEADLINE_MS = 15 * 60 * 1000;
const INSTALLER_ARGS = ['/S', '--updated', '--force-run'];
const fail = (result) => ({ state: 'failed', result });

// o: {dataDir, agentDir, now, log, platform, isPackaged, exe, exeArgs, launcherSource, getUpstream, getToken,
//     call(method, path) -> {status, data}, startup: {get: () => bool, set: (bool) => void}, quit: () => void,
//     system (test double for ./system), markUpdating() (a marker the shell launcher waits on),
//     spawnInstaller(file, args) -> Promise: resolves once the installer started and stayed up, rejects otherwise,
//     downloadDeadlineMs}
function createTierB(o) {
  const log = o.log || { info() {}, warn() {}, error() {} };
  const now = o.now || Date.now;
  const platform = o.platform || process.platform;
  const S = o.system || sys;
  const stateFile = path.join(o.dataDir, 'agent-state.json');
  const updatesDir = path.join(o.dataDir, 'updates');
  let st = store.load(stateFile);
  let keyChanged = false, lastKeyCheck = 0;
  let shellMode = 'app';
  let lastUpdateError = null, timeWarned = false;

  const supported = () => platform === 'win32' && !!o.isPackaged;
  const shellCapable = () => supported() && !!o.launcherSource && fs.existsSync(o.launcherSource);
  const saveState = () => store.save(stateFile, st);

  // ---- Trust on first use.
  // -> {key: Buffer} | {error}. Pins the first key per server; a different key later is refused.
  async function resolveKey(origin) {
    let r = null;
    try { r = await o.call('GET', '/command-key'); } catch (e) { r = null; }
    const pinned = st.servers[origin];
    if (r && r.status === 200 && r.data) {
      lastKeyCheck = now();
      const ev = V.evaluateKey(pinned && pinned.key, r.data.public_key);
      if (ev.state === 'changed') {
        if (!keyChanged) log.warn('agent: the server presents a different command key; signed commands are refused');
        keyChanged = true;
        return { error: 'bad_signature (server key changed)' };
      }
      if (ev.state === 'pin') {
        st.servers[origin] = { key: Buffer.from(ev.key).toString('base64'), seq: 0, timeMs: 0 };
        try { saveState(); } catch (e) { delete st.servers[origin]; return { error: 'bad_signature (cannot save key)' }; }
        log.info('agent: pinned the server command key');
      }
      if (ev.state !== 'invalid') { keyChanged = false; return { key: V.decodeB64(st.servers[origin].key, 32) }; }
    }
    // The key could not be fetched (or was malformed): the pinned one still protects us.
    if (pinned) return { key: V.decodeB64(pinned.key, 32) };
    return { error: 'bad_signature (no server key)' };
  }

  // The highest server_time_ms seen per server; a lower one is not believed. -> false when it went backwards.
  function observeTime(origin, t) {
    const e = st.servers[origin];
    if (!e) return true;
    if (V.timeBackwards(e.timeMs, t)) {
      if (!timeWarned) log.warn('agent: the server clock went backwards; signed commands are refused');
      timeWarned = true;
      return false;
    }
    timeWarned = false;
    const m = V.raisedMark(e.timeMs, t, now());
    if (m !== e.timeMs) {
      const big = m - e.timeMs >= TIME_SAVE_STEP_MS;
      e.timeMs = m;
      if (big) { try { saveState(); } catch (err) { /* kept in memory; the next save retries */ } }
    }
    return true;
  }

  // Pins early and notices a changed key without waiting for a command. Cheap: every 30 minutes.
  async function checkKey() {
    const up = o.getUpstream();
    if (!up || !supported() || now() - lastKeyCheck < KEY_RECHECK_MS) return;
    lastKeyCheck = now();
    await resolveKey(up.origin);
  }

  // ---- Startup state (autostart is the app's own login item; mode is read from the registry).
  async function refreshStartup() {
    if (!shellCapable()) { shellMode = 'app'; return; }
    try { shellMode = S.isOurShell(await S.readShell()) ? 'shell' : 'app'; } catch (e) { /* keep the last value */ }
  }
  function startupInfo() {
    let autostart = false;
    try { autostart = !!(o.startup && o.startup.get()); } catch (e) { autostart = false; }
    return { autostart, mode: shellMode, shell_capable: shellCapable() };
  }

  // What stats().agent adds.
  function report() {
    const can = supported();
    const out = { can_reboot: can, can_set_startup: can, can_update: can, startup: startupInfo() };
    if (keyChanged) out.command_key_changed = true;
    const up = o.getUpstream && o.getUpstream();
    const e = up && st.servers[up.origin];
    const raw = e && V.decodeB64(e.key, 32);
    if (raw) out.command_key_fingerprint = V.fingerprint(raw);   // compare with Admin > System
    if (lastUpdateError) out.last_update_error = lastUpdateError;
    return out;
  }

  // ---- The three actions. `p` is the verified payload.params.
  // shutdown.exe has a 30 s delay, so it is started BEFORE the acknowledgement: a failure (for example a
  // shutdown already pending) is then reported honestly instead of "rebooting". The cooldown counts only
  // when the restart really was scheduled.
  async function doReboot() {
    if (!supported()) return fail('unsupported');
    if (V.cooldownActive(st.rebootAt, now())) return fail('not_allowed (reboot cooldown)');
    let r;
    try { r = await S.reboot(); } catch (e) { r = { ok: false }; }
    if (!r || !r.ok) { log.error('agent: shutdown.exe failed'); return fail('reboot failed (shutdown.exe)'); }
    st.rebootAt = now();
    try { saveState(); } catch (e) { log.warn('agent: reboot cooldown not saved'); }
    return { state: 'done', result: 'rebooting' };
  }

  async function doStartup(p) {
    if (!supported()) return fail('unsupported');
    if (p.mode === 'shell' && !shellCapable()) return fail('unsupported (shell mode)');
    if (p.mode === 'shell') {
      const ok = await S.setShellMode({ source: o.launcherSource, agentDir: o.agentDir, exe: o.exe, args: o.exeArgs || [] });
      if (!ok) return fail('could not set the shell');
    } else if (p.mode === 'app') {
      const ok = await S.clearShellMode();
      if (!ok) return fail('could not clear the shell');
    }
    if ('autostart' in p) {
      try { o.startup.set(p.autostart); } catch (e) { return fail('could not change autostart'); }
    }
    await refreshStartup();
    const i = startupInfo();
    return { state: 'done', result: ('autostart ' + (i.autostart ? 'on' : 'off') + ', mode ' + i.mode).slice(0, 200) };
  }

  async function doUpdate(c, p) {
    if (!supported()) return fail('unsupported');
    if (V.cooldownActive(st.updateAt, now())) return fail('not_allowed (update cooldown)');
    const up = o.getUpstream(), token = o.getToken();
    if (!up || !token) return fail('no connection');
    lastUpdateError = null;
    // A fresh random folder per attempt: nobody can plant a file or link at a predictable path.
    let dir;
    try { fs.mkdirSync(updatesDir, { recursive: true }); dir = fs.mkdtempSync(path.join(updatesDir, 'u-')); } catch (e) { return fail('cannot create download folder'); }
    const discard = () => { try { fs.rmSync(dir, { recursive: true, force: true }); } catch (e) { /* cleaned at the next start */ } };
    const file = path.join(dir, 'AiSignX-Player-Setup-' + p.version + '.exe');
    let res;
    try {
      res = await up.request({ method: 'GET', path: API + '/commands/' + c.id + '/package',
        headers: { authorization: 'Bearer ' + token }, timeoutMs: 30000 });
    } catch (e) { discard(); return fail('download failed'); }
    if (res.statusCode !== 200) { res.resume(); discard(); return fail('download refused (' + res.statusCode + ')'); }
    if (typeof res.setTimeout === 'function') res.setTimeout(60000, () => res.destroy());
    const got = await pkg.savePackage(res, file, { size: p.size, sha256: p.sha256 }, res.headers && res.headers['content-length'],
      { deadlineMs: o.downloadDeadlineMs || DOWNLOAD_DEADLINE_MS });
    if (!got.ok) { log.warn('agent: update package refused:', got.reason); discard(); return fail('package ' + got.reason); }
    return { state: 'done', result: 'installing ' + p.version, after: async () => {
      // The file is hashed again right before it runs (it sat on disk during the acknowledgement).
      const again = await pkg.verifyFile(file, { size: p.size, sha256: p.sha256 });
      if (!again.ok) { lastUpdateError = 'package changed before install (' + again.reason + ')'; log.error('agent:', lastUpdateError); discard(); return; }
      try { await o.spawnInstaller(file, INSTALLER_ARGS); } catch (e) {
        // Not started (blocked, quarantined) or exited at once: the player simply keeps running.
        lastUpdateError = ('installer did not start: ' + (e && (e.code || e.message))).slice(0, 120);
        log.error('agent:', lastUpdateError);
        return;
      }
      st.updateAt = now();
      try { saveState(); } catch (e) { log.warn('agent: update cooldown not saved'); }
      log.info('agent: installer started for', p.version);
      try { if (o.markUpdating) o.markUpdating(); } catch (e) { /* the launcher then uses its normal rules */ }
      try { o.quit(); } catch (e) { /* ignore */ }
    } };
  }

  // c: the raw command (id already validated). ctx: {serverTimeMs, displayId, settings}.
  // -> {state, result, after?} to acknowledge, or null when nothing can be said (no connection).
  async function run(c, ctx) {
    // Automatic sign-in is only ever done by the privileged agent (docs/windows-agent-service.md).
    if (c && c.action === 'set_autologin') return fail('unsupported (needs the privileged agent)');
    const up = o.getUpstream();
    if (!up) return null;
    const k = await resolveKey(up.origin);
    if (k.error) return fail(k.error);
    const pinned = st.servers[up.origin];
    if (!observeTime(up.origin, ctx.serverTimeMs)) return fail('bad_time (server clock went backwards)');
    const v = V.verifyCommand(c, { key: k.key, displayId: ctx.displayId, serverTimeMs: ctx.serverTimeMs,
      lastSeq: pinned.seq, allowed: ctx.settings });
    if (!v.ok) {
      log.warn('agent:', c.action, 'refused:', v.reason);
      return fail(v.reason === 'replayed' ? 'replayed (seq not above the stored one; if the server was reset, use Forget server on this device)' : v.reason);
    }
    pinned.seq = v.payload.seq;            // before acting: a command runs at most once
    try { saveState(); } catch (e) { log.error('agent: cannot save state, command skipped'); return fail('not_allowed (state not saved)'); }
    log.info('agent: running', v.payload.action, c.id, 'seq', v.payload.seq);
    const p = v.payload.params;
    if (v.payload.action === 'reboot_device') return doReboot();
    if (v.payload.action === 'set_startup') return doStartup(p);
    return doUpdate(c, p);
  }

  function forgetKeys() {
    st.servers = Object.create(null);   // pin, seq and server-time mark go together
    timeWarned = false;
    keyChanged = false;
    try { saveState(); } catch (e) { /* the next save retries */ }
  }

  function cleanup() { return pkg.cleanupUpdates(updatesDir); }

  return { run, report, observeTime: (t) => { const up = o.getUpstream(); return up ? observeTime(up.origin, t) : true; }, checkKey, refreshStartup, forgetKeys, cleanup, get state() { return st; } };
}

module.exports = { createTierB, KEY_RECHECK_MS, INSTALLER_ARGS };
