'use strict';
// --smoke: start the real shell against a running server and check that the browser player
// reaches its pairing (or setup-wait) screen through the loopback proxy. Exit 0 or 1.
const fs = require('fs');
const os = require('os');
const path = require('path');
const http = require('http');

const LIMIT_MS = 30000;
const GOOD_STATES = new Set(['pairing', 'setup_wait']);

function get(url) {
  const u = new URL(url);
  return new Promise((resolve, reject) => {
    http.get({ host: u.hostname, port: u.port, path: u.pathname + u.search, agent: false,
      headers: { host: u.host } }, (res) => {
      res.resume();
      res.on('end', () => resolve(res.statusCode));
    }).on('error', reject);
  });
}

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

async function runSmoke({ getWindow, getOrigin, log }) {
  const started = Date.now();
  let state = '';
  const out = (msg) => { process.stdout.write(msg + '\n'); log.info(msg); };
  while (Date.now() - started < LIMIT_MS) {
    const win = getWindow();
    if (win && !win.isDestroyed() && !win.webContents.isLoading()) {
      try {
        state = await win.webContents.executeJavaScript('document.body ? (document.body.dataset.state || "") : ""', true);
      } catch (e) { state = ''; }
      if (GOOD_STATES.has(state)) break;
    }
    await sleep(500);
  }
  if (!GOOD_STATES.has(state)) {
    out('SMOKE FAIL: player state "' + state + '" after ' + LIMIT_MS / 1000 + ' s');
    return 1;
  }
  try {
    const win = getWindow();
    const hasShell = await win.webContents.executeJavaScript(
      'typeof window.AiSignXShell === "object" && window.AiSignXShell.info().name', true);
    const offline = await get(getOrigin() + '/__shell/offline');
    const health = await get(getOrigin() + '/healthz');
    const problems = [];
    if (hasShell !== 'electron') problems.push('window.AiSignXShell missing');
    if (offline !== 503) problems.push('/__shell/offline returned ' + offline);
    if (health !== 200) problems.push('/healthz returned ' + health);
    if (problems.length) {
      out('SMOKE FAIL: state=' + state + '; ' + problems.join('; '));
      return 1;
    }
    out('SMOKE OK: state=' + state + ' origin=' + getOrigin() + ' shell=electron offline=503 healthz=200');
    return 0;
  } catch (e) {
    out('SMOKE FAIL: ' + e.message);
    return 1;
  }
}

// Smoke runs use a fresh temp profile; remove the ones earlier runs left behind.
function tempProfile() {
  const tmp = os.tmpdir();
  for (const name of fs.readdirSync(tmp)) {
    if (!name.startsWith('aisignx-smoke-')) continue;
    try { fs.rmSync(path.join(tmp, name), { recursive: true, force: true }); } catch (e) { /* still in use */ }
  }
  return fs.mkdtempSync(path.join(tmp, 'aisignx-smoke-'));
}

module.exports = { runSmoke, tempProfile };
