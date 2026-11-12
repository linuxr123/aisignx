'use strict';
// A small file log: userData/aisignx-player.log, truncated at 2 MB.
// Never pass Cookie / Set-Cookie headers or request bodies here; safeHeaders() drops them.
const fs = require('fs');

const MAX_BYTES = 2 * 1024 * 1024;
const SECRET_HEADERS = new Set(['cookie', 'set-cookie', 'authorization', 'proxy-authorization']);
let file = '';
let maxBytes = MAX_BYTES;

function init(target, opts) {
  file = target || '';
  maxBytes = (opts && opts.maxBytes) || MAX_BYTES;
}

function text(v) {
  if (v instanceof Error) return v.code ? v.code + ' ' + v.message : v.message;
  if (typeof v === 'string') return v;
  try { return JSON.stringify(v); } catch (e) { return String(v); }
}

function write(level, args) {
  const line = new Date().toISOString() + ' ' + level + ' ' + args.map(text).join(' ').replace(/[\r\n]+/g, ' ') + '\n';
  if (!file) {
    if (process.env.AISIGNX_LOG_CONSOLE) process.stdout.write(line);
    return;
  }
  try {
    let size = 0;
    try { size = fs.statSync(file).size; } catch (e) { size = 0; }
    if (size + line.length > maxBytes) {
      fs.writeFileSync(file, new Date().toISOString() + ' INFO log truncated at ' + maxBytes + ' bytes\n');
    }
    fs.appendFileSync(file, line);
  } catch (e) { /* logging must never break the shell */ }
}

function safeHeaders(headers) {
  const out = {};
  for (const [k, v] of Object.entries(headers || {})) {
    if (!SECRET_HEADERS.has(k.toLowerCase())) out[k] = v;
  }
  return out;
}

module.exports = {
  init,
  safeHeaders,
  get file() { return file; },
  info: (...a) => write('INFO', a),
  warn: (...a) => write('WARN', a),
  error: (...a) => write('ERROR', a),
};
