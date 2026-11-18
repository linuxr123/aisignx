'use strict';
// set_autologin, the pure part: which request the agent may act on, and what it hands to PowerShell.
// The password never goes into a command line or an environment variable: only into the child's stdin,
// as base64 of its UTF-8 bytes (so the console code page cannot damage it).
const ecies = require('./ecies');

const USER_RE = /^[A-Za-z0-9._ -]{1,64}$/;

// params: verified payload.params. cfg: sanitized agent.json. ctx: {displayId, requestId, priv: Buffer(32)}.
// -> {ok:true, enabled:false} | {ok:true, enabled:true, user, domain, password: Buffer} | {ok:false, reason}.
// Every failure inside the decryption says the same 'bad_params'.
function open(params, cfg, ctx) {
  if (!cfg.allow || cfg.allow.autologin !== true) return { ok: false, reason: 'not_allowed' };
  if (!cfg.kioskUser) return { ok: false, reason: 'not_allowed (no kiosk account fixed at install)' };
  if (params.enabled === false) return { ok: true, enabled: false };
  if (params.enabled !== true) return { ok: false, reason: 'bad_params' };
  // The agent writes autologin for exactly the account fixed at install, never for what the server names.
  if (params.user !== cfg.kioskUser || params.domain !== (cfg.kioskDomain || '.')) return { ok: false, reason: 'not_allowed (account)' };
  if (!USER_RE.test(cfg.kioskUser)) return { ok: false, reason: 'bad_params' };
  const mine = ecies.keyId(ecies.publicOf(ctx.priv));
  if (params.key_id !== mine) return { ok: false, reason: 'bad_params' };
  const plain = ecies.decrypt(ctx.priv, params.enc, ecies.aad(ctx.displayId, ctx.requestId, params.user, params.domain));
  if (!plain || plain.length < 1 || plain.length > 127 * 4 || plain.includes(0)) { if (plain) plain.fill(0); return { ok: false, reason: 'bad_params' }; }
  return { ok: true, enabled: true, user: cfg.kioskUser, domain: cfg.kioskDomain || '.', password: plain };
}

// What is written to the helper's stdin. One line, base64, newline at the end.
function stdinPayload(password) { return Buffer.from(password.toString('base64') + '\n', 'ascii'); }

// What the child process gets in its environment: names only, never the password.
function childEnv(user, domain) { return { AISIGNX_USER: user, AISIGNX_DOMAIN: domain }; }

module.exports = { open, stdinPayload, childEnv, USER_RE };
