'use strict';
// Windows actions of the Tier B commands. Every program and every argument here is a fixed
// constant: nothing the server sends is ever put into a command line. The only variable text is
// our own launcher path, handed to PowerShell through an environment variable, not the command line.
const fs = require('fs');
const path = require('path');
const { execFile, spawn } = require('child_process');

const WINLOGON = 'HKCU:\\Software\\Microsoft\\Windows NT\\CurrentVersion\\Winlogon';
const LAUNCHER_NAME = 'shell-launcher.ps1';
const PS_ARGS = ['-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass', '-Command'];
const READ_SHELL = "$v=(Get-ItemProperty -LiteralPath '" + WINLOGON + "' -Name Shell -ErrorAction SilentlyContinue).Shell; if($v){[Console]::Out.Write($v)}";
const WRITE_SHELL = "$k='" + WINLOGON + "'; if(-not (Test-Path -LiteralPath $k)){New-Item -Path $k -Force | Out-Null}; " +
  'Set-ItemProperty -LiteralPath $k -Name Shell -Value $env:AISIGNX_SHELL -Type String';
const CLEAR_SHELL = "Remove-ItemProperty -LiteralPath '" + WINLOGON + "' -Name Shell -ErrorAction Stop";

// Same numbers as resources/shell-launcher.ps1: keep the two in step.
const QUICK_EXIT_MS = 30 * 1000;
const QUICK_EXITS_MAX = 3;

function run(file, args, opts) {
  return new Promise((resolve) => {
    execFile(file, args, Object.assign({ windowsHide: true, timeout: 20000 }, opts), (err, stdout, stderr) => {
      resolve({ ok: !err, out: String(stdout || ''), err: String(stderr || '') });
    });
  });
}

// The registry value that makes the launcher the per-user shell.
function shellValue(launcherPath, systemRoot) {
  // A double quote would end the quoted path early: refuse (a real profile path never has one).
  if (typeof launcherPath !== 'string' || !launcherPath || launcherPath.includes('"')) throw new Error('bad launcher path');
  const ps = path.win32.join(systemRoot || 'C:\\Windows', 'System32', 'WindowsPowerShell', 'v1.0', 'powershell.exe');
  return '"' + ps + '" -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "' + launcherPath + '"';
}
// Only a value we wrote may be removed: a shell somebody else chose is left alone.
function isOurShell(value) {
  return typeof value === 'string' && /powershell\.exe/i.test(value) && value.toLowerCase().includes(LAUNCHER_NAME);
}
// Mirror of the launcher's rule: the last three runs were all quick exits.
function crashLoop(durationsMs) {
  const d = durationsMs.slice(-QUICK_EXITS_MAX);
  return d.length >= QUICK_EXITS_MAX && d.every((ms) => ms < QUICK_EXIT_MS);
}

const readShell = async (r) => { const x = await (r || run)('powershell.exe', PS_ARGS.concat([READ_SHELL])); return x.ok ? x.out.trim() : null; };

// Copies the launcher next to the agent files (a stable path) and tells it which exe to start.
function installLauncher(o) {
  fs.mkdirSync(o.agentDir, { recursive: true });
  const target = path.join(o.agentDir, LAUNCHER_NAME);
  fs.copyFileSync(o.source, target);
  const tmp = path.join(o.agentDir, 'shell.json.tmp');
  fs.writeFileSync(tmp, JSON.stringify({ exe: o.exe, args: o.args || [] }));
  fs.renameSync(tmp, path.join(o.agentDir, 'shell.json'));
  return target;
}

// Both calls fail unless a read-back of the registry shows what was asked for.
async function setShellMode(o, r) {
  if (await readShell(r) === null) return false;              // PowerShell cannot run (blocked by policy?): do not start
  let value;
  try { value = shellValue(path.join(o.agentDir, LAUNCHER_NAME), process.env.SystemRoot); } catch (e) { return false; }
  const target = installLauncher(o);
  const x = await (r || run)('powershell.exe', PS_ARGS.concat([WRITE_SHELL]),
    { env: Object.assign({}, process.env, { AISIGNX_SHELL: value }) });
  if (!x.ok) return false;
  return (await readShell(r)) === value;
}
async function clearShellMode(r) {
  const cur = await readShell(r);
  if (cur === null) return false;
  if (!cur) return true;                       // nothing set: already in app mode
  if (!isOurShell(cur)) return false;          // somebody else's shell: not ours to remove
  if (!(await (r || run)('powershell.exe', PS_ARGS.concat([CLEAR_SHELL]))).ok) return false;
  return (await readShell(r)) === '';
}

// Starts the installer detached. Resolves once it has really started and stayed up for graceMs;
// rejects when it cannot start ('error', e.g. blocked or quarantined) or exits non-zero in that time.
// spawnFn is injectable for tests.
function spawnInstaller(file, args, graceMs, spawnFn) {
  return new Promise((resolve, reject) => {
    let child;
    try { child = (spawnFn || spawn)(file, args, { detached: true, stdio: 'ignore', windowsHide: true }); } catch (e) { return reject(e); }
    let settled = false;
    const end = (err) => { if (settled) return; settled = true; clearTimeout(timer); if (err) reject(err); else { try { child.unref(); } catch (e) { /* ignore */ } resolve(); } };
    const timer = setTimeout(() => end(null), graceMs);
    child.once('error', (e) => end(e));                 // always listened to: an unhandled 'error' would crash the process
    child.once('exit', (code) => { if (code) end(new Error('installer exited with ' + code)); else end(null); });
  });
}

// Fixed program, fixed arguments, 30 s delay so the acknowledgement gets out first.
function reboot(r) {
  return (r || run)('shutdown.exe', ['/r', '/t', '30', '/c', 'AiSignX Player: restart requested from the AiSignX server']);
}

module.exports = { run, shellValue, isOurShell, crashLoop, readShell, setShellMode, clearShellMode, reboot, installLauncher, spawnInstaller,
  LAUNCHER_NAME, QUICK_EXIT_MS, QUICK_EXITS_MAX, READ_SHELL, WRITE_SHELL, CLEAR_SHELL };
