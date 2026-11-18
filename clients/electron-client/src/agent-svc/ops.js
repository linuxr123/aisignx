'use strict';
// The agent's effects on Windows. Every program and argument is a constant or comes from the protected agent.json
// (validated in config.js); nothing the server sends is ever placed in a command line or an environment variable.
// PowerShell helpers are constant scripts in the protected program folder.
const fs = require('fs');
const path = require('path');
const { execFile, spawn } = require('child_process');
const AC = require('./autologin');
const auth = require('./authenticode');
const sysexe = require('./sysexe');
const marker = require('./marker');

const PS_BASE = ['-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass', '-File'];
const PLAYER_IMAGE = 'AiSignX Player.exe';
const LAUNCH_TASK = 'AiSignX Player Launch';

function run(file, args, opts) {
  file = sysexe.resolve(file);
  return new Promise((resolve) => {
    execFile(file, args, Object.assign({ windowsHide: true, timeout: 60000, maxBuffer: 256 * 1024 }, opts), (err, stdout, stderr) => {
      resolve({ ok: !err, out: String(stdout || ''), err: String(stderr || '') });
    });
  });
}

// o: {psDir, programDir, cfg, paths}
function createOps(o, runner) {
  const R = runner || run;
  const script = (name) => path.join(o.psDir, name);
  const ps = (name, args, opts) => R('powershell.exe', PS_BASE.concat([script(name)], args || []), opts);
  const json = (r) => { try { return r.ok ? JSON.parse(r.out.trim()) : null; } catch (e) { return null; } };

  let profile = null;
  const profileDir = async () => {
    if (profile !== null || !o.cfg.kioskSid) return profile || '';
    const r = await R('reg.exe', ['query', 'HKLM\\SOFTWARE\\Microsoft\\Windows NT\\CurrentVersion\\ProfileList\\' + o.cfg.kioskSid, '/v', 'ProfileImagePath']);
    profile = r.ok ? marker.parseProfileQuery(r.out) : '';
    return profile;
  };

  return {
    folderCheck: async (dir) => ({ ps: await ps('Check-Folder.ps1', ['-Path', dir]), acl: await R('icacls.exe', [dir]) }),
    importCert: () => ps('Import-Cert.ps1', ['-CertFile', o.paths.cert, '-Thumbprint', o.cfg.thumbprint, '-TrustScope', o.cfg.trustScope]),
    verifySignature: (file, nowMs) => auth.verifyFile(file, o.psDir, o.cfg.thumbprint, nowMs, R),
    reboot: () => R('shutdown.exe', ['/r', '/t', '30', '/c', 'AiSignX: restart requested from the AiSignX server']),
    killPlayer: () => R('taskkill.exe', ['/IM', PLAYER_IMAGE, '/F']),
    // The player must show in the kiosk user's session: a one-shot task that runs as that user (created at install).
    launchPlayer: () => R('schtasks.exe', ['/run', '/tn', LAUNCH_TASK]),
    // Shell mode only (the launcher folder exists in the kiosk profile): the launcher waits for the installer.
    markUpdating: async () => { const d = await profileDir(); return d ? marker.write(d, Date.now()) : false; },
    clearUpdating: async () => { const d = await profileDir(); return d ? marker.clear(d) : false; },
    installedVersion: async () => {
      if (!o.cfg.playerExe) return '';
      const r = await ps('Get-FileVersion.ps1', ['-Path', o.cfg.playerExe]);
      return r.ok ? r.out.trim().slice(0, 32) : '';
    },
    // {autostart?: bool, mode?: 'app'|'shell'} (typed, from a verified command) -> {autostart, mode} | null
    setStartup: async (p) => {
      if (!o.cfg.kioskSid || !o.cfg.playerExe) return null;
      if (p.mode === 'shell') {
        const dir = path.join(o.programDir, 'launcher');
        fs.mkdirSync(dir, { recursive: true });
        fs.writeFileSync(path.join(dir, 'shell.json'), JSON.stringify({ exe: o.cfg.playerExe, args: [] }));
      }
      const env = Object.assign({}, process.env, {
        AISIGNX_AUTOSTART: 'autostart' in p ? (p.autostart ? 'on' : 'off') : '', AISIGNX_MODE: p.mode || '',
        AISIGNX_EXE: o.cfg.playerExe, AISIGNX_SID: o.cfg.kioskSid,
        AISIGNX_LAUNCHER: path.join(o.psDir, 'shell-launcher.ps1'), AISIGNX_CFGDIR: path.join(o.programDir, 'launcher') });
      return json(await ps('Set-Startup.ps1', [], { env }));
    },
    // The password goes in through stdin only (base64 of UTF-8); the buffer is wiped afterwards.
    setAutologon: (user, domain, password) => new Promise((resolve) => {
      let done = false;
      const payload = AC.stdinPayload(password);
      const end = (v) => { if (!done) { done = true; password.fill(0); payload.fill(0); resolve(v); } };
      let child;
      try {
        child = spawn(sysexe.resolve('powershell.exe'), PS_BASE.concat([script('Set-Autologon.ps1'), '-Mode', 'Set']),
          { env: Object.assign({}, process.env, AC.childEnv(user, domain)), windowsHide: true, stdio: ['pipe', 'pipe', 'ignore'] });
      } catch (e) { return end(false); }
      child.once('error', () => end(false));
      child.stdin.on('error', () => {});
      child.once('close', (code) => end(code === 0));
      setTimeout(() => { try { child.kill(); } catch (e) { /* ignore */ } end(false); }, 60000).unref();
      child.stdin.end(payload);
    }),
    clearAutologon: async () => (await ps('Set-Autologon.ps1', ['-Mode', 'Clear'])).ok,
  };
}

module.exports = { createOps, run, PLAYER_IMAGE, LAUNCH_TASK };
