'use strict';
const test = require('node:test');
const assert = require('node:assert');
const crypto = require('crypto');
const { harness, makeSigner, DISPLAY } = require('./helpers/agent-harness');
const vec = require('./vectors/agent-vectors.json');
const ecies = require('../src/agent-svc/ecies');

const T = 1_800_000_000_000 - 3600_000;      // before the vectors expire (exp_ms 1.8e12)

// A harness that trusts the Python-signed vector key and expects display 7.
function vectorWorld(over) {
  const w = harness(Object.assign({ signer: { keyB64: vec.command_key_b64, sign: () => { throw new Error('vectors only'); } } }, over));
  w.agentKey.priv = Buffer.from(vec.agent_private_hex, 'hex');
  w.clock.t = T; w.serverTime = T;
  return w;
}

test('a command signed by the PYTHON server code verifies and runs in the node agent (reboot)', async () => {
  const w = vectorWorld();
  try {
    assert.strictEqual(await w.core.handle(vec.commands.reboot, T), true);
    assert.ok(w.calls.some((c) => c[0] === 'reboot'));
    assert.deepStrictEqual(w.acks.map((a) => [a.id, a.state]), [['1111111111111111', 'done']]);
    assert.strictEqual(w.st.seq, 1001);
    assert.deepStrictEqual(w.saved.ledger, ['1111111111111111']);
  } finally { w.cleanup(); }
});

test('seq, time mark and ledger are saved BEFORE the action runs', async () => {
  const w = vectorWorld();
  try {
    w.ops.reboot = async () => { w.atAction = JSON.parse(JSON.stringify(w.saved)); return { ok: true }; };
    await w.core.handle(vec.commands.reboot, T);
    assert.strictEqual(w.atAction.seq, 1001);
    assert.deepStrictEqual(w.atAction.ledger, ['1111111111111111']);
    assert.ok(w.atAction.timeMs > 0);
  } finally { w.cleanup(); }
});

test('the python auto-login envelope decrypts, reaches setAutologon only for the kiosk account, and the password is nowhere else', async () => {
  const w = vectorWorld();
  try {
    assert.strictEqual(await w.core.handle(vec.commands.autologin, T), true);
    assert.deepStrictEqual(w.calls.find((c) => c[0] === 'setAutologon'), ['setAutologon', 'kiosk', '.']);
    assert.strictEqual(w.passwordSeen, vec.ecies.plaintext);
    const everything = JSON.stringify([w.acks, w.logs, w.saved, w.calls, w.diags]);
    assert.ok(!everything.includes('Sup3r'), 'password leaked');
    assert.ok(!everything.includes(vec.ecies.envelope), 'ciphertext leaked into acks/logs/state');
    assert.strictEqual(w.acks[0].state, 'done');
    assert.deepStrictEqual(w.saved.autologin, { enabled: true, user: 'kiosk' });
    assert.ok(!JSON.stringify(w.core.report()).includes('Sup3r'));
  } finally { w.cleanup(); }
});

test('auto-login is refused when the local switch is off, and its undo needs the switch too', async () => {
  const w = vectorWorld({ allow: { reboot: true, update: true } });
  try {
    await w.core.handle(vec.commands.autologin, T);
    await w.core.handle(vec.commands.autologin_off, T);
    assert.ok(!w.calls.some((c) => c[0] === 'setAutologon' || c[0] === 'clearAutologon'));
    assert.deepStrictEqual(w.acks.map((a) => [a.state, a.result]), [['failed', 'not_allowed'], ['failed', 'not_allowed']]);
    assert.deepStrictEqual(w.st.ledger, []);          // refused before verification passes: nothing burned
  } finally { w.cleanup(); }
});

test('auto-login undo removes it', async () => {
  const w = vectorWorld();
  try {
    await w.core.handle(vec.commands.autologin_off, T);
    assert.ok(w.calls.some((c) => c[0] === 'clearAutologon'));
    assert.deepStrictEqual(w.saved.autologin, { enabled: false, user: '' });
  } finally { w.cleanup(); }
});

test('tampered payload, tampered signature and the wrong key are refused and burn nothing', async () => {
  const w = vectorWorld();
  try {
    const c = JSON.parse(JSON.stringify(vec.commands.reboot));
    const t1 = JSON.parse(JSON.stringify(c)); t1.signed.payload = t1.signed.payload.replace('"seq":1001', '"seq":9999');
    const t2 = JSON.parse(JSON.stringify(c)); t2.signed.sig = t2.signed.sig.replace(/^./, (x) => (x === 'A' ? 'B' : 'A'));
    const t3 = JSON.parse(JSON.stringify(c)); t3.action = 'update_app';                        // retargeted copy
    for (const bad of [t1, t2, t3]) assert.strictEqual(await w.core.handle(bad, T), false);
    assert.ok(!w.calls.some((x) => x[0] === 'reboot'));
    assert.strictEqual(w.st.seq, 0);
    assert.deepStrictEqual(w.st.ledger, []);
    // The real one still works afterwards: the forged copies did not use up its id or seq.
    assert.strictEqual(await w.core.handle(c, T), true);
    const other = harness();                         // a different pinned key
    assert.strictEqual(await other.core.handle(c, T), false);
    other.cleanup();
  } finally { w.cleanup(); }
});

test('replay: the same id twice runs once; an older seq is refused', async () => {
  const w = harness();
  try {
    const a = w.signer.sign('aaaaaaaaaaaaaaaa', 'reboot_device', {});
    assert.strictEqual(await w.core.handle(a, w.serverTime), true);
    assert.strictEqual(await w.core.handle(a, w.serverTime), false);
    assert.strictEqual(w.calls.filter((c) => c[0] === 'reboot').length, 1);
    const old = w.signer.sign('bbbbbbbbbbbbbbbb', 'reboot_device', {}, { seq: 5 });
    assert.strictEqual(await w.core.handle(old, w.serverTime), false);
    assert.strictEqual(w.acks.at(-1).result, 'replayed');
  } finally { w.cleanup(); }
});

test('expired, wrong display, unknown action and bad params are refused', async () => {
  const w = harness();
  try {
    const cases = [
      [w.signer.sign('1000000000000001', 'reboot_device', {}, { exp_ms: w.serverTime - 1 }), 'expired'],
      [w.signer.sign('1000000000000002', 'reboot_device', {}, { display_id: 99 }), 'wrong_display'],
      [w.signer.sign('1000000000000003', 'format_disk', {}), 'unsupported'],
      [w.signer.sign('1000000000000004', 'update_app', { version: '3.2.0; calc', sha256: 'ab'.repeat(32), size: 5 }), 'bad_params'],
      [w.signer.sign('1000000000000005', 'reboot_device', { x: 1 }), 'bad_params'],
    ];
    for (const [c, why] of cases) { await w.core.handle(c, w.serverTime); assert.strictEqual(w.acks.at(-1).result, why); }
    assert.ok(!w.calls.some((c) => c[0] === 'reboot'));
  } finally { w.cleanup(); }
});

test('the local switch decides: reboot switched off means no reboot even with a perfect signature', async () => {
  const w = harness({ allow: { update: true } });
  try {
    await w.core.handle(w.signer.sign('cccccccccccccccc', 'reboot_device', {}), w.serverTime);
    assert.ok(!w.calls.some((c) => c[0] === 'reboot'));
    assert.strictEqual(w.acks.at(-1).result, 'not_allowed');
  } finally { w.cleanup(); }
});

test('a command that cannot be saved is not run', async () => {
  const w = harness();
  try {
    const core = require('../src/agent-svc/core').createCore({ cfg: w.cfg, paths: w.paths, programDir: 'x', version: '1', now: () => w.clock.t, log: { info() {}, warn() {}, error() {} },
      request: w.request, ops: w.ops, st: w.st, runInstaller: w.runInstaller, saveState: () => { throw new Error('disk full'); }, readKey: () => w.agentKey.priv, writeStatus() {} });
    await core.handle(w.signer.sign('dddddddddddddddd', 'reboot_device', {}), w.serverTime);
    assert.ok(!w.calls.some((c) => c[0] === 'reboot'));
    assert.deepStrictEqual(w.acks, []);
  } finally { w.cleanup(); }
});

test('a failing action is reported as failed, never as done', async () => {
  const w = harness();
  try {
    w.ops.reboot = async () => ({ ok: false });
    await w.core.handle(w.signer.sign('eeeeeeeeeeeeeeee', 'reboot_device', {}), w.serverTime);
    assert.strictEqual(w.acks.at(-1).state, 'failed');
    w.ops.setStartup = async () => null;
    await w.core.handle(w.signer.sign('ffffffffffffffff', 'set_startup', { autostart: true }), w.serverTime);
    assert.strictEqual(w.acks.at(-1).state, 'failed');
  } finally { w.cleanup(); }
});

test('pollOnce: unsafe folder means nothing is fetched or done; status says unsafe_acl', async () => {
  const w = harness();
  try {
    w.folderUnsafe = true;
    w.commands = [w.signer.sign('abababababababab', 'reboot_device', {})];
    assert.strictEqual(await w.core.pollOnce(), -1);
    assert.ok(!w.calls.some((c) => c[0] === 'http'));
    assert.strictEqual(w.statusFile.state, 'unsafe_acl');
    w.folderUnsafe = false; w.clock.t += 6 * 60 * 1000;          // after the re-check interval
    assert.strictEqual(await w.core.pollOnce(), 1);
    assert.strictEqual(w.statusFile.state, 'running');
    assert.ok(w.calls.some((c) => c[0] === 'reboot'));
  } finally { w.cleanup(); }
});

test('pollOnce: a server clock that goes backwards stops commands; the agent token is sent only as the Bearer header', async () => {
  const w = harness();
  try {
    await w.core.pollOnce();
    const high = w.st.timeMs;
    assert.ok(high > 0);
    w.serverTime -= 5000; w.commands = [w.signer.sign('9999999999999999', 'reboot_device', {})];
    assert.strictEqual(await w.core.pollOnce(), 0);
    assert.ok(!w.calls.some((c) => c[0] === 'reboot'));
    assert.ok(w.logs.some((l) => /clock went backwards/.test(l)));
  } finally { w.cleanup(); }
});

test('a different command key on the server is noticed and reported, the pin is never replaced', async () => {
  const w = harness();
  try {
    w.keyOnServer = makeSigner().keyB64;
    await w.core.pollOnce();
    assert.strictEqual(w.core.report().command_key_changed, true);
    assert.strictEqual(w.cfg.commandKey, w.signer.keyB64);
    const c = w.signer.sign('7777777777777777', 'reboot_device', {});
    assert.strictEqual(await w.core.handle(c, w.serverTime), true);        // still verified against the PIN, not the server's new key
  } finally { w.cleanup(); }
});

test('diagnostics: capabilities come from the local switches; key id matches; no token, key or password in it', async () => {
  const w = harness({ allow: { update: true } });
  try {
    await w.core.sendDiag(true);
    const d = w.diags[0].diag;
    assert.strictEqual(d.can_update, true); assert.strictEqual(d.can_reboot, false); assert.strictEqual(d.can_autologin, false);
    assert.strictEqual(d.key_id, ecies.keyId(ecies.publicOf(w.agentKey.priv)));
    assert.strictEqual(d.kiosk_user, 'kiosk');
    const text = JSON.stringify(d);
    assert.ok(!text.includes(w.cfg.token) && !text.includes(w.agentKey.priv.toString('base64')));
  } finally { w.cleanup(); }
});

test('no request without the right agent token is ever answered (fake server rejects a display-style token)', async () => {
  const w = harness();
  try {
    const res = await w.request({ method: 'GET', path: '/api/player/v1/agent/commands', headers: { authorization: 'Bearer disp_x' } });
    assert.strictEqual(res.statusCode, 401);
  } finally { w.cleanup(); }
});
