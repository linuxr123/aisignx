'use strict';
// Requests from the loopback proxy to the configured AiSignX server.
// Streaming pass-through (bodies are piped, never buffered) so SSE works.
const http = require('http');
const https = require('https');
const trust = require('../trust');
const log = require('../log');

const HOP = new Set([
  'connection', 'keep-alive', 'proxy-connection', 'proxy-authenticate', 'proxy-authorization',
  'te', 'trailer', 'trailers', 'transfer-encoding', 'upgrade',
]);
const CONNECT_TIMEOUT_MS = 15000;

function hopNames(headers) {
  const names = new Set(HOP);
  const conn = headers.connection;
  if (typeof conn === 'string') conn.split(',').forEach((n) => names.add(n.trim().toLowerCase()));
  return names;
}

// Removes the Secure attribute: the hop to Chromium is http://127.0.0.1 and never leaves
// the machine, and Chromium would not store a Secure cookie for that origin.
function stripSecure(cookie) {
  return String(cookie).split(';').filter((part, i) => i === 0 || part.trim().toLowerCase() !== 'secure').join(';');
}

function rewriteOrigin(value, from, to) {
  if (typeof value !== 'string') return value;
  if (value === from) return to;
  return value.startsWith(from + '/') ? to + value.slice(from.length) : value;
}

function createUpstream(opts) {
  const base = new URL(opts.serverUrl);
  const origin = base.origin;
  const loopback = opts.loopbackOrigin;
  const cfg = opts.config || { serverUrl: opts.serverUrl, certPin: null };
  const agent = opts.agent || trust.agentFor(cfg);
  const mod = base.protocol === 'https:' ? https : http;

  function requestHeaders(input) {
    const drop = hopNames(input);
    const out = {};
    for (const [k, v] of Object.entries(input || {})) {
      const name = k.toLowerCase();
      if (drop.has(name) || name === 'host' || name === 'accept-encoding' || name.startsWith('x-forwarded-')) continue;
      out[name] = v;
    }
    out.host = base.host;
    out['accept-encoding'] = 'identity';
    if (loopback) {
      if (out.origin) out.origin = rewriteOrigin(out.origin, loopback, origin);
      if (out.referer) out.referer = rewriteOrigin(out.referer, loopback, origin);
    }
    return out;
  }

  function responseHeaders(input) {
    const drop = hopNames(input);
    const out = {};
    for (const [k, v] of Object.entries(input || {})) {
      const name = k.toLowerCase();
      if (drop.has(name)) continue;
      if (name === 'set-cookie') out[name] = (Array.isArray(v) ? v : [v]).map(stripSecure);
      else if (name === 'location' && loopback) out[name] = rewriteOrigin(v, origin, loopback);
      else out[name] = v;
    }
    return out;
  }

  function fail(err) {
    if (!err.kind) err.kind = trust.classify(err, cfg);
    return err;
  }

  // -> Promise<http.IncomingMessage>. The caller consumes or destroys the body.
  // timeoutMs bounds the wait for response headers only.
  function request(o) {
    return new Promise((resolve, reject) => {
      const req = mod.request({
        protocol: base.protocol,
        hostname: base.hostname.replace(/^\[|\]$/g, ''),
        port: base.port || undefined,
        method: o.method || 'GET',
        path: o.path,
        headers: requestHeaders(o.headers || {}),
        agent,
      });
      const timer = setTimeout(() => {
        req.destroy(Object.assign(new Error('upstream timeout'), { code: 'ETIMEDOUT', kind: 'network' }));
      }, o.timeoutMs || CONNECT_TIMEOUT_MS);
      let answered = false;
      req.on('response', (res) => { answered = true; clearTimeout(timer); resolve(res); });
      req.on('error', (err) => { clearTimeout(timer); reject(fail(err)); });
      // The caller went away before the server answered: abort the upstream request.
      if (o.signal) o.signal.once('close', () => { if (!answered) req.destroy(); });
      if (o.body) o.body.pipe(req);
      else req.end();
    });
  }

  // Pipes `req` to the server and the answer back to `res` without buffering.
  // A client abort aborts the upstream request.
  function forward(req, res, extra) {
    const headers = Object.assign({}, req.headers);
    let upstreamRes = null;
    let closed = false;
    res.on('close', () => {
      closed = true;
      if (upstreamRes && !upstreamRes.complete) upstreamRes.destroy();
    });
    const hasBody = !(req.method === 'GET' || req.method === 'HEAD');
    return request({ method: req.method, path: req.url, headers, body: hasBody ? req : null, signal: res })
      .then((up) => {
        upstreamRes = up;
        if (closed) { up.destroy(); return; }
        res.writeHead(up.statusCode, Object.assign(responseHeaders(up.headers), extra && extra.headers));
        res.flushHeaders();
        if (res.socket) res.socket.setNoDelay(true);
        up.on('error', () => res.destroy());
        up.pipe(res);
      })
      .catch((err) => {
        if (closed) return;
        log.warn('upstream', req.method, req.url.split('?')[0], err.kind, err.code || err.message);
        if (!res.headersSent) {
          const body = JSON.stringify({ error: 'upstream_unavailable', reason: err.kind });
          res.writeHead(502, { 'Content-Type': 'application/json', 'Cache-Control': 'no-store',
            'X-AiSignX-Shell-Error': err.kind, 'Content-Length': Buffer.byteLength(body) });
          res.end(body);
        } else {
          res.destroy();
        }
      });
  }

  return { origin, request, forward, requestHeaders, responseHeaders, agent, close: () => agent.destroy() };
}

module.exports = { createUpstream, stripSecure, HOP };
