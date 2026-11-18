'use strict';
// Is the privileged agent (SYSTEM) in charge of the Tier B commands on this PC? The agent writes
// %ProgramData%\AiSignXAgent\public\status.json (users can read it, only SYSTEM and Administrators can write it).
// The in-app Tier B code stands down while that file is fresh and says 'running' (an agent that reports
// unsafe_acl does nothing, so the in-app code must not stand down for it). A user who forges the file can only switch the
// in-app code OFF, which is the safe direction. Output only: the agent never reads this file.
const path = require('path');
const task = require('./task');

const FRESH_MS = 15 * 60 * 1000;

const statusFile = (env) => path.win32.join((env && env.ProgramData) || 'C:\\ProgramData', 'AiSignXAgent', 'public', 'status.json');

// raw: parsed status.json. -> true when it says the agent is alive and recent.
function judge(raw, nowMs) {
  if (!raw || typeof raw !== 'object' || raw.state !== 'running') return false;
  return Number.isFinite(raw.t) && nowMs - raw.t >= 0 && nowMs - raw.t < FRESH_MS;
}

const isActive = (file, nowMs) => judge(task.readJson(file), nowMs);

module.exports = { judge, isActive, statusFile, FRESH_MS };
