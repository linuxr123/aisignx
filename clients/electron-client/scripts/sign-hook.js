'use strict';
// electron-builder signing hook (package.json build.win.signtoolOptions.sign). Called for every executable the build
// produces (app exe, uninstaller, installer). Signs through scripts/Sign-File.ps1 with the thumbprint from the build
// environment (AISIGNX_SIGN_THUMBPRINT); without it the build still works but the files stay unsigned, and the
// privileged agent will refuse such an installer. The thumbprint is validated here and passed as one argument.
const path = require('path');
const { execFile } = require('child_process');

const THUMB_RE = /^[0-9A-Fa-f]{40}$/;

// -> the argument list for powershell.exe, or null when signing is off. Pure, tested.
function buildArgs(file, env, scriptsDir) {
  const t = env.AISIGNX_SIGN_THUMBPRINT;
  if (!t) return null;
  if (!THUMB_RE.test(t)) throw new Error('AISIGNX_SIGN_THUMBPRINT must be 40 hex characters');
  const args = ['-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass', '-File', path.join(scriptsDir, 'Sign-File.ps1'), '-Path', file, '-Thumbprint', t];
  if (env.AISIGNX_SIGN_TIMESTAMP) args.push('-TimestampUrl', env.AISIGNX_SIGN_TIMESTAMP);
  return args;
}

exports.buildArgs = buildArgs;
exports.default = async function sign(configuration) {
  const args = buildArgs(configuration.path, process.env, __dirname);
  if (!args) { console.log('sign-hook: AISIGNX_SIGN_THUMBPRINT not set, leaving ' + path.basename(configuration.path) + ' unsigned'); return; }
  await new Promise((resolve, reject) => {
    execFile('powershell.exe', args, { windowsHide: true, timeout: 120000 }, (err, stdout, stderr) => {
      if (stdout) console.log(String(stdout).trim());
      if (err) reject(new Error('signing failed: ' + String(stderr || err.message).trim()));
      else resolve();
    });
  });
};
