'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const { parseRange } = require('../src/proxy/range');

test('bytes=0- covers the whole file', () => {
  assert.deepEqual(parseRange('bytes=0-', 1000), { start: 0, end: 999 });
});

test('bytes=a-b, clamped to the end', () => {
  assert.deepEqual(parseRange('bytes=10-19', 1000), { start: 10, end: 19 });
  assert.deepEqual(parseRange('bytes=990-5000', 1000), { start: 990, end: 999 });
});

test('bytes=-n is a suffix range', () => {
  assert.deepEqual(parseRange('bytes=-100', 1000), { start: 900, end: 999 });
  assert.deepEqual(parseRange('bytes=-5000', 1000), { start: 0, end: 999 });
});

test('a range past the end is 416', () => {
  assert.deepEqual(parseRange('bytes=1000-', 1000), { status: 416 });
  assert.deepEqual(parseRange('bytes=2000-3000', 1000), { status: 416 });
  assert.deepEqual(parseRange('bytes=-0', 1000), { status: 416 });
});

test('missing, invalid and multi ranges are ignored (full response)', () => {
  for (const h of [undefined, '', 'bytes=', 'bytes=-', 'items=0-1', 'bytes=abc', 'bytes=5-2', 'bytes=0-1,5-6']) {
    assert.equal(parseRange(h, 1000), null, String(h));
  }
});
