'use strict';
// The agent's state file (state.json in the protected data folder), written atomically.
//   seq / timeMs   highest accepted seq and highest server_time_ms seen (one pinned server, so one pair)
//   ledger         ids of commands acted on (at most once)
//   minVersion     the lowest player version an update may install (raised after each success)
//   rebootAt / updateAt  cooldowns (device clock)
//   pending        the update in flight: {id, from, to, sha256, size, startedAt, phase}
//   previous       the last verified installer the agent installed: {version, sha256, size}
//   rolledBack     command ids already rolled back (one rollback per id)
//   lastUpdate     {id, state, to}
//   autologin      {enabled, user, dpl}  what the agent set (for the page and for undo)
// Never holds a token, a key or a password.
const task = require('../agent/task');
const L = require('../agent/logic');
const V = require('../agent/verify');

const PHASES = new Set(['downloading', 'installing', 'verifying', 'rolling_back']);
const isInt = (x) => Number.isSafeInteger(x) && x >= 0;

function sanitize(raw) {
  const r = raw && typeof raw === 'object' ? raw : {};
  const out = { seq: isInt(r.seq) ? r.seq : 0, timeMs: isInt(r.timeMs) ? r.timeMs : 0, ledger: L.ledgerSanitize(r.ledger),
    minVersion: typeof r.minVersion === 'string' && /^[0-9][0-9.]{0,30}$/.test(r.minVersion) ? r.minVersion : '0',
    rebootAt: isInt(r.rebootAt) ? r.rebootAt : 0, updateAt: isInt(r.updateAt) ? r.updateAt : 0,
    pending: null, previous: null, rolledBack: [], lastUpdate: null, autologin: null };
  const p = r.pending;
  if (p && typeof p === 'object' && L.ID_RE.test(String(p.id)) && PHASES.has(p.phase) && typeof p.to === 'string' && V.checkParams('update_app', { version: p.to, sha256: p.sha256, size: p.size }).ok) {
    out.pending = { id: p.id, from: typeof p.from === 'string' ? p.from.slice(0, 32) : '', to: p.to, sha256: p.sha256, size: p.size,
      startedAt: isInt(p.startedAt) ? p.startedAt : 0, phase: p.phase };
  }
  const q = r.previous;
  if (q && typeof q === 'object' && V.checkParams('update_app', { version: q.version, sha256: q.sha256, size: q.size }).ok) {
    out.previous = { version: q.version, sha256: q.sha256, size: q.size };
  }
  if (Array.isArray(r.rolledBack)) out.rolledBack = r.rolledBack.filter((x) => L.ID_RE.test(String(x))).slice(-20);
  const u = r.lastUpdate;
  if (u && typeof u === 'object' && L.ID_RE.test(String(u.id)) && typeof u.state === 'string') {
    out.lastUpdate = { id: u.id, state: u.state.slice(0, 24), to: typeof u.to === 'string' ? u.to.slice(0, 20) : '' };
  }
  const a = r.autologin;
  if (a && typeof a === 'object') out.autologin = { enabled: a.enabled === true, user: typeof a.user === 'string' ? a.user.slice(0, 64) : '' };
  return out;
}

const load = (file) => sanitize(task.readJson(file));
const save = (file, st) => task.writeJsonAtomic(file, sanitize(st));   // throws: the caller then does not act

module.exports = { sanitize, load, save, PHASES };
