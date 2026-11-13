'use strict';
// The PIN prompt with a stand-in for Electron (node --test runs each file in its own process,
// so the fake module stays here).
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('fs');
const os = require('os');
const path = require('path');

const handlers = {};
const windows = [];
class FakeWindow {
  constructor() {
    this.destroyed = false;
    this.events = {};
    this.webContents = { on() {}, setWindowOpenHandler() {} };
    windows.push(this);
  }
  setMenuBarVisibility() {}
  setAlwaysOnTop() {}
  once(name, fn) { if (name === 'ready-to-show') setImmediate(fn); }
  on(name, fn) { this.events[name] = fn; }
  loadFile() { return Promise.resolve(); }
  show() {}
  focus() {}
  isDestroyed() { return this.destroyed; }
  // Like Electron: close() is asynchronous, so the window still exists (and may still have
  // focus) when the action runs in setImmediate.
  close() { setTimeout(() => { this.destroyed = true; if (this.events.closed) this.events.closed(); }, 5); }
}
const electronPath = require.resolve('electron');
require.cache[electronPath] = {
  id: electronPath, filename: electronPath, loaded: true,
  exports: { BrowserWindow: FakeWindow, ipcMain: { handle: (name, fn) => { handlers[name] = fn; } } },
};

const unlock = require('../src/unlock');
const { createUnlockWindow, pickTarget } = require('../src/unlock-window');

const SALT = '000102030405060708090a0b0c0d0e0f';
const V1K = { alg: 'pbkdf2-sha256', iter: 1000, salt: SALT,
  hash: '42b99a197a7d9cafa9fa5b60091da410edb6f3c1cc4e159a5f901b5348abb716' };

function win() { return { destroyed: false, isDestroyed() { return this.destroyed; } }; }

test('pickTarget: the focused window, never the prompt', () => {
  const setup = win(), player = win(), prompt = win();
  assert.equal(pickTarget(setup, null, null, player), setup);
  assert.equal(pickTarget(null, null, null, player), player);
  assert.equal(pickTarget(prompt, prompt, setup, player), setup); // pressed again in the prompt
  assert.equal(pickTarget(prompt, prompt, null, player), player);
  setup.destroyed = true;
  assert.equal(pickTarget(prompt, prompt, setup, player), player);
  player.destroyed = true;
  assert.equal(pickTarget(null, null, null, player), null);
});

test('devtools after a correct PIN opens on the window focused when the shortcut fired', async () => {
  const dataDir = fs.mkdtempSync(path.join(os.tmpdir(), 'aisignx-unlockwin-'));
  const w = createUnlockWindow({ getUnlock: () => V1K, gate: unlock.createGate({ dataDir }) });
  const setup = win(), player = win();
  let focused = setup;
  const opened = [];
  const shortcut = w.guardWindow('devtools', { focused: () => focused, fallback: () => player },
    (target) => opened.push(target));

  shortcut();
  const prompt = w.window;
  assert.ok(prompt instanceof FakeWindow);
  assert.deepEqual(opened, []);
  focused = prompt; // the prompt takes focus, and keeps it while it closes
  shortcut();       // pressed again in the prompt: still the setup window

  const r = await handlers['unlock:try']({ sender: prompt.webContents }, '12345678');
  assert.equal(r.ok, true);
  await new Promise((resolve) => setImmediate(resolve));
  assert.equal(opened.length, 1);
  assert.equal(opened[0], setup);
  assert.notEqual(opened[0], prompt);
  await new Promise((resolve) => setTimeout(resolve, 10));
});

test('devtools without a PIN runs at once on the focused window', () => {
  const w = createUnlockWindow({ getUnlock: () => null, gate: unlock.createGate({ dataDir: fs.mkdtempSync(path.join(os.tmpdir(), 'aisignx-unlockwin-')) }) });
  const player = win();
  const opened = [];
  w.guardWindow('devtools', { focused: () => null, fallback: () => player }, (t) => opened.push(t))();
  assert.deepEqual(opened, [player]);
});
