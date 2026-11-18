'use strict';
const test = require('node:test');
const assert = require('node:assert');
const C = require('../src/agent-svc/config');
const state = require('../src/agent-svc/state');

const TOKEN = 'agt_' + 'A'.repeat(43);
const raw = (over) => Object.assign({ server: 'https://signage.example:8443', token: TOKEN, displayId: 7, commandKey: 'A'.repeat(43) + '=',
  thumbprint: '386bfbc220743f420e64ab481c4249eb1de9c4bc', allow: { update: true }, kioskUser: 'kiosk', kioskSid: 'S-1-5-21-1-2-3-1001',
  playerExe: 'C:\\Program Files\\AiSignX Player\\AiSignX Player.exe', junk: 'dropped' }, over);

test('sanitize keeps typed fields, upper-cases the thumbprint, defaults every switch to off, drops unknown keys', () => {
  const c = C.sanitize(raw());
  assert.strictEqual(c.thumbprint, '386BFBC220743F420E64AB481C4249EB1DE9C4BC');
  assert.deepStrictEqual(c.allow, { reboot: false, startup: false, update: true, autologin: false });
  assert.strictEqual(c.junk, undefined);
  assert.strictEqual(c.trustScope, 'SystemOnly');
  assert.strictEqual(c.kioskDomain, '.');
  assert.strictEqual(C.sanitize(raw({ allow: { reboot: 'true', update: 1 } })).allow.reboot, false);   // only a real true counts
  assert.deepStrictEqual(C.sanitize(raw({ allow: undefined })).allow, { reboot: false, startup: false, update: false, autologin: false });
});

test('sanitize refuses anything essential that is missing or malformed', () => {
  for (const bad of [null, [], 'x', {}, raw({ server: 'ftp://x' }), raw({ server: 'https://x/path' }), raw({ server: 'https://user@x' }), raw({ server: 'https://a b' }),
    raw({ token: 'disp_' + 'A'.repeat(43) }), raw({ token: TOKEN + 'x' }), raw({ displayId: 0 }), raw({ displayId: '7' }),
    raw({ commandKey: 'short' }), raw({ thumbprint: 'zz' }), raw({ thumbprint: '386BFBC2' })]) {
    assert.strictEqual(C.sanitize(bad), null, JSON.stringify(bad).slice(0, 60));
  }
});

test('optional fields that are malformed are blanked, not trusted', () => {
  const c = C.sanitize(raw({ kioskUser: 'a;b', kioskSid: 'S-1-5-18', playerExe: '..\\x.exe', trustScope: 'Everyone' }));
  assert.strictEqual(c.kioskUser, ''); assert.strictEqual(c.kioskSid, ''); assert.strictEqual(c.playerExe, ''); assert.strictEqual(c.trustScope, 'SystemOnly');
  assert.strictEqual(C.sanitize(raw({ trustScope: 'Machine' })).trustScope, 'Machine');
});

test('data paths all sit inside the one data folder', () => {
  const p = C.dataPaths('C:\\ProgramData\\AiSignXAgent');
  for (const k of ['config', 'state', 'keyFile', 'staging', 'previous', 'logs', 'lock', 'publicDir', 'status', 'cert']) assert.ok(p[k].startsWith('C:\\ProgramData\\AiSignXAgent\\'), k);
  assert.strictEqual(p.status, 'C:\\ProgramData\\AiSignXAgent\\public\\status.json');
});

test('childPath refuses separators, dots and traversal', () => {
  assert.strictEqual(C.childPath('C:\\r', 'abc123.exe'), 'C:\\r\\abc123.exe');
  for (const bad of ['..', '../x', 'a\\b', '.hidden', '', 'a/b', 'x'.repeat(65), 5, null]) assert.throws(() => C.childPath('C:\\r', bad), /unsafe/);
});

const ICACLS_OK = 'C:\\ProgramData\\AiSignXAgent NT AUTHORITY\\SYSTEM:(OI)(CI)(F)\n                    BUILTIN\\Administrators:(OI)(CI)(F)\n\nSuccessfully processed 1 files';
test('icacls output: only SYSTEM and Administrators is safe; Users with write rights is not; read/execute is fine', () => {
  assert.deepStrictEqual(C.unsafeAces(ICACLS_OK), []);
  assert.deepStrictEqual(C.unsafeAces(ICACLS_OK + '\n BUILTIN\\Users:(OI)(CI)(RX)'), []);
  assert.deepStrictEqual(C.unsafeAces('X BUILTIN\\Users:(OI)(CI)(M)'), ['BUILTIN\\Users']);
  assert.deepStrictEqual(C.unsafeAces('X Everyone:(F)'), ['Everyone']);
  assert.deepStrictEqual(C.unsafeAces('X NT AUTHORITY\\Authenticated Users:(I)(WD,AD)'), ['NT AUTHORITY\\Authenticated Users']);
  assert.deepStrictEqual(C.unsafeAces('X BUILTIN\\Users:(OI)(CI)(DENY)(W)'), []);
  assert.deepStrictEqual(C.unsafeAces('X *S-1-5-32-545:(OI)(CI)(F)'), ['*S-1-5-32-545']);
});

test('folder check output: owner must be SYSTEM/Administrators and no reparse point may exist', () => {
  const j = (o) => C.judgeFolder(JSON.stringify(o));
  assert.deepStrictEqual(j({ owner: 'BUILTIN\\Administrators', reparse: [] }), { ok: true });
  assert.deepStrictEqual(j({ owner: 'NT AUTHORITY\\SYSTEM', reparse: [] }), { ok: true });
  assert.strictEqual(j({ owner: 'PC\\user', reparse: [] }).ok, false);
  assert.strictEqual(j({ owner: 'BUILTIN\\Administrators', reparse: ['C:\\x'] }).ok, false);
  assert.strictEqual(j({ owner: 'unknown', reparse: ['error'] }).ok, false);
  assert.strictEqual(j({ owner: 'BUILTIN\\Administrators' }).ok, false);
  assert.strictEqual(C.judgeFolder('junk').ok, false);
});

test('environment that must not reach the agent', () => {
  assert.deepStrictEqual(C.envProblems({ ELECTRON_RUN_AS_NODE: '1', PATH: 'x' }), []);
  assert.deepStrictEqual(C.envProblems({ NODE_OPTIONS: '--require x' }), ['NODE_OPTIONS']);
  assert.deepStrictEqual(C.envProblems({ electron_enable_logging: '1', NODE_PATH: 'x' }).sort(), ['NODE_PATH', 'electron_enable_logging']);
});

test('state: sanitize bounds everything and never holds a secret field', () => {
  const s = state.sanitize({ seq: -1, timeMs: 'x', ledger: ['1111111111111111', 'bad'], minVersion: '3.1.0', pending: { id: 'bad' }, previous: { version: '3.1.0', sha256: 'zz', size: 1 },
    rolledBack: ['2222222222222222', 'x'], lastUpdate: { id: '2222222222222222', state: 'ok', to: '3.2.0' }, autologin: { enabled: true, user: 'kiosk', password: 'nope' }, token: 'agt_x' });
  assert.deepStrictEqual(s.ledger, ['1111111111111111']);
  assert.strictEqual(s.seq, 0); assert.strictEqual(s.pending, null); assert.strictEqual(s.previous, null);
  assert.deepStrictEqual(s.rolledBack, ['2222222222222222']);
  assert.deepStrictEqual(s.autologin, { enabled: true, user: 'kiosk' });
  assert.ok(!JSON.stringify(s).includes('nope') && !JSON.stringify(s).includes('agt_'));
  const p = { id: '2222222222222222', from: '3.1.0', to: '3.2.0', sha256: 'ab'.repeat(32), size: 5, startedAt: 9, phase: 'verifying' };
  assert.deepStrictEqual(state.sanitize({ pending: p }).pending, p);
  assert.strictEqual(state.sanitize({ pending: Object.assign({}, p, { phase: 'weird' }) }).pending, null);
  assert.strictEqual(state.sanitize(undefined).minVersion, '0');
});
