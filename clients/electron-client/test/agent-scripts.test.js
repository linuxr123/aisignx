'use strict';
// The agent's PowerShell scripts and installers: they must parse, validate their input, and the read-only ones are run for real.
// Nothing here is elevated and nothing touches this PC's registry, services or tasks (Sign-File only signs a temp copy).
const test = require('node:test');
const assert = require('node:assert');
const fs = require('fs');
const os = require('os');
const path = require('path');
const { spawnSync } = require('child_process');

const root = path.join(__dirname, '..');
const win = process.platform === 'win32';
const ps = (args, opts) => spawnSync('powershell.exe', ['-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass'].concat(args), Object.assign({ encoding: 'utf8', timeout: 90000 }, opts));
const psCmd = (cmd) => ps(['-Command', cmd]);
const files = (dir, ext) => fs.readdirSync(dir).filter((f) => f.endsWith(ext)).map((f) => path.join(dir, f));
const ALL_PS = files(path.join(root, 'resources'), '.ps1').concat(files(path.join(root, 'resources', 'agent', 'ps'), '.ps1'), files(path.join(root, 'scripts'), '.ps1'));
const INSTALL = path.join(root, 'resources', 'Install-Agent.ps1');
const A = require('../src/agent-svc/authenticode');
const C = require('../src/agent-svc/config');

test('every PowerShell script parses cleanly (PowerShell parser, nothing run)', { skip: !win }, () => {
  const list = ALL_PS.map((f) => "'" + f.replace(/'/g, "''") + "'").join(',');
  const r = psCmd("$bad=0; foreach($f in @(" + list + ")){ $e=$null;$t=$null;[void][System.Management.Automation.Language.Parser]::ParseFile($f,[ref]$t,[ref]$e); if($e.Count){$bad++; $f + ': ' + $e[0].Message} }; if($bad){exit 1}; 'ok'");
  assert.strictEqual(r.status, 0, r.stdout + r.stderr);
  assert.ok(ALL_PS.length >= 12);
});

test('every .ps1 is plain ASCII (no encoding surprises under Windows PowerShell 5.1)', () => {
  for (const f of ALL_PS) assert.ok(!/[^\x00-\x7f]/.test(fs.readFileSync(f, 'utf8')), path.basename(f));
});

test('Install-Agent -DryRun checks every parameter and needs no elevation', { skip: !win }, () => {
  const good = ['-File', INSTALL, '-DryRun', '-Server', 'https://signage.example:8443', '-Code', 'ABCD-EFGH-JKLM-NPQR', '-KeyFingerprint', 'ab12 cd34 ef56 7890',
    '-Thumbprint', '386BFBC220743F420E64AB481C4249EB1DE9C4BC', '-BundleSha256', 'a'.repeat(64), '-AllowUpdate'];
  const ok = ps(good);
  assert.strictEqual(ok.status, 0, ok.stdout + ok.stderr);
  assert.match(ok.stdout, /update=True/);
  assert.match(ok.stdout, /reboot=False/);                       // every switch defaults off
  assert.match(ok.stdout, /autologin=False/);
  assert.match(ok.stdout, /Dry run: nothing was changed/);
  const swap = (flag, value) => { const a = good.slice(); a[a.indexOf(flag) + 1] = value; return a; };
  for (const [flag, value] of [['-Server', 'ftp://x'], ['-Server', 'https://host/path'], ['-Server', 'https://h"; calc'], ['-Code', 'short'], ['-Code', 'ABCD-EFGH-JKLM-NPQR; calc'],
    ['-KeyFingerprint', 'zz'], ['-Thumbprint', '1234'], ['-Thumbprint', 'G'.repeat(40)]]) {
    const r = ps(swap(flag, value));
    assert.strictEqual(r.status, 2, flag + ' ' + value + '\n' + r.stdout + r.stderr);
    assert.match(r.stdout, /Problem:/);
  }
  const noKiosk = ps(good.concat(['-AllowAutologin']));
  assert.strictEqual(noKiosk.status, 2);                         // autologin and startup need the kiosk account
  assert.match(noKiosk.stdout, /need -KioskUser/);
  assert.match(ps(good.concat(['-KioskUser', 'no such user ' + process.pid])).stderr + '', /does not exist/);
  const unins = ps(['-File', INSTALL, '-Uninstall', '-DryRun']);
  assert.strictEqual(unins.status, 0);
});

test('without -DryRun the installer refuses to run unelevated', { skip: !win }, () => {
  const probe = psCmd("([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)");
  if (/True/.test(probe.stdout)) return;                          // never run the real install from an elevated test run
  const r = ps(['-File', INSTALL, '-Server', 'https://signage.example', '-Code', 'ABCD-EFGH-JKLM-NPQR', '-KeyFingerprint', 'ab12 cd34 ef56 7890',
    '-Thumbprint', '386BFBC220743F420E64AB481C4249EB1DE9C4BC', '-BundleSha256', 'a'.repeat(64)]);
  assert.notStrictEqual(r.status, 0);
  assert.match(r.stdout + r.stderr, /elevated/);
});

test('Install-Agent functions (dot-sourced, nothing executed): task XML, constant launcher, zip-slip, validators', { skip: !win }, () => {
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'asx-ps-'));
  try {
    const script = [
      '. "' + INSTALL + '"',
      '$x = New-AgentTaskXml "C:\\Program Files\\AiSignX\\Agent\\Run-Agent.cmd"',
      'Write-Output "XML:$x"', 'Write-Output ("CMD:" + (New-RunCmd))',
      // a good zip and a zip-slip zip
      'Add-Type -AssemblyName System.IO.Compression.FileSystem',
      '$good = "' + path.join(tmp, 'good.zip') + '"; $evil = "' + path.join(tmp, 'evil.zip') + '"',
      'foreach($pair in @(@($good,"src/a.txt"), @($evil,"../escape.txt"))){ $za=[IO.Compression.ZipFile]::Open($pair[0],"Create"); $en=$za.CreateEntry($pair[1]); $sw=New-Object IO.StreamWriter($en.Open()); $sw.Write("x"); $sw.Dispose(); $za.Dispose() }',
      'Expand-Safe $good "' + path.join(tmp, 'out') + '"; Write-Output ("GOOD:" + (Test-Path "' + path.join(tmp, 'out', 'src', 'a.txt') + '"))',
      'try { Expand-Safe $evil "' + path.join(tmp, 'out2') + '"; Write-Output "EVIL:extracted" } catch { Write-Output ("EVIL:refused " + $_.Exception.Message) }',
      'Write-Output ("ESCAPED:" + (Test-Path "' + path.join(tmp, 'escape.txt') + '"))',
      'Write-Output ("V:" + (Test-ServerUrl "https://a.b:1") + (Test-ServerUrl "https://a/b") + (Test-Thumbprint ("A"*1)) + (Test-EnrollCode "ABCD-EFGH-JKLM-NPQR") + (Test-EnrollCode "ABCD-EFGH-JKLM-NPQ0"))',
    ].join('\n');
    const f = path.join(tmp, 't.ps1'); fs.writeFileSync(f, script);
    const r = ps(['-File', f]);
    assert.strictEqual(r.status, 0, r.stdout + r.stderr);
    const out = r.stdout;
    const xml = out.slice(out.indexOf('XML:'), out.indexOf('CMD:'));
    for (const must of ['<UserId>S-1-5-18</UserId>', '<RunLevel>HighestAvailable</RunLevel>', '<BootTrigger>', '<Interval>PT5M</Interval>', '<RestartOnFailure>', '<Count>999</Count>',
      '<MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>', '<ExecutionTimeLimit>PT0S</ExecutionTimeLimit>', '<Command>C:\\Program Files\\AiSignX\\Agent\\Run-Agent.cmd</Command>']) assert.ok(xml.includes(must), must);
    assert.ok(!/<Arguments>/.test(xml), 'the task has no arguments, so nothing can be injected into them');
    const cmd = out.slice(out.indexOf('CMD:'), out.indexOf('GOOD:'));
    assert.match(cmd, /set ELECTRON_RUN_AS_NODE=1/); assert.match(cmd, /set NODE_OPTIONS=\r?\n/);
    assert.match(cmd, /"%~dp0aisignx-agent\.exe" "%~dp0src\\agent-svc\\main\.js"/);
    assert.match(out, /GOOD:True/);
    assert.match(out, /EVIL:refused Unsafe path/);
    assert.match(out, /ESCAPED:False/);
    assert.match(out, /V:TrueFalseFalseTrueFalse/);
  } finally { fs.rmSync(tmp, { recursive: true, force: true }); }
});

test('real read-only helpers: Verify-Signature, Check-Folder and Get-FileVersion on real files', { skip: !win }, () => {
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'asx-real-'));
  try {
    const dir = path.join(root, 'resources', 'agent', 'ps');
    const exe = path.join(process.env.SystemRoot || 'C:\\Windows', 'System32', 'notepad.exe');
    const sig = ps(['-File', path.join(dir, 'Verify-Signature.ps1'), '-Path', exe]);
    const parsed = JSON.parse(sig.stdout.trim());
    assert.ok(parsed.status && Array.isArray(parsed.eku));
    assert.strictEqual(A.judge(sig.stdout, 'F'.repeat(40), Date.now()).ok, false);                    // another pin: never accepted
    if (parsed.status === 'Valid') assert.strictEqual(A.judge(sig.stdout, parsed.thumbprint, Date.now()).ok, true);
    const plain = path.join(tmp, 'plain.exe'); fs.writeFileSync(plain, 'MZ not signed');
    const unsigned = ps(['-File', path.join(dir, 'Verify-Signature.ps1'), '-Path', plain]);
    assert.notStrictEqual(JSON.parse(unsigned.stdout.trim()).status, 'Valid');
    assert.strictEqual(A.judge(unsigned.stdout, parsed.thumbprint || 'A'.repeat(40), Date.now()).ok, false);
    const sps = path.join(tmp, 'x.ps1'); fs.writeFileSync(sps, 'Write-Host 1\r\n');
    assert.strictEqual(JSON.parse(ps(['-File', path.join(dir, 'Verify-Signature.ps1'), '-Path', sps]).stdout).status, 'NotSigned');
    const gone = ps(['-File', path.join(dir, 'Verify-Signature.ps1'), '-Path', path.join(tmp, 'missing.exe')]);
    assert.strictEqual(A.judge(gone.stdout, 'A'.repeat(40), Date.now()).ok, false);
    const folder = ps(['-File', path.join(dir, 'Check-Folder.ps1'), '-Path', tmp]);
    assert.deepStrictEqual(JSON.parse(folder.stdout).reparse, []);
    assert.strictEqual(C.judgeFolder(folder.stdout).ok, false);                                        // owned by an ordinary user: unsafe
    fs.symlinkSync(tmp, path.join(tmp, 'link'), 'junction');
    assert.ok(JSON.parse(ps(['-File', path.join(dir, 'Check-Folder.ps1'), '-Path', tmp]).stdout).reparse.length >= 1);
    const ver = ps(['-File', path.join(dir, 'Get-FileVersion.ps1'), '-Path', exe]);
    assert.match(ver.stdout.trim(), /^\d+\.\d+/);
    assert.strictEqual(ps(['-File', path.join(dir, 'Get-FileVersion.ps1'), '-Path', path.join(tmp, 'nope.exe')]).stdout.trim(), '');
  } finally { fs.rmSync(tmp, { recursive: true, force: true }); }
});

test('Set-Autologon.ps1 -Mode SelfTest compiles the LSA helper without touching the machine', { skip: !win }, () => {
  const r = ps(['-File', path.join(root, 'resources', 'agent', 'ps', 'Set-Autologon.ps1'), '-Mode', 'SelfTest']);
  assert.strictEqual(r.status, 0, r.stdout + r.stderr);
  assert.match(r.stdout, /ok/i);
});

test('signing end to end on a temp copy (skipped when the owner certificate is not on this PC): a changed byte fails', { skip: !win }, () => {
  const thumb = '386BFBC220743F420E64AB481C4249EB1DE9C4BC';
  if (!/True/.test(psCmd("Test-Path 'Cert:\\CurrentUser\\My\\" + thumb + "'").stdout)) return;
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'asx-sign-'));
  try {
    const exe = path.join(tmp, 'p.ps1');                          // a script: an unsigned PE would have to be built first
    fs.writeFileSync(exe, 'Write-Host "installer stand-in"\r\nWrite-Host "second line"\r\n');
    const s = ps(['-File', path.join(root, 'scripts', 'Sign-File.ps1'), '-Path', exe, '-Thumbprint', thumb]);
    assert.strictEqual(s.status, 0, s.stdout + s.stderr);
    const v = ps(['-File', path.join(root, 'resources', 'agent', 'ps', 'Verify-Signature.ps1'), '-Path', exe]);
    assert.strictEqual(JSON.parse(v.stdout).thumbprint, thumb);
    assert.strictEqual(A.judge(v.stdout, 'B'.repeat(40), Date.now()).ok, false);                          // another pin: refused
    if (JSON.parse(v.stdout).status === 'Valid') assert.strictEqual(A.judge(v.stdout, thumb, Date.now()).ok, true);
    const bytes = fs.readFileSync(exe); bytes[8] ^= 0x01; fs.writeFileSync(exe, bytes);              // one changed byte in the signed content
    const t = ps(['-File', path.join(root, 'resources', 'agent', 'ps', 'Verify-Signature.ps1'), '-Path', exe]);
    assert.notStrictEqual(JSON.parse(t.stdout).status, 'Valid');
    assert.strictEqual(A.judge(t.stdout, thumb, Date.now()).ok, false);                                 // tampered: refused even with the right pin
  } finally { fs.rmSync(tmp, { recursive: true, force: true }); }
});

test('sign hook: thumbprint comes only from the build environment, validated, one argument; without it nothing is signed', () => {
  const hook = require('../scripts/sign-hook');
  assert.strictEqual(hook.buildArgs('C:\\x.exe', {}, 'S'), null);
  const a = hook.buildArgs('C:\\dist\\x.exe', { AISIGNX_SIGN_THUMBPRINT: 'a'.repeat(40) }, 'S');
  assert.deepStrictEqual(a.slice(-4), ['-Path', 'C:\\dist\\x.exe', '-Thumbprint', 'a'.repeat(40)]);
  assert.throws(() => hook.buildArgs('x', { AISIGNX_SIGN_THUMBPRINT: 'a; calc' }, 'S'));
  assert.ok(hook.buildArgs('x', { AISIGNX_SIGN_THUMBPRINT: 'a'.repeat(40), AISIGNX_SIGN_TIMESTAMP: 'http://timestamp.digicert.com' }, 'S').includes('-TimestampUrl'));
});

test('package.json: agent scripts ship, installer is assisted (/allusers), hook is wired, nothing signs by default', () => {
  const p = JSON.parse(fs.readFileSync(path.join(root, 'package.json'), 'utf8'));
  assert.ok(p.build.extraResources.some((r) => r.from === 'resources/agent/ps'));
  assert.strictEqual(p.build.nsis.oneClick, false);
  assert.strictEqual(p.build.nsis.allowElevation, true);
  assert.strictEqual(p.build.nsis.perMachine, false);
  assert.strictEqual(p.build.win.signtoolOptions.sign, 'scripts/sign-hook.js');
  assert.ok(!JSON.stringify(p).match(/[0-9A-F]{40}/i), 'no thumbprint in a tracked file');
  assert.ok(fs.existsSync(path.join(root, p.build.win.signtoolOptions.sign)));
});

test('quoting: no Windows path or registry path in the agent JS has a single backslash before a letter inside a string', () => {
  const dirs = ['src/agent-svc', 'src/agent', 'scripts'].map((d) => path.join(root, d));
  const bad = /(['"`])[^'"`\n]*(?:[A-Za-z]:|HKLM:|HKCU:|HKU:|HKEY_[A-Z_]+)\\(?!\\)[A-Za-z]/;
  for (const d of dirs) for (const f of files(d, '.js')) {
    fs.readFileSync(f, 'utf8').split(/\r?\n/).forEach((line, i) => {
      if (/^\s*(\/\/|\*)/.test(line)) return;
      const code = line.replace(/\/\/ .*$/, '').replace(/\/(?:[^/\\\n]|\\.)+\/[gimsuy]*\.test/g, '');
      assert.ok(!bad.test(code), path.basename(f) + ':' + (i + 1) + ' ' + line.trim());
    });
  }
});

test('no JS string in the agent builds a command from server text: every spawn/exec call takes a constant program and an argument array', () => {
  for (const f of files(path.join(root, 'src', 'agent-svc'), '.js')) {
    const text = fs.readFileSync(f, 'utf8');
    assert.ok(!/(?<![.\w])exec\(|execSync|shell:\s*true|child_process'\)\.exec\b/.test(text), path.basename(f) + ' uses a shell');
    for (const m of text.matchAll(/\b(?:execFile|spawn)\(\s*([^,]+),/g)) assert.match(m[1].trim(), /^('[^']*'|"[^"]*"|file|R|sysexe\.resolve\('[^']*'\)|spawnFn \|\| spawn|\(spawnFn \|\| spawn\))$/, path.basename(f) + ': ' + m[1]);
  }
});
