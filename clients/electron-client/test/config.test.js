'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('fs');
const os = require('os');
const path = require('path');
const config = require('../src/config');

function tmp() {
  return fs.mkdtempSync(path.join(os.tmpdir(), 'aisignx-cfg-'));
}

test('normalizeServerUrl accepts and normalizes addresses', () => {
  const n = config.normalizeServerUrl;
  assert.equal(n('192.168.1.20:5000'), 'http://192.168.1.20:5000');
  assert.equal(n('  https://Signage.Example.LAN/  '), 'https://signage.example.lan');
  assert.equal(n('http://host:80'), 'http://host');
  assert.equal(n('localhost:5000'), 'http://localhost:5000');
  assert.equal(n('http://[::1]:5000'), 'http://[::1]:5000');
  assert.equal(n('https://host:8443//'), 'https://host:8443');
});

test('normalizeServerUrl rejects bad addresses', () => {
  const n = config.normalizeServerUrl;
  for (const bad of ['', '   ', 'ftp://host', 'http://user:pw@host', 'http://user@host', 'http://host/path',
    'http://host/?a=1', 'http://host/#x', 'http://host?x', 'javascript:alert(1)', 'http://']) {
    assert.throws(() => n(bad), Error, bad);
  }
});

test('saveConfig writes atomically and loadConfig reads it back', () => {
  const dir = tmp();
  const pin = { sha256: Array(32).fill('AB').join(':'), caPem: '-----BEGIN CERTIFICATE-----\nx\n-----END CERTIFICATE-----\n' };
  const saved = config.saveConfig(dir, { serverUrl: '10.0.0.5:5000', port: 47800, certPin: pin, autostart: false });
  assert.equal(saved.serverUrl, 'http://10.0.0.5:5000');
  assert.ok(!fs.existsSync(path.join(dir, 'config.json.tmp')));
  const { config: cfg, status } = config.loadConfig(dir);
  assert.equal(status, 'ok');
  assert.deepEqual(cfg, { serverUrl: 'http://10.0.0.5:5000', port: 47800, certPin: pin, autostart: false });
  assert.ok(!JSON.stringify(cfg).toLowerCase().includes('token'));
});

test('loadConfig reports a missing file', () => {
  const r = config.loadConfig(tmp());
  assert.equal(r.status, 'missing');
  assert.equal(r.config.serverUrl, '');
});

test('a corrupt config is renamed to .bad and defaults are returned', () => {
  const dir = tmp();
  fs.writeFileSync(path.join(dir, 'config.json'), '{not json');
  const r = config.loadConfig(dir);
  assert.equal(r.status, 'corrupt');
  assert.equal(r.config.serverUrl, '');
  assert.ok(fs.existsSync(path.join(dir, 'config.json.bad')));
  assert.ok(!fs.existsSync(path.join(dir, 'config.json')));

  fs.writeFileSync(path.join(dir, 'config.json'), JSON.stringify({ serverUrl: 'http://h', port: 'x' }));
  assert.equal(config.loadConfig(dir).status, 'corrupt');
});

test('client_id is 32 hex characters and stable', () => {
  const dir = tmp();
  const a = config.clientId(dir);
  assert.match(a, /^[0-9a-f]{32}$/);
  assert.equal(config.clientId(dir), a);
  fs.writeFileSync(path.join(dir, 'client_id.txt'), 'garbage');
  const b = config.clientId(dir);
  assert.match(b, /^[0-9a-f]{32}$/);
  assert.notEqual(b, 'garbage');
});
