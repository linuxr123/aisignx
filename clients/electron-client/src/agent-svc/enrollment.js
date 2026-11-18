'use strict';
// --init and --enroll, run once by the elevated install script. The script writes its settings to a params file
// INSIDE the protected data folder (no secret or text from the server ever goes through a command line); the agent
// reads it, enrolls with the one-time code, compares the server's command key with the fingerprint the admin typed
// (so there is no trust-on-first-use window) and only then writes agent.json.
const fs = require('fs');
const path = require('path');
const V = require('../agent/verify');
const task = require('../agent/task');
const ecies = require('./ecies');
const cfgm = require('./config');

const norm = (s) => String(s).replace(/[\s:-]/g, '').toLowerCase();

// Makes the X25519 key pair when there is none. -> public key (base64). The private key is a file in the protected folder.
function init(paths) {
  fs.mkdirSync(path.dirname(paths.keyFile), { recursive: true });
  let priv = null;
  try { priv = Buffer.from(fs.readFileSync(paths.keyFile, 'utf8').trim(), 'base64'); } catch (e) { priv = null; }
  if (!priv || priv.length !== 32) {
    const k = ecies.generateKeyPair();
    fs.writeFileSync(paths.keyFile, k.priv.toString('base64'));
    priv = k.priv;
  }
  return Buffer.from(ecies.publicOf(priv)).toString('base64');
}

function readKey(paths) {
  const priv = Buffer.from(fs.readFileSync(paths.keyFile, 'utf8').trim(), 'base64');
  if (priv.length !== 32) throw new Error('bad key file');
  return priv;
}

// params: parsed params file. request: http requester. -> {ok:true, cfg} | {ok:false, reason}. Writes nothing on failure.
async function enroll(params, paths, request, extra) {
  const bad = (reason) => ({ ok: false, reason });
  if (!params || typeof params !== 'object') return bad('no parameters');
  if (typeof params.server !== 'string' || !cfgm.SERVER_RE.test(params.server)) return bad('bad server address');
  if (!cfgm.isSecureServer(params.server) && params.allowInsecureHttp !== true) return bad('the server address is plain http (the install script needs -AllowInsecureHttp)');
  if (typeof params.code !== 'string' || !/^[A-Z2-9-]{16,24}$/i.test(params.code)) return bad('bad enrollment code');
  if (typeof params.fingerprint !== 'string' || norm(params.fingerprint).length !== 16) return bad('bad key fingerprint');
  const pub = init(paths);
  const body = Buffer.from(JSON.stringify({ code: params.code, public_key: pub, host: extra.host, version: extra.version }));
  let res;
  try {
    res = await request({ method: 'POST', path: '/api/player/v1/agent/enroll', headers: { 'content-type': 'application/json', 'content-length': String(body.length) },
      body: require('stream').Readable.from([body]), timeoutMs: 20000 });
  } catch (e) { return bad('cannot reach the server'); }
  const chunks = []; let size = 0;
  for await (const c of res) { size += c.length; if (size > 16384) { res.destroy(); break; } chunks.push(c); }
  let data = null;
  try { data = JSON.parse(Buffer.concat(chunks).toString('utf8')); } catch (e) { data = null; }
  if (res.statusCode !== 200 || !data) return bad('the server refused the code (' + res.statusCode + ')');
  const key = V.decodeB64(data.command_key, 32);
  if (!key) return bad('the server sent no usable command key');
  if (norm(V.fingerprint(key)) !== norm(params.fingerprint)) return bad('the server\'s command key does not match the fingerprint you gave');
  const cfg = cfgm.sanitize({ server: params.server.replace(/\/+$/, ''), token: data.agent_token, displayId: data.display_id, commandKey: data.command_key,
    thumbprint: params.thumbprint, allow: params.allow, trustScope: params.trustScope, kioskUser: params.kioskUser, kioskSid: params.kioskSid, playerExe: params.playerExe });
  if (!cfg) return bad('the answer or the parameters are not valid');
  task.writeJsonAtomic(paths.config, cfg);
  return { ok: true, cfg };
}

module.exports = { init, enroll, readKey, norm };
