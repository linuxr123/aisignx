'use strict';
// A fake world for the privileged agent: real files in a temp folder, a fake server, fake Windows effects.
const crypto = require('crypto');
const fs = require('fs');
const os = require('os');
const path = require('path');
const { Readable } = require('stream');
const { createCore } = require('../../src/agent-svc/core');
const state = require('../../src/agent-svc/state');
const cfgm = require('../../src/agent-svc/config');
const ecies = require('../../src/agent-svc/ecies');

const DISPLAY = 7;
const PIN = '386BFBC220743F420E64AB481C4249EB1DE9C4BC';
const sha = (b) => crypto.createHash('sha256').update(b).digest('hex');

// Signs like server/aisignx/displays/signing.py: compact JSON, sorted keys.
function makeSigner() {
  const { privateKey, publicKey } = crypto.generateKeyPairSync('ed25519');
  const raw = publicKey.export({ format: 'der', type: 'spki' }).subarray(-32);
  let seq = 1000;
  const sign = (id, action, params, over) => {
    const o = Object.assign({ action, display_id: DISPLAY, exp_ms: 2_000_000_000_000, params: sortKeys(params), request_id: id, seq: ++seq, v: 1 }, over || {});
    const payload = JSON.stringify(Object.fromEntries(Object.keys(o).sort().map((k) => [k, o[k]])));
    return { id, action, params: {}, signed: { payload, sig: crypto.sign(null, Buffer.from(payload), privateKey).toString('base64') } };
  };
  return { sign, keyB64: raw.toString('base64'), privateKey };
}
const sortKeys = (p) => Object.fromEntries(Object.keys(p).sort().map((k) => [k, p[k]]));

function stream(buf, status, headers) {
  const r = Readable.from([buf]);
  r.statusCode = status || 200;
  r.headers = headers || {};
  return r;
}

// opts: {allow, files: {commandId: Buffer}, signer, serverTime, cfg}
function harness(opts) {
  const o = opts || {};
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'asx-agent-'));
  const paths = cfgm.dataPaths(dir);
  for (const k of ['staging', 'previous', 'logs', 'publicDir']) fs.mkdirSync(paths[k], { recursive: true });
  const signer = o.signer || makeSigner();
  const agentKey = ecies.generateKeyPair();
  const cfg = cfgm.sanitize(Object.assign({ server: 'https://signage.example', token: 'agt_' + 'B'.repeat(43), displayId: DISPLAY, commandKey: signer.keyB64,
    thumbprint: PIN, allow: o.allow || { reboot: true, startup: true, update: true, autologin: true }, kioskUser: 'kiosk', kioskSid: 'S-1-5-21-1-2-3-1001',
    playerExe: 'C:\\Program Files\\AiSignX Player\\AiSignX Player.exe' }, o.cfg || {}));
  const w = { dir, paths, cfg, signer, agentKey, calls: [], acks: [], logs: [], clock: { t: 1_900_000_000_000 }, files: o.files || {},
    status: { player_version: '3.1.0', player_last_seen_s: 5 }, installed: '3.1.0', commands: [], serverTime: 1_900_000_000_000,
    sigResult: { ok: true }, installerResult: { ok: true, code: 0 }, installerRuns: [], keyOnServer: signer.keyB64, folderUnsafe: false, diags: [] };
  w.st = state.sanitize({});
  const rec = (name, ret) => (...a) => { w.calls.push([name].concat(a.map((x) => (Buffer.isBuffer(x) ? '<buf>' : x)))); return typeof ret === 'function' ? ret(...a) : ret; };
  w.ops = {
    reboot: rec('reboot', async () => ({ ok: true })),
    setStartup: rec('setStartup', async () => ({ autostart: true, mode: 'app' })),
    setAutologon: async (user, domain, password) => { w.calls.push(['setAutologon', user, domain]); w.passwordSeen = password.toString('utf8'); password.fill(0); return true; },
    clearAutologon: rec('clearAutologon', async () => true),
    verifySignature: async (file) => { w.calls.push(['verifySignature', path.basename(file)]); w.sigBytes = fs.readFileSync(file); return w.sigResult; },
    killPlayer: rec('killPlayer', async () => ({ ok: true })),
    launchPlayer: rec('launchPlayer', async () => ({ ok: true })),
    installedVersion: async () => w.installed,
    importCert: rec('importCert', async () => ({ ok: true })),
    folderCheck: async () => ({ ps: { out: JSON.stringify({ owner: 'BUILTIN\\Administrators', reparse: [] }) },
      acl: { out: w.folderUnsafe ? 'X BUILTIN\\Users:(OI)(CI)(F)' : 'X NT AUTHORITY\\SYSTEM:(OI)(CI)(F)' } }),
  };
  w.request = async (r) => {
    let body = '';
    if (r.body) for await (const c of r.body) body += c;
    const p = r.path.replace('/api/player/v1/agent', '');
    w.calls.push(['http', r.method, p]);
    if (r.headers.authorization !== 'Bearer ' + cfg.token) return stream(Buffer.from('{}'), 401);
    let m;
    if ((m = /^\/commands\/([0-9a-f]{16})\/ack$/.exec(p))) { const b = JSON.parse(body); w.acks.push(Object.assign({ id: m[1], at: w.calls.length }, b)); return stream(Buffer.from('{"ok":true}')); }
    if ((m = /^\/commands\/([0-9a-f]{16})\/package$/.exec(p))) {
      const f = w.files[m[1]];
      return f ? stream(f.body || f, f.status || 200, { 'content-length': String((f.body || f).length) }) : stream(Buffer.from('{}'), 404);
    }
    if (p === '/commands') return stream(Buffer.from(JSON.stringify({ commands: w.commands, server_time_ms: w.serverTime })));
    if (p === '/status') return stream(Buffer.from(JSON.stringify(w.status)));
    if (p === '/command-key') return stream(Buffer.from(JSON.stringify({ public_key: w.keyOnServer })));
    if (p === '/diagnostics') { w.diags.push(JSON.parse(body)); return stream(Buffer.from('{"ok":true}')); }
    return stream(Buffer.from('{}'), 404);
  };
  w.runInstaller = async (file, args) => { w.installerRuns.push({ file, args, bytes: fs.readFileSync(file) }); w.calls.push(['runInstaller', args.join(' ')]); return w.installerResult; };
  const log = { info: (...a) => w.logs.push(a.join(' ')), warn: (...a) => w.logs.push(a.join(' ')), error: (...a) => w.logs.push(a.join(' ')) };
  w.saves = 0;
  w.core = createCore({ cfg, paths, programDir: path.join(dir, 'prog'), version: '3.1.0', now: () => w.clock.t, log, request: w.request, ops: w.ops, st: w.st,
    started: w.clock.t, runInstaller: w.runInstaller, downloadDeadlineMs: 5000,
    saveState: (s) => { w.saves++; fs.writeFileSync(paths.state, JSON.stringify(state.sanitize(s))); w.saved = JSON.parse(fs.readFileSync(paths.state, 'utf8')); },
    readKey: () => agentKey.priv, writeStatus: (s) => { w.statusFile = s; } });
  w.cleanup = () => fs.rmSync(dir, { recursive: true, force: true });
  return w;
}

module.exports = { harness, makeSigner, sha, PIN, DISPLAY };
