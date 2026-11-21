'use strict';
// A freshly installed privileged agent publishes a claim id (%ProgramData%\AiSignXAgent\public\enroll-request.json: readable by
// users, written only by SYSTEM). The player, signed in as its display, tells the server "that agent is on my PC"; an admin
// (or the box ticked when pairing) then approves it, and the agent collects its own credential. The player never sees the
// credential and cannot give the agent anything: this is only the link between a PC's agent and a paired display.
// Plain Node: the rules are tested in test/agent-claim.test.js.
const path = require('path');
const task = require('./agent/task');

const ID_RE = /^[A-Za-z0-9_-]{22,64}$/;
const MAX_AGE_MS = 25 * 3600 * 1000;

const requestFile = (env) => path.win32.join((env && env.ProgramData) || 'C:\\ProgramData','AiSignXAgent', 'public', 'enroll-request.json');

// raw: parsed file -> the claim id, or null (nothing to do, or not acceptable).
function claimIdOf(raw, nowMs) {
  if (!raw || typeof raw !== 'object' || typeof raw.claim_id !== 'string' || !ID_RE.test(raw.claim_id)) return null;
  if (!Number.isFinite(raw.t) || nowMs - raw.t > MAX_AGE_MS || raw.t - nowMs > 3600 * 1000) return null;
  return raw.claim_id;
}

// o: {file, now(), getToken(), post(urlPath, obj) -> {status}, log}. One call = one look at the file.
function createClaimer(o) {
  return {
    async tick() {
      const id = claimIdOf(task.readJson(o.file), o.now());
      if (!id || !o.getToken()) return 'idle';
      let r;
      try { r = await o.post('/agent/claim', { claim_id: id }); } catch (e) { return 'offline'; }
      if (!r) return 'offline';
      if (r.status === 200) return 'claimed';
      if (r.status === 404 || r.status === 409) o.log.warn('agent: the server does not accept this claim (' + r.status + ')');
      return 'refused';
    },
  };
}

module.exports = { createClaimer, claimIdOf, requestFile, ID_RE };
