'use strict';
// ECIES-style envelope for the auto-login password. Twin of server/aisignx/displays/ecies.py (same vectors).
//   shared   = X25519(eph_private, agent_public)
//   key      = HKDF-SHA256(shared, salt = eph_public || agent_public, info = 'aisignx-autologin-v1', 32)
//   envelope = base64( eph_public(32) || nonce(12) || AES-256-GCM ciphertext || tag(16) )
//   aad      = 'v1|<display_id>|<request_id>|<user>|<domain>'
// Pure node:crypto. decrypt() answers null for every kind of failure (one answer, no oracle).
const crypto = require('crypto');

const INFO = Buffer.from('aisignx-autologin-v1');
const PKCS8_X25519 = Buffer.from('302e020100300506032b656e04220420', 'hex');
const SPKI_X25519 = Buffer.from('302a300506032b656e032100', 'hex');
const MAX_PLAINTEXT = 256;
const B64_RE = /^[A-Za-z0-9+/]+={0,2}$/;

const privateKeyObject = (raw) => crypto.createPrivateKey({ key: Buffer.concat([PKCS8_X25519, raw]), format: 'der', type: 'pkcs8' });
const publicKeyObject = (raw) => crypto.createPublicKey({ key: Buffer.concat([SPKI_X25519, raw]), format: 'der', type: 'spki' });
const publicOf = (raw) => crypto.createPublicKey(privateKeyObject(raw)).export({ format: 'der', type: 'spki' }).subarray(-32);

function generateKeyPair() {
  const { privateKey } = crypto.generateKeyPairSync('x25519');
  const priv = privateKey.export({ format: 'der', type: 'pkcs8' }).subarray(-32);
  return { priv: Buffer.from(priv), pub: Buffer.from(publicOf(priv)) };
}

const aad = (displayId, requestId, user, domain) => Buffer.from('v1|' + displayId + '|' + requestId + '|' + user + '|' + domain, 'utf8');
const keyId = (pub) => crypto.createHash('sha256').update(pub).digest('hex').slice(0, 16);

function deriveKey(shared, ephPub, agentPub) {
  return Buffer.from(crypto.hkdfSync('sha256', shared, Buffer.concat([ephPub, agentPub]), INFO, 32));
}

// For tests and the Python vectors; the server is the real sender. eph/nonce optional.
function encrypt(agentPub, plaintext, associated, eph, nonce) {
  if (agentPub.length !== 32 || !plaintext.length || plaintext.length > MAX_PLAINTEXT) throw new Error('bad input');
  const e = eph || generateKeyPair();
  const ephPub = eph ? Buffer.from(publicOf(eph)) : e.pub;
  const ephPriv = eph || e.priv;
  const shared = crypto.diffieHellman({ privateKey: privateKeyObject(ephPriv), publicKey: publicKeyObject(agentPub) });
  const n = nonce || crypto.randomBytes(12);
  const c = crypto.createCipheriv('aes-256-gcm', deriveKey(shared, ephPub, agentPub), n);
  c.setAAD(associated);
  const ct = Buffer.concat([c.update(plaintext), c.final(), c.getAuthTag()]);
  return Buffer.concat([ephPub, n, ct]).toString('base64');
}

// agentPriv: Buffer(32). -> Buffer plaintext | null.
function decrypt(agentPriv, envelope, associated) {
  try {
    if (typeof envelope !== 'string' || envelope.length > 600 || !B64_RE.test(envelope) || envelope.length % 4 !== 0) return null;
    const blob = Buffer.from(envelope, 'base64');
    if (blob.length < 32 + 12 + 16 + 1 || blob.length > 32 + 12 + 16 + MAX_PLAINTEXT) return null;
    const ephPub = blob.subarray(0, 32), nonce = blob.subarray(32, 44), sealed = blob.subarray(44);
    const shared = crypto.diffieHellman({ privateKey: privateKeyObject(agentPriv), publicKey: publicKeyObject(ephPub) });
    if (shared.every((b) => b === 0)) return null;
    const d = crypto.createDecipheriv('aes-256-gcm', deriveKey(shared, ephPub, publicOf(agentPriv)), nonce);
    d.setAAD(associated);
    d.setAuthTag(sealed.subarray(sealed.length - 16));
    return Buffer.concat([d.update(sealed.subarray(0, sealed.length - 16)), d.final()]);
  } catch (e) { return null; }
}

module.exports = { generateKeyPair, publicOf, aad, keyId, encrypt, decrypt, MAX_PLAINTEXT };
