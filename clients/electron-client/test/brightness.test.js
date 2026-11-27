'use strict';
// Real screen brightness on Windows: the pure rules and the controller (with a fake PowerShell).
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('fs');
const os = require('os');
const path = require('path');
const B = require('../src/brightness');

const dirs = [];
const tmp = () => { const d = fs.mkdtempSync(path.join(os.tmpdir(), 'aisignx-bright-')); dirs.push(d); return d; };
test.after(() => { for (const d of dirs) fs.rmSync(d, { recursive: true, force: true }); });

// 2026-01-15 at hh:mm UTC.
const at = (hh, mm) => Date.UTC(2026, 0, 15, hh, mm || 0);
const night = (from, to, tz, level) => ({ brightness: 80, dim: { level: level || 30, from, to, tz: tz === undefined ? 'UTC' : tz } });

// The level a fake run() was asked for, from the decoded script.
function levelOf(args) {
  const src = Buffer.from(args[args.length - 1], 'base64').toString('utf16le');
  return Number(/\$L = (-?\d+)/.exec(src)[1]);
}
function fakeRun(opts) {
  const o = opts || {};
  const calls = [];
  const run = (file, args) => {
    calls.push(levelOf(args));
    assert.equal(file, 'powershell.exe');
    return new Promise((resolve) => setTimeout(() => resolve(o.fail ? { ok: false, out: '', err: 'boom' } : { ok: true, out: o.out || '{"wmi":1,"ddc":0}' }), o.delay || 0));
  };
  return { run, calls };
}
const quiet = { info() {}, warn() {}, error() {} };

test('levelAt: the base level, clamped; bad values mean 100 and no window', () => {
  assert.equal(B.levelAt({ brightness: 60 }, at(12)), 60);
  assert.equal(B.levelAt({ brightness: 3 }, at(12)), 10);
  assert.equal(B.levelAt({ brightness: 250 }, at(12)), 100);
  for (const bad of [null, undefined, 'x', 5, {}, { brightness: '50' }, { brightness: NaN }, { brightness: Infinity }]) {
    assert.equal(B.levelAt(bad, at(12)), 100, String(bad));
  }
  for (const dim of [null, 'x', { level: 20, from: '22:00', to: '22:00' }, { level: 20, from: '25:00', to: '06:00' }, { level: 20, from: '9:00', to: '10:00' }]) {
    assert.equal(B.levelAt({ brightness: 70, dim }, at(22, 30)), 70);
  }
});

test('levelAt: a night window that crosses midnight, and a daytime one', () => {
  const s = night('22:00', '06:00');
  assert.equal(B.levelAt(s, at(21, 59)), 80);
  assert.equal(B.levelAt(s, at(22, 0)), 30);
  assert.equal(B.levelAt(s, at(0, 0)), 30);
  assert.equal(B.levelAt(s, at(5, 59)), 30);
  assert.equal(B.levelAt(s, at(6, 0)), 80);
  const day = night('12:00', '13:00');
  assert.equal(B.levelAt(day, at(12, 30)), 30);
  assert.equal(B.levelAt(day, at(13, 0)), 80);
  assert.equal(B.levelAt(night('22:00', '06:00', 'UTC', 95), at(23)), 80);   // never brighter than the base
});

test('levelAt: the window is in the organization time zone; an unknown zone falls back to local time', () => {
  const s = night('22:00', '06:00', 'America/New_York');
  assert.equal(B.levelAt(s, at(3)), 30);    // 22:00 in New York (EST, UTC-5)
  assert.equal(B.levelAt(s, at(23)), 80);   // 18:00 in New York
  const t = night('22:00', '06:00', 'Asia/Tokyo');
  assert.equal(B.levelAt(t, at(13)), 30);   // 22:00 in Tokyo
  const bad = night('22:00', '06:00', 'Not/AZone');
  const local = new Date(at(12)).getHours() * 60 + new Date(at(12)).getMinutes();
  assert.equal(B.minuteOfDay(at(12), 'Not/AZone'), local);
  assert.doesNotThrow(() => B.levelAt(bad, at(12)));
});

test('the script takes only a checked integer level', () => {
  for (const bad of [9, 101, 50.5, '50', NaN, undefined, -1]) assert.throws(() => B.script(bad), /bad brightness level/);
  assert.match(B.script(60), /\$L = 60\n/);
  assert.match(B.script(null), /\$L = -1\n/);
  assert.match(B.script(60), /WmiSetBrightness/);
  assert.match(B.script(60), /SetMonitorBrightness/);
  const args = B.psArgs(60);
  assert.deepEqual(args.slice(0, 5), ['-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass', '-EncodedCommand']);
  assert.equal(levelOf(args), 60);
});

test('parseResult reads the JSON line and refuses junk', () => {
  assert.deepEqual(B.parseResult('{"wmi":1,"ddc":2}'), { wmi: 1, ddc: 2 });
  assert.deepEqual(B.parseResult('{"wmi":-3,"ddc":"x","error":"ddc: no"}'), { wmi: 0, ddc: 0, error: 'ddc: no' });
  for (const bad of ['', 'nope', 'null', '5']) assert.equal(B.parseResult(bad), null);
});

test('never touches the brightness while the setting has always been 100', async () => {
  const f = fakeRun();
  const c = B.createBrightness({ run: f.run, log: quiet, dataDir: tmp(), now: () => at(12) });
  await c.update({ brightness: 100 });
  await c.update(null);
  await c.hold(true);
  await c.hold(false);
  await c.tick();
  assert.deepEqual(f.calls, []);
  assert.equal(c.isManaged(), false);
});

test('once changed, 100 sets 100; managed survives a restart', async () => {
  const dir = tmp();
  const f = fakeRun();
  const c = B.createBrightness({ run: f.run, log: quiet, dataDir: dir, now: () => at(12) });
  await c.update({ brightness: 60 });
  await c.update({ brightness: 100 });
  assert.deepEqual(f.calls, [60, 100]);
  assert.equal(c.isManaged(), true);
  const g = fakeRun();
  const again = B.createBrightness({ run: g.run, log: quiet, dataDir: dir, now: () => at(12) });
  assert.equal(again.isManaged(), true);
  await again.update({ brightness: 100 });
  assert.deepEqual(g.calls, [100]);
});

test('the same level does not run PowerShell again; the night window is checked on tick', async () => {
  let t = at(21, 58);
  const f = fakeRun();
  const c = B.createBrightness({ run: f.run, log: quiet, dataDir: tmp(), now: () => t });
  const s = night('22:00', '06:00');
  await c.update(s);
  await c.update(s);
  await c.tick();
  assert.deepEqual(f.calls, [80]);
  t = at(22, 1);
  await c.tick();
  await c.tick();
  assert.deepEqual(f.calls, [80, 30]);
  t = at(6, 1);
  await c.tick();
  assert.deepEqual(f.calls, [80, 30, 80]);
});

test('one PowerShell at a time; levels asked for meanwhile collapse to the newest', async () => {
  const f = fakeRun({ delay: 20 });
  const c = B.createBrightness({ run: f.run, log: quiet, dataDir: tmp(), now: () => at(12) });
  c.update({ brightness: 70 });
  c.update({ brightness: 50 });
  c.update({ brightness: 40 });
  c.update({ brightness: 30 });
  await c.idle();
  await c.idle();
  assert.deepEqual(f.calls, [70, 30]);
});

test('an emergency holds 100 until released', async () => {
  const f = fakeRun();
  const c = B.createBrightness({ run: f.run, log: quiet, dataDir: tmp(), now: () => at(12) });
  await c.update({ brightness: 40 });
  await c.hold(true);
  assert.equal(c.level(), 100);
  await c.update({ brightness: 20 });       // a new setting during the emergency: still 100
  await c.tick();
  await c.hold(false);
  assert.deepEqual(f.calls, [40, 100, 20]);
});

test('a failure is logged, never thrown, and retried only after a while', async () => {
  let t = at(12);
  const warns = [];
  const f = fakeRun({ fail: true });
  const c = B.createBrightness({ run: f.run, log: Object.assign({}, quiet, { warn: (...a) => warns.push(a.join(' ')) }), dataDir: tmp(), now: () => t });
  await c.update({ brightness: 50 });
  await c.tick();
  assert.deepEqual(f.calls, [50]);
  assert.match(warns[0], /could not set 50%/);
  t += 11 * 60 * 1000;
  await c.tick();
  assert.deepEqual(f.calls, [50, 50]);
  const thrower = B.createBrightness({ run: () => { throw new Error('spawn failed'); }, log: quiet, dataDir: tmp(), now: () => t });
  await assert.doesNotReject(thrower.update({ brightness: 50 }));
});

test('probe caches what the PC can control, so the next start knows at once', async () => {
  const dir = tmp();
  const none = B.createBrightness({ run: fakeRun({ out: '{"wmi":0,"ddc":0}' }).run, log: quiet, dataDir: dir });
  assert.equal(none.canControl(), false);       // nothing cached yet
  assert.deepEqual(await none.probe(), { wmi: 0, ddc: 0 });
  assert.equal(none.canControl(), false);
  const f = fakeRun({ out: '{"wmi":0,"ddc":1}' });
  const one = B.createBrightness({ run: f.run, log: quiet, dataDir: dir });
  await one.probe();
  assert.deepEqual(f.calls, [-1]);              // the probe changes nothing
  assert.equal(B.createBrightness({ run: f.run, log: quiet, dataDir: dir }).canControl(), true);
  const broken = B.createBrightness({ run: fakeRun({ fail: true }).run, log: quiet, dataDir: dir });
  assert.equal(await broken.probe(), null);
  assert.equal(broken.canControl(), true);      // a failed probe keeps the last good answer
});

test('a probe that found a screen but no screen takes a level: forget it once, so the page dims itself', async () => {
  const dir = tmp();
  fs.writeFileSync(path.join(dir, B.PROBE_FILE), JSON.stringify({ wmi: 1, ddc: 0 }));
  let lost = 0;
  const f = fakeRun({ out: '{"wmi":0,"ddc":0,"error":"wmi: not supported"}' });
  const c = B.createBrightness({ run: f.run, log: quiet, dataDir: dir, now: () => at(12), onLost: () => { lost++; c.release(); } });
  assert.equal(c.canControl(), true);
  await c.update({ brightness: 40 });
  await c.idle();
  assert.equal(lost, 1);
  assert.equal(c.canControl(), false);
  assert.deepEqual(f.calls, [40, 100]);       // release() asks for 100 (never dimmed twice), and does not loop
  assert.equal(c.level(), 100);
});

test('a screen that took a level once does not switch modes when a later level fails', async () => {
  const dir = tmp();
  fs.writeFileSync(path.join(dir, B.PROBE_FILE), JSON.stringify({ wmi: 1, ddc: 0 }));
  let out = '{"wmi":1,"ddc":0}';
  let lost = 0;
  const run = (file, args) => Promise.resolve({ ok: true, out, level: levelOf(args) });
  const c = B.createBrightness({ run, log: quiet, dataDir: dir, now: () => at(12), onLost: () => { lost++; } });
  await c.update({ brightness: 40 });
  out = '{"wmi":0,"ddc":0}';                  // e.g. the monitor went to sleep
  await c.update({ brightness: 50 });
  assert.equal(lost, 0);
  assert.equal(c.canControl(), true);
});

test('release drops the settings and the hold, and sets the screen back to 100', async () => {
  const f = fakeRun();
  const c = B.createBrightness({ run: f.run, log: quiet, dataDir: tmp(), now: () => at(12) });
  await c.update({ brightness: 30 });
  await c.hold(true);
  await c.hold(false);
  await c.release();
  await c.tick();
  assert.deepEqual(f.calls, [30, 100, 30, 100]);
});
