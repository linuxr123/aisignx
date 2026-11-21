'use strict';
// Entry of the privileged agent. Run by the Electron binary in node mode (ELECTRON_RUN_AS_NODE=1) from a SYSTEM
// scheduled task:  aisignx-agent.exe app\src\agent-svc\main.js
//   (no argument)          the agent loop
//   --init                 make the X25519 key pair; print the public key
//   --enroll <params.json> enroll with the one-time code (the install script's job)
//   --prepare <params.json> install WITHOUT a code: keep the settings, ask the server for approval when the agent starts
//   --version
// Reads configuration only from the protected data folder. No port, pipe or file drop is ever opened for input.
const fs = require('fs');
const os = require('os');
const path = require('path');
const task = require('../agent/task');
const store = require('./state');
const cfgm = require('./config');
const { createCore } = require('./core');
const { createOps } = require('./ops');
const { runInstaller } = require('./installer');
const { makeRequester } = require('./http');
const { createLog } = require('./log');
const lock = require('./lock');
const enrollment = require('./enrollment');
const approval = require('./approval');

const POLL_MS = 20000;
const MAX_ERRORS = 10;
const version = require('../../package.json').version;

const dataDirOf = (env) => path.win32.join(env.ProgramData || 'C:\\ProgramData', 'AiSignXAgent');

async function main(argv, env) {
  const paths = cfgm.dataPaths(dataDirOf(env));
  const programDir = path.dirname(process.execPath);
  const log = createLog(path.join(paths.logs, 'agent.log'));
  if (argv[0] === '--version') { process.stdout.write(version + '\n'); return 0; }
  if (argv[0] === '--init') { process.stdout.write(enrollment.init(paths) + '\n'); return 0; }
  if (argv[0] === '--enroll') {
    const params = task.readJson(argv[1]);
    const r = await enrollment.enroll(params, paths, params && cfgm.SERVER_RE.test(String(params.server)) ? makeRequester(params.server) : null, { host: os.hostname(), version });
    try { fs.rmSync(argv[1], { force: true }); } catch (e) { /* the install script removes the folder too */ }
    process.stdout.write(JSON.stringify(r.ok ? { ok: true, displayId: r.cfg.displayId } : { ok: false, reason: r.reason }) + '\n');
    return r.ok ? 0 : 4;
  }

  if (argv[0] === '--prepare') {
    const params = task.readJson(argv[1]);
    const r = enrollment.prepare(params, paths);
    try { fs.rmSync(argv[1], { force: true }); } catch (e) { /* the install script removes the folder too */ }
    process.stdout.write(JSON.stringify(r.ok ? { ok: true } : { ok: false, reason: r.reason }) + '\n');
    return r.ok ? 0 : 4;
  }

  const bad = cfgm.envProblems(env);
  if (bad.length) { log.error('agent: refusing to start, environment has', bad.join(', ')); return 2; }
  let cfg = cfgm.sanitize(task.readJson(paths.config));
  if (!cfg && approval.readPending(paths)) cfg = await waitForApproval(paths, log, env);   // installed, not yet approved by the server
  if (!cfg) { log.error('agent: not configured (agent.json missing or invalid)'); return 3; }
  const stamp = Date.now();
  if (!lock.acquire(paths.lock, process.pid, stamp)) return 0;          // another copy is running: harmless

  // Everything the agent starts (installers, PowerShell, the C# compile) uses a temp folder only SYSTEM and Administrators can
  // write, never C:\Windows\Temp (where ordinary users can plant files).
  const tmpDir = path.win32.join(paths.dataDir, 'tmp');
  try { fs.mkdirSync(tmpDir, { recursive: true }); process.env.TEMP = tmpDir; process.env.TMP = tmpDir; } catch (e) { log.error('agent: cannot make the private temp folder'); return 3; }
  const st = store.load(paths.state);
  const ops = createOps({ psDir: path.join(programDir, 'ps'), programDir, cfg, paths });
  const request = makeRequester(cfg.server);
  const core = createCore({ cfg, paths, programDir, version, log, request, ops, st, started: stamp, runInstaller,
    saveState: (s) => store.save(paths.state, s), readKey: () => enrollment.readKey(paths),
    writeStatus: (o) => { try { task.writeJsonAtomic(paths.status, o); } catch (e) { /* the player just shows nothing */ } } });
  fs.mkdirSync(paths.publicDir, { recursive: true });
  log.info('agent: started', version);
  if (await core.checkSafe()) { const r = await ops.importCert(); if (!r.ok) log.warn('agent: certificate import failed'); await core.recover(); await core.sendDiag(true); }

  let errors = 0;
  await new Promise((resolve) => {
    const tick = async () => {
      lock.refresh(paths.lock, process.pid, Date.now());
      try { await core.pollOnce(); errors = 0; } catch (e) { errors++; log.error('agent: internal error', e && e.message); if (errors >= MAX_ERRORS) resolve(); }
    };
    setInterval(tick, POLL_MS);
    tick();
  });
  lock.release(paths.lock, process.pid);
  return 1;       // non-zero: Task Scheduler starts a clean copy
}

// An agent that was installed without an enrollment code: ask the server for approval until it comes (or the admin rejects it).
async function waitForApproval(paths, log, env) {
  const pending = approval.readPending(paths);
  if (!pending) return null;
  const pubKey = enrollment.init(paths);
  const request = makeRequester(pending.server);
  fs.mkdirSync(paths.publicDir, { recursive: true });
  let last = '';
  for (;;) {
    const r = await approval.step({ paths, request, now: Date.now(), host: os.hostname(), version, readKey: () => enrollment.readKey(paths), pubKey });
    const line = r.state + (r.reason ? ': ' + r.reason : '') + (r.claimed ? ' (claimed by its player)' : '');
    if (line !== last) { log.info('agent: approval', line); last = line; }
    try { task.writeJsonAtomic(paths.status, { state: 'waiting', t: Date.now(), version }); } catch (e) { /* the player shows nothing */ }
    if (r.state === 'enrolled') { log.info('agent: approved; server key', r.fingerprint); return r.cfg; }
    if (r.state === 'rejected') { log.error('agent: the admin rejected this agent'); return null; }
    await new Promise((resolve) => setTimeout(resolve, POLL_MS));
  }
}

process.on('unhandledRejection', (e) => { try { createLog(path.join(dataDirOf(process.env), 'logs', 'agent.log')).error('agent: unhandled rejection', e && e.message); } catch (x) { /* ignore */ } });
if (require.main === module) {
  main(process.argv.slice(2), process.env).then((code) => process.exit(code), () => process.exit(1));
}

module.exports = { main, dataDirOf };
