'use strict';
// First start after "Set up a Windows PC": the admin's setup script already paired this PC with its screen and left the
// display credential in ProgramData\AiSignX\setup.json (writable by administrators, readable and deletable by the player's
// own account only). The player takes it once, so nobody has to type a pairing code, and deletes the file.
// Plain Node (no Electron): the rules are tested in test/firstrun.test.js.
const fs = require('fs');
const path = require('path');
const config = require('./config');

const TOKEN_RE = /^[A-Za-z0-9_-]{20,128}$/;
const MAX_AGE_MS = 24 * 3600 * 1000; // an old file is leftover from an abandoned set-up: ignore it
const MAX_BYTES = 4096;

function setupFile(env) {
  const base = (env && env.ProgramData) || process.env.ProgramData || 'C:\ProgramData';
  return path.join(base, 'AiSignX', 'setup.json');
}

// Text of setup.json -> {server, token} or null. The server must pass the same rules as the setup window's address.
function parseSetup(text, now) {
  if (typeof text !== 'string' || text.length > MAX_BYTES) return null;
  let o;
  try { o = JSON.parse(text.replace(/^\uFEFF/, '')); } catch (e) { return null; }
  if (!o || typeof o !== 'object' || Array.isArray(o)) return null;
  if (typeof o.token !== 'string' || !TOKEN_RE.test(o.token)) return null;
  let server;
  try { server = config.normalizeServerUrl(o.server); } catch (e) { return null; }
  const created = Date.parse(o.created);
  if (!Number.isFinite(created) || (now || Date.now()) - created > MAX_AGE_MS || created - (now || Date.now()) > 3600 * 1000) return null;
  return { server, token: o.token };
}

// Reads the file. {server, token} or null (no file, unreadable, or not acceptable).
function readSetup(file, now) {
  try {
    const st = fs.statSync(file);
    if (!st.isFile() || st.size > MAX_BYTES) return null;
    return parseSetup(fs.readFileSync(file, 'utf8'), now);
  } catch (e) {
    return null;
  }
}

// The credential must not stay on disk: delete the file (best effort; the caller logs a failure).
function consume(file) {
  try { fs.unlinkSync(file); return true; } catch (e) { return e.code === 'ENOENT'; }
}

// The cookie the server would have set when the page paired itself (server: player/auth.py set_display_cookie).
function displayCookie(origin, token, now) {
  return {
    url: origin + '/api/player', name: 'aisignx_display', value: token, path: '/api/player',
    httpOnly: true, sameSite: 'strict', expirationDate: Math.floor((now || Date.now()) / 1000) + 10 * 365 * 24 * 3600,
  };
}

module.exports = { setupFile, parseSetup, readSetup, consume, displayCookie, TOKEN_RE };
