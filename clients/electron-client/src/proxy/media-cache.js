'use strict';
// Media cache: userData/media/<sha1(path)>.bin plus index.json {path: {size, sha256, mime, lastUsed}}.
// Hits are served only to requests carrying the session's current display cookie, so other
// local processes cannot read media through the loopback port. Misses stream through the
// proxy and queue a full background download.
const fs = require('fs');
const path = require('path');
const crypto = require('crypto');
const { Transform } = require('stream');
const { pipeline } = require('stream/promises');
const { sendFile } = require('./range');
const { writeAtomic } = require('../config');
const log = require('../log');

const MEDIA_RE = /^\/api\/player\/v1\/media\/\d+\/[^/?#]+$/;
const COOKIE_NAME = 'aisignx_display';
const MAX_BUDGET = 10 * 1024 * 1024 * 1024;
const STALL_MS = 60000;

function keyOf(p) {
  return crypto.createHash('sha1').update(p).digest('hex');
}

function cookieFrom(header) {
  if (typeof header !== 'string') return '';
  for (const part of header.split(';')) {
    const i = part.indexOf('=');
    if (i > 0 && part.slice(0, i).trim() === COOKIE_NAME) return part.slice(i + 1).trim();
  }
  return '';
}

function createMediaCache(opts) {
  const dir = opts.dir;
  const upstream = opts.upstream;
  const getCookie = opts.getCookie || (() => '');
  const concurrency = opts.concurrency || 2;
  const retryDelays = opts.retryDelays || [2000, 10000, 30000];
  const saveDelayMs = opts.saveDelayMs === undefined ? 3000 : opts.saveDelayMs;
  const indexFile = path.join(dir, 'index.json');
  fs.mkdirSync(dir, { recursive: true });

  let index = loadIndex();
  const known = new Map();      // path -> {size, sha256, mime, video} from the latest manifests
  let current = new Set();      // paths in the current manifest: never evicted
  let queue = [];               // paths waiting for download
  const queued = new Set();
  const active = new Map();     // path -> response stream being downloaded
  const attempts = new Map();
  const timers = new Set();
  let waiters = [];
  let saveTimer = null;
  let stopped = false;

  function fileOf(p) { return path.join(dir, keyOf(p) + '.bin'); }

  function loadIndex() {
    let data = {};
    try { data = JSON.parse(fs.readFileSync(indexFile, 'utf8')) || {}; } catch (e) { data = {}; }
    const clean = {};
    for (const [p, e] of Object.entries(data)) {
      if (!MEDIA_RE.test(p) || !e || !Number.isSafeInteger(e.size)) continue;
      try {
        if (fs.statSync(path.join(dir, keyOf(p) + '.bin')).size === e.size) clean[p] = e;
      } catch (err) { /* file gone */ }
    }
    for (const name of fs.readdirSync(dir)) {
      if (name.endsWith('.part')) { try { fs.unlinkSync(path.join(dir, name)); } catch (e) { /* ignore */ } }
    }
    return clean;
  }

  function saveIndex() {
    clearTimeout(saveTimer);
    saveTimer = null;
    try { writeAtomic(indexFile, JSON.stringify(index)); } catch (e) { log.warn('media index save failed', e.message); }
  }
  function scheduleSave() {
    if (saveTimer || stopped) return;
    saveTimer = setTimeout(saveIndex, saveDelayMs);
  }

  function dropEntry(p) {
    delete index[p];
    try { fs.unlinkSync(fileOf(p)); } catch (e) { /* ignore */ }
  }

  // ---- Serving.
  function handle(req, res) {
    const p = req.url.split('?')[0];
    const session = getCookie();
    const cookieOk = !!session && cookieFrom(req.headers.cookie) === session;
    const entry = index[p];
    if (cookieOk && entry) {
      let size = -1;
      try { size = fs.statSync(fileOf(p)).size; } catch (e) { size = -1; }
      if (size === entry.size) {
        entry.lastUsed = Date.now();
        scheduleSave();
        sendFile(req, res, fileOf(p), size, {
          'Content-Type': entry.mime || 'application/octet-stream',
          'Cache-Control': 'private, max-age=31536000, immutable',
          'X-AiSignX-Shell-Cache': '1',
        });
        return;
      }
      dropEntry(p);
      scheduleSave();
    }
    upstream.forward(req, res);
    if (cookieOk && MEDIA_RE.test(p)) enqueue(p);
  }

  // ---- Downloads: at most `concurrency` at once, videos first, retries with backoff.
  function isVideo(p) {
    const k = known.get(p);
    return !!(k && (k.video || (k.mime || '').startsWith('video/')));
  }

  function enqueue(p) {
    if (stopped || index[p] || queued.has(p) || active.has(p)) return;
    queued.add(p);
    queue.push(p);
    pump();
  }

  function pump() {
    queue.sort((a, b) => Number(isVideo(b)) - Number(isVideo(a)));
    while (!stopped && active.size < concurrency && queue.length) {
      const p = queue.shift();
      queued.delete(p);
      active.set(p, null);
      download(p).then(() => {
        attempts.delete(p);
        return evict();
      }, (err) => {
        const n = (attempts.get(p) || 0) + 1;
        attempts.set(p, n);
        log.warn('media download failed', p, 'attempt', n, err.message);
        if (!err.permanent && n <= retryDelays.length && !stopped) {
          const t = setTimeout(() => { timers.delete(t); enqueue(p); }, retryDelays[n - 1]);
          timers.add(t);
        } else {
          attempts.delete(p);
        }
      }).finally(() => {
        active.delete(p);
        pump();
      });
    }
    if (!active.size && !queue.length && !timers.size) {
      const w = waiters;
      waiters = [];
      w.forEach((fn) => fn());
    }
  }

  async function download(p) {
    const cookie = getCookie();
    if (!cookie) throw Object.assign(new Error('no display cookie'), { permanent: true });
    const res = await upstream.request({ method: 'GET', path: p, timeoutMs: 15000,
      headers: { cookie: COOKIE_NAME + '=' + cookie, accept: '*/*' } });
    active.set(p, res);
    if (res.statusCode !== 200) {
      res.resume();
      const permanent = [401, 403, 404, 410].includes(res.statusCode);
      throw Object.assign(new Error('HTTP ' + res.statusCode), { permanent });
    }
    const meta = known.get(p) || {};
    const expected = Number.isSafeInteger(meta.size) ? meta.size : Number(res.headers['content-length']);
    const hash = crypto.createHash('sha256');
    let bytes = 0;
    let stall = null;
    const arm = () => { clearTimeout(stall); stall = setTimeout(() => res.destroy(new Error('stalled')), STALL_MS); };
    const meter = new Transform({
      transform(chunk, enc, cb) { arm(); bytes += chunk.length; hash.update(chunk); cb(null, chunk); },
    });
    const part = fileOf(p) + '.part';
    arm();
    try {
      await pipeline(res, meter, fs.createWriteStream(part));
    } catch (e) {
      try { fs.unlinkSync(part); } catch (e2) { /* ignore */ }
      throw e;
    } finally {
      clearTimeout(stall);
    }
    const sha = hash.digest('hex');
    const wantSha = typeof meta.sha256 === 'string' ? meta.sha256.toLowerCase() : '';
    if ((Number.isSafeInteger(expected) && bytes !== expected) || (wantSha && sha !== wantSha)) {
      try { fs.unlinkSync(part); } catch (e) { /* ignore */ }
      throw new Error('size or sha256 mismatch (' + bytes + ' bytes)');
    }
    if (stopped) { try { fs.unlinkSync(part); } catch (e) { /* ignore */ } return; }
    fs.renameSync(part, fileOf(p));
    index[p] = { size: bytes, sha256: sha, mime: meta.mime || res.headers['content-type'] || '', lastUsed: Date.now() };
    saveIndex();
  }

  // ---- Manifest (IPC from the player preload).
  function onManifest(json) {
    let m;
    try { m = typeof json === 'string' ? JSON.parse(json) : json; } catch (e) { return; }
    if (!m || !Array.isArray(m.items)) return;
    const next = new Set();
    for (const it of m.items) {
      if (!it || (it.type !== 'image' && it.type !== 'video') || typeof it.url !== 'string') continue;
      const p = it.url.split('?')[0];
      if (!MEDIA_RE.test(p)) continue;
      next.add(p);
      known.set(p, { size: Number.isSafeInteger(it.size) ? it.size : null,
        sha256: typeof it.sha256 === 'string' ? it.sha256 : null,
        mime: typeof it.mime === 'string' ? it.mime : '', video: it.type === 'video' });
    }
    current = next;
    for (const p of next) enqueue(p);
  }

  // ---- LRU eviction, only when over budget. Current-manifest files are never evicted.
  async function budget() {
    if (opts.budgetBytes !== undefined) return opts.budgetBytes;
    let free = MAX_BUDGET;
    try {
      const st = await fs.promises.statfs(dir);
      free = st.bavail * st.bsize;
    } catch (e) { /* keep the cap */ }
    // Half of the space the cache could use: what is free now plus what it already holds.
    return Math.min(MAX_BUDGET, Math.floor((free + used()) / 2));
  }
  function used() {
    return Object.values(index).reduce((n, e) => n + e.size, 0);
  }
  async function evict() {
    const limit = await budget();
    let total = used();
    if (total <= limit) return [];
    const removed = [];
    const candidates = Object.entries(index).filter(([p]) => !current.has(p))
      .sort((a, b) => (a[1].lastUsed || 0) - (b[1].lastUsed || 0));
    for (const [p, e] of candidates) {
      if (total <= limit) break;
      dropEntry(p);
      total -= e.size;
      removed.push(p);
    }
    if (removed.length) { log.info('media cache evicted', removed.length, 'files'); saveIndex(); }
    return removed;
  }

  function whenIdle() {
    return new Promise((resolve) => { waiters.push(resolve); pump(); });
  }

  function stop() {
    stopped = true;
    for (const t of timers) clearTimeout(t);
    timers.clear();
    for (const res of active.values()) if (res) res.destroy();
    if (saveTimer) saveIndex();
    queue = [];
    queued.clear();
  }

  function clear() {
    stop();
    index = {};
    known.clear();
    current = new Set();
    for (const name of fs.readdirSync(dir)) {
      try { fs.unlinkSync(path.join(dir, name)); } catch (e) { /* ignore */ }
    }
  }

  return {
    handle, onManifest, evict, whenIdle, stop, clear, flush: saveIndex,
    get index() { return index; },
    setLastUsed(p, t) { if (index[p]) index[p].lastUsed = t; },
  };
}

module.exports = { createMediaCache, cookieFrom, MEDIA_RE, COOKIE_NAME };
