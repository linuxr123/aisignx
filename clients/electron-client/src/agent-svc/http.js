'use strict';
// HTTP(S) to the one configured server. No redirects are followed and the host is never taken from a response.
// request({method, path, headers, body, timeoutMs}) -> Promise<IncomingMessage> (the caller reads or discards it).
const http = require('http');
const https = require('https');

function makeRequester(server) {
  const u = new URL(server);
  const lib = u.protocol === 'https:' ? https : http;
  return function request(o) {
    return new Promise((resolve, reject) => {
      const headers = Object.assign({ 'user-agent': 'AiSignX-Agent' }, o.headers || {});
      const req = lib.request({ protocol: u.protocol, hostname: u.hostname.replace(/^\[|\]$/g, ''), port: u.port || undefined,
        method: o.method, path: o.path, headers, timeout: o.timeoutMs || 20000 }, (res) => resolve(res));
      req.on('timeout', () => req.destroy(new Error('timeout')));
      req.on('error', reject);
      if (o.body) o.body.pipe(req); else req.end();
    });
  };
}

module.exports = { makeRequester };
