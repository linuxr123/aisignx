'use strict';
// A freshly installed agent has no credential. It asks the server to be enrolled, the player of the same PC tells the
// server which agent is on its PC (through the claim id published below), an admin approves (or the admin ticked the box
// when pairing), and the agent collects its token, encrypted to its own key.
//
// pending.json (written by the elevated install, protected folder) holds what only an administrator may decide:
// the server address, the pinned thumbprint, what the web page may do, and the player's account. Nothing the player or the
// network says can change those. The claim id is a random value that is published in public\enroll-request.json (readable
// by users: it only names this PC's request); the poll secret never leaves this process and the server.
const crypto = require('crypto');
const fs = require('fs');
const path = require('path');
const task = require('../agent/task');
const V = require('../agent/verify');
const ecies = require('./ecies');
const cfgm = require('./config');

const REQUEST_LIFE_MS = 20 * 3600 * 1000;       // the server keeps a request for 24 h: ask for a new one before that
const sha = (s) => crypto.createHash('sha256').update(s).digest('hex');
const newId = () => crypto.randomBytes(24).toString('base64url');         // 32 characters
const claimDigest = (claimId) => sha('aisignx-agent-claim|' + claimId);
const aad = (claimId) => Buffer.from('aisignx-agent-approval|' + claimDigest(claimId), 'ascii');

const pathsOf = (paths) => ({
  pending: path.win32.join(paths.dataDir, 'pending.json'),
  request: path.win32.join(paths.dataDir, 'request.json'),
  published: path.win32.join(paths.publicDir, 'enroll-request.json'),
});

// raw: pending.json -> the install-time settings (checked like agent.json), or null.
function readPending(paths) {
  const raw = task.readJson(pathsOf(paths).pending);
  if (!raw || typeof raw !== 'object' || typeof raw.server !== 'string' || !cfgm.SERVER_RE.test(raw.server)) return null;
  if (!cfgm.isSecureServer(raw.server) && raw.allowInsecureHttp !== true) return null;
  return raw;
}

// The ids of the request in flight (kept in the protected folder so a restart keeps asking with the same ones).
function currentRequest(paths, now) {
  const p = pathsOf(paths);
  const saved = task.readJson(p.request);
  if (saved && typeof saved.claim === 'string' && typeof saved.poll === 'string' && Number.isFinite(saved.t) && now - saved.t < REQUEST_LIFE_MS && now >= saved.t) return saved;
  const fresh = { claim: newId(), poll: newId(), t: now };
  task.writeJsonAtomic(p.request, fresh);
  return fresh;
}

async function readJsonBody(res, limit) {
  const chunks = []; let size = 0;
  for await (const c of res) { size += c.length; if (size > limit) { res.destroy(); break; } chunks.push(c); }
  try { return JSON.parse(Buffer.concat(chunks).toString('utf8')); } catch (e) { return null; }
}

async function post(request, urlPath, obj) {
  const body = Buffer.from(JSON.stringify(obj));
  const res = await request({ method: 'POST', path: urlPath, headers: { 'content-type': 'application/json', 'content-length': String(body.length) },
    body: require('stream').Readable.from([body]), timeoutMs: 20000 });
  return { status: res.statusCode, data: await readJsonBody(res, 16384) };
}

// One round: (re)announce the request, publish the claim id, ask for the answer.
// -> {state: 'waiting'|'enrolled'|'rejected'|'error', reason?, cfg?}. Writes agent.json only on approval.
async function step(o) {
  const { paths, request, now, host, version, readKey, pubKey } = o;
  const pending = readPending(paths);
  if (!pending) return { state: 'error', reason: 'pending.json is missing or not valid' };
  const req = currentRequest(paths, now);
  fs.mkdirSync(paths.publicDir, { recursive: true });
  task.writeJsonAtomic(pathsOf(paths).published, { claim_id: req.claim, host, t: now });
  let r;
  try { r = await post(request, '/api/player/v1/agent/request', { claim_id: req.claim, poll_secret: req.poll, public_key: pubKey, host, version }); }
  catch (e) { return { state: 'error', reason: 'cannot reach the server' }; }
  if (r.status === 400) { fs.rmSync(pathsOf(paths).request, { force: true }); return { state: 'error', reason: 'the server refused the request; asking again with new ids' }; }
  if (r.status !== 200) return { state: 'error', reason: 'the server answered ' + r.status };
  let p;
  try { p = await post(request, '/api/player/v1/agent/poll', { poll_secret: req.poll }); } catch (e) { return { state: 'error', reason: 'cannot reach the server' }; }
  if (p.status === 404) { fs.rmSync(pathsOf(paths).request, { force: true }); return { state: 'error', reason: 'the request expired; asking again' }; }
  if (p.status !== 200 || !p.data) return { state: 'error', reason: 'the server answered ' + p.status };
  if (p.data.state === 'rejected') return { state: 'rejected' };
  if (p.data.state !== 'approved') return { state: 'waiting', claimed: !!p.data.claimed };
  const key = V.decodeB64(p.data.command_key, 32);
  if (!key) return { state: 'error', reason: 'the server sent no usable command key' };
  const plain = ecies.decrypt(readKey(), p.data.ciphertext, aad(req.claim));
  const token = plain && plain.toString('ascii');
  if (!token || !/^agt_[A-Za-z0-9_-]{43}$/.test(token)) return { state: 'error', reason: 'the approval could not be opened' };
  const cfg = cfgm.sanitize({ server: pending.server.replace(/\/+$/, ''), token, displayId: p.data.display_id, commandKey: p.data.command_key,
    thumbprint: pending.thumbprint, allow: pending.allow, trustScope: pending.trustScope, kioskUser: pending.kioskUser, kioskSid: pending.kioskSid, playerExe: pending.playerExe });
  if (!cfg) return { state: 'error', reason: 'the approval or the install settings are not valid' };
  task.writeJsonAtomic(paths.config, cfg);                 // the key fingerprint is shown to the admin on the display page; first contact is trusted over https
  for (const f of [pathsOf(paths).pending, pathsOf(paths).request, pathsOf(paths).published]) { try { fs.rmSync(f, { force: true }); } catch (e) { /* the install script cleans up */ } }
  return { state: 'enrolled', cfg, fingerprint: V.fingerprint(key) };
}

module.exports = { step, readPending, currentRequest, claimDigest, aad, pathsOf, REQUEST_LIFE_MS };
