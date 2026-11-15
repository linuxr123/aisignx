'use strict';
// Device agent: command rules, ledger, redaction, gzip cap, watchdog decision, log rotation,
// the poll loop with a fake upstream, and (Windows) the real watchdog.ps1 with a dummy process.
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('fs');
const os = require('os');
const path = require('path');
const zlib = require('zlib');
const { spawnSync } = require('child_process');
const { Readable } = require('stream');
const L = require('../src/agent/logic');
const { createAgent } = require('../src/agent');
const task = require('../src/agent/task');
const log = require('../src/log');

const dirs = [];
const tmp = () => { const d = fs.mkdtempSync(path.join(os.tmpdir(), 'aisignx-agent-')); dirs.push(d); return d; };
test.after(() => { for (const d of dirs) fs.rmSync(d, { recursive: true, force: true }); });
const ID = '0123456789abcdef';

test('validateCommand: strict id, closed verb list, empty params', () => {
  assert.deepEqual(L.validateCommand({ id: ID, action: 'restart_player', params: {}, expires_in_s: 60 }), { ok: true, id: ID, action: 'restart_player' });
  assert.equal(L.validateCommand({ id: ID, action: 'collect_logs' }).ok, true);
  for (const bad of [null, [], 'x', {}, { id: 'ABCDEF0123456789', action: 'restart_player' }, { id: ID.slice(1), action: 'restart_player' },
    { id: 5, action: 'restart_player' }]) {
    const v = L.validateCommand(bad);
    assert.equal(v.ok, false); assert.equal(v.id, undefined);
  }
  for (const action of ['reboot', 'powershell', 'restart_player; calc', '', 5, undefined]) {
    assert.deepEqual(L.validateCommand({ id: ID, action }), { ok: false, id: ID, reason: 'unsupported' });
  }
  assert.equal(L.validateCommand({ id: ID, action: 'restart_player', params: { cmd: 'calc' } }).reason, 'unsupported');
  assert.equal(L.validateCommand({ id: ID, action: 'restart_player', expires_in_s: 0 }).reason, 'expired');
});

test('allowed follows the admin switches', () => {
  assert.equal(L.allowed('restart_player', { restart: true, logs: false }), true);
  assert.equal(L.allowed('collect_logs', { restart: true, logs: false }), false);
  assert.equal(L.allowed('collect_logs', null), false);
});

test('redact removes tokens, cookies, pins, hashes and certificates', () => {
  const token = 'T'.repeat(43);
  const cfg = { unlock: { hash: 'a'.repeat(64), salt: 'b'.repeat(32) }, certPin: { sha256: Array(32).fill('AB').join(':') } };
  const text = [
    'Authorization: Bearer ' + token, 'cookie: aisignx_display=' + token, 'set-cookie aisignx_display=xyz; Path=/',
    'literal ' + token + ' here', 'hash ' + cfg.unlock.hash, 'salt=' + cfg.unlock.salt, 'pin ' + cfg.certPin.sha256,
    '{"unlock":{"hash":"deadbeef01","salt":"cafe0001"},"token":"abc123"}', 'caPem: -----BEGIN CERTIFICATE-----\nMIIB\n-----END CERTIFICATE-----',
    'INFO player state PLAYING', 'INFO proxy listening on http://127.0.0.1:47800',
  ].join('\n');
  const out = L.redact(text, L.secretsFrom(cfg, token));
  for (const s of [token, cfg.unlock.hash, cfg.unlock.salt, cfg.certPin.sha256, 'deadbeef01', 'cafe0001', 'abc123', 'MIIB', 'xyz']) {
    assert.ok(!out.includes(s), 'leaked ' + s);
  }
  assert.ok(out.includes('player state PLAYING') && out.includes('127.0.0.1:47800'));
});

test('gzipNewest keeps the newest lines under the cap', () => {
  let text = '';
  const rnd = require('crypto');
  for (let i = 0; i < 40000; i++) text += 'line ' + i + ' ' + rnd.randomBytes(12).toString('hex') + '\n';
  const cap = 100 * 1024;
  const { gz, truncated } = L.gzipNewest(text, cap);
  assert.ok(truncated && gz.length <= cap && gz.length > cap * 0.5, 'size ' + gz.length);
  const out = zlib.gunzipSync(gz).toString();
  assert.ok(out.includes('line 39999 ') && !out.includes('line 0 '));
  const small = L.gzipNewest('short\n', cap);
  assert.equal(small.truncated, false);
  assert.equal(zlib.gunzipSync(small.gz).toString(), 'short\n');
});

test('ledger is bounded and sanitised', () => {
  let ids = [];
  for (let i = 0; i < 300; i++) ids = L.ledgerAdd(ids, String(i).padStart(16, '0'), 200);
  assert.equal(ids.length, 200);
  assert.deepEqual(L.ledgerSanitize(['bad', ID, 5]), [ID]);
  assert.deepEqual(L.ledgerSanitize('x'), []);
});

test('decide: watchdog rules', () => {
  const now = 10_000_000;
  const p = [{ pid: 7, startMs: now - 600000 }];
  const base = { enabled: true, stopped: false, procs: p, hb: { t: now - 10000, pid: 7 }, now, restarts: [], gaveUp: false };
  assert.equal(L.decide(base).action, 'none');
  assert.equal(L.decide(Object.assign({}, base, { enabled: false, procs: [] })).action, 'none');
  assert.equal(L.decide(Object.assign({}, base, { stopped: true, procs: [] })).reason, 'stopped by user');
  const dead = L.decide(Object.assign({}, base, { procs: [] }));
  assert.equal(dead.action, 'start'); assert.deepEqual(dead.restarts, [now]);
  assert.equal(L.decide(Object.assign({}, base, { hb: { t: now - 91000, pid: 7 } })).action, 'kill_start');
  assert.equal(L.decide(Object.assign({}, base, { hb: { t: now - 89000, pid: 7 } })).action, 'none');
  // heartbeat from an older process: grace counted from the process start
  const old = { t: now - 999999, pid: 3 };
  assert.equal(L.decide(Object.assign({}, base, { hb: old, procs: [{ pid: 7, startMs: now - 60000 }] })).action, 'none');
  assert.equal(L.decide(Object.assign({}, base, { hb: null })).action, 'kill_start');
  // cap: 6 per hour then gave_up, nothing started
  const six = Array.from({ length: 6 }, (_, i) => now - 1000 * (i + 1));
  const capped = L.decide(Object.assign({}, base, { procs: [], restarts: six }));
  assert.equal(capped.action, 'gave_up'); assert.equal(capped.gaveUp, true);
  assert.equal(L.decide(Object.assign({}, base, { procs: [], restarts: six.map((t) => t - 3600000) })).action, 'start');
  // sticky gave_up until healthy
  assert.equal(L.decide(Object.assign({}, base, { procs: [], gaveUp: true })).action, 'gave_up');
  const back = L.decide(Object.assign({}, base, { gaveUp: true }));
  assert.equal(back.action, 'none'); assert.equal(back.gaveUp, false);
});

test('summarize', () => {
  const now = 5_000_000_000;
  assert.deepEqual(L.summarize(null, now), { restarts_1h: 0, gave_up: false, last_restart_ms: 0 });
  assert.deepEqual(L.summarize({ restarts: [now - 7200000, now - 1000], gave_up: true }, now), { restarts_1h: 1, gave_up: true, last_restart_ms: now - 1000 });
});

test('log rotates to .1 instead of truncating', () => {
  const d = tmp();
  const f = path.join(d, 'x.log');
  log.init(f, { maxBytes: 300 });
  for (let i = 0; i < 20; i++) log.info('line number', i, 'cookie stays out');
  assert.ok(fs.existsSync(f + '.1'));
  assert.ok(fs.statSync(f).size <= 400 && fs.statSync(f + '.1').size > 0);
  assert.ok(log.readAll().includes('line number 19') && log.readAll().includes('rotated'));
  assert.ok(!fs.existsSync(f + '.2'));
  log.init('');
  fs.rmSync(d, { recursive: true, force: true });
});

// ---- Poll loop with a fake upstream.
function fakeUp(commands) {
  const calls = [];
  return {
    calls,
    request: async (o) => {
      let body = Buffer.alloc(0);
      if (o.body) for await (const c of o.body) body = Buffer.concat([body, c]);
      calls.push({ method: o.method, path: o.path, auth: o.headers.authorization, body });
      const reply = (status, obj) => Object.assign(Readable.from([Buffer.from(JSON.stringify(obj || {}))]), { statusCode: status });
      if (o.method === 'GET') return reply(200, { commands });
      return reply(200, { ok: true });
    },
  };
}
function mkAgent(up, extra) {
  const dataDir = tmp(); const agentDir = tmp();
  const relaunched = [];
  const agent = createAgent(Object.assign({
    dataDir, agentDir, version: '9.9.9', isPackaged: false, getUpstream: () => up, getToken: () => 'TOK'.repeat(10),
    getConfig: () => ({ unlock: { hash: 'h'.repeat(64), salt: 's'.repeat(32) } }), readLogs: () => 'INFO hello Bearer ' + 'TOK'.repeat(10) + '\n',
    relaunch: () => relaunched.push(1),
  }, extra));
  return { agent, relaunched, dataDir, agentDir };
}
const manifest = (a) => JSON.stringify({ settings: { agent: a } });
const acks = (up) => up.calls.filter((c) => c.path.endsWith('/ack')).map((c) => ({ id: c.path.split('/')[5], body: JSON.parse(c.body) }));

test('agent: no polling while both switches are off', async () => {
  const up = fakeUp([{ id: ID, action: 'restart_player' }]);
  const { agent } = mkAgent(up);
  agent.onManifest(manifest({ watchdog: false, restart: false, logs: false }));
  assert.equal(await agent.pollOnce(), 0);
  assert.equal(up.calls.length, 0);
});

test('agent: restart_player acks done, relaunches, runs once', async () => {
  const up = fakeUp([{ id: ID, action: 'restart_player', params: {}, expires_in_s: 100 }]);
  const { agent, relaunched, dataDir } = mkAgent(up);
  agent.onManifest(manifest({ watchdog: false, restart: true, logs: false }));
  assert.equal(await agent.pollOnce(), 1);
  assert.equal(up.calls[0].auth, 'Bearer ' + 'TOK'.repeat(10));
  assert.deepEqual(acks(up).map((a) => a.body.state), ['done']);
  assert.ok(JSON.parse(fs.readFileSync(path.join(dataDir, 'agent-handled.json'))).ids.includes(ID));
  await new Promise((r) => setTimeout(r, 700));
  assert.equal(relaunched.length, 1);
  await agent.pollOnce(); // the server hands the same id again (replay): ignored
  assert.equal(acks(up).length, 1);
  // a new agent instance (app restarted) still knows the id
  const again = createAgent({ dataDir, agentDir: tmp(), version: '1', getUpstream: () => up, getToken: () => 'T', readLogs: () => '', relaunch: () => relaunched.push(2) });
  again.onManifest(manifest({ restart: true }));
  await again.pollOnce();
  assert.equal(acks(up).length, 1);
});

test('agent: verb with its switch off is acked failed "switched off"', async () => {
  const up = fakeUp([{ id: ID, action: 'collect_logs' }]);
  const { agent, relaunched } = mkAgent(up);
  agent.onManifest(manifest({ restart: true, logs: false }));
  await agent.pollOnce();
  assert.deepEqual(acks(up).map((a) => a.body), [{ state: 'failed', result: 'switched off' }]);
  assert.equal(up.calls.filter((c) => c.path.endsWith('/logs')).length, 0);
  assert.equal(relaunched.length, 0);
});

test('agent: unknown verb and bad ids', async () => {
  const up = fakeUp([{ id: ID, action: 'reboot' }, { id: 'nope', action: 'restart_player' }, { id: 'f'.repeat(16), action: 'restart_player; calc' }]);
  const { agent, relaunched } = mkAgent(up);
  agent.onManifest(manifest({ restart: true, logs: true }));
  await agent.pollOnce();
  assert.deepEqual(acks(up).map((a) => a.body.result), ['unsupported', 'unsupported']);
  assert.equal(acks(up).length, 2); // the bad id is ignored, never answered
  assert.equal(relaunched.length, 0);
});

test('agent: collect_logs uploads redacted gzip, no separate ack', async () => {
  const up = fakeUp([{ id: ID, action: 'collect_logs' }]);
  const { agent } = mkAgent(up);
  agent.onManifest(manifest({ logs: true }));
  await agent.pollOnce();
  const up1 = up.calls.filter((c) => c.path === '/api/player/v1/commands/' + ID + '/logs');
  assert.equal(up1.length, 1);
  const text = zlib.gunzipSync(up1[0].body).toString();
  assert.ok(text.includes('hello') && !text.includes('TOK'.repeat(10)) && text.includes('9.9.9'));
  assert.equal(acks(up).length, 0);
});

test('agent: failed upload acks failed', async () => {
  const up = fakeUp([{ id: ID, action: 'collect_logs' }]);
  const orig = up.request;
  up.request = async (o) => {
    const r = await orig(o);
    if (o.path.endsWith('/logs')) r.statusCode = 409;
    return r;
  };
  const { agent } = mkAgent(up);
  agent.onManifest(manifest({ logs: true }));
  await agent.pollOnce();
  assert.equal(acks(up)[0].body.state, 'failed');
});

test('agent: heartbeat file only after an alive message, at most every 8 s', () => {
  let t = 1_000_000;
  const { agent, agentDir } = mkAgent(fakeUp([]), { now: () => t });
  const f = path.join(agentDir, 'heartbeat.json');
  assert.ok(!fs.existsSync(f));
  agent.onAlive('{"t":1,"state":"PLAYING"}');
  assert.deepEqual(JSON.parse(fs.readFileSync(f)), { t, pid: process.pid, state: 'PLAYING' });
  t += 3000; agent.onAlive('{"state":"PLAYING"}');
  assert.equal(JSON.parse(fs.readFileSync(f)).t, 1_000_000);
  t += 6000; agent.onAlive('{"state":"NO_CONTENT"}');
  assert.equal(JSON.parse(fs.readFileSync(f)).state, 'NO_CONTENT');
  agent.onAlive('not json'); agent.onAlive('x'.repeat(5000));
});

test('agent: stopped marker and stats', () => {
  const { agent, agentDir } = mkAgent(fakeUp([]));
  agent.markStopped();
  assert.ok(fs.existsSync(path.join(agentDir, 'stopped')));
  agent.start(); agent.stop();
  assert.ok(!fs.existsSync(path.join(agentDir, 'stopped')));
  fs.writeFileSync(path.join(agentDir, 'state.json'), JSON.stringify({ restarts: [Date.now() - 1000], gave_up: true }));
  const s = agent.stats();
  assert.equal(s.tier, 'plain'); assert.equal(s.can_reboot, false); assert.equal(s.restarts_1h, 1); assert.equal(s.gave_up, true);
});

test('task XML is per-user, hidden, one-minute', () => {
  const x = task.buildTaskXml({ script: 'C:\\a b\\w.ps1', dir: 'C:\\a b', headless: true });
  assert.ok(x.includes('PT1M') && x.includes('LeastPrivilege') && x.includes('InteractiveToken') && x.includes('conhost.exe'));
  assert.ok(!x.includes('HighestAvailable'));
});

// ---- The real script with a dummy process (Windows only; no scheduled task, no real app).
test('watchdog.ps1 restarts a dead dummy, respects stopped and the hourly cap', { skip: process.platform !== 'win32' }, () => {
  const d = tmp();
  const exe = path.join(d, 'aisignx-dummy.exe');
  fs.copyFileSync(process.execPath, exe);
  const script = path.join(__dirname, '..', 'resources', 'watchdog.ps1');
  const run = () => spawnSync('powershell.exe', ['-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', script, '-Dir', d], { encoding: 'utf8', timeout: 60000 });
  const procs = () => spawnSync('powershell.exe', ['-NoProfile', '-Command',
    "(Get-Process -Name aisignx-dummy -ErrorAction SilentlyContinue | Where-Object { $_.Path -eq '" + exe + "' } | ForEach-Object { $_.Id }) -join ','"], { encoding: 'utf8' }).stdout.trim();
  const kill = () => spawnSync('powershell.exe', ['-NoProfile', '-Command',
    "Get-Process -Name aisignx-dummy -ErrorAction SilentlyContinue | Where-Object { $_.Path -eq '" + exe + "' } | Stop-Process -Force"]);
  const state = () => JSON.parse(fs.readFileSync(path.join(d, 'state.json'), 'utf8').replace(/^\uFEFF/, ''));
  try {
    fs.writeFileSync(path.join(d, 'flag.json'), JSON.stringify({ enabled: true, exe, args: ['-e', 'setInterval(()=>{},1000)'] }));
    fs.writeFileSync(path.join(d, 'stopped'), '1');
    run(); assert.equal(procs(), '', 'stopped marker respected');
    fs.rmSync(path.join(d, 'stopped'));
    run(); const pid = procs(); assert.match(pid, /^\d+$/, 'started when not running');
    assert.equal(state().restarts.length, 1);
    run(); assert.equal(procs(), pid, 'fresh process in grace: left alone');
    // a stale heartbeat of that very process: killed and started again
    fs.writeFileSync(path.join(d, 'heartbeat.json'), JSON.stringify({ t: Date.now() - 200000, pid: Number(pid), state: 'PLAYING' }));
    run(); const pid2 = procs(); assert.match(pid2, /^\d+$/); assert.notEqual(pid2, pid, 'hung process replaced');
    assert.equal(state().restarts.length, 2);
    // fresh heartbeat: untouched
    fs.writeFileSync(path.join(d, 'heartbeat.json'), JSON.stringify({ t: Date.now(), pid: Number(pid2), state: 'PLAYING' }));
    run(); assert.equal(procs(), pid2);
    // cap
    kill();
    fs.writeFileSync(path.join(d, 'state.json'), JSON.stringify({ restarts: Array.from({ length: 6 }, (_, i) => Date.now() - 1000 * i), gave_up: false }));
    run(); assert.equal(procs(), '', 'no restart over the cap');
    assert.equal(state().gave_up, true);
  } finally {
    kill();
    fs.rmSync(d, { recursive: true, force: true });
  }
});
