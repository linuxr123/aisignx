'use strict';
// Network-first snapshot of the player page and its static files, for an offline cold start.
// userData/page-cache/<sha1(path+query)>.json (meta) + .body
const fs = require('fs');
const path = require('path');
const crypto = require('crypto');
const log = require('../log');

const TIMEOUT_MS = 5000;
const MAX_BODY = 8 * 1024 * 1024;
// Any upstream 5xx (Flask 500 during a restore, proxy/tunnel 52x, gateway 502-504) falls back to
// the snapshot: a passed-through error page would leave the kiosk stuck with nothing to retry it.
const GATEWAY_STATUS = new Set([502, 503, 504]);
function isServerError(status) {
  return status >= 500 && status <= 599;
}
const PAGE = '/player/';

function keyOf(url) {
  return crypto.createHash('sha1').update(url).digest('hex');
}

function isPage(url) {
  const p = url.split('?')[0];
  return p === '/player' || p === PAGE;
}

// /static/player/... URLs referenced by src= / href= in the player page.
function referencedUrls(html) {
  const out = new Set();
  const re = /(?:src|href)\s*=\s*"([^"]+)"/gi;
  let m;
  while ((m = re.exec(html))) {
    const u = m[1].replace(/&amp;/g, '&');
    if (u.startsWith('/static/player/')) out.add(u);
  }
  return out;
}

function readBody(res, timeoutMs) {
  return new Promise((resolve, reject) => {
    const chunks = [];
    let size = 0;
    const timer = setTimeout(() => { res.destroy(); reject(Object.assign(new Error('body timeout'), { kind: 'network' })); }, timeoutMs);
    res.on('data', (c) => {
      size += c.length;
      if (size > MAX_BODY) { res.destroy(); clearTimeout(timer); reject(Object.assign(new Error('too large'), { kind: 'http' })); return; }
      chunks.push(c);
    });
    res.on('end', () => { clearTimeout(timer); resolve(Buffer.concat(chunks)); });
    res.on('error', (e) => { clearTimeout(timer); reject(Object.assign(e, { kind: e.kind || 'network' })); });
  });
}

function createPageCache(opts) {
  const dir = opts.dir;
  const upstream = opts.upstream;
  const offlinePage = opts.offlinePage; // (res, reason) => void
  const timeoutMs = opts.timeoutMs || TIMEOUT_MS;
  fs.mkdirSync(dir, { recursive: true });

  function load(url) {
    const k = path.join(dir, keyOf(url));
    try {
      const meta = JSON.parse(fs.readFileSync(k + '.json', 'utf8'));
      if (meta.url !== url) return null;
      return { meta, body: fs.readFileSync(k + '.body') };
    } catch (e) {
      return null;
    }
  }

  function save(url, res, body) {
    const k = path.join(dir, keyOf(url));
    const meta = {
      url,
      status: 200,
      contentType: res.headers['content-type'] || 'application/octet-stream',
      csp: res.headers['content-security-policy'] || null,
      xfo: res.headers['x-frame-options'] || null,
      savedAt: Date.now(),
    };
    try {
      fs.writeFileSync(k + '.body.tmp', body);
      fs.renameSync(k + '.body.tmp', k + '.body');
      fs.writeFileSync(k + '.json.tmp', JSON.stringify(meta));
      fs.renameSync(k + '.json.tmp', k + '.json');
      if (isPage(url)) prune(body.toString('utf8'));
    } catch (e) {
      log.warn('page-cache save failed', url.split('?')[0], e.message);
    }
  }

  // Drops static snapshots that the newest page no longer references (the build ?v= moved on).
  function prune(html) {
    const keep = new Set([...referencedUrls(html)].map(keyOf));
    for (const name of fs.readdirSync(dir)) {
      if (!name.endsWith('.json')) continue;
      const k = name.slice(0, -5);
      let meta;
      try { meta = JSON.parse(fs.readFileSync(path.join(dir, name), 'utf8')); } catch (e) { meta = null; }
      if (meta && isPage(meta.url)) continue;
      if (keep.has(k)) continue;
      for (const ext of ['.json', '.body']) {
        try { fs.unlinkSync(path.join(dir, k + ext)); } catch (e) { /* ignore */ }
      }
    }
  }

  function serveSnapshot(res, snap) {
    const h = {
      'Content-Type': snap.meta.contentType,
      'Content-Length': snap.body.length,
      'Cache-Control': 'no-store',
      'X-Content-Type-Options': 'nosniff',
      'Referrer-Policy': 'no-referrer',
      'X-AiSignX-Shell-Cache': '1',
    };
    if (snap.meta.csp) h['Content-Security-Policy'] = snap.meta.csp;
    if (snap.meta.xfo) h['X-Frame-Options'] = snap.meta.xfo;
    res.writeHead(200, h);
    res.end(snap.body);
  }

  function fallback(req, res, reason) {
    const snap = load(req.url) || (isPage(req.url) ? load(PAGE) : null);
    if (snap) {
      serveSnapshot(res, snap);
      return;
    }
    if (isPage(req.url)) {
      offlinePage(res, reason);
      return;
    }
    res.writeHead(503, { 'Content-Type': 'text/plain; charset=utf-8', 'Cache-Control': 'no-store' });
    res.end('Offline and not cached.');
  }

  async function handle(req, res) {
    let up;
    const headers = Object.assign({}, req.headers);
    delete headers.range;
    if (!fs.existsSync(path.join(dir, keyOf(req.url) + '.json'))) {
      // Without a snapshot we need a full 200 to take one, not a 304 for Chromium's cache.
      delete headers['if-none-match'];
      delete headers['if-modified-since'];
    }
    try {
      up = await upstream.request({ method: 'GET', path: req.url, headers, timeoutMs, signal: res });
    } catch (err) {
      fallback(req, res, err.kind === 'network' ? 'unreachable' : err.kind);
      return;
    }
    if (isServerError(up.statusCode)) {
      up.resume();
      fallback(req, res, GATEWAY_STATUS.has(up.statusCode) ? 'unreachable' : 'http');
      return;
    }
    let body;
    try {
      body = await readBody(up, timeoutMs * 3);
    } catch (err) {
      fallback(req, res, 'unreachable');
      return;
    }
    if (up.statusCode === 200) save(req.url, up, body);
    const out = upstream.responseHeaders(up.headers);
    if (up.statusCode !== 304 && up.statusCode !== 204) out['content-length'] = body.length;
    res.writeHead(up.statusCode, out);
    res.end(req.method === 'HEAD' ? undefined : body);
  }

  function clear() {
    for (const name of fs.readdirSync(dir)) {
      try { fs.unlinkSync(path.join(dir, name)); } catch (e) { /* ignore */ }
    }
  }

  return { handle, clear, load, referencedUrls };
}

module.exports = { createPageCache, referencedUrls, keyOf };
