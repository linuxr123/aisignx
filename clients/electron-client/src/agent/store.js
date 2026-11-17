'use strict';
// The agent's small state file (userData/agent-state.json), written atomically:
//   servers  {origin: {key: base64 of the pinned 32-byte command key, seq: highest accepted seq, timeMs: highest server_time_ms seen}}
//   rebootAt / updateAt  device time of the last reboot / update (10-minute cooldowns)
// Never holds a token or password.
const task = require('./task');
const V = require('./verify');

const MAX_SERVERS = 8;

function sanitize(raw) {
  const out = { servers: Object.create(null), rebootAt: 0, updateAt: 0 };
  if (!raw || typeof raw !== 'object') return out;
  const s = raw.servers && typeof raw.servers === 'object' ? raw.servers : {};
  for (const origin of Object.keys(s).slice(0, MAX_SERVERS)) {
    const e = s[origin];
    if (e && V.decodeB64(e.key, 32)) {
      out.servers[origin] = { key: e.key, seq: Number.isSafeInteger(e.seq) && e.seq > 0 ? e.seq : 0,
        timeMs: Number.isSafeInteger(e.timeMs) && e.timeMs > 0 ? e.timeMs : 0 };
    }
  }
  for (const k of ['rebootAt', 'updateAt']) if (Number.isSafeInteger(raw[k]) && raw[k] > 0) out[k] = raw[k];
  return out;
}

function load(file) { return sanitize(task.readJson(file)); }
function save(file, st) { task.writeJsonAtomic(file, sanitize(st)); } // throws: the caller then does not act

module.exports = { sanitize, load, save, MAX_SERVERS };
