'use strict';
// Unlock PIN for the Ctrl+Alt shortcuts. Plain Node, no Electron.
// The server sends only a salted PBKDF2-SHA256 hash in the manifest `settings.unlock`; the PIN
// itself never reaches this app. Wrong PINs lock the prompt with a doubling wait that is kept in
// userData/unlock-state.json, so restarting the app does not reset it.
const fs = require('fs');
const path = require('path');
const crypto = require('crypto');
const { promisify } = require('util');

const pbkdf2 = promisify(crypto.pbkdf2);
const ALG = 'pbkdf2-sha256';
const ITER_MIN = 10000;
const ITER_MAX = 1000000;
const SALT_RE = /^[0-9a-f]{32}$/;
const HASH_RE = /^[0-9a-f]{64}$/;
const PIN_RE = /^\d{1,12}$/;
const STATE_FILE = 'unlock-state.json';
const LOCK_AFTER = 5;               // failures before the first wait
const FIRST_WAIT_MS = 60 * 1000;
const MAX_WAIT_MS = 15 * 60 * 1000;

// -> a clean {alg, iter, salt, hash}, or null for anything else. Never throws, so a bad value
// from the server or in config.json is dropped instead of breaking the config.
function sanitizeUnlock(u) {
  if (!u || typeof u !== 'object' || Array.isArray(u)) return null;
  if (u.alg !== ALG) return null;
  if (!Number.isInteger(u.iter) || u.iter < ITER_MIN || u.iter > ITER_MAX) return null;
  if (typeof u.salt !== 'string' || !SALT_RE.test(u.salt)) return null;
  if (typeof u.hash !== 'string' || !HASH_RE.test(u.hash)) return null;
  return { alg: ALG, iter: u.iter, salt: u.salt, hash: u.hash };
}

// -> Promise<boolean>. Does not enforce ITER_MIN, so the shared low-iteration test vector works.
async function verifyPin(pin, u) {
  if (typeof pin !== 'string' || !PIN_RE.test(pin)) return false;
  if (!u || !Number.isInteger(u.iter) || u.iter < 1 || u.iter > ITER_MAX) return false;
  if (typeof u.salt !== 'string' || !SALT_RE.test(u.salt)) return false;
  if (typeof u.hash !== 'string' || !HASH_RE.test(u.hash)) return false;
  const got = await pbkdf2(pin, Buffer.from(u.salt, 'hex'), u.iter, 32, 'sha256');
  return crypto.timingSafeEqual(got, Buffer.from(u.hash, 'hex'));
}

// Wait after `failures` wrong PINs: none for the first four, then 60 s doubling to 15 min.
function waitMs(failures) {
  if (!(failures >= LOCK_AFTER)) return 0;
  const steps = Math.min(failures - LOCK_AFTER, 10);
  return Math.min(MAX_WAIT_MS, FIRST_WAIT_MS * 2 ** steps);
}

// opts: {dataDir, now?: () => ms}
function createGate(opts) {
  const file = path.join(opts.dataDir, STATE_FILE);
  const now = opts.now || Date.now;
  let state = load();
  let busy = false;

  function load() {
    try {
      const raw = JSON.parse(fs.readFileSync(file, 'utf8'));
      const failures = Number.isInteger(raw.failures) && raw.failures >= 0 ? Math.min(raw.failures, 1000) : 0;
      const lockedUntil = Number.isFinite(raw.lockedUntil) && raw.lockedUntil > 0 ? raw.lockedUntil : 0;
      return { failures, lockedUntil };
    } catch (e) {
      return { failures: 0, lockedUntil: 0 };
    }
  }
  function save() {
    // Required here: config.js requires this module at load time.
    try { require('./config').writeAtomic(file, JSON.stringify(state) + '\n'); } catch (e) { /* keep in memory */ }
  }
  // Capped, so a clock that jumped backwards cannot lock the prompt for longer than the maximum.
  function remaining() {
    return Math.max(0, Math.min(state.lockedUntil - now(), MAX_WAIT_MS));
  }

  function status() {
    return { waitMs: remaining() };
  }

  // -> Promise<{ok, waitMs}>. While locked or while another check runs, the PIN is not tried.
  async function attempt(pin, u) {
    const wait = remaining();
    if (wait > 0 || busy) return { ok: false, waitMs: wait };
    busy = true;
    let ok = false;
    try { ok = await verifyPin(pin, u); } catch (e) { ok = false; } finally { busy = false; }
    if (ok) {
      reset();
      return { ok: true, waitMs: 0 };
    }
    state.failures += 1;
    const w = waitMs(state.failures);
    state.lockedUntil = w ? now() + w : 0;
    save();
    return { ok: false, waitMs: w };
  }

  function reset() {
    state = { failures: 0, lockedUntil: 0 };
    save();
  }

  return { status, attempt, reset, get failures() { return state.failures; } };
}

module.exports = { sanitizeUnlock, verifyPin, waitMs, createGate, LOCK_AFTER, FIRST_WAIT_MS, MAX_WAIT_MS };
