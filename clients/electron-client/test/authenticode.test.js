'use strict';
const test = require('node:test');
const assert = require('node:assert');
const A = require('../src/agent-svc/authenticode');

const PIN = '386BFBC220743F420E64AB481C4249EB1DE9C4BC';
const NOW = Date.parse('2026-11-17T00:00:00Z');
const good = () => ({ status: 'Valid', thumbprint: PIN, notAfter: '2029-11-15T16:13:40.0000000Z', timestamped: false, eku: ['1.3.6.1.5.5.7.3.3'], sigAlg: 'sha256RSA' });
const j = (o, pin) => A.judge(JSON.stringify(o), pin || PIN, NOW);

test('accepts Valid + the pinned thumbprint + code signing + sha256', () => {
  assert.deepStrictEqual(j(good()), { ok: true });
  assert.deepStrictEqual(j(Object.assign(good(), { thumbprint: PIN.toLowerCase() })), { ok: true });          // case does not matter
  assert.deepStrictEqual(A.judge('  ' + JSON.stringify(good()) + '\r\n', PIN.toLowerCase(), NOW), { ok: true });
});

test('rejects every status that is not Valid (a matching thumbprint is not enough)', () => {
  for (const status of ['UnknownError', 'HashMismatch', 'NotSigned', 'NotTrusted', 'Incompatible', 'NotSupportedFileFormat', 'Error', '']) {
    const r = j(Object.assign(good(), { status }));
    assert.strictEqual(r.ok, false, status);
  }
  assert.match(j(Object.assign(good(), { status: 'NotSigned' })).reason, /not signed/);
});

test('rejects another signer, including one that differs by a single character', () => {
  const near = PIN.slice(0, 39) + (PIN[39] === '0' ? '1' : '0');
  assert.strictEqual(j(Object.assign(good(), { thumbprint: near })).reason, 'wrong_signer');
  assert.strictEqual(j(Object.assign(good(), { thumbprint: 'A'.repeat(40) })).reason, 'wrong_signer');
  assert.strictEqual(j(Object.assign(good(), { thumbprint: PIN + '0' })).ok, false);
  assert.strictEqual(j(Object.assign(good(), { thumbprint: '' })).ok, false);
});

test('rejects a missing or malformed pin', () => {
  for (const pin of ['', null, undefined, 'short', PIN + 'AA', 'Z'.repeat(40)]) assert.strictEqual(A.judge(JSON.stringify(good()), pin, NOW).ok, false);
});

test('rejects a certificate without the code signing purpose or with a weak digest', () => {
  assert.strictEqual(j(Object.assign(good(), { eku: [] })).ok, false);
  assert.strictEqual(j(Object.assign(good(), { eku: ['1.3.6.1.5.5.7.3.2'] })).ok, false);
  assert.strictEqual(j(Object.assign(good(), { eku: 'x' })).ok, false);
  assert.strictEqual(j(Object.assign(good(), { sigAlg: 'sha1RSA' })).ok, false);
  assert.strictEqual(j(Object.assign(good(), { sigAlg: 'md5RSA' })).ok, false);
});

test('an expired certificate fails without a timestamp and passes with one', () => {
  const old = { notAfter: '2025-01-01T00:00:00.0000000Z' };
  assert.strictEqual(j(Object.assign(good(), old)).ok, false);
  assert.strictEqual(j(Object.assign(good(), old, { timestamped: true })).ok, true);
  assert.strictEqual(j(Object.assign(good(), { notAfter: 'garbage' })).ok, false);
});

test('garbage, extra output and wrong shapes are all refused', () => {
  for (const text of ['', 'not json', '[]', 'null', '5', '{"status":"Valid"}', 'x'.repeat(5000), JSON.stringify(good()) + '\n{"status":"Valid"}', undefined, null]) {
    assert.strictEqual(A.judge(text, PIN, NOW).ok, false, String(text).slice(0, 20));
  }
});

test('verifyFile runs the constant script with the file as the only variable argument and judges its output', async () => {
  let seen;
  const run = async (file, args) => { seen = { file, args }; return { ok: true, out: JSON.stringify(good()) }; };
  const r = await A.verifyFile('C:\\x\\staging\\a.exe', 'C:\\prog\\ps', PIN, NOW, run);
  assert.deepStrictEqual(r, { ok: true });
  assert.strictEqual(seen.file, 'powershell.exe');
  assert.deepStrictEqual(seen.args.slice(0, 5), ['-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass', '-File']);
  assert.ok(seen.args[5].endsWith('Verify-Signature.ps1'));
  assert.deepStrictEqual(seen.args.slice(6), ['-Path', 'C:\\x\\staging\\a.exe']);
  assert.ok(!seen.args.join(' ').includes(PIN));                                   // the thumbprint is never put into a command line
  const failed = await A.verifyFile('f', 'd', PIN, NOW, async () => ({ ok: false, out: '' }));
  assert.strictEqual(failed.ok, false);
});
