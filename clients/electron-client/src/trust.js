'use strict';
// TLS trust for the hop from the loopback proxy to the AiSignX server.
// Order: public CAs and CAs in the OS store first, then (only if the user ticked Trust in
// setup) a pinned leaf certificate. rejectUnauthorized stays true on every agent; there is
// no ignore-certificate-errors, certificate-error override or setCertificateVerifyProc.
const tls = require('tls');
const net = require('net');
const http = require('http');
const https = require('https');
const { X509Certificate } = require('crypto');

let baseCaCache = null;

// Bundled Mozilla roots plus the OS store, so internal CAs installed in Windows work.
function baseCa() {
  if (baseCaCache) return baseCaCache;
  const set = new Set();
  for (const kind of ['default', 'system']) {
    try {
      for (const pem of tls.getCACertificates(kind)) set.add(pem);
    } catch (e) {
      if (kind === 'default') for (const pem of tls.rootCertificates) set.add(pem);
    }
  }
  baseCaCache = [...set];
  return baseCaCache;
}

function pinnedIdentity(pin) {
  return (host, cert) => (cert && cert.fingerprint256 === pin ? undefined : tls.checkServerIdentity(host, cert));
}

// One keep-alive agent per configuration. opts.ca overrides the base CA list (tests).
function agentFor(cfg, opts) {
  const isHttps = typeof cfg.serverUrl === 'string' && cfg.serverUrl.startsWith('https:');
  if (!isHttps) return new http.Agent({ keepAlive: true, maxSockets: 64 });
  const ca = (opts && opts.ca) || baseCa();
  const options = { keepAlive: true, maxSockets: 64, rejectUnauthorized: true, ca };
  if (cfg.certPin && cfg.certPin.sha256) {
    options.ca = [...ca, cfg.certPin.caPem];
    // The hostname check is skipped only when the pin matches: LAN IPs are often
    // missing from a self-signed certificate's SAN.
    options.checkServerIdentity = pinnedIdentity(cfg.certPin.sha256);
  }
  return new https.Agent(options);
}

const CERT_CODES = new Set([
  'DEPTH_ZERO_SELF_SIGNED_CERT', 'SELF_SIGNED_CERT_IN_CHAIN', 'UNABLE_TO_VERIFY_LEAF_SIGNATURE',
  'UNABLE_TO_GET_ISSUER_CERT', 'UNABLE_TO_GET_ISSUER_CERT_LOCALLY', 'CERT_HAS_EXPIRED',
  'CERT_NOT_YET_VALID', 'CERT_UNTRUSTED', 'CERT_SIGNATURE_FAILURE', 'CERT_REVOKED',
  'ERR_TLS_CERT_ALTNAME_INVALID', 'HOSTNAME_MISMATCH', 'INVALID_CA', 'CERT_REJECTED',
]);

function isCertError(err) {
  return !!err && (CERT_CODES.has(err.code) || /certificate/i.test(err.message || ''));
}

// Error classes shared by the proxy and setup: network | tls-pin-mismatch | tls-untrusted.
function classify(err, cfg) {
  if (isCertError(err)) return cfg && cfg.certPin ? 'tls-pin-mismatch' : 'tls-untrusted';
  return 'network';
}

function topOfChain(peer) {
  let cur = peer;
  const seen = new Set();
  while (cur && cur.issuerCertificate && cur.issuerCertificate !== cur && !seen.has(cur.fingerprint256)) {
    seen.add(cur.fingerprint256);
    cur = cur.issuerCertificate;
  }
  return cur;
}

function selfSigned(x509) {
  try {
    return x509.subject === x509.issuer && x509.verify(x509.publicKey);
  } catch (e) {
    return false;
  }
}

// TLS handshake that only reads the presented certificate for the setup trust card, then
// destroys the socket. No HTTP bytes are sent. Verification is reported, not enforced,
// because this exists to show the user a certificate that the system does not trust yet.
function capture(url, opts) {
  const u = new URL(url);
  const timeoutMs = (opts && opts.timeoutMs) || 8000;
  const host = u.hostname.replace(/^\[|\]$/g, '');
  return new Promise((resolve, reject) => {
    const socket = tls.connect({
      host,
      port: Number(u.port) || 443,
      servername: net.isIP(host) ? undefined : host,
      ca: (opts && opts.ca) || baseCa(),
      rejectUnauthorized: false, // read-only probe: the socket is destroyed before any data
      ALPNProtocols: ['http/1.1'],
    });
    const timer = setTimeout(() => { socket.destroy(); reject(Object.assign(new Error('timeout'), { code: 'ETIMEDOUT' })); }, timeoutMs);
    socket.once('error', (err) => { clearTimeout(timer); reject(err); });
    socket.once('secureConnect', () => {
      clearTimeout(timer);
      try {
        // getPeerCertificate(true) first: calling getPeerX509Certificate() before it
        // empties the detailed chain in Node 24.
        const peer = socket.getPeerCertificate(true);
        const leaf = new X509Certificate(peer.raw);
        const top = topOfChain(peer);
        let caPem = null;
        if (top && top.raw) {
          const topX = new X509Certificate(top.raw);
          if (selfSigned(topX)) caPem = topX.toString();
        }
        const hostOk = !tls.checkServerIdentity(host, peer);
        resolve({
          fingerprint: leaf.fingerprint256,
          subject: leaf.subject.replace(/\n/g, ', '),
          issuer: leaf.issuer.replace(/\n/g, ', '),
          validTo: leaf.validTo,
          trusted: socket.authorized && hostOk,
          reason: socket.authorized ? (hostOk ? '' : 'hostname') : String(socket.authorizationError || ''),
          caPem,
          pinnable: !!caPem,
        });
      } catch (e) {
        reject(e);
      } finally {
        socket.destroy();
      }
    });
  });
}

module.exports = { baseCa, agentFor, capture, classify, isCertError };
