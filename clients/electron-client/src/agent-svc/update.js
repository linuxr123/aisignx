'use strict';
// update_app, the pure rules: versions, deadlines, rollback, crash recovery, health. No files, no processes.
const HEALTH_DEADLINE_MS = 10 * 60 * 1000;     // the installed player has this long to show up healthy
const STALE_PENDING_MS = 60 * 60 * 1000;       // a pending update older than this is simply closed as failed
const PLAYER_SEEN_MAX_S = 90;                  // the player's normal heartbeat is far below this
const INSTALL_TIMEOUT_MS = 10 * 60 * 1000;
const INSTALLER_ARGS = ['/S', '/allusers'];    // constants: nothing from the server reaches a command line

// Dotted numeric versions ('3.1.0', '3.10.2'); a suffix after the numbers ('3.2.0-beta') sorts below the plain release.
function parse(v) {
  const m = /^(\d+(?:\.\d+){0,3})(?:[-+._]?(.*))?$/.exec(String(v));
  if (!m) return null;
  return { nums: m[1].split('.').map(Number), pre: m[2] || '' };
}
function compareVersions(a, b) {
  const x = parse(a), y = parse(b);
  if (!x || !y) return null;
  for (let i = 0; i < Math.max(x.nums.length, y.nums.length); i++) {
    const d = (x.nums[i] || 0) - (y.nums[i] || 0);
    if (d) return d < 0 ? -1 : 1;
  }
  if (x.pre === y.pre) return 0;
  if (!x.pre) return 1;
  if (!y.pre) return -1;
  return x.pre < y.pre ? -1 : 1;
}
// Anti-downgrade: the installer may not be older than minVersion (raised after every success).
function versionAllowed(to, minVersion) {
  const c = compareVersions(to, minVersion || '0');
  return c !== null && c >= 0;
}

const deadline = (pending) => pending.startedAt + HEALTH_DEADLINE_MS;

// What to do with a pending update found at start (after a crash or a power cut).
// -> 'close' (give up, report failed) | 'finish' (installer was running: relaunch and verify) | 'verify' (resume the check)
function recover(pending, nowMs) {
  if (!pending) return 'none';
  if (!(nowMs - pending.startedAt < STALE_PENDING_MS) || nowMs < pending.startedAt) return 'close';
  if (pending.phase === 'installing') return 'finish';
  if (pending.phase === 'verifying') return 'verify';
  return 'close';   // downloading / rolling_back: start again from the admin
}

// Health: the installed file's version is the target AND the server saw the player recently with that version.
function healthy(h) {
  return !!h && h.installed === h.to && !!h.status && h.status.player_version === h.to &&
    Number.isFinite(h.status.player_last_seen_s) && h.status.player_last_seen_s >= 0 && h.status.player_last_seen_s < PLAYER_SEEN_MAX_S;
}

// -> {action:'wait'} | {action:'ok'} | {action:'rollback'} | {action:'fail', state}
function judgePending(pending, nowMs, isHealthy, previous, rolledBack) {
  if (isHealthy) return { action: 'ok' };
  if (nowMs < deadline(pending)) return { action: 'wait' };
  if (pending.phase === 'rolling_back') return { action: 'fail', state: 'failed' };      // the rollback itself failed: stop, no loop
  if (!previous) return { action: 'fail', state: 'failed_no_rollback' };
  if ((rolledBack || []).includes(pending.id)) return { action: 'fail', state: 'failed' };
  return { action: 'rollback' };
}

module.exports = { compareVersions, versionAllowed, deadline, recover, healthy, judgePending,
  HEALTH_DEADLINE_MS, STALE_PENDING_MS, PLAYER_SEEN_MAX_S, INSTALL_TIMEOUT_MS, INSTALLER_ARGS };
