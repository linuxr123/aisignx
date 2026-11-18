'use strict';
// The privileged agent's loop: poll the pinned server for signed Tier B commands, verify each one with the PINNED
// command key, persist seq / time / ledger BEFORE acting, do the action, acknowledge. Nothing here reads from a
// user-writable place, and nothing the server sends reaches a command line (ops.js). docs/windows-agent-service.md.
const V = require('../agent/verify');
const L = require('../agent/logic');
const AC = require('./autologin');
const cfgm = require('./config');
const ecies = require('./ecies');
const { createUpdater } = require('./updater');
const U = require('./update');

const API = '/api/player/v1/agent';
const MAX_BODY = 64 * 1024;
const TIME_SAVE_STEP_MS = 60 * 1000;
const KEY_RECHECK_MS = 30 * 60 * 1000;
const DIAG_EVERY_MS = 60 * 1000;
const FOLDER_RECHECK_MS = 5 * 60 * 1000;
const fail = (result) => ({ state: 'failed', result });

// o: {cfg, paths, programDir, version, now, log, request, ops, st (loaded state), saveState(st), readKey() -> Buffer(32),
//     writeStatus(obj), runInstaller, started}
function createCore(o) {
  const cfg = o.cfg, log = o.log, now = o.now || Date.now;
  const st = o.st;
  const pinned = V.decodeB64(cfg.commandKey, 32);
  const save = () => o.saveState(st);
  let busy = false, timeWarned = false, keyChanged = false, lastKeyCheck = 0, lastDiag = 0;
  let lastFolderCheck = 0, safe = false, pollWarned = false;

  async function api(method, p, body) {
    const headers = { authorization: 'Bearer ' + cfg.token, accept: 'application/json' };
    let data = null;
    if (body !== undefined) { data = Buffer.from(JSON.stringify(body)); headers['content-type'] = 'application/json'; headers['content-length'] = String(data.length); }
    const res = await o.request({ method, path: API + p, headers, body: data ? require('stream').Readable.from([data]) : null, timeoutMs: 20000 });
    const chunks = []; let size = 0;
    for await (const c of res) { size += c.length; if (size > MAX_BODY) { res.destroy(); break; } chunks.push(c); }
    let json = null;
    try { json = JSON.parse(Buffer.concat(chunks).toString('utf8')); } catch (e) { json = null; }
    return { status: res.statusCode, data: json };
  }

  const updater = createUpdater({ cfg, paths: o.paths, get st() { return st; }, save, log, now, request: o.request, ops: o.ops,
    runInstaller: o.runInstaller, api, downloadDeadlineMs: o.downloadDeadlineMs });

  async function ack(id, state, result) {
    try {
      const r = await api('POST', '/commands/' + id + '/ack', { state, result: String(result).slice(0, 200) });
      return r.status >= 200 && r.status < 300;
    } catch (e) { log.warn('agent: ack failed', e.code || e.message); return false; }
  }

  // ---- The five actions. p = verified params.
  async function doReboot() {
    if (V.cooldownActive(st.rebootAt, now())) return fail('not_allowed (reboot cooldown)');
    const r = await o.ops.reboot();
    if (!r || !r.ok) { log.error('agent: shutdown.exe failed'); return fail('reboot failed (shutdown.exe)'); }
    st.rebootAt = now();
    try { save(); } catch (e) { log.warn('agent: reboot cooldown not saved'); }
    return { state: 'done', result: 'rebooting' };
  }
  async function doStartup(p) {
    if (!cfg.kioskSid || !cfg.playerExe) return fail('unsupported (no kiosk account fixed at install)');
    const r = await o.ops.setStartup(p);
    if (!r) return fail('could not change the startup settings');
    return { state: 'done', result: ('autostart ' + (r.autostart ? 'on' : 'off') + ', mode ' + r.mode).slice(0, 200) };
  }
  async function doAutologin(c, p) {
    // The password's ciphertext goes over this connection: never over plain http to another machine (removing auto-login is always fine).
    if (p.enabled !== false && !cfgm.isSecureServer(cfg.server)) return fail('not_allowed (the server address is not https)');
    let priv;
    try { priv = o.readKey(); } catch (e) { return fail('bad_params'); }
    const r = AC.open(p, cfg, { displayId: cfg.displayId, requestId: c.id, priv });
    if (!r.ok) return fail(r.reason);
    if (!r.enabled) {
      if (!(await o.ops.clearAutologon())) return fail('could not remove automatic sign-in');
      st.autologin = { enabled: false, user: '' };
      try { save(); } catch (e) { /* reported by the next successful save */ }
      return { state: 'done', result: 'automatic sign-in removed' };
    }
    const ok = await o.ops.setAutologon(r.user, r.domain, r.password);     // wipes r.password
    if (!ok) return fail('could not set automatic sign-in');
    st.autologin = { enabled: true, user: r.user };
    try { save(); } catch (e) { /* ignore */ }
    return { state: 'done', result: 'automatic sign-in set for ' + r.user };
  }

  // c: raw command from GET /agent/commands. -> true when it was acted on.
  async function handle(c, serverTimeMs) {
    if (!c || typeof c !== 'object' || typeof c.id !== 'string' || !L.ID_RE.test(c.id) || st.ledger.includes(c.id)) return false;
    const v = V.verifyCommand(c, { key: pinned, displayId: cfg.displayId, serverTimeMs, lastSeq: st.seq, allowed: cfg.allow });
    if (!v.ok) {                                   // nothing is burned: a forged command cannot use up a real id
      log.warn('agent:', String(c.action).slice(0, 20), 'refused:', v.reason);
      await ack(c.id, 'failed', v.reason);
      return false;
    }
    st.seq = v.payload.seq;                        // before acting: a command runs at most once
    st.timeMs = V.raisedMark(st.timeMs, serverTimeMs, now());
    st.ledger = L.ledgerAdd(st.ledger, c.id);
    try { save(); } catch (e) { log.error('agent: cannot save state, command skipped'); return false; }
    log.info('agent: running', v.payload.action, c.id, 'seq', v.payload.seq);
    const p = v.payload.params;
    let out;
    try {
      if (v.payload.action === 'reboot_device') out = await doReboot();
      else if (v.payload.action === 'set_startup') out = await doStartup(p);
      else if (v.payload.action === 'set_autologin') out = await doAutologin(c, p);
      else out = await updater.start(c, p);
    } catch (e) { log.error('agent: command failed', e.message); out = fail('failed'); }
    await ack(c.id, out.state, out.result);
    if (out.after) { try { await out.after(); } catch (e) { log.error('agent: follow-up failed', e.message); } }
    return true;
  }

  async function checkKey() {
    if (now() - lastKeyCheck < KEY_RECHECK_MS) return;
    lastKeyCheck = now();
    try {
      const r = await api('GET', '/command-key');
      if (r.status === 200 && r.data) {
        const ev = V.evaluateKey(cfg.commandKey, r.data.public_key);       // the pin is never replaced
        if (ev.state === 'changed' && !keyChanged) log.warn('agent: the server presents a different command key; signed commands are refused');
        keyChanged = ev.state === 'changed';
      }
    } catch (e) { /* the pin still protects us */ }
  }

  async function folderOk() {
    if (safe && now() - lastFolderCheck < FOLDER_RECHECK_MS) return true;
    lastFolderCheck = now();
    safe = true;
    for (const dir of [o.paths.dataDir, o.programDir]) {
      const r = await o.ops.folderCheck(dir);
      const f = cfgm.judgeFolder(r.ps.out);
      if (!f.ok || cfgm.unsafeAces(r.acl.out).length) { safe = false; log.error('agent: unsafe folder', dir, f.ok ? 'users can write' : f.reason); }
    }
    return safe;
  }

  function report() {
    const a = cfg.allow;
    const out = { version: o.version, state: 'running', kiosk_user: cfg.kioskUser, can_reboot: a.reboot, can_update: a.update,
      can_set_startup: a.startup && !!cfg.kioskSid, can_autologin: a.autologin && !!cfg.kioskSid, uptime_s: Math.floor((now() - (o.started || now())) / 1000),
      command_key_fingerprint: V.fingerprint(pinned) };
    if (keyChanged) out.command_key_changed = true;
    if (st.lastUpdate) out.last_update = st.lastUpdate;
    if (st.autologin) out.autologin = st.autologin;
    out.key_id = ecies.keyId(ecies.publicOf(o.readKey()));
    return out;
  }
  async function sendDiag(force) {
    if (!force && now() - lastDiag < DIAG_EVERY_MS) return;
    lastDiag = now();
    try {
      const r = report();
      const v = await o.ops.installedVersion();
      if (v) r.installed_version = v;
      await api('POST', '/diagnostics', { diag: r });
    } catch (e) { /* next time */ }
  }

  // One poll. -> number of commands seen (-1: not allowed to act).
  async function pollOnce() {
    if (busy) return 0;
    busy = true;
    try {
      if (!(await folderOk())) { o.writeStatus({ state: 'unsafe_acl', t: now(), version: o.version }); return -1; }
      o.writeStatus({ state: 'running', t: now(), version: o.version });
      await checkKey();
      await updater.tick();
      const r = await api('GET', '/commands');
      if (r.status !== 200 || !r.data || !Array.isArray(r.data.commands)) {
        if (!pollWarned) { log.warn('agent: command poll answered', r.status); pollWarned = true; }
        return 0;
      }
      pollWarned = false;
      const t = r.data.server_time_ms;
      if (V.timeBackwards(st.timeMs, t)) {
        if (!timeWarned) log.warn('agent: the server clock went backwards; signed commands are refused');
        timeWarned = true;
        return 0;
      }
      timeWarned = false;
      const mark = V.raisedMark(st.timeMs, t, now());
      if (mark - st.timeMs >= TIME_SAVE_STEP_MS) { st.timeMs = mark; try { save(); } catch (e) { /* kept in memory */ } } else if (mark > st.timeMs) st.timeMs = mark;
      const list = r.data.commands.slice(0, 10);
      for (const c of list) await handle(c, t);
      await sendDiag(false);
      return list.length;
    } catch (e) {
      if (!pollWarned) { log.warn('agent: poll failed', e.code || e.message); pollWarned = true; }
      return 0;
    } finally { busy = false; }
  }

  return { pollOnce, handle, report, sendDiag, checkSafe: folderOk, recover: () => updater.recover(), updater, get state() { return st; } };
}

module.exports = { createCore, API, U };
