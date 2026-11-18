'use strict';
// Authenticode check of the installer. The only trusted fact is the output of resources/agent/ps/Verify-Signature.ps1
// (a constant script, the file path is its only argument), and it counts only when Windows says Status == Valid
// (a real chain AND digest check) and the signer's thumbprint equals the PINNED one. The thumbprint is compared
// here in JS, never interpolated into a script. "Untrusted root but the thumbprint matches" is not accepted.
const path = require('path');
const { execFile } = require('child_process');

const CODE_SIGNING_EKU = '1.3.6.1.5.5.7.3.3';
const THUMB_RE = /^[0-9A-F]{40}$/i;
const STRONG_ALG = /sha(256|384|512)/i;
const MAX_OUTPUT = 4096;

// text: stdout of the script. pinned: 40 hex. nowMs: device clock. -> {ok:true} | {ok:false, reason}.
function judge(text, pinned, nowMs) {
  const bad = (reason) => ({ ok: false, reason });
  if (typeof pinned !== 'string' || !THUMB_RE.test(pinned)) return bad('no pinned thumbprint');
  if (typeof text !== 'string' || text.length > MAX_OUTPUT) return bad('bad_signature (no result)');
  let r;
  try { r = JSON.parse(text.trim()); } catch (e) { return bad('bad_signature (unreadable result)'); }
  if (!r || typeof r !== 'object' || Array.isArray(r)) return bad('bad_signature (unreadable result)');
  if (r.status !== 'Valid') return bad(r.status === 'NotSigned' ? 'bad_signature (not signed)' : 'bad_signature (' + String(r.status).slice(0, 24) + ')');
  if (typeof r.thumbprint !== 'string' || !THUMB_RE.test(r.thumbprint)) return bad('bad_signature (no signer)');
  if (r.thumbprint.toUpperCase() !== pinned.toUpperCase()) return bad('wrong_signer');
  if (!Array.isArray(r.eku) || !r.eku.includes(CODE_SIGNING_EKU)) return bad('wrong_signer (not a code signing certificate)');
  if (typeof r.sigAlg !== 'string' || !STRONG_ALG.test(r.sigAlg)) return bad('bad_signature (weak algorithm)');
  if (r.timestamped !== true) {
    const end = Date.parse(r.notAfter);
    if (!Number.isFinite(end) || end <= nowMs) return bad('bad_signature (certificate expired and the file has no timestamp)');
  }
  return { ok: true };
}

// scriptsDir: protected folder with the .ps1 files. run: injectable (file, args, opts) -> Promise<{ok, out}>.
function runVerify(file, scriptsDir, run) {
  const script = path.join(scriptsDir, 'Verify-Signature.ps1');
  const args = ['-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass', '-File', script, '-Path', file];
  return (run || defaultRun)('powershell.exe', args, { windowsHide: true, timeout: 60000, maxBuffer: 64 * 1024 });
}
function defaultRun(file, args, opts) {
  file = require('./sysexe').resolve(file);
  return new Promise((resolve) => {
    execFile(file, args, opts, (err, stdout) => resolve({ ok: !err, out: String(stdout || '') }));
  });
}

async function verifyFile(file, scriptsDir, pinned, nowMs, run) {
  const r = await runVerify(file, scriptsDir, run);
  if (!r.ok) return { ok: false, reason: 'bad_signature (check failed to run)' };
  return judge(r.out, pinned, nowMs);
}

module.exports = { judge, verifyFile, runVerify, CODE_SIGNING_EKU, THUMB_RE };
