'use strict';
// Signed device commands (Tier B): the pure checks. No Electron, no files, no network.
// Protocol: docs/signed-commands.md. A command is acted on only when verifyCommand() says ok,
// and then only on the returned payload (never on the unsigned fields next to it).
const crypto = require('crypto');

const ACTIONS = new Set(['reboot_device', 'set_startup', 'update_app']);
const SWITCH_OF = { reboot_device: 'reboot', set_startup: 'startup', update_app: 'update' };
const SPKI_PREFIX = Buffer.from('302a300506032b6570032100', 'hex'); // Ed25519 SubjectPublicKeyInfo header
const MAX_PAYLOAD = 4096;
const MAX_PACKAGE_BYTES = 1024 * 1024 * 1024;
const VERSION_RE = /^[0-9A-Za-z][0-9A-Za-z._+-]{0,31}$/;
const SHA_RE = /^[0-9a-f]{64}$/;
const B64_RE = /^[A-Za-z0-9+/]+={0,2}$/;

// base64 text -> Buffer of exactly `len` bytes, or null. Strict: no whitespace, no other alphabet.
function decodeB64(text, len) {
  if (typeof text !== 'string' || text.length > 200 || !B64_RE.test(text) || text.length % 4 !== 0) return null;
  const buf = Buffer.from(text, 'base64');
  return buf.length === len ? buf : null;
}

function publicKeyObject(raw) {
  return crypto.createPublicKey({ key: Buffer.concat([SPKI_PREFIX, raw]), format: 'der', type: 'spki' });
}

const isPlain = (x) => !!x && typeof x === 'object' && !Array.isArray(x);
const isInt = (x) => typeof x === 'number' && Number.isSafeInteger(x);

// Typed params per action -> {ok:true, params} (cleaned copy) | {ok:false}.
function checkParams(action, p) {
  if (!isPlain(p)) return { ok: false };
  const keys = Object.keys(p);
  if (action === 'reboot_device') return keys.length === 0 ? { ok: true, params: {} } : { ok: false };
  if (action === 'set_startup') {
    if (!keys.length || keys.some((k) => k !== 'autostart' && k !== 'mode')) return { ok: false };
    const out = {};
    if ('autostart' in p) { if (typeof p.autostart !== 'boolean') return { ok: false }; out.autostart = p.autostart; }
    if ('mode' in p) { if (p.mode !== 'app' && p.mode !== 'shell') return { ok: false }; out.mode = p.mode; }
    return { ok: true, params: out };
  }
  if (action === 'update_app') {
    if (keys.length !== 3 || !keys.every((k) => k === 'version' || k === 'sha256' || k === 'size')) return { ok: false };
    if (typeof p.version !== 'string' || !VERSION_RE.test(p.version)) return { ok: false };
    if (typeof p.sha256 !== 'string' || !SHA_RE.test(p.sha256)) return { ok: false };
    if (!isInt(p.size) || p.size <= 0 || p.size > MAX_PACKAGE_BYTES) return { ok: false };
    return { ok: true, params: { version: p.version, sha256: p.sha256, size: p.size } };
  }
  return { ok: false };
}

// c: one entry of GET /commands. ctx: {key: Buffer(32), displayId, serverTimeMs, lastSeq, allowed: {reboot, startup, update}}
// -> {ok:true, payload} | {ok:false, reason} with reason in bad_signature, wrong_display, expired, replayed,
// not_allowed, unsupported, bad_params.
function verifyCommand(c, ctx) {
  const fail = (reason) => ({ ok: false, reason });
  const s = c && c.signed;
  if (!isPlain(s) || typeof s.payload !== 'string' || typeof s.sig !== 'string') return fail('bad_signature');
  if (!Buffer.isBuffer(ctx.key) || ctx.key.length !== 32) return fail('bad_signature');
  const sig = decodeB64(s.sig, 64);
  if (!sig || s.payload.length > MAX_PAYLOAD) return fail('bad_signature');
  let good = false;
  try { good = crypto.verify(null, Buffer.from(s.payload, 'utf8'), publicKeyObject(ctx.key), sig); } catch (e) { good = false; }
  if (!good) return fail('bad_signature');

  let p;
  try { p = JSON.parse(s.payload); } catch (e) { return fail('bad_signature'); }
  if (!isPlain(p) || p.v !== 1) return fail('unsupported');
  if (!isInt(ctx.displayId) || p.display_id !== ctx.displayId) return fail('wrong_display');
  if (p.request_id !== c.id || p.action !== c.action) return fail('bad_signature'); // retargeted copy
  if (!isInt(p.exp_ms) || !isInt(ctx.serverTimeMs) || p.exp_ms <= ctx.serverTimeMs) return fail('expired');
  if (!isInt(p.seq) || p.seq <= 0 || p.seq <= (isInt(ctx.lastSeq) ? ctx.lastSeq : 0)) return fail('replayed');
  if (typeof p.action !== 'string' || !ACTIONS.has(p.action)) return fail('unsupported');
  const pc = checkParams(p.action, p.params);
  if (!pc.ok) return fail('bad_params');
  if (!ctx.allowed || ctx.allowed[SWITCH_OF[p.action]] !== true) return fail('not_allowed');
  return { ok: true, payload: Object.assign({}, p, { params: pc.params }) };
}

// ---- Key pinning (trust on first use), per server origin.
// pinned: base64 text or null; fetched: base64 text from /command-key.
// -> {state:'invalid'} | {state:'pin', key} | {state:'same', key} | {state:'changed'}
function evaluateKey(pinned, fetched) {
  const raw = decodeB64(fetched, 32);
  if (!raw) return { state: 'invalid' };
  if (!pinned) return { state: 'pin', key: raw };
  const old = decodeB64(pinned, 32);
  if (old && crypto.timingSafeEqual(old, raw)) return { state: 'same', key: old };
  return { state: 'changed' };
}

// First 16 hex characters of SHA-256 of the raw key, in groups of four (same as Admin > System).
function fingerprint(raw) {
  const h = crypto.createHash('sha256').update(raw).digest('hex').slice(0, 16);
  return h.match(/.{4}/g).join(' ');
}

// ---- Server clock high-water mark. server_time_ms is not signed, so a response whose time is lower
// than the highest one seen is untrustworthy (a real server clock does not go backwards).
const MAX_AHEAD_MS = 30 * 24 * 3600 * 1000;
function timeBackwards(mark, serverTimeMs) { return isInt(mark) && mark > 0 && isInt(serverTimeMs) && serverTimeMs < mark; }
// A value absurdly far ahead of the device clock never raises the mark (it would lock the real server out).
function raisedMark(mark, serverTimeMs, deviceNow) {
  if (!isInt(serverTimeMs) || serverTimeMs <= 0 || serverTimeMs > deviceNow + MAX_AHEAD_MS) return mark;
  return serverTimeMs > mark ? serverTimeMs : mark;
}

// ---- Cooldowns (device clock, relative only). A clock that jumped back never blocks forever.
const COOLDOWN_MS = 10 * 60 * 1000;
function cooldownActive(at, now, windowMs) {
  if (!isInt(at) || at <= 0 || !isInt(now)) return false;
  const d = now - at;
  return d >= 0 && d < (windowMs || COOLDOWN_MS);
}

// ---- Update package: what was downloaded must equal the SIGNED size and sha256.
function checkPackage(want, gotSize, gotSha) {
  if (gotSize !== want.size) return { ok: false, reason: 'size mismatch' };
  if (typeof gotSha !== 'string' || gotSha.toLowerCase() !== want.sha256) return { ok: false, reason: 'sha256 mismatch' };
  return { ok: true };
}

module.exports = { ACTIONS, SWITCH_OF, COOLDOWN_MS, MAX_PACKAGE_BYTES, decodeB64, publicKeyObject, checkParams,
  verifyCommand, evaluateKey, fingerprint, timeBackwards, raisedMark, MAX_AHEAD_MS, cooldownActive, checkPackage };
