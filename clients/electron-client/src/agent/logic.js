'use strict';
// Pure rules of the device agent: command validation, at-most-once ledger, log redaction,
// the gzip size cap and the watchdog decision (resources/watchdog.ps1 mirrors decide()).
const zlib = require('zlib');

const ID_RE = /^[0-9a-f]{16}$/;
const VERBS = new Set(['restart_player', 'collect_logs']);
const SWITCH_OF = { restart_player: 'restart', collect_logs: 'logs' };

// Watchdog constants (the .ps1 uses the same numbers).
const STALE_MS = 90 * 1000;     // heartbeat older than this: the player is hung
const GRACE_MS = 150 * 1000;    // a process without a matching heartbeat gets this long to start
const MAX_PER_HOUR = 6;
const HOUR_MS = 60 * 60 * 1000;

const LOG_CAP_BYTES = 900 * 1024; // target; the server accepts 1 MB

// One entry of GET /commands -> {ok:true, id, action} | {ok:false, id?, reason}.
// Without a valid id the command cannot be answered, so it is only ignored (no id).
function validateCommand(c) {
  if (!c || typeof c !== 'object' || Array.isArray(c)) return { ok: false, reason: 'invalid' };
  if (typeof c.id !== 'string' || !ID_RE.test(c.id)) return { ok: false, reason: 'invalid' };
  if (typeof c.action !== 'string' || !VERBS.has(c.action)) return { ok: false, id: c.id, reason: 'unsupported' };
  const p = c.params;
  if (p !== undefined && p !== null && !(typeof p === 'object' && !Array.isArray(p) && Object.keys(p).length === 0)) {
    return { ok: false, id: c.id, reason: 'unsupported' };
  }
  if (c.expires_in_s !== undefined && !(typeof c.expires_in_s === 'number' && c.expires_in_s > 0)) {
    return { ok: false, id: c.id, reason: 'expired' };
  }
  return { ok: true, id: c.id, action: c.action };
}

// -> true when the admin's switch for this verb is on. settings = {watchdog, restart, logs}.
function allowed(action, settings) {
  return !!(settings && settings[SWITCH_OF[action]] === true);
}

// Ledger of handled ids (newest last, bounded). Persist BEFORE acting.
function ledgerAdd(ids, id, max) {
  const out = ids.filter((x) => x !== id);
  out.push(id);
  return out.slice(-(max || 200));
}
function ledgerSanitize(raw) {
  return Array.isArray(raw) ? raw.filter((x) => typeof x === 'string' && ID_RE.test(x)).slice(-200) : [];
}

// ---- Redaction.
const PATTERNS = [
  [/-----BEGIN [A-Z ]+-----[\s\S]*?(?:-----END [A-Z ]+-----|$)/g, '[redacted-pem]'],
  [/\bBearer\s+[A-Za-z0-9._~+\/=-]+/gi, 'Bearer [redacted]'],
  [/\b(aisignx_display|session|cookie|set-cookie)\s*[=:]\s*[^;\s]+/gi, '$1=[redacted]'],
  [/\b((?:[0-9A-F]{2}:){31}[0-9A-F]{2})\b/gi, '[redacted-fingerprint]'],
  [/"?\b(token|secret|password|passwd|pin|hash|salt|authorization|api[_-]?key|caPem)\b"?\s*[:=]\s*"?[^\s",;}]+"?/gi, '$1=[redacted]'],
  [/\b[A-Za-z0-9_-]{40,}\b/g, '[redacted-long-string]'],
];

// text: string. extra: literal secrets to remove wherever they appear (token, PIN hash, salt, pin).
function redact(text, extra) {
  let out = String(text);
  for (const s of extra || []) {
    if (typeof s === 'string' && s.length >= 6) out = out.split(s).join('[redacted]');
  }
  for (const [re, rep] of PATTERNS) out = out.replace(re, rep);
  return out;
}

// The literal secrets of this run, taken from the config and the token.
function secretsFrom(cfg, token) {
  const out = [];
  if (token) out.push(token);
  if (cfg && cfg.unlock) out.push(cfg.unlock.hash, cfg.unlock.salt);
  if (cfg && cfg.certPin) out.push(cfg.certPin.sha256);
  return out.filter((s) => typeof s === 'string' && s);
}

// Newest part of `text` whose gzip is at most `cap` bytes -> {gz, truncated}.
function gzipNewest(text, cap) {
  const limit = cap || LOG_CAP_BYTES;
  let buf = Buffer.from(text, 'utf8');
  let truncated = false;
  for (let i = 0; i < 12; i++) {
    const gz = zlib.gzipSync(buf, { level: 9 });
    if (gz.length <= limit) return { gz, truncated };
    truncated = true;
    let keep = Math.max(1024, Math.floor(buf.length * (limit / gz.length) * 0.9));
    if (keep >= buf.length) keep = Math.floor(buf.length * 0.8);
    let tail = buf.subarray(buf.length - keep);
    const nl = tail.indexOf(10);
    if (nl >= 0 && nl < tail.length - 1) tail = tail.subarray(nl + 1);
    buf = Buffer.concat([Buffer.from('... older lines left out ...\n'), tail]);
  }
  return { gz: zlib.gzipSync(Buffer.from('log too large to send\n')), truncated: true };
}

// ---- Watchdog decision.
// in: {enabled, stopped, procs: [{pid, startMs}], hb: {t, pid}|null, now, restarts: [ms], gaveUp}
// -> {action: 'none'|'start'|'kill_start'|'gave_up', reason, healthy, gaveUp, restarts}
function decide(i) {
  const now = i.now;
  const restarts = (i.restarts || []).filter((t) => now - t < HOUR_MS);
  const base = { restarts, gaveUp: !!i.gaveUp };
  if (!i.enabled) return Object.assign(base, { action: 'none', reason: 'disabled' });
  if (i.stopped) return Object.assign(base, { action: 'none', reason: 'stopped by user' });
  const procs = i.procs || [];
  let need = null;
  if (!procs.length) {
    need = 'not running';
  } else {
    const match = i.hb && procs.some((p) => p.pid === i.hb.pid);
    if (match) {
      if (now - i.hb.t > STALE_MS) need = 'heartbeat stale';
    } else {
      const newest = Math.max.apply(null, procs.map((p) => p.startMs));
      if (now - newest > GRACE_MS) need = 'no heartbeat';
    }
  }
  if (!need) {
    // Seen healthy again (for example the user started it): the limit is lifted.
    return Object.assign(base, { action: 'none', reason: 'healthy', healthy: true, gaveUp: false });
  }
  if (i.gaveUp || restarts.length >= MAX_PER_HOUR) {
    return Object.assign(base, { action: 'gave_up', reason: need, gaveUp: true });
  }
  restarts.push(now);
  return Object.assign(base, { action: procs.length ? 'kill_start' : 'start', reason: need, restarts });
}

// What stats().agent reports from the state file {restarts: [ms], gave_up}.
function summarize(state, now) {
  const r = ((state && state.restarts) || []).filter((t) => typeof t === 'number');
  return {
    restarts_1h: r.filter((t) => now - t < HOUR_MS).length,
    gave_up: !!(state && state.gave_up),
    last_restart_ms: r.length ? Math.max.apply(null, r) : 0,
  };
}

module.exports = {
  ID_RE, VERBS, STALE_MS, GRACE_MS, MAX_PER_HOUR, HOUR_MS, LOG_CAP_BYTES,
  validateCommand, allowed, ledgerAdd, ledgerSanitize, redact, secretsFrom, gzipNewest, decide, summarize,
};
