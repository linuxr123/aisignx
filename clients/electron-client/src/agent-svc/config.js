'use strict';
// The privileged agent's configuration (agent.json) and the checks of its own folders.
// agent.json is written by the elevated install script and lives where only SYSTEM and Administrators can write:
// nothing here is ever read from a user-writable place. Pure functions; the effects are in core.js / main.js.
const path = require('path');

const SERVER_RE = /^https?:\/\/(?:[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*|\[[0-9A-Fa-f:]+\])(?::\d{1,5})?$/;
const TOKEN_RE = /^agt_[A-Za-z0-9_-]{43}$/;
const THUMB_RE = /^[0-9A-F]{40}$/;
const SID_RE = /^S-1-5-21(?:-\d+){3,4}$/;
const USER_RE = /^[A-Za-z0-9._ -]{1,64}$/;
const WIN_PATH_RE = /^[A-Za-z]:\\[^"<>|*?\r\n]{1,240}$/;
const SWITCHES = ['reboot', 'startup', 'update', 'autologin'];

// raw: parsed agent.json (untrusted shape) -> clean config, or null when something essential is missing.
function sanitize(raw) {
  if (!raw || typeof raw !== 'object' || Array.isArray(raw)) return null;
  if (typeof raw.server !== 'string' || !SERVER_RE.test(raw.server)) return null;
  if (typeof raw.token !== 'string' || !TOKEN_RE.test(raw.token)) return null;
  if (!Number.isSafeInteger(raw.displayId) || raw.displayId <= 0) return null;
  if (typeof raw.commandKey !== 'string' || !/^[A-Za-z0-9+/]{43}=$/.test(raw.commandKey)) return null;
  const thumb = typeof raw.thumbprint === 'string' ? raw.thumbprint.toUpperCase() : '';
  if (!THUMB_RE.test(thumb)) return null;
  const allow = {};
  for (const k of SWITCHES) allow[k] = !!(raw.allow && raw.allow[k] === true);   // default off
  const out = { server: raw.server, token: raw.token, displayId: raw.displayId, commandKey: raw.commandKey, thumbprint: thumb, allow,
    trustScope: raw.trustScope === 'Machine' ? 'Machine' : 'SystemOnly',
    kioskUser: typeof raw.kioskUser === 'string' && USER_RE.test(raw.kioskUser) ? raw.kioskUser : '',
    kioskSid: typeof raw.kioskSid === 'string' && SID_RE.test(raw.kioskSid) ? raw.kioskSid : '',
    kioskDomain: '.',
    playerExe: typeof raw.playerExe === 'string' && WIN_PATH_RE.test(raw.playerExe) ? raw.playerExe : '' };
  return out;
}

// https, or http to this very PC (loopback). Enrollment and auto-login refuse anything else.
function isSecureServer(server) {
  const m = /^(https?):\/\/(\[[0-9A-Fa-f:]+\]|[^/:]+)/.exec(String(server));
  if (!m) return false;
  if (m[1] === 'https') return true;
  return /^(localhost|127(\.\d{1,3}){3}|\[::1\])$/i.test(m[2]);
}

// Same server origin only: the agent never follows a redirect and builds URLs only from cfg.server + a fixed path.
function originOf(server) { return server.replace(/\/+$/, ''); }

const dataPaths = (dataDir) => ({
  dataDir,
  config: path.win32.join(dataDir, 'agent.json'),
  state: path.win32.join(dataDir, 'state.json'),
  keyFile: path.win32.join(dataDir, 'keys', 'x25519.key'),
  staging: path.win32.join(dataDir, 'staging'),
  previous: path.win32.join(dataDir, 'previous'),
  logs: path.win32.join(dataDir, 'logs'),
  lock: path.win32.join(dataDir, 'agent.lock'),
  publicDir: path.win32.join(dataDir, 'public'),
  status: path.win32.join(dataDir, 'public', 'status.json'),
  cert: path.win32.join(dataDir, 'owner.cer'),
});

// ---- Folder safety (checked at every start, not only at install).
// Principals every ordinary user belongs to. An ACE for one of them that grants anything beyond reading is unsafe.
const UNSAFE_ACE = /(BUILTIN\\Users|Everyone|NT AUTHORITY\\Authenticated Users|NT AUTHORITY\\INTERACTIVE|[A-Za-z0-9 _.-]+\\Domain Users|\*S-1-1-0|\*S-1-5-11|\*S-1-5-32-545):((?:\([A-Za-z0-9,]+\))+)/g;
const WRITE_RIGHT = new Set(['F', 'M', 'W', 'D', 'DE', 'DC', 'WD', 'AD', 'WA', 'WEA', 'WDAC', 'WO', 'GA', 'GW']);

// text: stdout of `icacls <path>` -> the unsafe ACEs found (empty = fine). Deny entries are ignored.
function unsafeAces(text) {
  const bad = [];
  for (const line of String(text).split(/\r?\n/)) {
    UNSAFE_ACE.lastIndex = 0;
    let m;
    while ((m = UNSAFE_ACE.exec(line)) !== null) {
      const tokens = [...m[2].matchAll(/\(([A-Za-z0-9,]+)\)/g)].map((t) => t[1]);
      if (tokens.includes('DENY') || tokens.includes('N')) continue;
      if (tokens.flatMap((t) => t.split(',')).some((r) => WRITE_RIGHT.has(r))) bad.push(m[1]);
    }
  }
  return bad;
}

// Environment that must not reach the agent (the scheduled task starts it clean; this is a second line).
function envProblems(env) {
  const bad = [];
  for (const k of Object.keys(env || {})) {
    if (/^NODE_(OPTIONS|PATH|EXTRA_CA_CERTS|TLS_REJECT_UNAUTHORIZED)$/i.test(k)) bad.push(k);
    else if (/^ELECTRON_/i.test(k) && !/^ELECTRON_RUN_AS_NODE$/i.test(k)) bad.push(k);
  }
  return bad;
}

// Output of ps/Check-Folder.ps1: {"owner": "NT AUTHORITY\\SYSTEM", "reparse": ["..."], "ok": true}
// -> {ok:true} | {ok:false, reason}
// Windows rights that let a principal change the folder or its contents (FileSystemRights bits; a Generic* bit counts too).
const WRITE_MASK = 0x2 | 0x4 | 0x10 | 0x40 | 0x100 | 0x10000 | 0x40000 | 0x80000 | 0x10000000 | 0x40000000;
const SAFE_SIDS = new Set(['S-1-5-18', 'S-1-5-32-544']);          // SYSTEM, Administrators: the only principals that may write
function judgeFolder(text) {
  let r;
  try { r = JSON.parse(String(text).trim()); } catch (e) { return { ok: false, reason: 'folder check unreadable' }; }
  if (!r || typeof r.owner !== 'string') return { ok: false, reason: 'folder check unreadable' };
  if (typeof r.ownerSid === 'string') {
    // By SID: the account NAMES are translated on a non-English Windows, the SIDs are not.
    if (!SAFE_SIDS.has(r.ownerSid)) return { ok: false, reason: 'owner is not SYSTEM or Administrators' };
    if (!Array.isArray(r.aces)) return { ok: false, reason: 'folder check unreadable' };
    for (const a of r.aces) {
      if (!a || typeof a.sid !== 'string' || !Number.isFinite(a.rights)) return { ok: false, reason: 'folder check unreadable' };
      if (!SAFE_SIDS.has(a.sid) && (a.rights & WRITE_MASK) !== 0) return { ok: false, reason: 'another account can write to the data folder' };
    }
  } else if (!/^(NT AUTHORITY\\SYSTEM|BUILTIN\\Administrators)$/i.test(r.owner)) return { ok: false, reason: 'owner is not SYSTEM or Administrators' };
  if (Array.isArray(r.reparse) && r.reparse.length) return { ok: false, reason: 'reparse point inside the data folder' };
  if (!Array.isArray(r.reparse)) return { ok: false, reason: 'folder check unreadable' };
  return { ok: true };
}

// A path inside `root` made from a validated name only (no separators, no dots at the start).
function childPath(root, name) {
  if (typeof name !== 'string' || !/^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$/.test(name)) throw new Error('unsafe name');
  return path.win32.join(root, name);
}

module.exports = { isSecureServer, envProblems, sanitize, originOf, dataPaths, unsafeAces, judgeFolder, childPath, SWITCHES, SERVER_RE, TOKEN_RE };
