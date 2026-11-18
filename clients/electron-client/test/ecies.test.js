'use strict';
const test = require('node:test');
const assert = require('node:assert');
const crypto = require('crypto');
const ecies = require('../src/agent-svc/ecies');
const vec = require('./vectors/agent-vectors.json');

const priv = Buffer.from(vec.agent_private_hex, 'hex');
const E = vec.ecies;
const AAD = ecies.aad(E.display_id, E.request_id, E.user, E.domain);

test('interop: the envelope made by the Python server code opens in node', () => {
  assert.strictEqual(AAD.toString(), E.aad);                       // both sides build the same associated data
  assert.strictEqual(Buffer.from(ecies.publicOf(priv)).toString('base64'), vec.agent_public_b64);
  assert.strictEqual(ecies.keyId(ecies.publicOf(priv)), vec.key_id);
  const plain = ecies.decrypt(priv, E.envelope, AAD);
  assert.ok(plain);
  assert.strictEqual(plain.toString('utf8'), E.plaintext);         // includes a non-ASCII character
});

test('interop: node encrypts to the same format the vectors use (fixed ephemeral key and nonce gives the same bytes)', () => {
  const eph = Buffer.from(Array.from({ length: 32 }, (_, i) => 101 + i));
  const nonce = Buffer.from(Array.from({ length: 12 }, (_, i) => 201 + i));
  const env = ecies.encrypt(Buffer.from(vec.agent_public_b64, 'base64'), Buffer.from(E.plaintext, 'utf8'), AAD, eph, nonce);
  assert.strictEqual(env, E.envelope);
});

test('round trip with random keys', () => {
  const k = ecies.generateKeyPair();
  const env = ecies.encrypt(k.pub, Buffer.from('pässword'), AAD);
  assert.strictEqual(ecies.decrypt(k.priv, env, AAD).toString(), 'pässword');
  assert.notStrictEqual(env, ecies.encrypt(k.pub, Buffer.from('pässword'), AAD));   // fresh ephemeral key and nonce each time
});

test('every failure answers null: wrong key, wrong display/request/user/domain, flipped bit, truncation, junk', () => {
  const other = ecies.generateKeyPair();
  assert.strictEqual(ecies.decrypt(other.priv, E.envelope, AAD), null);
  for (const a of [ecies.aad(8, E.request_id, E.user, E.domain), ecies.aad(E.display_id, 'ffffffffffffffff', E.user, E.domain),
    ecies.aad(E.display_id, E.request_id, 'other', E.domain), ecies.aad(E.display_id, E.request_id, E.user, 'DOM')]) {
    assert.strictEqual(ecies.decrypt(priv, E.envelope, a), null);
  }
  const raw = Buffer.from(E.envelope, 'base64');
  for (const pos of [0, 20, 33, 50, raw.length - 1]) {
    const bad = Buffer.from(raw); bad[pos] ^= 1;
    assert.strictEqual(ecies.decrypt(priv, bad.toString('base64'), AAD), null, 'byte ' + pos);
  }
  assert.strictEqual(ecies.decrypt(priv, raw.subarray(0, raw.length - 1).toString('base64'), AAD), null);
  assert.strictEqual(ecies.decrypt(priv, Buffer.alloc(32 + 12 + 16).toString('base64'), AAD), null);      // empty plaintext
  assert.strictEqual(ecies.decrypt(priv, Buffer.alloc(32 + 12 + 16 + 300, 1).toString('base64'), AAD), null);   // oversized
  for (const junk of ['', 'not base64!', 5, null, undefined, 'A'.repeat(700), '====']) assert.strictEqual(ecies.decrypt(priv, junk, AAD), null);
});

test('an all-zero shared secret (low-order ephemeral key) is refused', () => {
  const blob = Buffer.concat([Buffer.alloc(32), crypto.randomBytes(12), crypto.randomBytes(40)]);
  assert.strictEqual(ecies.decrypt(priv, blob.toString('base64'), AAD), null);
});

test('encrypt refuses empty or oversized plaintext', () => {
  const k = ecies.generateKeyPair();
  assert.throws(() => ecies.encrypt(k.pub, Buffer.alloc(0), AAD));
  assert.throws(() => ecies.encrypt(k.pub, Buffer.alloc(ecies.MAX_PLAINTEXT + 1), AAD));
});
