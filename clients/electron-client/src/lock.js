'use strict';
// "Lock player": when the server sets kiosk_lock, the player window cannot be closed or
// minimized and pulls itself back to the front. An admin can lift the lock for a few minutes
// from the display page (an `unlock` SSE command the page forwards here); a `lock` command
// ends that early. The remote unlock lives in memory only, on a monotonic clock, so a
// restart locks again. Plain Node, no Electron.

const UNLOCK_MIN = 1;
const UNLOCK_MAX = 240;
const SEEN_MAX = 20;
const RID_RE = /^[0-9a-f]{16}$/;

function monoNow() {
  return Number(process.hrtime.bigint() / 1000000n);
}

function clampMinutes(m) {
  return Math.min(UNLOCK_MAX, Math.max(UNLOCK_MIN, m));
}

// The JSON the page forwards -> {action: 'unlock', minutes, rid} | {action: 'lock', rid} | null.
function parseCommand(json) {
  if (typeof json !== 'string' || json.length >= 1024) return null;
  let c;
  try { c = JSON.parse(json); } catch (e) { return null; }
  if (!c || typeof c !== 'object' || Array.isArray(c)) return null;
  const rid = c.request_id;
  if (typeof rid !== 'string' || !RID_RE.test(rid)) return null;
  if (c.action === 'unlock') {
    if (!Number.isInteger(c.minutes)) return null;
    return { action: 'unlock', minutes: clampMinutes(c.minutes), rid };
  }
  if (c.action === 'lock') return { action: 'lock', rid };
  return null;
}

// opts: {now, setTimer, clearTimer}, injectable for tests.
function createLock(opts) {
  const o = opts || {};
  const now = o.now || monoNow;
  const setTimer = o.setTimer || setTimeout;
  const clearTimer = o.clearTimer || clearTimeout;
  let kioskLock = false;
  let unlockedUntil = 0;
  let timer = null;
  const seen = [];
  const listeners = [];
  let last = 'off';

  function state() {
    if (!kioskLock) return 'off';
    return unlockedUntil > now() ? 'unlocked' : 'locked';
  }

  function emit() {
    const s = state();
    if (s === last) return;
    last = s;
    for (const cb of listeners) {
      try { cb(s); } catch (e) { /* a listener must not break the lock */ }
    }
  }

  function disarm() {
    if (timer !== null) { clearTimer(timer); timer = null; }
  }

  // true when the request id is new (and remembers it).
  function fresh(rid) {
    if (!rid) return true;
    if (seen.includes(rid)) return false;
    seen.push(rid);
    if (seen.length > SEEN_MAX) seen.shift();
    return true;
  }

  function configure(cfg) {
    kioskLock = !!(cfg && cfg.kioskLock);
    emit();
  }

  function unlockFor(minutes, rid) {
    if (!Number.isInteger(minutes) || !fresh(rid)) return false;
    const ms = clampMinutes(minutes) * 60000;
    unlockedUntil = now() + ms;
    disarm();
    timer = setTimer(() => { timer = null; emit(); }, ms + 50);
    if (timer && typeof timer.unref === 'function') timer.unref();
    emit();
    return true;
  }

  function relock(rid) {
    if (!fresh(rid)) return false;
    unlockedUntil = 0;
    disarm();
    emit();
    return true;
  }

  return {
    configure,
    isLocked: () => state() === 'locked',
    state,
    unlockFor,
    relock,
    onChange: (cb) => { if (typeof cb === 'function') listeners.push(cb); },
  };
}

module.exports = { createLock, parseCommand, clampMinutes, UNLOCK_MIN, UNLOCK_MAX };
