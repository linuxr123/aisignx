'use strict';
// Setup's "Test" button: GET /healthz through the same trust agent the proxy will use.
// -> {result: 'ok' | 'not-aisignx' | 'unreachable' | 'untrusted', message, card?}
const http = require('http');
const https = require('https');
const trust = require('./trust');

const TIMEOUT_MS = 8000;
const MAX_BODY = 64 * 1024;

function getJson(url, agent) {
  const mod = url.startsWith('https:') ? https : http;
  return new Promise((resolve, reject) => {
    const req = mod.get(url, { agent, headers: { accept: 'application/json' } }, (res) => {
      let body = '';
      res.setEncoding('utf8');
      res.on('data', (c) => { body += c; if (body.length > MAX_BODY) req.destroy(new Error('response too large')); });
      res.on('end', () => {
        let data = null;
        try { data = JSON.parse(body); } catch (e) { data = null; }
        resolve({ status: res.statusCode, data });
      });
    });
    req.setTimeout(TIMEOUT_MS, () => req.destroy(Object.assign(new Error('timed out'), { code: 'ETIMEDOUT' })));
    req.on('error', reject);
  });
}

function describe(err) {
  const code = err && err.code;
  if (code === 'ECONNREFUSED') return 'connection refused';
  if (code === 'ENOTFOUND' || code === 'EAI_AGAIN') return 'name not found';
  if (code === 'ETIMEDOUT' || code === 'ECONNRESET') return 'timed out';
  if (code === 'EHOSTUNREACH' || code === 'ENETUNREACH') return 'no route to host';
  return (err && (err.code || err.message)) || 'unknown error';
}

// cfg: {serverUrl, certPin}. opts.ca overrides the CA list (tests).
async function checkServer(cfg, opts) {
  const agent = trust.agentFor(cfg, opts);
  try {
    const r = await getJson(cfg.serverUrl + '/healthz', agent);
    if (r.status === 200 && r.data && r.data.app === 'aisignx') {
      return { result: 'ok', message: 'Connected to an AiSignX server' };
    }
    return { result: 'not-aisignx', message: 'This address is not an AiSignX server' };
  } catch (err) {
    if (cfg.serverUrl.startsWith('https:') && trust.isCertError(err)) {
      try {
        const card = await trust.capture(cfg.serverUrl, opts);
        const message = cfg.certPin ? 'Server certificate changed' : 'The server certificate is not trusted';
        return { result: 'untrusted', message, card, pinMismatch: !!cfg.certPin };
      } catch (e2) {
        return { result: 'unreachable', message: "Can't reach the server (" + describe(e2) + ')' };
      }
    }
    return { result: 'unreachable', message: "Can't reach the server (" + describe(err) + ')' };
  } finally {
    agent.destroy();
  }
}

module.exports = { checkServer };
