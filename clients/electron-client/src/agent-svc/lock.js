'use strict';
// Single-instance lock: agent.lock in the protected data folder holds {pid, t}. The running agent refreshes it
// every poll; a lock older than STALE_MS belongs to a dead process (a reused pid cannot keep it alive).
const task = require('../agent/task');

const STALE_MS = 90 * 1000;

// -> true when this process now owns the lock.
function acquire(file, pid, nowMs) {
  const cur = task.readJson(file);
  if (cur && Number.isSafeInteger(cur.pid) && cur.pid !== pid && Number.isFinite(cur.t) && nowMs - cur.t >= 0 && nowMs - cur.t < STALE_MS) return false;
  task.writeJsonAtomic(file, { pid, t: nowMs });
  return true;
}
function refresh(file, pid, nowMs) { try { task.writeJsonAtomic(file, { pid, t: nowMs }); } catch (e) { /* the next poll retries */ } }
function release(file, pid) {
  const cur = task.readJson(file);
  if (cur && cur.pid === pid) { try { require('fs').rmSync(file, { force: true }); } catch (e) { /* ignore */ } }
}

module.exports = { acquire, refresh, release, STALE_MS };
