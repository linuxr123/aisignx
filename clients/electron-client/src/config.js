'use strict';
// Shell configuration: userData/config.json and client_id.txt. Plain Node, no Electron.
// There is no token anywhere: the display credential is the HttpOnly cookie that the
// server sets, kept by Chromium in the persistent default session.
const fs = require('fs');
const path = require('path');
const crypto = require('crypto');
const { sanitizeUnlock } = require('./unlock');

const CONFIG_FILE = 'config.json';
const CLIENT_ID_FILE = 'client_id.txt';
const PORT_MIN = 47800;
const PORT_MAX = 47899;
const PIN_RE = /^[0-9A-F]{2}(:[0-9A-F]{2}){31}$/; // TLS certificate pin, not the unlock PIN
const CLIENT_ID_RE = /^[0-9a-f]{32}$/;

function defaults() {
  // unlock: the PBKDF2 hash of the display's unlock PIN, copied from the manifest (see unlock.js).
  // allowInput / kioskLock: the display's "Player controls" and "Lock player" options, copied
  // from the manifest so an offline start applies them. allowInput is true until the server
  // says otherwise (the legacy default, as on Android).
  return { serverUrl: '', port: 0, certPin: null, autostart: true, unlock: null, allowInput: true, kioskLock: false };
}

// Throws an Error with a user-facing message when the address is not acceptable.
function normalizeServerUrl(input) {
  let s = typeof input === 'string' ? input.trim() : '';
  if (!s) throw new Error('Enter the server address.');
  if (s.length > 300) throw new Error('The address is too long.');
  if (/^[a-z][a-z0-9+.-]*:\/*$/i.test(s)) throw new Error('This is not a valid address.');
  s = s.replace(/\/+$/, '');
  if (!/^[a-z][a-z0-9+.-]*:\/\//i.test(s)) s = 'http://' + s;
  let u;
  try { u = new URL(s); } catch (e) { throw new Error('This is not a valid address.'); }
  if (u.protocol !== 'http:' && u.protocol !== 'https:') throw new Error('Use an http:// or https:// address.');
  if (u.username || u.password || /@/.test(s.split('://')[1].split('/')[0])) {
    throw new Error('The address must not contain a user name or password.');
  }
  if (s.includes('?') || s.includes('#')) throw new Error('The address must not contain ? or #.');
  if (u.pathname !== '/' && u.pathname !== '') throw new Error('Enter only the server address, without a path.');
  if (!u.hostname) throw new Error('This is not a valid address.');
  return u.origin;
}

// LAN-style hosts, where plain http needs no warning in setup.
function isPrivateHost(host) {
  const h = String(host || '').toLowerCase().replace(/^\[|\]$/g, '');
  if (!h) return false;
  if (h === 'localhost' || !h.includes('.') && !h.includes(':')) return true; // single-label names
  if (/\.(local|lan|home|internal|intranet|corp|home\.arpa)$/.test(h)) return true;
  const v4 = /^(\d{1,3})\.(\d{1,3})\.(\d{1,3})\.(\d{1,3})$/.exec(h);
  if (v4) {
    const [a, b] = [Number(v4[1]), Number(v4[2])];
    return a === 10 || a === 127 || (a === 172 && b >= 16 && b <= 31) || (a === 192 && b === 168) ||
      (a === 169 && b === 254) || (a === 100 && b >= 64 && b <= 127);
  }
  if (h.includes(':')) return h === '::1' || /^f[cd][0-9a-f]{2}:/.test(h) || /^fe[89ab][0-9a-f]:/.test(h);
  return false;
}

function isValidPin(pin) {
  return !!pin && typeof pin === 'object' && typeof pin.sha256 === 'string' && PIN_RE.test(pin.sha256) &&
    typeof pin.caPem === 'string' && pin.caPem.startsWith('-----BEGIN CERTIFICATE-----') && pin.caPem.length < 20000;
}

function validPort(p) {
  return Number.isInteger(p) && p >= 1024 && p <= 65535;
}

// Returns a clean config object, or throws when the stored data is not usable.
function sanitize(raw) {
  if (!raw || typeof raw !== 'object' || Array.isArray(raw)) throw new Error('config is not an object');
  const cfg = defaults();
  if (raw.serverUrl) cfg.serverUrl = normalizeServerUrl(raw.serverUrl);
  if (raw.port !== undefined && raw.port !== 0) {
    if (!validPort(raw.port)) throw new Error('bad port');
    cfg.port = raw.port;
  }
  if (raw.certPin) {
    if (!isValidPin(raw.certPin)) throw new Error('bad certPin');
    cfg.certPin = { sha256: raw.certPin.sha256, caPem: raw.certPin.caPem };
  }
  if (raw.autostart !== undefined) cfg.autostart = raw.autostart !== false;
  cfg.unlock = sanitizeUnlock(raw.unlock); // a bad value is dropped, never thrown
  if (typeof raw.allowInput === 'boolean') cfg.allowInput = raw.allowInput;
  if (typeof raw.kioskLock === 'boolean') cfg.kioskLock = raw.kioskLock;
  return cfg;
}

// The config fields a manifest's `settings` changes -> {unlock?, allowInput?, kioskLock?}, only
// the ones that differ from cfg. No `settings` (old server): {}. `settings` without kiosk_lock
// means off; unlock and allow_input change only when present.
function settingsPatch(cfg, settings) {
  const patch = {};
  if (!settings || typeof settings !== 'object' || Array.isArray(settings)) return patch;
  if ('unlock' in settings) {
    const next = sanitizeUnlock(settings.unlock);
    if (JSON.stringify(next) !== JSON.stringify(cfg.unlock)) patch.unlock = next;
  }
  if (typeof settings.allow_input === 'boolean' && settings.allow_input !== cfg.allowInput) {
    patch.allowInput = settings.allow_input;
  }
  const kioskLock = settings.kiosk_lock === true;
  if (kioskLock !== cfg.kioskLock) patch.kioskLock = kioskLock;
  return patch;
}

// -> {config, status: 'ok' | 'missing' | 'corrupt'}. A corrupt file is renamed to .bad
// so the app opens setup instead of crashing on every start.
function loadConfig(dir) {
  const file = path.join(dir, CONFIG_FILE);
  let text;
  try {
    text = fs.readFileSync(file, 'utf8');
  } catch (e) {
    return { config: defaults(), status: 'missing' };
  }
  try {
    return { config: sanitize(JSON.parse(text)), status: 'ok' };
  } catch (e) {
    try { fs.renameSync(file, file + '.bad'); } catch (e2) { /* ignore */ }
    return { config: defaults(), status: 'corrupt' };
  }
}

function writeAtomic(file, data) {
  fs.mkdirSync(path.dirname(file), { recursive: true });
  const tmp = file + '.tmp';
  fs.writeFileSync(tmp, data, { encoding: 'utf8', mode: 0o600 });
  fs.renameSync(tmp, file);
}

function saveConfig(dir, cfg) {
  const clean = sanitize(cfg);
  writeAtomic(path.join(dir, CONFIG_FILE), JSON.stringify(clean, null, 2) + '\n');
  return clean;
}

// 32 lowercase hex characters, created once and kept across server changes.
function clientId(dir) {
  const file = path.join(dir, CLIENT_ID_FILE);
  try {
    const id = fs.readFileSync(file, 'utf8').trim();
    if (CLIENT_ID_RE.test(id)) return id;
  } catch (e) { /* create below */ }
  const id = crypto.randomBytes(16).toString('hex');
  writeAtomic(file, id + '\n');
  return id;
}

module.exports = {
  PORT_MIN, PORT_MAX, CLIENT_ID_RE,
  defaults, normalizeServerUrl, isPrivateHost, isValidPin, loadConfig, saveConfig, clientId, writeAtomic,
  settingsPatch,
};
