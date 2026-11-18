'use strict';
// The agent's log file (logs\agent.log in the protected folder): one line per event, redacted, rotated at 1 MB.
// Never given a password, token or key: callers pass short fixed messages, and L.redact is a second line.
const fs = require('fs');
const path = require('path');
const L = require('../agent/logic');

const MAX_BYTES = 1024 * 1024;

// secrets: () => [literal strings to remove]. -> {info, warn, error}
function createLog(file, secrets, clock) {
  const now = clock || Date.now;
  function write(level, parts) {
    try {
      const text = L.redact(parts.map((p) => (p instanceof Error ? p.message : String(p))).join(' '), secrets ? secrets() : []);
      fs.mkdirSync(path.dirname(file), { recursive: true });
      try { if (fs.statSync(file).size > MAX_BYTES) fs.renameSync(file, file + '.1'); } catch (e) { /* no file yet */ }
      fs.appendFileSync(file, new Date(now()).toISOString() + ' ' + level + ' ' + text.slice(0, 500) + '\n');
    } catch (e) { /* logging must never stop the agent */ }
  }
  return { info: (...a) => write('INFO', a), warn: (...a) => write('WARN', a), error: (...a) => write('ERROR', a) };
}

module.exports = { createLog, MAX_BYTES };
