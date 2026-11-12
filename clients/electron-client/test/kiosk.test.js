'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('fs');
const os = require('os');
const path = require('path');
const kiosk = require('../src/kiosk');
const { isPrivateHost } = require('../src/config');

const key = (k, mods) => Object.assign({ type: 'keyDown', key: k }, mods);

test('kiosk blocks reload, devtools, close and zoom keys', () => {
  for (const input of [key('F5'), key('F11'), key('F12'), key('I', { control: true, shift: true }),
    key('r', { control: true }), key('w', { control: true }), key('F4', { alt: true }),
    key('=', { control: true }), key('ArrowLeft', { alt: true }), key('Backspace')]) {
    assert.equal(kiosk.isBlocked(input), true, JSON.stringify(input));
  }
});

test('Ctrl+Alt combinations and plain keys pass', () => {
  assert.equal(kiosk.isBlocked(key('s', { control: true, alt: true })), false);
  assert.equal(kiosk.isBlocked(key('q', { control: true, alt: true })), false);
  assert.equal(kiosk.isBlocked(key('a')), false);
  assert.equal(kiosk.isBlocked(Object.assign(key('F5'), { type: 'keyUp' })), false);
});

test('autostart is skipped for a dev checkout and writes an XDG entry for an AppImage', () => {
  assert.equal(kiosk.applyAutostart(true, { app: { isPackaged: false } }), 'skipped');
  const home = fs.mkdtempSync(path.join(os.tmpdir(), 'aisignx-home-'));
  const opts = { app: { isPackaged: true }, platform: 'linux', env: { APPIMAGE: '/opt/AiSignX Player.AppImage' }, home };
  assert.equal(kiosk.applyAutostart(true, opts), 'xdg');
  const file = path.join(home, '.config', 'autostart', 'aisignx-player.desktop');
  assert.match(fs.readFileSync(file, 'utf8'), /^Exec="\/opt\/AiSignX Player\.AppImage"$/m);
  kiosk.applyAutostart(false, opts);
  assert.ok(!fs.existsSync(file));
  let calls = null;
  const winApp = { isPackaged: true, setLoginItemSettings: (s) => { calls = s; } };
  assert.equal(kiosk.applyAutostart(true, { app: winApp, platform: 'win32' }), 'login-item');
  assert.equal(calls.openAtLogin, true);
});

test('isPrivateHost tells LAN addresses from public ones', () => {
  for (const h of ['192.168.1.20', '10.0.0.1', '172.16.5.4', '127.0.0.1', 'localhost', 'signage', 'tv.lan', '[::1]', 'fd12:3456::1']) {
    assert.equal(isPrivateHost(h), true, h);
  }
  for (const h of ['8.8.8.8', '172.32.0.1', 'signage.example.com', '2001:db8::1']) {
    assert.equal(isPrivateHost(h), false, h);
  }
});
