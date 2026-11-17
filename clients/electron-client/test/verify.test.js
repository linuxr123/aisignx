'use strict';
// Tier B (signed commands): the pure rules in src/agent/{verify,store,system,package}.js.
const test = require('node:test');
const assert = require('node:assert/strict');
const crypto = require('crypto');
const fs = require('fs');
const os = require('os');
const path = require('path');
const { Readable } = require('stream');
const V = require('../src/agent/verify');
const store = require('../src/agent/store');
const sys = require('../src/agent/system');
const pkg = require('../src/agent/package');

const dirs = [];
const tmp = () => { const d = fs.mkdtempSync(path.join(os.tmpdir(), 'aisignx-verify-')); dirs.push(d); return d; };
test.after(() => { for (const d of dirs) fs.rmSync(d, { recursive: true, force: true }); });

// A key pair like the server's: raw 32-byte public key, signature over the payload text.
function makeKeys() {
  const { publicKey, privateKey } = crypto.generateKeyPairSync('ed25519');
  return { priv: privateKey, raw: publicKey.export({ format: 'der', type: 'spki' }).subarray(-32) };
}
const K = makeKeys();
const ID = '0123456789abcdef';
const NOW = 1_763_300_000_000;

function body(over) {
  return Object.assign({ v: 1, display_id: 7, request_id: ID, action: 'reboot_device', params: {}, seq: 412, exp_ms: NOW + 60000 }, over);
}
function sorted(o) { return JSON.stringify(Object.fromEntries(Object.entries(o).sort(([a], [b]) => (a < b ? -1 : 1)))); }
function sign(payloadText, priv) { return crypto.sign(null, Buffer.from(payloadText, 'utf8'), (priv || K.priv)).toString('base64'); }
function cmd(over, key) {
  const b = body(over);
  const text = sorted(b);
  return { id: b.request_id, action: b.action, params: b.params, expires_in_s: 60, signed: { payload: text, sig: sign(text, key) } };
}
const ALL = { reboot: true, startup: true, update: true };
const ctx = (over) => Object.assign({ key: K.raw, displayId: 7, serverTimeMs: NOW, lastSeq: 411, allowed: ALL }, over);
const reason = (c, x) => V.verifyCommand(c, ctx(x)).reason;

test('verify: a valid command passes and returns the signed payload', () => {
  const r = V.verifyCommand(cmd(), ctx());
  assert.equal(r.ok, true);
  assert.equal(r.payload.seq, 412);
  assert.equal(r.payload.action, 'reboot_device');
});

test('verify: only payload.params counts, never the unsigned params', () => {
  const c = cmd({ action: 'set_startup', params: { autostart: true } });
  c.params = { autostart: false, mode: 'shell' };
  const r = V.verifyCommand(c, ctx());
  assert.equal(r.ok, true);
  assert.deepEqual(r.payload.params, { autostart: true });
});

test('verify: tampered payload, tampered signature, missing or malformed signed member', () => {
  const c = cmd();
  const t = JSON.parse(c.signed.payload); t.display_id = 8;
  assert.equal(reason({ ...c, signed: { ...c.signed, payload: sorted(t) } }), 'bad_signature');
  const sig = Buffer.from(c.signed.sig, 'base64'); sig[0] ^= 1;
  assert.equal(reason({ ...c, signed: { ...c.signed, sig: sig.toString('base64') } }), 'bad_signature');
  assert.equal(reason({ id: ID, action: 'reboot_device', params: {} }), 'bad_signature');
  assert.equal(reason({ ...c, signed: { payload: 5, sig: 5 } }), 'bad_signature');
  assert.equal(reason({ ...c, signed: { ...c.signed, sig: 'not base64!' } }), 'bad_signature');
  assert.equal(reason({ ...c, signed: { ...c.signed, sig: Buffer.alloc(10).toString('base64') } }), 'bad_signature');
  assert.equal(reason(c, { key: null }), 'bad_signature');
  assert.equal(reason(c, { key: Buffer.alloc(31) }), 'bad_signature');
});

test('verify: signed by another key', () => {
  const other = makeKeys();
  assert.equal(reason(cmd({}, other.priv)), 'bad_signature');
  assert.equal(reason(cmd(), { key: other.raw }), 'bad_signature');
});

test('verify: wrong display, wrong version', () => {
  assert.equal(reason(cmd({ display_id: 8 })), 'wrong_display');
  assert.equal(reason(cmd(), { displayId: null }), 'wrong_display');
  assert.equal(reason(cmd({ v: 2 })), 'unsupported');
});

test('verify: expiry is judged against the server time, not the device clock', () => {
  assert.equal(reason(cmd({ exp_ms: NOW })), 'expired');
  assert.equal(reason(cmd({ exp_ms: NOW - 1 })), 'expired');
  assert.equal(V.verifyCommand(cmd({ exp_ms: NOW + 1 }), ctx()).ok, true);
  assert.equal(reason(cmd(), { serverTimeMs: undefined }), 'expired');
  const real = Date.now;
  Date.now = () => NOW + 10 * 3600 * 1000; // a device clock that is wrong changes nothing
  try { assert.equal(V.verifyCommand(cmd(), ctx()).ok, true); } finally { Date.now = real; }
});

test('verify: seq must be higher than the last accepted one', () => {
  assert.equal(reason(cmd({ seq: 412 }), { lastSeq: 412 }), 'replayed');
  assert.equal(reason(cmd({ seq: 400 }), { lastSeq: 412 }), 'replayed');
  assert.equal(reason(cmd({ seq: 0 }), { lastSeq: 0 }), 'replayed');
  assert.equal(V.verifyCommand(cmd({ seq: 413 }), ctx({ lastSeq: 412 })).ok, true);
  assert.equal(V.verifyCommand(cmd({ seq: 1 }), ctx({ lastSeq: undefined })).ok, true);
});

test('verify: request_id and action must equal the unsigned fields', () => {
  assert.equal(reason({ ...cmd(), id: 'fedcba9876543210' }), 'bad_signature');
  assert.equal(reason({ ...cmd(), action: 'update_app' }), 'bad_signature');
  assert.equal(reason({ ...cmd(), action: undefined }), 'bad_signature');
});

test('verify: unknown action and typed params', () => {
  assert.equal(reason(cmd({ action: 'format_disk' })), 'unsupported');
  assert.equal(reason(cmd({ action: 'restart_player' })), 'unsupported');
  const bad = [
    ['reboot_device', { now: true }], ['reboot_device', []], ['reboot_device', null],
    ['set_startup', {}], ['set_startup', { autostart: 'yes' }], ['set_startup', { autostart: 1 }], ['set_startup', { mode: 'kiosk' }],
    ['set_startup', { mode: 'shell; calc' }], ['set_startup', { autostart: true, extra: 1 }],
    ['update_app', {}], ['update_app', { version: '3.1.1', sha256: 'a'.repeat(64) }],
    ['update_app', { version: '3.1.1', sha256: 'A'.repeat(64), size: 5 }], ['update_app', { version: '3.1.1', sha256: 'a'.repeat(63), size: 5 }],
    ['update_app', { version: '3.1.1 & calc', sha256: 'a'.repeat(64), size: 5 }], ['update_app', { version: '', sha256: 'a'.repeat(64), size: 5 }],
    ['update_app', { version: '3.1.1', sha256: 'a'.repeat(64), size: '5' }], ['update_app', { version: '3.1.1', sha256: 'a'.repeat(64), size: 0 }],
    ['update_app', { version: '3.1.1', sha256: 'a'.repeat(64), size: 1.5 }], ['update_app', { version: '3.1.1', sha256: 'a'.repeat(64), size: 2 ** 40 }],
    ['update_app', { version: '3.1.1', sha256: 'a'.repeat(64), size: 5, url: 'http://x' }],
  ];
  for (const [action, params] of bad) assert.equal(reason(cmd({ action, params })), 'bad_params', action + ' ' + JSON.stringify(params));
  const good = [['set_startup', { autostart: false }], ['set_startup', { mode: 'app' }], ['set_startup', { autostart: true, mode: 'shell' }],
    ['update_app', { version: '3.1.1', sha256: 'a'.repeat(64), size: 120158154 }]];
  for (const [action, params] of good) assert.equal(V.verifyCommand(cmd({ action, params }), ctx()).ok, true, action);
});

test('verify: the local switch must be on', () => {
  assert.equal(reason(cmd(), { allowed: { ...ALL, reboot: false } }), 'not_allowed');
  assert.equal(reason(cmd(), { allowed: {} }), 'not_allowed');
  assert.equal(reason(cmd(), { allowed: null }), 'not_allowed');
  assert.equal(reason(cmd({ action: 'set_startup', params: { autostart: true } }), { allowed: { ...ALL, startup: false } }), 'not_allowed');
  assert.equal(reason(cmd({ action: 'update_app', params: { version: '1', sha256: 'a'.repeat(64), size: 3 } }), { allowed: { ...ALL, update: 'yes' } }), 'not_allowed');
});

test('key pinning: first key is pinned, the same key passes, a different key is a change', () => {
  const a = K.raw.toString('base64'), b = makeKeys().raw.toString('base64');
  const first = V.evaluateKey(null, a);
  assert.equal(first.state, 'pin'); assert.deepEqual(first.key, K.raw);
  assert.equal(V.evaluateKey(a, a).state, 'same');
  assert.equal(V.evaluateKey(a, b).state, 'changed');
  for (const junk of [undefined, null, '', 'abc', Buffer.alloc(31).toString('base64'), a + 'A', 7]) assert.equal(V.evaluateKey(a, junk).state, 'invalid');
});

test('cooldown: active for 10 minutes, never forever when the clock jumps back', () => {
  const t = 5_000_000_000;
  assert.equal(V.cooldownActive(0, t), false);
  assert.equal(V.cooldownActive(t - 1000, t), true);
  assert.equal(V.cooldownActive(t - V.COOLDOWN_MS + 1, t), true);
  assert.equal(V.cooldownActive(t - V.COOLDOWN_MS, t), false);
  assert.equal(V.cooldownActive(t + 5000, t), false);
  assert.equal(V.cooldownActive('x', t), false);
});

test('store: round trip, atomic file, junk dropped', () => {
  const f = path.join(tmp(), 'agent-state.json');
  assert.deepEqual(Object.keys(store.load(f).servers), []);
  const st = store.load(f);
  st.servers['http://s:5000'] = { key: K.raw.toString('base64'), seq: 9 };
  st.rebootAt = 123; st.updateAt = 456;
  store.save(f, st);
  assert.ok(!fs.existsSync(f + '.tmp'));
  const back = store.load(f);
  assert.deepEqual(back.servers['http://s:5000'], { key: K.raw.toString('base64'), seq: 9, timeMs: 0 });
  assert.equal(back.rebootAt, 123); assert.equal(back.updateAt, 456);
  const junk = store.sanitize({ servers: { a: { key: 'bad', seq: 3 }, b: { key: K.raw.toString('base64'), seq: -4 } }, rebootAt: 'x', updateAt: -1 });
  assert.deepEqual(Object.keys(junk.servers), ['b']);
  assert.equal(junk.servers.b.seq, 0); assert.equal(junk.rebootAt, 0); assert.equal(junk.updateAt, 0);
  fs.writeFileSync(f, '{not json');
  assert.deepEqual(Object.keys(store.load(f).servers), []);
});

test('update package: size and sha256 must equal the signed values', () => {
  const want = { size: 5, sha256: crypto.createHash('sha256').update('hello').digest('hex') };
  assert.equal(V.checkPackage(want, 5, want.sha256).ok, true);
  assert.deepEqual(V.checkPackage(want, 6, want.sha256), { ok: false, reason: 'size mismatch' });
  assert.deepEqual(V.checkPackage(want, 5, 'f'.repeat(64)), { ok: false, reason: 'sha256 mismatch' });
  assert.equal(V.checkPackage(want, 5, undefined).ok, false);
});

test('savePackage: keeps only a package that matches; deletes the rest', async () => {
  const dir = tmp();
  const data = Buffer.from('hello');
  const want = { size: 5, sha256: crypto.createHash('sha256').update(data).digest('hex') };
  const chunks = (b) => Readable.from([b.subarray(0, 2), b.subarray(2)]);
  const f = path.join(dir, 'u', 'a.exe');
  assert.deepEqual(await pkg.savePackage(chunks(data), f, want, '5'), { ok: true, size: 5 });
  assert.equal(fs.readFileSync(f, 'utf8'), 'hello');
  const f2 = path.join(dir, 'u', 'b.exe');
  assert.deepEqual(await pkg.savePackage(chunks(Buffer.from('hellp')), f2, want), { ok: false, reason: 'sha256 mismatch' });
  assert.ok(!fs.existsSync(f2));
  assert.deepEqual(await pkg.savePackage(chunks(Buffer.from('hell')), f2, want), { ok: false, reason: 'size mismatch' });
  assert.ok(!fs.existsSync(f2));
  assert.deepEqual(await pkg.savePackage(chunks(Buffer.from('hello!')), f2, want), { ok: false, reason: 'size mismatch' });
  assert.ok(!fs.existsSync(f2));
  assert.deepEqual(await pkg.savePackage(chunks(data), f2, want, '999'), { ok: false, reason: 'size mismatch' });
  const broken = Readable.from((async function* () { yield Buffer.from('he'); throw new Error('reset'); })());
  assert.deepEqual(await pkg.savePackage(broken, f2, want), { ok: false, reason: 'download failed' });
  assert.ok(!fs.existsSync(f2));
  assert.equal(pkg.cleanupUpdates(path.join(dir, 'u')), 1);
  assert.deepEqual(fs.readdirSync(path.join(dir, 'u')), []);
});

test('system: fixed command lines, own-shell detection, crash loop rule', async () => {
  const launcher = 'C:\\Users\\kiosk\\AppData\\Local\\AiSignX\\agent\\shell-launcher.ps1';
  const value = sys.shellValue(launcher, 'C:\\Windows');
  assert.equal(value, '"C:\\Windows\\System32\\WindowsPowerShell\\v1.0\\powershell.exe" -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "' + launcher + '"');
  assert.equal(sys.isOurShell(value), true);
  for (const other of ['explorer.exe', '', null, undefined, 'C:\\tools\\myshell.exe', 'powershell.exe -File other.ps1']) assert.equal(sys.isOurShell(other), false);
  // The PowerShell snippets are constants: nothing is interpolated from outside.
  for (const s of [sys.READ_SHELL, sys.WRITE_SHELL, sys.CLEAR_SHELL]) assert.ok(!/\$\{|%s|\bundefined\b/.test(s));
  assert.ok(sys.WRITE_SHELL.includes('$env:AISIGNX_SHELL'));
  // reboot: shutdown.exe /r /t 30 with a fixed comment.
  const calls = [];
  await sys.reboot(async (file, args) => { calls.push([file, args]); return { ok: true, out: '' }; });
  assert.deepEqual(calls, [['shutdown.exe', ['/r', '/t', '30', '/c', 'AiSignX Player: restart requested from the AiSignX server']]]);
  // crash loop: three quick exits in a row.
  assert.equal(sys.crashLoop([1000, 2000]), false);
  assert.equal(sys.crashLoop([1000, 2000, 3000]), true);
  assert.equal(sys.crashLoop([1000, 60000, 3000]), false);
  assert.equal(sys.crashLoop([90000, 1000, 2000, 3000]), true);
  assert.equal(sys.crashLoop([1000, 2000, 90000]), false);
});

test('system: clearing the shell leaves a foreign shell alone and needs a read-back', async () => {
  const calls = [];
  // A fake PowerShell with a registry value: READ returns it, CLEAR empties it (unless stuck).
  const fake = (cur, stuck) => { const reg = { v: cur }; return async (file, args) => {
    const script = args[args.length - 1]; calls.push(script);
    if (script === sys.CLEAR_SHELL && !stuck) reg.v = '';
    return { ok: true, out: script === sys.READ_SHELL ? reg.v : '' }; }; };
  assert.equal(await sys.clearShellMode(fake('explorer.exe')), false);
  assert.deepEqual(calls, [sys.READ_SHELL]);   // only the read
  calls.length = 0;
  assert.equal(await sys.clearShellMode(fake('')), true);
  assert.equal(calls.length, 1);
  calls.length = 0;
  const ours = sys.shellValue('C:\\x\\shell-launcher.ps1', 'C:\\Windows');
  assert.equal(await sys.clearShellMode(fake(ours)), true);
  assert.deepEqual(calls, [sys.READ_SHELL, sys.CLEAR_SHELL, sys.READ_SHELL]);
  // The command "worked" but the value is still there: not a success.
  assert.equal(await sys.clearShellMode(fake(ours, true)), false);
});

test('system: the PowerShell text keeps its backslashes (regression: JS escapes ate them)', () => {
  const key = 'HKCU:\\Software\\Microsoft\\Windows NT\\CurrentVersion\\Winlogon';
  assert.equal(sys.READ_SHELL, "$v=(Get-ItemProperty -LiteralPath '" + key + "' -Name Shell -ErrorAction SilentlyContinue).Shell; if($v){[Console]::Out.Write($v)}");
  assert.equal(sys.WRITE_SHELL, "$k='" + key + "'; if(-not (Test-Path -LiteralPath $k)){New-Item -Path $k -Force | Out-Null}; " +
    'Set-ItemProperty -LiteralPath $k -Name Shell -Value $env:AISIGNX_SHELL -Type String');
  assert.equal(sys.CLEAR_SHELL, "Remove-ItemProperty -LiteralPath '" + key + "' -Name Shell -ErrorAction Stop");
  assert.ok(key.split("\\").length === 6 && key.includes('Windows NT\\CurrentVersion'));
  // The SystemRoot fallback is a real Windows path too.
  assert.ok(sys.shellValue('C:\\a\\shell-launcher.ps1').startsWith('"C:\\Windows\\System32\\WindowsPowerShell\\v1.0\\powershell.exe"'));
  // A launcher path with a double quote is refused.
  assert.throws(() => sys.shellValue('C:\\a"b\\shell-launcher.ps1', 'C:\\Windows'), /bad launcher path/);
});

test('system: setting the shell fails unless the read-back equals the value', async () => {
  const agentDir = tmp(); const source = path.join(tmp(), 'l.ps1'); fs.writeFileSync(source, '# x');
  const want = sys.shellValue(path.join(agentDir, sys.LAUNCHER_NAME), process.env.SystemRoot);
  const mk = (stored, writeOk) => { const reg = { v: '' }; const calls = []; const r = async (f, args, opts) => {
    const script = args[args.length - 1]; calls.push(script);
    if (script === sys.WRITE_SHELL) { if (writeOk) reg.v = stored === undefined ? opts.env.AISIGNX_SHELL : stored; return { ok: writeOk, out: '' }; }
    return { ok: true, out: reg.v }; }; return { r, calls }; };
  const o = { source, agentDir, exe: 'C:\\app.exe', args: [] };
  let m = mk(undefined, true);
  assert.equal(await sys.setShellMode(o, m.r), true);
  assert.deepEqual(m.calls, [sys.READ_SHELL, sys.WRITE_SHELL, sys.READ_SHELL]);
  assert.equal(await sys.setShellMode(o, mk('something else', true).r), false);   // wrote, but read-back differs
  assert.equal(await sys.setShellMode(o, mk(undefined, false).r), false);
  assert.equal(await sys.setShellMode(o, async () => ({ ok: false, out: '' })), false);   // PowerShell cannot run
  assert.ok(want.includes(sys.LAUNCHER_NAME));
});

test('system: the registry READ really runs in PowerShell (read-only)', { skip: process.platform !== 'win32' }, async () => {
  // Same script text, HKLM instead of HKCU (always present), proves the path text is valid.
  const r = await sys.run('powershell.exe', ['-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass', '-Command', sys.READ_SHELL.replace('HKCU:', 'HKLM:')]);
  assert.equal(r.ok, true);
  assert.match(r.out.trim(), /explorer\.exe/i);
  // And the real HKCU read is well-formed (empty unless a shell is set).
  const v = await sys.readShell();
  assert.ok(v === '' || typeof v === 'string');
});

test('system: spawnInstaller rejects on a missing file or an early failure, resolves when it stays up', async () => {
  await assert.rejects(sys.spawnInstaller(path.join(tmp(), 'nope.exe'), [], 500), (e) => e.code === 'ENOENT');
  await assert.rejects(sys.spawnInstaller(process.execPath, ['-e', 'process.exit(3)'], 3000), /exited with 3/);
  await sys.spawnInstaller(process.execPath, ['-e', 'setTimeout(()=>{},700)'], 100);   // still running after the grace time
  await sys.spawnInstaller(process.execPath, ['-e', ''], 3000);                        // exits 0: fine
});

test('verify: fingerprint matches the server format and the server-time mark never goes down', () => {
  assert.equal(V.fingerprint(Buffer.alloc(32, 1)), crypto.createHash('sha256').update(Buffer.alloc(32, 1)).digest('hex').slice(0, 16).match(/.{4}/g).join(' '));
  assert.equal(V.timeBackwards(1000, 999), true);
  assert.equal(V.timeBackwards(1000, 1000), false);
  assert.equal(V.timeBackwards(0, 5), false);
  assert.equal(V.raisedMark(1000, 2000, 5000), 2000);
  assert.equal(V.raisedMark(1000, 500, 5000), 1000);
  assert.equal(V.raisedMark(1000, 5000 + V.MAX_AHEAD_MS + 1, 5000), 1000);   // absurdly far ahead: ignored
});

test('package: deadline stops a slow download, the exclusive open refuses an existing file, verifyFile re-hashes', async () => {
  const dir = tmp(); const data = crypto.randomBytes(100);
  const want = { size: 100, sha256: crypto.createHash('sha256').update(data).digest('hex') };
  const slow = Readable.from((async function* () { yield data.subarray(0, 10); await new Promise((r) => setTimeout(r, 1200)); yield data.subarray(10); })());
  const f = path.join(dir, 'a.exe');
  assert.deepEqual(await pkg.savePackage(slow, f, want, undefined, { deadlineMs: 100 }), { ok: false, reason: 'download timed out' });
  assert.ok(!fs.existsSync(f));
  const planted = path.join(dir, 'b.exe'); fs.writeFileSync(planted, 'planted');
  assert.deepEqual(await pkg.savePackage(Readable.from([data]), planted, want), { ok: false, reason: 'download failed' });
  assert.equal(fs.readFileSync(planted, 'utf8'), 'planted');   // not ours: left alone, not overwritten
  const good = path.join(dir, 'c.exe');
  assert.equal((await pkg.savePackage(Readable.from([data]), good, want)).ok, true);
  assert.equal((await pkg.verifyFile(good, want)).ok, true);
  fs.writeFileSync(good, Buffer.concat([data.subarray(0, 99), Buffer.from([data[99] ^ 1])]));
  assert.deepEqual(await pkg.verifyFile(good, want), { ok: false, reason: 'sha256 mismatch' });
  assert.deepEqual(await pkg.verifyFile(path.join(dir, 'missing.exe'), want), { ok: false, reason: 'file unreadable' });
});

test('launcher scripts: parse cleanly, no 24 h sleep, Explorer needs repeated misses, update marker is honoured', { skip: process.platform !== 'win32' }, async () => {
  const dir = path.join(__dirname, '..', 'resources');
  for (const f of ['shell-launcher.ps1', 'watchdog.ps1']) {
    const r = await sys.run('powershell.exe', ['-NoProfile', '-NonInteractive', '-Command',
      "$e=$null;[void][System.Management.Automation.Language.Parser]::ParseFile($env:AISIGNX_F,[ref]$null,[ref]$e);[Console]::Out.Write($e.Count)"],
      { env: Object.assign({}, process.env, { AISIGNX_F: path.join(dir, f) }) });
    assert.equal(r.out, '0', f + ' has parse errors');
  }
  const launcher = fs.readFileSync(path.join(dir, 'shell-launcher.ps1'), 'utf8');
  assert.ok(!/Start-Sleep\s+-Seconds\s+\d{4,}/.test(launcher));
  assert.ok(/\$misses -lt 12/.test(launcher));
  assert.ok(/'updating'/.test(launcher) && /'updating'/.test(fs.readFileSync(path.join(dir, 'watchdog.ps1'), 'utf8')));
});
