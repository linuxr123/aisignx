'use strict';
// Small pieces of the privileged agent: the stand-down flag, the lock, the log, init/enroll, and the in-app Tier B retirement.
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('fs');
const os = require('os');
const path = require('path');
const { Readable } = require('stream');
const delegate = require('../src/agent/delegate');
const lock = require('../src/agent-svc/lock');
const { createLog } = require('../src/agent-svc/log');
const enrollment = require('../src/agent-svc/enrollment');
const cfgm = require('../src/agent-svc/config');
const V = require('../src/agent/verify');
const ecies = require('../src/agent-svc/ecies');
const { createAgent } = require('../src/agent');
const vec = require('./vectors/agent-vectors.json');
const { harness, sha } = require('./helpers/agent-harness');

const dirs = [];
const tmp = () => { const d = fs.mkdtempSync(path.join(os.tmpdir(), 'asx-misc-')); dirs.push(d); return d; };
test.after(() => { for (const d of dirs) fs.rmSync(d, { recursive: true, force: true }); });

test('delegate: only a fresh status.json from the agent switches the in-app Tier B off', () => {
  const now = 1_900_000_000_000;
  assert.equal(delegate.judge({ state: 'running', t: now - 1000 }, now), true);
  assert.equal(delegate.judge({ state: 'unsafe_acl', t: now - 1000 }, now), false);         // a sick agent does nothing, so the in-app Tier B must not stand down
  assert.equal(delegate.judge({ state: 'running', t: now - delegate.FRESH_MS }, now), false); // stale: the agent is gone, in-app fallback is live again
  assert.equal(delegate.judge({ state: 'running', t: now + 5000 }, now), false);
  for (const bad of [null, [], 'x', {}, { state: 'stopped', t: now }, { state: 'running' }, { state: 'running', t: 'now' }]) assert.equal(delegate.judge(bad, now), false);
  assert.equal(delegate.statusFile({ ProgramData: 'D:\\PD' }), 'D:\\PD\\AiSignXAgent\\public\\status.json');
  assert.equal(delegate.statusFile({}), 'C:\\ProgramData\\AiSignXAgent\\public\\status.json');
  const f = path.join(tmp(), 's.json');
  assert.equal(delegate.isActive(f, now), false);
  fs.writeFileSync(f, JSON.stringify({ state: 'running', t: now }));
  assert.equal(delegate.isActive(f, now), true);
  fs.writeFileSync(f, '{broken');
  assert.equal(delegate.isActive(f, now), false);                                          // worst case of a forged file is OFF
});

function mkInApp(delegated, commands) {
  const calls = [];
  const up = { calls, request: async (o) => {
    let body = ''; if (o.body) for await (const c of o.body) body += c;
    calls.push({ method: o.method, path: o.path, body });
    const reply = (s, obj) => Object.assign(Readable.from([Buffer.from(JSON.stringify(obj))]), { statusCode: s, headers: {} });
    return o.method === 'GET' ? reply(200, { commands, server_time_ms: 1_900_000_000_000 }) : reply(200, { ok: true });
  } };
  const dataDir = tmp();
  const agent = createAgent({ dataDir, agentDir: tmp(), version: '9', isPackaged: true, platform: 'win32', getUpstream: () => up, getToken: () => 'TOK'.repeat(10),
    getConfig: () => ({}), readLogs: () => '', relaunch() {}, agentDelegated: () => delegated });
  agent.onManifest(JSON.stringify({ display: { id: 7 }, settings: { agent: { reboot: true, update: true, startup: true } } }));
  return { agent, calls, dataDir };
}

test('in-app Tier B stands down while the agent is in charge: no ack, no ledger entry, capabilities off', async () => {
  const cmd = vec.commands.reboot;
  const a = mkInApp(true, [cmd]);
  await a.agent.pollOnce();
  assert.equal(a.calls.filter((c) => c.method === 'POST').length, 0);
  assert.ok(!fs.existsSync(path.join(a.dataDir, 'agent-handled.json')) || !fs.readFileSync(path.join(a.dataDir, 'agent-handled.json'), 'utf8').includes(cmd.id));
  const s = a.agent.stats();
  assert.deepEqual([s.can_reboot, s.can_update, s.can_set_startup, s.delegated_to_agent], [false, false, false, true]);
  const b = mkInApp(false, [cmd]);                                                          // without the agent the in-app code still answers
  await b.agent.pollOnce();
  assert.equal(b.calls.filter((c) => c.method === 'POST').length, 1);
  assert.equal(b.agent.stats().delegated_to_agent, undefined);
});

test('in-app Tier B refuses set_autologin even with a good signature path (only the agent does it)', async () => {
  const b = mkInApp(false, [vec.commands.autologin_off]);
  await b.agent.pollOnce();
  const acks = b.calls.filter((c) => c.method === 'POST').map((c) => JSON.parse(c.body));
  assert.equal(acks.length, 1);
  assert.equal(acks[0].state, 'failed');
});

test('lock: a live copy blocks a second one; a stale lock does not; the owner can refresh and release', () => {
  const f = path.join(tmp(), 'agent.lock');
  const t = 5_000_000;
  assert.equal(lock.acquire(f, 111, t), true);
  assert.equal(lock.acquire(f, 222, t + 1000), false);
  assert.equal(lock.acquire(f, 111, t + 1000), true);                                      // the same process again
  lock.refresh(f, 111, t + 50_000);
  assert.equal(lock.acquire(f, 222, t + 50_000 + lock.STALE_MS - 1), false);
  assert.equal(lock.acquire(f, 222, t + 50_000 + lock.STALE_MS), true);                     // a dead owner's lock expires
  lock.release(f, 111);
  assert.ok(fs.existsSync(f));                                                              // not 111's any more
  lock.release(f, 222);
  assert.ok(!fs.existsSync(f));
  fs.writeFileSync(f, 'garbage');
  assert.equal(lock.acquire(f, 5, t), true);
});

test('log: redacts secrets, rotates, and never throws', () => {
  const d = tmp(); const f = path.join(d, 'logs', 'agent.log');
  const lg = createLog(f, () => ['SECRETVALUE123']);
  lg.info('token SECRETVALUE123 and Bearer ' + 'x'.repeat(30));
  assert.ok(!fs.readFileSync(f, 'utf8').includes('SECRETVALUE123'));
  assert.ok(!fs.readFileSync(f, 'utf8').includes('x'.repeat(30)));
  fs.writeFileSync(f, 'y'.repeat(1024 * 1024 + 10));
  lg.warn('after');
  assert.ok(fs.existsSync(f + '.1'));
  assert.doesNotThrow(() => createLog(path.join(d, 'nul\0bad', 'x.log')).error('x'));
});

function fakeEnrollServer(over) {
  const calls = [];
  const k = V.decodeB64(vec.command_key_b64, 32);
  const request = async (o) => {
    let body = ''; if (o.body) for await (const c of o.body) body += c;
    calls.push({ path: o.path, body: JSON.parse(body) });
    const data = Object.assign({ agent_token: 'agt_' + 'C'.repeat(43), display_id: 7, command_key: vec.command_key_b64 }, over);
    return Object.assign(Readable.from([Buffer.from(JSON.stringify(data))]), { statusCode: (over && over.status) || 200 });
  };
  return { request, calls, fp: V.fingerprint(k) };
}
const params = (fp, extra) => Object.assign({ server: 'https://signage.example', code: 'ABCD-EFGH-JKLM-NPQR', fingerprint: fp, thumbprint: '386BFBC220743F420E64AB481C4249EB1DE9C4BC',
  allow: { update: true }, trustScope: 'SystemOnly', kioskUser: 'kiosk', kioskSid: 'S-1-5-21-1-2-3-1001', playerExe: 'C:\\Program Files\\AiSignX Player\\AiSignX Player.exe' }, extra);

test('enroll: key pair is made once, the command key must match the typed fingerprint, agent.json holds the pins', async () => {
  const d = tmp(); const paths = cfgm.dataPaths(d);
  const pub1 = enrollment.init(paths);
  assert.equal(enrollment.init(paths), pub1);                                               // idempotent: the private key is kept
  assert.equal(Buffer.from(pub1, 'base64').length, 32);
  const srv = fakeEnrollServer();
  const r = await enrollment.enroll(params(srv.fp), paths, srv.request, { host: 'PC1', version: '3.1.0' });
  assert.equal(r.ok, true);
  assert.equal(srv.calls[0].path, '/api/player/v1/agent/enroll');
  assert.equal(srv.calls[0].body.public_key, pub1);
  assert.equal(srv.calls[0].body.code, 'ABCD-EFGH-JKLM-NPQR');
  const saved = JSON.parse(fs.readFileSync(paths.config, 'utf8'));
  assert.equal(saved.commandKey, vec.command_key_b64);
  assert.equal(saved.thumbprint, '386BFBC220743F420E64AB481C4249EB1DE9C4BC');
  assert.deepEqual(saved.allow, { reboot: false, startup: false, update: true, autologin: false });
  assert.equal(cfgm.sanitize(saved) !== null, true);
  assert.equal(ecies.keyId(ecies.publicOf(enrollment.readKey(paths))), ecies.keyId(Buffer.from(pub1, 'base64')));
});

test('enroll: a command key that does not match the fingerprint, a refused code or bad parameters write nothing', async () => {
  for (const [what, p, over, why] of [
    ['wrong fingerprint', (fp) => params('0000 0000 0000 0000'), undefined, /does not match the fingerprint/],
    ['refused code', (fp) => params(fp), { status: 403 }, /refused the code \(403\)/],
    ['bad server', (fp) => params(fp, { server: 'https://x/path' }), undefined, /bad server/],
    ['bad code', (fp) => params(fp, { code: 'x; calc' }), undefined, /bad enrollment code/],
    ['bad thumbprint', (fp) => params(fp, { thumbprint: 'nope' }), undefined, /not valid/],
    ['bad token from server', (fp) => params(fp), { agent_token: 'disp_' + 'C'.repeat(43) }, /not valid/],
  ]) {
    const d = tmp(); const paths = cfgm.dataPaths(d); const srv = fakeEnrollServer(over);
    const r = await enrollment.enroll(p(srv.fp), paths, srv.request, { host: 'h', version: '1' });
    assert.equal(r.ok, false, what); assert.match(r.reason, why, what);
    assert.ok(!fs.existsSync(paths.config), what + ' wrote agent.json');
  }
  assert.equal((await enrollment.enroll(null, cfgm.dataPaths(tmp()), null, {})).ok, false);
});

test('a cooldown stops a second update right after one (10 minutes)', async () => {
  const BODY = Buffer.from('MZ-x');
  const w = harness({ files: { '5555555555555555': BODY, '6666666666666666': BODY } });
  try {
    const c = (id) => w.signer.sign(id, 'update_app', { version: '3.2.0', sha256: sha(BODY), size: BODY.length });
    await w.core.handle(c('5555555555555555'), w.serverTime);
    w.installed = '3.2.0'; w.status = { player_version: '3.2.0', player_last_seen_s: 1 };
    await w.core.updater.tick();
    assert.equal(w.st.pending, null);
    await w.core.handle(c('6666666666666666'), w.serverTime);
    assert.match(w.acks.at(-1).result, /cooldown/);
  } finally { w.cleanup(); }
});

test('main: refuses to start with a poisoned environment or without agent.json; --init makes the key in the protected folder', async () => {
  const { main, dataDirOf } = require('../src/agent-svc/main');
  const pd = tmp();
  const env = { ProgramData: pd };
  assert.equal(dataDirOf(env), path.win32.join(pd, 'AiSignXAgent'));
  assert.equal(await main([], Object.assign({ NODE_OPTIONS: '--require x' }, env)), 2);
  assert.equal(await main([], Object.assign({ ELECTRON_ENABLE_LOGGING: '1' }, env)), 2);
  assert.equal(await main([], env), 3);                                                      // not configured: nothing runs
  const out = []; const w = process.stdout.write;
  process.stdout.write = (s) => { out.push(String(s)); return true; };
  try { assert.equal(await main(['--init'], env), 0); assert.equal(await main(['--version'], env), 0); } finally { process.stdout.write = w; }
  assert.equal(Buffer.from(out[0].trim(), 'base64').length, 32);
  assert.ok(fs.existsSync(path.win32.join(pd, 'AiSignXAgent', 'keys', 'x25519.key')));
  assert.match(out[1], /^\d+\.\d+\.\d+/);
});
