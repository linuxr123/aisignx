'use strict';
// The agent that was installed without a code: it asks, publishes its claim id, polls, and opens the approval.
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('fs');
const os = require('os');
const path = require('path');
const ecies = require('../src/agent-svc/ecies');
const approval = require('../src/agent-svc/approval');
const enrollment = require('../src/agent-svc/enrollment');
const cfgm = require('../src/agent-svc/config');
const V = require('../src/agent/verify');

const THUMB = 'AB'.repeat(20);
const TOKEN = 'agt_' + 'x'.repeat(43);
const dirs = [];
function setup() {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'aisignx-approval-'));
  dirs.push(dir);
  const paths = cfgm.dataPaths(dir);
  paths.dataDir = dir;
  Object.assign(paths, { config: path.join(dir, 'agent.json'), keyFile: path.join(dir, 'keys', 'x25519.key'), publicDir: path.join(dir, 'public'),
    status: path.join(dir, 'public', 'status.json') });
  return paths;
}
test.after(() => { for (const d of dirs) fs.rmSync(d, { recursive: true, force: true }); });

const PARAMS = { server: 'https://signs.example.com', thumbprint: THUMB, allow: { reboot: true, startup: false, update: true, autologin: false },
  trustScope: 'SystemOnly', kioskUser: 'kiosk', kioskSid: 'S-1-5-21-1-2-3-1001', playerExe: 'C:\\Program Files\\AiSignX Player\\AiSignX Player.exe' };

// A fake server: answers /agent/request and /agent/poll the way the real one does.
function fakeServer(state) {
  const calls = [];
  const request = async (o) => {
    const chunks = []; for await (const c of o.body) chunks.push(c);
    const body = JSON.parse(Buffer.concat(chunks).toString());
    calls.push({ path: o.path, body });
    let status = 200, data = {};
    if (o.path.endsWith('/agent/request')) { state.seen = body; data = { ok: true, state: 'pending' }; if (state.refuse) status = 400; }
    else if (o.path.endsWith('/agent/poll')) {
      if (state.gone) { status = 404; data = { error: 'unknown_request' }; }
      else if (state.decision === 'approved') {
        const aad = approval.aad(state.seen.claim_id);
        data = { state: 'approved', ciphertext: ecies.encrypt(Buffer.from(state.seen.public_key, 'base64'), Buffer.from(state.token || TOKEN), aad), display_id: 7,
          command_key: Buffer.alloc(32, 9).toString('base64'), fingerprint: 'x' };
      } else data = { state: state.decision || 'pending', claimed: !!state.claimed };
    }
    const payload = Buffer.from(JSON.stringify(data));
    return Object.assign((async function* () { yield payload; })(), { statusCode: status, destroy() {} });
  };
  return { request, calls };
}
const stepOf = (paths, srv, extra) => approval.step(Object.assign({ paths, request: srv.request, now: Date.now(), host: 'FRONTDESK', version: '3.4.0',
  readKey: () => enrollment.readKey(paths), pubKey: enrollment.init(paths) }, extra));

test('prepare keeps the install-time settings and nothing else; a bad value is refused', () => {
  const paths = setup();
  assert.equal(enrollment.prepare(PARAMS, paths).ok, true);
  const saved = JSON.parse(fs.readFileSync(approval.pathsOf(paths).pending, 'utf8'));
  assert.equal(saved.server, PARAMS.server);
  assert.deepEqual(saved.allow, PARAMS.allow);
  assert.equal(saved.token, undefined);
  for (const bad of [{ server: 'ftp://x' }, { thumbprint: 'zz' }, { server: 'http://192.168.1.19:5000' }]) {
    assert.equal(enrollment.prepare(Object.assign({}, PARAMS, bad), setup()).ok, false, JSON.stringify(bad));
  }
  assert.equal(enrollment.prepare(Object.assign({}, PARAMS, { server: 'http://192.168.1.19:5000', allowInsecureHttp: true }), setup()).ok, true);
});

test('while nobody approved: it asks, publishes the claim id (never the poll secret) and keeps the same ids', async () => {
  const paths = setup();
  enrollment.prepare(PARAMS, paths);
  const srv = fakeServer({});
  const r1 = await stepOf(paths, srv);
  assert.equal(r1.state, 'waiting');
  const published = JSON.parse(fs.readFileSync(approval.pathsOf(paths).published, 'utf8'));
  const asked = srv.calls[0].body;
  assert.equal(published.claim_id, asked.claim_id);
  assert.ok(!JSON.stringify(published).includes(asked.poll_secret));
  assert.notEqual(asked.claim_id, asked.poll_secret);
  assert.match(asked.claim_id, /^[A-Za-z0-9_-]{22,64}$/);
  await stepOf(paths, srv);
  assert.equal(srv.calls[2].body.claim_id, asked.claim_id);              // a restart or the next round: the same request
});

test('approved: the token is opened with the agent key, agent.json is written, the waiting files are gone', async () => {
  const paths = setup();
  enrollment.prepare(PARAMS, paths);
  const state = { decision: 'approved' };
  const srv = fakeServer(state);
  const r = await stepOf(paths, srv);
  assert.equal(r.state, 'enrolled');
  const cfg = JSON.parse(fs.readFileSync(paths.config, 'utf8'));
  assert.equal(cfg.token, TOKEN);
  assert.equal(cfg.server, PARAMS.server);
  assert.equal(cfg.displayId, 7);
  assert.equal(cfg.thumbprint, THUMB);
  assert.deepEqual(cfg.allow, PARAMS.allow);
  assert.equal(cfg.kioskUser, 'kiosk');
  assert.equal(r.fingerprint, V.fingerprint(Buffer.alloc(32, 9)));
  for (const f of ['pending', 'request', 'published']) assert.equal(fs.existsSync(approval.pathsOf(paths)[f]), false, f);
});

test('an approval that is not for this agent, or a token of the wrong shape, enrolls nothing', async () => {
  const paths = setup();
  enrollment.prepare(PARAMS, paths);
  const srv = fakeServer({ decision: 'approved', token: 'not-a-token' });
  const r = await stepOf(paths, srv);
  assert.equal(r.state, 'error');
  assert.equal(fs.existsSync(paths.config), false);
  const other = ecies.generateKeyPair();
  const r2 = await stepOf(paths, fakeServer({ decision: 'approved' }), { readKey: () => other.priv });   // another key cannot open it
  assert.equal(r2.state, 'error');
  assert.equal(fs.existsSync(paths.config), false);
});

test('rejected, expired and refused requests are reported, and a refused one asks again with new ids', async () => {
  const paths = setup();
  enrollment.prepare(PARAMS, paths);
  assert.equal((await stepOf(paths, fakeServer({ decision: 'rejected' }))).state, 'rejected');
  await stepOf(paths, fakeServer({}));
  const first = JSON.parse(fs.readFileSync(approval.pathsOf(paths).request, 'utf8'));
  assert.equal((await stepOf(paths, fakeServer({ gone: true }))).state, 'error');
  assert.equal(fs.existsSync(approval.pathsOf(paths).request), false);   // dropped: the next round makes a new request
  const again = await stepOf(paths, fakeServer({}));
  assert.equal(again.state, 'waiting');
  assert.notEqual(JSON.parse(fs.readFileSync(approval.pathsOf(paths).request, 'utf8')).claim, first.claim);
});

test('a request older than its life is replaced; no pending.json means nothing happens', async () => {
  const paths = setup();
  enrollment.prepare(PARAMS, paths);
  const a = approval.currentRequest(paths, 1000);
  assert.deepEqual(approval.currentRequest(paths, 2000), a);
  assert.notEqual(approval.currentRequest(paths, 1000 + approval.REQUEST_LIFE_MS + 1).claim, a.claim);
  fs.rmSync(approval.pathsOf(paths).pending);
  assert.equal((await stepOf(paths, fakeServer({}))).state, 'error');
});
