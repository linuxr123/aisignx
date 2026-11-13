'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const http = require('http');
const fs = require('fs');
const os = require('os');
const path = require('path');
const crypto = require('crypto');
const { createUpstream } = require('../src/proxy/upstream');
const { createPageCache } = require('../src/proxy/page-cache');
const { createMediaCache } = require('../src/proxy/media-cache');
const { createProxy, sendOffline } = require('../src/proxy/server');

const MEDIA = '/api/player/v1/media/7/abc.mp4';
const VIDEO = crypto.randomBytes(5000);
const PAGE = '<html><script src="/static/player/net.js?v=B1"></script></html>';

function listen(handler) {
  const s = http.createServer(handler);
  return new Promise((r) => s.listen(0, '127.0.0.1', () => r(s)));
}

// Fake AiSignX server; `state` lets each test change its behaviour.
async function fakeUpstream(state) {
  return listen((req, res) => {
    state.seen.push({ method: req.method, url: req.url, headers: req.headers });
    if (req.url === '/api/player/v1/echo') {
      let body = '';
      req.on('data', (c) => { body += c; });
      req.on('end', () => {
        res.writeHead(200, { 'Content-Type': 'text/plain', 'Set-Cookie': [
          'aisignx_display=abc; Path=/api/player; Secure; HttpOnly; SameSite=Strict', 'other=1; secure'] });
        res.end('got:' + body);
      });
      return;
    }
    if (req.url === '/api/player/v1/events') {
      res.writeHead(200, { 'Content-Type': 'text/event-stream', 'Cache-Control': 'no-cache' });
      res.write('event: hello\ndata: {}\n\n');
      state.sseRes = res;
      return;
    }
    if (state.errorStatus && (req.url.startsWith('/player/') || req.url.startsWith('/static/player/'))) {
      res.writeHead(state.errorStatus, { 'Content-Type': 'text/html' });
      res.end('<h1>Server error</h1>');
      return;
    }
    if (req.url.startsWith('/static/player/')) {
      res.writeHead(200, { 'Content-Type': 'text/javascript' });
      res.end('/* net */');
      return;
    }
    if (req.url.startsWith('/player/')) {
      res.writeHead(200, { 'Content-Type': 'text/html', 'Content-Security-Policy': "default-src 'self'", 'X-Frame-Options': 'DENY' });
      res.end(PAGE);
      return;
    }
    if (req.url.startsWith('/api/player/v1/media/')) {
      state.mediaHits = (state.mediaHits || 0) + 1;
      const body = state.mediaBody || VIDEO;
      const range = /bytes=(\d+)-/.exec(req.headers.range || '');
      if (range) {
        const start = Number(range[1]);
        res.writeHead(206, { 'Content-Type': 'video/mp4', 'Content-Range': `bytes ${start}-${body.length - 1}/${body.length}` });
        res.end(body.subarray(start));
      } else {
        res.writeHead(200, { 'Content-Type': 'video/mp4', 'Content-Length': body.length });
        res.end(body);
      }
      return;
    }
    res.writeHead(404, { 'Content-Type': 'application/json' });
    res.end('{"error":"not_found"}');
  });
}

async function setup(t, extra) {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'aisignx-proxy-'));
  const state = { seen: [], cookie: 'abc' };
  const up = await fakeUpstream(state);
  const serverUrl = 'http://127.0.0.1:' + up.address().port;
  const wiring = { port: 0 };
  const proxy = createProxy(wiring);
  await proxy.start();  // the loopback origin is known only after listen
  const upstream = createUpstream({ serverUrl, loopbackOrigin: proxy.origin });
  const pageCache = createPageCache({ dir: path.join(dir, 'page-cache'), upstream, offlinePage: sendOffline, timeoutMs: 1000 });
  const mediaCache = createMediaCache(Object.assign({ dir: path.join(dir, 'media'), upstream,
    getCookie: () => state.cookie, retryDelays: [], saveDelayMs: 0 }, extra));
  Object.assign(wiring, { upstream, pageCache, mediaCache });
  t.after(async () => { mediaCache.stop(); upstream.close(); await proxy.stop(); up.closeAllConnections(); up.close(); });
  return { dir, state, up, proxy, upstream, pageCache, mediaCache, port: proxy.port };
}

function request(port, opts, body) {
  return new Promise((resolve, reject) => {
    const req = http.request({ host: '127.0.0.1', port, method: 'GET', agent: false, ...opts,
      headers: { host: '127.0.0.1:' + port, ...(opts.headers || {}) } }, (res) => {
      const chunks = [];
      res.on('data', (c) => chunks.push(c));
      res.on('end', () => resolve({ status: res.statusCode, headers: res.headers, body: Buffer.concat(chunks) }));
    });
    req.on('error', reject);
    req.end(body);
  });
}

test('POST bodies pass through and Set-Cookie loses only Secure', async (t) => {
  const { port, state } = await setup(t);
  const r = await request(port, { method: 'POST', path: '/api/player/v1/echo', headers: { 'content-type': 'text/plain' } }, 'hello');
  assert.equal(r.status, 200);
  assert.equal(r.body.toString(), 'got:hello');
  assert.deepEqual(r.headers['set-cookie'], ['aisignx_display=abc; Path=/api/player; HttpOnly; SameSite=Strict', 'other=1']);
  const seen = state.seen.at(-1);
  assert.equal(seen.headers['accept-encoding'], 'identity');
  assert.ok(!Object.keys(seen.headers).some((h) => h.startsWith('x-forwarded')));
  assert.match(seen.headers.host, /^127\.0\.0\.1:\d+$/);
});

test('a wrong Host header gets 421', async (t) => {
  const { port } = await setup(t);
  const r = await request(port, { path: '/player/', headers: { host: 'evil.example:' + port } });
  assert.equal(r.status, 421);
  const r2 = await request(port, { path: '/player/', headers: { host: 'localhost:' + port } });
  assert.equal(r2.status, 421);
});

test('SSE chunks arrive before the upstream response ends', async (t) => {
  const { port, state } = await setup(t);
  await new Promise((resolve, reject) => {
    const req = http.get({ host: '127.0.0.1', port, path: '/api/player/v1/events', agent: false,
      headers: { host: '127.0.0.1:' + port } }, (res) => {
      assert.equal(res.headers['content-type'], 'text/event-stream');
      let got = '';
      res.on('data', (c) => {
        got += c;
        if (got.includes('event: hello') && !got.includes('event: second')) state.sseRes.write('event: second\ndata: {}\n\n');
        if (got.includes('event: second')) { req.destroy(); resolve(); }
      });
    });
    req.on('error', (e) => { if (e.code !== 'ECONNRESET') reject(e); });
  });
  // The client abort closes the upstream stream too.
  await new Promise((r) => setTimeout(r, 100));
  assert.ok(state.sseRes.destroyed || state.sseRes.writableEnded || state.sseRes.socket === null);
});

test('page snapshot is served when upstream is down, offline page without one', async (t) => {
  const { port, up, dir } = await setup(t);
  const live = await request(port, { path: '/player/' });
  assert.equal(live.status, 200);
  assert.equal(live.headers['x-aisignx-shell-cache'], undefined);
  up.closeAllConnections();
  await new Promise((r) => up.close(r));

  const cached = await request(port, { path: '/player/' });
  assert.equal(cached.status, 200);
  assert.equal(cached.headers['x-aisignx-shell-cache'], '1');
  assert.equal(cached.headers['content-security-policy'], "default-src 'self'");
  assert.equal(cached.headers['x-frame-options'], 'DENY');
  assert.equal(cached.body.toString(), PAGE);

  for (const f of fs.readdirSync(path.join(dir, 'page-cache'))) fs.unlinkSync(path.join(dir, 'page-cache', f));
  const offline = await request(port, { path: '/player/' });
  assert.equal(offline.status, 503);
  assert.match(offline.body.toString(), /data-reason="unreachable"/);
  assert.match(offline.headers['content-security-policy'], /default-src 'none'/);

  const shell = await request(port, { path: '/__shell/offline' });
  assert.equal(shell.status, 503);
  const js = await request(port, { path: '/__shell/offline.js' });
  assert.equal(js.status, 200);
});

test('any upstream 5xx falls back to the snapshot, or the offline page without one', async (t) => {
  const { port, state, dir } = await setup(t);
  assert.equal((await request(port, { path: '/player/' })).status, 200);
  assert.equal((await request(port, { path: '/static/player/net.js?v=B1' })).status, 200);
  for (const code of [500, 520, 530]) {
    state.errorStatus = code;
    const page = await request(port, { path: '/player/' });
    assert.equal(page.status, 200, String(code));
    assert.equal(page.headers['x-aisignx-shell-cache'], '1');
    assert.equal(page.body.toString(), PAGE);
    const js = await request(port, { path: '/static/player/net.js?v=B1' });
    assert.equal(js.status, 200, String(code));
    assert.equal(js.body.toString(), '/* net */');
  }
  for (const f of fs.readdirSync(path.join(dir, 'page-cache'))) fs.unlinkSync(path.join(dir, 'page-cache', f));
  state.errorStatus = 500;
  const offline = await request(port, { path: '/player/' });
  assert.equal(offline.status, 503);
  assert.match(offline.body.toString(), /data-reason="http"/);
  state.errorStatus = 0;
  assert.equal((await request(port, { path: '/player/' })).status, 200);
});

test('static snapshots not referenced by the newest page are pruned', async (t) => {
  const { port, dir } = await setup(t);
  const pc = path.join(dir, 'page-cache');
  fs.mkdirSync(pc, { recursive: true });
  const stale = crypto.createHash('sha1').update('/static/player/net.js?v=OLD').digest('hex');
  fs.writeFileSync(path.join(pc, stale + '.json'), JSON.stringify({ url: '/static/player/net.js?v=OLD' }));
  fs.writeFileSync(path.join(pc, stale + '.body'), 'old');
  await request(port, { path: '/player/' });
  assert.ok(!fs.existsSync(path.join(pc, stale + '.json')));
});

test('media: a miss passes through and downloads; a hit with the cookie returns 206', async (t) => {
  const { port, state, mediaCache } = await setup(t);
  const cookie = { cookie: 'aisignx_display=abc' };
  const miss = await request(port, { path: MEDIA, headers: { ...cookie, range: 'bytes=0-' } });
  assert.equal(miss.status, 206);
  await mediaCache.whenIdle();
  assert.equal(mediaCache.index[MEDIA].size, VIDEO.length);
  const hitsBefore = state.mediaHits;

  const hit = await request(port, { path: MEDIA, headers: { ...cookie, range: 'bytes=100-199' } });
  assert.equal(hit.status, 206);
  assert.equal(hit.headers['content-range'], `bytes 100-199/${VIDEO.length}`);
  assert.equal(hit.headers['x-aisignx-shell-cache'], '1');
  assert.deepEqual(hit.body, VIDEO.subarray(100, 200));
  assert.equal(state.mediaHits, hitsBefore);

  // Wrong or missing cookie: passed through to the server, never served from the cache.
  const wrong = await request(port, { path: MEDIA, headers: { cookie: 'aisignx_display=zzz', range: 'bytes=0-' } });
  assert.equal(wrong.headers['x-aisignx-shell-cache'], undefined);
  const none = await request(port, { path: MEDIA });
  assert.equal(none.headers['x-aisignx-shell-cache'], undefined);
  assert.equal(state.mediaHits, hitsBefore + 2);
});

test('downloads with a size or sha256 mismatch are discarded', async (t) => {
  const { mediaCache, dir } = await setup(t);
  mediaCache.onManifest(JSON.stringify({ items: [{ type: 'video', url: MEDIA, size: VIDEO.length + 1, mime: 'video/mp4' }] }));
  await mediaCache.whenIdle();
  assert.equal(mediaCache.index[MEDIA], undefined);

  mediaCache.onManifest({ items: [{ type: 'video', url: MEDIA, size: VIDEO.length, sha256: 'ab'.repeat(32) }] });
  await mediaCache.whenIdle();
  assert.equal(mediaCache.index[MEDIA], undefined);
  const leftovers = fs.readdirSync(path.join(dir, 'media')).filter((f) => f.endsWith('.bin') || f.endsWith('.part'));
  assert.deepEqual(leftovers, []);

  const sha = crypto.createHash('sha256').update(VIDEO).digest('hex');
  mediaCache.onManifest({ items: [{ type: 'video', url: MEDIA, size: VIDEO.length, sha256: sha }] });
  await mediaCache.whenIdle();
  assert.equal(mediaCache.index[MEDIA].sha256, sha);
});

test('LRU eviction never evicts items in the current manifest', async (t) => {
  const { mediaCache, state } = await setup(t, { budgetBytes: 2 * VIDEO.length });
  state.mediaBody = VIDEO;
  const urls = [1, 2, 3].map((n) => `/api/player/v1/media/${n}/f${n}.jpg`);
  // Download all three via one manifest, then make the current manifest only the oldest one.
  mediaCache.onManifest({ items: urls.map((url) => ({ type: 'image', url, size: VIDEO.length })) });
  await mediaCache.whenIdle();
  urls.forEach((u, i) => mediaCache.setLastUsed(u, 1000 + i));
  mediaCache.onManifest({ items: [{ type: 'image', url: urls[0], size: VIDEO.length }] });
  const removed = await mediaCache.evict();
  assert.deepEqual(removed, [urls[1]]);
  assert.ok(mediaCache.index[urls[0]]);
  assert.ok(mediaCache.index[urls[2]]);
});
