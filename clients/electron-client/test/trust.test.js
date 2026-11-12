'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const https = require('https');
const crypto = require('crypto');
const selfsigned = require('selfsigned');
const trust = require('../src/trust');
const { checkServer } = require('../src/health');

function makeCert(cn) {
  const pems = selfsigned.generate([{ name: 'commonName', value: cn }],
    { days: 30, keySize: 2048, algorithm: 'sha256' });
  const fp = new crypto.X509Certificate(pems.cert).fingerprint256;
  return { key: pems.private, cert: pems.cert, fp };
}

const certA = makeCert('lan-a');
const certB = makeCert('lan-b');

async function server(t, c) {
  const s = https.createServer({ key: c.key, cert: c.cert }, (req, res) => {
    res.writeHead(200, { 'Content-Type': 'application/json' });
    res.end(JSON.stringify({ status: 'ok', app: 'aisignx', api: 1 }));
  });
  await new Promise((r) => s.listen(0, '127.0.0.1', r));
  t.after(() => { s.closeAllConnections(); s.close(); });
  return 'https://127.0.0.1:' + s.address().port;
}

function get(url, agent) {
  return new Promise((resolve, reject) => {
    https.get(url + '/healthz', { agent }, (res) => {
      res.resume();
      res.on('end', () => resolve(res.statusCode));
    }).on('error', reject);
  });
}

test('a matching pin connects even though the hostname is not in the SAN', async (t) => {
  const url = await server(t, certA);
  const agent = trust.agentFor({ serverUrl: url, certPin: { sha256: certA.fp, caPem: certA.cert } });
  assert.equal(await get(url, agent), 200);
  agent.destroy();
});

test('a pin mismatch is rejected and classified as tls-pin-mismatch', async (t) => {
  const url = await server(t, certB);
  const cfg = { serverUrl: url, certPin: { sha256: certA.fp, caPem: certA.cert } };
  const agent = trust.agentFor(cfg);
  const err = await get(url, agent).then(() => null, (e) => e);
  assert.ok(err, 'expected a TLS error');
  assert.equal(trust.classify(err, cfg), 'tls-pin-mismatch');
  agent.destroy();
});

test('no pin plus a self-signed certificate is rejected', async (t) => {
  const url = await server(t, certA);
  const cfg = { serverUrl: url, certPin: null };
  const agent = trust.agentFor(cfg);
  const err = await get(url, agent).then(() => null, (e) => e);
  assert.ok(err);
  assert.equal(trust.classify(err, cfg), 'tls-untrusted');
  agent.destroy();
});

test('capture() returns the fingerprint and the self-signed CA', async (t) => {
  const url = await server(t, certA);
  const card = await trust.capture(url);
  assert.equal(card.fingerprint, certA.fp);
  assert.match(card.subject, /lan-a/);
  assert.equal(card.trusted, false);
  assert.equal(card.pinnable, true);
  assert.equal(new crypto.X509Certificate(card.caPem).fingerprint256, certA.fp);
});

test('checkServer reports untrusted with a card, then ok with the pin', async (t) => {
  const url = await server(t, certA);
  const first = await checkServer({ serverUrl: url, certPin: null });
  assert.equal(first.result, 'untrusted');
  assert.equal(first.card.fingerprint, certA.fp);
  const second = await checkServer({ serverUrl: url, certPin: { sha256: certA.fp, caPem: first.card.caPem } });
  assert.equal(second.result, 'ok');
});

test('checkServer reports unreachable for a closed port', async () => {
  const r = await checkServer({ serverUrl: 'http://127.0.0.1:1', certPin: null });
  assert.equal(r.result, 'unreachable');
});
