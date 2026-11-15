'use strict';
// Device agent (main process): remote restart and log collection, plus the watchdog's heartbeat
// file and Scheduled Task. Design: docs/device-agent-design.md. No Electron imports here, so
// it can be tested; main.js passes in what it needs.
//
// Commands come from the app's own pinned connection (never through the page), only two verbs
// exist, each runs at most once (the id is saved before acting) and only while the admin's
// switch for it is on. The display token is read from the session cookie by main.js, kept in
// memory only and never logged.
const fs = require('fs');
const path = require('path');
const { Readable } = require('stream');
const L = require('./agent/logic');
const task = require('./agent/task');

const POLL_MS = 20000;
const HEARTBEAT_MIN_MS = 8000;
const MAX_BODY = 64 * 1024;
const API = '/api/player/v1';

// o: {dataDir, version, getUpstream: () => upstream|null (upstream.request), getToken: () => string,
//     getConfig: () => cfg, readLogs: () => string, relaunch: () => void, log, isPackaged, exe,
//     exeArgs, scriptSource, offlinePage: () => bool, agentDir, taskName, now, pollMs}
function createAgent(o) {
  const log = o.log || { info() {}, warn() {}, error() {} };
  const now = o.now || Date.now;
  const dir = o.agentDir || task.agentDir();
  const ledgerFile = path.join(o.dataDir, 'agent-handled.json');
  const settings = { watchdog: false, restart: false, logs: false };
  let ledger = L.ledgerSanitize(task.readJson(ledgerFile) && task.readJson(ledgerFile).ids);
  let timer = null, hbTimer = null, busy = false, lastBeat = 0, lastState = 'unknown';
  let registered = null; // null: not tried; true / false: result of the last attempt
  let wanted = null;     // last watchdog switch acted on
  let pollWarned = false;

  function ensureDir() { try { fs.mkdirSync(dir, { recursive: true }); } catch (e) { /* reported by the writer */ } }

  // ---- Heartbeat file: only from main, only after proof the page (or the offline page) lives.
  function writeHeartbeat(state) {
    try {
      ensureDir();
      task.writeJsonAtomic(path.join(dir, 'heartbeat.json'), { t: now(), pid: process.pid, state });
      lastBeat = now();
    } catch (e) { /* the watchdog then sees a stale file: acceptable */ }
  }
  function onAlive(json) {
    if (typeof json !== 'string' || json.length > 2048) return;
    let m;
    try { m = JSON.parse(json); } catch (e) { return; }
    if (!m || typeof m !== 'object') return;
    lastState = typeof m.state === 'string' ? m.state.replace(/[^A-Z_]/gi, '').slice(0, 24) : 'unknown';
    if (now() - lastBeat >= HEARTBEAT_MIN_MS) writeHeartbeat(lastState);
  }

  // ---- Manifest switches.
  function onManifest(json) {
    let a;
    try { a = JSON.parse(json).settings; a = a && a.agent; } catch (e) { return; }
    if (!a || typeof a !== 'object') return;
    settings.restart = a.restart === true;
    settings.logs = a.logs === true;
    const wd = a.watchdog === true;
    if (wd !== wanted) { wanted = wd; syncWatchdog(); }
  }

  async function syncWatchdog() {
    const want = wanted;
    if (want && !o.isPackaged) { log.info('agent: watchdog skipped (not an installed app)'); return; }
    const r = want
      ? await task.register({ dir, scriptSource: o.scriptSource, exe: o.exe, args: o.exeArgs || [], taskName: o.taskName })
      : await task.unregister({ dir, taskName: o.taskName });
    if (want === wanted) { settings.watchdog = want; registered = want ? r.ok : false; }
    (r.ok ? log.info : log.warn)('agent: watchdog', want ? 'on' : 'off', r.message);
  }

  async function disableWatchdog() { wanted = false; await syncWatchdog(); }

  // ---- User quit marker.
  const stoppedFile = () => path.join(dir, 'stopped');
  function markStopped() { try { ensureDir(); fs.writeFileSync(stoppedFile(), String(now())); } catch (e) { /* ignore */ } }
  function clearStopped() { try { fs.rmSync(stoppedFile(), { force: true }); } catch (e) { /* ignore */ } }

  // ---- HTTP through the app's trusted upstream.
  async function call(method, urlPath, opts) {
    const up = o.getUpstream();
    const token = o.getToken();
    if (!up || !token) return null;
    const headers = { authorization: 'Bearer ' + token, accept: 'application/json' };
    let body = null;
    if (opts && opts.body) {
      headers['content-type'] = opts.type;
      headers['content-length'] = String(opts.body.length);
      body = Readable.from([opts.body]);
    }
    const res = await up.request({ method, path: API + urlPath, headers, body, timeoutMs: 20000 });
    let size = 0;
    const chunks = [];
    for await (const c of res) {
      size += c.length;
      if (size > MAX_BODY) { res.destroy(); break; }
      chunks.push(c);
    }
    let data = null;
    try { data = JSON.parse(Buffer.concat(chunks).toString('utf8')); } catch (e) { data = null; }
    return { status: res.statusCode, data };
  }

  async function ack(id, state, result) {
    try {
      const r = await call('POST', '/commands/' + id + '/ack', {
        type: 'application/json', body: Buffer.from(JSON.stringify({ state, result: String(result).slice(0, 200) })) });
      return !!r && r.status >= 200 && r.status < 300;
    } catch (e) {
      log.warn('agent: ack failed', e.code || e.message);
      return false;
    }
  }

  // ---- Commands.
  function saveLedger(id) {
    ledger = L.ledgerAdd(ledger, id);
    task.writeJsonAtomic(ledgerFile, { ids: ledger }); // throws: then the command is NOT run
  }

  function buildLog() {
    const cfg = o.getConfig ? o.getConfig() : null;
    const head = 'AiSignX Player ' + o.version + ' log collected ' + new Date(now()).toISOString() + '\n' +
      'agent: ' + JSON.stringify(stats()) + '\n\n';
    return L.redact(head + o.readLogs(), L.secretsFrom(cfg, o.getToken()));
  }

  async function runCommand(c) {
    const v = L.validateCommand(c);
    if (!v.ok) {
      if (v.id && !ledger.includes(v.id)) {
        try { saveLedger(v.id); } catch (e) { return; }
        await ack(v.id, 'failed', v.reason);
      }
      return;
    }
    if (ledger.includes(v.id)) return;                    // at most once
    try { saveLedger(v.id); } catch (e) { log.error('agent: cannot save the ledger, command skipped'); return; }
    if (!L.allowed(v.action, settings)) {
      log.info('agent:', v.action, 'refused (switched off)');
      await ack(v.id, 'failed', 'switched off');
      return;
    }
    log.info('agent: running', v.action, v.id);
    if (v.action === 'restart_player') {
      await ack(v.id, 'done', 'restarting');
      setTimeout(() => { try { o.relaunch(); } catch (e) { log.error('agent: relaunch failed', e.message); } }, 500);
      return;
    }
    // collect_logs: the upload itself closes the command on the server.
    try {
      const { gz, truncated } = L.gzipNewest(buildLog());
      const r = await call('POST', '/commands/' + v.id + '/logs', { type: 'application/gzip', body: gz });
      if (r && r.status >= 200 && r.status < 300) {
        log.info('agent: logs uploaded', gz.length, 'bytes', truncated ? '(truncated)' : '');
      } else {
        log.warn('agent: log upload refused', r ? r.status : 'no connection');
        await ack(v.id, 'failed', 'upload failed' + (r ? ' (' + r.status + ')' : ''));
      }
    } catch (e) {
      log.warn('agent: log upload failed', e.code || e.message);
      await ack(v.id, 'failed', 'upload failed');
    }
  }

  // One poll. -> number of commands seen.
  async function pollOnce() {
    if (busy || !(settings.restart || settings.logs)) return 0;
    busy = true;
    try {
      const r = await call('GET', '/commands');
      if (!r) return 0;
      if (r.status !== 200 || !r.data || !Array.isArray(r.data.commands)) {
        if (!pollWarned) { log.warn('agent: command poll answered', r.status); pollWarned = true; }
        return 0;
      }
      pollWarned = false;
      const list = r.data.commands.slice(0, 10);
      for (const c of list) await runCommand(c);
      return list.length;
    } catch (e) {
      if (!pollWarned) { log.warn('agent: command poll failed', e.code || e.message); pollWarned = true; }
      return 0;
    } finally {
      busy = false;
    }
  }

  // ---- Report for stats().agent.
  function stats() {
    const st = L.summarize(task.readJson(path.join(dir, 'state.json')), now());
    return Object.assign({
      version: o.version, tier: 'plain', can_restart: true, can_collect_logs: true,
      can_reboot: false, can_update: false, watchdog: settings.watchdog && registered === true,
    }, st);
  }

  function start() {
    clearStopped();
    timer = setInterval(() => { pollOnce(); }, o.pollMs || POLL_MS);
    // The offline page has no page script: main vouches for it while it is shown.
    hbTimer = setInterval(() => {
      if (o.offlinePage && o.offlinePage() && now() - lastBeat >= HEARTBEAT_MIN_MS) writeHeartbeat('offline');
    }, 10000);
    if (timer.unref) { timer.unref(); hbTimer.unref(); }
  }
  function stop() { clearInterval(timer); clearInterval(hbTimer); }

  return { start, stop, onAlive, onManifest, pollOnce, stats, markStopped, clearStopped, disableWatchdog,
    settings, dir, get ledger() { return ledger.slice(); } };
}

module.exports = { createAgent, POLL_MS };
