'use strict';
// update_app on the privileged agent: download to the protected staging folder, check size and sha256 against the
// SIGNED values, check the Authenticode signature (pinned thumbprint), hash again right before running, run the
// installer, then watch the player come back; roll back once to the last verified installer if it does not.
// Effects are injected (ctx), the rules are in update.js. docs/windows-agent-service.md section (e).
const fs = require('fs');
const path = require('path');
const V = require('../agent/verify');
const pkg = require('../agent/package');
const U = require('./update');

const API = '/api/player/v1/agent';
const DOWNLOAD_DEADLINE_MS = 15 * 60 * 1000;
const fail = (result) => ({ state: 'failed', result });

// ctx: {cfg, paths, st (live state object), save(), log, now, request, ops, runInstaller, api(method, path), downloadDeadlineMs}
function createUpdater(ctx) {
  const st = () => ctx.st;
  let installing = false;     // the installer of THIS process is running: tick() must not judge yet
  const prevFile = () => path.join(ctx.paths.previous, 'installer.exe');
  const rm = (f) => { try { fs.rmSync(f, { force: true }); } catch (e) { /* retried at the next start */ } };
  const persist = () => { try { ctx.save(); return true; } catch (e) { ctx.log.error('agent: cannot save state'); return false; } };

  // The launcher's 'updating' marker (shell mode); never allowed to break an update.
  const mark = async () => { try { if (ctx.ops.markUpdating) await ctx.ops.markUpdating(); } catch (e) { /* ignore */ } };
  const unmark = () => { try { if (ctx.ops.clearUpdating) Promise.resolve(ctx.ops.clearUpdating()).catch(() => {}); } catch (e) { /* ignore */ } };

  function close(state) {
    unmark();
    const p = st().pending;
    if (p) st().lastUpdate = { id: p.id, state, to: p.to };
    st().pending = null;
    persist();
    ctx.log.info('agent: update closed as', state);
  }

  // c: the verified command, p: its verified params. -> {state, result, after?} (the caller acknowledges).
  async function start(c, p) {
    if (st().pending) return fail('not_allowed (an update is already running)');
    if (!U.versionAllowed(p.version, st().minVersion)) return fail('not_allowed (older than the installed version)');
    if (V.cooldownActive(st().updateAt, ctx.now())) return fail('not_allowed (update cooldown)');
    // The floor is also the version that is installed NOW: the very first update may not install an older build either.
    const from = await ctx.ops.installedVersion();
    if (from && U.compareVersions(p.version, from) === -1) return fail('not_allowed (older than the installed version)');
    const want = { size: p.size, sha256: p.sha256 };
    const part = path.join(ctx.paths.staging, c.id + '.exe.part');
    const file = path.join(ctx.paths.staging, c.id + '.exe');
    try { fs.mkdirSync(ctx.paths.staging, { recursive: true }); for (const f of fs.readdirSync(ctx.paths.staging)) rm(path.join(ctx.paths.staging, f)); } catch (e) { return fail('cannot prepare the staging folder'); }
    st().pending = { id: c.id, from, to: p.version, sha256: p.sha256, size: p.size, startedAt: ctx.now(), phase: 'downloading' };
    if (!persist()) { st().pending = null; return fail('not_allowed (state not saved)'); }
    const abort = (result) => { rm(part); rm(file); st().pending = null; persist(); return fail(result); };

    let res;
    try {
      res = await ctx.request({ method: 'GET', path: API + '/commands/' + c.id + '/package', headers: { authorization: 'Bearer ' + ctx.cfg.token }, timeoutMs: 30000 });
    } catch (e) { return abort('download failed'); }
    if (res.statusCode !== 200) { res.resume(); return abort('download refused (' + res.statusCode + ')'); }
    if (typeof res.setTimeout === 'function') res.setTimeout(60000, () => res.destroy());
    const got = await pkg.savePackage(res, part, want, res.headers && res.headers['content-length'], { deadlineMs: ctx.downloadDeadlineMs || DOWNLOAD_DEADLINE_MS });
    if (!got.ok) return abort('package ' + got.reason);
    try { fs.renameSync(part, file); } catch (e) { return abort('cannot stage the installer'); }
    const sig = await ctx.ops.verifySignature(file, ctx.now());
    if (!sig.ok) { ctx.log.warn('agent: installer refused:', sig.reason); return abort(sig.reason); }
    const again = await pkg.verifyFile(file, want);
    if (!again.ok) return abort('package ' + again.reason);
    return { state: 'done', result: 'installing ' + p.version, after: () => install(file, want) };
  }

  async function install(file, want) {
    const pend = st().pending;
    if (!pend) return;
    const again = await pkg.verifyFile(file, want);            // it sat on disk during the acknowledgement
    if (!again.ok) { rm(file); close('failed'); return; }
    pend.phase = 'installing';
    if (!persist()) { close('failed'); return; }
    installing = true;
    try {
      await mark();
      await ctx.ops.killPlayer();
      const r = await ctx.runInstaller(file, U.INSTALLER_ARGS, U.INSTALL_TIMEOUT_MS);
      if (!r.ok) { ctx.log.error('agent: installer failed:', r.reason); close('installer_failed'); await ctx.ops.launchPlayer(); return; }
      st().updateAt = ctx.now();
      pend.phase = 'verifying';
      pend.startedAt = ctx.now();
      persist();
      await ctx.ops.launchPlayer();
    } finally { installing = false; }
  }

  function promote(pend) {
    const staged = path.join(ctx.paths.staging, pend.id + '.exe');
    try {
      fs.mkdirSync(ctx.paths.previous, { recursive: true });
      fs.copyFileSync(staged, prevFile() + '.tmp');
      fs.renameSync(prevFile() + '.tmp', prevFile());
      st().previous = { version: pend.to, sha256: pend.sha256, size: pend.size };
    } catch (e) { st().previous = null; rm(prevFile() + '.tmp'); }
    rm(staged);
    if (U.compareVersions(pend.to, st().minVersion) === 1) st().minVersion = pend.to;
  }

  async function rollback(pend) {
    const prev = st().previous;
    st().rolledBack = st().rolledBack.concat([pend.id]).slice(-20);     // once per command id, saved before acting
    const orig = { id: pend.id, to: pend.to };
    st().pending = { id: pend.id, from: pend.to, to: prev.version, sha256: prev.sha256, size: prev.size, startedAt: ctx.now(), phase: 'rolling_back' };
    if (!persist()) { st().pending = null; return; }
    const want = { size: prev.size, sha256: prev.sha256 };
    const ok = (await pkg.verifyFile(prevFile(), want)).ok && (await ctx.ops.verifySignature(prevFile(), ctx.now())).ok;
    if (!ok) { st().lastUpdate = { id: orig.id, state: 'failed', to: orig.to }; st().pending = null; persist(); return; }
    installing = true;
    try {
      await mark();
      await ctx.ops.killPlayer();
      const r = await ctx.runInstaller(prevFile(), U.INSTALLER_ARGS, U.INSTALL_TIMEOUT_MS);
      if (!r.ok) { ctx.log.error('agent: rollback installer failed:', r.reason); st().lastUpdate = { id: orig.id, state: 'failed', to: orig.to }; st().pending = null; persist(); }
      await ctx.ops.launchPlayer();
    } finally { installing = false; }
  }

  // Called every poll. Judges a pending update that is past the installer.
  async function tick() {
    const pend = st().pending;
    if (!pend || installing || pend.phase === 'downloading' || pend.phase === 'installing') return;
    const installed = await ctx.ops.installedVersion();
    let status = null;
    try { const r = await ctx.api('GET', '/status'); status = r && r.status === 200 ? r.data : null; } catch (e) { status = null; }
    const healthy = U.healthy({ installed, to: pend.to, status });
    const j = U.judgePending(pend, ctx.now(), healthy, st().previous, st().rolledBack);
    if (j.action === 'wait') return;
    if (j.action === 'ok') {
      if (pend.phase === 'rolling_back') { close('rolled_back'); return; }
      promote(pend);
      close('ok');
      return;
    }
    if (j.action === 'fail') { close(j.state); await ctx.ops.launchPlayer(); return; }
    await rollback(pend);
  }

  // At start, after a crash or a power cut.
  async function recover() {
    const pend = st().pending;
    const act = U.recover(pend, ctx.now());
    if (act === 'close') { close('failed'); await ctx.ops.launchPlayer(); return act; }
    if (act === 'finish') {
      pend.phase = 'verifying';
      pend.startedAt = ctx.now();
      persist();
      await ctx.ops.launchPlayer();
    }
    if (act === 'none') {
      try { for (const f of fs.readdirSync(ctx.paths.staging)) rm(path.join(ctx.paths.staging, f)); } catch (e) { /* none yet */ }
    }
    return act;
  }

  return { start, tick, recover };
}

module.exports = { createUpdater };
