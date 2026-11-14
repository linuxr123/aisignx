'use strict';
// The player window's Lock player behaviour, with a stand-in for Electron.
const test = require('node:test');
const assert = require('node:assert/strict');

class FakeWindow {
  constructor() {
    this.events = {};
    this.calls = [];
    this.focused = true;
    this.destroyed = false;
    const wcEvents = this.wcEvents = {};
    this.webContents = {
      on: (name, fn) => { wcEvents[name] = fn; },
      send: (ch, m) => this.calls.push(['send', ch, m]),
      setWindowOpenHandler() {}, setVisualZoomLevelLimits: () => Promise.resolve(), setZoomFactor() {},
      isLoading: () => true, isCrashed: () => false,
    };
  }
  on(name, fn) { this.events[name] = fn; }
  once(name, fn) { this.events[name] = fn; }
  emit(name, ...args) { if (this.events[name]) this.events[name](...args); }
  removeMenu() {}
  setMenuBarVisibility() {}
  isDestroyed() { return this.destroyed; }
  isFocused() { return this.focused; }
  setAlwaysOnTop(on, level) { this.calls.push(['top', on, level]); }
  show() { this.calls.push(['show']); }
  moveTop() {}
  focus() { this.calls.push(['focus']); this.focused = true; }
  restore() { this.calls.push(['restore']); }
}
let last = null;
const electronPath = require.resolve('electron');
require.cache[electronPath] = {
  id: electronPath, filename: electronPath, loaded: true,
  exports: { BrowserWindow: function (o) { last = new FakeWindow(o); return last; }, app: {} },
};
const { createPlayerWindow } = require('../src/window');
const { createLock } = require('../src/lock');

const wait = (ms) => new Promise((r) => setTimeout(r, ms));

function make(extra) {
  const lock = createLock();
  const state = { quitting: false, controls: true };
  const p = createPlayerWindow(Object.assign({ getOrigin: () => 'http://127.0.0.1:47800', lock,
    isQuitting: () => state.quitting, controls: () => state.controls }, extra));
  const w = last;
  const done = () => { w.destroyed = true; w.emit('closed'); };
  return { p, w, lock, state, done };
}

test('locked: close and minimize are blocked unless the app quits', () => {
  const { w, lock, state, done } = make();
  const close = () => { let prevented = false; w.emit('close', { preventDefault: () => { prevented = true; } }); return prevented; };
  assert.equal(close(), false);
  lock.configure({ kioskLock: true });
  assert.equal(close(), true);
  w.emit('minimize');
  assert.deepEqual(w.calls.at(-1), ['restore']);
  state.quitting = true;
  assert.equal(close(), false);
  lock.unlockFor(5, '0123456789abcdef');
  state.quitting = false;
  assert.equal(close(), false);
  done();
});

test('locked: the window stays on top below the PIN prompt and refocuses unless suspended', async () => {
  const { p, w, lock, done } = make();
  lock.configure({ kioskLock: true });
  p.applyLockLevel();
  assert.deepEqual(w.calls.at(-1), ['top', true, 'pop-up-menu']);
  w.focused = false;
  w.emit('blur');
  await wait(350);
  assert.ok(w.calls.some((c) => c[0] === 'focus'));
  w.calls.length = 0;
  p.suspend(true, 'unlock');
  assert.deepEqual(w.calls.at(-1), ['top', false, undefined]);
  w.focused = false;
  w.emit('blur');
  await wait(350);
  assert.ok(!w.calls.some((c) => c[0] === 'focus'));
  p.suspend(false, 'unlock');
  assert.deepEqual(w.calls.at(-1), ['top', true, 'pop-up-menu']);
  lock.configure({ kioskLock: false });
  p.applyLockLevel();
  assert.deepEqual(w.calls.at(-1), ['top', false, undefined]);
  done();
});

test('control keys reach the page as shell-control messages', () => {
  const { w, state, done } = make();
  const press = (input) => w.wcEvents['before-input-event']({ preventDefault() {} }, input);
  press({ type: 'keyDown', key: 'PageDown' });
  state.controls = false;
  press({ type: 'keyDown', key: 'PageUp' });
  assert.deepEqual(w.calls.filter((c) => c[0] === 'send'), [['send', 'shell-control', { a: 'next', r: false }]]);
  done();
});
