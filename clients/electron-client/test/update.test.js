'use strict';
const test = require('node:test');
const assert = require('node:assert');
const U = require('../src/agent-svc/update');

const P = (phase, startedAt) => ({ id: '2222222222222222', to: '3.2.0', phase, startedAt: startedAt === undefined ? 1000 : startedAt });
const st = (seen) => ({ player_version: '3.2.0', player_last_seen_s: seen });

test('versions compare numerically, a pre-release sorts below the release', () => {
  assert.strictEqual(U.compareVersions('3.10.0', '3.9.9'), 1);
  assert.strictEqual(U.compareVersions('3.1.0', '3.1'), 0);
  assert.strictEqual(U.compareVersions('3.2.0-beta', '3.2.0'), -1);
  assert.strictEqual(U.compareVersions('3.1.0', '3.2.0'), -1);
  assert.strictEqual(U.compareVersions('x', '3.2.0'), null);
});

test('anti-downgrade: nothing below minVersion; unknown text is refused', () => {
  assert.strictEqual(U.versionAllowed('3.2.0', '3.1.0'), true);
  assert.strictEqual(U.versionAllowed('3.1.0', '3.1.0'), true);
  assert.strictEqual(U.versionAllowed('3.0.9', '3.1.0'), false);
  assert.strictEqual(U.versionAllowed('3.0.9', undefined), true);      // no floor yet
  assert.strictEqual(U.versionAllowed('abc', '3.1.0'), false);
});

test('the installer arguments are constants and silent', () => {
  assert.deepStrictEqual(U.INSTALLER_ARGS, ['/S', '/allusers']);
});

test('health needs the installed file AND the server to agree on the new version, recently', () => {
  const ok = { installed: '3.2.0', to: '3.2.0', status: st(10) };
  assert.strictEqual(U.healthy(ok), true);
  assert.strictEqual(U.healthy(Object.assign({}, ok, { installed: '3.1.0' })), false);          // file not replaced
  assert.strictEqual(U.healthy(Object.assign({}, ok, { status: { player_version: '3.1.0', player_last_seen_s: 5 } })), false);
  assert.strictEqual(U.healthy(Object.assign({}, ok, { status: st(U.PLAYER_SEEN_MAX_S) })), false);
  assert.strictEqual(U.healthy(Object.assign({}, ok, { status: st(null) })), false);
  assert.strictEqual(U.healthy(Object.assign({}, ok, { status: st(-1) })), false);
  assert.strictEqual(U.healthy(Object.assign({}, ok, { status: null })), false);
  assert.strictEqual(U.healthy(null), false);
});

test('pending: healthy ends it, otherwise wait until the deadline', () => {
  const p = P('verifying');
  assert.deepStrictEqual(U.judgePending(p, 1000 + 5, true, null, []), { action: 'ok' });
  assert.deepStrictEqual(U.judgePending(p, 1000 + U.HEALTH_DEADLINE_MS - 1, false, { version: '3.1.0' }, []), { action: 'wait' });
  assert.strictEqual(U.deadline(p), 1000 + U.HEALTH_DEADLINE_MS);
});

test('past the deadline: roll back once per command id, never in a loop', () => {
  const late = 1000 + U.HEALTH_DEADLINE_MS;
  const prev = { version: '3.1.0' };
  assert.deepStrictEqual(U.judgePending(P('verifying'), late, false, prev, []), { action: 'rollback' });
  assert.deepStrictEqual(U.judgePending(P('verifying'), late, false, prev, ['2222222222222222']), { action: 'fail', state: 'failed' });
  assert.deepStrictEqual(U.judgePending(P('rolling_back'), late, false, prev, []), { action: 'fail', state: 'failed' });   // the rollback itself failed
  assert.deepStrictEqual(U.judgePending(P('verifying'), late, false, null, []), { action: 'fail', state: 'failed_no_rollback' });
});

test('crash recovery by phase and age', () => {
  const t = 5_000_000;
  assert.strictEqual(U.recover(null, t), 'none');
  assert.strictEqual(U.recover(P('installing', t - 1000), t), 'finish');
  assert.strictEqual(U.recover(P('verifying', t - 1000), t), 'verify');
  assert.strictEqual(U.recover(P('downloading', t - 1000), t), 'close');
  assert.strictEqual(U.recover(P('rolling_back', t - 1000), t), 'close');
  assert.strictEqual(U.recover(P('installing', t - U.STALE_PENDING_MS - 1), t), 'close');       // older than an hour
  assert.strictEqual(U.recover(P('installing', t + 5000), t), 'close');                          // clock went back
});
