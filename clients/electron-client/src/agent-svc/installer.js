'use strict';
// Runs an installer and waits for it: fixed program, fixed arguments, no shell. Always listens for 'error'.
// spawnFn is injectable for tests. -> {ok, code, reason}
const { spawn } = require('child_process');

function runInstaller(file, args, timeoutMs, spawnFn) {
  return new Promise((resolve) => {
    let child, done = false;
    const end = (r) => { if (done) return; done = true; clearTimeout(timer); resolve(r); };
    const timer = setTimeout(() => { try { child.kill(); } catch (e) { /* ignore */ } end({ ok: false, code: null, reason: 'installer timed out' }); }, timeoutMs);
    try { child = (spawnFn || spawn)(file, args, { stdio: 'ignore', windowsHide: true }); } catch (e) { return end({ ok: false, code: null, reason: 'installer did not start' }); }
    child.once('error', (e) => end({ ok: false, code: null, reason: 'installer did not start (' + (e && e.code || 'error') + ')' }));
    child.once('exit', (code) => end(code === 0 ? { ok: true, code } : { ok: false, code, reason: 'installer exited with ' + code }));
  });
}

module.exports = { runInstaller };
