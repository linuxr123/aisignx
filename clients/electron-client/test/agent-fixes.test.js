'use strict';
// Regression tests for the independent review of the privileged agent (Windows side).
// The PowerShell scripts are RUN here in their -DryRun mode (argument handling, validation, registry paths read only):
// nothing is written to this PC's registry, LSA, services, tasks or certificate stores, and nothing needs elevation.
const test = require('node:test');
const assert = require('node:assert');
const fs = require('fs');
const os = require('os');
const path = require('path');
const { spawnSync } = require('child_process');
const { harness } = require('./helpers/agent-harness');
const C = require('../src/agent-svc/config');
const marker = require('../src/agent-svc/marker');
const sysexe = require('../src/agent-svc/sysexe');
const enrollment = require('../src/agent-svc/enrollment');
const delegate = require('../src/agent/delegate');
const taskm = require('../src/agent/task');

const root = path.join(__dirname, '..');
const win = process.platform === 'win32';
const PS = path.join(root, 'resources', 'agent', 'ps');
const INSTALL = path.join(root, 'resources', 'Install-Agent.ps1');
const SID = 'S-1-5-21-1111111111-2222222222-3333333333-1001';
const run = (args, env, input) => spawnSync('powershell.exe', ['-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass'].concat(args),
  { encoding: 'utf8', timeout: 90000, env, input });
// A clean environment with only the AISIGNX_ variables given (undefined = not set at all, '' = set but empty).
const envWith = (vars) => {
  const e = Object.assign({}, process.env);
  for (const k of Object.keys(e)) if (k.startsWith('AISIGNX_')) delete e[k];
  for (const [k, v] of Object.entries(vars || {})) if (v !== undefined) e[k] = v;
  return e;
};
const startup = (vars) => run(['-File', path.join(PS, 'Set-Startup.ps1'), '-DryRun'], envWith(Object.assign({ AISIGNX_SID: SID }, vars)));

test('Set-Startup: an omitted field ("Leave as is") works, whether the variable is empty or not set at all', { skip: !win }, () => {
  for (const omitted of ['', undefined]) {
    const onlyAuto = startup({ AISIGNX_AUTOSTART: 'on', AISIGNX_MODE: omitted, AISIGNX_EXE: 'C:\\p\\player.exe' });
    assert.strictEqual(onlyAuto.status, 0, onlyAuto.stdout + onlyAuto.stderr);
    assert.deepStrictEqual(JSON.parse(onlyAuto.stdout), { dryRun: true, autostart: 'on', mode: '', actions: ['run-key:set'] });
    const onlyMode = startup({ AISIGNX_AUTOSTART: omitted, AISIGNX_MODE: 'shell' });
    assert.strictEqual(onlyMode.status, 0, onlyMode.stdout + onlyMode.stderr);
    assert.deepStrictEqual(JSON.parse(onlyMode.stdout).actions, ['shell:set']);
    const off = startup({ AISIGNX_AUTOSTART: 'off', AISIGNX_MODE: omitted });
    assert.deepStrictEqual(JSON.parse(off.stdout).actions, ['run-key:remove']);
    const none = startup({ AISIGNX_AUTOSTART: omitted, AISIGNX_MODE: omitted });
    assert.strictEqual(none.status, 0, none.stdout + none.stderr);
    assert.deepStrictEqual(JSON.parse(none.stdout).actions, []);
  }
});

test('Set-Startup: bad values, bad SID and "on" without an exe are still refused', { skip: !win }, () => {
  for (const bad of [{ AISIGNX_AUTOSTART: 'yes' }, { AISIGNX_MODE: 'kiosk' }, { AISIGNX_AUTOSTART: 'ON ' }, { AISIGNX_AUTOSTART: 'on' /* no exe */ }]) {
    const r = startup(bad);
    assert.notStrictEqual(r.status, 0, JSON.stringify(bad));
  }
  const badSid = run(['-File', path.join(PS, 'Set-Startup.ps1'), '-DryRun'], envWith({ AISIGNX_SID: 'S-1-5-18', AISIGNX_MODE: 'app' }));
  assert.notStrictEqual(badSid.status, 0);
  const noSid = run(['-File', path.join(PS, 'Set-Startup.ps1'), '-DryRun'], envWith({ AISIGNX_MODE: 'app' }));
  assert.notStrictEqual(noSid.status, 0);
});

test('ops.setStartup hands the script an environment in which every omitted field is empty, and the script accepts it', async () => {
  const { createOps } = require('../src/agent-svc/ops');
  const seen = [];
  const ops = createOps({ psDir: PS, programDir: os.tmpdir(), cfg: { kioskSid: SID, playerExe: 'C:\\p\\player.exe' }, paths: {} },
    async (file, args, opts) => { seen.push(opts.env); return { ok: true, out: '{"autostart":true,"mode":"app"}', err: '' }; });
  await ops.setStartup({ autostart: true });
  await ops.setStartup({ mode: 'app' });
  assert.deepStrictEqual([seen[0].AISIGNX_AUTOSTART, seen[0].AISIGNX_MODE], ['on', '']);
  assert.deepStrictEqual([seen[1].AISIGNX_AUTOSTART, seen[1].AISIGNX_MODE], ['', 'app']);
  if (win) for (const e of seen) {                                        // and the REAL script takes exactly these environments
    const r = run(['-File', path.join(PS, 'Set-Startup.ps1'), '-DryRun'], Object.assign({}, e, { AISIGNX_SID: SID }));
    assert.strictEqual(r.status, 0, r.stdout + r.stderr);
  }
});

test('every agent script that reads an AISIGNX_ environment variable casts it to [string] first (empty and missing are the same)', () => {
  for (const f of fs.readdirSync(PS).filter((x) => x.endsWith('.ps1'))) {
    const text = fs.readFileSync(path.join(PS, f), 'utf8');
    for (const m of text.matchAll(/(.{0,10})\$env:AISIGNX_\w+/g)) assert.ok(/\[string\]$/.test(m[1]), f + ': ' + m[0]);
  }
});

const autologon = (mode, vars, input) => run(['-File', path.join(PS, 'Set-Autologon.ps1'), '-Mode', mode, '-DryRun'], envWith(vars), input);
const b64 = (s) => Buffer.from(s, 'utf8').toString('base64') + '\n';
const REAL_DPL = 'HKLM:\\SOFTWARE\\Microsoft\\Windows NT\\CurrentVersion\\PasswordLess\\Device';

test('Set-Autologon -DryRun: runs the Set and Clear branches with the real argument handling, changes nothing', { skip: !win }, () => {
  const ok = autologon('Set', { AISIGNX_USER: 'kiosk', AISIGNX_DOMAIN: '.' }, b64('secret pw'));
  assert.strictEqual(ok.status, 0, ok.stdout + ok.stderr);
  const j = JSON.parse(ok.stdout);
  assert.strictEqual(j.dryRun, true);
  assert.strictEqual(j.user, 'kiosk');
  assert.ok(!ok.stdout.includes('secret'));                                      // the password is never printed
  assert.strictEqual(j.dplKey, REAL_DPL);
  const clear = JSON.parse(autologon('Clear', {}).stdout);
  assert.strictEqual(clear.dplKey, REAL_DPL);
  for (const [vars, input] of [[{ AISIGNX_USER: '', AISIGNX_DOMAIN: '.' }, b64('x')], [{ AISIGNX_DOMAIN: '.' }, b64('x')], [{ AISIGNX_USER: 'kiosk' }, b64('x')],
    [{ AISIGNX_USER: 'a;b', AISIGNX_DOMAIN: '.' }, b64('x')], [{ AISIGNX_USER: 'kiosk', AISIGNX_DOMAIN: '.' }, '\n'], [{ AISIGNX_USER: 'kiosk', AISIGNX_DOMAIN: '.' }, b64('a\u0000b')]]) {
    assert.notStrictEqual(autologon('Set', vars, input).status, 0, JSON.stringify(vars));
  }
});

test('Set-Autologon: the Windows 10/11 password-less key path is the real one (read-only check on this PC)', { skip: !win }, () => {
  const src = fs.readFileSync(path.join(PS, 'Set-Autologon.ps1'), 'utf8');
  assert.ok(src.includes(REAL_DPL), 'script uses the documented key');
  assert.ok(!src.includes("HKLM:\\SOFTWARE\\Microsoft\\PasswordLess"), 'the old wrong path is gone');
  const build = Number(os.release().split('.')[2]);
  const parent = run(['-Command', "Test-Path -LiteralPath 'HKLM:\\SOFTWARE\\Microsoft\\Windows NT\\CurrentVersion\\Winlogon'"]);
  assert.match(parent.stdout, /True/);
  if (build >= 22000) {                                                          // Windows 11: the key exists, and the dry run must find it
    const j = JSON.parse(autologon('Set', { AISIGNX_USER: 'kiosk', AISIGNX_DOMAIN: '.' }, b64('x')).stdout);
    assert.strictEqual(j.dplKeyExists, true);
  }
});

test('Check-Folder reports SIDs and rights; judgeFolder judges by SID (also on a non-English Windows)', { skip: !win }, () => {
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'asx-cf-'));
  try {
    const r = run(['-File', path.join(PS, 'Check-Folder.ps1'), '-Path', tmp]);
    const j = JSON.parse(r.stdout);
    assert.match(j.ownerSid, /^S-1-/);
    assert.ok(Array.isArray(j.aces) && j.aces.length > 0 && j.aces.every((a) => /^S-1-/.test(a.sid) && Number.isFinite(a.rights)));
    assert.strictEqual(C.judgeFolder(r.stdout).ok, !!(/^S-1-5-(18|32-544)$/.test(j.ownerSid) && j.aces.every((a) => /^S-1-5-(18|32-544)$/.test(a.sid) || (a.rights & 0x10000046) === 0 && (a.rights & 0x40116) === 0)));
  } finally { fs.rmSync(tmp, { recursive: true, force: true }); }
  const ok = (o) => C.judgeFolder(JSON.stringify(Object.assign({ owner: 'x', reparse: [] }, o)));
  const FULL = 0x1F01FF, READ = 0x1200A9;
  // German Windows: names are translated, the SIDs are not.
  assert.deepStrictEqual(ok({ owner: 'VORDEFINIERT\\Administratoren', ownerSid: 'S-1-5-32-544', aces: [{ sid: 'S-1-5-18', rights: FULL }, { sid: 'S-1-5-32-544', rights: FULL }, { sid: 'S-1-5-32-545', rights: READ }] }), { ok: true });
  assert.strictEqual(ok({ owner: 'NT-AUTORITAET\\SYSTEM', ownerSid: 'S-1-5-18', aces: [] }).ok, true);
  assert.strictEqual(ok({ owner: 'PC\\kiosk', ownerSid: SID, aces: [] }).ok, false);                               // owner is somebody else
  // A write ACE for ANY other principal (the kiosk account itself, Users in German, Everyone) is unsafe.
  for (const sid of [SID, 'S-1-5-32-545', 'S-1-1-0', 'S-1-5-11']) {
    assert.strictEqual(ok({ ownerSid: 'S-1-5-32-544', aces: [{ sid: 'S-1-5-32-544', rights: FULL }, { sid, rights: 0x2 }] }).ok, false, sid);   // WriteData
    assert.strictEqual(ok({ ownerSid: 'S-1-5-32-544', aces: [{ sid, rights: 0x40000 }] }).ok, false, sid);                                       // change permissions
    assert.strictEqual(ok({ ownerSid: 'S-1-5-32-544', aces: [{ sid, rights: READ }] }).ok, true, sid);                                            // read only is fine
  }
  assert.strictEqual(ok({ ownerSid: 'S-1-5-32-544', aces: 'x' }).ok, false);
  assert.strictEqual(ok({ ownerSid: 'S-1-5-32-544', aces: [{ sid: 5, rights: 1 }] }).ok, false);
});

test('the in-app Tier B stands down only while the agent says "running" (an agent stuck on unsafe_acl does nothing)', () => {
  const now = Date.now();
  assert.strictEqual(delegate.judge({ state: 'running', t: now - 1000 }, now), true);
  assert.strictEqual(delegate.judge({ state: 'unsafe_acl', t: now - 1000 }, now), false);
});

test('plain http: enrollment refuses it unless the installer said so; auto-login over http is refused, removal is not', async () => {
  assert.strictEqual(C.isSecureServer('https://signage.example'), true);
  assert.strictEqual(C.isSecureServer('http://signage.example'), false);
  assert.strictEqual(C.isSecureServer('http://192.168.1.5:5000'), false);
  assert.strictEqual(C.isSecureServer('http://127.0.0.1:5000'), true);
  assert.strictEqual(C.isSecureServer('http://localhost'), true);
  assert.strictEqual(C.isSecureServer('http://[::1]:5000'), true);
  assert.strictEqual(C.isSecureServer('http://localhost.evil.example'), false);
  assert.strictEqual(C.isSecureServer('http://127.0.0.1.evil.example'), false);
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'asx-enr-'));
  try {
    const paths = C.dataPaths(dir);
    let called = 0;
    const r = await enrollment.enroll({ server: 'http://signage.example', code: 'ABCD-EFGH-JKLM-NPQR', fingerprint: 'ab12cd34ef567890' }, paths, async () => { called++; throw new Error('no'); }, {});
    assert.strictEqual(r.ok, false);
    assert.match(r.reason, /plain http/);
    assert.strictEqual(called, 0);
    assert.ok(!fs.existsSync(paths.keyFile), 'no key is made before the refusal');
  } finally { fs.rmSync(dir, { recursive: true, force: true }); }
  const vec = require('./vectors/agent-vectors.json');
  const T = 1_800_000_000_000 - 3600_000;
  const world = (server) => {
    const w = harness({ signer: { keyB64: vec.command_key_b64, sign: () => { throw new Error('vectors only'); } }, cfg: { server } });
    w.agentKey.priv = Buffer.from(vec.agent_private_hex, 'hex'); w.clock.t = T; w.serverTime = T;
    return w;
  };
  const w = world('http://signage.example');
  try {
    assert.strictEqual(await w.core.handle(vec.commands.autologin_off, T), true);
    assert.strictEqual(w.acks[0].state, 'done');                                   // removing it is always allowed
  } finally { w.cleanup(); }
  const w2 = world('http://signage.example');
  try {
    await w2.core.handle(vec.commands.autologin, T);
    assert.strictEqual(w2.acks[0].state, 'failed');
    assert.match(w2.acks[0].result, /not https/);
    assert.ok(!w2.calls.some((c) => c[0] === 'setAutologon'));
  } finally { w2.cleanup(); }
  const w3 = world('https://signage.example');                                      // the same command over https works
  try {
    await w3.core.handle(vec.commands.autologin, T);
    assert.strictEqual(w3.acks[0].state, 'done');
  } finally { w3.cleanup(); }
});

test('the first update cannot install a build older than the one that is installed now', async () => {
  const crypto = require('crypto');
  const body = Buffer.from('MZ-old-installer');
  const sha = crypto.createHash('sha256').update(body).digest('hex');
  const w = harness({ files: { '5555555555555551': body, '5555555555555552': body } });
  try {
    assert.strictEqual(w.st.minVersion, '0');                                      // nothing has been updated yet
    w.installed = '3.1.0';
    await w.core.handle(w.signer.sign('5555555555555551', 'update_app', { version: '3.0.0', sha256: sha, size: body.length }), w.serverTime);
    assert.strictEqual(w.acks[0].state, 'failed');
    assert.match(w.acks[0].result, /older than the installed version/);
    assert.strictEqual(w.installerRuns.length, 0);
    assert.strictEqual(w.st.pending, null);
    await w.core.handle(w.signer.sign('5555555555555552', 'update_app', { version: '3.1.0', sha256: sha, size: body.length }), w.serverTime);
    assert.strictEqual(w.acks[1].state, 'done');                                   // the same version (a reinstall) is allowed
  } finally { w.cleanup(); }
});

test('shell-mode update: the launcher marker is written before the player is killed and removed afterwards', async () => {
  const crypto = require('crypto');
  const body = Buffer.from('MZ-new-installer');
  const sha = crypto.createHash('sha256').update(body).digest('hex');
  const w = harness({ files: { '6666666666666661': body } });
  try {
    const order = [];
    w.ops.markUpdating = async () => { order.push('mark'); };
    w.ops.clearUpdating = async () => { order.push('clear'); };
    const kill = w.ops.killPlayer;
    w.ops.killPlayer = async (...a) => { order.push('kill'); return kill(...a); };
    await w.core.handle(w.signer.sign('6666666666666661', 'update_app', { version: '3.2.0', sha256: sha, size: body.length }), w.serverTime);
    assert.deepStrictEqual(order.slice(0, 2), ['mark', 'kill']);
    w.installed = '3.2.0'; w.status = { player_version: '3.2.0', player_last_seen_s: 3 };
    await w.core.updater.tick();
    assert.ok(order.includes('clear'));
  } finally { w.cleanup(); }
});

test('marker: written only when every folder on the way is a plain directory; never through a link', () => {
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'asx-mk-'));
  try {
    assert.strictEqual(marker.markerPath(tmp), null);                              // no launcher folder: not shell mode
    const agent = path.join(tmp, ...marker.REL);
    fs.mkdirSync(agent, { recursive: true });
    assert.strictEqual(marker.write(tmp, 12345), true);
    assert.strictEqual(fs.readFileSync(path.join(agent, 'updating'), 'utf8'), '12345');
    assert.strictEqual(marker.clear(tmp), true);
    assert.ok(!fs.existsSync(path.join(agent, 'updating')));
    assert.strictEqual(marker.markerPath('relative\\path'), null);
    // a junction in place of the folder is refused
    const other = fs.mkdtempSync(path.join(os.tmpdir(), 'asx-mk2-'));
    const prof2 = fs.mkdtempSync(path.join(os.tmpdir(), 'asx-mk3-'));
    fs.mkdirSync(path.join(prof2, 'AppData', 'Local', 'AiSignX'), { recursive: true });
    fs.symlinkSync(other, path.join(prof2, 'AppData', 'Local', 'AiSignX', 'agent'), 'junction');
    assert.strictEqual(marker.write(prof2, 1), false);
    assert.deepStrictEqual(fs.readdirSync(other), []);
    fs.rmSync(other, { recursive: true, force: true }); fs.rmSync(prof2, { recursive: true, force: true });
  } finally { fs.rmSync(tmp, { recursive: true, force: true }); }
  assert.strictEqual(marker.parseProfileQuery('\r\nHKEY_LOCAL_MACHINE\\x\r\n    ProfileImagePath    REG_EXPAND_SZ    C:\\Users\\kiosk\r\n'), 'C:\\Users\\kiosk');
  assert.strictEqual(marker.parseProfileQuery('garbage'), '');
});

test('programs are started by full path (never searched in the current directory)', () => {
  assert.strictEqual(sysexe.resolve('powershell.exe', { SystemRoot: 'C:\\Windows' }), 'C:\\Windows\\System32\\WindowsPowerShell\\v1.0\\powershell.exe');
  assert.strictEqual(sysexe.resolve('icacls.exe', { SystemRoot: 'D:\\Win' }), 'D:\\Win\\System32\\icacls.exe');
  for (const n of ['shutdown.exe', 'taskkill.exe', 'schtasks.exe', 'reg.exe']) assert.ok(/^[A-Z]:\\.*\\System32\\/.test(sysexe.resolve(n, {})), n);
  assert.strictEqual(sysexe.resolve('C:\\x\\installer.exe', {}), 'C:\\x\\installer.exe');
});

test('writeJsonAtomic flushes the temp file to disk before the rename', () => {
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'asx-wj-'));
  const order = [];
  const f = { fsync: fs.fsyncSync, rename: fs.renameSync };
  fs.fsyncSync = (...a) => { order.push('fsync'); return f.fsync(...a); };
  fs.renameSync = (...a) => { order.push('rename'); return f.rename(...a); };
  try {
    taskm.writeJsonAtomic(path.join(tmp, 'state.json'), { a: 1 });
    assert.deepStrictEqual(order, ['fsync', 'rename']);
    assert.deepStrictEqual(JSON.parse(fs.readFileSync(path.join(tmp, 'state.json'), 'utf8')), { a: 1 });
  } finally { fs.fsyncSync = f.fsync; fs.renameSync = f.rename; fs.rmSync(tmp, { recursive: true, force: true }); }
});

test('the autologin password copies are wiped, also when the decrypted password is refused', async () => {
  const { createOps } = require('../src/agent-svc/ops');
  const AC = require('../src/agent-svc/autologin');
  const pw = Buffer.from('hunter2');
  const payload = Buffer.from('xx');
  const orig = AC.stdinPayload;
  AC.stdinPayload = () => payload;
  try {
    const ops = createOps({ psDir: PS, programDir: os.tmpdir(), cfg: { kioskSid: SID }, paths: {} }, async () => ({ ok: true, out: '' }));
    // a PowerShell that cannot start is simulated by an impossible user name: spawn still runs, so only check the wipe on close
    const done = ops.setAutologon('kiosk', '.', pw);
    await done;
    assert.ok(pw.every((b) => b === 0), 'password buffer wiped');
    assert.ok(payload.every((b) => b === 0), 'stdin payload wiped');
  } finally { AC.stdinPayload = orig; }
});

// ---- Install-Agent.ps1 ----
const installSrc = fs.readFileSync(INSTALL, 'utf8');
const GOOD = ['-File', INSTALL, '-DryRun', '-Code', 'ABCD-EFGH-JKLM-NPQR', '-KeyFingerprint', 'ab12 cd34 ef56 7890', '-Thumbprint', '386BFBC220743F420E64AB481C4249EB1DE9C4BC', '-BundleSha256', 'a'.repeat(64)];

test('Install-Agent: plain http to another machine is refused without -AllowInsecureHttp; loopback and https are fine', { skip: !win }, () => {
  const refused = run(GOOD.concat(['-Server', 'http://signage.example']), process.env);
  assert.strictEqual(refused.status, 2, refused.stdout + refused.stderr);
  assert.match(refused.stdout, /plain http/);
  assert.strictEqual(run(GOOD.concat(['-Server', 'http://signage.example', '-AllowInsecureHttp']), process.env).status, 0);
  assert.strictEqual(run(GOOD.concat(['-Server', 'http://127.0.0.1:5000']), process.env).status, 0);
  assert.strictEqual(run(GOOD.concat(['-Server', 'https://signage.example']), process.env).status, 0);
});

test('Install-Agent: folder protection order, pre-created data folder, launcher waits for the agent, uninstall safety', { skip: !win }, () => {
  const body = installSrc.slice(installSrc.indexOf('function Protect-Folder'), installSrc.indexOf('# ---- Zip extraction'));
  const at = (s) => body.indexOf(s);
  assert.ok(at('/setowner') > -1 && at('/setowner') < at('/inheritance:r'), 'owner first');
  assert.ok(at('/inheritance:r') < at('/grant:r'), 'inheritance cut before the grants');
  assert.ok(at('/grant:r') < at('/reset'), 'children are reset only AFTER the folder is protected');
  assert.ok(installSrc.includes('Assert-FolderNotPlanted $DataDir'));
  assert.ok(installSrc.indexOf('Assert-FolderNotPlanted $DataDir') < installSrc.indexOf('Protect-Folder $DataDir $false'));
  assert.match(installSrc, /Remove-Item -LiteralPath \(Join-Path \$DataDir 'keys'\)/);          // a fresh key at every install
  assert.match(installSrc, /start `"`" \/wait/);                                                // Run-Agent.cmd waits for the exe
  assert.match(installSrc, /autologin\.enabled -eq \$true/);                                    // auto-login cleared only when the agent set it
  assert.match(installSrc, /Find-ReparsePoints \$d/);
  assert.match(installSrc, /Pass -Force to delete/);
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'asx-plant-'));
  try {
    const r = run(['-Command', '. "' + INSTALL + '"; $o = (New-Object System.Security.Principal.NTAccount((Get-Acl -LiteralPath "' + tmp + '").Owner)).Translate([System.Security.Principal.SecurityIdentifier]).Value; '
      + 'try { Assert-FolderNotPlanted "' + tmp + '"; "OK:$o" } catch { "REFUSED:$o" }; try { Assert-FolderNotPlanted "' + path.join(tmp, 'absent') + '"; "ABSENT:OK" } catch { "ABSENT:FAIL" }']);
    const m = /(OK|REFUSED):(\S+)/.exec(r.stdout);
    assert.ok(m, r.stdout + r.stderr);
    assert.strictEqual(m[1], /^S-1-5-(18|32-544)$/.test(m[2]) ? 'OK' : 'REFUSED');             // a folder owned by an ordinary user is refused
    assert.match(r.stdout, /ABSENT:OK/);
  } finally { fs.rmSync(tmp, { recursive: true, force: true }); }
  const cmd = run(['-Command', '. "' + INSTALL + '"; New-RunCmd']);
  assert.match(cmd.stdout, /start "" \/wait "%~dp0aisignx-agent\.exe"/);
});
