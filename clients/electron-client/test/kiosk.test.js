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

test('control keys map to player actions', () => {
  const table = {
    ' ': 'toggle', Enter: 'toggle', ArrowRight: 'forward', ArrowLeft: 'back', ArrowUp: 'prev', ArrowDown: 'next',
    PageUp: 'prev', PageDown: 'next', MediaPlayPause: 'toggle', MediaPlay: 'play', MediaPause: 'pause',
    MediaStop: 'pause', MediaTrackNext: 'next', MediaTrackPrevious: 'prev', MediaFastForward: 'forward',
    MediaRewind: 'back',
  };
  for (const [k, a] of Object.entries(table)) assert.deepEqual(kiosk.controlAction(key(k)), { a, r: false }, k);
  assert.deepEqual(kiosk.controlAction(key('ArrowRight', { isAutoRepeat: true })), { a: 'forward', r: true });
  assert.deepEqual(kiosk.controlAction(key('Enter', { shift: true })), { a: 'toggle', r: false });
});

test('control keys with Ctrl, Alt or Meta, key-up events and other keys are not actions', () => {
  for (const mods of [{ control: true }, { alt: true }, { meta: true }, { control: true, alt: true }]) {
    assert.equal(kiosk.controlAction(key('ArrowRight', mods)), null, JSON.stringify(mods));
  }
  assert.equal(kiosk.controlAction(Object.assign(key('ArrowRight'), { type: 'keyUp' })), null);
  for (const k of ['a', 'Escape', 'Tab', 'F5', 'constructor', 'toString', '']) assert.equal(kiosk.controlAction(key(k)), null, k);
  assert.equal(kiosk.controlAction(null), null);
});

function fakeContents() {
  const wc = { handlers: {}, on(name, fn) { this.handlers[name] = fn; },
    setVisualZoomLevelLimits: () => Promise.resolve(), setZoomFactor() {} };
  wc.press = (input) => {
    let prevented = false;
    wc.handlers['before-input-event']({ preventDefault: () => { prevented = true; } }, input);
    return prevented;
  };
  return wc;
}

test('control keys are forwarded only while controls are allowed', () => {
  const sent = [];
  let allowed = false;
  const wc = fakeContents();
  kiosk.attachKeyBlock(wc, { controls: () => allowed, onControl: (m) => sent.push(m) });
  assert.equal(wc.press(key('ArrowRight')), false); // passes to the page, which blocks it itself
  assert.deepEqual(sent, []);
  allowed = true;
  assert.equal(wc.press(key('ArrowRight')), true);
  assert.equal(wc.press(key('MediaTrackNext')), true);
  assert.equal(wc.press(key('a')), false);
  assert.equal(wc.press(key('F4', { alt: true })), true); // still blocked, never a control
  assert.equal(wc.press(key('ArrowLeft', { alt: true })), true);
  assert.deepEqual(sent, [{ a: 'forward', r: false }, { a: 'next', r: false }]);
});

test('attachKeyBlock without options only blocks', () => {
  const wc = fakeContents();
  kiosk.attachKeyBlock(wc);
  assert.equal(wc.press(key('F5')), true);
  assert.equal(wc.press(key('ArrowRight')), false);
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

// main.js and window.js need Electron at load time, so these are source checks.
test('no application menu is installed on the kiosk', () => {
  const src = (f) => fs.readFileSync(path.join(__dirname, '..', 'src', f), 'utf8');
  assert.match(src('main.js'), /^Menu\.setApplicationMenu\(null\);/m);
  assert.match(src('window.js'), /win\.removeMenu\(\);/);
  assert.doesNotMatch(src('window.js'), /autoHideMenuBar:\s*true/);
});

test('the port-in-use screen does not send the technician to setup', () => {
  const js = fs.readFileSync(path.join(__dirname, '..', 'src', 'ui', 'offline.js'), 'utf8');
  const line = js.split(/\r?\n/).find((l) => l.includes("'port-in-use':"));
  assert.ok(line);
  assert.doesNotMatch(line, /setup/i);
});
