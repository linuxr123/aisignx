'use strict';
// The first start after the admin's "Set up a Windows PC" script: take the prepared credential once.
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('fs');
const os = require('os');
const path = require('path');
const F = require('../src/firstrun');

const NOW = Date.UTC(2026, 10, 21, 12, 0, 0);
const TOKEN = 'abcDEF123_-abcDEF123_-abcDEF123_-abcDEF12345';
const good = (extra) => JSON.stringify(Object.assign({ server: 'http://192.168.1.19:5000', token: TOKEN, created: new Date(NOW - 60000).toISOString() }, extra));
const dirs = [];
const tmp = () => { const d = fs.mkdtempSync(path.join(os.tmpdir(), 'aisignx-firstrun-')); dirs.push(d); return d; };
test.after(() => { for (const d of dirs) fs.rmSync(d, { recursive: true, force: true }); });

test('the file lives in ProgramData\AiSignX', () => {
  assert.equal(F.setupFile({ ProgramData: 'D:\PD' }), path.join('D:\PD', 'AiSignX', 'setup.json'));
});

test('a good file gives the normalized server and the token', () => {
  assert.deepEqual(F.parseSetup(good(), NOW), { server: 'http://192.168.1.19:5000', token: TOKEN });
  assert.deepEqual(F.parseSetup('\uFEFF' + good({ server: 'https://signs.example.com/' }), NOW), { server: 'https://signs.example.com', token: TOKEN });
});

test('anything else is refused: no token, a bad token, a bad or hostile server, junk', () => {
  for (const bad of [
    good({ token: '' }), good({ token: 'short' }), good({ token: TOKEN + ' ;calc' }), good({ token: 5 }),
    good({ server: 'javascript:alert(1)' }), good({ server: 'http://user:pw@host' }), good({ server: 'http://host/path' }),
    good({ server: '' }), good({ server: undefined }), '{}', '[]', 'null', 'not json', '', 'x'.repeat(5000),
  ]) assert.equal(F.parseSetup(bad, NOW), null, String(bad).slice(0, 60));
  assert.equal(F.parseSetup(undefined, NOW), null);
});

test('an old file (an abandoned set-up) or one from the future is ignored', () => {
  assert.equal(F.parseSetup(good({ created: new Date(NOW - 25 * 3600 * 1000).toISOString() }), NOW), null);
  assert.equal(F.parseSetup(good({ created: new Date(NOW + 2 * 3600 * 1000).toISOString() }), NOW), null);
  assert.equal(F.parseSetup(good({ created: 'yesterday' }), NOW), null);
  assert.equal(F.parseSetup(good({ created: undefined }), NOW), null);
});

test('readSetup reads a real file, and consume deletes it (and is fine when it is already gone)', () => {
  const d = tmp();
  const f = path.join(d, 'setup.json');
  assert.equal(F.readSetup(f, NOW), null);                       // no file
  fs.writeFileSync(f, good());
  assert.deepEqual(F.readSetup(f, NOW), { server: 'http://192.168.1.19:5000', token: TOKEN });
  assert.equal(F.consume(f), true);
  assert.equal(fs.existsSync(f), false);
  assert.equal(F.consume(f), true);
  fs.writeFileSync(f, 'x'.repeat(5000));
  assert.equal(F.readSetup(f, NOW), null);                       // too big
  assert.equal(F.readSetup(d, NOW), null);                       // a folder, not a file
});

test('the cookie matches the one the server sets (name, path, HttpOnly, SameSite=Strict)', () => {
  const c = F.displayCookie('http://127.0.0.1:47801', TOKEN, NOW);
  assert.equal(c.name, 'aisignx_display');
  assert.equal(c.value, TOKEN);
  assert.equal(c.path, '/api/player');
  assert.equal(c.url, 'http://127.0.0.1:47801/api/player');
  assert.equal(c.httpOnly, true);
  assert.equal(c.sameSite, 'strict');
  assert.ok(c.expirationDate > NOW / 1000 + 9 * 365 * 24 * 3600);
});
