'use strict';
// The loopback proxy: Chromium loads http://127.0.0.1:<port>/player/ from here, and every
// request is forwarded to the configured AiSignX server. Bound to 127.0.0.1 only.
const http = require('http');
const fs = require('fs');
const path = require('path');
const { MEDIA_RE } = require('./media-cache');
const log = require('../log');

const UI_DIR = path.join(__dirname, '..', 'ui');
const REASONS = new Set(['unreachable', 'tls-pin-mismatch', 'tls-untrusted', 'http', 'port-in-use', 'proxy-down', 'offline']);
const OFFLINE_CSP = "default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self'; " +
  "base-uri 'none'; form-action 'none'; frame-ancestors 'none'";
const SHELL_FILES = {
  '/__shell/offline.js': ['offline.js', 'text/javascript; charset=utf-8'],
  '/__shell/offline.css': ['offline.css', 'text/css; charset=utf-8'],
};

function offlineHtml(reason, target) {
  const code = REASONS.has(reason) ? reason : 'offline';
  return fs.readFileSync(path.join(UI_DIR, 'offline.html'), 'utf8')
    .replace('src="offline.js"', 'src="/__shell/offline.js"')
    .replace('href="offline.css"', 'href="/__shell/offline.css"')
    .replace('data-reason=""', 'data-reason="' + code + '"')
    .replace('data-target=""', 'data-target="' + target + '"');
}

function sendOffline(res, reason) {
  const body = Buffer.from(offlineHtml(reason, '/player/'), 'utf8');
  res.writeHead(503, {
    'Content-Type': 'text/html; charset=utf-8', 'Content-Length': body.length, 'Cache-Control': 'no-store',
    'Content-Security-Policy': OFFLINE_CSP, 'X-Frame-Options': 'DENY', 'X-Content-Type-Options': 'nosniff',
    'Referrer-Policy': 'no-referrer', 'Retry-After': '10',
  });
  res.end(body);
}

function sendShellFile(res, url) {
  const [name, type] = SHELL_FILES[url];
  const body = fs.readFileSync(path.join(UI_DIR, name));
  res.writeHead(200, { 'Content-Type': type, 'Content-Length': body.length, 'Cache-Control': 'no-store',
    'X-Content-Type-Options': 'nosniff' });
  res.end(body);
}

// opts: {port (0 = any free port), upstream, pageCache, mediaCache}
function createProxy(opts) {
  let port = opts.port || 0;
  const server = http.createServer(route);
  server.keepAliveTimeout = 65000;
  server.requestTimeout = 0; // SSE streams are long-lived
  server.headersTimeout = 30000;

  function route(req, res) {
    try {
      if (req.headers.host !== '127.0.0.1:' + port) {
        // Blocks DNS rebinding: only the exact loopback origin may talk to the proxy.
        res.writeHead(421, { 'Content-Type': 'text/plain', 'Connection': 'close' });
        res.end('Misdirected request');
        return;
      }
      const url = req.url || '/';
      const p = url.split('?')[0];
      const isGet = req.method === 'GET' || req.method === 'HEAD';
      if (p === '/__shell/offline') {
        const reason = new URL(url, 'http://x').searchParams.get('reason') || 'offline';
        sendOffline(res, reason);
        return;
      }
      if (SHELL_FILES[p] && isGet) {
        sendShellFile(res, p);
        return;
      }
      if (isGet && (p === '/player/' || p === '/player' || p.startsWith('/static/player/'))) {
        opts.pageCache.handle(req, res);
        return;
      }
      if (isGet && MEDIA_RE.test(p) && opts.mediaCache) {
        opts.mediaCache.handle(req, res);
        return;
      }
      opts.upstream.forward(req, res);
    } catch (e) {
      log.error('proxy route', e.stack || e.message);
      if (!res.headersSent) res.writeHead(500, { 'Content-Type': 'text/plain' });
      res.end();
    }
  }

  function start() {
    return new Promise((resolve, reject) => {
      server.once('error', reject);
      server.listen(port, '127.0.0.1', () => {
        server.removeListener('error', reject);
        port = server.address().port;
        server.on('error', (e) => log.error('proxy', e.message));
        resolve(port);
      });
    });
  }

  function stop() {
    return new Promise((resolve) => {
      if (!server.listening) { resolve(); return; }
      server.close(() => resolve());
      server.closeAllConnections();
    });
  }

  return { start, stop, server, get port() { return port; }, get origin() { return 'http://127.0.0.1:' + port; } };
}

// Resolves true when 127.0.0.1:port can be bound right now.
function portFree(port) {
  return new Promise((resolve) => {
    const s = http.createServer();
    s.once('error', () => resolve(false));
    s.listen(port, '127.0.0.1', () => s.close(() => resolve(true)));
  });
}

module.exports = { createProxy, sendOffline, offlineHtml, portFree, REASONS };
