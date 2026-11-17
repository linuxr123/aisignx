'use strict';
// Tier B through the real agent poll loop, with a fake server, a fake system and a real signing key.
const test = require('node:test');
const assert = require('node:assert/strict');
const crypto = require('crypto');
const fs = require('fs');
const os = require('os');
const path = require('path');
const { Readable } = require('stream');
const { createAgent } = require('../src/agent');
const sys = require('../src/agent/system');
const store = require('../src/agent/store');

const dirs = [];
const tmp = () => { const d = fs.mkdtempSync(path.join(os.tmpdir(), 'aisignx-tierb-')); dirs.push(d); return d; };
test.after(() => { for (const d of dirs) fs.rmSync(d, { recursive: true, force: true }); });

const NOW = 1_763_300_000_000;
const ORIGIN = 'http://srv.test:5000';
function makeKeys() {
  const { publicKey, privateKey } = crypto.generateKeyPairSync('ed25519');
  return { priv: privateKey, b64: publicKey.export({ format: 'der', type: 'spki' }).subarray(-32).toString('base64') };
}
let seqCounter = 100, idCounter = 0;
const newId = () => (++idCounter).toString(16).padStart(16, '0');
function signed(keys, action, params, over) {
  const id = (over && over.id) || newId();
  const b = Object.assign({ action, display_id: 7, exp_ms: NOW + 60000, params, request_id: id, seq: ++seqCounter, v: 1 }, over && over.body);
  const text = JSON.stringify(b); // keys already in sorted order above
  return { id, action, params, expires_in_s: 60, signed: { payload: text, sig: crypto.sign(null, Buffer.from(text), keys.priv).toString('base64') } };
}

// A fake server. state.keyB64 is what /command-key answers; state.package is the installer's bytes.
function fakeServer(state) {
  const calls = [];
  const reply = (status, obj, headers) => Object.assign(Readable.from([Buffer.from(JSON.stringify(obj || {}))]), { statusCode: status, headers: headers || {} });
  return {
    origin: ORIGIN, calls,
    request: async (o) => {
      let body = Buffer.alloc(0);
      if (o.body) for await (const c of o.body) body = Buffer.concat([body, c]);
      calls.push({ method: o.method, path: o.path, auth: o.headers.authorization, body });
      if (o.method === 'GET' && o.path.endsWith('/commands')) { const list = state.commands; state.commands = []; return reply(200, { commands: list, server_time_ms: NOW }); }
      if (o.path.endsWith('/command-key')) return state.keyDown ? reply(503) : reply(200, { public_key: state.keyB64, fingerprint: 'x' });
      if (o.path.endsWith('/package')) {
        if (!state.package) return reply(404);
        return Object.assign(Readable.from([state.package]), { statusCode: 200, headers: { 'content-length': String(state.package.length) } });
      }
      return reply(200, { ok: true });
    },
  };
}
const acks = (up) => up.calls.filter((c) => c.path.endsWith('/ack')).map((c) => ({ id: c.path.split('/')[5], ...JSON.parse(c.body) }));

function mk(state, extra) {
  const dataDir = (extra && extra.dataDir) || tmp();
  const up = (extra && extra.up) || fakeServer(state);
  const events = [];
  const system = {
    isOurShell: sys.isOurShell, readShell: async () => state.shell || '',
    reboot: async () => { events.push('reboot'); return { ok: !state.rebootFails }; },
    setShellMode: async (a) => { events.push('shell on'); state.shell = sys.shellValue(path.join(a.agentDir, 'shell-launcher.ps1'), 'C:\\Windows'); return true; },
    clearShellMode: async () => { events.push('shell off'); state.shell = ''; return true; },
  };
  const auto = { on: false };
  const launcher = path.join(tmp(), 'shell-launcher.ps1'); fs.writeFileSync(launcher, '# test');
  const agent = createAgent(Object.assign({
    dataDir, agentDir: tmp(), version: '3.1.0', isPackaged: true, platform: 'win32', exe: 'C:\\app.exe', now: () => (extra && extra.clock ? extra.clock.t : NOW),
    getUpstream: () => up, getToken: () => 'TOK'.repeat(10), getConfig: () => ({}), readLogs: () => '', relaunch() {},
    launcherSource: launcher, system, startup: { get: () => auto.on, set: (v) => { events.push('autostart ' + v); auto.on = v; } },
    quit: () => events.push('quit'),
    spawnInstaller: async (f, a) => { if (state.spawnFails) throw Object.assign(new Error('blocked'), { code: 'EPERM' }); events.push('install'); state.installed = { f, a }; },
  }, extra));
  return { agent, up, events, dataDir, auto, system };
}
const manifest = (a) => JSON.stringify({ display: { id: 7, name: 'x' }, settings: { agent: a } });
const ON = { reboot: true, startup: true, update: true };

test('tierb: pins the first key, reboots after the ack, once, with a cooldown', async () => {
  const keys = makeKeys();
  const state = { keyB64: keys.b64, commands: [signed(keys, 'reboot_device', {})] };
  const { agent, up, events, dataDir } = mk(state);
  agent.onManifest(manifest(ON));
  await agent.pollOnce();
  assert.deepEqual(acks(up).map((a) => [a.state, a.result]), [['done', 'rebooting']]);
  assert.deepEqual(events, ['reboot']);
  const saved = JSON.parse(fs.readFileSync(path.join(dataDir, 'agent-state.json')));
  assert.equal(saved.servers[ORIGIN].key, keys.b64);
  assert.ok(saved.servers[ORIGIN].seq > 100 && saved.rebootAt === NOW);
  // A fresh command (new seq) inside the 10-minute cooldown is refused.
  state.commands = [signed(keys, 'reboot_device', {})];
  await agent.pollOnce();
  assert.deepEqual(acks(up).slice(1).map((a) => a.state + ':' + a.result), ['failed:not_allowed (reboot cooldown)']);
  assert.deepEqual(events, ['reboot']);
});

test('tierb: replay of an old seq, even after an app restart, and the same id twice', async () => {
  const keys = makeKeys();
  const first = signed(keys, 'set_startup', { autostart: true });
  const state = { keyB64: keys.b64, commands: [first, first] };
  const a = mk(state);
  a.agent.onManifest(manifest(ON));
  await a.agent.pollOnce();
  assert.equal(acks(a.up).length, 1);              // the duplicate id is ignored
  const older = signed(keys, 'set_startup', { autostart: false }, { body: { seq: 5 } });
  state.commands = [older];
  const b = mk(state, { dataDir: a.dataDir, up: a.up });  // new agent instance, same state file
  b.agent.onManifest(manifest(ON));
  await b.agent.pollOnce();
  assert.match(acks(a.up)[1].result, /^replayed \(.*Forget server/);
  assert.deepEqual(b.events, []);
});

test('tierb: a changed server key refuses Tier B and is reported', async () => {
  const keys = makeKeys();
  const state = { keyB64: keys.b64, commands: [] };
  const { agent, up, events } = mk(state);
  agent.onManifest(manifest(ON));
  await agent.pollOnce();                                   // idle poll pins the key
  assert.equal(agent.stats().command_key_changed, undefined);
  const evil = makeKeys();
  state.keyB64 = evil.b64;
  state.commands = [signed(evil, 'reboot_device', {})];
  await agent.pollOnce();
  assert.match(acks(up)[0].result, /^bad_signature/);
  assert.deepEqual(events, []);
  assert.equal(agent.stats().command_key_changed, true);
  // Back to the pinned key: works again and the flag clears.
  state.keyB64 = keys.b64;
  state.commands = [signed(keys, 'reboot_device', {})];
  await agent.pollOnce();
  assert.deepEqual(events, ['reboot']);
  assert.equal(agent.stats().command_key_changed, undefined);
});

test('tierb: key fetch failing falls back to the pinned key, but never pins blindly', async () => {
  const keys = makeKeys();
  const state = { keyB64: keys.b64, keyDown: true, commands: [signed(keys, 'reboot_device', {})] };
  const first = mk(state);
  first.agent.onManifest(manifest(ON));
  await first.agent.pollOnce();
  assert.equal(acks(first.up)[0].result, 'bad_signature (no server key)');
  assert.deepEqual(first.events, []);
  state.keyDown = false; state.commands = [signed(keys, 'reboot_device', {})];
  await first.agent.pollOnce();                       // pinned now
  state.keyDown = true; state.commands = [signed(keys, 'set_startup', { autostart: true })];
  first.events.length = 0;
  await first.agent.pollOnce();
  assert.deepEqual(first.events, ['autostart true']);
});

test('tierb: switches off, wrong display, expired, unsigned lookalike, bad params', async () => {
  const keys = makeKeys();
  const unsigned = { id: newId(), action: 'reboot_device', params: {}, expires_in_s: 60 };
  const state = { keyB64: keys.b64, commands: [
    signed(keys, 'reboot_device', {}), signed(keys, 'reboot_device', {}, { body: { display_id: 9 } }),
    signed(keys, 'reboot_device', {}, { body: { exp_ms: NOW - 1 } }), unsigned,
    signed(keys, 'set_startup', { mode: 'rm -rf' }), signed(keys, 'set_startup', { autostart: true })] };
  const { agent, up, events } = mk(state);
  agent.onManifest(manifest({ reboot: false, startup: false, update: true }));
  await agent.pollOnce();
  assert.deepEqual(acks(up).map((a) => a.result), ['not_allowed', 'wrong_display', 'expired', 'bad_signature', 'bad_params', 'not_allowed']);
  assert.deepEqual(events, []);
});

test('tierb: set_startup sets shell mode and autostart, then undoes it', async () => {
  const keys = makeKeys();
  const state = { keyB64: keys.b64, commands: [signed(keys, 'set_startup', { autostart: true, mode: 'shell' })] };
  const { agent, up, events } = mk(state);
  agent.onManifest(manifest(ON));
  await agent.pollOnce();
  assert.deepEqual(events, ['shell on', 'autostart true']);
  assert.equal(acks(up)[0].result, 'autostart on, mode shell');
  assert.deepEqual(agent.stats().startup, { autostart: true, mode: 'shell', shell_capable: true });
  state.commands = [signed(keys, 'set_startup', { mode: 'app' })];
  await agent.pollOnce();
  assert.deepEqual(events, ['shell on', 'autostart true', 'shell off']);
  assert.equal(agent.stats().startup.mode, 'app');
});

test('tierb: capabilities and refusals when this is not an installed Windows app', async () => {
  const keys = makeKeys();
  const state = { keyB64: keys.b64, commands: [signed(keys, 'reboot_device', {}), signed(keys, 'set_startup', { mode: 'shell' })] };
  const dev = mk(state, { isPackaged: false });
  dev.agent.onManifest(manifest(ON));
  await dev.agent.pollOnce();
  assert.deepEqual(acks(dev.up).map((a) => a.result), ['unsupported', 'unsupported']);
  const s = dev.agent.stats();
  assert.equal(s.can_reboot, false); assert.equal(s.can_set_startup, false); assert.equal(s.can_update, false);
  assert.equal(s.startup.shell_capable, false);
  const win = mk({ keyB64: keys.b64, commands: [] }).agent.stats();
  assert.equal(win.can_reboot, true); assert.equal(win.can_set_startup, true); assert.equal(win.can_update, true);
  assert.equal(win.startup.shell_capable, true);
  const linux = mk({ keyB64: keys.b64, commands: [] }, { platform: 'linux' }).agent.stats();
  assert.equal(linux.can_reboot, false); assert.equal(linux.startup.shell_capable, false);
});

test('tierb: update_app downloads, verifies the signed size and sha256, acks, then installs and quits', async () => {
  const keys = makeKeys();
  const bytes = crypto.randomBytes(5000);
  const want = { sha256: crypto.createHash('sha256').update(bytes).digest('hex'), size: bytes.length, version: '3.1.1' };
  const state = { keyB64: keys.b64, package: bytes, commands: [signed(keys, 'update_app', want)] };
  const { agent, up, events, dataDir } = mk(state);
  agent.onManifest(manifest(ON));
  await agent.pollOnce();
  assert.equal(acks(up)[0].result, 'installing 3.1.1');
  const pk = up.calls.find((c) => c.path.endsWith('/package'));
  assert.equal(pk.auth, 'Bearer ' + 'TOK'.repeat(10));
  assert.deepEqual(events, ['install', 'quit']);   // quit only after the installer really started
  assert.ok(fs.existsSync(path.join(agent.dir, 'updating')));   // the launcher waits instead of starting Explorer
  assert.ok(!fs.existsSync(path.join(agent.dir, 'stopped')));
  assert.deepEqual(state.installed.a, ['/S', '--updated', '--force-run']);
  assert.ok(state.installed.f.startsWith(path.join(dataDir, 'updates')) && state.installed.f.endsWith('AiSignX-Player-Setup-3.1.1.exe'));
  assert.match(path.basename(path.dirname(state.installed.f)), /^u-/);   // its own fresh random folder
  assert.ok(fs.readFileSync(state.installed.f).equals(bytes));
  // A second update inside 10 minutes is refused.
  state.commands = [signed(keys, 'update_app', want)];
  await agent.pollOnce();
  assert.equal(acks(up)[1].result, 'not_allowed (update cooldown)');
  // A restarted app clears the leftover installer.
  agent.start(); agent.stop();
  assert.ok(!fs.existsSync(state.installed.f));
});

test('tierb: update spawn failure keeps the player running, reports the error and does not burn the cooldown', async () => {
  const keys = makeKeys();
  const bytes = crypto.randomBytes(2000);
  const want = { sha256: crypto.createHash('sha256').update(bytes).digest('hex'), size: bytes.length, version: '3.1.1' };
  const state = { keyB64: keys.b64, package: bytes, spawnFails: true, commands: [signed(keys, 'update_app', want)] };
  const m = mk(state);
  m.agent.onManifest(manifest(ON));
  await m.agent.pollOnce();
  assert.deepEqual(m.events, []);                                  // no quit, no marker
  assert.match(m.agent.stats().last_update_error, /installer did not start: EPERM/);
  assert.equal(m.agent.stats().command_key_fingerprint.length, 19);
  assert.equal(JSON.parse(fs.readFileSync(path.join(m.dataDir, 'agent-state.json'))).updateAt, 0);
  // A second try (new command) is allowed at once and works.
  state.spawnFails = false; state.commands = [signed(keys, 'update_app', want)];
  await m.agent.pollOnce();
  assert.deepEqual(m.events, ['install', 'quit']);
});

test('tierb: the installer is hashed again right before it runs; a swapped file is not run', async () => {
  const keys = makeKeys();
  const bytes = crypto.randomBytes(2000);
  const want = { sha256: crypto.createHash('sha256').update(bytes).digest('hex'), size: bytes.length, version: '3.1.1' };
  const state = { keyB64: keys.b64, package: bytes, commands: [signed(keys, 'update_app', want)] };
  const m = mk(state, { spawnInstaller: async () => { throw new Error('must not run'); } });
  const origAck = m.up.request;
  m.up.request = async (o) => { const r = await origAck(o);   // swap the file while the acknowledgement is on its way
    if (o.path.endsWith('/ack')) { const root = path.join(m.dataDir, 'updates'); for (const d of fs.readdirSync(root)) for (const f of fs.readdirSync(path.join(root, d))) fs.appendFileSync(path.join(root, d, f), 'x'); }
    return r; };
  m.agent.onManifest(manifest(ON));
  await m.agent.pollOnce();
  assert.match(m.agent.stats().last_update_error, /package changed before install/);
  assert.deepEqual(fs.readdirSync(path.join(m.dataDir, 'updates')), []);
});

test('tierb: a redirect or a slow-drip answer from the package URL is refused and leaves nothing behind', async () => {
  const keys = makeKeys();
  const bytes = crypto.randomBytes(3000);
  const want = { sha256: crypto.createHash('sha256').update(bytes).digest('hex'), size: bytes.length, version: '3.1.1' };
  const state = { keyB64: keys.b64, package: bytes, commands: [signed(keys, 'update_app', want)] };
  const m = mk(state);
  const base = m.up.request;
  m.up.request = async (o) => o.path.endsWith('/package')
    ? Object.assign(Readable.from([Buffer.from('')]), { statusCode: 302, headers: { location: 'http://evil.test/x.exe' } }) : base(o);
  m.agent.onManifest(manifest(ON));
  await m.agent.pollOnce();
  assert.equal(acks(m.up)[0].result, 'download refused (302)');
  assert.deepEqual(fs.readdirSync(path.join(m.dataDir, 'updates')), []);
  // slow drip with a tiny deadline
  state.commands = [signed(keys, 'update_app', want)];
  m.up.request = async (o) => o.path.endsWith('/package')
    ? Object.assign(Readable.from((async function* () { yield bytes.subarray(0, 10); await new Promise((r) => setTimeout(r, 1200)); yield bytes.subarray(10); })()),
      { statusCode: 200, headers: {} }) : base(o);
  const m2 = mk(state, { downloadDeadlineMs: 150, up: m.up });
  m2.agent.onManifest(manifest(ON));
  await m2.agent.pollOnce();
  assert.equal(acks(m2.up).pop().result, 'package download timed out');
  assert.deepEqual(m2.events, []);
});

test('tierb: reboot failure is reported and does not use up the cooldown; the cooldown ends with an advancing clock', async () => {
  const keys = makeKeys();
  const clock = { t: NOW };
  const state = { keyB64: keys.b64, rebootFails: true, commands: [signed(keys, 'reboot_device', {})] };
  const m = mk(state, { clock });
  m.agent.onManifest(manifest(ON));
  await m.agent.pollOnce();
  assert.equal(acks(m.up)[0].state + ':' + acks(m.up)[0].result, 'failed:reboot failed (shutdown.exe)');
  state.rebootFails = false; state.commands = [signed(keys, 'reboot_device', {})];
  await m.agent.pollOnce();
  assert.equal(acks(m.up)[1].result, 'rebooting');
  clock.t = NOW + 5 * 60 * 1000; state.commands = [signed(keys, 'reboot_device', {}, { body: { exp_ms: clock.t + 60000 } })];
  await m.agent.pollOnce();
  assert.match(acks(m.up)[2].result, /cooldown/);
  clock.t = NOW + 11 * 60 * 1000; state.commands = [signed(keys, 'reboot_device', {}, { body: { exp_ms: clock.t + 60000 } })];
  await m.agent.pollOnce();
  assert.equal(acks(m.up)[3].result, 'rebooting');
});

test('tierb: a server whose clock goes backwards is refused (old signed commands cannot be revived)', async () => {
  const keys = makeKeys();
  const state = { keyB64: keys.b64, commands: [] };
  const m = mk(state);
  m.agent.onManifest(manifest(ON));
  let serverTime = NOW + 5 * 60 * 1000;
  const base = m.up.request;
  m.up.request = async (o) => { const r = await base(o);
    if (o.method === 'GET' && o.path.endsWith('/commands')) { const body = []; for await (const c of r) body.push(c);
      const j = JSON.parse(Buffer.concat(body)); j.server_time_ms = serverTime;
      return Object.assign(Readable.from([Buffer.from(JSON.stringify(j))]), { statusCode: 200, headers: {} }); }
    return r; };
  await m.agent.pollOnce();                       // pins the key and records the time
  assert.equal(store.load(path.join(m.dataDir, 'agent-state.json')).servers[ORIGIN].timeMs, serverTime);
  // An old command (expired in real time) looks valid under a rewound clock: refused anyway.
  state.commands = [signed(keys, 'reboot_device', {}, { body: { exp_ms: NOW + 1000 } })];
  serverTime = NOW;
  await m.agent.pollOnce();
  assert.match(acks(m.up)[0].result, /^bad_time/);
  assert.deepEqual(m.events, []);
  // The mark survives an app restart.
  const m2 = mk(state, { dataDir: m.dataDir });
  m2.agent.onManifest(manifest(ON));
  state.commands = [signed(keys, 'reboot_device', {}, { body: { exp_ms: NOW + 1000 } })];
  await m2.agent.pollOnce();
  assert.deepEqual(m2.events, []);
});

test('tierb: a command that arrives before the display id is known keeps its id; forgetting clears pin, seq and time together', async () => {
  const keys = makeKeys();
  const c = signed(keys, 'set_startup', { autostart: true });
  const state = { keyB64: keys.b64, commands: [c] };
  const m = mk(state);
  m.agent.settings.startup = true;                 // switches known, but no manifest with the display id yet
  await m.agent.pollOnce();
  assert.equal(acks(m.up).length, 0);
  assert.deepEqual(m.agent.ledger, []);
  m.agent.onManifest(manifest(ON));
  state.commands = [c];
  await m.agent.pollOnce();
  assert.deepEqual(m.events, ['autostart true']);
  const file = path.join(m.dataDir, 'agent-state.json');
  assert.ok(store.load(file).servers[ORIGIN].seq > 0 && store.load(file).servers[ORIGIN].timeMs > 0);
  m.agent.forgetKeys();
  assert.deepEqual(Object.keys(store.load(file).servers), []);
  assert.equal(m.agent.stats().command_key_fingerprint, undefined);
});

test('tierb: update_app with the wrong sha256 or size is refused and nothing is kept or run', async () => {
  const keys = makeKeys();
  const bytes = crypto.randomBytes(3000);
  const good = { sha256: crypto.createHash('sha256').update(bytes).digest('hex'), size: bytes.length, version: '3.1.1' };
  for (const [bad, expect] of [[{ ...good, sha256: 'f'.repeat(64) }, 'package sha256 mismatch'], [{ ...good, size: 2999 }, 'package size mismatch'],
    [{ ...good, size: 3001 }, 'package size mismatch']]) {
    const state = { keyB64: keys.b64, package: bytes, commands: [signed(keys, 'update_app', bad)] };
    const { agent, up, events, dataDir } = mk(state);
    agent.onManifest(manifest(ON));
    await agent.pollOnce();
    assert.deepEqual(acks(up).map((a) => a.state + ':' + a.result), ['failed:' + expect]);
    assert.deepEqual(events, []);
    assert.deepEqual(fs.existsSync(path.join(dataDir, 'updates')) ? fs.readdirSync(path.join(dataDir, 'updates')) : [], []);
  }
  const none = { keyB64: keys.b64, package: null, commands: [signed(keys, 'update_app', good)] };
  const m = mk(none);
  m.agent.onManifest(manifest(ON));
  await m.agent.pollOnce();
  assert.equal(acks(m.up)[0].result, 'download refused (404)');
});

test('tierb: Tier A still works and logs never carry the key or signature', async () => {
  const keys = makeKeys();
  const c = signed(keys, 'reboot_device', {});
  const state = { keyB64: keys.b64, commands: [c, { id: newId(), action: 'restart_player', params: {}, expires_in_s: 60 }] };
  let relaunched = 0;
  const m = mk(state, { relaunch: () => relaunched++ });
  m.agent.onManifest(manifest({ restart: true, reboot: true }));
  await m.agent.pollOnce();
  await new Promise((r) => setTimeout(r, 700));
  assert.equal(relaunched, 1);
  assert.deepEqual(acks(m.up).map((a) => a.state), ['done', 'done']);
  assert.ok(!JSON.stringify(acks(m.up)).includes(c.signed.sig));
});
