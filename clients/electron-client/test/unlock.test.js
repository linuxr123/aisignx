'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('fs');
const os = require('os');
const path = require('path');
const unlock = require('../src/unlock');
const { nextConfig } = require('../src/setup');
const { createUnlockWindow } = require('../src/unlock-window');

const SALT = '000102030405060708090a0b0c0d0e0f';
// Shared with the server (Python) and Android (Kotlin) tests.
const V200K = { alg: 'pbkdf2-sha256', iter: 200000, salt: SALT,
  hash: '37cff31b6ad22b037e276408739e6702ab1a83eca9924fef485e1089b3c3ce2d' };
const V1K = { alg: 'pbkdf2-sha256', iter: 1000, salt: SALT,
  hash: '42b99a197a7d9cafa9fa5b60091da410edb6f3c1cc4e159a5f901b5348abb716' };

function tmp() {
  return fs.mkdtempSync(path.join(os.tmpdir(), 'aisignx-unlock-'));
}

test('sanitizeUnlock keeps a valid hash and drops anything else', () => {
  const s = unlock.sanitizeUnlock;
  assert.deepEqual(s(Object.assign({ extra: 1 }, V200K)), V200K);
  for (const bad of [null, undefined, 'x', 5, [], {}, Object.assign({}, V200K, { alg: 'md5' }),
    Object.assign({}, V200K, { iter: 9999 }), Object.assign({}, V200K, { iter: 1000001 }),
    Object.assign({}, V200K, { iter: '200000' }), Object.assign({}, V200K, { iter: 200000.5 }),
    Object.assign({}, V200K, { salt: SALT.toUpperCase() }), Object.assign({}, V200K, { salt: SALT + '00' }),
    Object.assign({}, V200K, { hash: V200K.hash.slice(1) }), Object.assign({}, V200K, { hash: null }), V1K]) {
    assert.equal(s(bad), null, JSON.stringify(bad));
  }
});

test('verifyPin matches the shared PBKDF2 vectors', async () => {
  assert.equal(await unlock.verifyPin('2468', V200K), true);
  assert.equal(await unlock.verifyPin('2469', V200K), false);
  assert.equal(await unlock.verifyPin('12345678', V1K), true);
  assert.equal(await unlock.verifyPin('1234567', V1K), false);
  for (const bad of ['', '12a4', ' 2468', '1234567890123', 2468, null]) {
    assert.equal(await unlock.verifyPin(bad, V1K), false, String(bad));
  }
  assert.equal(await unlock.verifyPin('2468', null), false);
});

test('waitMs: none for four failures, then 60 s doubling to 15 min', () => {
  assert.deepEqual([0, 1, 4].map(unlock.waitMs), [0, 0, 0]);
  assert.deepEqual([5, 6, 7, 8, 9, 10, 50, 1000].map((n) => unlock.waitMs(n) / 1000),
    [60, 120, 240, 480, 900, 900, 900, 900]);
});

test('the gate locks after five wrong PINs and keeps the lock across restarts', async () => {
  const dir = tmp();
  let t = 1000000;
  const now = () => t;
  const gate = unlock.createGate({ dataDir: dir, now });
  assert.deepEqual(gate.status(), { waitMs: 0 });
  for (let i = 1; i <= 4; i++) assert.deepEqual(await gate.attempt('1111', V1K), { ok: false, waitMs: 0 });
  assert.deepEqual(await gate.attempt('1111', V1K), { ok: false, waitMs: 60000 });

  // While locked, even the right PIN is not tried.
  t += 30000;
  assert.deepEqual(await gate.attempt('12345678', V1K), { ok: false, waitMs: 30000 });

  // A new gate (app restart) reads the same state.
  const again = unlock.createGate({ dataDir: dir, now });
  assert.deepEqual(again.status(), { waitMs: 30000 });
  assert.equal(again.failures, 5);

  t += 30000;
  assert.deepEqual(await again.attempt('1111', V1K), { ok: false, waitMs: 120000 });
  t += 120000;
  assert.deepEqual(await again.attempt('12345678', V1K), { ok: true, waitMs: 0 });
  assert.equal(unlock.createGate({ dataDir: dir, now }).failures, 0);
});

test('the gate caps a lock from a clock that went backwards and survives a corrupt file', async () => {
  const dir = tmp();
  fs.writeFileSync(path.join(dir, 'unlock-state.json'), JSON.stringify({ failures: 9, lockedUntil: 10 ** 12 }));
  assert.equal(unlock.createGate({ dataDir: dir, now: () => 0 }).status().waitMs, unlock.MAX_WAIT_MS);
  fs.writeFileSync(path.join(dir, 'unlock-state.json'), '{nope');
  const gate = unlock.createGate({ dataDir: dir });
  assert.equal(gate.failures, 0);
  gate.reset();
  assert.deepEqual(JSON.parse(fs.readFileSync(path.join(dir, 'unlock-state.json'), 'utf8')),
    { failures: 0, lockedUntil: 0 });
});

test('without a PIN the shortcut runs at once', () => {
  const w = createUnlockWindow({ getUnlock: () => null, gate: unlock.createGate({ dataDir: tmp() }) });
  let ran = 0;
  w.guard('quit', () => { ran += 1; })();
  w.requirePin('setup', () => { ran += 1; });
  assert.equal(ran, 2);
  assert.equal(w.window, null);
});

test('a setup save keeps the unlock PIN; a new server clears it', () => {
  const prev = { serverUrl: 'http://a', port: 47800, certPin: null, autostart: true, unlock: V200K };
  const fields = { serverUrl: 'http://a', port: 47800, certPin: null, autostart: false };
  assert.deepEqual(nextConfig(prev, fields, false), Object.assign({}, fields, { unlock: V200K }));
  assert.equal(nextConfig(prev, Object.assign({}, fields, { serverUrl: 'http://b' }), true).unlock, null);
});
