'use strict';
const test = require('node:test');
const assert = require('node:assert');
const AC = require('../src/agent-svc/autologin');
const ecies = require('../src/agent-svc/ecies');
const V = require('../src/agent/verify');
const vec = require('./vectors/agent-vectors.json');

const priv = Buffer.from(vec.agent_private_hex, 'hex');
const cfg = (over) => Object.assign({ allow: { autologin: true }, kioskUser: 'kiosk', kioskDomain: '.' }, over);
const params = () => ({ enabled: true, user: 'kiosk', domain: '.', key_id: vec.key_id, enc: vec.ecies.envelope });
const ctx = () => ({ displayId: vec.display_id, requestId: vec.ecies.request_id, priv });

test('opens the envelope made by the server for the kiosk account', () => {
  const r = AC.open(params(), cfg(), ctx());
  assert.strictEqual(r.ok, true);
  assert.strictEqual(r.user, 'kiosk');
  assert.strictEqual(r.password.toString('utf8'), vec.ecies.plaintext);
});

test('the local switch and the kiosk account fixed at install are both required', () => {
  assert.strictEqual(AC.open(params(), cfg({ allow: { autologin: false } }), ctx()).reason, 'not_allowed');
  assert.strictEqual(AC.open(params(), cfg({ allow: {} }), ctx()).reason, 'not_allowed');
  assert.match(AC.open(params(), cfg({ kioskUser: '' }), ctx()).reason, /not_allowed/);
});

test('an account other than the fixed one is refused, whatever the server says', () => {
  assert.match(AC.open(Object.assign(params(), { user: 'admin' }), cfg(), ctx()).reason, /not_allowed/);
  assert.match(AC.open(Object.assign(params(), { domain: 'CORP' }), cfg(), ctx()).reason, /not_allowed/);
});

test('every decryption problem answers bad_params, the same words', () => {
  const reasons = new Set([
    AC.open(Object.assign(params(), { key_id: '0000000000000000' }), cfg(), ctx()).reason,
    AC.open(params(), cfg(), Object.assign(ctx(), { requestId: 'ffffffffffffffff' })).reason,
    AC.open(params(), cfg(), Object.assign(ctx(), { displayId: 8 })).reason,
    AC.open(Object.assign(params(), { enc: 'AAAA' }), cfg(), ctx()).reason,
    AC.open(params(), cfg(), Object.assign(ctx(), { priv: ecies.generateKeyPair().priv })).reason,
    AC.open({ enabled: 'yes' }, cfg(), ctx()).reason,
  ]);
  assert.deepStrictEqual([...reasons], ['bad_params']);
});

test('undo needs only the switch', () => {
  assert.deepStrictEqual(AC.open({ enabled: false }, cfg(), ctx()), { ok: true, enabled: false });
  assert.strictEqual(AC.open({ enabled: false }, cfg({ allow: { autologin: false } }), ctx()).ok, false);
});

test('the password never reaches argv or the environment, only stdin as base64', () => {
  const pw = Buffer.from('Sup3r secret ü!', 'utf8');
  const payload = AC.stdinPayload(pw).toString('ascii');
  assert.match(payload, /^[A-Za-z0-9+/=]+\n$/);
  assert.strictEqual(Buffer.from(payload.trim(), 'base64').toString('utf8'), 'Sup3r secret ü!');
  const env = AC.childEnv('kiosk', '.');
  assert.deepStrictEqual(Object.keys(env).sort(), ['AISIGNX_DOMAIN', 'AISIGNX_USER']);
  assert.ok(!JSON.stringify(env).includes('secret'));
});

test('verify.js knows set_autologin: typed params and the autologin switch', () => {
  assert.deepStrictEqual(V.checkParams('set_autologin', { enabled: false }), { ok: true, params: { enabled: false } });
  assert.strictEqual(V.checkParams('set_autologin', { enabled: false, extra: 1 }).ok, false);
  assert.strictEqual(V.checkParams('set_autologin', Object.assign(params(), { extra: 1 })).ok, false);
  assert.strictEqual(V.checkParams('set_autologin', Object.assign(params(), { user: 'a;b' })).ok, false);
  assert.strictEqual(V.checkParams('set_autologin', Object.assign(params(), { key_id: 'XYZ' })).ok, false);
  assert.strictEqual(V.checkParams('set_autologin', Object.assign(params(), { enc: 'a'.repeat(700) })).ok, false);
  assert.strictEqual(V.checkParams('set_autologin', params()).ok, true);
  assert.strictEqual(V.SWITCH_OF.set_autologin, 'autologin');
});
