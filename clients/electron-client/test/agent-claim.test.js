'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('fs');
const os = require('os');
const path = require('path');
const C = require('../src/agent-claim');

const ID = 'AbCdEfGhIjKlMnOpQrStUvWx';
const NOW = 1_800_000_000_000;
const log = { warn() {}, info() {} };
const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'aisignx-claim-'));
test.after(() => fs.rmSync(dir, { recursive: true, force: true }));
const write = (obj) => { const f = path.join(dir, 'enroll-request.json'); fs.writeFileSync(f, typeof obj === 'string' ? obj : JSON.stringify(obj)); return f; };

test('the file lives in the agent public folder', () => {
  assert.equal(C.requestFile({ ProgramData: 'D:\\PD' }), 'D:\\PD\\AiSignXAgent\\public\\enroll-request.json');
});

test('only a fresh, well-formed claim id is used', () => {
  assert.equal(C.claimIdOf({ claim_id: ID, t: NOW - 1000 }, NOW), ID);
  for (const bad of [null, {}, { claim_id: ID }, { claim_id: ID, t: NOW - 26 * 3600 * 1000 }, { claim_id: ID, t: NOW + 2 * 3600 * 1000 },
    { claim_id: 'short', t: NOW }, { claim_id: ID + ' ;', t: NOW }, { claim_id: 5, t: NOW }, 'text']) assert.equal(C.claimIdOf(bad, NOW), null, JSON.stringify(bad));
});

test('it claims once per look, only with a display credential, and says what happened', async () => {
  const calls = [];
  const mk = (file, token, status) => C.createClaimer({ file, now: () => NOW, getToken: () => token, log,
    post: async (p, body) => { calls.push([p, body]); if (status === 'throw') throw new Error('x'); return status === 'none' ? null : { status }; } });
  const f = write({ claim_id: ID, t: NOW });
  assert.equal(await mk(f, 'tok', 200).tick(), 'claimed');
  assert.deepEqual(calls[0], ['/agent/claim', { claim_id: ID }]);
  assert.equal(await mk(f, '', 200).tick(), 'idle');                        // not paired yet: nothing is sent
  assert.equal(calls.length, 1);
  assert.equal(await mk(path.join(dir, 'missing.json'), 'tok', 200).tick(), 'idle');
  assert.equal(await mk(write('not json'), 'tok', 200).tick(), 'idle');
  write({ claim_id: ID, t: NOW });                                          // (the file was overwritten above)
  assert.equal(await mk(f, 'tok', 409).tick(), 'refused');
  assert.equal(await mk(f, 'tok', 'throw').tick(), 'offline');
});
