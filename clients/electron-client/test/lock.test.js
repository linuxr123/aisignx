'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const { createLock, parseCommand } = require('../src/lock');

// A lock on a fake clock with fake timers.
function fake() {
  const clock = { t: 1000, timers: [] };
  const lock = createLock({
    now: () => clock.t,
    setTimer: (fn, ms) => { const h = { fn, at: clock.t + ms }; clock.timers.push(h); return h; },
    clearTimer: (h) => { clock.timers = clock.timers.filter((x) => x !== h); },
  });
  clock.advance = (ms) => {
    clock.t += ms;
    const due = clock.timers.filter((h) => h.at <= clock.t);
    clock.timers = clock.timers.filter((h) => h.at > clock.t);
    for (const h of due) h.fn();
  };
  return { lock, clock };
}

const RID = (n) => String(n).padStart(16, '0');

test('configure turns the lock on and off', () => {
  const { lock } = fake();
  const states = [];
  lock.onChange((s) => states.push(s));
  assert.equal(lock.state(), 'off');
  assert.equal(lock.isLocked(), false);
  lock.configure({ kioskLock: true });
  assert.equal(lock.state(), 'locked');
  assert.equal(lock.isLocked(), true);
  lock.configure({ kioskLock: true }); // no change, no event
  lock.configure({});
  assert.equal(lock.state(), 'off');
  assert.deepEqual(states, ['locked', 'off']);
});

test('a remote unlock expires on its own', () => {
  const { lock, clock } = fake();
  const states = [];
  lock.onChange((s) => states.push(s));
  lock.configure({ kioskLock: true });
  assert.equal(lock.unlockFor(15, RID(1)), true);
  assert.equal(lock.state(), 'unlocked');
  assert.equal(lock.isLocked(), false);
  clock.advance(15 * 60000 - 1);
  assert.equal(lock.state(), 'unlocked');
  clock.advance(100);
  assert.equal(lock.state(), 'locked');
  assert.deepEqual(states, ['locked', 'unlocked', 'locked']);
  assert.equal(clock.timers.length, 0);
});

test('request ids are deduplicated', () => {
  const { lock, clock } = fake();
  lock.configure({ kioskLock: true });
  assert.equal(lock.unlockFor(5, RID(2)), true);
  assert.equal(lock.relock(RID(3)), true);
  assert.equal(lock.state(), 'locked');
  assert.equal(lock.unlockFor(5, RID(2)), false); // replayed unlock
  assert.equal(lock.state(), 'locked');
  for (let i = 10; i < 40; i++) lock.relock(RID(i)); // pushes RID(2) out of the last 20
  assert.equal(lock.unlockFor(5, RID(2)), true);
  assert.equal(lock.state(), 'unlocked');
  clock.advance(1);
});

test('relock ends a remote unlock and its timer', () => {
  const { lock, clock } = fake();
  lock.configure({ kioskLock: true });
  lock.unlockFor(60, RID(4));
  assert.equal(clock.timers.length, 1);
  lock.relock(RID(5));
  assert.equal(lock.state(), 'locked');
  assert.equal(clock.timers.length, 0);
});

test('minutes are clamped to 1..240', () => {
  const { lock, clock } = fake();
  lock.configure({ kioskLock: true });
  lock.unlockFor(0, RID(6));
  clock.advance(60000 - 1);
  assert.equal(lock.state(), 'unlocked');
  clock.advance(1);
  assert.equal(lock.state(), 'locked');
  lock.unlockFor(100000, RID(7));
  clock.advance(240 * 60000 - 1);
  assert.equal(lock.state(), 'unlocked');
  clock.advance(1);
  assert.equal(lock.state(), 'locked');
  assert.equal(lock.unlockFor(1.5, RID(8)), false);
});

test('a remote unlock without Lock player reports off', () => {
  const { lock } = fake();
  lock.unlockFor(5, RID(9));
  assert.equal(lock.state(), 'off');
  assert.equal(lock.isLocked(), false);
});

test('the production clock is monotonic milliseconds', () => {
  const lock = createLock();
  lock.configure({ kioskLock: true });
  lock.unlockFor(1, 'aaaaaaaaaaaaaaaa');
  assert.equal(lock.state(), 'unlocked');
  lock.relock('bbbbbbbbbbbbbbbb');
});

test('parseCommand accepts only well-formed unlock and lock commands', () => {
  const rid = '0123456789abcdef';
  assert.deepEqual(parseCommand(JSON.stringify({ action: 'unlock', minutes: 15, request_id: rid })),
    { action: 'unlock', minutes: 15, rid });
  assert.deepEqual(parseCommand(JSON.stringify({ action: 'unlock', minutes: 999, request_id: rid })),
    { action: 'unlock', minutes: 240, rid });
  assert.deepEqual(parseCommand(JSON.stringify({ action: 'lock', request_id: rid })), { action: 'lock', rid });
  for (const bad of [
    { action: 'unlock', minutes: '15', request_id: rid },
    { action: 'unlock', minutes: 15.5, request_id: rid },
    { action: 'unlock', minutes: 15 },
    { action: 'unlock', minutes: 15, request_id: rid.toUpperCase() },
    { action: 'lock', request_id: rid + '0' },
    { action: 'reload', request_id: rid },
    [rid],
  ]) {
    assert.equal(parseCommand(JSON.stringify(bad)), null, JSON.stringify(bad));
  }
  assert.equal(parseCommand('{not json'), null);
  assert.equal(parseCommand(42), null);
  assert.equal(parseCommand('"' + 'x'.repeat(1100) + '"'), null);
});
