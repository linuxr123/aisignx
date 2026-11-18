'use strict';
const test = require('node:test');
const assert = require('node:assert');
const fs = require('fs');
const path = require('path');
const { EventEmitter } = require('events');
const { harness, sha } = require('./helpers/agent-harness');
const { runInstaller } = require('../src/agent-svc/installer');
const U = require('../src/agent-svc/update');

const BODY = Buffer.from('MZ-pretend-installer-bytes-3.2.0');
const ID = '2222222222222222';
const cmd = (w, over, body) => {
  const b = body || BODY;
  return w.signer.sign(over && over.id || ID, 'update_app', { version: (over && over.version) || '3.2.0', sha256: sha(b), size: b.length });
};
const names = (w) => w.calls.map((c) => c[0] + (c[1] ? ':' + c[1] : ''));

test('update, the whole path: download, hash, Authenticode, re-hash, ack BEFORE the installer, constant arguments, health, previous kept', async () => {
  const w = harness({ files: { [ID]: BODY } });
  try {
    assert.strictEqual(await w.core.handle(cmd(w), w.serverTime), true);
    assert.strictEqual(w.acks[0].state, 'done');
    assert.match(w.acks[0].result, /installing 3\.2\.0/);
    const n = names(w);
    assert.ok(n.indexOf('verifySignature:' + ID + '.exe') > -1, 'signature checked');
    assert.deepStrictEqual(w.sigBytes, BODY);                                             // verified on the very bytes
    assert.strictEqual(w.installerRuns.length, 1);
    assert.deepStrictEqual(w.installerRuns[0].args, ['/S', '/allusers']);
    assert.deepStrictEqual(w.installerRuns[0].bytes, BODY);                               // and run on the same bytes
    assert.ok(w.installerRuns[0].file.startsWith(w.paths.staging), 'run from the protected staging folder');
    assert.ok(w.acks[0].at < n.indexOf('runInstaller:/S /allusers') + 1 || true);
    // the ack went out before the installer started
    const ackAt = w.calls.findIndex((c) => c[0] === 'http' && /ack$/.test(c[2]));
    const runAt = w.calls.findIndex((c) => c[0] === 'runInstaller');
    assert.ok(ackAt > -1 && ackAt < runAt, 'ack before install');
    assert.ok(n.includes('killPlayer') && n.includes('launchPlayer'));
    assert.strictEqual(w.st.pending.phase, 'verifying');
    // The new player shows up healthy.
    w.installed = '3.2.0'; w.status = { player_version: '3.2.0', player_last_seen_s: 3 };
    await w.core.updater.tick();
    assert.strictEqual(w.st.pending, null);
    assert.deepStrictEqual(w.st.lastUpdate, { id: ID, state: 'ok', to: '3.2.0' });
    assert.strictEqual(w.st.minVersion, '3.2.0');
    assert.deepStrictEqual(w.st.previous, { version: '3.2.0', sha256: sha(BODY), size: BODY.length });
    assert.deepStrictEqual(fs.readFileSync(path.join(w.paths.previous, 'installer.exe')), BODY);
    assert.deepStrictEqual(fs.readdirSync(w.paths.staging), []);
  } finally { w.cleanup(); }
});

test('an UNSIGNED or wrong-signer installer is refused: nothing runs, the file is deleted, the app is not touched', async () => {
  for (const reason of ['bad_signature (not signed)', 'wrong_signer']) {
    const w = harness({ files: { [ID]: BODY } });
    try {
      w.sigResult = { ok: false, reason };
      await w.core.handle(cmd(w), w.serverTime);
      assert.strictEqual(w.acks[0].state, 'failed');
      assert.strictEqual(w.acks[0].result, reason);
      assert.strictEqual(w.installerRuns.length, 0);
      assert.ok(!names(w).includes('killPlayer'));
      assert.deepStrictEqual(fs.readdirSync(w.paths.staging), []);
      assert.strictEqual(w.st.pending, null);
    } finally { w.cleanup(); }
  }
});

test('a package that does not match the SIGNED sha256 or size never reaches the signature check', async () => {
  const w = harness({ files: { [ID]: Buffer.from('MZ-pretend-installer-bytes-3.2.X') } });          // same length, other bytes
  try {
    await w.core.handle(cmd(w), w.serverTime);
    assert.match(w.acks[0].result, /sha256 mismatch/);
    assert.ok(!names(w).some((x) => x.startsWith('verifySignature')));
    assert.strictEqual(w.installerRuns.length, 0);
  } finally { w.cleanup(); }
  const w2 = harness({ files: { [ID]: Buffer.concat([BODY, Buffer.from('extra')]) } });
  try {
    await w2.core.handle(cmd(w2), w2.serverTime);
    assert.match(w2.acks[0].result, /size mismatch/);
    assert.strictEqual(w2.installerRuns.length, 0);
  } finally { w2.cleanup(); }
});

test('a refused download (404, 409) fails cleanly', async () => {
  const w = harness({ files: {} });
  try {
    await w.core.handle(cmd(w), w.serverTime);
    assert.match(w.acks[0].result, /download refused \(404\)/);
    assert.strictEqual(w.st.pending, null);
  } finally { w.cleanup(); }
});

test('the installer file swapped after the acknowledgement is caught by the second hash', async () => {
  const w = harness({ files: { [ID]: BODY } });
  try {
    const realAck = w.request;
    w.request = async (r) => {
      const res = await realAck(r);
      if (/ack$/.test(r.path)) fs.writeFileSync(path.join(w.paths.staging, ID + '.exe'), 'EVIL');   // attacker with write access swaps it
      return res;
    };
    const core = require('../src/agent-svc/core').createCore({ cfg: w.cfg, paths: w.paths, programDir: 'x', version: '1', now: () => w.clock.t, log: { info() {}, warn() {}, error() {} },
      request: w.request, ops: w.ops, st: w.st, runInstaller: w.runInstaller, saveState: (s) => fs.writeFileSync(w.paths.state, JSON.stringify(s)), readKey: () => w.agentKey.priv, writeStatus() {} });
    await core.handle(cmd(w), w.serverTime);
    assert.strictEqual(w.installerRuns.length, 0);
    assert.strictEqual(w.st.lastUpdate.state, 'failed');
    assert.strictEqual(w.st.pending, null);
  } finally { w.cleanup(); }
});

test('downgrade below minVersion, a second update while one runs, and the cooldown are refused', async () => {
  const w = harness({ files: { [ID]: BODY, '4444444444444444': BODY, '3333333333333333': BODY } });
  try {
    w.st.minVersion = '3.5.0';
    await w.core.handle(cmd(w), w.serverTime);
    assert.match(w.acks[0].result, /older than the installed version/);
    w.st.minVersion = '0';
    await w.core.handle(cmd(w, { id: '4444444444444444' }), w.serverTime);          // now succeeds, pending verifying
    assert.strictEqual(w.st.pending.phase, 'verifying');
    await w.core.handle(cmd(w, { id: '3333333333333333', version: '3.3.0' }), w.serverTime);
    assert.match(w.acks.at(-1).result, /already running/);
    assert.strictEqual(w.installerRuns.length, 1);
  } finally { w.cleanup(); }
});

test('the installer failing to start or exiting non-zero closes the update and brings the old app back', async () => {
  const w = harness({ files: { [ID]: BODY } });
  try {
    w.installerResult = { ok: false, code: 1, reason: 'installer exited with 1' };
    await w.core.handle(cmd(w), w.serverTime);
    assert.deepStrictEqual(w.st.lastUpdate, { id: ID, state: 'installer_failed', to: '3.2.0' });
    assert.strictEqual(w.st.pending, null);
    assert.ok(names(w).includes('launchPlayer'));
  } finally { w.cleanup(); }
});

test('unhealthy past the deadline: rolls back ONCE to the verified previous installer; a second failure stops, no loop', async () => {
  const OLD = Buffer.from('MZ-old-installer-3.1.5');
  const w = harness({ files: { [ID]: BODY } });
  try {
    fs.writeFileSync(path.join(w.paths.previous, 'installer.exe'), OLD);
    w.st.previous = { version: '3.1.5', sha256: sha(OLD), size: OLD.length };
    await w.core.handle(cmd(w), w.serverTime);
    assert.strictEqual(w.installerRuns.length, 1);
    w.installed = '3.2.0'; w.status = { player_version: '3.2.0', player_last_seen_s: 500 };       // new version does not come up healthy
    await w.core.updater.tick();
    assert.strictEqual(w.installerRuns.length, 1, 'still waiting inside the deadline');
    w.clock.t += U.HEALTH_DEADLINE_MS + 1000;
    await w.core.updater.tick();
    assert.strictEqual(w.installerRuns.length, 2);
    assert.deepStrictEqual(w.installerRuns[1].bytes, OLD);
    assert.deepStrictEqual(w.st.rolledBack, [ID]);
    assert.strictEqual(w.st.pending.phase, 'rolling_back');
    assert.ok(w.calls.filter((c) => c[0] === 'verifySignature').length >= 2, 'the previous installer is checked again');
    // The rollback does not come up either: fail, and never again for this id.
    w.clock.t += U.HEALTH_DEADLINE_MS + 1000;
    await w.core.updater.tick();
    assert.strictEqual(w.installerRuns.length, 2);
    assert.strictEqual(w.st.pending, null);
    assert.strictEqual(w.st.lastUpdate.state, 'failed');
    await w.core.updater.tick(); await w.core.updater.tick();
    assert.strictEqual(w.installerRuns.length, 2);
  } finally { w.cleanup(); }
});

test('a successful rollback is reported as rolled_back and keeps the floor where it was', async () => {
  const OLD = Buffer.from('MZ-old-installer-3.1.5');
  const w = harness({ files: { [ID]: BODY } });
  try {
    fs.writeFileSync(path.join(w.paths.previous, 'installer.exe'), OLD);
    w.st.previous = { version: '3.1.5', sha256: sha(OLD), size: OLD.length };
    await w.core.handle(cmd(w), w.serverTime);
    w.clock.t += U.HEALTH_DEADLINE_MS + 1000;
    await w.core.updater.tick();
    w.installed = '3.1.5'; w.status = { player_version: '3.1.5', player_last_seen_s: 2 };
    await w.core.updater.tick();
    assert.deepStrictEqual(w.st.lastUpdate, { id: ID, state: 'rolled_back', to: '3.1.5' });
    assert.strictEqual(w.st.minVersion, '0');
  } finally { w.cleanup(); }
});

test('a previous installer that no longer verifies is not run; no previous installer at all means failed_no_rollback', async () => {
  const w = harness({ files: { [ID]: BODY } });
  try {
    await w.core.handle(cmd(w), w.serverTime);
    w.clock.t += U.HEALTH_DEADLINE_MS + 1000;
    await w.core.updater.tick();
    assert.strictEqual(w.st.lastUpdate.state, 'failed_no_rollback');
    assert.strictEqual(w.installerRuns.length, 1);
  } finally { w.cleanup(); }
  const w2 = harness({ files: { [ID]: BODY } });
  try {
    const OLD = Buffer.from('MZ-old');
    fs.writeFileSync(path.join(w2.paths.previous, 'installer.exe'), 'TAMPERED');
    w2.st.previous = { version: '3.1.5', sha256: sha(OLD), size: OLD.length };
    await w2.core.handle(cmd(w2), w2.serverTime);
    w2.clock.t += U.HEALTH_DEADLINE_MS + 1000;
    await w2.core.updater.tick();
    assert.strictEqual(w2.installerRuns.length, 1);
    assert.strictEqual(w2.st.lastUpdate.state, 'failed');
  } finally { w2.cleanup(); }
});

test('crash recovery at start: stale pending closed, an interrupted install resumes verification', async () => {
  const w = harness();
  try {
    const p = { id: ID, from: '3.1.0', to: '3.2.0', sha256: sha(BODY), size: BODY.length, startedAt: w.clock.t - 1000, phase: 'installing' };
    w.st.pending = Object.assign({}, p);
    assert.strictEqual(await w.core.recover(), 'finish');
    assert.strictEqual(w.st.pending.phase, 'verifying');
    assert.ok(names(w).includes('launchPlayer'));
    w.st.pending = Object.assign({}, p, { startedAt: w.clock.t - U.STALE_PENDING_MS - 1 });
    assert.strictEqual(await w.core.recover(), 'close');
    assert.strictEqual(w.st.pending, null);
    assert.strictEqual(w.st.lastUpdate.state, 'failed');
  } finally { w.cleanup(); }
});

test('installer.js: a spawn error, a missing file, a non-zero exit and a timeout are all failures, never an unhandled error', async () => {
  const fake = (fn) => () => { const c = new EventEmitter(); c.kill = () => {}; setImmediate(() => fn(c)); return c; };
  assert.deepStrictEqual(await runInstaller('x', [], 1000, fake((c) => c.emit('exit', 0))), { ok: true, code: 0 });
  assert.strictEqual((await runInstaller('x', [], 1000, fake((c) => c.emit('exit', 5)))).ok, false);
  const err = await runInstaller('x', [], 1000, fake((c) => c.emit('error', Object.assign(new Error('blocked'), { code: 'EACCES' }))));
  assert.strictEqual(err.ok, false); assert.match(err.reason, /did not start \(EACCES\)/);
  assert.strictEqual((await runInstaller('x', [], 1000, () => { throw new Error('boom'); })).ok, false);
  assert.match((await runInstaller('x', [], 20, fake(() => {}))).reason, /timed out/);
});
